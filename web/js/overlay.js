// GPC 叠加比较：几个样品的色谱或分子量分布放在一张图上，左边勾选看哪几条。
// 横轴保留时间或分子量；纵轴扣基线 / 原始；范围：积分区间 / 全程。
// 归一（照原 GPC 软件的六种，再加"选一个峰"）：不归一、峰高、积分区间面积、选一个峰（在图上点，每条取自己那个峰的峰顶）、
//   选一段的最高点、选一段的面积（填或在图上框）、某一点（在图上点）。峰高和面积都只在积分区间里算，不让后面的溶剂峰抢走。
// 对齐（只改显示）：按标记峰——每条谱都有、不随样品变的峰（溶剂峰、系统峰）的峰顶对到参比的峰顶，后台在原始分辨率上算；
//   按整条曲线形状——原件的稳健互相关，只适合同一个样重复进样（样品不一样时会把真实差别对没）。对齐后按各自标准曲线重算一个 Mw 给你看（不存）。
// 对齐和归一是两件事：对齐的那一段（marker）和归一用的那一段 / 峰 / 点（pref.span / pick / at）各管各的，互不带动（原软件把两者绑在一起，是个毛病）。
// 下面可以开一条"减参比"差值图；结果表和参比比，按 Mw 差分档（只是筛查）。"设为默认"把看法存成作图偏好。
import {api, h, icon, toast} from './core.js';
import {COLORS, number, plotly} from './blocks.js';
import {visibleYRange} from './chartfit.js';

// 分子量轴（对数）只在 1、2、5 × 10ⁿ 标数：…500、1,000、2,000、5,000、10,000…，不挤在一起
const M_TICKS = [1, 10, 100, 1e3, 1e4, 1e5, 1e6, 1e7].flatMap((p) => [p, 2 * p, 5 * p]);
const PALETTE = [...COLORS, '#3d6fa8', '#b58a3a', '#7a5aa6', '#2f7d74', '#c0573e'];
const REF_COLOR = '#29261f';
const OPTIONS = {
  x: [['rt', '保留时间'], ['M', '分子量']],
  y: [['net', '扣基线'], ['raw', '原始信号']],
  range: [['region', '积分区间'], ['all', '全程']],
};
const NORMS = [['none', '不归一'], ['peak', '峰高 = 1'], ['area', '积分区间面积 = 1'], ['pick', '选一个峰 = 1'], ['span-peak', '选一段的最高点 = 1'],
  ['span-area', '选一段的面积 = 1'], ['at', '某一点 = 1']];
const NORM_TEXT = {peak: '÷ 积分区间里的峰高', area: '÷ 积分区间面积', pick: '÷ 选的那个峰的峰高', 'span-peak': '÷ 选的那段里的最高点', 'span-area': '÷ 选的那段的面积', at: '÷ 选的那一点'};
const ALIGNS = [['none', '不对齐'], ['marker', '按标记峰（推荐）'], ['shape', '按整条曲线形状']];
const DASHES = [['solid', '实线'], ['dash', '虚线'], ['dot', '点线'], ['dashdot', '点划线']];
const fmt0 = (value) => value == null ? '—' : Math.round(value).toLocaleString('en-US');
const rel = (a, b) => a == null || b == null || !b ? null : (a / b - 1) * 100;
const sec = (min) => `${min > 0 ? '+' : min < 0 ? '−' : ''}${Math.abs(min * 60).toFixed(1)} s`;

// 和参比比：Mw 差多少分档（筛查用）
function grade(diff, analysed) {
  if (!analysed || diff == null) return ['证据不足', 'none'];
  const d = Math.abs(diff);
  return d < 3 ? ['高度相似', 'done'] : d < 10 ? ['中等偏离', 'open'] : ['较大偏离', 'bad'];
}

// 曲线在 where 处的值（线性插值）；一段里的最高点（抛物线细化）；一段的面积（梯形）
const valueAt = (x, y, where) => {
  for (let i = 1; i < x.length; i++) if ((x[i - 1] - where) * (x[i] - where) <= 0) return y[i - 1] + (y[i] - y[i - 1]) * (where - x[i - 1]) / ((x[i] - x[i - 1]) || 1);
  return null;
};
// 归一的分母照原软件：峰高取 |y| 最大，面积取 ∫|y|，某一点取 |y|——原始信号带着负的基线偏移时不会把曲线除成倒过来的
const topIn = (x, y, lo, hi) => {
  let best = -1;
  for (let i = 0; i < x.length; i++) if (x[i] >= lo && x[i] <= hi && (best < 0 || Math.abs(y[i]) > Math.abs(y[best]))) best = i;
  return best < 0 ? null : {x: x[best], y: y[best]};
};
const areaIn = (x, y, lo, hi) => {
  let sum = 0;
  for (let i = 1; i < x.length; i++) if (x[i - 1] >= lo && x[i] <= hi) sum += (x[i] - x[i - 1]) * (Math.abs(y[i]) + Math.abs(y[i - 1])) / 2;
  return sum;
};

export async function gpcOverlay(state, library, ids, {title = '', all = null} = {}) {
  const data = await api(`/gpc-overlay/${encodeURIComponent(library)}?ids=${encodeURIComponent(ids.join(','))}`);
  const saved = data.preference || {};
  const pref = {x: 'rt', y: 'net', range: 'region', stack: false, gap: 0.3, norm: 'area', at: null, pick: null, span: null, diff: false, align: 'none', ...saved};
  if (pref.y === 'norm') { pref.y = 'net'; pref.norm = pref.norm || 'peak'; }             // 旧的偏好："归一"
  if (pref.norm === 'view') pref.norm = 'span-peak';                                         // 旧的"看得见的最高点"
  if (typeof pref.at === 'string') pref.at = parseFloat(pref.at) || null;
  const fresh = (sample, index) => ({...sample, color: PALETTE[index % PALETTE.length], on: index < 10, dx: 0, ky: 1, dy: 0, dash: 'solid', width: 1.8, aligned: null});
  const samples = data.samples.map(fresh);
  let ref = samples.find((s) => /^LT|参比|REF/i.test(s.name))?.id || samples[0]?.id || '';
  let marker = null, picking = null, hot = null, pendingNorm = null;            // pendingNorm：选了要点峰、点位或给一段的归一，还没给之前图照旧
  const normShown = () => pendingNorm || pref.norm;
  const commitNorm = () => { if (pendingNorm) { pref.norm = pendingNorm; pendingNorm = null; } };
  const setSpan = (span) => { pref.span = span; if (pendingNorm?.startsWith('span')) commitNorm(); draw(); };
  if (({pick: pref.pick == null, at: pref.at == null, 'span-peak': !pref.span, 'span-area': !pref.span})[pref.norm]) {
    pendingNorm = pref.norm; pref.norm = 'area'; picking = ['pick', 'at'].includes(pendingNorm) ? 'point' : null;
  }
  const manual = {};                                                      // 人在图上标的标记峰：样品 id → 这条谱自己的保留时间（没平移的）                          // 标记峰的那一段；图上正在选什么（norm-span / marker / point）；鼠标停在哪条
  const plot = h('div.overlay-plot'), list = h('div.overlay-list'), tune = h('div.overlay-tune'), table = h('div.overlay-table');
  const status = h('div.ov-status');

  // ---------- 取数、归一、平移 ----------
  const region = (sample) => [sample.region[0] ?? sample.t[0], sample.region[1] ?? sample.t[sample.t.length - 1]];
  const base = (sample) => {                                              // 这条曲线（没平移、没归一）：全程
    if (pref.x === 'M') return {x: sample.M, y: sample.w};                // 分子量分布本来就扣过基线、按面积归一
    if (pref.y === 'raw') return {x: sample.t, y: sample.s};
    // 扣基线：基线只在积分区间里有意义；区间外面基线照区间两头的值平着延出去（不用外推的那段，外推会把溶剂峰扭成负的）
    const [lo, hi] = region(sample), first = sample.t.findIndex((v) => v >= lo), lastHit = sample.t.findLastIndex((v) => v <= hi);
    const b0 = sample.b[Math.max(first, 0)], b1 = sample.b[Math.max(lastHit, 0)];
    return {x: sample.t, y: sample.t.map((v, i) => sample.s[i] - (v < lo ? b0 : v > hi ? b1 : sample.b[i]))};
  };
  const factor = (sample, x, y) => {                                      // x 已经是显示坐标（平移过）
    if (pref.norm === 'none') return 1;
    const [lo, hi] = pref.x === 'M' ? [-Infinity, Infinity] : region(sample).map((v) => v + sample.dx);
    const span = pref.span ? [Math.min(...pref.span), Math.max(...pref.span)] : null;
    let value = null;
    if (pref.norm === 'peak') value = topIn(x, y, lo, hi)?.y;
    else if (pref.norm === 'area') value = pref.x === 'M' ? 1 : areaIn(x, y, lo, hi);
    else if (pref.norm === 'pick' && pref.pick != null) value = topIn(x, y, pref.pick - (pref.x === 'M' ? pref.pick * 0.15 : 0.15), pref.pick + (pref.x === 'M' ? pref.pick * 0.15 : 0.15))?.y;
    else if (pref.norm === 'span-peak' && span) value = topIn(x, y, ...span)?.y;
    else if (pref.norm === 'span-area' && span) value = areaIn(x, y, ...span);
    else if (pref.norm === 'at' && pref.at != null) value = valueAt(x, y, pref.at);
    return value != null && Number.isFinite(value) && Math.abs(value) > 1e-12 ? Math.abs(value) : null;
  };
  const curve = (sample) => {
    const {x: x0, y: y0} = base(sample);
    const x = pref.x === 'M' ? x0 : x0.map((v) => v + sample.dx);
    const f = factor(sample, x, y0);
    const keep = pref.x === 'M' || pref.range === 'all' ? () => true : (() => { const [lo, hi] = region(sample).map((v) => v + sample.dx); return (v) => v >= lo && v <= hi; })();
    const xs = [], ys = [];
    x.forEach((v, i) => { if (keep(v)) { xs.push(v); ys.push((f ? y0[i] / f : y0[i]) * sample.ky + sample.dy); } });
    if (pref.norm !== 'none' && !f) return {x: [], y: [], missing: true};
    return {x: xs, y: ys, missing: false};
  };
  const needs = () => ({pick: pref.pick == null, 'span-peak': !pref.span, 'span-area': !pref.span, at: pref.at == null})[pref.norm];

  // ---------- 对齐（后台算） ----------
  const align = async () => {
    if (pref.align === 'none') { samples.forEach((s) => { s.dx = 0; s.aligned = null; }); marker = null; draw(); return; }
    if (pref.x === 'M') { toast('对齐只在保留时间轴上有意义；分子量轴用的是各自存的分布'); return; }
    status.textContent = '正在对齐…';
    try {
      const out = await api(`/gpc-align/${encodeURIComponent(library)}`, 'POST', {ids: samples.map((s) => s.id), ref, method: pref.align,
        window: pref.align === 'marker' ? marker : null, manual: pref.align === 'marker' ? manual : null});
      if (pref.align === 'marker') marker = out['区间'] || marker;
      samples.forEach((s) => { const item = out['平移'][s.id] || {}; s.dx = item['平移'] || 0; s.aligned = item; });
      const skipped = samples.filter((s) => s.aligned && s.aligned['能对齐'] === false);
      const refName = samples.find((s) => s.id === ref)?.name;
      status.replaceChildren(pref.align === 'marker'
        ? h('span', {}, marker ? `按 ${marker[0].toFixed(2)}–${marker[1].toFixed(2)} min 那个峰（${out['建议']?.['说明'] ? '自动找的' : '你选的'}）的峰顶对齐，` : '按你标的标记峰对齐，',
          Object.keys(manual).length ? `其中 ${Object.keys(manual).length} 条是你在图上标的，` : '',
          out['参比有峰'] ? `都对到参比的峰顶 ${number(out['对到'], 3)} min。` : `参比 ${refName} 在这一段没有这个峰，都对到这个峰的平均位置 ${number(out['对到'], 3)} min，参比没动。`,
          skipped.length ? h('b.bad-ink', {}, ` ${skipped.map((s) => `${s.name}（${s.aligned['撞边界'] ? '峰顶在这段边上' : s.aligned['形状'] === '凹' ? '这里是凹下去的' : '这里看不出峰'}）`).join('、')} 没对齐。`) : null)
        : h('span', {}, '按积分区间里整条曲线的形状对齐（原软件的稳健互相关）。', h('b.bad-ink', {}, '样品本来就不一样时，这样会把真实差别对没；只用在同一个样重复进样。')));
    } catch (error) { status.textContent = error.message; samples.forEach((s) => { s.dx = 0; s.aligned = null; }); }
    draw();
  };

  // ---------- 画 ----------
  const draw = async () => {
    const Plotly = await plotly();
    const shown = samples.filter((sample) => sample.on);
    const order = [...shown.filter((s) => s.id !== ref), ...shown.filter((s) => s.id === ref)];     // 参比画在最上面
    const curves = new Map(order.map((sample) => [sample.id, curve(sample)]));
    const top = Math.max(1e-12, ...[...curves.values()].flatMap((c) => c.y.filter(Number.isFinite)));
    const step = pref.stack ? (pref.gap || 0.3) * top : 0;
    const diffOn = pref.diff && ref && pref.x === 'rt' && curves.has(ref);
    const traces = order.map((sample) => {
      const c = curves.get(sample.id), isRef = sample.id === ref, offset = step * shown.indexOf(sample);
      const dim = hot && hot !== sample.id;
      return {x: c.x, y: c.y.map((v) => v + offset), name: sample.name + (isRef ? ' · 参比' : ''), type: 'scatter', mode: 'lines', yaxis: 'y', meta: sample.id,
        opacity: dim ? 0.18 : 1, line: {color: isRef ? REF_COLOR : sample.color, width: (isRef ? sample.width + 0.8 : sample.width) + (hot === sample.id ? 1.2 : 0), dash: sample.dash},
        hovertemplate: pref.x === 'M' ? `${sample.name}<br>M %{x:,.0f}<br>%{y:.4g}<extra></extra>` : '%{y:.4g}'};
    });
    if (pref.norm === 'pick' && pref.pick != null) {                                  // 选的峰：每条的峰顶打一个点
      order.forEach((sample) => {
        const c = curves.get(sample.id), w = pref.x === 'M' ? pref.pick * 0.15 : 0.15, apex = topIn(c.x, c.y, pref.pick - w, pref.pick + w);
        if (apex) traces.push({x: [apex.x], y: [apex.y + step * shown.indexOf(sample)], mode: 'markers', type: 'scatter', showlegend: false, hoverinfo: 'skip',
          marker: {size: 8, color: sample.id === ref ? REF_COLOR : sample.color, line: {color: '#fff', width: 1.5}}});
      });
    }
    if (diffOn) {                                                                    // 减参比：每条在自己的点上减去参比的插值
      const r = curves.get(ref);
      order.filter((s) => s.id !== ref).forEach((sample) => {
        const c = curves.get(sample.id);
        traces.push({x: c.x, y: c.y.map((v, i) => { const b = valueAt(r.x, r.y, c.x[i]); return b == null ? null : v - b; }), yaxis: 'y2', type: 'scatter', mode: 'lines', showlegend: false, meta: sample.id, fitY: false,
          opacity: hot && hot !== sample.id ? 0.18 : 1, line: {color: sample.color, width: 1.4}, hovertemplate: `${sample.name} 减参比 %{y:.3g}<extra></extra>`});
      });
    }
    // 标出来：参比的积分区间、标记峰那一段、归一用的那一段 / 那个峰 / 那一点
    const shapes = [], notes = [];
    const band = (x0, x1, color, label) => { shapes.push({type: 'rect', xref: 'x', yref: 'paper', x0, x1, y0: diffOn ? 0.3 : 0, y1: 1, fillcolor: color, line: {width: 0}, layer: 'below'});
      notes.push({x: (x0 + x1) / 2, y: 1, xref: 'x', yref: 'paper', yanchor: 'bottom', text: label, showarrow: false, font: {size: 11, color: '#6b665c'}}); };
    const vline = (x, label, dash = 'dot') => { shapes.push({type: 'line', xref: 'x', yref: 'paper', x0: x, x1: x, y0: diffOn ? 0.3 : 0, y1: 1, line: {color: '#a14a2b', width: 1.2, dash}});
      notes.push({x, y: 1, xref: 'x', yref: 'paper', yanchor: 'bottom', text: label, showarrow: false, font: {size: 11, color: '#a14a2b'}}); };
    if (pref.x === 'rt') {
      const refSample = samples.find((s) => s.id === ref);
      if (refSample && pref.range === 'all') { const [a, b] = region(refSample); band(a, b, 'rgba(41,38,31,0.04)', '参比的积分区间'); }
      if (pref.align === 'marker' && marker && pref.range === 'all') band(marker[0], marker[1], 'rgba(201,100,66,0.12)', '标记峰（对齐用）');   // 只看积分区间时不画，免得把横轴撑宽
    }
    if (['span-peak', 'span-area'].includes(pref.norm) && pref.span) band(Math.min(...pref.span), Math.max(...pref.span), 'rgba(63,124,192,0.10)', '归一用的这段');
    if (pref.norm === 'at' && pref.at != null) vline(pref.at, '这一点 = 1');
    if (pref.norm === 'pick' && pref.pick != null) vline(pref.pick, '这个峰 = 1', 'dash');
    const yTitle = (pref.y === 'raw' ? '原始信号' : '信号（扣基线）') + (pref.x === 'M' ? '：dw/dlogM' : '') + (pref.norm !== 'none' && !needs() ? ' ' + (NORM_TEXT[pref.norm] || '') : '') + (pref.stack ? '，错开' : '');
    const layout = {
      height: diffOn ? 600 : 470, margin: {l: 64, r: 18, t: notes.length ? 34 : 14, b: 52}, paper_bgcolor: '#fbfaf7', plot_bgcolor: '#fbfaf7', showlegend: false,
      dragmode: picking === 'span' || picking === 'marker' ? 'select' : picking ? false : 'zoom', selectdirection: 'h', uirevision: pref.x + pref.range, hovermode: pref.x === 'M' ? 'closest' : 'x unified',
      hoverlabel: {bgcolor: '#fffefb', bordercolor: '#d4cdbe', font: {size: 12}},
      font: {family: 'Microsoft YaHei, sans-serif', size: 12, color: '#29261f'}, shapes, annotations: notes,
      xaxis: pref.x === 'M' ? {title: {text: '分子量 M（PS 当量）'}, type: 'log', autorange: 'reversed', gridcolor: '#ebe6dc', tickformat: ',.0f', tickmode: 'array', tickvals: M_TICKS}
        : {title: {text: '保留时间 / min' + (samples.some((s) => s.dx) ? '（已对齐）' : '')}, gridcolor: '#ebe6dc', zeroline: false, hoverformat: '.2f'},
      yaxis: {title: {text: yTitle}, gridcolor: '#ebe6dc', zeroline: false, domain: diffOn ? [0.3, 1] : [0, 1],
        uirevision: [pref.x, pref.y, pref.range, pref.norm, pref.pick, pref.at, String(pref.span), pref.stack, pref.gap, diffOn].join('|')},
      ...(diffOn ? {yaxis2: {title: {text: '减参比'}, domain: [0, 0.22], gridcolor: '#ebe6dc', zeroline: true, zerolinecolor: '#8f897c'}} : {}),
    };
    await Plotly.react(plot, traces, layout, {displaylogo: false, responsive: true, modeBarButtonsToRemove: ['lasso2d'], toImageButtonOptions: {format: 'png', scale: 2, filename: title || 'GPC叠加'}});
    if (autoY) fitY();
    if (!plot.dataset.wired) {
      plot.dataset.wired = '1';
      plot.on('plotly_relayout', (event) => {                                       // 拖框放大后，横轴范围写回输入框
        const low = event['xaxis.range[0]'], high = event['xaxis.range[1]'];
        if (low != null) { from.value = pref.x === 'M' ? Math.round(10 ** low) : (+low).toFixed(2); to.value = pref.x === 'M' ? Math.round(10 ** high) : (+high).toFixed(2); }
        if (event['xaxis.autorange']) { from.value = ''; to.value = ''; }
        if (autoY && Object.keys(event).some((key) => key.startsWith('xaxis.range') || key === 'xaxis.autorange')) fitY();   // 横轴一缩放，纵轴跟着看得见的曲线走
      });
      plot.on('plotly_click', (event) => {                                          // 在图上点：只在按了对应按钮之后才算，对齐和归一各管各的
        const x = event.points?.[0]?.x;
        if (x == null || !picking) return;
        if (picking === 'marker-click') { marker = [x - 0.25, x + 0.25]; picking = null; align(); return; }
        if (picking.startsWith('manual:')) {
          const sample = samples.find((s) => s.id === picking.slice(7));
          if (sample) manual[sample.id] = x - (sample.dx || 0);                  // 记这条谱自己的保留时间（减掉现在的平移）
          picking = null; align(); return;
        }
        const mode = normShown();
        if (picking === 'point' && ['pick', 'at'].includes(mode)) { if (mode === 'pick') pref.pick = x; else pref.at = x; commitNorm(); picking = null; draw(); }
      });
      plot.on('plotly_selected', (event) => {                                       // 在图上框一段：归一用的段 / 标记峰
        const span = event?.range?.x;
        if (!span) return;
        const range = pref.x === 'M' ? span.map((v) => 10 ** v) : span;
        if (picking === 'marker') { marker = [Math.min(...range), Math.max(...range)]; picking = null; align(); return; }
        if (picking === 'span') { picking = null; setSpan(range); }
      });
    }
    const missing = order.filter((s) => curves.get(s.id).missing).map((s) => s.name);
    hint.textContent = picking === 'span' ? '在图上横着拖一段：归一按这一段算。' : picking === 'marker' ? '在图上横着拖一段：框住每条谱都有的那个峰（溶剂峰、系统峰）。'
      : picking === 'marker-click' ? '在图上点一下标记峰（每条谱都有的溶剂峰、系统峰）：每条在它附近找自己的峰顶。'
      : picking?.startsWith('manual:') ? `在图上点 ${samples.find((s) => s.id === picking.slice(7))?.name} 的标记峰：贴到它附近的峰顶，附近没峰就照你点的位置。`
      : pendingNorm ? (pendingNorm === 'pick' ? '在图上点一下要归一的那个峰（每条取自己在那附近的峰顶）；点之前图照旧。' : pendingNorm === 'at' ? '在图上点一下要归一的那个位置。' : '填一段，或点"在图上框一段"。')
      : missing.length ? `${missing.join('、')} 在归一用的位置找不到峰（或面积是零），先没画；换个峰或区间就回来。` : '';
    refSelect.replaceChildren(...samples.map((s) => h('option', {value: s.id, selected: s.id === ref}, s.name)));
    drawControls(); drawList(); drawTune(); drawTable();
  };

  // ---------- 工具栏 ----------
  const seg = (key, after) => h('div.segs', {}, OPTIONS[key].map(([value, label]) => h('button.seg' + (pref[key] === value ? '.on' : ''), {type: 'button', onclick: () => {
    pref[key] = value; after?.(); draw(); }}, label)));
  const hint = h('span.ov-hint');
  const refSelect = h('select', {onchange: () => { ref = refSelect.value; if (pref.align !== 'none') align(); else draw(); }});
  const spanBox = h('span.ov-span');
  const markerBox = h('span.ov-span');
  const num = (value, onchange, placeholder) => h('input.range-in', {type: 'text', inputmode: 'decimal', value: value ?? '', placeholder, onchange: (e) => onchange(parseFloat(e.target.value))});
  const viewSpan = () => plot.layout?.xaxis?.range && !plot.layout.xaxis.autorange ? plot.layout.xaxis.range.map((v) => pref.x === 'M' ? 10 ** v : +v) : null;
  const controls = h('div.ov-controls');
  const drawControls = () => {
    spanBox.replaceChildren(...(['span-peak', 'span-area'].includes(normShown()) ? [
      num(pref.span?.[0], (v) => setSpan([v, pref.span?.[1] ?? v]), '从'), h('span.quiet.small', {}, '到'),
      num(pref.span?.[1], (v) => setSpan([pref.span?.[0] ?? v, v]), '到'), h('span.quiet.small', {}, pref.x === 'M' ? 'g/mol' : 'min'),
      h('button.btn.ghost.small', {type: 'button', onclick: () => { picking = 'span'; draw(); }}, '在图上框一段'),
      h('button.btn.ghost.small', {type: 'button', title: '用现在图上看得见的这一段', onclick: () => { const v = viewSpan(); if (!v) return toast('先在图上拖框放大到要的那一段'); setSpan(v); }}, '用看得见的这段')]
      : ['pick', 'at'].includes(normShown()) ? [h('button.btn.ghost.small' + (picking === 'point' ? '.on' : ''), {type: 'button', onclick: () => { picking = 'point'; draw(); }},
        normShown() === 'pick' ? (pref.pick == null ? '在图上点一个峰' : `峰在 ${number(pref.pick, pref.x === 'M' ? 0 : 2)}，重新点`) : (pref.at == null ? '在图上点一点' : `在 ${number(pref.at, pref.x === 'M' ? 0 : 2)}，重新点`))] : []));
    markerBox.replaceChildren(...(pref.align === 'marker' && pref.x === 'rt' ? [
      num(marker?.[0], (v) => { marker = [v, marker?.[1] ?? v + 0.5]; align(); }, '从'), h('span.quiet.small', {}, '到'),
      num(marker?.[1], (v) => { marker = [marker?.[0] ?? v - 0.5, v]; align(); }, '到'), h('span.quiet.small', {}, 'min'),
      h('button.btn.ghost.small' + (picking === 'marker-click' ? '.on' : ''), {type: 'button', title: '在图上点一下那个峰，它前后 0.25 min 就是标记峰那一段',
        onclick: () => { picking = 'marker-click'; zoomMarker(); }}, '在图上点标记峰'),
      h('button.btn.ghost.small' + (picking === 'marker' ? '.on' : ''), {type: 'button', onclick: () => { picking = 'marker'; zoomMarker(); }}, '在图上框一段'),
      h('button.btn.ghost.small', {type: 'button', title: '切到全程，放大到标记峰附近，看对得齐不齐', onclick: () => zoomMarker()}, '看标记峰'),
      h('button.btn.ghost.small', {type: 'button', title: '让程序重新找每条谱都有的峰（你一条条标的也清掉）', onclick: () => { marker = null; Object.keys(manual).forEach((key) => delete manual[key]); align(); }}, '重新找')] : []));
    controls.replaceChildren(
      h('div.overlay-bar', {}, h('span.quiet.small', {}, '横轴'), seg('x', () => { picking = null; if (pref.x === 'rt' && pref.align !== 'none') setTimeout(align); }),
        h('span.quiet.small', {}, '纵轴'), seg('y'), pref.x === 'rt' ? [h('span.quiet.small', {}, '范围'), seg('range')] : null,
        h('label.inline.small', {}, h('input', {type: 'checkbox', checked: pref.stack, onchange: (e) => { pref.stack = e.target.checked; draw(); }}), '错开叠放'),
        pref.stack ? h('input.ov-gap', {type: 'range', min: 0.05, max: 1.2, step: 0.05, value: pref.gap || 0.3, title: '错开多少', oninput: (e) => { pref.gap = +e.target.value; draw(); }}) : null,
        pref.x === 'rt' ? h('label.inline.small', {title: '下面加一条：每条减去参比（归一、对齐之后）', }, h('input', {type: 'checkbox', checked: pref.diff, onchange: (e) => { pref.diff = e.target.checked; draw(); }}), '减参比') : null,
        h('span.ov-sep'), h('span.quiet.small', {}, '看'), from, h('span.quiet.small', {}, '到'), to,
        h('button.btn.ghost.small', {type: 'button', onclick: () => { from.value = ''; to.value = ''; zoom(); }}, '复原'),
        h('label.inline.small', {title: '横轴缩放或平移后，纵轴只按看得见的那段曲线自动调整'}, h('input', {type: 'checkbox', checked: autoY,
          onchange: async (e) => { autoY = e.target.checked; if (autoY) fitY(); else (await plotly()).relayout(plot, {'yaxis.autorange': true}); }}), '纵轴自适应'),
        h('span.grow'),
        h('button.btn.ghost.small', {type: 'button', title: '把现在的横轴、纵轴、范围、归一、对齐方式存成默认', onclick: save}, icon('check', 13), '设为默认')),
      h('div.overlay-bar', {}, h('span.quiet.small', {title: '归一只管上下（高低），和对齐是两回事：各有各的那一段'}, '归一（只管高低）'),
        h('select', {onchange: (e) => {
          const value = e.target.value;
          const missing = ({pick: pref.pick == null, at: pref.at == null, 'span-peak': !pref.span, 'span-area': !pref.span})[value];
          if (missing) { pendingNorm = value; picking = ['pick', 'at'].includes(value) ? 'point' : null; }
          else { pref.norm = value; pendingNorm = null; picking = null; }
          draw(); }},
          NORMS.map(([value, label]) => h('option', {value, selected: normShown() === value}, label))), spanBox, h('span.grow'),
        h('a.btn.ghost.small', {href: `#/analysis/new/${encodeURIComponent(samples.filter((s) => s.on).map((s) => s.id).join(','))}`}, icon('compare', 13), '到 GPC 对比里细看')),
      pref.x === 'rt' ? h('div.overlay-bar', {}, h('span.quiet.small', {}, '参比'), refSelect, h('span.quiet.small', {title: '对齐只管左右，用自己的标记峰那一段；和归一用的那一段互不影响'}, '对齐（只管左右）'),
        h('div.segs', {}, ALIGNS.map(([value, label]) => h('button.seg' + (pref.align === value ? '.on' : ''), {type: 'button', title: {
          none: '照原样画', marker: '每条谱都有、不随样品变的峰（溶剂峰、系统峰）的峰顶对到参比；进样之间的流速漂移就去掉了',
          shape: '原软件的稳健互相关：整条曲线形状最像的平移。只适合同一个样重复进样'}[value],
          onclick: () => { pref.align = value; align(); }}, label))), markerBox) : null,
      h('div.ov-notes', {}, hint, status));
  };
  const zoomMarker = async () => {                                         // 切到全程，放大到标记峰附近
    pref.range = 'all'; await draw();
    (await plotly()).relayout(plot, marker ? {'xaxis.range': [marker[0] - 0.6, marker[1] + 0.6], 'yaxis.autorange': true} : {'xaxis.autorange': true});
  };
  const from = h('input.range-in', {type: 'text', placeholder: '从', onchange: () => zoom()});
  const to = h('input.range-in', {type: 'text', placeholder: '到', onchange: () => zoom()});
  const zoom = async () => {
    const Plotly = await plotly();
    const low = parseFloat(from.value), high = parseFloat(to.value);
    if (!Number.isFinite(low) || !Number.isFinite(high)) { Plotly.relayout(plot, {'xaxis.autorange': pref.x === 'M' ? 'reversed' : true}); return; }
    Plotly.relayout(plot, {'xaxis.range': pref.x === 'M' ? [Math.log10(Math.max(low, high)), Math.log10(Math.min(low, high))] : [Math.min(low, high), Math.max(low, high)]});
  };
  const save = async () => {
    await api(`/chart-preference/${encodeURIComponent(library)}/${encodeURIComponent('GPC叠加')}`, 'PUT',
      {x: pref.x, y: pref.y, range: pref.range, stack: pref.stack, gap: pref.gap, norm: pref.norm, span: pref.span, at: pref.at, pick: pref.pick, diff: pref.diff, align: pref.align});
    toast('记下了：以后 GPC 叠加都这样画，助手作图也照这个来');
  };

  // 鼠标停在左边一条上：图上只亮它（只改亮度和粗细，不重画列表，移开就复原）
  const highlight = async () => {
    if (!plot.data) return;
    const Plotly = await plotly();
    const indices = plot.data.map((_, i) => i);
    Plotly.restyle(plot, {opacity: plot.data.map((trace) => hot && trace.meta && trace.meta !== hot ? 0.18 : 1)}, indices);
  };
  // 纵轴自适应（照工作台助手给单样品图做的那样）：横轴缩放、平移后，纵轴只按看得见的那段曲线调；不勾就保留手动的纵轴
  let autoY = true;
  const fitY = async () => {
    const range = plot._fullLayout?.xaxis?.range;
    if (!range) return;
    const xr = pref.x === 'M' ? range.map((v) => 10 ** v) : range;                       // 对数轴的范围是 log10
    const yr = visibleYRange((plot.data || []).filter((trace) => (trace.yaxis || 'y') === 'y'), xr);
    if (!yr) return;
    (await plotly()).relayout(plot, {'yaxis.range': yr, 'yaxis.autorange': false});
  };

  // ---------- 左边的样品 ----------
  const drawList = () => list.replaceChildren(...samples.map((sample) => {
    const isRef = sample.id === ref, shift = sample.dx;
    return h('label.overlay-item' + (sample.on ? '.on' : ''), {title: sample.name,
      onmouseenter: () => { if (!sample.on) return; hot = sample.id; highlight(); }, onmouseleave: () => { if (hot === sample.id) { hot = null; highlight(); } }},
      h('input', {type: 'checkbox', checked: sample.on, onchange: (event) => { sample.on = event.target.checked; draw(); }}),
      h('i', {style: `background:${isRef ? REF_COLOR : sample.color}`}),
      h('span.ov-name', {}, h('span', {}, sample.name), isRef ? h('small.ov-ref', {}, '参比') : null),
      h('small.ov-meta', {}, sample.Mw ? `Mw ${fmt0(sample.Mw)}` : '没分析', shift ? h('b', {title: '对齐平移'}, ' · ' + sec(shift)) : null,
        sample.aligned?.['能对齐'] === false ? h('b.bad-ink', {title: '标记峰那一段里这条没有像样的峰，没对齐'}, ' · 没对齐') : null),
      pref.align === 'marker' && pref.x === 'rt' ? h('span.ov-mark', {},
        manual[sample.id] != null ? h('button.ov-chip', {type: 'button', title: '去掉你标的，回到自动找', onclick: (e) => { e.preventDefault(); delete manual[sample.id]; align(); }},
          `人标 ${number(manual[sample.id], 2)} ×`) : null,
        h('button.ov-chip' + (picking === 'manual:' + sample.id ? '.on' : ''), {type: 'button', title: '自己标这条的标记峰：点了再到图上点它的那个峰',
          onclick: (e) => { e.preventDefault(); picking = 'manual:' + sample.id; zoomMarker(); }}, '标')) : null);
  }));

  // 逐条调整：X 平移、Y 倍数、Y 偏移、颜色、线型、线宽
  const drawTune = () => {
    const field = (sample, key, step) => h('input', {type: 'number', step, value: key === 'dx' ? +(sample.dx * 60).toFixed(2) : sample[key], style: 'width:76px',
      onchange: (e) => { const v = Number(e.target.value); sample[key] = key === 'dx' ? (v || 0) / 60 : (v || (key === 'ky' ? 1 : 0)); draw(); }});
    tune.replaceChildren(h('table.data.compact.edit-table', {}, h('thead', {}, h('tr', {}, ['样品', 'X 平移 / s', 'Y 倍数', 'Y 偏移', '颜色', '线型', '线宽', ''].map((name) => h('th', {}, name)))),
      h('tbody', {}, samples.map((sample) => h('tr' + (sample.on ? '' : '.excluded'), {}, h('td', {}, sample.name), h('td', {}, field(sample, 'dx', 0.5)), h('td', {}, field(sample, 'ky', 0.05)),
        h('td', {}, field(sample, 'dy', 0.01)), h('td', {}, h('input.band-color', {type: 'color', value: sample.color, onchange: (e) => { sample.color = e.target.value; draw(); }})),
        h('td', {}, h('select', {onchange: (e) => { sample.dash = e.target.value; draw(); }}, DASHES.map(([value, label]) => h('option', {value, selected: sample.dash === value}, label)))),
        h('td', {}, h('input', {type: 'number', min: 0.5, max: 6, step: 0.5, value: sample.width, style: 'width:56px', onchange: (e) => { sample.width = Number(e.target.value) || 1.8; draw(); }})),
        h('td', {}, h('button.btn.ghost.small', {type: 'button', title: '平移、倍数、偏移回到原样', onclick: () => { Object.assign(sample, {dx: 0, ky: 1, dy: 0}); draw(); }}, '还原')))))));
  };

  // 结果表 + 和参比比（筛查）+ 对齐后的 Mw
  const drawTable = () => {
    const baseRow = samples.find((s) => s.id === ref)?.result || {};
    const aligned = pref.align === 'marker' && samples.some((s) => s.aligned);
    table.replaceChildren(h('table.data.compact', {}, h('thead', {}, h('tr', {}, ['样品', 'Mn', 'Mw', 'Mz', 'Đ', 'Mp', 'D10', 'D50', 'D90', ...(ref ? ['Mw 比参比', 'D10 比', 'D90 比', '和参比比'] : []),
      ...(aligned ? ['标记峰顶 / min', '平移', '对齐后 Mw', '对齐后比参比'] : [])].map((name) => h('th', {}, name)))),
      h('tbody', {}, samples.map((sample) => {
        const r = sample.result || {}, isRef = sample.id === ref;
        const diff = rel(r.Mw, baseRow.Mw);
        const [label, tone] = isRef ? ['参比', 'current'] : grade(diff, sample.analysed);
        const pct = (value) => value == null || isRef ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(1)} %`;
        const a = sample.aligned || {}, mw2 = a['Mw对齐后'] ?? (sample.analysed && !a['平移'] ? r.Mw : null);
        return h('tr', {}, h('td', {}, h('i.kin-dot', {style: {background: isRef ? REF_COLOR : sample.color}}), sample.name, sample.analysed ? null : h('small.quiet', {}, '（没分析，只有原始信号）')),
          ...['Mn', 'Mw', 'Mz'].map((key) => h('td.num', {}, fmt0(r[key]))), h('td.num', {}, number(r.PDI, 3)), ...['Mp', 'D10', 'D50', 'D90'].map((key) => h('td.num', {}, fmt0(r[key]))),
          ...(ref ? [h('td.num', {}, pct(diff)), h('td.num', {}, pct(rel(r.D10, baseRow.D10))), h('td.num', {}, pct(rel(r.D90, baseRow.D90))), h('td', {}, h('span.st-tag.' + tone, {}, label))] : []),
          ...(aligned ? [h('td.num', {}, a['峰顶'] != null ? number(a['峰顶'], 3) : '—', a['能对齐'] === false ? h('small.bad-ink', {}, ` ${a['撞边界'] ? '在边上' : a['形状']}`)
            : a['人标'] != null ? h('small.quiet', {}, ' 人标') : null),
            h('td.num' + (a['能对齐'] === false ? '.bad-ink' : ''), {}, a['能对齐'] === false ? '没对齐' : sec(a['平移'] || 0)),
            h('td.num', {}, fmt0(mw2)), h('td.num', {}, pct(rel(mw2, baseRow.Mw)))] : []));
      }))),
    ref ? h('p.quiet.small', {}, '和参比比按 Mw 差分档：< 3 % 高度相似，3–10 % 中等偏离，> 10 % 较大偏离。D10 看低分子量那头，D90 看高分子量那头。只是筛查，不是统计检验。') : null,
    aligned ? h('p.quiet.small', {}, '对齐后 Mw：把这条谱按标记峰平移到同一个位置（参比有这个峰就是参比的，没有就是平均位置），积分区间跟着走，再按它自己的标准曲线算（不存，存着的结果不变）。差几秒就能让 Mw 差几个百分点。'
      + '标记峰附近有样品自己的小分子峰（反应液里的溶剂）时，峰顶会被带偏一点，这一列只当参考。') : null);
  };

  const pick = (test) => { samples.forEach((sample) => { sample.on = test(sample); }); draw(); };
  // 再加几条：输编号挑别的样品（参比批、上一个实验的终点……），当场加进来
  const choices = h('datalist', {id: 'gpc-names-' + Math.random().toString(36).slice(2, 7)});
  const adder = h('input.add-sample', {type: 'search', placeholder: '+ 加样品：输编号', list: choices.id, onchange: async () => {
    const found = (all || []).find((row) => row.name === adder.value.trim());
    adder.value = '';
    if (!found || samples.some((sample) => sample.id === found.id)) return;
    const more = await api(`/gpc-overlay/${encodeURIComponent(library)}?ids=${encodeURIComponent(found.id)}`);
    more.samples.forEach((sample) => samples.push({...fresh(sample, samples.length), on: true}));
    if (pref.align !== 'none') align(); else draw();
  }});
  if (!all) api(`/lib/${encodeURIComponent(library)}/records?kind=${encodeURIComponent('GPC样品')}`).then(({rows}) => { all = rows; fillChoices(); });
  const fillChoices = () => choices.replaceChildren(...(all || []).map((row) => h('option', {value: row.name}, row['所属项目'] || '')));
  fillChoices();
  requestAnimationFrame(() => { if (pref.align !== 'none' && pref.x === 'rt') align(); else draw(); });
  return h('div.overlay', {},
    controls,
    h('div.overlay-body', {}, h('div.overlay-side', {},
      h('div.overlay-picks', {}, h('button.btn.ghost.small', {type: 'button', onclick: () => pick(() => true)}, '全选'), h('button.btn.ghost.small', {type: 'button', onclick: () => pick(() => false)}, '全不选'),
        h('button.btn.ghost.small', {type: 'button', onclick: () => pick((s) => s.hours != null || s.id === ref)}, '只看反应时间点')),
      list, adder, choices), plot),
    h('p.quiet.small', {}, '鼠标停在左边一条上，图上就只亮它。在图上拖一个框放大，双击复原；也可以在上面直接填横轴从多少到多少。右上角能存成图片。分子量是聚苯乙烯当量读数；分子量轴用各自存的分布，不受对齐影响。'),
    h('section.block.exp-block', {}, h('header.block-head', {}, h('h3', {}, '结果和参比比')), table),
    h('details.block.overlay-tune-box', {}, h('summary', {}, '逐条调整：平移、倍数、偏移、颜色、线型'), tune));
}
