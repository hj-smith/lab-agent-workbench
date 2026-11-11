// GPC 实验演化：一组样品按反应时间排开——分子量怎么长、分布怎么变、各分子量区段占多少、像哪种动力学曲线；成员能改。
// 对着旧 GPC 软件的"实验演化"页；算在后端（lab/gpcexp.py），这里只画和改。
import {api, h, icon, toast} from './core.js';
import {number, plotly} from './blocks.js';
import {chartPref, defaultButton} from './chartprefs.js';

const enc = encodeURIComponent;
const METRICS = [['Mn', 'Mn'], ['Mw', 'Mw'], ['Mz', 'Mz'], ['Mz+1', 'Mz+1'], ['PDI', 'Đ'], ['Mp', 'Mp'], ['D10', 'D10'], ['D50', 'D50'], ['D90', 'D90']];
const LABEL = Object.fromEntries(METRICS);
const MODELS = [['zero_order', '零级'], ['first_order', '一级'], ['second_order', '二级'], ['general_order', '通用级数']];
const MODEL_COLOR = {zero_order: '#c96442', first_order: '#3f7cc0', second_order: '#b5841c', general_order: '#8a63b8'};
const HEAT = [[0, '#fbfaf7'], [0.12, '#f4e3d8'], [0.35, '#e6a98b'], [0.65, '#c96442'], [1, '#5c2410']];
const SUP = {'-': '⁻', 0: '⁰', 1: '¹', 2: '²', 3: '³', 4: '⁴', 5: '⁵', 6: '⁶', 7: '⁷', 8: '⁸', 9: '⁹'};
const sig = (value) => {                       // 很小或很大的数写成 4.36×10⁻⁵
  if (value == null) return '—';
  if (value === 0 || (Math.abs(value) >= 0.01 && Math.abs(value) < 1e5)) return Number(value.toPrecision(4)).toString();
  const [mantissa, power] = value.toExponential(2).split('e');
  return `${mantissa}×10${[...String(Number(power))].map((ch) => SUP[ch]).join('')}`;
};
const fmt = (value, digits = 0) => value == null ? '—' : digits ? number(value, digits) : Math.round(value).toLocaleString('en-US');
const BASE = {paper_bgcolor: '#fbfaf7', plot_bgcolor: '#fbfaf7', font: {family: 'Microsoft YaHei, sans-serif', size: 12, color: '#29261f'}, margin: {l: 62, r: 16, t: 12, b: 44}};
const axis = (title, extra = {}) => ({title: {text: title}, gridcolor: '#ebe6dc', zeroline: false, ...extra});

// 反应时间由浅到深（同一种颜色，时间越晚越深）
function timeColor(value, lo, hi) {
  const t = hi > lo ? (value - lo) / (hi - lo) : 1;
  const a = [240, 199, 180], b = [110, 40, 16];
  return `rgb(${a.map((c, i) => Math.round(c + (b[i] - c) * t)).join(',')})`;
}

async function draw(node, traces, layout) {
  const Plotly = await plotly();
  await Plotly.react(node, traces, {...BASE, ...layout}, {displaylogo: false, responsive: true, modeBarButtonsToRemove: ['select2d', 'lasso2d']});
}

export async function gpcExperiment(state, id) {
  const library = state.library;
  const box = h('div.gpc-exp');
  let view = await api(`/gpc-exp/${enc(library)}/${enc(id)}`);
  // 作图偏好"GPC实验演化"：看哪几个指标、要不要归一叠在一起、瀑布间隔
  const pref = await chartPref(library, 'GPC实验演化', {'指标': ['Mn', 'Mw', 'PDI'], '归一叠在一起': false, '瀑布间隔': 0.7});
  const saveDefault = () => defaultButton(library, 'GPC实验演化', () => ({'指标': picked, '归一叠在一起': together, '瀑布间隔': gap}));
  const render = () =>box.replaceChildren(head(), trendBlock(), distBlock(), bandBlock(), kineticsBlock(), memberBlock());

  function head() {
    const m = view.members, used = m.filter((item) => item.include);
    return h('div.exp-head', {},
      h('div.exp-facts', {}, view.project ? h('span.mini-chip', {}, view.project) : null,
        h('span', {}, `${m.length} 个样${used.length < m.length ? `（纳入 ${used.length}）` : ''}`),
        view.span ? h('span', {}, `反应时间 ${view.span[0]}–${view.span[1]} h`) : null,
        h('span.quiet', {}, view.replicates ? '有重复样：趋势图给均值 ± SD' : '没有重复样：趋势图是单点，看不出误差'),
        h('span.grow'), h('a.small', {href: `#/record/${enc(library)}/${enc(view.id)}`}, '看记录')),
      view.about ? h('p.quiet', {}, view.about) : null);
  }

  // ---------- 1 分子量怎么长 ----------
  let picked = [...pref['指标']], together = !!pref['归一叠在一起'];
  function trendBlock() {
    const charts = h('div.exp-trends');
    const chips = h('div.exp-chips', {}, METRICS.map(([key, label]) => h('button.chip' + (picked.includes(key) ? '.on' : ''), {type: 'button', onclick: () => {
      picked = picked.includes(key) ? picked.filter((item) => item !== key) : [...picked, key]; block.replaceWith(trendBlock());
    }}, label)), h('span.grow'),
    h('label.inline', {title: '各指标都缩到 0–1，叠在一张图上看谁先长、谁后长'}, h('input', {type: 'checkbox', checked: together, onchange: (e) => { together = e.target.checked; block.replaceWith(trendBlock()); }}), '归一化叠在一起'),
    saveDefault());
    const block = h('section.block.exp-block', {}, h('header.block-head', {}, h('h3', {}, '分子量怎么长'), h('span.quiet.small', {}, '点小签选指标；灰叉是没纳入的样')), chips, charts);
    requestAnimationFrame(async () => {
      const series = picked.map((key) => ({key, ...view.trend[key]})).filter((item) => item.points.length);
      if (!series.length) return charts.replaceChildren(h('p.quiet', {}, '这几个指标都没有数。'));
      if (together) {
        const node = h('div.plot'); charts.replaceChildren(node);
        const traces = series.map((item, index) => {
          const values = item.mean.map((p) => p.v), lo = Math.min(...values), hi = Math.max(...values);
          return {x: item.mean.map((p) => p.h), y: values.map((v) => hi > lo ? (v - lo) / (hi - lo) : 0.5), name: LABEL[item.key], mode: 'lines+markers',
            line: {width: 2, color: Object.values(MODEL_COLOR)[index % 4]}, marker: {size: 7}, hovertemplate: `${LABEL[item.key]} %{text}<br>%{x} h<extra></extra>`,
            text: values.map((v) => fmt(v, item.key === 'PDI' ? 3 : 0))};
        });
        await draw(node, traces, {height: 320, showlegend: true, legend: {orientation: 'h', y: -0.22}, xaxis: axis('反应时间 / h'), yaxis: axis('归一化（0 = 最小，1 = 最大）', {range: [-0.05, 1.05]})});
        return;
      }
      charts.replaceChildren(...series.map((item) => h('div.exp-trend', {}, h('div.exp-trend-title', {}, LABEL[item.key], h('small.quiet', {}, item.key === 'PDI' ? '' : ' g/mol（PS 当量）')), h('div.plot'))));
      series.forEach((item, index) => {
        const node = charts.children[index].lastChild;
        const used = item.points.filter((p) => p.include), dropped = item.points.filter((p) => !p.include);
        const digits = item.key === 'PDI' ? 3 : 0;
        const traces = [{x: item.mean.map((p) => p.h), y: item.mean.map((p) => p.v), mode: 'lines+markers', line: {color: '#c96442', width: 2}, marker: {size: 7, color: '#c96442'},
          error_y: view.replicates ? {type: 'data', array: item.mean.map((p) => p.sd || 0), visible: true, color: '#8f897c'} : undefined,
          text: item.mean.map((p) => used.filter((u) => u.h === p.h).map((u) => u.name).join('、')), hovertemplate: `%{text}<br>%{x} h · ${LABEL[item.key]} %{y:,.${digits}f}<extra></extra>`, showlegend: false}];
        if (dropped.length) traces.push({x: dropped.map((p) => p.h), y: dropped.map((p) => p.v), mode: 'markers', marker: {symbol: 'x', size: 9, color: '#968f82'}, text: dropped.map((p) => p.name),
          hovertemplate: '%{text}（没纳入）<extra></extra>', showlegend: false});
        draw(node, traces, {height: 220, margin: {l: 58, r: 10, t: 6, b: 38}, xaxis: axis('反应时间 / h'), yaxis: axis('')});
      });
    });
    return block;
  }

  // ---------- 2 分布怎么变 ----------
  let gap = Number(pref['瀑布间隔']) || 0.7;
  function distBlock() {
    const heat = h('div.plot'), fall = h('div.plot');
    const slider = h('input', {type: 'range', min: 0, max: 3, step: 0.05, value: gap, oninput: (e) => { gap = Number(e.target.value); gapText.textContent = gap.toFixed(2); drawFall(); }});
    const gapText = h('span.small', {}, gap.toFixed(2));
    const block = h('section.block.exp-block', {}, h('header.block-head', {}, h('h3', {}, '分布怎么变'), h('span.quiet.small', {}, '左：热图，颜色越深这段分子量越多；右：瀑布，每条分布缩到峰高 = 1，按时间往上叠')),
      view.heat ? h('div.exp-two', {}, h('div', {}, heat), h('div', {}, h('div.exp-tools', {}, h('label.inline', {}, '间隔', slider, gapText), h('span.grow'), saveDefault()), fall)) : h('p.quiet', {}, '成员里没有存好的分布。'));
    const lo = view.span?.[0] ?? 0, hi = view.span?.[1] ?? 1;
    async function drawFall() {
      const traces = view.waterfall.map((item, index) => {
        const top = Math.max(...item.w) || 1;
        return {x: item.logM, y: item.w.map((w) => w / top + index * gap), mode: 'lines', name: `${item.h} h`, line: {color: timeColor(item.h, lo, hi), width: 1.8},
          hovertemplate: `${item.name}（${item.h} h）<br>log M %{x:.2f}<extra></extra>`};
      });
      await draw(fall, traces, {height: 380, showlegend: false, xaxis: axis('log M（PS 当量）'), yaxis: axis('', {showticklabels: false}),
        annotations: view.waterfall.map((item, index) => ({x: item.logM[item.logM.length - 1], y: index * gap + 0.08, text: `${item.h} h`, showarrow: false, xanchor: 'right', font: {size: 10, color: '#6b665c'}}))});
    }
    if (view.heat) requestAnimationFrame(async () => {
      await draw(heat, [{type: 'heatmap', x: view.heat.h, y: view.heat.logM, z: view.heat.z, colorscale: HEAT, zsmooth: 'best', colorbar: {title: {text: 'dw/dlogM', side: 'right'}, thickness: 12},
        hovertemplate: '%{x} h · log M %{y:.2f}<br>dw/dlogM %{z:.3f}<extra></extra>'}],
      {height: 380, margin: {l: 58, r: 8, t: 12, b: 44}, xaxis: axis('反应时间 / h（按真实间隔）'), yaxis: axis('log M（PS 当量）')});
      drawFall();
    });
    return block;
  }

  // ---------- 3 分子量区段 ----------
  function bandBlock() {
    let list = view.bands.map((item) => ({...item}));
    const table = h('div'), stack = h('div.plot'), shape = h('div.plot');
    const edit = (item, key, value) => { item[key] = value; };
    const logText = (v) => v == null ? '' : String(Math.round(v));
    function drawTable() {
      table.replaceChildren(h('table.data.compact.edit-table.band-table', {}, h('thead', {}, h('tr', {}, ['', '名字', '下限 g/mol', '上限 g/mol', ''].map((name) => h('th', {}, name)))),
        h('tbody', {}, list.map((item, index) => h('tr', {},
          h('td', {}, h('input.band-color', {type: 'color', value: item.color || '#0072B2', onchange: (e) => edit(item, 'color', e.target.value)})),
          h('td', {}, h('input', {type: 'text', value: item.name, style: 'width:120px', onchange: (e) => edit(item, 'name', e.target.value)})),
          ...['lower', 'upper'].map((key) => h('td', {}, h('input', {type: 'number', step: 'any', value: logText(item[key]), placeholder: key === 'lower' ? '空 = 从最小起' : '空 = 到最大',
            onchange: (e) => edit(item, key, e.target.value === '' ? null : Number(e.target.value))}))),
          h('td', {}, h('button.btn.ghost.small', {type: 'button', title: '去掉', onclick: () => { list.splice(index, 1); drawTable(); }}, '×')))))),
      h('div.row', {}, h('button.btn.ghost.small', {type: 'button', onclick: () => { list.push({name: `区段 ${list.length + 1}`, lower: null, upper: null, color: '#0072B2'}); drawTable(); }}, icon('plus', 13), '加一段'),
        h('button.btn.ghost.small', {type: 'button', onclick: () => { list = view.defaults.map((item) => ({...item})); drawTable(); }}, '常用四段'),
        view.quartiles.length ? h('button.btn.ghost.small', {type: 'button', title: '所有分布平均后按累积质量 25 / 50 / 75 % 切成四段', onclick: () => { list = view.quartiles.map((item) => ({...item})); drawTable(); }}, '按这组数据四等分') : null,
        h('span.grow'), h('button.btn.primary.small', {type: 'button', onclick: save}, icon('check', 13), '存，重算')));
    }
    async function save() {
      await api(`/gpc-bands/${enc(library)}`, 'PUT', {project: view.project, bands: list.map((item) => ({...item, id: item.id && view.bands_saved ? item.id : undefined}))});
      view = await api(`/gpc-exp/${enc(library)}/${enc(id)}`); toast(`区段存进了 ${view.project} 的分子量分区，各段占比已重算`); block.replaceWith(bandBlock());
    }
    const block = h('section.block.exp-block', {}, h('header.block-head', {}, h('h3', {}, '分子量区段'),
      h('span.quiet.small', {}, view.bands_saved ? `用的是 ${view.project} 存着的区段（整个项目共用）` : '这个项目还没存过区段，先用常用四段')),
      h('div.exp-bands', {}, h('div.exp-band-edit', {}, table, h('div.exp-band-shape', {}, shape)), h('div', {}, stack)));
    drawTable();
    if (view.fractions.length) requestAnimationFrame(async () => {
      const hours = view.fractions.map((f) => f.h);
      await draw(stack, view.bands.map((band, index) => ({x: hours, y: view.fractions.map((f) => f.f[index] * 100), name: band.name, mode: 'lines', stackgroup: 'one',
        line: {width: 0.5, color: '#fbfaf7'}, fillcolor: band.color, hovertemplate: `${band.name} %{y:.1f} %<br>%{x} h<extra></extra>`})),
      {height: 360, showlegend: true, legend: {orientation: 'h', y: -0.2}, xaxis: axis('反应时间 / h'), yaxis: axis('质量分数 / %', {range: [0, 100]})});
      const first = view.waterfall[0], last = view.waterfall[view.waterfall.length - 1];
      const shapes = view.bands.map((band) => ({type: 'rect', xref: 'x', yref: 'paper', y0: 0, y1: 1, opacity: 0.16, line: {width: 0}, fillcolor: band.color, layer: 'below',
        x0: band.lower == null ? Math.min(first.logM[0], last.logM[0]) : Math.log10(band.lower), x1: band.upper == null ? Math.max(first.logM.at(-1), last.logM.at(-1)) : Math.log10(band.upper)}));
      await draw(shape, [first, last].filter((item, index, all) => all.indexOf(item) === index).map((item, index) => ({x: item.logM, y: item.w, mode: 'lines', name: `${item.h} h`,
        line: {color: index ? '#5c2410' : '#e6a98b', width: 1.8}})), {height: 190, margin: {l: 46, r: 8, t: 6, b: 36}, showlegend: true, legend: {orientation: 'h', y: 1.15, x: 0},
        xaxis: axis('log M'), yaxis: axis('', {showticklabels: false}), shapes});
    });
    return block;
  }

  // ---------- 4 动力学 ----------
  let onlyBest = false;
  function kineticsBlock() {
    const k = view.kinetics;
    const plot = h('div.plot');
    const metric = h('select', {onchange: () => store()}, METRICS.map(([key, label]) => h('option', {value: key, selected: key === k.metric}, label)));
    const boxes = MODELS.map(([key, label]) => h('label.inline', {}, h('input', {type: 'checkbox', value: key, checked: k.models.includes(key), onchange: () => store()}), label));
    async function store() {
      const models = boxes.map((label) => label.firstChild).filter((input) => input.checked).map((input) => input.value);
      view = await api(`/gpc-exp/${enc(library)}/${enc(id)}/kinetics`, 'PUT', {metric: metric.value, models: models.length ? models : ['zero_order']});
      block.replaceWith(kineticsBlock());
    }
    const fits = onlyBest ? k.fits.filter((fit) => fit.recommended) : k.fits;
    const block = h('section.block.exp-block', {}, h('header.block-head', {}, h('h3', {}, '动力学拟合'), h('span.quiet.small', {}, '描述性拟合：只说明数据像哪种曲线，不证明反应机理')),
      h('div.exp-tools', {}, h('label.inline', {}, '指标', metric), ...boxes,
        h('label.inline', {}, h('input', {type: 'checkbox', checked: onlyBest, onchange: (e) => { onlyBest = e.target.checked; block.replaceWith(kineticsBlock()); }}), '只看推荐的')),
      k.warning ? h('p.warn-ink.small', {}, k.warning) : null,
      h('div.exp-kin', {}, h('div', {}, plot),
        h('table.data.compact.kin-table', {}, h('thead', {}, h('tr', {}, ['模型', '级数 n', 'k（/h）', 'y0', 'R²', 'RMSE', ''].map((name) => h('th', {}, name)))),
          h('tbody', {}, k.fits.map((fit) => h('tr' + (fit.recommended ? '.best' : ''), {title: fit.equation},
            h('td', {}, h('i.kin-dot', {style: {background: MODEL_COLOR[fit.model]}}), fit.label), h('td.num', {}, number(fit.order, 2)), h('td.num', {}, sig(fit.k)),
            h('td.num', {}, fmt(fit.y0, k.metric === 'PDI' ? 3 : 0)), h('td.num', {}, number(fit.r2, 3)), h('td.num', {}, fmt(fit.rmse, k.metric === 'PDI' ? 3 : 1)),
            h('td', {}, fit.recommended ? h('span.st-tag.done', {title: 'RMSE 最小'}, '推荐') : '')))),
          h('caption.quiet.small', {}, '把鼠标放在一行上看方程。零级 y = y0 + kx；一级 y = y0·exp(kx)；二级 1/y = 1/y0 − kx；通用级数在 0.1–3.0 里挑最合适的 n。'))));
    requestAnimationFrame(() => {
      const pts = view.trend[k.metric].points;
      const traces = [{x: pts.filter((p) => p.include).map((p) => p.h), y: pts.filter((p) => p.include).map((p) => p.v), mode: 'markers', name: '实测', marker: {color: '#29261f', size: 8},
        text: pts.filter((p) => p.include).map((p) => p.name), hovertemplate: '%{text}<br>%{x} h · %{y:,.3~f}<extra></extra>'},
        ...fits.map((fit) => ({x: fit.curve.x, y: fit.curve.y, mode: 'lines', name: `${fit.label}${fit.model === 'general_order' ? `（n = ${number(fit.order, 2)}）` : ''}`,
          line: {color: MODEL_COLOR[fit.model], width: fit.recommended ? 2.6 : 1.5, dash: fit.recommended ? 'solid' : 'dot'}, hoverinfo: 'skip'}))];
      draw(plot, traces, {height: 340, margin: {l: 62, r: 16, t: 12, b: 92}, showlegend: true, legend: {orientation: 'h', y: -0.3}, xaxis: axis('反应时间 / h'), yaxis: axis(LABEL[k.metric])});
    });
    return block;
  }

  // ---------- 5 成员 ----------
  function memberBlock() {
    const rows = view.members.map((item) => ({...item}));
    const add = h('select', {}, h('option', {value: ''}, '加本项目的样品…'), view.candidates.map((item) => h('option', {value: item.id}, item.name)));
    const body = h('tbody');
    const drawRows = () => body.replaceChildren(...rows.map((item, index) => h('tr' + (item.include ? '' : '.excluded'), {},
      h('td', {}, h('a', {href: `#/testing/gpc/${enc(item.id)}`}, item.name)),
      h('td', {}, h('input', {type: 'number', step: 'any', value: item.h ?? '', style: 'width:80px', onchange: (e) => { item.h = e.target.value === '' ? null : Number(e.target.value); }})),
      h('td', {}, h('input', {type: 'checkbox', checked: item.include, onchange: (e) => { item.include = e.target.checked; drawRows(); }})),
      h('td', {}, h('input', {type: 'text', value: item.reason || '', placeholder: item.include ? '' : '为什么不纳入', style: 'width:200px', onchange: (e) => { item.reason = e.target.value; }})),
      h('td.num', {}, fmt(item.Mw)), h('td.num', {}, number(item.PDI, 3)),
      h('td', {}, h('button.btn.ghost.small', {type: 'button', title: '从实验里去掉（样品本身不动）', onclick: () => { rows.splice(index, 1); drawRows(); }}, '×')))));
    drawRows();
    const save = async () => {
      view = await api(`/gpc-exp/${enc(library)}/${enc(id)}/members`, 'PUT', {members: rows.map((item) => ({id: item.id, name: item.name, h: item.h, include: item.include, reason: item.reason}))});
      toast('成员存好了，图都重画了'); render();
    };
    return h('section.block.exp-block', {}, h('header.block-head', {}, h('h3', {}, '成员'), h('span.quiet.small', {}, '改反应时间、勾掉不纳入的（写上原因），存了上面的图都重画')),
      h('table.data.compact.edit-table', {}, h('thead', {}, h('tr', {}, ['样品', '反应时间 / h', '纳入', '剔除原因', 'Mw', 'Đ', ''].map((name) => h('th', {}, name)))), body),
      h('div.row', {}, add, h('button.btn.ghost.small', {type: 'button', onclick: () => {
        const item = view.candidates.find((c) => c.id === add.value); if (!item) return;
        const hours = /(\d+(?:\.\d+)?)\s*[hH](?![a-zA-Z])/.exec(item.name);
        rows.push({id: item.id, name: item.name, h: hours ? Number(hours[1]) : null, include: true, reason: ''}); add.value = ''; drawRows();
      }}, icon('plus', 13), '加'), h('span.grow'), h('button.btn.primary.small', {type: 'button', onclick: save}, icon('check', 13), '存成员')));
  }

  render();
  return box;
}
