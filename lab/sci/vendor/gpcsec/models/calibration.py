"""Calibration lifecycle, provenance, comparison, and robustness contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from gpcsec.models.domain import CalibrationPoint


class CalibrationVerificationState(StrEnum):
    DRAFT = "draft"
    VERIFIED = "verified"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class CalibrationStandardAssignment:
    """User-reviewed mapping from an imported standard trace to a certified mass."""

    sample_id: str
    standard_name: str
    molecular_weight: float
    peak_window_start_min: float
    peak_window_end_min: float
    peak_polarity: str = "positive"
    peak_method: str = "quadratic_apex"
    manual_retention_time_min: float | None = None
    enabled: bool = True
    standard_lot_id: str | None = None
    standard_component_id: str | None = None

    def __post_init__(self) -> None:
        if not self.sample_id.strip() or not self.standard_name.strip():
            raise ValueError("standard sample and name are required")
        if not math.isfinite(self.molecular_weight) or self.molecular_weight <= 0:
            raise ValueError("standard molecular weight must be finite and positive")
        if (
            not math.isfinite(self.peak_window_start_min)
            or not math.isfinite(self.peak_window_end_min)
            or self.peak_window_start_min >= self.peak_window_end_min
        ):
            raise ValueError("standard peak window is invalid")
        if self.peak_polarity not in {"positive", "negative"}:
            raise ValueError("standard peak polarity must be positive or negative")
        if self.peak_method not in {"quadratic_apex", "maximum", "centroid"}:
            raise ValueError("unknown standard peak method")
        if self.manual_retention_time_min is not None and not math.isfinite(
            self.manual_retention_time_min
        ):
            raise ValueError("manual retention time must be finite")


@dataclass(frozen=True, slots=True)
class MixedStandardCalibrationSuggestion:
    """Globally matched, review-required peak assignments for mixed-standard vials."""

    assignments: tuple[CalibrationStandardAssignment, ...]
    candidate_retention_times: Mapping[str, tuple[float, ...]]
    excluded_common_retention_times: tuple[float, ...]
    fitted_points: tuple[CalibrationPoint, ...]
    fit_rmse_log_m: float
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_retention_times",
            MappingProxyType(
                {
                    str(key): tuple(float(value) for value in values)
                    for key, values in self.candidate_retention_times.items()
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class CalibrationReferenceMetadata:
    """Experimental identity of a calibration measurement reference.

    Empty fields remain explicit rather than being silently inferred from filenames.
    """

    standard_polymer_type: str = ""
    standard_supplier: str = ""
    standard_product_lot: str = ""
    certificate_reference: str = ""
    first_use_date: str = ""
    expiry_date: str = ""
    calibration_date: str = ""
    operator: str = ""
    instrument: str = ""
    column_set_id: str = ""
    chromatographic_method_id: str = ""
    detector_model_id: str = ""
    solvent: str = ""
    temperature_c: float | None = None
    flow_rate_ml_min: float | None = None
    standard_concentration_mg_ml: float | None = None
    verification_state: CalibrationVerificationState = CalibrationVerificationState.DRAFT
    supersedes_calibration_id: str | None = None
    reason_for_recalibration: str = ""
    notes: str = ""
    extra: Mapping[str, str | float | int | bool | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, label in (
            (self.temperature_c, "temperature"),
            (self.flow_rate_ml_min, "flow rate"),
            (self.standard_concentration_mg_ml, "standard concentration"),
        ):
            if value is not None and not math.isfinite(value):
                raise ValueError(f"calibration {label} must be finite")
        if self.flow_rate_ml_min is not None and self.flow_rate_ml_min <= 0:
            raise ValueError("calibration flow rate must be positive")
        if self.standard_concentration_mg_ml is not None and self.standard_concentration_mg_ml <= 0:
            raise ValueError("standard concentration must be positive")
        object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))


@dataclass(frozen=True, slots=True)
class CalibrationStandardObservation:
    """Traceable link between a certified standard and its chromatographic peak."""

    observation_id: str
    standard_name: str
    molecular_weight: float
    retention_time_min: float
    source_sample_id: str | None = None
    source_sha256: str | None = None
    peak_method: str = "maximum_in_window"
    peak_polarity: str = "positive"
    peak_window_start_min: float | None = None
    peak_window_end_min: float | None = None
    enabled: bool = True
    notes: str = ""
    standard_lot_id: str | None = None
    standard_component_id: str | None = None

    def __post_init__(self) -> None:
        if not self.observation_id.strip() or not self.standard_name.strip():
            raise ValueError("calibration observation ID and standard name are required")
        if not math.isfinite(self.molecular_weight) or self.molecular_weight <= 0:
            raise ValueError("standard molecular weight must be finite and positive")
        if not math.isfinite(self.retention_time_min):
            raise ValueError("standard retention time must be finite")
        if self.peak_polarity not in {"positive", "negative"}:
            raise ValueError("standard peak polarity must be positive or negative")
        bounds = (self.peak_window_start_min, self.peak_window_end_min)
        if any(value is not None and not math.isfinite(value) for value in bounds):
            raise ValueError("peak-window bounds must be finite")
        if bounds[0] is not None and bounds[1] is not None and bounds[0] >= bounds[1]:
            raise ValueError("peak-window start must be less than its end")


@dataclass(frozen=True, slots=True)
class CalibrationDifferencePoint:
    retention_time_min: float
    reference_log_m: float
    candidate_log_m: float
    delta_log_m: float
    reference_slope: float
    candidate_slope: float


@dataclass(frozen=True, slots=True)
class CalibrationVersionComparison:
    reference_calibration_id: str
    candidate_calibration_id: str
    common_rt_min: float
    common_rt_max: float
    common_m_min: float
    common_m_max: float
    points: tuple[CalibrationDifferencePoint, ...]
    max_abs_delta_log_m: float
    rms_delta_log_m: float
    max_abs_slope_delta: float
    endpoint_delta_log_m: tuple[float, float]
    standard_rt_shifts_min: tuple[float, ...]
    downstream_changes_percent: Mapping[str, float] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "downstream_changes_percent",
            MappingProxyType(dict(self.downstream_changes_percent)),
        )


@dataclass(frozen=True, slots=True)
class CalibrationInfluenceResult:
    omitted_standard_index: int
    omitted_standard_name: str
    omitted_rt_min: float
    omitted_molecular_weight: float
    successful: bool
    max_abs_delta_log_m: float | None = None
    endpoint_delta_log_m: float | None = None
    downstream_changes_percent: Mapping[str, float] = field(default_factory=dict)
    message: str = ""
    max_abs_molecular_weight_change_percent: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "downstream_changes_percent",
            MappingProxyType(dict(self.downstream_changes_percent)),
        )


@dataclass(frozen=True, slots=True)
class CalibrationRobustnessResult:
    calibration_id: str
    loso: tuple[CalibrationInfluenceResult, ...]
    model_dependence: Mapping[str, float | str | None]
    endpoint_sensitivity: Mapping[str, float | str | None]
    residuals_log_m: tuple[float, ...]
    standardized_residuals: tuple[float | None, ...]
    leverage: tuple[float | None, ...]
    warnings: tuple[str, ...] = ()
    algorithm_version: str = "calibration-robustness-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_dependence", MappingProxyType(dict(self.model_dependence)))
        object.__setattr__(
            self, "endpoint_sensitivity", MappingProxyType(dict(self.endpoint_sensitivity))
        )


@dataclass(frozen=True, slots=True)
class CalibrationExpertEdit:
    """Reviewed expert changes used to derive, never overwrite, a calibration version."""

    reason: str
    points: tuple[tuple[float, float, bool], ...]
    degree: int
    valid_rt_min: float | None = None
    valid_rt_max: float | None = None
    model_kind: str = "polynomial"
    allow_range_extrapolation: bool = False
    allow_nonmonotonic: bool = False

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("expert calibration edit requires a reason")
        if self.degree < 1 or len(self.points) <= self.degree:
            raise ValueError("expert calibration edit has insufficient points")
        if (
            self.valid_rt_min is not None
            and self.valid_rt_max is not None
            and self.valid_rt_min >= self.valid_rt_max
        ):
            raise ValueError("expert calibration valid range is invalid")
