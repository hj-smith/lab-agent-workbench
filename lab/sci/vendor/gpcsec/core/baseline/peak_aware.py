"""Deterministic, review-only peak-aware automatic baseline recommendation."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

import numpy as np
import numpy.typing as npt
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks, peak_widths

from gpcsec.core.baseline.automatic import _arrays, estimate_baseline
from gpcsec.models import (
    AnalysisError,
    AutoBaselineSpec,
    AutoBaselineSuggestion,
    BaselineAlgorithm,
    BaselineCandidateScore,
    BaselinePeakWindow,
    PeakPolarity,
)

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]
SELECTOR_VERSION = 2


@dataclass(frozen=True, slots=True)
class _Segment:
    number: int
    time: FloatArray
    signal: FloatArray
    detrended: FloatArray


@dataclass(frozen=True, slots=True)
class _Detection:
    segment: int
    center: float
    polarity: PeakPolarity
    prominence: float
    start: float
    end: float
    fwhm: float
    source: str
    edge: bool


@dataclass(frozen=True, slots=True)
class _PeakRisk:
    absorption: float
    area_absorption: float
    prominence_retention: float
    area_retention: float
    crossing: float
    reverse_area: float
    shoulders: bool


def _mad_sigma(values: FloatArray) -> float:
    if not values.size:
        return 0.0
    median = float(np.median(values))
    return float(np.median(np.abs(values - median))) / 0.6744897501960817


def _prepare_segments(time: FloatArray, signal: FloatArray) -> tuple[_Segment, ...]:
    steps = np.diff(time)
    median = float(np.median(steps))
    mad = float(np.median(np.abs(steps - median)))
    breaks = np.flatnonzero(steps > max(4.0 * median, median + 8.0 * mad)) + 1
    bounds = (0, *(int(value) for value in breaks), len(time))
    output: list[_Segment] = []
    for number, (left, right) in enumerate(pairwise(bounds)):
        if right - left < 4:
            continue
        original_time = time[left:right]
        uniform_time = np.linspace(original_time[0], original_time[-1], right - left)
        uniform_signal = np.interp(uniform_time, original_time, signal[left:right])
        unit_time = (uniform_time - uniform_time[0]) / (uniform_time[-1] - uniform_time[0])
        included = np.ones(len(unit_time), dtype=np.bool_)
        linear = np.zeros(len(unit_time), dtype=np.float64)
        for _iteration in range(4):
            fitted = np.polynomial.Polynomial.fit(unit_time[included], uniform_signal[included], 1)
            linear = np.asarray(fitted(unit_time), dtype=np.float64)
            residual = uniform_signal - linear
            included = np.abs(residual - np.median(residual)) <= np.quantile(
                np.abs(residual - np.median(residual)), 0.65
            )
        output.append(
            _Segment(
                number,
                uniform_time,
                uniform_signal,
                np.asarray(uniform_signal - linear, dtype=np.float64),
            )
        )
    return tuple(output)


def _noise_sigma(segments: tuple[_Segment, ...]) -> float:
    differences = []
    for segment in segments:
        trend = gaussian_filter1d(
            segment.signal,
            max(5.0, len(segment.signal) / 35.0),
            mode="nearest",
        )
        differences.append(np.diff(segment.signal - trend))
    joined = np.concatenate(differences) if differences else np.asarray([], dtype=np.float64)
    sigma = _mad_sigma(joined) / np.sqrt(2.0)
    if sigma <= np.finfo(float).eps:
        sigma = _mad_sigma(np.concatenate([item.detrended for item in segments]))
    return float(max(sigma, np.finfo(float).eps))


def _return_boundary(
    signed: FloatArray, start: int, direction: int, threshold: float
) -> tuple[int, bool]:
    count = 0
    index = start
    while 0 <= index < len(signed):
        count = count + 1 if signed[index] <= threshold else 0
        if count == 3:
            return max(0, min(index - 2 * direction, len(signed) - 1)), True
        index += direction
    return (0 if direction < 0 else len(signed) - 1), False


def _scale_detections(
    segment: _Segment,
    smooth: FloatArray,
    scale: float,
    source: str,
    noise: float,
    prominence_sigma: float,
    single_sigma: float,
) -> list[_Detection]:
    output: list[_Detection] = []
    for polarity, signed in (
        (PeakPolarity.POSITIVE, smooth),
        (PeakPolarity.NEGATIVE, -smooth),
    ):
        peaks, properties = find_peaks(
            signed,
            prominence=prominence_sigma * noise,
            distance=max(2, round(scale)),
        )
        if peaks.size:
            widths = peak_widths(signed, peaks, rel_height=0.5)
            for peak, prominence, half_left, half_right in zip(
                peaks, properties["prominences"], widths[2], widths[3], strict=True
            ):
                left, left_return = _return_boundary(
                    signed, int(np.floor(half_left)), -1, 2.0 * noise
                )
                right, right_return = _return_boundary(
                    signed, int(np.ceil(half_right)), 1, 2.0 * noise
                )
                fwhm = float(
                    np.interp(half_right, np.arange(len(signed)), segment.time)
                    - np.interp(half_left, np.arange(len(signed)), segment.time)
                )
                output.append(
                    _Detection(
                        segment.number,
                        float(segment.time[int(peak)]),
                        polarity,
                        float(prominence),
                        float(segment.time[left]),
                        float(segment.time[right]),
                        max(fwhm, np.finfo(float).eps),
                        source,
                        not left_return or not right_return,
                    )
                )
        for endpoint, direction in ((0, 1), (len(signed) - 1, -1)):
            probe = len(signed) // 5 if direction > 0 else len(signed) - 1 - len(signed) // 5
            radius = max(2, len(signed) // 50)
            reference = float(
                np.median(signed[max(0, probe - radius) : min(len(signed), probe + radius + 1)])
            )
            raw_signed = segment.signal if polarity is PeakPolarity.POSITIVE else -segment.signal
            raw_reference = float(
                np.median(
                    raw_signed[max(0, probe - radius) : min(len(raw_signed), probe + radius + 1)]
                )
            )
            edge_prominence = float(raw_signed[endpoint] - raw_reference)
            nearby = (
                signed[: max(4, len(signed) // 30)]
                if direction > 0
                else signed[-max(4, len(signed) // 30) :]
            )
            edge_floor = max(single_sigma * noise, 0.05 * float(np.ptp(segment.signal)))
            if (
                edge_prominence < edge_floor
                or signed[endpoint] < np.max(nearby) - noise
                or raw_signed[endpoint] < np.quantile(raw_signed, 0.98)
            ):
                continue
            boundary, returned = _return_boundary(signed, endpoint, direction, 2.0 * noise)
            left, right = sorted((endpoint, boundary))
            if right <= left:
                continue
            half_level = reference + 0.5 * max(float(signed[endpoint] - reference), noise)
            path = range(left, right + 1) if direction > 0 else range(right, left - 1, -1)
            half = next((index for index in path if signed[index] <= half_level), boundary)
            fwhm = float(2.0 * abs(float(segment.time[half] - segment.time[endpoint])))
            output.append(
                _Detection(
                    segment.number,
                    float(segment.time[endpoint]),
                    polarity,
                    edge_prominence,
                    float(segment.time[left]),
                    float(segment.time[right]),
                    max(fwhm, float(np.median(np.diff(segment.time)))),
                    source,
                    True if endpoint in {0, len(signed) - 1} else not returned,
                )
            )
    return output


def detect_baseline_peak_windows(
    time_min: npt.ArrayLike,
    signal: npt.ArrayLike,
    *,
    prominence_sigma: float = 6.0,
    single_evidence_sigma: float = 8.0,
) -> tuple[tuple[BaselinePeakWindow, ...], float]:
    """Detect peak windows on gap-separated, temporary uniform-RT segments."""
    if prominence_sigma <= 0 or single_evidence_sigma < prominence_sigma:
        raise ValueError("peak detection sigma thresholds are inconsistent")
    time, values = _arrays(time_min, signal)
    segments = _prepare_segments(time, values)
    noise = _noise_sigma(segments)
    raw: list[_Detection] = []
    for segment in segments:
        size = len(segment.signal)
        scales = tuple(
            sorted({max(1.0, size / 500.0), max(2.0, size / 180.0), max(4.0, size / 65.0)})
        )
        for index, scale in enumerate(scales):
            smooth = gaussian_filter1d(segment.detrended, scale, mode="nearest")
            raw.extend(
                _scale_detections(
                    segment,
                    smooth,
                    scale,
                    ("narrow", "medium", "wide")[index],
                    noise,
                    prominence_sigma,
                    single_evidence_sigma,
                )
            )
    clusters: list[list[_Detection]] = []
    for item in sorted(raw, key=lambda value: (value.segment, value.center, value.polarity.value)):
        match = next(
            (
                cluster
                for cluster in reversed(clusters)
                if cluster[0].segment == item.segment
                and cluster[0].polarity == item.polarity
                and abs(item.center - float(np.median([entry.center for entry in cluster])))
                <= 0.35 * max(entry.end - entry.start for entry in (*cluster, item))
            ),
            None,
        )
        (clusters.append([item]) if match is None else match.append(item))
    staged: list[tuple[int, BaselinePeakWindow]] = []
    for cluster in clusters:
        sources = tuple(sorted({item.source for item in cluster}))
        strongest = max(cluster, key=lambda item: item.prominence)
        snr = strongest.prominence / noise
        if snr < (prominence_sigma if len(sources) >= 2 else single_evidence_sigma):
            continue
        staged.append(
            (
                strongest.segment,
                BaselinePeakWindow(
                    min(item.start for item in cluster),
                    max(item.end for item in cluster),
                    strongest.polarity,
                    strongest.center,
                    float(snr),
                    max(item.fwhm for item in cluster),
                    any(item.edge for item in cluster),
                    sources,
                ),
            )
        )
    merged: list[tuple[int, BaselinePeakWindow]] = []
    for segment_number, window in sorted(staged, key=lambda item: item[1].start_time_min):
        if merged:
            previous_segment, previous = merged[-1]
            gap = 0.15 * max(previous.fwhm_time_min, window.fwhm_time_min)
            if (
                segment_number == previous_segment
                and window.polarity == previous.polarity
                and window.start_time_min <= previous.end_time_min + gap
            ):
                chosen = previous if previous.prominence_snr >= window.prominence_snr else window
                merged[-1] = (
                    segment_number,
                    BaselinePeakWindow(
                        previous.start_time_min,
                        max(previous.end_time_min, window.end_time_min),
                        chosen.polarity,
                        chosen.center_time_min,
                        max(previous.prominence_snr, window.prominence_snr),
                        max(previous.fwhm_time_min, window.fwhm_time_min),
                        previous.edge_truncated or window.edge_truncated,
                        tuple(sorted(set(previous.sources) | set(window.sources))),
                    ),
                )
                continue
        merged.append((segment_number, window))
    return tuple(window for _segment, window in merged), noise


def merge_baseline_exclusions(
    automatic: tuple[BaselinePeakWindow, ...],
    manual: tuple[tuple[float, float], ...] = (),
) -> tuple[tuple[float, float], ...]:
    """Return the interval union; a manual exclusion is never narrowed or discarded."""
    if any(not np.isfinite((start, end)).all() or start >= end for start, end in manual):
        raise ValueError("manual baseline exclusions must be finite with start < end")
    intervals = [*manual, *((item.start_time_min, item.end_time_min) for item in automatic)]
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((float(start), float(end)))
    return tuple(merged)


def _candidate_specs(
    size: int, exclusions: tuple[tuple[float, float], ...], count: int
) -> tuple[AutoBaselineSpec, ...]:
    audit = {
        "selector_version": SELECTOR_VERSION,
        "detection_prominence_sigma": 6.0,
        "detection_single_evidence_sigma": 8.0,
        "detected_window_count": count,
    }
    specs = [
        AutoBaselineSpec(BaselineAlgorithm.POLYNOMIAL, {"degree": degree, **audit}, exclusions)
        for degree in range(4)
    ]
    specs.append(AutoBaselineSpec(BaselineAlgorithm.RUBBER_BAND, audit, exclusions))
    scale = (size / 1000.0) ** 4
    for algorithm in (BaselineAlgorithm.ASLS, BaselineAlgorithm.AIRPLS, BaselineAlgorithm.ARPLS):
        for reference in (1e4, 1e6):
            specs.append(
                AutoBaselineSpec(
                    algorithm,
                    {"lambda": reference * scale, "lambda_ref": reference, **audit},
                    exclusions,
                )
            )
    return tuple(specs)


def _mask(time: FloatArray, exclusions: tuple[tuple[float, float], ...]) -> BoolArray:
    clean = np.ones(len(time), dtype=np.bool_)
    for start, end in exclusions:
        clean[(time >= start) & (time <= end)] = False
    return clean


def _integral(time: FloatArray, values: FloatArray) -> float:
    return float(np.trapezoid(values, time)) if len(time) >= 2 else 0.0


def _peak_risk(
    time: FloatArray,
    signal: FloatArray,
    baseline: FloatArray,
    clean: BoolArray,
    window: BaselinePeakWindow,
    noise: float,
) -> _PeakRisk:
    indices = np.flatnonzero((time >= window.start_time_min) & (time <= window.end_time_min))
    left_all = np.flatnonzero(clean & (time < window.start_time_min))
    right_all = np.flatnonzero(clean & (time > window.end_time_min))
    if indices.size < 2 or left_all.size < 3 or right_all.size < 3:
        return _PeakRisk(0.0, 0.0, 1.0, 1.0, 0.0, 0.0, False)
    left = left_all[-min(5, left_all.size) :]
    right = right_all[: min(5, right_all.size)]
    shoulder_time = (float(np.median(time[left])), float(np.median(time[right])))
    shoulder_signal = (float(np.median(signal[left])), float(np.median(signal[right])))
    bridge = np.interp(time[indices], shoulder_time, shoulder_signal)
    sign = 1.0 if window.polarity is PeakPolarity.POSITIVE else -1.0
    expected_signed = sign * (signal[indices] - bridge)
    corrected_signed = sign * (signal[indices] - baseline[indices])
    expected = np.clip(expected_signed, 0.0, None)
    corrected = np.clip(corrected_signed, 0.0, None)
    span = float(np.ptp(time[indices]))
    expected_area = max(_integral(time[indices], expected), noise * span)
    area_retention = float(np.clip(_integral(time[indices], corrected) / expected_area, 0.0, 2.0))
    prominence_retention = float(
        np.clip(float(np.max(corrected)) / max(float(np.max(expected)), noise), 0.0, 2.0)
    )
    elevation = np.clip(sign * (baseline[indices] - bridge), 0.0, None)
    area_absorption = float(np.clip(_integral(time[indices], elevation) / expected_area, 0.0, 2.0))
    absorption = max(0.0, 1.0 - area_retention, 1.0 - prominence_retention, area_absorption)
    peak_support = expected > 2.0 * noise
    reverse = np.where(
        peak_support,
        np.clip(-corrected_signed - 2.0 * noise, 0.0, None),
        0.0,
    )
    reverse_area = float(np.clip(_integral(time[indices], reverse) / expected_area, 0.0, 2.0))
    return _PeakRisk(
        absorption,
        area_absorption,
        prominence_retention,
        area_retention,
        (
            float(np.mean(corrected_signed[peak_support] < -2.0 * noise))
            if np.any(peak_support)
            else 0.0
        ),
        reverse_area,
        True,
    )


def _clean_metrics(
    time: FloatArray,
    residual: FloatArray,
    clean: BoolArray,
    noise: float,
    signal_range: float,
) -> tuple[float, float, float, float]:
    clean_time, clean_residual = time[clean], residual[clean]
    residual_mad = max(_mad_sigma(clean_residual), noise)
    location = abs(float(np.median(clean_residual))) / residual_mad
    medians: list[tuple[float, float]] = []
    for block in np.array_split(np.arange(len(clean_time)), min(6, len(clean_time))):
        if len(block) >= 2:
            medians.append(
                (float(np.median(clean_time[block])), float(np.median(clean_residual[block])))
            )
    block_values = np.asarray([value for _point, value in medians], dtype=np.float64)
    block_drift = float(np.ptp(block_values)) / noise if block_values.size >= 2 else 0.0
    if len(medians) >= 3:
        block_time = np.asarray([point for point, _value in medians], dtype=np.float64)
        quadratic = np.polynomial.Polynomial.fit(block_time, block_values, 2)(block_time)
        block_drift = max(block_drift, float(np.ptp(quadratic)) / noise)
    coefficients = np.polynomial.Polynomial.fit(clean_time, clean_residual, 1).convert().coef
    slope = float(coefficients[1]) if len(coefficients) > 1 else 0.0
    linear_drift = abs(slope) * (time[-1] - time[0]) / signal_range
    return location, residual_mad / noise, block_drift, linear_drift


def _score_candidate(
    time: FloatArray,
    signal: FloatArray,
    noise: float,
    windows: tuple[BaselinePeakWindow, ...],
    exclusions: tuple[tuple[float, float], ...],
    spec: AutoBaselineSpec,
) -> BaselineCandidateScore:
    preview = estimate_baseline(time, signal, spec)
    baseline = np.asarray(preview.result.baseline, dtype=np.float64)
    clean = _mask(time, exclusions)
    reasons: list[str] = []
    if not preview.diagnostics.converged:
        reasons.append("estimator_not_converged")
    if not np.all(np.isfinite(baseline)):
        reasons.append("non_finite_baseline")
    if np.count_nonzero(clean) < max(12, int(np.ceil(0.12 * len(time)))):
        reasons.append("insufficient_clean_domain")
    signal_range = max(float(np.ptp(signal)), noise)
    residual = signal - baseline
    if np.count_nonzero(clean) >= 3:
        location, mad_ratio, block_drift, linear_drift = _clean_metrics(
            time, residual, clean, noise, signal_range
        )
    else:
        location, mad_ratio, block_drift, linear_drift = 10.0, 10.0, 10.0, 1.0
    risks = tuple(_peak_risk(time, signal, baseline, clean, item, noise) for item in windows)
    absorption = max((item.absorption for item in risks), default=0.0)
    area_absorption = max((item.area_absorption for item in risks), default=0.0)
    prominence_retention = min((item.prominence_retention for item in risks), default=1.0)
    area_retention = min((item.area_retention for item in risks), default=1.0)
    crossing = max((item.crossing for item in risks), default=0.0)
    reverse = max((item.reverse_area for item in risks), default=0.0)
    shoulders = all(item.shoulders for item in risks)
    if any(
        window.prominence_snr >= 8.0 and risk.absorption > 0.25
        for window, risk in zip(windows, risks, strict=True)
    ):
        reasons.append("strong_peak_absorption_hard_limit")
    if crossing > 0.02:
        reasons.append("baseline_crossing_hard_limit")
    if reverse > 0.02:
        reasons.append("reverse_peak_area_hard_limit")
    first = np.gradient(baseline, time)
    second = np.gradient(first, time)
    curvature = (
        float(np.sqrt(np.mean(np.square(second)))) * (time[-1] - time[0]) ** 2 / signal_range
    )
    penalty = (
        0.34 * min(absorption / 0.25, 1.0)
        + 0.14 * min(crossing / 0.02, 1.0)
        + 0.12 * min(reverse / 0.02, 1.0)
        + 0.10 * min(location / 3.0, 1.0)
        + 0.08 * min(abs(mad_ratio - 1.0) / 3.0, 1.0)
        + 0.10 * min(block_drift / 6.0, 1.0)
        + 0.06 * min(curvature / 2.0, 1.0)
        + 0.06 * min(linear_drift / 0.30, 1.0)
    )
    return BaselineCandidateScore(
        preview=preview,
        peak_absorption=absorption,
        baseline_crossing=crossing,
        reverse_residual=reverse,
        curvature=curvature,
        clean_domain_drift=linear_drift,
        composite_score=float(np.clip(1.0 - penalty, 0.0, 1.0)),
        safe=not reasons,
        rejection_reasons=tuple(reasons),
        peak_area_absorption=area_absorption,
        prominence_retention=prominence_retention,
        area_retention=area_retention,
        clean_location_bias=location,
        clean_residual_mad_ratio=mad_ratio,
        clean_block_drift=block_drift,
        shoulder_supported=shoulders,
    )


def _coverage(time: FloatArray, intervals: tuple[tuple[float, float], ...]) -> float:
    covered = sum(
        max(0.0, min(end, float(time[-1])) - max(start, float(time[0]))) for start, end in intervals
    )
    return float(np.clip(covered / (time[-1] - time[0]), 0.0, 1.0))


def suggest_peak_aware_baseline(
    time_min: npt.ArrayLike,
    signal: npt.ArrayLike,
    manual_exclusions: tuple[tuple[float, float], ...] = (),
) -> AutoBaselineSuggestion:
    """Generate a screened draft; low-identifiability cases have no default."""
    time, values = _arrays(time_min, signal)
    windows, noise = detect_baseline_peak_windows(time, values)
    exclusions = merge_baseline_exclusions(windows, manual_exclusions)
    scored: list[BaselineCandidateScore] = []
    failures: list[str] = []
    for spec in _candidate_specs(len(time), exclusions, len(windows)):
        try:
            scored.append(_score_candidate(time, values, noise, windows, exclusions, spec))
        except (AnalysisError, ValueError, np.linalg.LinAlgError) as error:
            failures.append(f"{spec.algorithm.value}:{dict(spec.parameters)} failed: {error}")

    def key(item: BaselineCandidateScore) -> tuple[float, str, str]:
        return (
            -item.composite_score,
            item.spec.algorithm.value,
            str(dict(item.spec.parameters)),
        )

    safe = sorted((item for item in scored if item.safe), key=key)
    rejected = sorted((item for item in scored if not item.safe), key=key)
    best = safe[0] if safe else None
    separation = (
        best.composite_score - safe[1].composite_score
        if best is not None and len(safe) > 1
        else 0.0
    )
    confidence = (
        float(np.clip(0.85 * best.composite_score + 0.15 * min(separation / 0.15, 1.0), 0, 1))
        if best is not None
        else 0.0
    )
    if len(safe) == 1:
        confidence = max(0.0, confidence - 0.15)
    excluded = _coverage(time, exclusions)
    automatic = _coverage(time, tuple((item.start_time_min, item.end_time_min) for item in windows))
    widest = max((item.end_time_min - item.start_time_min for item in windows), default=0.0) / (
        time[-1] - time[0]
    )
    edge = any(item.edge_truncated for item in windows)
    missing_shoulders = bool(best is not None and not best.shoulder_supported)
    forced_none = excluded > 0.80 or automatic > 0.80
    low_cap = missing_shoulders or widest > 0.60 or excluded > 0.65 or automatic > 0.60
    medium_cap = not windows or edge or automatic > 0.40
    if forced_none:
        confidence = 0.0
    elif low_cap:
        confidence = min(confidence, 0.49)
    elif medium_cap:
        confidence = min(confidence, 0.79)
    if best is None or forced_none:
        level = "none"
    elif confidence >= 0.80:
        level = "high"
    elif confidence >= 0.55:
        level = "medium"
    else:
        level = "low"
    recommended_score = best if level in {"high", "medium"} else None
    warnings: list[str] = []
    if not windows:
        warnings.append("no peak exceeded threshold; confidence cannot claim peak protection")
    if edge:
        warnings.append("edge-truncated evidence caps confidence at medium")
    if missing_shoulders or widest > 0.60:
        warnings.append("missing shoulders or an ultra-wide structure caps confidence at low")
    if excluded > 0.65:
        warnings.append("effective exclusions leave insufficient baseline identifiability")
    if level in {"low", "none"}:
        warnings.append("no baseline was recommended; explicit expert review is required")
    decision_log = (
        f"selector_version={SELECTOR_VERSION}",
        f"noise_sigma={noise:.8g} after low-frequency removal and difference MAD",
        f"detected_peak_windows={len(windows)}; effective_exclusions={len(exclusions)}",
        f"automatic_coverage={automatic:.4f}; exclusion_coverage={excluded:.4f}",
        (
            f"evaluated={len(scored)}; safe={len(safe)}; "
            f"rejected={len(rejected)}; failed={len(failures)}"
        ),
        f"confidence={confidence:.3f}; separation={separation:.3f}; level={level}",
        *failures,
    )
    return AutoBaselineSuggestion(
        windows,
        exclusions,
        tuple(safe[:3]),
        tuple(rejected),
        recommended_score.preview if recommended_score is not None else None,
        recommended_score,
        confidence,
        level,
        noise,
        decision_log,
        tuple(warnings),
    )
