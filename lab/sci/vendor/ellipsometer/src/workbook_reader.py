"""Workbook loading and reproducibility hashes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.workbook.workbook import Workbook


@dataclass(slots=True)
class WorkbookBundle:
    source_path: Path
    formula_workbook: Workbook
    value_workbook: Workbook
    file_hash: str
    sheet_hashes: dict[str, str]

    @property
    def source_paths(self) -> tuple[Path, ...]:
        return (self.source_path,)

    @property
    def source_manifest(self) -> list[dict[str, Any]]:
        return [
            {
                "source_id": source_id(self.file_hash),
                "source_file": self.source_path.name,
                "source_path": str(self.source_path),
                "source_file_hash": self.file_hash,
                "sheet_count": len(self.sheet_hashes),
                "status": "analyzed",
                "status_message": "已分析",
            }
        ]


@dataclass(slots=True)
class CombinedWorkbookBundle:
    """Lightweight provenance bundle for a multi-workbook analysis."""

    source_path: Path
    source_paths: tuple[Path, ...]
    file_hash: str
    sheet_hashes: dict[str, str]
    source_manifest: list[dict[str, Any]]


def source_id(file_hash: str) -> str:
    return f"WB-{file_hash[:16]}"


def aggregate_file_hash(file_hashes: list[str]) -> str:
    payload = json.dumps(sorted(file_hashes), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def hash_worksheet(worksheet: Any) -> str:
    rows = [
        [_canonical_value(cell.value) for cell in row]
        for row in worksheet.iter_rows()
    ]
    payload = {
        "name": worksheet.title,
        "max_row": worksheet.max_row,
        "max_column": worksheet.max_column,
        "merged": sorted(str(item) for item in worksheet.merged_cells.ranges),
        "rows": rows,
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_workbook_bundle(path: str | Path) -> WorkbookBundle:
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() != ".xlsx":
        raise ValueError("仅支持 .xlsx 工作簿")
    formula_workbook = load_workbook(source, data_only=False, read_only=False)
    value_workbook = load_workbook(source, data_only=True, read_only=False)
    sheet_hashes = {
        sheet.title: hash_worksheet(sheet) for sheet in formula_workbook.worksheets
    }
    return WorkbookBundle(
        source_path=source,
        formula_workbook=formula_workbook,
        value_workbook=value_workbook,
        file_hash=hash_file(source),
        sheet_hashes=sheet_hashes,
    )
