"""Descriptive SolvA self-comparison analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .analysis_mode import (
    MISSING_AFTER_STATUS,
    MISSING_BEFORE_STATUS,
    SolvA_COMPLETE_STATUS,
    SINGLE_STAGE_STATUS,
    is_single_stage_group,
    single_stage_sheets,
)
from .parser import NUMERIC_FIELDS


META_COLUMNS = [
    "source_file",
    "source_sheet",
    "test_date",
    "source_file_hash",
    "source_sheet_hash",
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
]


@dataclass(slots=True)
class AnalysisTables:
    sample_statistics: pd.DataFrame
    thickness_comparison: pd.DataFrame
    optical_changes: pd.DataFrame
    unpaired_samples: pd.DataFrame
    outlier_impact: pd.DataFrame
    field_coverage: pd.DataFrame
    summary: dict[str, Any]


def safe_relative_change(after: float | None, before: float | None, near_zero: float) -> float | None:
    if before is None or after is None or pd.isna(before) or pd.isna(after):
        return None
    if abs(float(before)) <= near_zero:
        return None
    return (float(after) - float(before)) / float(before) * 100


def _series_stats(series: pd.Series) -> dict[str, float | int | None]:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return {
            "count": 0,
            "mean": None,
            "std": None,
            "median": None,
            "min": None,
            "max": None,
            "range": None,
            "cv": None,
        }
    mean = float(clean.mean())
    std = float(clean.std(ddof=1)) if len(clean) > 1 else None
    return {
        "count": int(len(clean)),
        "mean": mean,
        "std": std,
        "median": float(clean.median()),
        "min": float(clean.min()),
        "max": float(clean.max()),
        "range": float(clean.max() - clean.min()),
        "cv": (std / abs(mean) * 100) if std is not None and mean != 0 else None,
    }


def _metadata(group: pd.DataFrame) -> dict[str, Any]:
    first = group.iloc[0]
    return {column: first.get(column) for column in META_COLUMNS}


def _optional_statistical_test(
    before: pd.DataFrame,
    after: pd.DataFrame,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    paired = bool(config["analysis"].get("paired_replicates", False))
    welch = bool(config["analysis"].get("optional_welch_test", False))
    default = {
        "statistical_test_method": "未执行（默认描述性统计）",
        "statistical_test_assumption": "未假定前后重复编号为相同物理位置",
        "statistical_test_n_before": int(before["thickness"].notna().sum()),
        "statistical_test_n_after": int(after["thickness"].notna().sum()),
        "statistical_test_n_pairs": None,
        "statistical_test_statistic": None,
        "statistical_test_p_value": None,
        "statistical_test_interpretation": "仅报告描述性统计",
    }
    if not paired and not welch:
        return default
    try:
        from scipy import stats
    except ImportError:
        default["statistical_test_method"] = "未执行（缺少 SciPy）"
        default["statistical_test_interpretation"] = "安装 requirements.txt 后可执行可选检验"
        return default
    if paired:
        before_values = before[["replicate_number", "thickness"]].dropna()
        after_values = after[["replicate_number", "thickness"]].dropna()
        pairs = before_values.merge(
            after_values,
            on="replicate_number",
            suffixes=("_before", "_after"),
            validate="one_to_one",
        )
        default["statistical_test_method"] = "配对 t 检验"
        default["statistical_test_assumption"] = (
            "用户已确认相同重复编号代表同一物理测量位置；检验还假设配对差值近似正态"
        )
        default["statistical_test_n_pairs"] = int(len(pairs))
        if len(pairs) < 2:
            default["statistical_test_interpretation"] = "有效配对少于2，未执行检验"
            return default
        statistic, p_value = stats.ttest_rel(
            pairs["thickness_after"],
            pairs["thickness_before"],
            nan_policy="omit",
        )
        n_effective = len(pairs)
    else:
        before_values = pd.to_numeric(before["thickness"], errors="coerce").dropna()
        after_values = pd.to_numeric(after["thickness"], errors="coerce").dropna()
        default["statistical_test_method"] = "Welch 非配对 t 检验"
        default["statistical_test_assumption"] = (
            "前后观测视为独立样本；不假定方差相等；各组近似正态"
        )
        if len(before_values) < 2 or len(after_values) < 2:
            default["statistical_test_interpretation"] = "任一组有效样本少于2，未执行检验"
            return default
        statistic, p_value = stats.ttest_ind(
            after_values,
            before_values,
            equal_var=False,
            nan_policy="omit",
        )
        n_effective = min(len(before_values), len(after_values))
    default["statistical_test_statistic"] = float(statistic)
    default["statistical_test_p_value"] = float(p_value)
    if n_effective < 3:
        default["statistical_test_interpretation"] = "样本量很小，仅报告检验值，不作显著性结论"
    elif p_value < 0.05:
        default["statistical_test_interpretation"] = (
            "在所列检验假设下观察到差异证据；不等同于材料性能因果结论"
        )
    else:
        default["statistical_test_interpretation"] = (
            "在所列检验假设下未观察到充分差异证据；不代表统计学等效"
        )
    return default


def analyze_measurements(
    measurements: pd.DataFrame,
    quality_issues: pd.DataFrame,
    config: Mapping[str, Any],
) -> AnalysisTables:
    raw = measurements[~measurements["is_source_average_row"].fillna(False)].copy()
    near_zero = float(config["analysis"].get("near_zero_threshold", 1.0e-8))
    quality_warning_count = (
        int(
            quality_issues.get(
                "severity", pd.Series(index=quality_issues.index, dtype=str)
            )
            .fillna("warning")
            .ne("info")
            .sum()
        )
        if not quality_issues.empty
        else 0
    )
    sample_stats_rows: list[dict[str, Any]] = []
    comparison_rows: list[dict[str, Any]] = []
    optical_rows: list[dict[str, Any]] = []
    unpaired_rows: list[dict[str, Any]] = []
    outlier_impact_rows: list[dict[str, Any]] = []
    field_coverage_rows: list[dict[str, Any]] = []
    handling = config["quality"].get("outlier_handling", {})
    statistics_population = str(
        handling.get("statistics_population", "all_measurements")
    )
    use_filtered_as_active = statistics_population == "exclude_thickness_outliers"
    if raw.empty:
        empty_summary = {
            "source_file_count": 0,
            "source_sheet_count": 0,
            "sample_instance_count": 0,
            "complete_solva_count": 0,
            "dissolved_after_count": 0,
            "single_stage_count": 0,
            "missing_before_count": 0,
            "missing_after_count": 0,
            "mean_single_stage_thickness": None,
            "mean_thickness_loss": None,
            "mean_loss_percent": None,
            "mean_retention_percent": None,
            "quality_warning_count": quality_warning_count,
            "statistics_population": statistics_population,
            "statistics_population_label": {
                "all_measurements": "全部测量",
                "exclude_thickness_outliers": "排除已标记膜厚异常值",
                "impact_only": "全部测量（同时展示异常值影响）",
            }.get(statistics_population, statistics_population),
            "thickness_unit": config["analysis"].get(
                "thickness_unit", "原表膜厚单位"
            ),
        }
        return AnalysisTables(
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            empty_summary,
        )
    resolved_single_stage_sheets = single_stage_sheets(raw, config)
    single_stage_instance_ids = {
        str(instance_id)
        for instance_id, group in raw.groupby("sample_instance_id", sort=False)
        if is_single_stage_group(group, resolved_single_stage_sheets)
    }
    for (instance_id, stage), group in raw.groupby(
        ["sample_instance_id", "treatment_stage"], sort=False
    ):
        row = {
            **_metadata(group),
            "treatment_stage": stage,
            "analysis_mode": (
                "常规单阶段分析"
                if str(instance_id) in single_stage_instance_ids
                else "SolvA前后自比较"
            ),
        }
        for field in NUMERIC_FIELDS:
            stats = _series_stats(group[field])
            for metric, value in stats.items():
                row[f"{field}_{metric}"] = value
        filtered_group = group[~group.get("is_thickness_outlier", False).fillna(False)]
        filtered_thickness = _series_stats(filtered_group["thickness"])
        for metric, value in filtered_thickness.items():
            row[f"thickness_{metric}_filtered"] = value
            row[f"thickness_{metric}_active"] = (
                value if use_filtered_as_active else row.get(f"thickness_{metric}")
            )
        row["outlier_count"] = int(group.get("is_thickness_outlier", False).sum())
        row["excluded_from_statistics_count"] = (
            row["outlier_count"] if use_filtered_as_active else 0
        )
        row["statistics_population"] = statistics_population
        row["source_rows"] = ",".join(str(int(value)) for value in group["source_row"])
        sample_stats_rows.append(row)
        all_thickness = _series_stats(group["thickness"])
        outlier_impact_rows.append(
            {
                **_metadata(group),
                "treatment_stage": stage,
                "sample_stage_label": f"{group.iloc[0].get('sample_raw')} | {stage}",
                "statistics_population": statistics_population,
                "all_measurement_count": all_thickness["count"],
                "filtered_measurement_count": filtered_thickness["count"],
                "excluded_outlier_count": int(
                    all_thickness["count"] - filtered_thickness["count"]
                ),
                "excluded_fraction_percent": (
                    (all_thickness["count"] - filtered_thickness["count"])
                    / all_thickness["count"]
                    * 100
                    if all_thickness["count"]
                    else 0.0
                ),
                "all_thickness_mean": all_thickness["mean"],
                "filtered_thickness_mean": filtered_thickness["mean"],
                "mean_difference_filtered_minus_all": (
                    filtered_thickness["mean"] - all_thickness["mean"]
                    if filtered_thickness["mean"] is not None
                    and all_thickness["mean"] is not None
                    else None
                ),
                "all_thickness_std": all_thickness["std"],
                "filtered_thickness_std": filtered_thickness["std"],
                "all_thickness_cv": all_thickness["cv"],
                "filtered_thickness_cv": filtered_thickness["cv"],
                "source_rows": ",".join(
                    str(int(value)) for value in group["source_row"]
                ),
                "excluded_source_rows": ",".join(
                    str(int(value))
                    for value in group.loc[
                        group.get("is_thickness_outlier", False).fillna(False),
                        "source_row",
                    ]
                ),
            }
        )
    sample_statistics = pd.DataFrame(sample_stats_rows)

    for instance_id, group in raw.groupby("sample_instance_id", sort=False):
        coverage = {**_metadata(group)}
        coverage["source_rows"] = ",".join(
            str(int(value)) for value in group["source_row"]
        )
        coverage["measurement_count"] = int(len(group))
        for field in NUMERIC_FIELDS:
            valid_count = int(pd.to_numeric(group[field], errors="coerce").notna().sum())
            coverage[f"{field}_valid_count"] = valid_count
            coverage[f"{field}_coverage_percent"] = (
                valid_count / len(group) * 100 if len(group) else None
            )
        field_coverage_rows.append(coverage)

    for instance_id, group in raw.groupby("sample_instance_id", sort=False):
        meta = _metadata(group)
        before = group[group["treatment_stage"] == "before_solva"]
        after_stages = sorted(
            (stage
            for stage in group["treatment_stage"].dropna().unique()
            if str(stage).startswith("after_solva_")),
            key=lambda stage: int(str(stage).rsplit("_", 1)[1]),
        )
        ordinary_single_stage = (
            str(instance_id) in single_stage_instance_ids
        )
        instance_issue_text = ""
        if not quality_issues.empty:
            issue_subset = quality_issues[
                quality_issues["sample_instance_id"] == instance_id
            ]
            instance_issue_text = "；".join(
                dict.fromkeys(issue_subset["warning_message"].dropna().astype(str))
            )
            warning_count = int(len(issue_subset))
        else:
            warning_count = 0
        stages_to_emit = after_stages or [None]
        for after_stage in stages_to_emit:
            after = (
                group[group["treatment_stage"] == after_stage]
                if after_stage
                else group.iloc[0:0]
            )
            before_thickness = _series_stats(before["thickness"])
            after_thickness = _series_stats(after["thickness"])
            before_filtered = before[
                ~before.get("is_thickness_outlier", False).fillna(False)
            ]
            after_filtered = after[
                ~after.get("is_thickness_outlier", False).fillna(False)
            ]
            before_thickness_filtered = _series_stats(before_filtered["thickness"])
            after_thickness_filtered = _series_stats(after_filtered["thickness"])
            before_mse = _series_stats(before["mse"])
            after_mse = _series_stats(after["mse"])
            before_mean = before_thickness["mean"]
            after_mean = after_thickness["mean"]
            delta = (
                float(after_mean) - float(before_mean)
                if before_mean is not None and after_mean is not None
                else None
            )
            loss = -delta if delta is not None else None
            change_percent = safe_relative_change(after_mean, before_mean, near_zero)
            loss_percent = -change_percent if change_percent is not None else None
            retention = (
                float(after_mean) / float(before_mean) * 100
                if before_mean is not None
                and after_mean is not None
                and abs(float(before_mean)) > near_zero
                else None
            )
            before_filtered_mean = before_thickness_filtered["mean"]
            after_filtered_mean = after_thickness_filtered["mean"]
            delta_filtered = (
                float(after_filtered_mean) - float(before_filtered_mean)
                if before_filtered_mean is not None and after_filtered_mean is not None
                else None
            )
            loss_filtered = -delta_filtered if delta_filtered is not None else None
            change_percent_filtered = safe_relative_change(
                after_filtered_mean, before_filtered_mean, near_zero
            )
            loss_percent_filtered = (
                -change_percent_filtered
                if change_percent_filtered is not None
                else None
            )
            retention_filtered = (
                float(after_filtered_mean) / float(before_filtered_mean) * 100
                if before_filtered_mean is not None
                and after_filtered_mean is not None
                and abs(float(before_filtered_mean)) > near_zero
                else None
            )
            completeness = (
                SolvA_COMPLETE_STATUS
                if before_thickness["count"] > 0 and after_thickness["count"] > 0
                else (
                    MISSING_BEFORE_STATUS
                    if before_thickness["count"] == 0
                    else (
                        SINGLE_STAGE_STATUS
                        if ordinary_single_stage
                        else MISSING_AFTER_STATUS
                    )
                )
            )
            if completeness in {MISSING_BEFORE_STATUS, MISSING_AFTER_STATUS}:
                unpaired_rows.append({
                    **meta,
                    "after_stage": after_stage,
                    "data_completeness_status": completeness,
                    "warning_message": instance_issue_text or completeness,
                    "before_count": before_thickness["count"],
                    "after_count": after_thickness["count"],
                })
            comparison = {
                **meta,
                "analysis_mode": (
                    "常规单阶段分析"
                    if ordinary_single_stage
                    else "SolvA前后自比较"
                ),
                "after_stage": after_stage,
                "after_dissolved": bool(after.get("is_dissolved", pd.Series(False, index=after.index)).any()),
                "before_measurement_count": before_thickness["count"],
                "after_measurement_count": after_thickness["count"],
                **{f"before_thickness_{key}": value for key, value in before_thickness.items()},
                **{f"after_thickness_{key}": value for key, value in after_thickness.items()},
                **{
                    f"before_thickness_{key}_filtered": value
                    for key, value in before_thickness_filtered.items()
                },
                **{
                    f"after_thickness_{key}_filtered": value
                    for key, value in after_thickness_filtered.items()
                },
                **{
                    f"active_before_thickness_{key}": (
                        before_thickness_filtered[key]
                        if use_filtered_as_active
                        else before_thickness[key]
                    )
                    for key in before_thickness
                },
                **{
                    f"active_after_thickness_{key}": (
                        after_thickness_filtered[key]
                        if use_filtered_as_active
                        else after_thickness[key]
                    )
                    for key in after_thickness
                },
                "delta_thickness": delta,
                "thickness_loss": loss,
                "change_percent": change_percent,
                "loss_percent": loss_percent,
                "retention_percent": retention,
                "delta_thickness_filtered": delta_filtered,
                "thickness_loss_filtered": loss_filtered,
                "change_percent_filtered": change_percent_filtered,
                "loss_percent_filtered": loss_percent_filtered,
                "retention_percent_filtered": retention_filtered,
                "before_excluded_count": int(
                    before_thickness["count"] - before_thickness_filtered["count"]
                ),
                "after_excluded_count": int(
                    after_thickness["count"] - after_thickness_filtered["count"]
                ),
                "outlier_impact_on_mean_before": (
                    before_filtered_mean - before_mean
                    if before_filtered_mean is not None and before_mean is not None
                    else None
                ),
                "outlier_impact_on_mean_after": (
                    after_filtered_mean - after_mean
                    if after_filtered_mean is not None and after_mean is not None
                    else None
                ),
                "outlier_impact_on_loss_percent": (
                    loss_percent_filtered - loss_percent
                    if loss_percent_filtered is not None and loss_percent is not None
                    else None
                ),
                "statistics_population": statistics_population,
                "active_before_thickness_mean": (
                    before_filtered_mean if use_filtered_as_active else before_mean
                ),
                "active_after_thickness_mean": (
                    after_filtered_mean if use_filtered_as_active else after_mean
                ),
                "active_delta_thickness": (
                    delta_filtered if use_filtered_as_active else delta
                ),
                "active_thickness_loss": (
                    loss_filtered if use_filtered_as_active else loss
                ),
                "active_change_percent": (
                    change_percent_filtered if use_filtered_as_active else change_percent
                ),
                "active_loss_percent": (
                    loss_percent_filtered if use_filtered_as_active else loss_percent
                ),
                "active_retention_percent": (
                    retention_filtered if use_filtered_as_active else retention
                ),
                "before_mse_mean": before_mse["mean"],
                "before_mse_max": before_mse["max"],
                "after_mse_mean": after_mse["mean"],
                "after_mse_max": after_mse["max"],
                "mse_change": (
                    after_mse["mean"] - before_mse["mean"]
                    if after_mse["mean"] is not None and before_mse["mean"] is not None
                    else None
                ),
                "thickness_cv_change": (
                    after_thickness["cv"] - before_thickness["cv"]
                    if after_thickness["cv"] is not None
                    and before_thickness["cv"] is not None
                    else None
                ),
                "data_completeness_status": completeness,
                "quality_warning_count": warning_count,
                "warning_message": instance_issue_text,
                "before_source_rows": ",".join(str(int(value)) for value in before["source_row"]),
                "after_source_rows": ",".join(str(int(value)) for value in after["source_row"]),
                **_optional_statistical_test(before, after, config),
            }
            comparison_rows.append(comparison)
            for field in (*NUMERIC_FIELDS[:-2], "mse"):
                before_stats = _series_stats(before[field])
                after_stats = _series_stats(after[field])
                delta_value = (
                    after_stats["mean"] - before_stats["mean"]
                    if before_stats["mean"] is not None and after_stats["mean"] is not None
                    else None
                )
                optical_rows.append(
                    {
                        **meta,
                        "analysis_mode": (
                            "常规单阶段分析"
                            if ordinary_single_stage
                            else "SolvA前后自比较"
                        ),
                        "after_stage": after_stage,
                        "parameter": field,
                        "before_mean": before_stats["mean"],
                        "after_mean": after_stats["mean"],
                        "absolute_change": delta_value,
                        "relative_change_percent": safe_relative_change(
                            after_stats["mean"], before_stats["mean"], near_zero
                        ),
                        "before_std": before_stats["std"],
                        "after_std": after_stats["std"],
                        "before_cv_percent": before_stats["cv"],
                        "after_cv_percent": after_stats["cv"],
                        "before_count": before_stats["count"],
                        "after_count": after_stats["count"],
                        "before_source_rows": ",".join(
                            str(int(value)) for value in before["source_row"]
                        ),
                        "after_source_rows": ",".join(
                            str(int(value)) for value in after["source_row"]
                        ),
                    }
                )
    thickness_comparison = pd.DataFrame(comparison_rows)
    complete = thickness_comparison[
        thickness_comparison["data_completeness_status"] == SolvA_COMPLETE_STATUS
    ].copy()
    single_stage = thickness_comparison[
        thickness_comparison["data_completeness_status"] == SINGLE_STAGE_STATUS
    ].copy()
    summary: dict[str, Any] = {
        "source_file_count": int(raw["source_file_hash"].nunique()),
        "source_sheet_count": int(
            raw[["source_file_hash", "source_sheet"]].drop_duplicates().shape[0]
        ),
        "sample_instance_count": int(raw["sample_instance_id"].nunique()),
        "complete_solva_count": int(complete["sample_instance_id"].nunique()),
        "dissolved_after_count": int(thickness_comparison.loc[
            thickness_comparison["after_dissolved"], "sample_instance_id"
        ].nunique()),
        "single_stage_count": int(single_stage["sample_instance_id"].nunique()),
        "missing_before_count": int(
            len({row["sample_instance_id"] for row in unpaired_rows
                 if row["data_completeness_status"] == MISSING_BEFORE_STATUS})
        ),
        "missing_after_count": int(
            len({row["sample_instance_id"] for row in unpaired_rows
                 if row["data_completeness_status"] == MISSING_AFTER_STATUS})
        ),
        "statistics_population": statistics_population,
        "statistics_population_label": {
            "all_measurements": "全部测量",
            "exclude_thickness_outliers": "排除已标记膜厚异常值",
            "impact_only": "全部测量（同时展示异常值影响）",
        }.get(statistics_population, statistics_population),
        "excluded_thickness_outlier_count": int(
            raw.get("is_thickness_outlier", False).fillna(False).sum()
            if "is_thickness_outlier" in raw
            else 0
        ),
        "mean_single_stage_thickness": (
            float(single_stage["active_before_thickness_mean"].mean())
            if not single_stage.empty
            else None
        ),
        "mean_thickness_loss": (
            float(complete["active_thickness_loss"].mean()) if not complete.empty else None
        ),
        "mean_loss_percent": (
            float(complete["active_loss_percent"].mean()) if not complete.empty else None
        ),
        "mean_retention_percent": (
            float(complete["active_retention_percent"].mean()) if not complete.empty else None
        ),
        "quality_warning_count": quality_warning_count,
        "thickness_unit": config["analysis"].get("thickness_unit", "原表膜厚单位"),
    }
    if not complete.empty:
        max_loss_row = complete.loc[complete["active_thickness_loss"].idxmax()]
        min_retention_row = complete.loc[complete["active_retention_percent"].idxmin()]
        summary["max_thickness_loss_sample"] = max_loss_row["sample_raw"]
        summary["max_thickness_loss"] = float(max_loss_row["active_thickness_loss"])
        summary["lowest_retention_sample"] = min_retention_row["sample_raw"]
        summary["lowest_retention_percent"] = float(
            min_retention_row["active_retention_percent"]
        )
    return AnalysisTables(
        sample_statistics=sample_statistics,
        thickness_comparison=thickness_comparison,
        optical_changes=pd.DataFrame(optical_rows),
        unpaired_samples=pd.DataFrame(unpaired_rows),
        outlier_impact=pd.DataFrame(outlier_impact_rows),
        field_coverage=pd.DataFrame(field_coverage_rows),
        summary=summary,
    )
