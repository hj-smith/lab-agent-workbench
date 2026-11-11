"""Scientific sensitivity, model-dependence, and Monte Carlo result contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class SensitivityLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    NOT_ASSESSED = "not_assessed"


class ResultQualityStatus(StrEnum):
    PASS = "pass"
    REVIEW = "review"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ResultQualityEvidenceItem:
    code: str
    label: str
    value: str
    status: ResultQualityStatus
    interpretation: str = ""


@dataclass(frozen=True, slots=True)
class ResultQualityEvidence:
    result_name: str
    result_value: float | None
    unit: str
    status: ResultQualityStatus
    items: tuple[ResultQualityEvidenceItem, ...]
    rationale: str
    algorithm_version: str = "result-quality-evidence-v1"


@dataclass(frozen=True, slots=True)
class RobustnessSettings:
    boundary_delta_min: float = 0.05
    baseline_offset: float = 0.0
    anchor_time_delta_min: float = 0.02
    calibration_rt_sigma_min: float = 0.01
    calibration_log_m_sigma: float = 0.005
    noise_sigma: float = 0.0
    clipping_fraction: float = 0.0
    low_threshold_percent: float = 2.0
    high_threshold_percent: float = 5.0
    monte_carlo_iterations: int = 500
    random_seed: int = 20260812

    def __post_init__(self) -> None:
        nonnegative = (
            self.boundary_delta_min,
            self.baseline_offset,
            self.anchor_time_delta_min,
            self.calibration_rt_sigma_min,
            self.calibration_log_m_sigma,
            self.noise_sigma,
            self.clipping_fraction,
            self.low_threshold_percent,
            self.high_threshold_percent,
        )
        if not all(math.isfinite(value) and value >= 0 for value in nonnegative):
            raise ValueError(
                "robustness perturbations and thresholds must be finite and nonnegative"
            )
        if self.clipping_fraction >= 1:
            raise ValueError("clipping fraction must be less than one")
        if self.low_threshold_percent > self.high_threshold_percent:
            raise ValueError("low sensitivity threshold must not exceed high threshold")
        if self.monte_carlo_iterations < 20:
            raise ValueError("Monte Carlo requires at least 20 iterations")


@dataclass(frozen=True, slots=True)
class PerturbationScenarioResult:
    scenario_id: str
    category: str
    label: str
    successful: bool
    parameter_values: Mapping[str, float | None] = field(default_factory=dict)
    extrapolated_mass_fraction: float | None = None
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameter_values", MappingProxyType(dict(self.parameter_values)))


@dataclass(frozen=True, slots=True)
class ParameterSensitivityResult:
    parameter: str
    nominal: float | None
    low: float | None
    high: float | None
    maximum_absolute_change: float | None
    maximum_relative_change_percent: float | None
    sensitivity: SensitivityLevel
    dominant_scenario: str | None = None
    dominant_category: str | None = None


@dataclass(frozen=True, slots=True)
class DeterministicSensitivityResult:
    sample_id: str
    calibration_id: str
    settings: RobustnessSettings
    parameters: tuple[ParameterSensitivityResult, ...]
    scenarios: tuple[PerturbationScenarioResult, ...]
    model_dependence: tuple[PerturbationScenarioResult, ...] = ()
    warnings: tuple[str, ...] = ()
    category_parameters: Mapping[str, tuple[ParameterSensitivityResult, ...]] = field(
        default_factory=dict
    )
    nominal_extrapolated_mass_fraction: float | None = None
    algorithm_version: str = "scientific-sensitivity-v1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "category_parameters",
            MappingProxyType(
                {key: tuple(value) for key, value in self.category_parameters.items()}
            ),
        )


@dataclass(frozen=True, slots=True)
class MonteCarloParameterResult:
    parameter: str
    nominal: float | None
    mean: float | None
    standard_deviation: float | None
    q025: float | None
    median: float | None
    q975: float | None
    coefficient_of_variation_percent: float | None


@dataclass(frozen=True, slots=True)
class MonteCarloUncertaintyResult:
    sample_id: str
    calibration_id: str
    settings: RobustnessSettings
    successful_iterations: int
    failed_iterations: int
    parameters: tuple[MonteCarloParameterResult, ...]
    failure_reasons: Mapping[str, int] = field(default_factory=dict)
    interpretation: str = (
        "Seeded simulation propagation interval; it is not experimental repeatability or a "
        "probability statement."
    )
    algorithm_version: str = "monte-carlo-propagation-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "failure_reasons", MappingProxyType(dict(self.failure_reasons)))
