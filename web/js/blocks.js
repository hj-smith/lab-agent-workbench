// 呈现块：服务器给一组块（数字卡、文字、表、曲线、步骤、关联记录、键值），这里画出来。
// 块的格式见 lab/present.py 开头。人可以隐藏、调顺序（存成这一类的版式），也可以让助手改这一类的呈现。
import {api, fmt, h, icon, md, spark, toast} from './core.js';

// 系列色按这个固定顺序用（过了色盲和对比度检查：陶土橙、蓝、土黄、紫、橄榄绿、玫红、暖灰、青）
export const COLORS = ['#c96442', '#3f7cc0', '#b5841c', '#8a63b8', '#5f8c3e', '#c25f8a', '#6b665c', '#3f8a86'];

let plotlyReady = null;
export function plotly() {
  plotlyReady ||= new Promise((resolve) => { const script = h('script', {src: '/web/vendor/plotly.min.js'}); script.onload = () => resolve(sizable(window.Plotly)); document.head.append(script); });
  return plotlyReady;
}

// 图框能拖高矮：每张图下边缘能往下拖高、往上拖矮，按"哪一页的哪张图"记住高度，双击回到原来的高度。所有图都经过这里，不用一张张改。
const remember = (key, value) => { try { if (value === undefined) return localStorage.getItem(key); if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch (error) { return null; } };
const chartKey = (node) => 'lab.chartH.' + (location.hash.split('/')[1] || '') + '|' + (node.closest('section, .block, .test-body, .ana')?.querySelector('h3, h2')?.textContent || node.className);
function sizable(P) {
  for (const name of ['newPlot', 'react']) {
    const original = P[name].bind(P);
    P[name] = (node, data, layout = {}, config) => {
      const key = chartKey(node), saved = Number(remember(key));
      const result = original(node, data, saved ? {...layout, height: saved} : layout, config);
      heightGrip(P, node, key, layout.height);
      return result;
    };
  }
  return P;
}
function heightGrip(P, node, key, original) {
  if (node.parentElement?.classList.contains('chart-wrap')) return;
  const wrap = h('div.chart-wrap');                 // 图和它下边的拖条包在一起，占图原来的位置（在两栏布局里也不会跑到别的格子去）
  node.replaceWith(wrap);
  wrap.append(node);
  const grip = h('div.chart-grip', {title: '往下拖变高，往上拖变矮；双击回到原来的高度', ondblclick: () => { remember(key, null); P.relayout(node, {height: original || 320}); }});
  grip.addEventListener('pointerdown', (event) => {
    event.preventDefault(); grip.setPointerCapture(event.pointerId); document.body.classList.add('resizing-v');
    const top = node.getBoundingClientRect().top;
    const move = (e) => P.relayout(node, {height: Math.round(Math.min(Math.max(e.clientY - top, 160), 1600))});
    const up = () => {
      grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up); document.body.classList.remove('resizing-v');
      remember(key, Math.round(node.getBoundingClientRect().height));
    };
    grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up);
  });
  wrap.append(grip);
}

export function number(value, digits) {
  if (typeof value !== 'number') return value ?? '';
  if (digits != null) return Math.abs(value) >= 1e5 && digits === 0 ? Math.round(value).toLocaleString('en-US') : value.toFixed(digits);
  return fmt.num(value, 4);
}

// ---------- 曲线 ----------

export async function chart(node, {series, x, y, height = 280, log_x: logX, legend, kind, stack, x_range: xRange}) {
  const Plotly = await plotly();
  const many = series.length > 1;
  Plotly.newPlot(node, series.map((line, index) => kind === 'bar' ? {x: line.x, y: line.y, name: line.name, type: 'bar', marker: {color: line.color || COLORS[index % COLORS.length]},
    hovertemplate: `${line.name ? line.name + '<br>' : ''}%{x}：%{y}<extra></extra>`} : ({
    x: line.x, y: line.y, name: line.name, type: 'scatter',
    mode: line.mode || (line.x.length > 60 ? 'lines' : 'lines+markers'),
    line: {color: line.color || COLORS[index % COLORS.length], width: 2, dash: line.dash || 'solid'}, marker: {size: 6},
    hovertemplate: `${line.name ? line.name + '<br>' : ''}${x || 'x'} %{x}<br>${y || 'y'} %{y}<extra></extra>`,
  })), {
    paper_bgcolor: '#fbfaf7', plot_bgcolor: '#fbfaf7', height, margin: {l: 62, r: 14, t: 10, b: many && legend !== false ? 70 : 46},
    showlegend: many && legend !== false, legend: {orientation: 'h', y: -0.28, font: {size: 11.5}},
    font: {family: 'Microsoft YaHei, sans-serif', size: 12, color: '#29261f'},
    xaxis: {title: {text: x || ''}, gridcolor: '#ebe6dc', zeroline: false, type: logX ? 'log' : kind === 'bar' ? 'category' : 'linear', ...(xRange ? {range: xRange, autorange: false} : {})},
    yaxis: {title: {text: y || ''}, gridcolor: '#ebe6dc', zeroline: false}, barmode: kind === 'bar' ? (stack ? 'stack' : 'group') : undefined,
  }, {displaylogo: false, responsive: true, modeBarButtonsToRemove: ['select2d', 'lasso2d']});
}

// ---------- 各种块 ----------

const recordHref = (library, id) => `#/record/${encodeURIComponent(library)}/${encodeURIComponent(id)}`;

// 状态和判定词自动上色
const TONES = {已完成: 'done', 完成: 'done', 做完: 'done', 成立: 'done', 达到: 'done', 更好: 'done', 容差内: 'done', 已确认: 'done', 通过: 'done', 是: 'done',
  设计中: 'open', 待配样: 'open', 待确认: 'open', 不确定: 'open', 暂定: 'open', 待定: 'open', 排队: 'open', 进行中: 'current', 跟踪中: 'current', 在做: 'current',
  超容差: 'off', 没跑成: 'off', 否定: 'off', 溶解: 'off', 历史导入: 'todo', 历史资料: 'todo', 计划: 'todo'};

function cell(value, library) {
  if (value && typeof value === 'object' && 'v' in value) return value.id ? h('a', {href: recordHref(library, value.id)}, String(value.v))
    : value.find ? h('a', {href: '#/find/' + encodeURIComponent(value.find)}, String(value.v)) : String(value.v ?? '');      // 只知道名字：按名字找过去
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'string' && TONES[value]) return h('span.st-tag.' + TONES[value], {}, value);
  return number(value);
}

const calm = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

// 数字从 0 滚到实际值（0.6 秒）
export function countUp(node, value, digits) {
  if (calm() || typeof value !== 'number' || !isFinite(value)) { node.textContent = number(value, digits); return node; }
  const start = performance.now(), dur = 600;
  const step = (now) => {
    const t = Math.min(1, (now - start) / dur), eased = 1 - Math.pow(1 - t, 3);
    node.textContent = t < 1 ? number(value * eased, digits ?? (Math.abs(value) >= 100 ? 0 : 2)) : number(value, digits);
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
  return node;
}

function statsBlock(block) {
  return h('div.stat-row', {}, block.items.map((item) => h('div.stat' + (item.tone ? '.' + item.tone : ''), {},
    h('div.stat-label', {}, item.label),
    h('div.stat-value', {}, typeof item.value === 'number' ? countUp(h('span'), item.value, item.digits) : String(item.value ?? '—'), item.unit ? h('small', {}, ' ' + item.unit) : null),
    item.note ? h('div.stat-note', {}, item.note) : null)));
}

function proseBlock(block) {
  return h('dl.prose', {}, block.items.map((item) => [h('dt', {}, item.label), h('dd' + (item.tone ? '.' + item.tone : ''), {}, item.text)]));
}

export function tableBlock(block, library, {limit = 30} = {}) {
  let sortAt = -1, dir = 1, all = false;
  const body = h('tbody');
  // 链接格子里的数、写成文字的数（"0.999212"）也算数：一样有数据条、按大小排
  const raw = (item) => { const v = item && typeof item === 'object' && 'v' in item ? item.v : item; return typeof v === 'string' && /^-?\d+(\.\d+)?(e-?\d+)?$/i.test(v.trim()) ? Number(v) : v; };
  const numeric = block.columns.map((_, index) => block.rows.some((row) => typeof raw(row[index]) === 'number'));
  const ranges = block.columns.map((_, index) => { const values = block.rows.map((row) => raw(row[index])).filter((v) => typeof v === 'number' && isFinite(v)); return values.length ? [Math.min(...values), Math.max(...values)] : [0, 0]; });
  const value = (row, index) => raw(row[index]);
  const head = h('tr', {}, block.columns.map((name, index) => h('th' + (numeric[index] ? '.num' : ''), {tabindex: 0, onclick: () => { dir = sortAt === index ? -dir : 1; sortAt = index; draw(); }}, name)));
  const more = h('button.btn.ghost.small', {type: 'button', onclick: () => { all = !all; draw(); }});
  const draw = () => {
    let rows = block.rows.slice();
    if (sortAt >= 0) rows.sort((a, b) => { const x = value(a, sortAt), y = value(b, sortAt); return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x ?? '').localeCompare(String(y ?? ''), 'zh')) * dir; });
    body.replaceChildren(...(all ? rows : rows.slice(0, limit)).map((row) => h('tr', {}, row.map((item, index) => {
      const value = raw(item);
      if (!numeric[index] || typeof value !== 'number') return h('td' + (numeric[index] ? '.num' : ''), {}, cell(item, library));
      const [lo, hi] = ranges[index];                    // 数字列：淡色数据条，一眼看出大小
      const p = hi > lo ? Math.round((value - Math.min(lo, 0)) / (hi - Math.min(lo, 0)) * 100) : 0;
      return h('td.num.databar', {style: `--p:${Math.max(0, Math.min(100, p))}%`}, cell(item, library));
    }))));
    head.querySelectorAll('th').forEach((th, index) => { th.dataset.sort = index === sortAt ? (dir > 0 ? 'asc' : 'desc') : ''; });
    more.hidden = block.rows.length <= limit;
    more.textContent = all ? '收起' : `显示全部 ${block.rows.length} 行`;
  };
  draw();
  return h('div', {}, h('div.tablewrap.flat', {}, h('table.data.compact', {}, h('thead', {}, head), body)), more);
}

function chartBlock(block) {
  const node = h('div.plot');
  requestAnimationFrame(() => chart(node, {...block, height: block.wide ? 320 : 260}));
  return node;
}

function stepsBlock(block) {
  return h('ol.steps', {}, block.items.map((item, index) => h('li', {},
    h('span.step-no', {}, index + 1),
    h('div.step-body', {},
      h('div.step-head', {}, h('b', {}, item.title), item.tag ? h('span.tag', {}, item.tag) : null, item.meta ? h('span.step-meta', {}, item.meta) : null),
      item.text ? h('p', {}, item.text) : null,
      item.chips?.length ? h('div.linked', {}, item.chips.map((chip) => h('span.mini-chip', {}, chip))) : null))));
}

function recordsBlock(block, library) {
  return h('div.mini-cards', {}, block.items.map((item, index) => h('a.mini-card', {href: recordHref(library, item.id)},
    h('div.mini-k', {}, item.kind), h('div.mini-name', {}, item.name),
    item.points?.length ? spark(item.points, {width: 150, height: 34, color: COLORS[index % 4], kind: item.kind === '膜' ? 'dots' : 'line'}) : null,
    item.text ? h('div.mini-text', {}, item.text) : null)));
}

function kvBlock(block, library) {
  return h('dl.kv', {}, block.items.map(([key, value]) => [h('dt', {}, key), h('dd', {}, cell(value, library))]));
}

// GPC 叠加：交给 overlay.js（数据按样品 id 现取）
function gpcOverlayBlock(block, library) {
  const box = h('div', {}, h('p.quiet.small', {}, '正在取色谱…'));
  import('./overlay.js').then(async ({gpcOverlay}) => box.replaceChildren(await gpcOverlay(null, library, block.ids, {title: block.title})));
  return box;
}

// 简报的块：页头一句话、markdown 文字、发现/建议（带记录小标签和"交给助手"）、等你拍板（能直接回答）
let taskHandler = null;
export const onTask = (fn) => { taskHandler = fn; };

function heroBlock(block) {
  return h('div.hero', {}, h('div.hero-text', {}, block.text || ''));         // 是哪天的什么简报，页头已经写了
}

function textBlock(block) {
  return h('div.md.text-block', {html: md(block.text || '')});
}

function itemsBlock(block, library) {
  return h('div.brief-items', {}, (block.items || []).map((item) => h('div.brief-item.' + (item.tone || 'plain'), {},
    h('div.bi-title', {}, item.title || ''),
    item.text ? h('div.bi-text.md', {html: md(item.text)}) : null,
    (item.refs || []).length || item.task ? h('div.bi-foot', {},
      (item.refs || []).map((name) => h('a.ref-chip', {href: '#/find/' + encodeURIComponent(name)}, name)),
      item.task ? h('button.btn.small', {type: 'button', title: item.task, onclick: () => { taskHandler?.(item.task, item.title); }}, icon('spark', 13), '交给助手') : null) : null)));
}

function decideBlock(block) {
  return h('div.brief-decide', {}, (block.items || []).map((item) => cardBox(item)));
}

// "关于"哪些记录：小标签能点；多于 4 个先摆 3 个，其余点"+N"展开
export function refChips(names) {
  const chip = (name) => h('a.ref-chip', {href: '#/find/' + encodeURIComponent(name)}, name);
  const box = h('div.decision-refs', {}, h('span', {}, '关于'), (names.length > 4 ? names.slice(0, 3) : names).map(chip));
  if (names.length > 4) {
    const more = h('button.ref-more', {type: 'button', onclick: () => more.replaceWith(...names.slice(3).map(chip))}, `+${names.length - 3}`);
    box.append(more);
  }
  return box;
}

// 一张等他拍板的卡片：问题、情况、依据（记录能点）、关于哪些记录、几处对话在等、要填的、选项；"细看"打开那段对话，"不用问了"收起来并记住。
// item = {lane, title（对话名）, card, waiting}；answer 不给就直接发回答。总览、简报、对话里都用它。
export function cardBox(item, {answer, open, free = false} = {}) {
  const card = item.card || {}, values = {};
  const send = answer || (async (text) => { await api(`/lanes/${encodeURIComponent(item.lane)}/answer`, 'POST', {text, values: Object.keys(values).length ? values : null}); toast('已回答：' + text); });
  const reply = async (text) => { box.classList.add('sent'); await send(text, values); };
  const fields = (card.fields || []).map((field) => {
    const input = h('input', {type: field.type === 'number' || field.type === 'confidence' ? 'number' : field.type === 'date' ? 'date' : 'text',
      min: field.type === 'confidence' ? 0 : null, max: field.type === 'confidence' ? 100 : null, placeholder: field.unit || '', oninput: () => { values[field.label || field.key] = input.value; }});
    return h('label.field', {}, h('span', {}, (field.label || field.key) + (field.type === 'confidence' ? '（把握 %）' : field.unit ? `（${field.unit}）` : '')), input);
  });
  const freeInput = free ? h('input.free', {type: 'text', placeholder: '或者直接写你的回答', onkeydown: (event) => { if (event.key === 'Enter' && freeInput.value.trim()) reply(freeInput.value.trim()); }}) : null;
  const box = h('div.decision', {'data-card': card.id},
    h('div.decision-q', {}, card.title || item.title),
    card.text ? h('div.decision-t', {}, card.text) : null,
    card.evidence?.length ? h('ul.decision-ev', {}, card.evidence.map((line) => h('li.md', {html: md(line)}))) : null,
    card.refs?.length ? refChips(card.refs) : null,
    item.waiting ? h('div.decision-wait', {}, `另有 ${item.waiting} 段对话在等同一件事，答一次都会收到`) : null,
    fields.length ? h('div.fields', {}, fields) : null,
    h('div.decision-actions', {}, (card.options || []).slice(0, 4).map((option, index) => h('button.btn' + (index ? '' : '.primary'), {type: 'button', onclick: () => reply(option)}, option))),
    freeInput,
    h('div.decision-foot', {},
      open !== false ? h('button.btn.ghost.small', {type: 'button', onclick: () => (open ? open(item.lane) : location.assign('#/assistant/' + encodeURIComponent(item.lane)))}, '细看') : null,
      h('button.btn.ghost.small', {type: 'button', title: '收起来，记进"定过的事"，助手以后不再问这件事', onclick: async () => {
        box.classList.add('sent'); await api(`/lanes/${encodeURIComponent(item.lane)}/close`, 'POST', {}); toast('收起来了，记进了"定过的事"');
      }}, '不用问了')));
  return box;
}

const DRAW = {hero: heroBlock, text: textBlock, items: itemsBlock, decide: decideBlock, stats: statsBlock, prose: proseBlock, table: tableBlock, chart: chartBlock, steps: stepsBlock, records: recordsBlock, kv: kvBlock, gpc_overlay: gpcOverlayBlock};

// 对话里的展示：同一套块，不带调版式的按钮
export function showBlocks(blocks, library, title) {
  return h('div.shown', {}, title ? h('div.shown-title', {}, title) : null, h('div.blocks.in-chat', {}, blocks.map((block) => {
    const wide = block.wide || block.type === 'stats' || block.type === 'gpc_overlay' || (block.type === 'table' && (block.columns || []).length > 4);
    return h('section.block' + (wide ? '.wide' : '') + '.b-' + block.type, {},
      block.title ? h('header.block-head', {}, h('h3', {}, block.title)) : null,
      (DRAW[block.type] || kvBlock)(block, library), block.note ? h('p.block-note', {}, block.note) : null);
  })));
}

// ---------- 一组块 + 调整版式 ----------

export function renderBlocks(view, {library, kind, onAsk} = {}) {
  const grid = h('div.blocks');
  let editing = false;
  const order = view.blocks.map((block) => block.key);
  const hidden = new Set(view.hidden || []);
  const save = async () => {
    await api('/present-layout/' + encodeURIComponent(kind), 'PUT', {order, hidden: [...hidden]});
    toast(`「${kind}」的版式已保存，这一类的记录都按这个显示`);
  };
  const draw = () => {
    grid.classList.toggle('editing', editing);
    grid.replaceChildren(...order.map((key) => {
      const block = view.blocks.find((item) => item.key === key);
      if (!block || (hidden.has(key) && !editing)) return null;
      const body = (DRAW[block.type] || kvBlock)(block, library);
      const tools = editing ? h('div.block-tools', {},
        h('button.btn.ghost.small', {type: 'button', title: '往前', onclick: () => { const at = order.indexOf(key); if (at > 0) { order.splice(at, 1); order.splice(at - 1, 0, key); draw(); save(); } }}, '↑'),
        h('button.btn.ghost.small', {type: 'button', title: '往后', onclick: () => { const at = order.indexOf(key); if (at < order.length - 1) { order.splice(at, 1); order.splice(at + 1, 0, key); draw(); save(); } }}, '↓'),
        h('button.btn.ghost.small', {type: 'button', onclick: () => { hidden.has(key) ? hidden.delete(key) : hidden.add(key); draw(); save(); }}, hidden.has(key) ? '显示' : '隐藏')) : null;
      const wide = block.wide || block.type === 'stats' || block.type === 'gpc_overlay' || (block.type === 'table' && block.columns.length > 5);
      return h('section.block' + (wide ? '.wide' : '') + (hidden.has(key) ? '.off' : '') + '.b-' + block.type, {},
        block.title || tools ? h('header.block-head', {}, h('h3', {}, block.title || ''), tools) : null,
        body, block.note ? h('p.block-note', {}, block.note) : null);
    }));
  };
  draw();
  const bar = h('div.view-bar', {},
    h('span.quiet', {}, `呈现：${view.source}`),
    h('button.btn.ghost.small', {type: 'button', onclick: (event) => { editing = !editing; event.currentTarget.textContent = editing ? '调好了' : '调整版式'; draw(); }}, '调整版式'),
    onAsk ? h('button.btn.ghost.small', {type: 'button', onclick: onAsk}, icon('spark', 14), '让助手改进这一类的呈现') : null);
  return h('div.view', {}, grid, bar);
}
