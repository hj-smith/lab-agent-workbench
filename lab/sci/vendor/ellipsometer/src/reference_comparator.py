"""Full horizontal comparison against one explicitly selected sample instance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .score_calculator import apply_optional_score
from .tolerance_evaluator import evaluate_tolerance
from .reference_scope import OPTICAL_FIELDS, select_stage_rows, project_metrics


PERCENTAGE_METRICS = {"change_percent", "loss_percent", "retention_percent"}


@dataclass(slots=True)
class ReferenceComparisonResult:
    wide: pd.DataFrame
    long: pd.DataFrame


def _number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _differences(
    sample: float | None, reference: float | None, near_zero: float
) -> dict[str, float | None | str]:
    if sample is None or reference is None:
        return {
            "absolute_difference": None,
            "relative_difference_percent": None,
            "ratio_to_reference": None,
            "absolute_deviation": None,
            "absolute_relative_deviation_percent": None,
            "status": "样品值或参考值缺失",
        }
    difference = sample - reference
    if abs(reference) <= near_zero:
        return {
            "absolute_difference": difference,
            "relative_difference_percent": None,
            "ratio_to_reference": None,
            "absolute_deviation": abs(difference),
            "absolute_relative_deviation_percent": None,
            "status": "参考值不适合计算相对比例",
        }
    return {
        "absolute_difference": difference,
        "relative_difference_percent": difference / reference * 100,
        "ratio_to_reference": sample / reference,
        "absolute_deviation": abs(difference),
        "absolute_relative_deviation_percent": abs(difference) / abs(reference) * 100,
        "status": "可比较",
    }


def _comparison_metric_map(row: pd.Series, optical: pd.DataFrame) -> dict[str, tuple[str, str, float | None]]:
    metrics: dict[str, tuple[str, str, float | None]] = {
        "before_thickness_mean": ("膜厚", "before_solva", _number(row.get("before_thickness_mean"))),
        "before_thickness_std": ("膜厚重复性", "before_solva", _number(row.get("before_thickness_std"))),
        "before_thickness_median": ("膜厚", "before_solva", _number(row.get("before_thickness_median"))),
        "before_thickness_range": ("膜厚重复性", "before_solva", _number(row.get("before_thickness_range"))),
        "before_thickness_cv": ("膜厚重复性", "before_solva", _number(row.get("before_thickness_cv"))),
        "before_measurement_count": ("完整性", "before_solva", _number(row.get("before_measurement_count"))),
        "after_thickness_mean": ("膜厚", "after_solva_1", _number(row.get("after_thickness_mean"))),
        "after_thickness_std": ("膜厚重复性", "after_solva_1", _number(row.get("after_thickness_std"))),
        "after_thickness_median": ("膜厚", "after_solva_1", _number(row.get("after_thickness_median"))),
        "after_thickness_range": ("膜厚重复性", "after_solva_1", _number(row.get("after_thickness_range"))),
        "after_thickness_cv": ("膜厚重复性", "after_solva_1", _number(row.get("after_thickness_cv"))),
        "after_measurement_count": ("完整性", "after_solva_1", _number(row.get("after_measurement_count"))),
        "delta_thickness": ("SolvA耐受性", "before_to_after", _number(row.get("delta_thickness"))),
        "thickness_loss": ("SolvA耐受性", "before_to_after", _number(row.get("thickness_loss"))),
        "change_percent": ("SolvA耐受性", "before_to_after", _number(row.get("change_percent"))),
        "loss_percent": ("SolvA耐受性", "before_to_after", _number(row.get("loss_percent"))),
        "retention_percent": ("SolvA耐受性", "before_to_after", _number(row.get("retention_percent"))),
        "thickness_cv_change": ("膜厚重复性", "before_to_after", _number(row.get("thickness_cv_change"))),
        "before_mse_mean": ("MSE", "before_solva", _number(row.get("before_mse_mean"))),
        "before_mse_max": ("MSE", "before_solva", _number(row.get("before_mse_max"))),
        "after_mse_mean": ("MSE", "after_solva_1", _number(row.get("after_mse_mean"))),
        "after_mse_max": ("MSE", "after_solva_1", _number(row.get("after_mse_max"))),
        "mse_change": ("MSE", "before_to_after", _number(row.get("mse_change"))),
        "quality_warning_count": ("数据质量", "all", _number(row.get("quality_warning_count"))),
    }
    for _, item in optical.iterrows():
        parameter = str(item["parameter"])
        metrics[f"{parameter}_before_mean"] = (
            "光学常数" if parameter != "mse" else "MSE",
            "before_solva",
            _number(item.get("before_mean")),
        )
        metrics[f"{parameter}_after_mean"] = (
            "光学常数" if parameter != "mse" else "MSE",
            "after_solva_1",
            _number(item.get("after_mean")),
        )
        metrics[f"{parameter}_absolute_change"] = (
            "光学常数漂移" if parameter != "mse" else "MSE",
            "before_to_after",
            _number(item.get("absolute_change")),
        )
        metrics[f"{parameter}_drift_magnitude"] = (
            "光学常数漂移" if parameter != "mse" else "MSE",
            "before_to_after",
            abs(_number(item.get("absolute_change")))
            if _number(item.get("absolute_change")) is not None
            else None,
        )
        metrics[f"{parameter}_relative_change_percent"] = (
            "光学常数漂移" if parameter != "mse" else "MSE",
            "before_to_after",
            _number(item.get("relative_change_percent")),
        )
        metrics[f"{parameter}_relative_drift_magnitude_percent"] = (
            "光学常数漂移" if parameter != "mse" else "MSE",
            "before_to_after",
            abs(_number(item.get("relative_change_percent")))
            if _number(item.get("relative_change_percent")) is not None
            else None,
        )
        metrics[f"{parameter}_before_std"] = (
            "光学常数重复性",
            "before_solva",
            _number(item.get("before_std")),
        )
        metrics[f"{parameter}_after_std"] = (
            "光学常数重复性",
            "after_solva_1",
            _number(item.get("after_std")),
        )
        metrics[f"{parameter}_before_cv"] = (
            "光学常数重复性",
            "before_solva",
            _number(item.get("before_cv_percent")),
        )
        metrics[f"{parameter}_after_cv"] = (
            "光学常数重复性",
            "after_solva_1",
            _number(item.get("after_cv_percent")),
        )
        for side in ("before", "after"):
            metrics[f"{parameter}_{side}_count"] = ("完整性", "before_solva" if side == "before" else "after_solva_1", _number(item.get(f"{side}_count")))
    for field in OPTICAL_FIELDS:
        for side in ("before", "after"):
            metrics.setdefault(f"{field}_{side}_mean", ("光学常数", "before_solva" if side == "before" else "after_solva_1", None))
    selected_after = row.get("after_stage") or "after_solva_1"
    metrics = {key: (group, selected_after if stage == "after_solva_1" else stage, value)
               for key, (group, stage, value) in metrics.items()}
    return metrics


def _condition_status(sample: pd.Series, reference: pd.Series, config: Mapping[str, Any]) -> tuple[str, str]:
    rules = config["reference_comparison"]["condition_matching"]
    comparisons = {
        "source_file": ("来源文件", sample.get("source_file_hash"), reference.get("source_file_hash")),
        "solva_condition": ("SolvA 条件", sample.get("solva_condition"), reference.get("solva_condition")),
        "bake_condition": ("烘烤/固化条件", sample.get("process_condition"), reference.get("process_condition")),
        "test_date": ("测试日期", sample.get("test_date"), reference.get("test_date")),
        "model": ("模型", sample.get("model"), reference.get("model")),
        "source_sheet": ("来源标签页", sample.get("source_sheet"), reference.get("source_sheet")),
        "measurement_count": (
            "测量次数",
            (sample.get("before_measurement_count"), sample.get("after_measurement_count")),
            (reference.get("before_measurement_count"), reference.get("after_measurement_count")),
        ),
    }
    warnings = [
        f"{label}不同"
        for key, (label, sample_value, reference_value) in comparisons.items()
        if (rules.get(key, "ignore") == "warn" or
            (not config["reference_comparison"].get("allow_cross_condition_comparison", True)
             and key in {"solva_condition", "bake_condition", "model", "test_date", "measurement_count"}))
        and ("" if sample_value is None else sample_value)
        != ("" if reference_value is None else reference_value)
    ]
    return ("一致" if not warnings else "条件不完全一致"), "；".join(warnings)


def compare_to_reference(
    reference_sample_instance_id: str,
    thickness_comparison: pd.DataFrame,
    optical_changes: pd.DataFrame,
    sample_statistics: pd.DataFrame,
    quality_issues: pd.DataFrame,
    config: Mapping[str, Any],
) -> ReferenceComparisonResult:
    if thickness_comparison.empty:
        return ReferenceComparisonResult(pd.DataFrame(), pd.DataFrame())
    selected_stage = str(config["reference_comparison"].get("after_stage", "after_solva_1"))
    candidates = select_stage_rows(thickness_comparison, selected_stage)
    optical_changes = select_stage_rows(optical_changes, selected_stage, optical=True)
    reference_rows = candidates[
        candidates["sample_instance_id"] == reference_sample_instance_id
    ]
    if len(reference_rows) != 1:
        raise ValueError("参考样必须由唯一 sample_instance_id 精确选择")
    reference = reference_rows.iloc[0]
    reference_optical = optical_changes[
        optical_changes["sample_instance_id"] == reference_sample_instance_id
    ]
    reference_metrics = _comparison_metric_map(reference, reference_optical)
    near_zero = float(config["reference_comparison"].get("near_zero_threshold", 1.0e-8))
    wide_rows: list[dict[str, Any]] = []
    long_rows: list[dict[str, Any]] = []
    for _, sample in candidates.iterrows():
        if sample["sample_instance_id"] == reference_sample_instance_id:
            continue
        sample_optical = optical_changes[
            optical_changes["sample_instance_id"] == sample["sample_instance_id"]
        ]
        sample_metrics = _comparison_metric_map(sample, sample_optical)
        condition_status, condition_warning = _condition_status(sample, reference, config)
        if not config["reference_comparison"].get("show_condition_mismatch_warning", True):
            condition_warning = ""
        issue_count = (
            int(
                quality_issues["sample_instance_id"]
                .eq(sample["sample_instance_id"])
                .sum()
            )
            if not quality_issues.empty
            else 0
        )
        sample_issues = (
            quality_issues[quality_issues["sample_instance_id"] == sample["sample_instance_id"]]
            if not quality_issues.empty
            else pd.DataFrame()
        )
        expected_fields = config["reference_comparison"].get("completeness_fields", ["thickness", *OPTICAL_FIELDS])
        expected_stages = ("before",) if sample.get("data_completeness_status") == "常规单阶段" else ("before", "after")
        expected_metrics = [f"{stage}_thickness_mean" if field == "thickness" else f"{field}_{stage}_mean"
                            for stage in expected_stages for field in expected_fields]
        present_count = sum(sample_metrics.get(key, ("", "", None))[2] is not None for key in expected_metrics)
        completeness = present_count / len(expected_metrics) * 100 if expected_metrics else None
        wide = {
            "reference_sample_instance_id": reference_sample_instance_id,
            "reference_sample_id": reference.get("sample_id"),
            "reference_sample_name": reference["sample_raw"],
            "reference_source_file": reference["source_file"],
            "reference_source_file_hash": reference.get("source_file_hash"),
            "reference_source_sheet": reference["source_sheet"],
            "reference_source_rows": (
                str(reference.get("before_source_rows") or "")
                + "|"
                + str(reference.get("after_source_rows") or "")
            ),
            "sample_instance_id": sample["sample_instance_id"],
            "sample_id": sample.get("sample_id"),
            "sample_name": sample["sample_raw"],
            "sample_source_file": sample["source_file"],
            "sample_source_file_hash": sample.get("source_file_hash"),
            "after_stage": selected_stage,
            "analysis_mode": sample.get("analysis_mode"),
            "data_completeness_status": sample.get("data_completeness_status"),
            "after_dissolved": bool(sample.get("after_dissolved", False)),
            "completeness_rule": "适用阶段有效均值数 / 配置要求的均值数 × 100%",
            "completeness_valid_count": present_count,
            "completeness_expected_count": len(expected_metrics),
            "sample_source_sheet": sample["source_sheet"],
            "sample_source_rows": (
                str(sample.get("before_source_rows") or "")
                + "|"
                + str(sample.get("after_source_rows") or "")
            ),
            "test_date": sample.get("test_date"),
            "sample_description": sample.get("sample_description"),
            "model": sample.get("model"),
            "process_condition": sample.get("process_condition"),
            "solva_condition": sample.get("solva_condition"),
            "bake_temperature": sample.get("bake_temperature"),
            "bake_time": sample.get("bake_time"),
            "curing_condition": sample.get("curing_condition"),
            "solva_time": sample.get("solva_time"),
            "solva_method": sample.get("solva_method"),
            "condition_match_status": condition_status,
            "before_thickness_sample": sample.get("before_thickness_mean"),
            "before_thickness_reference": reference.get("before_thickness_mean"),
            "after_thickness_sample": sample.get("after_thickness_mean"),
            "after_thickness_reference": reference.get("after_thickness_mean"),
            "thickness_loss_sample": sample.get("thickness_loss"),
            "thickness_loss_reference": reference.get("thickness_loss"),
            "loss_percent_sample": sample.get("loss_percent"),
            "loss_percent_reference": reference.get("loss_percent"),
            "retention_percent_sample": sample.get("retention_percent"),
            "retention_percent_reference": reference.get("retention_percent"),
            "mse_before_sample": sample.get("before_mse_mean"),
            "mse_before_reference": reference.get("before_mse_mean"),
            "mse_after_sample": sample.get("after_mse_mean"),
            "mse_after_reference": reference.get("after_mse_mean"),
            "thickness_cv_before_sample": sample.get("before_thickness_cv"),
            "thickness_cv_before_reference": reference.get("before_thickness_cv"),
            "thickness_cv_after_sample": sample.get("after_thickness_cv"),
            "thickness_cv_after_reference": reference.get("after_thickness_cv"),
            "data_completeness": completeness,
            "data_quality_warning_count": issue_count,
            "outlier_count": int(sample_issues["issue_type"].eq("thickness_outlier").sum())
            if not sample_issues.empty
            else 0,
            "average_mismatch_count": int(sample_issues["issue_type"].eq("average_mismatch").sum())
            if not sample_issues.empty
            else 0,
            "parse_warning_count": int(sample_issues["issue_type"].eq("parse_warning").sum())
            if not sample_issues.empty
            else 0,
            "repeat_measurement_completeness": (
                "处理后溶解（约定一次观测）"
                if sample.get("after_dissolved", False)
                else "完整且数量一致"
                if sample.get("data_completeness_status") == "完整"
                and sample.get("before_measurement_count") == sample.get("after_measurement_count")
                else "不完整或数量不一致"
            ),
        }
        long_classes: list[str] = []
        better_count = worse_count = 0
        for metric_name, (group_name, stage, sample_value) in sample_metrics.items():
            reference_value = reference_metrics.get(metric_name, ("", "", None))[2]
            diff = _differences(sample_value, reference_value, near_zero)
            pp_difference = (
                diff["absolute_difference"]
                if metric_name in PERCENTAGE_METRICS
                or metric_name.endswith("_relative_change_percent")
                else None
            )
            tolerance = evaluate_tolerance(
                metric_name,
                sample_value,
                reference_value,
                diff["relative_difference_percent"],
                pp_difference,
                config,
            )
            if (
                stage in {selected_stage, "before_to_after"}
                and tolerance.evaluation_status
                in {"sample_missing", "reference_missing", "percentage_point_missing"}
                and (
                    sample.get("data_completeness_status") == "常规单阶段"
                    or reference.get("data_completeness_status") == "常规单阶段"
                )
            ):
                tolerance.evaluation_status = "not_applicable_stage"
                tolerance.missing_reason = "常规单阶段数据不适用处理后或前后变化指标"
                tolerance.rule_description = tolerance.missing_reason
            long_classes.append(tolerance.comparison_class)
            if tolerance.is_better is True:
                better_count += 1
            elif tolerance.is_better is False:
                worse_count += 1
            warning = condition_warning
            if diff["status"] != "可比较":
                warning = "；".join(filter(None, [warning, str(diff["status"])]))
            long_rows.append(
                {
                    "reference_sample_instance_id": reference_sample_instance_id,
                    "reference_source_file": reference["source_file"],
                    "reference_source_sheet": reference["source_sheet"],
                    "reference_source_rows": reference.get("before_source_rows") if stage == "before_solva" else reference.get("after_source_rows") if stage == selected_stage else wide["reference_source_rows"],
                    "sample_instance_id": sample["sample_instance_id"],
                    "sample_source_file": sample["source_file"],
                    "sample_source_sheet": sample["source_sheet"],
                    "sample_source_rows": sample.get("before_source_rows") if stage == "before_solva" else sample.get("after_source_rows") if stage == selected_stage else wide["sample_source_rows"],
                    "is_applicable": not (stage in {selected_stage, "before_to_after"} and
                        (sample.get("data_completeness_status") == "常规单阶段" or reference.get("data_completeness_status") == "常规单阶段")),
                    "metric_group": group_name,
                    "metric_name": metric_name,
                    "treatment_stage": stage,
                    "sample_value": sample_value,
                    "reference_value": reference_value,
                    "sample_std": sample_metrics.get(metric_name.replace("_mean", "_std"), ("", "", None))[2] if metric_name.endswith("_mean") else None,
                    "reference_std": reference_metrics.get(metric_name.replace("_mean", "_std"), ("", "", None))[2] if metric_name.endswith("_mean") else None,
                    "sample_count": sample_metrics.get(metric_name.replace("_mean", "_count"), ("", "", None))[2] if metric_name.endswith("_mean") else None,
                    "reference_count": reference_metrics.get(metric_name.replace("_mean", "_count"), ("", "", None))[2] if metric_name.endswith("_mean") else None,
                    **diff,
                    "percentage_point_difference": pp_difference,
                    "preference_direction": tolerance.preference_direction,
                    "tolerance_config_key": tolerance.tolerance_config_key,
                    "tolerance_kind": tolerance.tolerance_kind,
                    "configured_tolerance_value": tolerance.configured_tolerance_value,
                    "tolerance_configured": tolerance.tolerance_configured,
                    "evaluated_value": tolerance.evaluated_value,
                    "evaluation_status": tolerance.evaluation_status,
                    "missing_reason": tolerance.missing_reason,
                    "tolerance_lower": tolerance.tolerance_lower,
                    "tolerance_upper": tolerance.tolerance_upper,
                    "comparison_class": tolerance.comparison_class,
                    "is_better_than_reference": tolerance.is_better,
                    "judgment_rule": tolerance.rule_description,
                    "absolute_limit_status": tolerance.absolute_limit_status,
                    "warning_message": warning,
                }
            )
        before_diff = _differences(
            _number(sample.get("before_thickness_mean")),
            _number(reference.get("before_thickness_mean")),
            near_zero,
        )
        after_diff = _differences(
            _number(sample.get("after_thickness_mean")),
            _number(reference.get("after_thickness_mean")),
            near_zero,
        )
        loss_diff = _differences(
            _number(sample.get("thickness_loss")),
            _number(reference.get("thickness_loss")),
            near_zero,
        )
        loss_pct_diff = _differences(
            _number(sample.get("loss_percent")),
            _number(reference.get("loss_percent")),
            near_zero,
        )
        retention_diff = _differences(
            _number(sample.get("retention_percent")),
            _number(reference.get("retention_percent")),
            near_zero,
        )
        wide.update(
            {
                "before_thickness_difference": before_diff["absolute_difference"],
                "before_thickness_relative_difference": before_diff["relative_difference_percent"],
                "after_thickness_difference": after_diff["absolute_difference"],
                "after_thickness_relative_difference": after_diff["relative_difference_percent"],
                "thickness_loss_difference": loss_diff["absolute_difference"],
                "loss_percent_pp_difference": loss_pct_diff["absolute_difference"],
                "loss_percent_relative_difference": loss_pct_diff["relative_difference_percent"],
                "retention_percent_pp_difference": retention_diff["absolute_difference"],
            }
        )
        total_optical_deviation = 0.0
        optical_count = 0
        for parameter in ("n_193", "k_193", "n_248", "k_248", "n_633", "k_633"):
            for stage in ("before", "after"):
                metric = f"{parameter}_{stage}_mean"
                sample_value = sample_metrics.get(metric, ("", "", None))[2]
                reference_value = reference_metrics.get(metric, ("", "", None))[2]
                optical_diff = _differences(sample_value, reference_value, near_zero)
                difference = optical_diff["absolute_difference"]
                wide[f"{parameter}_{stage}_sample"] = sample_value
                wide[f"{parameter}_{stage}_reference"] = reference_value
                wide[f"{parameter}_{stage}_relative_difference_percent"] = optical_diff["relative_difference_percent"]
                wide[f"{parameter}_{stage}_ratio"] = optical_diff["ratio_to_reference"]
                wide[f"{metric}_difference"] = difference
                wide[f"{parameter}_{stage}_difference"] = difference
                if difference is not None:
                    total_optical_deviation += abs(float(difference))
                    optical_count += 1
        wide["optical_constant_total_deviation"] = (
            total_optical_deviation if optical_count else None
        )
        wide["mse_before_difference"] = (
            _number(sample.get("before_mse_mean")) - _number(reference.get("before_mse_mean"))
            if _number(sample.get("before_mse_mean")) is not None
            and _number(reference.get("before_mse_mean")) is not None
            else None
        )
        wide["mse_after_difference"] = (
            _number(sample.get("after_mse_mean")) - _number(reference.get("after_mse_mean"))
            if _number(sample.get("after_mse_mean")) is not None
            and _number(reference.get("after_mse_mean")) is not None
            else None
        )
        wide["thickness_cv_before_difference"] = (
            _number(sample.get("before_thickness_cv")) - _number(reference.get("before_thickness_cv"))
            if _number(sample.get("before_thickness_cv")) is not None
            and _number(reference.get("before_thickness_cv")) is not None
            else None
        )
        wide["thickness_cv_after_difference"] = (
            _number(sample.get("after_thickness_cv")) - _number(reference.get("after_thickness_cv"))
            if _number(sample.get("after_thickness_cv")) is not None
            and _number(reference.get("after_thickness_cv")) is not None
            else None
        )
        wide["comparison_status"] = (
            "可比较" if sample.get("data_completeness_status") == "完整" else "部分指标可比较"
        )
        wide["better_or_worse_summary"] = (
            f"按已配置方向：{better_count} 项优于参考，{worse_count} 项未优于参考"
        )
        wide["warning_message"] = "；".join(
            filter(
                None,
                [
                    condition_warning,
                    str(sample.get("warning_message") or ""),
                    "未配置容差的指标不作工程容差分级"
                    if "未配置工程容差" in long_classes
                    else "",
                ],
            )
        )
        wide_rows.append(wide)
    wide_df = pd.DataFrame(wide_rows)
    detail_df = pd.DataFrame(long_rows)
    if not wide_df.empty:
        if not config["reference_comparison"].get("allow_cross_condition_comparison", True):
            wide_df = wide_df[wide_df["condition_match_status"] == "一致"].copy()
            detail_df = detail_df[detail_df["sample_instance_id"].isin(wide_df["sample_instance_id"])].copy()
        wide_df = project_metrics(wide_df, detail_df, config)
    return ReferenceComparisonResult(wide_df, detail_df)
