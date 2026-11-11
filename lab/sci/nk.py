"""NK 光学常数：从研发库取 n/k 谱 → 调原样复制的 NK Studio 内核（lab.sci.vendor.nkstudio）→ 结果存 results。

库里的东西（见 skills/NK/SKILL.md）：
  NK谱    一条 n、k 谱（一个仪器导出 txt）。曲线 `nk`：列 `波长 / nm`、`n`、`k`（清洗后：只留有限数、波长>0、按波长排序、重复波长取平均）。
          字段：谱名、分组、轮次、纳入、纳入重复性、内容指纹、文件名、旧id。原 txt 挂在 files 上。
  NK分组  同一样品的重复谱（文件名去掉 -1…-6 得到），NK谱 →属于→ NK分组。
  NK轮次  一轮试验（T11…T22、参比 REF01），NK分组 →属于→ NK轮次。
  设置    NK.分析参数（目标波长、波段、网格 1 nm、异常筛查参数……原样）、NK.参比、NK.导出方案、NK.对比来源、NK.配对。

给智能体用的函数（输入谱名/组名或记录 id，输出 JSON 能表示的字典）：
  at(lib, ref, wavelength)               某条谱或某组均值在某波长的 n、k（命中原点用原值，否则线性插值，超范围留空），
                                         另给 ε1、ε2、α（cm⁻¹）、R、穿透深度（nm）。组还给 σ(n)、σ(k)（样本 SD，ddof=1）。
  group(lib, name, wavelengths=None)     组均值 ± σ：各目标波长的 n、k、σ，和重复性摘要（平均σ、最大σ及波长、共同点数）。
  anomalies(lib, group=None)             同组异常筛查（MAD 修正 z，阈 3.5、异常点比例 5 %、组≥3、点≥20，加突跳）：只给候选和理由，不排除。
  pairs(lib)                             处理前后配对建议（名字去掉 SolvA 一一对应），已存的配对（NK.配对）照用。
  pair_delta(lib, before, after, wavelengths=None)
                                         配对差值：每个目标波长 Δn = 处理后 − 处理前、Δk。
  compare(lib, ref, reference=None)      和参比比：共同网格上 RMSE、MAE、最大差、相关系数；目标波长 Δn、Δk。参比默认 NK.参比。
  compute_all(lib, report=print)         每条谱算目标波长 n/k，每组算均值±σ，异常筛查和配对差值都存进 results。

结果（set_result 方法名）：NK谱 → `NK.目标波长`、`NK.异常筛查`；NK分组 → `NK.组均值`；配对 → 处理前那条谱上 `NK.配对差值`。
口径：取某波长的 n、k 一律用线性插值（旧智能体说明里"取最近点"的做法不用）。
"""
from __future__ import annotations

import copy
import dataclasses
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .vendor.nkstudio import studio_core as core
from .vendor.nkstudio import anomaly_detection, studio_pairing

VERSION = 'nkstudio-3.0.0@3302d5ae'
SPECTRUM, GROUP, ROUND = 'NK谱', 'NK分组', 'NK轮次'
COLUMNS = ['波长 / nm', 'n', 'k']


def config(lib):
    values = dict(lib.setting('NK.分析参数') or {})
    fields = {f.name for f in dataclasses.fields(core.Config)}
    cfg = core.Config(**{k: v for k, v in values.items() if k in fields})
    cfg.bands = {k: tuple(v) for k, v in cfg.bands.items()}
    cfg.reference_sample = lib.setting('NK.参比') or cfg.reference_sample
    return cfg


def _num(value):
    value = float(value) if value is not None else math.nan
    return value if math.isfinite(value) else None


def sample_from_record(lib, record, cfg):
    """一条 NK谱 → 原内核的 SampleData。有原 txt 就用原读法重读（得到 raw 和质量检查），没有就用曲线。"""
    files = lib.get(record['id'])['files']
    if files:
        sample, _ = core.parse_sample_file(lib.file_path(files[0]['id']), cfg)
    else:
        columns, rows, _ = lib.table(record['id'], 'nk')
        clean = pd.DataFrame(rows, columns=['Wavelength_nm', 'n', 'k'])
        raw = clean.assign(Original_Row=range(2, len(clean) + 2), Data_Status='正常', Import_Note='')
        info = {'unit_status': 'nm', 'encoding': '', 'delimiter': '', 'header_row': 1, 'wavelength_header': '波长 / nm', 'wavelength_unit': 'nm'}
        sample = core.SampleData(record['name'], record.get('文件名') or '', '', '', '', 1, '波长 / nm', 'nm', 'nm', raw, clean,
                                 core.build_quality(raw, clean, info, cfg), metadata={})
    sample.sample_id = record.get('谱名') or record['name']
    sample.file_name = record.get('文件名') or sample.file_name
    sample.metadata.update({'样品名称': sample.sample_id, '分组': record.get('分组') or '', '_db_id': record.get('旧id'),
                            '_content_hash': record.get('内容指纹') or ''})
    sample.included = bool(record.get('纳入', True))
    sample.repeatability_included = bool(record.get('纳入重复性', True))
    return sample


class Session:
    """原 studio_analysis.AnalysisSession 的计算主线（统一网格、插值、重复性、组均值来源、目标波长取值），去掉界面和明细表。"""

    def __init__(self, lib, records=None, cfg=None):
        self.cfg = copy.deepcopy(cfg or config(lib))
        self.records = records if records is not None else lib.rows(SPECTRUM)
        self.samples = [sample_from_record(lib, r, self.cfg) for r in self.records]
        self._sample_by_id = {s.sample_id: s for s in self.samples}
        self.grid = core.generate_common_grid(self.samples, self.cfg)
        self.curves = {s.sample_id: core.interpolate_sample(s, self.grid) for s in self.samples if s.included}
        self.groups, self.repeatability_summary = core.compute_repeatability(self.samples, self.grid, self.curves)
        core.add_group_mean_sources(self.curves, self.groups)
        self.key_cache = {}

    def source(self, name):
        if name in self._sample_by_id or name in self.curves:
            return name
        if core.group_mean_source_id(name) in self.curves:
            return core.group_mean_source_id(name)
        raise ValueError(f'没有 {name}（谱名或组名；组要至少 2 条纳入重复性的谱）。')

    def target_value(self, source, wavelength):
        """原样：样品目标值读原始清洗点；组均值来自共同有效网格。"""
        key = source, float(wavelength)
        if key not in self.key_cache:
            if source in self._sample_by_id:
                value = core.value_at_target(self._sample_by_id[source], float(wavelength))
            else:
                n = core.value_from_grid_curve(self.grid, self.curves[source]['n'], float(wavelength))
                k = core.value_from_grid_curve(self.grid, self.curves[source]['k'], float(wavelength))
                valid = bool(np.isfinite(n) and np.isfinite(k))
                value = {'n': n, 'k': k, 'source': '组均值' if valid else '范围外', 'in_range': valid}
            derived = core.derive_optical(np.array([float(wavelength)]), np.array([value['n']]), np.array([value['k']]),
                                          self.cfg.k_zero_threshold)
            self.key_cache[key] = {
                'n': value['n'], 'k': value['k'], 'epsilon1': derived['epsilon1'][0],
                'epsilon2': derived['epsilon2'][0], 'alpha_cm^-1': derived['alpha'][0],
                'R_理论值': derived['R'][0], 'delta_nm': derived['delta'][0],
                '数据来源': value['source'], '处于实测范围': '是' if value['in_range'] else '否'}
        return dict(self.key_cache[key])

    def value(self, name, wavelength):
        source = self.source(name)
        v = self.target_value(source, wavelength)
        out = {'波长 nm': float(wavelength), 'n': _num(v['n']), 'k': _num(v['k']), 'ε1': _num(v['epsilon1']), 'ε2': _num(v['epsilon2']),
               'α cm⁻¹': _num(v['alpha_cm^-1']), 'R': _num(v['R_理论值']), '穿透深度 nm': _num(v['delta_nm']),
               '来源': v['数据来源'], '在实测范围内': v['处于实测范围'] == '是'}
        if source.startswith('组均值｜'):
            curve = self.groups[source.split('｜', 1)[1]]
            out['σ(n)'] = _num(core.value_from_grid_curve(self.grid, curve['n_std'], float(wavelength)))
            out['σ(k)'] = _num(core.value_from_grid_curve(self.grid, curve['k_std'], float(wavelength)))
            out['重复数'] = len(curve['members'])
        return out


def at(lib, ref, wavelength, session=None):
    """某条谱或某组均值在某波长的 n、k（线性插值）。"""
    session = session or Session(lib)
    return {'名称': ref, **session.value(ref, wavelength)}


def group(lib, name, wavelengths=None, session=None):
    """组均值 ± σ（各目标波长）和重复性摘要。"""
    session = session or Session(lib)
    if name not in session.groups:
        raise ValueError(f'组 {name} 没有可算的重复（纳入重复性的谱少于 2 条，或不存在）。')
    summary = next(r for r in session.repeatability_summary if r['分组'] == name)
    return {'组': name, '成员': session.groups[name]['members'],
            '目标波长': [session.value(name, w) for w in (wavelengths or session.cfg.target_wavelengths_nm)],
            '重复性': {k: (_num(v) if isinstance(v, float) else v) for k, v in summary.items()},
            '说明': '每个波长组内全部重复都有值才算；σ 为样本标准差（ddof=1）；网格步长 %g nm' % session.cfg.grid_step_nm}


def anomalies(lib, name=None, session=None):
    """同组异常筛查：只给候选和理由，不改纳入标记。"""
    session = session or Session(lib)
    samples = [s for s in session.samples if name is None or s.metadata.get('分组') == name]
    records = anomaly_detection.detect_samples(samples, session.cfg)
    return {'候选': [{'谱名': r.sample_id, '分组': r.group, '级别': r.level, '理由': r.reasons, '组内状态': r.group_status,
                    '偏离': {s: {'超阈点比例': m['fraction'], '最大z': m['max_score'], '最大差': m['max_abs_delta'], '区间': m['ranges']}
                           for s, m in r.metrics.items()}} for r in records if r.candidate],
            '共筛查': len(records), '参数': {k: getattr(session.cfg, k) for k in anomaly_detection.PARAMETERS}}


def _pair_records(lib, records):
    return [{'id': int(r['旧id']), 'sample_id': r.get('谱名') or r['name'], 'content_hash': r.get('内容指纹') or '',
             'included': bool(r.get('纳入', True)), 'archived': False, 'group': r.get('分组') or '',
             'file_name': r.get('文件名') or '', 'metadata': {'分组': r.get('分组') or ''}} for r in records if r.get('旧id')]


def pairs(lib, records=None):
    """处理前后配对：存过的（NK.配对）＋按名字的 1:1 建议。"""
    records = records if records is not None else lib.rows(SPECTRUM)
    result = studio_pairing.propose_pairs(_pair_records(lib, records), lib.setting('NK.配对'))
    names = {int(r['旧id']): r.get('谱名') or r['name'] for r in records if r.get('旧id')}
    saved = studio_pairing.normalize_config(lib.setting('NK.配对'))['pairs']
    return {'已存配对': [{'处理前': names.get(p['before_id']), '处理后': names.get(p['after_id']), '确认': p['confirmed']} for p in saved],
            '建议': [{'处理前': names[p['before_id']], '处理后': names[p['after_id']], '依据': p['label']} for p in result['candidates']],
            '问题': [{'键': i['key'], '类型': i['type'], '说明': i['message'],
                    '处理前': [names.get(x) for x in i['before_ids']], '处理后': [names.get(x) for x in i['after_ids']]} for i in result['issues']],
            '没配上': [names.get(x) for x in result['unpaired']]}


def pair_delta(lib, before, after, wavelengths=None, session=None):
    """配对差值：Δn、Δk = 处理后 − 处理前，逐目标波长。"""
    session = session or Session(lib)
    records = session.records
    plain = _pair_records(lib, records)
    by_name = {r['sample_id']: r['id'] for r in plain}
    pair = studio_pairing.make_pair(by_name[before], by_name[after], plain, method='manual', confirmed=True)
    rows = studio_pairing.pair_target_rows(session, pair, plain, wavelengths)
    return {'处理前': before, '处理后': after,
            '差值': [{'波长 nm': r['wavelength_nm'], '处理前 n': _num(r['before_n']), '处理后 n': _num(r['after_n']), 'Δn': _num(r['delta_n']),
                    '处理前 k': _num(r['before_k']), '处理后 k': _num(r['after_k']), 'Δk': _num(r['delta_k'])} for r in rows]}


def compare(lib, ref, reference=None, session=None):
    """和参比比（参比可以是谱名或"组均值｜组名"/组名；默认 NK.参比）。"""
    session = session or Session(lib)
    source, ref_source = session.source(ref), session.source(reference or session.cfg.reference_sample)
    out = {}
    for signal in ('n', 'k'):
        metrics = core.comparison_metrics(session.curves[source][signal], session.curves[ref_source][signal])
        out[signal] = {'RMSE': _num(metrics['rmse']), 'MAE': _num(metrics['mae']), '最大差': _num(metrics['max_abs']), '相关系数': _num(metrics['corr'])}
    targets = []
    for w in session.cfg.target_wavelengths_nm:
        a, b = session.target_value(source, w), session.target_value(ref_source, w)
        dn, dk = a['n'] - b['n'], a['k'] - b['k']
        targets.append({'波长 nm': w, 'n': _num(a['n']), '参比 n': _num(b['n']), 'Δn': _num(dn), 'k': _num(a['k']), '参比 k': _num(b['k']), 'Δk': _num(dk),
                        'Δn 相对 %': _num(dn / b['n'] * 100) if b['n'] else None})
    return {'对象': source, '参比': ref_source, '全谱': out, '目标波长': targets, '说明': '全谱指标只在两条曲线共同有效的 1 nm 网格点上算'}


def compute_all(lib, report=print):
    session = Session(lib)
    cfg = session.cfg
    params = {'目标波长 nm': cfg.target_wavelengths_nm, '网格步长 nm': cfg.grid_step_nm, '参比': cfg.reference_sample}
    for record in session.records:
        name = record.get('谱名') or record['name']
        lib.set_result(record['id'], 'NK.目标波长', [session.value(name, w) for w in cfg.target_wavelengths_nm], params, VERSION)
    groups = {r.get('分组') or r['name']: r for r in lib.rows(GROUP)}
    done = 0
    for name in session.groups:
        if name in groups:
            lib.set_result(groups[name]['id'], 'NK.组均值', group(lib, name, session=session), params, VERSION)
            done += 1
    found = anomalies(lib, session=session)
    by_name = {(r.get('谱名') or r['name']): r for r in session.records}
    candidates = {c['谱名']: c for c in found['候选']}
    for name, record in by_name.items():
        lib.set_result(record['id'], 'NK.异常筛查', candidates.get(name), {**params, **found['参数']}, VERSION)
    paired = pairs(lib, session.records)
    deltas = 0
    for item in paired['已存配对'] + paired['建议']:
        if item['处理前'] in by_name and item['处理后'] in by_name:
            lib.set_result(by_name[item['处理前']]['id'], 'NK.配对差值', pair_delta(lib, item['处理前'], item['处理后'], session=session), params, VERSION)
            deltas += 1
    report(f'NK {lib.name}：{len(session.records)} 条谱、{done} 个组均值、{len(candidates)} 条异常候选、{deltas} 对配对差值')
    return {'谱': len(session.records), '组均值': done, '异常候选': len(candidates), '配对差值': deltas}
