// 课题：研发路线（主线 → 节点 → 子节点）、待办看板、研究底稿。数据是库里的"树脂.研发路线"，能直接改。
import {api, h, icon, toast} from './core.js';
import {tableBlock} from './blocks.js';

export const STATUS = {
  confirmed: ['已确认', 'done'], observed: ['观察到', 'done'], tracking: ['跟踪中', 'current'], doing: ['进行中', 'current'],
  pending: ['待定', 'open'], hypothesis: ['假设', 'open'], provisional: ['暂定', 'open'], planned: ['计划', 'todo'], todo: ['待办', 'todo'],
  later: ['以后', 'todo'], rejected: ['否定', 'off'],
};
const LINE_INK = ['#c96442', '#3f7cc0', '#b5841c', '#5f8c3e', '#8a63b8', '#c25f8a'];
const status = (key) => STATUS[key] || [key || '—', 'todo'];

// 总览线路图用：每条主线一条线，子节点是站
export function roadLines(road) {
  const stage = {done: '走过', current: '当前', open: '没到', todo: '没到', off: '走过'};
  return (road.root.children || []).map((line, index) => ({
    id: line.id, name: line.title, 一句话: [line.metric, line.summary].filter(Boolean).join(' · '), 线路色: LINE_INK[index % LINE_INK.length],
    站点: (line.children || []).map((node) => ({站: node.title.replace(/^[①-⑩]\s*/, ''), 标题: node.metric || '', 说明: node.summary || '', 状态: stage[status(node.status)[1]], 记录: [], node: node.id})),
  })).filter((line) => line.站点.length);
}

function findNode(node, id, path = []) {
  if (node.id === id) return {node, path};
  for (const child of node.children || []) { const found = findNode(child, id, [...path, node]); if (found) return found; }
  return null;
}

export function nodePanel(state, road, id, {onSaved, compact} = {}) {
  const found = findNode(road.root, id);
  if (!found) return h('p.quiet', {}, '这个节点不见了。');
  const {node, path} = found;
  const [label, tone] = status(node.status);
  const tasks = road.tasks.filter((task) => task.nodeId === node.id);
  const draft = {};
  const save = async () => {
    const {roadmap} = await api(`/roadmap/${encodeURIComponent(state.library)}/node/${encodeURIComponent(node.id)}`, 'PUT', draft);
    toast('节点已保存'); onSaved?.(roadmap);
  };
  const edit = (key, rows = 2) => h('textarea', {rows, oninput: (event) => { draft[key] = event.target.value; }}, node[key] || '');
  return h('div.node-panel', {},
    h('div.node-path', {}, path.map((item) => item.title).join(' › ')),
    h('div.node-title', {}, h('span.st-dot.' + tone), h('h3', {}, node.title), h('span.st-tag.' + tone, {}, label), node.metric ? h('span.node-metric', {}, node.metric) : null),
    node.summary ? h('p.node-summary', {}, node.summary) : null,
    node.detail ? h('div.node-detail', {}, node.detail) : null,
    node.evidence ? h('p.node-evidence', {}, h('b', {}, '证据 '), node.evidence) : null,
    (node.records || []).length ? h('div.node-records', {}, h('h4', {}, '记录'), node.records.map((record) => h('div.node-record', {},
      h('b', {}, record.kind), ' ', record.value, record.conditions ? h('div.quiet', {}, '条件：' + record.conditions) : null, record.conclusion ? h('div', {}, '结论：' + record.conclusion) : null))) : null,
    (node.children || []).length ? h('div.node-kids', {}, h('h4', {}, `下一层（${node.children.length}）`), h('div.node-chips', {}, node.children.map((child) =>
      h('button.node-chip', {type: 'button', onclick: (event) => event.currentTarget.closest('.node-panel').replaceWith(nodePanel(state, road, child.id, {onSaved, compact}))},
        h('span.st-dot.' + status(child.status)[1]), child.title)))) : null,
    tasks.length ? h('div.node-tasks', {}, h('h4', {}, '相关待办'), tasks.map((task) => taskCard(state, task, onSaved))) : null,
    compact ? null : h('details.node-edit', {}, h('summary', {}, icon('edit', 13), ' 改这个节点'),
      h('label.form-row', {}, h('span', {}, '状态'), h('select', {onchange: (event) => { draft.status = event.target.value; }},
        Object.entries(STATUS).map(([key, [text]]) => h('option', {value: key, selected: key === node.status}, text)))),
      h('label.form-row', {}, h('span', {}, '一句话'), edit('summary')), h('label.form-row', {}, h('span', {}, '指标'), edit('metric', 1)),
      h('label.form-row', {}, h('span', {}, '详细'), edit('detail', 5)), h('label.form-row', {}, h('span', {}, '证据'), edit('evidence', 2)),
      h('div.row', {}, h('button.btn.primary.small', {type: 'button', onclick: save}, '保存'))),
    h('div.drawer-actions', {},
      h('button.btn.primary.small', {type: 'button', onclick: () => state.panel.ask(`看一下研发路线的节点「${node.title}」（${path.map((item) => item.title).join(' › ')}）：现在的证据够不够、库里有哪些相关数据、下一步做什么实验。`, `课题节点「${node.title}」`)}, icon('spark', 14), '让助手看这一步')));
}

function taskCard(state, task, onSaved) {
  const move = async (value) => {
    const {roadmap} = await api(`/roadmap/${encodeURIComponent(state.library)}/task/${encodeURIComponent(task.id)}`, 'PUT', {status: value});
    toast(`「${task.title}」→ ${status(value)[0]}`); onSaved?.(roadmap);
  };
  return h('div.task-card', {},
    h('div.task-head', {}, task.priority ? h('span.prio.' + task.priority.toLowerCase(), {}, task.priority) : null, h('b', {}, task.title)),
    task.stage ? h('div.task-meta', {}, task.stage + (task.due ? ' · 期限 ' + task.due : '')) : null,
    task.summary ? h('p', {}, task.summary) : null,
    task.acceptance ? h('details', {}, h('summary', {}, '验收'), h('p', {}, task.acceptance), task.notes ? h('p.quiet', {}, task.notes) : null) : null,
    h('select.task-move', {'aria-label': '状态', onchange: (event) => move(event.target.value)},
      ['doing', 'todo', 'later', 'done'].map((key) => h('option', {value: key, selected: key === task.status}, {doing: '进行中', todo: '待办', later: '以后', done: '做完了'}[key]))));
}

export async function roadmapPage(main, state, header, extra) {
  const {roadmap: road} = await api('/roadmap/' + encodeURIComponent(state.library));
  if (!road) return null;
  const side = h('aside.road-side');
  const draw = (value) => { Object.assign(road, value); body.replaceChildren(...content()); };
  const open = (id) => { side.replaceChildren(nodePanel(state, road, id, {onSaved: draw})); side.classList.add('on'); state.anchor(`课题节点「${findNode(road.root, id)?.node.title}」`); };
  const counts = {};
  const walk = (node) => { counts[node.status] = (counts[node.status] || 0) + 1; (node.children || []).forEach(walk); };
  walk(road.root);
  const content = () => [
    h('section.road-goal', {},
      h('div.road-goal-text', {}, h('h2', {}, road.root.title, h('small', {}, `${road.source?.version ? 'V' + road.source.version : ''} · ${road.source?.updated || ''}`)),
        h('p', {}, road.summary.goal || road.root.detail || road.root.summary), road.summary.currentScope ? h('p.quiet', {}, road.summary.currentScope) : null),
      h('div.road-counts', {}, Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([key, value]) => h('span.road-count', {}, h('span.st-dot.' + status(key)[1]), `${status(key)[0]} ${value}`)))),
    h('section.road-lines', {}, (road.root.children || []).map((line, index) => h('div.road-line', {style: `--lc:${LINE_INK[index % LINE_INK.length]}`},
      h('div.road-line-head', {}, h('span.road-ink'), h('h3', {}, line.title), h('span.st-tag.' + status(line.status)[1], {}, status(line.status)[0]),
        line.metric ? h('span.node-metric', {}, line.metric) : null, h('button.btn.ghost.small', {type: 'button', onclick: () => open(line.id)}, '细看')),
      line.summary ? h('p.road-line-sum', {}, line.summary) : null,
      h('div.road-nodes', {}, (line.children || []).map((node) => h('button.road-node.' + status(node.status)[1], {type: 'button', onclick: () => open(node.id)},
        h('div.rn-top', {}, h('span.st-dot.' + status(node.status)[1]), h('span.rn-status', {}, status(node.status)[0]), (node.children || []).length ? h('span.rn-kids', {}, `${node.children.length} 项`) : null),
        h('div.rn-title', {}, node.title), node.metric ? h('div.rn-metric', {}, node.metric) : null, node.summary ? h('div.rn-sum', {}, node.summary) : null)))))),
    h('section.road-board', {}, h('h2', {}, '待办', h('small', {}, `${road.tasks.length} 项`)), h('div.board', {},
      [['doing', '进行中'], ['todo', '待办'], ['later', '以后'], ['done', '做完了']].map(([key, title]) => {
        const list = road.tasks.filter((task) => (task.status || 'todo') === key).sort((a, b) => String(a.priority).localeCompare(String(b.priority)));
        return h('div.board-col', {}, h('h3', {}, title, h('small', {}, list.length)), list.map((task) => taskCard(state, task, draw)));
      }))),
    h('section.road-base', {}, h('h2', {}, '研究底稿', h('small', {}, '目标、基线、候选路线、已知事实、约束、数据缺口')), h('div.blocks', {},
      road.targets.length ? baseBlock('目标', ['指标', '目标', '数值门槛', '门槛状态'], road.targets.map((item) => [item.metric, item.target, item.numericThreshold, item.thresholdStatus])) : null,
      road.baseline.length ? baseBlock('基线', ['项', '值', '单位', '状态'], road.baseline.map((item) => [item.key, item.value, item.unit, status(item.status)[0]])) : null,
      road.routes.length ? baseBlock('候选路线', ['路线', '状态', '关注'], road.routes.map((item) => [item.name, status(item.status)[0], item.focus])) : null,
      road.facts.length ? listBlock('已知事实', road.facts.map((item) => item.text)) : null,
      road.constraints.length ? listBlock('约束', road.constraints.map((item) => item.text)) : null,
      road.gaps.length ? listBlock('数据缺口', road.gaps.map((item) => item.item || item.text)) : null)),
    road.notes.length ? h('details.block.road-notes', {}, h('summary', {}, `版本记录（${road.notes.length}）`), h('ul', {}, road.notes.map((note) => h('li', {}, note)))) : null,
    extra || null];
  const body = h('div.road-main');
  body.replaceChildren(...content());
  main.replaceChildren(header('课题', `${road.root.title} · ${(road.root.children || []).length} 条主线 · ${road.tasks.length} 项待办`,
    h('button.btn', {type: 'button', onclick: () => state.panel.ask('看一下研发路线和待办：哪些节点该更新状态（库里已有新数据的）、哪些待办该推进，给我一份建议清单，我确认后你再改。', '课题')}, icon('spark', 15), '让助手更新路线')),
  h('div.road', {}, body, side));
  return road;
}

function baseBlock(title, columns, rows) {
  return h('section.block', {}, h('header.block-head', {}, h('h3', {}, title)), tableBlock({columns, rows}, '', {limit: 12}));
}

function listBlock(title, items) {
  return h('section.block', {}, h('header.block-head', {}, h('h3', {}, title)), h('ul.plain-list', {}, items.map((text) => h('li', {}, text))));
}
