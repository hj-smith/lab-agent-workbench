"""Automatic baseline estimators that produce preview-only, diagnosed results."""

from __future__ import annotations

import math
from typing import Protocol, cast

import numpy as np
import numpy.typing as npt
from scipy import sparse
from scipy.sparse.linalg import spsolve

from gpcsec.models import (
    AnalysisError,
    AutoBaselinePreview,
    AutoBaselineSpec,
    BaselineAlgorithm,
    BaselineDiagnostics,
    BaselineResult,
    ProcessingStep,
)

FloatArray = npt.NDArray[np.float64]


class BaselineEstimator(Protocol):
    algorithm: BaselineAlgorithm

    def estimate(
        self, time_min: npt.ArrayLike, signal: npt.ArrayLike, spec: AutoBaselineSpec
    ) -> AutoBaselinePreview: ...


def _arrays(time_min: npt.ArrayLike, signal: npt.ArrayLike) -> tuple[FloatArray, FloatArray]:
    time = np.asarray(time_min, dtype=np.float64)
    values = np.asarray(signal, dtype=np.float64)
    if time.ndim != 1 or time.shape != values.shape or len(time) < 4:
        raise AnalysisError("shape_mismatch", "automatic baseline requires equal 1D arrays")
    if not np.all(np.isfinite(time)) or not np.all(np.isfinite(values)):
        raise AnalysisError("non_finite_input", "automatic baseline arrays must be finite")
    if np.any(np.diff(time) <= 0):
        raise AnalysisError("non_increasing_time", "automatic baseline time must increase")
    return time, values


def _included(time: FloatArray, exclusions: tuple[tuple[float, float], ...]) -> FloatArray:
    mask = np.ones(len(time), dtype=np.float64)
    for start, end in exclusions:
        mask[(time >= start) & (time <= end)] = 0.0
    if np.count_nonzero(mask) < 3:
        raise AnalysisError("insufficient_baseline_domain", "exclusions leave too few points")
    return mask


def _penalty(size: int) -> sparse.csc_matrix:
    difference = sparse.diags([1.0, -2.0, 1.0], [0, 1, 2], shape=(size - 2, size))
    return cast(sparse.csc_matrix, (difference.T @ difference).tocsc())


def _diagnostics(
    signal: FloatArray,
    baseline: FloatArray,
    converged: bool,
    iterations: int,
) -> BaselineDiagnostics:
    residual = signal - baseline
    negative_fraction = float(np.mean(residual < 0.0))
    signal_range = max(float(np.ptp(signal)), np.finfo(np.float64).eps)
    baseline_ratio = float(np.ptp(baseline)) / signal_range
    risk = negative_fraction > 0.45 or baseline_ratio > 0.8
    warnings: list[str] = []
    if not converged:
        warnings.append("automatic baseline did not converge")
    if negative_fraction > 0.45:
        warnings.append("high negative-residual fraction after baseline")
    if baseline_ratio > 0.8:
        warnings.append("baseline span approaches signal span; broad-peak consumption risk")
    return BaselineDiagnostics(
        negative_fraction,
        baseline_ratio,
        risk,
        converged,
        iterations,
        tuple(warnings),
    )


def _preview(
    time: FloatArray,
    signal: FloatArray,
    baseline: FloatArray,
    spec: AutoBaselineSpec,
    version: str,
    converged: bool = True,
    iterations: int = 1,
) -> AutoBaselinePreview:
    if not np.all(np.isfinite(baseline)):
        raise AnalysisError("baseline_non_finite", "automatic baseline produced non-finite values")
    step = ProcessingStep(
        f"baseline.{spec.algorithm.value}",
        version,
        {"parameters": dict(spec.parameters), "exclusions": spec.exclusions, "preview_only": True},
    )
    result = BaselineResult(
        tuple(float(value) for value in time),
        tuple(float(value) for value in baseline),
        (),
        step,
    )
    return AutoBaselinePreview(spec, result, _diagnostics(signal, baseline, converged, iterations))


class PolynomialBaselineEstimator:
    algorithm = BaselineAlgorithm.POLYNOMIAL

    def estimate(
        self, time_min: npt.ArrayLike, signal: npt.ArrayLike, spec: AutoBaselineSpec
    ) -> AutoBaselinePreview:
        time, values = _arrays(time_min, signal)
        weights = _included(time, spec.exclusions)
        degree = int(spec.parameters.get("degree", 2))
        if degree < 0 or np.count_nonzero(weights) <= degree:
            raise AnalysisError("invalid_polynomial_degree", "baseline degree is unsupported")
        fitted = np.polynomial.Polynomial.fit(
            time[weights > 0], values[weights > 0], degree
        ).convert()
        return _preview(
            time,
            values,
            np.asarray(fitted(time), dtype=np.float64),
            spec,
            "polynomial-scaled-v1",
        )


class RubberBandBaselineEstimator:
    algorithm = BaselineAlgorithm.RUBBER_BAND

    def estimate(
        self, time_min: npt.ArrayLike, signal: npt.ArrayLike, spec: AutoBaselineSpec
    ) -> AutoBaselinePreview:
        time, values = _arrays(time_min, signal)
        included = _included(time, spec.exclusions) > 0
        indices = np.flatnonzero(included)
        hull: list[int] = []
        for index in indices:
            while len(hull) >= 2:
                left, middle = hull[-2], hull[-1]
                cross = (time[middle] - time[left]) * (values[index] - values[left]) - (
                    values[middle] - values[left]
                ) * (time[index] - time[left])
                if cross > 0:
                    break
                hull.pop()
            hull.append(int(index))
        if len(hull) < 2:
            raise AnalysisError("rubber_band_failure", "lower hull has fewer than two vertices")
        baseline = np.interp(time, time[hull], values[hull])
        return _preview(time, values, baseline, spec, "lower-convex-hull-v1")


def _smooth_solver(
    signal: FloatArray, weights: FloatArray, lam: float, penalty: sparse.csc_matrix
) -> FloatArray:
    system = sparse.diags(weights, 0, format="csc") + lam * penalty
    return np.asarray(spsolve(system, weights * signal), dtype=np.float64)


class AslsBaselineEstimator:
    algorithm = BaselineAlgorithm.ASLS

    def estimate(
        self, time_min: npt.ArrayLike, signal: npt.ArrayLike, spec: AutoBaselineSpec
    ) -> AutoBaselinePreview:
        time, values = _arrays(time_min, signal)
        included = _included(time, spec.exclusions)
        lam = float(spec.parameters.get("lambda", 1e6))
        asymmetry = float(spec.parameters.get("p", 0.01))
        max_iter = int(spec.parameters.get("max_iter", 50))
        tolerance = float(spec.parameters.get("tolerance", 1e-6))
        if lam <= 0 or not 0 < asymmetry < 1 or max_iter < 1 or tolerance <= 0:
            raise AnalysisError("invalid_baseline_parameters", "AsLS parameters are invalid")
        weights = included.copy()
        baseline = values.copy()
        converged = False
        penalty = _penalty(len(values))
        for _iteration in range(1, max_iter + 1):
            previous = baseline
            baseline = _smooth_solver(values, weights, lam, penalty)
            updated = np.where(values > baseline, asymmetry, 1.0 - asymmetry) * included
            change = np.linalg.norm(updated - weights) / max(np.linalg.norm(weights), 1e-15)
            weights = updated
            if change < tolerance and np.linalg.norm(baseline - previous) > 0:
                converged = True
                break
        return _preview(time, values, baseline, spec, "asls-sparse-v1", converged, _iteration)


class AirplsBaselineEstimator:
    algorithm = BaselineAlgorithm.AIRPLS

    def estimate(
        self, time_min: npt.ArrayLike, signal: npt.ArrayLike, spec: AutoBaselineSpec
    ) -> AutoBaselinePreview:
        time, values = _arrays(time_min, signal)
        included = _included(time, spec.exclusions)
        lam = float(spec.parameters.get("lambda", 1e6))
        max_iter = int(spec.parameters.get("max_iter", 50))
        tolerance = float(spec.parameters.get("tolerance", 1e-3))
        if lam <= 0 or max_iter < 1 or tolerance <= 0:
            raise AnalysisError("invalid_baseline_parameters", "airPLS parameters are invalid")
        weights = included.copy()
        baseline = values.copy()
        scale = max(float(np.sum(np.abs(values[included > 0]))), 1e-15)
        converged = False
        penalty = _penalty(len(values))
        for iteration in range(1, max_iter + 1):
            baseline = _smooth_solver(values, weights, lam, penalty)
            residual = values - baseline
            negative = residual[(residual < 0) & (included > 0)]
            negative_sum = float(np.sum(np.abs(negative)))
            if negative_sum <= tolerance * scale:
                converged = True
                break
            weights = np.zeros(len(values), dtype=np.float64)
            negative_mask = (residual < 0) & (included > 0)
            exponent = np.clip(iteration * np.abs(residual[negative_mask]) / negative_sum, 0, 50)
            weights[negative_mask] = np.exp(exponent)
            if np.any(included > 0):
                edge = math.exp(
                    min(50.0, iteration * float(np.max(np.abs(negative))) / negative_sum)
                )
                weights[0] = edge * included[0]
                weights[-1] = edge * included[-1]
        return _preview(time, values, baseline, spec, "airpls-sparse-v1", converged, iteration)


class ArplsBaselineEstimator:
    algorithm = BaselineAlgorithm.ARPLS

    def estimate(
        self, time_min: npt.ArrayLike, signal: npt.ArrayLike, spec: AutoBaselineSpec
    ) -> AutoBaselinePreview:
        time, values = _arrays(time_min, signal)
        included = _included(time, spec.exclusions)
        lam = float(spec.parameters.get("lambda", 1e6))
        max_iter = int(spec.parameters.get("max_iter", 50))
        tolerance = float(spec.parameters.get("tolerance", 1e-3))
        if lam <= 0 or max_iter < 1 or tolerance <= 0:
            raise AnalysisError("invalid_baseline_parameters", "arPLS parameters are invalid")
        weights = included.copy()
        baseline = values.copy()
        converged = False
        penalty = _penalty(len(values))
        for _iteration in range(1, max_iter + 1):
            baseline = _smooth_solver(values, weights, lam, penalty)
            residual = values - baseline
            negative = residual[(residual < 0) & (included > 0)]
            if len(negative) < 2:
                converged = True
                break
            mean = float(np.mean(negative))
            deviation = float(np.std(negative, ddof=1))
            if deviation <= np.finfo(np.float64).eps:
                converged = True
                break
            argument = np.clip(2.0 * (residual - (2.0 * deviation - mean)) / deviation, -50, 50)
            updated = (1.0 / (1.0 + np.exp(argument))) * included
            change = np.linalg.norm(updated - weights) / max(np.linalg.norm(weights), 1e-15)
            weights = updated
            if change < tolerance:
                converged = True
                break
        return _preview(time, values, baseline, spec, "arpls-sparse-v1", converged, _iteration)


ESTIMATORS: dict[BaselineAlgorithm, BaselineEstimator] = {
    BaselineAlgorithm.POLYNOMIAL: PolynomialBaselineEstimator(),
    BaselineAlgorithm.RUBBER_BAND: RubberBandBaselineEstimator(),
    BaselineAlgorithm.ASLS: AslsBaselineEstimator(),
    BaselineAlgorithm.AIRPLS: AirplsBaselineEstimator(),
    BaselineAlgorithm.ARPLS: ArplsBaselineEstimator(),
}


def estimate_baseline(
    time_min: npt.ArrayLike, signal: npt.ArrayLike, spec: AutoBaselineSpec
) -> AutoBaselinePreview:
    return ESTIMATORS[spec.algorithm].estimate(time_min, signal, spec)
