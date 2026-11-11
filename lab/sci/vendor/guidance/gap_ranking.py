"""离参比差距排序（A2-DSH · C）。

平台自己的新算法：把 A1a 的观测表和对标差值（Δ = 样品 − 参比）换算成可以
放在一起排的“归一化差距”，再回答用户每天要问的三件事：

* 离参比还差多少（每个项目、每个样品）；
* 哪一项差得最远、下一步主攻哪一项（最近一个项目里按 |归一化差距| 排）；
* 最近几轮在变好还是变差（同一条件下至少 ``trend_min_projects`` 个项目才写成推断）。

口径（默认值都可改，存在 analysis_choices 的 ``kind="guidance"``）：

1. Δ = 样品 − 参比。
2. 归一化 = Δ ÷ 本系列该指标的离散度（独立块的 MAD × 1.4826）；用户在容差里
   给某个指标填了数，就改用 Δ ÷ 容差。损失率的参比接近 0，不按相对参比的 %
   算。不归一化时只排原值。
3. 按项目汇总：每个项目取各指标 Δ 的中位数（条件不同的块分开算，条件未知的
   单独标出）。
4. 排序：指标按 |归一化差距|；项目按某个指标的 |Δ|；样品综合取各指标的最大
   （最差那一项决定），可改成平均或加权。
5. 趋势一律写成推断，并说明“各轮树脂批和配方同时在变，不能归因”。
6. 有严重质量标记的块不进中位数，单独列出。

本模块不配对、不算差值、不写库：数据来自 ``AnalysisService.run('benchmark')``
（椭偏参比接口给每块配好参比和 Δ），这里只做换算、汇总、排序和措辞。
"""
from collections import defaultdict
import math
from statistics import median

from .common import digest
from .settings import DEFAULTS

ALGORITHM = 'platform.gap_rank'
VERSION = 1
# 对标卡没有选定参比时，对照的推荐第 1 名。只是默认清单里的一项（参比是通用概念）。
DEFAULT_REFERENCE_LABEL = 'REF01'

# 推荐编号（对标比较行）→ 观测表里的响应编号。基准来自 A1a 的随附代码：
# 平台不另写配对，只把椭偏对标的字段换成观测编号。
BENCHMARK_KEYS = {
    'ellipsometry:n193': 'optics.n_193.before_diff',
    'ellipsometry:k193': 'optics.k_193.before_diff',
    'ellipsometry:thickness_nm': 'before_diff',
    'ellipsometry:thickness_loss_pct@SolvA': 'loss_pp',
}

# 条件不同就分开算；'' 表示条件未知，单独一组。
CONDITION_FIELDS = ('bake_temp_c', 'bake_time_s', 'spin_rpm')
CONDITION_LABELS = {'bake_temp_c': '烘烤温度', 'bake_time_s': '烘烤时间', 'spin_rpm': '转速'}


def configured(choices):
    """把用户存进 analysis_choices 的 guidance 选择并进默认值。"""
    params = {key: (dict(value) if isinstance(value, dict) else list(value) if isinstance(value, list) else value)
              for key, value in DEFAULTS['guidance'].items()}
    modified = []
    for choice in choices or []:
        if choice.get('kind') != 'guidance' or choice.get('deleted_at'):
            continue
        key, value = choice.get('key'), choice.get('value')
        if value is None:
            continue
        if key in (None, 'default'):
            params.update(value if isinstance(value, dict) else {})
        else:
            params[key] = value
        if key not in modified:
            modified.append(key)
    return params, modified


def merge_params(choices, params):
    base, modified = configured(choices)
    return {**base, **(params or {})}, modified


def mad_dispersion(values):
    """MAD × 1.4826（和标准差同一个量纲）；少于 2 个值或全相同时给 None。"""
    values = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    if len(values) < 2:
        return None
    center = median(values)
    scale = median(abs(v - center) for v in values) * 1.4826
    return scale if scale > 0 else None


def observed_loss(row):
    """损失率原值。

    W12 椭偏 ``group_summary`` 接上之前，溶解块的损失率是“处理后膜厚 0 → 100 %”
    这个口径（A1a 的验收把溶解记作 100）。这里照原样使用，并标出来：``source``
    为 ``dissolved`` 时，值是按“溶解即完全损失”约定的，等 W12 口径接上再核。
    """
    cell = row.get('responses', {}).get('ellipsometry:thickness_loss_pct@SolvA') or {}
    dissolved = bool((row.get('post_treatment') or {}).get('dissolved'))
    value = cell.get('value') if cell.get('state') in ('measured', 'design') else None
    # 溶解块：椭偏说“膜被溶掉了”（处理后膜厚 0），没有损失率单元格时照样按 100 % 记。
    if dissolved and value in (None, 0):
        return dict(value=100.0, source='dissolved', note='处理后膜厚 0，按“溶解即完全损失”记 100 %')
    if isinstance(value, (int, float)):
        return dict(value=value, source='dissolved' if dissolved else 'measured')
    return None


def _is_severe(row, flags):
    return any(flag in row.get('flags', []) for flag in flags)


def _is_diluted(rows, low_k, group_k_median):
    """稀释块：k@193 低于 low_k、而同组 k 的中位数不低（A1a 的“T15B 稀释 3 倍”那条规则）。

    A1a 只在观测行上标了 possible_outlier，看不出是哪条规则触发的；这里按 k 重算一次，
    只把“稀释”当严重块（抄错列、整块 MSE 偏高要等 W12 的椭偏质量标记）。
    """
    by_project = defaultdict(list)
    for row in rows:
        value = (row.get('responses', {}).get('ellipsometry:k193') or {}).get('value')
        if isinstance(value, (int, float)):
            by_project[(row.get('project') or {}).get('ref')].append(value)
    centers = {key: median(values) for key, values in by_project.items() if values}
    diluted = set()
    for row in rows:
        value = (row.get('responses', {}).get('ellipsometry:k193') or {}).get('value')
        center = centers.get((row.get('project') or {}).get('ref'))
        if isinstance(value, (int, float)) and center is not None and value < low_k and center > group_k_median:
            diluted.add(row['obs_id'])
    return diluted


def _condition_signature(row):
    values = tuple(row.get('conditions', {}).get(key) for key in CONDITION_FIELDS)
    return values if any(v is not None for v in values) else None


def _condition_label(signature):
    if signature is None:
        return '条件未知'
    return '、'.join(f"{CONDITION_LABELS[key]} {value:g}" if isinstance(value, (int, float)) else f"{CONDITION_LABELS[key]} {value}"
                    for key, value in zip(CONDITION_FIELDS, signature) if value is not None)


def _group_key(row, by):
    if by == 'resin_lot':
        cell = row.get('factors', {}).get('formulation:resin_lot') or {}
        return str(cell.get('value')) if cell.get('value') else '树脂批未知'
    if by == 'formula_series':
        import re
        code = row.get('sample_code') or ''
        return re.sub(r'[A-Z]$', '', code) or code
    project = row.get('project') or {}
    return project.get('ref') or '项目未知'


def _group_name(key, rows, by):
    if by == 'project':
        name = next((r['project'].get('name') for r in rows if (r.get('project') or {}).get('ref') == key), None)
        return name or key.replace('ellipsometry:project:', '项目 ')
    return key


def _display_digits(params, rid, default):
    digits = (params.get('display') or {}).get('digits') or {}
    value = digits.get(rid, digits.get(rid.split(':', 1)[-1]))
    return int(value) if isinstance(value, (int, float)) else default


def _unit(rid):
    if rid.endswith('n193') or rid.endswith('k193'):
        return ''
    if rid.endswith('thickness_nm'):
        return ' nm'
    if rid.endswith('loss_pct@SolvA'):
        return ' %'
    return ''


def _fmt(rid, value, params):
    """原值照存，显示时才取位：n、k 四位，膜厚一位，损失率两位。"""
    if value is None:
        return '—'
    digits = _display_digits(params, rid, 2 if rid.endswith('loss_pct@SolvA') else (1 if rid.endswith('thickness_nm') else 4))
    return f"{value:+.{digits}f}{_unit(rid)}"


def empty(params, warnings):
    params = {**{k: v for k, v in DEFAULTS['guidance'].items()}, **(params or {})}
    return dict(algorithm=ALGORITHM, version=VERSION, params=params,
                reference=dict(code=None, state='unavailable', label='等椭偏参比接口', chosen_by=None),
                metrics=[dict(id=rid, label=_metric_label(rid), unit=_unit(rid).strip()) for rid in params.get('metrics') or []],
                dispersion={}, normalization={}, combine=params.get('combine'), group_by=params.get('group_by'),
                projects=[], blocks=[], samples=[], trends={}, latest=None, trend_min_projects=params.get('trend_min_projects'),
                severe_flags=list(params.get('severe_flags') or []), warnings=list(warnings or []), inputs=[])


def _metric_label(rid):
    base = rid.split(':', 1)[-1]
    names = {'n193': 'n@193', 'k193': 'k@193', 'thickness_nm': '膜厚', 'thickness_loss_pct@SolvA': 'SolvA 损失率'}
    return names.get(base, base)


def _confidence(reason_codes):
    """1.2 的默认置信度：高 / 中 / 低。数据不足另由 insufficient 表达。"""
    reason_codes = set(reason_codes)
    if reason_codes & {'cross_day', 'conditions_differ', 'conditions_unknown', 'severe_quality', 'reference_unconfirmed'}:
        return '低'
    if {'single_film', 'quality_flag'} & reason_codes:
        return '中'
    return '高'


def _reference_cell(entry, rid):
    """对标比较行里的差值字段；比较行还没有给出这一项时返回 None（不补算）。"""
    if entry.get('response_differences') is not None:
        value = entry['response_differences'].get(rid)
        return value if isinstance(value, (int, float)) and math.isfinite(value) else None
    key = BENCHMARK_KEYS[rid]
    value = entry
    for part in key.split('.'):
        value = (value or {}).get(part) if isinstance(value, dict) else None
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def _match_pair(pairs, sample_id, observation_id, mapping):
    for pair in pairs:
        if sample_id and sample_id in (pair.get('module_sample_ids') or []):
            return pair
        if observation_id in (pair.get('observation_ids') or []):
            return pair
    return None


def _reference_label(label):
    """“REF01 · 400℃ · 90 s · SolvA · 2026-07-28”里的样品编号。"""
    return (label or '').split(' · ')[0].strip() or None


def _metric_presence(candidate):
    """这个参比有哪几项可比：损失率看它自己有没有损失率（溶解的参比没有），
    膜厚看有没有处理前膜厚；光学常数总是有。"""
    missing = set()
    if not isinstance(candidate.get('loss_percent'), (int, float)):
        missing.add('ellipsometry:thickness_loss_pct@SolvA')
    if not isinstance(candidate.get('before_mean'), (int, float)):
        missing.add('ellipsometry:thickness_nm')
    return {rid for rid in BENCHMARK_KEYS if rid not in missing}


def _days_between(later, earlier):
    if not later or not earlier:
        return None
    try:
        from datetime import date
        return (date.fromisoformat(str(later)[:10]) - date.fromisoformat(str(earlier)[:10])).days
    except ValueError:
        return None


def _reference_conditions(option):
    """推荐清单里的参比条件：键名和观测表不同（bake_temp_c / treatment），只看这 7 项。"""
    given = option.get('conditions') or {}
    return {'spin_rpm': given.get('spin_rpm'), 'bake_temp_c': given.get('bake_temperature_c', given.get('bake_temp_c')),
            'bake_time_s': given.get('bake_time_s'), 'treatment': given.get('treatment'),
            'treatment_time': given.get('treatment_time', given.get('post_bake_time_s')),
            'post_bake_temp_c': given.get('post_bake_temperature_c', given.get('post_bake_temp_c')),
            'post_bake_time_s': given.get('post_bake_time_s')}


def _condition_diff(source, target):
    """reference.py 的 condition_diffs 口径：candidate=样品，reference=参比。"""
    diffs = []
    for field in ('spin_rpm', 'bake_temp_c', 'bake_time_s', 'treatment', 'treatment_time', 'post_bake_temp_c', 'post_bake_time_s'):
        if (source.get(field) or None) != (target.get(field) or None):
            diffs.append(field)
    return diffs


def rank(rows, params=None, tolerances=None, series_id=None, warnings=None, inputs=None):
    params = {**{k: v for k, v in DEFAULTS['guidance'].items()}, **(params or {})}
    metrics = list(params.get('metrics') or [])
    tolerances = tolerances or {}
    normalization = params.get('normalization', 'dispersion')
    if normalization not in ('dispersion', 'tolerance', 'none'):
        raise ValueError('归一化方式必须是 dispersion、tolerance 或 none')
    combine = params.get('combine', 'max')
    if combine not in ('max', 'mean', 'weighted'):
        raise ValueError('综合方式必须是 max、mean 或 weighted')
    group_by = params.get('group_by', 'project')
    if group_by not in ('project', 'resin_lot', 'formula_series'):
        raise ValueError('分组方式必须是 project、resin_lot 或 formula_series')
    trend_min = int(params.get('trend_min_projects', 3))
    severe_flags = params.get('severe_flags') or DEFAULTS['guidance']['severe_flags']
    weights = {m: float((params.get('weights') or {}).get(m, 1)) for m in metrics}
    benchmark = params.get('benchmark') or {}
    warnings_list = list(warnings or [])
    entries = [e for e in (benchmark.get('comparisons', {}).get('rows') or []) if e.get('group_key')]
    by_key = {e['group_key']: e for e in entries}
    mapping = benchmark.get('identity_mapping') or {}
    pairs = benchmark.get('pairs') or []
    # 观测表里的处理方式等条件由合同字段 treatment 提供；这里只读，不改观测行
    # （不去改观测表的算法，只在算差值这一层把处理方式并进用于比较的条件）。
    rows = [{**row, 'conditions': {**row.get('conditions', {}), **row['treatment']}}
            if row.get('module') == 'ellipsometry' and isinstance(row.get('treatment'), dict) else row
            for row in rows]

    # 1. 每个观测块的：项目、条件、指标值、差值、质量标记。
    items = [row for row in rows if row.get('module') == 'ellipsometry' and row.get('independent') and not row.get('excluded')]
    diluted = _is_diluted(items, float(params.get('diluted_low_k', .05)), float(params.get('diluted_group_k_median', .3)))
    block_of = {}
    for row in items:
        sample_id = mapping.get(row['obs_id'], {}).get('sample_id')
        pair = _match_pair(pairs, sample_id, row['obs_id'], mapping)
        block_of[row['obs_id']] = dict(sample_id=sample_id, code=row.get('sample_code'), pair=pair,
                                       explicit_key=row['obs_id'].split(':', 1)[1] if ':' in row['obs_id'] else None,
                                       entry=by_key.get(pair['candidate_group_key']) if pair else None)

    # 对标卡还没有选参比（全是 needs_choice）、或者还没给出比较行时：按对标卡的推荐
    # 清单取第 1 名（同条件优先、再取日期最近），交给调用方用椭偏的显式只读比较算差值
    # 后传回（params.pairs）。本模块不自己配对，也不自己减原值——差值只有一份来源。
    from ..reference import EllipsometryReference
    explicit_rows = ([params['reference_row']] if isinstance(params.get('reference_row'), dict) else []) \
        + list(params.get('reference_rows') or [])
    if explicit_rows:
        # 差值从哪儿套到哪个块：只有两条路，都要求能对到唯一的椭偏样品编号（SI-…）——
        # ①显式比较行自己带 sample_id；②显式比较行带 group_key，加上 benchmark 里
        # 这一组的 module_sample_ids。按样品名套差值会把同一配方的别的条件块算错，不采用。
        by_group = {row.get('group_key'): row for row in explicit_rows if row.get('group_key')}
        group_pairs = list(params.get('explicit_pairs') or [])
        if isinstance(group_pairs, dict):
            group_pairs = list(group_pairs.values())
        mapping_obs = (params.get('benchmark') or {}).get('identity_mapping') or {}
        explicit = {}
        for row in explicit_rows:
            if row.get('sample_id'):
                explicit[row['sample_id']] = row
        for pair in group_pairs:
            item = by_group.get(pair.get('candidate_group_key'))
            if not item:
                continue
            for sample_id in pair.get('module_sample_ids') or []:
                explicit.setdefault(sample_id, item)
            for observation_id in pair.get('observation_ids') or []:
                sample_id = (mapping_obs.get(observation_id) or {}).get('sample_id')
                if sample_id:
                    explicit.setdefault(sample_id, item)
        for sample_id, entry in explicit.items():
            matched = [b for b in block_of.values()
                       if (b['sample_id'] and b['sample_id'] == sample_id) or b['explicit_key'] == sample_id]
            if len(matched) != 1:
                continue
            block = matched[0]
            if block['entry']:
                continue
            block['entry'] = entry
            block['explicit'] = True
    fallback = dict(params.get('reference_fallback') or {})
    fallback_code = _reference_label(fallback.get('reference_label') or DEFAULT_REFERENCE_LABEL)
    # 对标卡选定的那一次参比（用户可以指定，默认是推荐第 1 名）。
    chosen_reference = dict(params.get('reference') or {})
    for row in items:
        block = block_of[row['obs_id']]
        if block['entry'] or not fallback.get('enabled', True):
            continue
        # 推荐第 1 名：差的条件项数最少，再取日期最近。这里只决定“拿哪一次参比”，
        # 差值的数值由调用方按椭偏参比接口做显式比较再传回来（services._guidance_run）。
        pair = block['pair'] or {}
        if 'readable_recommendation' in pair:
            options = [pair['readable_recommendation']] if pair['readable_recommendation'] else []
        else:
            options = [option for option in pair.get('suggestions') or [] if option.get('has_valid_data') is not False]
        if not pair:
            options = [p for p in pairs if _reference_label(p.get('candidate_label')) == fallback_code]
        scored = []
        for option in options:
            if not EllipsometryReference.reference_not_later(row.get('measured_on'),option.get('test_date')):
                continue
            diffs = _condition_diff(row.get('conditions') or {}, _reference_conditions(option))
            date = option.get('test_date') or ''
            gap = abs(_days_between(date, row.get('measured_on'))) if date and row.get('measured_on') else None
            scored.append((len(diffs), gap if gap is not None else 99999, date, option, diffs, gap))
        if not scored:
            continue
        scored.sort(key=lambda s: (s[0], s[1], s[2]))
        _, _, date, option, diffs, gap = scored[0]
        block['fallback'] = dict(source='candidate', group_key=(block.get('pair') or {}).get('candidate_group_key'),
                                 label=option.get('label'), reference_key=option.get('reference_key'), test_date=date,
                                 date_diff_days=gap, condition_fields=diffs, conditions=_reference_conditions(option),
                                 metric_presence=_metric_presence(option), values=option.get('values') or {})

    blocks = []
    for row in items:
        block = block_of[row['obs_id']]
        values, deltas = {}, {}
        for rid in metrics:
            if rid.endswith('loss_pct@SolvA'):
                loss = observed_loss(row)
                cell_value = loss['value'] if loss else None
            else:
                cell = row.get('responses', {}).get(rid) or {}
                cell_value = cell.get('value') if cell.get('state') == 'measured' and isinstance(cell.get('value'), (int, float)) else None
            delta = None
            if block.get('entry'):
                delta = _reference_cell(block['entry'], rid)
            elif block.get('fallback') and rid in block['fallback']['metric_presence']:
                # 推荐清单本身没有数值：等调用方把显式比较的差值传回来（params.reference_row）。
                reference_value = block['fallback']['values'].get(rid)
                delta = cell_value - reference_value if isinstance(reference_value, (int, float)) and cell_value is not None else None
            if cell_value is not None:
                values[rid] = cell_value
            if delta is not None:
                deltas[rid] = delta
        entry_reference = (block.get('entry') or {}).get('reference') or {}
        pair = block.get('pair') or {}
        fallback_info = block.get('fallback')
        blocks.append(dict(
            obs_id=row['obs_id'], sample_code=row.get('sample_code'), measured_on=row.get('measured_on'),
            project=_group_key(row, group_by), project_name=_group_name(_group_key(row, group_by), [row], group_by),
            project_label=(row.get('project') or {}).get('name'),
            conditions=_condition_signature(row), conditions_label=_condition_label(_condition_signature(row)),
            raw_conditions=dict(row.get('conditions') or {}),
            values=values, deltas=deltas, flags=list(row.get('flags') or []),
            severe=_is_severe(row, severe_flags) or row['obs_id'] in diluted,
            diluted=row['obs_id'] in diluted,
            reference=dict(code=_reference_label(entry_reference.get('label')) or _reference_label(pair.get('reference_label'))
                           or _reference_label((fallback_info or {}).get('label')) or chosen_reference.get('label') or chosen_reference.get('code'),
                           reference_key=pair.get('reference_key'), candidate_group_key=pair.get('candidate_group_key'),
                           status=pair.get('status'), measured_on=entry_reference.get('test_date') or (fallback_info or {}).get('test_date'),
                           date_diff_days=entry_reference.get('date_diff_days') if entry_reference else (fallback_info or {}).get('date_diff_days'),
                           condition_diffs=pair.get('condition_diffs') or (fallback_info or {}).get('condition_fields') or [],
                           condition_diff_count=len((fallback_info or {}).get('condition_fields') or []),
                           source='explicit' if block.get('explicit') else ('benchmark' if block.get('entry') else (fallback_info or {}).get('source'))),
            fallback=fallback_info, date_source=row.get('date_source'), row=row))

    # 2. 参比状态：对标卡没给出差值、用推荐第 1 名算的就是“参比未确认”。
    reference_codes = sorted({b['reference'].get('code') for b in blocks if b['reference'].get('code')})
    selected_blocks = [b for b in blocks if b['reference'].get('reference_key')
                       and b['reference'].get('source') in ('benchmark', 'explicit')]
    # Blocks without an eligible reference have no deltas and do not participate in ranking.
    compared_blocks = [b for b in blocks if b['deltas'] or b.get('fallback')]
    confirmed = bool(selected_blocks) and all(b in selected_blocks for b in compared_blocks)
    selected_count = len({b['reference'].get('candidate_group_key') or b['obs_id'] for b in selected_blocks})
    used_fallback = any(b['reference'].get('source') in ('candidate', 'fallback')
                        or (b['reference'].get('source') == 'explicit' and not b['reference'].get('reference_key'))
                        for b in blocks)
    selected_reference = bool(chosen_reference.get('reference_key'))
    state = 'confirmed' if confirmed else ('selected' if selected_reference else 'unconfirmed')
    label = {'confirmed': '参比已确认', 'selected': '本次选定参比', 'unconfirmed': '参比未确认'}[state]
    chosen_by = (f'对标卡已选定 {selected_count} 个候选' if confirmed else ('本次指定（对标卡选定的那一次）' if selected_reference
                                               else ('按有读数的推荐参比试算（空读数顺延）' if used_fallback else '尚无推荐')))
    reference_info = dict(code=(reference_codes[0] if len(reference_codes) == 1 else (reference_codes or None)),
                          state=state, label=label, chosen_by=chosen_by, selected_count=selected_count,
                          method='对标卡给出的差值' if confirmed else ('按本次选定的参比做椭偏只读比较' if selected_reference
                                                                      else ('guidance 回退：同条件优先、日期最近的参比' if used_fallback else '对标卡不给差值')),
                          reference_key=chosen_reference.get('reference_key'),
                          statuses=sorted({b['reference'].get('status') for b in blocks if b['reference'].get('status')}))

    # 3. 离散度：本系列所有独立块（含参比），去掉用户标出的离群/稀释块。
    dispersion = {}
    for rid in metrics:
        # 离散度用本系列独立块的 MAD×1.4826：不含参比块（它属于参比系列），
        # 也不含严重块（默认就是稀释块）。
        pool = [b['values'][rid] for b in blocks if rid in b['values'] and not b['row'].get('is_reference') and not b['severe']]
        value = mad_dispersion(pool)
        if value is not None:
            dispersion[rid] = dict(value=value, unit=_unit(rid).strip(), blocks=len(pool), method='MAD×1.4826',
                                   excluded=sum(1 for b in blocks if rid in b['values'] and b['row'].get('is_reference')),
                                   excluded_severe=sum(1 for b in blocks if rid in b['values'] and not b['row'].get('is_reference') and b['severe']))

    # 4. 归一化：填了容差就用容差，否则用离散度（用户 2026-10-22 定的默认）。
    normalization_info = {}
    for rid in metrics:
        tolerance = tolerances.get(rid) if normalization != 'none' else None
        if normalization == 'none':
            normalization_info[rid] = dict(mode='none', scale=None, label='不归一化，只看原值')
        elif tolerance is not None:
            normalization_info[rid] = dict(mode='tolerance', scale=float(tolerance), label=f'÷ 容差 {tolerance}{_unit(rid)}')
        elif rid in dispersion:
            normalization_info[rid] = dict(mode='dispersion', scale=dispersion[rid]['value'], label=f"÷ 本系列离散度 {dispersion[rid]['value']:.4g}")
        else:
            normalization_info[rid] = dict(mode='unavailable', scale=None, label='离散度算不出，不归一化')

    def normalized(rid, delta):
        scale = normalization_info.get(rid, {}).get('scale')
        if delta is None:
            return None
        return delta if scale in (None, 0) else delta / scale

    # 5. 按项目（或树脂批、配方系列）× 条件分组，取各指标 Δ 的中位数。
    groups = defaultdict(list)
    for block in blocks:
        groups[(block['project'], block['conditions'])].append(block)
    projects = []
    for (project, conditions), members in groups.items():
        clean = [b for b in members if not b['severe']]
        severe = [b for b in members if b['severe']]
        deltas, normalized_values, detail = {}, {}, {}
        for rid in metrics:
            used = [b['deltas'][rid] for b in clean if rid in b['deltas']]
            if used:
                value = median(used)
                samples = [b['values'][rid] for b in clean if rid in b['deltas'] and rid in b['values']]
                deltas[rid] = dict(value=value, sample_value=median(samples) if samples else None, n=len(used),
                                   unit=_unit(rid).strip(), sources=[b['obs_id'] for b in clean if rid in b['deltas']])
                normalized_values[rid] = normalized(rid, value)
            detail[rid] = dict(used=used, n=len(used))
        scores = {rid: dict(delta=deltas[rid]['value'], normalized=normalized_values.get(rid),
                            scale=normalization_info[rid]['mode'], label=normalization_info[rid]['label'],
                            value=deltas[rid]['value'], unit=deltas[rid]['unit'])
                  for rid in metrics if rid in deltas}
        scored = [rid for rid in metrics if normalized_values.get(rid) is not None]
        priority = sorted(scored, key=lambda rid: -abs(normalized_values[rid]))
        projects.append(dict(
            name=members[0]['project_name'], ref=project if group_by == 'project' else None,
            kind='项目' if group_by == 'project' else ('树脂批' if group_by == 'resin_lot' else '配方系列'),
            conditions=dict(zip(CONDITION_FIELDS, conditions)) if conditions else None,
            conditions_label=_condition_label(conditions), conditions_known=conditions is not None,
            date=min((b['measured_on'] for b in members if b['measured_on']), default=None),
            samples=sorted({b['sample_code'] for b in members}), blocks=len(members), clean_blocks=len(clean),
            deltas=deltas,
            normalized=normalized_values, scores=scores, priority=priority,
            severe_excluded=[dict(sample_code=b['sample_code'], flags=b['flags'], deltas={rid: b['deltas'].get(rid) for rid in metrics if rid in b['deltas']}) for b in severe],
            reference=dict(reference_code=members[0]['reference'].get('code'), status=members[0]['reference'].get('status'),
                                              measured_on=members[0]['reference'].get('measured_on'),
                                              date_diff_days=members[0]['reference'].get('date_diff_days')),
            sources=[b['obs_id'] for b in members]))
        # 还没算出差值、只挑好了推荐参比的块：交给调用方显式比较（见 reference_pairs）。
        projects[-1]['reference_second_pass'] = 'pairs' in params
        _pending(projects[-1], members)
    projects.sort(key=lambda p: (p['date'] or '', p['conditions_label']), reverse=True)

    # 6. 样品综合排序：默认取各指标 |归一化差距| 的最大值（最差那一项决定）。
    samples = []
    for block in blocks:
        if block['severe']:
            continue
        scored = {rid: normalized(rid, block['deltas'][rid]) for rid in metrics if rid in block['deltas']}
        scored = {rid: v for rid, v in scored.items() if v is not None}
        if not scored:
            continue
        if combine == 'mean':
            overall = sum(abs(v) for v in scored.values()) / len(scored)
        elif combine == 'weighted':
            total = sum(weights.get(rid, 1) for rid in scored) or 1
            overall = sum(abs(v) * weights.get(rid, 1) for rid, v in scored.items()) / total
        else:
            overall = max(abs(v) for v in scored.values())
        worst = max(scored, key=lambda rid: abs(scored[rid]))
        samples.append(dict(sample_code=block['sample_code'], project=block['project_name'], measured_on=block['measured_on'],
                            conditions_label=block['conditions_label'], overall=overall, combine=combine, worst=worst,
                            normalized=scored, deltas={rid: block['deltas'][rid] for rid in scored},
                            sources=[block['obs_id']]))
    samples.sort(key=lambda s: -s['overall'])
    for index, sample in enumerate(samples, 1):
        sample['rank'] = index

    # 7. 趋势：同一条件下至少 trend_min 个项目才说变小/变大，一律写成推断。
    trends = {}
    if group_by == 'project':
        for rid in metrics:
            # “同一条件”按条件签名分组；条件未知的块彼此算同一组（它们同样不能和已知条件的块比），
            # 但结论里要写明“多数块的烘烤条件未知”。
            same = defaultdict(list)
            for project in projects:
                if project['normalized'].get(rid) is not None:
                    same[project['conditions_label'] if project['conditions_known'] else '条件未知'].append(project)
            label, group = max(same.items(), key=lambda item: len(item[1])) if same else ('', [])
            group.sort(key=lambda p: (p['date'] or '', p['name']))
            # 趋势点同时给原值差和归一化值：任务书里的写法用的是原值差。
            series = [dict(project=p['name'], delta=p['deltas'][rid]['value'] if rid in p['deltas'] else None,
                           normalized=p['normalized'][rid]) for p in group]
            if len(group) < trend_min:
                trends[rid] = dict(direction='insufficient', points=series, needed=trend_min, group=label,
                                   text=f"{label}的项目只有 {len(group)} 个，少于 {trend_min} 个，不判断趋势")
                continue
            first, last = group[0], group[-1]
            start, end = abs(first['normalized'][rid]), abs(last['normalized'][rid])
            if end > start:
                direction = 'away'
                text = f"最近几轮 {_metric_label(rid)} 离参比越来越远"
            elif end < start:
                direction = 'closer'
                text = f"最近几轮 {_metric_label(rid)} 离参比越来越近"
            else:
                direction = 'flat'
                text = f"最近几轮 {_metric_label(rid)} 离参比的距离基本没变"
            unknown = label == '条件未知'
            trail = ('（推断；' + (f'{label}的' if label else '') + '这几轮树脂批和配方同时在变，不能归因'
                     + ('；多数块的烘烤条件未知' if unknown else '') + '）')
            trends[rid] = dict(direction=direction, points=series, text=text + trail, inference=True,
                               label=_metric_label(rid), unit=_unit(rid).strip(),
                               group=label, from_project=first['name'], to_project=last['name'],
                               start=first['normalized'][rid], end=last['normalized'][rid],
                               projects=len(group), conditions_known_projects=0 if unknown else len(group))

    # 内部用的中间字段不往外带（参比候选只在 service 的第二遍比较里用）。
    for project in projects:
        project.pop('reference_second_pass', None)
        project.pop('_waiting', None)

    # 逐块清单（画图/Excel“依据数据”/核对用；不含整行观测，不外带模块数据）。
    block_rows = [dict(obs_id=b['obs_id'], sample_code=b['sample_code'], project=b['project_name'],
                       conditions_label=b['conditions_label'], conditions_known=b['conditions'] is not None,
                       date=b['measured_on'], values=b['values'], deltas=b['deltas'], severe=b['severe'],
                       diluted=b['diluted'], flags=b['flags'], reference=dict(code=b['reference'].get('code'),
                       source=b['reference'].get('source'), status=b['reference'].get('status'),
                       date_diff_days=b['reference'].get('date_diff_days'),
                       condition_diffs=b['reference'].get('condition_diffs'))) for b in blocks]

    # 8. 最近一个项目：主攻哪一项。
    latest = projects[0] if projects else None
    return dict(series_id=series_id, params=params, algorithm=ALGORITHM, version=VERSION,
                reference=reference_info, metrics=[dict(id=rid, label=_metric_label(rid), unit=_unit(rid).strip()) for rid in metrics],
                dispersion=dispersion, normalization=normalization_info, combine=combine, group_by=group_by,
                projects=projects, blocks=block_rows, samples=samples, trend_min_projects=trend_min, trends=trends,
                latest=latest, severe_flags=list(severe_flags),
                warnings=warnings_list + _warnings(blocks, projects, latest, normalization_info, metrics),
                inputs=sorted(inputs or {b['obs_id'] for b in blocks}))


def _warnings(blocks, projects, latest, normalization_info, metrics):
    result = []
    if not blocks:
        result.append('没有可比的配对：对标卡还没有给出参比差值')
    missing = [rid for rid in metrics if normalization_info.get(rid, {}).get('mode') == 'unavailable']
    if missing:
        result.append('这些指标算不出离散度，只显示原值：' + '、'.join(_metric_label(rid) for rid in missing))
    severe = [b['sample_code'] for b in blocks if b['severe']]
    if severe:
        result.append('有严重质量标记、没进中位数的块：' + '、'.join(dict.fromkeys(severe)))
    unknown = [p['name'] for p in projects if not p['conditions_known']]
    if unknown:
        result.append('条件未知的项目单独列出：' + '、'.join(unknown))
    if latest and latest['severe_excluded'] and any(rid not in latest['deltas'] for rid in metrics):
        result.append(latest['name'] + ' 有指标只有严重质量标记的块，没有可用的中位数')
    return result


def _pending(project, members):
    """项目里还没差值的块：把挑好的参比列出来，交给调用方显式比较。"""
    pairs, waiting = [], []
    for block in members:
        fallback = block.get('fallback') or {}
        if fallback.get('source') not in (None, 'candidate'):  # 已经算出差值的块不重复
            continue
        key = fallback.get('group_key')
        if fallback.get('reference_key') and key:
            pairs.append(dict(candidate_group_key=key, group_key=key, reference_key=fallback['reference_key'],
                              observation_id=block['obs_id'], sample_id=block.get('sample_id')))
        else:
            waiting.append(block['obs_id'])
    if pairs:
        project['_reference_candidates'] = pairs
        project['reference_state'] = 'selected_fallback' if not project.get('reference_second_pass') else 'compared'
    elif any(block.get('entry') for block in members):
        project['reference_state'] = 'compared'
    elif waiting:
        project['_waiting'] = waiting
        requested = bool(project.get('reference_second_pass'))
        project['reference_state'] = 'awaiting_comparison' if requested else 'no_candidate'


def resolve_reference(benchmark, spec):
    """把“对标卡选定的那一次参比”从椭偏 options 里找出来。

    spec 可以是 reference_key，也可以是 {'label': 'REF01', 'bake_temp_c': 400, 'bake_time_s': 90}。
    找不到就返回 None（调用方退回推荐第 1 名，并标“参比未确认”）。
    """
    spec = spec or {}
    options = ((benchmark or {}).get('options') or {}).get('options') or []
    if spec.get('reference_key'):
        return next((o for o in options if o.get('reference_key') == spec['reference_key']), None)
    wanted = {k: v for k, v in spec.items() if k not in ('label', 'reference_key')}
    for option in options:
        label = option.get('label') or ''
        if spec.get('label') and spec['label'] not in label:
            continue
        conditions = option.get('conditions') or {}
        if all(conditions.get(key) == value for key, value in wanted.items()):
            return option
    return None


def reference_pairs(result):
    """每个用推荐第 1 名的对标比较组：(candidate_group_key) → {reference_key, group_key}。

    调用方拿它逐个调椭偏的显式只读比较，再把差值传回 rank(params.pairs=…)。
    """
    selected = {}
    for project in result.get('projects') or []:
        for entry in project.get('_reference_candidates') or []:
            if entry.get('reference_key') and entry.get('candidate_group_key'):
                selected[entry['candidate_group_key']] = dict(reference_key=entry['reference_key'],
                                                              group_key=entry.get('group_key') or entry['candidate_group_key'])
    return selected


def card(result):
    """一张“建议卡”（任务书 1.2）。"""
    reference = result['reference']
    latest = result['latest']
    warnings_list = result.get('warnings') or []
    if not result['projects'] or not latest:
        return dict(kind='gap_rank', title='离参比差距排序', algorithm=ALGORITHM, version=VERSION,
                    suggestion=None, facts=warnings_list, inferences=[], confidence=None,
                    insufficient=dict(reason='没有可比的配对：对标卡还没有给出参比差值', missing=['对标卡参比配对（椭偏参比接口）']),
                    actions=[dict(type='open_benchmark', label='去对标卡按推荐选参比')],
                    inputs=result['inputs'], params=result['params'])
    facts, inferences, reasons, codes = [], [], [], []
    main = latest['priority'][0] if latest['priority'] else None
    for rid in result['metrics']:
        rid = rid['id']
        if rid not in latest['scores']:
            facts.append(f"{latest['name']}：{_metric_label(rid)} 没有差值")
            continue
        delta = latest['scores'][rid]['delta']
        norm = latest['scores'][rid]['normalized']
        facts.append(f"{latest['name']}：{_metric_label(rid)} 中位数比参比 {_fmt(rid, delta, result['params'])}"
                     f"（归一化 {norm:+.2f}；{latest['scores'][rid]['label']}）")
    if main:
        suggestion = f"下一步主攻：{_metric_label(main)}（{latest['name']} 里 |归一化差距| 最大，{latest['normalized'][main]:+.2f}）。"
        others = [rid for rid in latest['priority'][1:]]
        if others:
            suggestion += '其次是 ' + '、'.join(f"{_metric_label(rid)}（{latest['normalized'][rid]:+.2f}）" for rid in others) + '。'
        inferences.append(f"{latest['name']} 里离参比最远的指标是{_metric_label(main)}")
    else:
        suggestion = '最近一个项目没有可用的差值，先补参比。'
    trend_lines = []
    for rid, trend in result['trends'].items():
        if trend.get('direction') not in ('away', 'closer', 'flat'):
            continue
        tail = trend['points'][-4:]
        trail = ' → '.join(f"{point['project'].replace('2026.', '').replace('2026', '')} {_fmt(rid, point['delta'], result['params'])}"
                            for point in tail)
        trend_lines.append(f"{_metric_label(rid)}：{trail}；" + trend['text'])
    facts.extend(trend_lines)
    if trend_lines:
        inferences.append('最近几轮的整体走向以 n@193 为主；趋势是推断，各轮树脂批和配方同时在变，不能归因')
    if reference['state'] == 'unconfirmed':
        codes.append('reference_unconfirmed')
        reasons.append('参比未确认：' + (reference.get('chosen_by') or '还对不上参比'))
    if any(len(p['samples']) < 2 for p in result['projects']):
        codes.append('single_film')
        reasons.append('部分项目每个条件只有 1 片膜')
    unknown = [p['name'] for p in result['projects'] if not p['conditions_known']]
    if unknown:
        codes.append('conditions_unknown')
        reasons.append('这些块的烘烤条件未知，只按“条件未知”单独一组：' + '、'.join(unknown))
    if latest['severe_excluded']:
        codes.append('severe_quality')
        reasons.append('有严重质量标记的块没进中位数：' + '、'.join(s['sample_code'] for s in latest['severe_excluded']))
    codes.append('cross_day')
    reasons.append('不同天的项目混在一起比较；参比不是同天同条件')
    actions = [dict(type='export_excel', sheets=['建议', '依据数据', '口径'])]
    if reference['state'] != 'confirmed':
        actions.insert(0, dict(type='choose_reference', label='去对标卡确认参比'))
    if latest.get('severe_excluded'):
        actions.append(dict(type='remeasure', samples=[s['sample_code'] for s in latest['severe_excluded']]))
    reference_code = reference.get('code')
    reference_display = '、'.join(reference_code) if isinstance(reference_code, list) else reference_code
    reference_display = reference_display or (f"已选 {reference['selected_count']} 个候选" if reference.get('selected_count') else reference['label'])
    return dict(kind='gap_rank', title=f"离参比差距（参比：{reference_display}）",
                suggestion=suggestion, facts=facts, inferences=inferences,
                confidence=dict(level=_confidence(codes), reasons=reasons),
                insufficient=None, actions=actions, inputs=result['inputs'], params=result['params'],
                algorithm=ALGORITHM, version=VERSION)


def excel(result, card_result):
    """“建议”“依据数据”“口径”三个表页。"""
    from io import BytesIO
    from openpyxl import Workbook
    book = Workbook()
    advisory = book.active; advisory.title = '建议'
    advisory.append(['项目', '内容'])
    for key, value in [('标题', card_result['title']), ('建议', card_result['suggestion']), ('置信度', (card_result.get('confidence') or {}).get('level')),
                       ('置信度理由', '；'.join((card_result.get('confidence') or {}).get('reasons') or [])),
                       ('数据不足', (card_result.get('insufficient') or {}).get('reason'))]:
        advisory.append([key, value or ''])
    advisory.append([])
    advisory.append(['事实', ''])
    for fact in card_result['facts']:
        advisory.append(['', fact])
    advisory.append(['推断', ''])
    for inference in card_result['inferences']:
        advisory.append(['', inference])
    data = book.create_sheet('依据数据')
    data.append(['项目', '条件', '日期', '样品', '指标', '样品值', '对参比 Δ', '归一化差距', '归一化口径', '用到的块数', '来源观测'])
    for project in result['projects']:
        for rid in result['metrics']:
            rid = rid['id']
            if rid not in project['deltas']:
                continue
            delta = project['deltas'][rid]
            data.append([project['name'], project['conditions_label'], project['date'], '、'.join(project['samples']),
                         _metric_label(rid), delta.get('sample_value'), delta['value'], project['normalized'].get(rid),
                         project['scores'][rid]['label'], delta['n'], '；'.join(delta['sources'])])
        for severe in project['severe_excluded']:
            data.append([project['name'], project['conditions_label'], project['date'], severe['sample_code'],
                         '（严重质量标记，没进中位数）', None, None, None, '；'.join(severe['flags']), 0, ''])
    # 样品综合排序放在“依据数据”页的下半段（任务书只要三个表页）。
    data.append([])
    data.append(['样品综合排序', '名次', '样品', '项目', '条件', '综合', '综合方式', '最差指标', '归一化差距', '来源观测'])
    for sample in result['samples']:
        data.append(['', sample['rank'], sample['sample_code'], sample['project'], sample['conditions_label'], sample['overall'],
                     sample['combine'], _metric_label(sample['worst']),
                     '；'.join(f"{_metric_label(rid)} {value:+.2f}" for rid, value in sample['normalized'].items()), '；'.join(sample['sources'])])
    scale = book.create_sheet('口径')
    scale.append(['项', '值', '说明'])
    scale.append(['参比', result['reference'].get('code') or '', result['reference']['label'] + '；' + (result['reference'].get('chosen_by') or '')])
    scale.append(['Δ 的定义', '样品 − 参比', '差值由对标卡（椭偏参比接口）给出'])
    for rid, info in result['normalization'].items():
        scale.append(['归一化：' + _metric_label(rid), info['mode'], info['label']])
    for rid, info in result['dispersion'].items():
        scale.append(['本系列离散度：' + _metric_label(rid), info['value'], f"MAD×1.4826，{info['blocks']} 个独立块，去掉 {info['excluded']} 个离群块"])
    scale.append(['综合方式', result['combine'], '默认取最差的一项（最大）'])
    scale.append(['分组', result['group_by'], '条件不同分开算，条件未知单独标'])
    scale.append(['趋势最少项目', result['trend_min_projects'], '同一条件下少于这个数不判断趋势'])
    scale.append(['严重质量标记', '、'.join(result['severe_flags']), '有这些标记的块不进中位数'])
    for warning in result['warnings']:
        scale.append(['提示', '', warning])
    stream = BytesIO(); book.save(stream)
    return stream.getvalue()
