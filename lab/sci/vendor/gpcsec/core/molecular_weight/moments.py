"""Stable nonuniform-time molecular-weight moment calculations."""

from __future__ import annotations

import math
from itertools import pairwise

import numpy as np
import numpy.typing as npt

from gpcsec.core.calibration.models import Calibration
from gpcsec.core.distributions import build_distribution, mass_quantiles
from gpcsec.core.integration import union_effective_segments
from gpcsec.models import (
    AnalysisError,
    IntegrationResult,
    MolecularWeightResult,
    RangePolicy,
    RegionIntegrationResult,
)

FloatArray = npt.NDArray[np.float64]


def _validate_moment_arrays(
    time_min: npt.ArrayLike, effective_signal: npt.ArrayLike, molecular_weight: npt.ArrayLike
) -> tuple[FloatArray, FloatArray, FloatArray]:
    time = np.asarray(time_min, dtype=np.float64)
    signal = np.asarray(effective_signal, dtype=np.float64)
    mass = np.asarray(molecular_weight, dtype=np.float64)
    if time.ndim != 1 or time.shape != signal.shape or time.shape != mass.shape or len(time) < 2:
        raise AnalysisError("shape_mismatch", "moment arrays must be equal one-dimensional arrays")
    if np.any(np.diff(time) <= 0):
        raise AnalysisError("non_increasing_time", "moment time must be strictly increasing")
    if np.any(signal < 0) or np.any(mass <= 0):
        raise AnalysisError("invalid_moment_weight", "signal must be nonnegative and M positive")
    if (
        not np.all(np.isfinite(time))
        or not np.all(np.isfinite(signal))
        or not np.all(np.isfinite(mass))
    ):
        raise AnalysisError("non_finite_input", "moment arrays must be finite")
    return time, signal, mass


def calculate_moments(
    time_min: npt.ArrayLike, effective_signal: npt.ArrayLike, molecular_weight: npt.ArrayLike
) -> dict[int, float]:
    time, signal, mass = _validate_moment_arrays(time_min, effective_signal, molecular_weight)
    scale = float(np.exp(np.mean(np.log(mass))))
    scaled_mass = mass / scale
    scaled_moments: dict[int, float] = {}
    for exponent in (-1, 0, 1, 2, 3):
        terms = signal * np.power(scaled_mass, exponent)
        scaled_moments[exponent] = math.fsum(
            0.5 * float(terms[left] + terms[right]) * float(time[right] - time[left])
            for left, right in pairwise(range(len(time)))
        )
    moments = {exponent: value * scale**exponent for exponent, value in scaled_moments.items()}
    if not all(math.isfinite(value) for value in moments.values()):
        raise AnalysisError("moment_overflow", "molecular-weight moment overflowed")
    return moments


def _crop_to_calibration(
    time: FloatArray, signal: FloatArray, calibration: Calibration
) -> tuple[FloatArray, FloatArray]:
    start = max(float(time[0]), calibration.valid_rt_min)
    end = min(float(time[-1]), calibration.valid_rt_max)
    if start >= end:
        raise AnalysisError("no_in_range_area", "segment has no calibration-range overlap")
    inner = (time > start) & (time < end)
    cropped_time = np.concatenate(([start], time[inner], [end])).astype(np.float64)
    cropped_signal = np.concatenate(
        (
            [float(np.interp(start, time, signal))],
            signal[inner],
            [float(np.interp(end, time, signal))],
        )
    ).astype(np.float64)
    return cropped_time, cropped_signal


def _region_segments(region: RegionIntegrationResult) -> tuple[tuple[FloatArray, FloatArray], ...]:
    time = np.asarray(region.time_min, dtype=np.float64)
    signal = np.asarray(region.effective_signal, dtype=np.float64)
    segments: list[tuple[FloatArray, FloatArray]] = []
    offset = 0
    for length in region.segment_lengths:
        segment_time = time[offset : offset + length]
        segment_signal = signal[offset : offset + length]
        segments.append((segment_time, segment_signal))
        offset += length
    if offset != len(time):
        raise AnalysisError("invalid_segment_metadata", "segment lengths do not match arrays")
    return tuple(segments)


def select_calibrated_segments(
    integration: IntegrationResult, calibration: Calibration
) -> tuple[tuple[tuple[FloatArray, FloatArray], ...], bool, tuple[str, ...]]:
    """Select the exact effective-signal segments used by moments and distributions."""
    selected_segments: list[tuple[FloatArray, FloatArray]] = []
    warnings: list[str] = []
    for region in integration.regions:
        if not region.region.include_in_moments:
            continue
        out_of_range_area = (
            region.areas.out_of_calibration_high_m_area + region.areas.out_of_calibration_low_m_area
        )
        if region.region.range_policy is RangePolicy.REJECT and out_of_range_area > 1e-12:
            raise AnalysisError(
                "out_of_calibration_area",
                "region contains effective area outside calibration and policy is reject",
            )
        for time, signal in _region_segments(region):
            if region.region.range_policy is RangePolicy.EXPERT_EXTRAPOLATE:
                selected_segments.append((time, signal))
                warnings.append("expert extrapolation used")
            else:
                try:
                    selected_segments.append(_crop_to_calibration(time, signal, calibration))
                except AnalysisError as error:
                    if error.code != "no_in_range_area":
                        raise
    if not selected_segments:
        raise AnalysisError("no_moment_regions", "no enabled in-range segments for moments")
    overlap_present = any(
        max(float(left[0][0]), float(right[0][0])) < min(float(left[0][-1]), float(right[0][-1]))
        for index, left in enumerate(selected_segments)
        for right in selected_segments[index + 1 :]
    )
    selected_segments = list(union_effective_segments(tuple(selected_segments)))
    if overlap_present:
        warnings.append(
            "overlapping integration regions were de-duplicated in the aggregate result"
        )
    allow_extrapolation = any(
        region.region.range_policy is RangePolicy.EXPERT_EXTRAPOLATE
        for region in integration.regions
        if region.region.include_in_moments
    )
    return tuple(selected_segments), allow_extrapolation, tuple(dict.fromkeys(warnings))


def calculate_molecular_weight(
    integration: IntegrationResult, calibration: Calibration
) -> MolecularWeightResult:
    selected_segments, allow_extrapolation, selected_warnings = select_calibrated_segments(
        integration, calibration
    )
    warnings = list(selected_warnings)
    total_moments = {exponent: 0.0 for exponent in (-1, 0, 1, 2, 3)}
    peak_signal = -1.0
    mp_rt = math.nan
    for time, signal in selected_segments:
        molecular_weight = np.power(
            10.0,
            calibration.evaluate_log_m(time, allow_extrapolation=allow_extrapolation),
        )
        moments = calculate_moments(time, signal, molecular_weight)
        for exponent, value in moments.items():
            total_moments[exponent] += value
        index = int(np.argmax(signal))
        if float(signal[index]) > peak_signal:
            peak_signal = float(signal[index])
            mp_rt = float(molecular_weight[index])

    j_minus_1 = total_moments[-1]
    j_0 = total_moments[0]
    j_1 = total_moments[1]
    j_2 = total_moments[2]
    j_3 = total_moments[3]
    # These denominators have different physical dimensions, so a global scale
    # based on J3 would incorrectly reject a valid, small J-1.
    if any(value <= np.finfo(np.float64).tiny * 16 for value in (j_minus_1, j_0, j_1, j_2)):
        raise AnalysisError(
            "zero_moment_denominator", "effective molecular-weight moment is too small"
        )
    mn = j_0 / j_minus_1
    mw = j_1 / j_0
    mz = j_2 / j_1
    mz_plus_1 = j_3 / j_2
    pdi = mw / mn
    if not all(math.isfinite(value) and value > 0 for value in (mn, mw, mz, mz_plus_1, pdi, mp_rt)):
        raise AnalysisError(
            "invalid_molecular_weight_result", "moment result is not finite and positive"
        )

    distribution = build_distribution(
        tuple(selected_segments), calibration, allow_extrapolation=allow_extrapolation
    )
    quantiles = mass_quantiles(distribution)
    density = np.asarray(distribution.density_per_log10_m, dtype=np.float64)
    distribution_mass = np.asarray(distribution.molecular_weight, dtype=np.float64)
    mp_log_m = float(distribution_mass[int(np.argmax(density))])
    return MolecularWeightResult(
        j_minus_1=j_minus_1,
        j_0=j_0,
        j_1=j_1,
        j_2=j_2,
        j_3=j_3,
        mn=mn,
        mw=mw,
        mz=mz,
        mz_plus_1=mz_plus_1,
        pdi=pdi,
        mp_rt=mp_rt,
        mp_log_m=mp_log_m,
        mass_d10=quantiles[0],
        mass_d50=quantiles[1],
        mass_d90=quantiles[2],
        warnings=tuple(dict.fromkeys(warnings)),
    )


def moments_from_mass_fractions(
    molecular_weight: npt.ArrayLike, mass_fraction: npt.ArrayLike
) -> tuple[float, float, float, float, float]:
    mass = np.asarray(molecular_weight, dtype=np.float64)
    fraction = np.asarray(mass_fraction, dtype=np.float64)
    if mass.shape != fraction.shape or np.any(mass <= 0) or np.any(fraction < 0):
        raise AnalysisError("invalid_mixture", "mixture masses and fractions are invalid")
    total = float(np.sum(fraction))
    if total <= 0:
        raise AnalysisError("zero_mixture", "mixture mass fractions sum to zero")
    fraction = fraction / total
    mn = 1.0 / float(np.sum(fraction / mass))
    mw = float(np.sum(fraction * mass))
    mz = float(np.sum(fraction * mass**2) / np.sum(fraction * mass))
    mz_plus_1 = float(np.sum(fraction * mass**3) / np.sum(fraction * mass**2))
    return mn, mw, mz, mz_plus_1, mw / mn
