"""迁移时新写的小文件（"搬过来稍改"）：原 preparation_actuals.py 读写旧库，不搬。
这里留：PREMIX_STOCK_REMINDER（导出用，原文）；group_summary（预混实称 → 实际组成，原文照抄，
只把第一个参数 db 去掉、_component_composition 改成只用每个组分冻结的 source_composition——
旧备料单的组分都带 source_composition，原代码此时也是直接用它）。"""
import math

PREMIX_STOCK_REMINDER = '本次按新配预混液的实称浓度回算样品组成；如果样品混用了上次剩下的旧预混液，请人工核对样品的实际组成。'


def _number(value):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError('实际称量应填写非负数字。')
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError('实际称量应填写非负数字。') from None
    if not math.isfinite(number) or number < 0:
        raise ValueError('实际称量应填写非负数字。')
    return number


def _component_composition(prep, group, component):
    return component.get('source_composition')


def group_summary(prep, group, actual):
    records = actual.get('components', {})
    total = solid = solvent = volatile = 0.0
    species, rows = {}, []
    missing_composition = False
    count = 0
    for index, component in enumerate(group.get('components', [])):
        cid = 'c' + str(index)
        value = records.get(cid, {})
        mass = _number(value.get('mass_g'))
        comp = _component_composition(prep, group, component)
        rows.append({'component_id': cid, 'source_id': component['source_id'], 'actual_mass_g': mass,
                     'planned_mass_g': component['planned_mass_g'], 'review': value.get('review', '')})
        if mass is None:
            continue
        count += 1
        total += mass
        if comp is None:
            missing_composition = True
            continue
        solid += mass * float(comp.get('solid_fraction', 0))
        solvent += mass * float(comp.get('solvent_fraction', 0))
        volatile += mass * float(comp.get('volatile_fraction', 0))
        for sid, fraction in comp.get('solvents', {}).items():
            species[sid] = species.get(sid, 0) + mass * float(fraction)
    complete = bool(rows) and count == len(rows)
    composition = None
    if complete and not missing_composition and total > 0:
        composition = {'name': group.get('name', group['source_id']), 'solid_fraction': solid / total,
                       'solvent_fraction': solvent / total, 'volatile_fraction': volatile / total,
                       'solvents': {sid: mass / total for sid, mass in species.items()}}
    actual_yield = _number(actual.get('actual_yield_g'))
    if not complete:
        message = '实称尚未填全，暂不能计算这次预混的实际组成。'
    elif missing_composition:
        message = '缺少这张预混工单当时的原料组成，可记录实称总量，暂不能回算组成。'
    elif not total:
        message = '实际投料合计为 0，暂不能计算组成。'
    else:
        message = '已按各原料实称投料估算组成。实际产出单独记录，不据此推断挥发或成分损失。'
    return {'group_key': group['key'], 'source_id': group['source_id'], 'total_input_g': total,
            'actual_yield_g': actual_yield, 'loss_g': total - actual_yield if complete and actual_yield is not None else None,
            'recorded_count': count, 'line_count': len(rows), 'complete': complete,
            'composition': composition, 'message': message, 'lines': rows}

