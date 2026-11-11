"""Stepwise, evidence-led automatic deconvolution for GPC/SEC traces."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt
from scipy.signal import find_peaks, peak_widths, savgol_filter

from gpcsec.core.calibration import Calibration
from gpcsec.core.peak_fitting.constraints import build_physical_constraints
from gpcsec.core.peak_fitting.diagnostics import robust_noise_sigma
from gpcsec.core.peak_fitting.fit import fit_peaks
from gpcsec.core.peak_fitting.shapes import initial_parameters, peak_fwhm
from gpcsec.models import (
    PeakComponentDiagnostics,
    PeakComponentSpec,
    PeakFitConstraints,
    PeakFitDiagnostics,
    PeakFitMode,
    PeakFitResult,
    PeakFitSpec,
    PeakModelSummary,
    PeakSeedStrategy,
    PeakShape,
    constraints_for_mode,
)

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class PeakCandidate:
    """One residual-supported local peak proposal."""

    center_time_min: float
    height: float
    fwhm_time_min: float
    prominence_snr: float
    integrated_snr: float
    source: str


def _smooth_for_detection(values: FloatArray) -> FloatArray:
    if len(values) < 9:
        return values.copy()
    window = max(5, round(len(values) * 0.015))
    if window % 2 == 0:
        window += 1
    window = min(window, len(values) - 1 if len(values) % 2 == 0 else len(values))
    if window < 5:
        return values.copy()
    return np.asarray(savgol_filter(values, window, 2, mode="interp"), dtype=np.float64)


def detect_peak_candidates(
    time_min: npt.ArrayLike,
    signal: npt.ArrayLike,
    policy: PeakFitConstraints,
    *,
    source: str = "observed",
) -> tuple[PeakCandidate, ...]:
    """Find finite-width, significant positive structures in signal or residual."""

    time = np.asarray(time_min, dtype=np.float64)
    values = np.asarray(signal, dtype=np.float64)
    if time.ndim != 1 or values.shape != time.shape or len(time) < 5:
        return ()
    spacing = float(np.median(np.diff(time)))
    noise = robust_noise_sigma(values)
    smoothed = _smooth_for_detection(values)
    prominence = max(
        policy.candidate_prominence_snr * noise,
        float(np.ptp(smoothed)) * policy.minimum_peak_height_relative,
        np.finfo(np.float64).eps,
    )
    indices, properties = find_peaks(
        smoothed,
        prominence=prominence,
        width=policy.candidate_minimum_width_points,
        distance=max(1, round(policy.minimum_fwhm_points / 2.0)),
    )
    if not len(indices):
        return ()
    widths = peak_widths(smoothed, indices, rel_height=0.5)[0]
    output: list[PeakCandidate] = []
    for candidate_index, point_index in enumerate(indices):
        width_points = float(widths[candidate_index])
        candidate_prominence = float(properties["prominences"][candidate_index])
        left_base = int(properties["left_bases"][candidate_index])
        right_base = int(properties["right_bases"][candidate_index])
        segment = smoothed[left_base : right_base + 1]
        if len(segment) < 2:
            continue
        local_baseline = np.linspace(
            float(smoothed[left_base]),
            float(smoothed[right_base]),
            len(segment),
        )
        positive_structure = np.maximum(segment - local_baseline, 0.0)
        integrated_snr = float(
            np.sum(positive_structure) / (noise * math.sqrt(max(len(positive_structure), 1)))
        )
        if integrated_snr < policy.candidate_integrated_snr:
            continue
        output.append(
            PeakCandidate(
                float(time[point_index]),
                max(float(smoothed[point_index]), np.finfo(np.float64).eps),
                max(width_points * spacing, spacing * policy.minimum_fwhm_points),
                candidate_prominence / noise,
                integrated_snr,
                source,
            )
        )
    return tuple(sorted(output, key=lambda candidate: candidate.prominence_snr, reverse=True))


def _width_parameter_from_fwhm(shape: PeakShape, fwhm: float) -> float:
    if shape is PeakShape.LORENTZIAN:
        return fwhm / 2.0
    return fwhm / 2.3548200450309493


def _component_from_candidate(
    candidate: PeakCandidate,
    shape: PeakShape,
    index: int,
    policy: PeakFitConstraints,
) -> PeakComponentSpec:
    width = max(_width_parameter_from_fwhm(shape, candidate.fwhm_time_min), 1e-12)
    shape_parameter: float | None = None
    if shape is PeakShape.VOIGT:
        shape_parameter = max(candidate.fwhm_time_min / 4.0, 1e-12)
    elif shape is PeakShape.PSEUDO_VOIGT:
        shape_parameter = 0.5
    elif shape is PeakShape.EMG:
        shape_parameter = max(candidate.fwhm_time_min / 3.0, 1e-12)
    return PeakComponentSpec(
        shape,
        candidate.height,
        candidate.center_time_min,
        width,
        shape_parameter,
        name=f"Automatic Component {index}",
        fwhm_bounds_time_min=(
            max(
                candidate.fwhm_time_min * policy.candidate_fwhm_lower_factor,
                np.finfo(np.float64).eps,
            ),
            candidate.fwhm_time_min * policy.candidate_fwhm_upper_factor,
        ),
    )


def _with_unique_automatic_name(
    component: PeakComponentSpec,
    existing_components: tuple[PeakComponentSpec, ...],
    protected_component_names: frozenset[str],
) -> PeakComponentSpec:
    """Return an automatic component whose name cannot alias a manual prior.

    Manual components are protected during refinement by their stable display name.
    An automatically added residual component must therefore never reuse either an
    existing component name or a protected manual name, otherwise it could inherit
    that protection accidentally during backward pruning.
    """

    occupied = {
        item.name.strip()
        for item in existing_components
        if item.name is not None and item.name.strip()
    }
    occupied.update(name.strip() for name in protected_component_names if name.strip())
    base_name = (component.name or "Automatic Component").strip()
    if base_name not in occupied:
        return component
    suffix = 2
    candidate_name = f"{base_name} ({suffix})"
    while candidate_name in occupied:
        suffix += 1
        candidate_name = f"{base_name} ({suffix})"
    return replace(component, name=candidate_name)


def _user_component_from_candidate(
    component: PeakComponentSpec,
    candidate: PeakCandidate,
) -> PeakComponentSpec:
    """Move a user-defined component to supported residual evidence safely."""

    width = max(
        _width_parameter_from_fwhm(component.shape, candidate.fwhm_time_min),
        np.finfo(np.float64).eps,
    )
    shape_parameter = component.shape_parameter
    if component.shape is PeakShape.VOIGT and shape_parameter is None:
        shape_parameter = candidate.fwhm_time_min / 4.0
    elif component.shape is PeakShape.PSEUDO_VOIGT and shape_parameter is None:
        shape_parameter = 0.5
    elif component.shape is PeakShape.EMG and shape_parameter is None:
        shape_parameter = candidate.fwhm_time_min / 3.0
    return replace(
        component,
        amplitude=max(candidate.height, np.finfo(np.float64).eps),
        center_time_min=candidate.center_time_min,
        width_time_min=width,
        shape_parameter=shape_parameter,
    )


def _candidate_in_component_domain(
    candidate: PeakCandidate,
    component: PeakComponentSpec,
    base: PeakFitSpec,
    time: FloatArray,
    calibration: Calibration | None,
) -> bool:
    try:
        resolved = build_physical_constraints(
            replace(base, components=(component,)),
            time,
            calibration,
        )
    except (ArithmeticError, ValueError):
        return False
    lower, upper = resolved.components[0].center_bounds
    return lower <= candidate.center_time_min <= upper


def _updated_spec_from_result(specification: PeakFitSpec, result: PeakFitResult) -> PeakFitSpec:
    components: list[PeakComponentSpec] = []
    for component, fitted in zip(specification.components, result.components, strict=True):
        parameters = fitted.parameters
        shape_parameter: float | None = component.shape_parameter
        if component.shape is PeakShape.VOIGT:
            shape_parameter = float(parameters["gamma"])
        elif component.shape is PeakShape.PSEUDO_VOIGT:
            shape_parameter = float(parameters["eta"])
        elif component.shape is PeakShape.EMG:
            shape_parameter = float(parameters["tau"])
        components.append(
            replace(
                component,
                amplitude=max(float(parameters["amplitude"]), np.finfo(np.float64).eps),
                center_time_min=float(parameters["center"]),
                width_time_min=max(float(parameters["width"]), np.finfo(np.float64).eps),
                shape_parameter=shape_parameter,
            )
        )
    return replace(specification, components=tuple(components))


def _manual_seed_components(
    components: list[PeakComponentSpec],
    specification: PeakFitSpec,
    policy: PeakFitConstraints,
) -> tuple[PeakComponentSpec, ...]:
    """Turn the current edited components into local, stable warm-start priors.

    The names are used only to protect the user's warm-start components during
    backward pruning.  Existing unique user names are preserved; missing or
    duplicate names receive a deterministic ``Component N`` identity.  Unless
    the user already supplied stronger RT/MW/FWHM constraints, the current centre
    and width define an adaptive local refinement box rather than an unrestricted
    new search.
    """

    named: list[PeakComponentSpec] = []
    used: set[str] = set()
    for index, component in enumerate(components, start=1):
        proposed = (component.name or "").strip() or f"Component {index}"
        name = proposed
        suffix = 2
        while name in used:
            name = f"{proposed} ({suffix})"
            suffix += 1
        used.add(name)
        parameters = initial_parameters(component)
        current_fwhm = max(
            peak_fwhm(component.shape, parameters),
            np.finfo(np.float64).eps,
        )
        bounds = dict(component.bounds)
        if (
            "center" not in component.fixed
            and "center" not in bounds
            and component.molecular_weight_bounds is None
        ):
            shift = current_fwhm * policy.manual_center_max_shift_fwhm
            lower = max(
                specification.region.start_time_min,
                component.center_time_min - shift,
            )
            upper = min(
                specification.region.end_time_min,
                component.center_time_min + shift,
            )
            if lower < upper:
                bounds["center"] = (lower, upper)
        fwhm_bounds = component.fwhm_bounds_time_min
        if (
            "width" not in component.fixed
            and "width" not in bounds
            and fwhm_bounds is None
            and component.fwhm_bounds_log10_m is None
        ):
            fwhm_bounds = (
                current_fwhm * policy.manual_fwhm_lower_factor,
                current_fwhm * policy.manual_fwhm_upper_factor,
            )
        named.append(
            replace(
                component,
                name=name,
                bounds=bounds,
                fwhm_bounds_time_min=fwhm_bounds,
            )
        )
    return tuple(named)


def _summary(result: PeakFitResult, accepted: bool, reason: str) -> PeakModelSummary:
    if not result.success or result.rss is None or result.rmse is None:
        return PeakModelSummary(0, (), math.inf, math.inf, math.inf, math.inf, False, reason)
    return PeakModelSummary(
        len(result.components),
        tuple(component.shape.value for component in result.components),
        result.rss,
        result.rmse,
        result.aicc if result.aicc is not None else math.inf,
        result.bic if result.bic is not None else math.inf,
        accepted,
        reason,
    )


def _component_supported(
    component: PeakComponentDiagnostics | None,
    policy: PeakFitConstraints,
) -> tuple[bool, str]:
    if component is None:
        return False, "component diagnostics are unavailable"
    flags = set(component.flags)
    if "low_snr" in flags:
        return False, "new component is below the configured SNR threshold"
    if "low_height" in flags:
        return False, "new component is too small relative to the dominant component"
    if "low_area" in flags:
        return False, "new component contributes too little integrated area"
    if component.width_ratio is not None and component.width_ratio > 1.01:
        return False, "new component exceeds its hard local FWHM support"
    if (
        component.localization_score is not None
        and component.localization_score < policy.localization_warning_fraction * 0.75
    ):
        return False, "new component is poorly localized in its assigned region"
    return True, "component is locally significant"


def _accept_addition(
    previous: PeakFitResult,
    candidate: PeakFitResult,
    policy: PeakFitConstraints,
    proposed_center: float,
) -> tuple[bool, str]:
    if not candidate.success or candidate.aicc is None or candidate.bic is None:
        return False, "candidate model did not converge with complete statistics"
    if previous.aicc is None or previous.bic is None:
        return False, "previous model has incomplete information criteria"
    aicc_gain = previous.aicc - candidate.aicc
    bic_change = candidate.bic - previous.bic
    # Components are sorted before fitting to preserve their physical order, so
    # the residual-driven proposal is not necessarily the final table row.
    proposed_component = min(
        candidate.components,
        key=lambda component: abs(float(component.parameters["center"]) - proposed_center),
    )
    supported, component_reason = _component_supported(proposed_component.diagnostics, policy)
    if aicc_gain < policy.aicc_improvement_required:
        if aicc_gain <= 0.0:
            return False, f"AICc worsened by {-aicc_gain:.3g}"
        return False, f"AICc improved by only {aicc_gain:.3g}"
    if bic_change > policy.bic_worsening_tolerance:
        return False, f"BIC worsened by {bic_change:.3g}"
    if not supported:
        return False, component_reason
    return (
        True,
        f"AICc improved by {aicc_gain:.3g}; BIC change {bic_change:+.3g}; {component_reason}",
    )


def _candidate_far_from_existing(
    candidate: PeakCandidate,
    result: PeakFitResult,
) -> bool:
    for component in result.components:
        diagnostics = component.diagnostics
        fwhm = (
            diagnostics.fwhm_time_min
            if diagnostics is not None
            else max(float(component.parameters["width"]), 1e-12)
        )
        if abs(candidate.center_time_min - float(component.parameters["center"])) < 0.35 * fwhm:
            return False
    return True


def _fit_one_model(
    time: FloatArray,
    signal: FloatArray,
    specification: PeakFitSpec,
    calibration: Calibration | None,
    cancelled: Callable[[], bool] | None,
    progress: Callable[[Mapping[str, object]], None] | None,
) -> PeakFitResult:
    return fit_peaks(
        time,
        signal,
        replace(specification, automatic_model_selection=False),
        calibration=calibration,
        cancelled=cancelled,
        progress=progress,
    )


def _prune_model(
    time: FloatArray,
    signal: FloatArray,
    specification: PeakFitSpec,
    result: PeakFitResult,
    calibration: Calibration | None,
    policy: PeakFitConstraints,
    cancelled: Callable[[], bool] | None,
    decision_log: list[str],
    candidates: list[PeakModelSummary],
    protected_component_names: frozenset[str] = frozenset(),
) -> tuple[PeakFitSpec, PeakFitResult]:
    current_spec = specification
    current = result
    changed = True
    while changed and len(current.components) > 1:
        changed = False
        for index, component in enumerate(current.components):
            if cancelled is not None and cancelled():
                return current_spec, current
            component_spec = current_spec.components[index]
            if component_spec.name in protected_component_names:
                # User-edited warm-start components are scientific input.  They
                # may be refined within their locks/bounds, but automatic model
                # selection must not silently remove them.  The user can delete
                # a component explicitly or opt into REDISCOVER instead.
                continue
            diagnostics = component.diagnostics
            flags = set(diagnostics.flags if diagnostics is not None else ())
            ghost_like = "low_area" in flags or ("broad" in flags and "low_snr" in flags)
            reduced_components = tuple(
                value for position, value in enumerate(current_spec.components) if position != index
            )
            reduced_spec = replace(current_spec, components=reduced_components, multi_start=2)
            reduced = _fit_one_model(
                time,
                signal,
                reduced_spec,
                calibration,
                cancelled,
                None,
            )
            if not reduced.success or reduced.aicc is None or reduced.bic is None:
                continue
            assert current.aicc is not None and current.bic is not None
            not_materially_worse = (
                reduced.aicc <= current.aicc + policy.aicc_improvement_required / 2.0
                and reduced.bic <= current.bic + policy.bic_worsening_tolerance
            )
            # Broad/weak is a reason to test removal, not permission to discard a
            # genuine wide distribution. The reduced model still has to remain
            # statistically defensible.
            ghost_removal_supported = ghost_like and (
                reduced.aicc <= current.aicc + policy.aicc_improvement_required
                and reduced.bic <= current.bic + max(policy.bic_worsening_tolerance, 2.0)
            )
            if ghost_removal_supported or not_materially_worse:
                reason = (
                    f"Removed Component {index + 1}: "
                    f"ΔAICc={reduced.aicc - current.aicc:+.3g}, "
                    f"ΔBIC={reduced.bic - current.bic:+.3g}; "
                    + (
                        "ghost-like broad/weak component"
                        if ghost_removal_supported
                        else "simpler model retained"
                    )
                )
                decision_log.append(reason)
                candidates.append(_summary(reduced, True, reason))
                current_spec = _updated_spec_from_result(reduced_spec, reduced)
                current = reduced
                changed = True
                break
            reason = f"Component {index + 1} retained: removing it materially worsened AICc/BIC"
            candidates.append(_summary(reduced, False, reason))
    return current_spec, current


def _stability_evidence(
    time: FloatArray,
    signal: FloatArray,
    specification: PeakFitSpec,
    result: PeakFitResult,
    calibration: Calibration | None,
    policy: PeakFitConstraints,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[tuple[PeakComponentDiagnostics, ...], tuple[str, ...]]:
    if policy.stability_trials <= 0 or not result.success:
        return tuple(
            component.diagnostics
            for component in result.components
            if component.diagnostics is not None
        ), ()
    rng = np.random.default_rng(specification.random_seed + 7301)
    trials: list[PeakFitResult] = []
    base = _updated_spec_from_result(specification, result)
    for trial in range(policy.stability_trials):
        if cancelled is not None and cancelled():
            break
        components: list[PeakComponentSpec] = []
        for component in base.components:
            center_span = component.bounds.get(
                "center",
                (base.region.start_time_min, base.region.end_time_min),
            )
            center_scale = (center_span[1] - center_span[0]) * 0.04
            center = component.center_time_min
            if "center" not in component.fixed:
                center = float(
                    np.clip(
                        component.center_time_min + rng.normal(0.0, center_scale),
                        center_span[0],
                        center_span[1],
                    )
                )
            width_bounds = component.bounds.get("width", (1e-12, math.inf))
            amplitude_bounds = component.bounds.get("amplitude", (0.0, math.inf))
            width = component.width_time_min
            if "width" not in component.fixed:
                width = float(
                    np.clip(
                        component.width_time_min * math.exp(float(rng.normal(0.0, 0.08))),
                        width_bounds[0],
                        width_bounds[1],
                    )
                )
            amplitude = component.amplitude
            if "amplitude" not in component.fixed:
                amplitude = float(
                    np.clip(
                        component.amplitude * math.exp(float(rng.normal(0.0, 0.08))),
                        max(amplitude_bounds[0], 1e-12),
                        amplitude_bounds[1],
                    )
                )
            shape_parameter = component.shape_parameter
            shape_parameter_name = {
                PeakShape.VOIGT: "gamma",
                PeakShape.PSEUDO_VOIGT: "eta",
                PeakShape.EMG: "tau",
            }.get(component.shape)
            if (
                shape_parameter is not None
                and shape_parameter_name is not None
                and shape_parameter_name not in component.fixed
            ):
                shape_bounds = component.bounds.get(
                    shape_parameter_name,
                    (0.0, 1.0) if shape_parameter_name == "eta" else (1e-12, math.inf),
                )
                proposal = (
                    shape_parameter + float(rng.normal(0.0, 0.08))
                    if shape_parameter_name == "eta"
                    else shape_parameter * math.exp(float(rng.normal(0.0, 0.08)))
                )
                shape_parameter = float(np.clip(proposal, shape_bounds[0], shape_bounds[1]))
            components.append(
                replace(
                    component,
                    center_time_min=center,
                    width_time_min=max(width, 1e-12),
                    amplitude=max(amplitude, 1e-12),
                    shape_parameter=shape_parameter,
                )
            )
        trial_spec = replace(
            base,
            components=tuple(components),
            multi_start=1,
            random_seed=base.random_seed + trial + 1,
        )
        trial_result = _fit_one_model(
            time,
            signal,
            trial_spec,
            calibration,
            cancelled,
            None,
        )
        if cancelled is not None and cancelled():
            break
        if trial_result.success and len(trial_result.components) == len(result.components):
            trials.append(trial_result)
    if not trials:
        return tuple(
            component.diagnostics
            for component in result.components
            if component.diagnostics is not None
        ), ("stability refits did not produce comparable solutions",)
    updated: list[PeakComponentDiagnostics] = []
    warnings: list[str] = []
    for index, result_component in enumerate(result.components):
        original = result_component.diagnostics
        if original is None:
            continue
        centers = np.asarray(
            [trial.components[index].parameters["center"] for trial in trials], dtype=float
        )
        fwhms = np.asarray(
            [
                peak_fwhm(
                    trial.components[index].shape,
                    trial.components[index].parameters,
                )
                for trial in trials
            ],
            dtype=float,
        )
        areas = np.asarray([trial.components[index].area for trial in trials], dtype=float)
        center_sd = float(np.std(centers, ddof=1)) if len(centers) > 1 else 0.0
        width_cv = float(np.std(fwhms, ddof=1) / np.mean(fwhms)) if len(fwhms) > 1 else 0.0
        area_cv = float(np.std(areas, ddof=1) / np.mean(areas)) if len(areas) > 1 else 0.0
        local_scale = max(original.fwhm_time_min, 1e-12)
        center_fraction = center_sd / local_scale
        instability = max(
            center_fraction / max(policy.stability_center_fraction_warning, 1e-9),
            width_cv / max(policy.stability_width_cv_warning, 1e-9),
            area_cv / max(policy.stability_area_cv_warning, 1e-9),
        )
        score = 1.0 / (1.0 + instability)
        flags = list(original.flags)
        if instability > 1.0:
            flags.append("unstable")
            warnings.append(
                f"Component {index + 1} changes materially across nearby initial values"
            )
        updated.append(
            replace(
                original,
                stability_score=score,
                center_stability_sd=center_sd,
                fwhm_stability_cv=width_cv,
                area_stability_cv=area_cv,
                flags=tuple(dict.fromkeys(flags)),
            )
        )
    return tuple(updated), tuple(warnings)


def _window_stability_evidence(
    full_time: FloatArray,
    full_signal: FloatArray,
    specification: PeakFitSpec,
    result: PeakFitResult,
    calibration: Calibration | None,
    policy: PeakFitConstraints,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[tuple[PeakComponentDiagnostics, ...], tuple[str, ...]]:
    """Refit slightly expanded/contracted windows and quantify parameter drift.

    The expensive check is intentionally limited to the final screened model.
    A component that is actually compensating baseline or an unmodelled tail
    usually changes width or area sharply when the window edge moves.
    """

    original = tuple(
        component.diagnostics
        for component in result.components
        if component.diagnostics is not None
    )
    if not result.success or len(original) != len(result.components):
        return original, ()
    region = specification.region
    span = region.end_time_min - region.start_time_min
    delta = span * policy.fitting_window_fraction
    if delta <= 0:
        return original, ()
    data_start = float(full_time[0])
    data_end = float(full_time[-1])
    candidate_regions = (
        replace(
            region,
            start_time_min=max(data_start, region.start_time_min - delta),
            end_time_min=min(data_end, region.end_time_min + delta),
        ),
        replace(
            region,
            start_time_min=region.start_time_min + delta,
            end_time_min=region.end_time_min - delta,
        ),
    )
    base = _updated_spec_from_result(specification, result)
    trials: list[PeakFitResult] = []
    for trial_region in candidate_regions:
        if cancelled is not None and cancelled():
            break
        if trial_region.end_time_min - trial_region.start_time_min <= span * 0.5:
            continue
        trial_spec = replace(base, region=trial_region, multi_start=1)
        trial = _fit_one_model(
            full_time,
            full_signal,
            trial_spec,
            calibration,
            cancelled,
            None,
        )
        if cancelled is not None and cancelled():
            break
        if trial.success and len(trial.components) == len(result.components):
            trials.append(trial)
    if not trials:
        return original, ("fitting-window stability could not be evaluated",)
    updated: list[PeakComponentDiagnostics] = []
    warnings: list[str] = []
    for index, component in enumerate(result.components):
        evidence = component.diagnostics
        assert evidence is not None
        center = float(component.parameters["center"])
        fwhm = max(evidence.fwhm_time_min, np.finfo(np.float64).eps)
        area = max(component.area, np.finfo(np.float64).eps)
        center_shift = max(
            abs(float(trial.components[index].parameters["center"]) - center) / fwhm
            for trial in trials
        )
        fwhm_change = max(
            abs(
                peak_fwhm(
                    trial.components[index].shape,
                    trial.components[index].parameters,
                )
                - fwhm
            )
            / fwhm
            for trial in trials
        )
        area_change = max(
            abs(trial.components[index].area - component.area) / area for trial in trials
        )
        flags = list(evidence.flags)
        if (
            center_shift > policy.stability_center_fraction_warning
            or fwhm_change > policy.stability_width_cv_warning
            or area_change > policy.stability_area_cv_warning
        ):
            flags.append("window_unstable")
            warnings.append(
                f"Component {index + 1} depends materially on the fitting-window boundary"
            )
        updated.append(
            replace(
                evidence,
                window_center_shift_fraction=center_shift,
                window_fwhm_change_fraction=fwhm_change,
                window_area_change_fraction=area_change,
                flags=tuple(dict.fromkeys(flags)),
            )
        )
    return tuple(updated), tuple(warnings)


def fit_peaks_automatically(
    time_min: npt.ArrayLike,
    baseline_corrected_signal: npt.ArrayLike,
    specification: PeakFitSpec,
    *,
    calibration: Calibration | None = None,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[Mapping[str, object]], None] | None = None,
) -> PeakFitResult:
    """Select the simplest residual-supported, constrained, stable peak model."""

    full_time = np.asarray(time_min, dtype=np.float64)
    full_signal = np.asarray(baseline_corrected_signal, dtype=np.float64)
    selected = (full_time >= specification.region.start_time_min) & (
        full_time <= specification.region.end_time_min
    )
    time = full_time[selected]
    observed = full_signal[selected] * specification.region.polarity.direction
    policy = specification.constraints or constraints_for_mode(PeakFitMode.BALANCED)
    constrained_base = replace(
        specification,
        constraints=policy,
        automatic_model_selection=False,
    )
    if len(time) < 5:
        return fit_peaks(
            full_time,
            full_signal,
            constrained_base,
            calibration=calibration,
            cancelled=cancelled,
            progress=progress,
        )
    detected = detect_peak_candidates(time, observed, policy)
    decision_log: list[str] = [
        f"Estimated detector noise sigma={robust_noise_sigma(observed):.6g}.",
        f"Detected {len(detected)} finite-width candidate structure(s).",
    ]
    model_summaries: list[PeakModelSummary] = []
    user_components = list(constrained_base.components)
    refine_manual = specification.seed_strategy is PeakSeedStrategy.REFINE_MANUAL and bool(
        user_components
    )
    protected_component_names: frozenset[str] = frozenset()
    domain_components = [
        component
        for component in user_components
        if component.molecular_weight_bounds is not None or "center" in component.bounds
    ]
    if refine_manual:
        initial_components = _manual_seed_components(
            user_components,
            constrained_base,
            policy,
        )
        protected_component_names = frozenset(
            component.name for component in initial_components if component.name is not None
        )
        pending_domain_components = []
        decision_log.append(
            f"Started from {len(initial_components)} user-adjusted component(s) as a "
            "protected warm-start model. Free parameters may be refined and "
            "residual-supported components may be added; supplied components will "
            "not be silently pruned."
        )
        if len(initial_components) > policy.max_peaks:
            decision_log.append(
                f"The user supplied {len(initial_components)} components, exceeding the "
                f"automatic max_peaks={policy.max_peaks}; all user components were kept "
                "and no automatic component will be added until the count is below that limit."
            )
    elif domain_components:
        first_component = max(
            domain_components,
            key=lambda component: float(np.interp(component.center_time_min, time, observed)),
        )
        initial_components = (first_component,)
        pending_domain_components = [
            component for component in domain_components if component is not first_component
        ][: max(policy.max_peaks - 1, 0)]
        decision_log.append(
            "Started with the strongest user-constrained component; remaining "
            "domains require residual and information-criterion support."
        )
    else:
        pending_domain_components = []
        shape = user_components[0].shape if user_components else PeakShape.GAUSSIAN
        if detected:
            initial_components = (_component_from_candidate(detected[0], shape, 1, policy),)
        elif user_components:
            strongest = max(user_components, key=lambda component: component.amplitude)
            initial_components = (strongest,)
        else:
            local = int(np.argmax(observed))
            span = specification.region.end_time_min - specification.region.start_time_min
            fallback = PeakCandidate(
                float(time[local]),
                max(float(observed[local]), np.finfo(np.float64).eps),
                span / 8.0,
                0.0,
                0.0,
                "fallback",
            )
            initial_components = (_component_from_candidate(fallback, shape, 1, policy),)
        decision_log.append("Started with the simplest one-component model.")
    current_spec = replace(constrained_base, components=initial_components)
    current = _fit_one_model(
        full_time,
        full_signal,
        current_spec,
        calibration,
        cancelled,
        progress,
    )
    if not current.success:
        return current
    if current.failure_code == "cancelled" or (cancelled is not None and cancelled()):
        return current
    model_summaries.append(_summary(current, True, "initial simplest feasible model"))
    current_spec = _updated_spec_from_result(current_spec, current)
    while len(current.components) < policy.max_peaks:
        if cancelled is not None and cancelled():
            break
        residual_candidates = detect_peak_candidates(
            np.asarray(current.time_min, dtype=float),
            np.maximum(np.asarray(current.residual, dtype=float), 0.0),
            policy,
            source="positive residual",
        )
        eligible_candidates = [
            candidate
            for candidate in residual_candidates
            if _candidate_far_from_existing(candidate, current)
        ][: policy.candidate_trials_per_step]
        if not eligible_candidates:
            decision_log.append("Stopped: no significant finite-width positive residual remained.")
            break
        proposals: list[tuple[PeakCandidate, PeakComponentSpec, PeakComponentSpec | None]] = []
        for pending in pending_domain_components:
            matching = [
                candidate
                for candidate in eligible_candidates
                if _candidate_in_component_domain(
                    candidate,
                    pending,
                    constrained_base,
                    time,
                    calibration,
                )
            ]
            if matching:
                candidate = max(matching, key=lambda value: value.prominence_snr)
                proposals.append(
                    (candidate, _user_component_from_candidate(pending, candidate), pending)
                )
        shape = user_components[0].shape if user_components else PeakShape.GAUSSIAN
        proposals.extend(
            (
                candidate,
                _component_from_candidate(
                    candidate,
                    shape,
                    len(current.components) + 1,
                    policy,
                ),
                None,
            )
            for candidate in eligible_candidates
        )
        accepted_proposal = False
        attempted_centres: set[float] = set()
        for next_candidate, new_component, pending_component in proposals:
            rounded_center = round(next_candidate.center_time_min, 10)
            if rounded_center in attempted_centres:
                continue
            attempted_centres.add(rounded_center)
            if pending_component is None:
                new_component = _with_unique_automatic_name(
                    new_component,
                    current_spec.components,
                    protected_component_names,
                )
            proposal_components = tuple(
                sorted(
                    (*current_spec.components, new_component),
                    key=lambda component: component.center_time_min,
                )
            )
            proposal_spec = replace(
                current_spec,
                components=proposal_components,
                multi_start=2,
            )
            proposal = _fit_one_model(
                full_time,
                full_signal,
                proposal_spec,
                calibration,
                cancelled,
                progress,
            )
            if proposal.failure_code == "cancelled" or (cancelled is not None and cancelled()):
                return proposal
            accepted, reason = _accept_addition(
                current,
                proposal,
                policy,
                next_candidate.center_time_min,
            )
            candidate_message = (
                f"Tried Component {len(proposal_components)} at "
                f"{next_candidate.center_time_min:.6g} min from a "
                f"{next_candidate.prominence_snr:.3g}-sigma residual feature: {reason}."
            )
            decision_log.append(candidate_message)
            model_summaries.append(_summary(proposal, accepted, candidate_message))
            if not accepted:
                continue
            current = proposal
            current_spec = _updated_spec_from_result(proposal_spec, proposal)
            if pending_component is not None:
                pending_domain_components.remove(pending_component)
            accepted_proposal = True
            break
        if not accepted_proposal:
            decision_log.append(
                "Stopped: tested residual-supported additions did not pass AICc/BIC "
                "and component-significance checks."
            )
            break
    current_spec, current = _prune_model(
        full_time,
        full_signal,
        current_spec,
        current,
        calibration,
        policy,
        cancelled,
        decision_log,
        model_summaries,
        protected_component_names,
    )
    if current.failure_code == "cancelled" or (cancelled is not None and cancelled()):
        return current
    stable_components, stability_warnings = _stability_evidence(
        full_time,
        full_signal,
        current_spec,
        current,
        calibration,
        policy,
        cancelled,
    )
    if cancelled is not None and cancelled():
        return current
    if len(stable_components) == len(current.components):
        current = replace(
            current,
            components=tuple(
                replace(component, diagnostics=diagnostic)
                for component, diagnostic in zip(
                    current.components,
                    stable_components,
                    strict=True,
                )
            ),
        )
    window_components, window_warnings = _window_stability_evidence(
        full_time,
        full_signal,
        current_spec,
        current,
        calibration,
        policy,
        cancelled,
    )
    if cancelled is not None and cancelled():
        return current
    if len(window_components) == len(current.components):
        stable_components = window_components
    if len(stable_components) == len(current.components):
        fitted_components = tuple(
            replace(component, diagnostics=diagnostic)
            for component, diagnostic in zip(
                current.components,
                stable_components,
                strict=True,
            )
        )
    else:
        fitted_components = current.components
    diagnostics = current.diagnostics or PeakFitDiagnostics(
        current.adjusted_r_squared,
        current.aicc,
        None,
        None,
        None,
        0.0,
        0.0,
        0.0,
        0.0,
        "review",
        "review",
        "review",
    )
    warnings = tuple(dict.fromkeys((*diagnostics.warnings, *stability_warnings, *window_warnings)))
    physical = (
        "warning"
        if any(
            component.diagnostics is not None and component.diagnostics.flags
            for component in fitted_components
        )
        else diagnostics.physical_quality
    )
    diagnostics = replace(
        diagnostics,
        physical_quality=physical,
        warnings=warnings,
        decision_log=tuple(decision_log),
        model_candidates=tuple(model_summaries),
    )
    return replace(
        current,
        components=fitted_components,
        diagnostics=diagnostics,
        algorithm_version="manual-prior-stepwise-physical-aicc-bic-pruning-v2",
    )
