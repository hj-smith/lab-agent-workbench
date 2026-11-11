"""Premix suggestions for weighings that are too small to weigh accurately.

For every additive line with 0 < planned mass < limit, list (a) existing premixes
whose only non-solvent component is that source and (b) new premixes at 10 / 1 /
0.1 % in the main solvent. Each option is checked by really recalculating the
design with the row swapped, so the weigh mass and the balance are exact.
"""
from __future__ import annotations

from copy import deepcopy

from .formula_core import CalculationError, calculate_formula, resolve_source

DEFAULT_LIMIT_G = 0.05
CONCENTRATIONS_PCT = (10, 1, 0.1)


def _pct_text(pct):
    return f"{pct:g}%"


def _is_pure_solvent(registry, source_type, source_id):
    try:
        comp = resolve_source(source_type, source_id, registry)
    except CalculationError:
        return False
    return comp["solvent_fraction"] >= 1-1e-6


def _table(registry, key):
    value = registry.get(key, {})
    return value if isinstance(value, dict) else {x["id"]: x for x in value}


def _main_solvent(formula, preferred=None):
    if preferred:
        return preferred
    rows = [r for r in formula.get("solvents", []) if r.get("source_id")]
    if not rows:
        return ""
    return max(rows, key=lambda r: float(r.get("parts") or 0))["source_id"]


def _swapped_line(formula, registry, row_id, source_type, source_id):
    """Recalculate with the row swapped; return (planned mass, problem text)."""
    trial = deepcopy(formula)
    for row in trial.get("additives", []):
        if row.get("id") == row_id:
            row["source_type"], row["source_id"] = source_type, source_id
    result = calculate_formula(trial, registry)
    line = next((x for x in result["lines"] if x["id"] == row_id), None)
    problem = "" if result["ok"] else (result["errors"][0]["message"] if result["errors"] else "计算未通过")
    return (line["planned_mass_g"] if line else None), problem


def _existing_premixes(registry, source_id):
    found = []
    for pid, premix in _table(registry, "premixes").items():
        active = [c for c in premix.get("components", []) if not _is_pure_solvent(registry, c.get("source_type", "material"), c["source_id"])]
        if len(active) == 1 and active[0].get("source_type", "material") == "material" and active[0]["source_id"] == source_id:
            found.append((pid, premix, float(active[0]["fraction"])))
    return found


def _new_id(registry, source_id, pct, solvent):
    premixes = _table(registry, "premixes")
    base = f"{source_id}-{_pct_text(pct)}"
    if base not in premixes:
        return base
    candidate = f"{base}-{solvent}"
    n = 2
    while candidate in premixes:
        candidate = f"{base}-{solvent}-{n}"
        n += 1
    return candidate


def suggest_weighing(formula, registry, min_weigh_g=None, solvent=None):
    limit = float(min_weigh_g) if min_weigh_g not in (None, "") else float(formula.get("min_weigh_g") or DEFAULT_LIMIT_G)
    solvent = _main_solvent(formula, solvent)
    base = calculate_formula(formula, registry)
    out = {"ok": base["ok"], "limit_g": limit, "solvent": solvent, "lines": [], "errors": base["errors"]}
    if not base["ok"]:
        return out
    premixes = _table(registry, "premixes")
    additive_ids = {r.get("id") for r in formula.get("additives", [])}
    for line in base["lines"]:
        if line["id"] not in additive_ids or not 0 < line["planned_mass_g"] < limit:
            continue
        item = {"row_id": line["id"], "source_type": line["source_type"], "source_id": line["source_id"],
                "planned_mass_g": line["planned_mass_g"], "existing": [], "proposals": []}
        out["lines"].append(item)
        if line["source_type"] != "material":
            continue
        source_id = line["source_id"]
        known = set()
        for pid, premix, fraction in _existing_premixes(registry, source_id):
            mass, problem = _swapped_line(formula, registry, line["id"], "premix", pid)
            known.add(round(fraction, 9))
            item["existing"].append({"id": pid, "name": premix.get("name", ""), "fraction": fraction,
                "weigh_mass_g": mass, "ok": not problem, "problem": problem, "enough": mass is not None and mass >= limit})
        if not solvent or not _is_pure_solvent(registry, "material", solvent):
            continue
        recommended = False
        for pct in CONCENTRATIONS_PCT:
            fraction = pct/100
            pid = _new_id(registry, source_id, pct, solvent)
            trial_registry = deepcopy(registry)
            trial_registry["premixes"] = {**premixes, pid: {"id": pid, "components": [
                {"source_type": "material", "source_id": source_id, "fraction": fraction},
                {"source_type": "material", "source_id": solvent, "fraction": 1-fraction}]}}
            mass, problem = _swapped_line(formula, trial_registry, line["id"], "premix", pid)
            enough = mass is not None and mass >= limit
            rec = enough and not problem and not recommended
            recommended = recommended or rec
            item["proposals"].append({"id": pid, "percent": pct, "fraction": fraction, "solvent": solvent,
                "name": f"{registry_name(registry, source_id)} {_pct_text(pct)} 预混液",
                "weigh_mass_g": mass, "ok": not problem, "problem": problem, "enough": enough,
                "recommended": rec, "duplicate": round(fraction, 9) in known})
    return out


def registry_name(registry, source_id):
    material = _table(registry, "materials").get(source_id, {})
    return material.get("name") or source_id
