"""Optional, explicitly identified species calculations for formulation additives.

The formulation engine remains the authority for source weigh masses. This module
does not identify molecules from product names and does not modify a formula.
Fractions use 0..1, masses g, molar masses g/mol, equivalent weights g/eq.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import json
import math

from .formula_core import (CalculationError, calculate_formula, resolve_source,
                       normalize_dose_basis, fixed_dose_mass, resin_dose_coefficient)


def _issue(code, message, row_id=None):
    return {"code": code, "message": message, "row_id": row_id}


def _number(value, label, *, positive=False, maximum=None):
    if isinstance(value, bool):
        raise ValueError(f"{label}必须为数值")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须为数值") from None
    if (not math.isfinite(value) or value < 0 or (positive and value == 0)
            or (maximum is not None and value > maximum)):
        raise ValueError(f"{label}超出允许范围")
    return value


def _table(registry, name):
    values = registry.get(name, {})
    return {x["id"]: x for x in values} if isinstance(values, list) else values


def _leaves(source_type, source_id, registry, multiplier=1, stack=()):
    """Expand product fractions, preserving leaf identity (not inferred species)."""
    identity = (source_type, source_id)
    if identity in stack:
        raise CalculationError("PREMIX_CYCLE", f"预混液 {source_id}存在循环引用")
    item = _table(registry, "materials" if source_type == "material" else "premixes").get(source_id)
    if item is None:
        raise CalculationError("UNKNOWN_SOURCE", f"找不到来源：{source_id}")
    if source_type == "material":
        return [(source_id, multiplier, resolve_source(source_type, source_id, registry))]
    leaves = []
    # Validation of closure and references happens before expansion.
    resolve_source(source_type, source_id, registry)
    for component in item.get("components", []):
        leaves.extend(_leaves(component.get("source_type", "material"), component["source_id"],
                              registry, multiplier * float(component["fraction"]), (*stack, identity)))
    return leaves


def equivalent_options(formula, registry):
    """Selectable source fractions and identities, without any guessed MW.

    A species option is a candidate mass slice, not proof of chemical identity.
    All configurations require species_confirmed and a user-specified species_name.
    Historical aggregate compositions deliberately do not use live components.
    """
    rows, errors = [], []
    for index, row in enumerate(formula.get("additives", []), 1):
        rid = row.get("id") or f"additive-{index}"
        stype, sid, basis = row.get("source_type"), row.get("source_id"), row.get("basis", "solid")
        options, name, definitions = [], sid or "未选来源", []
        try:
            if row.get("frozen_source"):
                comp = deepcopy(row["frozen_source"])
                name = comp.get("name", sid)
                definitions = [{"frozen_source": comp}]
                for part in ("solid", "volatile"):
                    fraction = float(comp.get(part + "_fraction", 0))
                    if fraction > 0:
                        options.append({"id": f"frozen:{part}", "component_id": sid,
                            "component_basis": part, "name": name,
                            "label": f"{sid} · 历史总{'固体' if part == 'solid' else '易挥发份'}（须确认具体物种）",
                            "active_fraction": fraction, "requires_species_confirmation": True})
            else:
                comp = resolve_source(stype, sid, registry)
                name = comp["name"]
                combined = defaultdict(float)
                leaf_comps = {}
                for leaf_id, share, leaf_comp in _leaves(stype, sid, registry):
                    combined[leaf_id] += share
                    leaf_comps[leaf_id] = leaf_comp
                for leaf_id in sorted(combined):
                    share, leaf_comp = combined[leaf_id], leaf_comps[leaf_id]
                    definitions.append({"id": leaf_id, "fraction": share, "composition": leaf_comp})
                    for part in ("solid", "volatile", "whole"):
                        slice_fraction = 1 if part == "whole" else leaf_comp[part + "_fraction"]
                        fraction = share * slice_fraction
                        if fraction <= 0:
                            continue
                        suffix = {"solid": "固体部分", "volatile": "易挥发部分", "whole": "整个原料产品"}[part]
                        options.append({"id": f"material:{leaf_id}:{part}", "component_id": leaf_id,
                            "component_basis": part, "name": leaf_comp["name"],
                            "label": f"{leaf_id} · {leaf_comp['name']} · {suffix}",
                            "active_fraction": fraction, "requires_species_confirmation": True})
            signature_data = {"source_type": stype, "source_id": sid, "basis": basis,
                              "composition": comp, "components": definitions}
            signature = sha256(json.dumps(signature_data, sort_keys=True, ensure_ascii=False,
                                separators=(",", ":")).encode("utf-8")).hexdigest()
        except (CalculationError, ValueError, TypeError) as exc:
            errors.append(_issue(getattr(exc, "code", "SOURCE_UNAVAILABLE"), str(exc), rid))
            signature = None
        candidates = [x for x in options if x["component_basis"] == basis]
        rows.append({"row_id": rid, "source_type": stype, "source_id": sid, "name": name,
                     "basis": basis, "percent": row.get("percent", 0),
                     "dose_basis": normalize_dose_basis(row.get("dose_basis")),
                     "dose_value": row.get("percent", 0),
                     "full_label": f"{sid or '未选来源'} · {name} · {'固体' if basis == 'solid' else '易挥发'}基准",
                     "source_signature": signature, "species_options": options,
                     "recommended_species_id": candidates[0]["id"] if len(candidates) == 1 else None,
                     "frozen": bool(row.get("frozen_source"))})
    return {"ok": not errors, "errors": errors, "rows": rows,
            "notice": "组分列表提供质量来源，不能证明该部分是单一化学物质。请确认计算对象并填写其摩尔质量；固体中有多种物质时，应选择具体组分或填写该物种占整个来源的质量分数。"}


def _calculate_row(option, line, config, mode):
    rid = option["row_id"]
    output = {key: deepcopy(option[key]) for key in
              ("row_id", "source_type", "source_id", "name", "basis", "dose_basis", "dose_value", "full_label", "source_signature")}
    output.update({"configured": bool(config), "planned_mass_g": line.get("planned_mass_g") if line else None,
                   "active_fraction": None, "active_mass_g": None, "effective_mass_g": None,
                   "purity_applied": False, "purity_fraction": 1.0,
                   "moles": None, "equivalents": None, "relative_moles": None,
                   "relative_equivalents": None, "ratio": None,
                   "errors": [], "warnings": [], "configuration": deepcopy(config)})
    def error(code, message):
        output["errors"].append(_issue(code, message, rid))
    if not config:
        output["warnings"].append(_issue("CONFIGURATION_MISSING", "尚未设置计算物种和摩尔质量/当量质量", rid))
        return output
    if not line:
        error("SOURCE_MASS_UNAVAILABLE", "配方未能计算此来源的投料质量")
        return output
    if (config.get("source_type") != option["source_type"] or config.get("source_id") != option["source_id"]
            or not option["source_signature"] or config.get("source_signature") != option["source_signature"]):
        error("SOURCE_CHANGED", "来源、有效份基准或组成已改变，请重新确认计算物种和参数")
        return output
    if config.get("species_confirmed") is not True or not str(config.get("species_name") or "").strip():
        error("SPECIES_CONFIRMATION_REQUIRED", "请确认所选质量份对应的具体化学物种，并填写物种名称")
        return output
    output["species_name"] = str(config["species_name"]).strip()
    try:
        fraction_mode = config.get("fraction_mode", "component")
        if fraction_mode == "component":
            candidate = next((x for x in option["species_options"] if x["id"] == config.get("species_id")), None)
            if candidate is None:
                error("SPECIES_SELECTION_REQUIRED", "请选择来源中的计算组分")
                return output
            fraction = candidate["active_fraction"]
            output["species_label"] = candidate["label"]
        elif fraction_mode == "explicit":
            fraction = _number(config.get("active_fraction"), "物种占整个来源的质量分数", positive=True, maximum=1)
            output["species_label"] = "用户填写的物种质量分数（占整个来源）"
        else:
            error("INVALID_FRACTION_MODE", "质量份模式应为组成选择或手动填写")
            return output
        purity_applied = config.get("purity_applied") is True
        purity = _number(config.get("purity_fraction"), "额外纯度校正", positive=True, maximum=1) if purity_applied else 1.0
        if not purity_applied and config.get("purity_fraction") not in (None, "", 1, 1.0, "1"):
            output["warnings"].append(_issue("PURITY_NOT_APPLIED", "未启用额外纯度校正，填写的纯度未参与计算", rid))
        active_mass = line["planned_mass_g"] * fraction
        mass = active_mass * purity
        output.update({"active_fraction": fraction, "active_mass_g": active_mass, "effective_mass_g": mass,
                       "purity_applied": purity_applied, "purity_fraction": purity})
        mw_value = config.get("molar_mass_g_mol")
        mw = _number(mw_value, "摩尔质量", positive=True) if mw_value not in (None, "") else None
        fw_value = config.get("functionality")
        functionality = _number(fw_value, "参与计算的官能团数", positive=True) if fw_value not in (None, "") else None
        ew_value = config.get("equivalent_weight_g_eq")
        ew = _number(ew_value, "当量质量", positive=True) if ew_value not in (None, "") else None
        output.update({"molar_mass_g_mol": mw, "functionality": functionality, "equivalent_weight_g_eq": ew})
        if mw is not None:
            output["moles"] = mass / mw
        if ew is not None:
            output["equivalents"] = mass / ew
            output["functional_method"] = "equivalent_weight"
            if mw is not None and functionality is not None and not math.isclose(ew, mw / functionality, rel_tol=1e-6):
                error("CONFLICTING_EQUIVALENT_INPUTS", "当量质量与摩尔质量/官能团数不一致，请保留一种定义或修正参数")
                output["equivalents"] = None
        elif mw is not None and functionality is not None:
            output["equivalents"] = output["moles"] * functionality
            output["functional_method"] = "functionality"
        if mode == "molar" and mw is None:
            error("MOLAR_MASS_REQUIRED", "按摩尔比计算需要填写该物种的摩尔质量（g/mol）")
        elif mode == "functional" and output["equivalents"] is None and not output["errors"]:
            error("EQUIVALENT_INPUT_REQUIRED", "按官能团计算需要当量质量，或摩尔质量和参与反应的官能团数")
    except ValueError as exc:
        error("INVALID_EQUIVALENT_NUMBER", str(exc))
    return output


def calculate_equivalents(formula, registry, calculation=None):
    """Return optional ratios; an incomplete setup never changes core validity.

    ``ok`` concerns configured equivalent rows and the chosen reference only;
    ``calculation_ok`` is the independent formulation-engine result.
    """
    config = formula.get("equivalents") or {}
    mode = config.get("mode", "molar")
    calc = calculate_formula(formula, registry) if calculation is None else calculation
    options = equivalent_options(formula, registry)
    result = {"ok": False, "enabled": bool(config), "mode": mode,
              "reference_row_id": config.get("reference_row_id"),
              "reference_moles": None, "reference_equivalents": None,
              "rows": [], "errors": [], "warnings": [], "configuration": deepcopy(config),
              "calculation_ok": calc["ok"], "calculation_errors": deepcopy(calc["errors"])}
    if mode not in ("molar", "functional"):
        result["errors"].append(_issue("INVALID_EQUIVALENT_MODE", "当量模式应为摩尔比或官能团比"))
        return result
    configs = {}
    for row in config.get("rows", []):
        rid = row.get("row_id")
        if rid in configs:
            result["errors"].append(_issue("DUPLICATE_EQUIVALENT_ROW", "当量设置的配方行重复", rid))
        configs[rid] = row
    lines = {x["id"]: x for x in calc.get("lines", [])}
    for option in options["rows"]:
        row = _calculate_row(option, lines.get(option["row_id"]), configs.get(option["row_id"]), mode)
        result["rows"].append(row)
        result["errors"].extend(row["errors"])
        result["warnings"].extend(row["warnings"])
    active_ids = {x["row_id"] for x in options["rows"]}
    for removed in set(configs) - active_ids:
        result["warnings"].append(_issue("REMOVED_EQUIVALENT_ROW", "当量设置对应的添加剂行已删除，该设置未参与计算", removed))
    if not config:
        return result
    reference = next((x for x in result["rows"] if x["row_id"] == result["reference_row_id"]), None)
    if reference is None:
        result["errors"].append(_issue("REFERENCE_REQUIRED", "请选择作为1当量基准的添加剂"))
        return result
    selected_value = "moles" if mode == "molar" else "equivalents"
    if reference["errors"] or reference[selected_value] is None:
        result["errors"].append(_issue("REFERENCE_INVALID", "参照添加剂的当量资料不完整，请先补齐", reference["row_id"]))
        return result
    if reference[selected_value] <= 0:
        result["errors"].append(_issue("ZERO_REFERENCE", "参照添加剂有效用量为0，相对当量无定义", reference["row_id"]))
        return result
    result["reference_moles"] = reference["moles"]
    result["reference_equivalents"] = reference["equivalents"]
    result["reference_label"] = reference["full_label"]
    for row in result["rows"]:
        if row["errors"]:
            continue
        if reference["moles"] and row["moles"] is not None:
            row["relative_moles"] = row["moles"] / reference["moles"]
        if reference["equivalents"] and row["equivalents"] is not None:
            row["relative_equivalents"] = row["equivalents"] / reference["equivalents"]
        row["ratio"] = row["relative_moles"] if mode == "molar" else row["relative_equivalents"]
    result["ok"] = not result["errors"] and calc["ok"]
    return result


def _solve_equivalent_doses(formula, registry, calculation, constraints):
    """Substitute ordered species ratios, then solve the solid and start balances.

    Source masses are affine triples (resin solids, total mass, constant).
    Keeping total mass unknown lets source-amount and resin-solid starts retain
    their constraint when one or several equivalent doses change.
    """
    lines = {line['id']: line for line in calculation['lines']}
    resin_base = calculation['totals']['resin_solid_mass_g']
    solids = float(formula['solid_fraction'])
    masses, additive_rows = {}, {}
    for index, row in enumerate(formula.get('resins', []), 1):
        rid = row.get('id') or f'resin-{index}'
        masses[rid] = (lines[rid]['planned_mass_g']/resin_base, 0.0, 0.0)
    for index, row in enumerate(formula.get('additives', []), 1):
        rid = row.get('id') or f'additive-{index}'
        additive_rows[rid] = row
        fraction = lines[rid]['source_composition'][row.get('basis', 'solid') + '_fraction']
        basis = normalize_dose_basis(row.get('dose_basis'))
        relative = basis in ('resin_solid_pct', 'mmol_per_g_resin')
        value = float(row.get('percent', 0))
        masses[rid] = ((resin_dose_coefficient(row, formula, registry)/fraction, 0.0, 0.0)
                       if relative else (0.0, 0.0, value/fraction) if basis == 'fixed_g' else
                       (0.0, fixed_dose_mass(basis, value, 1.0, solids)/fraction, 0.0))
    for target, reference, multiplier in constraints:
        masses[target] = tuple(multiplier*value for value in masses[reference])
    solid = [0.0, -solids, 0.0]
    pool = [0.0, 1-solids, 0.0]
    carried = defaultdict(lambda: [0.0, 0.0, 0.0])
    for rid, coefficients in masses.items():
        comp = lines[rid]['source_composition']
        for i, value in enumerate(coefficients):
            solid[i] += value*comp['solid_fraction']
            pool[i] -= value*comp['volatile_fraction']
            for sid, fraction in comp['solvents'].items():
                carried[sid][i] += value*fraction
    solvents = formula.get('solvents', [])
    fixed_total = sum(float(row.get('fixed_g', 0)) for row in solvents if row.get('solvent_mode') == 'fixed_g')
    ratio_total = sum(float(row.get('parts', 0)) for row in solvents if row.get('solvent_mode') != 'fixed_g')
    for index, row in enumerate(solvents, 1):
        rid = row.get('id') or f'solvent-{index}'
        if row.get('solvent_mode') == 'fixed_g':
            final = (0.0, 0.0, float(row.get('fixed_g', 0)))
        else:
            share = float(row.get('parts', 0))/ratio_total if ratio_total else 0.0
            final = (pool[0]*share, pool[1]*share, (pool[2]-fixed_total)*share)
        masses[rid] = tuple(value-carried[row['source_id']][i] for i, value in enumerate(final))
    start = formula.get('design_start', 'total')
    if start == 'resin_solid':
        constraint = (1.0, 0.0, -float(formula['resin_solid_g']))
    elif start == 'source_amount':
        source = masses[formula['start_row_id']]
        constraint = (source[0], source[1], source[2]-float(formula['start_amount_g']))
    else:
        constraint = (0.0, 1.0, -float(formula['total_mass_g']))
    a, b, c = solid
    d, e, f = constraint
    determinant = a*e-b*d
    if not math.isfinite(determinant) or determinant == 0:
        raise ValueError('目标当量与设计起点不能确定唯一配样量。')
    resin_solid = (b*f-c*e)/determinant
    mass = (c*d-a*f)/determinant
    if not math.isfinite(resin_solid) or not math.isfinite(mass) or resin_solid <= 0 or mass <= 0:
        raise ValueError('目标当量与设计起点要求的树脂固体量或总量必须大于0。')
    for target, reference, _ in constraints:
        if sum(value*scale for value, scale in zip(masses[reference], (resin_solid, mass, 1))) <= 0:
            raise ValueError('当量参照添加剂的本实验点用量必须大于0：' + reference)
        row = additive_rows[target]
        basis = normalize_dose_basis(row.get('dose_basis'))
        fraction = lines[target]['source_composition'][row.get('basis', 'solid') + '_fraction']
        unit_row = dict(row, percent=1.0)
        unit_mass = (resin_solid*resin_dose_coefficient(unit_row, formula, registry)
                     if basis in ('resin_solid_pct', 'mmol_per_g_resin') else
                     fixed_dose_mass(basis, 1.0, mass, mass*solids))
        active_mass = sum(value*scale for value, scale in zip(masses[target], (resin_solid, mass, 1)))*fraction
        if unit_mass <= 0 or active_mass < 0:
            raise ValueError('目标当量不能转换为所选添加剂用量基准。')
        row['percent'] = active_mass/unit_mass


def suggest_equivalent_percent(formula, registry, target_row_id, target_ratio, mode=None):
    """Solve the species ratio with the formula's solid and design-start balances."""
    proposal = {"ok": False, "target_row_id": target_row_id, "target_ratio": target_ratio,
                "suggested_percent": None, "validated_ratio": None,
                "calculation_ok": False, "calculation_errors": [], "errors": [], "warnings": []}
    try:
        ratio = _number(target_ratio, "目标相对当量")
    except ValueError as exc:
        proposal["errors"].append(_issue("INVALID_TARGET_RATIO", str(exc), target_row_id))
        return proposal
    candidate = deepcopy(formula)
    config = candidate.setdefault("equivalents", {})
    if mode is not None:
        config["mode"] = mode
    if target_row_id == config.get("reference_row_id"):
        proposal["errors"].append(_issue("TARGET_IS_REFERENCE", "参照项固定为1当量，请选择另一添加剂调整", target_row_id))
        return proposal
    target = next((x for i, x in enumerate(candidate.get("additives", []), 1)
                   if (x.get("id") or f"additive-{i}") == target_row_id), None)
    if target is None:
        proposal["errors"].append(_issue("TARGET_NOT_FOUND", "目标添加剂行不存在", target_row_id))
        return proposal
    base_calc = calculate_formula(candidate, registry)
    if not base_calc["ok"]:
        proposal["errors"].append(_issue("FORMULA_INVALID", "请先修正配方质量平衡，再反算目标当量"))
        proposal["calculation_errors"] = base_calc["errors"]
        return proposal
    options = equivalent_options(candidate, registry)
    configs = {row.get("row_id"): row for row in config.get("rows", [])}
    reference_id = config.get("reference_row_id")
    selected = "moles" if config.get("mode", "molar") == "molar" else "equivalents"
    if config.get("mode", "molar") not in ("molar", "functional"):
        proposal["errors"].append(_issue("INVALID_EQUIVALENT_MODE", "当量模式应为摩尔比或官能团比"))
        return proposal
    unit_rows = {}
    for rid in (target_row_id, reference_id):
        option = next((row for row in options["rows"] if row["row_id"] == rid), None)
        if option is not None:
            unit_rows[rid] = _calculate_row(option, {"planned_mass_g": 1.0}, configs.get(rid), config.get("mode", "molar"))
        unit = unit_rows.get(rid)
        if not unit or unit["errors"] or not unit[selected] or unit[selected] <= 0:
            proposal["errors"] = (unit["errors"] if unit else []) or [_issue(
                "TARGET_PARAMETERS_REQUIRED", "请补齐参照与目标添加剂的当量设置", rid)]
            return proposal
    multiplier = ratio * unit_rows[reference_id][selected] / unit_rows[target_row_id][selected]
    try:
        _solve_equivalent_doses(candidate, registry, base_calc,
                                [(target_row_id, reference_id, multiplier)])
    except (CalculationError, ValueError) as exc:
        proposal['errors'].append(_issue(getattr(exc, 'code', 'TARGET_FORMULA_INVALID'), str(exc), target_row_id))
        return proposal
    percent = target['percent']
    target_basis = normalize_dose_basis(target.get('dose_basis'))
    validation = calculate_formula(candidate, registry)
    final = calculate_equivalents(candidate, registry, validation)
    target_final = next(x for x in final["rows"] if x["row_id"] == target_row_id)
    actual_ratio = target_final["ratio"]
    proposal.update({"suggested_percent": percent, "validated_ratio": actual_ratio,
                     "calculation_ok": validation["ok"], "calculation_errors": validation["errors"],
                     "warnings": validation["warnings"], "mode": config.get("mode", "molar"),
                     "planned_mass_g": target_final["planned_mass_g"]})
    proposal.update(dose_basis=target_basis, dose_value=percent)
    if actual_ratio is None or not math.isclose(actual_ratio, ratio, rel_tol=1e-9, abs_tol=1e-12):
        proposal["errors"].append(_issue("TARGET_RATIO_NOT_VERIFIED", "目标相对当量复核未通过", target_row_id))
    if not validation["ok"]:
        proposal["errors"].append(_issue("TARGET_FORMULA_INVALID", "建议比例会造成配方质量平衡不通过，不能应用；请调整溶剂/固含等设计条件", target_row_id))
    proposal["ok"] = not proposal["errors"] and validation["ok"]
    return proposal
