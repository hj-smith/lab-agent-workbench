"""研发台：树脂批总表与因子对比。

每个树脂批一行（合成因子、Mn/Mw/Đ、产率、代表膜的 n/k、膜厚、SolvA 损失、
老化 A500、用了它的配方）；再选一个合成因子和一个结果，给出最大可比对照组、
严格配对和中位差。统计部分从旧平台 xmod/service.py 原样搬来（_maximum_compatible、
_control、_pairs、_summary、_reading、recommendations），只把取数换成新库。
"""
from __future__ import annotations

import copy
import math
import re
from decimal import Decimal, ROUND_HALF_UP, localcontext
from itertools import combinations
from statistics import median

from lab import store
from lab.sci import resin

LIBRARY = '产品A'

# 结果列：键、名称、单位（同旧 RESULTS）
RESPONSES = [
    ('mn', 'Mn', 'g/mol'), ('mw', 'Mw', 'g/mol'), ('pdi', 'Đ', '1'), ('yield_pct', '产率', '%'),
    ('n193', 'n 193 nm', '1'), ('k193', 'k 193 nm', '1'), ('n248', 'n 248 nm', '1'),
    ('k248', 'k 248 nm', '1'), ('n633', 'n 633 nm', '1'), ('k633', 'k 633 nm', '1'),
    ('thickness_nm', '膜厚', 'nm'), ('solva_loss_pct', 'SolvA膜厚损失率', '%'),
    ('a500', '老化 A500', '1')]

# 总表的因子列，顺序参考旧 MASTER_COLUMNS
COLUMN_FACTORS = [
    'resin.factor.monoA_monoB_ratio', 'resin.factor.catalyst', 'resin.factor.catalyst_molpct',
    'resin.factor.main_solvent', 'resin.factor.reaction_solids', 'resin.factor.addition_mode',
    'resin.factor.reaction_temperature', 'resin.factor.reaction_hours', 'resin.factor.termination',
    'resin.factor.precipitation_solvent', 'resin.factor.addh_eq', 'resin.factor.water_separation']
COLUMN_RESPONSES = ['yield_pct', 'mn', 'mw', 'pdi', 'n193', 'k193', 'n248', 'k248', 'n633', 'k633',
                    'thickness_nm', 'solva_loss_pct', 'a500']

# 代表膜：椭偏块统计里的处理前读数
FILM_OPTICS = (('n193', 'n_193'), ('k193', 'k_193'), ('n248', 'n_248'),
               ('k248', 'k_248'), ('n633', 'n_633'), ('k633', 'k_633'))
GPC_FIELDS = {'mn': 'Mn', 'mw': 'Mw', 'pdi': 'Đ'}
GPC_RESULT_FIELDS = {'mn': 'Mn', 'mw': 'Mw', 'pdi': 'PDI'}


def _lib(lib):
    return lib if isinstance(lib, store.Library) else store.open(lib or LIBRARY)


# ---------- 显示和格子 ----------

def _places(key):
    if key in {'mn', 'mw'}:
        return 0
    if key == 'pdi' or 'loss_pct' in key:
        return 2
    if re.fullmatch(r'(?:delta_)?[nk]_?\d{3}', key):
        return 4
    if key in {'thickness_nm', 'yield_pct', 'dry_mass_g', 'theoretical_mass_g'}:
        return 1
    return None


def format_value(key, value, state='measured'):
    """旧的显示口径：Mn/Mw 千分位取整，Đ 和损失率两位，n/k 四位，其余 %g。"""
    if value is None or state == 'missing':
        return '—'
    if state == 'censored':
        return '≥4.0' if not finite(value) else f'≥{value:.1f}'
    if isinstance(value, bool):
        return '是' if value else '否'
    places = _places(key) if finite(value) else None
    if places is not None:
        number = Decimal(str(value))
        with localcontext() as context:
            context.prec = max(28, number.adjusted() + places + 2)
            rounded = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        return f'{rounded:,.{places}f}' if key in {'mn', 'mw'} else f'{rounded:.{places}f}'
    return f'{value:g}' if finite(value) else str(value)


def cell(key, value, unit=None, state='measured'):
    if isinstance(value, float) and not math.isfinite(value):
        value = None
    if value is None:
        state = 'missing'
    return {'value': value, 'display': format_value(key, value, state), 'state': state, 'unit': unit}


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def known(c):
    return c.get('value') is not None and c.get('state') != 'missing'


def same(a, b):
    if finite(a) and finite(b):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


def measured(c):
    return known(c) and c.get('state') == 'measured' and finite(c['value'])


def result_filled(c):
    # 有记录的下限也算覆盖；设计值和计划不算结果
    return known(c) and c.get('state') in ('measured', 'censored')


def condition_difference(a, b, key, metadata):
    ac, bc = a['cells'].get(key, {}), b['cells'].get(key, {})
    both = known(ac) and known(bc)
    one_sided = metadata.get('recorded', False) and known(ac) != known(bc)
    if (both and not same(ac['value'], bc['value'])) or one_sided:
        return {'factor_id': key, 'label': metadata['label'],
                'a_display': ac['display'] if known(ac) else '未记录',
                'b_display': bc['display'] if known(bc) else '未记录',
                'evidence_state': 'one_sided' if one_sided else 'different'}
    return None


# ---------- 批号排序 ----------

def lot_number(code):
    match = re.search(r'-(\d+)([^\d]*)$', code)
    mother = re.match(r'^[A-Z][A-Z0-9]*[-_](\d+)', code, re.I)
    return int(mother[1]) if mother else int(match[1]) if match else 0


def lot_sort_key(row):
    code = row['lot_key']
    number = lot_number(code)
    stem = re.match(r'^[A-Z][A-Z0-9]*[-_]\d+(.*)$', code, re.I)
    return (-number, stem[1].upper() if stem else code.upper(), code)


# ---------- 从库里取数 ----------


def _factor_defs(lib):
    """因子定义：树脂.因子规则为准，缺的用 平台.核心因子 补。"""
    registry = lib.setting('树脂.因子规则') or {}
    defs = []
    for item in registry.get('factors') or []:
        if item.get('active') is False:
            continue
        defs.append({'id': item['id'], 'label': item.get('label') or item['id'].rsplit('.', 1)[-1],
                     'unit': item.get('unit') or '1', 'kind': item.get('type') or 'number'})
    have = {item['id'] for item in defs}
    for item in lib.setting('平台.核心因子') or []:
        if item['key'] not in have:
            defs.append({'id': item['key'], 'label': item.get('名称') or item['key'].rsplit('.', 1)[-1],
                         'unit': item.get('单位') or '1', 'kind': 'number'})
    return defs


def _core(lib):
    return tuple(item['key'] for item in lib.setting('平台.核心因子') or [])


def _units(lib):
    units = {key: unit for key, _, unit in RESPONSES}
    for item in _factor_defs(lib):
        units[item['id']] = '' if item['unit'] in (None, '1') else item['unit']
    return units


def _lookup(lib):
    """比较条件只看因子；结果不参与条件差异。"""
    return {item['id']: dict(item) for item in _factor_defs(lib)}


def _formulas(lib, lots):
    """配方落在哪一批：先看 树脂批 字段，再看 树脂来源 的前缀（'R100-34-15%' → 'R100-34'）。"""
    stems = {}
    per_experiment = {}
    for lot in lots:
        stem = lot['批号'][:-3] if lot['批号'].endswith('-FP') else lot['批号']
        for key in (lot['批号'], stem, lot.get('分支')):
            if key:
                stems.setdefault(key, lot['批号'])
        per_experiment.setdefault(lot['实验编号'], []).append(lot['批号'])
    for experiment, keys in per_experiment.items():
        if len(keys) == 1:
            stems.setdefault(experiment, keys[0])
    out = []
    for formula in lib.rows('配方'):
        lot_key = next((stems[batch] for batch in formula.get('树脂批') or [] if batch in stems), None)
        if lot_key is None:
            for source in formula.get('树脂来源') or []:
                prefix = re.sub(r'-\d+(?:\.\d+)?%$', '', str(source))
                if prefix in stems:
                    lot_key = stems[prefix]
                    break
        out.append({'code': formula.get('配方编号'), 'lot': lot_key})
    return out


def _film_cells(value):
    """椭偏块统计 → 处理前 n/k、均值膜厚、SolvA 损失率。"""
    stage = (value.get('各阶段') or [{}])[0]
    optics = stage.get('光学') or {}
    cells = {key: (optics.get(field) or {}).get('处理前') for key, field in FILM_OPTICS}
    cells['thickness_nm'] = (stage.get('处理前') or {}).get('均值 nm')
    cells['solva_loss_pct'] = stage.get('损失率 %')
    return cells


def _films(lib):
    """每个配方编号下测过的膜，按测试日、导入顺序排。"""
    stats = {item['id']: item['value'] for item in lib.results('椭偏.块统计')}
    by_code = {}
    for film in lib.rows('膜'):
        code = film.get('配方编号')
        value = stats.get(film['id'])
        if not code or value is None:
            continue
        by_code.setdefault(code, []).append({
            'id': film['id'], 'code': code, 'name': film['name'],
            'date': str(film.get('测试日') or ''), 'order': film.get('导入顺序') or 0,
            'cells': _film_cells(value)})
    return by_code


def _representative(lot, films, chosen):
    """代表膜：人选的优先；否则取最新一条 n/k 齐全的膜。"""
    items = sorted(films, key=lambda f: (f['date'], f['order'], f['id']), reverse=True)
    picked = (chosen or {}).get(lot['批号'])
    if picked:
        chosen_item = next((f for f in items if f['id'] == picked), None)
        if chosen_item:
            return chosen_item
    for item in items:
        if all(item['cells'][key] is not None for key, _ in FILM_OPTICS):
            return item
    return None


def _aging(lib):
    """配方编号 → 老化谱里 A500 的一条（室温优先、天数最短、id 稳定）。"""
    rows = {row['id']: row for row in lib.rows('老化谱')}
    by_sample = {}
    for item in lib.results('老化.指标'):
        record = rows.get(item['id'])
        if not record or item['value'].get('a500') is None:
            continue
        by_sample.setdefault(record['样品'], []).append({
            'id': item['id'], 'a500': item['value']['a500'],
            'state': 'censored' if item['value'].get('at500Ceiling') else 'measured',
            'condition': record.get('条件'), 'days': record.get('天数')})

    def rank(item):
        days = item['days'] if finite(item['days']) else 1e9
        return (item['condition'] != '室温', days, item['id'])

    return {name: sorted(items, key=rank) for name, items in by_sample.items()}


def _sample(sample, gpc):
    """一批的 Mn/Mw/Đ：先取取样记录发布的 GPC 值，再看它挂的 GPC 样品。"""
    cells, ids = {}, {}
    for key, field in GPC_FIELDS.items():
        value = sample.get(field)
        if value is not None:
            cells[key], ids[key] = value, sample['id']
    linked = gpc.get(sample.get('GPC名') or '')
    if linked:
        for key, field in GPC_RESULT_FIELDS.items():
            if key not in cells and linked['value'].get(field) is not None:
                cells[key], ids[key] = linked['value'][field], linked['id']
    return cells, ids


def _yield(lot, sample, yields):
    """一批的产率：取样记录发布的 最终产率；没有就取树脂实验各路的计算值。"""
    if sample.get('最终产率') is not None:
        return sample['最终产率'], sample['id']
    result = yields.get(lot['实验编号'])
    if not result:
        return None, None
    value = result['value']
    route = lot.get('路线') or lot['节点']
    for item in value.get('各路') or []:
        if item.get('产品') == route and item.get('产率_%') is not None:
            return item['产率_%'], result['id']
    total = value.get('合计') or {}
    if total.get('产率_%') is not None and len(value.get('各路') or []) == 1:
        return total['产率_%'], result['id']
    return None, None


# 可选试剂在结构化设计里没出现就是「没加」，不是「没记录」；旧平台按 0 / 无 处理
OPTIONAL_ABSENT = {'resin.factor.addh_eq': 0, 'resin.factor.addh_timing': 0,
                   'resin.factor.additional_reagent': '无', 'resin.factor.additional_reagent_eq': 0,
                   'resin.factor.additional_reagent_timing': 0,
                   'resin.factor.second_addition_timing': 0}


def _factor_cells(lib, experiment, record_id):
    """一个实验的设计因子。设计值 → design；值为空 → missing。"""
    out, ids = {}, {}
    for key, item in (resin.factors(experiment, lib)['因子'] or {}).items():
        value = item.get('value')
        if value is None and item.get('state') == 'absent' and key in OPTIONAL_ABSENT:
            value = OPTIONAL_ABSENT[key]
        out[key] = cell(key, value, item.get('unit'), 'design' if value is not None else 'missing')
        ids[key] = record_id
    return out, ids


_ROWS = {}


def lot_rows(lib=None):
    """每批一行，形状与旧 _pairs 等函数一致：lot_key、cells、film。
    一页要用好几次（总表、推荐、对比），数据和设置都没变就用上次算的。"""
    lib = _lib(lib)
    stamp = (lib.sql('SELECT max(updated_at), count(*) FROM records')[1][0], lib.sql('SELECT max(updated_at) FROM settings')[1][0])
    if _ROWS.get(lib.name, (None,))[0] != stamp:
        _ROWS[lib.name] = (stamp, _lot_rows(lib))
    return copy.deepcopy(_ROWS[lib.name][1])


def _lot_rows(lib):
    lots = lib.rows('树脂批')
    samples = {row['样品名']: row for row in lib.rows('树脂取样')}
    gpc = {item['name']: item for item in lib.results('GPC分子量')}
    yields = {item['name']: item for item in lib.results('树脂.产率')}
    formulas = _formulas(lib, lots)
    films = _films(lib)
    aging = _aging(lib)
    chosen = lib.setting('研发台.代表膜') or {}
    units = _units(lib)
    experiment_records = {row['编号']: row['id'] for row in lib.rows('树脂实验')}
    codes_of_lot, lots_of_code = {}, {}
    for item in formulas:
        if not item['lot'] or not item['code']:
            continue
        codes_of_lot.setdefault(item['lot'], set()).add(item['code'])
        lots_of_code.setdefault(item['code'], set()).add(item['lot'])

    rows = []
    for lot in lots:
        key = lot['批号']
        sample = samples.get(key) or {}
        cells, ids = {}, {}
        factors, factor_ids = _factor_cells(
            lib, lot['实验编号'], experiment_records.get(lot['实验编号']) or lot['id'])
        cells.update(factors)
        ids.update(factor_ids)
        gpc_cells, gpc_ids = _sample(sample, gpc)
        for name, value in gpc_cells.items():
            cells[name] = cell(name, value, units.get(name), 'measured')
        ids.update(gpc_ids)
        value, source = _yield(lot, sample, yields)
        cells['yield_pct'] = cell('yield_pct', value, '%', 'measured')
        if source:
            ids['yield_pct'] = source
        codes = sorted(codes_of_lot.get(key) or ())
        film = _representative(lot, [f for code in codes for f in films.get(code, [])], chosen)
        for name, _ in FILM_OPTICS:
            cells[name] = cell(name, film['cells'][name] if film else None, '1')
        if film:
            ids.update({name: film['id'] for name, _ in FILM_OPTICS})
        cells['thickness_nm'] = cell('thickness_nm', film['cells']['thickness_nm'] if film else None, 'nm')
        cells['solva_loss_pct'] = cell('solva_loss_pct', film['cells']['solva_loss_pct'] if film else None, '%')
        if film:
            ids['thickness_nm'] = ids['solva_loss_pct'] = film['id']
        aging_item = next((aging[code][0] for code in codes
                           if lots_of_code.get(code) == {key} and aging.get(code)), None)
        cells['a500'] = cell('a500', aging_item['a500'] if aging_item else None, '1',
                             aging_item['state'] if aging_item else 'measured')
        if aging_item:
            ids['a500'] = aging_item['id']
        rows.append({'lot_key': key, 'experiment_id': lot['实验编号'], 'lot_id': lot['id'],
                     'cells': cells, 'ids': ids, 'formulas': codes,
                     'film': {'id': film['id'] if film else None,
                              'label': film['name'] if film else '',
                              'code': film['code'] if film else None}})
    return rows


def lot_table(lib=None):
    """树脂批总表：列、单位、行。单元格是 {'v': 显示值, 'id': 记录 id} 或 None。"""
    lib = _lib(lib)
    rows = lot_rows(lib)
    units = _units(lib)
    labels = {item['id']: item['label'] for item in _factor_defs(lib)}
    labels.update({key: label for key, label, _ in RESPONSES})
    keys = ['lot_key'] + COLUMN_FACTORS + COLUMN_RESPONSES + ['formula_code']
    labels['lot_key'] = '实验/样品'
    labels['formula_code'] = '配方'
    columns = [labels.get(key, key) for key in keys]
    table = []
    for row in rows:
        line = [{'v': row['lot_key'], 'id': row['lot_id']}]
        for key in COLUMN_FACTORS + COLUMN_RESPONSES:
            item = row['cells'].get(key) or {}
            if item.get('value') is None:
                line.append(None)
            else:
                line.append({'v': item['display'], 'id': row['ids'].get(key)})
        line.append('、'.join(row['formulas']) or None)
        table.append(line)
    return {'columns': columns,
            'keys': keys,
            'units': {label: units.get(key) or '' for key, label in zip(keys, columns)},
            'rows': table,
            'factors': list(COLUMN_FACTORS),
            'responses': list(COLUMN_RESPONSES)}


# ---------- 旧 service.py 的统计（原样搬） ----------

def _maximum_compatible(rows, keys, factor_id=None):
    """最大团：缺值是通配，不是相等传递。"""
    rows = sorted(rows, key=lot_sort_key)
    neighbors = {}
    for i, row in enumerate(rows):
        neighbors[i] = {j for j, other in enumerate(rows) if j != i and all(
            not known(row['cells'].get(k, {})) or not known(other['cells'].get(k, {})) or
            same(row['cells'][k]['value'], other['cells'][k]['value']) for k in keys)}
    best = []

    def search(chosen, possible):
        nonlocal best
        population = chosen + possible
        if factor_id is not None and (len(population) < 2 or not any(
                not same(rows[population[0]]['cells'][factor_id]['value'], rows[i]['cells'][factor_id]['value'])
                for i in population[1:])):
            return
        varies = factor_id is None or any(not same(rows[chosen[0]]['cells'][factor_id]['value'],
            rows[i]['cells'][factor_id]['value']) for i in chosen[1:])
        if len(chosen) > len(best) and varies:
            best = chosen[:]
        while possible and len(chosen) + len(possible) > len(best):
            v = possible.pop(0)
            search(chosen + [v], [p for p in possible if p in neighbors[v]])
    search([], list(range(len(rows))))
    return [rows[i] for i in best]


def _control(rows, factor_id, result_id, core, manual=False):
    keys = [k for k in core if k != factor_id]
    eligible = [r for r in rows if result_filled(r['cells'].get(result_id, {}))
                and known(r['cells'].get(factor_id, {}))]
    if manual:
        return sorted(eligible, key=lot_sort_key)
    eligible = [r for r in eligible if sum(not known(r['cells'].get(k, {})) for k in keys) < 2]
    return _maximum_compatible(eligible, keys, factor_id)


def _pair_candidates(rows, lookup, core):
    candidates = []
    for a, b in combinations(sorted(rows, key=lambda r: r['lot_key']), 2):
        differences = []
        unknown = any(not known(r['cells'].get(k, {})) for r in (a, b) for k in core)
        for key, f in lookup.items():
            difference = condition_difference(a, b, key, f)
            if difference:
                differences.append(difference)
        if differences:
            candidates.append((a, b, differences, unknown))
    return candidates


def _pairs(candidates, factor_id, result_id, core):
    pairs = []
    for a, b, differences, unknown in candidates:
        selected = any(d['factor_id'] == factor_id for d in differences)
        # 只差一个核心因子的对照也留着，作为参考配对
        if not selected and not (len(differences) == 1 and differences[0]['factor_id'] in core):
            continue
        ac, bc = a['cells'].get(result_id, {}), b['cells'].get(result_id, {})
        both_measured = all(measured(c) for c in (ac, bc))
        strict = selected and len(differences) == 1 and not unknown and both_measured
        one_sided = [d for d in differences if d['evidence_state'] == 'one_sided']
        reference_reason = '；'.join(
            f"{d['label']}：{a['lot_key']} {d['a_display']}／{b['lot_key']} {d['b_display']}"
            '（单边记录，不能确认条件相同）' for d in one_sided)
        confirmed_extra = [d for d in differences if d['factor_id'] != factor_id
                           and d['evidence_state'] == 'different']
        if confirmed_extra:
            reference_reason = '；'.join(filter(None, [reference_reason, '另有确证条件差异：' + '；'.join(
                f"{d['label']}：{a['lot_key']} {d['a_display']}／{b['lot_key']} {d['b_display']}"
                for d in confirmed_extra)]))
        if unknown:
            reference_reason = '；'.join(filter(None, [reference_reason, '部分核心因子未记录。']))
        states = [c.get('state', 'missing') for c in (ac, bc)]
        delta, delta_state, reason = None, 'measured', ''
        if both_measured:
            value = bc['value'] - ac['value']
            if finite(value):
                delta = {'value': value, 'display': format_value(result_id, value)}
            else:
                delta_state, reason = 'not_computable', '差值不是有限数值。'
        else:
            delta_state = next((state for state in ('planned', 'to_confirm', 'missing', 'censored', 'design', 'text')
                                if state in states), 'not_computable')
            reason = {'planned': '计划值',
                      'missing': '至少一批结果缺失，不能计算差值。',
                      'censored': '包含超量程或下限结果，不能计算精确差值。'}.get(
                          delta_state, '需要双方有限数值的实测结果才能计算差值。')
        if delta is None:
            strict = False
            reference_reason = '；'.join(filter(None, [reference_reason, reason]))
        pairs.append({'a': a['lot_key'], 'b': b['lot_key'], 'strict': strict,
                      'partial_unknown': unknown, 'approximate': selected and not strict,
                      'pair_type': 'strict' if strict else 'reference',
                      'reference_reason': reference_reason,
                      'differs': differences, 'a_y': ac.get('value'), 'b_y': bc.get('value'),
                      'a_state': states[0], 'b_state': states[1], 'delta': delta,
                      'delta_state': delta_state, 'delta_reason': reason,
                      'film': {'a_label': a['film']['label'], 'b_label': b['film']['label']}})
    pairs.sort(key=lambda p: (not (len(p['differs']) == 1 and p['differs'][0]['factor_id'] == factor_id),
                             p['partial_unknown'], not any(d['factor_id'] == factor_id for d in p['differs']),
                             not p['strict'], p['a'], p['b']))
    return pairs


def _summary(pairs, result_id):
    summary = {}
    for kind in ('strict', 'reference'):
        selected = [p for p in pairs if p['pair_type'] == kind]
        values = [p['delta']['value'] for p in selected if p['delta'] is not None]
        mid = median(values) if values else None
        summary[kind] = {'count': len(selected), 'valid_deltas': len(values),
                         'higher': sum(v > 0 for v in values), 'lower': sum(v < 0 for v in values),
                         'equal': sum(v == 0 for v in values),
                         'median_delta': mid,
                         'median_display': '—' if mid is None else format_value(result_id, mid)}
    return summary


def _reading(rows, control, pairs, factor, result, lookup, core):
    """读实际条件和结果，把不确定一起写出来。"""
    index = {r['lot_key']: r for r in rows}
    strict = [p for p in pairs if p['strict']]
    if strict:
        selected = [index[strict[0]['a']], index[strict[0]['b']]]
        kind = '实测严格配对'
    elif len(control) == 2:
        selected, kind = list(control), '参考比较'
    elif len(rows) == 2:
        selected, kind = list(rows), '参考比较'
    else:
        return '可逐批查看所选条件和结果；当前没有可直接读取的实测对照，仅描述记录，不作因果推断。'
    fid, rid = factor['id'], result['id']
    if all(finite(r['cells'].get(fid, {}).get('value')) for r in selected):
        selected.sort(key=lambda r: (r['cells'][fid]['value'], r['lot_key']))
    else:
        selected.sort(key=lambda r: r['lot_key'])
    a, b = selected

    def display(row, key):
        c = row['cells'].get(key, {})
        if not known(c):
            return '未记录'
        prefix = '计划 ' if c.get('state') == 'planned' else ''
        if lookup.get(key, {}).get('unit') == 'w/w' and finite(c['value']):
            return prefix + format_value(key, c['value'] * 100) + '%'
        return prefix + c.get('display', str(c['value']))

    def unit_suffix(unit):
        return ' ' + unit if unit and unit not in ('1', 'w/w') else ''

    reading = (f"{a['lot_key']}→{b['lot_key']}：{factor['label']} "
               f"{display(a, fid)}→{display(b, fid)}{unit_suffix(factor.get('unit'))}，"
               f"{result['label']} {display(a, rid)}→{display(b, rid)}{unit_suffix(result.get('unit'))}")
    ac, bc = a['cells'].get(rid, {}), b['cells'].get(rid, {})
    if all(measured(c) for c in (ac, bc)):
        delta = bc['value'] - ac['value']
        if finite(delta):
            reading += '，结果相同' if delta == 0 else (
                '，' + ('升高 ' if delta > 0 else '降低 ') + format_value(rid, abs(delta)) +
                (' 个百分点' if result.get('unit') == '%' else unit_suffix(result.get('unit'))))
    else:
        reading += '，仅实测结果可计算差值'
    reading += f'（2批，{kind}）'
    extras = [condition_difference(a, b, key, metadata) for key, metadata in lookup.items() if key != fid]
    extras = [d for d in extras if d]
    if extras:
        reading += '；另有' + '；'.join(
            f"{d['label']} {display(a, d['factor_id'])}→{display(b, d['factor_id'])}" +
            ('（单边记录）' if d['evidence_state'] == 'one_sided' else '不同') for d in extras)
    if any(not known(r['cells'].get(k, {})) for r in selected for k in core):
        reading += '；部分核心条件未记录'
    if len(strict) > 1:
        reading += f'；共有 {len(strict)} 对实测严格配对'
    return reading + '；仅描述记录，不作因果推断。'


# ---------- 对外 ----------

def factor_compare(lib=None, factor=None, response=None):
    """一个因子 × 一个结果：点、最大可比对照组、配对、汇总、一句话读法。"""
    lib = _lib(lib)
    rows = lot_rows(lib)
    lookup = _lookup(lib)
    core = _core(lib)
    factor_def = next(item for item in _factor_defs(lib) if item['id'] == factor)
    response_def = next({'id': key, 'label': label, 'unit': unit}
                        for key, label, unit in RESPONSES if key == response)
    control = _control(rows, factor, response, core)
    comparable = [row for row in rows if row['cells'].get(factor, {}).get('state') != 'to_confirm']
    pairs = _pairs(_pair_candidates(comparable, lookup, core), factor, response, core)
    a_of = {row['lot_key']: row['cells'].get(factor, {}).get('value') for row in rows}
    points = [{'lot': row['lot_key'], 'x': row['cells'].get(factor, {}).get('value'),
               'y': row['cells'].get(response, {}).get('value'),
               'id': row['ids'].get(response)} for row in rows]
    out_pairs = []
    for pair in pairs:
        dx = None
        if finite(a_of.get(pair['a'])) and finite(a_of.get(pair['b'])):
            dx = a_of[pair['b']] - a_of[pair['a']]
        out_pairs.append({'a': pair['a'], 'b': pair['b'], 'dx': dx,
                          'dy': pair['delta']['value'] if pair['delta'] else None,
                          'strict': pair['strict'], 'pair_type': pair['pair_type'],
                          'differs': [d['factor_id'] for d in pair['differs']],
                          'note': pair['reference_reason']})
    return {'factor': factor_def, 'response': response_def, 'points': points,
            'control': [row['lot_key'] for row in control], 'pairs': out_pairs,
            'summary': _summary(pairs, response),
            'reading': _reading(rows, control, pairs, factor_def, response_def, lookup, core)}


def recommendations(lib=None):
    """值得看的因子-结果组合：实测严格配对多的、控制组大的排前面，最多四个。"""
    lib = _lib(lib)
    rows = lot_rows(lib)
    lookup = _lookup(lib)
    core = _core(lib)
    factors = _factor_defs(lib)
    candidates = _pair_candidates(rows, lookup, core)
    ranked = []
    for factor in factors:
        fid = factor['id']
        for key, label, unit in RESPONSES:
            observed = [r for r in rows if measured(r['cells'].get(key, {}))]
            if len(observed) < 2:
                continue
            group = _control(observed, fid, key, core)
            strict = [p for p in _pairs(candidates, fid, key, core) if p['strict'] and p['delta'] is not None]
            if not strict and len(group) < 2:
                continue
            lots = sorted({r['lot_key'] for r in group} | {p[k] for p in strict for k in ('a', 'b')},
                          key=lambda k: lot_sort_key({'lot_key': k}))
            item = {'factor_id': fid, 'result_id': key, 'factor_label': factor['label'],
                    'title': factor['label'] + ' × ' + label,
                    'result_label': label, 'result_unit': unit, 'strict_pair_count': len(strict),
                    'control_group_size': len(group), 'lot_keys': lots,
                    'reason': f'{len(strict)} 对实测严格配对，{len(group)} 批有效控制组；仅供比较记录。'}
            ranked.append(((-len(strict), -len(group), [lot_sort_key({'lot_key': k}) for k in lots], fid, key), item))
    ranked.sort(key=lambda entry: entry[0])
    return {'items': [item for _, item in ranked[:4]],
            'reason': '按实测严格配对数、有效控制组大小及更新批次排序；仅比较记录，不作因果推断。'
                      if ranked else '暂时没有可推荐的问题；你仍可选择因子和结果，查看已有记录。'}
