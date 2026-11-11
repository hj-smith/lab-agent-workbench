"""GPC 报告和期刊图：样品报告、比较报告、实验报告（PDF / Word / Excel），每张图也能单独下载（PNG / SVG / TIFF）。

图按期刊习惯画：Arial + 微软雅黑，刻度朝内，只留左下框，无网格，线宽 1 左右；单栏 89 mm、双栏 183 mm。
报告的版面、表格、图注交给 export.report（同一套块，PDF、Word 各按自己的版面排），这里只备内容和图。
"""
import io
import math
from datetime import datetime
from pathlib import Path

import numpy as np

from . import config, export, gpcexp, interact, kernel, store

SIZES = {'single': (89, 66), 'double': (183, 115), 'wide': (183, 80)}
SIGNAL, BASE, FILL, INK, MUTED = '#2f5f9e', '#d9822b', '#c96442', '#29261f', '#6b665c'
METRIC_ROWS = [('Mn', 'Mn', 'g/mol'), ('Mw', 'Mw', 'g/mol'), ('Mz', 'Mz', 'g/mol'), ('Mz+1', 'Mz+1', 'g/mol'), ('PDI', 'Đ（Mw/Mn）', ''),
               ('Mp', 'Mp（分布峰顶）', 'g/mol'), ('Mp_峰高', 'Mp（色谱峰顶）', 'g/mol'), ('D10', 'D10', 'g/mol'), ('D50', 'D50', 'g/mol'), ('D90', 'D90', 'g/mol')]
PS_NOTE = '分子量是用聚苯乙烯（PS）窄分布标准品标定的相对值（PS 当量），不是绝对分子量。'


def _pyplot():
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot
    pyplot.rcParams.update({
        'font.family': ['Arial', 'Microsoft YaHei'], 'font.size': 7.5, 'axes.unicode_minus': False,        # 英文 Arial，中文落到微软雅黑
        'axes.linewidth': 0.8, 'axes.spines.top': False, 'axes.spines.right': False, 'axes.labelcolor': INK, 'axes.edgecolor': INK,
        'xtick.direction': 'in', 'ytick.direction': 'in', 'xtick.major.size': 3, 'ytick.major.size': 3, 'xtick.minor.size': 1.6, 'ytick.minor.size': 1.6,
        'xtick.major.width': 0.8, 'ytick.major.width': 0.8, 'xtick.color': INK, 'ytick.color': INK, 'legend.frameon': False, 'legend.fontsize': 6.5,
        'lines.linewidth': 1.1, 'svg.fonttype': 'none', 'mathtext.default': 'regular', 'savefig.facecolor': 'white'})
    return pyplot


def _figure(size, rows=1, cols=1, ratios=None, share_x=False):
    pyplot = _pyplot()
    width, height = SIZES.get(size, SIZES['single'])
    figure, axes = pyplot.subplots(rows, cols, figsize=(width / 25.4, height / 25.4), sharex=share_x, squeeze=False,
                                   gridspec_kw={'height_ratios': ratios} if ratios else None)
    if size != 'single':
        for axis in axes.flat:
            axis.tick_params(labelsize=8)
    return figure, axes


def _bytes(figure, fmt='png', dpi=300):
    pyplot = _pyplot()
    data = io.BytesIO()
    options = {'pil_kwargs': {'compression': 'tiff_lzw'}} if fmt == 'tiff' else {}
    figure.savefig(data, format=fmt, dpi=dpi, bbox_inches='tight', pad_inches=0.04, **options)
    pyplot.close(figure)
    return data.getvalue()


def _log_ticks(axis):
    from matplotlib.ticker import FuncFormatter, LogLocator
    axis.set_xscale('log')
    axis.xaxis.set_major_locator(LogLocator(base=10))
    axis.xaxis.set_minor_locator(LogLocator(base=10, subs=np.arange(2, 10) * 0.1))
    axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f'$10^{{{int(round(math.log10(value)))}}}$' if value > 0 else ''))


# ---------- 数据 ----------

def _sample(library, ref):
    lib = store.open(library)
    view = interact.gpc_view(library, ref)
    record = lib.get(ref, 'GPC样品')
    dist = ((record.get('results') or {}).get('GPC分布') or {}).get('value') or {}
    curve = lib.get(view['curve'], 'GPC标准曲线') if view.get('curve') else None
    return view, record, dist, curve


def _dist_sorted(dist):
    if not dist.get('logM'):
        return None
    log_m, weight = np.asarray(dist['logM'], float), np.asarray(dist['dw/dlogM'], float)
    order = np.argsort(log_m)
    log_m, weight = log_m[order], np.clip(weight[order], 0, None)
    return log_m, weight, gpcexp._cumulative(log_m, weight)          # 累积按 log M 从小到大积分，和横轴同向


# ---------- 图 ----------

def fig_chromatogram(view, size='single'):
    figure, axes = _figure(size)
    axis = axes[0, 0]
    t, s = np.asarray(view['trace']['t']), np.asarray(view['trace']['s'])
    edges = [float(r['起']) for r in view.get('regions') or []] + [float(r['止']) for r in view.get('regions') or []]
    lo, hi = (min(edges) - 1.5, max(edges) + 1.5) if edges else (t[0], t[-1])          # 看积分区间附近，溶剂峰不进图
    inside = (t >= lo) & (t <= hi)
    for region in view.get('regions') or []:
        axis.axvspan(float(region['起']), float(region['止']), color=FILL, alpha=0.1, lw=0)
    axis.plot(t[inside], s[inside], color=SIGNAL, lw=1.0, label='原信号')
    if view.get('baseline'):
        bt, bb = np.asarray(view['baseline']['t']), np.asarray(view['baseline']['b'], dtype=float)
        keep = (bt >= lo) & (bt <= hi)
        axis.plot(bt[keep], bb[keep], color=BASE, lw=1.0, ls=(0, (4, 2)), label='基线')
    anchors = [a for a in view.get('anchors') or [] if lo <= a[0] <= hi]
    if anchors:
        axis.plot([a[0] for a in anchors], [a[1] for a in anchors], 'o', ms=2.6, color=INK, label='基线锚点')
    span = s[inside].max() - s[inside].min()
    axis.set_xlim(lo, hi)
    axis.set_ylim(s[inside].min() - span * 0.05, s[inside].max() + span * 0.08)
    axis.set_xlabel('保留时间 / min')
    axis.set_ylabel('检测器响应')
    axis.legend(loc='upper left')
    return figure


def fig_distribution(dist, result, size='single'):
    figure, axes = _figure(size, 2, 1, ratios=[2.2, 1], share_x=True)
    top, bottom = axes[0, 0], axes[1, 0]
    log_m, weight, cumulative = _dist_sorted(dist)
    m = 10 ** log_m
    top.fill_between(m, weight, color=FILL, alpha=0.12, lw=0)
    top.plot(m, weight, color=FILL, lw=1.2)
    for step, (key, label, style) in enumerate((('Mn', 'Mn', (0, (1, 1.5))), ('Mw', 'Mw', (0, (4, 2))), ('Mp', 'Mp', '-'))):
        if result.get(key):
            top.axvline(result[key], color=MUTED, lw=0.7, ls=style)
            top.text(result[key], weight.max() * (1.03 + 0.07 * step), f' {label}', fontsize=6.5, color=MUTED, ha='left', va='bottom')
    top.set_ylim(0, weight.max() * 1.25)
    top.set_ylabel('dw/dlog M')
    if np.isfinite(cumulative).all():
        bottom.plot(m, cumulative, color=INK, lw=1.0)            # 从小分子量累到大
    bottom.set_ylim(0, 1.02)
    bottom.set_ylabel('累积质量分数')
    _log_ticks(bottom)
    bottom.set_xlabel('M / (g/mol)（PS 当量）')
    figure.subplots_adjust(hspace=0.08)
    return figure


def fig_overlay(items, size='double'):
    """几条分布叠在一起（每条峰高 = 1）。items: [(名字, dist)]。"""
    figure, axes = _figure(size)
    axis = axes[0, 0]
    for index, (name, dist) in enumerate(items):
        got = _dist_sorted(dist)
        if not got:
            continue
        log_m, weight, _ = got
        axis.plot(10 ** log_m, weight / (weight.max() or 1), lw=1.1, color=export.COLORS[index % len(export.COLORS)], label=name)
    _log_ticks(axis)
    axis.set_ylim(0, 1.08)
    axis.set_xlabel('M / (g/mol)（PS 当量）')
    axis.set_ylabel('dw/dlog M（峰高归一）')
    axis.legend(loc='upper left', ncol=1 if len(items) <= 6 else 2)
    return figure


def fig_chrom_overlay(views, size='double'):
    """扣基线后的色谱叠在一起（每条积分区间里的最高点 = 1）。"""
    figure, axes = _figure(size)
    axis = axes[0, 0]
    for index, (name, view) in enumerate(views):
        if not view.get('baseline'):
            continue
        t, s = np.asarray(view['trace']['t']), np.asarray(view['trace']['s'])
        base = np.interp(t, view['baseline']['t'], np.nan_to_num(np.asarray(view['baseline']['b'], dtype=float), nan=0.0))
        regions = view.get('regions') or []
        lo, hi = min(float(r['起']) for r in regions) - 0.5, max(float(r['止']) for r in regions) + 0.5
        keep = (t >= lo) & (t <= hi)
        corrected = s[keep] - base[keep]
        axis.plot(t[keep], corrected / (corrected.max() or 1), lw=1.0, color=export.COLORS[index % len(export.COLORS)], label=name)
    axis.set_xlabel('保留时间 / min')
    axis.set_ylabel('扣基线响应（归一）')
    axis.legend(loc='upper left', ncol=1 if len(views) <= 6 else 2)
    return figure


def fig_trends(view, size='double'):
    figure, axes = _figure(size, 1, 3)
    for axis, (key, label) in zip(axes[0], (('Mn', 'Mn / (g/mol)'), ('Mw', 'Mw / (g/mol)'), ('PDI', 'Đ'))):
        mean = view['trend'][key]['mean']
        axis.plot([p['h'] for p in mean], [p['v'] for p in mean], '-o', color=FILL, ms=3, lw=1.0)
        dropped = [p for p in view['trend'][key]['points'] if not p['include']]
        if dropped:
            axis.plot([p['h'] for p in dropped], [p['v'] for p in dropped], 'x', color=MUTED, ms=4)
        axis.set_xlabel('反应时间 / h')
        axis.set_ylabel(label)
    figure.subplots_adjust(wspace=0.42)
    return figure


def fig_waterfall(view, size='single', gap=0.7):
    figure, axes = _figure(size)
    axis = axes[0, 0]
    rows = view['waterfall']
    hours = [row['h'] for row in rows]
    lo, hi = min(hours), max(hours)
    for index, row in enumerate(rows):
        t = (row['h'] - lo) / (hi - lo) if hi > lo else 1
        color = tuple((a + (b - a) * t) / 255 for a, b in zip((232, 180, 155), (92, 36, 16)))
        weight = np.asarray(row['w'])
        axis.plot(10 ** np.asarray(row['logM']), weight / (weight.max() or 1) + index * gap, color=color, lw=1.0)
        axis.text(10 ** row['logM'][-1], index * gap + 0.06, f'{row["h"]:g} h', fontsize=6, color=MUTED, ha='right')
    _log_ticks(axis)
    axis.set_yticks([])
    axis.spines['left'].set_visible(False)
    axis.set_xlabel('M / (g/mol)（PS 当量）')
    return figure


def fig_bands(view, size='single'):
    figure, axes = _figure(size)
    axis = axes[0, 0]
    hours = [f['h'] for f in view['fractions']]
    stacks = [[f['f'][index] * 100 for f in view['fractions']] for index in range(len(view['bands']))]
    axis.stackplot(hours, *stacks, colors=[band['color'] for band in view['bands']], labels=[band['name'] for band in view['bands']], alpha=0.9, lw=0.3, edgecolor='white')
    axis.set_ylim(0, 100)
    axis.set_xlim(min(hours), max(hours))
    axis.set_xlabel('反应时间 / h')
    axis.set_ylabel('质量分数 / %')
    axis.legend(loc='upper left', bbox_to_anchor=(1.01, 1), fontsize=6)
    return figure


def fig_kinetics(view, size='single'):
    figure, axes = _figure(size)
    axis = axes[0, 0]
    k = view['kinetics']
    points = [p for p in view['trend'][k['metric']]['points'] if p['include']]
    axis.plot([p['h'] for p in points], [p['v'] for p in points], 'o', color=INK, ms=3, label='实测')
    colors = {'zero_order': '#c96442', 'first_order': '#3f7cc0', 'second_order': '#b5841c', 'general_order': '#8a63b8'}
    for fit in k['fits']:
        x = np.asarray(fit['curve']['x']); y = np.asarray([np.nan if v is None else v for v in fit['curve']['y']], dtype=float)
        axis.plot(x, y, color=colors[fit['model']], lw=1.4 if fit.get('recommended') else 0.9, ls='-' if fit.get('recommended') else (0, (3, 2)),
                  label=fit['label'] + (f'（n = {fit["order"]:.2f}）' if fit['model'] == 'general_order' else '') + ('，推荐' if fit.get('recommended') else ''))
    axis.set_xlabel('反应时间 / h')
    axis.set_ylabel({'PDI': 'Đ'}.get(k['metric'], k['metric']))
    axis.legend(loc='upper left')
    return figure


# ---------- 报告 ----------

def _today():
    return datetime.now(kernel.BEIJING).strftime('%Y-%m-%d')


def _folder(library):
    folder = config.outputs_dir(library) / 'GPC报告'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _image(folder, name, figure):
    path = folder / f'_{name}.png'
    path.write_bytes(_bytes(figure, 'png', 300))
    return path.name


def _number(value, digits=0):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return '—'
    return f'{value:,.{digits}f}'


def sample_blocks(library, ref, folder):
    view, record, dist, curve = _sample(library, ref)
    r = view.get('result') or {}
    data = record['data']
    rows = [[label, _number(r.get(key), 3 if key == 'PDI' else 0), unit] for key, label, unit in METRIC_ROWS]
    rows.append(['外推面积', f'{(r.get("外推面积占比") or 0) * 100:.2f}', '%'])
    blocks = [{'type': 'heading', 'text': '结果'}, {'type': 'table', 'caption': '分子量（' + PS_NOTE.rstrip('。') + '）', 'columns': ['指标', '数值', '单位'], 'rows': rows}]
    blocks += [{'type': 'heading', 'text': '色谱和基线'},
               {'type': 'image', 'file': _image(folder, f'{ref}-chrom', fig_chromatogram(view, 'double')), 'caption': '色谱图：原信号、基线（橙色虚线）、积分区间（浅色带）、基线锚点'}]
    if _dist_sorted(dist):
        blocks += [{'type': 'heading', 'text': '分子量分布'},
                   {'type': 'image', 'file': _image(folder, f'{ref}-dist', fig_distribution(dist, r, 'double')), 'caption': '上：质量分布 dw/dlog M，竖线是 Mn、Mw、Mp；下：累积质量分数'}]
    blocks += [{'type': 'heading', 'text': '分析参数'},
               {'type': 'table', 'caption': '积分区间', 'columns': ['名称', '起 / min', '止 / min', '峰方向', '计入分子量', '范围策略'],
                'rows': [[x.get('名称') or '', _number(float(x['起']), 3), _number(float(x['止']), 3), x.get('峰方向') or '正', '是' if x.get('计入分子量', True) else '否', x.get('范围策略') or '']
                         for x in view.get('regions') or []]},
               {'type': 'table', 'caption': f'基线锚点（连接方式：{ {"pchip_anchors": "PCHIP", "linear_anchors": "多点直线", "cubic_spline_anchors": "自然三次样条"}.get(view.get("method"), view.get("method")) }）',
                'columns': ['#', '时间 / min', '信号'], 'rows': [[i + 1, _number(a[0], 3), _number(a[1], 2)] for i, a in enumerate(view.get('anchors') or [])]}]
    if curve:
        c = curve['data']
        blocks.append({'type': 'table', 'caption': '标准曲线', 'columns': ['名称', '模型', '有效范围 / min', 'R²', '来源文件'],
                       'rows': [[curve['name'], 'PCHIP' if str(c.get('模型')).casefold() == 'pchip' else f'{c.get("次数")} 次多项式',
                                 f'{_number(c.get("有效范围起"), 2)}–{_number(c.get("有效范围止"), 2)}', _number(c.get('R²'), 4), c.get('来源文件名') or '']]})
    blocks.append({'type': 'text', 'text': PS_NOTE + ' 积分用原算法（exact-boundary-trapezoid-v2）；区间超出标准曲线有效范围的部分按"范围策略"处理。'})
    subtitle = ' · '.join(x for x in [f'项目 {data.get("所属项目")}' if data.get('所属项目') else '', f'标准曲线 {view.get("curve_name") or "—"}',
                                     f'分析第 {data.get("分析版本") or 1} 版', f'导出 {_today()}（北京时间）'] if x)
    sheets = {'结果': {'columns': ['指标', '数值', '单位'], 'rows': [[label, r.get(key), unit] for key, label, unit in METRIC_ROWS] + [['外推面积占比', r.get('外推面积占比'), '1']]},
              '积分区间': {'columns': ['名称', '起 / min', '止 / min', '峰方向', '计入分子量', '范围策略'],
                       'rows': [[x.get('名称'), float(x['起']), float(x['止']), x.get('峰方向') or '正', x.get('计入分子量', True), x.get('范围策略')] for x in view.get('regions') or []]},
              '基线锚点': {'columns': ['时间 / min', '信号'], 'rows': [list(a) for a in view.get('anchors') or []]},
              '色谱': {'columns': ['时间 / min', '信号', '基线'], 'rows': [[t, s, b] for t, s, b in zip(view['trace']['t'], view['trace']['s'],
                                                                           np.interp(view['trace']['t'], view['baseline']['t'], np.nan_to_num(np.asarray(view['baseline']['b'], float))) if view.get('baseline') else [None] * len(view['trace']['t']))]}}
    got = _dist_sorted(dist)
    if got:
        sheets['分布'] = {'columns': ['log M', 'M / (g/mol)', 'dw/dlogM', '累积质量分数'], 'rows': [[a, 10 ** a, b, c if math.isfinite(c) else None] for a, b, c in zip(*got)]}
    return f'GPC 样品报告 · {record["name"]}', subtitle, blocks, sheets, f'{record["name"]}_GPC样品报告_{_today()}'


def compare_blocks(library, refs, folder):
    lib = store.open(library)
    items = [_sample(library, ref) for ref in refs]
    names = [record['name'] for _, record, _, _ in items]
    results = [view.get('result') or {} for view, *_ in items]
    first = results[0].get('Mw')
    rows = [[name, _number(r.get('Mn')), _number(r.get('Mw')), _number(r.get('Mz')), _number(r.get('PDI'), 3), _number(r.get('Mp')),
             '—' if not first or not r.get('Mw') or index == 0 else f'{(r["Mw"] / first - 1) * 100:+.1f}'] for index, (name, r) in enumerate(zip(names, results))]
    blocks = [{'type': 'heading', 'text': '结果'},
              {'type': 'table', 'caption': f'分子量（PS 当量，g/mol）；"Mw 对第一条"以 {names[0]} 为参比', 'columns': ['样品', 'Mn', 'Mw', 'Mz', 'Đ', 'Mp', 'Mw 对第一条 %'], 'rows': rows},
              {'type': 'heading', 'text': '分子量分布'},
              {'type': 'image', 'file': _image(folder, 'cmp-dist', fig_overlay([(n, d) for n, (_, _, d, _) in zip(names, items)], 'double')), 'caption': '质量分布叠加（每条峰高归一到 1）'},
              {'type': 'heading', 'text': '色谱'},
              {'type': 'image', 'file': _image(folder, 'cmp-chrom', fig_chrom_overlay([(n, v) for n, (v, _, _, _) in zip(names, items)], 'double')), 'caption': '扣基线后的色谱叠加（每条积分区间里的最高点归一到 1）'},
              {'type': 'text', 'text': PS_NOTE}]
    curves = sorted({view.get('curve_name') or '—' for view, *_ in items})
    subtitle = f'{len(refs)} 个样品 · 标准曲线 {"、".join(curves)} · 导出 {_today()}（北京时间）'
    sheets = {'结果': {'columns': ['样品', 'Mn', 'Mw', 'Mz', 'Mz+1', 'Đ', 'Mp', 'D10', 'D50', 'D90', '外推面积占比'],
                       'rows': [[n] + [r.get(k) for k in ('Mn', 'Mw', 'Mz', 'Mz+1', 'PDI', 'Mp', 'D10', 'D50', 'D90', '外推面积占比')] for n, r in zip(names, results)]}}
    for name, (_, _, dist, _) in zip(names, items):
        got = _dist_sorted(dist)
        if got:
            sheets[f'分布 {name}'[:31]] = {'columns': ['log M', 'M / (g/mol)', 'dw/dlogM', '累积质量分数'], 'rows': [[a, 10 ** a, b, c if math.isfinite(c) else None] for a, b, c in zip(*got)]}
    return f'GPC 比较报告 · {names[0]} 等 {len(names)} 个', subtitle, blocks, sheets, f'{names[0]}等{len(names)}个_GPC比较报告_{_today()}'


def experiment_blocks(library, ref, folder):
    view = gpcexp.view(library, ref)
    k = view['kinetics']
    members = [[m['name'], _number(m['h'], 1) if m['h'] is not None else '—', '是' if m['include'] else f'否（{m["reason"] or "没写原因"}）', _number(m['Mw']), _number(m['PDI'], 3)] for m in view['members']]
    blocks = [{'type': 'text', 'text': view['about']} if view['about'] else None,
              {'type': 'heading', 'text': '成员'}, {'type': 'table', 'caption': '成员样品（反应时间按实验里记的）', 'columns': ['样品', '反应时间 / h', '纳入', 'Mw', 'Đ'], 'rows': members},
              {'type': 'heading', 'text': '分子量怎么长'}, {'type': 'image', 'file': _image(folder, f'{ref}-trend', fig_trends(view, 'wide')), 'caption': 'Mn、Mw、Đ 随反应时间（叉是没纳入的样）'}]
    if view['waterfall']:
        blocks += [{'type': 'heading', 'text': '分布怎么变'}, {'type': 'image', 'file': _image(folder, f'{ref}-fall', fig_waterfall(view, 'double')), 'caption': '分子量分布瀑布图（每条峰高归一，按反应时间往上叠，颜色越深越晚）'}]
    if view['fractions']:
        blocks += [{'type': 'heading', 'text': '分子量区段'},
                   {'type': 'image', 'file': _image(folder, f'{ref}-bands', fig_bands(view, 'double')), 'caption': '各分子量区段的质量分数随反应时间'},
                   {'type': 'table', 'caption': '区段定义', 'columns': ['区段', '下限 / (g/mol)', '上限 / (g/mol)'],
                    'rows': [[b['name'], _number(b['lower']) if b['lower'] else '（最小）', _number(b['upper']) if b['upper'] else '（最大）'] for b in view['bands']]}]
    if k['fits']:
        blocks += [{'type': 'heading', 'text': '动力学拟合'},
                   {'type': 'image', 'file': _image(folder, f'{ref}-kin', fig_kinetics(view, 'double')), 'caption': f'{ {"PDI": "Đ"}.get(k["metric"], k["metric"]) } 随反应时间的拟合（实线是推荐的，RMSE 最小）'},
                   {'type': 'table', 'caption': '拟合结果', 'columns': ['模型', '级数 n', 'k（/h）', 'y0', 'R²', 'RMSE', '方程'],
                    'rows': [[f['label'] + ('（推荐）' if f.get('recommended') else ''), _number(f['order'], 2), f'{f["k"]:.4g}', f'{f["y0"]:.4g}', _number(f['r2'], 3), f'{f["rmse"]:.4g}', f['equation']] for f in k['fits']]},
                   {'type': 'text', 'text': '描述性拟合：只说明数据像哪种曲线，不证明反应机理。' + (k.get('warning') or '')}]
    blocks.append({'type': 'text', 'text': PS_NOTE})
    subtitle = ' · '.join(x for x in [f'项目 {view["project"]}' if view['project'] else '', f'{len(view["members"])} 个样', f'反应时间 {view["span"][0]:g}–{view["span"][1]:g} h' if view['span'] else '', f'导出 {_today()}（北京时间）'] if x)
    sheets = {'成员': {'columns': ['样品', '反应时间 / h', '纳入', '剔除原因', 'Mw', 'Đ'], 'rows': [[m['name'], m['h'], m['include'], m['reason'], m['Mw'], m['PDI']] for m in view['members']]},
              '趋势': {'columns': ['反应时间 / h'] + gpcexp.METRICS, 'rows': [[row['h']] + [next((p['v'] for p in view['trend'][key]['mean'] if p['h'] == row['h']), None) for key in gpcexp.METRICS] for row in view['trend']['Mw']['mean']]},
              '区段质量分数': {'columns': ['反应时间 / h', '样品'] + [b['name'] for b in view['bands']], 'rows': [[f['h'], f['name']] + f['f'] for f in view['fractions']]}}
    if k['fits']:
        sheets['动力学'] = {'columns': ['模型', '级数 n', 'k（/h）', 'y0', 'R²', 'RMSE', '方程', '推荐'], 'rows': [[f['label'], f['order'], f['k'], f['y0'], f['r2'], f['rmse'], f['equation'], bool(f.get('recommended'))] for f in k['fits']]}
    return f'GPC 实验报告 · {view["name"]}', subtitle, [b for b in blocks if b], sheets, f'{view["name"]}_GPC实验报告_{_today()}'


BUILDERS = {'sample': sample_blocks, 'compare': compare_blocks, 'experiment': experiment_blocks}


def report(library, kind, refs, fmt):
    """出一份报告，返回文件路径。fmt：pdf / docx / xlsx。"""
    folder = _folder(library)
    target = refs if kind == 'compare' else refs[0]
    title, subtitle, blocks, sheets, name = BUILDERS[kind](library, target, folder)
    made = [export.excel(folder, name, sheets)] if fmt == 'xlsx' else export.report(folder, title, blocks, formats=[fmt], name=name, subtitle=subtitle)
    for leftover in folder.glob('_*.png'):                # 报告里的图已经嵌进去了
        leftover.unlink()
    return folder / made[0]


FIGURES = {'chromatogram': ('sample', lambda d, size: fig_chromatogram(d[0], size)), 'distribution': ('sample', lambda d, size: fig_distribution(d[2], d[0].get('result') or {}, size)),
           'overlay': ('compare', lambda d, size: fig_overlay([(x[1]['name'], x[2]) for x in d], size)),
           'chrom_overlay': ('compare', lambda d, size: fig_chrom_overlay([(x[1]['name'], x[0]) for x in d], size)),
           'trend': ('experiment', lambda d, size: fig_trends(d, size)), 'waterfall': ('experiment', lambda d, size: fig_waterfall(d, size)),
           'bands': ('experiment', lambda d, size: fig_bands(d, size)), 'kinetics': ('experiment', lambda d, size: fig_kinetics(d, size))}
FIGURE_NAMES = {'chromatogram': '色谱图', 'distribution': '分子量分布', 'overlay': '分布叠加', 'chrom_overlay': '色谱叠加', 'trend': '趋势', 'waterfall': '瀑布图',
                'bands': '区段质量分数', 'kinetics': '动力学拟合'}


def figure(library, name, refs, fmt='png', size='single'):
    """单独一张期刊图：返回 (字节, 文件名)。位图 600 dpi。"""
    kind, draw = FIGURES[name]
    data = gpcexp.view(library, refs[0]) if kind == 'experiment' else [_sample(library, ref) for ref in refs] if kind == 'compare' else _sample(library, refs[0])
    title = data['name'] if kind == 'experiment' else data[0][1]['name'] if kind == 'compare' else data[1]['name']
    blob = _bytes(draw(data, size), fmt, 600)
    return blob, f'{title}_{FIGURE_NAMES[name]}_{"单栏" if size == "single" else "双栏" if size == "double" else "宽幅"}.{fmt}'
