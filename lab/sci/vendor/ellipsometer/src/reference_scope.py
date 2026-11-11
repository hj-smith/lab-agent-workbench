"""One treatment selection and one metric scope for tables, charts and export."""
from __future__ import annotations

import re
from typing import Any, Mapping
import pandas as pd

OPTICAL_FIELDS = tuple(f"{kind}_{wave}" for wave in (193, 248, 633) for kind in ("n", "k"))
MODE_LABELS = {"full": "完整对比", "before": "处理前比较", "after": "处理后比较", "resistance": "SolvA耐受性比较"}


def select_stage_rows(frame: pd.DataFrame, selected_stage: str, *, optical: bool = False) -> pd.DataFrame:
    """Select the same after-stage for each instance; never inherit another stage."""
    if frame.empty or "after_stage" not in frame:
        return frame.copy()
    keys = ["sample_instance_id"] + (["parameter"] if optical else [])
    output = []
    for _, group in frame.groupby(keys, sort=False, dropna=False):
        matching = group[group["after_stage"] == selected_stage]
        if len(matching) > 1:
            raise ValueError(f"同一样品/参数的 {selected_stage} 结果重复")
        if not matching.empty:
            output.append(matching.iloc[0].to_dict())
            continue
        row = group.iloc[0].to_dict()
        missing = any(pd.notna(value) for value in group["after_stage"])
        for key in row:
            if key.startswith(("after_", "active_after_")) or key in {
                "delta_thickness", "thickness_loss", "change_percent", "loss_percent", "retention_percent",
                "absolute_change", "relative_change_percent", "thickness_cv_change", "mse_change",
            }:
                row[key] = None
        row["after_stage"] = selected_stage
        if not optical:
            row["after_dissolved"] = False
            row["after_measurement_count"] = 0
            row["after_source_rows"] = ""
            if missing or row.get("data_completeness_status") != "常规单阶段":
                row["data_completeness_status"] = "缺少处理后数据"
                row["warning_message"] = f"缺少所选处理阶段 {selected_stage}"
        output.append(row)
    return pd.DataFrame(output, columns=frame.columns)


def metric_column_bindings() -> dict[str, tuple[str, str]]:
    bindings = {}
    aliases = {
        "before_thickness": "before_thickness_mean", "after_thickness": "after_thickness_mean",
        "thickness_loss": "thickness_loss", "loss_percent": "loss_percent",
        "retention_percent": "retention_percent", "mse_before": "before_mse_mean",
        "mse_after": "after_mse_mean", "thickness_cv_before": "before_thickness_cv",
        "thickness_cv_after": "after_thickness_cv",
    }
    for stem, metric in aliases.items():
        for suffix, detail in {"sample": "sample_value", "reference": "reference_value",
                               "difference": "absolute_difference", "relative_difference": "relative_difference_percent",
                               "pp_difference": "percentage_point_difference"}.items():
            bindings[f"{stem}_{suffix}"] = (metric, detail)
    for parameter in OPTICAL_FIELDS:
        for stage in ("before", "after"):
            stem = f"{parameter}_{stage}"
            for suffix, detail in {"sample": "sample_value", "reference": "reference_value", "difference": "absolute_difference",
                                   "mean_difference": "absolute_difference", "relative_difference_percent": "relative_difference_percent",
                                   "ratio": "ratio_to_reference"}.items():
                bindings[f"{stem}_{suffix}"] = (f"{stem}_mean", detail)
    return bindings


def project_metrics(wide: pd.DataFrame, details: pd.DataFrame, config: Mapping[str, Any] | None = None) -> pd.DataFrame:
    """Derive every plotted scalar from the selected metric details, preserving identity."""
    result = wide.copy()
    if result.empty:
        return result
    projected = {}
    for column, (metric, value) in metric_column_bindings().items():
        subset = details[details["metric_name"] == metric] if not details.empty else pd.DataFrame()
        if not subset.empty:
            lookup = subset.set_index("sample_instance_id")[value]
            projected[column] = result["sample_instance_id"].map(lookup)
        else:
            projected[column] = pd.Series(float("nan"), index=result.index)
    result = pd.concat([result.drop(columns=list(projected), errors="ignore"), pd.DataFrame(projected, index=result.index)], axis=1).copy()
    applicable = details[details.get("is_applicable", pd.Series(True, index=details.index)).fillna(False)].copy()
    optical = applicable[applicable["metric_name"].str.fullmatch(r"[nk]_\d+_(before|after)_mean", na=False)] if not applicable.empty else applicable
    required = set(optical.loc[optical["reference_value"].notna(), "metric_name"]) if not optical.empty else set()
    drift = applicable[applicable["metric_name"].str.fullmatch(r"[nk]_\d+_drift_magnitude", na=False)] if not applicable.empty else applicable
    required_drift = set(drift.loc[drift["reference_value"].notna(), "metric_name"]) if not drift.empty else set()
    for index, row in result.iterrows():
        group = optical[optical["sample_instance_id"] == row["sample_instance_id"]] if not optical.empty else optical
        valid = group[group["absolute_difference"].notna()] if not group.empty else group
        complete = bool(required) and required.issubset(set(valid["metric_name"]))
        total = float(valid["absolute_difference"].abs().sum()) if not valid.empty else None
        result.loc[index, "optical_comparable_count"] = len(valid)
        result.loc[index, "optical_expected_count"] = len(required)
        result.loc[index, "optical_coverage_status"] = "同一参考指标集合完整" if complete else "比较项不足，不参与总偏差排名"
        result.loc[index, "optical_partial_deviation"] = total
        result.loc[index, "optical_constant_total_deviation"] = total if complete else None
        result.loc[index, "optical_comparison_basis"] = ",".join(sorted(required))
        own_drift = drift[drift["sample_instance_id"] == row["sample_instance_id"]] if not drift.empty else drift
        valid_drift = own_drift.dropna(subset=["sample_value", "reference_value"]) if not own_drift.empty else own_drift
        drift_complete = bool(required_drift) and required_drift.issubset(set(valid_drift["metric_name"]))
        for side in ("sample", "reference"):
            result.loc[index, f"optical_drift_{side}"] = float(valid_drift[f"{side}_value"].sum()) if drift_complete else None
        selected = applicable[applicable["sample_instance_id"] == row["sample_instance_id"]]
        comparable = selected.dropna(subset=["sample_value", "reference_value"])
        result.loc[index, "comparison_status"] = (
            "当前视图无指标" if selected.empty else "可比较" if len(comparable) == len(selected) else "部分指标可比较"
        )
        result.loc[index, "better_or_worse_summary"] = (
            f"当前指标：{int((selected['is_better_than_reference'] == True).sum())} 项数值方向更优；"
            "工程容差判断见指标明细"
        ) if "is_better_than_reference" in selected else ""
    if config is not None:
        from .score_calculator import apply_optional_score
        result = apply_optional_score(result, {}, config)
    else:
        # A view without scoring configuration must not reuse stale full-view ranks.
        for column in ("optional_composite_score", "optional_rank", "score_components", "score_missing_metrics"):
            result[column] = None
    return result
