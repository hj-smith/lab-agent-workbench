"""透明的异常候选筛查与人工审核记录，不自动改变样品纳入状态。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from .studio_time import now as _beijing_now, stamp as _beijing_stamp

METHOD_VERSION = 'mad-screen-1'
METHOD_SOURCE = 'https://itl.nist.gov/div898/handbook/eda/section3/eda35h.htm'
MODIFIED_Z_FACTOR = 0.6745
PARAMETERS = ('anomaly_z_threshold', 'anomaly_fraction', 'anomaly_min_group',
              'anomaly_min_points', 'anomaly_min_delta_n', 'anomaly_min_delta_k',
              'jump_window', 'jump_multiplier', 'large_gap_multiplier', 'grid_step_nm', 'k_zero_threshold')


def validate_parameters(cfg):
    for name in PARAMETERS:
        value = float(getattr(cfg, name))
        if not np.isfinite(value):
            raise ValueError(f'{name} 必须是有限数值')
    if cfg.anomaly_z_threshold <= 0 or not 0 < cfg.anomaly_fraction <= 1:
        raise ValueError('偏离阈值必须大于 0，异常点占比须在 (0,100%] 内')
    if cfg.anomaly_min_group < 3 or int(cfg.anomaly_min_group) != cfg.anomaly_min_group:
        raise ValueError('组内筛查至少需要 3 次重复测量')
    if cfg.anomaly_min_points < 2 or int(cfg.anomaly_min_points) != cfg.anomaly_min_points:
        raise ValueError('最低共同点数必须是大于等于 2 的整数')
    if min(cfg.anomaly_min_delta_n, cfg.anomaly_min_delta_k) < 0 or cfg.grid_step_nm <= 0:
        raise ValueError('最小绝对偏离不能为负，网格步长必须大于 0')


@dataclass
class ReviewRecord:
    file_name: str
    sample_id: str
    group: str
    imported: bool = True
    candidate: bool = False
    level: str = '未触发规则'
    reasons: list[str] = field(default_factory=list)
    group_status: str = '尚未评估'
    baseline_ids: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    file_hash: str = ''
    signature: str = ''
    review_status: str = '未审核'
    prior_review: dict = field(default_factory=dict)
    parameters: dict = field(default_factory=dict)
    # 预览数据不进入 JSON；波长轴始终为真实数值轴。
    preview: dict = field(default_factory=dict, repr=False)

    def to_dict(self):
        return {k: v for k, v in vars(self).items() if k != 'preview'}


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _intervals(x, mask, maximum=6):
    positions = np.flatnonzero(mask)
    if not len(positions):
        return ''
    groups = np.split(positions, np.where(np.diff(positions) > 1)[0] + 1)
    labels = [f'{x[g[0]]:.3f}–{x[g[-1]]:.3f}' if len(g) > 1 else f'{x[g[0]]:.3f}' for g in groups[:maximum]]
    return '、'.join(labels) + (f' 等{len(groups)}段' if len(groups) > maximum else '') + ' nm'


def jump_flags(values, cfg, signal='n'):
    """复用质量表的相邻差值倍数法，并返回对应波长索引。"""
    values = np.asarray(values, dtype=float)
    flags = np.zeros(len(values), dtype=bool)
    if len(values) < 4:
        return flags
    diffs = np.abs(np.diff(values))
    window = max(3, int(cfg.jump_window)) | 1
    median = float(np.median(diffs))
    local = pd.Series(diffs).rolling(window, center=True, min_periods=2).median().to_numpy()
    baseline = np.where(np.isfinite(local) & (local > 0), local, median)
    floor = max(cfg.anomaly_min_delta_k, cfg.k_zero_threshold) if signal == 'k' else cfg.anomaly_min_delta_n
    flags[1:] = (diffs > baseline * cfg.jump_multiplier) & (diffs > max(median * cfg.jump_multiplier, floor, np.finfo(float).eps))
    return flags


def _basic_record(sample, cfg):
    rec = ReviewRecord(sample.file_name, sample.sample_id, str(sample.metadata.get('分组', '')))
    if rec.group in {'', '未分组'}:
        rec.group_status = '未分组；仅执行基础质量检查'
    raw = sample.raw[['Wavelength_nm', 'n', 'k']].to_numpy(dtype=float)
    nonfinite = int(np.count_nonzero(~np.isfinite(raw).all(axis=1)))
    qc = sample.qc
    if qc['message'] != '未发现预设规则下的异常':
        rec.reasons.append(qc['message'])
    if nonfinite:
        rec.reasons.append(f'缺失或非有限数值 {nonfinite} 行')
    if '需要人工确认' in sample.unit_status:
        rec.reasons.append('波长单位需人工确认；不参与组内数值筛查')
    if np.any(raw[:, 0] <= 0):
        rec.reasons.append('存在非正波长')
    clean = sample.clean[['Wavelength_nm', 'n', 'k']].to_numpy(dtype=float)
    clean = clean[np.isfinite(clean).all(axis=1) & (clean[:, 0] > 0)]
    if len(clean) < 2:
        rec.reasons.append('有效光谱点少于 2，无法评估完整曲线')
    if len(clean):
        rec.preview = {'x': clean[:, 0], 'n': clean[:, 1], 'k': clean[:, 2]}
        for i, signal in [(1, 'n'), (2, 'k')]:
            flags = jump_flags(clean[:, i], cfg, signal)
            rec.preview[f'raw_flags_{signal}'] = flags
            if flags.any():
                rec.reasons.append(f'{signal} 突跳位置：{_intervals(clean[:, 0], flags)}')
    rec.candidate = bool(rec.reasons)
    if qc['n_nonpositive_count'] or qc['k_negative_count'] or nonfinite or len(clean) < 2 or np.any(raw[:, 0] <= 0):
        rec.level = '数据问题'
    elif '需要人工确认' in sample.unit_status:
        rec.level = '待确认单位'
    elif rec.candidate:
        rec.level = '质量提示'
    return rec


def _group_compare(records, samples, cfg):
    """在保留同组重复的共同范围内比较；被排除样品仍可接受复查。"""
    groups = {}
    by_name = {s.sample_id: s for s in samples}
    for rec in records:
        if rec.imported and rec.group and rec.group != '未分组':
            groups.setdefault(rec.group, []).append(rec)
    for members in groups.values():
        pool = [r for r in members if by_name[r.sample_id].included
                and by_name[r.sample_id].repeatability_included
                and '需要人工确认' not in by_name[r.sample_id].unit_status
                and len(r.preview.get('x', [])) >= 2]
        ids = [r.sample_id for r in pool]
        if len(pool) < cfg.anomaly_min_group:
            for rec in members:
                rec.group_status = f'可用重复 {len(pool)} 次，少于 {cfg.anomaly_min_group} 次；不判定组内离群'
                rec.baseline_ids = ids
            if len(pool) >= 2:
                lo=max(r.preview['x'][0] for r in pool); hi=min(r.preview['x'][-1] for r in pool)
                if hi > lo:
                    preview_grid=np.linspace(lo,hi,min(1000,max(2,int((hi-lo)/cfg.grid_step_nm)+1)))
                    matrices={s:np.array([np.interp(preview_grid,r.preview['x'],r.preview[s]) for r in pool]) for s in ('n','k')}
                    for rec in members:
                        if rec.preview:
                            rec.preview.update({'group_x':preview_grid,'group_n':np.median(matrices['n'],axis=0),
                                                'group_k':np.median(matrices['k'],axis=0),'peers_n':matrices['n'],'peers_k':matrices['k']})
            continue
        lo = max(r.preview['x'][0] for r in pool)
        hi = min(r.preview['x'][-1] for r in pool)
        size = int(np.floor((hi - lo) / cfg.grid_step_nm)) + 1
        if size < cfg.anomaly_min_points:
            for rec in members:
                rec.group_status = f'共同网格仅 {max(0, size)} 点，未达到 {cfg.anomaly_min_points} 点'
            continue
        if size > 100000:
            raise ValueError('异常筛查网格超过 100000 点，请增大网格步长')
        grid = lo + np.arange(size) * cfg.grid_step_nm
        matrices = {sig: np.array([np.interp(grid, r.preview['x'], r.preview[sig]) for r in pool]) for sig in ('n', 'k')}
        centers = {sig: np.median(data, axis=0) for sig, data in matrices.items()}
        spreads = {}
        for sig, data in matrices.items():
            mad = np.median(np.abs(data - centers[sig]), axis=0)
            # 全谱 MAD 中位数为经验底噪保护，并非仪器精度或工程容差。
            floor = max(float(np.median(mad)), np.finfo(float).eps * 64 * max(1.0, float(np.max(np.abs(data)))))
            spreads[sig] = np.maximum(mad, floor)
        for rec in members:
            rec.baseline_ids = ids
            if '需要人工确认' in by_name[rec.sample_id].unit_status or not rec.preview:
                rec.group_status = '单位或有效数值不足，未进行组内比较'
                continue
            valid = (grid >= rec.preview['x'][0]) & (grid <= rec.preview['x'][-1])
            if valid.sum() < cfg.anomaly_min_points:
                rec.group_status = '与组基线的共同有效点不足'
                continue
            rec.group_status = f'已评估；基线 {len(pool)} 次，共同有效 {int(valid.sum())} 点'
            rec.preview.update({'group_x': grid, 'group_n': centers['n'], 'group_k': centers['k'],
                                'peers_n': matrices['n'], 'peers_k': matrices['k']})
            for signal in ('n', 'k'):
                y = np.interp(grid, rec.preview['x'], rec.preview[signal])
                deviation = np.abs(y - centers[signal])
                score = MODIFIED_Z_FACTOR * deviation / spreads[signal]
                absolute_floor = getattr(cfg, f'anomaly_min_delta_{signal}')
                # 可视化直接使用与筛查同源的差值和界线，避免图与判定脱节。
                signed_delta = np.where(valid, y - centers[signal], np.nan)
                threshold = np.where(valid, np.maximum(spreads[signal] * cfg.anomaly_z_threshold / MODIFIED_Z_FACTOR,
                                                      absolute_floor), np.nan)
                flags = valid & (score > cfg.anomaly_z_threshold) & (deviation > absolute_floor)
                fraction = float(flags.sum() / valid.sum())
                rec.metrics[signal] = {'fraction': fraction, 'max_score': float(score[valid].max()),
                                       'max_abs_delta': float(deviation[valid].max()),
                                       'ranges': _intervals(grid, flags), 'points': int(valid.sum())}
                rec.preview[f'flags_{signal}'] = flags
                rec.preview[f'delta_{signal}'] = signed_delta
                rec.preview[f'threshold_{signal}'] = threshold
                if fraction >= cfg.anomaly_fraction:
                    rec.candidate = True
                    if rec.level == '未触发规则':
                        rec.level = '组内偏离候选'
                    rec.reasons.append(f'{signal} 偏离组中位数：{fraction:.1%} 点超过筛查阈值；{_intervals(grid, flags)}')


def detect_samples(samples, cfg, overrides=None, failures=()):
    """纯筛查：不改 included/repeatability_included/原始数据。"""
    validate_parameters(cfg)
    overrides = overrides or {}
    records = [_basic_record(sample, cfg) for sample in samples]
    for failure in failures:
        records.append(ReviewRecord(failure.file_name, overrides.get(failure.file_name, {}).get('sample_id', Path(failure.file_name).stem),
                                    overrides.get(failure.file_name, {}).get('metadata', {}).get('分组', ''),
                                    imported=False, candidate=True, level='导入失败', reasons=[failure.message]))
    by_id = {s.sample_id: s for s in samples}
    failed_paths = {f.file_name: f.file_path for f in failures}
    for rec in records:
        sample = by_id.get(rec.sample_id)
        stored_hash = sample.metadata.get('_content_hash', '') if sample is not None else ''
        p = Path(sample.file_path if sample is not None else failed_paths[rec.file_name])
        if stored_hash:
            rec.file_hash = stored_hash
        elif p.is_file():
            rec.file_hash = file_hash(p)
        elif sample is not None:
            rec.file_hash = hashlib.sha256(sample.raw.to_csv(index=False).encode('utf-8')).hexdigest()
        else:
            rec.file_hash = 'unavailable'
    _group_compare(records, samples, cfg)
    params = {name: getattr(cfg, name) for name in PARAMETERS}
    for rec in records:
        rec.parameters = dict(params)
        peers = sorted((r.sample_id, r.file_hash, r.group) for r in records if r.group == rec.group)
        context = json.dumps([METHOD_VERSION, params, rec.file_hash, peers], ensure_ascii=False, sort_keys=True)
        rec.signature = hashlib.sha256(context.encode()).hexdigest()
        item = overrides.get(rec.sample_id, overrides.get(rec.file_name, {}))
        rec.prior_review = item.get('anomaly_review', {})
        prior = rec.prior_review
        if prior:
            if prior.get('signature') != rec.signature:
                rec.review_status = '数据或规则已变，需复核'
            elif prior.get('decision') != ('排除全部分析' if not item.get('included', True) else ('仅排除重复性' if not item.get('repeatability_included', True) else '保留纳入')):
                rec.review_status = '纳入状态已在其它位置更改'
            elif prior.get('baseline_ids') != rec.baseline_ids:
                rec.review_status = '基线已变，保留上次决定'
            else:
                rec.review_status = '已审核：' + prior.get('decision', '')
    priority = {'导入失败': 0, '数据问题': 0, '待确认单位': 1, '质量提示': 2, '组内偏离候选': 3}
    return sorted(records, key=lambda r: (not r.candidate, priority.get(r.level, 4),
                                          -max((m['fraction'] for m in r.metrics.values()), default=0), r.file_name))


def scan_review(input_dir, cfg, overrides, progress=None):
    """软件后台扫描，与生成器共用同一个文件解析器和配置。"""
    import build_nk_workbook as engine
    samples, failures = [], []
    paths = engine.scan_input_files(Path(input_dir))
    for i, path in enumerate(paths, 1):
        if progress:
            progress(f'检测 {i}/{len(paths)}：{path.name}')
        before = path.stat()
        try:
            sample, _ = engine.parse_sample_file(path, cfg)
            after = path.stat()
            if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                raise ValueError('读取时文件发生变化，请重新检测')
            samples.append(sample)
        except Exception as exc:
            failures.append(engine.ImportRecord(file_name=path.name, file_path=str(path), status='失败',
                                               import_time=_beijing_now().isoformat(), message=f'{type(exc).__name__}: {exc}'))
    engine.apply_sample_overrides(samples, overrides)
    engine.make_unique_ids(samples)
    return detect_samples(samples, cfg, overrides, failures)


def choose_record(overrides, record, included, repeated, note=''):
    """仅在人工操作时改变纳入标志，并记录当时检测证据。"""
    item = overrides.setdefault(record.sample_id, {})
    if item.get('anomaly_review'):
        item.setdefault('anomaly_review_history', []).append(item['anomaly_review'])
    item['included'] = bool(included)
    item['repeatability_included'] = bool(repeated and included)
    decision = '排除全部分析' if not included else ('仅排除重复性' if not repeated else '保留纳入')
    item['anomaly_review'] = {'decision': decision, 'time': _beijing_now().isoformat(timespec='seconds'),
                              'signature': record.signature, 'file_hash': record.file_hash,
                              'baseline_ids': record.baseline_ids, 'reasons': list(record.reasons),
                              'note': note.strip(), 'method': METHOD_VERSION, 'parameters': dict(record.parameters)}


