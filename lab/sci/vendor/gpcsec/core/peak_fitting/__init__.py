"""Bounded peak-shape evaluation and evidence-led model selection."""

from gpcsec.core.peak_fitting.automatic import (
    PeakCandidate,
    detect_peak_candidates,
    fit_peaks_automatically,
)
from gpcsec.core.peak_fitting.fit import fit_peaks
from gpcsec.core.peak_fitting.shapes import (
    evaluate_peak,
    initial_parameters,
    parameter_names,
    parameter_semantics,
    peak_fwhm,
    peak_height,
)

__all__ = [
    "PeakCandidate",
    "detect_peak_candidates",
    "evaluate_peak",
    "fit_peaks",
    "fit_peaks_automatically",
    "initial_parameters",
    "parameter_names",
    "parameter_semantics",
    "peak_fwhm",
    "peak_height",
]
