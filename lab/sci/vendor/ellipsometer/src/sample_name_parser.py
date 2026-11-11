"""Deterministic extraction of sample identity and process metadata."""

from __future__ import annotations

import re
from typing import Any, Mapping


def normalize_multiline(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.strip() for line in text.split("\n") if line.strip())


def _first_regex(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return (match.group(1) if match.groups() else match.group(0)).strip()
    return None


def _condition_lines(lines: list[str], contains_solva: bool) -> list[str]:
    return [
        line
        for line in lines
        if ("solva" in line.lower()) is contains_solva
        and not re.match(r"^\s*模型\s*[:：]", line, flags=re.IGNORECASE)
    ]


def parse_sample_name(
    raw_value: Any,
    config: Mapping[str, Any],
    default_model: str | None = None,
) -> dict[str, Any]:
    sample_raw = normalize_multiline(raw_value)
    rules = config["sample_parsing"]
    first_line = sample_raw.split("\n", 1)[0] if sample_raw else ""
    sample_id = _first_regex(rules["sample_id_patterns"], first_line)
    if not sample_id:
        sample_id = _first_regex(rules["sample_id_patterns"], sample_raw)
    if not sample_id:
        sample_id = sample_raw.split("\n", 1)[0].strip() or "UNKNOWN"
    lines = sample_raw.split("\n") if sample_raw else []
    model = _first_regex(rules.get("model_patterns", []), sample_raw) or default_model
    solva_lines = _condition_lines(lines, True)
    non_solva_lines = _condition_lines(lines, False)
    description_lines = []
    condition_candidates = []
    for line in non_solva_lines:
        if line == sample_id or line.startswith(sample_id):
            remainder = line[len(sample_id) :].strip(" ,，;；()（）")
            if remainder:
                description_lines.append(remainder)
                condition_candidates.append(remainder)
        else:
            description_lines.append(line)
            condition_candidates.append(line)
    process_condition = "；".join(
        line
        for line in condition_candidates
        if re.search(r"℃|\b\d+(?:\.\d+)?\s*(?:s|min|h)\b|烘|固化", line, re.I)
    )
    solva_condition = "；".join(solva_lines)
    process_text = "；".join(condition_candidates)
    bake_temperature = _first_regex([r"(\d+(?:\.\d+)?)\s*℃"], process_text)
    bake_time_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(s|min|h)\b", process_text, flags=re.IGNORECASE
    )
    bake_time = (
        f"{bake_time_match.group(1)}{bake_time_match.group(2)}"
        if bake_time_match
        else None
    )
    solva_time_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(s|min|h)\b", solva_condition, flags=re.IGNORECASE
    )
    solva_time = (
        f"{solva_time_match.group(1)}{solva_time_match.group(2)}"
        if solva_time_match
        else None
    )
    solva_method = None
    for keyword in rules.get("solva_method_keywords", []):
        if keyword.lower() in solva_condition.lower():
            solva_method = keyword
            break
    return {
        "sample_raw": sample_raw,
        "sample_id": sample_id,
        "sample_description": "；".join(description_lines),
        "model": model,
        "process_condition": process_condition or None,
        "solva_condition": solva_condition or None,
        "bake_temperature": float(bake_temperature) if bake_temperature else None,
        "bake_time": bake_time,
        "curing_condition": process_condition or None,
        "solva_time": solva_time,
        "solva_method": solva_method,
    }


def extract_default_model(worksheet: Any, header_row: int, config: Mapping[str, Any]) -> str | None:
    patterns = config["sample_parsing"].get("model_patterns", [])
    for row in worksheet.iter_rows(min_row=1, max_row=max(1, header_row)):
        for cell in row:
            if cell.value is None:
                continue
            found = _first_regex(patterns, str(cell.value))
            if found:
                return found
    return None
