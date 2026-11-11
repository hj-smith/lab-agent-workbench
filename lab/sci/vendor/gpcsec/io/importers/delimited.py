"""Evidence-preserving delimited text probe and importer."""

from __future__ import annotations

import csv
import hashlib
import math
import statistics
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from gpcsec.models import FormatProbe, ImportResult, ParsedRowIssue, RawChromatogram, RawFileRecord

ENCODING_CANDIDATES = ("utf-8", "gb18030", "cp1252")
DELIMITER_CANDIDATES = ("\t", ";", ",", "whitespace")


@dataclass(frozen=True, slots=True)
class DelimitedImportOptions:
    time_column: int | str = 0
    signal_column: int | str = 1
    encoding: str | None = None
    delimiter: str | None = None
    has_header: bool | None = None
    decimal_mark: str | None = None


@dataclass(frozen=True, slots=True)
class NamedDelimitedImport:
    sample_name: str
    metadata: Mapping[str, str]
    result: ImportResult


def _column_index(
    value: int | str,
    *,
    header: list[str] | None,
    column_count: int,
) -> int:
    """Resolve an internal zero-based index or a user-facing column reference."""

    if isinstance(value, int):
        index = value
    else:
        reference = value.strip()
        if not reference:
            raise ValueError("column reference must not be blank")
        if header is not None:
            normalized = [item.strip().casefold() for item in header]
            if reference.casefold() in normalized:
                return normalized.index(reference.casefold())
        if reference.isdecimal():
            number = int(reference)
            index = 0 if number == 0 else number - 1
        elif reference.isalpha() and reference.isascii():
            index = 0
            for character in reference.upper():
                index = index * 26 + (ord(character) - ord("A") + 1)
            index -= 1
        else:
            available = "、".join(header) if header else "无表头"
            raise ValueError(f"找不到列 {reference!r}；可用列：{available}")
    if not 0 <= index < column_count:
        raise ValueError(f"column index {index} is outside the detected {column_count} columns")
    return index


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def decode_text(raw: bytes, forced_encoding: str | None = None) -> tuple[str, str]:
    if forced_encoding:
        return raw.decode(forced_encoding), forced_encoding
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16"), "utf-16-le-bom"
    if raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16-be-bom"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-bom"
    for encoding in ENCODING_CANDIDATES:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", raw, 0, min(1, len(raw)), "no supported encoding")


def _split(line: str, delimiter: str) -> list[str]:
    if delimiter == "whitespace":
        return line.strip().split()
    return next(csv.reader([line], delimiter=delimiter, skipinitialspace=True))


def _numeric(value: str, decimal_mark: str) -> float:
    cleaned = value.strip().replace("\u2212", "-")
    if decimal_mark == ",":
        cleaned = cleaned.replace(".", "").replace(",", ".")
    result = float(cleaned)
    if not math.isfinite(result):
        raise ValueError("non-finite numeric value")
    return result


def _delimiter_score(lines: list[str], delimiter: str) -> tuple[int, float, int]:
    counts = [len(_split(line, delimiter)) for line in lines[:50]]
    if not counts:
        return (0, 0.0, 0)
    mode, frequency = Counter(counts).most_common(1)[0]
    consistency = frequency / len(counts)
    return (1 if mode >= 2 else 0, consistency, mode)


def probe_delimited(raw: bytes, encoding: str | None = None) -> tuple[FormatProbe, str]:
    text, detected_encoding = decode_text(raw, encoding)
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("input contains no non-empty lines")

    delimiter = max(DELIMITER_CANDIDATES, key=lambda candidate: _delimiter_score(lines, candidate))
    has_columns, consistency, column_count = _delimiter_score(lines, delimiter)
    if not has_columns:
        raise ValueError("could not detect at least two stable columns")

    first = _split(lines[0], delimiter)
    second = _split(lines[1], delimiter) if len(lines) > 1 else []
    decimal_mark = "," if delimiter == ";" and any("," in value for value in second) else "."

    def first_two_numeric(columns: list[str]) -> bool:
        if len(columns) < 2:
            return False
        try:
            _numeric(columns[0], decimal_mark)
            _numeric(columns[1], decimal_mark)
        except ValueError:
            return False
        return True

    has_header = not first_two_numeric(first) and first_two_numeric(second)
    newline = "CRLF" if "\r\n" in text else "LF" if "\n" in text else "CR"
    confidence = min(1.0, 0.55 + 0.35 * consistency + (0.1 if column_count == 2 else 0.0))
    return (
        FormatProbe(
            encoding=detected_encoding,
            delimiter=delimiter,
            has_header=has_header,
            decimal_mark=decimal_mark,
            column_count=column_count,
            confidence=confidence,
            newline=newline,
        ),
        text,
    )


def import_delimited_bytes(
    raw: bytes,
    source_name: str,
    options: DelimitedImportOptions | None = None,
) -> ImportResult:
    selected = options or DelimitedImportOptions()
    detected, text = probe_delimited(raw, selected.encoding)
    delimiter = selected.delimiter or detected.delimiter
    first_columns = _split(
        next(line for line in text.splitlines() if line.strip()),
        delimiter,
    )
    normalized_first = {value.strip().casefold() for value in first_columns}
    named_mapping_matches_header = any(
        isinstance(reference, str) and reference.strip().casefold() in normalized_first
        for reference in (selected.time_column, selected.signal_column)
    )
    probe = FormatProbe(
        encoding=detected.encoding,
        delimiter=delimiter,
        has_header=(
            detected.has_header or named_mapping_matches_header
            if selected.has_header is None
            else selected.has_header
        ),
        decimal_mark=selected.decimal_mark or detected.decimal_mark,
        column_count=detected.column_count,
        confidence=detected.confidence,
        newline=detected.newline,
    )
    nonempty_lines = [line for line in text.splitlines() if line.strip()]
    header = _split(nonempty_lines[0], probe.delimiter) if probe.has_header else None
    time_column = _column_index(
        selected.time_column,
        header=header,
        column_count=probe.column_count,
    )
    signal_column = _column_index(
        selected.signal_column,
        header=header,
        column_count=probe.column_count,
    )
    if time_column == signal_column:
        raise ValueError("time and signal columns must be different")
    times: list[float] = []
    signals: list[float] = []
    issues: list[ParsedRowIssue] = []
    nonempty_index = 0
    required_column = max(time_column, signal_column)
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        if not raw_line.strip():
            continue
        nonempty_index += 1
        if probe.has_header and nonempty_index == 1:
            continue
        columns = _split(raw_line, probe.delimiter)
        if len(columns) <= required_column:
            issues.append(ParsedRowIssue(line_number, raw_line, "missing required column"))
            continue
        try:
            time_value = _numeric(columns[time_column], probe.decimal_mark)
            signal_value = _numeric(columns[signal_column], probe.decimal_mark)
        except ValueError as error:
            issues.append(ParsedRowIssue(line_number, raw_line, str(error)))
            continue
        times.append(time_value)
        signals.append(signal_value)
    if len(times) < 2:
        raise ValueError("fewer than two valid numeric rows")

    digest = _sha256(raw)
    raw_file = RawFileRecord(source_name, digest, len(raw), raw)
    chromatogram = RawChromatogram(tuple(times), tuple(signals), "detector_1", digest)
    return ImportResult(raw_file, chromatogram, probe, tuple(issues))


def import_delimited_file(
    path: Path, options: DelimitedImportOptions | None = None
) -> ImportResult:
    return import_delimited_bytes(path.read_bytes(), path.name, options)


def import_long_delimited_bytes(
    raw: bytes,
    source_name: str,
    *,
    sample_column: int | str,
    metadata_columns: Mapping[str, int | str] | None = None,
    options: DelimitedImportOptions | None = None,
) -> tuple[NamedDelimitedImport, ...]:
    """Split a long-table text file into named chromatograms without changing evidence bytes."""

    selected = options or DelimitedImportOptions()
    detected, text = probe_delimited(raw, selected.encoding)
    delimiter = selected.delimiter or detected.delimiter
    first_columns = _split(
        next(line for line in text.splitlines() if line.strip()),
        delimiter,
    )
    mapped_references = (
        sample_column,
        selected.time_column,
        selected.signal_column,
        *(metadata_columns or {}).values(),
    )
    normalized_first = {value.strip().casefold() for value in first_columns}
    named_mapping_matches_header = any(
        isinstance(reference, str) and reference.strip().casefold() in normalized_first
        for reference in mapped_references
    )
    probe = FormatProbe(
        encoding=detected.encoding,
        delimiter=delimiter,
        has_header=(
            detected.has_header or named_mapping_matches_header
            if selected.has_header is None
            else selected.has_header
        ),
        decimal_mark=selected.decimal_mark or detected.decimal_mark,
        column_count=detected.column_count,
        confidence=detected.confidence,
        newline=detected.newline,
    )
    nonempty_lines = [line for line in text.splitlines() if line.strip()]
    header = _split(nonempty_lines[0], probe.delimiter) if probe.has_header else None
    sample_index = _column_index(sample_column, header=header, column_count=probe.column_count)
    time_index = _column_index(
        selected.time_column,
        header=header,
        column_count=probe.column_count,
    )
    signal_index = _column_index(
        selected.signal_column,
        header=header,
        column_count=probe.column_count,
    )
    if len({sample_index, time_index, signal_index}) < 3:
        raise ValueError("sample, time, and signal columns must be different")
    metadata_indexes = {
        key: _column_index(reference, header=header, column_count=probe.column_count)
        for key, reference in (metadata_columns or {}).items()
    }
    grouped: dict[str, tuple[list[float], list[float], list[ParsedRowIssue], dict[str, str]]] = {}
    nonempty_index = 0
    required_index = max(sample_index, time_index, signal_index, *metadata_indexes.values())
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        if not raw_line.strip():
            continue
        nonempty_index += 1
        if probe.has_header and nonempty_index == 1:
            continue
        columns = _split(raw_line, probe.delimiter)
        if len(columns) <= required_index:
            continue
        sample_name = columns[sample_index].strip()
        if not sample_name:
            continue
        times, signals, issues, metadata = grouped.setdefault(
            sample_name,
            ([], [], [], {}),
        )
        try:
            time_value = _numeric(columns[time_index], probe.decimal_mark)
            signal_value = _numeric(columns[signal_index], probe.decimal_mark)
        except ValueError as error:
            issues.append(ParsedRowIssue(line_number, raw_line, str(error)))
            continue
        times.append(time_value)
        signals.append(signal_value)
        for key, index in metadata_indexes.items():
            value = columns[index].strip()
            if value and key not in metadata:
                metadata[key] = value
    digest = _sha256(raw)
    results: list[NamedDelimitedImport] = []
    for sample_name, (times, signals, issues, metadata) in grouped.items():
        if len(times) < 2:
            continue
        record = RawFileRecord(source_name, digest, len(raw), raw)
        chromatogram = RawChromatogram(tuple(times), tuple(signals), "detector_1", digest)
        results.append(
            NamedDelimitedImport(
                sample_name,
                metadata,
                ImportResult(record, chromatogram, probe, tuple(issues)),
            )
        )
    if not results:
        raise ValueError("long-table input contains no sample with at least two valid rows")
    return tuple(results)


def import_long_delimited_file(
    path: Path,
    *,
    sample_column: int | str,
    metadata_columns: Mapping[str, int | str] | None = None,
    options: DelimitedImportOptions | None = None,
) -> tuple[NamedDelimitedImport, ...]:
    return import_long_delimited_bytes(
        path.read_bytes(),
        path.name,
        sample_column=sample_column,
        metadata_columns=metadata_columns,
        options=options,
    )


def sampling_statistics(chromatogram: RawChromatogram) -> dict[str, float | int | bool]:
    times = chromatogram.time_min
    signals = chromatogram.signal
    intervals = [right - left for left, right in pairwise(times)]
    return {
        "points": len(times),
        "time_min": min(times),
        "time_max": max(times),
        "signal_min": min(signals),
        "signal_max": max(signals),
        "median_interval": statistics.median(intervals),
        "strictly_increasing": all(interval > 0 for interval in intervals),
        "duplicate_times": sum(interval == 0 for interval in intervals),
        "descending_steps": sum(interval < 0 for interval in intervals),
    }
