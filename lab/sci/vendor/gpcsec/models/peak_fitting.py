"""Serializable configuration and diagnostics for defensible peak fitting.

The persisted peak-shape parameters remain defined in :mod:`gpcsec.models.advanced`
for backwards compatibility.  This module contains only model-selection policy and
diagnostic evidence; it deliberately does not redefine Gaussian ``sigma`` or the
other historical shape parameters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum


class PeakFitMode(StrEnum):
    """How strongly automatic fitting prefers simple, local components."""

    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    FLEXIBLE = "flexible"


class PeakFitLoss(StrEnum):
    """Explicit SciPy least-squares loss selection."""

    LINEAR = "linear"
    SOFT_L1 = "soft_l1"
    HUBER = "huber"


class PeakSeedStrategy(StrEnum):
    """How automatic fitting treats the components currently edited by the user.

    ``REFINE_MANUAL`` makes the current component table a warm-start model: the
    solver may refine its free parameters and add residual-supported components,
    but it does not silently discard the supplied components.  ``REDISCOVER`` is
    the explicit opt-in path for starting again from detector candidates.
    """

    REFINE_MANUAL = "refine_manual"
    REDISCOVER = "rediscover"


@dataclass(frozen=True, slots=True)
class PeakFitConstraints:
    """Central policy for constrained automatic peak fitting.

    Values are relative to the data, fitting interval, noise, or a component's
    local centre window.  No absolute detector-response or retention-time number is
    embedded in the policy, which keeps it usable across instruments and methods.
    """

    mode: PeakFitMode = PeakFitMode.BALANCED
    max_peaks: int = 8
    minimum_fwhm_points: float = 4.0
    maximum_fwhm_region_fraction: float = 0.85
    maximum_fwhm_local_fraction: float = 0.95
    soft_width_ratio: float = 0.70
    center_locality_fraction: float = 0.35
    manual_center_max_shift_fwhm: float = 0.75
    manual_fwhm_lower_factor: float = 0.50
    manual_fwhm_upper_factor: float = 2.00
    minimum_peak_height_relative: float = 0.012
    minimum_peak_area_relative: float = 0.006
    minimum_peak_snr: float = 3.5
    candidate_prominence_snr: float = 4.0
    candidate_minimum_width_points: float = 3.0
    candidate_integrated_snr: float = 6.0
    candidate_fwhm_lower_factor: float = 0.35
    candidate_fwhm_upper_factor: float = 2.50
    candidate_trials_per_step: int = 4
    aicc_improvement_required: float = 4.0
    bic_worsening_tolerance: float = 1.5
    overlap_warning_distance: float = 0.70
    localization_warning_fraction: float = 0.80
    width_penalty_weight: float = 1.5
    center_penalty_weight: float = 0.35
    overlap_penalty_weight: float = 0.30
    preserve_peak_order: bool = True
    robust_loss: PeakFitLoss = PeakFitLoss.LINEAR
    robust_f_scale: float = 1.0
    stability_trials: int = 3
    fitting_window_fraction: float = 0.04
    stability_center_fraction_warning: float = 0.12
    stability_width_cv_warning: float = 0.25
    stability_area_cv_warning: float = 0.25
    multistart_objective_tolerance: float = 0.01

    def __post_init__(self) -> None:
        if not 1 <= self.max_peaks <= 20:
            raise ValueError("maximum peak count must be between one and twenty")
        positive_values = (
            self.minimum_fwhm_points,
            self.maximum_fwhm_region_fraction,
            self.maximum_fwhm_local_fraction,
            self.candidate_prominence_snr,
            self.candidate_minimum_width_points,
            self.candidate_integrated_snr,
            self.candidate_fwhm_lower_factor,
            self.candidate_fwhm_upper_factor,
            self.manual_center_max_shift_fwhm,
            self.manual_fwhm_lower_factor,
            self.manual_fwhm_upper_factor,
            self.robust_f_scale,
        )
        if any(not math.isfinite(value) or value <= 0 for value in positive_values):
            raise ValueError("positive peak-fit policy values must be finite")
        if self.candidate_fwhm_lower_factor >= self.candidate_fwhm_upper_factor:
            raise ValueError("candidate FWHM factors must be increasing")
        if self.manual_fwhm_lower_factor >= self.manual_fwhm_upper_factor:
            raise ValueError("manual-prior FWHM factors must be increasing")
        fractions = (
            self.soft_width_ratio,
            self.center_locality_fraction,
            self.minimum_peak_height_relative,
            self.minimum_peak_area_relative,
            self.localization_warning_fraction,
            self.fitting_window_fraction,
            self.stability_center_fraction_warning,
            self.stability_width_cv_warning,
            self.stability_area_cv_warning,
            self.multistart_objective_tolerance,
        )
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in fractions):
            raise ValueError("relative peak-fit policy values must be between zero and one")
        nonnegative = (
            self.minimum_peak_snr,
            self.aicc_improvement_required,
            self.bic_worsening_tolerance,
            self.overlap_warning_distance,
            self.width_penalty_weight,
            self.center_penalty_weight,
            self.overlap_penalty_weight,
        )
        if any(not math.isfinite(value) or value < 0 for value in nonnegative):
            raise ValueError("non-negative peak-fit policy values must be finite")
        if self.stability_trials < 0:
            raise ValueError("stability trial count cannot be negative")
        if not 1 <= self.candidate_trials_per_step <= 20:
            raise ValueError("candidate trials per step must be between one and twenty")


def constraints_for_mode(mode: PeakFitMode) -> PeakFitConstraints:
    """Return an explicit, inspectable policy preset without a magic total score."""

    base = PeakFitConstraints(mode=mode)
    if mode is PeakFitMode.CONSERVATIVE:
        return replace(
            base,
            max_peaks=6,
            maximum_fwhm_region_fraction=0.72,
            maximum_fwhm_local_fraction=0.82,
            minimum_peak_height_relative=0.018,
            minimum_peak_area_relative=0.010,
            minimum_peak_snr=4.5,
            candidate_prominence_snr=5.0,
            aicc_improvement_required=6.0,
            bic_worsening_tolerance=0.0,
            width_penalty_weight=2.2,
            candidate_fwhm_upper_factor=2.0,
            manual_center_max_shift_fwhm=0.55,
            manual_fwhm_lower_factor=0.60,
            manual_fwhm_upper_factor=1.65,
        )
    if mode is PeakFitMode.FLEXIBLE:
        return replace(
            base,
            max_peaks=12,
            maximum_fwhm_region_fraction=0.95,
            maximum_fwhm_local_fraction=1.15,
            minimum_peak_height_relative=0.007,
            minimum_peak_area_relative=0.003,
            minimum_peak_snr=2.8,
            candidate_prominence_snr=3.2,
            aicc_improvement_required=2.0,
            bic_worsening_tolerance=4.0,
            width_penalty_weight=0.9,
            candidate_fwhm_upper_factor=3.5,
            manual_center_max_shift_fwhm=1.25,
            manual_fwhm_lower_factor=0.35,
            manual_fwhm_upper_factor=3.0,
        )
    return base


@dataclass(frozen=True, slots=True)
class ResidualDiagnostics:
    """Evidence about whether residuals resemble random detector noise."""

    noise_sigma: float
    sign_runs: int
    expected_sign_runs: float
    longest_same_sign_run: int
    lag1_autocorrelation: float | None
    durbin_watson: float | None
    structured: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PeakComponentDiagnostics:
    """Shape-independent scientific evidence for one mathematical component."""

    height: float
    fwhm_time_min: float
    snr: float | None
    width_ratio: float | None
    overlap_score: float | None
    localization_score: float | None
    center_log10_m: float | None = None
    center_molecular_weight: float | None = None
    fwhm_log10_m: float | None = None
    molecular_weight_low: float | None = None
    molecular_weight_high: float | None = None
    stability_score: float | None = None
    center_stability_sd: float | None = None
    fwhm_stability_cv: float | None = None
    area_stability_cv: float | None = None
    window_center_shift_fraction: float | None = None
    window_fwhm_change_fraction: float | None = None
    window_area_change_fraction: float | None = None
    height_uncertainty: float | None = None
    fwhm_uncertainty: float | None = None
    area_uncertainty: float | None = None
    area_90_time_min: tuple[float, float] | None = None
    area_90_molecular_weight: tuple[float, float] | None = None
    flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PeakModelSummary:
    """Compact evidence for a recommended or alternative component model."""

    component_count: int
    shapes: tuple[str, ...]
    rss: float
    rmse: float
    aicc: float
    bic: float
    accepted: bool
    reason: str


@dataclass(frozen=True, slots=True)
class PeakFitDiagnostics:
    """Numerical, statistical, and physical evidence kept separate by design."""

    adjusted_r_squared: float | None
    aicc: float | None
    residual: ResidualDiagnostics | None
    condition_number: float | None
    maximum_parameter_correlation: float | None
    data_loss: float
    width_penalty: float
    center_penalty: float
    overlap_penalty: float
    numerical_quality: str
    statistical_quality: str
    physical_quality: str
    warnings: tuple[str, ...] = ()
    decision_log: tuple[str, ...] = ()
    model_candidates: tuple[PeakModelSummary, ...] = ()
    debug_values: tuple[tuple[str, float | str], ...] = ()
