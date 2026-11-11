"""Deterministic classification of SolvA-comparison and single-stage sheets."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd


SINGLE_STAGE_STATUS = "常规单阶段"
SolvA_COMPLETE_STATUS = "完整"
MISSING_BEFORE_STATUS = "缺少处理前数据"
MISSING_AFTER_STATUS = "缺少处理后数据"


def _sheet_key(frame: pd.DataFrame) -> pd.Series:
    file_identity = _file_key(frame)
    sheets = frame.get(
        "source_sheet", pd.Series("", index=frame.index, dtype=str)
    ).fillna("").astype(str)
    return file_identity + "::" + sheets


def _file_key(frame: pd.DataFrame) -> pd.Series:
    return frame.get(
        "source_file_hash",
        frame.get("source_file", pd.Series("", index=frame.index, dtype=str)),
    ).fillna("").astype(str)


def sheets_with_after_measurements(measurements: pd.DataFrame) -> set[str]:
    """Return sheets containing at least one explicit configured SolvA-after stage."""
    if measurements.empty:
        return set()
    stages = measurements.get(
        "treatment_stage", pd.Series(index=measurements.index, dtype=str)
    ).astype(str)
    return set(_sheet_key(measurements.loc[stages.str.startswith("after_solva_")]))


def single_stage_sheets(
    measurements: pd.DataFrame,
    config: Mapping[str, object],
) -> set[str]:
    """Resolve sheets whose data should be treated as ordinary one-stage tests.

    Modes:
    - ``auto_by_workbook``: only a workbook with no explicit SolvA-after marker
      anywhere is treated as ordinary single-stage data. This preserves true
      missing-after samples in a SolvA experiment that spans several sheets.
    - ``auto_by_sheet``: optional narrower behavior for mixed-purpose workbooks.
    - ``force_single_stage``: every sheet is treated as single-stage unless a
      sample itself contains an explicit after stage.
    - ``expect_solva``: before-only samples remain missing-after warnings.
    """
    if measurements.empty or "source_sheet" not in measurements:
        return set()
    analysis = config.get("analysis", {})
    mode = (
        str(analysis.get("single_stage_mode", "auto_by_workbook"))
        if isinstance(analysis, Mapping)
        else "auto_by_workbook"
    )
    all_sheets = set(_sheet_key(measurements))
    if mode == "force_single_stage":
        return all_sheets
    if mode == "expect_solva":
        return set()
    after_sheets = sheets_with_after_measurements(measurements)
    if mode == "auto_by_sheet":
        return all_sheets - after_sheets
    resolved: set[str] = set()
    keyed = measurements.copy()
    keyed["_workbook_key"] = _file_key(keyed)
    for _, workbook_rows in keyed.groupby("_workbook_key", dropna=False):
        workbook_sheets = set(_sheet_key(workbook_rows))
        workbook_after = sheets_with_after_measurements(workbook_rows)
        if not workbook_after:
            resolved.update(workbook_sheets)
    return resolved


def is_single_stage_group(
    group: pd.DataFrame,
    resolved_single_stage_sheets: set[str],
) -> bool:
    """Return True for a before-only sample on a resolved single-stage sheet."""
    if group.empty:
        return False
    stages = group["treatment_stage"].astype(str)
    has_before = stages.eq("before_solva").any()
    has_after = stages.str.startswith("after_solva_").any()
    sheet = str(_sheet_key(group.iloc[[0]]).iloc[0])
    return has_before and not has_after and sheet in resolved_single_stage_sheets
