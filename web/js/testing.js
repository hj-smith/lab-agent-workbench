// 测试：GPC（拖基线锚点和积分区间）、椭偏（剔点、选参比）、n/k（叠加 ±σ、纳入勾）、老化（谱、A500 曲线、导入确认）。
// 人在这里拖、勾、改；算用原算法；点"存"写回库，下次打开就是存下的样子。
import {api, go, h, icon, toast} from './core.js';
import {COLORS, chart, number, plotly, tableBlock} from './blocks.js';
import {chartPref, defaultButton, range, segs, span} from './chartprefs.js';
import {visibleYRange} from './chartfit.js';

const enc = encodeURIComponent;
const fmt0 = (value) => typeof value === 'number' ? Math.round(value).toLocaleString('en-US') : '—';
const debounce = (fn, ms = 350) => { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); }; };

// ---------- GPC：基线锚点、积分区间 ----------

export async function gpcAnalysis(state, sampleId) {
  const library = state.library;
  const box = h('div.gpc-work', {}, h('p.quiet', {}, '正在读谱图…'));
  const view = await api(`/gpc/${enc(library)}/${enc(sampleId)}`);
  const doc = {anchors: (view.anchors || []).map((item) => [Number(item[0]), Number(item[1])]), regions: (view.regions || []).map((item) => ({...item})), curve: view.curve};
  let current = view, adding = false, dirty = false, method = view.method;
  const snapList = () => doc.anchors.map((a, i) => a.snap ? i : -1).filter((i) => i >= 0);     // 拖过、新加、改过时间的锚点：信号让后台取曲线上的值
  const plot = h('div.plot.gpc-plot'), results = h('div.stat-row'), anchorBox = h('div'), regionBox = h('div');
  let autoY = true;
  const fitY = async () => {
    const xr = plot._fullLayout?.xaxis?.range || plot.layout?.xaxis?.range;
    if (!xr) return;
    const yr = visibleYRange(plot.data, xr);
    if (!yr) return;
    const Plotly = await plotly();
    await Plotly.relayout(plot, {'yaxis.range': yr, 'yaxis.autorange': false});
  };
  const autoYControl = h('label.inline.small', {title: '缩放或平移横轴后，纵轴按当前看得见的信号和基线自动调整'},
    h('input', {type: 'checkbox', checked: true, onchange: (event) => { autoY = event.target.checked; if (autoY) fitY(); }}), '纵轴自适应');
  const fitYButton = h('button.btn.ghost.small', {type: 'button', title: '保持横轴范围，重新适配纵轴', onclick: fitY}, '适配当前范围');
  const status = h('span.quiet.small', {}, view.problem ? '这个样还没有正式分析：先套模板或加锚点和区间' : '存着的分析');
  const recompute = debounce(async () => {
    if (!doc.anchors.length || !doc.regions.length) return;
    status.textContent = '正在算…';
    try {
      const out = await api(`/gpc/${enc(library)}/${enc(sampleId)}/try`, 'POST', {anchors: doc.anchors, regions: doc.regions, curve: doc.curve, method, snap: snapList()});
      doc.anchors = out.anchors.map((a) => [Number(a[0]), Number(a[1])]);
      current = {...current, ...out};
      status.textContent = dirty ? '改过了，还没存' : '存着的分析';
      drawResults(); drawTables(); drawPlot(true);
    } catch (error) { status.textContent = '这组设置算不出来：' + error.message; }
  });
  const changed = () => { dirty = true; drawTables(); recompute(); };

  function drawResults() {
    const r = current.result || {};
    results.replaceChildren(...[['Mn', fmt0(r.Mn)], ['Mw', fmt0(r.Mw)], ['Mz', fmt0(r.Mz)], ['Mz+1', fmt0(r['Mz+1'])], ['PDI', number(r.PDI, 3)], ['Mp', fmt0(r.Mp)], ['Mp（色谱峰顶）', fmt0(r['Mp_峰高'])],
      ['D10', fmt0(r.D10)], ['D50', fmt0(r.D50)], ['D90', fmt0(r.D90)],
      ['外推面积', r['外推面积占比'] != null ? (r['外推面积占比'] * 100).toFixed(2) + ' %' : '—'], ['标准曲线', current.curve_name || '—']].map(([label, value]) =>
      h('div.stat' + (label === '外推面积' && r['外推面积占比'] > 0.01 ? '.warn' : ''), {}, h('div.stat-label', {}, label), h('div.stat-value.small', {}, value))));
  }

  async function drawPlot(keepRange) {
    const Plotly = await plotly();
    const t = current.trace?.t || view.trace.t, s = current.trace?.s || view.trace.s;
    const xs = doc.anchors.map((a) => a[0]).concat(doc.regions.flatMap((r) => [Number(r['起']), Number(r['止'])]));
    const ys = s.filter((value, index) => !xs.length || (t[index] >= Math.min(...xs) - 1 && t[index] <= Math.max(...xs) + 1));
    const yr = [Math.min(...ys), Math.max(...ys)], xr = xs.length ? [Math.min(...xs) - 0.8, Math.max(...xs) + 0.8] : [t[0], t[t.length - 1]];
    const shapes = [
      ...doc.regions.map((r, i) => ({type: 'rect', xref: 'x', yref: 'paper', x0: Number(r['起']), x1: Number(r['止']), y0: 0, y1: 1, fillcolor: COLORS[(i + 2) % COLORS.length], opacity: 0.12, line: {width: 1, color: COLORS[(i + 2) % COLORS.length]}, layer: 'below', editable: true})),
      ...doc.anchors.map((a) => ({type: 'circle', xref: 'x', yref: 'y', xsizemode: 'pixel', ysizemode: 'pixel', xanchor: a[0], yanchor: a[1], x0: -5, x1: 5, y0: -5, y1: 5, fillcolor: '#29261f', line: {color: '#fff', width: 1.5}, editable: true})),
      ...(current.valid || []).map((x) => ({type: 'line', xref: 'x', yref: 'paper', x0: x, x1: x, y0: 0, y1: 1, line: {color: '#968f82', width: 1, dash: 'dot'}, editable: false})),
    ];
    const layout = {paper_bgcolor: '#fbfaf7', plot_bgcolor: '#fbfaf7', height: 440, margin: {l: 64, r: 16, t: 10, b: 46}, showlegend: true, legend: {orientation: 'h', y: -0.18},
      font: {family: 'Microsoft YaHei, sans-serif', size: 12, color: '#29261f'}, xaxis: {title: {text: '保留时间 / min'}, gridcolor: '#ebe6dc', range: keepRange && plot.layout ? plot.layout.xaxis.range : xr},
      yaxis: {title: {text: '信号'}, gridcolor: '#ebe6dc', zeroline: false, range: keepRange && plot.layout ? plot.layout.yaxis.range : [yr[0] - (yr[1] - yr[0]) * 0.08, yr[1] + (yr[1] - yr[0]) * 0.08]}, shapes, dragmode: 'zoom'};
    const traces = [{x: t, y: s, name: '原信号', mode: 'lines', line: {color: '#3f7cc0', width: 1.6}, hovertemplate: '%{x:.3f} min<br>%{y:.2f}<extra></extra>'}];
    if (current.baseline) traces.push({x: current.baseline.t, y: current.baseline.b, name: '基线', mode: 'lines', line: {color: '#c96442', width: 1.6, dash: 'dash'}, hoverinfo: 'skip'});
    await Plotly.react(plot, traces, layout, {displaylogo: false, responsive: true, edits: {shapePosition: true}, modeBarButtonsToRemove: ['select2d', 'lasso2d']});
    if (!plot.__wired) {
      plot.__wired = true;
      plot.on('plotly_relayout', (event) => {           // 拖了锚点或区间
        if (autoY && Object.keys(event).some((key) => key.startsWith('xaxis.range') || key === 'xaxis.autorange')) fitY();
        let moved = false;
        for (const [key, value] of Object.entries(event)) {
          const m = key.match(/^shapes\[(\d+)\]\.(x0|x1|y0|y1|xanchor|yanchor)$/);
          if (!m) continue;
          const index = Number(m[1]), shape = plot.layout.shapes[index];
          if (index < doc.regions.length) { doc.regions[index]['起'] = +Math.min(shape.x0, shape.x1).toFixed(4); doc.regions[index]['止'] = +Math.max(shape.x0, shape.x1).toFixed(4); moved = true; }
          else if (index < doc.regions.length + doc.anchors.length) { const a = doc.anchors[index - doc.regions.length]; a[0] = +(shape.xsizemode === 'pixel' ? shape.xanchor : (shape.x0 + shape.x1) / 2).toFixed(5); a.snap = true; moved = true; }
        }
        if (moved) { doc.anchors.sort((a, b) => a[0] - b[0]); changed(); }
      });
      plot.on('plotly_restyle', () => { if (autoY) fitY(); });
      plot.on('plotly_click', (event) => {               // 加锚点：在谱图上点一下
        if (!adding || !event.points?.length) return;
        const point = event.points[0];
        const a = [+point.x.toFixed(5), +point.y.toFixed(4)]; a.snap = true; doc.anchors.push(a); doc.anchors.sort((p, q) => p[0] - q[0]); changed(); drawPlot(true);
      });
    }
    if (autoY) await fitY();
  }

  function drawTables() {
    anchorBox.replaceChildren(h('table.data.compact.edit-table', {}, h('thead', {}, h('tr', {}, h('th', {}, '#'), h('th', {}, '时间 / min'), h('th', {}, '信号'), h('th', {}))),
      h('tbody', {}, doc.anchors.map((a, i) => h('tr', {}, h('td', {}, i + 1),
        h('td', {}, h('input', {type: 'number', step: 'any', value: a[0], onchange: (e) => { a[0] = Number(e.target.value); a.snap = true; doc.anchors.sort((p, q) => p[0] - q[0]); changed(); drawPlot(true); }})),
        h('td.num.quiet', {title: '锚点贴着曲线：信号取这个时间的曲线值'}, number(a[1], 3)),
        h('td', {}, h('button.btn.ghost.small', {type: 'button', title: '去掉', onclick: () => { doc.anchors.splice(i, 1); changed(); drawPlot(true); }}, '×')))))));
    regionBox.replaceChildren(h('table.data.compact.edit-table', {}, h('thead', {}, h('tr', {}, ['名称', '起 / min', '止 / min', '峰方向', '计入分子量', '范围策略', ''].map((name) => h('th', {}, name)))),
      h('tbody', {}, doc.regions.map((r, i) => h('tr', {},
        h('td', {}, h('input', {type: 'text', value: r['名称'] || '', style: 'width:90px', onchange: (e) => { r['名称'] = e.target.value; dirty = true; }})),
        ...['起', '止'].map((key) => h('td', {}, h('input', {type: 'number', step: 'any', value: r[key], onchange: (e) => { r[key] = Number(e.target.value); changed(); drawPlot(true); }}))),
        h('td', {}, h('select', {onchange: (e) => { r['峰方向'] = e.target.value; changed(); }}, ['正', '负'].map((v) => h('option', {selected: (r['峰方向'] || '正') === v}, v)))),
        h('td', {}, h('input', {type: 'checkbox', checked: r['计入分子量'] !== false, onchange: (e) => { r['计入分子量'] = e.target.checked; changed(); }})),
        h('td', {}, h('select', {onchange: (e) => { r['范围策略'] = e.target.value; changed(); }}, ['只算曲线范围内', '外推', '超范围报错'].map((v) => h('option', {selected: (r['范围策略'] || '只算曲线范围内') === v}, v)))),
        h('td', {}, h('button.btn.ghost.small', {type: 'button', onclick: () => { doc.regions.splice(i, 1); changed(); drawPlot(true); }}, '×')))))),
      h('button.btn.ghost.small', {type: 'button', onclick: () => { const t = doc.anchors.length ? [doc.anchors[0][0], doc.anchors[doc.anchors.length - 1][0]] : [10, 20];
        doc.regions.push({名称: `区间${doc.regions.length + 1}`, 起: t[0], 止: t[1], 峰方向: '正', 计入分子量: true, 范围策略: view.new_region_strategy || '外推'}); changed(); drawPlot(true); }}, icon('plus', 13), '加区间'));
  }

  const addButton = h('button.btn.small', {type: 'button', onclick: (e) => { adding = !adding; e.currentTarget.classList.toggle('primary', adding); e.currentTarget.lastChild.textContent = adding ? '点谱图加锚点（再点这里结束）' : '加锚点'; }}, icon('plus', 13), h('span', {}, '加锚点'));
  // 停用、过期的曲线在名字后面标出来，选了提醒一句（还是能用，人定）
  const curveNote = (c) => c.off ? '（停用）' : c.expired ? '（过期）' : '';
  const curveSelect = h('select', {onchange: (e) => { doc.curve = e.target.value; changed(); const c = view.curves.find((x) => x.id === doc.curve); if (curveNote(c)) toast(`这条标准曲线${curveNote(c)}，确定要用它吗？`); }},
    view.curves.map((c) => h('option', {value: c.id, selected: c.id === doc.curve}, c.name + curveNote(c))));
  // 沿用分析模板，或抄同项目别的样品的做法（锚点按设置贴到这条谱上）；只填进草稿，点"存"才写回
  const groups = {};
  view.templates.forEach((t) => (groups[t.project || '其它'] ||= []).push(t));
  const methodSelect = h('select', {onchange: (e) => { method = e.target.value; changed(); }},
    [['pchip_anchors', 'PCHIP（默认）'], ['linear_anchors', '多点直线'], ['cubic_spline_anchors', '自然三次样条']].map(([value, label]) => h('option', {value, selected: method === value}, label)));
  const templateSelect = h('select', {onchange: async (e) => {
    if (!e.target.value) return;
    const t = await api(`/gpc/${enc(library)}/${enc(sampleId)}/template/${enc(e.target.value)}`);
    doc.anchors = t.anchors.map((a) => [Number(a[0]), Number(a[1])]); doc.regions = t.regions.map((r) => ({...r}));
    method = t.method || method; methodSelect.value = method;
    changed(); drawPlot(false); e.target.value = '';
    const moved = (t.moved || []).map((m) => `${m['从']} → ${m['到']} min`).join('、');
    toast(`填进了${t.kind === '样品' ? '样品' : '模板'}「${t.name}」的做法` + (moved ? `；落在峰上的锚点挪到了附近谷底（${moved}），看一眼再存` : '，看一眼再存'));
  }}, h('option', {value: ''}, '沿用模板或别的样品…'),
    view.siblings.length ? h('optgroup', {label: '抄同项目的样品'}, view.siblings.map((s) => h('option', {value: s.id}, s.name))) : null,
    Object.entries(groups).map(([project, list]) => h('optgroup', {label: '分析模板 · ' + project}, list.map((t) => h('option', {value: t.id}, t.name)))));
  const asTemplate = async () => {
    const name = prompt('存成模板，起个名字：', `${view.name} 的做法`); if (!name) return;
    await api(`/gpc/${enc(library)}/${enc(sampleId)}/as-template`, 'POST', {name, anchors: doc.anchors, regions: doc.regions, method});
    toast(`存成模板「${name}」了，批处理和别的样品都能用`);
  };
  const save = async () => {
    const out = await api(`/gpc/${enc(library)}/${enc(sampleId)}`, 'PUT', {anchors: doc.anchors, regions: doc.regions, curve: doc.curve, method, snap: snapList()});
    current.result = out.result; dirty = false; drawResults(); toast('基线和区间已存，结果已更新');
    status.replaceChildren('已存，分子量和分布已更新', out.demo ? h('input.why-in', {type: 'text', placeholder: '这次为什么这样调？（可不填，助手学你的做法时用）',   // 跟你学：理由最有用
      onchange: async (event) => { if (event.target.value.trim()) { await api(`/demos/${enc(library)}/${out.demo}/why`, 'POST', {text: event.target.value}); event.target.replaceWith(h('span.quiet', {}, ' · 记下了理由')); } }}) : '');
  };
  box.replaceChildren(
    h('div.gpc-bar', {}, status, h('label.inline', {}, '标准曲线', curveSelect),
      h('label.inline', {}, '基线连接', methodSelect),
      templateSelect, addButton,
      h('button.btn.ghost.small', {type: 'button', title: '把现在的锚点、区间、连接方式存成分析模板（含没存的改动）', onclick: asTemplate}, '存成模板'),
      h('button.btn.primary.small', {type: 'button', onclick: save}, icon('check', 13), '存')),
    results, h('div.gpc-bar', {}, autoYControl, fitYButton), plot,
    h('p.block-note', {}, '拖圆点改锚点（锚点会贴回曲线上），拖色块的边改积分区间，点"加锚点"后在谱图上点一下加一个；拖框放大，双击复原；默认纵轴随横轴范围自动适配，取消"纵轴自适应"可保留手动范围，也可点"适配当前范围"单次调整。灰色虚线是标准曲线的有效范围。数随改随算（原算法 v2 积分），点"存"才写回。'),
    h('div.blocks', {}, h('section.block', {}, h('header.block-head', {}, h('h3', {}, '基线锚点')), anchorBox),
      h('section.block', {}, h('header.block-head', {}, h('h3', {}, '积分区间')), regionBox)));
  drawResults(); drawTables(); requestAnimationFrame(() => drawPlot(false));
  return box;
}

// ---------- GPC 批处理：一批样品套同一套做法，先看"现在 → 套用后"，勾了再存 ----------

const pct = (value) => value == null ? '—' : `${value > 0 ? '+' : ''}${(value * 100).toFixed(1)} %`;

export async function gpcBatch(state, ids, rows, {keep = false} = {}) {
  const library = state.library;
  const picked = rows.filter((row) => ids.includes(row.id));
  const projects = {};
  picked.forEach((row) => { projects[row['所属项目'] || ''] = (projects[row['所属项目'] || ''] || 0) + 1; });
  const mainProject = Object.entries(projects).sort((a, b) => b[1] - a[1])[0]?.[0] || '';
  const [templates, curves, snapState] = await Promise.all([api(`/lib/${enc(library)}/records?kind=${enc('GPC分析模板')}`), api(`/lib/${enc(library)}/records?kind=${enc('GPC标准曲线')}`),
    api(`/gpc-batch/${enc(library)}/snap`, 'PUT', {})]);
  const tplList = templates.rows.sort((a, b) => ((a['所属项目'] || '') !== mainProject) - ((b['所属项目'] || '') !== mainProject) || String(b.updated_at).localeCompare(String(a.updated_at)));
  const tplGroups = {};
  tplList.forEach((t) => (tplGroups[t['所属项目'] || '其它'] ||= []).push(t));
  const withAnalysis = rows.filter((row) => row['积分区间']?.length && (row['所属项目'] || '') === mainProject);
  const source = h('select', {},
    Object.entries(tplGroups).map(([project, list]) => h('optgroup', {label: '分析模板 · ' + project}, list.map((t) => h('option', {value: t.id}, t.name)))),
    withAnalysis.length ? h('optgroup', {label: '抄这个项目的样品'}, withAnalysis.map((s) => h('option', {value: s.id}, s.name))) : null,
    h('optgroup', {label: '不换做法'}, h('option', {value: ''}, '各自现在的做法（只换标准曲线）')));
  const curve = h('select', {}, h('option', {value: ''}, '各自现在的'), h('option', {value: '跟着来源'}, '跟着抄的那个样品'),
    h('optgroup', {label: '都换成'}, curves.rows.map((c) => h('option', {value: c.id}, c.name + (c['停用'] ? '（停用）' : c['有效期至'] && String(c['有效期至']).slice(0, 10) < new Date().toISOString().slice(0, 10) ? '（过期）' : '')))));
  curve.addEventListener('change', () => { if (/（停用）|（过期）/.test(curve.selectedOptions[0]?.textContent || '')) toast('选的这条标准曲线' + curve.selectedOptions[0].textContent.match(/（.+）/)[0] + '，确定要用它吗？'); });
  if (keep) source.value = '';
  const snap = h('input', {type: 'checkbox', checked: keep || snapState.snap, onchange: () => api(`/gpc-batch/${enc(library)}/snap`, 'PUT', {snap: snap.checked})});
  const status = h('span.quiet.small');
  const table = h('div.batch-table'), compare = h('div.batch-compare'), foot = h('div.batch-foot');
  let preview = [], chosen = new Set(), saved = {};

  const run = async () => {
    status.textContent = `正在用原算法算 ${ids.length} 条…`; table.replaceChildren(); compare.replaceChildren(); foot.replaceChildren();
    try {
      const out = await api(`/gpc-batch/${enc(library)}/preview`, 'POST', {ids, source: source.value || null, curve: curve.value || null, snap: snap.checked});
      preview = out.rows; saved = {};
      chosen = new Set(preview.filter((row) => !row.problems.length && row.after).map((row) => row.id));
      const flagged = preview.filter((row) => row.problems.length).length;
      status.textContent = `算好了：${preview.length} 条，${flagged ? `${flagged} 条要看一眼（默认没勾）` : '都没发现问题'}。点一行看"现在 → 套用后"。`;
      draw();
      if (preview[0]) show(preview.find((row) => row.problems.length) || preview[0]);
    } catch (error) { status.textContent = '算不出来：' + error.message; }
  };

  function draw() {
    const mixed = new Set(preview.map((row) => row.project)).size > 1;
    const box = (row) => h('input', {type: 'checkbox', checked: chosen.has(row.id), disabled: !row.after || !!saved[row.id],
      onclick: (e) => e.stopPropagation(), onchange: (e) => { e.target.checked ? chosen.add(row.id) : chosen.delete(row.id); drawFoot(); }});
    table.replaceChildren(h('table.data.compact.batch', {}, h('thead', {}, h('tr', {}, ['', '样品', '现在 Mw', '现在 PDI', '套用后 Mn', '套用后 Mw', '套用后 PDI', 'Mw 变化', '外推面积', '要看一眼的地方'].map((name) => h('th', {}, name)))),
      h('tbody', {}, preview.map((row) => h('tr' + (row.problems.length ? '.flagged' : '') + (saved[row.id] ? '.saved' : ''), {onclick: () => show(row)},
        h('td', {}, box(row)), h('td', {}, h('b', {}, row.name), mixed && row.project ? h('small.quiet', {}, ' ' + row.project) : null),
        h('td.num', {}, fmt0(row.before?.Mw)), h('td.num', {}, number(row.before?.PDI, 3)),
        h('td.num', {}, fmt0(row.after?.Mn)), h('td.num', {}, h('b', {}, fmt0(saved[row.id]?.Mw ?? row.after?.Mw))), h('td.num', {}, number(row.after?.PDI, 3)),
        h('td.num' + (Math.abs(row.change || 0) > 0.1 ? '.warn-ink' : Math.abs(row.change || 0) < 0.005 ? '.quiet' : ''), {}, pct(row.change)),
        h('td.num' + ((row.after?.['外推面积占比'] || 0) > 0.01 ? '.warn-ink' : '.quiet'), {}, row.after ? ((row.after['外推面积占比'] || 0) * 100).toFixed(2) + ' %' : '—'),
        h('td.small', {}, saved[row.id] ? h('span.st-tag.done', {}, saved[row.id].ok ? `已存 · 第 ${saved[row.id].version} 版` : '没存上：' + saved[row.id].problem)
          : [...row.problems, ...row.notes].join('；') || h('span.quiet', {}, '—')))))));
    drawFoot();
  }

  function drawFoot() {
    const open = preview.filter((row) => row.after && !saved[row.id]);
    foot.replaceChildren(
      h('button.btn.ghost.small', {type: 'button', onclick: () => { open.forEach((row) => chosen.add(row.id)); draw(); }}, '全选'),
      h('button.btn.ghost.small', {type: 'button', onclick: () => { chosen = new Set(open.filter((row) => !row.problems.length).map((row) => row.id)); draw(); }}, '只选没问题的'),
      h('button.btn.ghost.small', {type: 'button', onclick: () => { chosen.clear(); draw(); }}, '全不选'), h('span.grow'),
      h('span.quiet.small', {}, `勾了 ${[...chosen].filter((id) => !saved[id]).length} 条`),
      h('button.btn.primary', {type: 'button', disabled: ![...chosen].some((id) => !saved[id]), onclick: save}, icon('check', 14), '存勾上的'));
  }

  async function save() {
    const list = preview.filter((row) => chosen.has(row.id) && !saved[row.id] && row.after);
    status.textContent = `正在存 ${list.length} 条，重算分子量和分布…`;
    const out = await api(`/gpc-batch/${enc(library)}/save`, 'POST', {rows: list.map((row) => ({id: row.id, plan: row.plan}))});
    out.rows.forEach((row) => { saved[row.id] = row; chosen.delete(row.id); });
    const bad = out.rows.filter((row) => !row.ok).length;
    status.textContent = `存好了 ${out.rows.length - bad} 条` + (bad ? `，${bad} 条没存上（看最后一列）` : '') + '。分析版本各加了 1，分子量和分布都重算存好了。';
    toast(status.textContent); draw();
  }

  async function show(row) {
    compare.replaceChildren(h('p.quiet.small', {}, `正在画 ${row.name}…`));
    const data = await api(`/gpc-batch/${enc(library)}/compare/${enc(row.id)}`, 'POST', {plan: row.plan});
    const Plotly = await plotly();
    const plot = h('div.plot.batch-plot');
    // 看积分区间附近（两边各多 1.5 min）：差别都在这一段；纵轴按这一段的信号和两条基线定。双击看全谱
    const t = data.trace.t, s = data.trace.s;
    const edges = [...data.now.regions, ...data.next.regions].flatMap((r) => [Number(r['起']), Number(r['止'])]);
    const xs = edges.length ? edges : [...(data.now.anchors || []), ...(data.next.anchors || [])].map((a) => Number(a[0]));
    const shifted = (row.plan.moved || []).map((m) => Number(m['到']));            // 挪过的锚点也要在图里
    const xr = xs.length ? [Math.min(...xs, ...shifted.map((x) => x + 0.3)) - 1.5, Math.max(...xs, ...shifted.map((x) => x - 1.2)) + 1.5] : [t[0], t[t.length - 1]];
    const inside = (line) => line ? line.b.filter((value, index) => line.t[index] >= xr[0] && line.t[index] <= xr[1]) : [];
    const ys = [...s.filter((value, index) => t[index] >= xr[0] && t[index] <= xr[1]), ...inside(data.now.baseline), ...inside(data.next.baseline)];
    const pad = (Math.max(...ys) - Math.min(...ys)) * 0.08;
    const band = (regions, y0, y1, color) => regions.map((r) => ({type: 'rect', xref: 'x', yref: 'paper', x0: Number(r['起']), x1: Number(r['止']), y0, y1, fillcolor: color, opacity: 0.55, line: {width: 0}}));
    const traces = [{x: t, y: s, name: '原信号', mode: 'lines', line: {color: '#3f7cc0', width: 1.5}, hovertemplate: '%{x:.3f} min<br>%{y:.2f}<extra></extra>'}];
    if (data.now.baseline) traces.push({x: data.now.baseline.t, y: data.now.baseline.b, name: '现在的基线', mode: 'lines', line: {color: '#8f897c', width: 1.6, dash: 'dash'}, hoverinfo: 'skip'});
    if (data.next.baseline) traces.push({x: data.next.baseline.t, y: data.next.baseline.b, name: '套用后的基线', mode: 'lines', line: {color: '#c96442', width: 1.8}, hoverinfo: 'skip'});
    // 下面一小格：套用后基线减现在的基线（两条线挨得太近时，差别在这里看得见）
    if (data.now.baseline && data.next.baseline) {
      const before = new Map(data.now.baseline.t.map((x, i) => [x, data.now.baseline.b[i]]));
      const dt = [], db = [];
      data.next.baseline.t.forEach((x, i) => { const a = data.next.baseline.b[i], b = before.get(x); if (a != null && b != null && x >= xr[0] && x <= xr[1]) { dt.push(x); db.push(a - b); } });
      traces.push({x: dt, y: db, name: '基线差（套用后 − 现在）', mode: 'lines', line: {color: '#b5841c', width: 1.6}, yaxis: 'y2', hovertemplate: '%{x:.3f} min<br>差 %{y:.1f}<extra></extra>'});
    }
    traces.push({x: data.now.anchors.map((a) => a[0]), y: data.now.anchors.map((a) => a[1]), name: '现在的锚点', mode: 'markers', marker: {color: '#fff', size: 8, line: {color: '#6b665c', width: 1.5}}},
      {x: data.next.anchors.map((a) => a[0]), y: data.next.anchors.map((a) => a[1]), name: '套用后的锚点', mode: 'markers', marker: {color: '#c96442', size: 8, line: {color: '#fff', width: 1.5}}});
    const moved = (row.plan.moved || []).map((m) => ({m, a: data.next.anchors.find((a) => Math.abs(a[0] - m['到']) < 0.006)})).filter((item) => item.a);
    if (moved.length) traces.push({x: moved.map((item) => item.a[0]), y: moved.map((item) => item.a[1]), name: '从峰上挪到谷底的锚点', mode: 'markers',
      marker: {symbol: 'diamond-open', color: '#b5841c', size: 13, line: {width: 2}}, text: moved.map((item) => `从 ${item.m['从']} min 挪到谷底 ${item.m['到']} min`),
      hovertemplate: '%{text}<extra></extra>'});
    compare.replaceChildren(h('div.batch-compare-head', {}, h('h3', {}, `${row.name}：现在 → 套用后`),
      h('span.quiet.small', {}, `Mw ${fmt0(row.before?.Mw)} → ${fmt0(row.after?.Mw)}（${pct(row.change)}）`), data.next.problem ? h('span.warn-ink.small', {}, '算不出来：' + data.next.problem) : null,
      h('span.grow'), h('a.small', {href: `#/testing/gpc/${enc(row.id)}`}, '去这个样品里细调')),
      h('p.quiet.small', {}, '顶上两条色带是积分区间：灰的是现在的，陶土色的是套用后的。灰色虚线是现在的基线，陶土色实线是套用后的。只看积分区间附近，双击看全谱。下面一小格是两条基线的差（套用后减现在），偏在零线上下就说明基线挪了。'), plot);
    await Plotly.newPlot(plot, traces, {paper_bgcolor: '#fbfaf7', plot_bgcolor: '#fbfaf7', height: 460, margin: {l: 64, r: 16, t: 26, b: 46},
      font: {family: 'Microsoft YaHei, sans-serif', size: 12, color: '#29261f'}, showlegend: true, legend: {orientation: 'h', y: -0.2},
      xaxis: {title: {text: '保留时间 / min'}, gridcolor: '#ebe6dc', range: xr, anchor: 'y2'}, yaxis: {title: {text: '信号'}, gridcolor: '#ebe6dc', zeroline: false, domain: [0.3, 1], range: [Math.min(...ys) - pad, Math.max(...ys) + pad * 2.5]},
      yaxis2: {title: {text: '基线差'}, gridcolor: '#ebe6dc', zeroline: true, zerolinecolor: '#8f897c', rangemode: 'tozero', domain: [0, 0.22]},
      shapes: [...band(data.now.regions, 0.965, 0.99, '#b9b2a4'), ...band(data.next.regions, 0.935, 0.96, '#c96442'),
        ...(data.valid || []).map((x) => ({type: 'line', xref: 'x', yref: 'paper', x0: x, x1: x, y0: 0.3, y1: 0.92, line: {color: '#968f82', width: 1, dash: 'dot'}}))]},
    {displaylogo: false, responsive: true, modeBarButtonsToRemove: ['select2d', 'lasso2d']});
  }

  const box = h('div.gpc-batch', {},
    h('div.gpc-bar.batch-bar', {}, h('label.inline', {}, '做法', source), h('label.inline', {}, '标准曲线', curve),
      h('label.inline', {title: '库里人调过的样，锚点几乎都正好落在自己的曲线上；套别处来的做法时把锚点贴到这条谱上，和手调一样。关掉就原样用模板里的信号值（旧 GPC 软件的做法）。会记住。'}, snap, '锚点贴到每条谱上'),
      h('button.btn.primary.small', {type: 'button', onclick: run}, icon('spark', 13), '预览')),
    h('p.block-note', {}, '先预览：每条用原算法算出套用后的分子量，和现在的比。没问题的默认勾上；要看一眼的（算不出、锚点在谱外、外推超过 1 %、Mw 变了 10 % 以上）默认不勾，点那一行看现在和套用后的基线、区间。勾好点"存勾上的"才写回。'),
    status, table, foot, compare);
  run();
  return box;
}

// ---------- 椭偏：一天的膜、6 点表剔点、组和参比 ----------

// 读数表里旧程序的写法翻成人话：before_solva → 处理前，after_solva → 处理后（SolvA），true/false → 是/空
function readable(value) {
  if (value == null || value === false || value === 'false' || value === 'False') return '';
  if (value === true || value === 'true' || value === 'True') return '是';
  const text = String(value), stage = text.match(/^(before|after)(?:_(.+))?$/i);
  if (stage) return (stage[1].toLowerCase() === 'before' ? '处理前' : '处理后') + (stage[2] && stage[1].toLowerCase() === 'after' ? `（${stage[2].toUpperCase()}）` : '');
  return text;
}

export async function ellDay(state, day) {
  const library = state.library;
  const data = await api(`/ell/${enc(library)}/${enc(day)}`);
  const verdictTone = {better: 'good', within: 'good', worse: 'bad', not_evaluable: ''};
  const verdictText = {better: '更好', within: '容差内', worse: '超容差', not_evaluable: '没法判'};
  const films = data.films.map((film) => {
    const excluded = new Map((film.excluded || []).map((item) => [item['读数id'], item]));
    const idIndex = film.columns.indexOf('读数id');
    const flags = new Set(data.quality.filter((q) => q['样品编号'] === film.code).map((q) => q['读数id']));
    const outliers = new Set(film.stats['建议离群读数'] || []);
    const stage = (film.stats['各阶段'] || [])[0] || {};
    const summary = h('div.stat-row', {}, [['处理前', stage['处理前']?.['均值 nm'], 'nm'], ['CV', stage['处理前']?.['CV %'], '%'], ['处理后', stage['处理后']?.['均值 nm'], 'nm'], ['损失', stage['损失率 %'], '%']].map(([label, value, unit]) =>
      h('div.stat', {}, h('div.stat-label', {}, label), h('div.stat-value.small', {}, typeof value === 'number' ? value.toFixed(2) : '—', h('small', {}, ' ' + unit)))));
    const toggle = async (rid) => {
      excluded.has(rid) ? excluded.delete(rid) : excluded.set(rid, {读数id: rid, 范围: '整点', 原因: '人工剔除'});
      const stats = await api(`/ell/${enc(library)}/film/${enc(film.id)}`, 'PUT', {items: [...excluded.values()]});
      film.stats = stats; toast(excluded.has(rid) ? '已剔除，重算了' : '已恢复，重算了'); state.rerender?.();
    };
    return h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, h('a', {href: `#/record/${enc(library)}/${enc(film.id)}`}, film.name)),
      h('span.quiet.small', {}, film.condition_text)), summary,
      h('div.tablewrap.flat', {}, h('table.data.compact', {}, h('thead', {}, h('tr', {}, h('th', {}, '剔除'), film.columns.filter((c) => c !== '读数id').map((c) => h('th', {}, c)))),
        h('tbody', {}, film.rows.map((row) => {
          const rid = row[idIndex];
          return h('tr' + (excluded.has(rid) ? '.excluded' : '') + (flags.has(rid) || outliers.has(rid) ? '.flagged' : ''), {title: outliers.has(rid) ? '建议离群' : flags.has(rid) ? '有质量提示' : ''},
            h('td', {}, h('input', {type: 'checkbox', checked: excluded.has(rid), onchange: () => toggle(rid)})),
            row.filter((_, i) => i !== idIndex).map((value) => h('td' + (typeof value === 'number' ? '.num' : ''), {}, typeof value === 'number' ? number(value) : readable(value))));
        })))));
  });
  const groups = data.groups.map((group) => {
    const v = group['判定'] || {};
    const options = [...(group['参比推荐']?.['候选'] || [])];
    const select = h('select', {onchange: async (e) => { await api(`/ell/${enc(library)}/reference`, 'PUT', {group: group['组key'], reference: e.target.value || null}); toast('参比已改，重新判定'); state.rerender?.(); }},
      h('option', {value: ''}, '用推荐的'), options.map((o) => h('option', {value: o['参比key'], selected: o['参比key'] === group['参比key']}, `${o['参比']}（${o['日期差'] || ''}）`)));
    return h('tr', {}, h('td', {}, group['组']), h('td.num', {}, number(group['处理前']?.['均值 nm'], 4)), h('td.num', {}, number(group['损失率 %'], 3)),
      h('td', {}, select, h('div.quiet.small', {}, group['参比推荐']?.['状态'] || '')),
      h('td', {}, v['判定'] ? h('span.st-tag.' + (verdictTone[v['判定']] === 'good' ? 'done' : verdictTone[v['判定']] === 'bad' ? 'off' : 'todo'), {}, verdictText[v['判定']] || v['判定']) : '—'),
      h('td.num', {}, number(v['处理前膜厚差 nm'], 3)), h('td.num', {}, number(v['损失率差 百分点'], 3)),
      h('td', {}, v['各项判定'] ? Object.entries(v['各项判定']).filter(([, value]) => value === 'worse').map(([key]) => h('span.mini-chip.bad', {}, key)) : ''));
  });
  return h('div.view', {},
    h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, `${day} 的组和参比判定（12 项容差）`)),
      h('div.tablewrap.flat', {}, h('table.data.compact', {}, h('thead', {}, h('tr', {}, ['组', '处理前 / nm', '损失 %', '参比', '判定', '膜厚差 / nm', '损失率差 / pp', '超容差的项'].map((name) => h('th', {}, name)))), h('tbody', {}, groups)))),
    h('h3.band-title', {}, `这一天的 ${films.length} 块膜（勾掉的读数不参与统计；标色的是建议离群或有质量提示）`), ...films);
}

// ---------- n/k：组叠加 ±σ、纳入勾 ----------

export async function nkGroup(state, name) {
  const library = state.library;
  // 作图偏好"n/k"：波长看哪段（n/k 组、处理前后配对、两组对比共用）、显示各条谱还是只看均值 ± σ
  const [data, pref] = await Promise.all([api(`/nk/${enc(library)}/${enc(name)}`), chartPref(library, 'n/k', {'波长从': null, '波长到': null, '显示': '各条谱和均值'})]);
  const flagged = new Set((data.anomalies || []).map((a) => a['谱名']));
  const box = h('div.view');
  const plots = {n: h('div.plot'), k: h('div.plot')};
  const drawOne = (key) => {
    const series = pref['显示'] === '只看均值 ± σ' ? [] : data.members.filter((m) => m.included).map((m, i) => ({name: m.name + (m.repeat ? '' : '（不计重复性）'), x: m.w, y: m[key], mode: 'lines', color: flagged.has(m.name) ? '#c96442' : COLORS[i % COLORS.length], dash: m.repeat ? 'solid' : 'dot'}));
    const band = data.band || {};
    if (band[`${key}_mean`] && pref['显示'] !== '只看各条谱') {
      const mean = band[`${key}_mean`], sd = band[`${key}_std`] || [];
      series.push({name: '均值 + σ', x: band.w, y: mean.map((v, i) => v != null && sd[i] != null ? v + sd[i] : null), mode: 'lines', color: '#968f82', dash: 'dot'});
      series.push({name: '均值', x: band.w, y: mean, mode: 'lines', color: '#29261f'});
      series.push({name: '均值 − σ', x: band.w, y: mean.map((v, i) => v != null && sd[i] != null ? v - sd[i] : null), mode: 'lines', color: '#968f82', dash: 'dot'});
    }
    chart(plots[key], {series, x: '波长 / nm', y: key, height: 360, x_range: range(pref, '波长从', '波长到').range});
  };
  const redraw = () => { drawOne('n'); drawOne('k'); };
  requestAnimationFrame(redraw);
  const draw = (key) => h('section.block', {}, h('header.block-head', {}, h('h3', {}, key === 'n' ? '折射率 n' : '消光系数 k')), plots[key]);
  const tools = h('div.chart-tools', {}, h('span.quiet.small', {}, '波长'), span(pref, '波长从', '波长到', 'nm', redraw),
    h('span.quiet.small', {}, '显示'), segs(pref, '显示', ['各条谱和均值', '只看均值 ± σ', '只看各条谱'], redraw),
    h('span.grow'), defaultButton(library, 'n/k', () => ({...pref})));
  const toggles = h('div.tablewrap.flat', {}, h('table.data.compact', {}, h('thead', {}, h('tr', {}, ['谱', '纳入', '纳入重复性', '异常筛查'].map((c) => h('th', {}, c)))),
    h('tbody', {}, data.members.map((m) => h('tr' + (flagged.has(m.name) ? '.flagged' : ''), {}, h('td', {}, h('a', {href: `#/record/${enc(library)}/${enc(m.id)}`}, m.name)),
      ...['纳入', '纳入重复性'].map((field) => h('td', {}, h('input', {type: 'checkbox', checked: field === '纳入' ? m.included : m.repeat, onchange: async (e) => {
        await api(`/nk/${enc(library)}/spectrum/${enc(m.id)}`, 'PUT', {field, value: e.target.checked}); toast('改了，重算 σ'); state.rerender?.();
      }}))),
      h('td', {}, flagged.has(m.name) ? (data.anomalies.find((a) => a['谱名'] === m.name)?.['理由'] || []).join('；') : ''))))));
  const s = data.summary;
  box.append(
    s ? h('section.block.wide.b-stats', {}, h('div.stat-row', {}, (s['目标波长'] || []).filter((t) => [193, 248, 633].includes(t['波长 nm'])).flatMap((t) => [
      h('div.stat', {}, h('div.stat-label', {}, `n${t['波长 nm']}`), h('div.stat-value.small', {}, number(t.n, 4), h('small', {}, ` ± ${number(t['σ(n)'], 2)}`))),
      h('div.stat', {}, h('div.stat-label', {}, `k${t['波长 nm']}`), h('div.stat-value.small', {}, number(t.k, 4), h('small', {}, ` ± ${number(t['σ(k)'], 2)}`)))]))) : h('p.notice', {}, data.problem || ''),
    tools, h('div.blocks', {}, draw('n'), draw('k')),
    h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '每条谱的纳入（去掉坏谱，σ 马上重算；红色的是异常筛查的候选）')), toggles));
  return box;
}

// ---------- 老化：系列、A500 曲线、导入确认 ----------

// 天数由浅到深（同一种陶土色，越晚越深）
const dayColor = (value, lo, hi) => {
  const t = hi > lo ? (value - lo) / (hi - lo) : 1, a = [236, 190, 166], b = [92, 36, 16];
  return `rgb(${a.map((c, i) => Math.round(c + (b[i] - c) * t)).join(',')})`;
};
const DASH = ['solid', 'dash', 'dot', 'dashdot'];

export async function agingSample(state, sample) {
  const library = state.library;
  // 作图偏好"老化"：波长看哪段、颜色每条一色还是按天数深浅、全部天还是每个条件首末两天、纵轴 A500 还是比第 0 天涨了多少
  const [data, pref] = await Promise.all([api(`/aging/${enc(library)}/${enc(sample)}`),
    chartPref(library, '老化', {'波长从': null, '波长到': null, '颜色': '每条一色', '显示': '全部天', '纵轴': 'A500'})]);
  const spectraPlot = h('div.plot'), trendPlot = h('div.plot');
  const ceiling = data.settings?.ceiling || 3.999;
  const drawSpectra = () => {
    let list = data.spectra;
    if (pref['显示'] === '首末两天') {
      const ends = {};
      list.forEach((s) => { const e = ends[s.condition] ||= {}; if (e.first == null || (s.day ?? 0) < (e.first.day ?? 0)) e.first = s; if (e.last == null || (s.day ?? 0) > (e.last.day ?? 0)) e.last = s; });
      list = list.filter((s) => ends[s.condition].first === s || ends[s.condition].last === s);
    }
    const conditions = [...new Set(list.map((s) => s.condition))], days = list.map((s) => s.day ?? 0), lo = Math.min(...days), hi = Math.max(...days);
    chart(spectraPlot, {x: '波长 / nm', y: '吸光度', height: 340, x_range: range(pref, '波长从', '波长到').range, series: list.map((s, i) => ({name: `${s.condition} ${s.day ?? ''}${s.day != null ? ' 天' : ''}`, x: s.x, y: s.y, mode: 'lines',
      ...(pref['颜色'] === '按天数' ? {color: dayColor(s.day ?? 0, lo, hi), dash: DASH[conditions.indexOf(s.condition) % DASH.length]} : {color: COLORS[i % COLORS.length]})}))});
  };
  const groups = (data.series['条件'] || []);
  const day = (p) => p['天数 / 天'] ?? p['天数'];
  // ΔA500：有"起点"就都减起点那条谱的 A500；没有起点的，各条件减自己最早那天
  const firstOf = (g) => g['点'].reduce((best, p) => best == null || (day(p) ?? 0) < (day(best) ?? 0) ? p : best, null);
  const start = groups.find((g) => g['条件'] === '起点');
  const drawTrend = () => {
    const delta = pref['纵轴'] !== 'A500';
    chart(trendPlot, {x: '天数', y: delta ? (start ? 'ΔA500（减起点）' : 'ΔA500（减最早一天）') : 'A500', height: 320, series: [
      ...groups.map((g, i) => {
        const base = delta ? firstOf(start || g)?.['A500 / Abs'] ?? 0 : 0;
        return {name: g['条件'], x: g['点'].map(day), y: g['点'].map((p) => p['A500 / Abs'] == null ? null : p['A500 / Abs'] - base), mode: 'lines+markers', color: COLORS[i % COLORS.length]};
      }),
      ...(delta ? [] : [{name: '饱和上限 4.0', x: [0, Math.max(1, ...groups.flatMap((g) => g['点'].map((p) => day(p) ?? 0)))], y: [ceiling, ceiling], mode: 'lines', color: '#c96442', dash: 'dot'}])]});
  };
  requestAnimationFrame(() => { drawSpectra(); drawTrend(); });
  const tools = h('div.chart-tools', {},
    h('span.quiet.small', {}, '波长'), span(pref, '波长从', '波长到', 'nm', drawSpectra),
    h('span.quiet.small', {}, '颜色'), segs(pref, '颜色', ['每条一色', '按天数'], drawSpectra),
    h('span.quiet.small', {}, '显示'), segs(pref, '显示', ['全部天', '首末两天'], drawSpectra),
    h('span.quiet.small', {}, '纵轴'), segs(pref, '纵轴', ['A500', 'ΔA500'], drawTrend),
    h('span.grow'), defaultButton(library, '老化', () => ({...pref})));
  const kin = data.kinetics || {};
  return h('div.view', {}, tools, h('div.blocks', {},
    h('section.block', {}, h('header.block-head', {}, h('h3', {}, '各天的谱（500 nm 看显色峰）')), spectraPlot),
    h('section.block', {}, h('header.block-head', {}, h('h3', {}, `A500 随天数（≥ 4.0 是饱和，读作下限）`)), trendPlot),
    kin['各条件'] ? h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, `动力学和货架期 · ${kin['指标'] || ''}`)),
      tableBlock({columns: Object.keys(kin['各条件'][0] || {}), rows: kin['各条件'].map((row) => Object.values(row))}, library),
      kin['温度依赖'] ? h('p.block-note', {}, Object.entries(kin['温度依赖']).map(([k, v]) => `${k} ${typeof v === 'number' ? number(v) : v}`).join(' · ')) : null) : h('p.quiet', {}, kin.problem || '')));
}

export function agingImport(state, onDone) {
  const library = state.library;
  const items = [];
  const table = h('div');
  const draw = () => table.replaceChildren(items.length ? h('div.tablewrap.flat', {}, h('table.data.compact', {}, h('thead', {}, h('tr', {}, ['文件', '样品', '种类', '条件', '天数', '时间标签', '说明'].map((c) => h('th', {}, c)))),
    h('tbody', {}, items.map((item) => h('tr' + (item.problem ? '.flagged' : ''), {}, h('td', {}, item.file),
      ...['样品', '种类', '条件', '天数', '时间标签'].map((key) => h('td', {}, h('input', {type: 'text', value: item[key] ?? '', style: 'width:110px', onchange: (e) => { item[key] = key === '天数' ? (e.target.value === '' ? null : Number(e.target.value)) : e.target.value; }}))),
      h('td.quiet', {}, item.problem || item['说明'] || '')))))) : h('p.quiet', {}, '把 UV 仪器导出的 *.WVS.csv 拖进来或点上面的按钮；文件名认出的样品、条件、天数可以在表里改对，再导入。'));
  const picker = h('input', {type: 'file', multiple: true, accept: '.csv,.txt,.dat,.asc', hidden: true, onchange: async () => {
    for (const file of picker.files) items.push(await (await fetch(`/api/aging/${enc(library)}/upload/${enc(file.name)}`, {method: 'PUT', body: file})).json());
    picker.value = ''; draw();
  }});
  draw();
  return h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '导入老化谱'), h('button.btn.small', {type: 'button', onclick: () => picker.click()}, icon('upload', 13), '选文件'), picker,
    h('button.btn.primary.small', {type: 'button', onclick: async () => {
      const ok = items.filter((item) => !item.problem);
      if (!ok.length) { toast('没有能导入的文件'); return; }
      const {saved} = await api(`/aging/${enc(library)}/commit`, 'POST', {items: ok});
      toast(`导入了 ${saved.length} 条，指标已算好`); items.splice(0); draw(); onDone?.();
    }}, '导入')), table);
}
