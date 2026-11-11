"""Evidence-preserving AIA/ANDI and generic NetCDF chromatogram importer."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import netcdf_file

from gpcsec.models import FormatProbe, ImportResult, ParsedRowIssue, RawChromatogram, RawFileRecord

_TIME_CANDIDATES = (
    "retention_time",
    "retention_times",
    "time",
    "times",
    "scan_acquisition_time",
)
_SIGNAL_CANDIDATES = (
    "ordinate_values",
    "detector_response",
    "signal",
    "intensity",
    "absorbance",
)
_INTERVAL_CANDIDATES = (
    "actual_sampling_interval",
    "sampling_interval",
    "time_increment",
)
_DELAY_CANDIDATES = (
    "actual_delay_time",
    "delay_time",
    "start_time",
)


@dataclass(frozen=True, slots=True)
class NetcdfImportOptions:
    time_variable: str | None = None
    signal_variable: str | None = None
    channel_index: int = 0


def _normalized_variables(variables: dict[str, Any]) -> dict[str, str]:
    return {name.casefold(): name for name in variables}


def _variable_name(
    variables: dict[str, Any],
    requested: str | None,
    candidates: tuple[str, ...],
    label: str,
) -> str | None:
    normalized = _normalized_variables(variables)
    if requested:
        match = normalized.get(requested.casefold())
        if match is None:
            raise ValueError(f"NetCDF {label} variable {requested!r} was not found")
        return match
    return next((normalized[item] for item in candidates if item in normalized), None)


def _array(variable: Any, channel_index: int, *, label: str) -> np.ndarray:
    values = np.asarray(variable[:], dtype=float)
    values = np.squeeze(values)
    if values.ndim == 0:
        return values.reshape(1)
    if values.ndim == 1:
        return values.copy()
    if not 0 <= channel_index < values.shape[0]:
        raise ValueError(
            f"NetCDF {label} channel {channel_index} is outside 0..{values.shape[0] - 1}"
        )
    return np.asarray(values[channel_index], dtype=float).reshape(-1).copy()


def _units(variable: Any) -> str:
    value = getattr(variable, "units", "")
    if isinstance(value, bytes):
        return value.decode("ascii", errors="ignore").strip().casefold()
    return str(value).strip().casefold()


def _time_scale(units: str) -> float:
    if "millisecond" in units or units in {"ms", "msec"}:
        return 1.0 / 60_000.0
    if "second" in units or units in {"s", "sec"}:
        return 1.0 / 60.0
    if "hour" in units or units in {"h", "hr"}:
        return 60.0
    return 1.0


def _scalar(
    variables: dict[str, Any],
    candidates: tuple[str, ...],
    *,
    default: float,
) -> tuple[float, str]:
    normalized = _normalized_variables(variables)
    for candidate in candidates:
        name = normalized.get(candidate)
        if name is None:
            continue
        variable = variables[name]
        values = np.asarray(variable[:], dtype=float).reshape(-1)
        if values.size and math.isfinite(float(values[0])):
            return float(values[0]), _units(variable)
    return default, ""


def import_netcdf_bytes(
    raw: bytes,
    source_name: str,
    options: NetcdfImportOptions | None = None,
) -> ImportResult:
    """Read one chromatogram while retaining the original NetCDF bytes unchanged."""

    selected = options or NetcdfImportOptions()
    if selected.channel_index < 0:
        raise ValueError("NetCDF channel index must not be negative")
    try:
        with netcdf_file(BytesIO(raw), mode="r", mmap=False) as dataset:
            variables = dict(dataset.variables)
            signal_name = _variable_name(
                variables,
                selected.signal_variable,
                _SIGNAL_CANDIDATES,
                "signal",
            )
            if signal_name is None:
                available = ", ".join(sorted(variables))
                raise ValueError(f"NetCDF contains no recognized signal variable ({available})")
            signal = _array(
                variables[signal_name],
                selected.channel_index,
                label="signal",
            )
            time_name = _variable_name(
                variables,
                selected.time_variable,
                _TIME_CANDIDATES,
                "time",
            )
            if time_name is not None:
                time_variable = variables[time_name]
                time_values = _array(time_variable, selected.channel_index, label="time")
                time_min = time_values * _time_scale(_units(time_variable))
            else:
                interval, interval_units = _scalar(
                    variables,
                    _INTERVAL_CANDIDATES,
                    default=1.0,
                )
                delay, delay_units = _scalar(
                    variables,
                    _DELAY_CANDIDATES,
                    default=0.0,
                )
                interval_min = interval * _time_scale(interval_units or "second")
                delay_min = delay * _time_scale(delay_units or interval_units or "second")
                time_min = delay_min + np.arange(signal.size, dtype=float) * interval_min
    except (OSError, TypeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith("NetCDF"):
            raise
        raise ValueError(f"invalid or unsupported NetCDF chromatogram: {error}") from error

    if time_min.size != signal.size:
        raise ValueError(
            f"NetCDF time/signal length mismatch: {time_min.size} versus {signal.size}"
        )
    times: list[float] = []
    signals: list[float] = []
    issues: list[ParsedRowIssue] = []
    for index, (time_value, signal_value) in enumerate(zip(time_min, signal, strict=True), 1):
        time_float = float(time_value)
        signal_float = float(signal_value)
        if not math.isfinite(time_float) or not math.isfinite(signal_float):
            issues.append(
                ParsedRowIssue(
                    index,
                    f"time={time_float!r}, signal={signal_float!r}",
                    "non-finite NetCDF value",
                )
            )
            continue
        times.append(time_float)
        signals.append(signal_float)
    if len(times) < 2:
        raise ValueError("fewer than two valid NetCDF points")

    digest = hashlib.sha256(raw).hexdigest()
    raw_file = RawFileRecord(source_name, digest, len(raw), raw)
    chromatogram = RawChromatogram(tuple(times), tuple(signals), signal_name, digest)
    probe = FormatProbe(
        encoding="binary-netcdf",
        delimiter="variables",
        has_header=False,
        decimal_mark=".",
        column_count=2,
        confidence=1.0 if time_name is not None else 0.9,
        newline="n/a",
    )
    return ImportResult(raw_file, chromatogram, probe, tuple(issues))


def import_netcdf_file(
    path: Path,
    options: NetcdfImportOptions | None = None,
) -> ImportResult:
    return import_netcdf_bytes(path.read_bytes(), path.name, options)
