"""Numerically stable peak-shape functions with explicit parameter semantics."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import numpy.typing as npt
from scipy.optimize import brentq, minimize_scalar
from scipy.special import voigt_profile
from scipy.stats import exponnorm

from gpcsec.models import PeakComponentSpec, PeakShape

FloatArray = npt.NDArray[np.float64]

_GAUSSIAN_FWHM_FACTOR = 2.0 * np.sqrt(2.0 * np.log(2.0))


def parameter_semantics(shape: PeakShape) -> Mapping[str, str]:
    """Return the historical parameter meanings without changing persisted data.

    ``amplitude`` is the value at the location parameter, not integrated area.  For
    EMG the mode is displaced from that location, so the actual height is reported
    separately by :func:`peak_height`.
    """

    common = {
        "amplitude": "profile value at the location parameter (not area)",
        "center": "retention-time location parameter in minutes",
    }
    if shape is PeakShape.GAUSSIAN:
        return {**common, "width": "Gaussian sigma in minutes"}
    if shape is PeakShape.LORENTZIAN:
        return {**common, "width": "Lorentzian HWHM in minutes"}
    if shape is PeakShape.VOIGT:
        return {
            **common,
            "width": "Gaussian sigma in minutes",
            "gamma": "Lorentzian HWHM in minutes",
        }
    if shape is PeakShape.PSEUDO_VOIGT:
        return {
            **common,
            "width": "legacy shared Gaussian-sigma/Lorentzian-HWHM scale",
            "eta": "Lorentzian mixing fraction",
        }
    return {
        **common,
        "width": "Gaussian sigma in minutes",
        "tau": "exponential decay time in minutes",
    }


def initial_parameters(component: PeakComponentSpec) -> dict[str, float]:
    """Translate one persisted component specification to shape parameters."""
    values = {
        "amplitude": component.amplitude,
        "center": component.center_time_min,
        "width": component.width_time_min,
    }
    if component.shape is PeakShape.VOIGT:
        values["gamma"] = component.shape_parameter or component.width_time_min
    elif component.shape is PeakShape.PSEUDO_VOIGT:
        values["eta"] = 0.5 if component.shape_parameter is None else component.shape_parameter
    elif component.shape is PeakShape.EMG:
        values["tau"] = component.shape_parameter or component.width_time_min
    return values


def parameter_names(shape: PeakShape) -> tuple[str, ...]:
    common = ("amplitude", "center", "width")
    if shape is PeakShape.VOIGT:
        return (*common, "gamma")
    if shape is PeakShape.PSEUDO_VOIGT:
        return (*common, "eta")
    if shape is PeakShape.EMG:
        return (*common, "tau")
    return common


def evaluate_peak(
    shape: PeakShape,
    time_min: npt.ArrayLike,
    parameters: Mapping[str, float],
) -> FloatArray:
    """Evaluate a peak where amplitude is the value at the location parameter."""
    time = np.asarray(time_min, dtype=np.float64)
    amplitude = float(parameters["amplitude"])
    center = float(parameters["center"])
    width = float(parameters["width"])
    offset = time - center
    if shape is PeakShape.GAUSSIAN:
        profile = np.exp(-0.5 * (offset / width) ** 2)
    elif shape is PeakShape.LORENTZIAN:
        profile = 1.0 / (1.0 + (offset / width) ** 2)
    elif shape is PeakShape.VOIGT:
        gamma = float(parameters["gamma"])
        profile = voigt_profile(offset, width, gamma)
        profile = profile / float(voigt_profile(0.0, width, gamma))
    elif shape is PeakShape.PSEUDO_VOIGT:
        eta = float(parameters["eta"])
        gaussian = np.exp(-0.5 * (offset / width) ** 2)
        lorentzian = 1.0 / (1.0 + (offset / width) ** 2)
        profile = (1.0 - eta) * gaussian + eta * lorentzian
    else:
        tau = float(parameters["tau"])
        distribution = exponnorm(tau / width, loc=center, scale=width)
        reference = float(distribution.pdf(center))
        profile = distribution.pdf(time) / reference
    return np.asarray(amplitude * profile, dtype=np.float64)


def peak_height(shape: PeakShape, parameters: Mapping[str, float]) -> float:
    """Return the actual maximum height of one component."""

    if shape is not PeakShape.EMG:
        return float(parameters["amplitude"])
    center = float(parameters["center"])
    width = float(parameters["width"])
    tau = float(parameters["tau"])
    radius = max(8.0 * width, 10.0 * tau)
    optimum = minimize_scalar(
        lambda value: -float(evaluate_peak(shape, (value,), parameters)[0]),
        bounds=(center - radius, center + radius),
        method="bounded",
    )
    if not optimum.success:
        return float(parameters["amplitude"])
    return float(-optimum.fun)


def peak_fwhm(shape: PeakShape, parameters: Mapping[str, float]) -> float:
    """Return a common full width at half maximum in retention-time minutes.

    The conversion is analytic where possible.  Pseudo-Voigt and EMG retain their
    legacy parameterization and are converted numerically, preventing the UI from
    comparing unlike internal ``width`` parameters.
    """

    width = float(parameters["width"])
    if shape is PeakShape.GAUSSIAN:
        return float(_GAUSSIAN_FWHM_FACTOR * width)
    if shape is PeakShape.LORENTZIAN:
        return 2.0 * width
    if shape is PeakShape.VOIGT:
        gaussian_fwhm = float(_GAUSSIAN_FWHM_FACTOR * width)
        lorentzian_fwhm = 2.0 * float(parameters["gamma"])
        return float(
            0.5346 * lorentzian_fwhm + np.sqrt(0.2166 * lorentzian_fwhm**2 + gaussian_fwhm**2)
        )
    center = float(parameters["center"])
    if shape is PeakShape.PSEUDO_VOIGT:
        target = 0.5 * float(parameters["amplitude"])
        radius = max(20.0 * width, np.finfo(np.float64).eps)
        root = brentq(
            lambda offset: float(evaluate_peak(shape, (center + offset,), parameters)[0] - target),
            np.finfo(np.float64).eps,
            radius,
        )
        return 2.0 * float(root)

    height = peak_height(shape, parameters)
    target = 0.5 * height
    tau = float(parameters["tau"])
    radius = max(12.0 * width, 16.0 * tau)
    grid = np.linspace(center - radius, center + radius, 1025, dtype=np.float64)
    values = evaluate_peak(shape, grid, parameters)
    mode_index = int(np.argmax(values))
    left_candidates = np.flatnonzero(values[: mode_index + 1] <= target)
    right_candidates = np.flatnonzero(values[mode_index:] <= target)
    if not len(left_candidates) or not len(right_candidates):
        return float(np.sqrt((_GAUSSIAN_FWHM_FACTOR * width) ** 2 + (1.7 * tau) ** 2))
    left_index = int(left_candidates[-1])
    right_index = int(mode_index + right_candidates[0])
    left = brentq(
        lambda value: float(evaluate_peak(shape, (value,), parameters)[0] - target),
        float(grid[left_index]),
        float(grid[min(left_index + 1, mode_index)]),
    )
    right = brentq(
        lambda value: float(evaluate_peak(shape, (value,), parameters)[0] - target),
        float(grid[max(mode_index, right_index - 1)]),
        float(grid[right_index]),
    )
    return float(right - left)
