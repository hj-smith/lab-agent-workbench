// n/k 处理前后配对：两组均值 ± σ 叠在一起，Δn、Δk（处理后 − 处理前）随波长画出来，带 ±2 倍标准误的带子；
// 关键波长一张表，|Δ| 超过 2 倍标准误标"明显变了"。算在后端（interact.nk_pair_view），这里只画。
import {api, h} from './core.js';
import {number, plotly} from './blocks.js';
import {chartPref, defaultButton, range, span} from './chartprefs.js';

const enc = encodeURIComponent;
const BEFORE = '#3f7cc0', AFTER = '#c96442';      // 第一组（处理前）、第二组（处理后）
const BASE = {paper_bgcolor: '#fbfaf7', plot_bgcolor: '#fbfaf7', font: {family: 'Microsoft YaHei, sans-serif', size: 12, color: '#29261f'}, margin: {l: 60, r: 14, t: 10, b: 46}};
const axis = (title, extra = {}) => ({title: {text: title}, gridcolor: '#ebe6dc', zeroline: false, ...extra});

function band(w, mean, sd, color, name) {
  const upper = mean.map((m, i) => m == null || sd[i] == null ? null : m + sd[i]);
  const lower = mean.map((m, i) => m == null || sd[i] == null ? null : m - sd[i]);
  return [{x: w, y: upper, mode: 'lines', line: {width: 0}, hoverinfo: 'skip', showlegend: false},
    {x: w, y: lower, mode: 'lines', line: {width: 0}, fill: 'tonexty', fillcolor: color + '26', hoverinfo: 'skip', showlegend: false},
    {x: w, y: mean, mode: 'lines', line: {color, width: 1.8}, name, hovertemplate: `${name}<br>%{x} nm · %{y:.4f}<extra></extra>`}];
}

export async function nkPair(state, before, after) {
  // 波长看哪段跟 n/k 组页共用作图偏好"n/k"
  const [data, pref] = await Promise.all([api(`/nk-pair/${enc(state.library)}?before=${enc(before)}&after=${enc(after)}`),
    chartPref(state.library, 'n/k', {'波长从': null, '波长到': null, '显示': '各条谱和均值'})]);
  const Plotly = await plotly();
  const charts = {n: h('div.plot'), k: h('div.plot'), dn: h('div.plot'), dk: h('div.plot')};
  const delta = (key) => {
    const d = data[key].delta, se = data[key].se;
    return [{x: data.w, y: d.map((v, i) => v == null ? null : v + 2 * se[i]), mode: 'lines', line: {width: 0}, hoverinfo: 'skip', showlegend: false},
      {x: data.w, y: d.map((v, i) => v == null ? null : v - 2 * se[i]), mode: 'lines', line: {width: 0}, fill: 'tonexty', fillcolor: '#8f897c33', hoverinfo: 'skip', showlegend: false},
      {x: data.w, y: d, mode: 'lines', line: {color: '#29261f', width: 1.6}, name: `Δ${key}`, hovertemplate: `Δ${key} %{y:+.4f}<br>%{x} nm<extra></extra>`}];
  };
  const draw = () => {
    const x = range(pref, '波长从', '波长到');
    for (const key of ['n', 'k']) {
      Plotly.newPlot(charts[key], [...band(data.w, data[key].before, data[key].before_sd, BEFORE, data.before), ...band(data.w, data[key].after, data[key].after_sd, AFTER, data.after)],
        {...BASE, height: 300, showlegend: true, legend: {orientation: 'h', y: -0.25}, xaxis: axis('波长 / nm', x), yaxis: axis(key)}, {displaylogo: false, responsive: true});
      Plotly.newPlot(charts['d' + key], delta(key), {...BASE, height: 260, showlegend: false, xaxis: axis('波长 / nm', x), yaxis: axis('Δ' + key, {zeroline: true, zerolinecolor: '#8f897c'})},
        {displaylogo: false, responsive: true});
    }
  };
  const box = h('div.view', {},
    h('p.quiet', {}, `${data.before} ${data.counts[0]} 条谱、${data.after} ${data.counts[1]} 条谱（只算勾了"纳入"的）。带子：上面两张是各组 ± σ，下面两张是 Δ ± 2 倍标准误（√(σ前²/N前 + σ后²/N后)）；Δ 的带子不跨 0 才算超出误差（不是噪声）；大不大还要看业务要求。`),
    h('div.chart-tools', {}, h('span.quiet.small', {}, '波长'), span(pref, '波长从', '波长到', 'nm', draw), h('span.grow'), defaultButton(state.library, 'n/k', () => ({...pref}))),
    h('div.exp-two', {}, h('section.block', {}, h('header.block-head', {}, h('h3', {}, 'n')), charts.n), h('section.block', {}, h('header.block-head', {}, h('h3', {}, 'k')), charts.k)),
    h('div.exp-two', {}, h('section.block', {}, h('header.block-head', {}, h('h3', {}, 'Δn（后 − 前）')), charts.dn), h('section.block', {}, h('header.block-head', {}, h('h3', {}, 'Δk（后 − 前）')), charts.dk)),
    h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '关键波长')),
      h('table.data.compact', {}, h('thead', {}, h('tr', {}, ['波长 / nm', 'n 前', 'n 后', 'Δn', '', 'k 前', 'k 后', 'Δk', ''].map((c) => h('th', {}, c)))),
        h('tbody', {}, data.table.map((row) => h('tr', {}, h('td.num', {}, row.w),
          ...['n', 'k'].flatMap((key) => [h('td.num', {}, number(row[key].before, 4)), h('td.num', {}, number(row[key].after, 4)),
            h('td.num' + (row[key].clear ? '.warn-ink' : ''), {}, `${row[key].delta > 0 ? '+' : ''}${number(row[key].delta, 4)}`),
            h('td.small', {}, row[key].clear ? h('span.st-tag.open', {title: '变化超过 2 倍标准误（统计上不是噪声）；大不大还要看业务上要多少'}, '超出误差') : h('span.quiet', {}, `±${number(2 * row[key].se, 4)} 内`))])))))));
  requestAnimationFrame(draw);
  return box;
}
