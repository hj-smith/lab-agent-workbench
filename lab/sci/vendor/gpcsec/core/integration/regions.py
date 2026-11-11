"""Multi-region integration without mutating source arrays."""

from __future__ import annotations

import math
from itertools import combinations, pairwise

import numpy as np
import numpy.typing as npt

from gpcsec.models import (
    AnalysisError,
    AreaBreakdown,
    IntegrationAreaSummary,
    IntegrationRegion,
    IntegrationResult,
    ProcessingStep,
    RegionIntegrationResult,
)

FloatArray = npt.NDArray[np.float64]


def _arrays(
    time_min: npt.ArrayLike, signal: npt.ArrayLike, baseline: npt.ArrayLike
) -> tuple[FloatArray, FloatArray, FloatArray]:
    time = np.asarray(time_min, dtype=np.float64)
    raw = np.asarray(signal, dtype=np.float64)
    base = np.asarray(baseline, dtype=np.float64)
    if time.ndim != 1 or time.shape != raw.shape or time.shape != base.shape or len(time) < 2:
        raise AnalysisError("shape_mismatch", "time, signal, and baseline must be equal 1D arrays")
    if (
        not np.all(np.isfinite(time))
        or not np.all(np.isfinite(raw))
        or not np.all(np.isfinite(base))
    ):
        raise AnalysisError("non_finite_input", "integration arrays must contain finite values")
    if np.any(np.diff(time) <= 0):
        raise AnalysisError("non_increasing_time", "time must be strictly increasing")
    return time, raw, base


def _slice_exact(
    time: FloatArray, arrays: tuple[FloatArray, ...], start: float, end: float
) -> tuple[FloatArray, ...]:
    if not time[0] <= start < end <= time[-1]:
        raise AnalysisError("region_out_of_data", "integration region lies outside time data")
    inner = (time > start) & (time < end)
    sliced_time = np.concatenate(([start], time[inner], [end])).astype(np.float64)
    output: list[FloatArray] = [sliced_time]
    for values in arrays:
        left = float(np.interp(start, time, values))
        right = float(np.interp(end, time, values))
        output.append(np.concatenate(([left], values[inner], [right])).astype(np.float64))
    return tuple(output)


def _trap(time: FloatArray, values: FloatArray) -> float:
    return math.fsum(
        0.5 * (float(left_y) + float(right_y)) * float(right_t - left_t)
        for (left_t, left_y), (right_t, right_y) in pairwise(zip(time, values, strict=True))
    )


def _included_segments(
    start: float, end: float, exclusions: tuple[tuple[float, float], ...]
) -> tuple[tuple[float, float], ...]:
    clipped = sorted(
        (max(start, left), min(end, right))
        for left, right in exclusions
        if right > start and left < end
    )
    for left, right in clipped:
        if left >= right:
            raise AnalysisError("invalid_exclusion", "exclusion start must be less than end")
    for previous, current in pairwise(clipped):
        if current[0] < previous[1]:
            raise AnalysisError("overlapping_exclusions", "exclusion regions must not overlap")
    boundaries = [start]
    for left, right in clipped:
        boundaries.extend((left, right))
    boundaries.append(end)
    segments: list[tuple[float, float]] = []
    for left, right in pairwise(boundaries):
        midpoint = 0.5 * (left + right)
        if left < right and not any(
            ex_left <= midpoint <= ex_right for ex_left, ex_right in clipped
        ):
            segments.append((left, right))
    return tuple(segments)


def _area_by_window(
    time: FloatArray,
    values: FloatArray,
    lower: float | None,
    upper: float | None,
) -> float:
    start = float(time[0]) if lower is None else max(float(time[0]), lower)
    end = float(time[-1]) if upper is None else min(float(time[-1]), upper)
    if start >= end:
        return 0.0
    sliced_time, sliced_values = _slice_exact(time, (values,), start, end)
    return _trap(sliced_time, sliced_values)


def _result_segments(
    region: RegionIntegrationResult,
) -> tuple[tuple[FloatArray, FloatArray], ...]:
    time = np.asarray(region.time_min, dtype=np.float64)
    signal = np.asarray(region.effective_signal, dtype=np.float64)
    segments: list[tuple[FloatArray, FloatArray]] = []
    offset = 0
    for length in region.segment_lengths:
        segment_time = time[offset : offset + length]
        segment_signal = signal[offset : offset + length]
        if len(segment_time) >= 2:
            segments.append((segment_time, segment_signal))
        offset += length
    if offset != len(time):
        raise AnalysisError("invalid_segment_metadata", "segment lengths do not match arrays")
    return tuple(segments)


def union_effective_segments(
    segments: tuple[tuple[FloatArray, FloatArray], ...],
) -> tuple[tuple[FloatArray, FloatArray], ...]:
    """Return the pointwise union envelope without double-counting overlapping intervals."""
    if not segments:
        return ()
    ordered = sorted(segments, key=lambda item: float(item[0][0]))
    groups: list[list[tuple[FloatArray, FloatArray]]] = []
    current: list[tuple[FloatArray, FloatArray]] = []
    current_end = -math.inf
    for time, signal in ordered:
        if time.ndim != 1 or signal.shape != time.shape or len(time) < 2:
            raise AnalysisError("shape_mismatch", "union segments must be equal 1D arrays")
        start = float(time[0])
        end = float(time[-1])
        if current and start > current_end:
            groups.append(current)
            current = []
            current_end = -math.inf
        current.append((time, signal))
        current_end = max(current_end, end)
    if current:
        groups.append(current)

    output: list[tuple[FloatArray, FloatArray]] = []
    for group in groups:
        if len(group) == 1:
            output.append(group[0])
            continue
        knots = np.unique(np.concatenate(tuple(time for time, _ in group))).astype(np.float64)
        for left, right in pairwise(knots):
            midpoint = 0.5 * float(left + right)
            active = [
                (time, signal)
                for time, signal in group
                if float(time[0]) <= midpoint <= float(time[-1])
            ]
            if not active:
                continue
            values = np.asarray(
                [
                    max(float(np.interp(boundary, time, signal)) for time, signal in active)
                    for boundary in (left, right)
                ],
                dtype=np.float64,
            )
            output.append((np.asarray((left, right), dtype=np.float64), values))
    return tuple(output)


def summarize_integration_areas(integration: IntegrationResult) -> IntegrationAreaSummary:
    """Summarize independent region areas and their de-duplicated pointwise union."""
    areas = tuple(region.areas.effective_nonnegative_area for region in integration.regions)
    independent = math.fsum(areas)
    segments = tuple(
        segment for region in integration.regions for segment in _result_segments(region)
    )
    union_area = math.fsum(
        _trap(time, signal) for time, signal in union_effective_segments(segments)
    )
    duplicate = max(0.0, independent - union_area)
    fractions = tuple(area / independent if independent > 0 else 0.0 for area in areas)
    overlap_pairs = tuple(
        (left_index, right_index)
        for (left_index, left), (right_index, right) in combinations(
            enumerate(integration.regions, start=1), 2
        )
        if max(left.region.start_time_min, right.region.start_time_min)
        < min(left.region.end_time_min, right.region.end_time_min)
    )
    return IntegrationAreaSummary(
        independent,
        union_area,
        duplicate,
        duplicate / independent if independent > 0 else 0.0,
        fractions,
        overlap_pairs,
    )


def integrate_regions(
    time_min: npt.ArrayLike,
    signal: npt.ArrayLike,
    baseline: npt.ArrayLike,
    regions: tuple[IntegrationRegion, ...],
    *,
    exclusions: tuple[tuple[float, float], ...] = (),
    calibration_bounds: tuple[float, float] | None = None,
    calibration_decreasing: bool = True,
    absolute_mode: bool = False,
) -> IntegrationResult:
    time, raw, base = _arrays(time_min, signal, baseline)
    if not regions:
        raise AnalysisError("no_integration_regions", "at least one region is required")
    outputs: list[RegionIntegrationResult] = []
    global_warnings: list[str] = []
    if any(
        max(left.start_time_min, right.start_time_min) < min(left.end_time_min, right.end_time_min)
        for left, right in combinations(regions, 2)
    ):
        global_warnings.append(
            "overlapping integration regions are evaluated independently; aggregate results use "
            "their de-duplicated union"
        )
    for region in regions:
        segments = _included_segments(region.start_time_min, region.end_time_min, exclusions)
        if not segments:
            raise AnalysisError("fully_excluded_region", "integration region is fully excluded")
        accumulated: dict[str, list[FloatArray]] = {
            "time": [],
            "raw": [],
            "baseline": [],
            "residual": [],
            "effective": [],
            "clipped": [],
        }
        totals = {
            key: 0.0
            for key in (
                "raw",
                "positive",
                "negative",
                "direction",
                "effective",
                "clipped",
                "absolute",
                "in",
                "high",
                "low",
            )
        }
        segment_lengths: list[int] = []
        for start, end in segments:
            segment_time, segment_raw, segment_base = _slice_exact(time, (raw, base), start, end)
            residual = segment_raw - segment_base
            direction = region.polarity.direction * residual
            effective = np.abs(residual) if absolute_mode else np.maximum(direction, 0.0)
            clipped = np.maximum(-direction, 0.0)
            accumulated["time"].append(segment_time)
            accumulated["raw"].append(segment_raw)
            accumulated["baseline"].append(segment_base)
            accumulated["residual"].append(residual)
            accumulated["effective"].append(effective)
            accumulated["clipped"].append(clipped)
            segment_lengths.append(len(segment_time))
            totals["raw"] += _trap(segment_time, segment_raw)
            totals["positive"] += _trap(segment_time, np.maximum(residual, 0.0))
            totals["negative"] += _trap(segment_time, np.maximum(-residual, 0.0))
            totals["direction"] += _trap(segment_time, direction)
            totals["effective"] += _trap(segment_time, effective)
            totals["clipped"] += _trap(segment_time, clipped)
            totals["absolute"] += _trap(segment_time, np.abs(residual))
            if calibration_bounds:
                lower, upper = calibration_bounds
                totals["in"] += _area_by_window(segment_time, effective, lower, upper)
                before = _area_by_window(segment_time, effective, None, lower)
                after = _area_by_window(segment_time, effective, upper, None)
                if calibration_decreasing:
                    totals["high"] += before
                    totals["low"] += after
                else:
                    totals["low"] += before
                    totals["high"] += after
            else:
                totals["in"] += _trap(segment_time, effective)

        excluded_area = 0.0
        for exclusion_start, exclusion_end in exclusions:
            start = max(region.start_time_min, exclusion_start)
            end = min(region.end_time_min, exclusion_end)
            if start < end:
                ex_time, ex_raw, ex_base = _slice_exact(time, (raw, base), start, end)
                ex_residual = ex_raw - ex_base
                ex_direction = region.polarity.direction * ex_residual
                ex_effective = (
                    np.abs(ex_residual) if absolute_mode else np.maximum(ex_direction, 0.0)
                )
                excluded_area += _trap(ex_time, ex_effective)

        warnings: list[str] = []
        if totals["high"] > 0 or totals["low"] > 0:
            warnings.append("integration region contains out-of-calibration effective area")
            global_warnings.append(warnings[-1])
        areas = AreaBreakdown(
            raw_signed_area=totals["raw"],
            positive_area_after_baseline=totals["positive"],
            negative_magnitude_after_baseline=totals["negative"],
            direction_corrected_area=totals["direction"],
            effective_nonnegative_area=totals["effective"],
            clipped_reverse_residual_area=totals["clipped"],
            excluded_area=excluded_area,
            in_calibration_area=totals["in"],
            out_of_calibration_high_m_area=totals["high"],
            out_of_calibration_low_m_area=totals["low"],
            absolute_area=totals["absolute"] if absolute_mode else None,
        )
        outputs.append(
            RegionIntegrationResult(
                region=region,
                time_min=tuple(float(value) for part in accumulated["time"] for value in part),
                raw_signal=tuple(float(value) for part in accumulated["raw"] for value in part),
                baseline=tuple(float(value) for part in accumulated["baseline"] for value in part),
                residual=tuple(float(value) for part in accumulated["residual"] for value in part),
                effective_signal=tuple(
                    float(value) for part in accumulated["effective"] for value in part
                ),
                clipped_reverse_signal=tuple(
                    float(value) for part in accumulated["clipped"] for value in part
                ),
                segment_lengths=tuple(segment_lengths),
                areas=areas,
                warnings=tuple(warnings),
            )
        )
    step = ProcessingStep(
        "integration.regions",
        "exact-boundary-trapezoid-v2",
        {
            "absolute_mode": absolute_mode,
            "exclusions": exclusions,
            "region_overlap_policy": "independent-with-deduplicated-union",
        },
    )
    return IntegrationResult(tuple(outputs), (step,), tuple(dict.fromkeys(global_warnings)))


def detect_polarities(
    signal: npt.ArrayLike, baseline: npt.ArrayLike | None = None
) -> tuple[str, ...]:
    values = np.asarray(signal, dtype=np.float64)
    if baseline is not None:
        values = values - np.asarray(baseline, dtype=np.float64)
    if values.ndim != 1 or len(values) < 3 or not np.all(np.isfinite(values)):
        raise AnalysisError("invalid_signal", "polarity detection requires a finite 1D signal")
    scale = max(float(np.max(values)), float(-np.min(values)), 1e-15)
    threshold = 0.05 * scale
    detected: list[str] = []
    if float(np.max(values)) > threshold:
        detected.append("positive")
    if float(-np.min(values)) > threshold:
        detected.append("negative")
    return tuple(detected)
