"""System-suitability snapshot and control-chart contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class SuitabilityStatus(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    INSUFFICIENT_HISTORY = "insufficient_history"
    NOT_AVAILABLE = "not_available"


@dataclass(frozen=True, slots=True)
class SuitabilityMetric:
    code: str
    group: str
    label: str
    value: float | None
    unit: str = ""
    reference_value: float | None = None
    delta: float | None = None
    lower_limit: float | None = None
    upper_limit: float | None = None
    status: SuitabilityStatus = SuitabilityStatus.NOT_AVAILABLE
    interpretation: str = ""


@dataclass(frozen=True, slots=True)
class SystemSuitabilitySnapshot:
    snapshot_id: str
    timestamp_utc: str
    project_id: str
    sample_id: str
    sample_name: str
    calibration_id: str
    calibration_name: str
    analysis_version: int
    metrics: tuple[SuitabilityMetric, ...]
    source_hashes: tuple[str, ...]
    settings: Mapping[str, float | str | bool | None] = field(default_factory=dict)
    algorithm_version: str = "system-suitability-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))


@dataclass(frozen=True, slots=True)
class ControlChartPoint:
    timestamp_utc: str
    value: float
    center_line: float
    lower_control_limit: float
    upper_control_limit: float
    rule_hits: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ControlChartResult:
    metric_code: str
    points: tuple[ControlChartPoint, ...]
    status: SuitabilityStatus
    warning: str = ""
    baseline_count: int = 0
    algorithm_version: str = "descriptive-control-chart-v1"
