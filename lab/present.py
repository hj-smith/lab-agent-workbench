"""呈现：把一条记录变成给人看的一组"块"（数字卡、文字、表、曲线、步骤、关联记录）。

每种记录有一套基础呈现；没有专门呈现的类型走通用呈现（什么都不藏）。
呈现可以改：data/呈现/<类型>.py 里写 present(lib, record, blocks) -> blocks（智能体或人写），
data/呈现/<类型>.json 里放人调过的顺序和隐藏（{"order": [块标题…], "hidden": [块标题…]}）。

块的格式（前端照着画）：
  {'type': 'stats', 'title'?, 'items': [{'label', 'value', 'unit'?, 'note'?, 'tone'?: good|warn|bad, 'digits'?}]}
  {'type': 'prose', 'title'?, 'items': [{'label', 'text', 'tone'?}]}
  {'type': 'table', 'title', 'columns': [...], 'rows': [[单元格…]], 'note'?}   单元格可以是 {'v': 显示, 'id': 记录 id}
  {'type': 'chart', 'title', 'x', 'y', 'series': [{'name', 'x': [], 'y': [], 'dash'?, 'mode'?}], 'note'?, 'log_x'?}
  {'type': 'steps', 'title', 'items': [{'title', 'tag'?, 'meta'?, 'text'?, 'chips'?: []}]}
  {'type': 'records', 'title', 'items': [{'id', 'kind', 'name', 'text', 'points'}]}
  {'type': 'kv', 'title', 'items': [[键, 值]]}
  {'type': 'gpc_overlay', 'title', 'ids': [GPC样品 记录 id…]}   几个样品的色谱叠加（横轴保留时间 / 分子量，可勾选、缩放、归一）
  每块可带 'wide': True（占整行），默认半行。
"""
import importlib.util
import json
import re
import traceback

from . import config, store

ROLE = {'reactant': '单体', 'monomer': '单体', 'catalyst': '催化剂', 'solvent': '溶剂', 'additive': '添加剂', 'addition': '添加剂', 'neutralizer': '中和剂',
        'neutralization': '中和剂', 'quench': '终止剂', 'antisolvent': '沉淀剂', 'precipitant': '沉淀剂', 'wash': '洗涤', 'washing': '洗涤', 'reagent': '试剂',
        'workup': '后处理', 'carrier': '载体溶剂'}
STATUS_TONE = {'已完成': 'good', '完成': 'good', '进行中': 'warn', '草稿': None, '失败': 'bad'}


def views_dir():
    return config.HOME / '呈现'


# ---------- 小工具 ----------

def _res(record, name):
    return (record['results'].get(name) or {}).get('value')


def _thin(points, keep=48):
    step = max(1, len(points) // keep)
    return [point for point in points[::step] if point[0] is not None and point[1] is not None]


def _g(value):
    """250.0 → 250；别的原样。"""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def conditions_text(cond):
    """椭偏的成膜和处理条件写成人话：烘烤 250 ℃ / 120 s · SolvA 浸泡 2 min · 后烘 250 ℃ / 60 s。不是字典的原样返回。"""
    if not isinstance(cond, dict):
        return cond
    pair = lambda temp, time: ' / '.join(part for part in (f'{_g(temp)} ℃' if temp is not None else '', f'{_g(time)} s' if time is not None else '') if part)
    span = lambda text: re.sub(r'(\d)\s*(min|s|h)\b', r'\1 \2', str(text))
    parts = [f'{_g(cond["转速"])} rpm'] if cond.get('转速') is not None else []
    if cond.get('烘烤温度') is not None or cond.get('烘烤时间') is not None:
        parts.append('烘烤 ' + pair(cond.get('烘烤温度'), cond.get('烘烤时间')))
    if cond.get('处理'):
        parts.append(f'{cond["处理"]} 浸泡' + (f' {span(cond["处理时间"])}' if cond.get('处理时间') else ''))
    if cond.get('后烘温度') is not None or cond.get('后烘时间') is not None:
        parts.append('后烘 ' + pair(cond.get('后烘温度'), cond.get('后烘时间')))
    known = {'转速', '烘烤温度', '烘烤时间', '处理', '处理时间', '后烘温度', '后烘时间'}
    parts += [f'{key} {value}' for key, value in cond.items() if key not in known and value not in (None, '')]
    return ' · '.join(parts) or '（没写条件）'


def value_text(value):
    """列表、字典写成一句话（不给人看 JSON）。"""
    if isinstance(value, list):
        return '、'.join(str(value_text(item)) for item in value)
    if isinstance(value, dict):
        return '，'.join(f'{key} {value_text(item)}' for key, item in value.items() if item not in (None, ''))
    return value


def _hours(name):
    found = re.search(r'(\d+(?:\.\d+)?)\s*[hH](?![a-zA-Z])', name or '')
    return float(found[1]) if found else None


def _text(value):
    """字段里有时是对象或列表：拼成人读得懂的一句。"""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, dict) and 'verdict' in value:          # 判定另有一栏，这里只留文字
        return '；'.join(str(value[key]) for key in ('text', 'summary', 'note', 'detail', 'reason') if value.get(key)) or None
    if isinstance(value, dict):
        return '；'.join(f'{key}：{_text(item)}' for key, item in value.items() if item not in (None, '', [], {}))
    if isinstance(value, list):
        return '；'.join(str(_text(item)) for item in value if item not in (None, ''))
    return str(value)


def _f(value, digits=2):
    return f'{value:.{digits}f}' if isinstance(value, (int, float)) else '—'


def _link(lib, record_id, name=None):
    return {'v': name or record_id, 'id': record_id}


def _try(blocks, title, function, *args):
    """一块算不出来时写一句原因，别的块照常出。"""
    try:
        return function(*args)
    except Exception as error:
        blocks.append({'type': 'prose', 'title': title, 'items': [{'label': '没算出来', 'text': f'{type(error).__name__}: {error}', 'tone': 'warn'}]})
        return None


def preview(lib, row):
    """一条记录的一句话和一条小曲线（真实数据，不是装饰）。row 至少有 id、kind、name、data。"""
    kind, record_id = row['kind'], row['id']
    result = lambda method: (lambda found: json.loads(found[0][0]) if found else None)(
        lib.sql('SELECT value FROM results WHERE record_id=? AND method=?', (record_id, method))[1])
    series = lambda name: (lambda found: (json.loads(found[0][0]), json.loads(found[0][1])) if found else (None, None))(
        lib.sql('SELECT columns, rows FROM series WHERE record_id=? AND name=?', (record_id, name))[1])
    data = row.get('data') or {}
    text, points, axis = '', [], ''
    if kind == 'GPC样品':
        value = result('GPC分子量') or {}
        if value.get('Mw'):
            text = f'Mw {value["Mw"]:.0f} · Mn {value["Mn"]:.0f} · PDI {value["PDI"]:.2f}'
        dist = result('GPC分布') or {}
        points, axis = _thin(list(zip(dist.get('logM') or [], dist.get('dw/dlogM') or []))), 'log M'
    elif kind == '膜':
        value = result('椭偏.块统计') or {}
        stage = (value.get('各阶段') or [{}])[0]
        before = stage.get('处理前') or {}
        if before.get('均值 nm') is not None:
            text = f'{before["均值 nm"]:.1f} nm · CV {before["CV %"]:.2f} %' + (
                ' · 溶解' if stage.get('溶解') else f' · 损失 {stage["损失率 %"]:.2f} %' if stage.get('损失率 %') is not None else '')
        columns, rows = series('读数')
        if columns:
            at = columns.index('膜厚 / nm')
            points, axis = [[index + 1, item[at]] for index, item in enumerate(rows) if item[at] is not None and not item[columns.index('原表平均行')]], '读数'
    elif kind == '老化谱':
        value = result('老化.指标') or {}
        if value.get('a500') is not None:
            text = f'{data.get("条件") or "起点"} {data.get("时间标签") or ""} · A500 ' + ('≥ 4.0' if value.get('at500Ceiling') else f'{value["a500"]:.3f}')
        columns, rows = series('谱')
        points, axis = (_thin(rows), '波长 / nm') if rows else ([], '')
    elif kind == 'NK谱':
        value = result('NK.目标波长') or []
        at = {item['波长 nm']: item for item in value}
        if 193.0 in at:
            text = f'n193 {at[193.0]["n"]:.3f} · k193 {at[193.0]["k"]:.3f}'
        columns, rows = series('nk')
        if rows:
            points, axis = _thin([[item[0], item[2]] for item in rows]), '波长 / nm（k）'
    elif kind == '树脂实验':
        value = result('树脂.产率') or {}
        route = (value.get('各路') or [{}])[0]
        rate = (value.get('合计') or {}).get('产率_%') if isinstance(value.get('合计'), dict) else route.get('产率_%')
        text = (data.get('标题') or '')[:40] + (f' · 产率 {rate:.1f} %' if rate is not None else '')
    elif kind == '工单':
        text = f'{data.get("配方编号", "")} · {data.get("状态", "")}'
    elif kind == '树脂取样':
        text = ' · '.join(str(part) for part in (data.get('种类'), data.get('节点'), '、'.join(data.get('计划检测') or [])) if part)
    elif kind == '树脂批':
        text = ' · '.join(str(part) for part in (data.get('实验编号'), data.get('节点'), data.get('路线')) if part)
    elif kind == '配方':
        text = f'总量 {data.get("总量", "")} g · 固含 {(data.get("固含") or 0) * 100:.2f} %' + (f' · 树脂 {"、".join(data.get("树脂来源") or [])}' if data.get('树脂来源') else '')
    return {'id': record_id, 'kind': kind, 'name': row['name'], 'date': row.get('date') or '', 'text': text, 'points': points, 'axis': axis}


def _records_block(lib, title, ids, limit=24):
    items = []
    for record_id in ids[:limit]:
        found = lib.sql('SELECT id, kind, name, data FROM records WHERE id=? AND deleted_at IS NULL', (record_id,))[1]
        if found:
            item = found[0]
            items.append(preview(lib, {'id': item[0], 'kind': item[1], 'name': item[2], 'data': json.loads(item[3])}))
    return {'type': 'records', 'title': title + (f'（前 {limit} 条，共 {len(ids)} 条）' if len(ids) > limit else ''), 'items': items, 'wide': True} if items else None


def _gpc_table(lib, names_like):
    """名字以某个前缀开头的 GPC 样品：分子量表 + 随时间的 Mw + 叠加分布。"""
    _, found = lib.sql("SELECT r.id, r.name, a.value, b.value FROM records r LEFT JOIN results a ON a.record_id=r.id AND a.method='GPC分子量' "
                       "LEFT JOIN results b ON b.record_id=r.id AND b.method='GPC分布' WHERE r.kind='GPC样品' AND r.deleted_at IS NULL AND r.name LIKE ? ORDER BY r.name",
                       (names_like,))
    rows = []
    for record_id, name, mw, dist in found:
        mw, dist = json.loads(mw) if mw else {}, json.loads(dist) if dist else {}
        rows.append({'id': record_id, 'name': name, 'h': _hours(name), 'mw': mw, 'dist': dist})
    return sorted(rows, key=lambda row: (row['h'] is None, row['h'] or 0, row['name']))


def _gpc_blocks(rows, title='GPC'):
    blocks = []
    if not rows:
        return blocks
    blocks.append({'type': 'table', 'title': f'{title}：分子量（PS 当量读数）', 'wide': True,
                   'columns': ['样品', '时间 / h', 'Mn', 'Mw', 'Mz', 'PDI', 'Mp'],
                   'rows': [[{'v': row['name'], 'id': row['id']}, row['h'], row['mw'].get('Mn'), row['mw'].get('Mw'), row['mw'].get('Mz'), row['mw'].get('PDI'), row['mw'].get('Mp')] for row in rows]})
    timed = [row for row in rows if row['h'] is not None and row['mw'].get('Mw')]
    if len(timed) >= 2:
        blocks.append({'type': 'chart', 'title': f'{title}：Mw 随反应时间', 'x': '反应时间 / h', 'y': 'Mw（PS 当量）',
                       'series': [{'name': 'Mw', 'x': [row['h'] for row in timed], 'y': [row['mw']['Mw'] for row in timed], 'mode': 'lines+markers'},
                                  {'name': 'Mn', 'x': [row['h'] for row in timed], 'y': [row['mw'].get('Mn') for row in timed], 'mode': 'lines+markers'}]})
    drawn = [row for row in rows if row['dist'].get('logM')]
    if drawn:          # 色谱叠加：默认按保留时间，能切分子量、扣基线、归一、勾选、缩放（web/js/overlay.js）
        blocks.append({'type': 'gpc_overlay', 'title': f'{title}：色谱叠加', 'ids': [row['id'] for row in drawn][:24], 'wide': True})
    return blocks


# ---------- 各类型的基础呈现 ----------

def resin_experiment(lib, record):
    from .sci import resin
    data, design = record['data'], record['data'].get('设计') or {}
    blocks = []
    charges = _try(blocks, '投料', resin.charges, record['id'], lib)
    yields = _try(blocks, '产率', resin.yield_, record['id'], lib)
    schedule = _try(blocks, '步骤', resin.schedule, record['id'], lib)
    profile = _try(blocks, '过程', resin.profile, record['id'], lib)
    hplc = _try(blocks, 'HPLC', resin.hplc, record['id'], lib)
    variables = (charges or {}).get('变量') or {}
    var = lambda key: (variables.get(key) or {}).get('值')
    route = ((yields or {}).get('各路') or [{}])
    total = (yields or {}).get('合计') if isinstance((yields or {}).get('合计'), dict) else None
    rate = total['产率_%'] if total else route[0].get('产率_%')
    dry = total['干品_g'] if total else sum(row.get('干品_g') or 0 for row in route) or None
    samples = [link['from'] for link in record['linked_from'] if link['relation'] == '属于']
    stats = [{'label': '状态', 'value': data.get('状态') or design.get('status') or '', 'tone': STATUS_TONE.get(data.get('状态'))},
             {'label': '干品', 'value': dry, 'unit': 'g', 'digits': 2},
             {'label': '产率', 'value': rate, 'unit': '%', 'digits': 1, 'note': f'计划 {yields["计划产率_%"]:.0f} %' if (yields or {}).get('计划产率_%') else '',
              'tone': 'good' if rate and (yields or {}).get('计划产率_%') and rate >= yields['计划产率_%'] else None},
             {'label': '理论净树脂', 'value': (yields or {}).get('理论净树脂_g'), 'unit': 'g', 'digits': 1},
             {'label': '反应', 'value': f'{var("反应内温") or "?"} °C × {var("恒温反应时间") or "?"} h'},
             {'label': '单体比', 'value': var('工序单体比') or var('MonB:MonA摩尔比'), 'digits': 3},
             {'label': '催化剂', 'value': var('工序催化剂mol%') or var('催化剂用量 mol%'), 'unit': 'mol%', 'digits': 3},
             {'label': '取样', 'value': len(samples), 'unit': '个'}]
    blocks.insert(0, {'type': 'stats', 'items': [item for item in stats if item['value'] not in (None, '', 0) or item['label'] == '取样'], 'wide': True})
    blocks.insert(1, {'type': 'prose', 'wide': True, 'items': [item for item in [
        {'label': '路线', 'text': _text(data.get('标题') or design.get('title'))},
        {'label': '目的', 'text': _text(design.get('purpose'))}, {'label': '问题', 'text': _text(data.get('问题') or design.get('question'))},
        {'label': '假设', 'text': _text(data.get('假设') or design.get('hypothesis'))},
        {'label': '结论', 'text': (f'【{data["结论判定"]}】' if data.get('结论判定') else '') + (_text(data.get('结论') or design.get('conclusion')) or ''),
         'tone': 'good' if data.get('结论判定') in ('成立', '达到') else 'warn' if data.get('结论判定') == '不确定' else None}] if item['text']]})
    if charges:
        rows, base = charges['投料'], next((row['物质的量_mmol'] for row in charges['投料'] if row['物质的量_mmol']), None)
        roles = {item['id']: item.get('roleLabel') for item in design.get('charges') or []}
        blocks.append({'type': 'table', 'title': '投料', 'wide': True, 'note': '计划量由设计公式现算；当量以第一个单体为 1。',
                       'columns': ['物料', '角色', '计划 / g', 'mmol', '当量', 'mL', '实测 / g', '偏差 %'],
                       'rows': [[row['名称'], roles.get(row['id']) or ROLE.get(row['角色'], row['角色']), row['质量_g'], row['物质的量_mmol'],
                                 row['物质的量_mmol'] / base if base and row['物质的量_mmol'] else None, row['体积_mL'] or None,
                                 row['实测'] if isinstance(row['实测'], (int, float)) else None,
                                 (row['实测'] - row['质量_g']) / row['质量_g'] * 100 if isinstance(row['实测'], (int, float)) and row['质量_g'] else None] for row in rows]})
    if schedule:
        timing = {row['步骤']: row for row in schedule['时间表']}
        names = {item.get('id'): item.get('name') or item.get('label') or item.get('material') for item in design.get('charges') or []}
        names.update({row['id']: row['名称'] for row in (charges or {}).get('投料') or []})
        items = []
        for step in design.get('steps') or []:
            when = timing.get(step.get('id')) or {}
            meta = ' · '.join(part for part in [f'{when["起_h"]:g}–{when["止_h"]:g} h' if when.get('起_h') is not None and when.get('止_h') is not None else '',
                                                 f'{when["温度_C"]:g} °C' if isinstance(when.get('温度_C'), (int, float)) else ''] if part)
            items.append({'title': step.get('title') or step.get('category') or '', 'tag': step.get('category') or step.get('stage'), 'meta': meta,
                          'text': step.get('text') or '', 'chips': [names.get(charge, charge) if isinstance(charge, str) else charge.get('name') or names.get(charge.get('id'), charge.get('id'))
                                                                    for charge in step.get('charges') or []]})
        if items:
            blocks.append({'type': 'steps', 'title': f'操作步骤（{len(items)} 步）', 'items': items, 'wide': True})
    if profile:
        temp = profile['温度']
        series = [{'name': '计划内温', 'x': [p['hours'] for p in temp.get('planned') or []], 'y': [p['tempC'] for p in temp.get('planned') or []], 'dash': 'dash'},
                  {'name': '实测内温', 'x': [p['hours'] for p in temp.get('actual') or []], 'y': [p['tempC'] for p in temp.get('actual') or []], 'mode': 'lines+markers'}]
        if any(item['x'] for item in series):
            blocks.append({'type': 'chart', 'title': '过程：温度', 'x': '时间 / h', 'y': '内温 / °C', 'series': [item for item in series if item['x']]})
        solids = profile['固含']
        series = [{'name': '计划固含', 'x': [p['hours'] for p in solids.get('planned') or [] if p.get('value') is not None], 'y': [p['value'] * 100 for p in solids.get('planned') or [] if p.get('value') is not None], 'dash': 'dash'},
                  {'name': '名义固含', 'x': [p['hours'] for p in solids.get('nominal') or [] if p.get('nominalSolids') is not None],
                   'y': [p['nominalSolids'] * 100 for p in solids.get('nominal') or [] if p.get('nominalSolids') is not None]},
                  {'name': '实测固含', 'x': [p['hours'] for p in solids.get('actual') or [] if p.get('solids') is not None], 'y': [p['solids'] * 100 for p in solids.get('actual') or [] if p.get('solids') is not None], 'mode': 'lines+markers'}]
        if any(item['x'] for item in series):
            blocks.append({'type': 'chart', 'title': '过程：固含', 'x': '时间 / h', 'y': '固含 / %', 'series': [item for item in series if item['x']]})
        if profile['偏差']:
            blocks.append({'type': 'table', 'title': '和计划不一样的地方', 'columns': ['说明', '计划', '实际', '单位', '原因'],
                           'rows': [[row['说明'], row['计划'], row['实际'], row['单位'], row['原因']] for row in profile['偏差']]})
    if hplc and hplc['取样点']:
        names = sorted({name for point in hplc['取样点'] for name in point['组分']})
        blocks.append({'type': 'chart', 'title': 'HPLC：单体剩余', 'x': '时间 / h', 'y': '剩余 / %（投料 = 100）', 'note': hplc['口径'],
                       'series': [{'name': name, 'x': [p['时间_h'] for p in hplc['取样点']], 'y': [(p['组分'].get(name) or {}).get('剩余_%') for p in hplc['取样点']], 'mode': 'lines+markers'} for name in names]})
        blocks.append({'type': 'table', 'title': 'HPLC 取样点', 'columns': ['样品', '时间 / h', '取样 / g', '检测日期'] + [f'{name} mg/g' for name in names] + [f'{name} 剩余 %' for name in names],
                       'rows': [[p['样品'], p['时间_h'], p['取样量_g'], p['检测日期']] + [(p['组分'].get(name) or {}).get('含量_mg_per_g') for name in names]
                                + [(p['组分'].get(name) or {}).get('剩余_%') for name in names] for p in hplc['取样点']]})
    if yields and yields.get('各路'):
        blocks.append({'type': 'table', 'title': '产率', 'note': f'{yields["口径"]}。{yields.get("公式") or ""}',
                       'columns': ['产品', '路线', '干品 / g', '分料比', '理论 / g', '产率 %', '历史总表 %'],
                       'rows': [[row['产品'], row['路线'], row['干品_g'], row['分料比'], row['理论_g'], row['产率_%'], row['历史总表产率_%']] for row in yields['各路']]})
    blocks += _gpc_blocks(_gpc_table(lib, (data.get('编号') or record['name']) + '-%'), 'GPC')
    block = _records_block(lib, '取样', samples)
    if block:
        blocks.append(block)
    return blocks


def formulation(lib, record):
    data, blocks = record['data'], []
    check = _res(record, '配方核算') or {}
    totals = check.get('totals') or {}
    blocks.append({'type': 'stats', 'wide': True, 'items': [
        {'label': '总量', 'value': data.get('总量'), 'unit': 'g'}, {'label': '固含', 'value': (data.get('固含') or 0) * 100, 'unit': '%', 'digits': 3},
        {'label': '树脂固体', 'value': totals.get('resin_solid_mass_g'), 'unit': 'g', 'digits': 3},
        {'label': '直接补加溶剂', 'value': totals.get('solvent_addition_g'), 'unit': 'g', 'digits': 3},
        {'label': '质量差', 'value': (check.get('checks') or {}).get('mass_delta_g'), 'unit': 'g', 'digits': 3, 'tone': 'good' if abs((check.get('checks') or {}).get('mass_delta_g') or 0) < 1e-6 else 'warn'},
        {'label': '修订', 'value': data.get('修订')}, {'label': '建立', 'value': (data.get('建立时间') or '')[:10]}]})
    if check.get('lines'):
        category = {'resin': '树脂', 'additive': '添加剂', 'solvent': '溶剂'}
        blocks.append({'type': 'table', 'title': '组成（核算）', 'wide': True, 'note': '按配方核算引擎现算；预混液按组成拆成固体和溶剂。',
                       'columns': ['类别', '原料', '名称', '计划 / g', '固体 / g', '溶剂 / g', '占固体 %', '备注'],
                       'rows': [[category.get(line['category'], line['category']), line['source_id'], line['name'], line['planned_mass_g'], line['solid_mass_g'],
                                 line['solvent_mass_g'], line['solid_mass_g'] / totals['solid_mass_g'] * 100 if totals.get('solid_mass_g') else None, line.get('note')]
                                for line in check['lines']]})
    if check.get('solvent_balance'):
        blocks.append({'type': 'table', 'title': '溶剂平衡', 'columns': ['溶剂', '目标比例', '目标 / g', '带入 / g', '补加 / g', '最终 / g'],
                       'rows': [[row['source_id'], row['fraction'], row['target_mass_g'], row['carried_mass_g'], row['addition_mass_g'], row['final_mass_g']] for row in check['solvent_balance']]})
    code = data.get('配方编号') or record['name']
    films = [row for row in lib.rows('膜', where=f"json_extract(data,'$.配方编号') = '{code}'")]
    if films:
        rows = []
        for film in films:
            stat = ((lib.get(film['id'])['results'].get('椭偏.块统计') or {}).get('value') or {}).get('各阶段') or [{}]
            stage = stat[0]
            rows.append([{'v': film['name'], 'id': film['id']}, film.get('测试日'), f'{film.get("转速") or ""} rpm · {film.get("烘烤温度") or ""} °C {film.get("烘烤时间") or ""} s',
                         (stage.get('处理前') or {}).get('均值 nm'), (stage.get('处理后') or {}).get('均值 nm'), stage.get('损失率 %'),
                         ((stage.get('光学') or {}).get('n_193') or {}).get('处理前'), ((stage.get('光学') or {}).get('k_193') or {}).get('处理前')])
        blocks.append({'type': 'table', 'title': f'涂膜和椭偏（{len(films)} 块膜）', 'wide': True,
                       'columns': ['膜', '测试日', '条件', '处理前 / nm', '处理后 / nm', '损失 %', 'n193', 'k193'], 'rows': rows})
    groups = lib.sql("SELECT r.id, r.name, v.value FROM records r LEFT JOIN results v ON v.record_id=r.id AND v.method='NK.组均值' "
                     "WHERE r.kind='NK分组' AND r.deleted_at IS NULL AND r.name LIKE ?", (code + '%',))[1]
    if groups:
        rows = []
        for group_id, name, value in groups:
            at = {item.get('波长 nm'): item for item in ((json.loads(value) if value else {}).get('目标波长') or [])}
            pick = lambda wl, key: (at.get(wl) or {}).get(key + ' 均值', (at.get(wl) or {}).get(key))
            rows.append([{'v': name, 'id': group_id}, pick(193.0, 'n'), pick(193.0, 'k'), pick(248.0, 'n'), pick(248.0, 'k')])
        blocks.append({'type': 'table', 'title': f'n/k（{len(groups)} 组）', 'columns': ['分组', 'n193', 'k193', 'n248', 'k248'], 'rows': rows})
    aging = _aging_trend(lib, code)
    if aging:
        blocks.append(aging)
    orders = [link['from'] for link in record['linked_from'] if link['relation'] == '按配方']
    block = _records_block(lib, '工单', orders)
    if block:
        blocks.append(block)
    return blocks


RESPONSE_NAMES = ['处理前膜厚 / nm', '损失 %', 'n193', 'k193', 'A500（最近）', 'A500 是哪天']


def _formula_metrics(lib, code):
    """一个配方测到的结果（给 DOE 结果表）：膜厚和损失取这个配方各块膜的平均；n、k 有 n/k 分组用分组均值，没有用膜的椭偏；A500 取最近一天。"""
    out = {}
    if not code:
        return out
    thick, loss, n193, k193 = [], [], [], []
    for film in lib.rows('膜', where=f"json_extract(data,'$.配方编号') = '{code}'"):
        stage = (((lib.get(film['id'])['results'].get('椭偏.块统计') or {}).get('value') or {}).get('各阶段') or [{}])[0]
        if (stage.get('处理前') or {}).get('均值 nm') is not None:
            thick.append(stage['处理前']['均值 nm'])
        if stage.get('损失率 %') is not None:
            loss.append(stage['损失率 %'])
        optics = stage.get('光学') or {}
        for key, bucket in (('n_193', n193), ('k_193', k193)):
            value = (optics.get(key) or {}).get('处理前')
            value = value.get('mean') if isinstance(value, dict) else value
            if isinstance(value, (int, float)):
                bucket.append(value)
    groups = lib.sql("SELECT v.value FROM records r LEFT JOIN results v ON v.record_id=r.id AND v.method='NK.组均值' "
                     "WHERE r.kind='NK分组' AND r.deleted_at IS NULL AND r.name LIKE ?", (code + '%',))[1]
    for (value,) in groups:
        at = {item.get('波长 nm'): item for item in ((json.loads(value) if value else {}).get('目标波长') or [])}
        row = at.get(193.0) or {}
        if row.get('n 均值', row.get('n')) is not None:
            n193 = [row.get('n 均值', row.get('n'))]
            k193 = [row.get('k 均值', row.get('k'))]
            break
    average = lambda values: sum(values) / len(values) if values else None
    out.update({'处理前膜厚 / nm': average(thick), '损失 %': average(loss), 'n193': average(n193), 'k193': average(k193)})
    _, aging = lib.sql("SELECT json_extract(r.data,'$.天数'), json_extract(v.value,'$.a500'), json_extract(r.data,'$.条件'), json_extract(v.value,'$.at500Ceiling') FROM records r "
                       "JOIN results v ON v.record_id=r.id AND v.method='老化.指标' WHERE r.kind='老化谱' AND r.deleted_at IS NULL "
                       "AND json_extract(r.data,'$.样品')=? ORDER BY 1 DESC LIMIT 1", (code,))
    if aging and aging[0][1] is not None:
        day, value, condition, ceiling = aging[0]
        out['A500（最近）'] = f'≥ {value:.1f}（饱和）' if ceiling else value          # 饱和读数只是下限：写成 ≥，不进平均
        out['A500 是哪天'] = f'第 {day:g} 天' + (f'，{condition}' if condition else '')
    return out


def _aging_trend(lib, sample):
    _, found = lib.sql("SELECT json_extract(r.data,'$.条件'), json_extract(r.data,'$.天数'), json_extract(v.value,'$.a500'), json_extract(v.value,'$.at500Ceiling') "
                       "FROM records r JOIN results v ON v.record_id=r.id AND v.method='老化.指标' WHERE r.kind='老化谱' AND r.deleted_at IS NULL "
                       "AND json_extract(r.data,'$.样品')=? ORDER BY 2", (sample,))
    if not found:
        return None
    lines = {}
    for condition, days, a500, ceiling in found:
        line = lines.setdefault(condition or '起点', {'x': [], 'y': []})
        line['x'].append(days or 0)
        line['y'].append(a500)
    return {'type': 'chart', 'title': '储存老化：A500 随天数', 'x': '天数', 'y': 'A500', 'note': '500 nm 显色峰；≥ 4.0 是饱和下限。',
            'series': [{'name': name, 'x': line['x'], 'y': line['y'], 'mode': 'lines+markers'} for name, line in lines.items()]}


def work_order(lib, record):
    data, blocks = record['data'], []
    plan = data.get('冻结计划') or {}
    totals = plan.get('totals') or {}
    blocks.append({'type': 'stats', 'wide': True, 'items': [
        {'label': '状态', 'value': data.get('状态')}, {'label': '计划日期', 'value': data.get('计划日期')},
        {'label': '配方', 'value': data.get('配方编号')}, {'label': '目标总量', 'value': totals.get('target_mass_g'), 'unit': 'g'},
        {'label': '固含', 'value': (totals.get('solid_fraction') or 0) * 100, 'unit': '%', 'digits': 3},
        {'label': '操作人', 'value': (data.get('实称信息') or {}).get('operator')}]})
    weigh = record['series'].get('称量')
    if weigh:
        columns, rows = weigh['columns'], weigh['rows']
        at = {name: index for index, name in enumerate(columns)}
        blocks.append({'type': 'table', 'title': '称量', 'wide': True, 'columns': ['顺序', '原料', '名称', '计划 / g', '实称 / g', '偏差 %', '复核'],
                       'rows': [[row[at['行']], row[at['来源编号']], row[at['名称']], row[at['计划量']], row[at['实称']],
                                 (row[at['实称']] - row[at['计划量']]) / row[at['计划量']] * 100 if isinstance(row[at['实称']], (int, float)) and row[at['计划量']] else None,
                                 row[at['复核']]] for row in rows]})
    elif plan.get('lines'):
        blocks.append({'type': 'table', 'title': '计划称量', 'wide': True, 'columns': ['原料', '名称', '计划 / g', '固体 / g'],
                       'rows': [[line['source_id'], line['name'], line['planned_mass_g'], line['solid_mass_g']] for line in plan['lines']]})
    if (data.get('实称信息') or {}).get('note'):
        blocks.append({'type': 'prose', 'items': [{'label': '实称备注', 'text': data['实称信息']['note']}]})
    return blocks


def gpc_sample(lib, record):
    data, blocks = record['data'], []
    value = _res(record, 'GPC分子量') or {}
    blocks.append({'type': 'stats', 'wide': True, 'items': [
        {'label': 'Mn', 'value': value.get('Mn'), 'digits': 0}, {'label': 'Mw', 'value': value.get('Mw'), 'digits': 0},
        {'label': 'Mz', 'value': value.get('Mz'), 'digits': 0}, {'label': 'PDI', 'value': value.get('PDI'), 'digits': 3},
        {'label': 'Mp', 'value': value.get('Mp'), 'digits': 0}, {'label': 'D50', 'value': value.get('D50'), 'digits': 0},
        {'label': '标准曲线', 'value': value.get('标准曲线') or data.get('标准曲线')},
        {'label': '外推面积', 'value': (value.get('外推面积占比') or 0) * 100, 'unit': '%', 'digits': 2, 'tone': 'warn' if (value.get('外推面积占比') or 0) > 0.01 else None}]})
    prefix = re.sub(r'-[^-]*$', '', record['name'])
    siblings = _gpc_table(lib, prefix + '-%') if prefix != record['name'] else []
    if len(siblings) > 1:
        blocks += _gpc_blocks(siblings, f'同批 {prefix}')
    return blocks


def film(lib, record):
    data, blocks = record['data'], []
    stat = _res(record, '椭偏.块统计') or {}
    for stage in stat.get('各阶段') or []:
        before, after, optics = stage.get('处理前') or {}, stage.get('处理后') or {}, stage.get('光学') or {}
        blocks.append({'type': 'stats', 'wide': True, 'title': stage.get('处理后阶段') or '', 'items': [
            {'label': '处理前膜厚', 'value': before.get('均值 nm'), 'unit': 'nm', 'digits': 2, 'note': f'CV {_f(before.get("CV %"))} % · n={before.get("n")}' if before else ''},
            {'label': '处理后膜厚', 'value': after.get('均值 nm'), 'unit': 'nm', 'digits': 2, 'note': f'CV {_f(after.get("CV %"))} %' if after else ''},
            {'label': '损失', 'value': stage.get('损失率 %'), 'unit': '%', 'digits': 2, 'tone': 'bad' if stage.get('溶解') else 'good' if (stage.get('损失率 %') or 0) < 2 else 'warn'},
            {'label': 'n193', 'value': (optics.get('n_193') or {}).get('处理前'), 'digits': 4}, {'label': 'k193', 'value': (optics.get('k_193') or {}).get('处理前'), 'digits': 4},
            {'label': 'n248', 'value': (optics.get('n_248') or {}).get('处理前'), 'digits': 4}]})
    units = {'转速': ' rpm', '烘烤温度': ' °C', '烘烤时间': ' s', '后烘温度': ' °C', '后烘时间': ' s'}
    blocks.append({'type': 'kv', 'title': '条件', 'items': [[key, f'{data[key]}{units[key]}' if key in units and isinstance(data[key], (int, float)) else word(data.get(key))]
                                                     for key in ('测试日', '配方编号', '转速', '烘烤温度', '烘烤时间', '处理', '处理时间', '后烘温度', '后烘时间', '样品说明', '表脚注') if data.get(key) not in (None, '')]})
    reads = record['series'].get('读数')
    if reads:
        columns = reads['columns']
        keep = [index for index, name in enumerate(columns) if name not in ('读数id', '其它', '原表平均行')]
        rows = [row for row in reads['rows'] if not row[columns.index('原表平均行')]]
        blocks.append({'type': 'table', 'title': f'读数（{len(rows)} 个）', 'wide': True, 'columns': [columns[index] for index in keep], 'rows': [[_cell(row[index]) for index in keep] for row in rows]})
        stages = {}
        for row in rows:
            stages.setdefault(word(row[columns.index('阶段')]), []).append(row)
        blocks.append({'type': 'chart', 'title': '膜厚读数', 'x': '次数', 'y': '膜厚 / nm',
                       'series': [{'name': name, 'x': [row[columns.index('次数')] for row in items], 'y': [row[columns.index('膜厚 / nm')] for row in items], 'mode': 'markers'} for name, items in stages.items()]})
    tips = _res(record, '椭偏.质量提示') or []
    if tips:
        blocks.append({'type': 'prose', 'title': '质量提示', 'items': [{'label': tip.get('级别') or '提示', 'text': f'{tip.get("规则")}：{word(tip.get("阶段")) or ""} 第 {tip.get("次数")} 次 {tip.get("列") or ""}', 'tone': 'warn'} for tip in tips]})
    return blocks


def nk_spectrum(lib, record):
    blocks = []
    target = _res(record, 'NK.目标波长') or []
    at = {item['波长 nm']: item for item in target}
    blocks.append({'type': 'stats', 'wide': True, 'items': [item for item in [
        {'label': 'n193', 'value': (at.get(193.0) or {}).get('n'), 'digits': 4}, {'label': 'k193', 'value': (at.get(193.0) or {}).get('k'), 'digits': 4},
        {'label': 'n248', 'value': (at.get(248.0) or {}).get('n'), 'digits': 4}, {'label': 'k248', 'value': (at.get(248.0) or {}).get('k'), 'digits': 4},
        {'label': '193 穿透深度', 'value': (at.get(193.0) or {}).get('穿透深度 nm'), 'unit': 'nm', 'digits': 1},
        {'label': '分组', 'value': record['data'].get('分组')}, {'label': '轮次', 'value': record['data'].get('轮次')},
        {'label': '纳入', 'value': '是' if record['data'].get('纳入') else '否'}] if item['value'] is not None]})
    spectrum = record['series'].get('nk')
    if spectrum:
        rows = spectrum['rows']
        blocks.append({'type': 'chart', 'title': 'n', 'x': '波长 / nm', 'y': 'n', 'series': [{'name': 'n', 'x': [row[0] for row in rows], 'y': [row[1] for row in rows]}]})
        blocks.append({'type': 'chart', 'title': 'k', 'x': '波长 / nm', 'y': 'k', 'series': [{'name': 'k', 'x': [row[0] for row in rows], 'y': [row[2] for row in rows]}]})
    if target:
        keys = [key for key in target[0] if key not in ('来源', '在实测范围内')]
        blocks.append({'type': 'table', 'title': '目标波长', 'wide': True, 'columns': keys, 'rows': [[item.get(key) for key in keys] for item in target]})
    return blocks


def nk_group(lib, record):
    blocks = []
    members = [link['from'] for link in record['linked_from'] if link['relation'] == '属于']
    series = []
    for member in members[:12]:
        item = lib.get(member, with_series=True)
        table = item['series'].get('nk')
        if table:
            series.append({'name': item['name'], 'x': [row[0] for row in table['rows']], 'y': [row[2] for row in table['rows']]})
    if series:
        blocks.append({'type': 'chart', 'title': f'k 叠加（{len(series)} 条）', 'x': '波长 / nm', 'y': 'k', 'series': series, 'wide': True})
    mean = _res(record, 'NK.组均值') or {}
    if mean.get('目标波长'):
        keys = list(mean['目标波长'][0])
        blocks.append({'type': 'table', 'title': '组均值', 'wide': True, 'columns': keys, 'rows': [[item.get(key) for key in keys] for item in mean['目标波长']]})
    block = _records_block(lib, '组里的谱', members)
    if block:
        blocks.append(block)
    return blocks


def aging_spectrum(lib, record):
    data, blocks = record['data'], []
    value = _res(record, '老化.指标') or {}
    change = _res(record, '老化.对起点变化') or {}
    blocks.append({'type': 'stats', 'wide': True, 'items': [
        {'label': 'A500', 'value': '≥ 4.0' if value.get('at500Ceiling') else value.get('a500'), 'digits': 3, 'note': '饱和，下限' if value.get('at500Ceiling') else ''},
        {'label': '峰位', 'value': (value.get('peak') or {}).get('nm'), 'unit': 'nm'}, {'label': '峰高', 'value': value.get('height'), 'digits': 3},
        {'label': '半峰宽', 'value': value.get('fwhm'), 'unit': 'nm', 'digits': 1},
        {'label': '对起点 ΔA500', 'value': change.get('delta'), 'digits': 3}, {'label': '样品', 'value': data.get('样品')},
        {'label': '条件', 'value': data.get('条件') or '起点'}, {'label': '时间', 'value': data.get('时间标签')}]})
    _, found = lib.sql("SELECT r.id, r.name, json_extract(r.data,'$.天数'), s.rows FROM records r JOIN series s ON s.record_id=r.id AND s.name='谱' "
                       "WHERE r.kind='老化谱' AND r.deleted_at IS NULL AND json_extract(r.data,'$.样品')=? AND "
                       "(json_extract(r.data,'$.条件')=? OR json_extract(r.data,'$.条件') IS NULL) ORDER BY 3", (data.get('样品'), data.get('条件')))
    if found:
        blocks.append({'type': 'chart', 'title': f'{data.get("样品")} · {data.get("条件") or "起点"}：各天的谱', 'x': '波长 / nm', 'y': '吸光度', 'wide': True,
                       'series': [{'name': f'{name}', 'x': [row[0] for row in json.loads(rows)], 'y': [row[1] for row in json.loads(rows)]} for _, name, _, rows in found[:12]]})
    trend = _aging_trend(lib, data.get('样品'))
    if trend:
        blocks.append(trend)
    return blocks


def resin_sample(lib, record):
    data, blocks = record['data'], []
    blocks.append({'type': 'stats', 'wide': True, 'items': [{'label': '实验', 'value': data.get('实验编号')}, {'label': '种类', 'value': data.get('种类')},
                                                            {'label': '节点', 'value': data.get('节点')}, {'label': '计划检测', 'value': '、'.join(data.get('计划检测') or [])},
                                                            {'label': '时间', 'value': _hours(record['name']), 'unit': 'h'}]})
    name = re.sub(r'\.0h$', 'H', record['name'], flags=re.I)
    rows = [row for row in _gpc_table(lib, data.get('实验编号', '') + '-%') if row['name'].lower() in (record['name'].lower(), name.lower())]
    blocks += _gpc_blocks(rows, 'GPC')
    metrics = record['series'].get('指标')
    if metrics and metrics['rows']:
        blocks.append({'type': 'table', 'title': '指标', 'columns': metrics['columns'], 'rows': metrics['rows']})
    return blocks


def ellipsometry_day(lib, record):
    blocks = []
    groups = _res(record, '椭偏.组统计') or []
    if groups:
        blocks.append({'type': 'table', 'title': '各组', 'wide': True, 'columns': ['组', '配方编号', '块数', '条件', '对应状态'],
                       'rows': [[item.get('组'), item.get('配方编号'), item.get('块数'), conditions_text(item.get('条件')), item.get('对应状态')] for item in groups]})
    verdict = _res(record, '椭偏.参比判定') or []
    if verdict:
        blocks.append({'type': 'table', 'title': '和参比比', 'wide': True, 'columns': ['组', '判定', '说明'], 'rows': [[item.get('组'), item.get('判定'), item.get('说明')] for item in verdict]})
    block = _records_block(lib, '这一天的膜', [link['from'] for link in record['linked_from']])
    if block:
        blocks.append(block)
    return blocks


def doe(lib, record):
    data, blocks = record['data'], []
    blocks.append({'type': 'prose', 'wide': True, 'items': [item for item in [{'label': '目的', 'text': data.get('目的')}, {'label': '判据', 'text': data.get('判据')}] if item['text']]})
    factors = data.get('因子') or []
    if factors:
        blocks.append({'type': 'table', 'title': '因子', 'columns': ['因子', '类型', '分组', '值', '单位'],
                       'rows': [[item.get('label') or item.get('name'), FACTOR_TYPE.get(item.get('type'), item.get('type')), FACTOR_GROUP.get(item.get('group'), item.get('group')),
                                 value_text(item.get('value')), item.get('unit')] for item in factors]})
    runs = data.get('实验行') or []
    if runs:
        # 每个实验点：因子水平 + 测到的结果（膜厚、损失、n/k、老化），一行一个配方；下面按因子水平求平均（主效应）
        named = {item.get('key'): (item.get('label') or item.get('name') or item.get('key')) + (f'（{item["unit"]}）' if item.get('unit') else '') for item in factors}
        keys = [key for key in named if any(key in (run.get('values') or {}) for run in runs)]
        keys += sorted({key for run in runs for key in (run.get('values') or {})} - set(keys) - {'total_mass_g'})
        metrics = {run.get('code'): _formula_metrics(lib, run.get('code')) for run in runs}
        names = [name for name in RESPONSE_NAMES if any(metrics[run.get('code')].get(name) is not None for run in runs)]
        blocks.append({'type': 'table', 'title': f'实验点和结果（{len(runs)}）', 'wide': True,
                       'columns': ['配方'] + [named.get(key, key) for key in keys] + names,
                       'rows': [[{'v': run.get('code'), 'find': run.get('code')}] + [(run.get('values') or {}).get(key) for key in keys]
                                + [metrics[run.get('code')].get(name) for name in names] for run in runs]})
        if names:
            effect_rows = []
            for key in keys:
                levels = []
                for run in runs:
                    level = (run.get('values') or {}).get(key)
                    if level not in levels:
                        levels.append(level)
                if len(levels) < 2:
                    continue
                for level in levels:
                    group = [metrics[run.get('code')] for run in runs if (run.get('values') or {}).get(key) == level]
                    def mean(name):
                        values = [m[name] for m in group if isinstance(m.get(name), (int, float))]
                        capped = sum(1 for m in group if str(m.get(name) or '').startswith('≥'))
                        average = sum(values) / len(values) if values else None
                        if not capped:
                            return average
                        if average is None:
                            return f'都饱和（≥ 4.0）'
                        return f'{average:.3f}；另 {capped} 个饱和（≥ 4.0）没算进来，实际更高'
                    effect_rows.append([named.get(key, key), level, len(group)] + [mean(name) for name in names if name != 'A500 是哪天'])
            if effect_rows:
                blocks.append({'type': 'table', 'title': '主效应（每个因子水平下结果的平均）', 'wide': True, 'columns': ['因子', '水平', '几个点'] + [name for name in names if name != 'A500 是哪天'], 'rows': effect_rows})
                blocks.append({'type': 'prose', 'wide': True, 'items': [{'label': '怎么读', 'text': '同一个因子不同水平的平均差得多，说明这个因子影响大。点少（每个水平一两个点）只能看方向，不能下显著的结论；没测的格子空着；A500 饱和（≥ 4.0）的点只是下限，不进平均。'}]})
    block = _records_block(lib, '实验点的工单', [link['to'] for link in record['links']])
    if block:
        blocks.append(block)
    return blocks


def gpc_experiment(lib, record):
    """GPC 实验：成员样品（按它自己记的反应时间）的分子量表、Mw 随时间、分布叠加。"""
    members = record['series'].get('成员') or {'columns': [], 'rows': []}
    at = {name: index for index, name in enumerate(members['columns'])}
    rows = []
    for row in members['rows']:
        sample_id = row[at['样品id']] if '样品id' in at else None
        found = lib.sql("SELECT r.id, r.name, a.value, b.value FROM records r LEFT JOIN results a ON a.record_id=r.id AND a.method='GPC分子量' "
                        "LEFT JOIN results b ON b.record_id=r.id AND b.method='GPC分布' WHERE r.id IN (?, ?)", (sample_id, 'gpc-' + str(sample_id)))[1]
        if not found:
            continue
        record_id, name, mw, dist = found[0]
        hours = row[at['反应时间']] if '反应时间' in at and isinstance(row[at['反应时间']], (int, float)) else _hours(name)
        if '时间单位' in at and row[at['时间单位']] in ('min', '分钟') and hours is not None:
            hours = hours / 60
        rows.append({'id': record_id, 'name': name, 'h': hours, 'mw': json.loads(mw) if mw else {}, 'dist': json.loads(dist) if dist else {}})
    if not rows:
        ids = [link['to'] for link in record['links'] if link['relation'] == '成员']
        for record_id in ids:
            found = lib.sql("SELECT r.id, r.name, a.value, b.value FROM records r LEFT JOIN results a ON a.record_id=r.id AND a.method='GPC分子量' "
                            "LEFT JOIN results b ON b.record_id=r.id AND b.method='GPC分布' WHERE r.id=?", (record_id,))[1]
            if found:
                _, name, mw, dist = found[0]
                rows.append({'id': record_id, 'name': name, 'h': _hours(name), 'mw': json.loads(mw) if mw else {}, 'dist': json.loads(dist) if dist else {}})
    rows.sort(key=lambda row: (row['h'] is None, row['h'] or 0, row['name']))
    blocks = [{'type': 'prose', 'wide': True, 'items': [{'label': '说明', 'text': record['data'].get('说明')}]}] if record['data'].get('说明') else []
    return blocks + _gpc_blocks(rows, record['name'])


def resin_batch(lib, record):
    data = record['data']
    blocks = [{'type': 'stats', 'wide': True, 'items': [{'label': '批号', 'value': data.get('批号')}, {'label': '实验', 'value': data.get('实验编号')},
                                                       {'label': '节点', 'value': data.get('节点')}, {'label': '路线', 'value': data.get('路线') or '—'}]}]
    blocks += _gpc_blocks(_gpc_table(lib, (data.get('批号') or record['name']) + '%'), 'GPC')
    experiment = [link['to'] for link in record['links'] if link['relation'] == '来自']
    block = _records_block(lib, '来自的实验', experiment)
    if block:
        blocks.append(block)
    return blocks


def brief_view(lib, record):
    """简报：页头一句话 → 关键数 → 新进库的图（程序算的）→ 助手写的发现和建议 → 现在等你拍板的卡片（能直接回答）。"""
    data = record['data']
    numbers = data.get('数') or {}
    blocks = [{'type': 'hero', 'text': data.get('一句话') or record['name'], 'wide': True}]
    fresh, moved, health = numbers.get('新进库') or {}, numbers.get('搬入旧数据') or {}, numbers.get('数据体检') or {}
    if numbers:
        from .loop import inbox
        cards = inbox.view()['cards']
        blocks.append({'type': 'stats', 'wide': True, 'items': [
            {'label': '新进库', 'value': sum(fresh.values()), 'unit': '条', 'note': '新做或新导入'},
            {'label': '搬进来的旧数据', 'value': sum(moved.values()), 'unit': '条', 'note': '不算新实验'},
            {'label': '等你拍板', 'value': len(cards), 'unit': '件', 'tone': 'warn' if cards else 'good'},
            {'label': '待关联', 'value': health.get('待确认关联', 0), 'unit': '条', 'note': f'断链 {health.get("断链", 0)}'}]})
        kinds = sorted(set(fresh) | set(moved), key=lambda kind: -(fresh.get(kind, 0) + moved.get(kind, 0)))[:14]
        if kinds:
            blocks.append({'type': 'chart', 'kind': 'bar', 'stack': True, 'title': '这段时间进库的数据', 'x': '', 'y': '条', 'wide': True,
                           'series': [{'name': '新数据', 'x': kinds, 'y': [fresh.get(kind, 0) for kind in kinds]},
                                      {'name': '搬进来的旧数据', 'x': kinds, 'y': [moved.get(kind, 0) for kind in kinds], 'color': '#bdb6a8'}]})
    blocks += [dict(block) for block in data.get('块') or []]
    if numbers:
        from .loop import inbox
        cards = inbox.view()['cards']
        if cards:
            blocks.append({'type': 'decide', 'title': '等你拍板', 'wide': True,
                           'items': [{'lane': item['lane'], 'title': item['title'], 'card': item['card'], 'waiting': item.get('waiting', 0)} for item in cards]})
    return blocks


FACTOR_TYPE = {'numeric': '数值', 'categorical': '分类'}
FACTOR_GROUP = {'additives': '添加剂', 'resins': '树脂', 'solvents': '溶剂', 'process': '工艺'}


def resin_material(lib, record):
    """树脂物料：物性和出处（哪来的、待不待确认），以及用在哪些实验（每个实验投料里存着自己的分子量，和物料库对不对得上）。"""
    data = record['data']
    raw = data.get('原文') or {}
    pending = set(record.get('unconfirmed') or [])
    rows = []
    for field, key, unit, source in (('分子量', 'mw', 'g/mol', 'mwSource'), ('密度', 'density', 'g/mL', 'densitySource'), ('纯度', 'purity', '', 'purityBasis'),
                                     ('溶液浓度', 'concentrationPct', '%', None), ('沸点', 'bp', '°C', 'bpSource'), ('熔点', 'mp', '°C', 'mpSource'),
                                     ('CAS', 'cas', '', 'casSource'), ('分子式', 'formula', '', None)):
        value = data.get(field)
        if value is None and raw.get(key) is None:
            continue
        note = raw.get(source) if source else ''
        if field == '密度' and raw.get('densityTempC') is not None:
            note = f'{raw["densityTempC"]} °C；{note}' if note else f'{raw["densityTempC"]} °C'
        rows.append([field, value, unit, note or '', '待确认（还没进计算）' if field in pending else ''])
    kind = WORDS.get(str(data.get('种类') or ''), data.get('种类'))          # 和表格用同一份词表
    blocks = [{'type': 'prose', 'wide': True, 'items': [{'label': '名称', 'text': ' · '.join(str(x) for x in (data.get('名称'), data.get('简称'), kind, data.get('形态')) if x and x != '未指定')},
                                                        {'label': '别名', 'text': value_text(data.get('别名')) or '—'}]},
              {'type': 'table', 'title': '物性和出处', 'wide': True, 'columns': ['性质', '数值', '单位', '出处 / 依据', '状态'], 'rows': rows}]
    if raw.get('propertyNotes'):
        blocks.append({'type': 'prose', 'wide': True, 'items': [{'label': '查资料时记的', 'text': raw['propertyNotes']}]})
    material_id = data.get('物料id') or record['name']
    used = []
    for row in lib.rows('树脂实验'):
        for charge in (row.get('设计') or {}).get('charges') or []:
            if charge.get('materialId') == material_id:
                mw = charge.get('mw')
                if mw is None:
                    used.append([row['name'], charge.get('roleLabel') or charge.get('role') or '', '（没存，用物料库的）', '跟着物料库变'])
                    continue
                same = data.get('分子量') is None or abs(float(mw) - float(data['分子量'])) < 1e-6
                used.append([row['name'], charge.get('roleLabel') or charge.get('role') or '', mw, '一样' if same else f'不一样（物料库 {data.get("分子量")}）'])
    used.sort(key=lambda item: item[0])
    blocks.append({'type': 'table', 'title': f'用在这些实验（{len(used)}）', 'wide': True, 'columns': ['实验', '角色', '这个实验里存的分子量', '和物料库比'], 'rows': used}
                  if used else {'type': 'prose', 'wide': True, 'items': [{'label': '用在', 'text': '还没有实验用它'}]})
    blocks.append({'type': 'prose', 'wide': True, 'items': [{'label': '说明', 'text': '有的实验投料里存了自己的分子量，改物料库不影响它们；没存的（多是早期实验）跟着物料库算，改物料库会让它们的投料数跟着变——上表"跟着物料库变"的就是。还"待确认"的值不进计算，确认后才用。'}]})
    return blocks


PRESENTERS = {'简报': brief_view, '树脂实验': resin_experiment, '配方': formulation, '工单': work_order, 'GPC样品': gpc_sample, '膜': film, 'NK谱': nk_spectrum,
              'NK分组': nk_group, '老化谱': aging_spectrum, '树脂取样': resin_sample, '椭偏测试日': ellipsometry_day, 'DOE研究': doe, 'GPC实验': gpc_experiment, '树脂物料': resin_material,
              '树脂批': resin_batch}


# ---------- 通用呈现：什么都不藏 ----------

def _cell(value):
    """表格里的一格写成人话（列表用顿号，小结构写成"键：值"），不出现程序格式。"""
    if isinstance(value, bool):
        return '是' if value else '否'
    if isinstance(value, list):
        return '、'.join(str(_cell(item)) for item in value if not _looks_id(item))[:300]
    if isinstance(value, dict):
        return '；'.join(f'{key}：{_cell(item)}' for key, item in value.items() if not technical(key, item) and item not in (None, '', [], {}))[:300]
    return word(value)


WORDS = {'monoA': '单体A', 'monoB': '单体B', 'acid': '酸', 'catalyst': '催化剂', 'solvent': '溶剂', 'base': '碱', 'additive': '添加剂', 'reductant': '还原剂', 'quench': '终止剂', 'neutralizer': '中和剂', 'antisolvent': '沉淀剂', 'monomer': '单体', 'salt': '盐', 'reagent': '试剂', 'other': '其它', 'unknown': '未指定', 'before_solva': '浸泡前', 'after_solva': '浸泡后', 'before': '处理前', 'after': '处理后', 'pchip_anchors': 'PCHIP（连锚点）', 'linear_anchors': '多点直线', 'cubic_spline_anchors': '自然三次样条', 'pchip': 'PCHIP', 'linear': '直线', 'cubic_spline': '三次样条', 'natural_spline': '自然样条'}   # 和 web/js/core.js 的 WORDS 一样


def word(value):
    """程序里的英文取值换成人话（阶段、基线方法、检测器、物料角色）。"""
    return WORDS.get(value, re.sub(r'^detector_(\d+)$', r'检测器 \1', value)) if isinstance(value, str) else value


_CJK = re.compile('[一-鿿]')
_TECH = re.compile(r'版本|格式$|方案$|模板$|锚点|系数$|拟合统计|设置$|快照|payload|原文$|实称信息|过期原因|^来源$|来源状态|条件来源|关联$')


def _looks_id(text):
    return bool(re.match(r'^[0-9a-f]{8,}$|^[0-9a-f-]{20,}$|^(r|frm|res|gpc-sample|ell)-[0-9a-z_-]{6,}$', str(text), re.I))


def technical(key, value=None):
    """程序用的数据（人看了没用）：英文字段名、版本 / 格式 / 方案 / 系数这类、来源记号、一串数或坐标、键大多是英文的结构。和 web/js/core.js 的 technical 一样。"""
    key = str(key)
    if internal(key) or not _CJK.search(key) or _TECH.search(key):
        return True
    if isinstance(value, list):
        return any(isinstance(item, (list, int, float)) and not isinstance(item, bool) for item in value)
    if isinstance(value, dict):
        return sum(not _CJK.search(str(k)) for k in value) > len(value) / 2
    return False


INTERNAL = {'设计', '实验原文', '冻结配方', '冻结计划', '冻结输出', '冻结设计指纹', '仪器表头', '基线锚点', '积分区间', '基线参数', '站点', '原路径', '指纹',
            '原文', '条件来源', '内容指纹', '分析版本', '基线算法版本', '复制自', '节点', '节点种类'}


def internal(key):
    """程序内部用的字段（旧系统 id、uid、指纹、原路径、冻结快照）：存着，人看的页面上不出现。和 web/js/core.js 的 internal 一样。"""
    return key in INTERNAL or bool(re.search(r'(uid|UID|id|ID)$|^旧|sha256|指纹', str(key)))


def generic(lib, record, skip=()):
    data, blocks = record['data'], []
    data = {key: value for key, value in data.items() if not technical(key, value)}
    numbers = [(key, value) for key, value in data.items() if isinstance(value, (int, float)) and not isinstance(value, bool) and key not in skip and not key.endswith('id')]
    if numbers:
        blocks.append({'type': 'stats', 'wide': True, 'items': [{'label': key, 'value': value} for key, value in numbers[:8]]})
    texts = [(key, value) for key, value in data.items() if isinstance(value, str) and len(value) > 40 and key not in skip]
    if texts:
        blocks.append({'type': 'prose', 'wide': True, 'items': [{'label': key, 'text': value} for key, value in texts]})
    for key, value in data.items():
        if key in skip:
            continue
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            columns = [column for column in dict.fromkeys(column for item in value for column in item)
                       if not technical(column, next((item[column] for item in value if item.get(column) is not None), None))][:12]
            blocks.append({'type': 'table', 'title': key, 'wide': True, 'columns': columns, 'rows': [[_cell(item.get(column)) for column in columns] for item in value[:300]]})
        elif isinstance(value, dict) and value:
            flat = [[inner, _cell(item)] for inner, item in value.items()]
            blocks.append({'type': 'kv', 'title': key, 'items': flat[:40]})
    for name, table in record['series'].items():
        if name in skip:
            continue
        columns, rows = table['columns'], table['rows']
        numeric = [index for index in range(len(columns)) if rows and all(row[index] is None or isinstance(row[index], (int, float)) for row in rows[:200])]
        if len(numeric) >= 2 and len(rows) >= 3:
            x = numeric[0]
            blocks.append({'type': 'chart', 'title': name, 'x': columns[x], 'y': columns[numeric[1]] if len(numeric) == 2 else '',
                           'series': [{'name': columns[index], 'x': [row[x] for row in rows], 'y': [row[index] for row in rows]} for index in numeric[1:6]]})
        if len(rows) <= 400:
            blocks.append({'type': 'table', 'title': f'{name}（表）', 'wide': True, 'columns': columns, 'rows': [[_cell(cell) for cell in row] for row in rows]})
    for name, result in record['results'].items():
        if name in skip:
            continue
        value = result.get('value')
        if isinstance(value, dict):
            arrays = {key: item for key, item in value.items() if isinstance(item, list) and len(item) >= 10 and all(isinstance(x, (int, float)) or x is None for x in item)}
            scalars = [[key, _cell(item)] for key, item in value.items() if key not in arrays]
            if scalars:
                blocks.append({'type': 'kv', 'title': name, 'items': scalars[:40]})
            if len(arrays) >= 2:
                keys = list(arrays)
                blocks.append({'type': 'chart', 'title': name, 'x': keys[0], 'y': keys[1], 'series': [{'name': key, 'x': arrays[keys[0]], 'y': arrays[key]} for key in keys[1:3]]})
        elif isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            columns = [column for column in dict.fromkeys(column for item in value for column in item)
                       if not technical(column, next((item[column] for item in value if item.get(column) is not None), None))][:12]
            blocks.append({'type': 'table', 'title': name, 'wide': True, 'columns': columns, 'rows': [[_cell(item.get(column)) for column in columns] for item in value[:300]]})
    return blocks


# ---------- 入口 ----------

def _custom(kind):
    """智能体或人写的呈现：data/呈现/<类型>.py 里的 present(lib, record, blocks)。"""
    path = views_dir() / f'{kind}.py'
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f'lab_view_{abs(hash(kind))}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, 'present', None)


def layout(kind):
    path = views_dir() / f'{kind}.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def save_layout(kind, value):
    views_dir().mkdir(parents=True, exist_ok=True)
    (views_dir() / f'{kind}.json').write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')


def clean(value):
    """NaN、无穷大浏览器读不了：换成空。"""
    if isinstance(value, float) and (value != value or value in (float('inf'), float('-inf'))):
        return None
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    return value


def present(library, record_id):
    return clean(_present(library, record_id))


def _present(library, record_id):
    lib = store.open(library)
    record = lib.get(record_id, with_series=True)
    kind = record['kind']
    base = PRESENTERS.get(kind)
    blocks = []
    if base:
        blocks = _try(blocks, '基础呈现', base, lib, record) or blocks
    else:
        blocks = generic(lib, record)
    custom = _custom(kind)
    source = '基础' if base else '通用'
    if custom:
        try:
            blocks = custom(lib, record, blocks) or blocks
            source += ' + 自定义'
        except Exception as error:
            blocks.insert(0, {'type': 'prose', 'items': [{'label': '自定义呈现出错', 'text': f'{type(error).__name__}: {error}\n{traceback.format_exc(limit=2)}', 'tone': 'warn'}]})
    blocks = [block for block in blocks if block]
    tweak = layout(kind)
    hidden = set(tweak.get('hidden') or [])
    order = {title: index for index, title in enumerate(tweak.get('order') or [])}
    for index, block in enumerate(blocks):
        block['key'] = block.get('title') or f'{block["type"]}-{index}'
    shown = sorted([block for block in blocks if block['key'] not in hidden], key=lambda block: order.get(block['key'], 1000 + blocks.index(block)))
    return {'id': record['id'], 'kind': kind, 'source': source, 'blocks': shown, 'hidden': [block['key'] for block in blocks if block['key'] in hidden],
            'custom': (views_dir() / f'{kind}.py').is_file()}


# ---------- 课题：研发路线 ----------

ROAD_KEY = '树脂.研发路线'


def roadmap(library):
    lib = store.open(library)
    value = lib.setting(ROAD_KEY)
    if not value:
        return None
    model = value['sourceModel']
    return {'source': value.get('source'), 'root': model['root'], 'tasks': model.get('tasks') or [], 'notes': model.get('releaseNotes') or [],
            'summary': value.get('strategicSummary') or {}, 'baseline': value.get('baseline') or [], 'routes': value.get('candidateRoutes') or [],
            'facts': value.get('knownFacts') or [], 'targets': value.get('targets') or [], 'constraints': value.get('importantConstraints') or [],
            'gaps': value.get('dataGaps') or []}


def update_roadmap(library, kind, item_id, changes):
    """改一个节点（status、summary、detail、metric、evidence、title）或一个待办（status、priority、notes…）。改了就存。"""
    lib = store.open(library)
    value = lib.setting(ROAD_KEY)
    model = value['sourceModel']
    if kind == 'task':
        for task in model.get('tasks') or []:
            if task['id'] == item_id:
                task.update(changes)
    else:
        def walk(node):
            if node['id'] == item_id:
                node.update(changes)
            for child in node.get('children') or []:
                walk(child)
        walk(model['root'])
        for node in value.get('flatNodes') or []:
            if node['id'] == item_id:
                node.update(changes)
    lib.set_setting(ROAD_KEY, value)
    return roadmap(library)


# ---------- 列表：实验卡片、带小曲线的样品 ----------

_TAGS = {}


def _num(value, digits):
    return f'{value:.{digits}f}'.rstrip('0').rstrip('.') if isinstance(value, (int, float)) else str(value)


def factor_tags(lib, row):
    """实验卡上的因子标签，和旧实验列表一样最多 5 个：AddH、温度 × 时间、主溶剂、单体A/单体B、催化剂 · 固含、沉降倍数。
    因子由树脂核心按"因子规则"从设计里抽（和研发台的因子对比同一套）。设计没变就用上次抽的。"""
    from .sci import resin
    stamp = json.dumps(row.get('设计') or {}, sort_keys=True, ensure_ascii=False)
    cached = _TAGS.get((lib.name, row['id']))
    if cached and cached[0] == stamp:
        return cached[1]
    found = (resin.factors(row['id'], lib)['因子'] or {}) if row.get('设计') else {}     # 历史资料没有设计，也就没有因子
    f = {key.rsplit('.', 1)[-1]: (item or {}).get('value') for key, item in found.items()}
    tags = []
    if f.get('addh_eq'):
        tags.append(f'AddH {_num(f["addh_eq"], 2)} eq/MonA')
    temp, hours = f.get('reaction_temperature'), f.get('reaction_hours')
    if temp is not None:
        tags.append(f'{_num(temp, 1)} °C' + (f' × {_num(hours, 1)} h' if hours is not None else ''))
    elif hours is not None:
        tags.append(f'{_num(hours, 1)} h')
    if f.get('main_solvent') and not f.get('addh_eq'):
        tags.append(str(f['main_solvent']))
    if f.get('monoA_monoB_ratio') is not None:
        tags.append(f'单体A/单体B {_num(f["monoA_monoB_ratio"], 2)}')
    solids = f.get('reaction_solids')
    parts = [f'{f.get("catalyst") or "催化剂"} {_num(f["catalyst_molpct"], 1)} mol%' if f.get('catalyst_molpct') is not None else '',
             f'固含 {_num(solids * 100 if solids <= 1 else solids, 1)} %' if isinstance(solids, (int, float)) else '']
    if any(parts):
        tags.append(' · '.join(part for part in parts if part))
    if f.get('precipitation_ratio'):
        tags.append(f'沉降 {_num(f["precipitation_ratio"], 1)} 倍')
    _TAGS[(lib.name, row['id'])] = (stamp, tags[:5])
    return tags[:5]


def experiments(library):
    """树脂实验列表：每个实验一张卡，带产率、反应条件、因子标签、GPC 末点和 Mw 随时间的小曲线。"""
    lib = store.open(library)
    rows = lib.rows('树脂实验')
    _, yields = lib.sql("SELECT record_id, value FROM results WHERE method='树脂.产率'")
    yields = {record_id: json.loads(value) for record_id, value in yields}
    _, gpc = lib.sql("SELECT r.name, json_extract(v.value,'$.Mw'), json_extract(v.value,'$.PDI') FROM records r JOIN results v ON v.record_id=r.id AND v.method='GPC分子量' "
                     "WHERE r.kind='GPC样品' AND r.deleted_at IS NULL")
    _, samples = lib.sql("SELECT json_extract(data,'$.实验编号'), count(*) FROM records WHERE kind='树脂取样' AND deleted_at IS NULL GROUP BY 1")
    samples = dict(samples)
    out = []
    for row in rows:
        code = row.get('编号') or row['name']
        design = row.get('设计') or {}
        value = yields.get(row['id']) or {}
        route = (value.get('各路') or [{}])[0]
        rate = value['合计'].get('产率_%') if isinstance(value.get('合计'), dict) else route.get('产率_%')
        mine = [(name, mw, pdi) for name, mw, pdi in gpc if name.upper().startswith(code.upper() + '-') and mw]
        timed = sorted([(_hours(name), mw) for name, mw, _ in mine if _hours(name) is not None])
        final = next((item for item in mine if re.search(r'FP|final|成品|S\d-F', item[0], re.I)), None) or (max(mine, key=lambda item: _hours(item[0]) or -1) if mine else None)
        variables = {item.get('key'): item.get('value') for item in design.get('vars') or [] if isinstance(item, dict)}
        title = row.get('标题') or design.get('title') or ''
        if not variables.get('反应内温'):
            found = re.search(r'(\d{2,3})\s*(?:°C|℃|°)', title)
            variables['反应内温'] = float(found[1]) if found else None
        if not variables.get('恒温反应时间'):
            found = re.search(r'(\d+(?:\.\d+)?)\s*h\b', title)
            variables['恒温反应时间'] = float(found[1]) if found else None
        out.append({'id': row['id'], 'code': code, 'title': row.get('标题') or design.get('title') or '', 'status': row.get('状态') or '', 'list_status': row.get('列表状态') or '',
                    'verdict': row.get('结论判定') or '', 'conclusion': (row.get('结论') or '')[:160], 'question': (row.get('问题') or '')[:160],
                    'parent': row.get('父编号') or design.get('parentCode') or '', 'yield': rate, 'dry': route.get('干品_g'),
                    'temp': variables.get('反应内温'), 'hours': variables.get('恒温反应时间'), 'samples': samples.get(code, 0),
                    'gpc': len(mine), 'mw_final': final[1] if final else None, 'pdi_final': final[2] if final else None, 'final_name': final[0] if final else '',
                    'growth': timed, 'created': ((row.get('实验原文') or {}).get('createdAt') or '')[:10], 'tags': factor_tags(lib, row),
                    'project': row.get('所属项目') or ''})
    number = lambda code: [(0, int(part), '') if part.isdigit() else (1, 0, part) for part in re.split(r'(\d+)', code)]
    return clean(sorted(out, key=lambda item: number(item['code']), reverse=True))


def gallery(library, kind, limit=600):
    lib = store.open(library)
    _, found = lib.sql('SELECT id, kind, name, data, updated_at FROM records WHERE kind=? AND deleted_at IS NULL ORDER BY name LIMIT ?', (kind, limit))
    return clean([preview(lib, {'id': item[0], 'kind': item[1], 'name': item[2], 'data': json.loads(item[3])}) for item in found])
