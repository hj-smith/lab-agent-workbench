"""Configuration loading, validation and snapshot helpers."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Mapping

import yaml


class ConfigError(ValueError):
    """Raised when a required configuration item is missing or invalid."""


def deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, Mapping)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: str | Path, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    if overrides:
        config = deep_merge(config, overrides)
    validate_config(config)
    config["_config_path"] = str(config_path.resolve())
    return config


def validate_config(config: Mapping[str, Any]) -> None:
    required = ("config_version", "headers", "stage_rules", "sample_parsing", "quality")
    missing = [key for key in required if key not in config]
    if missing:
        raise ConfigError(f"配置缺少必要部分: {', '.join(missing)}")
    decimals = config.get("visualization", {}).get("decimal_places", 4)
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 8:
        raise ConfigError("visualization.decimal_places 必须为 0～8 的整数")
    if not config["stage_rules"].get("after_markers"):
        raise ConfigError("stage_rules.after_markers 不能为空")
    if not config["stage_rules"].get("average_markers"):
        raise ConfigError("stage_rules.average_markers 不能为空")
    for key in ("after_markers", "average_markers", "dissolution_markers"):
        patterns = config["stage_rules"].get(key, [])
        if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
            raise ConfigError(f"stage_rules.{key} 必须为正则字符串列表（可用空列表关闭溶解规则）")
        for pattern in config["stage_rules"].get(key, []):
            try:
                re.compile(str(pattern))
            except re.error as exc:
                raise ConfigError(f"stage_rules.{key} 包含无效正则：{pattern}（{exc}）") from exc
    reference = config.get("reference_comparison", {})
    if reference.get("default_mode", "full") not in {"full", "before", "after", "resistance"}:
        raise ConfigError("reference_comparison.default_mode 必须为 full/before/after/resistance")
    if not re.fullmatch(r"after_solva_[1-9]\d*", str(reference.get("after_stage", "after_solva_1"))):
        raise ConfigError("reference_comparison.after_stage 必须指定有效阶段，例如 after_solva_1")
    multiplier = reference.get("grading", {}).get("clear_deviation_multiplier")
    if multiplier is not None and (isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)) or multiplier <= 1):
        raise ConfigError("明显偏离倍数必须大于 1 或为 null")
    fields = reference.get("completeness_fields", ["thickness", "n_193", "k_193", "n_248", "k_248", "n_633", "k_633"])
    if not fields or len(fields) != len(set(fields)) or not set(fields) <= {"thickness", "mse", "n_193", "k_193", "n_248", "k_248", "n_633", "k_633"}:
        raise ConfigError("completeness_fields 必须是非空且不重复的标准数值字段列表")
    tolerances = reference.get("tolerances", {})
    for key, value in tolerances.items():
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"reference_comparison.tolerances.{key} 必须为数值或 null")
        if float(value) < 0:
            raise ConfigError(f"reference_comparison.tolerances.{key} 不能为负数")
    near_zero = reference.get("near_zero_threshold", 1.0e-8)
    if isinstance(near_zero, bool) or not isinstance(near_zero, (int, float)) or float(near_zero) < 0:
        raise ConfigError("reference_comparison.near_zero_threshold 必须为非负数")
    allowed_preferences = {
        "higher", "lower", "closer_to_reference", "within_tolerance", "information_only"
    }
    for metric, entry in config.get("comparison_metrics", {}).items():
        preference = entry.get("preference") if isinstance(entry, Mapping) else None
        if preference not in allowed_preferences:
            raise ConfigError(f"comparison_metrics.{metric}.preference 无效：{preference}")
    score_metrics = reference.get("score", {}).get("metrics", {})
    for metric, entry in score_metrics.items():
        weight = entry.get("weight") if isinstance(entry, Mapping) else None
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or float(weight) < 0:
            raise ConfigError(f"reference_comparison.score.metrics.{metric}.weight 必须为非负数")


def config_snapshot(config: Mapping[str, Any]) -> str:
    public = {key: value for key, value in config.items() if not key.startswith("_")}
    return yaml.safe_dump(public, allow_unicode=True, sort_keys=False)


def config_fingerprint(config: Mapping[str, Any]) -> str:
    import hashlib

    public = {key: value for key, value in config.items() if not key.startswith("_")}
    payload = json.dumps(public, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
