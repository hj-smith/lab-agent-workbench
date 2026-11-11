"""Safe in-memory ZIP member discovery and legacy filename recovery."""

from __future__ import annotations

import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from gpcsec.io.importers.delimited import DelimitedImportOptions, import_delimited_bytes
from gpcsec.io.importers.excel import import_xlsx_bytes
from gpcsec.io.importers.netcdf import NetcdfImportOptions, import_netcdf_bytes
from gpcsec.models import ImportResult


@dataclass(frozen=True, slots=True)
class ZipNameCandidate:
    name: str
    encoding: str
    score: int


@dataclass(frozen=True, slots=True)
class ZipMemberImport:
    name: str
    name_encoding: str
    confidence: float
    candidates: tuple[ZipNameCandidate, ...]
    result: ImportResult
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ZipCalibrationPdfMember:
    """Evidence-preserving PDF member that may contain a calibration table."""

    name: str
    name_encoding: str
    confidence: float
    candidates: tuple[ZipNameCandidate, ...]
    original_bytes: bytes


def _score_name(value: str) -> int:
    replacement = value.count("\ufffd")
    controls = sum(ord(char) < 32 for char in value)
    cjk = sum("\u3400" <= char <= "\u9fff" for char in value)
    mojibake = sum(char in "╖┤╙ª╥║╘¡╩╝╩²╛▌" for char in value)
    return 20 * cjk - 50 * replacement - 20 * controls - 5 * mojibake


def decode_member_name(
    info: zipfile.ZipInfo,
) -> tuple[str, str, float, tuple[ZipNameCandidate, ...]]:
    if info.flag_bits & 0x800:
        candidate = ZipNameCandidate(info.filename, "utf-8", _score_name(info.filename))
        return info.filename, "utf-8", 1.0, (candidate,)
    raw_name = info.filename.encode("cp437")
    candidates: list[ZipNameCandidate] = []
    for encoding in ("gb18030", "gbk", "cp932", "cp1252"):
        try:
            value = raw_name.decode(encoding)
        except UnicodeDecodeError:
            continue
        candidates.append(ZipNameCandidate(value, encoding, _score_name(value)))
    if not candidates:
        fallback = ZipNameCandidate(info.filename, "cp437", _score_name(info.filename))
        return info.filename, "cp437", 0.2, (fallback,)
    candidates.sort(key=lambda item: item.score, reverse=True)
    best = candidates[0]
    runner_up = candidates[1].score if len(candidates) > 1 else best.score - 20
    confidence = min(1.0, max(0.3, 0.6 + (best.score - runner_up) / 100.0))
    return best.name, best.encoding, confidence, tuple(candidates)


def _validate_member_path(name: str) -> None:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError(f"unsafe ZIP member path: {name!r}")


def _decoded_file_members(
    raw: bytes,
) -> tuple[
    tuple[str, str, float, tuple[ZipNameCandidate, ...], bytes],
    ...,
]:
    from io import BytesIO

    members: list[tuple[str, str, float, tuple[ZipNameCandidate, ...], bytes]] = []
    seen: set[str] = set()
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(f"ZIP CRC failure: {bad_member}")
        for info in archive.infolist():
            name, encoding, confidence, candidates = decode_member_name(info)
            _validate_member_path(name)
            canonical = name.replace("\\", "/").casefold()
            if canonical in seen:
                raise ValueError(f"ZIP member collision after normalization: {name}")
            seen.add(canonical)
            if info.is_dir():
                continue
            members.append((name, encoding, confidence, candidates, archive.read(info)))
    return tuple(members)


def discover_zip_calibration_pdfs(raw: bytes) -> tuple[ZipCalibrationPdfMember, ...]:
    """Return every PDF member so the calibration importer can probe it explicitly."""

    return tuple(
        ZipCalibrationPdfMember(name, encoding, confidence, candidates, member_raw)
        for name, encoding, confidence, candidates, member_raw in _decoded_file_members(raw)
        if PurePosixPath(name).suffix.casefold() == ".pdf"
    )


def import_zip_bytes(
    raw: bytes,
    *,
    delimited_options: DelimitedImportOptions | None = None,
    netcdf_options: NetcdfImportOptions | None = None,
    xlsx_time_column: int | str = 1,
    xlsx_signal_column: int | str = 2,
) -> tuple[ZipMemberImport, ...]:
    results: list[ZipMemberImport] = []
    for name, encoding, confidence, candidates, member_raw in _decoded_file_members(raw):
        suffix = PurePosixPath(name).suffix.casefold()
        if suffix in {".csv", ".txt"}:
            imported = import_delimited_bytes(member_raw, name, delimited_options)
        elif suffix == ".xlsx":
            imported = import_xlsx_bytes(
                member_raw,
                name,
                time_column=xlsx_time_column,
                signal_column=xlsx_signal_column,
            )
        elif suffix in {".cdf", ".nc", ".netcdf"}:
            imported = import_netcdf_bytes(member_raw, name, netcdf_options)
        else:
            continue
        results.append(ZipMemberImport(name, encoding, confidence, candidates, imported))
    return tuple(results)
