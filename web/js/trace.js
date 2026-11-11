// 产品穿透：树脂合成 → 配方与工单 → 涂膜与测量，一张从上游到下游的图。
// 点一格就以它为起点重新看；悬停时这一条谱系亮起来；虚线框是库里找不到的一端。
import {api, h, icon} from './core.js';
import {number} from './blocks.js';

const DIGITS = {产率: 1, '末点 Mw': 0, PDI: 2, 总量: 1, 固含: 1, 膜厚: 1, 'SolvA 损失': 2, n193: 4, k193: 4, A500: 3};
const TONE = {树脂实验: 'resin', 配方: 'formula', 膜: 'film', 'n/k': 'nk', 老化: 'aging'};

function nodeCard(library, node) {
  const target = `#/record/${encodeURIComponent(library)}/${encodeURIComponent(node.id)}`;
  const metrics = node.metrics.filter(([, value]) => value != null && value !== '');
  const restart = () => { if (!node.missing) location.hash = target.slice(1) + '/trace'; };
  return h('div.trace-node.' + (TONE[node.kind] || 'other') + (node.focus ? '.focus' : '') + (node.missing ? '.missing' : ''),
    {'data-id': node.id, role: node.missing ? null : 'link', tabindex: node.missing ? null : 0, title: node.missing ? '' : '以它为起点看穿透',
      onclick: restart, onkeydown: (event) => { if (event.key === 'Enter') restart(); }},
    h('div.tn-head', {}, h('span.tn-kind', {}, node.kind), h('b.tn-name', {}, node.name),
      node.missing ? null : h('a.tn-open', {href: target, title: '打开这条记录', onclick: (event) => event.stopPropagation()}, icon('open', 13))),
    node.sub ? h('div.tn-sub', {}, node.sub) : null,
    metrics.length ? h('div.tn-metrics', {}, metrics.map(([label, value, unit]) =>
      h('span', {}, h('small', {}, label), h('b', {}, typeof value === 'number' ? number(value, DIGITS[label]) : value), unit ? h('small', {}, unit) : null))) : null,
    node.chips?.length ? h('div.tn-chips', {}, node.chips.map((chip) => h('span', {}, chip))) : null);
}

export async function traceView(state, library, ref) {
  const graph = await api(`/trace/${encodeURIComponent(library)}/${encodeURIComponent(ref)}`);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.classList.add('trace-wires');
  const columns = graph.layers.map((layer) => h('div.trace-col', {},
    h('div.trace-col-head', {}, h('b', {}, layer.title), h('small', {}, `${layer.nodes.length}`)),
    layer.nodes.length ? layer.nodes.map((node) => nodeCard(library, node)) : h('div.trace-empty', {}, '这一层还没有')));
  const stage = h('div.trace-stage', {}, svg, ...columns);
  const near = (id) => new Set(graph.edges.flatMap(([from, to]) => from === id ? [to] : to === id ? [from] : []).concat(id));

  const wire = () => {
    const box = stage.getBoundingClientRect();
    svg.setAttribute('width', stage.scrollWidth); svg.setAttribute('height', stage.scrollHeight);
    svg.replaceChildren(...graph.edges.map(([from, to]) => {
      const a = stage.querySelector(`[data-id="${CSS.escape(from)}"]`), b = stage.querySelector(`[data-id="${CSS.escape(to)}"]`);
      if (!a || !b) return null;
      const p = a.getBoundingClientRect(), q = b.getBoundingClientRect();
      const x1 = p.right - box.left, y1 = p.top + p.height / 2 - box.top, x2 = q.left - box.left, y2 = q.top + q.height / 2 - box.top, mid = (x1 + x2) / 2;
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`);
      path.dataset.from = from; path.dataset.to = to;
      if (from.startsWith('missing:') || to.startsWith('missing:')) path.classList.add('dashed');
      if (from === graph.focus || to === graph.focus) path.classList.add('hot');
      return path;
    }).filter(Boolean));
  };
  stage.addEventListener('mouseover', (event) => {
    const card = event.target.closest('.trace-node');
    if (!card) return;
    const keep = near(card.dataset.id);
    stage.classList.add('hovering');
    stage.querySelectorAll('.trace-node').forEach((node) => node.classList.toggle('near', keep.has(node.dataset.id)));
    svg.querySelectorAll('path').forEach((path) => path.classList.toggle('lit', path.dataset.from === card.dataset.id || path.dataset.to === card.dataset.id));
  });
  stage.addEventListener('mouseleave', () => { stage.classList.remove('hovering'); svg.querySelectorAll('.lit').forEach((path) => path.classList.remove('lit')); });
  new ResizeObserver(() => requestAnimationFrame(wire)).observe(stage);
  requestAnimationFrame(wire);

  const missing = graph.layers.flatMap((layer) => layer.nodes).filter((node) => node.missing);
  return h('div.trace', {},
    h('div.trace-legend', {},
      h('span', {}, `从「${graph.start.name}」（${graph.start.kind}）出发：往回找树脂，往后找配方、工单、膜、n/k、老化。`),
      h('span.quiet', {}, '点一格以它为起点再看；', icon('open', 12), ' 打开那条记录。'),
      missing.length ? h('span.trace-warn', {}, `虚线框 ${missing.length} 个：库里找不到这一端，可在研发台 · 数据体检里处理`) : null),
    h('div.trace-scroll', {}, stage));
}

// 研发台里的入口：先挑一个起点（编号、配方、膜……都行）
export async function traceDesk(state, library, ref) {
  const host = h('div.trace-host');
  const input = h('input', {type: 'search', placeholder: '输入编号：R100-41、PA-T23A、工单号……', value: ref || ''});
  const list = h('div.trace-picks');
  const open = async (id) => { host.replaceChildren(h('p.quiet', {}, '正在找它的上下游…')); host.replaceChildren(await traceView(state, library, id)); };
  const search = async () => {
    const q = input.value.trim();
    if (!q) return;
    const {rows} = await api(`/lib/${encodeURIComponent(library)}/records?q=${encodeURIComponent(q)}&limit=40`);
    const order = ['树脂实验', '配方', '工单', '膜', 'NK分组', '老化谱', '树脂批', 'GPC样品', '树脂取样', 'NK谱'];
    const useful = rows.filter((row) => order.includes(row.kind)).sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));
    list.replaceChildren(...(useful.length > 1 ? useful.slice(0, 8) : []).map((row) => h('button.btn.ghost.small', {type: 'button', onclick: () => open(row.id)}, `${row.kind} · ${row.name}`)),
      useful.length > 8 ? h('span.quiet.small', {}, `还有 ${useful.length - 8} 条，写得更具体些`) : null);
    if (useful[0]) open(useful[0].id); else host.replaceChildren(h('p.quiet', {}, `没找到「${q}」。`));
  };
  input.addEventListener('keydown', (event) => { if (event.key === 'Enter') search(); });
  if (ref) search(); else {
    const {rows} = await api(`/lib/${encodeURIComponent(library)}/records?q=${encodeURIComponent('PA-T')}&limit=60`);
    const latest = rows.filter((row) => row.kind === '配方').sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))[0];
    if (latest) { input.value = latest.name; open(latest.id); }
  }
  return h('div', {}, h('div.kv-tools', {}, h('label.search', {}, icon('search', 15), input), h('button.btn', {type: 'button', onclick: search}, '看穿透'), list), host);
}
