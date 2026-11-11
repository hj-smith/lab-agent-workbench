"""Optional transparent engineering screening score."""

from __future__ import annotations

import json
from typing import Any, Mapping

import pandas as pd


def _component_score(
    sample: float | None, reference: float | None, preference: str
) -> float | None:
    if sample is None or pd.isna(sample):
        return None
    if preference == "higher":
        if reference is None or pd.isna(reference) or reference <= 0:
            return None
        return 100.0 if sample >= reference else max(0.0, sample / reference * 100)
    if preference == "lower":
        if reference is None or pd.isna(reference) or reference < 0:
            return None
        if sample <= reference:
            return 100.0
        if sample <= 0:
            return 100.0
        return max(0.0, reference / sample * 100)
    return None


def apply_optional_score(
    wide: pd.DataFrame,
    reference_row: Mapping[str, Any],
    config: Mapping[str, Any],
) -> pd.DataFrame:
    result = wide.copy()
    score_config = config["reference_comparison"]["score"]
    if not score_config.get("enabled", False) or result.empty:
        result["optional_composite_score"] = None
        result["optional_rank"] = None
        result["score_components"] = None
        result["score_missing_metrics"] = None
        return result
    mapping = {
        "thickness_loss_percent": ("loss_percent_sample", "loss_percent_reference"),
        "thickness_retention_percent": (
            "retention_percent_sample",
            "retention_percent_reference",
        ),
        "optical_constant_stability": ("optical_drift_sample", "optical_drift_reference"),
        "mse_after": ("mse_after_sample", "mse_after_reference"),
        "thickness_cv_after": (
            "thickness_cv_after_sample",
            "thickness_cv_after_reference",
        ),
        "data_completeness": ("data_completeness", None),
    }
    scores: list[float | None] = []
    components_json: list[str] = []
    missing_json: list[str] = []
    for _, row in result.iterrows():
        weighted = 0.0
        participating_weight = 0.0
        components: dict[str, Any] = {}
        missing: list[str] = []
        for metric, settings in score_config.get("metrics", {}).items():
            sample_col, reference_col = mapping.get(metric, (None, None))
            if not sample_col:
                missing.append(metric)
                continue
            sample_value = row.get(sample_col)
            if metric == "data_completeness":
                component = (
                    float(sample_value)
                    if sample_value is not None and not pd.isna(sample_value)
                    else None
                )
                rule = "数据完整性百分数"
            else:
                reference_value = row.get(reference_col) if reference_col else None
                component = _component_score(
                    sample_value, reference_value, str(settings.get("preference"))
                )
                rule = f"方向={settings.get('preference')}，以参考样为100分上限"
                if metric == "optical_constant_stability":
                    rule += "；使用同一组光学参数的 SolvA 前后绝对漂移之和（非参考样均值偏差）"
            weight = float(settings.get("weight", 0))
            if component is None:
                missing.append(metric)
                continue
            weighted += component * weight
            participating_weight += weight
            components[metric] = {
                "score": component,
                "weight": weight,
                "rule": rule,
            }
        final = weighted / participating_weight if participating_weight > 0 else None
        scores.append(final)
        components_json.append(json.dumps(components, ensure_ascii=False))
        missing_json.append("；".join(missing))
    result["optional_composite_score"] = scores
    result["score_components"] = components_json
    result["score_missing_metrics"] = missing_json
    result["optional_rank"] = (
        result["optional_composite_score"].rank(method="min", ascending=False)
    )
    return result
