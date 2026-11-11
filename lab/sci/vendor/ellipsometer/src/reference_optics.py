"""Readable per-wavelength reference comparison views from metric details."""

from __future__ import annotations

import pandas as pd


def optical_reference_table(wide: pd.DataFrame, details: pd.DataFrame) -> pd.DataFrame:
    """Honor the candidate/stage/metric filtering already applied to details."""
    if wide.empty or details.empty:
        return pd.DataFrame()
    means = details[details["metric_name"].str.fullmatch(
        r"[nk]_(193|248|633)_(before|after)_mean", na=False
    )].copy()
    if means.empty:
        return means
    identity = wide[["sample_instance_id", "sample_name", "process_condition",
                     "solva_condition", "condition_match_status"]].drop_duplicates("sample_instance_id")
    means = means.merge(identity, on="sample_instance_id", how="inner", validate="many_to_one")
    parts = means["metric_name"].str.extract(r"([nk])_(\d+)_(before|after)_mean")
    means["parameter"] = parts[0].str.upper()
    means["wavelength_nm"] = parts[1].astype(int)
    means["stage_label"] = ["处理前 / 常规" if side == "before" else
                            f"处理后（第 {stage.rsplit('_', 1)[1]} 次）"
                            for side, stage in zip(parts[2], means["treatment_stage"])]
    for column in ("sample_std", "reference_std", "sample_count", "reference_count"):
        if column not in means:
            means[column] = None
    return means[[
        "sample_instance_id", "sample_name", "parameter", "wavelength_nm", "stage_label",
        "treatment_stage", "sample_value", "reference_value", "absolute_difference",
        "relative_difference_percent", "ratio_to_reference", "status", "comparison_class",
        "sample_std", "reference_std", "sample_count", "reference_count",
        "condition_match_status", "process_condition", "solva_condition",
        "sample_source_file", "sample_source_sheet", "sample_source_rows",
        "reference_source_file", "reference_source_sheet", "reference_source_rows",
        "warning_message",
    ]]
