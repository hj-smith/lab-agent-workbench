"""Shape-controlled multi-anchor baselines for interactive manual review."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
from scipy.interpolate import CubicSpline, PchipInterpolator

from gpcsec.models import AnalysisError, BaselineResult, ProcessingStep


def interpolated_anchor_baseline(
    time_min: npt.ArrayLike,
    anchors: tuple[tuple[float, float], ...],
    *,
    method: str,
) -> BaselineResult:
    """Evaluate reviewed anchors on the sample grid without extrapolating past anchors.

    Anchors describe the baseline function and need not lie inside the sample grid.
    Retain their exact coordinates and responses when restoring a saved analysis.

    PCHIP is shape preserving and is the safer smooth default. Cubic spline is
    intentionally available as an expert option because it can overshoot between
    sparse or noisy anchors.
    """
    time = np.asarray(time_min, dtype=np.float64)
    if time.ndim != 1 or len(time) < 2 or not np.all(np.isfinite(time)):
        raise AnalysisError("invalid_time", "time must be a finite one-dimensional array")
    if np.any(np.diff(time) <= 0):
        raise AnalysisError("non_increasing_time", "time must be strictly increasing")
    minimum_anchors = 3 if method == "pchip_anchors" else 4
    if method not in {"pchip_anchors", "cubic_spline_anchors"}:
        raise AnalysisError("unsupported_baseline_method", f"unsupported baseline method: {method}")
    if len(anchors) < minimum_anchors:
        raise AnalysisError(
            "insufficient_baseline_anchors",
            f"{method} requires at least {minimum_anchors} anchors",
        )
    ordered = tuple(sorted(anchors))
    anchor_time = np.asarray([item[0] for item in ordered], dtype=np.float64)
    anchor_signal = np.asarray([item[1] for item in ordered], dtype=np.float64)
    if (
        np.any(np.diff(anchor_time) <= 0)
        or not np.all(np.isfinite(anchor_time))
        or not np.all(np.isfinite(anchor_signal))
    ):
        raise AnalysisError(
            "invalid_baseline_anchors", "baseline anchors must be finite and unique"
        )
    clipped_time = np.clip(time, anchor_time[0], anchor_time[-1])
    if method == "pchip_anchors":
        baseline = np.asarray(
            PchipInterpolator(anchor_time, anchor_signal, extrapolate=False)(clipped_time),
            dtype=np.float64,
        )
        version = "pchip-anchors-v1"
    else:
        baseline = np.asarray(
            CubicSpline(anchor_time, anchor_signal, bc_type="natural", extrapolate=False)(
                clipped_time
            ),
            dtype=np.float64,
        )
        version = "natural-cubic-spline-anchors-v1"
    baseline[time < anchor_time[0]] = anchor_signal[0]
    baseline[time > anchor_time[-1]] = anchor_signal[-1]
    step = ProcessingStep(
        f"baseline.{method}",
        version,
        {"anchors": ordered, "outside_anchor_policy": "constant"},
    )
    return BaselineResult(
        tuple(float(value) for value in time),
        tuple(float(value) for value in baseline),
        ordered,
        step,
    )
