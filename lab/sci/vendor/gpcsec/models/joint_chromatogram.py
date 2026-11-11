"""Explicit, immutable contracts for joint fitting in retention/response space."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise

from gpcsec.models.advanced import PeakShape
from gpcsec.models.experiment import JointPeakCenterMode, JointPeakWidthMode, PeakGroupDefinition

JOINT_CHROMATOGRAM_ALGORITHM = "joint-chromatogram-fit-v1"


def valid_interval(interval: tuple[float, float], name: str) -> None:
    if len(interval) != 2 or not all(map(math.isfinite, interval)) or interval[0] >= interval[1]:
        raise ValueError(f"{name} must contain finite start < end")


class RetentionCoordinate(StrEnum):
    TIME = "retention_time"
    VOLUME = "retention_volume"


class RetentionAlignmentMethod(StrEnum):
    MANUAL = "manual"
    REFERENCE_WINDOW_CORRELATION = "reference_window_correlation"


@dataclass(frozen=True, slots=True)
class JointCalibrationSnapshot:
    calibration_id: str
    source_sha256: str
    algorithm_version: str
    model_kind: str
    coordinate_kind: RetentionCoordinate
    coordinate_unit: str
    valid_interval: tuple[float, float]
    monotonic_direction: int
    coefficients: tuple[float, ...]
    scaled_coefficients: tuple[float, ...]
    knots_x: tuple[float, ...]
    knots_log_m: tuple[float, ...]

    def __post_init__(self) -> None:
        valid_interval(self.valid_interval, "calibration interval")
        if self.model_kind not in {"polynomial", "pchip"}:
            raise ValueError("unsupported joint calibration model")
        if self.model_kind == "polynomial" and not self.coefficients:
            raise ValueError("polynomial calibration coefficients are required")
        if self.model_kind == "pchip" and (
            len(self.knots_x) < 2
            or len(self.knots_x) != len(self.knots_log_m)
            or any(b <= a for a, b in pairwise(self.knots_x))
        ):
            raise ValueError("PCHIP calibration requires ordered matching knots")
        if not all(
            map(
                math.isfinite,
                (*self.coefficients, *self.scaled_coefficients, *self.knots_x, *self.knots_log_m),
            )
        ):
            raise ValueError("calibration snapshot must be finite")


@dataclass(frozen=True, slots=True)
class JointChromatogramTrace:
    sample_id: str
    sample_name: str
    experiment_member_id: str
    experiment_axis_value: float
    experiment_axis_unit: str
    analysis_version: int
    source_sha256: str
    detector_id: str
    coordinate_kind: RetentionCoordinate
    coordinate_unit: str
    retention_x: tuple[float, ...]
    response_y: tuple[float, ...]
    calibration: JointCalibrationSnapshot | None
    baseline_provenance: str

    def __post_init__(self) -> None:
        if not self.sample_id or not self.sample_name or not self.detector_id.strip():
            raise ValueError("sample identity and detector/channel identity are required")
        if self.analysis_version < 1 or not self.baseline_provenance:
            raise ValueError(f"{self.sample_name}: applied baseline analysis is required")
        if len(self.source_sha256) != 64:
            raise ValueError("source SHA-256 is required")
        if not self.coordinate_unit or not self.experiment_axis_unit:
            raise ValueError("retention and experiment coordinate units are required")
        if len(self.retention_x) < 5 or len(self.retention_x) != len(self.response_y):
            raise ValueError(f"{self.sample_name}: at least five matching X/Y points are required")
        if not all(
            map(math.isfinite, (*self.retention_x, *self.response_y, self.experiment_axis_value))
        ):
            raise ValueError(f"{self.sample_name}: chromatogram values must be finite")
        if any(b <= a for a, b in pairwise(self.retention_x)):
            raise ValueError(f"{self.sample_name}: retention coordinate must strictly increase")
        if self.calibration is not None and (
            self.coordinate_kind != self.calibration.coordinate_kind
            or self.coordinate_unit != self.calibration.coordinate_unit
        ):
            raise ValueError("trace and calibration retention coordinates must match")


@dataclass(frozen=True, slots=True)
class RetentionAlignmentSettings:
    enabled: bool
    method: RetentionAlignmentMethod | None
    reference_sample_id: str | None
    reference_interval: tuple[float, float] | None
    max_abs_shift: float | None
    manual_shifts: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        if not self.enabled:
            if (
                any(
                    value is not None
                    for value in (
                        self.method,
                        self.reference_sample_id,
                        self.reference_interval,
                        self.max_abs_shift,
                    )
                )
                or self.manual_shifts
            ):
                raise ValueError("disabled alignment must not carry active correction settings")
            return
        if not isinstance(self.method, RetentionAlignmentMethod):
            raise TypeError("enabled alignment requires a RetentionAlignmentMethod enum")
        if self.method is None or not self.reference_sample_id:
            raise ValueError("enabled alignment requires a method and reference sample")
        if self.method is RetentionAlignmentMethod.MANUAL:
            if self.reference_interval is not None or self.max_abs_shift is not None:
                raise ValueError("manual correction does not use a correlation window")
            ids = [item[0] for item in self.manual_shifts]
            if len(ids) != len(set(ids)) or not all(
                math.isfinite(x) for _, x in self.manual_shifts
            ):
                raise ValueError("manual shifts must be finite with unique sample IDs")
        else:
            if self.manual_shifts or self.reference_interval is None or self.max_abs_shift is None:
                raise ValueError(
                    "correlation requires an explicit reference interval and max shift"
                )
            valid_interval(self.reference_interval, "alignment reference interval")
            if not math.isfinite(self.max_abs_shift) or self.max_abs_shift <= 0:
                raise ValueError("maximum alignment shift must be finite and positive")


@dataclass(frozen=True, slots=True)
class SampleRetentionAlignment:
    sample_id: str
    shift: float
    score: float | None
    boundary_hit: bool

    def __post_init__(self) -> None:
        if not math.isfinite(self.shift) or (
            self.score is not None and not math.isfinite(self.score)
        ):
            raise ValueError("alignment result must be finite")


@dataclass(frozen=True, slots=True)
class RetentionAlignmentResult:
    settings: RetentionAlignmentSettings
    sample_results: tuple[SampleRetentionAlignment, ...]
    input_fingerprint: str


@dataclass(frozen=True, slots=True)
class JointChromatogramSeed:
    component_id: str
    shape: PeakShape
    center: float
    width: float
    shape_parameters: tuple[tuple[str, float], ...]
    center_bounds: tuple[float, float]
    width_bounds: tuple[float, float]

    def __post_init__(self) -> None:
        if not isinstance(self.shape, PeakShape):
            raise TypeError("reference peak shape must be a PeakShape enum")
        valid_interval(self.center_bounds, "component center bounds")
        valid_interval(self.width_bounds, "component width bounds")
        if not (
            self.center_bounds[0] <= self.center <= self.center_bounds[1]
            and 0 < self.width_bounds[0] <= self.width <= self.width_bounds[1]
        ):
            raise ValueError("reference peak center/width must lie in explicit bounds")
        required = {
            PeakShape.GAUSSIAN: set(),
            PeakShape.LORENTZIAN: set(),
            PeakShape.VOIGT: {"gamma"},
            PeakShape.PSEUDO_VOIGT: {"eta"},
            PeakShape.EMG: {"tau"},
        }[self.shape]
        values = dict(self.shape_parameters)
        if len(values) != len(self.shape_parameters) or set(values) != required:
            raise ValueError("reference peak shape parameters must be complete")
        if not all(map(math.isfinite, values.values())):
            raise ValueError("reference shape parameters must be finite")
        if any(v <= 0 for k, v in values.items() if k in {"gamma", "tau"}):
            raise ValueError("gamma/tau must be positive")
        if "eta" in values and not 0 <= values["eta"] <= 1:
            raise ValueError("pseudo-Voigt eta must lie between zero and one")


@dataclass(frozen=True, slots=True)
class JointChromatogramFitSettings:
    fit_interval: tuple[float, float]
    alignment: RetentionAlignmentSettings
    reference_sample_id: str
    reference_scheme_id: str
    reference_scheme_name: str
    reference_analysis_version: int
    seeds: tuple[JointChromatogramSeed, ...]
    center_mode: JointPeakCenterMode
    width_mode: JointPeakWidthMode
    smoothness: float
    reference_strength: float
    multi_start: int
    max_nfev: int
    random_seed: int
    tolerance: float
    derive_molecular_weight: bool
    allow_calibration_extrapolation: bool
    parameter_provenance: tuple[tuple[str, str], ...] = ()
    warm_start_vector: tuple[float, ...] = ()
    warm_start_fingerprint: str | None = None

    @property
    def component_count(self) -> int:
        return len(self.seeds)

    def __post_init__(self) -> None:
        if not isinstance(self.center_mode, JointPeakCenterMode) or not isinstance(
            self.width_mode, JointPeakWidthMode
        ):
            raise TypeError("joint center/width modes must be explicit enum values")
        valid_interval(self.fit_interval, "manually selected joint fit interval")
        if not 1 <= len(self.seeds) <= 15:
            raise ValueError("joint fitting requires one to fifteen explicit reference peaks")
        ids = [item.component_id for item in self.seeds]
        if len(set(ids)) != len(ids) or any(
            not x.startswith("P") or not x[1:].isdigit() for x in ids
        ):
            raise ValueError("component IDs must be unique P identities")
        if (
            not self.reference_sample_id
            or not self.reference_scheme_id
            or self.reference_analysis_version < 1
        ):
            raise ValueError("saved reference scheme provenance is required")
        if any(not math.isfinite(x) or x < 0 for x in (self.smoothness, self.reference_strength)):
            raise ValueError("regularization weights must be finite and nonnegative")
        if self.multi_start < 1 or self.max_nfev < 1 or not 1e-12 <= self.tolerance <= 1e-2:
            raise ValueError("solver budgets and tolerance must be explicitly valid")
        if self.warm_start_vector and (
            not self.warm_start_fingerprint or not all(map(math.isfinite, self.warm_start_vector))
        ):
            raise ValueError("continuation requires a finite complete vector and fingerprint")


@dataclass(frozen=True, slots=True)
class JointChromatogramComponentPoint:
    sample_id: str
    center_retention: float
    fwhm_retention: float
    response_area: float
    response_fraction: float
    mrep: float | None
    molecular_weight_status: str

    def __post_init__(self) -> None:
        if not all(
            map(
                math.isfinite,
                (
                    self.center_retention,
                    self.fwhm_retention,
                    self.response_area,
                    self.response_fraction,
                ),
            )
        ):
            raise ValueError("component response results must be finite")
        if (
            self.fwhm_retention <= 0
            or self.response_area < 0
            or not 0 <= self.response_fraction <= 1 + 1e-10
        ):
            raise ValueError("component widths, areas and fractions violate the result contract")
        if self.mrep is not None and (not math.isfinite(self.mrep) or self.mrep <= 0):
            raise ValueError("Mrep must be a finite positive calibrated value when available")


@dataclass(frozen=True, slots=True)
class JointChromatogramComponent:
    component_id: str
    shape: PeakShape
    points: tuple[JointChromatogramComponentPoint, ...]


@dataclass(frozen=True, slots=True)
class JointChromatogramFitResult:
    experiment_id: str
    axis_name: str
    traces: tuple[JointChromatogramTrace, ...]
    settings: JointChromatogramFitSettings
    alignment: RetentionAlignmentResult | None
    x_grid: tuple[float, ...]
    observed_rows: tuple[tuple[float, ...], ...]
    fitted_rows: tuple[tuple[float, ...], ...]
    component_rows: tuple[tuple[tuple[float, ...], ...], ...]
    components: tuple[JointChromatogramComponent, ...]
    solver_state: tuple[float, ...]
    input_fingerprint: str
    completion_status: str
    nfev: int
    objective: float
    component_labels: tuple[tuple[str, str], ...] = ()
    peak_groups: tuple[PeakGroupDefinition, ...] = ()
    algorithm_version: str = JOINT_CHROMATOGRAM_ALGORITHM

    def __post_init__(self) -> None:
        if self.algorithm_version != JOINT_CHROMATOGRAM_ALGORITHM:
            raise ValueError("not a supported chromatogram-domain result")
        n, k, m = len(self.traces), len(self.components), len(self.x_grid)
        if n < 2 or k != self.settings.component_count or m < 5:
            raise ValueError("joint chromatogram result dimensions are invalid")
        if (
            len(self.observed_rows) != n
            or len(self.fitted_rows) != n
            or len(self.component_rows) != n
        ):
            raise ValueError("joint chromatogram result rows must match samples")
        if any(len(row) != m for row in (*self.observed_rows, *self.fitted_rows)) or any(
            len(row) != k or any(len(curve) != m for curve in row) for row in self.component_rows
        ):
            raise ValueError("joint chromatogram result curves must match grid and components")
        if (
            not self.solver_state
            or not self.input_fingerprint
            or not all(map(math.isfinite, self.solver_state))
        ):
            raise ValueError("joint result requires a finite complete solver checkpoint")
        if self.completion_status not in {"completed", "cancelled", "evaluation_limit"}:
            raise ValueError("unknown joint chromatogram completion status")
        identities = tuple(c.component_id for c in self.components)
        if identities != tuple(s.component_id for s in self.settings.seeds):
            raise ValueError("result component identities must match the saved reference model")
        if any(
            tuple(p.sample_id for p in c.points) != self.source_sample_ids for c in self.components
        ):
            raise ValueError("component points must match source sample order")
        if any(not set(g.component_ids) <= set(identities) for g in self.peak_groups):
            raise ValueError("saved group references an unavailable component")

    @property
    def axis_values(self) -> tuple[float, ...]:
        return tuple(t.experiment_axis_value for t in self.traces)

    @property
    def axis_unit(self) -> str:
        return self.traces[0].experiment_axis_unit

    @property
    def source_sample_ids(self) -> tuple[str, ...]:
        return tuple(t.sample_id for t in self.traces)

    @property
    def source_sample_names(self) -> tuple[str, ...]:
        return tuple(t.sample_name for t in self.traces)
