"""Versioned Phase 2 project, sample, method, and audit contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType
from typing import Any

from gpcsec.models.analysis import CalibrationStatistics
from gpcsec.models.batch import BatchRun
from gpcsec.models.calibration import (
    CalibrationReferenceMetadata,
    CalibrationStandardObservation,
)
from gpcsec.models.domain import (
    CalibrationPoint,
    IntegrationRegion,
    PeakPolarity,
    RangePolicy,
    RawChromatogram,
    RawFileRecord,
)
from gpcsec.models.experiment import ExperimentRecord
from gpcsec.models.persistence import ContainerState
from gpcsec.models.standards import CertifiedStandardLot

PROJECT_SCHEMA_VERSION = 2
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_id(value: str, label: str) -> None:
    if not SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} must contain only ASCII letters, digits, underscore, or hyphen")


@dataclass(frozen=True, slots=True)
class BaselineSpec:
    anchors: tuple[tuple[float, float], ...]
    method: str = "linear_anchors"
    schema_version: int = PROJECT_SCHEMA_VERSION
    parameters: Mapping[str, float | int] = field(default_factory=dict)
    exclusions: tuple[tuple[float, float], ...] = ()
    algorithm_version: str = "linear-anchor-v1"

    def __post_init__(self) -> None:
        minimum_anchors = {
            "linear_anchors": 2,
            "pchip_anchors": 3,
            "cubic_spline_anchors": 4,
        }.get(self.method)
        if minimum_anchors is not None and len(self.anchors) < minimum_anchors:
            raise ValueError(
                f"{self.method} baseline specification requires at least {minimum_anchors} anchors"
            )
        times = [anchor[0] for anchor in self.anchors]
        if any(right <= left for left, right in pairwise(times)):
            raise ValueError("baseline anchor times must be strictly increasing")
        if any(start >= end for start, end in self.exclusions):
            raise ValueError("baseline exclusions must have start < end")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


class BoundaryReference(StrEnum):
    """How a reusable method locates one integration boundary on a new curve."""

    ABSOLUTE_RT = "absolute_rt"
    MAIN_PEAK = "main_peak"
    REFERENCE_PEAK = "reference_peak"
    CUMULATIVE_AREA = "cumulative_area"
    LOCAL_MINIMUM = "local_minimum"
    LOCAL_MAXIMUM = "local_maximum"


@dataclass(frozen=True, slots=True)
class RelativeBoundarySpec:
    """A traceable boundary rule resolved against each target chromatogram.

    ``value`` means retention time for ``absolute_rt`` and local-extremum rules,
    an offset in minutes for peak-relative rules, and a fraction in [0, 1] for
    cumulative-area rules.  ``offset_min`` is then applied to the resolved point.
    """

    reference: BoundaryReference
    value: float
    offset_min: float = 0.0
    search_radius_min: float = 0.5
    reference_name: str = ""

    def __post_init__(self) -> None:
        if self.reference is BoundaryReference.CUMULATIVE_AREA and not 0 <= self.value <= 1:
            raise ValueError("cumulative-area boundary fraction must be between 0 and 1")
        if self.search_radius_min <= 0:
            raise ValueError("relative-boundary search radius must be positive")


@dataclass(frozen=True, slots=True)
class RelativeIntegrationRegionSpec:
    region_id: str
    name: str
    start: RelativeBoundarySpec
    end: RelativeBoundarySpec
    polarity: PeakPolarity = PeakPolarity.POSITIVE
    include_in_moments: bool = True
    range_policy: RangePolicy = RangePolicy.EXPERT_EXTRAPOLATE

    def __post_init__(self) -> None:
        _validate_id(self.region_id, "relative integration region ID")
        if not self.name.strip():
            raise ValueError("relative integration region name must not be blank")


@dataclass(frozen=True, slots=True)
class AnalysisMethod:
    method_id: str
    name: str
    baseline_method: str = "linear_anchors"
    signal_weight_policy: str = "direction_clipped_nonnegative"
    schema_version: int = PROJECT_SCHEMA_VERSION
    baseline_spec: BaselineSpec | None = None
    integration_regions: tuple[IntegrationRegion, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)
    algorithm_versions: Mapping[str, str] = field(default_factory=dict)
    revision: int = 1
    parent_method_id: str | None = None
    relative_regions: tuple[RelativeIntegrationRegionSpec, ...] = ()
    inherit_baseline_algorithm_only: bool = False
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_id(self.method_id, "method_id")
        if not self.name.strip():
            raise ValueError("method name must not be blank")
        if self.revision < 1:
            raise ValueError("method revision must be positive")
        if self.parent_method_id is not None:
            _validate_id(self.parent_method_id, "parent_method_id")
        relative_ids = [item.region_id for item in self.relative_regions]
        if len(relative_ids) != len(set(relative_ids)):
            raise ValueError("relative integration region IDs must be unique")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        object.__setattr__(
            self, "algorithm_versions", MappingProxyType(dict(self.algorithm_versions))
        )


@dataclass(frozen=True, slots=True)
class CalibrationRecord:
    calibration_id: str
    name: str
    raw_file: RawFileRecord
    points: tuple[CalibrationPoint, ...]
    degree: int
    coefficients: tuple[float, ...]
    scaled_coefficients: tuple[float, ...]
    valid_rt_min: float
    valid_rt_max: float
    monotonic_direction: int
    statistics: CalibrationStatistics
    algorithm_version: str
    schema_version: int = PROJECT_SCHEMA_VERSION
    reference_metadata: CalibrationReferenceMetadata = field(
        default_factory=CalibrationReferenceMetadata
    )
    standard_observations: tuple[CalibrationStandardObservation, ...] = ()
    evidence_kind: str = "imported_pdf"
    model_kind: str = "polynomial"

    def __post_init__(self) -> None:
        _validate_id(self.calibration_id, "calibration_id")
        if not self.name.strip() or len(self.points) <= self.degree:
            raise ValueError("calibration name and sufficient points are required")
        point_domain = self.degree == 0 and self.valid_rt_min == self.valid_rt_max
        if (
            self.valid_rt_min >= self.valid_rt_max and not point_domain
        ) or self.monotonic_direction not in (-1, 0, 1):
            raise ValueError("calibration range or monotonic direction is invalid")
        if not self.evidence_kind.strip() or not self.model_kind.strip():
            raise ValueError("calibration evidence and model kinds must not be blank")


@dataclass(frozen=True, slots=True)
class SampleRecord:
    sample_id: str
    batch_id: str
    name: str
    raw_file: RawFileRecord
    chromatogram: RawChromatogram
    baseline_spec: BaselineSpec | None = None
    integration_regions: tuple[IntegrationRegion, ...] = ()
    analysis_version: int = 0
    method_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = PROJECT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_id(self.sample_id, "sample_id")
        _validate_id(self.batch_id, "batch_id")
        if not self.name.strip():
            raise ValueError("sample name must not be blank")
        if self.raw_file.sha256 != self.chromatogram.source_sha256:
            raise ValueError("sample raw file and chromatogram hashes differ")
        if self.analysis_version < 0:
            raise ValueError("analysis_version must not be negative")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


class AnalysisBranchStatus(StrEnum):
    DRAFT = "draft"
    FORMAL = "formal"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class AnalysisBranchRecord:
    """One named analysis alternative without overwriting the formal sample state."""

    branch_id: str
    sample_id: str
    name: str
    status: AnalysisBranchStatus
    created_utc: str
    modified_utc: str
    source_analysis_version: int
    baseline_spec: BaselineSpec | None = None
    integration_regions: tuple[IntegrationRegion, ...] = ()
    calibration_id: str | None = None
    method_id: str | None = None
    peak_fit: Mapping[str, Any] = field(default_factory=dict)
    report_options: Mapping[str, Any] = field(default_factory=dict)
    result_snapshot: Mapping[str, float | int | str | bool | None] = field(default_factory=dict)
    parent_branch_id: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        _validate_id(self.branch_id, "branch_id")
        _validate_id(self.sample_id, "branch sample_id")
        if self.method_id is not None:
            _validate_id(self.method_id, "branch method_id")
        if self.calibration_id is not None:
            _validate_id(self.calibration_id, "branch calibration_id")
        if self.parent_branch_id is not None:
            _validate_id(self.parent_branch_id, "parent_branch_id")
        if not self.name.strip() or not self.created_utc or not self.modified_utc:
            raise ValueError("branch name and timestamps are required")
        if self.source_analysis_version < 0:
            raise ValueError("branch source analysis version must not be negative")
        object.__setattr__(self, "peak_fit", MappingProxyType(dict(self.peak_fit)))
        object.__setattr__(self, "report_options", MappingProxyType(dict(self.report_options)))
        object.__setattr__(self, "result_snapshot", MappingProxyType(dict(self.result_snapshot)))


@dataclass(frozen=True, slots=True)
class MolecularWeightBandDefinition:
    band_id: str
    name: str
    lower_molecular_weight: float | None
    upper_molecular_weight: float | None
    color: str = ""

    def __post_init__(self) -> None:
        _validate_id(self.band_id, "molecular-weight band ID")
        if not self.name.strip():
            raise ValueError("molecular-weight band name must not be blank")
        if self.lower_molecular_weight is not None and self.lower_molecular_weight <= 0:
            raise ValueError("molecular-weight band lower bound must be positive")
        if self.upper_molecular_weight is not None and self.upper_molecular_weight <= 0:
            raise ValueError("molecular-weight band upper bound must be positive")
        if (
            self.lower_molecular_weight is not None
            and self.upper_molecular_weight is not None
            and self.lower_molecular_weight >= self.upper_molecular_weight
        ):
            raise ValueError("molecular-weight band bounds must be increasing")


@dataclass(frozen=True, slots=True)
class BatchRecord:
    batch_id: str
    name: str
    sample_ids: tuple[str, ...] = ()
    schema_version: int = PROJECT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_id(self.batch_id, "batch_id")
        if not self.name.strip():
            raise ValueError("batch name must not be blank")
        if len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError("batch sample IDs must be unique")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    timestamp_utc: str
    action: str
    actor: str
    details: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = PROJECT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_id(self.event_id, "event_id")
        if not self.timestamp_utc or not self.action or not self.actor:
            raise ValueError("audit timestamp, action, and actor are required")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@dataclass(frozen=True, slots=True)
class ProjectManifest:
    project_id: str
    name: str
    created_utc: str
    modified_utc: str
    software_version: str
    schema_version: int = PROJECT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_id(self.project_id, "project_id")
        if not self.name.strip() or not self.created_utc or not self.modified_utc:
            raise ValueError("project name and timestamps are required")


@dataclass(frozen=True, slots=True)
class ProjectDocument:
    manifest: ProjectManifest
    batches: tuple[BatchRecord, ...]
    samples: tuple[SampleRecord, ...]
    methods: tuple[AnalysisMethod, ...] = ()
    audit_events: tuple[AuditEvent, ...] = ()
    calibrations: tuple[CalibrationRecord, ...] = ()
    schema_version: int = PROJECT_SCHEMA_VERSION
    batch_runs: tuple[BatchRun, ...] = ()
    experiments: tuple[ExperimentRecord, ...] = ()
    standard_lots: tuple[CertifiedStandardLot, ...] = ()
    analysis_branches: tuple[AnalysisBranchRecord, ...] = ()
    molecular_weight_bands: tuple[MolecularWeightBandDefinition, ...] = ()
    container_state: ContainerState = field(
        default_factory=ContainerState, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        batch_ids = [batch.batch_id for batch in self.batches]
        sample_ids = [sample.sample_id for sample in self.samples]
        if len(set(batch_ids)) != len(batch_ids) or len(set(sample_ids)) != len(sample_ids):
            raise ValueError("project batch and sample IDs must be unique")
        known_batches = set(batch_ids)
        known_samples = set(sample_ids)
        if any(sample.batch_id not in known_batches for sample in self.samples):
            raise ValueError("every sample must reference an existing batch")
        for batch in self.batches:
            if any(sample_id not in known_samples for sample_id in batch.sample_ids):
                raise ValueError("batch references an unknown sample")
        referenced = [sample_id for batch in self.batches for sample_id in batch.sample_ids]
        if sorted(referenced) != sorted(sample_ids):
            raise ValueError("each sample must occur in exactly one batch")
        calibration_ids = [item.calibration_id for item in self.calibrations]
        if len(set(calibration_ids)) != len(calibration_ids):
            raise ValueError("project calibration IDs must be unique")
        method_ids = [item.method_id for item in self.methods]
        if len(set(method_ids)) != len(method_ids):
            raise ValueError("project method IDs must be unique")
        known_methods = set(method_ids)
        if any(
            sample.method_id not in known_methods for sample in self.samples if sample.method_id
        ):
            raise ValueError("sample references an unknown method")
        run_ids = [run.run_id for run in self.batch_runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("project batch run IDs must be unique")
        experiment_ids = [experiment.experiment_id for experiment in self.experiments]
        if len(experiment_ids) != len(set(experiment_ids)):
            raise ValueError("project experiment IDs must be unique")
        lot_ids = [lot.lot_id for lot in self.standard_lots]
        if len(lot_ids) != len(set(lot_ids)):
            raise ValueError("project standard lot IDs must be unique")
        known_lots = set(lot_ids)
        known_components = {
            (lot.lot_id, component.component_id)
            for lot in self.standard_lots
            for component in lot.components
        }
        for calibration in self.calibrations:
            for observation in calibration.standard_observations:
                if observation.standard_lot_id is None:
                    continue
                if observation.standard_lot_id not in known_lots:
                    raise ValueError("calibration observation references an unknown standard lot")
                if (
                    observation.standard_component_id is not None
                    and (observation.standard_lot_id, observation.standard_component_id)
                    not in known_components
                ):
                    raise ValueError(
                        "calibration observation references an unknown standard component"
                    )
        for experiment in self.experiments:
            if any(member.sample_id not in known_samples for member in experiment.members):
                raise ValueError("experiment references an unknown sample")
        branch_ids = [branch.branch_id for branch in self.analysis_branches]
        if len(branch_ids) != len(set(branch_ids)):
            raise ValueError("project analysis branch IDs must be unique")
        known_calibrations = set(calibration_ids)
        for branch in self.analysis_branches:
            if branch.sample_id not in known_samples:
                raise ValueError("analysis branch references an unknown sample")
            if branch.method_id is not None and branch.method_id not in known_methods:
                raise ValueError("analysis branch references an unknown method")
            if (
                branch.calibration_id is not None
                and branch.calibration_id not in known_calibrations
            ):
                raise ValueError("analysis branch references an unknown calibration")
            if branch.parent_branch_id is not None and branch.parent_branch_id not in branch_ids:
                raise ValueError("analysis branch references an unknown parent branch")
        formal_samples = [
            branch.sample_id
            for branch in self.analysis_branches
            if branch.status is AnalysisBranchStatus.FORMAL
        ]
        if len(formal_samples) != len(set(formal_samples)):
            raise ValueError("each sample may have at most one formal analysis branch")
        band_ids = [band.band_id for band in self.molecular_weight_bands]
        if len(band_ids) != len(set(band_ids)):
            raise ValueError("molecular-weight band IDs must be unique")
