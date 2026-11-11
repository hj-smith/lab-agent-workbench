"""迁移时新写的小文件：原 batch_management.py（863 行，工单库服务）不搬，这里只留 excel_io 导出用到的 plan_fingerprint，原文照抄。"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def plan_fingerprint(batch):
    """Identify a complete frozen plan, excluding actuals, status and edit times."""
    value = {key: batch.get(key) for key in ("batch_code", "formula_code", "planned_date")}
    value["formula"] = deepcopy(batch.get("formula_data") or {})
    value["registry_snapshot"] = deepcopy(batch.get("registry_snapshot") or {})
    value["lines"] = [{key: line.get(key) for key in
                      ("id", "source_type", "source_id", "planned_mass_g", "source_composition", "order", "note")}
                      for line in (batch.get("result") or {}).get("lines", [])]
    def canonical(item):
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, dict):
            return {key: canonical(part) for key, part in item.items()}
        if isinstance(item, list):
            return [canonical(part) for part in item]
        return item
    return sha256(_json(canonical(value)).encode("utf-8")).hexdigest()


