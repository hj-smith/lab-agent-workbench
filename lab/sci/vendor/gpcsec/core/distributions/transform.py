"""Conservative RT interval to molecular-weight distribution transforms."""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import numpy.typing as npt

from gpcsec.core.calibration.models import Calibration
from gpcsec.models import AnalysisError, DistributionResult

FloatArray = npt.NDArray[np.float64]


def _validated_segments(
    segments: tuple[tuple[npt.ArrayLike, npt.ArrayLike], ...],
) -> tuple[tuple[FloatArray, FloatArray], ...]:
    output: list[tuple[FloatArray, FloatArray]] = []
    for time_raw, signal_raw in segments:
        time = np.asarray(time_raw, dtype=np.float64)
        signal = np.asarray(signal_raw, dtype=np.float64)
        if time.ndim != 1 or time.shape != signal.shape or len(time) < 2:
            raise AnalysisError("shape_mismatch", "distribution segments require equal 1D arrays")
        if np.any(np.diff(time) <= 0) or np.any(signal < 0):
            raise AnalysisError(
                "invalid_distribution_segment",
                "time must increase and effective signal must be nonnegative",
            )
        if not np.all(np.isfinite(time)) or not np.all(np.isfinite(signal)):
            raise AnalysisError("non_finite_input", "distribution segments must be finite")
        output.append((time, signal))
    if not output:
        raise AnalysisError("empty_distribution", "no distribution segments were supplied")
    return tuple(output)


def build_distribution(
    segments: tuple[tuple[npt.ArrayLike, npt.ArrayLike], ...],
    calibration: Calibration,
    *,
    allow_extrapolation: bool = False,
) -> DistributionResult:
    validated = _validated_segments(segments)
    records: list[tuple[float, float, float, float, float, float, float, float, float]] = []
    total_mass_rt = 0.0
    total_mass_log = 0.0
    total_relative_number = 0.0
    for time, signal in validated:
        log_m = calibration.evaluate_log_m(time, allow_extrapolation=allow_extrapolation)
        molecular_weight = np.power(10.0, log_m)
        if np.any(molecular_weight <= 0) or not np.all(np.isfinite(molecular_weight)):
            raise AnalysisError("invalid_molecular_weight", "calibration produced invalid M")
        for left_index, right_index in pairwise(range(len(time))):
            dt = float(time[right_index] - time[left_index])
            mass_slice = 0.5 * float(signal[left_index] + signal[right_index]) * dt
            if mass_slice < 0:
                raise AnalysisError(
                    "negative_mass_slice", "effective mass slice cannot be negative"
                )
            midpoint_log_m = 0.5 * float(log_m[left_index] + log_m[right_index])
            midpoint_m = 10.0**midpoint_log_m
            delta_log_m = abs(float(log_m[right_index] - log_m[left_index]))
            delta_m = abs(float(molecular_weight[right_index] - molecular_weight[left_index]))
            if delta_log_m <= 0 or delta_m <= 0:
                raise AnalysisError("zero_calibration_slope", "calibration interval has zero slope")
            density_log = mass_slice / delta_log_m
            density_m = mass_slice / delta_m
            number_slice = mass_slice / midpoint_m
            number_density_log = number_slice / delta_log_m
            number_density_m = number_slice / delta_m
            mapped_signal = 0.5 * float(signal[left_index] + signal[right_index])
            records.append(
                (
                    midpoint_m,
                    midpoint_log_m,
                    mapped_signal,
                    density_log,
                    density_m,
                    mass_slice,
                    number_density_log,
                    number_density_m,
                    number_slice,
                )
            )
            total_mass_rt += mass_slice
            total_mass_log += density_log * delta_log_m
            total_relative_number += number_slice
    if total_mass_rt <= 0:
        raise AnalysisError("zero_effective_area", "distribution has no positive effective area")
    records.sort(key=lambda item: item[0])
    cumulative = 0.0
    cumulative_number = 0.0
    cumulative_fractions: list[float] = []
    cumulative_number_fractions: list[float] = []
    for record in records:
        cumulative += record[5]
        cumulative_number += record[8]
        cumulative_fractions.append(cumulative / total_mass_rt)
        cumulative_number_fractions.append(cumulative_number / total_relative_number)
    return DistributionResult(
        molecular_weight=tuple(record[0] for record in records),
        log10_molecular_weight=tuple(record[1] for record in records),
        mapped_signal=tuple(record[2] for record in records),
        density_per_log10_m=tuple(record[3] for record in records),
        density_per_m=tuple(record[4] for record in records),
        cumulative_mass_fraction=tuple(cumulative_fractions),
        total_mass_rt=total_mass_rt,
        total_mass_log_m=total_mass_log,
        density_number_per_log10_m=tuple(record[6] for record in records),
        density_number_per_m=tuple(record[7] for record in records),
        cumulative_number_fraction=tuple(cumulative_number_fractions),
        total_relative_number=total_relative_number,
    )


def mass_quantiles(
    distribution: DistributionResult, probabilities: tuple[float, ...] = (0.1, 0.5, 0.9)
) -> tuple[float, ...]:
    if any(not 0.0 <= probability <= 1.0 for probability in probabilities):
        raise ValueError("quantile probabilities must lie between zero and one")
    cumulative = np.asarray(distribution.cumulative_mass_fraction, dtype=np.float64)
    molecular_weight = np.asarray(distribution.molecular_weight, dtype=np.float64)
    return tuple(
        float(np.interp(probability, cumulative, molecular_weight)) for probability in probabilities
    )
