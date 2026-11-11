"""Explicit baseline construction."""

from gpcsec.core.baseline.automatic import BaselineEstimator, estimate_baseline
from gpcsec.core.baseline.interpolated import interpolated_anchor_baseline
from gpcsec.core.baseline.linear import linear_anchor_baseline, two_point_baseline
from gpcsec.core.baseline.peak_aware import (
    detect_baseline_peak_windows,
    merge_baseline_exclusions,
    suggest_peak_aware_baseline,
)

__all__ = [
    "BaselineEstimator",
    "detect_baseline_peak_windows",
    "estimate_baseline",
    "interpolated_anchor_baseline",
    "linear_anchor_baseline",
    "merge_baseline_exclusions",
    "suggest_peak_aware_baseline",
    "two_point_baseline",
]
