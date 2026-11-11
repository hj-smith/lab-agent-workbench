"""Adaptive physical constraints for local chromatographic components."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from itertools import pairwise

import numpy as np
import numpy.typing as npt
from scipy.optimize import brentq

from gpcsec.core.calibration import Calibration
from gpcsec.core.peak_fitting.shapes import initial_parameters, peak_fwhm
from gpcsec.models import AnalysisError, PeakComponentSpec, PeakFitSpec, PeakShape

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class ComponentConstraintContext:
    """Resolved bounds used by one constrained solver component."""

    initial_center: float
    center_bounds: tuple[float, float]
    fwhm_bounds: tuple[float, float]
    molecular_weight_bounds: tuple[float, float] | None
    user_center_constraint: bool


@dataclass(frozen=True, slots=True)
class ConstraintBuildResult:
    specification: PeakFitSpec
    components: tuple[ComponentConstraintContext, ...]
    warnings: tuple[str, ...] = ()


def _mw_window_to_rt(
    bounds: tuple[float, float],
    calibration: Calibration | None,
) -> tuple[float, float]:
    if calibration is None:
        raise AnalysisError(
            "missing_peak_calibration",
            "a calibration is required for molecular-weight peak constraints",
        )
    if calibration.monotonic_direction == 0:
        raise AnalysisError(
            "non_monotonic_peak_calibration",
            "molecular-weight peak constraints require an invertible monotonic calibration",
        )
    first = float(calibration.invert_log_m(math.log10(bounds[0])))
    second = float(calibration.invert_log_m(math.log10(bounds[1])))
    return min(first, second), max(first, second)


def _center_domain_for_log_width(
    maximum_log_width: float,
    calibration: Calibration | None,
) -> tuple[float, float]:
    """Return centres whose full requested log-M FWHM remains calibrated."""

    if calibration is None:
        raise AnalysisError(
            "missing_peak_calibration",
            "a calibration is required for log-M FWHM constraints",
        )
    if calibration.monotonic_direction == 0:
        raise AnalysisError(
            "non_monotonic_peak_calibration",
            "log-M FWHM constraints require an invertible monotonic calibration",
        )
    valid_lower = float(calibration.valid_rt_min)
    valid_upper = float(calibration.valid_rt_max)
    midpoint = (valid_lower + valid_upper) / 2.0

    def maximum_symmetric_span(center: float) -> float:
        half_span = min(center - valid_lower, valid_upper - center)
        if half_span <= 0.0:
            return 0.0
        endpoints = calibration.evaluate_log_m(
            np.asarray([center - half_span, center + half_span], dtype=np.float64),
            allow_extrapolation=False,
        )
        return abs(float(endpoints[1] - endpoints[0]))

    midpoint_span = maximum_symmetric_span(midpoint)
    if midpoint_span < maximum_log_width:
        raise AnalysisError(
            "peak_width_outside_calibration",
            "the requested log-M FWHM is wider than the calibrated molecular-weight range",
        )
    lower = float(
        brentq(
            lambda center: maximum_symmetric_span(center) - maximum_log_width,
            valid_lower,
            midpoint,
        )
    )
    upper = float(
        brentq(
            lambda center: maximum_symmetric_span(center) - maximum_log_width,
            midpoint,
            valid_upper,
        )
    )
    if not lower < upper:
        raise AnalysisError(
            "peak_width_outside_calibration",
            "the requested log-M FWHM leaves no admissible peak-centre interval",
        )
    return lower, upper


def calibrated_log_fwhm(
    calibration: Calibration,
    center_time_min: float,
    fwhm_time_min: float,
) -> float:
    """Return the exact log10(M) span of a symmetric RT FWHM interval.

    Peak functions are fitted in retention-time space, so their half-height
    endpoints are ``center +/- FWHM/2``. Evaluating those exact endpoints is
    deliberately different from applying a local calibration slope or from
    centring an interval in log-M space.
    """

    if fwhm_time_min <= 0.0:
        raise ValueError("retention-time FWHM must be positive")
    half_width = fwhm_time_min / 2.0
    endpoints = np.asarray(
        [center_time_min - half_width, center_time_min + half_width],
        dtype=np.float64,
    )
    values = calibration.evaluate_log_m(endpoints, allow_extrapolation=False)
    return abs(float(values[1] - values[0]))


def retention_fwhm_for_log_width(
    calibration: Calibration,
    center_time_min: float,
    log_fwhm: float,
) -> float:
    """Invert an exact symmetric RT FWHM at one admissible centre."""

    if log_fwhm <= 0.0:
        raise ValueError("log-M FWHM must be positive")
    maximum = 2.0 * min(
        center_time_min - calibration.valid_rt_min,
        calibration.valid_rt_max - center_time_min,
    )
    if maximum <= 0.0:
        raise AnalysisError(
            "peak_width_outside_calibration",
            "the peak centre is on or outside the calibration boundary",
        )
    maximum_log_span = calibrated_log_fwhm(calibration, center_time_min, maximum)
    tolerance = max(1e-12, log_fwhm * 1e-10)
    if maximum_log_span + tolerance < log_fwhm:
        raise AnalysisError(
            "peak_width_outside_calibration",
            "the requested log-M FWHM cannot be represented at the fitted peak centre",
        )
    if abs(maximum_log_span - log_fwhm) <= tolerance:
        return float(maximum)
    return float(
        brentq(
            lambda width: calibrated_log_fwhm(calibration, center_time_min, width) - log_fwhm,
            max(np.finfo(np.float64).eps, maximum * 1e-12),
            maximum,
        )
    )


def project_parameters_to_log_width(
    component: PeakComponentSpec,
    parameters: dict[str, float],
    calibration: Calibration | None,
) -> dict[str, float]:
    """Project free shape scales into the component's exact log-M width box.

    Every supported peak family is homogeneous in its RT scale parameters.
    Scaling ``width`` together with ``gamma`` or ``tau`` therefore changes the
    common FWHM without changing the Voigt/EMG shape ratio. Fixed scale
    parameters are never altered; contradictory fixed values are rejected by
    the mandatory post-fit validation instead.
    """

    log_bounds = component.fwhm_bounds_log10_m
    if log_bounds is None:
        return parameters
    if calibration is None:
        raise AnalysisError(
            "missing_peak_calibration",
            "a calibration is required for log-M FWHM constraints",
        )
    scale_names = ["width"]
    if component.shape is PeakShape.VOIGT:
        scale_names.append("gamma")
    elif component.shape is PeakShape.EMG:
        scale_names.append("tau")
    if any(name in component.fixed for name in scale_names):
        return parameters
    center = float(parameters["center"])
    lower = retention_fwhm_for_log_width(calibration, center, log_bounds[0])
    upper = retention_fwhm_for_log_width(calibration, center, log_bounds[1])
    current = peak_fwhm(component.shape, parameters)
    target = min(max(current, lower), upper)
    if math.isclose(target, current, rel_tol=1e-13, abs_tol=1e-15):
        return parameters
    scale = target / max(current, np.finfo(np.float64).eps)
    projected = dict(parameters)
    for name in scale_names:
        projected[name] = float(projected[name] * scale)
    return projected


def _log_width_to_rt(
    bounds: tuple[float, float],
    center_bounds: tuple[float, float],
    calibration: Calibration | None,
) -> tuple[float, float]:
    """Build an RT-width preconditioner for a hard log-M FWHM interval.

    No single RT-width box can exactly represent the same log-M interval at
    every centre on a curved calibration. Exact centre-dependent projection and
    post-fit validation are therefore applied separately; this sampled box only
    keeps the solver near the feasible manifold.
    """

    if calibration is None:
        raise AnalysisError(
            "missing_peak_calibration",
            "a calibration is required for log-M FWHM constraints",
        )
    if calibration.monotonic_direction == 0:
        raise AnalysisError(
            "non_monotonic_peak_calibration",
            "log-M FWHM constraints require an invertible monotonic calibration",
        )
    centres = np.linspace(center_bounds[0], center_bounds[1], 129)
    lower_spans: list[float] = []
    upper_spans: list[float] = []
    for center in centres:
        spans = [
            retention_fwhm_for_log_width(calibration, float(center), log_width)
            for log_width in bounds
        ]
        lower_spans.append(spans[0])
        upper_spans.append(spans[1])
    # Keep the native-variable box broad enough to cover every centre-specific
    # feasible interval. The exact model projection below supplies the hard
    # constraint; taking the intersection here would incorrectly reject curved
    # calibrations for which the admissible RT width changes with centre.
    lower = min(lower_spans)
    upper = max(upper_spans)
    if not lower < upper:
        raise AnalysisError(
            "incompatible_log_peak_width",
            "the requested log-M FWHM interval has no feasible retention-time width",
        )
    return float(lower), float(upper)


def _ordered_center_cells(
    components: tuple[PeakComponentSpec, ...],
    start: float,
    end: float,
) -> dict[int, tuple[float, float]]:
    ordered = sorted(range(len(components)), key=lambda index: components[index].center_time_min)
    centers = [components[index].center_time_min for index in ordered]
    separators = [(left + right) / 2.0 for left, right in pairwise(centers)]
    cells: dict[int, tuple[float, float]] = {}
    for position, index in enumerate(ordered):
        lower = start if position == 0 else separators[position - 1]
        upper = end if position == len(ordered) - 1 else separators[position]
        cells[index] = (lower, upper)
    return cells


def _internal_width_bounds(
    component: PeakComponentSpec,
    fwhm_bounds: tuple[float, float],
    spacing: float,
) -> dict[str, tuple[float, float]]:
    """Translate common FWHM limits to safe legacy shape-parameter boxes.

    The stored ``width`` field intentionally keeps its historical meaning. For
    two-width shapes (Voigt and EMG), both scale parameters are bounded along a
    homothetic box whose upper-right corner has the requested maximum FWHM.
    This prevents either parameter from creating a super-wide component while
    preserving the existing serialized parameter definitions.
    """

    parameters = initial_parameters(component)
    current_fwhm = peak_fwhm(component.shape, parameters)
    bounds = dict(component.bounds)
    tiny_width = max(spacing / 10.0, np.finfo(np.float64).eps)
    if component.shape is PeakShape.PSEUDO_VOIGT:
        # Exact factor ranges from 2 (Lorentzian) to 2.35482 (Gaussian).
        lower = max(fwhm_bounds[0] / 2.0, tiny_width)
        upper = max(lower * (1.0 + 1e-6), fwhm_bounds[1] / 2.3548200450309493)
    else:
        ratio = current_fwhm / max(component.width_time_min, np.finfo(np.float64).eps)
        ratio = max(ratio, np.finfo(np.float64).eps)
        lower = max(fwhm_bounds[0] / ratio, tiny_width)
        upper = max(lower * (1.0 + 1e-6), fwhm_bounds[1] / ratio)
    bounds["width"] = _intersect_bounds(bounds.get("width"), (lower, upper), "width")
    if component.shape is PeakShape.VOIGT:
        scale_lower = fwhm_bounds[0] / max(current_fwhm, np.finfo(np.float64).eps)
        scale_upper = fwhm_bounds[1] / max(current_fwhm, np.finfo(np.float64).eps)
        gamma_initial = max(float(parameters["gamma"]), tiny_width)
        width_initial = max(float(parameters["width"]), tiny_width)
        bounds["width"] = _intersect_bounds(
            component.bounds.get("width"),
            (
                max(width_initial * scale_lower, tiny_width),
                max(width_initial * scale_upper, tiny_width * (1.0 + 1e-6)),
            ),
            "Voigt Gaussian width",
        )
        gamma_bounds = (
            max(gamma_initial * scale_lower, tiny_width),
            max(gamma_initial * scale_upper, tiny_width * (1.0 + 1e-6)),
        )
        bounds["gamma"] = _intersect_bounds(bounds.get("gamma"), gamma_bounds, "Voigt gamma")
    elif component.shape is PeakShape.EMG:
        scale_lower = fwhm_bounds[0] / max(current_fwhm, np.finfo(np.float64).eps)
        scale_upper = fwhm_bounds[1] / max(current_fwhm, np.finfo(np.float64).eps)
        tau_initial = max(float(parameters["tau"]), tiny_width)
        width_initial = max(float(parameters["width"]), tiny_width)
        bounds["width"] = _intersect_bounds(
            component.bounds.get("width"),
            (
                max(width_initial * scale_lower, tiny_width),
                max(width_initial * scale_upper, tiny_width * (1.0 + 1e-6)),
            ),
            "EMG Gaussian width",
        )
        tau_bounds = (
            max(tau_initial * scale_lower, tiny_width),
            max(tau_initial * scale_upper, tiny_width * (1.0 + 1e-6)),
        )
        bounds["tau"] = _intersect_bounds(bounds.get("tau"), tau_bounds, "EMG tau")
    elif component.shape is PeakShape.PSEUDO_VOIGT:
        bounds["eta"] = _intersect_bounds(bounds.get("eta"), (0.0, 1.0), "eta")
    return bounds


def _intersect_bounds(
    current: tuple[float, float] | None,
    proposed: tuple[float, float],
    label: str,
) -> tuple[float, float]:
    lower, upper = proposed
    if current is not None:
        lower = max(lower, current[0])
        upper = min(upper, current[1])
    if not lower < upper:
        raise ValueError(f"incompatible {label} constraints")
    return float(lower), float(upper)


def build_physical_constraints(
    specification: PeakFitSpec,
    time_min: npt.ArrayLike,
    calibration: Calibration | None = None,
) -> ConstraintBuildResult:
    """Resolve relative policy values to component-specific hard bounds.

    A specification without ``constraints`` is returned unchanged.  This is the
    compatibility boundary that preserves historical manual fitting behaviour.
    """

    policy = specification.constraints
    time = np.asarray(time_min, dtype=np.float64)
    if policy is None:
        legacy_contexts = tuple(
            ComponentConstraintContext(
                component.center_time_min,
                component.bounds.get(
                    "center",
                    (
                        specification.region.start_time_min,
                        specification.region.end_time_min,
                    ),
                ),
                component.fwhm_bounds_time_min
                or (0.0, specification.region.end_time_min - specification.region.start_time_min),
                component.molecular_weight_bounds,
                "center" in component.bounds or component.molecular_weight_bounds is not None,
            )
            for component in specification.components
        )
        return ConstraintBuildResult(specification, legacy_contexts)
    if len(time) < 2 or np.any(np.diff(time) <= 0):
        raise ValueError("physical peak constraints require increasing time values")
    spacing = float(np.median(np.diff(time)))
    start = specification.region.start_time_min
    end = specification.region.end_time_min
    region_span = end - start
    cells = _ordered_center_cells(specification.components, start, end)
    updated_components: list[PeakComponentSpec] = []
    contexts: list[ComponentConstraintContext] = []
    warnings: list[str] = []
    for index, component in enumerate(specification.components):
        initial_center = float(component.center_time_min)
        explicit_center = component.bounds.get("center")
        mw_center = (
            _mw_window_to_rt(component.molecular_weight_bounds, calibration)
            if component.molecular_weight_bounds is not None
            else None
        )
        user_center_constraint = explicit_center is not None or mw_center is not None
        center_bounds = (start, end)
        if policy.preserve_peak_order and len(specification.components) > 1:
            center_bounds = _intersect_bounds(center_bounds, cells[index], "ordered centre")
        locality_radius = region_span * policy.center_locality_fraction
        locality = (initial_center - locality_radius, initial_center + locality_radius)
        center_bounds = _intersect_bounds(center_bounds, locality, "centre locality")
        if explicit_center is not None:
            center_bounds = _intersect_bounds(center_bounds, explicit_center, "centre")
        if mw_center is not None:
            center_bounds = _intersect_bounds(center_bounds, mw_center, "molecular-weight centre")
        if component.fwhm_bounds_log10_m is not None:
            supported_centres = _center_domain_for_log_width(
                component.fwhm_bounds_log10_m[1],
                calibration,
            )
            center_bounds = _intersect_bounds(
                center_bounds,
                supported_centres,
                "calibrated log-M width support",
            )
        center = min(max(initial_center, center_bounds[0]), center_bounds[1])
        if center != initial_center:
            warnings.append(
                f"Component {index + 1} initial centre was clipped to its physical window."
            )

        local_span = center_bounds[1] - center_bounds[0]
        fwhm_min = spacing * policy.minimum_fwhm_points
        fwhm_max = min(
            region_span * policy.maximum_fwhm_region_fraction,
            local_span * policy.maximum_fwhm_local_fraction,
        )
        if component.fwhm_bounds_time_min is not None:
            fwhm_min = max(fwhm_min, component.fwhm_bounds_time_min[0])
            fwhm_max = min(fwhm_max, component.fwhm_bounds_time_min[1])
        if component.fwhm_bounds_log10_m is not None:
            log_bounds = _log_width_to_rt(
                component.fwhm_bounds_log10_m,
                center_bounds,
                calibration,
            )
            fwhm_min = max(fwhm_min, log_bounds[0])
            fwhm_max = min(fwhm_max, log_bounds[1])
        if not fwhm_min < fwhm_max:
            raise ValueError(f"component {index + 1} has no feasible FWHM interval")
        fwhm_bounds = (float(fwhm_min), float(fwhm_max))
        bounds = _internal_width_bounds(component, fwhm_bounds, spacing)
        bounds["center"] = _intersect_bounds(bounds.get("center"), center_bounds, "centre")
        bounds["amplitude"] = _intersect_bounds(
            bounds.get("amplitude"),
            (0.0, math.inf),
            "non-negative amplitude",
        )
        parameters = initial_parameters(component)
        width_lower, width_upper = bounds["width"]
        width = (
            float(parameters["width"])
            if "width" in component.fixed
            else min(max(parameters["width"], width_lower), width_upper)
        )
        shape_parameter = component.shape_parameter
        if component.shape is PeakShape.VOIGT:
            gamma_lower, gamma_upper = bounds["gamma"]
            gamma = (
                float(parameters["gamma"])
                if "gamma" in component.fixed
                else min(max(parameters["gamma"], gamma_lower), gamma_upper)
            )
            shape_parameter = gamma
        elif component.shape is PeakShape.EMG:
            tau_lower, tau_upper = bounds["tau"]
            tau = (
                float(parameters["tau"])
                if "tau" in component.fixed
                else min(max(parameters["tau"], tau_lower), tau_upper)
            )
            shape_parameter = tau
        elif component.shape is PeakShape.PSEUDO_VOIGT:
            eta_lower, eta_upper = bounds["eta"]
            eta = (
                float(parameters["eta"])
                if "eta" in component.fixed
                else min(max(parameters["eta"], eta_lower), eta_upper)
            )
            shape_parameter = eta
        updated_components.append(
            replace(
                component,
                center_time_min=center,
                width_time_min=width,
                shape_parameter=shape_parameter,
                bounds=bounds,
                fwhm_bounds_time_min=fwhm_bounds,
            )
        )
        contexts.append(
            ComponentConstraintContext(
                initial_center,
                center_bounds,
                fwhm_bounds,
                component.molecular_weight_bounds,
                user_center_constraint,
            )
        )
    return ConstraintBuildResult(
        replace(specification, components=tuple(updated_components)),
        tuple(contexts),
        tuple(warnings),
    )
