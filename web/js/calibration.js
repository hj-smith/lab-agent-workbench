// GPC 标准曲线工作台：标准点和拟合线、残差、每个点的影响、试改后重拟合、和别的曲线比（漂移）、确认 / 有效期 / 停用。
// 算在后端（lab/gpccal.py，用原件的拟合函数）；这里只画和改。库里同一条曲线各项目各存一份，这里当一条看。
import {api, h, icon, toast} from './core.js';
import {number, plotly} from './blocks.js';

const enc = encodeURIComponent;
const BASE = {paper_bgcolor: '#fbfaf7', plot_bgcolor: '#fbfaf7', font: {family: 'Microsoft YaHei, sans-serif', size: 12, color: '#29261f'}};
const axis = (title, extra = {}) => ({title: {text: title}, gridcolor: '#ebe6dc', zeroline: false, ...extra});
const big = (value) => value == null ? '—' : Math.round(value).toLocaleString('en-US');
const pct = (value, digits = 2) => value == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(digits)} %`;

async function draw(node, traces, layout) {
  const Plotly = await plotly();
  await Plotly.react(node, traces, {...BASE, ...layout}, {displaylogo: false, responsive: true, modeBarButtonsToRemove: ['select2d', 'lasso2d']});
}

export async function gpcCalibration(state, id) {
  const library = state.library;
  const box = h('div.gpc-cal');
  const view = await api(`/gpc-cal/${enc(library)}/${enc(id)}`);
  const enabled = view.points.map((p) => p.on);
  let degree = view.degree, tried = null;
  const plot = h('div.plot'), table = h('div'), trialBox = h('div.cal-trial');

  // ---------- 头：是什么、能不能用 ----------
  const status = async (patch) => { await api(`/gpc-cal/${enc(library)}/${enc(id)}/status`, 'PUT', patch); toast(`改好了（${view.copies.length} 份副本一起改）`); };
  const head = h('div.cal-head', {},
    h('div.exp-facts', {},
      h('span.mini-chip', {}, view.model === 'pchip' ? 'PCHIP（从标样直接连）' : `${view.degree} 次多项式`),
      h('span', {}, `有效范围 ${number(view.valid[0], 2)}–${number(view.valid[1], 2)} min（M ${big(view.valid_M[0])}–${big(view.valid_M[1])}）`),
      h('span', {}, `R² ${number(view.r2, 5)}`), view.rmse != null ? h('span', {}, `RMSE ${number(view.rmse, 4)}（log M）`) : null,
      h('span', {}, `${view.points.length} 个标准点`), h('span.grow'),
      h('a.small', {href: `#/record/${enc(library)}/${enc(view.id)}`}, '看记录')),
    h('div.exp-facts', {},
      view.file ? h('span.quiet', {}, `来自 ${view.file}${view.imported ? ' · ' + view.imported.slice(0, 10) + ' 导入' : ''}`) : null,
      h('span.quiet', {}, `${view.copies.length} 份副本（各项目各一份），在用的：${view.copies.filter((c) => c.uses).map((c) => `${c.project} ${c.uses} 个`).join('、') || '没有'}`),
      view.samples.length ? h('a.small', {href: `#/testing/gpc/${enc('批处理:' + view.samples.join(','))}`, title: '打开批处理：做法选"各自现在的做法"，标准曲线选要换成的那条'}, `用在 ${view.samples.length} 个样 → 给它们换曲线`) : h('span.quiet', {}, '还没有样品用它')),
    h('div.cal-status', {},
      h('label.inline', {}, h('input', {type: 'checkbox', checked: view.confirmed, onchange: (e) => status({confirmed: e.target.checked})}), '确认过（可以放心用）'),
      h('label.inline', {}, '有效期至', h('input', {type: 'date', value: (view.until || '').slice(0, 10), onchange: (e) => status({until: e.target.value || null})})),
      view.expired ? h('span.st-tag.open', {}, '已过期') : null,
      h('label.inline', {title: '停用后，给样品选标准曲线时会标出来并提醒'}, h('input', {type: 'checkbox', checked: view.off, onchange: (e) => status({off: e.target.checked})}), '停用')));

  // ---------- 图：标准点 + 拟合线 + 残差 ----------
  function drawPlot() {
    const points = view.points, line = view.line;
    const off = points.filter((p, i) => tried && !enabled[i]);
    const traces = [
      {x: line.inside.rt, y: line.inside.logM, mode: 'lines', name: '拟合线（有效范围内）', line: {color: '#3f7cc0', width: 2}, hoverinfo: 'skip'},
      ...line.outside.map((part, i) => ({x: part.rt, y: part.logM, mode: 'lines', name: '超出有效范围（外推）', showlegend: i === 0, line: {color: '#3f7cc0', width: 1.4, dash: 'dot'}, hoverinfo: 'skip'})),
      {x: points.filter((p) => p.on).map((p) => p.rt), y: points.filter((p) => p.on).map((p) => p.logM), mode: 'markers', name: '标准点', marker: {color: '#c96442', size: 9, line: {color: '#fff', width: 1.2}},
        text: points.filter((p) => p.on).map((p) => `M ${big(p.M)}，拟合 ${big(p.fit)}（${pct(p.dev * 100)}）`), hovertemplate: '%{x:.3f} min<br>%{text}<extra></extra>'},
      {x: points.filter((p) => p.on).map((p) => p.rt), y: points.filter((p) => p.on).map((p) => p.dev * 100), mode: 'markers+lines', name: '偏差 %', yaxis: 'y2',
        marker: {color: '#c96442', size: 7}, line: {color: '#e6a98b', width: 1}, hovertemplate: '%{x:.3f} min · %{y:.2f} %<extra></extra>', showlegend: false},
    ];
    if (points.some((p) => !p.on)) traces.push({x: points.filter((p) => !p.on).map((p) => p.rt), y: points.filter((p) => !p.on).map((p) => p.logM), mode: 'markers', name: '停用的点（存着的）',
      marker: {color: '#fff', size: 9, line: {color: '#968f82', width: 1.5}}});
    if (tried?.line) {
      traces.push({x: tried.line.inside.rt, y: tried.line.inside.logM, mode: 'lines', name: '试改后的线', line: {color: '#b5841c', width: 2, dash: 'dash'}, hoverinfo: 'skip'});
      if (off.length) traces.push({x: off.map((p) => p.rt), y: off.map((p) => p.logM), mode: 'markers', name: '试改时去掉的点', marker: {symbol: 'x', size: 11, color: '#b5841c'}});
      const used = tried.points.filter((p) => p.on && p.dev != null);
      traces.push({x: used.map((p) => p.rt), y: used.map((p) => p.dev * 100), mode: 'markers', name: '试改后的偏差 %', yaxis: 'y2', marker: {symbol: 'diamond-open', color: '#b5841c', size: 9}});
    }
    draw(plot, traces, {height: 470, margin: {l: 64, r: 16, t: 14, b: 80}, showlegend: true, legend: {orientation: 'h', y: -0.16},
      xaxis: axis('保留时间 / min', {anchor: 'y2'}), yaxis: axis('log M（标准品 M，g/mol）', {domain: [0.34, 1]}),
      yaxis2: axis('偏差 %', {domain: [0, 0.24], zeroline: true, zerolinecolor: '#8f897c'}),
      shapes: [view.valid[0], view.valid[1]].map((x) => ({type: 'line', xref: 'x', yref: 'paper', x0: x, x1: x, y0: 0.34, y1: 1, line: {color: '#968f82', width: 1, dash: 'dot'}}))});
  }

  // ---------- 标准点表（试改） ----------
  function drawTable() {
    const top = Math.max(...view.influence.filter((v) => v != null));
    table.replaceChildren(h('table.data.compact.cal-table', {}, h('thead', {}, h('tr', {}, ['#', '保留时间 / min', 'M / (g/mol)', '拟合 M', '偏差', '用这个点', '去掉它，曲线最多变'].map((name) => h('th', {}, name)))),
      h('tbody', {}, view.points.map((p, i) => h('tr' + (enabled[i] ? '' : '.excluded'), {},
        h('td', {}, i + 1), h('td.num', {}, number(p.rt, 3)), h('td.num', {}, big(p.M)), h('td.num', {}, big(p.fit)),
        h('td.num' + (Math.abs(p.dev || 0) > 0.05 ? '.warn-ink' : ''), {}, pct(p.dev == null ? null : p.dev * 100)),
        h('td', {}, h('input', {type: 'checkbox', checked: enabled[i], onchange: (e) => { enabled[i] = e.target.checked; changed(); }})),
        h('td', {}, view.influence[i] == null ? h('span.quiet', {}, '—') : h('div.infl', {}, h('i', {style: {width: `${Math.max(4, view.influence[i] / top * 100)}%`}}),
          h('span', {}, `${number(view.influence[i], 2)} %`))))))));
  }

  // ---------- 试改：换点、换次数 → 重拟合预览；存成新曲线 ----------
  const degreeSelect = view.model === 'pchip' ? null : h('select', {onchange: (e) => { degree = Number(e.target.value); changed(); }},
    [1, 2, 3, 4, 5].map((n) => h('option', {value: n, selected: n === degree}, `${n} 次`)));
  const dirty = () => degree !== view.degree || enabled.some((on, i) => on !== view.points[i].on);
  function changed() { tried = null; drawTable(); drawPlot(); drawTrial(); }
  function drawTrial() {
    const parts = [h('div.cal-trial-bar', {}, h('b', {}, '试改'), h('span.quiet.small', {}, '在表里勾掉可疑的点、换次数，点"试算"看重拟合后的线和用这条曲线的样 Mw 变多少；满意再存成新曲线（这条不动）'),
      h('span.grow'), degreeSelect ? h('label.inline', {}, '次数', degreeSelect) : null,
      h('button.btn.small', {type: 'button', disabled: !dirty(), onclick: run}, icon('spark', 13), '试算'))];
    if (tried?.problem) parts.push(h('p.warn-ink.small', {}, tried.problem));
    else if (tried) parts.push(h('div.stat-row', {},
      h('div.stat', {}, h('div.stat-label', {}, 'R²'), h('div.stat-value.small', {}, `${number(view.r2, 5)} → ${number(tried.r2, 5)}`)),
      h('div.stat', {}, h('div.stat-label', {}, 'RMSE（log M）'), h('div.stat-value.small', {}, `${number(view.rmse, 4)} → ${number(tried.rmse, 4)}`)),
      h('div.stat' + (tried.diff > 2 ? '.warn' : ''), {}, h('div.stat-label', {}, '和这条线最多差'), h('div.stat-value.small', {}, `${number(tried.diff, 2)} %`)),
      Math.abs(tried.valid[0] - view.valid[0]) > 1e-3 || Math.abs(tried.valid[1] - view.valid[1]) > 1e-3 ? h('div.stat.warn', {}, h('div.stat-label', {}, '有效范围变了'),
        h('div.stat-value.small', {}, `${number(view.valid[0], 2)}–${number(view.valid[1], 2)} → ${number(tried.valid[0], 2)}–${number(tried.valid[1], 2)} min`),
        h('div.stat-note', {}, '谱上落到范围外的那一截不再按曲线算，Mw 可能跳得很大')) : null,
      h('div.stat' + (Math.max(Math.abs(tried.mw.min || 0), Math.abs(tried.mw.max || 0)) > 0.02 ? '.warn' : ''), {}, h('div.stat-label', {}, `用这条曲线的 ${tried.mw.n} 个样 Mw`),
        h('div.stat-value.small', {}, tried.mw.n ? `${pct(tried.mw.min * 100)} ~ ${pct(tried.mw.max * 100)}` : '—'), tried.mw.n ? h('div.stat-note', {}, `中位 ${pct(tried.mw.median * 100)}`) : null)),
      h('div.row', {}, h('span.grow'), h('button.btn.primary.small', {type: 'button', onclick: saveNew}, icon('check', 13), '存成新曲线')));
    trialBox.replaceChildren(...parts);
  }
  async function run() {
    trialBox.append(h('p.quiet.small', {}, `正在重拟合，再用新线重算这 ${view.samples.length} 个样（每 10 个样约 1 秒）…`));
    tried = await api(`/gpc-cal/${enc(library)}/${enc(id)}/trial`, 'POST', {enabled, degree});
    drawTrial(); drawPlot();
  }
  async function saveNew() {
    const name = prompt('新曲线叫什么？（原来那条不动；要用就去批处理里给样品换）', `${view.name}（改）`); if (!name) return;
    const out = await api(`/gpc-cal/${enc(library)}/${enc(id)}/new`, 'POST', {enabled, degree, name});
    toast(`存成了「${out.name}」`); location.hash = `#/testing/gpc/${enc('曲线:' + out.id)}`;
  }

  // ---------- 和别的曲线比 ----------
  const picked = new Set(view.others.map((o) => o.id));
  const cmpPlot = h('div.plot'), diffPlot = h('div.plot'), cmpTable = h('div');
  const shade = (index, count) => { const t = count > 1 ? index / (count - 1) : 1; const a = [190, 205, 222], b = [40, 70, 120]; return `rgb(${a.map((c, i) => Math.round(c + (b[i] - c) * t)).join(',')})`; };
  async function compare() {
    const data = await api(`/gpc-cal/${enc(library)}/${enc(id)}/compare`, 'POST', {others: [...picked]});
    const [me, ...rest] = data.rows;
    const ordered = [...rest].sort((a, b) => (a.date || '').localeCompare(b.date || ''));
    const color = (row) => row === me ? '#c96442' : shade(ordered.indexOf(row), ordered.length);
    // 同一 M 的保留时间按曲线日期排开：柱子有没有慢慢漂（这条是 0）
    const dated = [...data.rows].filter((row) => row.date && row.model !== 'pchip').sort((a, b) => a.date.localeCompare(b.date));
    const direct = data.rows.filter((row) => row.date && row.model === 'pchip');          // 标样直接连的曲线另画，不和多项式连成一条
    const day = (d) => `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;
    const LEVEL_COLOR = ['#c96442', '#3f7cc0', '#b5841c', '#8a63b8', '#5f8c3e'];
    draw(cmpPlot, data.levels.map((m, i) => ({x: dated.map((row) => day(row.date)), y: dated.map((row) => row.shift[m]), mode: 'lines+markers', name: `M ${Number(m).toLocaleString('en-US')}`,
      line: {color: LEVEL_COLOR[i % 5], width: 1.6}, marker: {size: 7}, text: dated.map((row) => row.name), hovertemplate: '%{text}<br>%{y:+.1f} s<extra></extra>'}))
      .concat(direct.length ? data.levels.map((m, i) => ({x: direct.map((row) => day(row.date)), y: direct.map((row) => row.shift[m]), mode: 'markers', showlegend: i === 0,
        name: '标样直接连的（PCHIP）', marker: {symbol: 'diamond-open', size: 10, color: LEVEL_COLOR[i % 5], line: {width: 2}}, text: direct.map((row) => `${row.name} · M ${Number(m).toLocaleString('en-US')}`),
        hovertemplate: '%{text}<br>%{y:+.1f} s<extra></extra>'})) : []),
    {height: 340, margin: {l: 60, r: 12, t: 10, b: 90}, showlegend: true, legend: {orientation: 'h', y: -0.32}, xaxis: axis('曲线日期', {type: 'date'}),
      yaxis: axis('同一 M 的保留时间（比这条）/ s', {zeroline: true, zerolinecolor: '#8f897c'}),
      shapes: me.date ? [{type: 'line', xref: 'x', yref: 'paper', x0: day(me.date), x1: day(me.date), y0: 0, y1: 1, line: {color: '#c96442', width: 1, dash: 'dot'}}] : []});
    draw(diffPlot, rest.map((row) => ({x: row.diff.rt, y: row.diff.pct, mode: 'lines', name: row.name.replace('GPC标准曲线-', ''), line: {color: color(row), width: 1.5},
      hovertemplate: `${row.name}<br>%{x:.2f} min · %{y:.2f} %<extra></extra>`})), {height: 330, margin: {l: 60, r: 12, t: 10, b: 44}, showlegend: false,
      xaxis: axis('保留时间 / min'), yaxis: axis(`M 对这条的偏差 %`, {zeroline: true, zerolinecolor: '#8f897c'})});
    cmpTable.replaceChildren(h('table.data.compact', {}, h('caption.quiet.small', {}, '右边几列：同一个 M，这条曲线算出的保留时间比本页这条晚（+）或早（−）几秒。'),
      h('thead', {}, h('tr', {}, h('th', {}, '曲线'), h('th', {}, 'M 最多差'),
      ...data.levels.map((m) => h('th', {}, `M ${Number(m).toLocaleString('en-US')}`)))),
      h('tbody', {}, rest.sort((a, b) => (b.date || '').localeCompare(a.date || '')).map((row) => h('tr', {},
        h('td', {}, h('i.kin-dot', {style: {background: color(row)}}), h('a', {href: `#/testing/gpc/${enc('曲线:' + row.id)}`}, row.name)),
        h('td.num' + (row.max > 3 ? '.warn-ink' : ''), {}, row.max == null ? '—' : `${number(row.max, 2)} %`),
        ...data.levels.map((m) => h('td.num', {}, row.shift[m] == null ? '—' : `${row.shift[m] > 0 ? '+' : ''}${number(row.shift[m], 1)} s`)))))));
  }
  const pickList = h('div.cal-picks', {}, view.others.map((o) => h('label.inline', {}, h('input', {type: 'checkbox', checked: true, onchange: (e) => { e.target.checked ? picked.add(o.id) : picked.delete(o.id); compare(); }}),
    `${o.name.replace('GPC标准曲线-', '')}${o.copies > 1 ? `（${o.copies} 份）` : ''}`)));

  box.append(head,
    h('section.block.exp-block', {}, h('header.block-head', {}, h('h3', {}, '标准点和拟合线'), h('span.quiet.small', {}, '下面一小格是每个标准点的偏差（拟合 M ÷ 标准 M − 1）；灰虚线是有效范围')), plot),
    h('section.block.exp-block', {}, h('header.block-head', {}, h('h3', {}, '标准点'), h('span.quiet.small', {}, '"去掉它，曲线最多变"：去掉这一个点重拟合，有效范围内 M 最多变多少；越长越要看准这个点')), table, trialBox),
    h('section.block.exp-block', {}, h('header.block-head', {}, h('h3', {}, '和别的曲线比（柱子有没有漂）'), h('span.quiet.small', {}, '左：同一 M 的保留时间按曲线日期排开；右：别的曲线算出的 M 比这条差多少（按日期由浅到深）')),
      pickList, h('div.exp-two', {}, h('div', {}, cmpPlot), h('div', {}, diffPlot)), cmpTable));
  drawTable(); drawTrial();
  requestAnimationFrame(() => { drawPlot(); compare(); });
  return box;
}
