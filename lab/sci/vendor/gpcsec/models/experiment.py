"""Explicit experiment design and traceable polymer-distribution evolution contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType


class ExperimentFactorKind(StrEnum):
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"


class ExperimentMemberReviewState(StrEnum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
    REVIEW = "review"


class KineticModel(StrEnum):
    ZERO_ORDER = "zero_order"
    FIRST_ORDER = "first_order"
    SECOND_ORDER = "second_order"
    GENERAL_ORDER = "general_order"


@dataclass(frozen=True, slots=True)
class KineticSegment:
    segment_id: str
    label: str
    lower_axis: float | None
    upper_axis: float | None

    def __post_init__(self) -> None:
        if not self.segment_id.strip() or not self.label.strip():
            raise ValueError("kinetic segment ID and label are required")
        bounds = (self.lower_axis, self.upper_axis)
        if any(value is not None and not math.isfinite(value) for value in bounds):
            raise ValueError("kinetic segment bounds must be finite")
        if (
            self.lower_axis is not None
            and self.upper_axis is not None
            and self.lower_axis >= self.upper_axis
        ):
            raise ValueError("kinetic segment lower bound must be less than its upper bound")


@dataclass(frozen=True, slots=True)
class KineticPointDecision:
    member_id: str
    included: bool = True
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.member_id.strip():
            raise ValueError("kinetic point decision member ID is required")
        if not self.included and not self.reason.strip():
            raise ValueError("excluded kinetic points require a reason")


@dataclass(frozen=True, slots=True)
class KineticAnalysisSpec:
    metric: str = "Mw"
    group_factor_id: str | None = None
    models: tuple[KineticModel, ...] = (
        KineticModel.ZERO_ORDER,
        KineticModel.FIRST_ORDER,
        KineticModel.SECOND_ORDER,
        KineticModel.GENERAL_ORDER,
    )
    segments: tuple[KineticSegment, ...] = ()
    point_decisions: tuple[KineticPointDecision, ...] = ()
    arrhenius_temperature_factor_id: str | None = None
    arrhenius_rate_policy: str = "magnitude"
    arrhenius_temperature_interpretation: str = "auto"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.metric.strip():
            raise ValueError("kinetic analysis metric is required")
        for factor_id in (self.group_factor_id, self.arrhenius_temperature_factor_id):
            if factor_id is not None and not factor_id.strip():
                raise ValueError("kinetic analysis factor IDs must not be blank")
        if not self.models or len(self.models) != len(set(self.models)):
            raise ValueError("kinetic analysis models must be nonempty and unique")
        segment_ids = [segment.segment_id for segment in self.segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("kinetic segment IDs must be unique")
        decision_ids = [decision.member_id for decision in self.point_decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("kinetic point decisions must be unique per member")
        if self.arrhenius_rate_policy not in {"magnitude", "positive", "negative"}:
            raise ValueError("unknown Arrhenius rate policy")
        if self.arrhenius_temperature_interpretation not in {"auto", "c", "k"}:
            raise ValueError("unknown Arrhenius temperature interpretation")
        if self.schema_version != 1:
            raise ValueError("unsupported kinetic analysis schema version")


@dataclass(frozen=True, slots=True)
class SamplePreparation:
    preparation_id: str
    name: str
    protocol_reference: str = ""
    solvent: str = ""
    concentration_value: float | None = None
    concentration_unit: str = "mg/mL"
    filtration: str = ""
    dilution_factor: float | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.preparation_id.strip() or not self.name.strip():
            raise ValueError("sample preparation ID and name are required")
        if self.concentration_value is not None and (
            not math.isfinite(self.concentration_value) or self.concentration_value <= 0
        ):
            raise ValueError("sample preparation concentration must be positive")
        if self.dilution_factor is not None and (
            not math.isfinite(self.dilution_factor) or self.dilution_factor <= 0
        ):
            raise ValueError("sample preparation dilution factor must be positive")


@dataclass(frozen=True, slots=True)
class ExperimentFactor:
    factor_id: str
    name: str
    kind: ExperimentFactorKind
    unit: str = ""

    def __post_init__(self) -> None:
        if not self.factor_id.strip() or not self.name.strip():
            raise ValueError("experiment factor ID and name are required")


@dataclass(frozen=True, slots=True)
class ExperimentMember:
    member_id: str
    sample_id: str
    time_value: float
    time_unit: str
    replicate: str
    factor_levels: Mapping[str, str | float] = field(default_factory=dict)
    preparation_id: str | None = None
    batch_label: str = ""
    review_state: ExperimentMemberReviewState = ExperimentMemberReviewState.INCLUDED
    review_reason: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.member_id.strip() or not self.sample_id.strip():
            raise ValueError("experiment member and sample IDs are required")
        if not math.isfinite(self.time_value):
            raise ValueError("experiment time value must be finite")
        if not self.time_unit.strip() or not self.replicate.strip():
            raise ValueError("experiment time unit and replicate label are required")
        object.__setattr__(self, "factor_levels", MappingProxyType(dict(self.factor_levels)))


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    experiment_id: str
    name: str
    time_axis_name: str
    time_unit: str
    factors: tuple[ExperimentFactor, ...]
    members: tuple[ExperimentMember, ...]
    sample_preparations: tuple[SamplePreparation, ...] = ()
    description: str = ""
    created_utc: str = ""
    updated_utc: str = ""
    metadata: Mapping[str, str | float | int | bool | None] = field(default_factory=dict)
    kinetic_analysis: KineticAnalysisSpec | None = None
    joint_peak_analysis: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.experiment_id.strip() or not self.name.strip():
            raise ValueError("experiment ID and name are required")
        if not self.time_axis_name.strip() or not self.time_unit.strip():
            raise ValueError("experiment time axis name and unit are required")
        factor_ids = [factor.factor_id for factor in self.factors]
        member_ids = [member.member_id for member in self.members]
        if len(factor_ids) != len(set(factor_ids)):
            raise ValueError("experiment factor IDs must be unique")
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("experiment member IDs must be unique")
        known_factors = set(factor_ids)
        preparation_ids = [item.preparation_id for item in self.sample_preparations]
        if len(preparation_ids) != len(set(preparation_ids)):
            raise ValueError("sample preparation IDs must be unique")
        known_preparations = set(preparation_ids)
        for member in self.members:
            if member.time_unit != self.time_unit:
                raise ValueError("experiment members must use the experiment time unit")
            if not set(member.factor_levels).issubset(known_factors):
                raise ValueError("experiment member references an unknown factor")
            if (
                member.preparation_id is not None
                and member.preparation_id not in known_preparations
            ):
                raise ValueError("experiment member references an unknown sample preparation")
        if self.kinetic_analysis is not None:
            specification = self.kinetic_analysis
            for factor_id in (
                specification.group_factor_id,
                specification.arrhenius_temperature_factor_id,
            ):
                if factor_id is not None and factor_id not in known_factors:
                    raise ValueError("kinetic analysis references an unknown factor")
            if not {decision.member_id for decision in specification.point_decisions}.issubset(
                member_ids
            ):
                raise ValueError("kinetic analysis references an unknown experiment member")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(
            self,
            "joint_peak_analysis",
            MappingProxyType(dict(self.joint_peak_analysis)),
        )


@dataclass(frozen=True, slots=True)
class ExperimentTrace:
    experiment_member_id: str
    sample_id: str
    sample_name: str
    analysis_version: int
    method_id: str | None
    calibration_id: str
    source_sha256: str
    time_value: float
    time_unit: str
    replicate: str
    factor_levels: Mapping[str, str | float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor_levels", MappingProxyType(dict(self.factor_levels)))


@dataclass(frozen=True, slots=True)
class EvolutionMetricPoint:
    time_value: float
    sample_count: int
    mean: float
    standard_deviation: float | None
    lower_interval: float | None = None
    upper_interval: float | None = None
    source_sample_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvolutionObservation:
    code: str
    title: str
    description: str
    evidence: str
    interpretation_limit: str = "Descriptive pattern; not an automatic mechanism assignment."


@dataclass(frozen=True, slots=True)
class ExperimentEvolutionResult:
    experiment_id: str
    time_unit: str
    log10_molecular_weight_grid: tuple[float, ...]
    time_points: tuple[float, ...]
    mean_density_rows: tuple[tuple[float, ...], ...]
    standard_deviation_rows: tuple[tuple[float, ...], ...]
    replicate_counts: tuple[int, ...]
    metric_series: Mapping[str, tuple[EvolutionMetricPoint, ...]]
    traces: tuple[ExperimentTrace, ...]
    source_density_rows: tuple[tuple[float, ...], ...] = ()
    observations: tuple[EvolutionObservation, ...] = ()
    cumulative_thresholds: Mapping[str, tuple[EvolutionMetricPoint, ...]] = field(
        default_factory=dict
    )
    molecular_weight_bands: Mapping[str, tuple[EvolutionMetricPoint, ...]] = field(
        default_factory=dict
    )
    molecular_weight_bands_adaptive: bool = False
    warnings: tuple[str, ...] = ()
    axis_name: str = "Time"
    axis_kind: str = "continuous"
    algorithm_version: str = "experiment-evolution-logm-grid-v3"

    def __post_init__(self) -> None:
        width = len(self.log10_molecular_weight_grid)
        row_count = len(self.time_points)
        if not (
            len(self.mean_density_rows)
            == len(self.standard_deviation_rows)
            == len(self.replicate_counts)
            == row_count
        ):
            raise ValueError("experiment evolution rows must match time points")
        if self.source_density_rows and (
            len(self.source_density_rows) != len(self.traces)
            or any(len(row) != width for row in self.source_density_rows)
        ):
            raise ValueError("experiment source density rows must match traces and grid")
        density_rows = (*self.mean_density_rows, *self.standard_deviation_rows)
        if any(len(row) != width for row in density_rows):
            raise ValueError("experiment evolution density rows must match the logM grid")
        object.__setattr__(self, "metric_series", MappingProxyType(dict(self.metric_series)))
        object.__setattr__(
            self,
            "cumulative_thresholds",
            MappingProxyType(dict(self.cumulative_thresholds)),
        )
        object.__setattr__(
            self,
            "molecular_weight_bands",
            MappingProxyType(dict(self.molecular_weight_bands)),
        )


class JointPeakCenterMode(StrEnum):
    LINEAR = "linear"
    FREE_SMOOTH = "free_smooth"


class JointPeakWidthMode(StrEnum):
    SHARED = "shared"
    LINEAR = "linear"
    FREE_SMOOTH = "free_smooth"


@dataclass(frozen=True, slots=True)
class JointPeakFitSettings:
    component_count: int = 2
    center_mode: JointPeakCenterMode = JointPeakCenterMode.LINEAR
    width_mode: JointPeakWidthMode = JointPeakWidthMode.SHARED
    smoothness: float = 0.2
    multi_start: int = 2
    random_seed: int = 0
    max_nfev: int = 2500
    reference_sample_id: str | None = None
    reference_axis_value: float | None = None
    reference_centers_log10_m: tuple[float, ...] = ()
    reference_strength: float = 1.0
    reference_fwhm_log10_m: tuple[float, ...] = ()
    warm_start_vector: tuple[float, ...] = ()
    reference_scheme_id: str | None = None
    reference_scheme_name: str | None = None
    reference_calibration_id: str | None = None
    reference_analysis_version: int | None = None
    warm_start_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.component_count <= 15:
            raise ValueError("joint peak fitting supports one to fifteen components")
        if not math.isfinite(self.smoothness) or self.smoothness < 0:
            raise ValueError("joint peak smoothness must be finite and non-negative")
        if self.multi_start < 1 or self.max_nfev < 1:
            raise ValueError("joint peak solver limits must be positive")
        if self.reference_sample_id is not None and not self.reference_sample_id.strip():
            raise ValueError("joint peak reference sample ID must not be blank")
        if self.reference_axis_value is not None and not math.isfinite(self.reference_axis_value):
            raise ValueError("joint peak reference axis value must be finite")
        if self.reference_centers_log10_m and (
            len(self.reference_centers_log10_m) != self.component_count
            or any(not math.isfinite(value) for value in self.reference_centers_log10_m)
            or any(right <= left for left, right in pairwise(self.reference_centers_log10_m))
        ):
            raise ValueError(
                "joint peak reference centers must be finite, increasing, and match component count"
            )
        if not math.isfinite(self.reference_strength) or self.reference_strength < 0.0:
            raise ValueError("joint peak reference strength must be finite and non-negative")
        if self.reference_fwhm_log10_m and (
            len(self.reference_fwhm_log10_m) != self.component_count
            or any(
                not math.isfinite(value) or value <= 0.0 for value in self.reference_fwhm_log10_m
            )
        ):
            raise ValueError(
                "joint peak reference widths must be finite, positive, and match component count"
            )
        if any(not math.isfinite(value) for value in self.warm_start_vector):
            raise ValueError("joint peak warm-start parameters must be finite")


@dataclass(frozen=True, slots=True)
class JointPeakTrajectoryPoint:
    axis_value: float
    center_log10_m: float
    center_molecular_weight: float
    width_log10_m: float
    area_fraction: float
    component_area: float
    detection_status: str = "detected"
    identity_confidence: float = 1.0

    def __post_init__(self) -> None:
        values = (
            self.axis_value,
            self.center_log10_m,
            self.center_molecular_weight,
            self.width_log10_m,
            self.area_fraction,
            self.component_area,
            self.identity_confidence,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("joint peak trajectory values must be finite")
        if self.center_molecular_weight <= 0 or self.width_log10_m <= 0:
            raise ValueError("joint peak center molecular weight and width must be positive")
        if not 0 <= self.area_fraction <= 1 + 1e-9 or self.component_area < 0:
            raise ValueError("joint peak areas must be non-negative fractions")
        if self.detection_status not in {"detected", "weak", "not_detected"}:
            raise ValueError("unknown joint peak detection status")
        if not 0.0 <= self.identity_confidence <= 1.0:
            raise ValueError("joint peak identity confidence must be between zero and one")


@dataclass(frozen=True, slots=True)
class JointPeakComponentTrajectory:
    component_index: int
    label: str
    points: tuple[JointPeakTrajectoryPoint, ...]

    def __post_init__(self) -> None:
        if self.component_index < 1 or not self.label.strip() or not self.points:
            raise ValueError("joint peak component requires an index, label, and points")


@dataclass(frozen=True, slots=True)
class PeakGroupDefinition:
    group_id: str
    name: str
    component_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not (
                self.group_id.startswith("G")
                and self.group_id[1:].isdigit()
                and int(self.group_id[1:]) > 0
            )
            or not self.name.strip()
            or not self.component_ids
        ):
            raise ValueError("peak group requires a stable G ID, name, and members")
        if len(set(self.component_ids)) != len(self.component_ids):
            raise ValueError("peak group membership must not repeat a component")


@dataclass(frozen=True, slots=True)
class JointPeakFitResult:
    experiment_id: str
    axis_name: str
    axis_unit: str
    axis_values: tuple[float, ...]
    log10_molecular_weight_grid: tuple[float, ...]
    observed_rows: tuple[tuple[float, ...], ...]
    fitted_rows: tuple[tuple[float, ...], ...]
    residual_rows: tuple[tuple[float, ...], ...]
    component_rows: tuple[tuple[tuple[float, ...], ...], ...]
    trajectories: tuple[JointPeakComponentTrajectory, ...]
    row_rmse: tuple[float, ...]
    row_r_squared: tuple[float, ...]
    global_rmse: float
    global_r_squared: float
    settings: JointPeakFitSettings
    warnings: tuple[str, ...] = ()
    algorithm_version: str = "joint-reference-checkpoint-v3"
    completion_status: str = "completed"
    nfev: int = 0
    objective: float | None = None
    initial_objective: float | None = None
    solver_state: tuple[float, ...] = ()
    component_labels: tuple[tuple[str, str], ...] = ()
    input_fingerprint: str = ""
    source_sample_ids: tuple[str, ...] = ()
    source_sample_names: tuple[str, ...] = ()
    peak_groups: tuple[PeakGroupDefinition, ...] = ()

    def __post_init__(self) -> None:
        row_count = len(self.axis_values)
        if self.source_sample_ids and len(self.source_sample_ids) != row_count:
            raise ValueError("joint source sample IDs must align with result rows")
        if self.source_sample_names and len(self.source_sample_names) != row_count:
            raise ValueError("joint source sample names must align with result rows")
        grid_size = len(self.log10_molecular_weight_grid)
        if row_count < 2 or grid_size < 5:
            raise ValueError("joint peak fit needs at least two rows and five grid points")
        aligned_rows = (*self.observed_rows, *self.fitted_rows, *self.residual_rows)
        if any(len(row) != grid_size for row in aligned_rows):
            raise ValueError("joint peak fit rows must match the molecular-weight grid")
        if not (
            len(self.observed_rows)
            == len(self.fitted_rows)
            == len(self.residual_rows)
            == len(self.component_rows)
            == len(self.row_rmse)
            == len(self.row_r_squared)
            == row_count
        ):
            raise ValueError("joint peak fit row collections must match axis values")
        if any(
            len(components) != self.settings.component_count
            or any(len(curve) != grid_size for curve in components)
            for components in self.component_rows
        ):
            raise ValueError("joint peak component arrays do not match the requested model")
        if len(self.trajectories) != self.settings.component_count:
            raise ValueError("joint peak trajectories do not match the requested model")
        if self.completion_status not in {"completed", "cancelled", "evaluation_limit"}:
            raise ValueError("unknown joint peak completion status")
        if self.nfev < 0:
            raise ValueError("joint peak evaluation count must be non-negative")
        if self.objective is not None and not math.isfinite(self.objective):
            raise ValueError("joint peak objective must be finite when present")
        if self.initial_objective is not None and not math.isfinite(self.initial_objective):
            raise ValueError("joint peak initial objective must be finite when present")
        if any(not math.isfinite(value) for value in self.solver_state):
            raise ValueError("joint peak solver state must be finite")
        expected_labels = tuple(
            f"P{index}" for index in range(1, self.settings.component_count + 1)
        )
        label_ids = tuple(item[0] for item in self.component_labels)
        if len({group.group_id for group in self.peak_groups}) != len(self.peak_groups):
            raise ValueError("peak group IDs must be unique")
        if any(
            identity not in expected_labels
            for group in self.peak_groups
            for identity in group.component_ids
        ):
            raise ValueError("peak group references an unknown stable component")
        if label_ids and label_ids != expected_labels:
            raise ValueError("joint peak labels must follow stable P identity order")


@dataclass(frozen=True, slots=True)
class KineticFitResult:
    """Phenomenological integrated-rate fit for one metric and condition group."""

    metric: str
    group_label: str
    model: KineticModel
    order: float
    axis_values: tuple[float, ...]
    observed_values: tuple[float, ...]
    fitted_values: tuple[float, ...]
    rate_constant: float
    initial_value: float
    r_squared: float
    rmse: float
    equation: str
    source_sample_ids: tuple[str, ...] = ()
    segment_id: str = "full"
    segment_label: str = "全范围"
    lower_axis: float | None = None
    upper_axis: float | None = None
    source_member_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        size = len(self.axis_values)
        if size < 2 or len(self.observed_values) != size or len(self.fitted_values) != size:
            raise ValueError("kinetic fit arrays must have the same length and at least two points")
        if not all(
            math.isfinite(value)
            for value in (
                self.order,
                self.rate_constant,
                self.initial_value,
                self.r_squared,
                self.rmse,
            )
        ):
            raise ValueError("kinetic fit statistics must be finite")


@dataclass(frozen=True, slots=True)
class KineticObservation:
    """Traceable member-level decision used to build kinetic aggregates."""

    member_id: str
    sample_id: str
    sample_name: str
    axis_value: float
    observed_value: float
    group_label: str
    segment_id: str | None
    segment_label: str | None
    included: bool
    decision_reason: str = ""
    segment_ids: tuple[str, ...] = ()
    segment_labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.segment_ids) != len(self.segment_labels):
            raise ValueError("kinetic observation segment IDs and labels must align")


@dataclass(frozen=True, slots=True)
class ArrheniusFitResult:
    metric: str
    kinetic_model: KineticModel
    temperature_factor_id: str
    temperature_unit: str
    temperatures_k: tuple[float, ...]
    rate_constants: tuple[float, ...]
    inverse_temperatures_k: tuple[float, ...]
    log_rate_constants: tuple[float, ...]
    fitted_log_rate_constants: tuple[float, ...]
    activation_energy_j_mol: float
    pre_exponential_factor: float
    r_squared: float
    rmse: float
    rate_policy: str = "magnitude"
    temperature_interpretation: str = "auto"

    def __post_init__(self) -> None:
        size = len(self.temperatures_k)
        arrays = (
            self.rate_constants,
            self.inverse_temperatures_k,
            self.log_rate_constants,
            self.fitted_log_rate_constants,
        )
        if size < 2 or any(len(values) != size for values in arrays):
            raise ValueError("Arrhenius fit requires aligned data at two or more temperatures")
        if any(value <= 0 or not math.isfinite(value) for value in self.temperatures_k):
            raise ValueError("Arrhenius temperatures must be finite and positive")


@dataclass(frozen=True, slots=True)
class ExperimentKineticsResult:
    experiment_id: str
    metric: str
    axis_name: str
    axis_unit: str
    group_factor_id: str | None
    group_factor_name: str
    fits: tuple[KineticFitResult, ...]
    best_model_by_group: Mapping[str, KineticModel]
    arrhenius: ArrheniusFitResult | None = None
    warnings: tuple[str, ...] = ()
    algorithm_version: str = "experiment-kinetics-integrated-rate-v1"
    specification: KineticAnalysisSpec | None = None
    observations: tuple[KineticObservation, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "best_model_by_group",
            MappingProxyType(dict(self.best_model_by_group)),
        )


@dataclass(frozen=True, slots=True)
class MolecularWeightBandChange:
    label: str
    lower_molecular_weight: float | None
    upper_molecular_weight: float | None
    reference_mass_fraction: float
    candidate_mass_fraction: float
    delta_percentage_points: float


@dataclass(frozen=True, slots=True)
class DistributionDifferenceResult:
    reference_sample_id: str
    candidate_sample_id: str
    log10_molecular_weight_grid: tuple[float, ...]
    reference_density: tuple[float, ...]
    candidate_density: tuple[float, ...]
    delta_density: tuple[float, ...]
    bands: tuple[MolecularWeightBandChange, ...]
    main_change: str
    reference_trace: ExperimentTrace | None = None
    candidate_trace: ExperimentTrace | None = None
    warnings: tuple[str, ...] = ()
    algorithm_version: str = "distribution-difference-bands-v1"
