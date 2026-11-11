"""Reference-sample identity, filtering and validation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from typing import Mapping

import pandas as pd
from .reference_scope import MODE_LABELS, project_metrics


def build_reference_catalog(
    thickness_comparison: pd.DataFrame,
    *,
    source_file: str | None = None,
    source_file_hash: str | None = None,
    source_sheet: str | None = None,
    test_date: str | None = None,
    search: str | None = None,
    process_condition: str | None = None,
    solva_condition: str | None = None,
    model: str | None = None,
    complete_only: bool = False,
) -> pd.DataFrame:
    if thickness_comparison.empty:
        expected_columns = [
            "sample_instance_id",
            "sample_id",
            "sample_raw",
            "source_file",
            "source_file_hash",
            "source_sheet",
            "test_date",
            "process_condition",
            "solva_condition",
            "model",
            "data_completeness_status",
            "reference_display_label",
        ]
        return pd.DataFrame(columns=expected_columns)
    catalog = thickness_comparison.drop_duplicates("sample_instance_id").copy()
    if source_file:
        catalog = catalog[catalog["source_file"] == source_file]
    if source_file_hash:
        catalog = catalog[catalog["source_file_hash"] == source_file_hash]
    if source_sheet:
        catalog = catalog[catalog["source_sheet"] == source_sheet]
    if test_date:
        catalog = catalog[catalog["test_date"] == test_date]
    if process_condition:
        catalog = catalog[
            catalog["process_condition"].fillna("").str.contains(
                process_condition, case=False, regex=False
            )
        ]
    if solva_condition:
        catalog = catalog[
            catalog["solva_condition"].fillna("").str.contains(
                solva_condition, case=False, regex=False
            )
        ]
    if model:
        catalog = catalog[catalog["model"] == model]
    if complete_only:
        catalog = catalog[catalog["data_completeness_status"] == "完整"]
    if search:
        haystack = (
            catalog["sample_raw"].fillna("")
            + " "
            + catalog["sample_id"].fillna("")
            + " "
            + catalog["process_condition"].fillna("")
        )
        catalog = catalog[haystack.str.contains(search, case=False, regex=False)]
    catalog["reference_display_label"] = catalog.apply(_display_label, axis=1)
    return catalog.reset_index(drop=True)


def _display_label(row: pd.Series) -> str:
    def present(value: Any) -> bool:
        return value is not None and pd.notna(value) and str(value).strip() != ""

    primary = row.get("sample_raw")
    if not present(primary):
        primary = row.get("sample_id")
    parts = [str(primary) if present(primary) else "未知样品"]
    if present(row.get("source_file")):
        parts.append(str(row["source_file"]))
    if present(row.get("source_sheet")):
        parts.append(str(row["source_sheet"]))
    if present(row.get("process_condition")):
        parts.append(str(row["process_condition"]))
    if present(row.get("solva_condition")):
        parts.append(str(row["solva_condition"]))
    if present(row.get("sample_instance_id")):
        parts.append(f"ID:{row['sample_instance_id']}")
    return " | ".join(parts)


def apply_reference_view(
    wide: pd.DataFrame,
    long: pd.DataFrame,
    reference_row: pd.Series,
    *,
    candidate_filters: Iterable[str] = (),
    mode: str = "完整对比",
    metric_groups: Iterable[str] | None = None,
    sort_by: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply one reproducible UI/export view to both reference result tables."""
    if wide.empty:
        return wide.copy(), long.copy()
    filtered_wide = wide.copy()
    filters = set(candidate_filters)
    if "仅SolvA条件一致" in filters:
        filtered_wide = filtered_wide[
            filtered_wide["solva_condition"].fillna("")
            == ("" if pd.isna(reference_row.get("solva_condition")) else reference_row.get("solva_condition"))
        ]
    if "仅烘烤条件一致" in filters:
        filtered_wide = filtered_wide[
            filtered_wide["process_condition"].fillna("")
            == ("" if pd.isna(reference_row.get("process_condition")) else reference_row.get("process_condition"))
        ]
    if "仅同一标签页" in filters:
        filtered_wide = filtered_wide[
            (filtered_wide["sample_source_file_hash"] == reference_row.get("source_file_hash"))
            & (filtered_wide["sample_source_sheet"] == reference_row.get("source_sheet"))
        ]
    if "仅同一文件" in filters:
        filtered_wide = filtered_wide[
            filtered_wide["sample_source_file_hash"] == reference_row.get("source_file_hash")
        ]
    if "仅同一模型" in filters:
        filtered_wide = filtered_wide[
            filtered_wide["model"].fillna("")
            == ("" if pd.isna(reference_row.get("model")) else reference_row.get("model"))
        ]

    candidate_ids = set(filtered_wide.get("sample_instance_id", pd.Series(dtype=str)))
    filtered_long = long[
        long.get("sample_instance_id", pd.Series(index=long.index, dtype=str)).isin(candidate_ids)
    ].copy()
    mode = MODE_LABELS.get(mode, mode)
    stage_for_mode = {
        "处理前比较": "before_solva",
        "处理后比较": "after_solva",
        "SolvA耐受性比较": "before_to_after",
    }.get(mode)
    if stage_for_mode is not None and "treatment_stage" in filtered_long:
        filtered_long = filtered_long[filtered_long["treatment_stage"].astype(str).str.startswith("after_solva_")
                                      if stage_for_mode == "after_solva" else filtered_long["treatment_stage"] == stage_for_mode]
    if metric_groups is not None and "metric_group" in filtered_long:
        filtered_long = filtered_long[
            filtered_long["metric_group"].isin(list(metric_groups))
        ]
    filtered_wide = project_metrics(filtered_wide, filtered_long, config)
    if config is not None:
        percentage_options = config["reference_comparison"].get("percentage_comparison", {})
        if not percentage_options.get("output_percentage_point_difference", True):
            filtered_long["percentage_point_difference"] = None
            for column in [c for c in filtered_wide if "_pp_" in c]:
                filtered_wide[column] = None
        if not percentage_options.get("output_relative_difference", True):
            mask = filtered_long["metric_name"].isin(["change_percent", "loss_percent", "retention_percent"]) | filtered_long["metric_name"].str.endswith("_relative_change_percent")
            filtered_long.loc[mask, "relative_difference_percent"] = None
            for column in ("loss_percent_relative_difference", "retention_percent_relative_difference"):
                filtered_wide[column] = None
    if sort_by and sort_by in filtered_wide and filtered_wide[sort_by].notna().any():
        filtered_wide = filtered_wide.sort_values(sort_by)
    return filtered_wide.reset_index(drop=True), filtered_long.reset_index(drop=True)


def prepare_reference_export(result, config):
    """Use the saved scope for both exporters, including CLI configuration replay."""
    from copy import copy
    from .comparison_summary import generate_comparison_summary
    if result.reference_comparison.empty:
        return result
    selection = config["reference_comparison"].get("active_selection", {}) or {}
    reference_id = result.reference_comparison.iloc[0]["reference_sample_instance_id"]
    reference_row = result.analysis.thickness_comparison[
        result.analysis.thickness_comparison["sample_instance_id"] == reference_id
    ].iloc[0]
    exported = copy(result)
    exported.reference_comparison, exported.reference_metric_details = apply_reference_view(
        result.reference_comparison, result.reference_metric_details, reference_row,
        candidate_filters=selection.get("candidate_filters", []),
        mode=selection.get("comparison_mode", config["reference_comparison"].get("default_mode", "full")),
        metric_groups=selection.get("metric_groups"), sort_by=selection.get("sort_by"), config=config,
    )
    exported.reference_summary = generate_comparison_summary(exported.reference_comparison, exported.reference_metric_details, config)
    return exported


def validate_reference(
    sample_instance_id: str,
    measurements: pd.DataFrame,
    quality_issues: pd.DataFrame,
    thickness_comparison: pd.DataFrame,
    selected_stage: str = "after_solva_1",
) -> dict[str, Any]:
    rows = measurements[
        (measurements["sample_instance_id"] == sample_instance_id)
        & (~measurements["is_source_average_row"].fillna(False))
    ]
    if rows.empty:
        raise ValueError(f"参考样 sample_instance_id 不存在: {sample_instance_id}")
    before = rows[rows["treatment_stage"] == "before_solva"]
    after = rows[rows["treatment_stage"] == selected_stage]
    optical_fields = ["n_193", "k_193", "n_248", "k_248", "n_633", "k_633"]
    applicable = [before] + ([after] if not after.empty else [])
    optical_complete = all(group[field].notna().any() for group in applicable for field in optical_fields)
    issues = (
        quality_issues[quality_issues["sample_instance_id"] == sample_instance_id]
        if not quality_issues.empty
        else pd.DataFrame()
    )
    available: list[str] = []
    unavailable: dict[str, str] = {}
    if before["thickness"].notna().any():
        available.append("处理前膜厚")
    else:
        unavailable["处理前膜厚"] = "缺少有效处理前膜厚"
    if after["thickness"].notna().any():
        available.append("处理后膜厚")
    else:
        unavailable["处理后膜厚"] = "缺少有效处理后膜厚"
    if not before.empty and not after.empty:
        available.append("SolvA 前后变化")
    else:
        unavailable["SolvA 前后变化"] = "处理阶段不完整"
    if optical_complete:
        available.append("完整 N/K")
    else:
        unavailable["完整 N/K"] = "至少一个波长的 N 或 K 缺失"
    representative = rows.iloc[0]
    return {
        "sample_instance_id": sample_instance_id,
        "sample_name": representative["sample_raw"],
        "sample_id": representative["sample_id"],
        "source_file": representative["source_file"],
        "source_file_hash": representative.get("source_file_hash"),
        "source_sheet": representative["source_sheet"],
        "test_date": representative.get("test_date"),
        "process_condition": representative.get("process_condition"),
        "solva_condition": representative.get("solva_condition"),
        "model": representative.get("model"),
        "has_before": not before.empty,
        "has_after": not after.empty,
        "selected_after_stage": selected_stage,
        "before_replicates": int(len(before)),
        "after_replicates": int(len(after)),
        "optical_complete": optical_complete,
        "has_valid_thickness": bool(rows["thickness"].notna().any()),
        "sufficient_replicates": len(before) >= 2 and (after.empty or len(after) >= 2),
        "parse_warning_count": int(
            issues["issue_type"].eq("parse_warning").sum()
        )
        if not issues.empty
        else 0,
        "outlier_count": int(
            issues["issue_type"].eq("thickness_outlier").sum()
        )
        if not issues.empty
        else 0,
        "average_mismatch_count": int(
            issues["issue_type"].eq("average_mismatch").sum()
        )
        if not issues.empty
        else 0,
        "available_metrics": available,
        "unavailable_metrics": unavailable,
        "warning_messages": list(
            dict.fromkeys(issues.get("warning_message", pd.Series(dtype=str)).dropna().astype(str))
        ),
    }
