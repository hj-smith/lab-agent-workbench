"""Pure curve-view operations that never modify formal analysis arrays."""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt
from scipy.integrate import trapezoid

from gpcsec.models import (
    AnalysisError,
    ComparisonResult,
    NormalizationCoordinate,
    NormalizationMode,
    ViewTransform,
)

FloatArray = npt.NDArray[np.float64]


def _arrays(time_min: npt.ArrayLike, signal: npt.ArrayLike) -> tuple[FloatArray, FloatArray]:
    time = np.asarray(time_min, dtype=np.float64)
    values = np.asarray(signal, dtype=np.float64)
    if time.ndim != 1 or time.shape != values.shape or len(time) < 2:
        raise AnalysisError("shape_mismatch", "curve arrays must be equal and one-dimensional")
    if not np.all(np.isfinite(time)) or not np.all(np.isfinite(values)):
        raise AnalysisError("non_finite_input", "curve arrays must be finite")
    if np.any(np.diff(time) <= 0):
        raise AnalysisError("non_increasing_time", "curve time must increase")
    return time, values


def _normalization_factor(
    time: FloatArray,
    signal: FloatArray,
    transform: ViewTransform,
    reference_value: float | None,
) -> float:
    if transform.normalization is NormalizationMode.NONE:
        return 1.0
    if transform.normalization is NormalizationMode.PEAK_HEIGHT:
        factor = float(np.max(np.abs(signal)))
    elif transform.normalization is NormalizationMode.INTERVAL_PEAK:
        if transform.normalization_interval is None:
            raise AnalysisError(
                "missing_normalization_interval", "interval peak normalization needs bounds"
            )
        start, end = transform.normalization_interval
        selected = (time >= start) & (time <= end)
        if not np.any(selected):
            raise AnalysisError(
                "empty_normalization_interval", "normalization interval has no points"
            )
        factor = float(np.max(np.abs(signal[selected])))
    elif transform.normalization is NormalizationMode.AT_TIME:
        if transform.normalization_time_min is None:
            raise AnalysisError(
                "missing_normalization_time", "point normalization needs a retention time"
            )
        point = transform.normalization_time_min
        if point < time[0] or point > time[-1]:
            raise AnalysisError(
                "normalization_time_out_of_range", "normalization time lies outside curve"
            )
        factor = abs(float(np.interp(point, time, signal)))
    elif transform.normalization is NormalizationMode.TOTAL_AREA:
        factor = float(trapezoid(np.abs(signal), time))
    elif transform.normalization is NormalizationMode.INTERVAL_AREA:
        if transform.normalization_interval is None:
            raise AnalysisError(
                "missing_normalization_interval", "interval normalization needs bounds"
            )
        start, end = transform.normalization_interval
        selected = (time >= start) & (time <= end)
        if np.count_nonzero(selected) < 2:
            raise AnalysisError(
                "empty_normalization_interval", "normalization interval has too few points"
            )
        factor = float(trapezoid(np.abs(signal[selected]), time[selected]))
    else:
        if reference_value is None or not math.isfinite(reference_value):
            raise AnalysisError("missing_reference_value", "reference normalization needs a value")
        factor = reference_value
    if factor <= np.finfo(np.float64).eps:
        raise AnalysisError("zero_normalization_factor", "curve normalization factor is zero")
    return factor


def apply_view_transform(
    time_min: npt.ArrayLike,
    signal: npt.ArrayLike,
    transform: ViewTransform,
    *,
    reference_value: float | None = None,
) -> tuple[FloatArray, FloatArray]:
    """Return independent display arrays; input arrays and analysis results stay untouched."""
    time, values = _arrays(time_min, signal)
    display_time = time.copy() + transform.x_shift_min
    normalization_time = (
        display_time
        if transform.normalization_coordinate is NormalizationCoordinate.DISPLAY_RT
        else time
    )
    factor = _normalization_factor(normalization_time, values, transform, reference_value)
    display_signal = values.copy() / factor
    display_signal = display_signal * transform.amplitude_scale + transform.y_shift
    return display_time, display_signal


def _common_grid(
    left_time: FloatArray, right_time: FloatArray
) -> tuple[FloatArray, tuple[str, ...]]:
    start = max(float(left_time[0]), float(right_time[0]))
    end = min(float(left_time[-1]), float(right_time[-1]))
    if start >= end:
        raise AnalysisError("no_curve_overlap", "curves have no common time range")
    points = max(
        int(np.count_nonzero((left_time >= start) & (left_time <= end))),
        int(np.count_nonzero((right_time >= start) & (right_time <= end))),
    )
    grid = np.linspace(start, end, max(points, 2), dtype=np.float64)
    warning = (
        () if np.array_equal(left_time, right_time) else ("curves interpolated to a common grid",)
    )
    return grid, warning


def align_by_cross_correlation(
    reference_time: npt.ArrayLike,
    reference_signal: npt.ArrayLike,
    target_time: npt.ArrayLike,
    target_signal: npt.ArrayLike,
) -> float:
    """Return a robust x shift to add to the target curve.

    Scores are normalized separately at every lag and require substantial
    overlap.  This prevents long flat baselines, unequal sampling, truncated
    edges, or one extreme detector spike from dominating the alignment.  A
    light derivative term helps shoulders align without replacing the primary
    shape correlation.
    """
    ref_time, ref_signal = _arrays(reference_time, reference_signal)
    test_time, test_signal = _arrays(target_time, target_signal)
    start = max(float(ref_time[0]), float(test_time[0]))
    end = min(float(ref_time[-1]), float(test_time[-1]))
    if start >= end:
        raise AnalysisError("no_curve_overlap", "curves have no common time range")
    available_points = max(
        int(np.count_nonzero((ref_time >= start) & (ref_time <= end))),
        int(np.count_nonzero((test_time >= start) & (test_time <= end))),
    )
    grid = np.linspace(start, end, min(max(available_points, 64), 4096), dtype=np.float64)
    reference = np.interp(grid, ref_time, ref_signal)
    target = np.interp(grid, test_time, test_signal)

    def robust_trace(values: FloatArray) -> FloatArray:
        edge_count = max(3, len(values) // 12)
        left = float(np.median(values[:edge_count]))
        right = float(np.median(values[-edge_count:]))
        detrended = values - np.linspace(left, right, len(values), dtype=np.float64)
        detrended -= float(np.median(detrended))
        scale = 1.4826 * float(np.median(np.abs(detrended)))
        if not np.isfinite(scale) or scale <= np.finfo(np.float64).eps:
            scale = float(np.std(detrended))
        if not np.isfinite(scale) or scale <= np.finfo(np.float64).eps:
            return np.zeros_like(detrended)
        # Detector overload spikes remain visible but cannot determine the lag
        # on their own.
        return np.clip(detrended / scale, -8.0, 8.0)

    reference = robust_trace(reference)
    target = robust_trace(target)
    if np.linalg.norm(reference) <= 1e-12 or np.linalg.norm(target) <= 1e-12:
        raise AnalysisError("flat_alignment_curve", "cross-correlation needs non-flat curves")

    reference_gradient = robust_trace(np.gradient(reference))
    target_gradient = robust_trace(np.gradient(target))

    def normalized_score(left: FloatArray, right: FloatArray) -> float:
        left = left - float(np.mean(left))
        right = right - float(np.mean(right))
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        return float(np.dot(left, right) / denominator) if denominator > 1e-12 else -1.0

    point_count = len(grid)
    max_lag = max(1, round(point_count * 0.30))
    minimum_overlap = max(32, round(point_count * 0.65))
    lags: list[int] = []
    scores: list[float] = []
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            ref_slice = slice(0, lag)
            target_slice = slice(-lag, None)
        elif lag > 0:
            ref_slice = slice(lag, None)
            target_slice = slice(0, -lag)
        else:
            ref_slice = slice(None)
            target_slice = slice(None)
        overlap = len(reference[ref_slice])
        if overlap < minimum_overlap:
            continue
        shape_score = normalized_score(reference[ref_slice], target[target_slice])
        gradient_score = normalized_score(
            reference_gradient[ref_slice], target_gradient[target_slice]
        )
        lags.append(lag)
        scores.append(0.82 * shape_score + 0.18 * gradient_score)
    if not scores:
        raise AnalysisError("insufficient_alignment_overlap", "alignment overlap is too small")

    best_index = int(np.argmax(scores))
    best_lag = float(lags[best_index])
    # Parabolic refinement gives sub-sample shifts without changing the
    # deterministic lag search.
    if 0 < best_index < len(scores) - 1:
        left_score, center_score, right_score = scores[best_index - 1 : best_index + 2]
        curvature = left_score - 2.0 * center_score + right_score
        if abs(curvature) > 1e-12:
            offset = 0.5 * (left_score - right_score) / curvature
            best_lag += float(np.clip(offset, -0.5, 0.5))
    spacing = float(grid[1] - grid[0])
    return best_lag * spacing


def compare_curves(
    left_time_min: npt.ArrayLike,
    left_signal: npt.ArrayLike,
    right_time_min: npt.ArrayLike,
    right_signal: npt.ArrayLike,
    *,
    kind: str = "difference",
) -> ComparisonResult:
    left_time, left = _arrays(left_time_min, left_signal)
    right_time, right = _arrays(right_time_min, right_signal)
    grid, interpolation_warnings = _common_grid(left_time, right_time)
    left_common = np.interp(grid, left_time, left)
    right_common = np.interp(grid, right_time, right)
    warnings = list(interpolation_warnings)
    if kind == "difference":
        values = left_common - right_common
    elif kind == "ratio":
        threshold = np.finfo(np.float64).eps * max(1.0, float(np.max(np.abs(right_common))))
        valid = np.abs(right_common) > threshold
        values = np.full(len(grid), np.nan, dtype=np.float64)
        values[valid] = left_common[valid] / right_common[valid]
        if not np.all(valid):
            warnings.append("ratio masked where denominator is zero or numerically negligible")
    else:
        raise ValueError("comparison kind must be 'difference' or 'ratio'")
    return ComparisonResult(
        kind,
        tuple(float(value) for value in grid),
        tuple(float(value) for value in values),
        "linear",
        tuple(warnings),
    )
