"""Data-quality checks; flags observations without deleting them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .analysis_mode import is_single_stage_group, single_stage_sheets
from .parser import NUMERIC_FIELDS


@dataclass(slots=True)
class QualityResult:
    measurements: pd.DataFrame
    issues: pd.DataFrame


def _issue(
    row: Mapping[str, Any],
    issue_type: str,
    severity: str,
    message: str,
    field: str | None = None,
    observed: Any = None,
    expected: Any = None,
) -> dict[str, Any]:
    return {
        "source_file": row.get("source_file"),
        "source_sheet": row.get("source_sheet"),
        "source_row": row.get("source_row"),
        "source_cell_range": row.get("source_cell_range"),
        "sample_instance_id": row.get("sample_instance_id"),
        "sample_id": row.get("sample_id"),
        "treatment_stage": row.get("treatment_stage"),
        "issue_type": issue_type,
        "field": field,
        "severity": severity,
        "observed_value": observed,
        "expected_value": expected,
        "warning_message": message,
    }


def _cv(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < 2:
        return None
    mean = float(clean.mean())
    if mean == 0:
        return None
    return float(clean.std(ddof=1) / abs(mean) * 100)


def _outlier_diagnostics(
    series: pd.Series, config: Mapping[str, Any]
) -> tuple[set[int], float | None, float | None, bool, float | None, float | None, str]:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    minimum_count = int(
        config["quality"].get("minimum_outlier_detection_count", 4)
    )
    if len(clean) < minimum_count:
        return set(), None, None, False, None, None, f"有效测量数少于 {minimum_count}，不执行异常值检测"
    method = str(config["quality"].get("outlier_method", "iqr")).lower()
    if method == "mad":
        median = float(clean.median())
        mad = float(np.median(np.abs(clean - median)))
        if mad == 0:
            return set(), median, median, True, median, mad, "MAD=0，未标记异常值"
        z = 0.6745 * (clean - median) / mad
        threshold = float(config["quality"].get("mad_z_threshold", 3.5))
        half_width = threshold * mad / 0.6745
        lower, upper = median - half_width, median + half_width
        return (
            set(clean.index[np.abs(z) > threshold]),
            lower,
            upper,
            True,
            median,
            mad,
            f"MAD 修正Z值阈值={threshold}",
        )
    q1, q3 = clean.quantile([0.25, 0.75])
    iqr = float(q3 - q1)
    multiplier = float(config["quality"].get("iqr_multiplier", 1.5))
    lower, upper = float(q1 - multiplier * iqr), float(q3 + multiplier * iqr)
    return (
        set(clean.index[(clean < lower) | (clean > upper)]),
        lower,
        upper,
        True,
        float(clean.median()),
        iqr,
        f"IQR 倍数={multiplier}",
    )


def _outlier_indexes(series: pd.Series, config: Mapping[str, Any]) -> set[int]:
    return _outlier_diagnostics(series, config)[0]


def run_quality_checks(
    measurements: pd.DataFrame, config: Mapping[str, Any]
) -> QualityResult:
    data = measurements.copy()
    if data.empty:
        return QualityResult(data, pd.DataFrame())
    if "is_dissolved" not in data:
        data["is_dissolved"] = False
    data["is_thickness_outlier"] = False
    data["is_excluded_from_statistics"] = False
    data["exclusion_reason"] = None
    data["outlier_method"] = str(
        config["quality"].get("outlier_method", "iqr")
    ).lower()
    data["outlier_group"] = None
    data["outlier_detection_eligible"] = False
    data["outlier_lower_bound"] = None
    data["outlier_upper_bound"] = None
    data["outlier_center"] = None
    data["outlier_dispersion"] = None
    data["outlier_rule_description"] = None
    handling = config["quality"].get("outlier_handling", {})
    statistics_population = str(
        handling.get("statistics_population", "all_measurements")
    )
    exclude_outliers = statistics_population == "exclude_thickness_outliers"
    data["active_statistics_population"] = statistics_population
    issues: list[dict[str, Any]] = []
    raw_rows = data[~data["is_source_average_row"].fillna(False)].copy()
    average_rows = data[data["is_source_average_row"].fillna(False)].copy()
    resolved_single_stage_sheets = single_stage_sheets(raw_rows, config)

    for index, row in data.iterrows():
        if row["is_dissolved"]:
            issues.append(_issue(
                row, "dissolved_after_treatment", "info",
                f"处理后溶解：按膜厚 0、损失率 100% 计入（原文：{row['dissolution_note']}）",
                "thickness", 0.0,
            ))
        if row.get("parse_status") != "ok":
            issues.append(
                _issue(
                    row,
                    "parse_warning",
                    "warning",
                    str(row.get("warning_message") or "解析警告"),
                )
            )

    grouped = raw_rows.groupby(["sample_instance_id", "treatment_stage"], dropna=False)
    for (instance_id, stage), group in grouped:
        outlier_group = f"{instance_id}|{stage}"
        data.loc[group.index, "outlier_group"] = outlier_group
        if group["is_dissolved"].any():
            data.loc[group.index, "outlier_rule_description"] = "处理后溶解的约定零膜厚，不执行异常值检测"
            continue
        (
            outlier_indexes,
            lower_bound,
            upper_bound,
            detection_eligible,
            center,
            dispersion,
            rule_description,
        ) = _outlier_diagnostics(group["thickness"], config)
        data.loc[group.index, "outlier_detection_eligible"] = detection_eligible
        data.loc[group.index, "outlier_lower_bound"] = lower_bound
        data.loc[group.index, "outlier_upper_bound"] = upper_bound
        data.loc[group.index, "outlier_center"] = center
        data.loc[group.index, "outlier_dispersion"] = dispersion
        data.loc[group.index, "outlier_rule_description"] = rule_description
        for index in outlier_indexes:
            data.loc[index, "is_thickness_outlier"] = True
            if exclude_outliers:
                data.loc[index, "is_excluded_from_statistics"] = True
                data.loc[index, "exclusion_reason"] = (
                    "膜厚异常值（按样品实例和处理阶段独立检测）"
                )
            issues.append(
                _issue(
                    data.loc[index],
                    "thickness_outlier",
                    "warning",
                    (
                        "膜厚被异常值规则标记；已从膜厚主统计中排除，原始记录保留"
                        if exclude_outliers
                        else "膜厚被异常值规则标记；主统计仍保留该观测"
                    ),
                    "thickness",
                    data.loc[index, "thickness"],
                )
            )
        fraction_threshold = handling.get("high_exclusion_fraction_warning")
        if fraction_threshold is not None and len(group) > 0:
            configured_fraction = float(fraction_threshold)
            configured_fraction = (
                configured_fraction / 100.0 if configured_fraction > 1 else configured_fraction
            )
            observed_fraction = len(outlier_indexes) / len(group)
            if observed_fraction > configured_fraction:
                issues.append(
                    _issue(
                        group.iloc[0],
                        "high_outlier_fraction",
                        "warning",
                        f"异常值占比 {observed_fraction * 100:.1f}% 高于配置阈值 {configured_fraction * 100:.1f}%",
                        "thickness",
                        observed_fraction * 100,
                        configured_fraction * 100,
                    )
                )
        mse_threshold = config["quality"].get("mse_warning_threshold")
        if mse_threshold is not None:
            for index in group.index[pd.to_numeric(group["mse"], errors="coerce") > float(mse_threshold)]:
                issues.append(
                    _issue(
                        data.loc[index],
                        "high_mse",
                        "warning",
                        f"MSE 高于配置阈值 {mse_threshold}",
                        "mse",
                        data.loc[index, "mse"],
                        mse_threshold,
                    )
                )
        cv_threshold = config["quality"].get("high_cv_threshold_percent")
        thickness_cv = _cv(group["thickness"])
        if cv_threshold is not None and thickness_cv is not None and thickness_cv > float(cv_threshold):
            issues.append(
                _issue(
                    group.iloc[0],
                    "high_within_sample_dispersion",
                    "warning",
                    f"膜厚 CV {thickness_cv:.4f}% 高于配置阈值",
                    "thickness_cv",
                    thickness_cv,
                    cv_threshold,
                )
            )

    tolerance = float(config["quality"].get("average_verification_tolerance", 0.001))
    for _, average_row in average_rows.iterrows():
        dissolved_stage = raw_rows[
            (raw_rows["sample_instance_id"] == average_row["sample_instance_id"])
            & (raw_rows["treatment_stage"] == average_row["treatment_stage"])
        ]["is_dissolved"].any()
        if dissolved_stage:
            continue
        group = raw_rows[
            (raw_rows["sample_instance_id"] == average_row["sample_instance_id"])
            & (raw_rows["treatment_stage"] == average_row["treatment_stage"])
            & (raw_rows["source_row"] < average_row["source_row"])
        ]
        if group.empty:
            issues.append(
                _issue(
                    average_row,
                    "average_row_without_measurements",
                    "warning",
                    "平均值行之前未找到同阶段原始重复测量",
                )
            )
            continue
        for field in NUMERIC_FIELDS:
            computed = pd.to_numeric(group[field], errors="coerce").dropna().mean()
            source_value = average_row.get(field)
            source_formula = average_row.get(f"source_formula_{field}")
            if pd.isna(source_value):
                if source_formula:
                    issues.append(
                        _issue(
                            average_row,
                            "average_formula_without_cached_value",
                            "info",
                            "源平均值为公式但文件未保存公式缓存；已保留公式并按原始测量重算",
                            field,
                            source_formula,
                            computed,
                        )
                    )
                continue
            difference = abs(float(source_value) - float(computed))
            if difference > tolerance:
                issues.append(
                    _issue(
                        average_row,
                        "average_mismatch",
                        "warning",
                        f"表内平均值与程序重算值相差 {difference:.6g}",
                        field,
                        source_value,
                        computed,
                    )
                )

    for sample_instance_id, group in raw_rows.groupby("sample_instance_id"):
        before = group[group["treatment_stage"] == "before_solva"]
        after = group[group["treatment_stage"].astype(str).str.startswith("after_solva_")]
        representative = group.iloc[0]
        ordinary_single_stage = is_single_stage_group(
            group, resolved_single_stage_sheets
        )
        if not before["thickness"].notna().any():
            issues.append(
                _issue(representative, "missing_before", "warning", "缺少处理前数据")
            )
        if not after["thickness"].notna().any() and not ordinary_single_stage:
            issues.append(
                _issue(representative, "missing_after", "warning", "缺少处理后数据")
            )
        if not before.empty and not after.empty:
            for stage, stage_group in after.groupby("treatment_stage"):
                if stage_group["is_dissolved"].any():
                    continue
                if len(before) != len(stage_group):
                    issues.append(
                        _issue(
                            representative,
                            "replicate_count_mismatch",
                            "warning",
                            f"处理前测量数 {len(before)} 与 {stage} 测量数 {len(stage_group)} 不一致",
                            "measurement_count",
                            len(stage_group),
                            len(before),
                        )
                    )

    instance_meta = (
        raw_rows[
            [
                "sample_instance_id",
                "sample_id",
                "source_sheet",
                "process_condition",
                "solva_condition",
            ]
        ]
        .drop_duplicates("sample_instance_id")
        .copy()
    )
    for sample_id, group in instance_meta.groupby("sample_id", dropna=False):
        if len(group) > 1:
            first = raw_rows[raw_rows["sample_instance_id"] == group.iloc[0]["sample_instance_id"]].iloc[0]
            issues.append(
                _issue(
                    first,
                    "duplicate_sample_id",
                    "info",
                    f"同一样品编号存在 {len(group)} 个测试实例；按 sample_instance_id 区分",
                    "sample_id",
                    sample_id,
                )
            )
            conditions = set(
                group[["process_condition", "solva_condition"]]
                .fillna("")
                .astype(str)
                .agg("|".join, axis=1)
            )
            if len(conditions) > 1:
                issues.append(
                    _issue(
                        first,
                        "same_name_different_conditions",
                        "warning",
                        "同名样品在不同测试实例中工艺或 SolvA 条件不同",
                        "process_condition",
                        "；".join(sorted(conditions)),
                    )
                )
    return QualityResult(data, pd.DataFrame(issues))
