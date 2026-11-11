"""Exact-boundary, nonuniform, interval-polarity integration."""

from gpcsec.core.integration.regions import (
    detect_polarities,
    integrate_regions,
    summarize_integration_areas,
    union_effective_segments,
)

__all__ = [
    "detect_polarities",
    "integrate_regions",
    "summarize_integration_areas",
    "union_effective_segments",
]
