"""GPC 标准曲线工作台：分子量可不可信全看标准曲线。

库里各项目各存一份同一条曲线（同来源指纹、同系数），这里按来源指纹归成"一条曲线 + 几份副本"，用量把副本加起来；
确认、有效期、停用改了整组副本一起改。曲线本身用存着的系数（和算分子量时一样），试改时用原件的拟合函数重拟合。
"""
import math
import re
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime

import numpy as np

from . import kernel, store
from .present import clean
from .sci import gpc
from gpcsec.core.calibration.models import fit_pchip       # noqa: E402  gpc 已把原件放进路径；同一份类，isinstance 才认

KIND = 'GPC标准曲线'


def _key(row):
    return row.get('来源sha256') or row['id']


def _plain(name):
    """副本的名字去掉"（复制 N）"，当一条看；人起的"（改）"留着。"""
    return re.sub(r'（复制\s*\d*）$', '', name)


def _date(name):
    digits = ''.join(ch for ch in name if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ''


def _usage(lib):
    count = defaultdict(list)
    for row in lib.rows('GPC样品'):
        if row.get('标准曲线id'):
            count[row['标准曲线id']].append(row['id'])
    return count


def _expired(row, today=None):
    until = str(row.get('有效期至') or '')[:10]
    return bool(until) and until < (today or datetime.now(kernel.BEIJING).strftime('%Y-%m-%d'))


def groups(library):
    """8 条真正不同的曲线：代表记录（用得最多的那份）、副本、总用量、状态。给左栏和"和别的曲线比"。"""
    lib = store.open(library)
    usage = _usage(lib)
    found = defaultdict(list)
    for row in lib.rows(KIND):
        found[_key(row)].append(row)
    out = []
    for key, rows in found.items():
        main = max(rows, key=lambda row: (len(usage[row['id']]), row['updated_at']))
        out.append({'id': main['id'], 'name': _plain(main['name']), 'date': _date(main['name']), 'copies': len(rows),
                    'uses': sum(len(usage[row['id']]) for row in rows), 'model': main.get('模型') or 'polynomial', 'degree': main.get('次数'),
                    'confirmed': bool(main.get('确认')), 'until': main.get('有效期至'), 'off': bool(main.get('停用')), 'expired': _expired(main)})
    return sorted(out, key=lambda item: item['date'] or item['name'], reverse=True)


def _record(lib, ref):
    return gpc._calibration_record(lib, ref)


def _points(record):
    return [(float(t), float(m), bool(on)) for t, m, on in record['_points']]


def _fit(record, enabled, degree):
    """按勾着的点重拟合（多项式用原件 fit_polynomial，PCHIP 用 fit_pchip）。"""
    points = tuple(gpc.CalibrationPoint(t, m, on) for (t, m, _), on in zip(_points(record), enabled))
    if str(record['data'].get('模型') or 'polynomial').casefold() == 'pchip':
        return fit_pchip(points, record['data'].get('来源sha256'))
    return gpc.fit_polynomial(points, int(degree), record['data'].get('来源sha256'))


def _log_m(cal, rt, extrapolate=False):
    """log M；有效范围外给 NaN（多项式要外推时 extrapolate=True）。"""
    rt = np.asarray(rt, dtype=float)
    inside = (rt >= cal.valid_rt_min - 1e-9) & (rt <= cal.valid_rt_max + 1e-9)
    if isinstance(cal, gpc.PolynomialCalibration):
        out = cal.evaluate_log_m(rt, allow_extrapolation=True)
        return out if extrapolate else np.where(inside, out, np.nan)
    out = np.full(rt.shape, np.nan)
    out[inside] = cal.evaluate_log_m(np.clip(rt[inside], cal.valid_rt_min, cal.valid_rt_max))
    return out


def _line(cal, points):
    """画线：有效范围内实线，标准点超出范围的那一截虚线（多项式才外推）。"""
    lo, hi = cal.valid_rt_min, cal.valid_rt_max
    span = [min([lo] + [p[0] for p in points]), max([hi] + [p[0] for p in points])]
    inside = np.linspace(lo, hi, 160)
    out = {'inside': {'rt': inside.tolist(), 'logM': _log_m(cal, inside).tolist()}, 'outside': []}
    if isinstance(cal, gpc.PolynomialCalibration):
        for a, b in ((span[0], lo), (hi, span[1])):
            if b - a > 1e-6:
                grid = np.linspace(a, b, 30)
                out['outside'].append({'rt': grid.tolist(), 'logM': _log_m(cal, grid, True).tolist()})
    return out


def _residuals(cal, points):
    rows = []
    for t, m, on in points:
        fitted = float(_log_m(cal, [t], True)[0]) if isinstance(cal, gpc.PolynomialCalibration) or cal.valid_rt_min <= t <= cal.valid_rt_max else float('nan')
        rows.append({'rt': t, 'M': m, 'logM': math.log10(m), 'on': on, 'fit': 10 ** fitted if math.isfinite(fitted) else None,
                     'dev': (10 ** fitted / m - 1) if math.isfinite(fitted) else None})
    return rows


def _max_diff(a, b):
    """两条曲线在共同有效范围内 M 的最大相对差（%）。"""
    lo, hi = max(a.valid_rt_min, b.valid_rt_min), min(a.valid_rt_max, b.valid_rt_max)
    if hi <= lo:
        return None
    grid = np.linspace(lo, hi, 200)
    return float(np.nanmax(np.abs(10 ** (_log_m(b, grid) - _log_m(a, grid)) - 1)) * 100)


def _influence(record, cal, enabled, degree):
    """F4：去掉每个点重拟合，有效范围内 M 最多变多少 %。去掉后拟合不了的写 None。"""
    out = []
    for index, on in enumerate(enabled):
        if not on:
            out.append(None)
            continue
        trial = list(enabled)
        trial[index] = False
        try:
            out.append(_max_diff(cal, _fit(record, trial, degree)))
        except Exception:
            out.append(None)
    return out


def view(library, ref):
    lib = store.open(library)
    record = _record(lib, ref)
    data = record['data']
    cal = gpc.calibration(record)
    points = _points(record)
    enabled = [p[2] for p in points]
    degree = int(data.get('次数') or 3)
    usage = _usage(lib)
    copies = [row for row in lib.rows(KIND) if _key(row) == _key({**data, 'id': record['id']})]
    samples = [sid for row in copies for sid in usage[row['id']]]
    stats = data.get('拟合统计') or {}
    return clean({'id': record['id'], 'name': _plain(record['name']), 'file': data.get('来源文件名'), 'imported': data.get('导入时间'),
                  'model': data.get('模型') or 'polynomial', 'degree': degree, 'valid': [cal.valid_rt_min, cal.valid_rt_max],
                  'valid_M': [10 ** float(_log_m(cal, [cal.valid_rt_max])[0]), 10 ** float(_log_m(cal, [cal.valid_rt_min])[0])],
                  'r2': data.get('R²') or stats.get('r_squared'), 'rmse': data.get('RMSE') or stats.get('rmse'),
                  'confirmed': bool(data.get('确认')), 'until': data.get('有效期至'), 'off': bool(data.get('停用')), 'expired': _expired(data),
                  'copies': [{'id': row['id'], 'project': row.get('所属项目'), 'uses': len(usage[row['id']])} for row in copies],
                  'samples': samples, 'points': _residuals(cal, points), 'line': _line(cal, points),
                  'influence': _influence(record, cal, enabled, degree), 'others': [item for item in groups(library) if item['id'] != record['id']]})


def _mw_with(lib, sample_id, cal):
    sample, record, _, time, signal, base, picked = gpc._prepare(lib, sample_id)
    calibrated = gpc.integrate_regions(time, signal, base.baseline, picked, calibration_bounds=(cal.valid_rt_min, cal.valid_rt_max))
    return gpc.calculate_molecular_weight(calibrated, cal).mw


def trial(library, ref, enabled, degree):
    """试改：按勾着的点和次数重拟合，和存着的曲线比，再看用这条曲线的样 Mw 变多少（不存）。"""
    lib = store.open(library)
    record = _record(lib, ref)
    old = gpc.calibration(record)
    try:
        new = _fit(record, enabled, degree)
    except Exception as error:
        return {'problem': f'拟合不了：{error}'}
    points = [(t, m, on) for (t, m, _), on in zip(_points(record), enabled)]
    usage = _usage(lib)
    copies = [row['id'] for row in lib.rows(KIND) if _key(row) == _key({**record['data'], 'id': record['id']})]
    changes = []
    for sid in [sid for cid in copies for sid in usage[cid]]:
        try:
            before = gpc.analyze(sid, lib, save=False)['Mw']
            changes.append(_mw_with(lib, sid, new) / before - 1)
        except Exception:
            continue
    stats = new.statistics
    return clean({'r2': stats.r_squared if stats else None, 'rmse': stats.rmse if stats else None, 'valid': [new.valid_rt_min, new.valid_rt_max],
                  'diff': _max_diff(old, new), 'line': _line(new, points), 'points': _residuals(new, points),
                  'mw': {'n': len(changes), 'min': min(changes) if changes else None, 'max': max(changes) if changes else None,
                         'median': float(np.median(changes)) if changes else None}})


def save_new(library, ref, enabled, degree, name):
    """试改好的存成一条新曲线（原曲线不动），记父曲线；要用就去批处理里换。"""
    lib = store.open(library)
    record = _record(lib, ref)
    data = record['data']
    new = _fit(record, enabled, degree)
    rows = [[t, m, on] for (t, m, _), on in zip(_points(record), enabled)]
    fields = {'name': name, '所属项目': data.get('所属项目'), '模型': data.get('模型') or 'polynomial', '次数': int(degree),
              '有效范围起': new.valid_rt_min, '有效范围止': new.valid_rt_max, '单调方向': new.monotonic_direction, '算法版本': new.algorithm_version,
              '来源文件名': data.get('来源文件名'), '来源sha256': None, '证据类型': '人在标准曲线工作台改点重拟合', '父曲线': record['name'],
              '导入时间': datetime.now(kernel.BEIJING).strftime('%Y-%m-%d %H:%M:%S'), 'series': {'标样点': {'columns': ['保留时间 / min', 'M / (g/mol)', '启用'], 'rows': rows}},
              'links': [{'relation': '派生自', 'to': record['id'], 'by': '人'}]}
    if isinstance(new, gpc.PolynomialCalibration):
        fields.update({'系数': list(new.coefficients), '缩放系数': list(new.scaled_coefficients), 'R²': new.statistics.r_squared,
                       'RMSE': new.statistics.rmse, '拟合统计': asdict(new.statistics)})
    done = lib.save(KIND, [fields], by='人')
    return {'id': done[0]['id'], 'name': name}


def save_status(library, ref, values):
    """确认、有效期至、停用：整组副本一起改。"""
    lib = store.open(library)
    record = lib.get(ref, KIND)
    key = _key({**record['data'], 'id': record['id']})
    patch = {name: values[field] for field, name in (('confirmed', '确认'), ('until', '有效期至'), ('off', '停用')) if field in values}
    lib.save(KIND, [{'id': row['id'], **patch} for row in lib.rows(KIND) if _key(row) == key], by='人')
    return {'ok': True}


def compare(library, ref, others):
    """F5/F10：几条曲线叠在一起；对这条的 M 偏差 %；同一 M 的保留时间挪了几秒。"""
    lib = store.open(library)
    base = gpc.calibration(_record(lib, ref))
    grid = np.linspace(base.valid_rt_min, base.valid_rt_max, 160)
    base_log = _log_m(base, grid)
    levels = [m for m in (1e3, 3e3, 1e4, 3e4, 1e5) if np.nanmin(base_log) <= math.log10(m) <= np.nanmax(base_log)]

    def rt_at(cal, log_m):
        g = np.linspace(cal.valid_rt_min, cal.valid_rt_max, 400)
        v = _log_m(cal, g)
        order = np.argsort(v)
        return float(np.interp(log_m, v[order], g[order])) if v.min() <= log_m <= v.max() else None

    rows = []
    for item in [ref] + list(others):
        record = _record(lib, item)
        cal = gpc.calibration(record)
        values = _log_m(cal, grid)
        diff = 10 ** (values - base_log) - 1
        inside = (grid >= cal.valid_rt_min) & (grid <= cal.valid_rt_max)
        shifts = {}
        for m in levels:
            a, b = rt_at(base, math.log10(m)), rt_at(cal, math.log10(m))
            shifts[f'{m:g}'] = (b - a) * 60 if a is not None and b is not None else None
        own = np.linspace(cal.valid_rt_min, cal.valid_rt_max, 160)
        rows.append({'id': record['id'], 'name': _plain(record['name']), 'date': _date(record['name']), 'model': record['data'].get('模型') or 'polynomial',
                     'curve': {'rt': own.tolist(), 'logM': _log_m(cal, own).tolist()},
                     'diff': {'rt': grid[inside].tolist(), 'pct': (diff[inside] * 100).tolist()},
                     'max': float(np.nanmax(np.abs(diff[inside])) * 100) if inside.any() else None, 'shift': shifts})
    return clean({'rows': rows, 'levels': [f'{m:g}' for m in levels]})
