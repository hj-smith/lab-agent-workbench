"""GPC 分子量：由库里存的基线锚点 + 积分区间 + 标准曲线算 Mn、Mw、Mz、Mz+1、PDI、Mp、D10/D50/D90、面积、外推占比和分布曲线。

算法是旧 GPC 模块原件 gpcsec 0.28.7（提交 5e5aff1）的 core + models，原样放在 lab/sci/vendor/gpcsec/，一行没改。
积分固定 exact-boundary-trapezoid-v2（原件只有这一个）；标准曲线用库里存的系数，不重新拟合（照 advanced_analysis.calibration_from_record）。
由已存分析求值的那条链照 analysis_session.applied_result + advanced_analysis.calculate_sample_molecular_weight；
面积和外推占比照 analysis_branching._snapshot；分布抽点照 web/capabilities.mw_distribution（≤200 点，除以 total_mass_rt）。

给智能体用的函数（输入记录 id 或名字，输出 JSON 能表示的字典）：
    analyze(ref, lib=None, 锚点=None, 积分区间=None, 标准曲线=None, save=True)
        算一个 GPC样品 的分子量。可临时换锚点 [[t min, 信号]…]、积分区间（同字段格式）或标准曲线（id/名字）试算，换了就不存。
        → {样品, 标准曲线, Mn, Mw, Mz, Mz+1, PDI, Mp, Mp_峰高, D10, D50, D90, 面积, 并集面积, 外推面积占比, 单位, 提示}
    distribution(ref, lib=None, save=True)
        分子量分布 dw/dlogM（≤200 点，按整条分布的 total_mass_rt 归一，面积≈1）。
        → {样品, logM: [...], dw/dlogM: [...], M: [...], 累积质量分数: [...], 归一除数, 抽点说明}
    refit_calibration(ref, lib=None, degree=None)
        用标样点（启用的）重拟合多项式标准曲线，和存的系数比。→ {系数(存), 系数(重拟合), 最大差, R², RMSE, 有效范围}
    experiment_trend(ref, lib=None, metric='Mw')
        一个 GPC实验 的成员按反应时间排好，带各自结果。→ {实验, 指标, 点: [{样品, 时间, 单位, 重复, 纳入, 值}]}
    read_instrument_file(path)
        读仪器导出文件（ChemStation CSV/TXT、xlsx、NetCDF、zip、Cirrus 标准曲线 PDF）。
        → 谱图 {类型:'色谱', 时间 / min, 信号, 格式, 提示} 或标准曲线 {类型:'标准曲线', 标样点, 次数, 系数…}；zip 给列表
    compute_all(lib=None, report=print)
        把库里所有有分析的 GPC样品 都算一遍（分子量 + 分布），存进 results。→ 计数

结果存法：set_result(样品 id, 'GPC分子量', 值, 参数, 版本)、set_result(样品 id, 'GPC分布', …)。
"""
import math
import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / 'vendor'
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))                            # 原件用绝对导入 gpcsec.xxx，所以把 vendor 放进路径

import numpy as np                                             # noqa: E402

import gpcsec                                                  # noqa: E402
from gpcsec.core.baseline import estimate_baseline, interpolated_anchor_baseline, linear_anchor_baseline  # noqa: E402
from gpcsec.core.calibration.models import PchipCalibration, PolynomialCalibration, fit_polynomial  # noqa: E402
from gpcsec.core.distributions import build_distribution     # noqa: E402
from gpcsec.core.integration import integrate_regions, summarize_integration_areas  # noqa: E402
from gpcsec.core.molecular_weight import calculate_molecular_weight, select_calibrated_segments  # noqa: E402
from gpcsec.models import (AutoBaselineSpec, BaselineAlgorithm, CalibrationPoint, CalibrationStatistics,  # noqa: E402
                           IntegrationRegion, PeakPolarity, RangePolicy)

from lab import store                                          # noqa: E402

VERSION = f'gpcsec {gpcsec.__version__} (5e5aff1) / exact-boundary-trapezoid-v2'
MAX_POINTS = 200
POLARITY = {'正': 'positive', '负': 'negative'}
POLICY = {'外推': 'expert_extrapolate', '只算曲线范围内': 'in_range_only', '超范围报错': 'reject'}
UNITS = {'Mn': 'g/mol', 'Mw': 'g/mol', 'Mz': 'g/mol', 'Mz+1': 'g/mol', 'PDI': '1', 'Mp': 'g/mol', 'Mp_峰高': 'g/mol',
         'D10': 'g/mol', 'D50': 'g/mol', 'D90': 'g/mol', '面积': '信号·min', '并集面积': '信号·min', '外推面积占比': '1'}


def _lib(lib):
    return lib if hasattr(lib, 'get') else store.open(lib or '产品A')


# ---------- 从库里的字段拼回原件的对象 ----------

def calibration(record):
    """GPC标准曲线 记录（get 的结果）→ 原件的校准对象，用存的系数（同 calibration_from_record）。"""
    data = record['data']
    if str(data.get('模型', 'polynomial')).casefold() == 'pchip':
        points = sorted((row for row in record['_points'] if row[2]), key=lambda row: row[0])
        return PchipCalibration(tuple(row[0] for row in points), tuple(math.log10(row[1]) for row in points),
                                data['有效范围起'], data['有效范围止'], data['单调方向'], data.get('来源sha256'),
                                data['算法版本'])
    stats = data.get('拟合统计') or None
    return PolynomialCalibration(tuple(data['系数']), tuple(data['缩放系数']), (data['有效范围起'], data['有效范围止']),
                                 data['有效范围起'], data['有效范围止'],
                                 CalibrationStatistics(**stats) if stats else None, data['单调方向'], data.get('来源sha256'),
                                 data['算法版本'])


def _calibration_record(lib, ref):
    record = lib.get(ref, 'GPC标准曲线', with_series=True)
    record['_points'] = record['series']['标样点']['rows'] if '标样点' in record['series'] else []
    return record


def regions(items):
    """字段 积分区间 → 原件的 IntegrationRegion。"""
    return tuple(IntegrationRegion(float(item['起']), float(item['止']), PeakPolarity(POLARITY.get(item.get('峰方向', '正'), item.get('峰方向'))),
                                   bool(item.get('计入分子量', True)),
                                   RangePolicy(POLICY.get(item.get('范围策略', '只算曲线范围内'), item.get('范围策略'))),
                                   item.get('来源') or 'manual', {'name': item['名称']} if item.get('名称') else {})
                 for item in items)


def baseline(time, signal, data, anchors=None):
    """照 analysis_session.applied_result 算基线。"""
    method = data.get('基线方法') or 'pchip_anchors'
    if method in {'linear_anchors', 'pchip_anchors', 'cubic_spline_anchors'}:
        points = tuple((float(t), float(v)) for t, v in (anchors if anchors is not None else data['基线锚点']))
        if method == 'linear_anchors':
            return linear_anchor_baseline(time, points)
        return interpolated_anchor_baseline(time, points, method=method)
    return estimate_baseline(time, signal, AutoBaselineSpec(BaselineAlgorithm(method), data.get('基线参数') or {},
                                                            tuple(tuple(item) for item in data.get('基线排除区') or ()))).result


VALLEY_HALF = 0.3        # 看锚点左右各 0.3 min
VALLEY_SHARE = 0.03      # 锚点处的谱比这段里的谷底高出积分区内峰高的 3 % 以上，算落在峰上（坡上）


def snap_anchors(time, signal, anchors, regions):
    """套模板、抄别的样品时把锚点贴到这条谱上（信号取这条谱在锚点时间的值），和手调时一样。
    落在峰上（坡上）的挪到左右 0.3 min 内的谷底再贴：人手调时锚点放在峰脚和谷底，模板带来的时间落到别的样品的峰上，原地贴会把基线抬进峰里。
    落在峰上 = 锚点处的谱比这段里的谷底高出积分区内峰高的 3 %。用人调过的 283 个样试过只误报 8 个，R100-29-1H~4H、R100-34-MEOH-PC2 都认出来了。
    返回 (锚点, 挪过的 [{从, 到, 高出 %}])。"""
    t, s = np.asarray(time, dtype=np.float64), np.asarray(signal, dtype=np.float64)
    inside = np.zeros(len(t), bool)
    for item in regions or []:
        inside |= (t >= float(item['起'])) & (t <= float(item['止']))
    height = float(np.ptp(s[inside] if inside.any() else s)) or 1.0
    out, moved = {}, []
    for a in anchors:
        x = float(a[0])
        here = float(np.interp(x, t, s))
        near = (t >= x - VALLEY_HALF) & (t <= x + VALLEY_HALF)
        if near.any() and here - float(s[near].min()) > VALLEY_SHARE * height:
            valley = float(t[near][np.argmin(s[near])])
            moved.append({'从': round(x, 2), '到': round(valley, 2), '高出': round((here - float(s[near].min())) / height * 100, 1)})
            x = valley
        out.setdefault(round(x, 6), [x, float(np.interp(x, t, s))])     # 挪到和别的锚点同一处时只留一个
    return sorted(out.values()), moved


def _trace(lib, record_id):
    columns, rows, _ = lib.table(record_id, '色谱')
    array = np.asarray(rows, dtype=np.float64)
    return tuple(array[:, 0].tolist()), tuple(array[:, 1].tolist())


def _prepare(lib, ref, anchors=None, region_items=None, curve=None, method=None):
    sample = lib.get(ref, 'GPC样品')
    data = {**sample['data'], **({'基线方法': method} if method else {})}      # 试算时可以换基线连接方式
    if not (data.get('积分区间') or region_items) or not (data.get('基线方法') or anchors):
        raise store.StoreError(f'{sample["name"]} 没有已保存的正式分析（基线 + 积分区间）。')
    curve_ref = curve or data.get('标准曲线id')
    if not curve_ref:
        raise store.StoreError(f'{sample["name"]} 没有绑定标准曲线。给 标准曲线=… 试算，或在字段 标准曲线id 里绑定。')
    time, signal = _trace(lib, sample['id'])
    base = baseline(time, signal, data, anchors)
    picked = regions(region_items if region_items is not None else data['积分区间'])
    record = _calibration_record(lib, curve_ref)
    return sample, record, calibration(record), time, signal, base, picked


# ---------- 给智能体的计算 ----------

def analyze(ref, lib=None, 锚点=None, 积分区间=None, 标准曲线=None, save=True, 方法=None):
    lib = _lib(lib)
    sample, record, cal, time, signal, base, picked = _prepare(lib, ref, 锚点, 积分区间, 标准曲线, 方法)
    plain = integrate_regions(time, signal, base.baseline, picked)
    summary = summarize_integration_areas(plain)
    calibrated = integrate_regions(time, signal, base.baseline, picked, calibration_bounds=(cal.valid_rt_min, cal.valid_rt_max))
    effective = math.fsum(item.areas.effective_nonnegative_area for item in calibrated.regions)
    outside = math.fsum(item.areas.out_of_calibration_high_m_area + item.areas.out_of_calibration_low_m_area for item in calibrated.regions)
    result = calculate_molecular_weight(calibrated, cal)
    value = {'样品': sample['name'], '标准曲线': record['name'],
             'Mn': result.mn, 'Mw': result.mw, 'Mz': result.mz, 'Mz+1': result.mz_plus_1, 'PDI': result.pdi,
             'Mp': (result.mp_rt if (lib.setting('GPC.Mp口径') or '分布峰顶') == '色谱峰高' or result.mp_log_m is None else result.mp_log_m),
             'Mp_峰高': result.mp_rt,                                       # Mp 用哪个：库设置"GPC.Mp口径"（分布峰顶 / 色谱峰高）
             'D10': result.mass_d10, 'D50': result.mass_d50, 'D90': result.mass_d90,
             '面积': summary.independent_area_sum, '并集面积': summary.union_area,
             '外推面积占比': outside / effective if effective > 0 else 0.0,
             '单位': UNITS, '提示': list(result.warnings)}
    tried = 锚点 is not None or 积分区间 is not None or 标准曲线 is not None
    if save and not tried:
        lib.set_result(sample['id'], 'GPC分子量', value, {'标准曲线id': record['id'], '分析版本': sample['data'].get('分析版本'),
                                                          'Mp 口径': (lib.setting('GPC.Mp口径') or '分布峰顶') + '；Mp_峰高 总是色谱峰高处'}, VERSION)
    return value


def distribution(ref, lib=None, save=True):
    lib = _lib(lib)
    sample, record, cal, time, signal, base, picked = _prepare(lib, ref)
    calibrated = integrate_regions(time, signal, base.baseline, picked, calibration_bounds=(cal.valid_rt_min, cal.valid_rt_max))
    segments, allow, _ = select_calibrated_segments(calibrated, cal)
    curve = build_distribution(segments, cal, allow_extrapolation=allow)
    density = curve.density_per_log10_m
    count = len(density)
    peak = max(range(count), key=density.__getitem__)
    limit = max(10, int(lib.setting('GPC.分布抽点上限') or MAX_POINTS))   # 库设置"GPC.分布抽点上限"
    if count <= limit:
        indices = list(range(count))
    else:                                                      # 照原件：按索引均匀抽，保留首尾和峰顶，不插值
        indices = sorted({round(i * (count - 1) / (limit - 2)) for i in range(limit - 1)} | {peak})
    value = {'样品': sample['name'], '标准曲线': record['name'],
             'logM': [curve.log10_molecular_weight[i] for i in indices],
             'dw/dlogM': [density[i] / curve.total_mass_rt for i in indices],
             'M': [curve.molecular_weight[i] for i in indices],
             '累积质量分数': [curve.cumulative_mass_fraction[i] for i in indices],
             '归一除数': curve.total_mass_rt,
             '单位': {'logM': 'log10(g/mol)', 'dw/dlogM': '1', 'M': 'g/mol', '累积质量分数': '1', '归一除数': '信号·min'},
             '抽点说明': f'原始 {count} 点按索引均匀抽样，保留首尾和峰顶，共 {len(indices)} 点；除以整条分布的 total_mass_rt，抽点后不再归一'}
    if save:
        lib.set_result(sample['id'], 'GPC分布', value, {'标准曲线id': record['id'], '最多点数': limit}, VERSION)
    return value


# ---------- 叠加对齐（只改显示，不改存着的分子量） ----------
# 进样之间流速、柱温有一点漂移，同一个峰会早几秒或晚几秒出来。对齐要找一个"每次都有、不随样品变"的峰（溶剂峰、系统峰、
# 流速标记），把它对到参比的位置；不能拿样品自己的峰对（样品本来就不一样，对齐会把真实差别对没）。
# 算法照原 GPC 软件的漂移参考区间（services/retention_alignment.align_retention_traces）：参比在区间里的那段谱（减均值、单位化），
# 样品在 ±搜索范围里逐个平移，取相关最大的那个平移，抛物线细化；撞到搜索边界的标出来（多半没对上）。

def _raw(lib, ref):
    time, signal = _trace(lib, ref)
    return np.asarray(time, dtype=np.float64), np.asarray(signal, dtype=np.float64)


def suggest_marker(lib, ids):
    """在积分区间后面找每条谱都有的明显峰（溶剂峰、系统峰）：每条谱在这段里缩到 0–1，平均起来最高的地方，前后各 0.25 min。
    返回 {区间, 峰顶, 说明}；找不到返回区间 None。"""
    lib = _lib(lib)
    traces, ends = [], []
    for ref in ids:
        sample = lib.get(ref, 'GPC样品')
        time, signal = _raw(lib, sample['id'])
        items = sample['data'].get('积分区间') or []
        ends.append(max((float(item['止']) for item in items), default=float(time[0])))
        traces.append((time, signal))
    low = max(ends) + 0.3
    high = min(float(time[-1]) for time, _ in traces) - 0.3
    if high - low < 0.6:
        return {'区间': None, '说明': '积分区间后面没有足够长的谱，找不到标记峰；可以手填一段。'}
    grid = np.linspace(low, high, max(50, int((high - low) / 0.005)))
    stack = []
    for time, signal in traces:
        part = np.interp(grid, time, signal)
        part = part - np.median(part)
        top = float(np.max(part))
        if top > 0:
            stack.append(np.clip(part / top, 0, 1))
    if not stack:
        return {'区间': None, '说明': '积分区间后面没有明显的峰。'}
    common = np.min(stack, axis=0) * 0.5 + np.mean(stack, axis=0) * 0.5      # 每条都要有：取最小和平均各一半，单条谱独有的峰压下去
    apex = float(grid[int(np.argmax(common))])
    return {'区间': [round(apex - 0.25, 3), round(apex + 0.25, 3)], '峰顶': round(apex, 3),
            '说明': f'积分区间后面 {apex:.2f} min 处每条谱都有一个明显的峰（多半是溶剂或系统峰），拿它对齐。看叠加图确认它不是样品自己的峰。'}


def _window_shift(ref_time, ref_signal, time, signal, window, limit):
    """原件 align_retention_traces 的做法：参比在区间里的谱 vs 样品平移后的谱，相关最大的平移。返回 (平移 min, 相关, 撞边界)。"""
    start, end = window
    step = float(np.median(np.diff(ref_time)))
    grid = np.linspace(start, end, max(5, int(np.ceil((end - start) / step)) + 1))
    reference = np.interp(grid, ref_time, ref_signal)
    reference = reference - reference.mean()
    norm = float(np.linalg.norm(reference))
    if norm <= 1e-12:
        raise store.StoreError('参比在这一段里是平的，没法对齐；换一段有峰的。')
    reference = reference / norm
    shifts = np.linspace(-limit, limit, 2 * max(1, int(np.ceil(limit / step))) + 1)

    def score(shift):
        response = np.interp(grid - shift, time, signal)
        response = response - response.mean()
        size = float(np.linalg.norm(response))
        return float(np.dot(reference, response / size)) if size > 1e-12 else float('-inf')
    scores = np.array([score(float(shift)) for shift in shifts])
    best = int(np.argmax(scores))
    shift, boundary = float(shifts[best]), best in (0, len(shifts) - 1)
    if not boundary:
        left, middle, right = scores[best - 1:best + 2]
        bend = left - 2 * middle + right
        if np.isfinite(bend) and bend < 0:
            candidate = shift + 0.5 * (left - right) / bend * float(shifts[1] - shifts[0])
            if score(candidate) > scores[best]:
                shift = candidate
    return shift, score(shift), boundary


def marker_apex(time, signal, window):
    """这一段里的峰顶（扣掉两端连线，抛物线细化）。返回 (峰顶 min, 是否顶在区间边上, 像不像峰)。
    像不像峰：峰 = 往上的、比噪声高得多；凹 = 往下凹得比往上鼓得多（示差检测器的系统峰有时是负的）；平 = 看不出峰。"""
    start, end = window
    inside = np.flatnonzero((time >= start) & (time <= end))
    if len(inside) < 5:
        raise store.StoreError(f'{start:.2f}–{end:.2f} min 这一段没有谱。')
    t, s = time[inside], signal[inside]
    s = s - np.interp(t, [t[0], t[-1]], [s[0], s[-1]])
    noise = 1.4826 * float(np.median(np.abs(np.diff(s) - np.median(np.diff(s))))) / math.sqrt(2) or 1e-12
    i = int(np.argmax(s))
    rise, dip = float(s[i]), float(-np.min(s))
    kind = '凹' if dip > rise else '平' if rise < 8 * noise else '峰'
    if i in (0, len(s) - 1):
        return float(t[i]), True, kind
    left, middle, right = s[i - 1], s[i], s[i + 1]
    bend = left - 2 * middle + right
    offset = 0.5 * (left - right) / bend if bend < 0 else 0.0
    return float(t[i] + offset * (t[i + 1] - t[i])), False, kind


def align(ids, ref, lib=None, method='marker', window=None, limit=0.5, manual=None):
    """叠加对齐：每条谱要加的保留时间平移（min），参比是 0。只改显示。
    method：marker 按标记峰——每条谱在 window 这一段里的峰顶对到参比的峰顶（每次都有、不随样品变的峰：溶剂峰、系统峰、流速标记）；
    shape 按积分区间里整条曲线的形状（原件的稳健互相关；只适合同一个样重复进样，样品不一样时会把真实差别对没）。"""
    lib = _lib(lib)
    from gpcsec.core.comparison.curves import align_by_cross_correlation
    ref_time, ref_signal = _raw(lib, ref)
    manual = {key: float(value) for key, value in (manual or {}).items() if value is not None}
    if method == 'marker' and not window and not all(item in manual for item in ids):
        window = suggest_marker(lib, ids)['区间']
        if not window:
            raise store.StoreError('找不到标记峰：在图上点一下每条谱都有的峰（溶剂峰、系统峰），或者手填一段。')
    if method == 'marker':
        # 每条找峰顶；只有真像峰的才对齐。人在图上标过的（manual：这条谱自己的保留时间）用人标的：贴到附近 ±0.08 min 的峰顶，附近没有峰就照人点的位置。
        # 参比有这个峰就都对到参比；参比没有（凹下去、平的），就都对到这个峰的平均位置（中位数），参比不动
        found = {}
        for item in ids:
            time, signal = (ref_time, ref_signal) if item == ref else _raw(lib, item)
            if item in manual:
                where = manual[item]
                apex, edge, kind = marker_apex(time, signal, (where - 0.08, where + 0.08))
                if kind != '峰' or edge:
                    apex = where
                found[item] = {'峰顶': round(apex, 4), '撞边界': False, '形状': '人标', '能对齐': True, '人标': round(where, 4)}
                continue
            apex, edge, kind = marker_apex(time, signal, window)
            found[item] = {'峰顶': round(apex, 4), '撞边界': edge, '形状': kind, '能对齐': kind == '峰' and not edge}
        good = [value['峰顶'] for value in found.values() if value['能对齐']]
        if not good:
            raise store.StoreError('这一段里没有一条谱有像样的峰；换一段（看叠加图里每条都往上鼓的那个峰）。')
        target = found[ref]['峰顶'] if found[ref]['能对齐'] else float(np.median(good))
        for item, value in found.items():
            value['平移'] = round(target - value['峰顶'], 4) if value['能对齐'] else 0.0
        return {'平移': found, '区间': window, '方法': method, '对到': round(target, 4), '参比有峰': found[ref]['能对齐']}
    out = {}
    for item in ids:
        if item == ref:
            out[item] = {'平移': 0.0, '峰顶': None, '撞边界': False}
            continue
        time, signal = _raw(lib, item)
        # 按整条曲线形状（原件的稳健互相关），在参比的积分区间里比
        sample = lib.get(item, 'GPC样品')
        reference = lib.get(ref, 'GPC样品')
        spans = [(float(r['起']), float(r['止'])) for r in (reference['data'].get('积分区间') or [])] or [(float(ref_time[0]), float(ref_time[-1]))]
        low, high = min(a for a, _ in spans), max(b for _, b in spans)
        mine = (time >= low) & (time <= high)
        theirs = (ref_time >= low) & (ref_time <= high)
        base_mine = baseline(time, signal, sample['data']).baseline if sample['data'].get('基线方法') else np.zeros_like(signal)
        base_ref = baseline(ref_time, ref_signal, reference['data']).baseline if reference['data'].get('基线方法') else np.zeros_like(ref_signal)
        shift = align_by_cross_correlation(ref_time[theirs], (ref_signal - base_ref)[theirs], time[mine], (signal - base_mine)[mine])
        out[item] = {'平移': round(shift, 4), '峰顶': None, '撞边界': False}
    return {'平移': out, '区间': window, '方法': method}


def analyze_shifted(ref, shift, lib=None):
    """把这条谱整体平移 shift min（积分区间跟着走）再按它的标准曲线算分子量：看流速漂移校正后分子量变多少。不存。"""
    from dataclasses import replace
    lib = _lib(lib)
    sample, record, cal, time, signal, base, picked = _prepare(lib, ref)
    moved = tuple(float(t) + shift for t in time)
    regions_moved = tuple(replace(item, start_time_min=item.start_time_min + shift, end_time_min=item.end_time_min + shift) for item in picked)
    calibrated = integrate_regions(moved, signal, base.baseline, regions_moved, calibration_bounds=(cal.valid_rt_min, cal.valid_rt_max))
    result = calculate_molecular_weight(calibrated, cal)
    return {'Mn': result.mn, 'Mw': result.mw, 'PDI': result.pdi}


def refit_calibration(ref, lib=None, degree=None):
    lib = _lib(lib)
    record = _calibration_record(lib, ref)
    data = record['data']
    points = tuple(CalibrationPoint(float(t), float(m), bool(on)) for t, m, on in record['_points'])
    fitted = fit_polynomial(points, int(degree or data.get('次数') or 3), data.get('来源sha256'))
    stored = list(data.get('系数') or [])
    new = list(fitted.coefficients)
    return {'标准曲线': record['name'], '次数': int(degree or data.get('次数') or 3), '系数(存)': stored, '系数(重拟合)': new,
            '最大差': max((abs(a - b) for a, b in zip(stored, new)), default=None) if len(stored) == len(new) else None,
            'R²': fitted.statistics.r_squared, 'RMSE': fitted.statistics.rmse, 'R²(存)': (data.get('拟合统计') or {}).get('r_squared'),
            '有效范围': [fitted.valid_rt_min, fitted.valid_rt_max], '单位': {'有效范围': 'min', '系数': 'log10(M) 对 RT(min) 的多项式，a0 起'}}


def experiment_trend(ref, lib=None, metric='Mw'):
    lib = _lib(lib)
    exp = lib.get(ref, 'GPC实验', with_series=True)
    columns, rows = exp['series']['成员']['columns'], exp['series']['成员']['rows']
    found = {item['id']: item['value'] for item in lib.results('GPC分子量', 'GPC样品')}
    points = []
    for row in rows:
        item = dict(zip(columns, row))
        points.append({'样品': item['样品'], '时间': item['反应时间'], '单位': item['时间单位'], '重复': item['重复'], '纳入': item['纳入'],
                       '剔除原因': item['剔除原因'], '因子水平': item['因子水平'], '值': (found.get(item['样品id']) or {}).get(metric)})
    points.sort(key=lambda item: (item['时间'] is None, item['时间'] or 0))
    return {'实验': exp['name'], '指标': metric, '单位': UNITS.get(metric, ''), '点': points}


def read_instrument_file(path):
    """读一个仪器导出文件。CSV/TXT 自动猜编码、分隔符、小数点、表头（安捷伦 ChemStation 导出是 UTF-16 制表符两列）。"""
    from gpcsec.io.importers import (import_calibration_pdf_bytes, import_delimited_bytes, import_netcdf_bytes,
                                     import_xlsx_bytes, import_zip_bytes)
    path = Path(path)
    raw = path.read_bytes()
    suffix = path.suffix.casefold()

    def trace(result, name):
        return {'类型': '色谱', '样品名': Path(name).stem, '原文件名': name, '时间 / min': list(result.chromatogram.time_min),
                '信号': list(result.chromatogram.signal), '点数': len(result.chromatogram.time_min), 'sha256': result.raw_file.sha256,
                '格式': {'编码': result.probe.encoding, '分隔符': result.probe.delimiter, '小数点': result.probe.decimal_mark,
                         '表头': result.probe.has_header, '把握': result.probe.confidence},
                '提示': [f'第 {item.line_number} 行跳过：{item.reason}（{item.raw_line[:60]}）' for item in result.issues]}

    if suffix == '.pdf':
        record, probe = import_calibration_pdf_bytes(raw, path.name, 'calibration_new')
        return {'类型': '标准曲线', '名称': record.name, '原文件名': path.name, 'sha256': record.raw_file.sha256,
                '标样点': [[p.retention_time_min, p.molecular_weight, p.enabled] for p in record.points], '次数': record.degree,
                '系数': list(record.coefficients), '缩放系数': list(record.scaled_coefficients),
                '有效范围起': record.valid_rt_min, '有效范围止': record.valid_rt_max, '单调方向': record.monotonic_direction,
                'R²': record.statistics.r_squared, 'RMSE': record.statistics.rmse, '算法版本': record.algorithm_version,
                '说明': '从 PDF 的 Calibration Points 表读点、按 Curve Fit Used 次数重拟合，不用报告上四舍五入的方程'}
    if suffix == '.xlsx':
        return trace(import_xlsx_bytes(raw, path.name), path.name)
    if suffix in {'.cdf', '.nc'}:
        return trace(import_netcdf_bytes(raw, path.name), path.name)
    if suffix == '.zip':
        return [{**trace(item.result, item.name), '成员名编码': item.name_encoding} for item in import_zip_bytes(raw)]
    return trace(import_delimited_bytes(raw, path.name), path.name)


def compute_all(lib=None, report=print):
    lib = _lib(lib)
    count = {'样品': 0, '算了': 0, '没有分析': 0, '没有标准曲线': 0, '出错': 0}
    for row in lib.rows('GPC样品'):
        count['样品'] += 1
        if not row.get('积分区间'):
            count['没有分析'] += 1
            continue
        if not row.get('标准曲线id'):
            count['没有标准曲线'] += 1
            report(f'  {row["name"]}：有分析但没绑定标准曲线，没算')
            continue
        try:
            analyze(row['id'], lib)
            distribution(row['id'], lib)
            count['算了'] += 1
        except (ValueError, TypeError, store.StoreError) as error:   # 原件的 AnalysisError 也是 ValueError
            count['出错'] += 1
            report(f'  {row["name"]}：{error}')
    report(f'GPC 计算：{count}')
    return count
