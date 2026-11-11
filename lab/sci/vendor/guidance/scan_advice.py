"""下一轮扫描建议（A2-DSH · A）。

平台自己的新算法：读 A1a 的 ``scan``（默认按项目，即“当天那张工作表”分组）
和椭偏参比，回答用户每天要问的：

* 这一轮扫完了，下一轮配哪几个水平？
* 要不要先重测？
* 要不要继续往这个方向扫？

口径（默认值都可改，存在 analysis_choices 的 ``kind="guidance"``）：

1. 有效点：去掉严重质量标记后少于 3 个 → 数据不足；严重的点不删，标“先重测”。
2. 间隔类型：看现有水平是等差还是等比（零水平不参与判断）。
3. 关键区间：每个指标按方向找变化最大的相邻两点，或者跨过参比、容差线的那一段。
   损失率越小越好；n、k、膜厚越接近参比越好。
4. 加密：在关键区间里按同一种间隔加 ``points_to_add`` 个点（等比用比值^(1/(n+1))，
   等差用等分）。关键区间里变化小于 ``0.5×本系列离散度``（或用户容差）时不加密。
5. 外推：最好的点在端点上、趋势单调、还没到参比 → 往外再走一步；已经接近参比
   （差值小于离散度的 ``near_reference_factor`` 倍，或用户填的容差）→ 不再往外走。
6. 多个指标冲突时分别给建议，把冲突写进推断，不硬合成一个。
7. 拟合：A1a 的 ``_fit_scan`` 已经在点数 ≥ 参数个数 + 3 时调配方引擎；不够只连线。
8. 动作：“导出为下一轮 DOE 计划”和“先重测”。

本模块不配对、不算差值、不写库；参比值取观测表里那次参比测量的原值。
"""
from collections import defaultdict
import math
import re

from .settings import DEFAULTS

ALGORITHM = 'platform.scan_next'
VERSION = 1

METRIC_LABELS = {'n193': 'n@193', 'k193': 'k@193', 'thickness_nm': '膜厚',
                 'thickness_loss_pct@SolvA': 'SolvA 损失率'}
# 默认方向：损失率越小越好；其余越接近参比越好（用户可逐个改）。
DEFAULT_DIRECTIONS = {'ellipsometry:thickness_loss_pct@SolvA': 'min'}
# 严重质量标记：默认空（椭偏逐块质量标记 W12 才接得上），用户可加。
DEFAULT_SEVERE = []


def merge_params(choices, params):
    from .gap_ranking import merge_params as gap_merge
    return gap_merge(choices, params)


def _label(rid):
    return METRIC_LABELS.get(rid.split(':', 1)[-1], rid.split(':', 1)[-1])


def _level_text(value):
    """建议水平怎么显示：0.9、1.0、1.19、1.41、1.68（保留至少一位小数，去掉多余的零）。"""
    text = f"{value:.2f}".rstrip('0')
    return text + '0' if text.endswith('.') else text


def _unit(rid):
    if rid.endswith(('n193', 'k193')):
        return ''
    if rid.endswith('thickness_nm'):
        return ' nm'
    if rid.endswith('loss_pct@SolvA'):
        return ' %'
    return ''


def _digits(rid, params):
    digits = (params.get('display') or {}).get('digits') or {}
    fallback = 2 if rid.endswith('loss_pct@SolvA') else (1 if rid.endswith('thickness_nm') else 4)
    value = digits.get(rid, digits.get(rid.split(':', 1)[-1]))
    return int(value) if isinstance(value, (int, float)) else fallback


def _fmt(rid, value, params):
    if value is None:
        return '—'
    return f"{value:+.{_digits(rid, params)}f}{_unit(rid)}" if value < 0 else f"{value:.{_digits(rid, params)}f}{_unit(rid)}"


def _number(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def directions(params):
    result = dict(DEFAULT_DIRECTIONS)
    for rid, direction in (params.get('directions') or {}).items():
        if direction not in ('min', 'max', 'reference'):
            raise ValueError('指标方向必须是 min、max 或 reference')
        result[rid] = direction
    return result


def interval_type(levels, tolerance=0.12):
    """等差还是等比：相邻比值大致相等（±tolerance）就是等比；零水平不参与。"""
    positive = sorted(x for x in levels if isinstance(x, (int, float)) and x > 0)
    if len(positive) < 3:
        return 'arithmetic'
    ratios = [b / a for a, b in zip(positive, positive[1:])]
    if max(ratios) > 0 and (max(ratios) - min(ratios)) / max(ratios) <= tolerance and min(ratios) > 1.0:
        return 'geometric'
    return 'arithmetic'


def steps_between(low, high, kind, count):
    """关键区间里按同一种间隔加 count 个点（等比用比值^(1/(count+1))）。"""
    if kind == 'geometric' and low > 0 and high > 0:
        ratio = (high / low) ** (1 / (count + 1))
        return [round(low * ratio ** index, 4) for index in range(1, count + 1)]
    return [round(low + (high - low) * index / (count + 1), 4) for index in range(1, count + 1)]


def next_outside(low, high, kind):
    """往外再走一步（同一种间隔）：端点之外的下一个水平。"""
    if kind == 'geometric' and low > 0 and high > low > 0:
        return round(high * (high / low), 4)
    return round(high + (high - low), 4)


def observed_measurements(point):
    return [m for m in point.get('measurements') or [] if m.get('independent')]


def point_flags(point):
    flags = set()
    for row in point.get('measurements') or []:
        flags.update(row.get('flags') or [])
    return sorted(flags)


# 椭偏对标的逐块质量计数（模块自己的质量标记，平台只读不改）：抄错列、平均值不符、
# 膜厚离群、解析告警。>0 就值得先重测。
QUALITY_FIELDS = {'data_quality_warning_count': '数据质量告警', 'outlier_count': '膜厚离群',
                  'average_mismatch_count': '平均值不符（可能抄错列）', 'parse_warning_count': '解析告警',
                  'module_action_count': '椭偏待复测标记（未自动剔除）'}
SEVERE_QUALITY = ('average_mismatch_count', 'parse_warning_count', 'data_quality_warning_count')


def quality_issues(point, quality):
    counts = (quality or {}).get(point.get('code')) or {}
    return {field: counts[field] for field in QUALITY_FIELDS if isinstance(counts.get(field), (int, float)) and counts[field] > 0}


def quality_evidence(issues):
    return '；'.join(f"{QUALITY_FIELDS[field]} {int(count)} 处" for field, count in issues.items())


def _evidence(point, quality=None):
    """重测的理由：优先用椭偏给的质量标记，再用观测表里的离群依据。"""
    issues = quality_issues(point, quality)
    if issues:
        return '椭偏质量标记：' + quality_evidence(issues)
    for row in point.get('measurements') or []:
        for item in row.get('outlier_evidence') or []:
            return (f"{item.get('response_id')} = {item.get('value')}，同组中位数 {item.get('median')}，"
                    f"判据 {item.get('rule')}")
    flags = point_flags(point)
    return '平台标记：' + '、'.join(flags) if flags else None


def point_value(point, rid):
    value = (point.get('responses') or {}).get(rid, {}).get('value')
    return _number(value)


def lose_severe(points, severe_flags):
    """有严重质量标记的点：保留在结果里，标“先重测”。"""
    return [p for p in points if set(point_flags(p)) & set(severe_flags)]


def _goodness(value, direction, reference):
    if direction == 'min':
        return -value
    if direction == 'max':
        return value
    return -abs(value - reference)


def _distance(value, direction, reference):
    if direction == 'min':
        return value
    if direction == 'max':
        return -value
    return abs(value - reference)


def scan_card(scan, params, tolerances, series_id, dispersion, reference_values, index, quality=None):
    """一张扫描的建议卡。"""
    severe_flags = list(params.get('severe_flags') or DEFAULT_SEVERE)
    points = [p for p in scan.get('points') or [] if p.get('state') == 'measured' and p.get('x') is not None]
    awaiting = [p for p in scan.get('points') or [] if p.get('state') != 'measured']
    severe = lose_severe(points, severe_flags)
    # 椭偏给到平台的逐块质量标记：抄错列、平均值不符、解析告警算严重；膜厚离群算轻微。
    quality_severe = [p for p in points if p not in severe and set(quality_issues(p, quality)) & set(SEVERE_QUALITY)]
    severe = severe + quality_severe
    # “可能异常”的点要重测；但溶解块（处理后膜厚 0）是有效结果，不因为平台的离群标记
    # 就叫用户重测——它的损失率是“溶解即完全损失”的约定值，不是异常读数。
    flagged = [p for p in points if p not in severe and
               ('possible_outlier' in point_flags(p) or quality_issues(p, quality))
               and not _convention_value('ellipsometry:thickness_loss_pct@SolvA', p)]
    valid = [p for p in points if p not in severe]
    levels = sorted({p['x'] for p in valid})
    kind = interval_type(levels)
    facts, inferences, reasons, codes, actions = [], [], [], [], []
    metrics = list(params.get('metrics') or [])
    directions_map = directions(params)
    result = dict(scan_id=scan.get('scan_id'), factor=scan.get('factor'), group=scan.get('group'),
                  factor_label=_factor_label(scan.get('factor')), interval_type=kind, levels=levels,
                  points=[p['code'] for p in valid], awaiting=[p['code'] for p in awaiting],
                  remeasure=[dict(sample_code=p['code'], x=p['x'], flags=point_flags(p),
                                  quality=quality_issues(p, quality), evidence=_evidence(p, quality))
                             for p in severe + flagged],
                  metrics={}, corners=[], insufficient=None, conflicts=[], fit=scan.get('fit'),
                  fit_note=scan.get('fit_note'), different_days=scan.get('different_days'))
    if len(valid) < 3:
        # 同一天的两条臂（例如 T24 的 DMAP、AddH）合起来列要测的配方，不按臂分开重复。
        missing = sorted({p['code'] for p in awaiting} | {p['code'] for p in severe}
                         | set(scan.get('_family_awaiting') or []))
        reason = f"有效点只有 {len(valid)} 个（去掉严重质量标记后），少于 3 个"
        result['insufficient'] = dict(reason=reason, missing=['补测扫描点'], points=missing,
                                      detail='还没测的配方：' + '、'.join(missing) if missing else None)
        facts.append(f"{scan.get('group')} 这条扫描{reason}")
        if missing:
            facts.append('还没测的配方（' + str(len(missing)) + ' 个）：' + '、'.join(missing))
        card = dict(kind='scan_next', title=f"{_group_title(scan)} 下一轮建议",
                    suggestion=('数据不足：先测这 ' + str(len(missing)) + ' 个配方（' + '、'.join(missing) + '），测完再谈下一轮。')
                               if missing else '数据不足，先补测再谈下一轮。',
                    facts=facts, inferences=[], confidence=None, insufficient=result['insufficient'],
                    actions=[dict(type='measure', samples=missing)] if missing else [],
                    inputs=[m['obs_id'] for p in points for m in observed_measurements(p)],
                    params=params, algorithm=ALGORITHM, version=VERSION)
        return result, card

    reference_map = reference_values or {}
    for rid in metrics:
        direction = directions_map.get(rid, 'reference')
        reference = _number(reference_map.get(rid))
        values = [(p, point_value(p, rid)) for p in valid]
        values = [(p, v) for p, v in values if v is not None]
        if len(values) < 2:
            reason = '没测处理后数据' if rid.endswith('loss_pct@SolvA') else '这个指标没有足够的实测值'
            result['metrics'][rid] = dict(state='insufficient', reason=reason)
            facts.append(f"{_label(rid)}：数据不足（{reason}）")
            continue
        if direction == 'reference' and reference is None:
            result['metrics'][rid] = dict(state='needs_reference', reason='还没有参比值，只显示点',
                                          values=[dict(code=p['code'], x=p['x'], value=v) for p, v in values])
            continue
        reference = 0.0 if direction == 'min' else reference
        scale = _scale(rid, params, tolerances, dispersion)
        # 关键区间：变化最大的相邻两点（溶解块的约定值不算“变化”，也不跨过参比线时优先）。
        pairs = []
        for (left, lv), (right, rv) in zip(values, values[1:]):
            if _convention_value(rid, left) or _convention_value(rid, right):
                continue
            change = abs(_distance(rv, direction, reference) - _distance(lv, direction, reference))
            pairs.append(dict(low=left['x'], high=right['x'], change=change, low_value=lv, high_value=rv,
                              codes=[left['code'], right['code']]))
        if not pairs:
            reason = ('没测处理后数据（这个项目只有溶解块，没有损失率原值）' if rid.endswith('loss_pct@SolvA')
                      else '没有可用于判断变化的相邻两点（只有溶解等约定值）')
            result['metrics'][rid] = dict(state='insufficient', reason=reason)
            facts.append(f"{_label(rid)}：数据不足（{reason}）")
            continue
        key = max(pairs, key=lambda pair: pair['change'])
        entry = dict(direction=direction, reference=reference, scale=scale, key=key,
                     change=key['change'], no_change=key['change'] < 0.5 * scale if scale else False)
        result['metrics'][rid] = entry
        # 最优点、单调性和“离参比够不够近”先记下来；外推放到所有指标都算完之后再定，
        # 这样“只有变化最大的那个指标才提外推”用的是完整的一组指标。
        ordered = values
        distances = [_distance(v, direction, reference) for _, v in ordered]
        best_index = min(range(len(ordered)), key=lambda i: distances[i])
        monotone = all(b <= a for a, b in zip(distances, distances[1:])) or all(b >= a for a, b in zip(distances, distances[1:]))
        entry.update(best=ordered[best_index][0]['code'], best_x=ordered[best_index][0]['x'],
                     best_distance=distances[best_index], monotone=monotone, best_index=best_index,
                     ordered_x=[p['x'] for p, _ in ordered],
                     near_reference=_near(distances[best_index], scale, params) or entry['no_change'])
        if entry['no_change']:
            facts.append(f"{_label(rid)} 在整条扫描里只变 {_fmt(rid, max(v for _, v in values) - min(v for _, v in values), params)}，"
                         f"小于 0.5×离散度 {0.5 * scale:.4g}，不加密")
            continue
        low, high = key['low'], key['high']
        added = steps_between(low, high, kind, int(params.get('points_to_add', 3)))
        entry['add'] = added
        facts.append(f"{_label(rid)}：{low:g} → {high:g} 这一段变化最大（{_fmt(rid, key['low_value'], params)} → "
                     f"{_fmt(rid, key['high_value'], params)}），按{'等比' if kind == 'geometric' else '等差'}"
                     f"在 {low:g}～{high:g} 之间加 {len(added)} 个点：" + '、'.join(_level_text(value) for value in added))
        # 转折（推断，用“可能/看起来”）。
        if direction == 'min':
            if key['low_value'] > key['high_value']:
                inferences.append(f"看起来 {_label(rid)} 的转折在 {low:g}～{high:g} 之间：{low:g} 时 {_fmt(rid, key['low_value'], params)}，"
                                  f"{high:g} 时已经降到 {_fmt(rid, key['high_value'], params)}")
            else:
                inferences.append(f"看起来 {_label(rid)} 从 {low:g} 到 {high:g} 变差（{_fmt(rid, key['low_value'], params)} → "
                                  f"{_fmt(rid, key['high_value'], params)}），上限可能在 {low:g}～{high:g} 之间")
        # 跨过参比 / 容差线的那一段。
        if direction == 'reference' and min(key['low_value'], key['high_value']) <= reference <= max(key['low_value'], key['high_value']):
            entry['crosses_reference'] = True
            inferences.append(f"{_label(rid)} 的参比落在 {low:g}～{high:g} 这一段里，转折可能在这里")
    # 外推 / 不再往外走：只对“变化最大”的那个指标提一次。
    scored = [(rid, entry) for rid, entry in result['metrics'].items() if isinstance(entry, dict) and entry.get('change')]
    primary = max(scored, key=lambda item: item[1]['change'])[0] if scored else None
    for rid, entry in scored:
        ordered = result['metrics'][rid]['ordered_x']
        best_index = entry['best_index']
        if rid == primary and best_index == len(ordered) - 1 and entry['monotone'] and not entry['near_reference']:
            outside = next_outside(ordered[-2], ordered[-1], kind)
            entry['extend'] = outside
            facts.append(f"{_label(rid)} 最好的点在 {ordered[-1]:g}（端点上），还没到参比，可以往外再走一步：{outside:g}")
        elif best_index in (0, len(ordered) - 1) and entry['near_reference']:
            entry['stop'] = True
            entry['stop_x'] = ordered[best_index]
            facts.append(f"{_label(rid)} 最好的点 {entry['best']} 离参比只有 {_fmt(rid, entry['best_distance'], params)}"
                         f"（小于 {float(params.get('near_reference_factor', 0.5)):g}×离散度 {(entry.get('scale') or 0):.4g}），不必再往外走")
    # 内部用过的中间字段不往外带。
    for entry in result['metrics'].values():
        for key in ('best_index', 'ordered_x', 'monotone', 'no_change', 'crosses_reference'):
            entry.pop(key, None)

    # 关掉点的重测动作。
    if severe:
        others = [p['code'] for p in severe]
        actions.append(dict(type='remeasure', samples=others,
                            reason='平台标了严重质量标记'))
    if flagged:
        actions.append(dict(type='remeasure', samples=[p['code'] for p in flagged],
                            reason='平台标了“可能异常”，建议先重测这一个点'))
    if result['remeasure']:
        facts.append('先重测：' + '、'.join(item['sample_code'] for item in result['remeasure']))
    # 加密 / 外推动作。
    additions = {rid: entry['add'] for rid, entry in result['metrics'].items() if entry.get('add')}
    extensions = {rid: entry['extend'] for rid, entry in result['metrics'].items() if entry.get('extend')}
    if additions:
        first = next(iter(additions.values()))
        actions.append(dict(type='export_doe_plan', variable=result['factor'], levels=first,
                            note='一个变量、建议的水平；用 W7 的配方计划文件导出'))
    if extensions:
        actions.append(dict(type='extend_scan', variable=result['factor'], levels=list(extensions.values())))
    # 指标冲突：一个变好、另一个离参比更远。
    for left in result['metrics']:
        for right in result['metrics']:
            if left >= right or 'key' not in result['metrics'][left] or 'key' not in result['metrics'][right]:
                continue
            left_entry, right_entry = result['metrics'][left], result['metrics'][right]
            left_improves = _distance(left_entry['key']['high_value'], left_entry['direction'], left_entry['reference']) < \
                _distance(left_entry['key']['low_value'], left_entry['direction'], left_entry['reference'])
            right_worse = _distance(right_entry['key']['high_value'], right_entry['direction'], right_entry['reference']) > \
                _distance(right_entry['key']['low_value'], right_entry['direction'], right_entry['reference'])
            if left_improves and right_worse:
                message = f"{_label(left)} 往 {left_entry['key']['high']:g} 方向变好，{_label(right)} 却离参比更远；两个指标的走向冲突"
                result['conflicts'].append(message)
                inferences.append(message)
    # 整条扫描都解决不了的差距：某些指标在扫描里几乎没变，离参比又还远。
    for rid, entry in result['metrics'].items():
        if entry.get('state') or entry.get('reference') is None:
            continue
        values = [v for _, v in [(p, point_value(p, rid)) for p in valid] if v is not None]
        if len(values) < 2:
            continue
        span = max(values) - min(values)
        gap = min(abs(entry['reference'] - v) for v in values)
        if span < 0.5 * (entry.get('scale') or 0) and gap > span:
            inferences.append(f"{_label(rid)} 在整条扫描里只变 {_fmt(rid, span, params)}，这个扫描解决不了 "
                              f"{_label(rid)} 的差距（参比 {entry['reference']:.{_digits(rid, params)}f}，"
                              f"还差约 {_fmt(rid, gap, params)}）")
    # 星形：两条臂共用一个中心 → 补四个角点。
    result['corners'] = scan.get('_corners') or []
    if result['corners']:
        shown = '、'.join(' / '.join(f"{value:g}" for value in corner['factors'].values()) for corner in result['corners'])
        facts.append('这是星形：两条臂共用一个中心；要估两者的相互影响，补 4 个角点：' + shown)
        actions.append(dict(type='export_doe_plan', factors=[corner['factors'] for corner in result['corners']],
                            note='星形补四个角点（两个因子各取高低水平）'))
    # 全扫描都解决不了的差距：某些指标在扫描里几乎没变、离参比又还远。
    for rid, entry in result['metrics'].items():
        if entry.get('state') or entry.get('reference') is None:
            continue
        values = [v for _, v in [(p, point_value(p, rid)) for p in valid] if v is not None]
        if len(values) < 2:
            continue
        span = max(values) - min(values)
        gap = min(abs(entry['reference'] - v) for v in values)
        if span < 0.5 * (entry.get('scale') or 0) and gap > span:
            inferences.append(f"{_label(rid)} 在整条扫描里只变 {_fmt(rid, span, params)}，这个扫描解决不了 "
                              f"{_label(rid)} 的差距（参比 {entry['reference']:.{_digits(rid, params)}f}，"
                              f"还差约 {_fmt(rid, gap, params)}）")
    # 置信度（1.2 的默认规则）。
    if valid and all(p.get('x') is not None for p in valid):
        codes.append('one_factor')
    if not scan.get('different_days'):
        codes.append('same_day')
    else:
        codes.append('cross_day')
    if any(not observed_measurements(p) for p in points):
        codes.append('missing_measurement')
    if any(len(observed_measurements(p)) < 2 for p in valid):
        codes.append('single_film')
    if severe:
        codes.append('severe_quality')
    if flagged:
        codes.append('quality_flag')
    # 烘烤条件未知，但整条扫描是同一批未知条件：按“中”（和 1.2 的默认规则一致：
    # “条件不全”指跨条件比较或不同天，不是同一张表里的同一个未知条件）。
    signatures = {(row.get('conditions', {}).get('bake_temp_c'), row.get('conditions', {}).get('bake_time_s'))
                  for p in valid for row in observed_measurements(p)}
    if len(signatures) > 1:
        codes.append('conditions_differ')
    elif signatures and any(value is None for value in next(iter(signatures))):
        codes.append('same_unknown_conditions')
    level = _confidence(codes)
    suggestion = _suggestion(result, params, level)
    facts.extend(_level_facts(valid, result, params))
    card = dict(kind='scan_next', title=f"{_group_title(scan)} 下一轮建议", suggestion=suggestion,
                facts=facts, inferences=_unique(inferences), confidence=dict(level=level, reasons=_reasons(codes)),
                insufficient=None, actions=actions,
                inputs=sorted({m['obs_id'] for p in points for m in observed_measurements(p)}),
                params=params, algorithm=ALGORITHM, version=VERSION)
    return result, card


def _unique(values):
    seen, result = set(), []
    for value in values:
        if value not in seen:
            seen.add(value); result.append(value)
    return result


def _factor_label(factor):
    if not factor:
        return '扫描变量'
    name = factor.split(':', 1)[-1]
    name = re.sub(r':resin_solid_pct$', '', name)
    name = re.sub(r'\.resin_solid_pct$', '', name)
    return name


def _group_title(scan):
    group = str(scan.get('group') or '')
    if group.startswith('ellipsometry:project:'):
        return group.replace('ellipsometry:project:', '项目 ')
    return group or '这条扫描'


def _near(distance, scale, params):
    """离参比“已经够近”：差值小于 near_reference_factor × 离散度（或用户容差）。"""
    factor = float((params or {}).get('near_reference_factor', 0.5))
    return scale is not None and distance <= factor * scale


def _scale(rid, params, tolerances, dispersion):
    tolerance = (tolerances or {}).get(rid)
    if isinstance(tolerance, (int, float)) and tolerance > 0:
        return float(tolerance)
    value = (dispersion or {}).get(rid)
    if isinstance(value, dict):
        value = value.get('value')
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def _convention_value(rid, point):
    """溶解块记成 100 % 的损失率不是实测值：不拿来判断“变化最大”。"""
    if not rid.endswith('loss_pct@SolvA'):
        return False
    for row in point.get('measurements') or []:
        if (row.get('post_treatment') or {}).get('dissolved'):
            return True
    return False


def _confidence(codes):
    codes = set(codes)
    if codes & {'cross_day', 'conditions_differ', 'conditions_unknown', 'severe_quality', 'missing_measurement'}:
        return '低'
    if codes & {'single_film', 'quality_flag', 'same_unknown_conditions'}:
        return '中'
    return '高'


def _reasons(codes):
    mapping = {'cross_day': '跨天', 'conditions_unknown': '烘烤条件不全', 'conditions_differ': '各水平的烘烤条件不同',
               'severity': '有关键点带严重质量标记', 'severe_quality': '有关键点带严重质量标记',
               'missing_measurement': '有水平没有测量', 'single_film': '每个水平只有 1 片膜',
               'quality_flag': '有“可能异常”标记的点', 'one_factor': '只差一个因子', 'same_day': '同一天',
               'same_unknown_conditions': '烘烤条件未知（同一扫描内相同）'}
    return [mapping[code] for code in codes if code in mapping]


def _level_facts(points, result, params):
    """每个指标在各水平上的原值（事实，能点回来源）。"""
    facts = []
    for rid, entry in result['metrics'].items():
        if entry.get('state'):
            continue
        values = [(p, point_value(p, rid)) for p in points]
        values = [(p, v) for p, v in values if v is not None]
        if not values:
            continue
        shown = ['溶解' if _convention_value(rid, p) else f"{v:.{_digits(rid, params)}f}" for p, v in values]
        facts.append(f"{_label(rid)}：" + '；'.join(f"{p['x']:g} → {text}" for (p, _), text in zip(values, shown)))
    return facts


def _suggestion(result, params, level):
    remeasure = [item['sample_code'] for item in result['remeasure']]
    parts = []
    if remeasure:
        parts.append('先重测 ' + '、'.join(remeasure))
    # 多个指标指向同一段时合并成一句，不重复列同一组水平。
    grouped = {}
    for rid, entry in result['metrics'].items():
        if not entry.get('add'):
            continue
        signature = (round(entry['key']['low'], 6), round(entry['key']['high'], 6), tuple(entry['add']))
        grouped.setdefault(signature, []).append(_label(rid))
    for (low, high, added), labels in grouped.items():
        parts.append(f"在 {low:g}～{high:g} 之间加 " + '、'.join(_level_text(value) for value in added) +
                     f"（{'、'.join(labels)}；{'等比' if result['interval_type'] == 'geometric' else '等差'}步长）")
    stopped = {}
    for rid, entry in result['metrics'].items():
        if entry.get('stop') and entry.get('stop_x') is not None:
            stopped.setdefault(entry['stop_x'], []).append(_label(rid))
    for level, labels in stopped.items():
        parts.append(f"{level:g} 已经接近参比（{'、'.join(labels)}），不必再往上加")
    if not grouped and not remeasure:
        parts.append('不加密：这条扫描里没有值得加密的变化')
    return '；'.join(parts) + '。'


def corners_for(family):
    """星形：同组两条臂共用一个中心 → 两个因子各取高低水平的 4 个角点。"""
    arms = [scan for scan in family
            if sum(1 for p in scan.get('points') or [] if p.get('x') is not None and p.get('state') == 'measured') >= 2]
    if len(arms) != 2:
        return []
    (left, right) = arms
    left_levels = sorted({p['x'] for p in left['points'] if p.get('x') is not None})
    right_levels = sorted({p['x'] for p in right['points'] if p.get('x') is not None})
    left_codes = {p['code'] for p in left['points']}
    right_codes = {p['code'] for p in right['points']}
    if not (left_codes & right_codes):
        return []  # 两条臂没有共用的中心，不算星形
    # 水平的量级大的那条臂放前面（TMOM-BP 15/25 在前、CHTS 1/4 在后），
    # 顺序就是 15/1、15/4、25/1、25/4。
    if max(left_levels) < max(right_levels):
        left, right = right, left
        left_levels, right_levels = right_levels, left_levels
    corners = []
    for first in (left_levels[0], left_levels[-1]):
        for second in (right_levels[0], right_levels[-1]):
            corners.append(dict(factors={_factor_label(left.get('factor')): first,
                                         _factor_label(right.get('factor')): second}))
    return corners


def advise(scans, params=None, tolerances=None, series_id=None, dispersion=None, reference_values=None, quality=None, fit=True):
    params = {**{k: v for k, v in DEFAULTS['guidance'].items()}, **(params or {})}
    dispersion = dispersion or {}
    reference_values = reference_values or {}
    # 星形检测：按 A1a 的分组把臂放在一起看。
    families = defaultdict(list)
    for scan in scans:
        families[scan.get('group')].append(scan)
    corners, awaiting = {}, {}
    for group, arms in families.items():
        awaiting[group] = sorted({p['code'] for arm in arms for p in arm.get('points') or []
                                  if p.get('state') != 'measured'})
        if len(arms) != 2:
            continue
        found = corners_for(arms)
        for arm in arms:
            corners[arm.get('scan_id')] = found
    results, cards = [], []
    for index, scan in enumerate(scans):
        scan = {**scan, '_corners': corners.get(scan.get('scan_id')) or [],
                '_family_awaiting': awaiting.get(scan.get('group')) or []}
        result, card = scan_card(scan, params, tolerances, series_id, dispersion, reference_values, index, quality)
        result['corners'] = scan['_corners']
        results.append(result); cards.append(card)
    return dict(algorithm=ALGORITHM, version=VERSION, series_id=series_id, params=params, scans=results, cards=cards,
                dispersion=dispersion, reference_values=reference_values,
                inputs=sorted({value for result in results for value in result.get('points', [])}))


def card(result):
    """整体一张建议卡（多条扫描时把最重要的放前面）。"""
    cards = result.get('cards') or []
    actionable = [c for c in cards if c.get('suggestion') and '数据不足' not in c['suggestion']]
    chosen = actionable or cards
    if not chosen:
        return dict(kind='scan_next', title='下一轮扫描建议', algorithm=ALGORITHM, version=VERSION,
                    suggestion=None, facts=['没有识别到扫描'], inferences=[], confidence=None,
                    insufficient=dict(reason='没有识别到扫描', missing=['按项目分组的扫描']), actions=[], params=result['params'])
    head = chosen[0]
    return dict(kind='scan_next', title=head['title'], suggestion=head['suggestion'], facts=head['facts'],
                inferences=head['inferences'], confidence=head['confidence'], insufficient=head['insufficient'],
                actions=head['actions'], inputs=head['inputs'], params=head['params'],
                all_cards=cards, algorithm=ALGORITHM, version=VERSION)


def excel(result, card_result):
    """“建议”“依据数据”“口径”三个表页。"""
    from io import BytesIO
    from openpyxl import Workbook
    book = Workbook()
    advisory = book.active; advisory.title = '建议'
    advisory.append(['项目', '内容'])
    for card_item in [card_result] + list((card_result.get('all_cards') or [])[1:]):
        advisory.append([card_item['title'], card_item.get('suggestion') or ''])
        advisory.append(['  置信度', (card_item.get('confidence') or {}).get('level') or ''])
        advisory.append(['  理由', '；'.join((card_item.get('confidence') or {}).get('reasons') or [])])
        for fact in card_item.get('facts') or []:
            advisory.append(['  事实', fact])
        for inference in card_item.get('inferences') or []:
            advisory.append(['  推断', inference])
        if card_item.get('insufficient'):
            advisory.append(['  数据不足', card_item['insufficient'].get('reason')])
        advisory.append([])
    data = book.create_sheet('依据数据')
    data.append(['扫描', '变量', '间隔', '水平', '指标', '方向', '关键区间低', '关键区间高', '变化', '加密点', '外推', '最优点', '离参比'])
    for scan in result.get('scans') or []:
        for rid, entry in (scan.get('metrics') or {}).items():
            data.append([scan.get('group'), scan.get('factor_label'), scan.get('interval_type'),
                         '、'.join(f"{level:g}" for level in scan.get('levels') or []), _label(rid),
                         entry.get('direction'), (entry.get('key') or {}).get('low'), (entry.get('key') or {}).get('high'),
                         entry.get('change'), '、'.join(f"{v:g}" for v in entry.get('add') or []),
                         entry.get('extend'), entry.get('best'), entry.get('best_distance')])
    scale = book.create_sheet('口径')
    scale.append(['项', '值', '说明'])
    for rid, direction in directions(result.get('params') or {}).items():
        scale.append(['方向：' + _label(rid), direction, 'min 越小越好；reference 越接近参比越好'])
    scale.append(['加密点数', (result.get('params') or {}).get('points_to_add'), '关键区间里加几个点'])
    scale.append(['间隔类型', (result.get('params') or {}).get('interval_type'), 'auto 按相邻比值判断等差/等比'])
    scale.append(['接近参比', (result.get('params') or {}).get('near_reference_factor'), '差值小于这个倍数×离散度就不再外推'])
    scale.append(['严重质量标记', '、'.join((result.get('params') or {}).get('severe_flags') or []) or '（默认无）', '这些标记的点不进有效点，先重测'])
    for rid, info in (result.get('dispersion') or {}).items():
        scale.append(['本系列离散度：' + _label(rid), info.get('value'), 'MAD×1.4826（和差距排序同一份）'])
    stream = BytesIO(); book.save(stream)
    return stream.getvalue()
