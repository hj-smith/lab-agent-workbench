"""Rule-based, non-LLM comparison summary generation."""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd
from .reference_scope import project_metrics
from .display_formatting import display_decimals, format_number


def _sample_label(row: pd.Series) -> str:
    return f"{row.get('sample_name')}（{row.get('sample_source_sheet')}）"


def generate_comparison_summary(
    wide: pd.DataFrame, long: pd.DataFrame, config: Mapping[str, Any]
) -> list[str]:
    if wide.empty:
        return ["未选择参考样或没有候选样，未生成横向比较摘要。"]
    wide = project_metrics(wide, long, config)
    reference_name = wide.iloc[0]["reference_sample_name"]
    lines = [
        f"在当前测试数据和设定容差下，参考样为 {reference_name}；候选样数量为 {len(wide)}。"
    ]
    lower_loss = wide[
        wide["loss_percent_sample"].notna()
        & wide["loss_percent_reference"].notna()
        & (wide["loss_percent_sample"] < wide["loss_percent_reference"])
    ]
    if not lower_loss.empty:
        names = "、".join(_sample_label(row) for _, row in lower_loss.iterrows())
        lines.append(f"膜厚损失率低于参考样的候选样：{names}。")
    comparable_before = wide.dropna(
        subset=["before_thickness_difference"]
    ).copy()
    if not comparable_before.empty:
        closest = comparable_before.loc[
            comparable_before["before_thickness_difference"].abs().idxmin()
        ]
        lines.append(
            f"处理前膜厚最接近参考样的是 {_sample_label(closest)}，绝对差为 "
            f"{format_number(abs(closest['before_thickness_difference']), display_decimals(config))}（{config['analysis'].get('thickness_unit', '原表膜厚单位')}）。"
        )
    optical = wide.dropna(subset=["optical_constant_total_deviation"])
    if not optical.empty:
        best = optical.loc[optical["optical_constant_total_deviation"].idxmin()]
        lines.append(
            f"在同一组 {int(best['optical_expected_count'])} 项参考样可用指标上，N/K 总绝对偏差最小的是 {_sample_label(best)}。"
        )
    excluded = wide[wide["optical_comparable_count"] < wide["optical_expected_count"]]
    if not excluded.empty:
        lines.append(f"{len(excluded)} 个候选样的 N/K 比较项不足，未参与总偏差排名；逐项已有数据仍保留。")
    lower_mse = wide[
        wide["mse_after_sample"].notna()
        & wide["mse_after_reference"].notna()
        & (wide["mse_after_sample"] < wide["mse_after_reference"])
    ]
    if not lower_mse.empty:
        lines.append(
            "处理后平均 MSE 低于参考样的候选样："
            + "、".join(_sample_label(row) for _, row in lower_mse.iterrows())
            + "。"
        )
    for side, label in (("before", "处理前"), ("after", "处理后")):
        repeat = wide[wide[f"thickness_cv_{side}_difference"].notna() & (wide[f"thickness_cv_{side}_difference"] < 0)]
        if not repeat.empty:
            lines.append(f"{label}膜厚 CV 低于参考样的候选样：" + "、".join(_sample_label(row) for _, row in repeat.iterrows()) + "。")
    if not long.empty:
        optical_bad = long[long["metric_name"].str.fullmatch(r"[nk]_\d+_(before|after)_mean", na=False)
                           & long["comparison_class"].str.contains("偏离", na=False)]
        mixed = lower_loss[lower_loss["sample_instance_id"].isin(optical_bad["sample_instance_id"])]
        if not mixed.empty:
            lines.append("膜厚损失率较低但有 N/K 指标超出所设容差的候选样：" + "、".join(_sample_label(row) for _, row in mixed.iterrows()) + "。")
        configured = long[long["tolerance_configured"].fillna(False)]
        passed_ids = []
        for instance_id, group in configured.groupby("sample_instance_id"):
            accepted = group["comparison_class"].isin(["与参考样基本相当", "优于参考样", "明显优于参考样", "符合配置最大值"])
            accepted |= group["absolute_limit_status"].eq("符合配置最大值")
            if accepted.all() and group["evaluation_status"].isin(["evaluated", "evaluated_absolute_only"]).all():
                passed_ids.append(instance_id)
        passed = wide[wide["sample_instance_id"].isin(passed_ids)]
        if not passed.empty:
            lines.append("当前视图中全部已配置关键指标满足工程容差的候选样：" + "、".join(_sample_label(row) for _, row in passed.iterrows()) + "；这是工程容差判断，并非统计学等效。")
    mismatch = wide[wide["condition_match_status"] != "一致"]
    if not mismatch.empty and config["reference_comparison"].get("show_condition_mismatch_warning", True):
        lines.append(
            f"{len(mismatch)} 个候选样存在工艺、模型、测量次数或来源条件不完全一致；这些比较仅供趋势参考，不能直接归因于材料性能。"
        )
    incomplete = wide[wide["comparison_status"] != "可比较"]
    if not incomplete.empty:
        lines.append(
            f"{len(incomplete)} 个候选样的数据阶段不完整，只对现有指标比较，不作完整优劣判断。"
        )
    tolerance_configured = any(
        value is not None
        for value in config["reference_comparison"]["tolerances"].values()
    )
    if not tolerance_configured:
        lines.append("当前未设置正式工程容差；结果展示方向和差异，但不声称统计学等效或可替代参考样。")
    return lines
