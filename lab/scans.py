"""下一轮扫描建议的取数层：从配方和膜里找"只改一个用量、≥3 个水平"的单因素扫描，配上每个点的椭偏结果、参比值和离散度，
交给旧平台原样拷来的 scan_advice（lab/sci/vendor/guidance）出建议卡。结果不存，打开就现算。

照旧平台 analysis/algorithms.scans：系列 = 配方编号去掉末尾的大写字母；因子 = 每个添加剂的用量（{来源}:{口径}，值是百分数），
树脂和固含当作"其它不变"的条件；同一系列里其它因子都一样、这一个因子有 ≥3 个水平的一组算一个扫描。
"""
import re
from collections import defaultdict
from statistics import median

from . import store
from .present import clean
from .sci.vendor.guidance import scan_advice
from .sci.vendor.guidance.common import digest

METRICS = {'ellipsometry:n193': ('n193', 'n_193'), 'ellipsometry:k193': ('k193', 'k_193'),
           'ellipsometry:thickness_nm': ('处理前膜厚', None), 'ellipsometry:thickness_loss_pct@SolvA': ('损失率', None)}
REFERENCE = 'REF01'                    # 默认参比配方（约定口径里改了以后接过来）


def _factors(formula):
    out = {f'{item["source_id"]}:{item.get("basis")}': item.get('percent') or 0 for item in formula.get('添加剂') or []}
    return out, {'树脂': '+'.join(sorted(item['source_id'] for item in formula.get('树脂') or [])), '固含': formula.get('固含')}


def _film_values(lib, code):
    """一个配方每块膜的读数：[{膜, 测试日, n193, k193, 处理前膜厚, 损失率, 溶解}]。"""
    out = []
    for film in lib.rows('膜', where=f"json_extract(data,'$.配方编号') = '{code}'"):
        stage = (((lib.get(film['id'])['results'].get('椭偏.块统计') or {}).get('value') or {}).get('各阶段') or [{}])[0]
        optics = stage.get('光学') or {}
        pick = lambda key: (lambda value: value.get('mean') if isinstance(value, dict) else value)((optics.get(key) or {}).get('处理前'))
        out.append({'膜': film['name'], '测试日': film.get('测试日'), 'ellipsometry:n193': pick('n_193'), 'ellipsometry:k193': pick('k_193'),
                    'ellipsometry:thickness_nm': (stage.get('处理前') or {}).get('均值 nm'), 'ellipsometry:thickness_loss_pct@SolvA': stage.get('损失率 %'),
                    '溶解': bool(stage.get('溶解块数'))})
    return out


def _mean(values):
    values = [value for value in values if isinstance(value, (int, float))]
    return sum(values) / len(values) if values else None


def find_scans(library):
    lib = store.open(library)
    formulas = lib.rows('配方')
    groups = defaultdict(list)
    for formula in formulas:
        groups[re.sub(r'[A-Z]$', '', formula.get('配方编号') or formula['name'])].append(formula)
    films = {}
    scans = []
    for group, members in groups.items():
        keys = {key for formula in members for key in _factors(formula)[0]}
        for changing in sorted(keys):
            buckets = defaultdict(list)
            for formula in members:
                values, fixed = _factors(formula)
                stable = {key: values.get(key, 0) for key in keys if key != changing}
                buckets[str(sorted({**stable, **fixed}.items()))].append(formula)
            for bucket in buckets.values():
                if len({_factors(formula)[0].get(changing, 0) for formula in bucket}) < 3:
                    continue
                points = []
                for formula in sorted(bucket, key=lambda item: _factors(item)[0].get(changing, 0)):
                    code = formula.get('配方编号') or formula['name']
                    rows = films.setdefault(code, _film_values(lib, code))
                    responses = {rid: {'value': _mean([row[rid] for row in rows])} for rid in METRICS if _mean([row[rid] for row in rows]) is not None}
                    points.append({'code': code, 'x': _factors(formula)[0].get(changing, 0), 'state': 'measured' if responses else 'awaiting_measurement',
                                   'responses': responses, 'requires_confirmation': [],
                                   'measurements': [{'obs_id': f'film:{row["膜"]}', 'independent': True, 'flags': [], 'measured_on': row['测试日'],
                                                     'post_treatment': {'dissolved': row['溶解']}} for row in rows]})
                scans.append({'scan_id': digest([group, changing, [p['code'] for p in points]])[:24], 'group': group, 'factor': changing, 'points': points,
                              'fit': None, 'fit_note': '只给点', 'different_days': len({m['measured_on'] for p in points for m in p['measurements'] if m['measured_on']}) > 1,
                              '_rows': {p['code']: films[p['code']] for p in points}})
    return scans, films


def _dispersion(scan):
    """这条扫描里每个配方几块膜围绕配方均值的偏差，合起来取 MAD×1.4826（旧平台 gap_ranking 的离散度口径）。"""
    out = {}
    for rid in METRICS:
        residuals = []
        for rows in scan['_rows'].values():
            values = [row[rid] for row in rows if isinstance(row[rid], (int, float))]
            if len(values) >= 2:
                centre = sum(values) / len(values)
                residuals += [abs(value - centre) for value in values]
        if residuals:
            out[rid] = median(residuals) * 1.4826
    return out


def _reference(lib, days):
    """参比值：参比配方的膜（同测试日的优先，没有就全部）各指标的平均。"""
    rows = _film_values(lib, REFERENCE)
    same = [row for row in rows if row['测试日'] in days] or rows
    return {rid: _mean([row[rid] for row in same]) for rid in METRICS if rid != 'ellipsometry:thickness_loss_pct@SolvA' and _mean([row[rid] for row in same]) is not None}


def advice(library):
    """每条扫描一张建议卡（下一轮配哪几个水平、要不要先重测、要不要往外扫），加点和参比，给页面画。"""
    lib = store.open(library)
    scans, _ = find_scans(library)
    cards = []
    for scan in scans:
        days = {m['measured_on'] for p in scan['points'] for m in p['measurements'] if m['measured_on']}
        reference = _reference(lib, days)
        dispersion = _dispersion(scan)
        rows = scan.pop('_rows')
        result = scan_advice.advise([scan], reference_values=reference, dispersion=dispersion)
        card = result['cards'][0]
        cards.append({'scan_id': scan['scan_id'], 'group': scan['group'], 'factor': scan['factor'], 'card': card, 'detail': result['scans'][0],
                      'reference': reference, 'dispersion': dispersion,
                      'points': [{'code': p['code'], 'x': p['x'], 'state': p['state'], **{rid: (p['responses'].get(rid) or {}).get('value') for rid in METRICS},
                                  'films': len(rows[p['code']])} for p in scan['points']]})
    return clean({'cards': cards, 'reference': REFERENCE, 'metrics': {rid: label for rid, (label, _) in METRICS.items()}})
