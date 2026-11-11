"""Phase 0 domain contracts that freeze evidence and region-level scientific policy."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import StrEnum
from types import MappingProxyType
from typing import Any

SCHEMA_VERSION = 1


class PeakPolarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"

    @property
    def direction(self) -> int:
        return 1 if self is PeakPolarity.POSITIVE else -1


class RangePolicy(StrEnum):
    REJECT = "reject"
    IN_RANGE_ONLY = "in_range_only"
    EXPERT_EXTRAPOLATE = "expert_extrapolate"


@dataclass(frozen=True, slots=True)
class RawFileRecord:
    source_name: str
    sha256: str
    size_bytes: int
    original_bytes: bytes | None = field(default=None, repr=False)
    schema_version: int = SCHEMA_VERSION
    source_path: str | None = None
    imported_utc: str | None = None
    imported_utc_inferred: bool = False
    attachment_relative_path: str | None = None

    def __post_init__(self) -> None:
        if not self.source_name:
            raise ValueError("source_name must not be empty")
        if len(self.sha256) != 64 or any(char not in "0123456789abcdef" for char in self.sha256):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        if self.original_bytes is not None and self.size_bytes != len(self.original_bytes):
            raise ValueError("size_bytes must equal the original byte length")


@dataclass(frozen=True, slots=True)
class RawChromatogram:
    time_min: tuple[float, ...]
    signal: tuple[float, ...]
    detector_name: str
    source_sha256: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if len(self.time_min) != len(self.signal) or len(self.time_min) < 2:
            raise ValueError("time and signal must have the same length of at least two")
        if not all(math.isfinite(value) for value in (*self.time_min, *self.signal)):
            raise ValueError("raw arrays must contain only finite values")


@dataclass(frozen=True, slots=True)
class CalibrationPoint:
    retention_time_min: float
    molecular_weight: float
    enabled: bool = True
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not math.isfinite(self.retention_time_min):
            raise ValueError("retention time must be finite")
        if not math.isfinite(self.molecular_weight) or self.molecular_weight <= 0:
            raise ValueError("molecular weight must be finite and positive")


@dataclass(frozen=True, slots=True)
class IntegrationRegion:
    start_time_min: float
    end_time_min: float
    polarity: PeakPolarity
    include_in_moments: bool = True
    range_policy: RangePolicy = RangePolicy.IN_RANGE_ONLY
    creation_method: str = "manual"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.start_time_min, self.end_time_min)):
            raise ValueError("region bounds must be finite")
        if self.start_time_min >= self.end_time_min:
            raise ValueError("region start must be less than region end")
        if not isinstance(self.polarity, PeakPolarity):
            raise TypeError("polarity must be an explicit PeakPolarity")
        if not isinstance(self.range_policy, RangePolicy):
            raise TypeError("range_policy must be an explicit RangePolicy")
        if not self.creation_method.strip():
            raise ValueError("creation_method must not be blank")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class AreaBreakdown:
    raw_signed_area: float
    positive_area_after_baseline: float
    negative_magnitude_after_baseline: float
    direction_corrected_area: float
    effective_nonnegative_area: float
    clipped_reverse_residual_area: float
    excluded_area: float
    in_calibration_area: float
    out_of_calibration_high_m_area: float
    out_of_calibration_low_m_area: float
    absolute_area: float | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        values = (
            value for item in fields(self) if isinstance((value := getattr(self, item.name)), float)
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("area values must be finite")
