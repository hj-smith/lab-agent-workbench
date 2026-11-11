"""End-to-end deterministic analysis pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from .analyzer import AnalysisTables, analyze_measurements
from .parser import BASE_COLUMNS, ParseResult, parse_workbook
from .quality_checker import QualityResult, run_quality_checks
from .sheet_detector import SheetDetection, detect_workbook_sheets
from .workbook_reader import (
    CombinedWorkbookBundle,
    WorkbookBundle,
    aggregate_file_hash,
    load_workbook_bundle,
    source_id,
)


Bundle = WorkbookBundle | CombinedWorkbookBundle


@dataclass(slots=True)
class PipelineResult:
    bundle: Bundle
    detections: list[SheetDetection]
    parsed: ParseResult
    quality: QualityResult
    analysis: AnalysisTables
    reference_comparison: pd.DataFrame
    reference_metric_details: pd.DataFrame
    reference_summary: list[str]
    reference_validation: dict[str, Any] | None
    selection_manifest: dict[str, Any]


def attach_reference_comparison(
    result: PipelineResult,
    reference_sample_instance_id: str | None,
    config: Mapping[str, Any],
) -> PipelineResult:
    """Recalculate horizontal comparison without rereading the workbook."""
    result.reference_comparison = pd.DataFrame()
    result.reference_metric_details = pd.DataFrame()
    result.reference_summary = []
    result.reference_validation = None
    if not reference_sample_instance_id:
        return result
    from .comparison_summary import generate_comparison_summary
    from .reference_comparator import compare_to_reference
    from .reference_selector import validate_reference

    result.reference_validation = validate_reference(
        reference_sample_instance_id,
        result.quality.measurements,
        result.quality.issues,
        result.analysis.thickness_comparison,
        str(config["reference_comparison"].get("after_stage", "after_solva_1")),
    )
    comparison_view = result.analysis.thickness_comparison.copy()
    if str(
        config.get("quality", {})
        .get("outlier_handling", {})
        .get("statistics_population", "all_measurements")
    ) == "exclude_thickness_outliers":
        active_to_standard = {
            "active_before_thickness_mean": "before_thickness_mean",
            "active_after_thickness_mean": "after_thickness_mean",
            "active_before_thickness_std": "before_thickness_std",
            "active_after_thickness_std": "after_thickness_std",
            "active_before_thickness_cv": "before_thickness_cv",
            "active_after_thickness_cv": "after_thickness_cv",
            "active_before_thickness_median": "before_thickness_median",
            "active_after_thickness_median": "after_thickness_median",
            "active_before_thickness_min": "before_thickness_min",
            "active_after_thickness_min": "after_thickness_min",
            "active_before_thickness_max": "before_thickness_max",
            "active_after_thickness_max": "after_thickness_max",
            "active_before_thickness_range": "before_thickness_range",
            "active_after_thickness_range": "after_thickness_range",
            "active_before_thickness_count": "before_measurement_count",
            "active_after_thickness_count": "after_measurement_count",
            "active_thickness_loss": "thickness_loss",
            "active_delta_thickness": "delta_thickness",
            "active_loss_percent": "loss_percent",
            "active_retention_percent": "retention_percent",
            "active_change_percent": "change_percent",
        }
        for active_column, standard_column in active_to_standard.items():
            if active_column in comparison_view:
                comparison_view[standard_column] = comparison_view[active_column]
        comparison_view["thickness_cv_change"] = (
            comparison_view["after_thickness_cv"] - comparison_view["before_thickness_cv"]
        )
    reference_result = compare_to_reference(
        reference_sample_instance_id,
        comparison_view,
        result.analysis.optical_changes,
        result.analysis.sample_statistics,
        result.quality.issues,
        config,
    )
    result.reference_comparison = reference_result.wide
    result.reference_metric_details = reference_result.long
    result.reference_summary = generate_comparison_summary(
        reference_result.wide, reference_result.long, config
    )
    return result


def _manual_mappings_for_bundle(
    bundle: WorkbookBundle,
    configured: Mapping[str, Mapping[str, int]],
) -> dict[str, Mapping[str, int]]:
    """Resolve legacy sheet-only and collision-safe file+sheet mappings."""
    resolved: dict[str, Mapping[str, int]] = {}
    sid = source_id(bundle.file_hash)
    for sheet_name in bundle.formula_workbook.sheetnames:
        for key in (
            f"{bundle.file_hash}::{sheet_name}",
            f"{sid}::{sheet_name}",
            f"{bundle.source_path.name}::{sheet_name}",
            sheet_name,
        ):
            if key in configured:
                resolved[sheet_name] = configured[key]
                break
    return resolved


def _selected_sheets_for_bundle(
    bundle: WorkbookBundle,
    selected_sources: Mapping[str, set[str] | None] | None,
) -> tuple[bool, set[str] | None]:
    if selected_sources is None:
        return True, None
    for key in (
        bundle.file_hash,
        source_id(bundle.file_hash),
        str(bundle.source_path),
        bundle.source_path.name,
    ):
        if key in selected_sources:
            sheets = selected_sources[key]
            return True, None if sheets is None else set(sheets)
    return False, set()


def _tag_detections(
    detections: list[SheetDetection], bundle: WorkbookBundle
) -> None:
    for detection in detections:
        detection.source_file = bundle.source_path.name
        detection.source_file_hash = bundle.file_hash
        detection.source_path = str(bundle.source_path)


def _combine_frames(frames: list[pd.DataFrame], columns: Sequence[str] = ()) -> pd.DataFrame:
    nonempty = [frame for frame in frames if frame is not None and not frame.empty]
    if not nonempty:
        return pd.DataFrame(columns=list(columns))
    ordered_columns = list(
        dict.fromkeys(
            [*columns, *(column for frame in nonempty for column in frame.columns)]
        )
    )
    pruned = [frame.dropna(axis=1, how="all") for frame in nonempty]
    combined = pd.concat(pruned, ignore_index=True, sort=False)
    for column in ordered_columns:
        if column not in combined:
            combined[column] = None
    return combined.reindex(columns=ordered_columns)


def _finalize(
    bundle: Bundle,
    detections: list[SheetDetection],
    parsed: ParseResult,
    config: Mapping[str, Any],
    reference_sample_instance_id: str | None,
    selection_manifest: dict[str, Any],
) -> PipelineResult:
    quality = run_quality_checks(parsed.measurements, config)
    analysis = analyze_measurements(quality.measurements, quality.issues, config)
    result = PipelineResult(
        bundle=bundle,
        detections=detections,
        parsed=parsed,
        quality=quality,
        analysis=analysis,
        reference_comparison=pd.DataFrame(),
        reference_metric_details=pd.DataFrame(),
        reference_summary=[],
        reference_validation=None,
        selection_manifest=selection_manifest,
    )
    return attach_reference_comparison(result, reference_sample_instance_id, config)


def run_multi_pipeline(
    workbook_paths: Sequence[str | Path],
    config: Mapping[str, Any],
    *,
    selected_sources: Mapping[str, set[str] | None] | None = None,
    selected_sample_instance_ids: set[str] | None = None,
    manual_mappings: Mapping[str, Mapping[str, int]] | None = None,
    reference_sample_instance_id: str | None = None,
) -> PipelineResult:
    """Analyze one or more workbooks as one traceable dataset.

    ``selected_sources`` is keyed by a full file hash (preferred), ``WB-`` source
    id, path, or filename. Values are selected sheet names; ``None`` means all
    valid sheets in that workbook. Identical file hashes are analyzed once.
    """
    paths = [Path(path).expanduser().resolve() for path in workbook_paths]
    if not paths:
        raise ValueError("至少需要选择一个 .xlsx 工作簿")
    configured_manual = {
        **config.get("manual_mappings", {}),
        **(manual_mappings or {}),
    }
    detections_all: list[SheetDetection] = []
    measurement_frames: list[pd.DataFrame] = []
    anomaly_frames: list[pd.DataFrame] = []
    mapping_frames: list[pd.DataFrame] = []
    source_manifest: list[dict[str, Any]] = []
    combined_sheet_hashes: dict[str, str] = {}
    analyzed_paths: list[Path] = []
    analyzed_hashes: list[str] = []
    seen_hashes: dict[str, Path] = {}
    selected_sheet_tokens: list[str] = []

    for path in paths:
        bundle = load_workbook_bundle(path)
        sid = source_id(bundle.file_hash)
        include_source, selected_sheets = _selected_sheets_for_bundle(
            bundle, selected_sources
        )
        manifest_row: dict[str, Any] = {
            "source_id": sid,
            "source_file": bundle.source_path.name,
            "source_path": str(bundle.source_path),
            "source_file_hash": bundle.file_hash,
            "sheet_count": len(bundle.sheet_hashes),
            "selected_sheet_count": 0,
            "selected_sheets": "",
            "status": "pending",
            "status_message": "",
        }
        if bundle.file_hash in seen_hashes:
            manifest_row["status"] = "duplicate_skipped"
            manifest_row["status_message"] = (
                f"与 {seen_hashes[bundle.file_hash].name} 内容完全相同，已避免重复导入"
            )
            source_manifest.append(manifest_row)
            bundle.formula_workbook.close()
            bundle.value_workbook.close()
            continue
        seen_hashes[bundle.file_hash] = bundle.source_path
        if not include_source:
            manifest_row["status"] = "not_selected"
            manifest_row["status_message"] = "本次分析未选择该文件"
            source_manifest.append(manifest_row)
            bundle.formula_workbook.close()
            bundle.value_workbook.close()
            continue

        manual_for_file = _manual_mappings_for_bundle(bundle, configured_manual)
        detections = detect_workbook_sheets(
            bundle.formula_workbook, config, manual_for_file
        )
        _tag_detections(detections, bundle)
        detections_all.extend(detections)
        parsed = parse_workbook(bundle, detections, config, selected_sheets)
        measurement_frames.append(parsed.measurements)
        anomaly_frames.append(parsed.anomalies)
        mapping_frames.append(parsed.field_mappings)
        selected_names = [
            item.sheet_name
            for item in detections
            if item.is_data_sheet
            and (selected_sheets is None or item.sheet_name in selected_sheets)
        ]
        manifest_row["selected_sheet_count"] = len(selected_names)
        manifest_row["selected_sheets"] = "；".join(selected_names)
        manifest_row["status"] = "analyzed"
        manifest_row["status_message"] = "已分析"
        source_manifest.append(manifest_row)
        analyzed_paths.append(bundle.source_path)
        analyzed_hashes.append(bundle.file_hash)
        for sheet_name in selected_names:
            token = f"{sid}::{sheet_name}"
            selected_sheet_tokens.append(token)
            combined_sheet_hashes[token] = bundle.sheet_hashes[sheet_name]
        bundle.formula_workbook.close()
        bundle.value_workbook.close()

    measurements = _combine_frames(measurement_frames, BASE_COLUMNS)
    if selected_sample_instance_ids is not None:
        selected_ids = set(map(str, selected_sample_instance_ids))
        measurements = measurements[
            measurements.get(
                "sample_instance_id", pd.Series(index=measurements.index, dtype=str)
            )
            .astype(str)
            .isin(selected_ids)
        ].reset_index(drop=True)
    parsed = ParseResult(
        measurements=measurements,
        anomalies=_combine_frames(anomaly_frames),
        field_mappings=_combine_frames(mapping_frames),
    )
    if not analyzed_paths:
        raise ValueError("当前数据范围未包含任何可分析的工作簿")
    aggregate_hash = (
        analyzed_hashes[0]
        if len(analyzed_hashes) == 1
        else aggregate_file_hash(analyzed_hashes)
    )
    synthetic_name = (
        analyzed_paths[0].name
        if len(analyzed_paths) == 1
        else f"多文件联合分析_{len(analyzed_paths)}个工作簿.xlsx"
    )
    combined_bundle = CombinedWorkbookBundle(
        source_path=Path(synthetic_name),
        source_paths=tuple(analyzed_paths),
        file_hash=aggregate_hash,
        sheet_hashes=combined_sheet_hashes,
        source_manifest=source_manifest,
    )
    selection_manifest = {
        "imported_source_count": len(paths),
        "analyzed_source_count": len(analyzed_paths),
        "duplicate_source_count": sum(
            row["status"] == "duplicate_skipped" for row in source_manifest
        ),
        "selected_sheet_count": len(selected_sheet_tokens),
        "selected_sheet_tokens": selected_sheet_tokens,
        "selected_sample_mode": (
            "all" if selected_sample_instance_ids is None else "selected"
        ),
        "selected_sample_count": int(
            measurements.get(
                "sample_instance_id", pd.Series(index=measurements.index, dtype=str)
            ).nunique()
        ),
        "selected_sample_instance_ids": (
            None
            if selected_sample_instance_ids is None
            else sorted(map(str, selected_sample_instance_ids))
        ),
    }
    return _finalize(
        combined_bundle,
        detections_all,
        parsed,
        config,
        reference_sample_instance_id,
        selection_manifest,
    )


def run_pipeline(
    workbook_path: str | Path,
    config: Mapping[str, Any],
    selected_sheets: set[str] | None = None,
    manual_mappings: Mapping[str, Mapping[str, int]] | None = None,
    reference_sample_instance_id: str | None = None,
) -> PipelineResult:
    """Backward-compatible single-workbook entry point."""
    selected_sources = None
    if selected_sheets is not None:
        selected_sources = {
            str(Path(workbook_path).expanduser().resolve()): selected_sheets
        }
    return run_multi_pipeline(
        [workbook_path],
        config,
        selected_sources=selected_sources,
        manual_mappings=manual_mappings,
        reference_sample_instance_id=reference_sample_instance_id,
    )
