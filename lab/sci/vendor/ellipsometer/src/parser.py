"""Traceable workbook-to-long-table parser."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd
from openpyxl.utils import get_column_letter

from .sample_name_parser import extract_default_model, parse_sample_name
from .sheet_detector import SheetDetection
from .stage_classifier import StageClassifier, parse_replicate_number
from .workbook_reader import WorkbookBundle


NUMERIC_FIELDS = (
    "n_193",
    "k_193",
    "n_248",
    "k_248",
    "n_633",
    "k_633",
    "thickness",
    "mse",
)

BASE_COLUMNS = (
    "source_file",
    "source_sheet",
    "source_row",
    "source_cell_range",
    "source_file_hash",
    "source_sheet_hash",
    "test_date",
    "measurement_block_id",
    "sample_instance_id",
    "sample_raw",
    "sample_id",
    "sample_description",
    "model",
    "process_condition",
    "solva_condition",
    "bake_temperature",
    "bake_time",
    "curing_condition",
    "solva_time",
    "solva_method",
    "treatment_stage",
    "treatment_sequence",
    "replicate_number",
    "test_label_raw",
    *NUMERIC_FIELDS,
    "is_dissolved",
    "dissolution_note",
    "is_source_average_row",
    "parse_status",
    "warning_message",
)


@dataclass(slots=True)
class ParseResult:
    measurements: pd.DataFrame
    anomalies: pd.DataFrame
    field_mappings: pd.DataFrame


def _numeric(value: Any) -> tuple[float | None, str | None]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if isinstance(value, bool):
        return None, f"布尔值不能作为数值: {value}"
    if isinstance(value, (int, float)):
        return float(value), None
    text = str(value).strip().replace(",", "").replace("−", "-")
    if re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", text):
        return float(text), None
    return None, f"非法数值: {value}"


def _parse_test_date(sheet_name: str) -> str | None:
    digits = [int(item) for item in re.findall(r"\d+", sheet_name)]
    year = month = day = None
    if len(digits) >= 3:
        year, month, day = digits[:3]
    elif len(digits) == 2 and len(str(digits[1])) in (3, 4):
        year = digits[0]
        suffix = str(digits[1])
        month, day = int(suffix[:-2]), int(suffix[-2:])
    if year and month and day:
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


def _stable_ids(
    source_file: str,
    source_file_hash: str,
    sheet_name: str,
    metadata: Mapping[str, Any],
    occurrence: int,
) -> tuple[str, str]:
    identity = {
        "source_file": source_file.lower(),
        "source_file_hash": source_file_hash,
        "source_sheet": sheet_name,
        "sample_id": metadata.get("sample_id"),
        "sample_raw": metadata.get("sample_raw"),
        "process_condition": metadata.get("process_condition"),
        "solva_condition": metadata.get("solva_condition"),
        "occurrence_within_sheet": occurrence,
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:20]
    return f"MB-{digest}", f"SI-{digest}"


def _is_nonempty_row(worksheet: Any, row_number: int) -> bool:
    return any(
        worksheet.cell(row_number, column).value not in (None, "")
        for column in range(1, worksheet.max_column + 1)
    )


def _record_range(mapping: Mapping[str, int], row: int) -> str:
    columns = list(mapping.values())
    return f"{get_column_letter(min(columns))}{row}:{get_column_letter(max(columns))}{row}"


def parse_workbook(
    bundle: WorkbookBundle,
    detections: Iterable[SheetDetection],
    config: Mapping[str, Any],
    selected_sheets: set[str] | None = None,
) -> ParseResult:
    records: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    dissolution_patterns = [
        re.compile(pattern, re.IGNORECASE)
        for pattern in config["stage_rules"].get("dissolution_markers", [])
    ]
    source_file = bundle.source_path.name
    for detection in detections:
        mapping_row = {
            "source_file": source_file,
            "source_file_hash": bundle.file_hash,
            "source_sheet": detection.sheet_name,
            "is_data_sheet": detection.is_data_sheet,
            "header_row": detection.header_row,
            "confidence": detection.confidence,
            "reason": detection.reason,
            **{
                f"column_{field}": column
                for field, column in detection.field_mapping.items()
            },
        }
        mapping_rows.append(mapping_row)
        if not detection.is_data_sheet:
            continue
        if selected_sheets is not None and detection.sheet_name not in selected_sheets:
            continue
        if detection.header_row is None:
            continue
        worksheet = bundle.formula_workbook[detection.sheet_name]
        value_worksheet = bundle.value_workbook[detection.sheet_name]
        mapping = detection.field_mapping
        active_numeric_fields = {
            field
            for field in NUMERIC_FIELDS
            if field in mapping
            and any(
                worksheet.cell(row_number, mapping[field]).value not in (None, "")
                for row_number in range(detection.header_row + 1, worksheet.max_row + 1)
            )
        }
        for field in NUMERIC_FIELDS:
            mapping_row[f"has_data_{field}"] = field in active_numeric_fields
        classifier = StageClassifier(config)
        default_model = extract_default_model(worksheet, detection.header_row, config)
        current_metadata: dict[str, Any] | None = None
        measurement_block_id: str | None = None
        sample_instance_id: str | None = None
        occurrences: dict[str, int] = {}
        for row_number in range(detection.header_row + 1, worksheet.max_row + 1):
            sample_cell = worksheet.cell(row_number, mapping["sample_raw"]).value
            if sample_cell not in (None, ""):
                classifier.reset()
                current_metadata = parse_sample_name(sample_cell, config, default_model)
                occurrence_key = json.dumps(current_metadata, ensure_ascii=False, sort_keys=True)
                occurrences[occurrence_key] = occurrences.get(occurrence_key, 0) + 1
                measurement_block_id, sample_instance_id = _stable_ids(
                    source_file,
                    bundle.file_hash,
                    detection.sheet_name,
                    current_metadata,
                    occurrences[occurrence_key],
                )
            test_label = worksheet.cell(row_number, mapping["test_label"]).value
            decision = classifier.classify(test_label)
            raw_numeric_values = {
                field: worksheet.cell(row_number, mapping[field]).value
                if field in mapping
                else None
                for field in NUMERIC_FIELDS
            }
            cached_numeric_values = {
                field: value_worksheet.cell(row_number, mapping[field]).value
                if field in mapping
                else None
                for field in NUMERIC_FIELDS
            }
            has_measurement_content = any(
                value not in (None, "") for value in raw_numeric_values.values()
            )
            if (
                not has_measurement_content
                and not decision.is_average
                and not decision.is_after_marker
            ):
                if _is_nonempty_row(worksheet, row_number):
                    anomalies.append(
                        {
                            "source_file": source_file,
                            "source_file_hash": bundle.file_hash,
                            "source_sheet": detection.sheet_name,
                            "source_row": row_number,
                            "source_cell_range": _record_range(mapping, row_number),
                            "raw_sample": sample_cell,
                            "raw_test_label": test_label,
                            "parse_status": "unrecognized_nonempty_row",
                            "warning_message": "非空行未识别为测量记录或平均值行",
                        }
                    )
                continue
            if current_metadata is None or measurement_block_id is None or sample_instance_id is None:
                anomalies.append(
                    {
                        "source_file": source_file,
                        "source_file_hash": bundle.file_hash,
                        "source_sheet": detection.sheet_name,
                        "source_row": row_number,
                        "source_cell_range": _record_range(mapping, row_number),
                        "raw_sample": sample_cell,
                        "raw_test_label": test_label,
                        "parse_status": "missing_sample_context",
                        "warning_message": "测量行之前没有可继承的样品名称",
                    }
                )
                continue
            dissolution_note = next(
                (value for value in raw_numeric_values.values()
                 if isinstance(value, str)
                 and not value.startswith("=")
                 and any(pattern.search(value) for pattern in dissolution_patterns)),
                None,
            ) if decision.stage.startswith("after_solva_") else None
            is_dissolved = dissolution_note is not None
            warnings: list[str] = []
            parsed_values: dict[str, float | None] = {}
            source_formulas: dict[str, Any] = {}
            for field in NUMERIC_FIELDS:
                raw_value = raw_numeric_values[field]
                source_formulas[f"source_formula_{field}"] = (
                    raw_value if isinstance(raw_value, str) and raw_value.startswith("=") else None
                )
                value_to_parse = (
                    cached_numeric_values[field] if decision.is_average else raw_value
                )
                parsed, warning = _numeric(value_to_parse)
                parsed_values[field] = parsed
                if (
                    not decision.is_average
                    and field in active_numeric_fields
                    and raw_value in (None, "")
                ):
                    warnings.append(f"{field}: 空值")
                if warning:
                    warnings.append(f"{field}: {warning}")
            if is_dissolved and parsed_values.get("thickness") is not None:
                # A note beside a measured thickness is a remark, not a dissolved film.
                is_dissolved = False
                dissolution_note = None
                warnings.append("含溶解类备注但膜厚有数值，未按溶解处理，请核对原表")
            if is_dissolved:
                parsed_values = {field: None for field in NUMERIC_FIELDS}
                parsed_values["thickness"] = 0.0
                warnings.clear()
            replicate = None if decision.is_average else parse_replicate_number(test_label)
            if not decision.is_average and not is_dissolved and replicate is None:
                warnings.append("测试次数未识别为重复编号")
            record = {
                "source_file": source_file,
                "source_sheet": detection.sheet_name,
                "source_row": row_number,
                "source_cell_range": _record_range(mapping, row_number),
                "source_file_hash": bundle.file_hash,
                "source_sheet_hash": bundle.sheet_hashes[detection.sheet_name],
                "test_date": _parse_test_date(detection.sheet_name),
                "measurement_block_id": measurement_block_id,
                "sample_instance_id": sample_instance_id,
                **current_metadata,
                "treatment_stage": decision.stage,
                "treatment_sequence": decision.sequence,
                "replicate_number": replicate,
                "test_label_raw": test_label,
                **parsed_values,
                **source_formulas,
                "is_dissolved": is_dissolved,
                "dissolution_note": dissolution_note,
                "is_source_average_row": decision.is_average and not is_dissolved,
                "parse_status": "warning" if warnings else "ok",
                "warning_message": "；".join(warnings),
            }
            records.append(record)
    measurements = pd.DataFrame(records)
    for column in BASE_COLUMNS:
        if column not in measurements.columns:
            measurements[column] = None
    if not measurements.empty:
        # One observation per dissolved stage, located at the first original note.
        # Keep source averages for provenance; merged cells are never forward-filled.
        drop_indexes = []
        for _, group in measurements.groupby(["sample_instance_id", "treatment_stage"], sort=False):
            dissolved = group[group["is_dissolved"]]
            measured = group[
                ~group["is_dissolved"].astype(bool)
                & ~group["is_source_average_row"].astype(bool)
                & group["thickness"].notna()
            ]
            if not dissolved.empty and not measured.empty:
                # Real thicknesses win over a dissolution note in the same stage.
                for index in dissolved.index:
                    measurements.at[index, "is_dissolved"] = False
                    measurements.at[index, "thickness"] = None
                    measurements.at[index, "parse_status"] = "warning"
                    measurements.at[index, "warning_message"] = (
                        "同一处理阶段既有膜厚数值又有溶解类备注，未按溶解处理，请核对原表"
                    )
            elif not dissolved.empty:
                keep_index = dissolved.index[0]
                drop_indexes.extend(group.index[
                    ~group["is_source_average_row"] & (group.index != keep_index)
                ])
        measurements = measurements.drop(index=drop_indexes)
        measurements = measurements.drop_duplicates(
            subset=["source_file_hash", "source_sheet", "source_row", "sample_instance_id"],
            keep="first",
        ).reset_index(drop=True)
    return ParseResult(
        measurements=measurements,
        anomalies=pd.DataFrame(anomalies),
        field_mappings=pd.DataFrame(mapping_rows),
    )
