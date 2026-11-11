"""Forward log10(M)=f(RT) calibration fitting and guarded evaluation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt
from numpy.polynomial import Polynomial
from scipy.interpolate import PchipInterpolator
from scipy.optimize import brentq

from gpcsec.models import AnalysisError, CalibrationPoint, CalibrationStatistics

FloatArray = npt.NDArray[np.float64]


class Calibration(Protocol):
    @property
    def valid_rt_min(self) -> float: ...

    @property
    def valid_rt_max(self) -> float: ...

    @property
    def monotonic_direction(self) -> int: ...

    @property
    def statistics(self) -> CalibrationStatistics | None: ...

    @property
    def algorithm_version(self) -> str: ...

    def evaluate_log_m(
        self, retention_time: npt.ArrayLike, *, allow_extrapolation: bool = False
    ) -> FloatArray: ...

    def derivative(self, retention_time: npt.ArrayLike) -> FloatArray: ...

    def invert_log_m(self, log_m: float) -> float: ...


def _as_float_array(values: npt.ArrayLike) -> FloatArray:
    result = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(result)):
        raise AnalysisError("non_finite_input", "calibration input must be finite")
    return result


def _guard_range(values: FloatArray, lower: float, upper: float, allow: bool) -> None:
    if not allow and (np.any(values < lower) or np.any(values > upper)):
        raise AnalysisError(
            "calibration_extrapolation_forbidden",
            f"retention time is outside calibration range [{lower}, {upper}]",
        )


def _monotonic_direction(derivative: FloatArray, *, require_monotonic: bool = True) -> int:
    tolerance = max(1e-12, float(np.max(np.abs(derivative))) * 1e-10)
    if np.all(derivative < -tolerance):
        return -1
    if np.all(derivative > tolerance):
        return 1
    if require_monotonic:
        raise AnalysisError(
            "calibration_not_monotonic",
            "calibration derivative changes sign or reaches zero in the valid range",
        )
    return 0


@dataclass(frozen=True, slots=True)
class PolynomialCalibration:
    coefficients: tuple[float, ...]
    scaled_coefficients: tuple[float, ...]
    fit_domain: tuple[float, float]
    valid_rt_min: float
    valid_rt_max: float
    statistics: CalibrationStatistics | None
    monotonic_direction: int
    source_sha256: str | None = None
    algorithm_version: str = "polynomial-scaled-lstsq-v1"

    def evaluate_log_m(
        self, retention_time: npt.ArrayLike, *, allow_extrapolation: bool = False
    ) -> FloatArray:
        values = _as_float_array(retention_time)
        _guard_range(values, self.valid_rt_min, self.valid_rt_max, allow_extrapolation)
        result = np.polynomial.polynomial.polyval(values, self.coefficients)
        return np.asarray(result, dtype=np.float64)

    def evaluate_m(
        self, retention_time: npt.ArrayLike, *, allow_extrapolation: bool = False
    ) -> FloatArray:
        log_m = self.evaluate_log_m(retention_time, allow_extrapolation=allow_extrapolation)
        molecular_weight = np.power(10.0, log_m)
        if not np.all(np.isfinite(molecular_weight)) or np.any(molecular_weight <= 0):
            raise AnalysisError("invalid_molecular_weight", "calibration produced invalid M")
        return np.asarray(molecular_weight, dtype=np.float64)

    def derivative(self, retention_time: npt.ArrayLike) -> FloatArray:
        values = _as_float_array(retention_time)
        derivative_coefficients = np.polynomial.polynomial.polyder(self.coefficients)
        return np.asarray(
            np.polynomial.polynomial.polyval(values, derivative_coefficients), dtype=np.float64
        )

    def invert_log_m(self, log_m: float) -> float:
        if not math.isfinite(log_m):
            raise AnalysisError("non_finite_input", "log molecular weight must be finite")

        def residual(retention_time: float) -> float:
            value = np.polynomial.polynomial.polyval(retention_time, self.coefficients)
            return float(value - log_m)

        left = residual(self.valid_rt_min)
        right = residual(self.valid_rt_max)
        endpoint_tolerance = max(1e-12, abs(log_m) * 1e-12)
        if abs(left) <= endpoint_tolerance:
            return self.valid_rt_min
        if abs(right) <= endpoint_tolerance:
            return self.valid_rt_max
        if left * right > 0:
            raise AnalysisError(
                "inverse_out_of_range", "log molecular weight is outside calibration"
            )
        return float(brentq(residual, self.valid_rt_min, self.valid_rt_max))


@dataclass(frozen=True, slots=True)
class PchipCalibration:
    retention_time: tuple[float, ...]
    log_molecular_weight: tuple[float, ...]
    valid_rt_min: float
    valid_rt_max: float
    monotonic_direction: int
    source_sha256: str | None = None
    algorithm_version: str = "pchip-v1"

    @property
    def statistics(self) -> CalibrationStatistics | None:
        return None

    def _interpolator(self) -> PchipInterpolator:
        return PchipInterpolator(self.retention_time, self.log_molecular_weight, extrapolate=True)

    def evaluate_log_m(
        self, retention_time: npt.ArrayLike, *, allow_extrapolation: bool = False
    ) -> FloatArray:
        values = _as_float_array(retention_time)
        _guard_range(values, self.valid_rt_min, self.valid_rt_max, allow_extrapolation)
        return np.asarray(self._interpolator()(values), dtype=np.float64)

    def evaluate_m(
        self, retention_time: npt.ArrayLike, *, allow_extrapolation: bool = False
    ) -> FloatArray:
        return np.asarray(
            np.power(
                10.0,
                self.evaluate_log_m(retention_time, allow_extrapolation=allow_extrapolation),
            ),
            dtype=np.float64,
        )

    def derivative(self, retention_time: npt.ArrayLike) -> FloatArray:
        return np.asarray(self._interpolator().derivative()(_as_float_array(retention_time)))

    def invert_log_m(self, log_m: float) -> float:
        if not math.isfinite(log_m):
            raise AnalysisError("non_finite_input", "log molecular weight must be finite")

        def residual(retention_time: float) -> float:
            return float(self._interpolator()(retention_time) - log_m)

        left = residual(self.valid_rt_min)
        right = residual(self.valid_rt_max)
        endpoint_tolerance = max(1e-12, abs(log_m) * 1e-12)
        if abs(left) <= endpoint_tolerance:
            return self.valid_rt_min
        if abs(right) <= endpoint_tolerance:
            return self.valid_rt_max
        if left * right > 0:
            raise AnalysisError(
                "inverse_out_of_range", "log molecular weight is outside calibration"
            )
        return float(brentq(residual, self.valid_rt_min, self.valid_rt_max))


def fit_polynomial(
    points: tuple[CalibrationPoint, ...],
    degree: int = 3,
    source_sha256: str | None = None,
    *,
    require_monotonic: bool = True,
) -> PolynomialCalibration:
    enabled = [point for point in points if point.enabled]
    if degree < 0:
        raise ValueError("polynomial degree must be nonnegative")
    if len(enabled) <= degree:
        raise AnalysisError(
            "insufficient_calibration_points", "more points than degree are required"
        )
    x = np.asarray([point.retention_time_min for point in enabled], dtype=np.float64)
    y = np.log10(np.asarray([point.molecular_weight for point in enabled], dtype=np.float64))
    if len(np.unique(x)) != len(x):
        raise AnalysisError("duplicate_calibration_rt", "calibration RT values must be unique")
    order = np.argsort(x)
    x = x[order]
    y = y[order]
    if degree == 0:
        constant = float(np.mean(y))
        residual = y - constant
        sse = float(np.dot(residual, residual))
        statistics = CalibrationStatistics(
            sse=sse,
            corrected_sum_squares=sse,
            r_squared=None if sse == 0.0 else 0.0,
            rmse=math.sqrt(sse / len(x)),
            residual_standard_error=math.sqrt(sse / (len(x) - 1)) if len(x) > 1 else None,
            point_count=len(x),
            parameter_count=1,
        )
        return PolynomialCalibration(
            (constant,),
            (constant,),
            (float(x[0]), float(x[-1])),
            float(x[0]),
            float(x[-1]),
            statistics,
            0,
            source_sha256,
            "polynomial-constant-lstsq-v1",
        )
    fitted_scaled = Polynomial.fit(x, y, degree)
    fitted_original = fitted_scaled.convert()
    predicted = np.asarray(fitted_original(x), dtype=np.float64)
    residual = y - predicted
    sse = float(np.dot(residual, residual))
    centered = y - float(np.mean(y))
    corrected_sum = float(np.dot(centered, centered))
    if corrected_sum <= 0 and require_monotonic:
        raise AnalysisError(
            "constant_calibration_response", "calibration log M values are constant"
        )
    parameter_count = degree + 1
    statistics = CalibrationStatistics(
        sse=sse,
        corrected_sum_squares=corrected_sum,
        r_squared=1.0 - sse / corrected_sum if corrected_sum > 0 else None,
        rmse=math.sqrt(sse / len(x)),
        residual_standard_error=(
            math.sqrt(sse / (len(x) - parameter_count)) if len(x) > parameter_count else None
        ),
        point_count=len(x),
        parameter_count=parameter_count,
    )
    grid = np.linspace(float(x[0]), float(x[-1]), 1025, dtype=np.float64)
    direction = _monotonic_direction(
        np.asarray(fitted_original.deriv()(grid), dtype=np.float64),
        require_monotonic=require_monotonic,
    )
    domain = tuple(float(value) for value in fitted_scaled.domain)
    return PolynomialCalibration(
        coefficients=tuple(float(value) for value in fitted_original.coef),
        scaled_coefficients=tuple(float(value) for value in fitted_scaled.coef),
        fit_domain=(domain[0], domain[1]),
        valid_rt_min=float(x[0]),
        valid_rt_max=float(x[-1]),
        statistics=statistics,
        monotonic_direction=direction,
        source_sha256=source_sha256,
    )


def fit_pchip(
    points: tuple[CalibrationPoint, ...], source_sha256: str | None = None
) -> PchipCalibration:
    enabled = sorted(
        (point for point in points if point.enabled), key=lambda point: point.retention_time_min
    )
    if len(enabled) < 2:
        raise AnalysisError("insufficient_calibration_points", "PCHIP requires at least two points")
    x = np.asarray([point.retention_time_min for point in enabled], dtype=np.float64)
    y = np.log10(np.asarray([point.molecular_weight for point in enabled], dtype=np.float64))
    if np.any(np.diff(x) <= 0):
        raise AnalysisError(
            "duplicate_calibration_rt", "PCHIP RT values must be strictly increasing"
        )
    interpolator = PchipInterpolator(x, y)
    grid = np.linspace(float(x[0]), float(x[-1]), 1025, dtype=np.float64)
    direction = _monotonic_direction(np.asarray(interpolator.derivative()(grid), dtype=np.float64))
    return PchipCalibration(
        retention_time=tuple(float(value) for value in x),
        log_molecular_weight=tuple(float(value) for value in y),
        valid_rt_min=float(x[0]),
        valid_rt_max=float(x[-1]),
        monotonic_direction=direction,
        source_sha256=source_sha256,
    )
