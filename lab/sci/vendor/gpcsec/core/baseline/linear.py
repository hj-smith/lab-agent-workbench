"""Two-point and multi-anchor piecewise-linear baselines."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from gpcsec.models import AnalysisError, BaselineResult, ProcessingStep


def _validated_time(time_min: npt.ArrayLike) -> npt.NDArray[np.float64]:
    time = np.asarray(time_min, dtype=np.float64)
    if time.ndim != 1 or len(time) < 2 or not np.all(np.isfinite(time)):
        raise AnalysisError("invalid_time", "time must be a finite one-dimensional array")
    if np.any(np.diff(time) <= 0):
        raise AnalysisError("non_increasing_time", "time must be strictly increasing")
    return time


def linear_anchor_baseline(
    time_min: npt.ArrayLike, anchors: tuple[tuple[float, float], ...]
) -> BaselineResult:
    time = _validated_time(time_min)
    if len(anchors) < 2:
        raise AnalysisError("insufficient_baseline_anchors", "at least two anchors are required")
    ordered = tuple(sorted(anchors))
    anchor_time = np.asarray([item[0] for item in ordered], dtype=np.float64)
    anchor_signal = np.asarray([item[1] for item in ordered], dtype=np.float64)
    if np.any(np.diff(anchor_time) <= 0) or not np.all(np.isfinite(anchor_signal)):
        raise AnalysisError(
            "invalid_baseline_anchors", "baseline anchors must be finite and unique"
        )
    baseline = np.interp(time, anchor_time, anchor_signal)
    step = ProcessingStep(
        "baseline.linear_anchors",
        "linear-anchors-v1",
        {"anchors": ordered, "outside_anchor_policy": "constant"},
    )
    return BaselineResult(
        tuple(float(value) for value in time),
        tuple(float(value) for value in baseline),
        ordered,
        step,
    )


def two_point_baseline(
    time_min: npt.ArrayLike,
    signal: npt.ArrayLike,
    start_time_min: float,
    end_time_min: float,
) -> BaselineResult:
    time = _validated_time(time_min)
    values = np.asarray(signal, dtype=np.float64)
    if values.shape != time.shape or not np.all(np.isfinite(values)):
        raise AnalysisError("invalid_signal", "signal must match time and contain finite values")
    if not time[0] <= start_time_min < end_time_min <= time[-1]:
        raise AnalysisError("baseline_anchor_out_of_range", "baseline anchors must lie within time")
    anchors = (
        (start_time_min, float(np.interp(start_time_min, time, values))),
        (end_time_min, float(np.interp(end_time_min, time, values))),
    )
    slope = (anchors[1][1] - anchors[0][1]) / (end_time_min - start_time_min)
    baseline = anchors[0][1] + slope * (time - start_time_min)
    step = ProcessingStep(
        "baseline.two_point",
        "two-point-v1",
        {"start_time_min": start_time_min, "end_time_min": end_time_min},
    )
    return BaselineResult(
        tuple(float(value) for value in time),
        tuple(float(value) for value in baseline),
        anchors,
        step,
    )
