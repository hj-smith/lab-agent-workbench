# 迁移说明：从 formulation-173a/formulation_workbench/experiment_service.py 原样复制前 457 行（到 failed_calculation 为止），
# 后面读写旧库的 generate_experiment_design 等函数没搬。除这两行注释外没有改动。
"""Apply experimental variables to real formulations and validate DOE matrices.

Category labels are explicitly user-defined. Only an explicit source mapping
changes a source; ordinary process categories are saved as descriptive factors.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
import re

from .doe import generate_design
from .formula_core import (calculate_formula, resolve_source, CalculationError, FRACTION_TOL,
                       normalize_dose_basis)


_PARTS = re.compile(r"^(resins|additives|solvents)\.(\d+)\.(parts|percent)$")
_FIELDS = {"resins": "parts", "additives": "percent", "solvents": "parts"}
_SINGULAR = {"resins": "resin", "additives": "additive", "solvents": "solvent"}


def _number(value, label, minimum=0):
    if isinstance(value, bool):
        raise ValueError(f"{label}必须为有限数值。")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须为有限数值。") from None
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{label}必须为不小于 {minimum:g} 的有限数值。")
    return value


def _row_id(group, row, index):
    return row.get("id") or f"{_SINGULAR[group]}-{index+1}"


def _by_id(base, group, row_id):
    if group not in _FIELDS:
        raise ValueError("原料变量或平衡组分的配方分组无效。")
    found = [(i, row) for i, row in enumerate(base.get(group, [])) if _row_id(group, row, i) == row_id]
    if len(found) != 1:
        raise ValueError("原料变量或平衡组分的配方行不存在或标识重复，请重新选择。")
    return found[0]


def _numeric_target(base, variable):
    key = variable.get("key", "")
    if key == 'total_mass_g' and base.get('design_start', 'total') != 'total':
        raise ValueError('当前设计起点会自动求总量，不能同时把总量设为独立实验变量。')
    if key in ("total_mass_g", "solid_fraction") or key.startswith('equivalent:'):
        return None
    match = _PARTS.fullmatch(key)
    if not match:
        raise ValueError("不支持的实验变量：" + str(key))
    group, index, field = match.groups()
    index = int(index)
    if field != _FIELDS[group] or index >= len(base.get(group, [])):
        raise ValueError("实验变量与基准配方不一致，请重新选择变量。")
    if group == 'solvents' and base[group][index].get('solvent_mode') == 'fixed_g':
        raise ValueError('固定克数溶剂不能作为溶剂份数变量，请选择按比例分配的溶剂行。')
    rid = _row_id(group, base[group][index], index)
    if variable.get("row_id") and variable["row_id"] != rid:
        raise ValueError("实验变量对应的配方行已改变，请重新选择变量。")
    return group, index, field, rid


def _mapped_source(value, label):
    """Legacy strings mean raw materials; typed mappings may select premixes."""
    source_type, source_id = ('material', value) if isinstance(value, str) else (
        (value.get('source_type'), value.get('source_id')) if isinstance(value, dict) else (None, None))
    if source_type not in ('material', 'premix'):
        raise ValueError(label + '的来源类型必须为原料或预混液。')
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError(label + '的来源编号必须是非空文本。')
    return source_type, source_id


def _equivalent_spec(base, variable):
    spec = variable.get('equivalent')
    if not isinstance(spec, dict):
        raise ValueError('当量变量必须指定目标添加剂、参照添加剂和计算模式。')
    target, reference, mode = (spec.get(key) for key in ('target_row_id', 'reference_row_id', 'mode'))
    if variable.get('key') != 'equivalent:' + str(target) or not target or not reference:
        raise ValueError('当量变量标识与目标添加剂不一致，请重新选择。')
    _by_id(base, 'additives', target)
    _by_id(base, 'additives', reference)
    if target == reference:
        raise ValueError('当量目标与参照必须是不同的添加剂。')
    if mode not in ('molar', 'functional'):
        raise ValueError('当量变量模式必须为摩尔比或官能团当量比。')
    return target, reference, mode


def _ordered_equivalents(base, variables):
    selected = {}
    for variable in variables:
        if variable.get('type', 'numeric') == 'numeric' and variable['key'].startswith('equivalent:'):
            target, reference, mode = _equivalent_spec(base, variable)
            if target in selected:
                raise ValueError('同一添加剂不能被多个当量变量同时调整：' + target)
            selected[target] = (variable, reference, mode)
    ordered, visiting, done = [], [], set()
    def visit(target):
        if target in visiting:
            raise ValueError('当量变量存在循环引用：' + ' → '.join(visiting[visiting.index(target):] + [target]))
        if target in done:
            return
        visiting.append(target)
        variable, reference, _ = selected[target]
        if reference in selected:
            visit(reference)
        visiting.pop()
        done.add(target)
        ordered.append(variable)
    for target in selected:
        visit(target)
    return ordered


def validate_series_variables(base, variables, balance=None, registry=None):
    """Validate bindings once; never turn unknown raw IDs into filtered DOE runs."""
    if not isinstance(variables, list):
        raise ValueError("实验变量必须为列表。")
    seen, names, changed_rows, bound_rows, direct_rows = set(), set(), set(), set(), set()
    sources = {}
    for source_type, table in (('material', 'materials'), ('premix', 'premixes')):
        entries = (registry or {}).get(table, {})
        sources[source_type] = {x['id']: x for x in entries} if isinstance(entries, list) else entries
    for variable in variables:
        key = variable.get("key")
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("实验变量 key 必须存在且不能重复。")
        seen.add(key)
        kind = variable.get("type", "numeric")
        if kind == "numeric":
            if variable.get("binding") or variable.get("mapping"):
                raise ValueError("数值变量不能绑定原料替换；请用明确的自定义类别变量。")
            if key.startswith('equivalent:'):
                _equivalent_spec(base, variable)
                continue
            target = _numeric_target(base, variable)
            if target:
                changed_rows.add((target[0], target[3]))
                direct_rows.add((target[0], target[3]))
            continue
        if kind != "categorical" or not key.startswith("custom:") or not key[7:]:
            raise ValueError("类别变量必须使用 custom: 开头的唯一标识，并由用户定义名称。")
        name = str(variable.get("name") or variable.get("label") or "").strip()
        if not name or name in names:
            raise ValueError("自定义类别变量需要互不重复的名称。")
        names.add(name)
        levels = variable.get("levels")
        if (not isinstance(levels, list) or not levels or any(not isinstance(x, str) or not x.strip() for x in levels)
                or len(set(levels)) != len(levels)):
            raise ValueError(name + "需要互不重复的非空类别水平。")
        binding, mapping = variable.get("binding"), variable.get("mapping")
        if binding is None and mapping is None:
            continue
        if not isinstance(binding, dict) or not isinstance(mapping, dict):
            raise ValueError(name + "的来源绑定必须同时提供配方行与各水平的原料或预混编号。")
        group, rid = binding.get("group"), binding.get("row_id")
        _by_id(base, group, rid)
        identity = (group, rid)
        if identity in bound_rows:
            raise ValueError("同一配方行不能被两个类别变量同时替换。")
        bound_rows.add(identity)
        changed_rows.add(identity)
        if set(mapping) != set(levels):
            raise ValueError(name + "的每个类别水平都必须且只能对应一个原料或预混编号。")
        for level, source in mapping.items():
            label = f'{name} / {level}'
            source_type, sid = _mapped_source(source, label)
            if group == 'solvents' and source_type != 'material':
                raise ValueError(label + '：溶剂行只能选择纯溶剂原料，不能替换为预混液。')
            if registry is not None and sid not in sources[source_type]:
                raise ValueError(f"{label}：未登记{'原料' if source_type == 'material' else '预混液'} {sid}。")
            if registry is not None and group == 'solvents':
                try:
                    comp = resolve_source(source_type, sid, registry)
                except CalculationError as error:
                    raise ValueError(f'{label}：{error}') from None
                if comp['solvent_fraction'] < 1-FRACTION_TOL or comp['solvents'] != {sid: comp['solvent_fraction']}:
                    raise ValueError(f'{label}：{sid}不是归属到自身的纯溶剂。')
    equivalent_variables = _ordered_equivalents(base, variables)
    equivalent_targets = {variable['equivalent']['target_row_id'] for variable in equivalent_variables}
    for variable in equivalent_variables:
        target, reference, _ = _equivalent_spec(base, variable)
        if ('additives', target) in direct_rows:
            raise ValueError('同一添加剂不能同时设置直接百分比和当量变量：' + target)
        _, reference_row = _by_id(base, 'additives', reference)
        if reference not in equivalent_targets and _number(reference_row.get('percent', 0), '参照添加剂比例') <= 0:
            raise ValueError('当量参照添加剂的比例必须大于0：' + reference)
    balance = balance or {}
    if not isinstance(balance, dict) or set(balance) - {"resin_row_id", "solvent_row_id", "solvent_total"}:
        raise ValueError("平衡设置仅支持树脂行、溶剂行及溶剂总份数。")
    for group, key in (("resins", "resin_row_id"), ("solvents", "solvent_row_id")):
        rid = balance.get(key)
        if not rid:
            continue
        _, selected_row = _by_id(base, group, rid)
        if group == 'solvents' and selected_row.get('solvent_mode') == 'fixed_g':
            raise ValueError('溶剂平衡行必须按比例分配，不能使用固定克数行。')
        if (group, rid) in changed_rows:
            raise ValueError("依赖平衡行不能同时作为独立实验变量，请选择其他行补足余量。")
    if balance.get("solvent_total") not in (None, ""):
        _number(balance["solvent_total"], "溶剂合计份数", 1e-12)


def _apply_equivalent_variables(base, formula, row, variables, registry):
    from .equivalents import equivalent_options, calculate_equivalents, _calculate_row, _solve_equivalent_doses
    ordered = _ordered_equivalents(base, variables)
    if not ordered:
        return
    if registry is None:
        raise ValueError('当量实验设计需要当前产品原料库，请重新预览。')
    values = row.get('values') or {}
    active = [variable for variable in ordered if values.get(variable['key']) not in (None, '')]
    if not active:
        return
    required = {rid for variable in active for rid in _equivalent_spec(base, variable)[:2]}
    original = {item['row_id']: item for item in equivalent_options(base, registry)['rows']}
    current = {item['row_id']: item for item in equivalent_options(formula, registry)['rows']}
    config = formula.setdefault('equivalents', {})
    for item in config.get('rows', []):
        rid = item.get('row_id')
        if rid not in required:
            continue
        old, new = original[rid], current[rid]
        same_species = item.get('species_id') in ({species['id'] for species in old['species_options']}
                                                 & {species['id'] for species in new['species_options']})
        if (item.get('fraction_mode', 'component') == 'component' and item.get('species_confirmed') is True
                and item.get('source_signature') == old['source_signature'] and old['source_signature']
                and (item.get('source_type'), item.get('source_id')) == (old['source_type'], old['source_id'])
                and (new['source_type'], new['source_id']) != (old['source_type'], old['source_id']) and same_species):
            item.update(source_type=new['source_type'], source_id=new['source_id'], source_signature=new['source_signature'])
    calculation = calculate_formula(formula, registry)
    if not calculation['ok']:
        raise ValueError('请先修正配方质量平衡，再反算目标当量。')
    configs = {item.get('row_id'): item for item in config.get('rows', [])}
    constraints, results = [], []
    for variable in active:
        target, reference, mode = _equivalent_spec(base, variable)
        ratio = _number(values[variable['key']], '目标当量比')
        selected = 'moles' if mode == 'molar' else 'equivalents'
        coefficients = {}
        for rid in (target, reference):
            unit = _calculate_row(current[rid], {'planned_mass_g': 1.0}, configs.get(rid), mode)
            if unit['errors'] or not unit[selected]:
                messages = '；'.join(issue['message'] for issue in unit['errors'])
                raise ValueError('当量变量 ' + target + '：' +
                                 (messages or '请补齐参照与目标添加剂的当量设置。'))
            coefficients[rid] = unit[selected]
        multiplier = ratio * coefficients[reference] / coefficients[target]
        constraints.append((target, reference, multiplier))
        results.append({'key': variable['key'], 'target_row_id': target, 'reference_row_id': reference,
                        'mode': mode, 'target_ratio': ratio})
    _solve_equivalent_doses(formula, registry, calculation, constraints)
    for result in results:
        _, target_row = _by_id(formula, 'additives', result['target_row_id'])
        value = target_row['percent']
        result.update(percent=value, dose_basis=normalize_dose_basis(target_row.get('dose_basis')), dose_value=value)
    calculation = calculate_formula(formula, registry)
    for result in results:
        config.update(reference_row_id=result['reference_row_id'], mode=result['mode'])
        equivalent = calculate_equivalents(formula, registry, calculation)
        target_result = next(item for item in equivalent['rows'] if item['row_id'] == result['target_row_id'])
        actual_ratio = target_result['ratio']
        if (not calculation['ok'] or actual_ratio is None or
                not math.isclose(actual_ratio, result['target_ratio'], rel_tol=1e-9, abs_tol=1e-12)):
            messages = equivalent['errors'] or equivalent.get('calculation_errors', [])
            raise ValueError('当量变量 ' + result['target_row_id'] + '：' +
                             ('；'.join(issue['message'] for issue in messages) or '目标相对当量复核未通过'))
        result['ratio'] = actual_ratio
    formula['equivalent_design_results'] = results


def apply_series_row(base, row, variables, balance=None, registry=None):
    """Return one independent formula, keeping historical optional fields intact."""
    validate_series_variables(base, variables, balance)
    formula = deepcopy(base)
    formula.pop('equivalent_design_results', None)
    if row.get("code"):
        formula["code"] = str(row["code"])
    if row.get("process_factors"):
        if not isinstance(row["process_factors"], dict):
            raise ValueError("工艺类别记录必须为名称与值的映射。")
        formula.setdefault("process_factors", {}).update(deepcopy(row["process_factors"]))
    values = row.get("values") or {}
    for variable in variables:
        key = variable["key"]
        value = values.get(key)
        if value in (None, ""):
            continue  # Preserve the existing explicit-matrix blank-means-base behavior.
        name = str(variable.get("name") or variable.get("label") or key).strip()
        if key.startswith('equivalent:'):
            continue  # Derived percentages are applied after every ordinary input.
        if variable.get("type", "numeric") == "categorical":
            if value not in variable["levels"]:
                raise ValueError(name + "的值不在设置的类别水平中。")
            formula.setdefault("process_factors", {})[name] = value
            binding = variable.get("binding")
            if binding:
                index, _ = _by_id(formula, binding["group"], binding["row_id"])
                target = formula[binding["group"]][index]
                source_type, source_id = _mapped_source(variable['mapping'][value], name)
                target.update(source_type=source_type, source_id=source_id)
                target.pop("frozen_source", None)
            continue
        numeric = _number(value, name)
        if key == "total_mass_g":
            formula[key] = numeric
        elif key == "solid_fraction":
            formula[key] = numeric / 100
        else:
            group, index, field, _ = _numeric_target(formula, variable)
            formula[group][index][field] = numeric
    balance = balance or {}
    for group, key in (("resins", "resin_row_id"), ("solvents", "solvent_row_id")):
        if not balance.get(key):
            continue
        index, _ = _by_id(formula, group, balance[key])
        total = 100.0 if group == "resins" else _number(
            balance.get("solvent_total") if balance.get("solvent_total") not in (None, "")
            else sum(_number(x.get('parts', 0), '溶剂份数') for x in base.get(group, [])
                     if x.get('solvent_mode') != 'fixed_g'), '溶剂合计份数', 1e-12)
        remainder = total - sum(_number(x.get('parts', 0), '固定组分份数')
                                for i, x in enumerate(formula.get(group, []))
                                if i != index and (group != 'solvents' or x.get('solvent_mode') != 'fixed_g'))
        if remainder < -1e-9:
            raise ValueError(f"{'树脂' if group == 'resins' else '溶剂'}独立组分超过合计，依赖行剩余为 {remainder:g} 份。")
        formula[group][index]["parts"] = max(0.0, remainder)
    _apply_equivalent_variables(base, formula, row, variables, registry)
    if row.get("design_metadata") is not None:
        formula["design_metadata"] = deepcopy(row["design_metadata"])
    if row.get("design_row") is not None:
        formula["design_row"] = deepcopy(row["design_row"])
    return formula


def row_constraint_errors(row, variables, constraints):
    """Recheck explicit constraints at preview/commit, including edited matrices."""
    known = {x["key"]: x for x in variables}
    values = row.get("values") or {}
    errors = []
    for constraint in constraints or []:
        keys = constraint.get("keys", [])
        if (constraint.get("type") != "sum" or not keys or len(set(keys)) != len(keys)
                or any(key not in known or known[key].get("type", "numeric") != "numeric" for key in keys)):
            raise ValueError("份数合计约束仅能包含互不重复的已选数值变量。")
        total = _number(constraint.get("total"), "份数合计")
        tolerance = _number(constraint.get("tolerance", 1e-7), "约束容差", 1e-12)
        if any(values.get(key) in (None, "") for key in keys):
            errors.append("合计约束中的变量不能留空。")
            continue
        actual = sum(_number(values[key], "受约束变量") for key in keys)
        if abs(actual - total) > tolerance:
            errors.append(f"变量合计为 {actual:g}，应为 {total:g}（容差 {tolerance:g}）。")
    return errors


def _design_constraints(base, variables, balance, method, provided):
    constraints = deepcopy(provided or [])
    if method != "simplex_lattice":
        return constraints
    targets = [_numeric_target(base, v) if v.get("type", "numeric") == "numeric" else None for v in variables]
    if not targets or any(t is None for t in targets):
        raise ValueError("混料设计需要选择同一组的树脂或溶剂份数。")
    groups = {t[0] for t in targets}
    if len(groups) != 1 or next(iter(groups)) not in ("resins", "solvents"):
        raise ValueError("混料设计只能使用同一组的树脂份数或溶剂份数。")
    group = targets[0][0]
    selected = {t[1] for t in targets}
    total = 100.0 if group == "resins" else _number(
        balance.get("solvent_total") if balance.get("solvent_total") not in (None, "")
        else sum(_number(x.get('parts', 0), '溶剂份数') for x in base.get(group, [])
                 if x.get('solvent_mode') != 'fixed_g'), '溶剂合计份数', 1e-12)
    available = total - sum(_number(x.get('parts', 0), '固定组分份数')
                            for i, x in enumerate(base.get(group, []))
                            if i not in selected and (group != 'solvents' or x.get('solvent_mode') != 'fixed_g'))
    if available <= 0:
        raise ValueError("固定组分已经占满合计，选中组分没有可分配的混料份数。")
    keys = [v["key"] for v in variables]
    if constraints:
        if (len(constraints) != 1 or constraints[0].get("type") != "sum"
                or set(constraints[0].get("keys", [])) != set(keys)
                or len(constraints[0].get("keys", [])) != len(keys)
                or not math.isclose(_number(constraints[0].get("total"), "混料合计"), available, abs_tol=1e-7, rel_tol=0)):
            raise ValueError(f"混料合计必须覆盖全部所选变量，且等于扣除固定组分后的 {available:g} 份。")
    else:
        constraints = [{"type": "sum", "keys": keys, "total": available, "tolerance": 1e-7}]
    return constraints


def design_fingerprint(payload):
    """Detect editing/deleting a recommended point without mislabelling its DOE."""
    base = deepcopy(payload["base_formula"])
    for key in ("code", "note", "design_metadata", "design_row", "equivalent_design_results"):
        base.pop(key, None)
    variables = payload.get("variables", [])
    numeric_keys = {v["key"] for v in variables if v.get("type", "numeric") == "numeric"}
    rows = []
    for row in payload.get("rows", []):
        values = deepcopy(row.get("values") or {})
        for key in numeric_keys:
            if values.get(key) not in (None, ""):
                values[key] = _number(values[key], "矩阵变量")
        rows.append({"values": values, "repeat": _number(row.get("repeat", 1), "重复次数", 1),
                     "process_factors": deepcopy(row.get("process_factors") or {})})
    definition = {"base_formula": base, "variables": variables,
                  "balance": payload.get("balance") or {}, "constraints": payload.get("constraints") or [],
                  "rows": rows}
    def canonical(value):
        # Browser JSON serializes 1.0 as 1. These are the same experimental value.
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, dict):
            return {key: canonical(item) for key, item in value.items()}
        if isinstance(value, list):
            return [canonical(item) for item in value]
        return value
    return sha256(json.dumps(canonical(definition), sort_keys=True, ensure_ascii=False,
                             allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def series_metadata_errors(payload):
    metadata = payload.get("design_metadata") or {}
    errors = []
    if metadata.get("valid") is False:
        errors.append("原推荐设计未通过模型或配方检查，请调整范围并重新生成实验矩阵。")
    if metadata.get("matrix_fingerprint") and metadata["matrix_fingerprint"] != design_fingerprint(payload):
        original = deepcopy(payload)
        labels = metadata.get('variable_labels_before_edits') or {}
        for variable in original.get('variables', []):
            before = labels.get(variable.get('key')) if isinstance(labels, dict) else None
            if isinstance(before, dict):
                for field in ('name', 'label'):
                    if field in before:
                        variable[field] = deepcopy(before[field])
                    elif field in (before.get('__missing__') or []):
                        variable.pop(field, None)
        if metadata['matrix_fingerprint'] != design_fingerprint(original):
            errors.append("推荐矩阵或基准配方已修改，请重新生成设计；若采用手工矩阵，请先明确移除原设计标识。")
    return errors


def failed_calculation(message, calculation=None):
    result = deepcopy(calculation) if calculation is not None else {
        "engine_version": "experiment-validation", "ok": False, "errors": [], "warnings": [],
        "lines": [], "solvent_balance": [], "totals": {}, "checks": {}}
    result["ok"] = False
    result["errors"].append({"code": "EXPERIMENT_INVALID", "message": str(message), "row_id": None})
    return result


