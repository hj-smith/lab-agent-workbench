"""Deterministic bounded TRF multi-start fitting for chromatographic peaks."""

from __future__ import annotations

import math
import time as time_module
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt
from scipy.integrate import trapezoid
from scipy.optimize import least_squares

from gpcsec.core.calibration import Calibration
from gpcsec.core.peak_fitting.constraints import (
    build_physical_constraints,
    calibrated_log_fwhm,
    project_parameters_to_log_width,
)
from gpcsec.core.peak_fitting.diagnostics import (
    build_fit_diagnostics,
    component_diagnostics,
    information_criteria,
    jacobian_identifiability,
    residual_diagnostics,
    robust_noise_sigma,
    strong_parameter_correlations,
)
from gpcsec.core.peak_fitting.shapes import (
    evaluate_peak,
    initial_parameters,
    parameter_names,
    peak_fwhm,
)
from gpcsec.models import (
    AnalysisError,
    PeakComponentResult,
    PeakComponentSpec,
    PeakFitMode,
    PeakFitResult,
    PeakFitSpec,
    PeakSeedStrategy,
)

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class _Variable:
    key: str
    lower: float
    upper: float
    initial: float


class _PeakFitCancelled(Exception):
    """Internal cooperative stop raised from the residual evaluation hot path."""


def _failure(
    code: str,
    message: str,
    time: FloatArray,
    observed: FloatArray,
    spec: PeakFitSpec,
    nfev: int = 0,
) -> PeakFitResult:
    return PeakFitResult(
        False,
        code,
        message,
        tuple(float(value) for value in np.ravel(time)),
        tuple(float(value) for value in np.ravel(observed)),
        (),
        (),
        (),
        (),
        None,
        None,
        None,
        None,
        None,
        nfev,
        spec.random_seed,
    )


def _default_bounds(name: str, start: float, end: float, spacing: float) -> tuple[float, float]:
    span = end - start
    if name == "amplitude":
        return 0.0, math.inf
    if name == "center":
        return start, end
    if name == "eta":
        return 0.0, 1.0
    if name in {"width", "gamma", "tau"}:
        return max(spacing / 10.0, np.finfo(np.float64).eps), span * 5.0
    raise ValueError(f"unsupported peak parameter: {name}")


def _variable_key(index: int, name: str, component: PeakComponentSpec) -> str:
    group = component.shared.get(name)
    return f"shared:{group}:{name}" if group else f"component:{index}:{name}"


def _build_variables(
    spec: PeakFitSpec, spacing: float
) -> tuple[list[_Variable], list[dict[str, float]], dict[tuple[int, str], str]]:
    constants = [initial_parameters(component) for component in spec.components]
    variables: dict[str, _Variable] = {}
    lookup: dict[tuple[int, str], str] = {}
    start, end = spec.region.start_time_min, spec.region.end_time_min
    for index, component in enumerate(spec.components):
        valid_names = parameter_names(component.shape)
        unknown_fixed = set(component.fixed) - set(valid_names)
        if unknown_fixed:
            raise ValueError(f"unknown fixed peak parameters: {sorted(unknown_fixed)}")
        for name in valid_names:
            if name in component.fixed:
                continue
            key = _variable_key(index, name, component)
            lower, upper = component.bounds.get(name, _default_bounds(name, start, end, spacing))
            initial = constants[index][name]
            if not lower <= initial <= upper:
                raise ValueError(f"initial {name} lies outside its bounds")
            candidate = _Variable(key, lower, upper, initial)
            if key in variables and variables[key] != candidate:
                raise ValueError(f"shared parameter {key} has inconsistent bounds or initials")
            variables[key] = candidate
            lookup[(index, name)] = key
    return list(variables.values()), constants, lookup


def _unpack(
    vector: FloatArray,
    variables: list[_Variable],
    constants: list[dict[str, float]],
    lookup: Mapping[tuple[int, str], str],
) -> list[dict[str, float]]:
    by_key = {variable.key: float(value) for variable, value in zip(variables, vector, strict=True)}
    parameters = [dict(values) for values in constants]
    for (index, name), key in lookup.items():
        parameters[index][name] = by_key[key]
    return parameters


def _model(
    time: FloatArray,
    spec: PeakFitSpec,
    vector: FloatArray,
    variables: list[_Variable],
    constants: list[dict[str, float]],
    lookup: Mapping[tuple[int, str], str],
    calibration: Calibration | None = None,
) -> tuple[FloatArray, list[FloatArray], list[dict[str, float]]]:
    parameters = _unpack(vector, variables, constants, lookup)
    parameters = [
        project_parameters_to_log_width(component, values, calibration)
        for component, values in zip(spec.components, parameters, strict=True)
    ]
    curves = [
        evaluate_peak(component.shape, time, values)
        for component, values in zip(spec.components, parameters, strict=True)
    ]
    total = np.sum(curves, axis=0)
    return np.asarray(total, dtype=np.float64), curves, parameters


def _covariance_matrix(
    jacobian: FloatArray,
    rss: float,
    observations: int,
    parameters: int,
) -> FloatArray:
    if parameters == 0:
        return np.empty((0, 0), dtype=np.float64)
    if observations <= parameters:
        return np.full((parameters, parameters), np.nan, dtype=np.float64)
    return np.asarray(
        np.linalg.pinv(jacobian.T @ jacobian) * (rss / (observations - parameters)),
        dtype=np.float64,
    )


def _derived_standard_error(
    function: Callable[[FloatArray], float],
    vector: FloatArray,
    covariance: FloatArray,
    variables: list[_Variable],
    active_indices: Sequence[int],
) -> float | None:
    """Delta-method uncertainty for a reported common derived parameter."""

    if not len(vector) or covariance.shape != (len(vector), len(vector)):
        return None
    gradient = np.zeros(len(vector), dtype=np.float64)
    for index in active_indices:
        variable = variables[index]
        value = float(vector[index])
        bound_span = variable.upper - variable.lower
        scale = bound_span if math.isfinite(bound_span) else abs(value)
        step = max(abs(value) * 1e-5, scale * 1e-7, 1e-9)
        lower_value = max(variable.lower, value - step)
        upper_value = min(variable.upper, value + step)
        if not lower_value < upper_value:
            continue
        lower_vector = vector.copy()
        upper_vector = vector.copy()
        lower_vector[index] = lower_value
        upper_vector[index] = upper_value
        try:
            lower_result = float(function(lower_vector))
            upper_result = float(function(upper_vector))
        except (ArithmeticError, ValueError):
            return None
        gradient[index] = (upper_result - lower_result) / (upper_value - lower_value)
    variance = float(gradient @ covariance @ gradient)
    if not math.isfinite(variance) or variance < 0:
        return None
    return math.sqrt(variance)


def _component_derived_uncertainties(
    component_index: int,
    component: PeakComponentSpec,
    time: FloatArray,
    vector: FloatArray,
    covariance: FloatArray,
    variables: list[_Variable],
    constants: list[dict[str, float]],
    lookup: Mapping[tuple[int, str], str],
    calibration: Calibration | None,
) -> tuple[float | None, float | None, float | None]:
    variable_index_by_key = {variable.key: index for index, variable in enumerate(variables)}
    active_indices = tuple(
        variable_index_by_key[key]
        for parameter_name in parameter_names(component.shape)
        if (key := lookup.get((component_index, parameter_name))) is not None
    )

    def component_parameters(candidate: FloatArray) -> dict[str, float]:
        values = _unpack(candidate, variables, constants, lookup)[component_index]
        return project_parameters_to_log_width(component, values, calibration)

    height_error = _derived_standard_error(
        lambda candidate: float(
            np.max(
                evaluate_peak(
                    component.shape,
                    time,
                    component_parameters(candidate),
                )
            )
        ),
        vector,
        covariance,
        variables,
        active_indices,
    )
    fwhm_error = _derived_standard_error(
        lambda candidate: peak_fwhm(
            component.shape,
            component_parameters(candidate),
        ),
        vector,
        covariance,
        variables,
        active_indices,
    )
    area_error = _derived_standard_error(
        lambda candidate: float(
            trapezoid(
                evaluate_peak(
                    component.shape,
                    time,
                    component_parameters(candidate),
                ),
                time,
            )
        ),
        vector,
        covariance,
        variables,
        active_indices,
    )
    return height_error, fwhm_error, area_error


def _perturbed_start(
    initial: FloatArray,
    variables: list[_Variable],
    lower: FloatArray,
    upper: FloatArray,
    rng: np.random.Generator,
) -> FloatArray:
    """Create a bounded, parameter-aware multi-start proposal.

    Positive scale parameters are perturbed multiplicatively, centres relative
    to their own admissible cell, and shape fractions additively.  This avoids
    the former absolute ``15 % of value`` rule that moved a 20-minute centre by
    several minutes while barely perturbing a small width.
    """

    proposal = initial.copy()
    for index, variable in enumerate(variables):
        name = variable.key.rsplit(":", maxsplit=1)[-1]
        value = float(initial[index])
        if name in {"amplitude", "width", "gamma", "tau"}:
            proposal[index] = value * math.exp(float(rng.normal(0.0, 0.16)))
        elif name == "center":
            span = variable.upper - variable.lower
            proposal[index] = value + float(rng.normal(0.0, 0.08 * span))
        elif name == "eta":
            proposal[index] = value + float(rng.normal(0.0, 0.12))
        else:
            proposal[index] = value
    return np.clip(proposal, lower, upper)


def fit_peaks(
    time_min: npt.ArrayLike,
    baseline_corrected_signal: npt.ArrayLike,
    spec: PeakFitSpec,
    *,
    calibration: Calibration | None = None,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[Mapping[str, object]], None] | None = None,
) -> PeakFitResult:
    """Fit only the selected interval after applying its explicit peak direction."""
    full_time = np.asarray(time_min, dtype=np.float64)
    full_signal = np.asarray(baseline_corrected_signal, dtype=np.float64)
    if full_time.ndim != 1 or full_time.shape != full_signal.shape:
        return _failure(
            "shape_mismatch",
            "peak-fit arrays must be equal and one-dimensional",
            full_time,
            full_signal,
            spec,
        )
    finite = np.all(np.isfinite(full_time)) and np.all(np.isfinite(full_signal))
    if not finite or np.any(np.diff(full_time) <= 0):
        return _failure(
            "invalid_input",
            "peak-fit arrays must be finite with increasing time",
            full_time,
            full_signal,
            spec,
        )
    selected = (full_time >= spec.region.start_time_min) & (full_time <= spec.region.end_time_min)
    time = full_time[selected]
    observed = full_signal[selected] * spec.region.polarity.direction
    if len(time) < 5:
        return _failure(
            "insufficient_points",
            "peak-fit interval contains fewer than five points",
            time,
            observed,
            spec,
        )
    noise_sigma = robust_noise_sigma(observed)
    try:
        resolved_constraints = build_physical_constraints(spec, time, calibration)
        fit_spec = resolved_constraints.specification
        variables, constants, lookup = _build_variables(fit_spec, float(np.median(np.diff(time))))
    except AnalysisError as exc:
        return _failure(exc.code, str(exc), time, observed, spec)
    except ValueError as exc:
        return _failure("invalid_specification", str(exc), time, observed, spec)
    initial = np.asarray([variable.initial for variable in variables], dtype=np.float64)
    lower = np.asarray([variable.lower for variable in variables], dtype=np.float64)
    upper = np.asarray([variable.upper for variable in variables], dtype=np.float64)
    parameter_count = len(variables)
    automatic_start_count = (
        2
        if fit_spec.seed_strategy is PeakSeedStrategy.REFINE_MANUAL
        else 3
        if fit_spec.constraints is None or fit_spec.constraints.mode is not PeakFitMode.FLEXIBLE
        else 4
    )
    effective_multi_start = spec.multi_start or automatic_start_count
    effective_max_nfev = spec.max_nfev or min(
        4000,
        max(500, 120 * max(parameter_count, 1)),
    )
    live_best_vector: FloatArray | None = None
    live_best_rss = math.inf
    live_best_objective = math.inf
    evaluations_in_start = 0

    def objective_parts(
        vector: FloatArray,
    ) -> tuple[FloatArray, tuple[float, float, float, float], float]:
        total, _, parameters = _model(
            time, fit_spec, vector, variables, constants, lookup, calibration
        )
        data_residual = total - observed
        raw_rss = float(np.sum(data_residual**2))
        policy = fit_spec.constraints
        if policy is None:
            return data_residual, (raw_rss, 0.0, 0.0, 0.0), raw_rss
        normalized_data = data_residual / noise_sigma
        count_scale = float(len(time))
        width_terms: list[float] = []
        center_terms: list[float] = []
        overlap_terms: list[float] = []
        fwhms: list[float] = []
        centers: list[float] = []
        for component, values, context in zip(
            fit_spec.components,
            parameters,
            resolved_constraints.components,
            strict=True,
        ):
            fwhm = peak_fwhm(component.shape, values)
            fwhms.append(fwhm)
            center = float(values["center"])
            centers.append(center)
            ratio = fwhm / context.fwhm_bounds[1]
            denominator = max(1.0 - policy.soft_width_ratio, 1e-9)
            width_terms.append(max(0.0, ratio - policy.soft_width_ratio) / denominator)
            half_window = max(
                (context.center_bounds[1] - context.center_bounds[0]) / 2.0,
                np.finfo(np.float64).eps,
            )
            displacement = abs(center - context.initial_center) / half_window
            center_terms.append(max(0.0, displacement - 0.5))
        for first in range(len(centers)):
            for second in range(first + 1, len(centers)):
                distance = abs(centers[first] - centers[second]) / max(
                    (fwhms[first] + fwhms[second]) / 2.0,
                    np.finfo(np.float64).eps,
                )
                threshold = max(policy.overlap_warning_distance, 1e-9)
                overlap_terms.append(max(0.0, threshold - distance) / threshold)
        width_residual = np.sqrt(count_scale * policy.width_penalty_weight) * np.asarray(
            width_terms,
            dtype=np.float64,
        )
        center_residual = np.sqrt(count_scale * policy.center_penalty_weight) * np.asarray(
            center_terms,
            dtype=np.float64,
        )
        overlap_residual = np.sqrt(count_scale * policy.overlap_penalty_weight) * np.asarray(
            overlap_terms, dtype=np.float64
        )
        objective = np.concatenate(
            (normalized_data, width_residual, center_residual, overlap_residual)
        )
        return (
            objective,
            (
                float(np.sum(normalized_data**2)),
                float(np.sum(width_residual**2)),
                float(np.sum(center_residual**2)),
                float(np.sum(overlap_residual**2)),
            ),
            raw_rss,
        )

    def residual(vector: FloatArray) -> FloatArray:
        nonlocal live_best_objective, live_best_rss, live_best_vector
        nonlocal evaluations_in_start
        if cancelled is not None and cancelled():
            raise _PeakFitCancelled
        objective, _parts, current_rss = objective_parts(vector)
        evaluations_in_start += 1
        current_objective = float(np.sum(objective**2))
        if math.isfinite(current_objective) and current_objective < live_best_objective:
            live_best_objective = current_objective
            live_best_rss = current_rss
            live_best_vector = np.asarray(vector, dtype=np.float64).copy()
        return objective

    def data_rss(vector: FloatArray) -> float:
        total, _, _ = _model(time, fit_spec, vector, variables, constants, lookup, calibration)
        return float(np.sum((total - observed) ** 2))

    rng = np.random.default_rng(spec.random_seed)
    best = None
    best_objective = math.inf
    converged_starts: list[tuple[float, FloatArray]] = []
    total_nfev = 0
    stopped = False
    solver_limited = False
    interrupted_vector: FloatArray | None = None
    interrupted_objective = math.inf
    last_progress_at = 0.0
    starts = [initial]
    for _ in range(1, effective_multi_start):
        starts.append(_perturbed_start(initial, variables, lower, upper, rng))

    def progress_callback(
        start_number: int,
        evaluations_before_start: int,
    ) -> Callable[[object], None]:
        def report_iteration(intermediate_result: object) -> None:
            nonlocal last_progress_at
            now = time_module.monotonic()
            should_emit = progress is not None and (
                last_progress_at == 0.0
                or now - last_progress_at >= 0.08
                or (cancelled is not None and cancelled())
            )
            if should_emit and live_best_vector is not None and progress is not None:
                fitted, component_curves, _parameters = _model(
                    time,
                    fit_spec,
                    live_best_vector,
                    variables,
                    constants,
                    lookup,
                    calibration,
                )
                progress(
                    {
                        "start_index": start_number,
                        "total_starts": len(starts),
                        "nfev": evaluations_before_start
                        + int(getattr(intermediate_result, "nfev", 0)),
                        "max_nfev": effective_max_nfev * len(starts),
                        "best_rss": live_best_rss,
                        "time_min": tuple(float(value) for value in time),
                        "total_fit": tuple(float(value) for value in fitted),
                        "component_curves": tuple(
                            tuple(float(value) for value in curve) for curve in component_curves
                        ),
                    }
                )
                last_progress_at = now
            if cancelled is not None and cancelled():
                raise StopIteration

        return report_iteration

    jacobian: FloatArray | None
    try:
        if len(variables) == 0:
            total_fit, curves, parameters_by_component = _model(
                time, fit_spec, initial, variables, constants, lookup, calibration
            )
            jacobian = np.empty((len(time), 0), dtype=np.float64)
            optimizer_message = "all parameters fixed"
            if progress is not None:
                progress(
                    {
                        "start_index": 1,
                        "total_starts": 1,
                        "nfev": 0,
                        "max_nfev": 0,
                        "best_rss": float(np.sum((observed - total_fit) ** 2)),
                        "time_min": tuple(float(value) for value in time),
                        "total_fit": tuple(float(value) for value in total_fit),
                        "component_curves": tuple(
                            tuple(float(value) for value in curve) for curve in curves
                        ),
                    }
                )
        else:
            for start_index, start in enumerate(starts, start=1):
                if cancelled is not None and cancelled():
                    stopped = True
                    break

                evaluations_in_start = 0
                try:
                    candidate = least_squares(
                        residual,
                        start,
                        bounds=(lower, upper),
                        method="trf",
                        max_nfev=effective_max_nfev,
                        callback=progress_callback(start_index, total_nfev),
                        ftol=1e-7,
                        xtol=1e-7,
                        gtol=1e-7,
                        x_scale="jac",
                        loss=(
                            fit_spec.constraints.robust_loss.value
                            if fit_spec.constraints is not None
                            else "linear"
                        ),
                        f_scale=(
                            fit_spec.constraints.robust_f_scale
                            if fit_spec.constraints is not None
                            else 1.0
                        ),
                    )
                except _PeakFitCancelled:
                    total_nfev += evaluations_in_start
                    stopped = True
                    if live_best_vector is not None:
                        interrupted_vector = live_best_vector.copy()
                        interrupted_objective = live_best_objective
                    break
                total_nfev += candidate.nfev
                candidate_objective = float(2.0 * candidate.cost)
                if np.all(np.isfinite(candidate.fun)) and math.isfinite(candidate_objective):
                    converged_starts.append(
                        (
                            candidate_objective,
                            np.asarray(candidate.x, dtype=np.float64).copy(),
                        )
                    )
                improved = best is None or candidate_objective < best_objective
                if np.all(np.isfinite(candidate.fun)) and improved:
                    best = candidate
                    best_objective = candidate_objective
                candidate_rss = data_rss(np.asarray(candidate.x, dtype=np.float64))
                if math.isfinite(candidate_rss) and candidate_rss < live_best_rss:
                    live_best_rss = candidate_rss
                    live_best_vector = np.asarray(candidate.x, dtype=np.float64).copy()
                if int(candidate.status) == -2:
                    stopped = True
                    break
                if int(candidate.status) == 0:
                    solver_limited = True
            if best is None and interrupted_vector is None:
                if stopped:
                    return _failure(
                        "cancelled",
                        "peak fit was stopped before a usable estimate was available",
                        time,
                        observed,
                        spec,
                        total_nfev,
                    )
                return _failure(
                    "non_convergence",
                    "no multi-start fit converged",
                    time,
                    observed,
                    spec,
                    total_nfev,
                )
            use_interrupted = interrupted_vector is not None and (
                best is None or interrupted_objective < best_objective
            )
            if use_interrupted:
                assert interrupted_vector is not None
                final_vector = interrupted_vector
                best_objective = interrupted_objective
                jacobian = None
                optimizer_message = "stopped during residual evaluation"
            else:
                assert best is not None
                final_vector = np.asarray(best.x, dtype=np.float64)
                jacobian = np.asarray(best.jac, dtype=np.float64)[: len(time)]
                optimizer_message = str(best.message)
            total_fit, curves, parameters_by_component = _model(
                time, fit_spec, final_vector, variables, constants, lookup, calibration
            )
    except (FloatingPointError, ValueError) as exc:
        return _failure("solver_failure", str(exc), time, observed, spec, total_nfev)

    residual_values = observed - total_fit
    rss = float(np.sum(residual_values**2))
    count = len(time)
    variable_count = len(variables)
    rmse = math.sqrt(rss / count)
    centered = observed - float(np.mean(observed))
    total_sum_squares = float(np.sum(centered**2))
    r_squared = 1.0 - rss / total_sum_squares if total_sum_squares > 0 else math.nan
    adjusted_r_squared, aic, aicc, bic = information_criteria(
        rss,
        count,
        variable_count,
        r_squared,
    )
    uncertainty_rss = rss / noise_sigma**2 if fit_spec.constraints is not None else rss
    if len(variables) == 0:
        final_vector = initial
    jacobian_available = jacobian is not None and np.all(np.isfinite(jacobian))
    covariance = (
        _covariance_matrix(
            jacobian,
            uncertainty_rss,
            count,
            variable_count,
        )
        if jacobian_available and jacobian is not None
        else np.full((variable_count, variable_count), np.nan, dtype=np.float64)
    )
    errors = (
        np.sqrt(np.where(np.diag(covariance) >= 0.0, np.diag(covariance), np.nan))
        if variable_count
        else np.empty(0, dtype=np.float64)
    )
    error_by_key = {
        variable.key: float(error) if math.isfinite(float(error)) else None
        for variable, error in zip(variables, errors, strict=True)
    }
    areas = [float(trapezoid(curve, time)) for curve in curves]
    total_area = sum(areas)
    component_results: list[PeakComponentResult] = []
    for index, (component, parameters, area) in enumerate(
        zip(fit_spec.components, parameters_by_component, areas, strict=True)
    ):
        standard_errors: dict[str, float | None] = {}
        confidence: dict[str, tuple[float, float] | None] = {}
        boundary_hits: list[str] = []
        for name, value in parameters.items():
            key = lookup.get((index, name))
            error = error_by_key.get(key) if key is not None else None
            standard_errors[name] = error
            confidence[name] = (
                None if error is None else (value - 1.96 * error, value + 1.96 * error)
            )
            lower_bound, upper_bound = component.bounds.get(
                name,
                _default_bounds(
                    name,
                    fit_spec.region.start_time_min,
                    fit_spec.region.end_time_min,
                    float(np.median(np.diff(time))),
                ),
            )
            tolerance = 1e-6 * max(1.0, abs(value))
            if abs(value - lower_bound) <= tolerance or abs(value - upper_bound) <= tolerance:
                boundary_hits.append(name)
        component_results.append(
            PeakComponentResult(
                component.shape,
                parameters,
                standard_errors,
                confidence,
                tuple(boundary_hits),
                area,
                area / total_area if total_area > 0 else math.nan,
            )
        )
    for index, (component, parameters) in enumerate(
        zip(fit_spec.components, parameters_by_component, strict=True)
    ):
        log_bounds = component.fwhm_bounds_log10_m
        if log_bounds is None:
            continue
        if calibration is None:
            return _failure(
                "missing_peak_calibration",
                "a calibration is required for log-M FWHM constraints",
                time,
                observed,
                spec,
                total_nfev,
            )
        try:
            actual_log_width = calibrated_log_fwhm(
                calibration,
                float(parameters["center"]),
                peak_fwhm(component.shape, parameters),
            )
        except (AnalysisError, ArithmeticError, ValueError) as exc:
            return _failure(
                "log_width_constraint_violation",
                f"component {index + 1} log-M FWHM could not be validated: {exc}",
                time,
                observed,
                spec,
                total_nfev,
            )
        tolerance = max(1e-9, (log_bounds[1] - log_bounds[0]) * 1e-6)
        if actual_log_width < log_bounds[0] - tolerance or (
            actual_log_width > log_bounds[1] + tolerance
        ):
            return _failure(
                "log_width_constraint_violation",
                (
                    f"component {index + 1} fitted log-M FWHM {actual_log_width:.8g} "
                    f"is outside the hard interval [{log_bounds[0]:.8g}, "
                    f"{log_bounds[1]:.8g}]"
                ),
                time,
                observed,
                spec,
                total_nfev,
            )
    common_diagnostics = component_diagnostics(
        time,
        curves,
        fit_spec.components,
        component_results,
        resolved_constraints.components,
        noise_sigma,
        fit_spec.constraints,
        calibration,
    )
    derived_diagnostics = []
    for component_index, (component, diagnostic) in enumerate(
        zip(fit_spec.components, common_diagnostics, strict=True)
    ):
        height_error, fwhm_error, area_error = _component_derived_uncertainties(
            component_index,
            component,
            time,
            final_vector,
            covariance,
            variables,
            constants,
            lookup,
            calibration,
        )
        derived_diagnostics.append(
            replace(
                diagnostic,
                height_uncertainty=height_error,
                fwhm_uncertainty=fwhm_error,
                area_uncertainty=area_error,
            )
        )
    common_diagnostics = tuple(derived_diagnostics)
    residual_evidence = residual_diagnostics(residual_values, noise_sigma)
    if jacobian_available and jacobian is not None:
        condition_number, maximum_correlation = jacobian_identifiability(jacobian)
        correlation_pairs = strong_parameter_correlations(
            jacobian,
            tuple(variable.key for variable in variables),
        )
    else:
        condition_number, maximum_correlation = None, None
        correlation_pairs = ()
    if correlation_pairs:
        affected: dict[int, set[str]] = {}
        for first, second, _correlation in correlation_pairs:
            pair = (first, second)
            for key in pair:
                parts = key.split(":")
                if len(parts) == 3 and parts[0] == "component":
                    affected.setdefault(int(parts[1]), set()).update(
                        value.split(":")[-1] for value in pair
                    )
        updated_diagnostics = []
        for index, diagnostic in enumerate(common_diagnostics):
            flags = list(diagnostic.flags)
            names = affected.get(index, set())
            if names:
                flags.append("highly_correlated")
                shape = fit_spec.components[index].shape
                if shape.value == "voigt" and {"width", "gamma"}.issubset(names):
                    flags.append("shape_fraction_unstable")
                if shape.value == "pseudo_voigt" and "eta" in names:
                    flags.append("shape_fraction_unstable")
                if shape.value == "exponentially_modified_gaussian" and "tau" in names:
                    flags.append("tail_parameter_unstable")
            updated_diagnostics.append(replace(diagnostic, flags=tuple(dict.fromkeys(flags))))
        common_diagnostics = tuple(updated_diagnostics)
    near_start_count = 0
    maximum_start_spread = 0.0
    unstable_start_components: set[int] = set()
    if fit_spec.constraints is not None and len(converged_starts) > 1:
        tolerance = fit_spec.constraints.multistart_objective_tolerance
        objective_scale = max(abs(best_objective), 1.0)
        near_vectors = [
            vector
            for objective, vector in converged_starts
            if (objective - best_objective) / objective_scale <= tolerance
        ]
        near_start_count = len(near_vectors)
        if len(near_vectors) > 1:
            matrix = np.vstack(near_vectors)
            for variable_index, variable in enumerate(variables):
                values = matrix[:, variable_index]
                name = variable.key.rsplit(":", maxsplit=1)[-1]
                scale = max(abs(float(np.mean(values))), np.finfo(np.float64).eps)
                if name == "center":
                    scale = max(variable.upper - variable.lower, scale)
                spread = float(np.ptp(values) / scale)
                maximum_start_spread = max(maximum_start_spread, spread)
                if spread > 0.20:
                    parts = variable.key.split(":")
                    if len(parts) == 3 and parts[0] == "component":
                        unstable_start_components.add(int(parts[1]))
            if unstable_start_components:
                common_diagnostics = tuple(
                    replace(
                        diagnostic,
                        flags=tuple(
                            dict.fromkeys(
                                (
                                    *diagnostic.flags,
                                    *(
                                        ("multistart_unstable",)
                                        if index in unstable_start_components
                                        else ()
                                    ),
                                )
                            )
                        ),
                    )
                    for index, diagnostic in enumerate(common_diagnostics)
                )
    component_results = [
        replace(result, diagnostics=diagnostic)
        for result, diagnostic in zip(component_results, common_diagnostics, strict=True)
    ]
    _objective, objective_terms, _raw_rss = objective_parts(final_vector)
    fit_diagnostics = build_fit_diagnostics(
        adjusted_r_squared=adjusted_r_squared,
        aicc=aicc,
        residual=residual_evidence,
        condition_number=condition_number,
        maximum_parameter_correlation=maximum_correlation,
        data_loss=objective_terms[0],
        width_penalty=objective_terms[1],
        center_penalty=objective_terms[2],
        overlap_penalty=objective_terms[3],
        rmse=rmse,
        components=common_diagnostics,
        warnings=resolved_constraints.warnings,
    )
    fit_diagnostics = replace(
        fit_diagnostics,
        debug_values=(
            ("noise_sigma", noise_sigma),
            ("data_loss", objective_terms[0]),
            ("width_penalty", objective_terms[1]),
            ("center_penalty", objective_terms[2]),
            ("overlap_penalty", objective_terms[3]),
            ("condition_number", condition_number if condition_number is not None else "n/a"),
            (
                "maximum_parameter_correlation",
                maximum_correlation if maximum_correlation is not None else "n/a",
            ),
            ("multistart_near_solutions", float(near_start_count)),
            ("multistart_max_relative_spread", maximum_start_spread),
            ("free_parameter_count", float(parameter_count)),
            ("effective_multi_start", float(effective_multi_start)),
            ("effective_max_nfev", float(effective_max_nfev)),
            ("solver_tolerance", 1e-7),
            *tuple(
                (f"correlation:{first}|{second}", value)
                for first, second, value in correlation_pairs[:20]
            ),
        ),
    )
    failure_code = "cancelled" if stopped else "evaluation_limit" if solver_limited else None
    if stopped:
        result_message = "stopped by user; retained the best estimate reached so far"
    elif solver_limited:
        result_message = "evaluation limit reached; retained the best estimate reached so far"
    else:
        result_message = optimizer_message
    return PeakFitResult(
        True,
        failure_code,
        result_message,
        tuple(float(value) for value in time),
        tuple(float(value) for value in observed),
        tuple(float(value) for value in total_fit),
        tuple(float(value) for value in residual_values),
        tuple(tuple(float(value) for value in curve) for curve in curves),
        tuple(component_results),
        rss,
        rmse,
        r_squared,
        aic,
        bic,
        total_nfev,
        spec.random_seed,
        "physical-local-trf-multistart-v4",
        spec.schema_version,
        adjusted_r_squared,
        aicc,
        fit_diagnostics,
    )
