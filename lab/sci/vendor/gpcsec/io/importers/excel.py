"""Minimal evidence-preserving XLSX chromatogram importer."""

from __future__ import annotations

import hashlib
from io import BytesIO
from itertools import chain
from typing import Any

from openpyxl import load_workbook

from gpcsec.models import FormatProbe, ImportResult, ParsedRowIssue, RawChromatogram, RawFileRecord


def import_xlsx_bytes(
    raw: bytes,
    source_name: str,
    *,
    sheet_name: str | None = None,
    time_column: int | str = 1,
    signal_column: int | str = 2,
    has_header: bool = True,
) -> ImportResult:
    workbook = load_workbook(BytesIO(raw), read_only=True, data_only=True)
    worksheet = workbook[sheet_name] if sheet_name else workbook.active
    if worksheet is None:
        raise ValueError("workbook has no active worksheet")
    rows = worksheet.iter_rows(values_only=True)
    first_row = tuple(next(rows, ()))

    def column_index(reference: int | str) -> int:
        if isinstance(reference, int):
            index = reference - 1
        else:
            cleaned = reference.strip()
            normalized = [str(value).strip().casefold() for value in first_row]
            if has_header and cleaned.casefold() in normalized:
                return normalized.index(cleaned.casefold())
            if cleaned.isdecimal():
                index = int(cleaned) - 1
            elif cleaned.isalpha() and cleaned.isascii():
                index = 0
                for character in cleaned.upper():
                    index = index * 26 + (ord(character) - ord("A") + 1)
                index -= 1
            else:
                raise ValueError(f"XLSX column {reference!r} was not found")
        if index < 0:
            raise ValueError("XLSX column indexes are one-based")
        return index

    time_index = column_index(time_column)
    signal_index = column_index(signal_column)
    if time_index == signal_index:
        raise ValueError("time and signal columns must be different")
    data_rows: Any = rows if has_header else chain((first_row,), rows)
    times: list[float] = []
    signals: list[float] = []
    issues: list[ParsedRowIssue] = []
    start_row = 2 if has_header else 1
    for row_number, row in enumerate(data_rows, start_row):
        time_raw = row[time_index] if len(row) > time_index else None
        signal_raw = row[signal_index] if len(row) > signal_index else None
        if time_raw is None and signal_raw is None:
            continue
        try:
            time_value = float(time_raw)  # type: ignore[arg-type]
            signal_value = float(signal_raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            issues.append(ParsedRowIssue(row_number, repr(row), "time or signal is not numeric"))
            continue
        times.append(time_value)
        signals.append(signal_value)
    workbook.close()
    if len(times) < 2:
        raise ValueError("fewer than two valid XLSX rows")
    digest = hashlib.sha256(raw).hexdigest()
    probe = FormatProbe(
        "xlsx",
        "worksheet-cells",
        has_header,
        ".",
        max(time_index, signal_index) + 1,
        1.0,
        "n/a",
    )
    record = RawFileRecord(source_name, digest, len(raw), raw)
    chromatogram = RawChromatogram(tuple(times), tuple(signals), "detector_1", digest)
    return ImportResult(record, chromatogram, probe, tuple(issues))
