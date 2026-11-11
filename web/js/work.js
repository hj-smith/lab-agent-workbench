// 研发工作区：样品与数据按项目组织；实验的 SOP 与批记录（纸面、网页里填、导出、导回）；配方的工单；工单的称量。
import {previewExport} from './viewer.js';
import {api, go, h, icon, toast} from './core.js';
import {number, renderBlocks} from './blocks.js';

const enc = encodeURIComponent;

// ---------- 样品与数据：项目树 + 项目页 ----------

export async function projectsData(main, state, key, header, ask) {
  const tree = await api('/projects/' + enc(state.library));
  const all = tree.groups.flatMap((group) => group.items.flatMap((item) => [item, ...(item.children || [])]));
  const current = key || tree.groups[0]?.items[0]?.key;
  let filter = '';
  const nav = h('nav.project-tree', {'aria-label': '项目'});
  const counts = (item) => h('span.pt-counts', {}, Object.entries(item.counts || {}).filter(([, value]) => value).map(([label, value]) => h('span', {}, `${label} ${value}`)));
  const drawNav = () => nav.replaceChildren(
    h('label.search.small', {}, icon('search', 14), h('input', {type: 'search', placeholder: '找项目、编号', value: filter, oninput: (event) => { filter = event.target.value.trim().toLowerCase(); drawNav(); event.target.focus(); }})),
    ...tree.groups.map((group) => {
      const items = group.items.filter((item) => !filter || [item.name, item.sub, ...(item.children || []).map((child) => child.name)].join(' ').toLowerCase().includes(filter));
      return items.length ? h('details.pt-group', {open: true}, h('summary', {}, group.name, h('small', {}, items.length)),
        h('p.pt-about', {}, group.about),
        items.map((item) => item.children ? h('details.pt-series', {open: item.children.some((child) => child.key === current) || item.key === current},
          h('summary', {}, h('a' + (item.key === current ? '.on' : ''), {href: '#/data/' + enc(item.key)}, h('b', {}, item.name), h('small', {}, item.sub)), counts(item)),
          item.children.map((child) => h('a.pt-item.child' + (child.key === current ? '.on' : ''), {href: '#/data/' + enc(child.key)}, h('b', {}, child.name), counts(child))))
          : h('a.pt-item' + (item.key === current ? '.on' : ''), {href: '#/data/' + enc(item.key)},
            h('div.pt-line', {}, h('b', {}, item.name), item.status ? h('span.st-tag.' + (item.status === '已完成' ? 'done' : item.status.includes('设计') ? 'open' : 'current'), {}, item.status) : null),
            item.sub ? h('div.pt-sub', {}, item.sub) : null, counts(item)))) : null;
    }),
    h('a.pt-bykind', {href: '#/data/kind'}, icon('columns', 14), '按类型看全部测试'));
  drawNav();
  const body = h('div.project-main', {}, h('p.quiet', {}, '正在打开…'));
  const meta = all.find((item) => item.key === current);
  state.anchor(`样品与数据 · ${meta?.name || current}`);
  main.replaceChildren(header('样品与数据', '按项目：实验 → 取样 → 测试；树脂 → 配方 → 膜、n/k、老化'), h('div.split.project-split', {}, nav, body));
  if (!current) { body.replaceChildren(h('p.quiet', {}, '还没有项目。')); return; }
  const view = await api(`/projects/${enc(state.library)}/${enc(current)}`);
  body.replaceChildren(
    h('div.project-head', {}, h('div', {}, h('h2', {}, view.title), view.subtitle ? h('p.quiet', {}, view.subtitle) : null),
      h('div.head-actions', {},
        view.record ? h('a.btn', {href: `#/record/${enc(state.library)}/${enc(view.record)}`}, icon('open', 14), view.kind === '树脂实验' ? '打开实验（设计、SOP、批记录）' : '打开配方（组成、工单）') : null,
        h('button.btn', {type: 'button', onclick: () => ask(`看一下项目「${view.title}」的全部数据：结果说明了什么，有没有异常，和同类比怎样，下一步做什么？`)}, icon('spark', 14), '让助手看这个项目'))),
    renderBlocks({blocks: view.blocks.map((block, index) => ({...block, key: block.title || `${block.type}-${index}`})), hidden: [], source: '项目'}, {library: state.library, kind: '项目 · ' + current.split(':')[0]}));
}

// ---------- 实验：SOP 与批记录 ----------

// 过程图：温度—时间曲线 + 投料和操作流程（树脂核心按设计画，和旧版同一张）
export function processFigure(figure) {
  return h('div.pe-figure.paper-figure-box', {},
    h('div.pe-figure-curve', {html: figure.curve}),
    h('div.sp-flow', {html: figure.flow}));
}

// 偏差检查：超差的在最前面，写清计划、实际、差多少、允许多少；没填的放最后
function deviationView(rows) {
  const count = (kind) => rows.filter((row) => row['判定'] === kind).length;
  const tone = {超差: 'off', 正常: 'done', 没填: 'todo'};
  const allowed = (tol) => !tol ? '' : tol.relative != null ? `±${(tol.relative * 100).toFixed(1).replace(/\.0$/, '')} %` : tol.absolute != null ? `±${tol.absolute}` : '';
  return h('div.deviation-view', {},
    h('div.stat-row', {},
      h('div.stat' + (count('超差') ? '.bad' : '.good'), {}, h('div.stat-label', {}, '超出允许范围'), h('div.stat-value', {}, count('超差'), h('small', {}, ' 项'))),
      h('div.stat.good', {}, h('div.stat-label', {}, '在范围内'), h('div.stat-value', {}, count('正常'), h('small', {}, ' 项'))),
      h('div.stat', {}, h('div.stat-label', {}, '还没填'), h('div.stat-value', {}, count('没填'), h('small', {}, ' 项')))),
    h('div.tablewrap.flat', {}, h('table.data.compact', {},
      h('thead', {}, h('tr', {}, ['项目', '计划', '实际', '差', '允许', '判定'].map((name) => h('th', {}, name)))),
      h('tbody', {}, rows.map((row) => h('tr' + (row['判定'] === '超差' ? '.row-bad' : ''), {},
        h('td', {}, row['项目']),
        h('td.num', {}, row['计划'] != null ? `${+Number(row['计划']).toFixed(2)} ${row['单位']}` : '—'),
        h('td.num', {}, row['实际'] != null && row['实际'] !== '' ? `${row['实际']} ${row['单位']}`.trim() : '—'),
        h('td.num', {}, row['允许']?.absolute != null && row['差值'] != null ? `${row['差值'] > 0 ? '+' : ''}${+Number(row['差值']).toFixed(2)} ${row['单位']}`   // 按绝对值管的（温度）就写差几度
          : row['差'] != null ? `${row['差'] > 0 ? '+' : ''}${(row['差'] * 100).toFixed(1)} %` : '—'),
        h('td.num', {}, allowed(row['允许']) ? allowed(row['允许']) + (row['允许']?.absolute != null ? ' ' + row['单位'] : '') : '—'),
        h('td', {}, h('span.st-tag.' + tone[row['判定']], {}, row['判定']))))))),
    h('p.quiet.small', {}, '偏差按树脂核心的偏差规则算（和导出的批记录同一套）。超差不等于做错：外温比内温设定高是常见的，看说明再判断。'));
}

export async function paperView(state, library, record) {
  const box = h('div.paper-view', {}, h('p.quiet', {}, '正在排版…'));
  const doc = await api(`/paper/${enc(library)}/${enc(record.id)}`);
  let sheet = doc.sheets[0]?.name;
  const changes = {};
  let timer = null;
  const status = h('span.quiet.small');
  const flush = async () => {
    const values = {...Object.fromEntries(Object.entries(changes))};
    Object.keys(changes).forEach((key) => delete changes[key]);
    if (!Object.keys(values).length) return;
    const result = await api(`/paper/${enc(library)}/${enc(record.id)}`, 'PUT', {values});
    status.textContent = `已存 · 共填了 ${result.filled} 格`;
  };
  const queue = (key, value) => { changes[key] = value; status.textContent = '正在存…'; clearTimeout(timer); timer = setTimeout(flush, 700); };
  const picker = h('input', {type: 'file', accept: '.xlsx', hidden: true, onchange: async () => {
    const file = picker.files[0]; if (!file) return;
    const response = await fetch(`/api/paper/${enc(library)}/${enc(record.id)}/import/${enc(file.name)}`, {method: 'PUT', body: file});
    const result = await response.json();
    if (!response.ok) { toast('导回失败：' + (result.detail || response.statusText)); return; }
    toast(`导回了 ${Object.keys(result.values || result['实测'] || {}).length || ''} 个实测值`); draw(await api(`/paper/${enc(library)}/${enc(record.id)}`));
  }});
  const cell = (item) => {
    const span = item.span ? {style: `grid-column: span ${item.span}`} : {};
    if (item.input) {
      const input = h('input.paper-input', {type: 'text', value: item.value ?? '', placeholder: item.unit || '', title: item.key,
        onchange: (event) => { const text = event.target.value.trim(); queue(item.key, text === '' ? null : item.type === 'number' && !Number.isNaN(Number(text)) ? Number(text) : text); }});
      return h('td.paper-in', {colspan: item.span || 1}, input);
    }
    return h('td' + (item.calc ? '.paper-calc' : '') + (item.style === 'signature' ? '.paper-sign' : ''), {colspan: item.span || 1}, item.t);
  };
  const drawSheet = (data) => {
    const current = data.sheets.find((item) => item.name === sheet) || data.sheets[0];
    if (current.name === '偏差检查') return deviationView(data.deviations || []);
    const figure = current.name === 'SOP' && data.figure?.curve ? processFigure(data.figure) : null;   // 和旧版一样：SOP 开头先放过程图
    return h('article.paper', {}, figure, current.blocks.map((block) => {
      if (block.type === 'title') return h('h1.paper-title', {}, block.text);
      if (block.type === 'section') return h('h2.paper-section', {}, block.text);
      if (block.type === 'note') return h('p.paper-note', {}, block.text);
      return h('div.paper-table', {}, block.title ? h('div.paper-caption', {}, block.title) : null,
        h('table', {}, block.header.length ? h('thead', {}, h('tr', {}, block.header.map((item) => h('th', {colspan: item.span || 1}, item.label ?? item)))) : null,
          h('tbody', {}, block.rows.map((row) => h('tr', {}, row.map(cell))))));
    }));
  };
  const draw = (data) => box.replaceChildren(
    h('div.paper-bar', {},
      h('div.segs', {}, data.sheets.map((item) => h('button.seg' + (item.name === sheet ? '.on' : ''), {type: 'button', onclick: () => { sheet = item.name; draw(data); }}, item.name))),
      status,
      h('div.kv-actions', {},
        h('button.btn.small', {type: 'button', onclick: () => previewExport(`/api/paper/${enc(library)}/${enc(record.id)}/export?fmt=xlsx`, '执行版 Excel')}, icon('download', 14), '执行版 Excel'),
        h('button.btn.small', {type: 'button', onclick: () => previewExport(`/api/paper/${enc(library)}/${enc(record.id)}/export?fmt=docx`, 'SOP Word')}, icon('download', 14), 'SOP Word'),
        h('button.btn.small', {type: 'button', onclick: () => previewExport(`/api/paper/${enc(library)}/${enc(record.id)}/export?fmt=xlsx&filled=true`, '已填批记录')}, icon('download', 14), '已填批记录'),
        h('button.btn.small', {type: 'button', onclick: () => picker.click()}, icon('upload', 14), '导回填好的批记录'), picker)),
    h('p.paper-tip', {}, `批记录的格子可以直接填（已填 ${data.filled} 格），填完自动存，产率、偏差、过程曲线马上按新值算。也可以导出执行版打印，填好后再导回。`),
    drawSheet(data));
  draw(doc);
  return box;
}

// ---------- 配方：工单 ----------

export async function ordersView(state, library, record) {
  const {rows} = await api(`/lib/${enc(library)}/records?kind=${enc('工单')}`);
  const mine = rows.filter((row) => row['配方编号'] === record.name).sort((a, b) => String(b['计划日期'] || '').localeCompare(String(a['计划日期'] || '')));
  const total = h('input', {type: 'number', value: record.data['总量'] || 50, min: 0, step: 'any', style: 'width:110px'});
  const date = h('input', {type: 'date', value: new Date().toLocaleDateString('sv-SE', {timeZone: 'Asia/Shanghai'})});
  const selected = new Set();
  const exportLink = h('button.btn.small', {type: 'button', onclick: () => selected.size ? previewExport(`/api/orders/${enc(library)}/export?refs=${enc([...selected].join(','))}`, '称量单')
    : toast('先勾上要导出的工单')}, icon('download', 14), '导出所选称量单');
  return h('div.orders', {},
    h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '开一张工单')),
      h('div.row.wrap', {}, h('label.inline', {}, '总量', total, 'g'), h('label.inline', {}, '日期', date),
        h('button.btn.primary.small', {type: 'button', onclick: async () => {
          const result = await api(`/orders/${enc(library)}`, 'POST', {formula: record.id, total: Number(total.value), date: date.value});
          toast(`开好了 ${result.name}` + (result.warnings.length ? `（${result.warnings.length} 条提醒）` : '')); go('record', library, result.id);
        }}, icon('plus', 14), '按这个配方开单')),
      h('p.block-note', {}, '按配方现在的组成和原料库算称量计划，冻结在工单里（以后原料库改了，这张工单不变）。')),
    h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, `这个配方的工单（${mine.length}）`), exportLink),
      mine.length ? h('div.tablewrap.flat', {}, h('table.data.compact', {}, h('thead', {}, h('tr', {}, ['', '工单', '计划日期', '状态', '操作人'].map((name) => h('th', {}, name)))),
        h('tbody', {}, mine.map((row) => h('tr', {}, h('td', {}, h('input', {type: 'checkbox', onchange: (event) => { event.target.checked ? selected.add(row.id) : selected.delete(row.id); }})),
          h('td', {}, h('a', {href: `#/record/${enc(library)}/${enc(row.id)}`}, row.name)), h('td', {}, row['计划日期'] || ''), h('td', {}, row['状态'] || ''),
          h('td', {}, (row['实称信息'] || {}).operator || '')))))) : h('p.quiet', {}, '还没有工单。')));
}

// ---------- 工单：称量 ----------

export async function weighingView(state, library, record) {
  const full = await api(`/lib/${enc(library)}/records/${enc(record.id)}`);
  const table = full.series['称量'];
  if (!table) return h('p.quiet', {}, '这张工单没有称量表。');
  const at = Object.fromEntries(table.columns.map((name, index) => [name, index]));
  const changes = {};
  let timer = null;
  const status = h('span.quiet.small');
  const operator = h('input', {type: 'text', value: (record.data['实称信息'] || {}).operator || '', placeholder: '操作人', style: 'width:120px'});
  const save = async (extra = {}) => {
    const result = await api(`/orders/${enc(library)}/${enc(record.id)}`, 'PUT', {rows: {...changes}, operator: operator.value, ...extra});
    Object.keys(changes).forEach((key) => delete changes[key]);
    status.textContent = `已存 · ${result.filled} / ${result.lines} 行称好`;
    return result;
  };
  operator.addEventListener('change', () => save());
  const rows = table.rows.map((row) => {
    const plan = row[at['计划量']];
    const dev = h('td.num');
    const showDev = (value) => { const pct = typeof value === 'number' && plan ? (value - plan) / plan * 100 : null; dev.textContent = pct == null ? '' : `${pct >= 0 ? '+' : ''}${pct.toFixed(2)} %`; dev.className = 'num' + (pct != null && Math.abs(pct) > 1 ? ' over' : ''); };
    const actual = h('input.paper-input', {type: 'text', value: row[at['实称']] ?? '', placeholder: 'g', onchange: (event) => {
      const value = event.target.value.trim() === '' ? null : Number(event.target.value);
      changes[row[at['行']]] = {...(changes[row[at['行']]] || {}), 实称: value}; showDev(value); status.textContent = '正在存…'; clearTimeout(timer); timer = setTimeout(() => save(), 600);
    }});
    const review = h('input.paper-input', {type: 'text', value: row[at['复核']] ?? '', placeholder: '复核', onchange: (event) => {
      changes[row[at['行']]] = {...(changes[row[at['行']]] || {}), 复核: event.target.value}; clearTimeout(timer); timer = setTimeout(() => save(), 600);
    }});
    showDev(row[at['实称']]);
    return h('tr', {}, h('td', {}, row[at['来源编号']]), h('td', {}, row[at['名称']]), h('td.num', {}, number(plan, 3)), h('td.paper-in', {}, actual), dev, h('td.paper-in', {}, review));
  });
  return h('section.block.wide.weighing', {},
    h('header.block-head', {}, h('h3', {}, '称量记录'), status,
      h('button.btn.small', {type: 'button', onclick: () => previewExport(`/api/orders/${enc(library)}/export?refs=${enc(record.id)}&fmt=pdf`, '称量单 PDF')}, icon('download', 14), '打印称量单'),
      h('button.btn.small', {type: 'button', onclick: () => previewExport(`/api/orders/${enc(library)}/export?refs=${enc(record.id)}`, '称量单 Excel')}, icon('download', 14), 'Excel')),
    h('div.row.wrap', {}, h('label.inline', {}, '操作人', operator),
      h('button.btn.small', {type: 'button', onclick: async () => { await save({status: '已完成'}); toast('这张工单标成已完成'); }}, icon('check', 14), '称完了，标成已完成')),
    h('div.tablewrap.flat', {}, h('table.data.compact', {}, h('thead', {}, h('tr', {}, ['原料', '名称', '计划 / g', '实称 / g', '偏差', '复核'].map((name) => h('th', {}, name)))), h('tbody', {}, rows))),
    h('p.block-note', {}, '打印称量单拿去称，称完把实称填在这里（天平边有电脑也可以边称边填）。偏差超过 ±1 % 标红，填完自动存，实际组成和偏差马上按实称算。'));
}
