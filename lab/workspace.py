"""研发工作区：按项目组织（实验 → 取样 → 测试；树脂 → 配方 → 膜 / n,k / 老化）、产品穿透、
实验的 SOP 与批记录（纸面预览、网页里填实测、导出、导回）、配方的工单（开单、称量、导出）。

都是在研发库上现算，不另存。块的格式和 present.py 一样。
"""
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from . import config, kernel, store
from .present import _gpc_blocks, _gpc_table, _hours, _records_block, clean, preview

# 不在"样品与数据"里单列的类型：样品归进实验，物料、方法、历史各有去处
FOLDED = {'树脂取样': '实验里的样品', '树脂批': '实验里的样品', '树脂物料': '物料', '原料': '物料', '预混液': '物料', '备料单': '配方与工单',
          'GPC分析模板': '方法', 'GPC标准品批次': '方法', 'GPC标准曲线': '方法', 'GPC分子量分区': '方法', 'NK轮次': '整理用', 'NK分组': '整理用',
          '椭偏测试日': '整理用', '旧LAB记录': '历史', '旧工单汇总': '历史', '课题': '课题'}
EXPERIMENT_KINDS = ('树脂实验', '配方', '工单', 'DOE研究', '树脂批')


def _norm(name):
    """样品名比对：大小写、'.0h' 和 'H' 写法都算一样。"""
    text = str(name or '').upper().replace(' ', '')
    return re.sub(r'(\d+)(?:\.0+)?H$', r'\1H', text)


def _resin_codes(text):
    """配方的树脂来源里提到哪些树脂实验（R100-34-15%、R100-38_39Mixed-15% → R100-34、R100-38、R100-39）。"""
    found = []
    for match in re.finditer(r'(CP\d{3})-(\d+[A-Z]?)((?:_\d+[A-Z]?)*)', str(text or '')):
        found.append(f'{match[1]}-{match[2]}')
        found += [f'{match[1]}-{extra}' for extra in match[3].split('_') if extra]
    return list(dict.fromkeys(found))


def _series(code):
    return re.sub(r'[A-Z]$', '', code) if re.match(r'.*T\d+[A-Z]$', code or '') else code


# ---------- 项目树 ----------

def tree(library):
    lib = store.open(library)
    q = lambda sql, params=(): lib.sql(sql, params)[1]
    experiments = q("SELECT id, name, json_extract(data,'$.标题'), json_extract(data,'$.状态') FROM records WHERE kind='树脂实验' AND deleted_at IS NULL")
    samples = dict(q("SELECT json_extract(data,'$.实验编号'), count(*) FROM records WHERE kind='树脂取样' AND deleted_at IS NULL GROUP BY 1"))
    gpc_names = [row[0] for row in q("SELECT name FROM records WHERE kind='GPC样品' AND deleted_at IS NULL")]
    formulas = q("SELECT id, name, json_extract(data,'$.树脂来源'), json_extract(data,'$.建立时间') FROM records WHERE kind='配方' AND deleted_at IS NULL")
    films = dict(q("SELECT json_extract(data,'$.配方编号'), count(*) FROM records WHERE kind='膜' AND deleted_at IS NULL GROUP BY 1"))
    orders = dict(q("SELECT json_extract(data,'$.配方编号'), count(*) FROM records WHERE kind='工单' AND deleted_at IS NULL GROUP BY 1"))
    aging = dict(q("SELECT json_extract(data,'$.样品'), count(*) FROM records WHERE kind='老化谱' AND deleted_at IS NULL GROUP BY 1"))
    nk_groups = [row[0] for row in q("SELECT name FROM records WHERE kind='NK分组' AND deleted_at IS NULL")]
    number = lambda code: [int(part) if part.isdigit() else 0 for part in re.split(r'(\d+)', code)]

    used_by = {}
    for _, code, source, _ in formulas:
        for resin in _resin_codes(source):
            used_by.setdefault(resin, []).append(code)
    exp_items = []
    for record_id, code, title, status in sorted(experiments, key=lambda row: number(row[1]), reverse=True):
        gpc = sum(1 for name in gpc_names if name.upper().startswith(code.upper() + '-'))
        exp_items.append({'key': 'exp:' + code, 'name': code, 'sub': title or '', 'status': status or '', 'record': record_id,
                          'counts': {'取样': samples.get(code, 0), 'GPC': gpc, '配方': len(used_by.get(code, []))}})
    series = {}
    for record_id, code, source, made in formulas:
        nk = sum(1 for name in nk_groups if name.upper().startswith(code.upper()))
        series.setdefault(_series(code), []).append({'key': 'formula:' + code, 'name': code, 'sub': '树脂 ' + '、'.join(json.loads(source) if source and source.startswith('[') else [source or '']),
                                                     'record': record_id, 'made': made or '',
                                                     'counts': {'膜': films.get(code, 0), 'n/k': nk, '老化': aging.get(code, 0), '工单': orders.get(code, 0)}})
    series_items = [{'key': 'series:' + name, 'name': name, 'sub': f'{len(items)} 个配方', 'children': sorted(items, key=lambda item: item['name']),
                     'counts': {key: sum(item['counts'][key] for item in items) for key in ('膜', 'n/k', '老化', '工单')}}
                    for name, items in sorted(series.items(), key=lambda pair: number(pair[0]), reverse=True)]
    gpc_projects = q("SELECT json_extract(data,'$.所属项目'), count(*) FROM records WHERE kind='GPC样品' AND deleted_at IS NULL GROUP BY 1 ORDER BY 1 DESC")
    known = {item['name'] for item in exp_items}
    gpc_items = [{'key': 'gpc:' + name, 'name': name, 'sub': 'GPC 项目', 'counts': {'GPC': count}} for name, count in gpc_projects if name and name not in known]
    formula_codes = {code for _, code, _, _ in formulas}
    others = {}
    for name, count in list(aging.items()) + list(films.items()):
        if name and name not in formula_codes:
            key = re.split(r'[-_ ]', name)[0]
            others.setdefault(key, 0)
            others[key] += count
    other_items = [{'key': 'other:' + name, 'name': name, 'sub': '参比、对照和其它样品', 'counts': {'记录': count}} for name, count in sorted(others.items())]
    return {'groups': [g for g in [
        {'name': '树脂合成', 'about': '每个实验：取样、GPC、HPLC，以及用它配的配方', 'items': exp_items},
        {'name': '配方', 'about': '按系列：每个配方的涂膜、n/k、老化和工单', 'items': series_items},
        {'name': 'GPC 项目', 'about': '跨实验整理的 GPC 对比', 'items': gpc_items},
        {'name': '参比与其它', 'about': '不属于上面任何项目的样品', 'items': other_items}] if g['items']]}


# ---------- 项目页 ----------

def page(library, key):
    lib = store.open(library)
    kind, _, name = key.partition(':')
    out = {'exp': experiment_page, 'formula': formula_page, 'series': series_page, 'gpc': gpc_page, 'other': other_page}[kind](lib, name)
    return clean(out)


def _samples_table(lib, code):
    """一个实验的取样 + 各自的 GPC 和 HPLC 结果合在一张表。"""
    samples = sorted(lib.rows('树脂取样', where=f"json_extract(data,'$.实验编号') = '{code}'"), key=lambda row: row.get('序号') or 0)
    gpc = {_norm(row['name']): row for row in _gpc_table(lib, code + '-%')}
    hplc = {}
    try:
        from .sci import resin
        for point in resin.hplc(code, lib)['取样点']:
            hplc[_norm(point['样品'])] = point
    except Exception:
        pass
    names = sorted({name for point in hplc.values() for name in point['组分']})
    rows, seen = [], set()
    for sample in samples:
        key = _norm(sample['name'])
        seen.add(key)
        mw = (gpc.get(key) or {}).get('mw') or {}
        point = hplc.get(key) or {}
        rows.append([{'v': sample['name'], 'id': sample['id']}, sample.get('种类'), _hours(sample['name']) if _hours(sample['name']) is not None else sample.get('节点'),
                     '、'.join(sample.get('计划检测') or []),
                     {'v': round(mw['Mw']), 'id': gpc[key]['id']} if mw.get('Mw') else None, mw.get('Mn'), mw.get('PDI')]
                    + [(point.get('组分', {}).get(name) or {}).get('剩余_%') for name in names])
    for key, row in gpc.items():                    # GPC 有、取样计划里没有的样（后处理的滤饼、滤液……）
        if key not in seen:
            mw = row['mw']
            rows.append([{'v': row['name'], 'id': row['id']}, 'GPC', row['h'], 'GPC', round(mw['Mw']) if mw.get('Mw') else None, mw.get('Mn'), mw.get('PDI')] + [None for _ in names])
    return {'type': 'table', 'title': f'样品和结果（{len(rows)}）', 'wide': True, 'note': 'GPC 是聚苯乙烯当量读数；HPLC 剩余 % 以投料为 100。点样品名打开那一条。',
            'columns': ['样品', '种类', '时间 / h 或节点', '计划检测', 'Mw', 'Mn', 'PDI'] + [f'{name} 剩余 %' for name in names], 'rows': rows}


def _formula_rows(lib, codes):
    """几个配方的性能一行一个：膜厚、SolvA 损失、n/k、最新 A500。"""
    rows = []
    for code in codes:
        found = lib.sql("SELECT id, json_extract(data,'$.总量'), json_extract(data,'$.固含'), json_extract(data,'$.建立时间') FROM records WHERE kind='配方' AND name=? AND deleted_at IS NULL", (code,))[1]
        record_id, total, solids, made = found[0] if found else (None, None, None, None)
        film = lib.sql("SELECT v.value FROM records r JOIN results v ON v.record_id=r.id AND v.method='椭偏.块统计' WHERE r.kind='膜' AND r.deleted_at IS NULL "
                       "AND json_extract(r.data,'$.配方编号')=? ORDER BY json_extract(r.data,'$.测试日') DESC LIMIT 1", (code,))[1]
        stage = ((json.loads(film[0][0]) if film else {}).get('各阶段') or [{}])[0]
        nk = lib.sql("SELECT v.value FROM records r JOIN results v ON v.record_id=r.id AND v.method='NK.组均值' WHERE r.kind='NK分组' AND r.deleted_at IS NULL AND r.name LIKE ? LIMIT 1", (code + '%',))[1]
        at = {item.get('波长 nm'): item for item in ((json.loads(nk[0][0]) if nk else {}).get('目标波长') or [])}
        pick = lambda key: (at.get(193.0) or {}).get(key + ' 均值', (at.get(193.0) or {}).get(key))
        aging = lib.sql("SELECT json_extract(v.value,'$.a500'), json_extract(r.data,'$.天数'), json_extract(r.data,'$.条件') FROM records r JOIN results v ON v.record_id=r.id AND v.method='老化.指标' "
                        "WHERE r.kind='老化谱' AND r.deleted_at IS NULL AND json_extract(r.data,'$.样品')=? ORDER BY 2 DESC LIMIT 1", (code,))[1]
        rows.append([{'v': code, 'id': record_id} if record_id else code, total, solids * 100 if isinstance(solids, (int, float)) else None,
                     (stage.get('处理前') or {}).get('均值 nm'), stage.get('损失率 %'), pick('n'), pick('k'),
                     f'{aging[0][0]:.3f}（{aging[0][2] or "起点"} {aging[0][1] or 0:g} 天）' if aging and aging[0][0] is not None else None, (made or '')[:10]])
    return {'type': 'table', 'title': f'配方和性能（{len(rows)}）', 'wide': True, 'note': '膜厚和损失取最近一次椭偏；n/k 取分组均值；A500 取最长储存的一次。',
            'columns': ['配方', '总量 / g', '固含 %', '膜厚 / nm', 'SolvA 损失 %', 'n193', 'k193', '最新 A500', '建立'], 'rows': rows}


def experiment_page(lib, code):
    from .present import _try, resin_experiment
    record = lib.get(code, kind='树脂实验', with_series=True)
    blocks = []
    full = resin_experiment(lib, record)
    blocks += [block for block in full if block['type'] in ('stats', 'prose')]
    blocks.append(_samples_table(lib, code))
    blocks += [block for block in full if block['type'] in ('chart', 'gpc_overlay') and ('GPC' in (block.get('title') or '') or 'HPLC' in (block.get('title') or ''))]
    formulas = sorted({row['name'] for row in lib.rows('配方') if code in _resin_codes(row.get('树脂来源'))})
    if formulas:
        blocks.append(_formula_rows(lib, formulas))
        trend = _aging_lines(lib, formulas)
        if trend:
            blocks.append(trend)
    children = lib.sql("SELECT id FROM records WHERE kind='树脂实验' AND deleted_at IS NULL AND json_extract(data,'$.父编号')=?", (code,))[1]
    parent = record['data'].get('父编号')
    family = ([lib.sql("SELECT id FROM records WHERE kind='树脂实验' AND name=?", (parent,))[1][0][0]] if parent and lib.sql("SELECT id FROM records WHERE kind='树脂实验' AND name=?", (parent,))[1] else []) + [row[0] for row in children]
    block = _records_block(lib, '前后的实验（派生自 / 派生出）', family)
    if block:
        blocks.append(block)
    return {'title': code, 'subtitle': record['data'].get('标题') or '', 'record': record['id'], 'kind': '树脂实验', 'blocks': blocks}


def _aging_lines(lib, codes):
    lines = []
    for code in codes:
        found = lib.sql("SELECT json_extract(r.data,'$.条件'), json_extract(r.data,'$.天数'), json_extract(v.value,'$.a500') FROM records r JOIN results v ON v.record_id=r.id AND v.method='老化.指标' "
                        "WHERE r.kind='老化谱' AND r.deleted_at IS NULL AND json_extract(r.data,'$.样品')=? ORDER BY 2", (code,))[1]
        by = {}
        for condition, days, a500 in found:
            by.setdefault(condition or '起点', ([], []))
            by[condition or '起点'][0].append(days or 0)
            by[condition or '起点'][1].append(a500)
        lines += [{'name': f'{code} · {condition}', 'x': xs, 'y': ys, 'mode': 'lines+markers'} for condition, (xs, ys) in by.items() if len(xs) >= 2]
    return {'type': 'chart', 'title': '储存老化：A500 随天数', 'x': '天数', 'y': 'A500', 'wide': True, 'note': '500 nm 显色峰；≥ 4.0 是饱和下限。', 'series': lines[:16]} if lines else None


# ---------- 产品穿透：一张从上游到下游的图 ----------

def _formula_of(name, formulas):
    """NK 分组、样品名 → 它属于哪个配方（最长的配方编号前缀，后面是结尾或"-"）。"""
    hits = [code for code in formulas if name == code or name.startswith(code + '-')]
    return max(hits, key=len) if hits else None


def _film_node(lib, film):
    stats = lib.sql("SELECT value FROM results WHERE record_id=? AND method='椭偏.块统计'", (film['id'],))[1]
    stage = ((json.loads(stats[0][0]) if stats else {}).get('各阶段') or [{}])[0]
    return {'id': film['id'], 'kind': '膜', 'name': film['name'].split(' · ')[0], 'sub': f'椭偏 · {film.get("测试日") or ""}',
            'metrics': [['膜厚', (stage.get('处理前') or {}).get('均值 nm'), 'nm'], ['SolvA 损失', stage.get('损失率 %'), '%']]}


def trace(library, ref):
    """产品穿透：从任意一条记录出发，往回找到树脂实验，往后找到配方、工单、膜、n/k、老化，排成三层（上游 → 下游）。
    关联按编号和设计来认：配方的树脂来源、膜和工单的配方编号、n/k 分组名、老化样品名。库里找不到的一端画成虚框。"""
    from .present import experiments
    lib = store.open(library)
    record = lib.get(ref)
    kind, data = record['kind'], record['data']
    formula_rows = {row['name']: row for row in lib.rows('配方')}
    exps = {item['code']: item for item in experiments(library)}
    aliases, lots = lib.setting('树脂.实验别名') or {}, {row['name']: row.get('实验编号') for row in lib.rows('树脂批')}

    def experiment(code):                        # 配方里写的可能是批号或别名（R100-38A → R100-38）
        bare = re.sub(r'[A-Z]$', '', code)
        return code if code in exps else aliases.get(code) or lots.get(code) or (bare if bare in exps else code)

    def sources(row):
        return {experiment(code): code for code in _resin_codes((row or {}).get('树脂来源'))}

    resins, formulas, focus = [], [], record['id']
    if kind in ('树脂实验', '树脂批', '树脂取样', 'GPC样品'):
        code = record['name'] if kind == '树脂实验' else data.get('实验编号') or next(
            (code for code in sorted(exps, key=len, reverse=True) if record['name'].upper().startswith(code.upper() + '-')), None)
        resins = [code] if code else []
        formulas = [name for name, row in formula_rows.items() if code and code in sources(row)]
        if code in exps:
            focus = exps[code]['id']
    else:
        if kind == '配方':
            code = record['name']
        elif kind in ('工单', '膜'):
            code = data.get('配方编号')
        elif kind == '老化谱':
            code = data.get('样品')
        elif kind == 'NK谱':
            code = _formula_of(data.get('分组') or record['name'], formula_rows)
        else:
            code = _formula_of(record['name'], formula_rows)
        formulas = [code] if code else []
        resins = list(sources(formula_rows.get(code)))
        if kind in ('配方', '工单') and code in formula_rows:     # 工单画在配方那一格上
            focus = formula_rows[code]['id']
    nodes, edges = {0: [], 1: [], 2: []}, []
    used = {}                                     # 实验 → 配方里写的批号（和实验编号不同时标出来）
    for code in formulas:
        for exp_code, written in sources(formula_rows.get(code)).items():
            if written != exp_code:
                used.setdefault(exp_code, set()).add(written)
    for code in resins:
        item = exps.get(code)
        nodes[0].append({'id': item['id'], 'kind': '树脂实验', 'name': code, 'sub': item['title'][:40],
                         'chips': [f'用的是 {lot}' for lot in sorted(used.get(code) or [])] + (item.get('tags') or [])[:3],
                         'metrics': [['产率', item['yield'], '%'], ['末点 Mw', item['mw_final'], ''], ['PDI', item['pdi_final'], '']]} if item else
                        {'id': 'missing:' + code, 'kind': '树脂实验', 'name': code, 'sub': '库里没有这个实验', 'missing': True, 'metrics': []})
    orders, films, spectra = {}, {}, {}
    for row in lib.rows('工单'):
        orders.setdefault(row.get('配方编号'), []).append(row)
    for row in lib.rows('膜'):
        films.setdefault(row.get('配方编号'), []).append(row)
    for row in lib.rows('老化谱'):
        spectra.setdefault(row.get('样品'), []).append(row)
    groups = lib.rows('NK分组')
    for code in formulas:
        row = formula_rows.get(code)
        mine = orders.get(code) or []
        latest = max(mine, key=lambda item: item.get('计划日期') or '') if mine else None
        node = ({'id': row['id'], 'kind': '配方', 'name': code, 'sub': (row.get('note') or '')[:40],
                 'metrics': [['总量', row.get('总量'), 'g'], ['固含', row['固含'] * 100 if isinstance(row.get('固含'), (int, float)) else None, '%']],
                 'chips': [f'工单 {len(mine)} 张 · 最近 {latest.get("状态") or ""}'] if latest else []}
                if row else {'id': None, 'kind': '配方', 'name': code, 'sub': '库里没有这个配方', 'missing': True, 'metrics': []})
        key = node['id'] or 'missing:' + code
        node['id'] = node['id'] or key
        nodes[1].append(node)
        edges += [[resin['id'], key] for resin in nodes[0] if resin['id'] and row and resin['name'] in sources(row)]
        for film in films.get(code) or []:
            nodes[2].append(_film_node(lib, film))
            edges.append([key, film['id']])
        for group in groups:
            if _formula_of(group['name'], formula_rows) == code or (not row and group['name'].startswith(code + '-')):
                mean = lib.sql("SELECT value FROM results WHERE record_id=? AND method='NK.组均值'", (group['id'],))[1]
                at = {item.get('波长 nm'): item for item in ((json.loads(mean[0][0]) if mean else {}).get('目标波长') or [])}.get(193.0) or {}
                nodes[2].append({'id': group['id'], 'kind': 'n/k', 'name': group['name'], 'sub': f'{group.get("谱数") or ""} 条谱的组均值',
                                 'metrics': [['n193', at.get('n 均值', at.get('n')), ''], ['k193', at.get('k 均值', at.get('k')), '']]})
                edges.append([key, group['id']])
        aged = {}
        for spectrum in spectra.get(code) or []:
            aged.setdefault(spectrum.get('条件') or '起点', []).append(spectrum)
        for condition, items in aged.items():
            last = max(items, key=lambda item: item.get('天数') or 0)
            a500 = lib.sql("SELECT json_extract(value,'$.a500') FROM results WHERE record_id=? AND method='老化.指标'", (last['id'],))[1]
            value = a500[0][0] if a500 else None
            nodes[2].append({'id': last['id'], 'kind': '老化', 'name': f'{code} · {condition}', 'sub': f'{len(items)} 次读数，最长 {last.get("天数") or 0:g} 天',
                             'metrics': [['A500', '≥ 4.0' if isinstance(value, (int, float)) and value >= 3.999 else value, '']]})   # 饱和记成下限
            edges.append([key, last['id']])
    target = {'NK谱': data.get('分组'), '老化谱': f'{data.get("样品")} · {data.get("条件") or "起点"}'}.get(kind)
    if target:                                   # 一条谱归在它的组或储存条件那一格
        focus = next((node['id'] for node in nodes[2] if node['name'] == target), focus)
    for layer in nodes.values():
        for node in layer:
            node['focus'] = node['id'] == focus
    return clean({'start': {'id': record['id'], 'kind': kind, 'name': record['name']}, 'focus': focus,
                  'layers': [{'title': '树脂合成', 'nodes': nodes[0]}, {'title': '配方与工单', 'nodes': nodes[1]}, {'title': '涂膜与测量', 'nodes': nodes[2]}],
                  'edges': edges})


def formula_page(lib, code):
    from .present import formulation
    record = lib.get(code, kind='配方', with_series=True)
    blocks = formulation(lib, record)
    resins = _resin_codes(record['data'].get('树脂来源'))
    ids = [row[0] for code_ in resins for row in lib.sql("SELECT id FROM records WHERE kind='树脂实验' AND name=? AND deleted_at IS NULL", (code_,))[1]]
    block = _records_block(lib, '用的树脂来自（产品穿透：往回看）', ids)
    if block:
        blocks.insert(1, block)
    return {'title': code, 'subtitle': '配方 · 树脂 ' + '、'.join(record['data'].get('树脂来源') or []), 'record': record['id'], 'kind': '配方', 'blocks': blocks}


def series_page(lib, series):
    codes = sorted(row['name'] for row in lib.rows('配方') if _series(row['name']) == series)
    blocks = [_formula_rows(lib, codes)]
    comp = []
    for code in codes:
        data = lib.get(code, kind='配方')['data']
        adds = '；'.join(f'{item.get("source_id")} {item.get("percent")}%{"（" + item["note"] + "）" if item.get("note") else ""}' for item in data.get('添加剂') or [])
        comp.append([code, '、'.join(data.get('树脂来源') or []), adds, '、'.join(f'{item.get("source_id")} {item.get("parts")}' for item in data.get('溶剂') or [])])
    blocks.append({'type': 'table', 'title': '组成对比', 'wide': True, 'columns': ['配方', '树脂', '添加剂（占固体）', '溶剂（份）'], 'rows': comp})
    trend = _aging_lines(lib, codes)
    if trend:
        blocks.append(trend)
    films = [row for row in lib.rows('膜') if row.get('配方编号') in codes]
    if films:
        rows = []
        for film in sorted(films, key=lambda row: (row.get('测试日') or '', row['name'])):
            stage = ((lib.get(film['id'])['results'].get('椭偏.块统计') or {}).get('value') or {}).get('各阶段') or [{}]
            rows.append([{'v': film['name'], 'id': film['id']}, film.get('配方编号'), film.get('测试日'), f'{film.get("转速") or ""} rpm · {film.get("烘烤温度") or ""} °C/{film.get("烘烤时间") or ""} s',
                         (stage[0].get('处理前') or {}).get('均值 nm'), stage[0].get('损失率 %')])
        blocks.append({'type': 'table', 'title': f'所有涂膜（{len(rows)}）', 'wide': True, 'columns': ['膜', '配方', '测试日', '条件', '膜厚 / nm', '损失 %'], 'rows': rows})
    orders = [row['id'] for row in lib.rows('工单') if row.get('配方编号') in codes]
    block = _records_block(lib, '工单', orders, limit=40)
    if block:
        blocks.append(block)
    return {'title': series, 'subtitle': f'配方系列 · {len(codes)} 个配方', 'blocks': blocks}


def gpc_page(lib, name):
    rows = [row for row in _gpc_table(lib, '%') if True]
    ids = {row['id'] for row in lib.rows('GPC样品') if row.get('所属项目') == name}
    rows = [row for row in rows if row['id'] in ids]
    return {'title': name, 'subtitle': f'GPC 项目 · {len(rows)} 个样品', 'blocks': _gpc_blocks(rows, name)}


def other_page(lib, prefix):
    ids = [row[0] for row in lib.sql("SELECT id FROM records WHERE deleted_at IS NULL AND kind IN ('膜','老化谱','NK谱','GPC样品') AND (name LIKE ? OR json_extract(data,'$.样品') LIKE ? OR json_extract(data,'$.配方编号') LIKE ?)",
                                     (prefix + '%', prefix + '%', prefix + '%'))[1]]
    blocks = []
    trend = _aging_lines(lib, sorted({row[0] for row in lib.sql("SELECT json_extract(data,'$.样品') FROM records WHERE kind='老化谱' AND json_extract(data,'$.样品') LIKE ?", (prefix + '%',))[1]}))
    if trend:
        blocks.append(trend)
    block = _records_block(lib, '记录', ids, limit=60)
    if block:
        blocks.append(block)
    return {'title': prefix, 'subtitle': '参比、对照和其它样品', 'blocks': blocks}


# ---------- 树脂实验：SOP 与批记录 ----------

def _fmt(value, fmt):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return '' if value is None else str(value)
    digits = len(re.search(r'0\.(0+)', fmt or '')[1]) if re.search(r'0\.(0+)', fmt or '') else (0 if fmt and re.match(r'^0(?!\.)', fmt) else None)
    suffix = ''.join(re.findall(r'"([^"]*)"', fmt or ''))
    text = f'{value:.{digits}f}' if digits is not None else f'{value:.4g}'
    return text + suffix


def _value(expr, values):
    """格子里的公式：[变量] 直接取值；只有四则运算的现算；别的（IF、TEXT…）在导出的 Excel 里算。"""
    simple = re.fullmatch(r'\s*\[([^\]]+)\]\s*', expr or '')
    if simple:
        return values.get(simple[1])
    swapped = re.sub(r'\[([^\]]+)\]', lambda m: repr(values.get(m[1])) if isinstance(values.get(m[1]), (int, float)) else 'None', expr or '')
    if re.fullmatch(r'[\d\s.+\-*/()eE]+', swapped):
        try:
            return eval(swapped, {'__builtins__': {}})          # noqa: S307  只有数字和四则运算
        except ZeroDivisionError:
            return None
    return None


def paper(library, ref):
    """SOP、批记录、检测记录、参数四张纸：格子算好值；批记录里要填的格子带上键和已填的值。"""
    from .sci import resin
    lib = store.open(library)
    doc, _, record = resin.design(ref, lib)
    document = resin.document(ref, records=True, lib=lib)
    actual = document.get('actual') or {}
    pending, sheets = [], []
    for sheet in document['sheets']:
        if not sheet.get('paperSections') and sheet['name'] not in ('参数归档', '偏差检查'):
            continue
        blocks = []
        for block in sheet.get('blocks') or []:
            if block['type'] in ('note', 'title', 'section'):
                blocks.append({'type': block['type'], 'text': block.get('text') or ''})
                continue
            if block['type'] != 'table':
                continue
            header = [{'label': item.get('label') or item.get('key') or '', 'span': item.get('span') or 1} for item in block.get('header') or []]
            keys = [item.get('key') or item.get('label') for item in block.get('header') or []]
            rows = []
            for row in block.get('rows') or []:
                cells = []
                for index, cell in enumerate(row.get('cells') or []):
                    if cell.get('input'):
                        key = f'{block["id"]}/{row.get("id")}/{keys[index] if index < len(keys) else index}'
                        cells.append({'input': True, 'key': key, 'type': cell.get('type') or 'text', 'unit': cell.get('unit') or '', 'value': actual.get(key), 'span': cell.get('span')})
                    elif 'f' in cell or 'var' in cell:
                        out = {'t': '', 'span': cell.get('span'), 'calc': True}
                        pending.append((out, {'f': cell['f'] if 'f' in cell else f'[{cell["var"]}]', 'fmt': cell.get('fmt')}))
                        cells.append(out)
                    elif '{' in (cell.get('t') or ''):            # 正文里带 {[反应内温]} 这类模板：交给核心算成字
                        out = {'t': '', 'span': cell.get('span'), 'style': cell.get('style')}
                        pending.append((out, {'t': cell['t']}))
                        cells.append(out)
                    else:
                        cells.append({'t': cell.get('t') or '', 'span': cell.get('span'), 'style': cell.get('style')})
                rows.append(cells)
            blocks.append({'type': 'table', 'title': block.get('title') or block.get('caption') or '', 'header': header, 'rows': rows})
        sheets.append({'name': sheet['name'], 'blocks': blocks})
    if pending:                                     # 公式格子一次交给核心算
        for (out, item), result in zip(pending, resin.call('cells', lib, design=doc, formulas=[item for _, item in pending])):
            if 't' in item:
                out['t'] = result['text'] if not result.get('error') else item['t']
                continue
            value = result.get('value')
            number = isinstance(value, (int, float)) and not isinstance(value, bool)
            if number and not item['fmt']:            # 没给格式：mmol 留 3 位，其它留 2 位，整数照原样
                out['t'] = f'{value:g}' if float(value).is_integer() else f'{value:.3f}' if 'mmol' in item['f'] else f'{value:.2f}'
            else:
                out['t'] = result['text'] or (_fmt(value, item['fmt']) if number else '')
    filled = sum(1 for value in actual.values() if value not in (None, ''))
    return clean({'code': record['name'], 'title': record['data'].get('标题') or '', 'sheets': sheets, 'filled': filled, 'record': record['id'],
                  'figure': resin.call('figure', lib, design=doc), 'deviations': deviations(lib, doc, document)})


def deviations(lib, doc, document):
    """偏差检查给人看：每个记录项填了没有、和计划差多少、超没超允许范围（核心的 deviation 算法），超差的排前面。"""
    from .sci import resin
    actual = doc.get('actual') or {}
    over = {item['key']: item for item in resin.call('profile', lib, design=doc)['deviations']}
    sheet = next((s for s in document['sheets'] if s['name'] == '偏差检查'), None)
    rows = []
    for block in (sheet or {}).get('blocks') or []:
        for row in block.get('rows') or []:
            key = row.get('recordKey')
            if not key:
                continue
            label = ((row.get('cells') or [{}])[0].get('t') or key).replace(' / 实际值', ' 投料').replace(' / ', ' · ')
            node = re.search(r'/t-(\d+(?:\.\d+)?)/', key)                 # 过程节点写上是第几小时
            if node:
                label = label.replace('过程节点', f'{node[1]} h')
            hit = over.get(key)
            value = actual.get(key)
            rows.append({'项目': label, '实际': value, '单位': (hit or {}).get('unit') or '', '计划': (hit or {}).get('planned'),
                         '差': (hit or {}).get('percent'), '差值': (hit or {}).get('delta'), '允许': (hit or {}).get('tolerance'),
                         '判定': '超差' if hit else ('没填' if value in (None, '') else '正常'), '说明': (hit or {}).get('message') or ''})
    order = {'超差': 0, '正常': 1, '没填': 2}
    return sorted(rows, key=lambda row: order[row['判定']])


def save_actual(library, ref, changes):
    """网页上填的批记录：并进这个实验的"实测"表（键 → 值），之后产率、偏差、过程曲线都按新值算。"""
    lib = store.open(library)
    record = lib.get(ref, kind='树脂实验', with_series=True)
    table = record['series'].get('实测') or {'columns': ['键', '值'], 'rows': []}
    current = {key: value for key, value in table['rows']}
    for key, value in changes.items():
        if value in (None, ''):
            current.pop(key, None)
        else:
            current[key] = value
    lib.put_series(record['id'], '实测', ['键', '值'], [[key, value] for key, value in current.items()], table.get('meta') or {})
    return {'saved': len(changes), 'filled': len(current)}


def export_paper(library, ref, fmt, filled):
    from .sci import resin
    out = resin.export_sop(ref, fmt=fmt, records=filled, lib=store.open(library))
    return Path(out['文件'])


def import_paper(library, ref, path):
    from .sci import resin
    return resin.import_record(ref, str(path), save=True, lib=store.open(library))


# ---------- 配方：工单 ----------

def order_name(lib, formula_code, day):
    """工单号按库设置"配方.工单编号规则"：<产品前缀>（配方编号第一个"-"前，PA-T23A → PA）、<配方编号>、YYYYMMDD、
    NNN（当天这个前缀下的流水号，几个 N 就补几位）。旧配方程序的写法是 <产品前缀>-YYYYMMDD-NNN（PB-20261012-003）。"""
    rule = lib.setting('配方.工单编号规则') or '<产品前缀>-YYYYMMDD-NNN'
    head = rule.replace('<产品前缀>', formula_code.split('-')[0]).replace('<配方编号>', formula_code).replace('YYYYMMDD', day.replace('-', ''))
    found = re.search(r'N+', head)
    if not found:
        return head
    prefix, width = head[:found.start()], len(found[0])
    taken = [name for (name,) in lib.sql("SELECT name FROM records WHERE kind='工单' AND name LIKE ?", (prefix + '%',))[1]]
    numbers = [int(match[1]) for name in taken if (match := re.match(re.escape(prefix) + r'(\d+)', name))]
    return head[:found.start()] + f'{(max(numbers) if numbers else 0) + 1:0{width}d}' + head[found.end():]


def new_order(library, formula_ref, total_g, date=None, note=''):
    """从配方开一张工单：按总量现算称量计划，冻结配方和计划，建好称量表（实称留空）。"""
    from .sci import formulation as fm
    lib = store.open(library)
    record = lib.get(formula_ref, kind='配方')
    formula = fm.formula_of(record)
    formula['total_mass_g'] = float(total_g)
    plan = fm.calculate_formula(formula=formula, lib=lib)
    day = (date or kernel.now()[:10])
    name = order_name(lib, record['name'], day)
    lines = plan.get('lines') or []
    saved = lib.save('工单', [{'name': name, '工单号': name, '配方编号': record['name'], '修订': record['data'].get('修订'), '状态': '待配样', '计划日期': day,
                               '冻结配方': formula, '冻结计划': plan, '实称信息': {'operator': '', 'note': note},
                               'series': {'称量': {'columns': ['行', '来源编号', '名称', '计划量', '实称', '复核'],
                                                   'rows': [[line.get('id'), line.get('source_id'), line.get('name'), line.get('planned_mass_g'), None, None] for line in lines]}},
                               'links': [{'relation': '按配方', 'to': record['id']}]}], source='工作台开单', by='人')
    return {'id': saved[0]['id'], 'name': name, 'ok': plan.get('ok'), 'warnings': plan.get('warnings') or [], 'errors': plan.get('errors') or []}


def save_weighing(library, order_ref, rows, operator=None, status=None):
    """填实称：rows = {行: {实称, 复核}}。都填了可以顺手把状态改成已完成。"""
    lib = store.open(library)
    record = lib.get(order_ref, kind='工单', with_series=True)
    table = record['series'].get('称量')
    columns = table['columns']
    at = {name: index for index, name in enumerate(columns)}
    out = []
    for row in table['rows']:
        row = list(row)
        change = rows.get(str(row[at['行']])) or rows.get(row[at['行']])
        if change:
            for key in ('实称', '复核'):
                if key in change:
                    row[at[key]] = change[key]
        out.append(row)
    lib.put_series(record['id'], '称量', columns, out, table.get('meta') or {})
    patch = {'id': record['id']}
    if operator is not None:
        patch['实称信息'] = {**(record['data'].get('实称信息') or {}), 'operator': operator}
    if status:
        patch['状态'] = status
    lib.save('工单', [patch], by='人')
    return {'filled': sum(1 for row in out if row[at['实称']] is not None), 'lines': len(out)}


def export_orders(library, refs, fmt='xlsx'):
    from .sci import formulation as fm
    return Path(fm.export_orders(refs, lib=store.open(library), fmt=fmt))


# ---------- 合成、配方：按项目分组 ----------

def _base(code):
    found = re.match(r'CP\d{3}-(\d+)', code or '')
    return int(found[1]) if found else None


# ---------- 项目：改名、归组、排顺序（合成、配方、GPC 三处一个办法） ----------
# 每条记录的"所属项目"字段写了就按它归组，没写的按规则自动分；项目的先后存在库设置"项目顺序.<哪处>"里。
PROJECT_KINDS = {'合成': '树脂实验', '配方': '配方', 'GPC': 'GPC样品'}


def project_order(library, scope):
    return store.open(library).setting(f'项目顺序.{scope}') or []


def ordered(library, scope, items, name=lambda item: item['name']):
    """排过的项目按你排的先后；没排过的（新出来的）放最前面，彼此照原来的顺序。"""
    order = {value: index for index, value in enumerate(project_order(library, scope))}
    return sorted(items, key=lambda item: order.get(name(item), -1))


def save_project_order(library, scope, names):
    store.open(library).set_setting(f'项目顺序.{scope}', [str(name) for name in names], about=f'{scope}页项目的先后（拖动排的）')
    return project_order(library, scope)


def project_move(library, scope, ids, name):
    """几条记录挪到一个项目（没有就新建）；name 空 = 不再手动归组，回到按规则分。"""
    name = str(name or '').strip() or None
    store.open(library).save(PROJECT_KINDS[scope], [{'id': record_id, '所属项目': name} for record_id in ids], by='人')
    return {'moved': len(ids), 'name': name}


def project_rename(library, scope, old, new, ids=(), record=None):
    """项目改名：组里每条记录的"所属项目"都写成新名字（配方的实验系列就是那条 DOE 研究，改它的名字）；排好的先后跟着换名。"""
    lib, new = store.open(library), str(new or '').strip()
    if record:
        lib.save('DOE研究', [{'id': record, 'name': new}], by='人')
    else:
        if scope == 'GPC':
            ids = [row[0] for row in lib.sql("SELECT id FROM records WHERE kind='GPC样品' AND deleted_at IS NULL AND json_extract(data,'$.所属项目')=?", (old,))[1]]
        project_move(library, scope, ids, new)
    order = project_order(library, scope)
    if old in order:
        save_project_order(library, scope, [new if name == old else name for name in order])
    return {'name': new}


def experiment_projects(library, rows):
    """合成页的项目：
    0. 你给实验写了"所属项目"（改名、拖进别的项目）的，按你写的；
    1. 有派生关系的连成一个系列（R100-38 → 39 → 40 → 41、38 → 42 算"R100-38 系列"）；
    2. 旧 GPC 程序里你建的项目（R100-18到R100-25……），编号落在范围里的实验也归进去；
    3. 剩下连续编号的按 8 个一段（R100-26 到 R100-33）。"""
    lib = store.open(library)
    codes = {row['code']: row for row in rows}
    parent = {row['code']: row.get('parent') for row in rows if row.get('parent') in codes}

    def root(code):
        seen = set()
        while code in parent and code not in seen:
            seen.add(code)
            code = parent[code]
        return code
    family = {}
    for code in codes:
        family.setdefault(root(code), []).append(code)
    group = {code: row['project'] for code, row in codes.items() if row.get('project')}
    mine = set(group)
    for top, members in family.items():
        if len(members) > 1:
            for code in members:
                if code not in mine:
                    group[code] = f'{top} 系列'
    for code in codes:                              # 名字带着系列根编号的（R100-38-39 混合再处理）也归进那个系列
        if code not in group:
            stem = next((f'{top} 系列' for top, members in family.items() if len(members) > 1 and code.startswith(top + '-')), None)
            if stem:
                group[code] = stem
    ranges = []
    for name, in lib.sql("SELECT DISTINCT json_extract(data,'$.所属项目') FROM records WHERE kind='GPC样品' AND deleted_at IS NULL")[1]:
        found = re.match(r'CP\d{3}-(\d+)\s*到\s*CP\d{3}-(\d+)$', name or '')
        if found:
            ranges.append((int(found[1]), int(found[2]), name))
    for code in codes:
        if code not in group and _base(code) is not None:
            hit = next((name for low, high, name in ranges if low <= _base(code) <= high), None)
            if hit:
                group[code] = hit
    rest = sorted((code for code in codes if code not in group), key=lambda code: (_base(code) or 0, code))
    chunk = []
    for code in rest + [None]:
        if code is None or (chunk and (_base(code) or 0) - (_base(chunk[-1]) or 0) > 1) or len({_base(c) for c in chunk}) >= 8:
            if chunk:
                name = chunk[0] if len(chunk) == 1 else f'{chunk[0]} 到 {chunk[-1]}'
                for item in chunk:
                    group[item] = name
            chunk = []
        if code is not None:
            chunk.append(code)
    projects = {}
    for code, name in group.items():
        projects.setdefault(name, []).append(code)
    out = []
    for name, members in projects.items():
        members.sort(key=lambda code: (_base(code) or 0, code), reverse=True)
        items = [codes[code] for code in members]
        statuses = {}
        for item in items:
            statuses[item['status'] or '未设'] = statuses.get(item['status'] or '未设', 0) + 1
        out.append({'name': name, 'codes': members, 'top': max(_base(code) or 0 for code in members), 'statuses': statuses, 'mine': all(code in mine for code in members),
                    'samples': sum(item['samples'] for item in items), 'gpc': sum(item['gpc'] for item in items)})
    return ordered(library, '合成', sorted(out, key=lambda item: item['top'], reverse=True))


def formula_projects(library):
    """配方页的项目：旧配方程序里的"实验系列"（DMAP中和处理效果、AddH后处理树脂评估……）带着它的配方；
    其余的配方按编号系列（PA-T23、T24……）成组。一个配方只出现一次。"""
    lib = store.open(library)
    items = {item['name']: item for group in tree(library)['groups'] if group['name'] == '配方' for series in group['items'] for item in series['children']}
    used, out, mine = set(), [], {}
    for code, project in lib.sql("SELECT name, json_extract(data,'$.所属项目') FROM records WHERE kind='配方' AND deleted_at IS NULL")[1]:
        if project and code in items:                 # 你写了"所属项目"的配方按你写的归组
            mine.setdefault(project, []).append(items[code])
            used.add(code)
    out += [{'name': name, 'kind': '项目', 'mine': True, 'items': sorted(members, key=lambda item: item['name'])} for name, members in mine.items()]
    for study in lib.rows('DOE研究'):
        points = (lib.get(study['id'], with_series=True)['series'].get('实验点') or {})
        columns, rows = points.get('columns') or [], points.get('rows') or []
        at = columns.index('配方编号') if '配方编号' in columns else None
        codes = list(dict.fromkeys(row[at] for row in rows if at is not None and row[at] in items and row[at] not in used))
        codes += [code for code in items if ' ' in code and code.split(' ')[0] in codes and code not in used]      # 修订版跟着原配方
        if codes:
            used.update(codes)
            out.append({'name': study['name'], 'kind': '实验系列', 'record': study['id'], 'items': [items[code] for code in sorted(codes)]})
    series = {}
    for code, item in items.items():
        if code not in used:
            series.setdefault(_series(code.split(' ')[0]), []).append(item)
    out += [{'name': name, 'kind': '编号系列', 'items': sorted(members, key=lambda item: item['name'])} for name, members in series.items()]
    latest = lambda project: max((item.get('made') or '') for item in project['items'])
    return ordered(library, '配方', sorted(out, key=latest, reverse=True))
