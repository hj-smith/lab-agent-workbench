"""Metric direction and editable engineering-tolerance evaluation.

This module is the single source of truth for tolerance-capable metrics.  The
UI, comparator, charts and exporters consume the same registry so a configured
metric cannot silently disappear from the engineering-tolerance matrix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ToleranceMetricSpec:
    metric_name: str
    label: str
    config_key: str
    tolerance_kind: str
    metric_group: str
    treatment_stage: str
    unit: str


def _spec(
    metric_name: str,
    label: str,
    config_key: str,
    tolerance_kind: str,
    metric_group: str,
    treatment_stage: str,
    unit: str,
) -> ToleranceMetricSpec:
    return ToleranceMetricSpec(
        metric_name,
        label,
        config_key,
        tolerance_kind,
        metric_group,
        treatment_stage,
        unit,
    )


TOLERANCE_METRICS: dict[str, ToleranceMetricSpec] = {
    item.metric_name: item
    for item in (
        _spec("before_thickness_mean", "处理前膜厚", "thickness_before_relative_percent", "relative_percent", "膜厚", "before_solva", "原表膜厚单位"),
        _spec("after_thickness_mean", "处理后膜厚", "thickness_after_relative_percent", "relative_percent", "膜厚", "after_solva_1", "原表膜厚单位"),
        _spec("loss_percent", "膜厚损失率", "thickness_loss_percent_pp", "percentage_point", "SolvA耐受性", "before_to_after", "%"),
        _spec("retention_percent", "膜厚保持率", "retention_percent_pp", "percentage_point", "SolvA耐受性", "before_to_after", "%"),
        _spec("n_193_before_mean", "193 nm N（处理前）", "n_193_absolute", "absolute", "光学常数", "before_solva", ""),
        _spec("n_193_after_mean", "193 nm N（处理后）", "n_193_absolute", "absolute", "光学常数", "after_solva_1", ""),
        _spec("k_193_before_mean", "193 nm K（处理前）", "k_193_absolute", "absolute", "光学常数", "before_solva", ""),
        _spec("k_193_after_mean", "193 nm K（处理后）", "k_193_absolute", "absolute", "光学常数", "after_solva_1", ""),
        _spec("n_248_before_mean", "248 nm N（处理前）", "n_248_absolute", "absolute", "光学常数", "before_solva", ""),
        _spec("n_248_after_mean", "248 nm N（处理后）", "n_248_absolute", "absolute", "光学常数", "after_solva_1", ""),
        _spec("k_248_before_mean", "248 nm K（处理前）", "k_248_absolute", "absolute", "光学常数", "before_solva", ""),
        _spec("k_248_after_mean", "248 nm K（处理后）", "k_248_absolute", "absolute", "光学常数", "after_solva_1", ""),
        _spec("n_633_before_mean", "633 nm N（处理前）", "n_633_absolute", "absolute", "光学常数", "before_solva", ""),
        _spec("n_633_after_mean", "633 nm N（处理后）", "n_633_absolute", "absolute", "光学常数", "after_solva_1", ""),
        _spec("k_633_before_mean", "633 nm K（处理前）", "k_633_absolute", "absolute", "光学常数", "before_solva", ""),
        _spec("k_633_after_mean", "633 nm K（处理后）", "k_633_absolute", "absolute", "光学常数", "after_solva_1", ""),
        _spec("before_mse_mean", "处理前 MSE", "mse_maximum", "maximum", "MSE", "before_solva", ""),
        _spec("after_mse_mean", "处理后 MSE", "mse_maximum", "maximum", "MSE", "after_solva_1", ""),
        _spec("before_thickness_cv", "处理前膜厚 CV", "thickness_cv_maximum", "maximum", "膜厚重复性", "before_solva", "%"),
        _spec("after_thickness_cv", "处理后膜厚 CV", "thickness_cv_maximum", "maximum", "膜厚重复性", "after_solva_1", "%"),
    )
}


@dataclass(slots=True)
class ToleranceResult:
    preference_direction: str
    tolerance_lower: float | None
    tolerance_upper: float | None
    comparison_class: str
    is_better: bool | None
    rule_description: str
    absolute_limit_status: str | None = None
    tolerance_config_key: str | None = None
    tolerance_kind: str | None = None
    configured_tolerance_value: float | None = None
    tolerance_configured: bool = False
    evaluated_value: float | None = None
    evaluation_status: str = "not_configured"
    missing_reason: str | None = None


def tolerance_metric_spec(metric_name: str) -> ToleranceMetricSpec | None:
    return TOLERANCE_METRICS.get(metric_name)


def configured_tolerance_specs(config: Mapping[str, Any]) -> list[ToleranceMetricSpec]:
    tolerances = config.get("reference_comparison", {}).get("tolerances", {})
    return [
        spec
        for spec in TOLERANCE_METRICS.values()
        if tolerances.get(spec.config_key) is not None
    ]


def metric_tolerance(metric_name: str, config: Mapping[str, Any]) -> tuple[float | None, str]:
    spec = tolerance_metric_spec(metric_name)
    if spec is None:
        return None, "absolute"
    value = config.get("reference_comparison", {}).get("tolerances", {}).get(spec.config_key)
    return value, spec.tolerance_kind


def metric_preference(metric_name: str, config: Mapping[str, Any]) -> str:
    specific = {"before_thickness_cv": "thickness_cv_before", "after_thickness_cv": "thickness_cv_after"}
    if metric_name in specific:
        return str(config.get("comparison_metrics", {}).get(specific[metric_name], {}).get("preference", "information_only"))
    if metric_name.endswith("_std"):
        return str(config.get("comparison_metrics", {}).get("optical_std", {}).get("preference", "lower"))
    if metric_name.endswith("_cv"):
        return str(config.get("comparison_metrics", {}).get("optical_cv", {}).get("preference", "lower"))
    if metric_name.endswith(("_drift_magnitude", "_relative_drift_magnitude_percent")):
        return str(config.get("comparison_metrics", {}).get("optical_drift_magnitude", {}).get("preference", "lower"))
    if metric_name.endswith(("_absolute_change", "_relative_change_percent")):
        return "information_only"
    aliases = {
        "before_thickness_mean": "thickness_before",
        "after_thickness_mean": "thickness_after",
        "thickness_loss": "thickness_loss",
        "loss_percent": "thickness_loss_percent",
        "retention_percent": "thickness_retention_percent",
        "before_mse_mean": "mse_before",
        "after_mse_mean": "mse_after",
        "before_thickness_cv": "thickness_cv_before",
        "after_thickness_cv": "thickness_cv_after",
    }
    lookup = aliases.get(metric_name)
    if not lookup:
        for optical in ("n_193", "k_193", "n_248", "k_248", "n_633", "k_633"):
            if metric_name.startswith(optical):
                lookup = optical
                break
    entry = config.get("comparison_metrics", {}).get(lookup or "", {})
    return str(entry.get("preference", "information_only"))


def _result(
    preference: str,
    spec: ToleranceMetricSpec | None,
    tolerance: float | None,
    comparison_class: str,
    is_better: bool | None,
    rule: str,
    *,
    lower: float | None = None,
    upper: float | None = None,
    absolute_limit_status: str | None = None,
    evaluated_value: float | None = None,
    status: str,
    missing_reason: str | None = None,
) -> ToleranceResult:
    return ToleranceResult(
        preference,
        lower,
        upper,
        comparison_class,
        is_better,
        rule,
        absolute_limit_status,
        spec.config_key if spec else None,
        spec.tolerance_kind if spec else None,
        None if tolerance is None else float(tolerance),
        tolerance is not None,
        evaluated_value,
        status,
        missing_reason,
    )


def evaluate_tolerance(
    metric_name: str,
    sample_value: float | None,
    reference_value: float | None,
    relative_difference_percent: float | None,
    percentage_point_difference: float | None,
    config: Mapping[str, Any],
) -> ToleranceResult:
    """Evaluate one candidate metric without hiding missing-data causes.

    Maximum limits are absolute candidate limits and therefore remain
    evaluable when the reference metric is missing.
    """

    preference = metric_preference(metric_name, config)
    spec = tolerance_metric_spec(metric_name)
    tolerance, tolerance_kind = metric_tolerance(metric_name, config)
    if tolerance is None:
        is_better = None
        if sample_value is not None and reference_value is not None:
            sample_for_direction = float(sample_value)
            reference_for_direction = float(reference_value)
            if preference == "lower":
                is_better = sample_for_direction < reference_for_direction
            elif preference == "higher":
                is_better = sample_for_direction > reference_for_direction
            elif preference in {"closer_to_reference", "within_tolerance"}:
                is_better = None
        return _result(
            preference, spec, None, "未配置工程容差", is_better,
            f"方向={preference}；仅展示差异，不作容差分级",
            status="not_configured", missing_reason="未配置该指标的工程容差",
        )
    tol = abs(float(tolerance))
    if sample_value is None:
        return _result(
            preference, spec, tol, "数据不足，无法判断", None, "候选样指标缺失",
            status="sample_missing", missing_reason="候选样指标缺失",
        )
    sample = float(sample_value)

    if tolerance_kind == "maximum":
        reference = None if reference_value is None else float(reference_value)
        is_better = (None if reference is None or preference == "information_only" else
                     sample > reference if preference == "higher" else sample < reference)
        within = sample <= tol
        if not within:
            classification = "超过配置最大值"
        elif reference is None or preference == "information_only":
            classification = "符合配置最大值"
        elif is_better:
            classification = "优于参考样"
        elif sample == reference:
            classification = "与参考样基本相当"
        else:
            classification = "轻微偏离参考样"
        status = "evaluated" if reference is not None else "evaluated_absolute_only"
        missing = None if reference is not None else "参考样指标缺失；仅完成绝对上限判断"
        return _result(
            preference, spec, tol, classification, is_better,
            f"样品值不高于配置最大值 {tol}；参考样方向={preference}",
            upper=tol,
            absolute_limit_status="符合配置最大值" if within else "超过配置最大值",
            evaluated_value=sample,
            status=status,
            missing_reason=missing,
        )

    if reference_value is None:
        return _result(
            preference, spec, tol, "数据不足，无法判断", None, "参考样指标缺失",
            status="reference_missing", missing_reason="参考样指标缺失",
        )
    reference = float(reference_value)
    if preference == "information_only":
        return _result(
            preference, spec, tol, "仅供信息", None, "未配置指标理想方向",
            status="information_only", missing_reason="指标方向为 information_only",
        )
    if preference == "lower":
        is_better = sample < reference
    elif preference == "higher":
        is_better = sample > reference
    elif preference in {"closer_to_reference", "within_tolerance"}:
        is_better = None
    else:
        is_better = None

    difference = sample - reference
    if tolerance_kind == "relative_percent":
        if relative_difference_percent is None:
            return _result(
                preference, spec, tol, "数据不足，无法判断", is_better,
                "参考值接近零，不能使用相对百分比容差",
                status="reference_near_zero", missing_reason="参考值接近零，无法计算相对差",
            )
        evaluated = abs(float(relative_difference_percent))
        lower, upper = -tol, tol
    elif tolerance_kind == "percentage_point":
        if percentage_point_difference is None:
            return _result(
                preference, spec, tol, "数据不足，无法判断", is_better,
                "百分数指标缺少百分点差",
                status="percentage_point_missing", missing_reason="无法计算百分点差",
            )
        evaluated = abs(float(percentage_point_difference))
        lower, upper = -tol, tol
    else:
        evaluated = abs(difference)
        lower, upper = reference - tol, reference + tol

    multiplier = config.get("reference_comparison", {}).get("grading", {}).get("clear_deviation_multiplier")
    is_clear = multiplier is not None and tol > 0 and evaluated >= float(multiplier) * tol
    if evaluated <= tol:
        classification = "与参考样基本相当"
    elif (preference == "lower" and difference < 0) or (preference == "higher" and difference > 0):
        classification = "明显优于参考样" if is_clear else "优于参考样"
    elif multiplier is None:
        classification = "偏离参考样（超出容差）"
    else:
        classification = "明显偏离参考样" if is_clear else "轻微偏离参考样"
    return _result(
        preference, spec, tol, classification, is_better,
        f"容差类型={tolerance_kind}；阈值={tol}",
        lower=lower, upper=upper, evaluated_value=evaluated, status="evaluated",
    )
