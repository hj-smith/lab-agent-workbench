"""膜厚–固含曲线：几个固含配出来的膜厚连成曲线，反算要多少固含才能到目标膜厚，再算一批料要补多少溶剂。

拟合用配方模块原样搬来的 pilot_fit（线性、二次、双对数幂函数；按 AICc 推荐），固含在库里存的是分数（0.0384），页面上写成 %。
点可以从膜里取（同一转速、烘烤条件，按配方的固含），也可以手填或贴进来；每个点能勾掉不参与拟合（照样画出来）。
"""
import math
from collections import defaultdict

import numpy as np

from . import store
from .present import clean
from .sci.vendor.formulation import pilot_fit

KIND = '膜厚曲线'
ABOUT = '膜厚–固含曲线：点（固含、膜厚、纳入）、成膜条件、选的模型、目标膜厚。反算目标膜厚要的固含和稀释量。'
MODELS = {'linear': '直线', 'quadratic': '二次', 'power': '幂函数（双对数直线）'}


def _ensure(lib):
    if not lib.kind(KIND):
        lib.define(KIND, ABOUT, [{'key': '条件'}, {'key': '点'}, {'key': '模型'}, {'key': '目标膜厚', 'unit': 'nm'}], '方法')


def film_points(library):
    """库里能当点用的膜：按（转速、烘烤温度、烘烤时间）分组，每块膜一个点（配方固含、处理前平均膜厚）。"""
    lib = store.open(library)
    solids = {(row.get('配方编号') or row['name']): row.get('固含') for row in lib.rows('配方')} if lib.kind('配方') else {}
    groups = defaultdict(list)
    for film in lib.rows('膜') if lib.kind('膜') else []:
        code = film.get('配方编号')
        stage = (((lib.get(film['id'])['results'].get('椭偏.块统计') or {}).get('value') or {}).get('各阶段') or [{}])[0]
        thick = (stage.get('处理前') or {}).get('均值 nm')
        if not solids.get(code) or thick is None:
            continue
        key = ' · '.join(part for part in (f'{film["转速"]:g} rpm' if film.get('转速') else '', f'{film["烘烤温度"]:g} °C' if film.get('烘烤温度') else '',
                                           f'{film["烘烤时间"]:g} s' if film.get('烘烤时间') else '') if part) or '没写条件'
        groups[key].append({'固含': solids[code], '膜厚': thick, '纳入': True, '来源': f'{film["name"]}（{code}）'})
    return [{'条件': key, '点': items, '固含水平': len({p['固含'] for p in items})} for key, items in sorted(groups.items(), key=lambda kv: -len(kv[1]))]


def analyse(points, target=None, mass=None, solids_now=None):
    """三个模型都拟合，按 AICc 推荐；画线用的点；目标膜厚反算固含；一批料要补多少溶剂。"""
    rows = [{'x': float(p['固含']), 'y': float(p['膜厚']), 'include': p.get('纳入', True) is not False} for p in points
            if p.get('固含') not in (None, '') and p.get('膜厚') not in (None, '')]
    fits = {}
    problems = {}
    for model in MODELS:
        try:
            fits[model] = pilot_fit.fit_model(rows, model)
        except Exception as error:                       # 点不够、幂函数遇到 0……：这个模型画不了，写原因
            problems[model] = str(error)
    best, reason = pilot_fit.recommend_model(fits) if fits else (None, '点不够，还拟合不了')
    xs = [row['x'] for row in rows]
    lines = {}
    if xs:
        lo, hi = min(xs), max(xs)
        span = (hi - lo) or hi * 0.2 or 0.01
        grid = np.linspace(max(lo - span * 0.25, 1e-6), hi + span * 0.25, 80)
        for model, fit in fits.items():
            lines[model] = {'x': grid.tolist(), 'y': [pilot_fit.predict(fit, float(x)) for x in grid]}
    table = [{'model': model, 'label': MODELS[model], 'r2': fit.get('r_squared'), 'rmse': fit.get('rmse'), 'aicc': fit.get('aicc'), 'n': fit.get('n'),
              'recommended': fit.get('recommended', False), 'warnings': fit.get('warnings') or [], 'coefficients': fit.get('coefficients')}
             for model, fit in fits.items()]
    answer = {}
    if target and best:
        for model, fit in fits.items():
            try:
                need = pilot_fit.invert(fit, float(target))
            except Exception as error:
                answer[model] = {'problem': str(error)}
                continue
            item = {'solids': need, 'outside': not (min(xs) <= need <= max(xs))}
            if mass and solids_now and need:
                add = float(mass) * (float(solids_now) / need - 1)          # 树脂量不变：m0·s0 = (m0 + 加的溶剂)·s1
                item['solvent'] = add
            answer[model] = item
    return clean({'fits': table, 'problems': problems, 'best': best, 'reason': reason, 'lines': lines,
                  'residuals': {model: fit.get('residual_rows') for model, fit in fits.items()}, 'answer': answer})


def view(library, ref):
    lib = store.open(library)
    record = lib.get(ref, KIND)
    data = record['data']
    return {'id': record['id'], 'name': record['name'], 'condition': data.get('条件') or '', 'points': data.get('点') or [], 'model': data.get('模型') or '',
            'target': data.get('目标膜厚'), **analyse(data.get('点') or [], data.get('目标膜厚'))}


def save(library, ref, data):
    """新建或改一条曲线：名字、条件、点、选的模型、目标膜厚。"""
    lib = store.open(library)
    _ensure(lib)
    row = {'name': data.get('name') or '膜厚曲线', '条件': data.get('condition') or '', '点': data.get('points') or [], '模型': data.get('model') or '',
           '目标膜厚': data.get('target')}
    if ref:
        row['id'] = ref
    done = lib.save(KIND, [row], by='人')
    return view(library, done[0]['id'])


def listing(library):
    lib = store.open(library)
    rows = lib.rows(KIND) if lib.kind(KIND) else []
    return [{'id': row['id'], 'name': row['name'], 'condition': row.get('条件') or '', 'points': len(row.get('点') or []), 'updated': row.get('updated_at')} for row in rows]
