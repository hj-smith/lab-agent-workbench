// 膜厚–固含曲线：几个固含配出来的膜厚连成曲线（直线 / 二次 / 幂函数，按 AICc 推荐），反算目标膜厚要多少固含，一批料要补多少溶剂。
// 点从膜里取（同一转速、烘烤条件）或手填；勾掉的点不参与拟合，照样画出来（空心）。算用配方模块原样搬来的 pilot_fit。
import {api, h, icon, toast} from './core.js';
import {number, plotly} from './blocks.js';

const enc = encodeURIComponent;
const COLOR = {linear: '#c96442', quadratic: '#3f7cc0', power: '#b5841c'};
const pct = (fraction) => fraction == null || fraction === '' ? '' : +(Number(fraction) * 100).toFixed(4);

export async function thicknessPage(state, box) {
  const library = state.library;
  const {curves, films} = await api(`/thickness/${enc(library)}`);
  let current = {id: null, name: '新的膜厚曲线', condition: '', points: [], target: null};
  const list = h('nav.kind-list.test-list'), body = h('div.thick-body');
  const plot = h('div.plot'), fitBox = h('div'), answerBox = h('div'), pointBox = h('div');
  let mass = '', solidsNow = '';

  const open = async (id) => {
    const got = await api(`/thickness/${enc(library)}/${enc(id)}`);
    current = {id: got.id, name: got.name, condition: got.condition, points: got.points, target: got.target};
    drawAll();
  };
  const drawList = () => list.replaceChildren(
    h('button.btn.primary.small', {type: 'button', onclick: () => { current = {id: null, name: '新的膜厚曲线', condition: '', points: [], target: null}; drawAll(); }}, icon('plus', 13), '新曲线'),
    ...curves.map((c) => h('a.pick-link' + (c.id === current.id ? '.on' : ''), {href: 'javascript:void 0', onclick: () => open(c.id)}, c.name, h('small.quiet', {}, ` ${c.points} 点`))),
    curves.length ? null : h('p.quiet.small', {}, '还没有存过曲线。'));

  const analyse = async () => {
    const out = await api(`/thickness/${enc(library)}/analyse`, 'POST', {points: current.points, target: current.target,
      mass: mass === '' ? null : Number(mass), solids: solidsNow === '' ? null : Number(solidsNow) / 100});
    drawFits(out); drawAnswer(out); drawPlot(out);
  };

  function drawPoints() {
    const row = (p, i) => h('tr' + (p['纳入'] === false ? '.excluded' : ''), {},
      h('td', {}, h('input', {type: 'number', step: 'any', value: pct(p['固含']), style: 'width:90px', onchange: (e) => { p['固含'] = e.target.value === '' ? null : Number(e.target.value) / 100; analyse(); }})),
      h('td', {}, h('input', {type: 'number', step: 'any', value: p['膜厚'] == null ? '' : +Number(p['膜厚']).toFixed(2), style: 'width:90px', onchange: (e) => { p['膜厚'] = e.target.value === '' ? null : Number(e.target.value); analyse(); }})),
      h('td', {}, h('input', {type: 'checkbox', checked: p['纳入'] !== false, onchange: (e) => { p['纳入'] = e.target.checked; drawPoints(); analyse(); }})),
      h('td.small.quiet', {}, p['来源'] || '手填'),
      h('td', {}, h('button.btn.ghost.small', {type: 'button', onclick: () => { current.points.splice(i, 1); drawPoints(); analyse(); }}, '×')));
    const take = h('select', {onchange: (e) => {
      const group = films.find((g) => g['条件'] === e.target.value); e.target.value = ''; if (!group) return;
      group['点'].forEach((p) => { if (!current.points.some((q) => q['来源'] === p['来源'])) current.points.push({...p}); });
      if (!current.condition) current.condition = group['条件'];
      drawAll();
    }}, h('option', {value: ''}, '从膜里取点…'), films.map((g) => h('option', {value: g['条件']}, `${g['条件']}（${g['点'].length} 块膜，${g['固含水平']} 个固含）`)));
    pointBox.replaceChildren(
      h('table.data.compact.edit-table', {}, h('thead', {}, h('tr', {}, ['固含 / %', '膜厚 / nm', '纳入', '来源', ''].map((c) => h('th', {}, c)))),
        h('tbody', {}, current.points.map(row))),
      h('div.row', {}, h('button.btn.ghost.small', {type: 'button', onclick: () => { current.points.push({'固含': null, '膜厚': null, '纳入': true}); drawPoints(); }}, icon('plus', 13), '加一行'), take));
  }

  function drawFits(out) {
    fitBox.replaceChildren(out.fits.length ? h('table.data.compact', {}, h('thead', {}, h('tr', {}, ['模型', 'R²', 'RMSE / nm', 'AICc', '点数', ''].map((c) => h('th', {}, c)))),
      h('tbody', {}, out.fits.map((f) => h('tr' + (f.recommended ? '.best' : ''), {}, h('td', {}, h('i.kin-dot', {style: {background: COLOR[f.model]}}), f.label),
        h('td.num', {}, number(f.r2, 4)), h('td.num', {}, number(f.rmse, 2)), h('td.num', {}, f.aicc == null ? '—' : number(f.aicc, 2)), h('td.num', {}, f.n),
        h('td.small', {}, f.recommended ? h('span.st-tag.done', {}, '推荐') : '', f.warnings.length ? h('span.warn-ink', {title: f.warnings.join('；')}, ' 有提醒') : ''))))) : null,
    out.fits.some((f) => f.warnings.length) ? h('p.quiet.small', {}, [...new Set(out.fits.flatMap((f) => f.warnings))].join('；')) : null,
    h('p.quiet.small', {}, out.reason || ''),
    Object.keys(out.problems).length ? h('p.quiet.small', {}, Object.entries(out.problems).map(([m, text]) => `${m === 'power' ? '幂函数' : m === 'quadratic' ? '二次' : '直线'}：${text}`).join('；')) : null);
  }

  function drawAnswer(out) {
    const best = out.answer[out.best] || {};
    const rows = Object.entries(out.answer).map(([model, a]) => h('tr' + (model === out.best ? '.best' : ''), {},
      h('td', {}, {linear: '直线', quadratic: '二次', power: '幂函数'}[model]),
      h('td.num', {}, a.problem ? a.problem : `${number(a.solids * 100, 4)} %`),
      h('td.small', {}, a.outside ? h('span.warn-ink', {}, '在已有点的范围外（外推）') : ''),
      h('td.num', {}, a.solvent == null ? '—' : a.solvent >= 0 ? `补溶剂 ${number(a.solvent, 1)} g` : `要提高固含（不能加溶剂；相当于少 ${number(-a.solvent, 1)} g 溶剂）`)));
    answerBox.replaceChildren(
      h('div.row', {}, h('label.inline', {}, '目标膜厚', h('input', {type: 'number', step: 'any', value: current.target ?? '', style: 'width:90px', onchange: (e) => { current.target = e.target.value === '' ? null : Number(e.target.value); analyse(); }}), ' nm'),
        h('label.inline', {}, '这批料', h('input', {type: 'number', step: 'any', value: mass, style: 'width:90px', placeholder: 'g', onchange: (e) => { mass = e.target.value; analyse(); }}), ' g'),
        h('label.inline', {}, '现在固含', h('input', {type: 'number', step: 'any', value: solidsNow, style: 'width:80px', placeholder: '%', onchange: (e) => { solidsNow = e.target.value; analyse(); }}), ' %')),
      rows.length ? h('table.data.compact', {}, h('thead', {}, h('tr', {}, ['模型', '要的固含', '', '这批料怎么调'].map((c) => h('th', {}, c)))), h('tbody', {}, rows)) : h('p.quiet.small', {}, '填目标膜厚就反算要多少固含；再填这批料多少克、现在固含，算出要补多少溶剂。'),
      best.solids != null ? h('p.small', {}, `按推荐的模型：到 ${current.target} nm 要固含 ${number(best.solids * 100, 4)} %` + (best.outside ? '（超出已有点的范围，外推的，先做一个点验证）' : '') + '。') : null);
  }

  async function drawPlot(out) {
    const Plotly = await plotly();
    const used = current.points.filter((p) => p['纳入'] !== false && p['固含'] != null && p['膜厚'] != null);
    const dropped = current.points.filter((p) => p['纳入'] === false && p['固含'] != null && p['膜厚'] != null);
    const traces = [
      ...Object.entries(out.lines).map(([model, line]) => ({x: line.x.map((x) => x * 100), y: line.y, mode: 'lines', name: {linear: '直线', quadratic: '二次', power: '幂函数'}[model],
        line: {color: COLOR[model], width: model === out.best ? 2.6 : 1.3, dash: model === out.best ? 'solid' : 'dot'}, hoverinfo: 'skip'})),
      {x: used.map((p) => p['固含'] * 100), y: used.map((p) => p['膜厚']), mode: 'markers', name: '点', marker: {color: '#29261f', size: 9}, text: used.map((p) => p['来源'] || '手填'), hovertemplate: '%{text}<br>固含 %{x:.3f} % · %{y:.1f} nm<extra></extra>'},
      ...(dropped.length ? [{x: dropped.map((p) => p['固含'] * 100), y: dropped.map((p) => p['膜厚']), mode: 'markers', name: '勾掉的点', marker: {color: '#fff', size: 9, line: {color: '#968f82', width: 1.5}}}] : [])];
    const best = out.answer[out.best];
    if (best?.solids != null && current.target) traces.push({x: [best.solids * 100], y: [current.target], mode: 'markers', name: '目标', marker: {symbol: 'star', size: 14, color: '#c96442'}});
    await Plotly.react(plot, traces, {paper_bgcolor: '#fbfaf7', plot_bgcolor: '#fbfaf7', height: 360, margin: {l: 60, r: 16, t: 10, b: 50}, showlegend: true, legend: {orientation: 'h', y: -0.2},
      font: {family: 'Microsoft YaHei, sans-serif', size: 12, color: '#29261f'}, xaxis: {title: {text: '固含 / %'}, gridcolor: '#ebe6dc', zeroline: false}, yaxis: {title: {text: '膜厚 / nm'}, gridcolor: '#ebe6dc', zeroline: false}},
    {displaylogo: false, responsive: true});
  }

  const save = async () => {
    const got = await api(`/thickness/${enc(library)}`, 'PUT', {id: current.id, name: current.name, condition: current.condition, points: current.points, target: current.target});
    current.id = got.id;
    const item = curves.find((c) => c.id === got.id);
    if (item) Object.assign(item, {name: got.name, points: got.points.length}); else curves.unshift({id: got.id, name: got.name, condition: got.condition, points: got.points.length});
    toast('曲线存好了'); drawList();
  };

  function drawAll() {
    drawList(); drawPoints();
    body.replaceChildren(
      h('div.gpc-bar', {}, h('label.inline', {}, '名字', h('input', {type: 'text', value: current.name, style: 'width:220px', onchange: (e) => { current.name = e.target.value; }})),
        h('label.inline', {}, '成膜条件', h('input', {type: 'text', value: current.condition, placeholder: '例：1500 rpm · 250 °C · 60 s', style: 'width:240px', onchange: (e) => { current.condition = e.target.value; }})),
        h('span.grow'), h('button.btn.primary.small', {type: 'button', onclick: save}, icon('check', 13), '存')),
      h('p.block-note', {}, '同一个成膜条件下，几个固含各配一个、涂膜测厚，连成曲线；勾掉的点不参与拟合。点越多越可靠（少于 5 个会提醒）。'),
      h('div.exp-two', {}, h('section.block', {}, h('header.block-head', {}, h('h3', {}, '点')), pointBox),
        h('section.block', {}, h('header.block-head', {}, h('h3', {}, '曲线')), plot, fitBox)),
      h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '反算：到目标膜厚要多少固含')), answerBox));
    analyse();
  }

  box.replaceChildren(h('div.split.test-split', {}, list, h('div.split-main', {}, body)));
  if (curves.length) await open(curves[0].id); else drawAll();
}
