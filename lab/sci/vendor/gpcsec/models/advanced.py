"""Strongly typed advanced-baseline, peak-fit, and comparison contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from gpcsec.models.analysis import BaselineResult, MolecularWeightResult
from gpcsec.models.domain import AreaBreakdown, IntegrationRegion, PeakPolarity
from gpcsec.models.peak_fitting import (
    PeakComponentDiagnostics,
    PeakFitConstraints,
    PeakFitDiagnostics,
    PeakSeedStrategy,
)

ADVANCED_SCHEMA_VERSION = 1


class BaselineAlgorithm(StrEnum):
    POLYNOMIAL = "polynomial"
    RUBBER_BAND = "rubber_band"
    ASLS = "asls"
    AIRPLS = "airpls"
    ARPLS = "arpls"


@dataclass(frozen=True, slots=True)
class AutoBaselineSpec:
    algorithm: BaselineAlgorithm
    parameters: Mapping[str, float | int] = field(default_factory=dict)
    exclusions: tuple[tuple[float, float], ...] = ()
    schema_version: int = ADVANCED_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        if any(start >= end for start, end in self.exclusions):
            raise ValueError("baseline exclusions must have start < end")


@dataclass(frozen=True, slots=True)
class BaselineDiagnostics:
    negative_residual_fraction: float
    baseline_to_signal_range_ratio: float
    broad_peak_consumption_risk: bool
    converged: bool
    iterations: int
    warnings: tuple[str, ...] = ()
    schema_version: int = ADVANCED_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class AutoBaselinePreview:
    spec: AutoBaselineSpec
    result: BaselineResult
    diagnostics: BaselineDiagnostics
    schema_version: int = ADVANCED_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class BaselinePeakWindow:
    """Peak interval automatically protected from baseline fitting."""

    start_time_min: float
    end_time_min: float
    polarity: PeakPolarity
    center_time_min: float
    prominence_snr: float
    fwhm_time_min: float
    edge_truncated: bool = False
    sources: tuple[str, ...] = ()
    schema_version: int = ADVANCED_SCHEMA_VERSION

    def __post_init__(self) -> None:
        numeric = (
            self.start_time_min,
            self.end_time_min,
            self.center_time_min,
            self.prominence_snr,
            self.fwhm_time_min,
        )
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("baseline peak-window values must be finite")
        if not self.start_time_min < self.end_time_min:
            raise ValueError("baseline peak window must have start < end")
        if not self.start_time_min <= self.center_time_min <= self.end_time_min:
            raise ValueError("baseline peak center must lie inside its window")
        if self.prominence_snr < 0 or self.fwhm_time_min <= 0:
            raise ValueError("baseline peak prominence and FWHM must be positive")
        if not self.sources:
            raise ValueError("baseline peak window needs at least one detection source")


@dataclass(frozen=True, slots=True)
class BaselineCandidateScore:
    """Safety evidence for one deterministic automatic-baseline candidate."""

    preview: AutoBaselinePreview
    peak_absorption: float
    baseline_crossing: float
    reverse_residual: float
    curvature: float
    clean_domain_drift: float
    composite_score: float
    safe: bool
    rejection_reasons: tuple[str, ...] = ()
    peak_area_absorption: float = 0.0
    prominence_retention: float = 1.0
    area_retention: float = 1.0
    clean_location_bias: float = 0.0
    clean_residual_mad_ratio: float = 1.0
    clean_block_drift: float = 0.0
    shoulder_supported: bool = True
    schema_version: int = ADVANCED_SCHEMA_VERSION

    def __post_init__(self) -> None:
        metrics = (
            self.peak_absorption,
            self.baseline_crossing,
            self.reverse_residual,
            self.curvature,
            self.clean_domain_drift,
            self.composite_score,
            self.peak_area_absorption,
            self.prominence_retention,
            self.area_retention,
            self.clean_location_bias,
            self.clean_residual_mad_ratio,
            self.clean_block_drift,
        )
        if not all(math.isfinite(value) and value >= 0 for value in metrics):
            raise ValueError("baseline candidate scores must be finite and nonnegative")
        if self.composite_score > 1.0 + 1e-12:
            raise ValueError("baseline composite score must not exceed one")
        if self.prominence_retention > 2.0 or self.area_retention > 2.0:
            raise ValueError("baseline peak-retention ratios must not exceed two")
        if self.safe == bool(self.rejection_reasons):
            raise ValueError("safe baseline candidates cannot have rejection reasons")

    @property
    def spec(self) -> AutoBaselineSpec:
        """Convenient direct access for review UIs."""
        return self.preview.spec


@dataclass(frozen=True, slots=True)
class AutoBaselineSuggestion:
    """Review-only peak-aware recommendation; never mutates a sample."""

    peak_windows: tuple[BaselinePeakWindow, ...]
    effective_exclusions: tuple[tuple[float, float], ...]
    candidates: tuple[BaselineCandidateScore, ...]
    rejected_candidates: tuple[BaselineCandidateScore, ...]
    recommended: AutoBaselinePreview | None
    recommended_score: BaselineCandidateScore | None
    confidence: float
    confidence_level: str
    noise_sigma: float
    decision_log: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_version: int = ADVANCED_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if any(start >= end for start, end in self.effective_exclusions):
            raise ValueError("effective baseline exclusions must have start < end")
        if tuple(sorted(self.effective_exclusions)) != self.effective_exclusions:
            raise ValueError("effective baseline exclusions must be ordered")
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("automatic baseline confidence must be between zero and one")
        if not math.isfinite(self.noise_sigma) or self.noise_sigma < 0:
            raise ValueError("automatic baseline noise must be finite and nonnegative")
        if self.confidence_level not in {"high", "medium", "low", "none"}:
            raise ValueError("unsupported automatic baseline confidence level")
        if (self.recommended is None) != (self.recommended_score is None):
            raise ValueError("recommended preview and score must be present together")
        if self.recommended_score is not None:
            if not self.recommended_score.safe:
                raise ValueError("recommended automatic baseline must be safe")
            if self.recommended_score.preview != self.recommended:
                raise ValueError("recommended automatic baseline preview and score differ")


class PeakShape(StrEnum):
    GAUSSIAN = "gaussian"
    LORENTZIAN = "lorentzian"
    VOIGT = "voigt"
    PSEUDO_VOIGT = "pseudo_voigt"
    EMG = "exponentially_modified_gaussian"


@dataclass(frozen=True, slots=True)
class PeakComponentSpec:
    shape: PeakShape
    amplitude: float
    center_time_min: float
    width_time_min: float
    shape_parameter: float | None = None
    fixed: tuple[str, ...] = ()
    bounds: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    shared: Mapping[str, str] = field(default_factory=dict)
    schema_version: int = ADVANCED_SCHEMA_VERSION
    name: str | None = None
    molecular_weight_bounds: tuple[float, float] | None = None
    fwhm_bounds_time_min: tuple[float, float] | None = None
    fwhm_bounds_log10_m: tuple[float, float] | None = None
    minimum_area_fraction: float | None = None

    def __post_init__(self) -> None:
        if self.amplitude <= 0 or self.width_time_min <= 0:
            raise ValueError("peak amplitude and width must be positive")
        object.__setattr__(self, "bounds", MappingProxyType(dict(self.bounds)))
        object.__setattr__(self, "shared", MappingProxyType(dict(self.shared)))
        if any(lower >= upper for lower, upper in self.bounds.values()):
            raise ValueError("peak parameter bounds must have lower < upper")
        for value, label in (
            (self.molecular_weight_bounds, "molecular-weight"),
            (self.fwhm_bounds_time_min, "retention-time FWHM"),
            (self.fwhm_bounds_log10_m, "log-M FWHM"),
        ):
            if value is not None and (
                len(value) != 2
                or not all(math.isfinite(item) for item in value)
                or value[0] <= 0
                or value[0] >= value[1]
            ):
                raise ValueError(f"{label} bounds must be finite, positive, and increasing")
        if self.minimum_area_fraction is not None and not (
            math.isfinite(self.minimum_area_fraction) and 0.0 <= self.minimum_area_fraction < 1.0
        ):
            raise ValueError("minimum peak area fraction must be between zero and one")


@dataclass(frozen=True, slots=True)
class PeakFitSpec:
    region: IntegrationRegion
    components: tuple[PeakComponentSpec, ...]
    multi_start: int = 0
    random_seed: int = 0
    max_nfev: int = 0
    schema_version: int = ADVANCED_SCHEMA_VERSION
    constraints: PeakFitConstraints | None = None
    automatic_model_selection: bool = False
    seed_strategy: PeakSeedStrategy = PeakSeedStrategy.REFINE_MANUAL

    def __post_init__(self) -> None:
        if not self.components or self.multi_start < 0 or self.max_nfev < 0:
            raise ValueError("peak fit needs components and non-negative solver limits")


@dataclass(frozen=True, slots=True)
class PeakModelPreview:
    """Display arrays evaluated from an editable peak specification."""

    time_min: tuple[float, ...]
    raw_signal: tuple[float, ...]
    baseline: tuple[float, ...]
    observed: tuple[float, ...]
    total_fit: tuple[float, ...]
    residual: tuple[float, ...]
    component_curves: tuple[tuple[float, ...], ...]
    schema_version: int = ADVANCED_SCHEMA_VERSION

    def __post_init__(self) -> None:
        lengths = {
            len(self.time_min),
            len(self.raw_signal),
            len(self.baseline),
            len(self.observed),
            len(self.total_fit),
            len(self.residual),
            *(len(curve) for curve in self.component_curves),
        }
        if len(lengths) != 1:
            raise ValueError("peak preview arrays must have equal lengths")


@dataclass(frozen=True, slots=True)
class PeakComponentResult:
    shape: PeakShape
    parameters: Mapping[str, float]
    standard_errors: Mapping[str, float | None]
    confidence_95: Mapping[str, tuple[float, float] | None]
    boundary_hits: tuple[str, ...]
    area: float
    area_fraction: float
    schema_version: int = ADVANCED_SCHEMA_VERSION
    diagnostics: PeakComponentDiagnostics | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        object.__setattr__(self, "standard_errors", MappingProxyType(dict(self.standard_errors)))
        object.__setattr__(self, "confidence_95", MappingProxyType(dict(self.confidence_95)))


@dataclass(frozen=True, slots=True)
class PeakFitResult:
    success: bool
    failure_code: str | None
    message: str
    time_min: tuple[float, ...]
    observed: tuple[float, ...]
    total_fit: tuple[float, ...]
    residual: tuple[float, ...]
    component_curves: tuple[tuple[float, ...], ...]
    components: tuple[PeakComponentResult, ...]
    rss: float | None
    rmse: float | None
    r_squared: float | None
    aic: float | None
    bic: float | None
    nfev: int
    random_seed: int
    algorithm_version: str = "stepwise-physical-aicc-bic-pruning-v1"
    schema_version: int = ADVANCED_SCHEMA_VERSION
    adjusted_r_squared: float | None = None
    aicc: float | None = None
    diagnostics: PeakFitDiagnostics | None = None

    def __post_init__(self) -> None:
        if self.success and (not self.components or self.rss is None):
            raise ValueError("successful peak fit requires components and statistics")
        if not self.success and (self.components or self.total_fit):
            raise ValueError("failed peak fit must not expose component results")


@dataclass(frozen=True, slots=True)
class PeakMolecularWeightComparison:
    direct_integration: MolecularWeightResult | None
    fitted_components: tuple[MolecularWeightResult | None, ...]
    calibration_id: str | None
    warnings: tuple[str, ...] = ()
    schema_version: int = ADVANCED_SCHEMA_VERSION
    direct_unavailable_reason: str | None = None
    component_unavailable_reasons: tuple[str | None, ...] = ()
    direct_in_calibration_area_fraction: float | None = None
    component_in_calibration_area_fractions: tuple[float | None, ...] = ()

    def __post_init__(self) -> None:
        if (self.direct_integration is None) == (self.direct_unavailable_reason is None):
            raise ValueError(
                "direct peak molecular weight requires either a result or an unavailable reason"
            )
        component_count = len(self.fitted_components)
        component_metadata_present = bool(
            self.component_unavailable_reasons or self.component_in_calibration_area_fractions
        )
        if component_metadata_present:
            if (
                len(self.component_unavailable_reasons) != component_count
                or len(self.component_in_calibration_area_fractions) != component_count
            ):
                raise ValueError("component molecular-weight result tuples must have equal lengths")
            for result, reason in zip(
                self.fitted_components,
                self.component_unavailable_reasons,
                strict=True,
            ):
                if (result is None) == (reason is None):
                    raise ValueError(
                        "each component requires either a result or an unavailable reason"
                    )
        elif any(result is None for result in self.fitted_components):
            raise ValueError("unavailable component results require reasons")
        fractions = (
            self.direct_in_calibration_area_fraction,
            *self.component_in_calibration_area_fractions,
        )
        if any(
            fraction is not None and (not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0)
            for fraction in fractions
        ):
            raise ValueError("peak in-calibration area fractions must be between zero and one")


@dataclass(frozen=True, slots=True)
class RegionMolecularWeightResult:
    """Formal molecular-weight result for one reviewed integration interval."""

    region_index: int
    region: IntegrationRegion
    molecular_weight: MolecularWeightResult | None
    unavailable_reason: str | None = None
    areas: AreaBreakdown | None = None
    independent_area_fraction: float | None = None
    in_calibration_area_fraction: float | None = None
    overlap_region_indices: tuple[int, ...] = ()
    schema_version: int = ADVANCED_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.region_index < 1:
            raise ValueError("region index must be one-based")
        if (self.molecular_weight is None) == (self.unavailable_reason is None):
            raise ValueError("region result requires either a value or an unavailable reason")
        for fraction in (
            self.independent_area_fraction,
            self.in_calibration_area_fraction,
        ):
            if fraction is not None and not (
                math.isfinite(fraction) and 0.0 <= fraction <= 1.0 + 1e-12
            ):
                raise ValueError("region area fractions must be finite and between zero and one")


class NormalizationMode(StrEnum):
    NONE = "none"
    PEAK_HEIGHT = "peak_height"
    INTERVAL_PEAK = "interval_peak"
    AT_TIME = "at_time"
    TOTAL_AREA = "total_area"
    INTERVAL_AREA = "interval_area"
    REFERENCE = "reference"


class NormalizationCoordinate(StrEnum):
    """Coordinate system used to locate RT points or intervals for display normalization."""

    DISPLAY_RT = "display_rt"
    ORIGINAL_RT = "original_rt"


class ComparisonSignalSource(StrEnum):
    """Signal source used for display-only sample comparison."""

    BASELINE_CORRECTED = "baseline_corrected"
    RAW = "raw"


class ComparisonAxis(StrEnum):
    RETENTION_TIME = "retention_time"
    LOG10_MOLECULAR_WEIGHT = "log10_molecular_weight"
    MOLECULAR_WEIGHT = "molecular_weight"


@dataclass(frozen=True, slots=True)
class ViewTransform:
    x_shift_min: float = 0.0
    y_shift: float = 0.0
    amplitude_scale: float = 1.0
    normalization: NormalizationMode = NormalizationMode.NONE
    normalization_interval: tuple[float, float] | None = None
    normalization_time_min: float | None = None
    normalization_coordinate: NormalizationCoordinate = NormalizationCoordinate.DISPLAY_RT
    reference_sample_id: str | None = None
    alignment_method: str = "manual"
    display_only: bool = True
    x_axis: ComparisonAxis = ComparisonAxis.RETENTION_TIME
    schema_version: int = ADVANCED_SCHEMA_VERSION

    def __post_init__(self) -> None:
        values = (self.x_shift_min, self.y_shift, self.amplitude_scale)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("view transform values must be finite")
        if self.amplitude_scale == 0:
            raise ValueError("view amplitude scale must not be zero")
        if self.normalization_interval is not None:
            start, end = self.normalization_interval
            if not math.isfinite(start) or not math.isfinite(end) or start >= end:
                raise ValueError("view normalization interval must have finite start < end")
        if self.normalization_time_min is not None and not math.isfinite(
            self.normalization_time_min
        ):
            raise ValueError("view normalization time must be finite")
        if not self.display_only:
            raise ValueError("view transforms must remain display-only")


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    kind: str
    grid_time_min: tuple[float, ...]
    values: tuple[float, ...]
    interpolation: str
    warnings: tuple[str, ...] = ()
    schema_version: int = ADVANCED_SCHEMA_VERSION
