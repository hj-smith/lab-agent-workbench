// 公用的小工具：建 DOM、调接口、事件流、路由、格式、Markdown、图标。
export const $ = (selector, root = document) => root.querySelector(selector);


export function h(tag, attrs = {}, ...children) {
  const [head, ...classes] = tag.split('.');
  const [name, id] = head.split('#');
  const node = document.createElement(name || 'div');
  if (id) node.id = id;
  if (classes.length) node.className = classes.join(' ');
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'style' && typeof value === 'object') Object.assign(node.style, value);
    else if (key === 'class') node.className += (node.className ? ' ' : '') + value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// 和 h() 一个规矩：空值不画。不然 replaceChildren(…, 条件 ? 块 : null) 会在页面上印出 "null"
for (const name of ['replaceChildren', 'append', 'prepend']) {
  const original = Element.prototype[name];
  Element.prototype[name] = function (...nodes) { return original.apply(this, nodes.flat(Infinity).filter((node) => node != null && node !== false)); };
}

export const svg = (markup) => { const box = document.createElement('div'); box.innerHTML = markup.trim(); return box.firstChild; };

export async function api(path, method = 'GET', body) {
  const response = await fetch('/api' + path, {method, headers: body ? {'Content-Type': 'application/json'} : {}, body: body ? JSON.stringify(body) : undefined});
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && data.login) { location.href = '/login?next=' + encodeURIComponent(location.pathname); throw new Error('请先登录'); }   // 外网访问口：登录过期就回登录页
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
}

// 一条事件流，所有页面共用
const listeners = new Set();
let lastSeq = 0, source = null;
export function listen(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function connect(after) {
  lastSeq = after || lastSeq;
  source?.close();
  source = new EventSource(`/api/stream?after=${lastSeq}`);
  source.onmessage = (message) => { const event = JSON.parse(message.data); lastSeq = event.seq; listeners.forEach((fn) => fn(event)); };
}

// 路由：#/页面/参数
export function route() {
  const [page = '', ...rest] = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent);
  return {page: page || 'overview', args: rest};
}
export const go = (...parts) => { location.hash = '#/' + parts.map(encodeURIComponent).join('/'); };

// 格式
export const fmt = {
  num: (value, digits = 3) => value == null || value === '' ? '' : typeof value === 'number' ? (Math.abs(value) >= 1000 ? value.toFixed(0) : Number(value.toPrecision(digits + 1)).toString()) : String(value),
  size: (bytes) => bytes > 1048576 ? (bytes / 1048576).toFixed(1) + ' MB' : bytes > 1024 ? Math.round(bytes / 1024) + ' KB' : bytes + ' B',
  clock: (seconds) => new Date(seconds * 1000).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false}),
  today: () => new Date().toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false}),
  value: (value) => humanText(value),
};

// Markdown（够用的一小段：标题、列表、表格、代码、引用、粗体、行内代码、链接）
const esc = (text) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
function inline(text) {
  const codes = [];
  text = esc(text).replace(/`([^`]+)`/g, (_, code) => { codes.push(code); return `\u0001${codes.length - 1}\u0001`; });
  text = text.replace(/\[\[([^\]]+)\]\]/g, (_, name) => `<a class="ref" href="#/find/${encodeURIComponent(name)}">${name}</a>`)     // [[记录名]] → 去那条记录
    .replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|#[^\s)]*|\/[^\s)]*)\)/g, (_, label, href) => href.startsWith('#') ? `<a href="${href}">${label}</a>` : `<a href="${href}" target="_blank" rel="noopener">${label}</a>`);
  return text.replace(/\u0001(\d+)\u0001/g, (_, index) => `<code>${codes[index]}</code>`);
}
export function md(source) {
  const lines = String(source || '').replace(/\r/g, '').split('\n'), out = [];
  const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim());
  const isRule = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line || '');
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) { const body = []; i++; while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]); i++; out.push(`<pre><code>${esc(body.join('\n'))}</code></pre>`); continue; }
    if (!line.trim()) { i++; continue; }
    const head = line.match(/^(#{1,4})\s+(.*)$/);
    if (head) { out.push(`<h${head[1].length + 2}>${inline(head[2])}</h${head[1].length + 2}>`); i++; continue; }
    if (line.includes('|') && isRule(lines[i + 1])) {
      const header = cells(line), rows = []; i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(cells(lines[i++]));
      out.push('<div class="tablewrap"><table><thead><tr>' + header.map((cell) => `<th>${inline(cell)}</th>`).join('') + '</tr></thead><tbody>' +
        rows.map((row) => '<tr>' + header.map((_, k) => `<td>${inline(row[k] || '')}</td>`).join('') + '</tr>').join('') + '</tbody></table></div>');
      continue;
    }
    if (/^\s*>/.test(line)) { const body = []; while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, '')); out.push(`<blockquote>${md(body.join('\n'))}</blockquote>`); continue; }
    const item = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
    if (item.test(line)) {
      const tag = /\d/.test(line.match(item)[2]) ? 'ol' : 'ul', items = [];
      while (i < lines.length && item.test(lines[i])) items.push(lines[i++].match(item)[3]);
      out.push(`<${tag}>` + items.map((text) => `<li>${inline(text)}</li>`).join('') + `</${tag}>`);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^\s*(```|#{1,4}\s|>|([-*+]|\d+[.)])\s)/.test(lines[i]) && !(lines[i].includes('|') && isRule(lines[i + 1]))) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]);
    out.push(`<p>${para.map(inline).join('<br>')}</p>`);
  }
  return out.join('');
}

// 图标：一套线条、一种粗细
const paths = {
  overview: '<path d="M4 7h7l4 5h5"/><path d="M4 17h5l4-5"/><circle cx="11" cy="7" r="1.6"/><circle cx="15" cy="12" r="2.2"/><circle cx="9" cy="17" r="1.6"/>',
  projects: '<path d="M4 6h16M4 12h10M4 18h13"/><circle cx="20" cy="12" r="1.6"/>',
  experiments: '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3"/><path d="M7.5 14h9"/>',
  data: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 10h16M10 4v16"/>',
  knowledge: '<path d="M5 4h9a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h9"/>',
  outputs: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 14h6M10 17h4"/>',
  assistant: '<path d="M5 5h14v10H10l-4 4v-4H5z"/><path d="M9 10h.01M12 10h.01M15 10h.01"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
  me: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20c1.3-3.7 4-5.6 7-5.6s5.7 1.9 7 5.6"/>',
  users: '<circle cx="9" cy="9" r="3.2"/><path d="M3.5 19c1-3.2 3.2-4.8 5.5-4.8s4.5 1.6 5.5 4.8"/><path d="M15.5 6.2a3 3 0 0 1 0 5.6M17.5 14.6c1.6.6 2.6 2 3 4.4"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
  send: '<path d="M5 12h12M13 6l6 6-6 6"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="1.5"/>',
  attach: '<path d="M20 11.5l-8 8a5 5 0 0 1-7-7l8.5-8.5a3.5 3.5 0 0 1 5 5L10 17.5a2 2 0 0 1-2.8-2.8L15 7"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  collapse: '<path d="M9 6l6 6-6 6"/>',
  expand: '<path d="M15 6l-6 6 6 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  open: '<path d="M14 4h6v6M20 4l-9 9M18 14v5H5V6h5"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
  file: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  trash: '<path d="M5 7h14M10 7V5h4v2M7 7l1 13h8l1-13"/>',
  skill: '<path d="M12 3l2.4 5 5.6.8-4 3.9.9 5.6L12 15.6 7.1 18.3 8 12.7 4 8.8 9.6 8z"/>',
  tool: '<path d="M14.5 5.5a4 4 0 0 0-5 5L4 16l4 4 5.5-5.5a4 4 0 0 0 5-5l-2.5 2.5-2.5-.5-.5-2.5z"/>',
  plug: '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4"/>',
  rule: '<path d="M13 3L5 14h6l-1 7 8-11h-6z"/>',
  runs: '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4h4M12 8v4l3 2"/>',
  layers: '<path d="M12 4l8 4-8 4-8-4z"/><path d="M4 12l8 4 8-4M4 16l8 4 8-4"/>',
  edit: '<path d="M5 19h4L19 9l-4-4L5 15z"/><path d="M13 7l4 4"/>',
  upload: '<path d="M12 16V5M7 10l5-5 5 5M5 19h14"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.9-4M4 4v4h4M4 13a8 8 0 0 0 14.9 4M20 20v-4h-4"/>',
  download: '<path d="M12 5v11M7 11l5 5 5-5M5 19h14"/>',
  columns: '<rect x="4" y="5" width="16" height="14" rx="1.5"/><path d="M10 5v14M15 5v14"/>',
  chart: '<path d="M4 4v16h16"/><path d="M8 15l3-4 3 2 5-6"/>',
  compare: '<path d="M4 17c3-6 5-9 8-9s5 3 8 9"/><path d="M4 13c3-3 5-4 8-4s5 1 8 4" stroke-dasharray="2 2.5"/>',
  restore: '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4h4"/>',
  check: '<path d="M5 12l4.5 4.5L19 7"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6"/>',
  synthesis: '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3"/><path d="M7.5 14h9"/>',
  formulation: '<path d="M6 3h12M8 3v4a4 4 0 0 0 8 0V3"/><path d="M12 11v4M7 21h10l-1-6H8z"/>',
  testing: '<path d="M3 17l5-8 4 5 3-4 6 7"/><path d="M3 21h18"/>',
  desk: '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M7 20h10M12 16v4M7 12l3-3 3 2 4-4"/>',
  analysis: '<path d="M4 4v16h16"/><path d="M7 15c2-6 4-8 6-8s3 4 6 4" /><path d="M7 17c2-3 4-4 6-4s3 2 6 2" stroke-dasharray="2 2"/>',
};
export const icon = (name, size = 18) => svg(`<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || ''}</svg>`);

// 屏幕下方的一句提示，可带一个按钮（例：撤销）
export function toast(text, action, label = '撤销') {
  document.querySelector('.toast')?.remove();
  const node = h('div.toast', {role: 'status'}, h('span', {}, text),
    action ? h('button.btn.small', {type: 'button', onclick: () => { node.remove(); action(); }}, label) : null);
  document.body.append(node);
  setTimeout(() => node.remove(), action ? 8000 : 3500);
}

// 一条真实数据的小曲线
export function spark(points, {width = 96, height = 32, color = 'var(--line-blue)', kind = 'line'} = {}) {
  if (!points || points.length < 2) return h('span.spark-empty');
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const px = (x) => 2 + (x - x0) / ((x1 - x0) || 1) * (width - 4), py = (y) => height - 3 - (y - y0) / ((y1 - y0) || 1) * (height - 6);
  const body = kind === 'dots'
    ? points.map((p) => `<circle cx="${px(p[0]).toFixed(1)}" cy="${py(p[1]).toFixed(1)}" r="2.2" fill="${color}"/>`).join('')
    : `<polyline fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round" points="${points.map((p) => px(p[0]).toFixed(1) + ',' + py(p[1]).toFixed(1)).join(' ')}"/>`;
  return svg(`<svg class="spark" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img">${body}</svg>`);
}

// 程序内部用的字段（旧系统的 id、uid、文件指纹、原路径、冻结快照……）：存着，但不在人看的页面上出现
const INTERNAL = new Set(['现场照片', '设计', '实验原文', '冻结配方', '冻结计划', '冻结输出', '冻结设计指纹', '仪器表头', '基线锚点', '积分区间', '基线参数', '站点', '原路径', '指纹',
  '原文', '条件来源', '内容指纹', '分析版本', '基线算法版本', '复制自', '节点', '节点种类']);
export const internal = (key) => INTERNAL.has(key) || /(uid|UID|id|ID)$|^旧|sha256|指纹/.test(String(key));

// 程序用的数据（人看了也没用的）：英文字段名、版本 / 格式 / 方案 / 模板 / 系数这类、来源记号、一串数或坐标、键大多是英文的结构。
// 和 lab/present.py 的 technical() 同一套。存着，助手照样能读，人看的页面上不出现。
const CJK = /[一-鿿]/;
const TECH = /版本|格式$|方案$|模板$|锚点|系数$|拟合统计|设置$|快照|payload|原文$|实称信息|过期原因|^来源$|来源状态|条件来源|关联$/;
export function technical(key, value) {
  if (internal(key) || !CJK.test(key) || TECH.test(key)) return true;
  if (Array.isArray(value)) return value.some((item) => Array.isArray(item) || typeof item === 'number');
  if (value && typeof value === 'object') { const keys = Object.keys(value); return keys.filter((k) => !CJK.test(k)).length > keys.length / 2; }
  return false;
}
export const looksId = (text) => /^[0-9a-f]{8,}$|^[0-9a-f-]{20,}$|^(r|frm|res|gpc-sample|ell)-[0-9a-z_-]{6,}$/i.test(String(text));
export const empty = (value) => value == null || value === '' || (Array.isArray(value) && !value.length) || (typeof value === 'object' && !Array.isArray(value) && !Object.keys(value).length);

// 程序里的英文取值 → 人话（和 lab/present.py 的 WORDS 一样）
export const WORDS = {monoA: '单体A', monoB: '单体B', acid: '酸', catalyst: '催化剂', solvent: '溶剂', base: '碱', additive: '添加剂', reductant: '还原剂', quench: '终止剂', neutralizer: '中和剂', antisolvent: '沉淀剂', monomer: '单体', salt: '盐', reagent: '试剂', other: '其它', unknown: '未指定', before_solva: '浸泡前', after_solva: '浸泡后', before: '处理前', after: '处理后', pchip_anchors: 'PCHIP（连锚点）', linear_anchors: '多点直线', cubic_spline_anchors: '自然三次样条', pchip: 'PCHIP', linear: '直线', cubic_spline: '三次样条', natural_spline: '自然样条'};
const word = (text) => WORDS[text] || String(text).replace(/^detector_(\d+)$/, '检测器 $1');

// 一个值写成人话：是 / 否，时间只到分钟，列表用顿号，小结构写成"键：值"
export function humanText(value) {
  if (value == null) return '';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString('en-US') : String(+value.toPrecision(6));
  if (typeof value === 'string') return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value) ? value.slice(0, 16).replace('T', ' ') : word(value);
  if (Array.isArray(value)) return value.filter((item) => !looksId(item)).map(humanText).join('、');
  return Object.entries(value).filter(([key, item]) => !technical(key, item) && !empty(item)).map(([key, item]) => `${key}：${humanText(item)}`).join('；');
}
