"""Pure pilot-production calculations: g, mL, and fractions (never percent).

The returned dictionaries can be saved with an adjustment round, so a later
change to a batch does not change the calculation that an operator approved.
"""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from statistics import mean, stdev


# Defaults follow the balances used for weighing. Products may change these
# display tiers; saved masses and all calculation results retain full precision.
DEFAULT_MASS_DISPLAY = {"limits_g": [220, 5000], "decimals": [4, 2, 1]}
DEFAULT_SOLIDS_DISPLAY = {"decimals": 2}


def normalize_solids_display(display=None, *, validate=False):
    """Percentage display precision; fractions used in calculations stay unchanged."""
    value = display.get('decimals') if isinstance(display, dict) else None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value != int(value) or not 0 <= value <= 6):
        if validate:
            raise PilotCalculationError('固含显示位数 solids_display.decimals 须为 0 至 6 的整数')
        return dict(DEFAULT_SOLIDS_DISPLAY)
    return {'decimals': int(value)}


def solids_decimals(display=None):
    return normalize_solids_display(display)['decimals']


def solids_text(value, display=None, *, extra=0):
    """Render a fraction as percentage points, without the percent sign."""
    unit = Decimal(1).scaleb(-solids_decimals(display) - extra)
    return format((Decimal(str(value)) * 100).quantize(unit, rounding=ROUND_HALF_UP), 'f')


def solids_format(display=None, *, fraction=False, extra=0):
    places = solids_decimals(display) + extra
    return '0' + ('.' + '0' * places if places else '') + ('%' if fraction else '')


def normalize_mass_display(display=None, *, validate=False):
    """Read old/invalid settings as defaults; reject invalid edits on save."""
    try:
        if not isinstance(display, dict):
            raise PilotCalculationError("质量显示位数 mass_display 应包含质量分界和小数位数")
        limits, decimals = display.get("limits_g"), display.get("decimals")
        if not isinstance(limits, (list, tuple)):
            raise PilotCalculationError("质量显示第 1 行分界 limits_g 须为列表")
        for index, value in enumerate(limits, 1):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise PilotCalculationError(f"质量显示第 {index} 行分界 limits_g[{index - 1}] 须大于 0 且为有限数字")
            if index > 1 and value <= limits[index - 2]:
                raise PilotCalculationError(f"质量显示第 {index} 行分界 limits_g[{index - 1}] 须大于上一行分界")
        if not isinstance(decimals, (list, tuple)):
            raise PilotCalculationError("质量显示第 1 行小数位数 decimals 须为列表")
        if len(decimals) != len(limits) + 1:
            row = min(len(decimals), len(limits) + 1) + 1
            raise PilotCalculationError(f"质量显示第 {row} 行小数位数 decimals 项数不符：每行一项，须比质量分界多一项")
        for index, value in enumerate(decimals, 1):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value != int(value) or not 0 <= value <= 8):
                raise PilotCalculationError(f"质量显示第 {index} 行小数位数 decimals[{index - 1}] 须为 0 至 8 的整数")
        return {"limits_g": list(limits), "decimals": [int(value) for value in decimals]}
    except PilotCalculationError:
        if validate:
            raise
        return {key: list(value) for key, value in DEFAULT_MASS_DISPLAY.items()}


def mass_decimals(value, display=None):
    display = normalize_mass_display(display)
    return next((display["decimals"][index] for index, limit in enumerate(display["limits_g"])
                 if abs(value) < limit), display["decimals"][-1])


def shown_mass(value, display=None):
    """The mass as displayed, rounded like the browser's toLocaleString: the
    shortest decimal text of the number, halves away from zero."""
    unit = Decimal(1).scaleb(-mass_decimals(value, display))
    return Decimal(str(value)).quantize(unit, rounding=ROUND_HALF_UP)


def mass_text(value, display=None, *, grouping=False):
    return f"{shown_mass(value, display):{',' if grouping else ''}f}"


class PilotCalculationError(ValueError):
    def __init__(self, message):
        super().__init__(message)
        self.code = "INVALID_PILOT_CALCULATION"


def _number(value, label, *, minimum=0, maximum=None, positive=False):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise PilotCalculationError(f"{label}必须为数值") from None
    if not math.isfinite(value) or value < minimum or (positive and value == 0) or (maximum is not None and value > maximum):
        raise PilotCalculationError(f"{label}超出允许范围")
    return value


def _fraction(value, label, *, positive=False):
    return _number(value, label, maximum=1, positive=positive)


def _diluent_parts(diluent):
    if isinstance(diluent, dict):
        parts = {str(key): _fraction(value, "稀释剂比例") for key, value in diluent.items()}
    else:
        parts = {}
        for row in diluent or []:
            key = str(row.get("source_id") or row.get("code") or row.get("id") or "")
            if not key:
                raise PilotCalculationError("稀释剂需选择原料")
            parts[key] = parts.get(key, 0) + _fraction(row.get("fraction", 0), "稀释剂比例")
    if not parts or not math.isclose(sum(parts.values()), 1, abs_tol=1e-6):
        raise PilotCalculationError("稀释剂比例合计须为 100%")
    return parts


def pour_mass(pour):
    """Mass from a bottle difference or a direct net weighing, in g."""
    method = pour.get("method", "direct")
    if method == "difference":
        before = _number(pour.get("before_g", pour.get("gross_before_g")), "倒前毛重")
        after = _number(pour.get("after_g", pour.get("gross_after_g")), "倒后毛重")
        if after > before:
            raise PilotCalculationError("倒后毛重不能大于倒前毛重")
        return before - after
    if method not in ("direct", "net"):
        raise PilotCalculationError("请选择减量法或直接称量")
    return _number(pour.get("mass_g"), "本次投入")


def sum_pours(pours):
    """Soft-deleted pours retain their identity but contribute no material."""
    return sum(pour_mass(pour) for pour in pours
               if not pour.get("deleted_at") and not pour.get("deleted", False))


def premix_powder_target(solvent_actual_g, concentration):
    """Solvent-first preparation: powder / (powder + solvent) = c."""
    solvent = _number(solvent_actual_g, "溶剂累计已投")
    concentration = _fraction(concentration, "设计浓度")
    if concentration == 1:
        raise PilotCalculationError("先称溶剂时设计浓度须小于 100%")
    return solvent * concentration / (1 - concentration)


def lot_corrected_plan(lines, planned_mass_g, diluent=None):
    """Keep designed dry masses and balance lot changes against design solvent.

    ``diluent`` is the product setting, never an inferred batch solvent ratio.
    Negative solvent targets remain visible in previews and cannot be saved.
    """
    from copy import deepcopy
    total = _number(planned_mass_g, "设计总质量", positive=True)
    rows = deepcopy(lines)
    solvents = []
    for row in rows:
        design = _number(row.get("design_mass_g", row.get("planned_mass_g", 0)), "设计投料量")
        composition = deepcopy(row.get("design_source_composition", row.get("source_composition", {})))
        design_fraction = _fraction(row.get("design_solid_fraction", composition.get("solid_fraction", 0)), "设计固含")
        dry = _number(row.get("designed_solid_mass_g", design * design_fraction), "设计干质量")
        lot = row.get("lot_solid_fraction")
        fraction = design_fraction if lot in (None, "") else _fraction(lot, "批次固含")
        is_solvent = row.get("category") == "solvent" or (design_fraction == 0 and composition.get("solvent_fraction") == 1)
        if dry and not fraction:
            raise PilotCalculationError(f"{row.get('source_id', '')}批次固含应大于零")
        target = dry / fraction if dry else design
        if fraction != composition.get("solid_fraction", design_fraction):
            remainder = 1 - composition.get("solid_fraction", design_fraction)
            multiplier = (1 - fraction) / remainder if remainder else 0
            composition["solid_fraction"] = fraction
            composition["solvent_fraction"] = composition.get("solvent_fraction", 0) * multiplier
            composition["volatile_fraction"] = composition.get("volatile_fraction", 0) * multiplier
            composition["solvents"] = {sid: share * multiplier for sid, share in composition.get("solvents", {}).items()}
            if not remainder:
                composition["volatile_fraction"] = 1 - fraction
        row.update(design_mass_g=design, design_solid_fraction=design_fraction,
                   designed_solid_mass_g=dry, planned_mass_g=target,
                   source_composition=composition, lot_solid_fraction=None if lot in (None, "") else fraction)
        if is_solvent:
            solvents.append(row)
    delta = total - sum(row["planned_mass_g"] for row in rows)
    if diluent:
        ratios = _diluent_parts(diluent)
        for sid, ratio in ratios.items():
            matching = [row for row in solvents if row["source_id"] == sid]
            if ratio and not matching:
                raise PilotCalculationError(f"稀释剂 {sid} 缺少溶剂投料行")
            basis = sum(row["design_mass_g"] for row in matching)
            for row in matching:
                share = row["design_mass_g"] / basis if basis else 1 / len(matching)
                row["planned_mass_g"] += delta * ratio * share
    elif solvents:
        main = max(solvents, key=lambda row: row["design_mass_g"])
        main["planned_mass_g"] += delta
    elif abs(delta) > 1e-7:
        raise PilotCalculationError("没有可平衡总质量的主溶剂行，请添加溶剂。")
    warnings = []
    for row in rows:
        mass = row["planned_mass_g"]
        row["mass_change_g"] = mass - row["design_mass_g"]
        row["solvent_change_g"] = row["mass_change_g"] if row in solvents else 0
        comp = row["source_composition"]
        row.update(solid_mass_g=mass * comp.get("solid_fraction", 0),
                   solvent_mass_g=mass * comp.get("solvent_fraction", 0),
                   volatile_mass_g=mass * comp.get("volatile_fraction", 0))
        if mass < 0:
            warnings.append(f"{row.get('source_id', '')}溶剂目标为负（{mass:g} g）；批次过稀，无法达到设计，请调整设计或原料批次后保存。")
    rows.sort(key=lambda row: row.get("order", 0))
    for sequence, row in enumerate(rows, 1):
        row["sequence"] = sequence
    solid = sum(row["solid_mass_g"] for row in rows)
    return {"lines": rows, "planned_mass_g": total, "total_mass_g": total,
            "solid_mass_g": solid, "solid_fraction": solid / total,
            "solvent_change_g": sum(row["mass_change_g"] for row in solvents),
            "warnings": warnings, "can_save": not warnings,
            "explanation": "保持各组分设计干质量，按批次固含换算溶液量；差额按中试设置稀释剂比例分摊，未设置时由主溶剂平衡。"}


def proportional_replan(lines, actuals, base_targets=None, manual_factor=None,
                        note="", vessel_limit_g=None, display=None):
    """Scale a composition-corrected f=1 design to cover every charged line.

    The caller recalculates the formula using actual premix compositions and
    passes those masses in ``base_targets``. ``lines`` should carry those same
    actual source compositions, so the solids check uses the prepared material.
    Original design masses remain on each returned row for reference. All
    factors are relative to the original batch, never to the last adopted plan.
    """
    base_targets = base_targets or {}
    rows, minimum, basis = [], 1.0, None
    for line in lines:
        identity = line["id"]
        original = _number(line["planned_mass_g"], "原设计目标")
        target = _number(base_targets.get(identity, original), "追平计算基准目标")
        actual = actuals.get(identity, 0)
        completed = False
        if isinstance(actual, dict):
            completed = bool(actual.get("completed")) or actual.get("status") == "completed"
            actual = sum_pours(actual["pours"]) if "pours" in actual else actual.get("mass_g", actual.get("actual_mass_g", 0))
        elif isinstance(actual, list):
            actual = sum_pours(actual)
        actual = _number(actual, "累计已投")
        if actual and target == 0:
            raise PilotCalculationError(f"{line.get('source_id', identity)}追平目标为 0，已有投料无法按比例追平")
        ratio = actual / target if target else 0
        if ratio > minimum:
            minimum, basis = ratio, line
        rows.append({"id": identity, "source_id": line.get("source_id", identity),
                     "original_target_g": original, "base_target_g": target,
                     "actual_g": actual, "actual_mass_g": actual, "completed": completed,
                     "solid_fraction": _fraction(line.get("source_composition", {}).get("solid_fraction", 0), "来源固体比例")})
    factor = minimum
    if manual_factor is not None:
        factor = _number(manual_factor, "手动放大倍数", minimum=1)
        if factor < minimum - 1e-12:
            raise PilotCalculationError("手动放大倍数不能低于已投料需要的追平倍数")
        if not str(note).strip():
            raise PilotCalculationError("手动放大请填写原因")
    def displayed_mass(value):
        return shown_mass(value, display)

    warnings = []
    for row in rows:
        row["target_g"] = row["target_mass_g"] = row["base_target_g"] * factor
        row["remaining_g"] = row["target_g"] - row["actual_g"]
        # Only the warning compares the values as the operator sees them, so a
        # pour that matches the shown target does not count as short.
        shown_gap = float(displayed_mass(row["target_g"]) - displayed_mass(row["actual_g"]))
        if row["completed"] and shown_gap > 1e-9:
            warnings.append(f"{row['source_id']}已标记完成，还需要再倒 {mass_text(shown_gap, display)} g")
    total = sum(row["target_g"] for row in rows)
    solids = sum(row["target_g"] * row["solid_fraction"] for row in rows)
    limit = None if vessel_limit_g in (None, "") else _number(vessel_limit_g, "釜最大装载量", positive=True)
    if limit is not None and total > limit + 1e-9:
        warnings.append(f"新投料总量超过釜最大装载量 {mass_text(total - limit, display)} g")
    return {"factor": factor, "minimum_factor": minimum,
            "basis_line_id": basis["id"] if basis else None,
            "basis_source_id": basis.get("source_id", basis["id"]) if basis else None,
            "basis_line": basis.get("source_id", basis["id"]) if basis else None,
            "manual_override": manual_factor is not None, "note": str(note).strip(),
            "lines": rows, "targets": {row["id"]: row["target_g"] for row in rows},
            "total_mass_g": total, "new_total_mass_g": total, "solid_mass_g": solids,
            "theoretical_solids": solids / total if total else 0,
            "vessel_limit_g": limit, "warnings": warnings}


def thickness_stats(values):
    """Sample standard deviation is used for 3σ, as in the production sheet."""
    values = [_number(value, "膜厚") for value in values]
    if not values:
        raise PilotCalculationError("请填写膜厚测点")
    average = mean(values)
    low, high = min(values), max(values)
    return {
        "n": len(values), "mean": average, "min": low, "max": high,
        "range": high - low, "three_sigma": 3 * stdev(values) if len(values) > 1 else 0,
        "uniformity": (high - low) / (2 * average) if average else None,
    }


def linear_fit(points):
    """Fit y = intercept + slope*x. Excluded points stay outside all statistics."""
    included = []
    for point in points:
        if isinstance(point, dict):
            if point.get("include", True) is False or point.get("excluded", False):
                continue
            x, y = point["x"], point["y"]
        else:
            x, y = point
        included.append((_number(x, "拟合横坐标", minimum=-math.inf), _number(y, "拟合纵坐标", minimum=-math.inf)))
    if len(included) < 2:
        raise PilotCalculationError("拟合至少需要两个有效点")
    xs, ys = zip(*included)
    x_mean, y_mean = mean(xs), mean(ys)
    sxx = sum((x - x_mean) ** 2 for x in xs)
    if sxx == 0:
        raise PilotCalculationError("拟合点的横坐标不能全部相同")
    slope = sum((x - x_mean) * (y - y_mean) for x, y in included) / sxx
    intercept = y_mean - slope * x_mean
    residuals = [y - (intercept + slope * x) for x, y in included]
    sse = sum(value ** 2 for value in residuals)
    syy = sum((y - y_mean) ** 2 for y in ys)
    r_squared = 1 - sse / syy if syy else (1 if sse < 1e-20 else 0)
    warnings = []
    if len(included) < 5:
        warnings.append("有效点少于 5 个，建议增加实验点")
    if r_squared < 0.99:
        warnings.append("拟合 R² 小于 0.99，建议检查数据")
    return {
        "slope": slope, "intercept": intercept, "r_squared": r_squared,
        "rmse": math.sqrt(sse / len(included)),
        "max_abs_residual": max(abs(value) for value in residuals),
        "n": len(included), "x_min": min(xs), "x_max": max(xs),
        "y_min": min(ys), "y_max": max(ys), "residuals": residuals,
        "warnings": warnings,
    }


def dilution_series(stock_solids, available_g, points, diluent):
    stock_solids = _fraction(stock_solids, "原液固含", positive=True)
    available_g = _number(available_g, "原液可用量")
    parts = _diluent_parts(diluent)
    result = []
    for index, point in enumerate(points, 1):
        target = _fraction(point["target_solids"], "目标固含")
        sample = _number(point["sample_mass_g"], "样品量", positive=True)
        if target > stock_solids:
            raise PilotCalculationError(f"稀释点{index}目标固含（{target * 100:.4g}%）不能高于原液固含（{stock_solids * 100:.4g}%）")
        stock = sample * target / stock_solids
        addition = sample - stock
        row = dict(point, target_solids=target, sample_mass_g=sample, stock_g=stock,
                   diluent_g=addition, diluent_components={key: addition * fraction for key, fraction in parts.items()})
        if point.get("actual_stock_g") is not None and point.get("actual_diluent_g") is not None:
            actual_stock = _number(point["actual_stock_g"], "原液实际量")
            actual_diluent = _number(point["actual_diluent_g"], "稀释剂实际量")
            if actual_stock + actual_diluent == 0:
                raise PilotCalculationError("实际样品总量须大于 0")
            row["actual_solids"] = actual_stock * stock_solids / (actual_stock + actual_diluent)
        result.append(row)
    total = sum(row["stock_g"] for row in result)
    actual_total = sum(float(row.get("actual_stock_g") if row.get("actual_stock_g") is not None else row["stock_g"]) for row in result)
    within = max(total, actual_total) <= available_g + 1e-9
    return {"points": result, "total_stock_g": total, "actual_total_stock_g": actual_total,
            "available_g": available_g, "within_available": within,
            "warnings": [] if within else ["原液用量超过可用量"]}


def concentrate_mass(mass_g, solids, target_solids, concentrate_solids):
    mass_g = _number(mass_g, "当前质量", positive=True)
    solids = _fraction(solids, "当前固含")
    target_solids = _fraction(target_solids, "目标固含")
    concentrate_solids = _fraction(concentrate_solids, "母液固含")
    if not concentrate_solids > target_solids > solids:
        raise PilotCalculationError("母液固含须高于目标固含，目标固含须高于当前固含")
    return mass_g * (target_solids - solids) / (concentrate_solids - target_solids)


def _inverse(target, fit):
    target = _number(target, "目标值", minimum=-math.inf)
    slope = _number(fit["slope"], "拟合斜率", minimum=-math.inf)
    if slope == 0:
        raise PilotCalculationError("拟合斜率为 0，无法反算")
    return (target - float(fit["intercept"])) / slope


def solids_back_calculation(target_thickness, fit, mass_g, solids, diluent, concentrate_solids=None):
    target_solids = _fraction(_inverse(target_thickness, fit), "反算目标固含", positive=True)
    mass_g = _number(mass_g, "当前质量", positive=True)
    solids = _fraction(solids, "当前固含")
    warnings = []
    extrapolated = target_solids < fit["x_min"] or target_solids > fit["x_max"]
    if extrapolated:
        warnings.append("外推，建议增加实验点")
    parts = _diluent_parts(diluent)
    addition, method, addition_solids = 0, "none", 0
    if solids > target_solids:
        addition = mass_g * (solids / target_solids - 1)
        method = "dilution"
    elif solids < target_solids:
        method = "concentrate"
        warnings.append("需配方/质量批准，不得默认蒸发浓缩")
        if concentrate_solids is None:
            return {"target_solids": target_solids, "method": method, "addition_g": None,
                    "diluent_g": 0, "diluent_components": {}, "mass_after_g": None,
                    "solids_after": None, "predicted_thickness": float(target_thickness),
                    "extrapolated": extrapolated, "warnings": warnings}
        addition = concentrate_mass(mass_g, solids, target_solids, concentrate_solids)
        addition_solids = float(concentrate_solids)
    after = mass_g + addition
    solids_after = (mass_g * solids + addition * addition_solids) / after
    return {"target_solids": target_solids, "method": method, "addition_g": addition,
            "diluent_g": addition if method == "dilution" else 0,
            "diluent_components": {key: addition * fraction for key, fraction in parts.items()} if method == "dilution" else {},
            "mass_after_g": after, "solids_after": solids_after,
            "predicted_thickness": float(fit["intercept"]) + float(fit["slope"]) * solids_after,
            "extrapolated": extrapolated, "warnings": warnings}


def spike_series(component_fraction, concentration, points):
    component_fraction = _fraction(component_fraction, "组分质量分数", positive=True)
    concentration = _fraction(concentration, "加料液浓度", positive=True)
    result = []
    for point in points:
        delta = _number(point["delta"], "增加比例")
        sample = _number(point["sample_mass_g"], "样品量", positive=True)
        row = dict(point, delta=delta, sample_mass_g=sample,
                   spike_g=delta * sample * component_fraction / concentration)
        if point.get("actual_spike_g") is not None:
            actual = _number(point["actual_spike_g"], "实际加料量")
            actual_sample = _number(point.get("actual_sample_g", sample), "实际样品量", positive=True)
            row["actual_delta"] = actual * concentration / (actual_sample * component_fraction)
        result.append(row)
    return {"points": result, "component_fraction": component_fraction, "concentration": concentration}


def dose_back_calculation(target, fit, mass_g, component_fraction, concentration, solids=0, spike_solids=0, thickness_fit=None):
    delta = _inverse(target, fit)
    mass_g = _number(mass_g, "当前质量", positive=True)
    component_fraction = _fraction(component_fraction, "组分质量分数", positive=True)
    concentration = _fraction(concentration, "加料液浓度", positive=True)
    solids = _fraction(solids, "当前固含")
    spike_solids = _fraction(spike_solids, "加料液固含")
    warnings = []
    addition = max(0, delta) * mass_g * component_fraction / concentration
    if delta <= 0:
        warnings.append("需要降低这个组分的比例，不能靠加它实现；可改做作用相反的组分（PAG ↔ PDB）的梯度")
    extrapolated = delta < fit["x_min"] or delta > fit["x_max"]
    if extrapolated:
        warnings.append("外推，建议增加实验点")
    after = mass_g + addition
    solids_after = (mass_g * solids + addition * spike_solids) / after
    predicted_thickness = None
    if thickness_fit is not None:
        predicted_thickness = float(thickness_fit["intercept"]) + float(thickness_fit["slope"]) * solids_after
    return {"delta": delta, "addition_g": addition, "mass_after_g": after, "solids_after": solids_after,
            "predicted_thickness": predicted_thickness, "extrapolated": extrapolated, "warnings": warnings}


def yield_prediction(mass_g, density, fill_ml=3785, samples_g=0, holdup_g=0, discard_ml=0, residue_g=0):
    mass_g = _number(mass_g, "当前质量")
    density = _number(density, "密度", positive=True)
    fill_ml = _number(fill_ml, "每瓶灌装量", positive=True)
    samples_g = _number(samples_g, "剩余计划取样量")
    holdup_g = _number(holdup_g, "滞留量")
    discard_g = _number(discard_ml, "前料废弃量") * density
    residue_g = _number(residue_g, "釜底残留")
    available = mass_g - samples_g - holdup_g - discard_g - residue_g
    per_bottle = fill_ml * density
    bottles = max(0, math.floor(available / per_bottle))
    return {"available_g": available, "bottle_mass_g": per_bottle, "bottles": bottles,
            "remainder_g": max(0, available) - bottles * per_bottle,
            "shortfall_g": max(0, -available), "density": density, "fill_ml": fill_ml}


def required_charge(bottles, density, fill_ml=3785, samples_g=0, holdup_g=0, discard_ml=0, residue_g=0, overage=0):
    bottles = _number(bottles, "目标瓶数")
    if not bottles.is_integer():
        raise PilotCalculationError("目标瓶数须为整数")
    density = _number(density, "密度", positive=True)
    fill_ml = _number(fill_ml, "每瓶灌装量", positive=True)
    product_g = bottles * density * fill_ml
    samples_g = _number(samples_g, "计划取样量")
    holdup_g = _number(holdup_g, "滞留量")
    discard_g = _number(discard_ml, "前料废弃量") * density
    residue_g = _number(residue_g, "釜底残留")
    base = product_g + samples_g + holdup_g + discard_g + residue_g
    overage = _number(overage, "余量比例")
    return {"mass_g": base * (1 + overage), "base_mass_g": base, "product_g": product_g,
            "samples_g": samples_g, "holdup_g": holdup_g, "discard_g": discard_g,
            "residue_g": residue_g, "overage_g": base * overage}


def mass_balance(charge_g, adjustment_g=0, samples_g=0, discard_g=0, product_g=0, holdup_g=0, residue_g=0):
    charge_g = _number(charge_g, "投料量")
    adjustment_g = _number(adjustment_g, "调整加料量")
    samples_g = _number(samples_g, "取样量")
    discard_g = _number(discard_g, "废弃量")
    product_g = _number(product_g, "成品净重")
    holdup_g = _number(holdup_g, "滞留量")
    total = charge_g + adjustment_g
    if residue_g is None:
        residue_g = max(0, total - samples_g - discard_g - product_g - holdup_g)
    residue_g = _number(residue_g, "残留量")
    accounted = samples_g + discard_g + product_g + holdup_g + residue_g
    return {"total_input_g": total, "charge_g": charge_g, "adjustment_g": adjustment_g,
            "samples_g": samples_g, "discard_g": discard_g, "product_g": product_g,
            "holdup_g": holdup_g, "residue_g": residue_g, "accounted_g": accounted,
            "unaccounted_g": total - accounted, "yield_fraction": product_g / total if total else 0}
