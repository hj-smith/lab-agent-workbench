"""Content-based source sheet and merged-header detection."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping


STANDARD_FIELDS = (
    "sample_raw",
    "test_label",
    "n_193",
    "k_193",
    "n_248",
    "k_248",
    "n_633",
    "k_633",
    "thickness",
    "mse",
)


@dataclass(slots=True)
class SheetDetection:
    sheet_name: str
    is_data_sheet: bool
    header_row: int | None
    field_mapping: dict[str, int]
    confidence: float
    reason: str
    max_row: int
    max_column: int
    source_file: str | None = None
    source_file_hash: str | None = None
    source_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_header(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    return re.sub(r"[\s\r\n\t_\-:：/\\()（）]+", "", text)


def _matches_alias(text: str, aliases: list[str]) -> bool:
    normalized = normalize_header(text)
    return any(normalize_header(alias) in normalized for alias in aliases)


def _map_header_row(
    worksheet: Any, row_number: int, config: Mapping[str, Any]
) -> dict[str, int]:
    mapping: dict[str, int] = {}
    aliases = config["headers"]["aliases"]
    for column in range(1, worksheet.max_column + 1):
        raw = worksheet.cell(row_number, column).value
        text = normalize_header(raw)
        if not text:
            continue
        if _matches_alias(text, aliases["sample"]):
            mapping["sample_raw"] = column
            continue
        if _matches_alias(text, aliases["test_count"]):
            mapping["test_label"] = column
            continue
        if _matches_alias(text, aliases["thickness"]):
            mapping["thickness"] = column
            continue
        if _matches_alias(text, aliases["mse"]):
            mapping["mse"] = column
            continue
        for wavelength in (193, 248, 633):
            if re.search(fr"{wavelength}.*(?:nk|n.?k)", text, flags=re.IGNORECASE):
                mapping[f"n_{wavelength}"] = column
                if column < worksheet.max_column:
                    mapping[f"k_{wavelength}"] = column + 1
                break
            if text in {f"n{wavelength}", f"{wavelength}n"}:
                mapping[f"n_{wavelength}"] = column
                break
            if text in {f"k{wavelength}", f"{wavelength}k"}:
                mapping[f"k_{wavelength}"] = column
                break
    return mapping


def detect_sheet(
    worksheet: Any,
    config: Mapping[str, Any],
    manual_mapping: Mapping[str, int] | None = None,
) -> SheetDetection:
    excluded_prefixes = tuple(config["headers"].get("excluded_sheet_prefixes", []))
    if worksheet.title.startswith(excluded_prefixes):
        return SheetDetection(
            worksheet.title,
            False,
            None,
            {},
            0.0,
            "标签页名称匹配结果页排除前缀",
            worksheet.max_row,
            worksheet.max_column,
        )
    if manual_mapping:
        return SheetDetection(
            worksheet.title,
            True,
            int(manual_mapping.get("header_row", 2)),
            {key: int(value) for key, value in manual_mapping.items() if key != "header_row"},
            1.0,
            "使用手动字段映射",
            worksheet.max_row,
            worksheet.max_column,
        )
    best_row: int | None = None
    best_mapping: dict[str, int] = {}
    scan_rows = min(worksheet.max_row, int(config["headers"].get("search_rows", 20)))
    for row_number in range(1, scan_rows + 1):
        mapping = _map_header_row(worksheet, row_number, config)
        if len(mapping) > len(best_mapping):
            best_mapping = mapping
            best_row = row_number
    essential = {"sample_raw", "test_label", "thickness"}
    optical_count = sum(key.startswith(("n_", "k_")) for key in best_mapping)
    is_data_sheet = essential.issubset(best_mapping) and optical_count >= 4
    confidence = len(best_mapping) / len(STANDARD_FIELDS)
    if not is_data_sheet:
        missing = sorted(essential - best_mapping.keys())
        reason = "未识别为原始数据页"
        if missing:
            reason += f"；缺少必要字段: {', '.join(missing)}"
        if optical_count < 4:
            reason += "；N/K 字段不足"
    else:
        reason = "根据表头与数据结构识别为原始椭偏仪数据页"
    return SheetDetection(
        worksheet.title,
        is_data_sheet,
        best_row,
        best_mapping,
        confidence,
        reason,
        worksheet.max_row,
        worksheet.max_column,
    )


def detect_workbook_sheets(
    workbook: Any,
    config: Mapping[str, Any],
    manual_mappings: Mapping[str, Mapping[str, int]] | None = None,
) -> list[SheetDetection]:
    manual_mappings = manual_mappings or {}
    return [
        detect_sheet(worksheet, config, manual_mappings.get(worksheet.title))
        for worksheet in workbook.worksheets
    ]
