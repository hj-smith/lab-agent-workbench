"""Shape-independent diagnostics for chromatographic peak models."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import numpy.typing as npt
from scipy.integrate import cumulative_trapezoid, trapezoid

from gpcsec.core.calibration import Calibration
from gpcsec.core.peak_fitting.constraints import ComponentConstraintContext
from gpcsec.core.peak_fitting.shapes import peak_fwhm, peak_height
from gpcsec.models import (
    PeakComponentDiagnostics,
    PeakComponentResult,
    PeakComponentSpec,
    PeakFitConstraints,
    PeakFitDiagnostics,
    ResidualDiagnostics,
)

FloatArray = npt.NDArray[np.float64]


def _central_area_interval(
    time: FloatArray,
    curve: FloatArray,
    retained_fraction: float = 0.90,
) -> tuple[float, float] | None:
    """Return the central interval containing a requested component area."""

    if len(time) < 2 or not 0.0 < retained_fraction < 1.0:
        return None
    nonnegative = np.asarray(np.maximum(curve, 0.0), dtype=np.float64)
    cumulative = cumulative_trapezoid(nonnegative, time, initial=0)
    total = float(cumulative[-1])
    if not math.isfinite(total) or total <= 0:
        return None
    cumulative /= total
    tail = (1.0 - retained_fraction) / 2.0
    lower = float(np.interp(tail, cumulative, time))
    upper = float(np.interp(1.0 - tail, cumulative, time))
    return lower, upper


def robust_noise_sigma(signal: npt.ArrayLike) -> float:
    """Estimate point noise from first differences using a robust MAD scale."""

    values = np.asarray(signal, dtype=np.float64)
    finite = values[np.isfinite(values)]
    if finite.size < 3:
        return float(np.finfo(np.float64).eps)
    differences = np.diff(finite)
    median = float(np.median(differences))
    mad = float(np.median(np.abs(differences - median)))
    estimate = 1.4826 * mad / math.sqrt(2.0)
    if estimate <= np.finfo(np.float64).eps:
        centered = finite - float(np.median(finite))
        estimate = 1.4826 * float(np.median(np.abs(centered)))
    return max(float(estimate), float(np.finfo(np.float64).eps))


def residual_diagnostics(
    residual: npt.ArrayLike,
    noise_sigma: float,
) -> ResidualDiagnostics:
    """Detect simple sign runs and serial structure without claiming significance."""

    values = np.asarray(residual, dtype=np.float64)
    values = values[np.isfinite(values)]
    if not len(values):
        return ResidualDiagnostics(noise_sigma, 0, 0.0, 0, None, None, False)
    tolerance = max(noise_sigma * 0.1, np.finfo(np.float64).eps)
    signs = np.sign(values)
    signs[np.abs(values) <= tolerance] = 0.0
    nonzero = signs[signs != 0]
    if not len(nonzero):
        return ResidualDiagnostics(noise_sigma, 0, 0.0, 0, None, None, False)
    changes = np.flatnonzero(nonzero[1:] != nonzero[:-1]) + 1
    boundaries = np.concatenate(([0], changes, [len(nonzero)]))
    run_lengths = np.diff(boundaries)
    runs = len(run_lengths)
    positive = int(np.count_nonzero(nonzero > 0))
    negative = int(np.count_nonzero(nonzero < 0))
    expected = 1.0 + (2.0 * positive * negative / max(positive + negative, 1))
    longest = int(np.max(run_lengths)) if len(run_lengths) else 0
    lag1: float | None = None
    durbin_watson: float | None = None
    centered = values - float(np.mean(values))
    denominator = float(np.dot(centered, centered))
    if len(values) > 1 and denominator > 0:
        lag1 = float(np.dot(centered[:-1], centered[1:]) / denominator)
        durbin_watson = float(np.sum(np.diff(values) ** 2) / denominator)
    run_warning = longest >= max(8, round(len(nonzero) * 0.08))
    serial_warning = lag1 is not None and abs(lag1) >= 0.45
    warnings: list[str] = []
    if run_warning:
        warnings.append("long same-sign residual run suggests local model mismatch")
    if serial_warning:
        warnings.append("residuals show strong adjacent-point correlation")
    return ResidualDiagnostics(
        noise_sigma,
        runs,
        expected,
        longest,
        lag1,
        durbin_watson,
        run_warning or serial_warning,
        tuple(warnings),
    )


def information_criteria(
    rss: float,
    observation_count: int,
    parameter_count: int,
    r_squared: float | None,
) -> tuple[float | None, float, float, float | None]:
    """Return adjusted R², AIC, AICc, and BIC from the unpenalized data RSS."""

    safe_rss = max(float(rss), float(np.finfo(np.float64).tiny))
    count = int(observation_count)
    parameters = int(parameter_count)
    aic = count * math.log(safe_rss / count) + 2.0 * parameters
    correction_denominator = count - parameters - 1
    aicc = (
        aic + (2.0 * parameters * (parameters + 1)) / correction_denominator
        if correction_denominator > 0
        else math.inf
    )
    bic = count * math.log(safe_rss / count) + parameters * math.log(count)
    adjusted: float | None = None
    if r_squared is not None and math.isfinite(r_squared) and count > parameters + 1:
        adjusted = 1.0 - (1.0 - r_squared) * (count - 1) / (count - parameters - 1)
    return adjusted, float(aic), float(aicc), float(bic)


def jacobian_identifiability(jacobian: npt.ArrayLike) -> tuple[float | None, float | None]:
    """Return condition number and maximum absolute off-diagonal correlation."""

    matrix = np.asarray(jacobian, dtype=np.float64)
    if matrix.ndim != 2 or not matrix.size or matrix.shape[1] < 1:
        return None, None
    information = matrix.T @ matrix
    condition = float(np.linalg.cond(information))
    covariance = np.linalg.pinv(information)
    scale = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    denominator = np.outer(scale, scale)
    correlation = np.divide(
        covariance,
        denominator,
        out=np.zeros_like(covariance),
        where=denominator > 0,
    )
    np.fill_diagonal(correlation, 0.0)
    maximum = float(np.max(np.abs(correlation))) if correlation.size > 1 else 0.0
    return condition if math.isfinite(condition) else math.inf, maximum


def strong_parameter_correlations(
    jacobian: npt.ArrayLike,
    parameter_keys: Sequence[str],
    *,
    threshold: float = 0.95,
) -> tuple[tuple[str, str, float], ...]:
    """List strongly correlated fitted parameters for explainable diagnostics."""

    matrix = np.asarray(jacobian, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != len(parameter_keys) or not matrix.size:
        return ()
    information = matrix.T @ matrix
    covariance = np.linalg.pinv(information)
    scale = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    denominator = np.outer(scale, scale)
    correlation = np.divide(
        covariance,
        denominator,
        out=np.zeros_like(covariance),
        where=denominator > 0,
    )
    output: list[tuple[str, str, float]] = []
    for first in range(len(parameter_keys)):
        for second in range(first + 1, len(parameter_keys)):
            value = float(correlation[first, second])
            if math.isfinite(value) and abs(value) >= threshold:
                output.append((parameter_keys[first], parameter_keys[second], value))
    return tuple(sorted(output, key=lambda item: abs(item[2]), reverse=True))


def _molecular_weight_evidence(
    time: FloatArray,
    curve: FloatArray,
    center: float,
    fwhm: float,
    component: PeakComponentSpec,
    calibration: Calibration | None,
) -> tuple[float | None, float | None, float | None, float | None, float | None, float | None]:
    if calibration is None:
        return (None, None, None, None, None, None)
    try:
        center_log_m = float(
            calibration.evaluate_log_m(np.asarray([center]), allow_extrapolation=False)[0]
        )
    except (ArithmeticError, ValueError):
        return (None, None, None, None, None, None)
    center_m = 10.0**center_log_m
    left = center - fwhm / 2.0
    right = center + fwhm / 2.0
    fwhm_log_m: float | None = None
    mw_low: float | None = None
    mw_high: float | None = None
    try:
        endpoints = calibration.evaluate_log_m(np.asarray([left, right]), allow_extrapolation=False)
    except (ArithmeticError, ValueError):
        pass
    else:
        fwhm_log_m = abs(float(endpoints[1] - endpoints[0]))
        molecular = np.power(10.0, endpoints)
        mw_low = float(np.min(molecular))
        mw_high = float(np.max(molecular))
    localization: float | None = None
    if component.molecular_weight_bounds is not None:
        valid = (time >= calibration.valid_rt_min) & (time <= calibration.valid_rt_max)
        if np.count_nonzero(valid) >= 2:
            log_m = calibration.evaluate_log_m(time[valid], allow_extrapolation=False)
            molecular = np.power(10.0, log_m)
            lower, upper = component.molecular_weight_bounds
            local = (molecular >= lower) & (molecular <= upper)
            total_area = float(trapezoid(curve, time))
            if total_area > 0:
                valid_time = time[valid]
                valid_curve = curve[valid]
                masked_curve = np.where(local, valid_curve, 0.0)
                localization = float(trapezoid(masked_curve, valid_time) / total_area)
                localization = min(max(localization, 0.0), 1.0)
    return center_log_m, center_m, fwhm_log_m, mw_low, mw_high, localization


def component_diagnostics(
    time: npt.ArrayLike,
    curves: Sequence[npt.ArrayLike],
    specifications: Sequence[PeakComponentSpec],
    results: Sequence[PeakComponentResult],
    contexts: Sequence[ComponentConstraintContext],
    noise_sigma: float,
    policy: PeakFitConstraints | None,
    calibration: Calibration | None = None,
) -> tuple[PeakComponentDiagnostics, ...]:
    """Calculate common FWHM, localization, overlap, SNR, and flags."""

    x = np.asarray(time, dtype=np.float64)
    curve_arrays = [np.asarray(curve, dtype=np.float64) for curve in curves]
    fwhms = [
        peak_fwhm(spec.shape, result.parameters)
        for spec, result in zip(specifications, results, strict=True)
    ]
    heights = [
        peak_height(spec.shape, result.parameters)
        for spec, result in zip(specifications, results, strict=True)
    ]
    maximum_height = max(heights, default=0.0)
    centers = [float(result.parameters["center"]) for result in results]
    overlaps: list[float | None] = []
    for index, center in enumerate(centers):
        distances = [
            abs(center - other_center) / max((fwhms[index] + fwhms[other]) / 2.0, 1e-15)
            for other, other_center in enumerate(centers)
            if other != index
        ]
        overlaps.append(min(distances) if distances else None)
    output: list[PeakComponentDiagnostics] = []
    for index, (spec, result, context, curve, fwhm) in enumerate(
        zip(specifications, results, contexts, curve_arrays, fwhms, strict=True)
    ):
        height = heights[index]
        snr = height / noise_sigma if noise_sigma > 0 else None
        width_ratio = fwhm / context.fwhm_bounds[1] if context.fwhm_bounds[1] > 0 else None
        center_log_m, center_m, fwhm_log_m, mw_low, mw_high, localization = (
            _molecular_weight_evidence(
                x,
                curve,
                centers[index],
                fwhm,
                spec,
                calibration,
            )
        )
        area_90_time = _central_area_interval(x, curve)
        area_90_molecular: tuple[float, float] | None = None
        if calibration is not None and area_90_time is not None:
            try:
                log_endpoints = calibration.evaluate_log_m(
                    np.asarray(area_90_time, dtype=np.float64),
                    allow_extrapolation=False,
                )
            except (ArithmeticError, ValueError):
                pass
            else:
                molecular_endpoints = np.power(10.0, log_endpoints)
                area_90_molecular = (
                    float(np.min(molecular_endpoints)),
                    float(np.max(molecular_endpoints)),
                )
        if localization is None:
            inside = (x >= context.center_bounds[0]) & (x <= context.center_bounds[1])
            total_area = float(trapezoid(curve, x))
            localization = (
                float(trapezoid(np.where(inside, curve, 0.0), x) / total_area)
                if total_area > 0
                else None
            )
        flags: list[str] = []
        if policy is not None:
            if maximum_height > 0 and (
                height / maximum_height < policy.minimum_peak_height_relative
            ):
                flags.append("low_height")
            if fwhm <= context.fwhm_bounds[0] * 1.02:
                flags.append("too_narrow")
            if width_ratio is not None and width_ratio >= policy.soft_width_ratio:
                flags.append("broad")
            if snr is not None and snr < policy.minimum_peak_snr:
                flags.append("low_snr")
            if result.area_fraction < max(
                policy.minimum_peak_area_relative,
                spec.minimum_area_fraction or 0.0,
            ):
                flags.append("low_area")
            overlap = overlaps[index]
            if overlap is not None and overlap < policy.overlap_warning_distance:
                flags.append("strong_overlap")
            if localization is not None and localization < policy.localization_warning_fraction:
                flags.append("low_localization")
        if "center" in result.boundary_hits:
            flags.append("at_center_bound")
        if any(name in result.boundary_hits for name in ("width", "gamma", "tau")):
            flags.append("at_width_bound")
        if "eta" in result.boundary_hits:
            flags.append("shape_fraction_unstable")
        if "tau" in result.boundary_hits:
            flags.append("tail_parameter_unstable")
        if "broad" in flags and (
            "low_area" in flags or "low_snr" in flags or "low_height" in flags
        ):
            flags.append("baseline_like")
        output.append(
            PeakComponentDiagnostics(
                height,
                fwhm,
                snr,
                width_ratio,
                overlaps[index],
                localization,
                center_log_m,
                center_m,
                fwhm_log_m,
                mw_low,
                mw_high,
                area_90_time_min=area_90_time,
                area_90_molecular_weight=area_90_molecular,
                flags=tuple(dict.fromkeys(flags)),
            )
        )
    return tuple(output)


def fit_quality_labels(
    rmse: float,
    noise_sigma: float,
    residual: ResidualDiagnostics,
    components: Sequence[PeakComponentDiagnostics],
    condition_number: float | None,
) -> tuple[str, str, str, tuple[str, ...]]:
    """Keep numerical, statistical, and physical judgements visibly separate."""

    numerical = (
        "excellent"
        if rmse <= 1.5 * noise_sigma
        else "good"
        if rmse <= 3 * noise_sigma
        else "review"
    )
    statistical = "review" if residual.structured else "good"
    warnings = list(residual.warnings)
    physical_flags = tuple(flag for component in components for flag in component.flags)
    physical = "warning" if physical_flags else "good"
    if condition_number is not None and condition_number > 1e12:
        statistical = "review"
        warnings.append("parameter information matrix is ill-conditioned")
    if any(flag == "broad" for flag in physical_flags):
        warnings.append("one or more components occupy most of their allowed local window")
    return numerical, statistical, physical, tuple(dict.fromkeys(warnings))


def build_fit_diagnostics(
    *,
    adjusted_r_squared: float | None,
    aicc: float | None,
    residual: ResidualDiagnostics,
    condition_number: float | None,
    maximum_parameter_correlation: float | None,
    data_loss: float,
    width_penalty: float,
    center_penalty: float,
    overlap_penalty: float,
    rmse: float,
    components: Sequence[PeakComponentDiagnostics],
    warnings: Sequence[str] = (),
) -> PeakFitDiagnostics:
    numerical, statistical, physical, generated = fit_quality_labels(
        rmse,
        residual.noise_sigma,
        residual,
        components,
        condition_number,
    )
    all_warnings = tuple(dict.fromkeys((*warnings, *generated)))
    return PeakFitDiagnostics(
        adjusted_r_squared,
        aicc,
        residual,
        condition_number,
        maximum_parameter_correlation,
        data_loss,
        width_penalty,
        center_penalty,
        overlap_penalty,
        numerical,
        statistical,
        physical,
        all_warnings,
    )
