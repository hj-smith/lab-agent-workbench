// 一类记录的工作区：卡片（真曲线缩略）/ 表格（选列、双击改），选中后对比曲线、导出、让助手看、放回收站；
// 作图（任意两列数）、新建、导入 Excel/CSV（先预览再对列）、导出、回收站。
import {previewExport} from './viewer.js';
import {api, fmt, go, h, icon, internal, spark, toast, WORDS} from './core.js';
import {COLORS, chart, number} from './blocks.js';

const HIDDEN = {has: internal};
const FILLABLE = ['树脂物料', '原料', '预混液'];
// 旧程序里存的英文类别词 → 中文（存的不变）
const CARD_KINDS = new Set(['GPC样品', 'NK谱', '老化谱', '膜', '配方', '工单', '树脂实验']);
const keep = (key, value) => { try { if (value === undefined) return JSON.parse(localStorage.getItem(key) || 'null'); localStorage.setItem(key, JSON.stringify(value)); } catch (error) { return null; } };
const enc = encodeURIComponent;

export async function kindView(state, kindName, {ask} = {}) {
  const library = state.library;
  const [{kinds}, {rows}] = await Promise.all([api(`/lib/${enc(library)}/kinds`), api(`/lib/${enc(library)}/records?kind=${enc(kindName)}`)]);
  const kind = kinds.find((item) => item.name === kindName) || {name: kindName, fields: [], about: ''};
  const usable = (kind.fields || []).map((field) => field.key).filter((key) => !HIDDEN.has(key) && rows.some((row) => row[key] != null && typeof row[key] !== 'object'));
  const units = Object.fromEntries((kind.fields || []).map((field) => [field.key, field.unit]));
  const numeric = usable.filter((key) => rows.some((row) => typeof row[key] === 'number'));
  const view = {mode: keep('lab.mode.' + kindName) || (CARD_KINDS.has(kindName) ? 'cards' : 'table'), filter: '', columns: keep('lab.cols.' + kindName) || usable.slice(0, 8),
    selected: new Set(), sortAt: null, dir: 1, recycle: false};
  let gallery = null;
  const root = h('div.kind-view');
  const tools = h('div.kv-tools'), selBar = h('div.sel-bar'), extra = h('div.kv-extra'), body = h('div.kv-body');
  root.append(tools, selBar, extra, body);

  const shown = () => rows.filter((row) => !view.filter || [row.name, ...view.columns.map((key) => row[key])].some((value) => fmt.value(value).toLowerCase().includes(view.filter)));

  // ----- 工具条 -----
  const search = h('input', {type: 'search', placeholder: '筛选名字和字段', value: view.filter, oninput: () => { view.filter = search.value.trim().toLowerCase(); draw(); }});
  const count = h('span.quiet');
  const modeButton = (mode, label, name) => h('button.seg' + (view.mode === mode ? '.on' : ''), {type: 'button', onclick: () => { view.mode = mode; keep('lab.mode.' + kindName, mode); drawTools(); draw(); }}, icon(name, 15), label);
  const drawTools = () => tools.replaceChildren(
    h('label.search.small', {}, icon('search', 14), search), count,
    h('div.segs', {}, modeButton('cards', '卡片', 'data'), modeButton('table', '表格', 'columns')),
    h('div.kv-actions', {},
      view.mode === 'table' ? h('button.btn.small', {type: 'button', onclick: () => columnPicker()}, icon('columns', 14), '选列') : null,
      numeric.length >= 2 ? h('button.btn.small', {type: 'button', onclick: () => scatter()}, icon('chart', 14), '作图') : null,
      h('button.btn.small', {type: 'button', onclick: () => newRecord()}, icon('plus', 14), '新建'),
      h('button.btn.small', {type: 'button', onclick: () => importFile()}, icon('upload', 14), '导入'),
      h('button.btn.small', {type: 'button', onclick: () => previewExport(`/api/lib/${enc(library)}/export?kind=${enc(kindName)}`, kindName + ' Excel')}, icon('download', 14), '导出 Excel'),
      h('button.btn.small.ghost', {type: 'button', onclick: () => recycle()}, icon('trash', 14), '回收站')),
      FILLABLE.includes(kindName) && blanks() ? h('button.btn.small.primary', {type: 'button', title: '派给"文献调研"成员在后台查：只补空着的格子，每个数标待确认、写出处', onclick: fillIn},
        icon('spark', 14), `让助手联网补全（${blanks()} 格空着）`) : null);
  drawTools();

  // ----- 空着的格子让助手联网补（物性、CAS、全名……；纯度、批号这类要看检验报告的不碰） -----
  const fillable = () => usable.filter((key) => !['纯度', '溶液浓度', '溶剂', '已归档', '停用', '批号'].includes(key));   // 溶剂是配好的溶液才有的
  const blanks = () => rows.reduce((sum, row) => sum + fillable().filter((key) => row[key] == null || row[key] === '').length, 0);
  const fillIn = async () => {
    const missing = rows.map((row) => [row.name, fillable().filter((key) => row[key] == null || row[key] === '')]).filter(([, keys]) => keys.length);
    const text = [`把「${library}」库「${kindName}」里空着的信息联网查清补上（${missing.length} 条，${blanks()} 格）。`,
      '规矩：只补空着的格子，已有的不改；查 PubChem、供应商技术资料（Sigma-Aldrich、TCI 等）这类可靠来源；每补一个数，把字段名放进 unconfirmed（页面显示"待确认"），并在这条记录的备注里写上来源链接；'
      + '查不到或拿不准的留空，不猜；纯度、批号这类要看检验报告的不碰。做完交回一张表：每条记录补了哪些、数值、来源。',
      '要补的：', ...missing.map(([name, keys]) => `- ${name}：${keys.join('、')}`)].join('\n');
    const {lane} = await api('/team/research/task', 'POST', {text, library, anchor: `${kindName}（补全空着的信息）`});
    toast('派给"文献调研"了，在后台查；做完通知你'); location.hash = '#/assistant/' + enc(lane);
  };

  // ----- 选中 -----
  const drawSel = () => {
    const ids = [...view.selected];
    selBar.hidden = !ids.length;
    selBar.replaceChildren(h('b', {}, `已选 ${ids.length} 条`),
      h('button.btn.small.primary', {type: 'button', onclick: () => compare(ids)}, icon('compare', 14), '对比曲线'),
      h('button.btn.small', {type: 'button', onclick: () => previewExport(`/api/lib/${enc(library)}/export?kind=${enc(kindName)}&ids=${enc(ids.join(','))}&series=true`, kindName + ' Excel')}, icon('download', 14), '导出所选（含曲线）'),
      h('button.btn.small', {type: 'button', onclick: () => ask?.(`看一下这几条${kindName}：${rows.filter((row) => view.selected.has(row.id)).map((row) => row.name).join('、')}。比一比，有什么差别和值得注意的？`)}, icon('spark', 14), '让助手看所选'),
      h('button.btn.small.ghost', {type: 'button', onclick: () => trash(ids)}, icon('trash', 14), '放进回收站'),
      h('button.btn.small.ghost', {type: 'button', onclick: () => { view.selected.clear(); drawSel(); draw(); }}, '取消选择'));
  };
  const toggle = (id, on) => { on ? view.selected.add(id) : view.selected.delete(id); drawSel(); };

  // ----- 主体 -----
  async function draw() {
    const list = shown();
    count.textContent = `${list.length} / ${rows.length} 条`;
    if (view.mode === 'cards') {
      if (!gallery) { body.replaceChildren(h('p.quiet', {}, '正在画缩略图…')); gallery = Object.fromEntries((await api(`/gallery/${enc(library)}?kind=${enc(kindName)}`)).rows.map((item) => [item.id, item])); }
      body.replaceChildren(h('div.cards', {}, list.slice(0, 400).map((row, index) => {
        const item = gallery[row.id] || {};
        const box = h('input', {type: 'checkbox', checked: view.selected.has(row.id), 'aria-label': '选中', onclick: (event) => event.stopPropagation(), onchange: (event) => toggle(row.id, event.target.checked)});
        return h('a.card', {href: `#/record/${enc(library)}/${enc(row.id)}`},
          h('label.card-check', {onclick: (event) => event.stopPropagation()}, box),
          h('div.card-name', {}, row.name),
          item.points?.length ? spark(item.points, {width: 220, height: 54, color: COLORS[index % 4], kind: kindName === '膜' ? 'dots' : 'line'}) : h('div.card-nocurve'),
          h('div.card-text', {}, item.text || view.columns.slice(0, 3).map((key) => row[key] != null ? `${key} ${typeof row[key] === 'number' ? number(row[key]) : row[key]}` : '').filter(Boolean).join(' · ')),
          item.axis ? h('div.card-axis', {}, item.axis) : null);
      })), list.length > 400 ? h('p.quiet', {}, `只画了前 400 条，用筛选缩小范围。`) : null);
      return;
    }
    let sorted = list.slice();
    if (view.sortAt) sorted.sort((a, b) => { const x = view.sortAt === '名字' ? a.name : a[view.sortAt], y = view.sortAt === '名字' ? b.name : b[view.sortAt];
      return (typeof x === 'number' && typeof y === 'number' ? x - y : fmt.value(x).localeCompare(fmt.value(y), 'zh')) * view.dir; });
    const all = h('input', {type: 'checkbox', 'aria-label': '全选', onchange: (event) => { sorted.forEach((row) => event.target.checked ? view.selected.add(row.id) : view.selected.delete(row.id)); drawSel(); draw(); }});
    const th = (label, key) => h('th', {'data-sort': view.sortAt === key ? (view.dir > 0 ? 'asc' : 'desc') : '', onclick: () => { view.dir = view.sortAt === key ? -view.dir : 1; view.sortAt = key; draw(); }}, label);
    body.replaceChildren(h('div.tablewrap', {}, h('table.data.grid', {},
      h('thead', {}, h('tr', {}, h('th.check', {}, all), th('名字', '名字'), view.columns.map((key) => th(key + (units[key] ? ` / ${units[key]}` : ''), key)))),
      h('tbody', {}, sorted.slice(0, 1000).map((row) => h('tr', {class: view.selected.has(row.id) ? 'sel' : null},
        h('td.check', {}, h('input', {type: 'checkbox', checked: view.selected.has(row.id), onchange: (event) => { toggle(row.id, event.target.checked); event.target.closest('tr').classList.toggle('sel', event.target.checked); }})),
        h('td.name', {}, h('a', {href: `#/record/${enc(library)}/${enc(row.id)}`}, row.name)),
        view.columns.map((key) => editableCell(row, key))))))),
      sorted.length > 1000 ? h('p.quiet', {}, '只显示前 1000 条，用筛选缩小范围。') : null);
  }

  // 双击就地改，回车或点别处存
  function editableCell(row, key) {
    const value = row[key];
    const unsure = (row.unconfirmed || []).includes(key);
    const td = h('td' + (typeof value === 'number' ? '.num' : '') + (unsure ? '.unsure-cell' : ''), {title: unsure ? '待确认' : '双击修改'},
      typeof value === 'number' ? number(value) : value && typeof value === 'object' ? (Array.isArray(value) ? value.join('、') : '…') : WORDS[value] || fmt.value(value));
    td.addEventListener('dblclick', () => {
      if (value && typeof value === 'object') return;
      const input = h('input.cell-edit', {type: 'text', value: value ?? ''});
      td.replaceChildren(input); input.focus(); input.select();
      const done = async (commit) => {
        if (!commit) { draw(); return; }
        const text = input.value.trim();
        const next = text === '' ? null : typeof value === 'number' || /^[-+]?\d*\.?\d+(e[-+]?\d+)?$/i.test(text) ? Number(text) : text;
        if (next !== value) {
          await api(`/lib/${enc(library)}/records`, 'POST', {kind: kindName, rows: [{id: row.id, [key]: next}]});
          const old = value; row[key] = next;
          toast(`${row.name} · ${key} 改成 ${next ?? '空'}`, async () => { await api(`/lib/${enc(library)}/records`, 'POST', {kind: kindName, rows: [{id: row.id, [key]: old}]}); row[key] = old; draw(); });
        }
        draw();
      };
      input.addEventListener('keydown', (event) => { if (event.key === 'Enter') done(true); if (event.key === 'Escape') done(false); });
      input.addEventListener('blur', () => done(true), {once: true});
    });
    return td;
  }

  // ----- 选列 -----
  function columnPicker() {
    const boxes = usable.map((key) => h('label.col-pick', {}, h('input', {type: 'checkbox', checked: view.columns.includes(key), onchange: (event) => {
      view.columns = event.target.checked ? [...view.columns, key] : view.columns.filter((item) => item !== key);
      view.columns = usable.filter((item) => view.columns.includes(item)); keep('lab.cols.' + kindName, view.columns); draw();
    }}), key));
    panel('选列', h('div.col-picks', {}, boxes), h('button.btn.small.ghost', {type: 'button', onclick: () => { view.columns = usable.slice(0, 8); keep('lab.cols.' + kindName, null); extra.replaceChildren(); draw(); }}, '恢复默认'));
  }

  function panel(title, ...children) {
    extra.replaceChildren(h('section.block.kv-panel', {}, h('header.block-head', {}, h('h3', {}, title), h('button.btn.ghost.small', {type: 'button', onclick: () => extra.replaceChildren()}, icon('close', 14))), ...children));
  }

  // ----- 作图：任意两列 -----
  function scatter() {
    const pick = (label, at) => h('label.inline', {}, label, h('select', {onchange: drawPlot}, numeric.map((key, index) => h('option', {value: key, selected: index === at}, key))));
    const x = pick('横轴', 0), y = pick('纵轴', 1);
    const plot = h('div.plot');
    async function drawPlot() {
      const xk = x.querySelector('select').value, yk = y.querySelector('select').value;
      const list = shown().filter((row) => typeof row[xk] === 'number' && typeof row[yk] === 'number');
      await chart(plot, {x: xk + (units[xk] ? ` / ${units[xk]}` : ''), y: yk + (units[yk] ? ` / ${units[yk]}` : ''), height: 340,
        series: [{name: kindName, x: list.map((row) => row[xk]), y: list.map((row) => row[yk]), mode: 'markers', text: list.map((row) => row.name)}]});
      plot.removeAllListeners?.('plotly_click');
      plot.on?.('plotly_click', (event) => { const row = list[event.points[0].pointIndex]; if (row) go('record', library, row.id); });
    }
    panel('作图（点一个点打开那条记录）', h('div.row', {}, x, y), plot);
    requestAnimationFrame(drawPlot);
  }

  // ----- 对比曲线 -----
  async function compare(ids) {
    const items = await Promise.all(ids.slice(0, 12).map((id) => api(`/lib/${enc(library)}/records/${enc(id)}`)));
    const curves = {};
    for (const item of items) {
      for (const [name, table] of Object.entries(item.series || {})) {
        const cols = table.columns, numericCols = cols.map((_, index) => table.rows.length > 2 && table.rows.slice(0, 50).every((row) => row[index] == null || typeof row[index] === 'number'));
        const xi = numericCols.indexOf(true);
        cols.forEach((col, index) => { if (numericCols[index] && index !== xi && xi >= 0) (curves[`${name} · ${col}`] ||= {x: cols[xi], lines: []}).lines.push({name: item.name, x: table.rows.map((row) => row[xi]), y: table.rows.map((row) => row[index])}); });
      }
      for (const [name, result] of Object.entries(item.results || {})) {
        const value = result.value;
        if (!value || typeof value !== 'object' || Array.isArray(value)) continue;
        const arrays = Object.entries(value).filter(([, arr]) => Array.isArray(arr) && arr.length >= 10 && arr.every((v) => v == null || typeof v === 'number'));
        if (arrays.length < 2) continue;
        const [xName, x] = arrays.find(([key]) => key === 'logM') || arrays[0];
        arrays.filter(([key]) => key !== xName && key !== 'M').forEach(([key, y]) => (curves[`${name} · ${key}`] ||= {x: xName, lines: []}).lines.push({name: item.name, x, y}));
      }
    }
    const names = Object.keys(curves).sort((a, b) => curves[b].lines.length - curves[a].lines.length);
    if (!names.length) { toast('所选记录没有能叠在一起的曲线'); return; }
    const plot = h('div.plot');
    const select = h('select', {onchange: () => drawIt()}, names.map((name) => h('option', {value: name}, `${name}（${curves[name].lines.length} 条）`)));
    const drawIt = () => { const curve = curves[select.value]; chart(plot, {x: curve.x, y: select.value.split(' · ').pop(), height: 380, series: curve.lines}); };
    panel(`对比 ${items.length} 条`, h('label.inline', {}, '曲线', select), plot);
    requestAnimationFrame(drawIt);
  }

  // ----- 新建 -----
  function newRecord() {
    const values = {};
    const inputs = [h('label.form-row', {}, h('span', {}, '名字'), h('input', {type: 'text', oninput: (event) => { values.name = event.target.value.trim(); }}))].concat(
      usable.slice(0, 16).map((key) => h('label.form-row', {}, h('span', {}, key + (units[key] ? ` / ${units[key]}` : '')), h('input', {type: 'text', oninput: (event) => {
        const text = event.target.value.trim(); values[key] = text === '' ? undefined : /^[-+]?\d*\.?\d+(e[-+]?\d+)?$/i.test(text) ? Number(text) : text;
      }}))));
    panel(`新建一条${kindName}`, h('div.form', {}, inputs),
      h('div.row', {}, h('button.btn.primary.small', {type: 'button', onclick: async () => {
        if (!values.name) { toast('先写个名字'); return; }
        const row = Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined));
        const {saved} = await api(`/lib/${enc(library)}/records`, 'POST', {kind: kindName, rows: [row]});
        go('record', library, saved[0].id);
      }}, '建好，打开它'), h('span.quiet', {}, '别的字段建好后在记录页里加。')));
  }

  // ----- 导入 -----
  function importFile() {
    const picker = h('input', {type: 'file', accept: '.xlsx,.xlsm,.csv,.txt,.tsv', hidden: true, onchange: async () => {
      const file = picker.files[0]; if (!file) return;
      const response = await fetch(`/api/lib/${enc(library)}/import/${enc(file.name)}`, {method: 'PUT', body: file});
      const preview = await response.json();
      if (!response.ok) { toast('读不了这个文件：' + (preview.detail || response.statusText)); return; }
      mapping(preview, file.name);
    }});
    document.body.append(picker); picker.click(); setTimeout(() => picker.remove(), 60000);
  }

  function mapping(preview, fileName) {
    const state_ = {kind: kindName, sheet: preview.sheet, name: preview.name_column, map: {...preview.mapping}};
    const fieldList = h('datalist', {id: 'imp-fields'}, usable.map((key) => h('option', {value: key})));
    const draw_ = () => panel(`导入 ${fileName}（${preview.total} 行，表头在第 ${preview.header_row} 行）`, fieldList,
      h('div.row.wrap', {},
        preview.sheets.length > 1 ? h('label.inline', {}, '工作表', h('select', {onchange: async (event) => { Object.assign(preview, await api(`/lib/${enc(library)}/import/${preview.token}/inspect`, 'POST', {sheet: event.target.value, kind: state_.kind})); state_.sheet = preview.sheet; state_.name = preview.name_column; state_.map = {...preview.mapping}; draw_(); }},
          preview.sheets.map((sheet) => h('option', {selected: sheet === preview.sheet}, sheet)))) : null,
        h('label.inline', {}, '存成哪一类', h('input', {type: 'text', value: state_.kind, list: 'imp-kinds', oninput: (event) => { state_.kind = event.target.value.trim(); }}),
          h('datalist', {id: 'imp-kinds'}, preview.kinds.map((name) => h('option', {value: name})))),
        h('label.inline', {}, '名字在哪一列', h('select', {onchange: (event) => { state_.name = event.target.value; }}, preview.columns.map((column) => h('option', {selected: column === state_.name}, column))))),
      h('div.tablewrap.flat', {}, h('table.data.compact', {},
        h('thead', {}, h('tr', {}, preview.columns.map((column) => h('th', {}, column))),
          h('tr.map-row', {}, preview.columns.map((column) => h('th', {}, column === state_.name ? h('span.quiet', {}, '（名字）') :
            h('input', {type: 'text', list: 'imp-fields', value: state_.map[column] ?? '', placeholder: '不要这列', oninput: (event) => { state_.map[column] = event.target.value.trim(); }}))))),
        h('tbody', {}, preview.rows.map((row) => h('tr', {}, row.map((value) => h('td', {}, typeof value === 'number' ? number(value) : fmt.value(value)))))))),
      h('p.block-note', {}, '第二行是"存成哪个字段"：已有的字段会自动对上；写个新名字就是新字段；清空就不要这一列。同名的记录只更新给出的字段，原文件挂到每条记录上。'),
      h('div.row', {}, h('button.btn.primary.small', {type: 'button', onclick: async () => {
        const result = await api(`/lib/${enc(library)}/import/${preview.token}`, 'POST', {kind: state_.kind, sheet: state_.sheet, name_column: state_.name, mapping: state_.map});
        toast(`导入了 ${result.saved} 条（新建 ${result.created}）` + (result.skipped ? `，${result.skipped} 行没名字跳过了` : ''));
        if (state_.kind === kindName) { const fresh = await api(`/lib/${enc(library)}/records?kind=${enc(kindName)}`); rows.splice(0, rows.length, ...fresh.rows); gallery = null; extra.replaceChildren(); draw(); }
        else go('data', state_.kind);
      }}, `导入 ${preview.total} 行`)));
    draw_();
  }

  // ----- 回收站 -----
  async function trash(ids) {
    for (const id of ids) await api(`/lib/${enc(library)}/records/${enc(id)}`, 'DELETE');
    const gone = rows.filter((row) => ids.includes(row.id));
    rows.splice(0, rows.length, ...rows.filter((row) => !ids.includes(row.id)));
    view.selected.clear(); drawSel(); draw();
    toast(`${gone.length} 条放进回收站了`, async () => { for (const row of gone) await api(`/lib/${enc(library)}/records/${enc(row.id)}/restore`, 'POST'); rows.push(...gone); draw(); });
  }

  async function recycle() {
    const {rows: gone} = await api(`/lib/${enc(library)}/records?kind=${enc(kindName)}&deleted=true`);
    panel(`回收站（${gone.length}）`, gone.length ? h('ul.links', {}, gone.map((row) => h('li', {}, row.name, ' ', h('button.btn.small', {type: 'button', onclick: async (event) => {
      await api(`/lib/${enc(library)}/records/${enc(row.id)}/restore`, 'POST'); event.target.closest('li').remove(); rows.push(row); draw(); toast(`「${row.name}」拿回来了`);
    }}, icon('restore', 13), '拿回来')))) : h('p.quiet', {}, '回收站是空的。'));
  }

  drawSel();
  await draw();
  return {node: root, kind, total: rows.length};
}
