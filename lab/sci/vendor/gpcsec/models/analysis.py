"""Typed Phase 1 analysis, import, and result models."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from gpcsec.models.domain import AreaBreakdown, IntegrationRegion, RawChromatogram, RawFileRecord

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ParsedRowIssue:
    line_number: int
    raw_line: str
    reason: str


@dataclass(frozen=True, slots=True)
class FormatProbe:
    encoding: str
    delimiter: str
    has_header: bool
    decimal_mark: str
    column_count: int
    confidence: float
    newline: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.column_count < 1:
            raise ValueError("column_count must be positive")


@dataclass(frozen=True, slots=True)
class ImportResult:
    raw_file: RawFileRecord
    chromatogram: RawChromatogram
    probe: FormatProbe
    issues: tuple[ParsedRowIssue, ...] = ()
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ProcessingStep:
    kind: str
    algorithm_version: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.kind or not self.algorithm_version:
            raise ValueError("processing step kind and algorithm version are required")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


@dataclass(frozen=True, slots=True)
class BaselineResult:
    time_min: tuple[float, ...]
    baseline: tuple[float, ...]
    anchors: tuple[tuple[float, float], ...]
    processing_step: ProcessingStep
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if len(self.time_min) != len(self.baseline):
            raise ValueError("baseline time and values must have equal length")


@dataclass(frozen=True, slots=True)
class CalibrationStatistics:
    sse: float
    corrected_sum_squares: float
    r_squared: float | None
    rmse: float
    residual_standard_error: float | None
    point_count: int
    parameter_count: int
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        numeric = (self.sse, self.corrected_sum_squares, self.rmse)
        if self.r_squared is not None:
            numeric = (*numeric, self.r_squared)
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("calibration statistics must be finite")


@dataclass(frozen=True, slots=True)
class RegionIntegrationResult:
    region: IntegrationRegion
    time_min: tuple[float, ...]
    raw_signal: tuple[float, ...]
    baseline: tuple[float, ...]
    residual: tuple[float, ...]
    effective_signal: tuple[float, ...]
    clipped_reverse_signal: tuple[float, ...]
    segment_lengths: tuple[int, ...]
    areas: AreaBreakdown
    warnings: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class IntegrationResult:
    regions: tuple[RegionIntegrationResult, ...]
    processing_steps: tuple[ProcessingStep, ...]
    warnings: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class IntegrationAreaSummary:
    """Area accounting for independently evaluated, potentially overlapping regions."""

    independent_area_sum: float
    union_area: float
    duplicate_overlap_area: float
    duplicate_overlap_fraction: float
    region_area_fractions: tuple[float, ...]
    overlap_pairs: tuple[tuple[int, int], ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        values = (
            self.independent_area_sum,
            self.union_area,
            self.duplicate_overlap_area,
            self.duplicate_overlap_fraction,
            *self.region_area_fractions,
        )
        if not all(math.isfinite(value) and value >= 0 for value in values):
            raise ValueError("integration area summary values must be finite and nonnegative")
        if self.duplicate_overlap_fraction > 1.0 + 1e-12:
            raise ValueError("duplicate overlap fraction must not exceed one")


@dataclass(frozen=True, slots=True)
class MolecularWeightResult:
    j_minus_1: float
    j_0: float
    j_1: float
    j_2: float
    j_3: float
    mn: float
    mw: float
    mz: float
    mz_plus_1: float
    pdi: float
    mp_rt: float
    mp_log_m: float | None
    mass_d10: float | None
    mass_d50: float | None
    mass_d90: float | None
    relative_to_standards: bool = True
    warnings: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class DistributionResult:
    molecular_weight: tuple[float, ...]
    log10_molecular_weight: tuple[float, ...]
    mapped_signal: tuple[float, ...]
    density_per_log10_m: tuple[float, ...]
    density_per_m: tuple[float, ...]
    cumulative_mass_fraction: tuple[float, ...]
    total_mass_rt: float
    total_mass_log_m: float
    density_number_per_log10_m: tuple[float, ...] = ()
    density_number_per_m: tuple[float, ...] = ()
    cumulative_number_fraction: tuple[float, ...] = ()
    total_relative_number: float | None = None
    schema_version: int = SCHEMA_VERSION


class AnalysisError(ValueError):
    """Structured scientific failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
