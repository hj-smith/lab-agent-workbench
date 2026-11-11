"""GPC 实验演化：一组样品按反应时间排开，看分子量怎么长、分布怎么变、各分子量区段占多少、按动力学模型拟合。

对着旧 GPC 软件的"实验演化"页（services/experiment_analysis.py）：
- 趋势：Mn、Mw、Mz、Mz+1、Đ、Mp、D10、D50、D90 随反应时间；同一时间有几个重复样时给均值 ± SD。
- 热图、瀑布图：用存着的分布（dw/dlogM，200 点）插到同一条 log M 网格上。
- 分子量区段：本项目的"GPC分子量分区"，没有就用默认四段；质量分数 = 按 log M 积分的累积分数在上下限处之差。
- 动力学：照旧软件的线性化拟合（零级、一级、二级、通用级数 0.1–3.0），RMSE 最小的推荐。描述性拟合，不证明机理。
"""
import json
import math
from collections import Counter, defaultdict
from datetime import datetime

import numpy as np

from . import kernel, store
from .present import _hours, clean

KIND = 'GPC实验'
METRICS = ['Mn', 'Mw', 'Mz', 'Mz+1', 'PDI', 'Mp', 'D10', 'D50', 'D90']
COLUMNS = ['样品', '样品id', '反应时间', '时间单位', '重复', '纳入', '剔除原因', '因子水平', '批次', '备注', '成员旧id']
DEFAULT_BANDS = [{'name': '<10³', 'lower': None, 'upper': 1e3, 'color': '#0072B2'}, {'name': '10³–10⁴', 'lower': 1e3, 'upper': 1e4, 'color': '#56B4E9'},
                 {'name': '10⁴–10⁵', 'lower': 1e4, 'upper': 1e5, 'color': '#009E73'}, {'name': '≥10⁵', 'lower': 1e5, 'upper': None, 'color': '#E69F00'}]
QUARTILE_NAMES = ('低分子量区', '中低分子量区', '中高分子量区', '高分子量区')
QUARTILE_COLORS = ('#0072B2', '#56B4E9', '#009E73', '#E69F00')
MODELS = {'zero_order': '零级', 'first_order': '一级', 'second_order': '二级', 'general_order': '通用级数'}
BAND_KIND = 'GPC分子量分区'


# ---------- 成员 ----------

def _table(lib, record_id):
    try:
        columns, rows, _ = lib.table(record_id, '成员')
    except store.StoreError:
        return COLUMNS, []
    return columns, rows


def members(lib, record):
    """成员样品：名字、反应时间（h）、纳入、剔除原因、存着的分子量和分布。"""
    columns, rows = _table(lib, record['id'])
    at = {name: index for index, name in enumerate(columns)}
    ids = [row[at['样品id']] for row in rows]
    found = {}
    if ids:
        marks = ','.join('?' * len(ids))
        for record_id, name, project, mw, dist in lib.sql(
                f"SELECT r.id, r.name, json_extract(r.data,'$.所属项目'), a.value, b.value FROM records r "
                f"LEFT JOIN results a ON a.record_id=r.id AND a.method='GPC分子量' LEFT JOIN results b ON b.record_id=r.id AND b.method='GPC分布' "
                f"WHERE r.id IN ({marks}) AND r.deleted_at IS NULL", tuple(ids))[1]:
            found[record_id] = (name, project, json.loads(mw) if mw else {}, json.loads(dist) if dist else {})
    out = []
    for index, row in enumerate(rows):
        sample_id = row[at['样品id']]
        if sample_id not in found:
            continue
        name, project, mw, dist = found[sample_id]
        value = row[at['反应时间']] if '反应时间' in at else None
        hours = float(value) if isinstance(value, (int, float)) else _hours(name)
        if hours is not None and '时间单位' in at and row[at['时间单位']] in ('min', '分钟'):
            hours = hours / 60
        out.append({'id': sample_id, 'name': name, 'project': project, 'h': hours, 'row': index,
                    'include': bool(row[at['纳入']]) if '纳入' in at else True, 'reason': row[at['剔除原因']] if '剔除原因' in at else '',
                    'replicate': row[at['重复']] if '重复' in at else None, 'levels': row[at['因子水平']] if '因子水平' in at else {},
                    'mw': mw, 'dist': dist})
    out.sort(key=lambda item: (item['h'] is None, item['h'] or 0, item['name']))
    return out


# ---------- 分布 ----------

def _curve(dist):
    """存着的分布 → 按 log M 从小到大的 (logM, w)。"""
    if not dist.get('logM'):
        return None
    log_m, weight = np.asarray(dist['logM'], dtype=float), np.asarray(dist['dw/dlogM'], dtype=float)
    order = np.argsort(log_m)
    return log_m[order], np.clip(weight[order], 0, None)


def _cumulative(log_m, weight):
    area = np.concatenate(([0.0], np.cumsum(0.5 * (weight[1:] + weight[:-1]) * np.diff(log_m))))
    return area / area[-1] if area[-1] > 0 else area


def _fraction(log_m, cumulative, lower, upper):
    lo = 0.0 if lower is None else float(np.interp(math.log10(lower), log_m, cumulative, left=0.0, right=1.0))
    hi = 1.0 if upper is None else float(np.interp(math.log10(upper), log_m, cumulative, left=0.0, right=1.0))
    return max(hi - lo, 0.0)


def bands(lib, project):
    rows = [row for row in lib.rows(BAND_KIND) if row.get('所属项目') == project] if lib.kind(BAND_KIND) else []
    if not rows:
        return [dict(item) for item in DEFAULT_BANDS], False
    rows.sort(key=lambda row: row.get('下限') or 0)
    return [{'id': row['id'], 'name': row['name'].removeprefix(f'{project} ').strip(), 'lower': row.get('下限'), 'upper': row.get('上限'),
             'color': row.get('颜色') or '#0072B2'} for row in rows], True


def quartile_bands(curves):
    """按这组样品的数据四等分（照旧软件 adaptive_molecular_weight_bands：所有分布平均后按累积面积 25/50/75 %）。"""
    grid = np.linspace(min(c[0][0] for c in curves), max(c[0][-1] for c in curves), 400)
    pooled = np.mean([np.interp(grid, c[0], c[1], left=0, right=0) for c in curves], axis=0)
    cumulative = _cumulative(grid, pooled)
    cuts = [float(grid[0]), *np.interp((0.25, 0.5, 0.75), cumulative, grid), float(grid[-1])]
    return [{'name': QUARTILE_NAMES[i], 'lower': 10 ** cuts[i], 'upper': 10 ** cuts[i + 1], 'color': QUARTILE_COLORS[i]} for i in range(4)]


# ---------- 动力学（照旧软件的线性化拟合） ----------

def _fit(x, y, model, order=None):
    if model != 'zero_order' and np.any(y <= 0):
        raise ValueError('一级、二级、通用级数要求指标都大于 0')
    if model == 'zero_order':
        transformed, exponent = y, 0.0
    elif model == 'first_order':
        transformed, exponent = np.log(y), 1.0
    elif model == 'second_order':
        transformed, exponent = 1.0 / y, 2.0
    else:
        transformed, exponent = np.power(y, 1.0 - order), float(order)
    slope, intercept = np.polyfit(x, transformed, 1)
    if model == 'zero_order':
        return float(intercept), float(slope), intercept + slope * x, exponent
    if model == 'first_order':
        initial = math.exp(intercept)
        return initial, float(slope), initial * np.exp(slope * x), exponent
    if intercept <= 0:
        raise ValueError('线性化后初值不合理')
    base = intercept + slope * x
    if np.any(base <= 0):
        raise ValueError('拟合出了定义域')
    return float(intercept ** (1.0 / (1.0 - exponent))), float(slope / (1.0 - exponent)), np.power(base, 1.0 / (1.0 - exponent)), exponent


def _curve_of(model, y0, k, n, xs):
    xs = np.asarray(xs, dtype=float)
    if model == 'zero_order':
        return y0 + k * xs
    if model == 'first_order':
        return y0 * np.exp(k * xs)
    base = y0 ** (1.0 - n) + (1.0 - n) * k * xs
    return np.where(base > 0, np.power(np.clip(base, 1e-300, None), 1.0 / (1.0 - n)), np.nan)


def _equation(model, n):
    return {'zero_order': 'y = y0 + kx', 'first_order': 'y = y0·exp(kx)', 'second_order': '1/y = 1/y0 − kx'}.get(model, f'y^(1−{n:.3g}) = y0^(1−{n:.3g}) + (1−{n:.3g})kx')


def kinetics(points, models):
    """points: [(h, 值)]。每个模型取 RMSE 最小的一组；全部里 RMSE 最小的推荐。"""
    x = np.asarray([p[0] for p in points], dtype=float)
    y = np.asarray([p[1] for p in points], dtype=float)
    if len(points) < 3:
        return {'fits': [], 'warning': f'只有 {len(points)} 个时间点，至少 3 个才拟合'}
    xs = np.linspace(float(x.min()), float(x.max()), 120)
    fits = []
    for model in models:
        orders = [float(v) for v in np.linspace(0.1, 3.0, 30) if abs(v - 1.0) > 0.04] if model == 'general_order' else [None]
        best = None
        for order in orders:
            try:
                y0, k, fitted, n = _fit(x, y, model, order)
            except (ValueError, FloatingPointError, np.linalg.LinAlgError):
                continue
            if not np.all(np.isfinite(fitted)):
                continue
            residual = y - fitted
            ss_res, ss_tot = float(residual @ residual), float(((y - y.mean()) ** 2).sum())
            item = {'model': model, 'label': MODELS[model], 'order': n, 'k': k, 'y0': y0, 'r2': 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0,
                    'rmse': math.sqrt(ss_res / len(y)), 'equation': _equation(model, n)}
            if best is None or item['rmse'] < best['rmse']:
                best = item
        if best:
            best['curve'] = {'x': xs.tolist(), 'y': [None if not math.isfinite(v) else float(v) for v in _curve_of(model, best['y0'], best['k'], best['order'], xs)]}
            fits.append(best)
    if fits:
        min(fits, key=lambda item: item['rmse'])['recommended'] = True
    return {'fits': fits, 'warning': ''}


# ---------- 给页面的全貌 ----------

def view(library, ref):
    lib = store.open(library)
    record = lib.get(ref, KIND)
    data = record['data']
    people = members(lib, record)
    project = data.get('所属项目') or Counter(m['project'] for m in people).most_common(1)[0][0] if people else data.get('所属项目')
    used = [m for m in people if m['include'] and m['h'] is not None]
    # 趋势：每个指标的点；同一时间多个重复样给均值 ± SD
    trend = {}
    for metric in METRICS:
        points = [{'h': m['h'], 'v': m['mw'].get(metric), 'name': m['name'], 'include': m['include']} for m in people if m['h'] is not None and m['mw'].get(metric) is not None]
        groups = defaultdict(list)
        for point in points:
            if point['include']:
                groups[point['h']].append(point['v'])
        trend[metric] = {'points': points, 'mean': [{'h': h, 'v': float(np.mean(v)), 'sd': float(np.std(v, ddof=1)) if len(v) > 1 else None, 'n': len(v)}
                                                    for h, v in sorted(groups.items())]}
    # 分布：热图网格、瀑布
    curves = [(m, _curve(m['dist'])) for m in used]
    curves = [(m, c) for m, c in curves if c is not None]
    heat, waterfall = None, []
    if curves:
        grid = np.linspace(min(c[0][0] for _, c in curves), max(c[0][-1] for _, c in curves), 160)
        by_hour = defaultdict(list)
        for m, c in curves:
            by_hour[m['h']].append(np.interp(grid, c[0], c[1], left=0, right=0))
            waterfall.append({'name': m['name'], 'h': m['h'], 'logM': c[0].tolist(), 'w': c[1].tolist()})
        hours = sorted(by_hour)
        heat = {'h': hours, 'logM': grid.tolist(), 'z': np.asarray([np.mean(by_hour[h], axis=0) for h in hours]).T.tolist()}
    band_list, saved = bands(lib, project)
    cum = {m['id']: (c[0], _cumulative(*c)) for m, c in curves}
    fractions = [{'h': m['h'], 'name': m['name'], 'f': [_fraction(*cum[m['id']], band['lower'], band['upper']) for band in band_list]} for m, _ in curves]
    settings = data.get('动力学设置') or {}
    metric = settings.get('metric') if settings.get('metric') in METRICS else 'Mw'
    models = [item for item in settings.get('models') or [] if item in MODELS] or list(MODELS)
    fit_points = [(m['h'], m['mw'][metric]) for m in used if m['mw'].get(metric) is not None]
    hours_all = [m['h'] for m in people if m['h'] is not None]
    return clean({'id': record['id'], 'name': record['name'], 'about': data.get('说明') or '', 'project': project,
                  'members': [{key: m[key] for key in ('id', 'name', 'h', 'include', 'reason', 'replicate')} | {'Mw': m['mw'].get('Mw'), 'PDI': m['mw'].get('PDI')} for m in people],
                  'span': [min(hours_all), max(hours_all)] if hours_all else None, 'replicates': any(t['n'] > 1 for t in trend['Mw']['mean']),
                  'trend': trend, 'heat': heat, 'waterfall': waterfall,
                  'bands': band_list, 'bands_saved': saved, 'fractions': fractions, 'quartiles': quartile_bands([c for _, c in curves]) if curves else [],
                  'defaults': DEFAULT_BANDS,
                  'kinetics': {'metric': metric, 'models': models, **kinetics(fit_points, models)},
                  'candidates': [{'id': row['id'], 'name': row['name']} for row in lib.rows('GPC样品')
                                 if row.get('所属项目') == project and row['id'] not in {m['id'] for m in people}]})


# ---------- 改 ----------

def save_members(library, ref, items):
    """成员表：样品、反应时间、纳入、剔除原因。别的列（重复、因子水平、批次…）原样留着；新加的样品补上关联。"""
    lib = store.open(library)
    record = lib.get(ref, KIND)
    columns, rows = _table(lib, record['id'])
    at = {name: index for index, name in enumerate(columns)}
    old = {row[at['样品id']]: row for row in rows}
    out, links = [], []
    for item in items:
        row = list(old.get(item['id']) or [None] * len(columns))
        row[at['样品id']] = item['id']
        row[at['样品']] = item.get('name') or row[at['样品']]
        row[at['反应时间']] = item.get('h')
        if '时间单位' in at:
            row[at['时间单位']] = 'h'
        row[at['纳入']] = bool(item.get('include', True))
        row[at['剔除原因']] = item.get('reason') or ''
        if '因子水平' in at and row[at['因子水平']] is None:
            row[at['因子水平']] = {}
        out.append(row)
        if item['id'] not in old:
            links.append({'relation': '成员', 'to': item['id'], 'by': '人', 'note': f'{item.get("h")} h' if item.get('h') is not None else ''})
    lib.put_series(record['id'], '成员', columns, out)
    if links:
        lib.save(KIND, [{'id': record['id'], 'links': links}], by='人')
    return view(library, ref)


def save_bands(library, project, items):
    """本项目的分子量区段：有 id 的改，新的加，表里去掉的放进回收站。"""
    lib = store.open(library)
    if not lib.kind(BAND_KIND):
        lib.define(BAND_KIND, '分子量区段：名字、下限、上限（g/mol，空 = 开放端）、颜色；实验演化里算各区段的质量分数。', [], '方法')
    old = {row['id'] for row in lib.rows(BAND_KIND) if row.get('所属项目') == project}
    rows = [{**({'id': item['id']} if item.get('id') else {}), 'name': f'{project} {item["name"]}', '所属项目': project,
             '下限': item.get('lower'), '上限': item.get('upper'), '颜色': item.get('color') or '#0072B2'} for item in items]
    done = lib.save(BAND_KIND, rows, by='人') if rows else []
    gone = old - {item['id'] for item in done}
    if gone:
        lib.delete(list(gone))
    return {'ok': True}


def save_kinetics(library, ref, metric, models):
    lib = store.open(library)
    record = lib.get(ref, KIND)
    settings = {**(record['data'].get('动力学设置') or {}), 'metric': metric, 'models': models}
    lib.save(KIND, [{'id': record['id'], '动力学设置': settings}], by='人')
    return view(library, ref)


def create(library, name, ids):
    """勾的几个样品建成一个实验：反应时间从名字里读（3H → 3 h），读不出的空着让人填。"""
    lib = store.open(library)
    samples = [lib.get(ref, 'GPC样品') for ref in ids]
    project = Counter(s['data'].get('所属项目') for s in samples).most_common(1)[0][0]
    rows = [[s['name'], s['id'], _hours(s['name']), 'h', 1, True, '', {}, s['data'].get('批次'), '', None] for s in samples]
    done = lib.save(KIND, [{'name': name, '所属项目': project, '说明': '', '因子': [], '创建时间': datetime.now(kernel.BEIJING).strftime('%Y-%m-%d %H:%M'),
                            '动力学设置': {'metric': 'Mw', 'models': list(MODELS)}, 'series': {'成员': {'columns': COLUMNS, 'rows': rows}},
                            'links': [{'relation': '成员', 'to': s['id'], 'by': '人'} for s in samples]}], by='人')
    return {'id': done[0]['id']}
