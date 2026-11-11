"""椭偏膜厚：从研发库取数 → 调原样复制的椭偏核心（lab.sci.vendor.ellipsometer）→ 结果存 results。

库里的东西（类型 `膜`、`椭偏测试日`，见 skills/椭偏/SKILL.md）：
  膜           一片膜（旧程序的"样品块"）。字段：样品编号、原名、测试日、配方编号、对应状态、8 项工艺条件、条件来源、剔除……
               读数挂在曲线/表 `读数` 上：每行一次读数（处理前/处理后各约 6 次 + 原表平均行）。
  椭偏测试日   一天测的一批膜（旧程序的"项目"），膜 →测于→ 测试日。
  设置         椭偏.容差（12 项）、椭偏.默认条件、椭偏.配方别名规则、椭偏.参比选择、椭偏.其它规则。

给智能体用的函数（输入记录 id 或名字，输出 JSON 能表示的字典，数都带单位）：
  film(lib, ref)                         一片膜的块统计：处理前/后膜厚均值、SD、CV、点数、损失、损失率、保留率、溶解；
                                         n/k/MSE 三波长处理前后均值；建议离群点。输出 {样品编号, 处理前: {均值 nm, SD nm, CV %, n}, …}
  groups(lib, test_date=None, group_by=None, formula=None)
                                         组统计（默认分组：配方+转速+烘温+烘时+处理+测试日+变体）：组均值=各块均值再平均，
                                         组 SD/CV=块与块之间，pooled SD 用全部单点；组损失率=各块损失率平均。返回 [{组, 组key, …}]
  pairing(lib, test_date=None)           每个样品组推荐的参比组（规则 A：同系列标准参比、测试日不晚于样品；条件不同项少优先，再比日期近）
  compare(lib, candidate, reference=None, test_date=None)
                                         样品组对参比组：差值、相对差、百分点差，12 项容差逐项判定（better/within/worse/not_evaluable）
                                         和整行判定。candidate/reference 可以是组key、膜 id/名字、或"配方编号@测试日"。
  quality(lib, ref=None)                 质量提示（抄错列、MSE 高、n/k/膜厚偏离、读数不是 6 个、缺阶段、平均行不一致、溶解、文本数字、重复块冲突）
  compute_all(lib, report=print)         全部膜算块统计和质量提示、每个测试日算组统计和参比判定，存进 results。

结果（set_result 的方法名）：膜 → `椭偏.块统计`、`椭偏.质量提示`；椭偏测试日 → `椭偏.组统计`、`椭偏.参比判定`。
"""
from __future__ import annotations

import json
import math

from .vendor.ellipsometer.ellipsometer2 import memstore
from .vendor.ellipsometer.ellipsometer2.memstore import CONDITIONS, ListStore, api_stage, measurement_load
from .vendor.ellipsometer.src.parser import NUMERIC_FIELDS

VERSION = 'ellipsometer-2.0.0-dev@ddbc78a4'
KIND, DAY = '膜', '椭偏测试日'

# 工艺条件：旧字段名 → 新字段 key 和单位
CONDITION_FIELDS = {'spin_rpm': ('转速', 'rpm'), 'bake_temp_c': ('烘烤温度', '℃'), 'bake_time_s': ('烘烤时间', 's'),
                    'treatment': ('处理', ''), 'treatment_time': ('处理时间', ''), 'post_bake_temp_c': ('后烘温度', '℃'),
                    'post_bake_time_s': ('后烘时间', 's'), 'variant': ('变体', '')}
SOURCE_LABELS = {'manual': '手改', 'name': '样品名', 'footnote': '表脚注', 'library_default': '默认（用户确认）',
                 'sheet': '表页名', 'filename': '文件名'}
LINK_STATUS = {'confirmed': '已确认', 'reference': '参比', 'auto': '同编号', 'rule': '别名规则', 'pending': '待对应'}
# 读数表：原解析行的字段 → 列名（其余字段放在"其它"列里，一个不丢）
READING_COLUMNS = [('measurement_id', '读数id'), ('source_row', '行'), ('treatment_stage', '阶段'), ('replicate_number', '次数'),
                   ('thickness', '膜厚 / nm'), ('n_193', 'n 193'), ('k_193', 'k 193'), ('n_248', 'n 248'), ('k_248', 'k 248'),
                   ('n_633', 'n 633'), ('k_633', 'k 633'), ('mse', 'MSE'), ('is_dissolved', '溶解'), ('is_source_average_row', '原表平均行')]
TOLERANCE_LABELS = {'thickness_before_relative_percent': '处理前膜厚相对差 %', 'thickness_after_relative_percent': '处理后膜厚相对差 %',
                    'thickness_loss_percent_pp': '损失率差 百分点', 'retention_percent_pp': '保留率差 百分点',
                    'n_193_absolute': 'n 193 绝对差', 'k_193_absolute': 'k 193 绝对差', 'n_248_absolute': 'n 248 绝对差',
                    'k_248_absolute': 'k 248 绝对差', 'n_633_absolute': 'n 633 绝对差', 'k_633_absolute': 'k 633 绝对差',
                    'mse_maximum': 'MSE 上限', 'thickness_cv_maximum': '膜厚 CV 上限 %'}


# ---------- 新库记录 ⇄ 原程序的样品 / 读数 ----------

def reading_table(rows):
    """读数行（原解析行 + measurement_id）→ 曲线/表 `读数`。"""
    keys = [key for key, _ in READING_COLUMNS]
    table = []
    for row in rows:
        record = dict(row['payload'], measurement_id=row['measurement_id'])
        table.append([record.get(key) for key in keys] + [{k: v for k, v in record.items() if k not in keys}])
    return [label for _, label in READING_COLUMNS] + ['其它'], table


def film_fields(sample, link, sheet, book):
    """原样品 → 膜的字段（工艺条件存的是读表时得到的值；默认条件在计算时按设置补上）。"""
    fields = {'样品编号': sample['sample_code'], '原名': sample['raw_name'], '测试日': sample['test_date'],
              '系列': sample['series_id'], '工作簿': book['file_name'], '页名': sheet['name'], '表脚注': sample.get('footnote'),
              '样品说明': sample.get('note') or None, '处理待确认': bool(sample.get('treatment_pending')),
              '配方编号': link.get('formulation_code'), '对应状态': LINK_STATUS.get(link.get('status'), link.get('status')),
              '对应说明': link.get('reason'), '旧id': sample['sample_id'], '剔除': []}
    for old, (key, _) in CONDITION_FIELDS.items():
        fields[key] = sample.get(old)
    fields['条件来源'] = {CONDITION_FIELDS[k][0] if k in CONDITION_FIELDS else '测试日': SOURCE_LABELS.get(v, v)
                       for k, v in (sample.get('field_sources') or {}).items() if v}
    return fields


def _back(mapping, value):
    return next((k for k, v in mapping.items() if v == value), value)


def _engine_inputs(lib, records=None):
    records = records if records is not None else lib.rows(KIND)
    samples, measurements, books, sheets, links, exclusions = [], [], {}, {}, {}, {}
    for film in sorted(records, key=lambda r: (r.get('导入顺序') or 0, r['name'])):
        columns, rows, meta = lib.table(film['id'], '读数')
        index = {label: i for i, label in enumerate(columns)}
        sid = film.get('旧id') or film['id']
        sources = {}
        for label, source in (film.get('条件来源') or {}).items():
            old = 'test_date' if label == '测试日' else _back({k: v[0] for k, v in CONDITION_FIELDS.items()}, label)
            sources[old] = _back(SOURCE_LABELS, source)
        sample = {'sample_id': sid, 'sample_code': film['样品编号'], 'raw_name': film.get('原名'), 'series_id': film.get('系列') or 'unassigned',
                  'test_date': film.get('测试日'), 'note': film.get('样品说明') or '', 'footnote': film.get('表脚注'),
                  'treatment_pending': int(bool(film.get('处理待确认'))), 'project_id': film.get('测试日'),
                  'field_sources': {k: sources.get(k) for k in [*CONDITIONS, 'test_date']}}
        for old, (key, _) in CONDITION_FIELDS.items():
            # 默认条件来源的值清空，让程序按当前的"椭偏.默认条件"重新补（和原程序一样在读的时候补）。
            sample[old] = None if sources.get(old) == 'library_default' else film.get(key)
            if sources.get(old) == 'library_default':
                sample['field_sources'][old] = None
        payloads = []
        for row in rows:
            payload = dict(row[index['其它']])
            for key, label in READING_COLUMNS:
                payload[key] = row[index[label]]
            mid = payload.pop('measurement_id')
            payloads.append(payload)
            measurements.append({'measurement_id': mid, 'sample_id': sid, 'workbook_id': meta['workbook_id'], 'sheet_id': meta['sheet_id'],
                                 'source_row': payload['source_row'], 'is_average': int(bool(payload['is_source_average_row'])),
                                 'payload': payload, 'active': 1})
        sample['payload'] = memstore.dumps(payloads[0]) if payloads else '{}'
        samples.append(sample)
        books.setdefault(meta['workbook_id'], {'workbook_id': meta['workbook_id'], 'file_name': meta['file_name'], 'sha256': meta['sha256'],
                                               'original_source': 'path', 'status': 'active',
                                               'path': str(lib.file_path(meta['file_id'])) if meta.get('file_id') else None})
        sheets[meta['sheet_id']] = {'name': meta['sheet'], 'field_mapping': meta.get('field_mapping') or {}}
        status = _back(LINK_STATUS, film.get('对应状态'))
        links[sid] = {'status': 'auto' if status == 'rule' else status, 'formulation_key': film.get('配方编号'),
                      'formulation_code': film.get('配方编号'), 'product_name': None, 'source': None, 'reason': film.get('对应说明')}
        for item in film.get('剔除') or []:
            scope = {'整点': 'all', '膜厚': 'thickness', '几列': 'fields'}.get(item.get('范围'), item.get('范围'))
            fields = [_back({k: v for k, v in READING_COLUMNS}, f) for f in item.get('列') or []]
            exclusions[item['读数id']] = {'scope': scope, 'fields': fields, 'reason': item.get('原因') or '', 'note': item.get('说明') or '',
                                         'at': item.get('时间'), 'exclusion_id': item['读数id']}
    return dict(samples=samples, measurements=measurements, books=list(books.values()), sheets=sheets, links=links, exclusions=exclusions)


def library_values(lib):
    """库设置 → 原程序的 r2_values。"""
    values = dict(lib.setting('椭偏.其它规则') or {})
    values['tolerances'] = lib.setting('椭偏.容差') or values.get('tolerances')
    values['default_conditions'] = lib.setting('椭偏.默认条件') or values.get('default_conditions')
    return {k: v for k, v in values.items() if v is not None}


def load(lib, records=None):
    """整个库（或给定的几片膜）→ 原程序的 ListStore。"""
    inputs = _engine_inputs(lib, records)
    choices = lib.setting('椭偏.参比选择') or {}
    return ListStore(**inputs, values=library_values(lib), dismissals=lib.setting('椭偏.忽略的提示') or [],
                     reference_choices={sig: {k: tuple(v) for k, v in items.items()} for sig, items in choices.items()})


# ---------- 给智能体的计算 ----------

def _num(value):
    return None if value is None or (isinstance(value, float) and not math.isfinite(value)) else value


def _film_record(lib, ref):
    record = lib.get(ref, KIND)
    return {'id': record['id'], 'name': record['name'], **record['data']}


def _block(store, sid):
    _, analysis = store.analysis()
    comps = [r for r in memstore.clean(analysis.thickness_comparison.to_dict('records')) if r['sample_instance_id'] == sid]
    optics = [r for r in memstore.clean(analysis.optical_changes.to_dict('records')) if r['sample_instance_id'] == sid]
    out = []
    for row in comps:
        stage = row.get('after_stage')
        item = {'处理后阶段': api_stage(stage)[1] if stage else None, '数据完整性': row['data_completeness_status'],
                '处理前': {'均值 nm': _num(row['before_thickness_mean']), 'SD nm': _num(row['before_thickness_std']),
                         'CV %': _num(row['before_thickness_cv']), 'n': row['before_thickness_count']},
                '处理后': {'均值 nm': _num(row['after_thickness_mean']), 'SD nm': _num(row['after_thickness_std']),
                         'CV %': _num(row['after_thickness_cv']), 'n': row['after_thickness_count']},
                '损失 nm': _num(row['thickness_loss']), '损失率 %': _num(row['loss_percent']), '保留率 %': _num(row['retention_percent']),
                '溶解': bool(row['after_dissolved']), '光学': {}}
        for o in optics:
            if o.get('after_stage') == stage:
                item['光学'][o['parameter']] = {'处理前': _num(o['before_mean']), '处理后': _num(o['after_mean']), '变化': _num(o['absolute_change'])}
        out.append(item)
    quality, _ = store.analysis()
    outliers = [r['_measurement_id'] for r in quality.measurements.to_dict('records')
                if r['sample_instance_id'] == sid and bool(r.get('is_thickness_outlier'))]
    return out, outliers


def film(lib, ref, store=None):
    """一片膜的块统计（膜厚 nm、CV %、损失率 %……）。"""
    record = _film_record(lib, ref)
    store = store or load(lib, [record])
    blocks, outliers = _block(store, record.get('旧id') or record['id'])
    return {'膜': record['name'], '样品编号': record['样品编号'], '测试日': record.get('测试日'), '各阶段': blocks,
            '建议离群读数': outliers, '说明': '均值/SD(ddof=1)/CV 只用 6 个单点，原表平均行不参与；损失率=(前−后)/前×100'}


def _group_out(group):
    keep = {'组': group['label'], '组key': group['group_key'], '配方编号': group.get('formulation_code'), '测试日': group.get('test_date'),
            '对应状态': LINK_STATUS.get(group.get('link_status'), group.get('link_status')), '块数': group['n_samples'],
            '条件': {CONDITION_FIELDS[k][0]: v for k, v in group['conditions'].items()},
            '条件来源': {CONDITION_FIELDS[k][0]: SOURCE_LABELS.get(v, v) for k, v in group.get('condition_sources', {}).items()},
            '默认条件说明': group.get('default_note'),
            '处理前': {'均值 nm': group['before_mean'], '块间SD nm': group['before_sd'], '块间CV %': group['before_cv'],
                    'pooled SD nm': group['pooled_before_sd'], '点数': group['n_before']},
            '处理后': {'均值 nm': group['after_mean'], '块间SD nm': group['after_sd'], '块间CV %': group['after_cv'],
                    'pooled SD nm': group['pooled_after_sd'], '点数': group['n_after']},
            '损失 nm': group['loss'], '损失率 %': group['loss_percent'], '损失率(不含溶解) %': group['loss_percent_excl_dissolved'],
            '保留率 %': group['retention_percent'], '溶解块数': group['dissolved_count'],
            '处理后阶段': api_stage(group['after_stage'])[1] if group.get('after_stage') else None,
            '光学': {p: {'处理前': v['before'], '处理后': v['after'], '漂移': v['drift']} for p, v in (group.get('optics') or {}).items()},
            '膜': group['sample_ids']}
    return keep


def groups(lib, test_date=None, group_by=None, formula=None, store=None):
    """组统计。test_date 只看这一天；group_by 用旧字段名列表（formulation, spin_rpm, …, test_date, variant）。"""
    store = store or load(lib)
    summary = store.groups_summary(group_by=group_by or '')
    out = [_group_out(g) for g in summary['groups']
           if (not test_date or g.get('test_date') == test_date) and (not formula or g.get('formulation_code') == formula)]
    return {'组': out, '分组依据': summary['group_by'], '单位': '膜厚 nm；CV、损失率、保留率 %'}


def _find_group(store, ref, lib):
    """组key / 膜 id 或名字 / "配方编号@测试日" → 组key。"""
    from .vendor.ellipsometer.ellipsometer2 import reference
    contexts = reference._contexts(store)
    if any(g['group_key'] == ref for g in contexts) or str(ref).startswith('sample:'):
        return ref
    if '@' in str(ref):
        code, day = str(ref).split('@', 1)
        found = [g for g in contexts if (g.get('formulation_code') or g['label'].split(' · ')[0]) == code and g.get('test_date') == day]
    else:
        record = _film_record(lib, ref)
        sid = record.get('旧id') or record['id']
        found = [g for g in contexts if sid in g.get('sample_ids', [])]
    found = [g for g in found if g.get('after_stage') in (None, 'after_solva_1')] or found
    if not found:
        raise ValueError(f'找不到 {ref} 所在的组。')
    return found[0]['group_key']


def pairing(lib, test_date=None, store=None):
    """每个样品组推荐的参比组（规则 A）。"""
    from .vendor.ellipsometer.ellipsometer2 import reference
    store = store or load(lib)
    result = reference.pairing(store, {'after_stage': 'after_1'})
    out = []
    for pair in result['pairs']:
        if test_date and pair.get('test_date') != test_date:
            continue
        out.append({'组': pair['candidate_label'], '组key': pair['candidate_group_key'], '测试日': pair.get('test_date'),
                    '状态': {'auto': '同天同条件，自动', 'nearest': '最近的更早同条件参比', 'chosen': '人选', 'needs_choice': '要人选'}.get(pair['status'], pair['status']),
                    '参比': (pair.get('reference') or {}).get('label'), '参比key': pair.get('reference_key'),
                    '日期差': pair.get('date_diff_text') or (pair['suggestions'][0]['date_diff_text'] if pair.get('suggestions') else None),
                    '说明': pair.get('note'),
                    '候选': [{'参比': s['label'], '参比key': s['reference_key'], '日期差': s['date_diff_text'],
                            '条件不同': [f"{d['label']}：{d['candidate']} vs {d['reference']}" for d in s['condition_diffs']]}
                           for s in pair.get('suggestions', [])[:5]]})
    return {'配对': out}


def compare(lib, candidate, reference=None, test_date=None, store=None):
    """样品组对参比组的差值和 12 项容差判定。reference 不给就用 pairing 推荐的。"""
    from .vendor.ellipsometer.ellipsometer2 import reference as engine
    store = store or load(lib)
    key = _find_group(store, candidate, lib)
    if reference is None:
        pair = engine.pairing(store, {'after_stage': 'after_1', 'candidate_group_keys': [key]})['pairs'][0]
        ref_key = pair.get('reference_key')
        if not ref_key:
            return {'组key': key, '判定': None, '说明': pair.get('note') or '没有推荐参比，请给 reference'}
    else:
        ref_key = _find_group(store, reference, lib)
    result = engine.compare(store, {'reference_key': ref_key, 'candidate_group_keys': [key], 'after_stage': 'after_1'})
    rows = [r for r in result['rows'] if r['group_key'] == key]
    row = rows[0] if rows else {}
    return {'组': row.get('label'), '参比': result['reference']['label'], '参比key': ref_key,
            '判定': row.get('verdict'), '判定说明': row.get('verdict_reason'),
            '处理前膜厚差 nm': row.get('before_diff'), '处理前相对差 %': row.get('before_rel_percent'),
            '处理后膜厚差 nm': row.get('after_diff'), '处理后相对差 %': row.get('after_rel_percent'),
            '损失率差 百分点': row.get('loss_pp'), '保留率差 百分点': row.get('retention_pp'),
            '各项判定': row.get('verdicts'), '各项理由': row.get('verdict_reasons'), '光学差': row.get('optics'),
            '容差': {TOLERANCE_LABELS.get(k, k): v for k, v in store._values['tolerances'].items()},
            '摘要': result.get('summary'), '提醒': result.get('warnings')}


def quality(lib, ref=None, store=None):
    """质量提示（只提示，不剔除）。ref 给一片膜就只看它。"""
    from .vendor.ellipsometer.ellipsometer2.quality_flags import flags_for
    store = store or load(lib)
    sid = None
    if ref is not None:
        record = _film_record(lib, ref)
        sid = record.get('旧id') or record['id']
    result = flags_for(store, sample_id=sid)
    return {'提示': [{'规则': f['rule'], '级别': {'action': '要处理', 'review': '请复核', 'info': '知道一下'}.get(f['level'], f['level']),
                    '样品编号': f['sample_code'], '阶段': f['stage_label'], '次数': f['replicate'], '读数id': f['measurement_id'],
                    '列': f['fields'], '位置': f"{f['location']['workbook']} / {f['location']['sheet']} / {f['location']['cells'] or ('行 ' + str(f['location']['row']))}",
                    '说明': f['message'], '已剔除': f['excluded'], '已忽略': f['dismissed']} for f in result['flags']],
            '按规则计数': result['counts'], '按级别计数': result.get('levels')}


def compute_all(lib, report=print):
    """全部膜和测试日算一遍，结果存 results。"""
    store = load(lib)
    films = lib.rows(KIND)
    by_old = {f.get('旧id') or f['id']: f for f in films}
    params = {'容差': store._values['tolerances'], '默认条件': store._values['default_conditions'], '分组': store._values['default_group_by']}
    all_flags = quality(lib, store=store)['提示']
    for f in films:
        lib.set_result(f['id'], '椭偏.块统计', film(lib, f['id'], store=store), params, VERSION)
        lib.set_result(f['id'], '椭偏.质量提示', [x for x in all_flags if x['样品编号'] == f['样品编号'] and
                                               (x['读数id'] is None or x['读数id'] in {r[0] for r in lib.table(f['id'], '读数')[1]})], params, VERSION)
    summary = groups(lib, store=store)['组']
    pairs = {p['组key']: p for p in pairing(lib, store=store)['配对']}
    days = lib.rows(DAY)
    verdicts = 0
    for day in days:
        mine = [g for g in summary if g['测试日'] == day['测试日'] and any(sid in by_old for sid in g['膜'])]
        lib.set_result(day['id'], '椭偏.组统计', mine, params, VERSION)
        rows = []
        for g in mine:
            if g['对应状态'] == '参比' or g['处理后阶段'] not in (None, '处理后（SolvA）'):
                continue
            pair = pairs.get(g['组key'])
            if pair and pair['参比key']:
                rows.append({**compare(lib, g['组key'], pair['参比key'], store=store), '配对方式': pair['状态']})
                verdicts += 1
            else:
                rows.append({'组': g['组'], '判定': None, '说明': (pair or {}).get('说明') or '没有推荐参比'})
        lib.set_result(day['id'], '椭偏.参比判定', rows, params, VERSION)
    report(f'椭偏 {lib.name}：{len(films)} 片膜、{len(days)} 个测试日、{len(summary)} 个组、{verdicts} 个参比判定、{len(all_flags)} 条质量提示')
    return {'膜': len(films), '测试日': len(days), '组': len(summary), '参比判定': verdicts, '质量提示': len(all_flags)}
