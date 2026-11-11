"""Presentation precision only: never round the analysis data in place."""

from __future__ import annotations

import math
from numbers import Real
import re
from typing import Any, Mapping

import numpy as np
import pandas as pd


def display_decimals(config: Mapping[str, Any]) -> int:
    return int(config.get("visualization", {}).get(
        "decimal_places", config.get("analysis", {}).get("decimal_places", 4)
    ))


def format_number(value: Real, decimals: int = 4) -> str:
    if pd.isna(value):
        return "—"
    number = float(value)
    if not math.isfinite(number):
        return "—"
    if number != 0 and abs(number) < 0.5 * 10 ** -decimals:
        return f"{number:.{decimals}e}"
    # Avoid showing negative zero from numerical noise.
    return f"{0.0 if number == 0 else number:.{decimals}f}"


def numeric_format(values: Any, decimals: int) -> str:
    numbers = [float(v) for v in np.asarray(values, dtype=object).flat
               if isinstance(v, Real) and not isinstance(v, (bool, np.bool_))
               and math.isfinite(float(v))]
    tiny = any(0 < abs(v) < 0.5 * 10 ** -decimals for v in numbers)
    return f".{decimals}{'e' if tiny else 'f'}"


def apply_figure_precision(figure: Any, config: Mapping[str, Any]) -> None:
    """Format Plotly hover fields, labels, axes and color scales consistently."""
    decimals = display_decimals(config)
    token = re.compile(r"%\{(x|y|z|r|text|customdata\[\d+\])(?::[^}]+)?\}")
    for trace in figure.data:
        formats: dict[str, str] = {}
        for field in ("x", "y", "z", "r", "text"):
            values = getattr(trace, field, None)
            if values is not None and any(
                isinstance(v, Real) and not isinstance(v, (bool, np.bool_))
                for v in np.asarray(values, dtype=object).flat
            ):
                formats[field] = numeric_format(values, decimals)
        custom = getattr(trace, "customdata", None)
        if custom is not None:
            array = np.asarray(custom, dtype=object)
            if array.ndim >= 2:
                for index in range(array.shape[-1]):
                    values = array[..., index]
                    valid = [v for v in values.flat if v is not None and not pd.isna(v)]
                    if valid and all(isinstance(v, Real) and not isinstance(v, bool) for v in valid):
                        # Source row and replicate fields retain integer display.
                        if not all(isinstance(v, (int, np.integer)) for v in valid):
                            formats[f"customdata[{index}]"] = numeric_format(values, decimals)
        for template_field in ("hovertemplate", "texttemplate"):
            template = getattr(trace, template_field, None)
            if isinstance(template, str):
                updated = token.sub(
                    lambda m: "%{" + m[1] + ":" + formats[m[1]] + "}"
                    if m[1] in formats else m[0], template
                )
                setattr(trace, template_field, updated)
        for axis in ("x", "y", "z"):
            property_name = axis + "hoverformat"
            if property_name in trace._valid_props and axis in formats:
                trace[property_name] = formats[axis]
        if "colorbar" in trace._valid_props and not trace.colorbar.ticktext:
            trace.colorbar.tickformat = formats.get("z", f".{decimals}f")
    for axis in figure.select_xaxes():
        axis.hoverformat = f".{decimals}f"
    for axis in figure.select_yaxes():
        axis.hoverformat = f".{decimals}f"
    for coloraxis in figure.select_coloraxes():
        if not coloraxis.colorbar.ticktext:
            coloraxis.colorbar.tickformat = f".{decimals}f"
