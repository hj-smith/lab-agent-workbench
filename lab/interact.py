"""交互视图的后端：人在界面上拖、勾、改，这里用原算法现算，人点"存"再写回库。

GPC 基线和积分区间；树脂设计的投料和变量；配方组成和备料；椭偏剔点和参比；n/k 组的纳入勾；老化系列和导入；约定口径。
"""
import json
import math
import re
import shutil
import uuid
from pathlib import Path

import numpy as np

from . import config, kernel, store
from .present import clean


def _thin(xs, ys, keep=1500):
    step = max(1, len(xs) // keep)
    return list(xs[::step]), list(ys[::step])


# ---------- GPC：基线锚点、积分区间 ----------

def gpc_view(library, ref):
    from .sci import gpc
    lib = store.open(library)
    sample = lib.get(ref, 'GPC样品')
    data = sample['data']
    time, signal = gpc._trace(lib, sample['id'])
    out = {'id': sample['id'], 'name': sample['name'], 'method': data.get('基线方法') or 'pchip_anchors',
           'anchors': data.get('基线锚点') or [], 'regions': data.get('积分区间') or [], 'curve': data.get('标准曲线id'),
           'curves': [{'id': row['id'], 'name': row['name'], 'off': bool(row.get('停用')),                    # 停用、过期的在下拉里标出来
                       'expired': bool(row.get('有效期至')) and str(row['有效期至'])[:10] < kernel.now()[:10]} for row in lib.rows('GPC标准曲线')],
           'templates': sorted(({'id': row['id'], 'name': row['name'], 'project': row.get('所属项目') or ''} for row in lib.rows('GPC分析模板')),
                               key=lambda row: (row['project'] != data.get('所属项目'), row['project'], row['name'])),       # 本项目的在前
           'siblings': [{'id': row['id'], 'name': row['name']} for row in lib.rows('GPC样品')                 # 能抄做法的：同项目里有分析的样
                        if row.get('所属项目') == data.get('所属项目') and row.get('积分区间') and row['id'] != sample['id']],
           'new_region_strategy': '外推' if lib.setting('GPC.新区间默认外推') else '只算曲线范围内'}
    t, s = _thin(time, signal)
    out['trace'] = {'t': t, 's': s}
    try:
        out.update(_gpc_compute(lib, sample['id'], None, None, None))
    except Exception as error:                  # 还没有正式分析的样：先给谱图，基线和区间等人来定
        out['problem'] = str(error)
    return clean(out)


def _gpc_compute(lib, ref, anchors, regions, curve, method=None):
    from .sci import gpc
    sample, record, cal, time, signal, base, picked = gpc._prepare(lib, ref, anchors, regions, curve, method)
    result = gpc.analyze(sample['id'], lib, 锚点=anchors, 积分区间=regions, 标准曲线=curve, save=False, 方法=method)
    t, b = _thin(time, list(np.asarray(base.baseline)))
    return {'baseline': {'t': t, 'b': b}, 'result': result, 'valid': [cal.valid_rt_min, cal.valid_rt_max], 'curve_name': record['name']}


def _snap(lib, ref, anchors, snap):
    """拖过、新加、改过时间的锚点：信号取原始色谱在这个时间的值（和旧 GPC 软件一样，锚点贴着曲线）。"""
    from .sci import gpc
    if not snap or not anchors:
        return anchors
    time, signal = gpc._trace(lib, lib.get(ref, 'GPC样品')['id'])
    return [[a[0], float(np.interp(a[0], time, signal))] if index in set(snap) else a for index, a in enumerate(anchors)]


def gpc_try(library, ref, anchors=None, regions=None, curve=None, method=None, snap=None):
    lib = store.open(library)
    anchors = _snap(lib, ref, anchors, snap)
    return clean({**_gpc_compute(lib, ref, anchors, regions, curve, method), 'anchors': anchors})


def gpc_save(library, ref, anchors, regions, curve=None, method=None, snap=None):
    """人调好的基线和区间写回样品，重算分子量和分布并存结果。"""
    from .sci import gpc
    lib = store.open(library)
    anchors = _snap(lib, ref, anchors, snap)
    sample = lib.get(ref, 'GPC样品')
    patch = {'id': sample['id'], '基线锚点': anchors, '积分区间': regions, '基线方法': method or sample['data'].get('基线方法') or 'pchip_anchors'}
    if curve:
        patch['标准曲线id'] = curve
    patch['分析版本'] = (sample['data'].get('分析版本') or 0) + 1
    lib.save('GPC样品', [patch], by='人')
    result = gpc.analyze(sample['id'], lib, save=True)
    gpc.distribution(sample['id'], lib, save=True)
    return clean({'result': result})


SNAP_KEY = 'GPC.套模板时锚点贴曲线'
SNAP_ABOUT = '套分析模板、抄别的样品时，锚点的信号取这条谱在锚点时间的值（和手调时一样贴着曲线）。关掉就原样用模板里的信号值（旧 GPC 软件的做法）。'


def _snap_all(lib):
    return lib.setting(SNAP_KEY, True) is not False


def _plan(lib, source):
    """一套做法从哪来：分析模板或别的样品 → {锚点, 区间, 方法, 曲线, 名字, 类型}。"""
    record = lib.get(source)
    data = record['data']
    return {'anchors': [list(item) for item in data.get('基线锚点') or []], 'regions': [dict(item) for item in data.get('积分区间') or []],
            'method': data.get('基线方法') or 'pchip_anchors', 'curve': data.get('标准曲线id'), 'name': record['name'],
            'kind': '模板' if record['kind'] == 'GPC分析模板' else '样品', 'id': record['id']}


def gpc_template(library, ref, template):
    """沿用分析模板（或抄别的样品）的锚点、区间和基线连接方式（只给出，存不存人定）。锚点按设置贴到这条谱上。"""
    from .sci import gpc
    lib = store.open(library)
    plan = _plan(lib, template)
    anchors, moved = plan['anchors'], []
    if _snap_all(lib):                                      # 落在峰上的锚点挪到附近谷底再贴，报出来
        anchors, moved = gpc.snap_anchors(*gpc._trace(lib, lib.get(ref, 'GPC样品')['id']), plan['anchors'], plan['regions'])
    return {'anchors': anchors, 'regions': plan['regions'], 'method': plan['method'], 'name': plan['name'], 'kind': plan['kind'], 'moved': moved}


def gpc_as_template(library, ref, name, anchors, regions, method):
    """把这个样品现在的做法（含没存的改动）存成分析模板，所属项目跟样品。"""
    lib = store.open(library)
    sample = lib.get(ref, 'GPC样品')
    done = lib.save('GPC分析模板', [{'name': name, '所属项目': sample['data'].get('所属项目'), '基线锚点': anchors, '积分区间': regions,
                                    '基线方法': method or 'pchip_anchors', '修订': 1, '来源样品': sample['name']}], source='GPC 页"存成模板"', by='人')
    return {'id': done[0]['id'], 'name': name}


# ---------- GPC 批处理：一批样品套同一套做法，先看"现在 → 套用后"，勾了再存 ----------

def _numbers(value):
    value = value or {}
    return {key: value.get(key) for key in ('Mn', 'Mw', 'PDI', '外推面积占比')}


def _candidate(sample, plan, curve, snap, trace):
    """这条样品套用后的锚点、区间、方法、曲线。plan 为 None：各自现在的做法（只换曲线）。
    勾了"贴到谱上"：锚点贴到这条谱上，落在峰上的挪到附近谷底再贴（moved 里报出来）；各自现在的做法也一样，原来不在谱上的锚点贴上去。"""
    from .sci import gpc
    data = sample['data']
    if plan:
        anchors, regions, method = plan['anchors'], plan['regions'], plan['method']
    else:
        anchors, regions, method = data.get('基线锚点') or [], data.get('积分区间') or [], data.get('基线方法') or 'pchip_anchors'
    moved = []
    if snap:
        anchors, moved = gpc.snap_anchors(*trace, anchors, regions)
    chosen = (plan or {}).get('curve') if curve == '跟着来源' else curve
    return {'anchors': anchors, 'regions': regions, 'method': method, 'curve': chosen or data.get('标准曲线id'),
            'template': plan['name'] if plan and plan['kind'] == '模板' else None, 'from_sample': plan['name'] if plan and plan['kind'] == '样品' else None,
            'moved': moved}


def gpc_batch_preview(library, ids, source=None, curve=None, snap=None):
    """每条样品：现在的分子量、套用后的分子量、变化、要人看一眼的地方，和要存的做法（存的时候原样写回）。"""
    from .sci import gpc
    lib = store.open(library)
    snap = _snap_all(lib) if snap is None else bool(snap)
    plan = _plan(lib, source) if source else None
    rows = []
    for ref in ids:
        sample = lib.get(ref, 'GPC样品')
        before = ((sample.get('results') or {}).get('GPC分子量') or {}).get('value')
        time, signal = gpc._trace(lib, sample['id'])
        want = _candidate(sample, plan, curve, snap, (time, signal))
        row = {'id': sample['id'], 'name': sample['name'], 'project': sample['data'].get('所属项目') or '', 'before': _numbers(before) if before else None,
               'plan': want, 'notes': [] if before else ['原来没有分析'], 'problems': []}
        own = sample['data'].get('基线锚点') or []
        span = (max(signal) - min(signal)) or 1
        if not plan and snap and any(abs(a[1] - float(np.interp(a[0], time, signal))) > 0.001 * span for a in own):
            row['problems'].append('原来的锚点不在这条谱上，这次贴上去了')
        for item in want['moved']:
            row['notes'].append(f'{item["从"]} min 的锚点落在峰上，挪到附近谷底 {item["到"]} min 再贴')
        outside = [a[0] for a in want['anchors'] if a[0] < time[0] or a[0] > time[-1]]
        if outside:
            row['problems'].append(f'{len(outside)} 个锚点在这条谱的时间范围外')
        try:
            after = gpc.analyze(sample['id'], lib, 锚点=want['anchors'], 积分区间=want['regions'], 标准曲线=want['curve'], save=False, 方法=want['method'])
            row['after'] = _numbers(after)
            if (after.get('外推面积占比') or 0) > 0.01:
                row['problems'].append(f'外推面积 {after["外推面积占比"] * 100:.1f} %')
            if before and before.get('Mw') and after.get('Mw'):
                row['change'] = after['Mw'] / before['Mw'] - 1
                if abs(row['change']) > 0.10:
                    row['problems'].append(f'Mw 变了 {row["change"] * 100:+.0f} %')
        except Exception as error:
            row['after'] = None
            row['problems'].append('算不出来：' + str(error))
        rows.append(row)
    return clean({'rows': rows, 'snap': snap, 'source': {key: plan[key] for key in ('name', 'kind', 'id')} if plan else None})


def gpc_batch_compare(library, ref, plan):
    """点一行看"现在 → 套用后"：原信号、两条基线、两组区间。"""
    lib = store.open(library)
    view = gpc_view(library, ref)
    out = {'trace': view['trace'], 'now': {'baseline': view.get('baseline'), 'regions': view.get('regions') or [], 'anchors': view.get('anchors') or []},
           'valid': view.get('valid')}
    try:
        got = _gpc_compute(lib, ref, plan['anchors'], plan['regions'], plan['curve'], plan['method'])
        out['next'] = {'baseline': got['baseline'], 'regions': plan['regions'], 'anchors': plan['anchors']}
        out['valid'] = got['valid']
    except Exception as error:
        out['next'] = {'regions': plan['regions'], 'anchors': plan['anchors'], 'problem': str(error)}
    return clean(out)


def gpc_batch_save(library, rows):
    """按预览的做法写回：锚点、区间、方法、曲线；分析版本 +1，分子量和分布重算存好。"""
    lib = store.open(library)
    done = []
    for row in rows:
        plan = row['plan']
        try:
            result = gpc_save(library, row['id'], plan['anchors'], plan['regions'], plan.get('curve'), plan.get('method'))['result']
            if plan.get('template'):
                lib.save('GPC样品', [{'id': row['id'], '分析模板': plan['template']}], by='人')
            done.append({'id': row['id'], 'ok': True, 'Mw': result.get('Mw'), 'PDI': result.get('PDI'),
                         'version': lib.get(row['id'], 'GPC样品')['data'].get('分析版本')})
        except Exception as error:
            done.append({'id': row['id'], 'ok': False, 'problem': str(error)})
    return clean({'rows': done})


def gpc_snap_setting(library, value=None):
    lib = store.open(library)
    if value is not None:
        lib.set_setting(SNAP_KEY, bool(value), SNAP_ABOUT)
    return {'snap': _snap_all(lib)}


# ---------- 树脂设计：投料和变量 ----------

MODES = {'mass': '质量 g', 'volume': '体积 mL', 'mmol': '物质的量 mmol', 'eq': '当量（对某物料）', 'molpct': 'mol%（对某物料）',
         'solidFill': '按目标固含补足', 'perProduct': '每克预计产品', 'expr': '公式'}


def resin_design(library, ref):
    from .sci import resin
    lib = store.open(library)
    doc, _, record = resin.design(ref, lib)
    out = resin.call('evaluate', lib, design=doc)
    computed = {item['id']: item for item in out['charges']}
    variables = {item['key']: item for item in out['vars']}
    charges = [{'id': c['id'], 'name': c.get('name') or c.get('material'), 'role': c.get('roleLabel') or c.get('role'), 'amount': c.get('amount') or {},
                'g': computed.get(c['id'], {}).get('g'), 'mmol': computed.get(c['id'], {}).get('mmol'), 'mL': computed.get(c['id'], {}).get('mL'),
                'actual': computed.get(c['id'], {}).get('actual')} for c in doc.get('charges') or []]
    editable = [{'key': v['key'], 'label': v.get('label') or v['key'], 'unit': v.get('unit') or '', 'expr': v.get('expr'),
                 'value': variables.get(v['key'], {}).get('value')} for v in doc.get('vars') or []
                if v.get('expr') is not None and _plain_number(v.get('expr'))]
    labels = {v['key']: v.get('label') or v['key'] for v in doc.get('vars') or []}
    steps = doc.get('steps') or []
    texts = resin.call('cells', lib, design=doc, formulas=[{'t': step.get('text') or ''} for step in steps]) if steps else []
    stage = {'prep': '准备', 'reaction': '反应', 'workup': '后处理'}
    return clean({'code': record['name'], 'charges': charges, 'vars': editable, 'modes': MODES,
                  'computed_vars': [{'key': k, 'label': labels.get(k, k), 'unit': v.get('unit'), 'value': v.get('value')} for k, v in variables.items()
                                    if not k.startswith('charge-') and isinstance(v.get('value'), (int, float)) and k not in {item['key'] for item in editable}][:40],
                  'figure': resin.call('figure', lib, design=doc), 'editable_steps': _step_fields(doc),
                  'steps': [{'id': step.get('id'), 'title': step.get('title') or step.get('category') or '', 'kind': step.get('category') or '', 'stage': stage.get(step.get('stage'), '反应'),
                             'text': (result.get('text') if not result.get('error') else None) or step.get('text') or ''} for step, result in zip(steps, texts)]})


STAGE_KEY = {'准备': 'prep', '反应': 'reaction', '后处理': 'workup'}
STEP_KIND = {'text': '说明', 'ramp': '升温', 'hold': '反应', 'cool': '降温'}           # 新加的步只给这几种；加料步要连投料表，在投料表里加


def _knob_text(value):
    """工序里的值 → 编辑框里的字：{knob: 反应内温} → [反应内温]，数字原样。"""
    return f'[{value["knob"]}]' if isinstance(value, dict) and value.get('knob') else ('' if value is None else str(value))


def _knob_value(text):
    """编辑框里的字 → 工序里的值：[反应内温] → {knob: 反应内温}，数字变数字，空的不改。"""
    text = str(text or '').strip()
    if not text:
        return None
    found = re.fullmatch(r'\[(.+)\]', text)
    if found:
        return {'knob': found[1]}
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        return text


def _step_fields(design):
    """每一步能改的：标题、说明、阶段、温度、时间（按 id，和工序一一对上）。"""
    stage = {'prep': '准备', 'reaction': '反应', 'workup': '后处理'}
    compiled = {step['id']: step for step in design.get('steps') or []}
    out = []
    for step in (design.get('procedure') or {}).get('steps') or []:
        shown = compiled.get(step['id'], {})
        out.append({'id': step['id'], 'type': step.get('type'), 'title': step.get('title') or shown.get('title') or '',
                    'text': step.get('textOverride') or step.get('text') or shown.get('text') or '',
                    'stage': stage.get(step.get('legacyStage') or shown.get('stage'), '反应'),
                    'temp': _knob_text(step.get('tempC')) if step.get('type') in ('ramp', 'hold', 'cool', 'dry') else None,
                    'hours': _knob_text((step.get('duration') or {}).get('hours')) if step.get('type') in ('ramp', 'hold', 'cool', 'dry', 'text') else None})
    return out


def resin_design_steps(library, ref, items):
    """改步骤：标题、说明、阶段、温度、时间；排顺序、删、加（说明 / 升温 / 保温 / 降温）。
    工序（设计.procedure.steps，过程图用）和编好的步骤（设计.steps，SOP 和批记录用）按 id 一起改；别的内容（加料、模板参数、记录）原样留着。
    不重新编译：有 4 个设计（R100-36/37/38/42）的步骤带着旧模板参数，重编会丢。"""
    lib = store.open(library)
    record = lib.get(ref, kind='树脂实验')
    design = record['data']['设计']
    procedure = design['procedure']
    old_p = {step['id']: step for step in procedure['steps']}
    old_c = {step['id']: step for step in design.get('steps') or []}
    new_p, new_c = [], []
    for item in items:
        sid = item.get('id')
        if sid not in old_p:
            kind = item.get('type') if item.get('type') in STEP_KIND else 'text'
            sid = 'p-' + uuid.uuid4().hex[:8]
            old_p[sid] = {'id': sid, 'type': kind, 'title': '', 'duration': {'kind': 'fixed', 'hours': 0}, 'rows': [], 'legacyStage': 'reaction',
                          **({'tempC': 25} if kind in ('ramp', 'hold', 'cool') else {})}
            old_c[sid] = {'id': sid, 'module': 'custom', 'category': STEP_KIND[kind], 'title': '', 'stage': 'reaction', 'args': {}, 'text': '',
                          'charges': [], 'conditions': {'kind': kind} if kind in ('ramp', 'hold', 'cool') else {}, 'controls': [], 'records': [], 'notes': []}
        p, c = old_p[sid], old_c.setdefault(sid, {'id': sid, 'module': 'custom', 'category': '', 'args': {}, 'charges': [], 'conditions': {}, 'controls': [], 'records': [], 'notes': []})
        if 'title' in item:
            p['title'] = c['title'] = item['title']
        if 'text' in item:                                 # 人改的说明：工序里记成人写的，SOP 里照这段话出
            p['textOverride'], p['textOverrideOrigin'] = item['text'], 'user'
            c['text'] = c['sourceText'] = item['text']
        if item.get('stage') in STAGE_KEY:
            p['legacyStage'] = c['stage'] = STAGE_KEY[item['stage']]
        conditions = dict(c.get('conditions') or {})
        if 'temp' in item and _knob_value(item['temp']) is not None:
            p['tempC'] = _knob_value(item['temp'])
            conditions['tempC'] = item['temp'].strip()
        if 'hours' in item and _knob_value(item['hours']) is not None:
            p['duration'] = {**(p.get('duration') or {'kind': 'fixed'}), 'hours': _knob_value(item['hours'])}
            conditions['hours'] = item['hours'].strip()
        c['conditions'] = conditions
        new_p.append(p)
        new_c.append(c)
    procedure['steps'], design['steps'] = new_p, new_c
    lib.save('树脂实验', [{'id': record['id'], '设计': design}], by='人')
    return resin_design(library, record['id'])


def resin_next_code(library, ref):
    """派生时建议的编号：同一前缀里最大的号 + 1（R100-41 → 库里最大 R100-45 就给 R100-46）。"""
    lib = store.open(library)
    code = lib.get(ref, kind='树脂实验')['name']
    found = re.match(r'^(.*?)(\d+)$', code)
    if not found:
        return code + '-2'
    prefix = found[1]
    numbers = [int(m[1]) for row in lib.rows('树脂实验') for m in [re.match(re.escape(prefix) + r'(\d+)$', row['name'])] if m]
    return f'{prefix}{max(numbers + [int(found[2])]) + 1}'


def resin_derive(library, ref, code, title=None):
    """从这个设计派生下一个实验：投料、变量、步骤、取样计划都抄过来；实测、执行、结论清空；记父编号和"派生自"。原设计不动。"""
    import copy
    lib = store.open(library)
    record = lib.get(ref, kind='树脂实验')
    code = str(code).strip()
    if any(row['name'] == code for row in lib.rows('树脂实验')):
        raise store.StoreError(f'已经有 {code} 了（不覆盖），换一个编号。')
    data = record['data']
    design = copy.deepcopy(data['设计'])
    parent = design.get('code') or record['name']
    uid = str(uuid.uuid4())
    design.update({'code': code, 'parentCode': parent, 'title': title or design.get('title') or '', 'status': '设计中', 'uid': uid,
                   'revisionUid': str(uuid.uuid4()), 'revisionCode': f'{code}@r1', 'revision': 1, 'aliases': [code], 'revisionAliases': [],
                   'batch': None, 'executionUid': None, 'actualRevisionUid': None, 'actualFlags': None, 'importedRows': None, 'events': [],
                   'attachments': [], 'conclusion': {'verdict': '', 'text': '', 'date': ''}})
    for nodes in ((design.get('samplingPlan') or {}).get('nodes') or [], (design.get('procedure') or {}).get('legacyNodes') or []):
        for node in nodes:
            if node.get('sampleId'):
                node['sampleId'] = str(node['sampleId']).replace(parent, code, 1)
            node.pop('uid', None)
            node.pop('legacySampleId', None)
    done = lib.save('树脂实验', [{'name': code, '编号': code, '标题': design['title'], '父编号': parent, '状态': '设计中', '列表状态': '设计中',
                                 '问题': data.get('问题') or '', '目的': '', '假设': '', '结论': '', '设计uid': uid, '设计': design,
                                 'links': [{'relation': '派生自', 'to': record['id'], 'by': '人'}]}], source=f'从 {parent} 派生', by='人')
    return {'id': done[0]['id'], 'code': code}


def _plain_number(expr):
    try:
        float(str(expr).strip())
        return True
    except ValueError:
        return False


def resin_design_set(library, ref, charges=None, variables=None):
    """改投料方式和数量、改变量的值，写回设计，返回重算的投料表。"""
    lib = store.open(library)
    record = lib.get(ref, kind='树脂实验')
    design = record['data']['设计']
    for item in design.get('charges') or []:
        change = (charges or {}).get(item['id'])
        if change:
            item['amount'] = {**(item.get('amount') or {}), **change}
    for item in design.get('vars') or []:
        if item['key'] in (variables or {}):
            item['expr'] = str((variables or {})[item['key']])
    lib.save('树脂实验', [{'id': record['id'], '设计': design}], by='人')
    return resin_design(library, record['id'])


# ---------- 配方：组成、试算、备料 ----------

def formula_view(library, ref):
    from .sci import formulation as fm
    lib = store.open(library)
    record = lib.get(ref, kind='配方')
    reg = fm.registry(lib)
    sources = [{'id': key, 'name': item.get('name') or key, 'type': kind_, 'solid': item.get('solid_fraction')}
               for kind_, group in (('material', reg.get('materials') or {}), ('premix', reg.get('premixes') or {}))
               for key, item in (group.items() if isinstance(group, dict) else [(g.get('id'), g) for g in group])]
    return clean({'id': record['id'], 'name': record['name'], 'fields': {key: record['data'].get(key) for key in ('总量', '固含', '树脂', '添加剂', '溶剂', '设计起算', '起算质量')},
                  'sources': sources, **formula_calc(library, record['data'])})


def formula_calc(library, fields):
    """按给的组成现算：每行称量、三项平衡、错误和提醒，以及剂量报告（四种口径）。"""
    from .sci import formulation as fm
    lib = store.open(library)
    formula = fm.formula_of({'data': fields})
    result = fm.calculate_formula(formula=formula, lib=lib)
    try:
        dose = fm.dose_report(formula=formula, lib=lib)
    except Exception as error:
        dose = {'problem': str(error)}
    return clean({'calc': result, 'dose': dose})


def formula_save(library, ref, fields):
    lib = store.open(library)
    record = lib.get(ref, kind='配方')
    lib.save('配方', [{'id': record['id'], **fields, '修订': (record['data'].get('修订') or 0) + 1}], by='人')
    return formula_calc(library, {**record['data'], **fields})


def premix_plan(library, orders):
    from .sci import formulation as fm
    return clean(fm.premix_plan(orders, lib=store.open(library)))


# ---------- 椭偏：测试日、剔点、组、参比 ----------

_ELL = {}


def ell_store(lib):
    """椭偏算法的数据（全部膜和测试日）。几天的页面共用一份，算过的统计留着；膜或椭偏设置一改就重新装。"""
    from .sci import ellipsometry as ell
    stamp = (lib.sql("SELECT max(updated_at), count(*) FROM records WHERE kind IN ('膜','椭偏测试日')")[1][0],
             lib.sql("SELECT max(updated_at) FROM settings WHERE key LIKE '椭偏.%'")[1][0])
    if _ELL.get(lib.name, (None,))[0] != stamp:
        _ELL[lib.name] = (stamp, ell.load(lib), {})
    return _ELL[lib.name][1]


def _ell_memo(lib, key, compute):
    """算过的组统计、配对、参比判定、质量提示、块统计跟着那份数据留着（几天的页面共用）；数据一换就跟着清空。"""
    import copy
    memo = _ELL[lib.name][2]
    if key not in memo:
        memo[key] = compute()
    return copy.deepcopy(memo[key])


def ell_day(library, day):
    from .sci import ellipsometry as ell
    lib = store.open(library)
    films = [row for row in lib.rows('膜') if row.get('测试日') == day]
    st = ell_store(lib)
    memo = lambda key, compute: _ell_memo(lib, key, compute)
    out = []
    for film in films:
        record = lib.get(film['id'], with_series=True)
        reads = record['series'].get('读数') or {'columns': [], 'rows': []}
        cols = reads['columns']
        keep = [i for i, name in enumerate(cols) if name not in ('其它',)]
        out.append({'id': film['id'], 'name': film['name'], 'code': film.get('样品编号'), 'formula': film.get('配方编号'),
                    'conditions': {k: film.get(k) for k in ('转速', '烘烤温度', '烘烤时间', '处理', '处理时间', '后烘温度', '后烘时间')},
                    'condition_text': __import__('lab.present', fromlist=['conditions_text']).conditions_text({k: film.get(k) for k in ('转速', '烘烤温度', '烘烤时间', '处理', '处理时间', '后烘温度', '后烘时间')}),
                    'stats': memo(('块', film['id']), lambda: ell.film(lib, film['id'], store=st)), 'columns': [cols[i] for i in keep],
                    'rows': [[row[i] for i in keep] for row in reads['rows']], 'excluded': film.get('剔除') or []})
    groups = [group for group in memo('组', lambda: ell.groups(lib, store=st)['组']) if group.get('测试日') == day]
    pairs = {item['组key']: item for item in memo('配对', lambda: ell.pairing(lib, store=st)['配对']) if item.get('测试日') == day}
    chosen = lib.setting('椭偏.人选参比') or {}
    for group in groups:
        pair = pairs.get(group['组key']) or {}
        group['参比推荐'] = pair
        ref_key = chosen.get(group['组key']) or pair.get('参比key')
        group['参比key'] = ref_key
        group['判定'] = memo(('判定', group['组key'], ref_key), lambda: ell.compare(lib, group['组key'], reference=ref_key, store=st)) if ref_key else None
    quality = memo('质量', lambda: ell.quality(lib, store=st)['提示'])
    names = {film['code'] for film in out}
    return clean({'day': day, 'films': out, 'groups': groups, 'quality': [q for q in quality if q['样品编号'] in names]})


def ell_days(library):
    lib = store.open(library)
    days = {}
    for row in lib.rows('膜'):
        days.setdefault(row.get('测试日') or '没日期', []).append(row['name'])
    return [{'day': day, 'films': len(names)} for day, names in sorted(days.items(), reverse=True)]


def ell_exclude(library, film, items):
    """一块膜的剔除清单整体换掉：items = [{读数id, 范围: 整点|膜厚|几列, 列, 原因}]。"""
    lib = store.open(library)
    stamp = kernel.now()
    lib.save('膜', [{'id': film, '剔除': [{**item, '时间': item.get('时间') or stamp} for item in items]}], by='人')
    from .sci import ellipsometry as ell
    return clean(ell.film(lib, film))


def ell_reference(library, group_key, reference_key):
    lib = store.open(library)
    chosen = lib.setting('椭偏.人选参比') or {}
    if reference_key:
        chosen[group_key] = reference_key
    else:
        chosen.pop(group_key, None)
    lib.set_setting('椭偏.人选参比', chosen, '人在界面上给样品组选的参比组：组key → 参比key')
    from .sci import ellipsometry as ell
    return clean(ell.compare(lib, group_key, reference=reference_key) if reference_key else {})


# ---------- n/k：组叠加、纳入勾 ----------

def nk_groups(library):
    lib = store.open(library)
    groups = {}
    for row in lib.rows('NK谱'):
        groups.setdefault(row.get('分组') or row['name'], []).append(row)
    return [{'group': name, 'round': rows[0].get('轮次'), 'count': len(rows), 'included': sum(1 for row in rows if row.get('纳入重复性', True))}
            for name, rows in sorted(groups.items())]


def nk_group_view(library, name):
    from .sci import nk
    lib = store.open(library)
    rows = [row for row in lib.rows('NK谱') if (row.get('分组') or row['name']) == name]
    members = []
    for row in rows:
        table = lib.get(row['id'], with_series=True)['series'].get('nk') or {'rows': []}
        w, n, k = (list(col) for col in zip(*table['rows'])) if table['rows'] else ([], [], [])
        members.append({'id': row['id'], 'name': row['name'], 'included': row.get('纳入', True), 'repeat': row.get('纳入重复性', True),
                        'w': w[::2], 'n': n[::2], 'k': k[::2]})
    out = {'group': name, 'members': members}
    session = nk.Session(lib, records=rows)              # 只算这一组，一次
    try:
        out['summary'] = nk.group(lib, name, session=session)
        curve = session.groups[name]
        step = 2
        out['band'] = {'w': [float(x) for x in session.grid][::step],
                       **{key: [float(x) if x == x else None for x in curve[key]][::step] for key in ('n', 'k', 'n_mean', 'k_mean', 'n_std', 'k_std') if key in curve}}
    except Exception as error:
        out['problem'] = str(error)
    out['anomalies'] = nk.anomalies(lib, name, session=session)['候选']
    return clean(out)


PAIR_SUFFIX = re.compile(r'^(.*?)-?(SolvA|浸泡后|处理后)$')


def nk_pairs(library):
    """处理前后配对：分组名去掉"SolvA"（或"-SolvA"）就是处理前那一组，两组都在才算一对。"""
    names = [item['group'] for item in nk_groups(library)]
    out = []
    for name in names:
        found = PAIR_SUFFIX.match(name)
        if found and found.group(1) in names:
            out.append({'before': found.group(1), 'after': name})
    return out


def _nk_curve(lib, name):
    """一组的均值和标准差（1 nm 网格）和纳入的条数。"""
    from .sci import nk
    rows = [row for row in lib.rows('NK谱') if (row.get('分组') or row['name']) == name]
    session = nk.Session(lib, records=rows)
    nk.group(lib, name, session=session)
    curve = session.groups[name]
    count = sum(1 for row in rows if row.get('纳入', True) and row.get('纳入重复性', True))
    return np.asarray(session.grid, dtype=float), {key: np.asarray(curve[key], dtype=float) for key in ('n_mean', 'k_mean', 'n_std', 'k_std')}, max(count, 1)


def nk_pair_view(library, before, after):
    """处理前后配对：两组均值 ± σ、Δn、Δk（后减前）随波长，和 Δ 的标准误（√(σ前²/N前 + σ后²/N后)）；|Δ| 超过 2 倍标准误算"明显变了"。"""
    lib = store.open(library)
    grid_b, b, count_b = _nk_curve(lib, before)
    grid_a, a, count_a = _nk_curve(lib, after)
    lo, hi = max(grid_b.min(), grid_a.min()), min(grid_b.max(), grid_a.max())
    grid = grid_b[(grid_b >= lo) & (grid_b <= hi)]
    at = lambda g, values: np.interp(grid, g, values, left=np.nan, right=np.nan)
    out = {'before': before, 'after': after, 'counts': [count_b, count_a], 'w': grid[::2].tolist()}
    for key in ('n', 'k'):
        mb, ma = at(grid_b, b[key + '_mean']), at(grid_a, a[key + '_mean'])
        sb, sa = at(grid_b, b[key + '_std']), at(grid_a, a[key + '_std'])
        se = np.sqrt(np.nan_to_num(sb) ** 2 / count_b + np.nan_to_num(sa) ** 2 / count_a)
        out[key] = {'before': mb[::2].tolist(), 'after': ma[::2].tolist(), 'before_sd': sb[::2].tolist(), 'after_sd': sa[::2].tolist(),
                    'delta': (ma - mb)[::2].tolist(), 'se': se[::2].tolist()}
    rows = []
    for wl in (193.0, 248.0, 365.0, 633.0):
        if not (lo <= wl <= hi):
            continue
        row = {'w': wl}
        for key in ('n', 'k'):
            mb, ma = float(np.interp(wl, grid_b, b[key + '_mean'])), float(np.interp(wl, grid_a, a[key + '_mean']))
            sb, sa = float(np.interp(wl, grid_b, b[key + '_std'])), float(np.interp(wl, grid_a, a[key + '_std']))
            se = math.sqrt((sb if sb == sb else 0) ** 2 / count_b + (sa if sa == sa else 0) ** 2 / count_a)
            row[key] = {'before': mb, 'after': ma, 'delta': ma - mb, 'se': se, 'clear': abs(ma - mb) > 2 * se if se else None}
        rows.append(row)
    out['table'] = rows
    return clean(out)


def nk_toggle(library, record_id, field, value):
    lib = store.open(library)
    lib.save('NK谱', [{'id': record_id, field: bool(value)}], by='人')
    return {'ok': True}


# ---------- 老化：系列、动力学、导入 ----------

def aging_samples(library):
    lib = store.open(library)
    out = {}
    for row in lib.rows('老化谱'):
        item = out.setdefault(row.get('样品') or '没样品', {'sample': row.get('样品') or '没样品', 'kind': row.get('种类'), 'count': 0, 'conditions': set(), 'last': ''})
        item['count'] += 1
        item['conditions'].add(row.get('条件') or '起点')
        item['last'] = max(item['last'], row.get('测试日') or '')
    return [{**item, 'conditions': sorted(item['conditions'])} for item in sorted(out.values(), key=lambda item: item['last'], reverse=True)]


def aging_view(library, sample):
    from .sci import aging
    lib = store.open(library)
    series = aging.sample_series(sample, lib)
    try:
        kin = aging.kinetics(sample, lib=lib)
    except Exception as error:
        kin = {'problem': str(error)}
    spectra = []
    for row in lib.rows('老化谱'):
        if row.get('样品') == sample:
            table = lib.get(row['id'], with_series=True)['series'].get('谱') or {'rows': []}
            spectra.append({'id': row['id'], 'name': row['name'], 'condition': row.get('条件') or '起点', 'day': row.get('天数'),
                            'start': row.get('是否起点'), 'x': [r[0] for r in table['rows']], 'y': [r[1] for r in table['rows']]})
    return clean({'sample': sample, 'series': series, 'kinetics': kin, 'spectra': sorted(spectra, key=lambda s: (s['condition'], s['day'] or 0)),
                  'settings': aging.settings_of(lib)})


def aging_preview(library, files):
    """导入前预览：每个文件按文件名认出的样品、条件、天数，人改对再导入。"""
    from .sci import aging
    lib = store.open(library)
    rules = lib.setting('老化.文件名规则') or aging.DEFAULT_RULES
    out = []
    for path in files:
        n = aging.parse_name(Path(path).name, rules)
        spec = aging.parse_spectrum(Path(path).read_bytes().decode('utf-8', errors='replace'))
        out.append({'file': Path(path).name, 'path': str(path), 'problem': spec['problem'], '样品': n['sample'], '种类': n['kind'], '条件': n['condition'],
                    '天数': n['day'], '时间标签': n['timeLabel'], '说明': '；'.join(n['notes'])})
    return clean(out)


def aging_commit(library, items):
    from .sci import aging
    lib = store.open(library)
    paths = [item['path'] for item in items]
    overrides = {Path(item['path']).name: {key: item[key] for key in ('样品', '种类', '条件', '天数', '时间标签') if key in item} for item in items}
    out = aging.import_spectra(paths, lib, overrides)
    aging.compute_all(lib, report=lambda *_: None)
    return clean(out)


# ---------- 约定口径（助手每次都读） ----------

KEY = '约定口径'


def conventions(library):
    return store.open(library).setting(KEY) or []


def save_conventions(library, items):
    lib = store.open(library)
    lib.set_setting(KEY, items, '人定下的口径和规矩，助手每次干活都先读；改了马上生效')
    return items


# ---------- GPC 叠加：几个样品的色谱放在一起看 ----------

def gpc_overlay(library, ids, points=4000):
    """每个样品：保留时间、原始信号、基线（存着的锚点算的）、积分区间、分子量分布（M 和 dw/dlogM）、Mw、PDI。
    点太多的抽到 points 个左右（保留首尾），只为画图。"""
    lib = store.open(library)
    out = []
    for ref in ids:
        view = gpc_view(library, ref)
        record = lib.get(ref, with_series=True)
        dist = (record['results'].get('GPC分布') or {}).get('value') or {}
        t, s = view['trace']['t'], view['trace']['s']
        b = (view.get('baseline') or {}).get('b') or [0.0] * len(t)       # 还没分析过的样品：没有基线，只能看原始信号
        step = max(1, len(t) // points)
        keep = list(range(0, len(t), step)) + ([len(t) - 1] if (len(t) - 1) % step else [])
        region = (view.get('regions') or [{}])[0]
        out.append({'id': record['id'], 'name': record['name'], 'hours': _hours_of(record['name']),
                    't': [t[i] for i in keep], 's': [s[i] for i in keep], 'b': [b[i] for i in keep],
                    'region': [region.get('起'), region.get('止')], 'M': dist.get('M') or [], 'w': dist.get('dw/dlogM') or [],
                    'Mw': (view.get('result') or {}).get('Mw'), 'PDI': (view.get('result') or {}).get('PDI'), 'analysed': bool(view.get('result')),
                    'result': {key: (view.get('result') or {}).get(key) for key in ('Mn', 'Mw', 'Mz', 'PDI', 'Mp', 'D10', 'D50', 'D90', '外推面积占比')},
                    'project': record['data'].get('所属项目')})
    return clean({'samples': out, 'preference': (lib.setting('作图偏好') or {}).get('GPC叠加') or {}})


def _hours_of(name):
    found = re.search(r'(\d+(?:\.\d+)?)\s*[hH](?![a-zA-Z])', name or '')
    return float(found[1]) if found else None


def save_chart_preference(library, chart, value):
    """作图偏好（例：GPC 叠加横轴用保留时间、纵轴归一、只看积分区间）。页面和助手作这类图都按它来。"""
    lib = store.open(library)
    prefs = lib.setting('作图偏好') or {}
    prefs[chart] = value
    lib.set_setting('作图偏好', prefs, '他定的作图习惯：每类图默认怎么画。页面上"设为默认"或在对话里说一句都会改这里，助手作图前会读。')
    return prefs
