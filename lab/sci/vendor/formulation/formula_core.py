"""Pure PA formulation mathematics. All masses are g; fractions are 0..1.

No Excel, database, UI, filesystem, or network dependency. See docs/calculation_spec.md.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import json
import math

VERSION = "1.0.0"
FRACTION_TOL = 1e-6
MASS_TOL = 1e-7
DOSE_BASES = ("resin_solid_pct", "total_solid_pct", "total_pct", "total_ppm", "fixed_g", "mmol_per_g_resin")


def normalize_dose_basis(value):
    return value if value in DOSE_BASES else "resin_solid_pct"


def fixed_dose_mass(dose_basis, value, mass, target_solid):
    """Chosen active mass for a non-resin-relative dose, in g."""
    if dose_basis == "total_solid_pct":
        return target_solid*value/100
    if dose_basis == "total_pct":
        return mass*value/100
    if dose_basis == "total_ppm":
        return mass*value*1e-6
    return value


class CalculationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _number(value, label, *, minimum=0, maximum=None):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise CalculationError("INVALID_NUMBER", f"{label}必须为数值") from None
    if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
        raise CalculationError("INVALID_NUMBER", f"{label}超出允许范围")
    return value


def _issue(exc, row_id=None):
    return {"code": exc.code, "message": str(exc), "row_id": row_id}


def _table(registry, key):
    value = registry.get(key, {})
    return {x["id"]: x for x in value} if isinstance(value, list) else value


def molar_mass_for_source(row, formula, registry):
    """Molar mass of the chosen active species; historical sources stay frozen."""
    sid = row.get("source_id", "未选来源")
    material = row.get("frozen_source") or _table(registry, "materials" if row.get("source_type") == "material" else "premixes").get(sid, {})
    value = material.get("molar_mass_g_mol")
    if value in (None, ""):
        config = next((item for item in (formula.get("equivalents") or {}).get("rows", [])
                       if item.get("row_id") == row.get("id")
                       and item.get("source_id", sid) == sid
                       and item.get("source_type", row.get("source_type")) == row.get("source_type")), {})
        value = config.get("molar_mass_g_mol")
    if value in (None, ""):
        raise CalculationError("MOLAR_MASS_REQUIRED", f"{sid}缺少有效成分摩尔质量，无法按 mmol/g 树脂设计")
    return _number(value, f"{sid}摩尔质量", minimum=1e-12)


def resin_dose_coefficient(row, formula, registry):
    """Chosen active grams per gram of resin solids; zero for fixed doses."""
    basis = normalize_dose_basis(row.get("dose_basis"))
    value = _number(row.get("percent", 0), f"{row.get('source_id', '')}设计用量")
    if basis == "resin_solid_pct":
        return value/100
    if basis == "mmol_per_g_resin":
        return value*molar_mass_for_source(row, formula, registry)/1000
    return 0.0


def formula_fingerprint(formula):
    """Detect changed historical design before reusing its aggregate carry-in."""
    fields = ("id", "source_type", "source_id", "parts", "percent", "basis", "frozen_source")
    content = {"total_mass_g": formula.get("total_mass_g"), "solid_fraction": formula.get("solid_fraction")}
    start = formula.get("design_start", "total")
    if start != "total":
        content.update({key: formula.get(key) for key in
                        (("design_start", "resin_solid_g") if start == "resin_solid" else
                         ("design_start", "start_row_id", "start_amount_g"))})
    for group in ("resins", "additives", "solvents"):
        content[group] = [{k: row[k] for k in fields if k in row} for row in formula.get(group, [])]
        for row, content_row in zip(formula.get(group, []), content[group]):
            dose_basis = normalize_dose_basis(row.get("dose_basis"))
            if group == "additives" and dose_basis != "resin_solid_pct":
                content_row["dose_basis"] = dose_basis
            if group == "resins" and row.get("parts_basis", "solid") != "solid":
                content_row["parts_basis"] = row["parts_basis"]
            if group == "solvents" and row.get("solvent_mode") == "fixed_g":
                content_row.update(solvent_mode="fixed_g", fixed_g=row.get("fixed_g"))
            if group == "additives" and dose_basis == "mmol_per_g_resin":
                content_row["molar_basis"] = (formula.get("equivalents") or {}).get("rows", [])
    return sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validate_composition(item, name, *, permit_unknown_solvents=False):
    solid = _number(item.get("solid_fraction"), f"{name}固体比例", maximum=1)
    solvent = _number(item.get("solvent_fraction"), f"{name}溶剂比例", maximum=1)
    volatile = _number(item.get("volatile_fraction", 1-solid-solvent), f"{name}挥发比例", maximum=1)
    if abs(solid+solvent+volatile-1) > FRACTION_TOL:
        raise CalculationError("COMPOSITION_NOT_CLOSED", f"{name}的固体、溶剂、挥发比例合计必须为100%")
    solvents = dict(item.get("solvents") or {})
    if not solvents and item.get("solvent_id") and solvent > 0:
        solvents[str(item["solvent_id"])] = solvent
    solvents = {str(k): _number(v, f"{name}内含溶剂 {k}", maximum=1) for k, v in solvents.items()}
    unknown = solvent > FRACTION_TOL and not solvents
    if abs(sum(solvents.values())-solvent) > FRACTION_TOL and not (unknown and permit_unknown_solvents):
        raise CalculationError("SOLVENT_ATTRIBUTION_MISSING", f"{name}的内含溶剂归属缺失或与溶剂比例不一致")
    return {"name": item.get("name", name), "solid_fraction": solid, "solvent_fraction": solvent,
            "volatile_fraction": volatile, "solvents": solvents, "composition_known": not unknown}


def resolve_source(source_type, source_id, registry, _stack=()):
    """Resolve a material or premix to fractions and per-species solvent fractions.

    Raises CalculationError for unknown IDs, incomplete recipes, cycles, bad closure.
    Premix component fractions are explicit; never normalized silently.
    """
    if source_type not in ("material", "premix"):
        raise CalculationError("INVALID_SOURCE_TYPE", f"{source_id}来源类型应为原料或预混液")
    identity = (source_type, source_id)
    if identity in _stack:
        raise CalculationError("PREMIX_CYCLE", f"预混液 {source_id}存在循环引用")
    item = _table(registry, "materials" if source_type == "material" else "premixes").get(source_id)
    if item is None:
        raise CalculationError("UNKNOWN_SOURCE", f"找不到{('原料' if source_type == 'material' else '预混液')}编号：{source_id}")
    if source_type == "material":
        return _validate_composition(item, source_id)
    components = item.get("components", [])
    if not components:
        raise CalculationError("PREMIX_COMPONENTS_MISSING", f"预混液 {source_id}尚未填写组成明细")
    solid = solvent = volatile = total = 0.0
    species = defaultdict(float)
    for c in components:
        fraction = _number(c.get("fraction"), f"{source_id}组成比例", maximum=1)
        part = resolve_source(c.get("source_type", "material"), c["source_id"], registry, (*_stack, identity))
        total += fraction
        solid += fraction*part["solid_fraction"]
        solvent += fraction*part["solvent_fraction"]
        volatile += fraction*part["volatile_fraction"]
        for key, value in part["solvents"].items():
            species[key] += fraction*value
    if abs(total-1) > FRACTION_TOL:
        raise CalculationError("PREMIX_NOT_CLOSED", f"预混液 {source_id}组成配比合计为 {total:.4%}，应为100%")
    return _validate_composition({"name": item.get("name", source_id), "solid_fraction": solid,
        "solvent_fraction": solvent, "volatile_fraction": volatile, "solvents": species}, source_id)


def calculate_formula(formula, registry):
    """Return {ok, errors, warnings, lines, solvent_balance, totals, checks}.

    Invalid input has ok=False and explicit diagnostics. Do not issue work orders
    unless ok is True. Negative solvent additions are preserved in diagnostics,
    never emitted as negative weigh quantities. Zero quantity source lines remain.
    """
    result = {"engine_version": VERSION, "ok": False, "errors": [], "warnings": [],
              "lines": [], "solvent_balance": [], "totals": {}, "checks": {}}
    errors, warnings = result["errors"], result["warnings"]
    try:
        design_start = formula.get("design_start", "total")
        if design_start not in ("total", "resin_solid", "source_amount"):
            raise CalculationError("INVALID_DESIGN_START", "起算方式应为总量、树脂固体量或来源可用量")
        mass = _number(formula.get("total_mass_g"), "总配样量", minimum=1e-12) if design_start == "total" else 1.0
        solid_fraction = _number(formula.get("solid_fraction"), "目标固含", minimum=1e-12, maximum=1)
        min_weigh = _number(formula.get("min_weigh_g", 0), "最小称量提示")
    except CalculationError as exc:
        errors.append(_issue(exc))
        return result

    resolved, solvent_rows, seen_ids = [], [], set()
    uses_frozen = False
    for category, group in (("resin", "resins"), ("additive", "additives"), ("solvent", "solvents")):
        for index, row in enumerate(formula.get(group, [])):
            rid = row.get("id") or f"{category}-{index+1}"
            try:
                if rid in seen_ids:
                    raise CalculationError("DUPLICATE_ROW_ID", "存在重复的投料行，请重新添加该行。")
                seen_ids.add(rid)
                sid = row.get("source_id", "")
                if not sid:
                    raise CalculationError("SOURCE_REQUIRED", "请选择来源编号")
                order = _number(row.get("order") or 0, f"{sid}投料顺序")
                field = "percent" if category == "additive" else "fixed_g" if category == "solvent" and row.get("solvent_mode") == "fixed_g" else "parts"
                value = _number(row.get(field, 0), f"{sid}设计用量")
                stype = "material" if category == "solvent" else row.get("source_type")
                if row.get("frozen_source"):
                    uses_frozen = True
                    comp = _validate_composition(row["frozen_source"], sid, permit_unknown_solvents=True)
                else:
                    comp = resolve_source(stype, sid, registry)
                if category == "solvent":
                    if comp["solvent_fraction"] < 1-FRACTION_TOL or comp["solvents"] != {sid: comp["solvent_fraction"]}:
                        raise CalculationError("NOT_PURE_SOLVENT", f"{sid}不是归属到自身的纯溶剂")
                    solvent_rows.append((row, rid, comp, value, order))
                    continue
                basis = "solid" if category == "resin" else row.get("basis", "solid")
                if basis not in ("solid", "volatile"):
                    raise CalculationError("INVALID_BASIS", f"{sid}有效份基准应为固体或易挥发")
                active = comp[basis+"_fraction"]
                if active <= 0:
                    raise CalculationError("ZERO_EFFECTIVE_FRACTION", f"{sid}的所选有效份为0，无法计算来源量")
                if category == "additive" and normalize_dose_basis(row.get("dose_basis")) == "mmol_per_g_resin":
                    molar_mass_for_source(row, formula, registry)
                resolved.append((row, rid, category, comp, value, active, order))
            except CalculationError as exc:
                errors.append(_issue(exc, rid))
    resin_weights = {rid: value*(comp["solid_fraction"] if row.get("parts_basis", "solid") == "solution" else 1)
                     for row, rid, cat, comp, value, _, _ in resolved if cat == "resin"}
    solution_parts = any(row.get("parts_basis") == "solution" for row, _, cat, *_ in resolved if cat == "resin")
    resin_parts = sum(resin_weights.values())
    if solution_parts and resin_parts > 0:
        resolved = [(row, rid, cat, comp, resin_weights[rid]/resin_parts*100 if cat == "resin" else value, active, order)
                    for row, rid, cat, comp, value, active, order in resolved]
        resin_parts = 100.0
    solvent_parts = sum(value for row, _, _, value, _ in solvent_rows if row.get("solvent_mode", "ratio") != "fixed_g")
    fixed_solvent = sum(value for row, _, _, value, _ in solvent_rows if row.get("solvent_mode") == "fixed_g")
    ratio_rows = any(row.get("solvent_mode", "ratio") != "fixed_g" for row, *_ in solvent_rows)
    if abs(resin_parts-100) > 0.001:
        errors.append(_issue(CalculationError("RESIN_PARTS_SUM", f"树脂固体份数应合计100，当前为 {resin_parts:g}")))
    if ratio_rows and solvent_parts <= 0:
        errors.append(_issue(CalculationError("SOLVENT_PARTS_SUM", "溶剂相对份数合计必须大于0")))
    solvent_ids = [x[0]["source_id"] for x in solvent_rows]
    if len(set(solvent_ids)) != len(solvent_ids):
        errors.append(_issue(CalculationError("DUPLICATE_SOLVENT", "溶剂设计中同一溶剂只能出现一次")))
    if errors:
        return result

    relative = {rid: (value/100 if cat == "resin" else resin_dose_coefficient(row, formula, registry))
                for row, rid, cat, _, value, _, _ in resolved
                if cat == "resin" or normalize_dose_basis(row.get("dose_basis")) in ("resin_solid_pct", "mmol_per_g_resin")}
    factor = sum(relative[rid]*(1 if cat == "resin" else comp["solid_fraction"]/active)
                 for _, rid, cat, comp, _, active, _ in resolved if rid in relative)

    def evaluate(total):
        target = total*solid_fraction
        fixed = {rid: fixed_dose_mass(normalize_dose_basis(row.get("dose_basis")), value, total, target)
                 for row, rid, cat, _, value, _, _ in resolved if cat == "additive" and rid not in relative}
        fixed_solid = sum(fixed[rid]*comp["solid_fraction"]/active
                          for _, rid, _, comp, _, active, _ in resolved if rid in fixed)
        resin = (target-fixed_solid)/factor
        return resin, fixed

    if design_start != "total":
        try:
            wanted = _number(formula.get("resin_solid_g" if design_start == "resin_solid" else "start_amount_g"),
                             "起算质量", minimum=1e-12)
            start_id = formula.get("start_row_id")
            if design_start == "source_amount" and start_id not in seen_ids:
                raise CalculationError("START_ROW_NOT_FOUND", "找不到起算来源行，请重新选择")

            def start_value(total):
                resin, fixed = evaluate(total)
                if design_start == "resin_solid":
                    return resin
                amounts = {rid: (fixed[rid] if rid in fixed else resin*relative[rid])/active
                           for _, rid, _, _, _, active, _ in resolved}
                if start_id in amounts:
                    return amounts[start_id]
                volatile = sum(amounts[rid]*comp["volatile_fraction"] for _, rid, _, comp, *_ in resolved)
                pool = total*(1-solid_fraction)-volatile
                row, _, _, parts, _ = next(item for item in solvent_rows if item[1] == start_id)
                target = parts if row.get("solvent_mode") == "fixed_g" else (pool-fixed_solvent)*parts/solvent_parts
                carried = sum(amounts[rid]*comp["solvents"].get(row["source_id"], 0)
                              for _, rid, _, comp, *_ in resolved)
                return target-carried

            low, high = start_value(0.0), start_value(1.0)
            slope = high-low
            if abs(slope) <= 1e-12:
                raise CalculationError("START_CONDITION_UNSOLVABLE", "起算行用量为0或不随总量变化，无法由此确定总量")
            mass = (wanted-low)/slope
            if not math.isfinite(mass) or mass <= 0:
                raise CalculationError("START_CONDITION_UNSOLVABLE", "起算条件无法满足，推算总量必须大于0")
        except CalculationError as exc:
            errors.append(_issue(exc, formula.get("start_row_id") if design_start == "source_amount" else None))
            return result
    target_solid = mass*solid_fraction
    resin_solid, fixed_masses = evaluate(mass)
    if resin_solid <= 0:
        errors.append(_issue(CalculationError("ADDITIVE_SOLID_EXCEEDS_TARGET",
            "按总体系 / 固定量设计的添加剂固体已超过目标固体量")))
        return result
    species = defaultdict(float)
    unknown_species = False
    for row, rid, category, comp, value, active, order in resolved:
        amount = fixed_masses[rid]/active if rid in fixed_masses else (resin_solid*value/100/active if category == "resin" or normalize_dose_basis(row.get("dose_basis")) == "resin_solid_pct" else resin_solid*relative[rid]/active)
        line = {"id": rid, "category": category, "source_type": row["source_type"],
                "source_id": row["source_id"], "name": comp["name"], "planned_mass_g": amount,
                "solid_mass_g": amount*comp["solid_fraction"], "solvent_mass_g": amount*comp["solvent_fraction"],
                "volatile_mass_g": amount*comp["volatile_fraction"], "order": order, "note": row.get("note", ""),
                "source_composition": deepcopy(comp), "frozen": bool(row.get("frozen_source"))}
        line["effective_mass_g"] = line["solid_mass_g"]+line["volatile_mass_g"]
        if category == "additive":
            line.update(dose_basis=normalize_dose_basis(row.get("dose_basis")), dose_value=value)
        result["lines"].append(line)
        for sid, share in comp["solvents"].items():
            species[sid] += amount*share
        unknown_species |= not comp["composition_known"]
        if 0 < amount < min_weigh:
            warnings.append(_issue(CalculationError("BELOW_WEIGH_LIMIT", f"{row['source_id']}计划量 {amount:.6f} g 低于设置的称量提示值"), rid))
    source_total = sum(x["planned_mass_g"] for x in result["lines"])
    solid_total = sum(x["solid_mass_g"] for x in result["lines"])
    solvent_carried = sum(x["solvent_mass_g"] for x in result["lines"])
    volatile_total = sum(x["volatile_mass_g"] for x in result["lines"])
    solvent_pool = mass-target_solid-volatile_total
    if solvent_pool < -MASS_TOL:
        errors.append(_issue(CalculationError("NEGATIVE_SOLVENT_POOL", "易挥发组分过多，目标溶剂池为负")))
    if uses_frozen:
        warnings.append(_issue(CalculationError("FROZEN_SOURCE_PROPERTIES", "本次使用历史冻结有效份，未用当前原料库替换历史定义")))
    if unknown_species:
        carry = formula.get("frozen_solvent_carry")
        if carry is None:
            errors.append(_issue(CalculationError("HISTORICAL_COMPOSITION_MISSING", "历史预混液缺少各溶剂组成；可核算来源量，不能重新设计溶剂或生成预混备料")))
        elif formula.get("frozen_design_fingerprint") != formula_fingerprint(formula):
            errors.append(_issue(CalculationError("FROZEN_DESIGN_CHANGED", "历史设计已改变，冻结的溶剂带入量不再适用；请补齐历史组成或明确使用当前定义重新设计")))
        else:
            try:
                species = {k: _number(v, f"历史带入溶剂{k}") for k, v in carry.items()}
                warnings.append(_issue(CalculationError("HISTORICAL_REPLAY_ONLY", "按历史冻结的各溶剂带入量复核原设计；不代表已恢复历史预混明细")))
            except CalculationError as exc:
                errors.append(_issue(exc))
    unallocated = solvent_carried-sum(species.get(sid, 0) for sid in solvent_ids)
    if abs(unallocated) > MASS_TOL:
        errors.append(_issue(CalculationError("UNALLOCATED_SOLVENT", f"有 {unallocated:.6f} g 带入溶剂未被溶剂设计正确归属")))
    addition_total = 0.0
    remaining_pool = solvent_pool-fixed_solvent
    if remaining_pool < -MASS_TOL:
        errors.append(_issue(CalculationError("FIXED_SOLVENT_EXCEEDS_POOL", "固定溶剂总量超过目标溶剂池")))
    if not ratio_rows and abs(remaining_pool) > MASS_TOL:
        errors.append(_issue(CalculationError("SOLVENT_RATIO_REQUIRED", "没有比例溶剂行，剩余溶剂池必须为0")))
    for row, rid, comp, parts, order in solvent_rows:
        sid = row["source_id"]
        fixed = row.get("solvent_mode") == "fixed_g"
        fraction = parts/solvent_parts if not fixed and solvent_parts else 0.0
        target = parts if fixed else max(0, remaining_pool)*parts/solvent_parts
        carried = species.get(sid, 0)
        addition = target-carried
        if addition < -MASS_TOL:
            errors.append(_issue(CalculationError("CARRIED_EXCEEDS_FIXED" if fixed else "SOLVENT_OVERFLOW",
                f"{sid}已带入量超过固定量 {abs(addition):.6f} g" if fixed else f"{sid}已带入量超过目标 {abs(addition):.6f} g，请调整预混浓度或溶剂比例"), rid))
        weigh = max(0, addition)
        addition_total += weigh
        result["solvent_balance"].append({"source_id": sid, "name": comp["name"], "fraction": fraction,
            "target_mass_g": target, "carried_mass_g": carried, "addition_mass_g": addition,
            "final_mass_g": carried+weigh, "deviation_mass_g": carried+weigh-target})
        result["lines"].append({"id": rid, "category": "solvent", "source_type": "material", "source_id": sid,
            "name": comp["name"], "planned_mass_g": weigh, "solid_mass_g": 0.0, "solvent_mass_g": weigh,
            "volatile_mass_g": 0.0, "effective_mass_g": 0.0, "order": order, "note": row.get("note", ""), "source_composition": deepcopy(comp),
            "frozen": bool(row.get("frozen_source"))})
        if 0 < weigh < min_weigh:
            warnings.append(_issue(CalculationError("BELOW_WEIGH_LIMIT", f"{sid}计划量 {weigh:.6f} g 低于设置的称量提示值"), rid))
    result["lines"].sort(key=lambda x: x["order"] if x["order"] > 0 else 900)
    for i, line in enumerate(result["lines"], 1):
        line["sequence"] = i
    actual_total = source_total+addition_total
    result["totals"] = {"design_start": design_start, "derived_total_mass_g": mass,
        "target_mass_g": mass, "actual_mass_g": actual_total, "target_solid_mass_g": target_solid,
        "solid_mass_g": solid_total, "resin_solid_mass_g": resin_solid, "solid_fraction": solid_total/actual_total,
        "solvent_mass_g": solvent_carried+addition_total, "volatile_mass_g": volatile_total,
        "effective_mass_g": solid_total+volatile_total,
        "solvent_pool_g": solvent_pool, "source_mass_g": source_total, "solvent_addition_g": addition_total,
        "solid_coefficient": factor, "resin_parts": resin_parts, "solvent_parts": solvent_parts}
    result["totals"].update({"target_total_mass_g": mass, "total_mass_g": actual_total,
        "mass_error_g": actual_total-mass, "solid_error_g": solid_total-target_solid,
        "solvent_error_g": solvent_carried+addition_total-solvent_pool})
    result["checks"] = {"mass_delta_g": actual_total-mass, "solid_delta_g": solid_total-target_solid,
        "solvent_delta_g": solvent_carried+addition_total-solvent_pool, "unallocated_solvent_g": unallocated,
        "historical_replay": unknown_species, "composition_complete": not unknown_species}
    for key in ("mass_delta_g", "solid_delta_g", "solvent_delta_g"):
        if abs(result["checks"][key]) > MASS_TOL:
            errors.append(_issue(CalculationError("BALANCE_MISMATCH", f"{dict(mass_delta_g='总质量',solid_delta_g='固体质量',solvent_delta_g='溶剂质量')[key]}不平衡 {result['checks'][key]:.6f} g")))
    result["ok"] = not errors
    return result


def plan_premix_preparation(calculations, registry, settings=None):
    """Aggregate premix demand from successful calculations and expand components.

    settings keyed by premix id: stock_g, extra_fraction, extra_g, min_prepare_g,
    step_g. Need=max(0,demand*(1+extra_fraction)+extra_g-stock), then minimum and
    step rounding when Need>0. Input may be a calculation or {batch_id,result}.
    Frozen/history source definitions are never replaced by current components.
    """
    settings = settings or {}
    errors, demand = [], defaultdict(float)
    for item in calculations:
        calc = item.get("result", item)
        if not calc.get("ok"):
            errors.append(_issue(CalculationError("INVALID_BATCH", "所选工单计算未通过，不能生成备料")))
            continue
        for line in calc.get("lines", []):
            if line.get("source_type") == "premix":
                if line.get("frozen"):
                    errors.append(_issue(CalculationError("HISTORICAL_PREMIX_RECIPE_MISSING", f"{line['source_id']}使用历史冻结定义，不能用当前组成生成备料"), line["id"]))
                demand[line["source_id"]] += line["planned_mass_g"]
    plans, raw_totals = [], defaultdict(float)
    if errors:
        return {"ok": False, "errors": errors, "plans": [], "raw_materials": []}
    for sid, quantity in demand.items():
        try:
            resolve_source("premix", sid, registry)
            recipe = _table(registry, "premixes")[sid]
            opt = settings.get(sid, {})
            stock = _number(opt.get("stock_g", 0), f"{sid}可用库存")
            extra = _number(opt.get("extra_fraction", 0), f"{sid}余量比例")
            extra_g = _number(opt.get("extra_g", 0), f"{sid}固定余量")
            minimum = _number(opt.get("min_prepare_g", 0), f"{sid}最低配制量")
            step = _number(opt.get("step_g", 0), f"{sid}取整步长")
            need = max(0, quantity*(1+extra)+extra_g-stock)
            prepare = max(need, minimum) if need > MASS_TOL else 0.0
            if step and prepare:
                prepare = math.ceil(prepare/step-1e-12)*step
            components = []
            for i, component in enumerate(recipe["components"], 1):
                ctype = component.get("source_type", "material")
                cid = component["source_id"]
                part = resolve_source(ctype, cid, registry)
                amount = prepare*float(component["fraction"])
                components.append({"sequence": i, "source_type": ctype, "source_id": cid,
                    "name": part["name"], "fraction": float(component["fraction"]), "planned_mass_g": amount})
                raw_totals[(ctype, cid)] += amount
            plans.append({"source_id": sid, "name": recipe.get("name", sid), "demand_mass_g": quantity,
                "stock_mass_g": stock, "extra_fraction": extra, "extra_mass_g": extra_g,
                "net_need_mass_g": need, "prepare_mass_g": prepare,
                "remaining_mass_g": stock+prepare-quantity, "components": components})
        except CalculationError as exc:
            errors.append(_issue(exc, sid))
    return {"ok": not errors, "errors": errors, "plans": plans,
            "raw_materials": [{"source_type": k[0], "source_id": k[1], "planned_mass_g": v} for k, v in raw_totals.items()]}


def expand_series(base_formula, rows):
    """Create independent formula inputs from explicit matrix rows.

    A row has code, total_mass_g/solid_fraction (optional), and overrides:
    [{group:'resins'|'additives'|'solvents', id:'stable-line-id', field:'percent',
    value:...}]. No positional matching; base input remains unchanged.
    """
    result = []
    allowed = {"resins": {"source_type", "source_id", "parts", "parts_basis", "order", "note"},
               "additives": {"source_type", "source_id", "percent", "basis", "dose_basis", "order", "note"},
               "solvents": {"source_id", "parts", "solvent_mode", "fixed_g", "order", "note"}}
    for row in rows:
        item = deepcopy(base_formula)
        for key in ("code", "total_mass_g", "solid_fraction", "note", "design_start", "resin_solid_g", "start_row_id", "start_amount_g"):
            if key in row:
                item[key] = row[key]
        for override in row.get("overrides", []):
            group, field = override["group"], override["field"]
            if group not in allowed or field not in allowed[group]:
                raise CalculationError("INVALID_SERIES_OVERRIDE", "实验项目不支持修改此项条件")
            targets = [x for x in item.get(group, []) if x.get("id") == override["id"]]
            if len(targets) != 1:
                raise CalculationError("SERIES_LINE_NOT_FOUND", "实验项目找不到对应的唯一投料行")
            targets[0][field] = override["value"]
        result.append(item)
    return result
