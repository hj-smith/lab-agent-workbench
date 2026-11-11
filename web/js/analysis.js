// 交互分析：GPC 对比叠加。选样、叠加、归一、参比、分子量表、分区面积占比、结论——都自动存成一条"对比分析"记录，打开就是上次的样子。
import {api, fmt, go, h, icon, toast} from './core.js';
import {COLORS, chart, number} from './blocks.js';
import {chartPref, defaultButton} from './chartprefs.js';
import {orderBy} from './grouping.js';

const enc = encodeURIComponent;
const KIND = '对比分析';
const CURVE = {dist: '分布 dw/dlogM', cum: '累积', raw: '色谱'}, AXIS = {logM: 'log M', M: 'M（对数轴）'}, NORM = {area: '按面积', peak: '按峰高', none: '不归一'};
const DEFAULT_REGIONS = [{name: '< 1000', low: 0, high: 1000}, {name: '1000–3000', low: 1000, high: 3000}, {name: '3000–10⁴', low: 3000, high: 10000}, {name: '> 10⁴', low: 10000, high: 1e9}];

export async function analysisList(main, state, header) {
  const {rows} = await api(`/lib/${enc(state.library)}/records?kind=${enc(KIND)}`).catch(() => ({rows: []}));
  state.anchor('分析');
  main.replaceChildren(header('分析', '交互分析：选样、叠加、比较；每次的设置和结论都存下来，下次打开原样恢复',
    h('button.btn.primary', {type: 'button', onclick: () => go('analysis', 'new')}, icon('plus', 16), '新的 GPC 对比')),
  h('div.page-body', {},
    h('div.cards', {}, rows.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at))).map((row) => h('a.card', {href: '#/analysis/' + enc(row.id)},
      h('div.card-name', {}, row.name), h('div.card-text', {}, `${row['类型'] || 'GPC 对比'} · ${(row['样品'] || []).length} 个样品`),
      row['结论'] ? h('div.mini-text', {}, row['结论']) : null, h('div.card-axis', {}, '改动 ' + String(row.updated_at || '').slice(0, 16))))),
    rows.length ? null : h('div.empty-state', {}, h('p', {}, '还没有存下来的分析。'), h('p.quiet', {}, '在"样品与数据"里勾几条 GPC 样品点"对比分析"，或者点上面的按钮从头选。'))));
}

export async function gpcCompare(main, state, header, [id, preset]) {
  const library = state.library;
  const [{rows: samples}, {order}, pref] = await Promise.all([api(`/lib/${enc(library)}/records?kind=${enc('GPC样品')}`), api(`/project-order/${enc(library)}/GPC`),
    chartPref(library, 'GPC对比', {})]);
  let saved = id && id !== 'new' ? await api(`/lib/${enc(library)}/records/${enc(id)}?series=false`) : null;
  // 新开的对比按作图偏好"GPC对比"画（存的是中文：曲线=累积、横轴=log M、归一=按峰高），已存的对比按它自己的设置
  const byLabel = (table, label) => Object.entries(table).find(([, text]) => text === label)?.[0];
  const preferred = Object.fromEntries([['curve', CURVE, '曲线'], ['axis', AXIS, '横轴'], ['norm', NORM, '归一']].map(([key, table, name]) => [key, byLabel(table, pref[name])]).filter(([, value]) => value));
  const doc = {
    id: saved?.id || null, name: saved?.name || `GPC 对比 ${new Date().toLocaleDateString('zh-CN', {timeZone: 'Asia/Shanghai'})}`,
    samples: saved?.data['样品'] || (preset ? preset.split(',') : []), conclusion: saved?.data['结论'] || '',
    settings: {curve: 'dist', axis: 'logM', norm: 'area', reference: null, regions: DEFAULT_REGIONS, ...preferred, ...(saved?.data['设置'] || {})},
  };
  state.anchor(`GPC 对比分析「${doc.name}」`);
  const cache = {};
  let timer = null;
  const status = h('span.quiet.small', {}, doc.id ? '已存' : '还没存（选了样品就自动存）');
  const persist = () => { clearTimeout(timer); status.textContent = '正在存…'; timer = setTimeout(async () => {
    if (!doc.samples.length) { status.textContent = '选几个样品'; return; }
    const {saved: done} = await api(`/lib/${enc(library)}/records`, 'POST', {kind: KIND, rows: [{...(doc.id ? {id: doc.id} : {}), name: doc.name, 类型: 'GPC 对比', 样品: doc.samples, 设置: doc.settings, 结论: doc.conclusion,
      links: doc.samples.map((sample) => ({relation: '对比了', to: sample}))}]});
    if (!doc.id) { doc.id = done[0].id; history.replaceState(null, '', '#/analysis/' + enc(doc.id)); }
    status.textContent = '已存 · ' + new Date().toLocaleTimeString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false});
  }, 600); };

  // ----- 选样 -----
  let filter = '';
  const picker = h('div.pick-list');
  const groups = {};
  samples.forEach((row) => (groups[row['所属项目'] || '其它'] ||= []).push(row));
  const drawPicker = () => picker.replaceChildren(...orderBy(order, Object.entries(groups).sort((a, b) => b[0].localeCompare(a[0], 'zh-CN', {numeric: true})), ([name]) => name).map(([group, items]) => {
    const shown = items.filter((row) => !filter || (row.name + group).toLowerCase().includes(filter));
    if (!shown.length) return null;
    return h('details.pick-group', {open: !!filter || shown.some((row) => doc.samples.includes(row.id))}, h('summary', {}, group, h('small', {}, shown.length)),
      shown.map((row) => h('label.pick-item', {}, h('input', {type: 'checkbox', checked: doc.samples.includes(row.id), onchange: (event) => {
        doc.samples = event.target.checked ? [...doc.samples, row.id] : doc.samples.filter((item) => item !== row.id);
        if (doc.settings.reference && !doc.samples.includes(doc.settings.reference)) doc.settings.reference = null;
        drawAll(); persist();
      }}), row.name)));
  }));
  const search = h('input', {type: 'search', placeholder: '找样品、项目', oninput: () => { filter = search.value.trim().toLowerCase(); drawPicker(); }});

  // ----- 取数 -----
  const load = async (sampleId, withSeries) => {
    const key = sampleId + (withSeries ? ':s' : '');
    cache[key] ||= api(`/lib/${enc(library)}/records/${enc(sampleId)}?series=${withSeries}`);
    return cache[key];
  };
  const trapz = (x, y) => { let sum = 0; for (let i = 1; i < x.length; i++) if (x[i] != null && y[i] != null && x[i - 1] != null && y[i - 1] != null) sum += (x[i] - x[i - 1]) * (y[i] + y[i - 1]) / 2; return Math.abs(sum); };
  const regionShare = (dist, low, high) => {
    const x = dist.logM, y = dist['dw/dlogM'];
    if (!x || !y) return null;
    const lo = Math.log10(Math.max(low, 1)), hi = Math.log10(high);
    const xs = [], ys = [];
    x.forEach((value, index) => { if (value >= lo && value <= hi) { xs.push(value); ys.push(y[index]); } });
    const total = trapz(x, y);
    return total ? trapz(xs, ys) / total * 100 : null;
  };

  // ----- 画 -----
  const controls = h('div.ana-controls'), plot = h('div.plot.ana-plot'), tableBox = h('div'), regionBox = h('div');
  const seg = (key, options) => h('div.segs', {}, options.map(([value, label]) => h('button.seg' + (doc.settings[key] === value ? '.on' : ''), {type: 'button', onclick: () => { doc.settings[key] = value; drawAll(); persist(); }}, label)));
  async function drawAll() {
    const s = doc.settings;
    controls.replaceChildren(
      h('label.inline', {}, '曲线', seg('curve', Object.entries(CURVE))),
      s.curve !== 'raw' ? h('label.inline', {}, '横轴', seg('axis', Object.entries(AXIS))) : null,
      h('label.inline', {}, '归一', seg('norm', s.curve === 'cum' ? [['none', '不归一']] : Object.entries(NORM))),
      h('label.inline', {}, '参比', h('select', {onchange: (event) => { s.reference = event.target.value || null; drawAll(); persist(); }},
        h('option', {value: ''}, '不设'), doc.samples.map((sampleId) => h('option', {value: sampleId, selected: sampleId === s.reference}, samples.find((row) => row.id === sampleId)?.name || sampleId)))),
      defaultButton(library, 'GPC对比', () => ({'曲线': CURVE[s.curve], '横轴': AXIS[s.axis], '归一': NORM[s.norm]})));
    if (!doc.samples.length) { plot.replaceChildren(h('p.quiet.pad-l', {}, '在左边勾几个样品。')); tableBox.replaceChildren(); regionBox.replaceChildren(); return; }
    const items = await Promise.all(doc.samples.map((sampleId) => load(sampleId, s.curve === 'raw')));
    const series = [];
    items.forEach((item, index) => {
      const color = COLORS[index % COLORS.length], ref = item.id === s.reference;
      if (s.curve === 'raw') {
        const table = item.series['色谱'] || Object.values(item.series)[0];
        if (!table) return;
        let y = table.rows.map((row) => row[1]);
        const peak = Math.max(...y.filter((value) => value != null));
        if (s.norm === 'peak' && peak) y = y.map((value) => value / peak);
        if (s.norm === 'area') { const area = trapz(table.rows.map((row) => row[0]), y); if (area) y = y.map((value) => value / area); }
        series.push({name: item.name, x: table.rows.map((row) => row[0]), y, color, dash: ref ? 'dash' : 'solid', mode: 'lines'});
        return;
      }
      const dist = item.results['GPC分布']?.value;
      if (!dist) return;
      const x = s.axis === 'M' ? dist.M : dist.logM;
      let y = s.curve === 'cum' ? dist['累积质量分数'] : dist['dw/dlogM'];
      if (s.curve === 'dist' && s.norm === 'peak') { const peak = Math.max(...y); y = y.map((value) => value / peak); }
      if (s.curve === 'dist' && s.norm === 'area') { const area = trapz(dist.logM, y); if (area) y = y.map((value) => value / area); }
      series.push({name: item.name + (ref ? '（参比）' : ''), x, y, color, dash: ref ? 'dash' : 'solid', mode: 'lines'});
    });
    chart(plot, {series, height: 460, log_x: s.curve !== 'raw' && s.axis === 'M',
      x: s.curve === 'raw' ? '保留时间 / min' : s.axis === 'M' ? 'M（PS 当量）' : 'log M（PS 当量）',
      y: s.curve === 'cum' ? '累积质量分数' : s.curve === 'raw' ? '信号' + (s.norm === 'none' ? '' : '（归一）') : 'dw/dlogM' + (s.norm === 'none' ? '' : '（归一）')});
    const refMw = items.find((item) => item.id === s.reference)?.results['GPC分子量']?.value?.Mw;
    tableBox.replaceChildren(h('section.block', {}, h('header.block-head', {}, h('h3', {}, '分子量（PS 当量读数）')),
      h('div.tablewrap.flat', {}, h('table.data.compact', {}, h('thead', {}, h('tr', {}, ['', '样品', 'Mn', 'Mw', 'Mz', 'PDI', 'Mp', s.reference ? 'Mw 对参比' : null].filter((name) => name !== null).map((name) => h('th', {}, name)))),
        h('tbody', {}, items.map((item, index) => {
          const mw = item.results['GPC分子量']?.value || {};
          return h('tr', {}, h('td', {}, h('span.swatch', {style: `background:${COLORS[index % COLORS.length]}`})), h('td', {}, h('a', {href: `#/record/${enc(library)}/${enc(item.id)}`}, item.name)),
            ['Mn', 'Mw', 'Mz'].map((key) => h('td.num', {}, mw[key] != null ? Math.round(mw[key]).toLocaleString('en-US') : '')), h('td.num', {}, number(mw.PDI, 3)), h('td.num', {}, mw.Mp != null ? Math.round(mw.Mp).toLocaleString('en-US') : ''),
            s.reference ? h('td.num', {}, refMw && mw.Mw ? `${((mw.Mw / refMw - 1) * 100).toFixed(1)} %` : '') : null);
        }))))));
    const regions = s.regions;
    regionBox.replaceChildren(h('section.block', {}, h('header.block-head', {}, h('h3', {}, '分子量区间的面积占比 %'),
      h('button.btn.ghost.small', {type: 'button', onclick: () => { regions.push({name: '新区间', low: 1000, high: 2000}); drawAll(); persist(); }}, icon('plus', 13), '加区间'),
      h('button.btn.ghost.small', {type: 'button', onclick: () => { s.regions = DEFAULT_REGIONS.map((item) => ({...item})); drawAll(); persist(); }}, '恢复默认')),
      h('div.tablewrap.flat', {}, h('table.data.compact', {},
        h('thead', {}, h('tr', {}, h('th', {}, '样品'), regions.map((region, index) => h('th', {}, h('div.region-edit', {},
          h('input', {type: 'text', value: region.low, title: '下限 g/mol', onchange: (event) => { region.low = Number(event.target.value) || 0; region.name = `${region.low}–${region.high}`; drawAll(); persist(); }}), '–',
          h('input', {type: 'text', value: region.high >= 1e9 ? '∞' : region.high, title: '上限 g/mol', onchange: (event) => { region.high = event.target.value.trim() === '∞' ? 1e9 : Number(event.target.value) || 1e9; drawAll(); persist(); }}),
          h('button.btn.ghost.small', {type: 'button', title: '去掉', onclick: () => { regions.splice(index, 1); drawAll(); persist(); }}, '×')))))),
        h('tbody', {}, items.map((item) => h('tr', {}, h('td', {}, item.name), regions.map((region) => {
          const share = item.results['GPC分布']?.value ? regionShare(item.results['GPC分布'].value, region.low, region.high) : null;
          return h('td.num', {}, share != null ? share.toFixed(1) : '');
        })))))),
      h('p.block-note', {}, '按 dw/dlogM 对 log M 积分算面积占比；区间端点是 PS 当量分子量（g/mol），可以直接改。')));
  }

  const nameInput = h('input.ana-name', {type: 'text', value: doc.name, onchange: (event) => { doc.name = event.target.value.trim() || doc.name; persist(); }});
  const conclusion = h('textarea', {rows: 3, placeholder: '这次对比看出了什么（自动存）', oninput: (event) => { doc.conclusion = event.target.value; persist(); }}, doc.conclusion);
  main.replaceChildren(header('分析', 'GPC 对比叠加', status,
    h('button.btn', {type: 'button', onclick: () => state.panel.ask(`解读这次 GPC 对比「${doc.name}」：${doc.samples.map((sampleId) => samples.find((row) => row.id === sampleId)?.name).join('、')}。分布形状、分子量、各区间占比有什么差别，说明了什么？结论我会填到分析里。`, `GPC 对比「${doc.name}」`)}, icon('spark', 15), '让助手解读'),
    h('a.btn', {href: '#/analysis'}, '全部分析')),
  h('div.ana', {},
    h('aside.ana-pick', {}, h('label.search.small', {}, icon('search', 14), search), h('div.quiet.small', {}, `${samples.length} 个 GPC 样品，按项目分组`), picker),
    h('section.ana-main', {}, nameInput, controls, plot, h('div.blocks', {}, tableBox, regionBox), h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '结论')), conclusion))));
  drawPicker();
  await drawAll();
  if (!doc.id && doc.samples.length) persist();
}
