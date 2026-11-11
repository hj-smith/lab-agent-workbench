// 各个页面。每个页面函数拿到 (主区, 状态, 参数)，画出来，并告诉助手栏"他在看什么"。
import {api, empty, fmt, go, h, humanText, icon, internal, looksId, md, spark, technical, toast} from './core.js';
import {lineColor, transitMap} from './transit.js';
import {COLORS, cardBox, number, plotly, renderBlocks} from './blocks.js';
import {nodePanel, roadLines, roadmapPage} from './research.js';
import {kindView} from './grid.js';
import {ordersView, paperView, projectsData, weighingView} from './work.js';
import {designView, formulaEditor} from './stages.js';
import {gpcAnalysis} from './testing.js';
import {traceView} from './trace.js';
import {previewContent} from './viewer.js';
import {literaturePage} from './literature.js';
import {learnPage} from './learn.js';
import {projectTools, remember} from './grouping.js';
import {settingsTabs} from './users.js';
const EXPERIMENT_KINDS = ['树脂实验', '配方', '工单', 'DOE研究', '树脂批'];
const HIDDEN = {has: internal};

export function header(title, sub, ...extra) {
  return h('header.page-head', {}, h('div.page-title', {}, h('h1', {}, title), sub ? h('p.page-sub', {}, sub) : null), ...extra);
}

function searchBox(state) {
  const input = h('input', {type: 'search', placeholder: '搜索编号、样品、配方、文献…', 'aria-label': '搜索',
    onkeydown: (event) => { if (event.key === 'Enter' && input.value.trim()) go('search', input.value.trim()); }});
  return h('label.search', {}, icon('search', 16), input);
}

const ask = (state, text, anchor) => state.panel.ask(text, anchor);
const firstSentence = (text) => (String(text || '').match(/^[^。]*。?/) || [''])[0];   // 类型说明是写给助手的，页面上只露第一句

// ---------- 总览 ----------

export async function overview(main, state) {
  const [data, {roadmap: road}, {brief}] = await Promise.all([api('/overview/' + encodeURIComponent(state.library)), api('/roadmap/' + encodeURIComponent(state.library)).catch(() => ({roadmap: null})),
    api('/brief/' + encodeURIComponent(state.library) + '/latest')]);
  state.anchor('总览');
  const projects = road ? roadLines(road) : data.projects;
  const cards = data.inbox.cards, running = data.inbox.running, agentInfo = data.agent;
  const status = [road ? `${road.root.title} · ${projects.length} 条主线` : `${projects.length} 条课题线路`, `${cards.length} 件事等你拍板`,
    data.inbox.timers.length ? `${data.inbox.timers.length} 个约好的复查` : ''].filter(Boolean).join(' · ');
  const mapBox = h('div.map-box'), drawer = h('div.station-drawer');
  let focus = null;
  const drawMap = () => mapBox.replaceChildren(projects.length ? transitMap(projects, {width: Math.max(620, Math.min(1100, mapBox.clientWidth || 820)), focus, onStation: (row, col) => {
    focus = focus && focus.row === row && focus.col === col ? null : {row, col};
    drawMap();
    if (!focus) { drawer.replaceChildren(); return; }
    const station = projects[row]['站点'][col];
    if (road && station.node) {
      state.anchor(`课题节点「${station['站']}」`);
      drawer.replaceChildren(h('div.drawer-card', {}, nodePanel(state, road, station.node, {compact: true}), h('a.btn.small', {href: '#/projects'}, '在课题页打开')));
    } else drawStation(drawer, state, projects[row], col, row);
  }}) : h('div.empty-state', {}, h('p', {}, '还没有课题线路。'), h('button.btn.primary', {type: 'button', onclick: () => ask(state, '根据我最近的实验和笔记，帮我把正在做的课题整理成课题记录（每个课题的站点和挂的记录），整理完给我看。', '总览')}, '让助手整理课题')));
  requestAnimationFrame(drawMap);
  new ResizeObserver(() => { clearTimeout(mapBox.t); mapBox.t = setTimeout(drawMap, 150); }).observe(mapBox);

  // 助手现在：在做什么、约了什么、会什么、最近跑了多少
  const runs = agentInfo.runs || [];
  const tile = (href, iconName, label, value, note, extra) => h('a.agent-tile', {href}, h('div.at-top', {}, icon(iconName, 15), label), h('div.at-value', {}, value), note ? h('div.at-note', {}, note) : null, extra || null);
  const band = h('section.agent-band', {},
    tile(running.length ? '#/assistant/' + encodeURIComponent(running[0].lane) : '#/assistant', 'assistant', '正在做', running.length ? `${running.length} 件` : '空闲',
      running.length ? running.map((item) => item.title).join('、') : `${agentInfo.lanes} 段对话`),
    tile('#/agent/rules', 'clock', '约好的', `${data.inbox.timers.length} 个复查`, data.inbox.timers[0] ? `${data.inbox.timers[0].at || ''} ${data.inbox.timers[0].about}` : '晨报、复盘按规则自己跑'),
    tile('#/agent/skills', 'skill', '技能', `${agentInfo.skills} 个`, agentInfo.mine.length ? `沉淀 ${agentInfo.mine.length}：${agentInfo.mine.map((item) => item.name).join('、')}` : (agentInfo.top_skills[0] ? `最常用：${agentInfo.top_skills[0].name}` : '还没有沉淀出来的')),
    tile('#/agent/tools', 'plug', '工具和连接', `${agentInfo.tools} 个工具`, agentInfo.mcp.length ? `MCP：${agentInfo.mcp.map((item) => `${item.name}${item.state === 'connected' ? '' : '（' + item.state + '）'}`).join('、')}` : '还没接 MCP'),
    tile('#/agent/runs', 'runs', '近 14 天', `${runs.reduce((sum, day) => sum + day.done + day.failed + day.other, 0)} 轮`,
      `${agentInfo.notes} 篇笔记` + (agentInfo.cost ? ` · 花费 $${agentInfo.cost.toFixed(2)}` : ''),
      runs.length >= 2 ? spark(runs.map((day, index) => [index, day.done + day.failed + day.other]), {width: 120, height: 26}) : null),
    tile('#/outputs', 'outputs', '最近产出', `${data.outputs.length} 个`, data.outputs[0]?.name || '还没有'));

  // 数据进展：每月进库多少条（按记录自己的日期）
  const activity = data.activity || [];
  const groups = [...new Set(activity.flatMap((month) => Object.keys(month).filter((key) => key !== 'month')))];
  const progress = h('div.plot.progress-plot');
  if (activity.length) requestAnimationFrame(async () => {
    const Plotly = await plotly();
    const months = activity.map((month) => `${Number(month.month.slice(5))} 月`);
    Plotly.newPlot(progress, groups.map((group, index) => ({type: 'bar', name: group, x: months, y: activity.map((month) => month[group] || 0), marker: {color: COLORS[index % COLORS.length]},
      hovertemplate: `${group} %{y} 条<extra></extra>`})),
      {barmode: 'stack', height: 200, margin: {l: 40, r: 10, t: 30, b: 30}, paper_bgcolor: '#fbfaf7', plot_bgcolor: '#fbfaf7', font: {family: 'Microsoft YaHei, sans-serif', size: 11.5},
        legend: {orientation: 'h', x: 0, y: 1.22, font: {size: 11.5}}, xaxis: {type: 'category', tickangle: 0}, yaxis: {gridcolor: '#ebe6dc'}}, {displayModeBar: false, responsive: true});
  });

  const decide = h('section.side-block', {}, h('h2', {}, '等你拍板', h('small', {}, cards.length ? `${cards.length} 件` : '')),
    cards.length ? cards.map((item) => decisionCard(item, state)) : h('p.quiet', {}, '没有要你定的事。'));
  const fresh = h('section.side-block', {}, h('h2', {}, '新进来的数据', h('small', {}, '按测试日期')), h('div.rows', {}, data.recent.map((item, index) =>
    h('a.data-row', {href: `#/record/${encodeURIComponent(state.library)}/${encodeURIComponent(item.id)}`},
      spark(item.points, {color: COLORS[index % 4], kind: item.kind === '膜' ? 'dots' : 'line'}),
      h('div.data-text', {}, h('div.k', {}, `${item.kind} · ${item.name}`), h('div.v', {}, item.text || '')),
      h('span.t', {}, (item.date || '').slice(5, 10))))));
  const pending = (data.health || {})['待确认关联'] || 0, broken = (data.health || {})['断链'] || 0;
  const quick = h('div.quick', {}, [['#/synthesis', 'synthesis', '设计新实验'], ['#/formulation', 'formulation', '配方和工单'], ['#/testing/import', 'upload', '导入文件'],
    ['#/analysis/new', 'compare', 'GPC 对比'], ['#/desk/lots', 'desk', '树脂批总表'], ['#/desk/trace', 'layers', '产品穿透'],
    ['#/desk/health', 'check', '数据体检', pending || broken ? `待关联 ${pending} · 断链 ${broken}` : '']].map(([href, name, label, count]) =>
    h('a.quick-item', {href, title: count ? '数据体检：等你确认的关联和找不到另一端的记录' : null}, icon(name, 16), label, count ? h('small.quick-count', {}, count) : null)));
  // 今日晨报：一句话 + 要点，点开是整页；今天还没有就能马上做一份
  const today = new Date().toLocaleDateString('sv-SE', {timeZone: 'Asia/Shanghai'});   // 北京时间的 2026-11-10
  const briefCard = h('a.brief-card' + (brief && brief.date === today ? '' : '.old'), {href: brief ? `#/record/${encodeURIComponent(state.library)}/${encodeURIComponent(brief.id)}` : '#/agent/rules'},
    h('div.bc-label', {}, icon('spark', 14), brief ? `${brief.name}${brief.date === today ? '' : '（不是今天的）'}` : '还没有晨报'),
    h('div.bc-head', {}, brief ? brief.headline : '每天 08:30 助手会写一份：昨天到现在发生了什么、该先做什么'),
    brief?.points?.length ? h('ul.bc-points', {}, brief.points.map((point) => h('li', {}, point))) : null,
    !brief || brief.date !== today ? h('button.btn.small.primary', {type: 'button', onclick: async (event) => {
      event.preventDefault(); event.stopPropagation(); await api('/rules/' + encodeURIComponent('研发晨报') + '/run?library=' + encodeURIComponent(state.library), 'POST'); toast('助手开始写今天的晨报，写好会通知你');
    }}, '现在做今天的晨报') : null);
  main.replaceChildren(
    header('总览', status, searchBox(state)),
    h('div.overview', {}, h('section.map-area', {}, briefCard, quick,
      h('h2', {}, road ? '研发主线' : '进行中的课题', h('small', {}, road ? '每条线是一条研发主线，站是它的节点：实心 = 已确认或观察到，大圆 = 跟踪中，空心 = 待定或计划；点一站看详情' : '每条线是一个课题；点一站看那一步')),
      legend(), mapBox, drawer,
      h('h2.band-title', {}, '助手现在', h('small', {}, '它在做什么、约了什么、会什么、最近做了多少')), band,
      activity.length ? [h('h2.band-title', {}, '数据进展', h('small', {}, '每月进库的数据（按记录自己的日期）')), progress] : null),
    h('aside.side', {}, decide, fresh)));
}

function legend() {
  return h('div.legend', {}, h('span', {}, h('i.lg-done'), '走过的站'), h('span', {}, h('i.lg-current'), '当前站'),
    h('span', {}, h('i.lg-x'), '换乘：两个课题挂着同一条记录'), h('span', {}, h('i.lg-todo'), '还没到'));
}

function decisionCard(item, state) {
  return cardBox(item, {open: (lane) => state.panel.open(lane),
    answer: async (text) => { await api(`/lanes/${encodeURIComponent(item.lane)}/answer`, 'POST', {text}); state.panel.open(item.lane); }});
}

async function drawStation(drawer, state, project, col) {
  const station = project['站点'][col];
  const ids = station['记录'] || [];
  const found = await Promise.all(ids.map((id) => api(`/lib/${encodeURIComponent(state.library)}/records/${encodeURIComponent(id)}?series=false`).catch(() => null)));
  state.anchor(`课题「${project.name}」的「${station['站']}${station['标题'] ? ' · ' + station['标题'] : ''}」站`);
  drawer.replaceChildren(h('div.drawer-card', {},
    h('div.drawer-head', {}, h('span.line-chip', {style: {background: lineColor(project, 0)}}), h('h3', {}, `${station['站']} · ${station['标题'] || '（还没定）'}`),
      h('span.state-tag', {}, station['状态'])),
    station['说明'] ? h('p', {}, station['说明']) : null,
    found.filter(Boolean).length ? h('div.linked', {}, found.filter(Boolean).map((record) =>
      h('a.record-chip', {href: `#/record/${encodeURIComponent(state.library)}/${encodeURIComponent(record.id)}`}, h('span.k', {}, record.kind), record.name))) : h('p.quiet', {}, '这一站还没有挂记录。'),
    h('div.drawer-actions', {},
      h('button.btn.primary', {type: 'button', onclick: () => ask(state, `看一下课题「${project.name}」的「${station['站']}」这一站，告诉我现在的情况和下一步该做什么。`)}, '让助手看这一站'),
      h('a.btn', {href: '#/project/' + encodeURIComponent(project.id)}, '打开课题'))));
}

// ---------- 课题 ----------

export async function projects(main, state) {
  const {rows} = await api(`/lib/${encodeURIComponent(state.library)}/records?kind=${encodeURIComponent('课题')}`).catch(() => ({rows: []}));
  state.anchor('课题');
  const extra = rows.length ? h('details.block.road-notes', {}, h('summary', {}, `另外记的课题（${rows.length}，助手整理的草稿，待你确认）`),
    h('div.mini-cards', {}, rows.map((row) => h('a.mini-card', {href: '#/project/' + encodeURIComponent(row.id)}, h('div.mini-k', {}, row['当前站'] || ''), h('div.mini-name', {}, row.name), h('div.mini-text', {}, row['一句话'] || ''))))) : null;
  if (await roadmapPage(main, state, header, extra)) return;
  main.replaceChildren(header('课题', `${rows.length} 个课题`, h('button.btn.primary', {type: 'button', onclick: () => newProject(state)}, icon('plus', 16), '新课题')),
    h('div.page-body', {}, rows.length ? transitMap(rows, {width: 1000, onStation: (row) => go('project', rows[row].id)}) : null,
      table(['名字', '问题', '当前站', '一句话', '开始'], rows.map((row) => [row.name, row['问题'], row['当前站'], row['一句话'], row['开始']]),
        (index) => go('project', rows[index].id))));
}

async function newProject(state) {
  const name = prompt('新课题的名字');
  if (!name) return;
  const stations = ['调研', '设计', '批次', '测试', '结论'].map((stage, index) => ({站: stage, 标题: '', 说明: '', 状态: index ? '没到' : '当前', 记录: []}));
  const {saved} = await api(`/lib/${encodeURIComponent(state.library)}/records`, 'POST', {kind: '课题', rows: [{name, 当前站: '调研', 站点: stations, 开始: new Date().toISOString().slice(0, 10)}]});
  go('project', saved[0].id);
}

export async function project(main, state, [id]) {
  const record = await api(`/lib/${encodeURIComponent(state.library)}/records/${encodeURIComponent(id)}`);
  const data = record.data;
  state.anchor(`课题「${record.name}」`);
  const stations = data['站点'] || [];
  main.replaceChildren(header(record.name, data['问题'] || '', h('button.btn', {type: 'button', onclick: () => ask(state, `看一下课题「${record.name}」：现在走到哪了，下一步做什么，有什么风险。`)}, '让助手梳理'),
    h('a.btn', {href: `#/record/${encodeURIComponent(state.library)}/${encodeURIComponent(id)}`}, '原始记录')),
  h('div.page-body', {}, record.unconfirmed.length ? h('p.notice', {}, `待确认：${record.unconfirmed.join('、')}。${record.note || ''}`) : null,
    transitMap([{...data, name: record.name}], {width: 1000}),
    h('ol.stations', {}, stations.map((station) => h('li.station-item.' + ({'走过': 'done', '当前': 'current', '没到': 'todo'}[station['状态']] || 'todo'), {},
      h('div.st-head', {}, h('b', {}, station['站']), station['标题'] ? ` · ${station['标题']}` : '', h('span.state-tag', {}, station['状态'])),
      station['说明'] ? h('p', {}, station['说明']) : null,
      h('div.linked', {}, (station['记录'] || []).map((rid) => h('a.record-chip', {href: `#/record/${encodeURIComponent(state.library)}/${encodeURIComponent(rid)}`}, rid.replace(/^(res|frm|gpc|ell|nk|age)-/, ''))))))),
    await projectKnowledge(state, record)));
}

// 这个课题的知识：命题（状态、把握、证据几条）和预测（中没中）；还没有就交给助手去调研、写预测
async function projectKnowledge(state, record) {
  const {claims, predictions} = await api(`/project-knowledge/${encodeURIComponent(state.library)}/${encodeURIComponent(record.id)}`);
  const TONE = {成立: 'done', 待验证: 'open', 否定: 'off', 中: 'done', 部分: 'open', 没中: 'off', 等结果: 'todo'};
  const sure = (value) => value == null || value === '' ? null : h('span.sure', {title: `把握 ${value}%`}, h('span.sure-track', {}, h('i', {style: `width:${Math.max(0, Math.min(100, Number(value)))}%`})), h('small', {}, `把握 ${value}%`));
  const research = () => ask(state, `按技能「调研」调研课题「${record.name}」的问题：${record.data['问题'] || ''}。结论写成命题，挂到这个课题上（关联"属于"→课题），出处连文献，证据连我们的记录。`);
  const predictNext = () => ask(state, `按技能「预测和验证」，给课题「${record.name}」下一个要做的实验写预测：每个要看的指标一条，写预测值或范围、把握和依据。`);
  return h('section.block.wide.project-know', {},
    h('header.block-head', {}, h('h3', {}, '这个课题的知识'), h('span.grow'),
      h('button.btn.small', {type: 'button', onclick: research}, icon('spark', 13), '让助手调研'), h('button.btn.small', {type: 'button', onclick: predictNext}, '给下一个实验写预测')),
    h('div.know-cols', {},
      h('div', {}, h('h4', {}, `命题（${claims.length}）`), claims.length ? h('ul.claim-list', {}, claims.map((claim) => h('li', {},
        h('span.st-tag.' + (TONE[claim['状态']] || 'todo'), {}, claim['状态']), h('a', {href: `#/record/${encodeURIComponent('知识库')}/${encodeURIComponent(claim.id)}`}, claim['陈述']), sure(claim['把握']),
        h('small.quiet', {}, [claim['证据'] ? `证据 ${claim['证据']} 条` : '', claim['出处'] ? `出处 ${claim['出处']} 条` : ''].filter(Boolean).join(' · ') || '还没有证据'))))
        : h('p.quiet.small', {}, '还没有。命题是一句能单独读懂的结论，带适用范围、状态（待验证 / 成立 / 否定）和把握，出处连文献、证据连我们的记录。')),
      h('div', {}, h('h4', {}, `预测（${predictions.length}）`), predictions.length ? h('ul.claim-list', {}, predictions.map((item) => h('li', {},
        h('span.st-tag.' + (TONE[item['命中'] || item['状态']] || 'todo'), {}, item['命中'] || item['状态']),
        h('a', {href: `#/record/${encodeURIComponent(state.library)}/${encodeURIComponent(item.id)}`}, `${item['实验'] || ''} · ${item['指标'] || ''}：预测 ${item['预测'] ?? '—'}` + (item['实测'] != null && item['实测'] !== '' ? `，实测 ${item['实测']}` : '')),
        sure(item['把握']))))
        : h('p.quiet.small', {}, '还没有。做实验之前先写下会怎样、多有把握、凭什么；结果出来对照，高把握却没中最值得回头看。'))));
}

// ---------- 表格 ----------

function table(columns, rows, onRow) {
  let sortAt = -1, dir = 1, filter = '';
  const body = h('tbody'), count = h('span.quiet');
  const head = h('tr', {}, columns.map((column, index) => h('th', {scope: 'col', tabindex: 0, onclick: () => { dir = sortAt === index ? -dir : 1; sortAt = index; draw(); }}, column)));
  const draw = () => {
    let shown = rows.map((row, index) => ({row, index})).filter(({row}) => !filter || row.some((cell) => fmt.value(cell).toLowerCase().includes(filter)));
    if (sortAt >= 0) shown.sort((a, b) => { const x = a.row[sortAt], y = b.row[sortAt]; return (typeof x === 'number' && typeof y === 'number' ? x - y : fmt.value(x).localeCompare(fmt.value(y), 'zh')) * dir; });
    body.replaceChildren(...shown.slice(0, 500).map(({row, index}) => h('tr', {onclick: onRow ? () => onRow(index) : null, class: onRow ? 'clickable' : null},
      row.map((cell) => {
        if (typeof cell === 'number') return h('td.num', {}, fmt.num(cell, 4));
        const text = fmt.value(cell);
        const color = /^#[0-9a-f]{6}$/i.test(text) ? h('span.swatch', {style: `background:${text}`}) : null;
        return h('td', {title: text.length > 24 ? text : null}, color, text.slice(0, 120));
      }))));
    head.querySelectorAll('th').forEach((th, index) => th.dataset.sort = index === sortAt ? (dir > 0 ? 'asc' : 'desc') : '');
    count.textContent = `${shown.length} / ${rows.length} 条` + (shown.length > 500 ? '（只显示前 500）' : '');
  };
  const input = h('input', {type: 'search', placeholder: '筛选', oninput: () => { filter = input.value.trim().toLowerCase(); draw(); }});
  draw();
  return h('div.table-block', {}, h('div.table-tools', {}, h('label.search.small', {}, icon('search', 14), input), count),
    h('div.tablewrap', {}, h('table.data', {}, h('thead', {}, head), body)));
}

function kindTable(state, kind, rows) {
  const fields = (kind.fields || []).map((field) => field.key).filter((key) => !HIDDEN.has(key)).slice(0, 9);
  const columns = ['名字', ...fields.map((key) => { const field = kind.fields.find((item) => item.key === key); return key + (field?.unit ? ` / ${field.unit}` : ''); })];
  return table(columns, rows.map((row) => [row.name, ...fields.map((key) => {
    const value = row[key];
    return row.unconfirmed?.includes(key) && value != null ? `${fmt.value(value)}（待确认）` : (value && typeof value === 'object' ? (Array.isArray(value) ? `${value.length} 项` : '…') : value);
  })]), (index) => go('record', state.library, rows[index].id));
}

async function kindPage(main, state, library, name, headerExtra = []) {
  const [{kinds}, {rows, total}] = await Promise.all([api(`/lib/${encodeURIComponent(library)}/kinds`), api(`/lib/${encodeURIComponent(library)}/records?kind=${encodeURIComponent(name)}`)]);
  const kind = kinds.find((item) => item.name === name) || {fields: [], about: ''};
  return {kind, rows, total, node: kindTable({...state, library}, kind, rows)};
}

// ---------- 实验 ----------

export async function experiments(main, state, [kindName = '树脂实验']) {
  const {kinds} = await api(`/lib/${encodeURIComponent(state.library)}/kinds`);
  const present = EXPERIMENT_KINDS.filter((name) => kinds.some((kind) => kind.name === name && kind.records));
  state.anchor(`实验 · ${kindName}`);
  const tabs = h('nav.tabs', {}, present.map((name) => h('a.tab' + (name === kindName ? '.on' : ''), {href: '#/experiments/' + encodeURIComponent(name)}, name, h('small', {}, kinds.find((item) => item.name === name).records))));
  if (kindName === '树脂实验') {
    const {rows} = await api('/experiments/' + encodeURIComponent(state.library));
    main.replaceChildren(header('实验', `树脂合成 ${rows.length} 个：产率、条件、分子量增长一眼看到`,
      h('button.btn.primary', {type: 'button', onclick: () => ask(state, '我要设计一个新的树脂合成实验。先看最近几个实验的结果和研发路线上的待办，建议下一个实验怎么做（路线、投料比、温度时间、要验证什么），我确认后建设计。', '实验')}, icon('plus', 16), '设计新实验')),
    tabs, h('div.page-body', {}, resinCards(state, rows)));
    return;
  }
  const {node, kind, total} = await kindView(state, kindName, {ask: (text) => ask(state, text)});
  main.replaceChildren(header('实验', `${kindName}：${total} 条。${firstSentence(kind.about)}`), tabs, h('div.page-body', {}, node));
}

// 一个树脂实验的卡片：编号、状态、标题、因子标签、产率、温度时间、Mw 增长小图、结论
export function expCard(state, row) {
  return h('a.exp-card', {href: `#/record/${encodeURIComponent(state.library)}/${encodeURIComponent(row.id)}`},
    h('div.exp-top', {}, h('span.exp-code', {}, row.code), row.status ? h('span.st-tag.' + (row.status === '已完成' ? 'done' : row.status.includes('设计') ? 'open' : 'current'), {}, row.status) : null,
      row.verdict ? h('span.st-tag', {}, row.verdict) : null, row.parent ? h('span.exp-parent', {}, '← ' + row.parent) : null),
    h('div.exp-title', {}, row.title),
    row.tags?.length ? h('div.exp-tags', {title: '设计因子（按因子规则从设计里抽出）'}, row.tags.map((tag) => h('span.factor-tag', {}, tag))) : null,
    h('div.exp-nums', {},
      h('span', {}, h('b', {}, row.yield != null ? row.yield.toFixed(1) : '—'), h('small', {}, row.yield != null ? ' % 产率' : ' 产率')),
      row.dry != null ? h('span', {}, h('b', {}, row.dry.toFixed(1)), h('small', {}, ' g 干品')) : null,
      row.temp ? h('span', {}, h('b', {}, `${row.temp}`), h('small', {}, ' °C'), row.hours ? [' × ', h('b', {}, row.hours), h('small', {}, ' h')] : null) : null),
    h('div.exp-gpc', {},
      row.growth.length >= 2 ? spark(row.growth, {width: 120, height: 34, color: 'var(--line-blue)'}) : h('span.spark-empty'),
      h('div', {}, row.mw_final ? h('div', {}, h('b', {}, Math.round(row.mw_final).toLocaleString('en-US')), h('small', {}, ' Mw')) : h('div.quiet', {}, '没有 GPC'),
        h('div.quiet.small', {}, `${row.samples} 个取样 · ${row.gpc} 个 GPC`))),
    row.conclusion ? h('div.exp-concl', {}, row.conclusion) : row.question ? h('div.exp-concl.q', {}, row.question) : null);
}

// 合成页：按项目分组（派生系列、你在旧 GPC 程序里建的项目、连续编号段），每组能收起；也能切成全部平铺
export function resinCards(state, rows, projects = []) {
  let filter = '', sort = 'code', grouped = projects.length > 0;
  const box = h('div');
  const byCode = Object.fromEntries(rows.map((row) => [row.code, row]));
  const match = (row) => !filter || [row.code, row.title, row.conclusion, row.question, ...(row.tags || [])].join(' ').toLowerCase().includes(filter);
  const sorted = (list) => sort === 'yield' ? list.slice().sort((a, b) => (b.yield ?? -1) - (a.yield ?? -1)) : sort === 'mw' ? list.slice().sort((a, b) => (b.mw_final ?? -1) - (a.mw_final ?? -1)) : list;
  // 项目标题拖着排先后、笔改名；实验卡拖到别的项目标题上就挪过去，拖到最下面"新建项目"就开一个
  const tools = projects.length ? projectTools({library: state.library, scope: '合成', names: () => projects.map((project) => project.name), after: () => state.render()}) : null;
  const entry = (row, group) => ({id: row.id, name: row.code, project: row.project || '', group});
  const draw = () => {
    if (!grouped) { box.replaceChildren(h('div.exp-cards', {}, sorted(rows.filter(match)).map((row) => expCard(state, row)))); return; }
    box.replaceChildren(h('p.grp-tip', {}, '拖项目标题排先后，点标题旁的笔改名；把实验卡拖到别的项目标题上就挪过去。'), ...projects.map((project, index) => {
      const list = sorted(project.codes.map((code) => byCode[code]).filter(Boolean).filter(match));
      if (!list.length) return null;
      const status = Object.entries(project.statuses).map(([key, count]) => h('span.st-tag.' + (key === '已完成' ? 'done' : key.includes('设计') ? 'open' : 'todo'), {}, `${key} ${count}`));
      const members = project.codes.map((code) => byCode[code]).filter(Boolean).map((row) => entry(row, project.name));
      return remember(h('details.exp-project', {},
        tools.head(h('summary', {}, h('b', {}, project.name), h('span.quiet.small', {}, `${list.length} 个实验 · ${project.samples} 个取样 · ${project.gpc} 个 GPC`), ...status),
          {name: project.name, mine: project.mine, items: members}),
        h('div.exp-cards', {}, list.map((row) => tools.item(expCard(state, row), [entry(row, project.name)])))), '合成', project.name, index < 2, !!filter);
    }), tools.newZone());
  };
  const search = h('input', {type: 'search', placeholder: '筛选编号、路线、结论、因子', oninput: () => { filter = search.value.trim().toLowerCase(); draw(); }});
  const sorter = h('select', {onchange: (event) => { sort = event.target.value; draw(); }}, [['code', '按编号'], ['yield', '按产率'], ['mw', '按末点 Mw']].map(([key, label]) => h('option', {value: key}, label)));
  const mode = h('div.segs', {}, [[true, '按项目'], [false, '全部平铺']].map(([value, label]) => h('button.seg' + (grouped === value ? '.on' : ''), {type: 'button', onclick: (event) => {
    grouped = value; mode.querySelectorAll('.seg').forEach((b) => b.classList.toggle('on', b === event.currentTarget)); draw(); }}, label)));
  draw();
  return h('div', {}, h('div.kv-tools', {}, h('label.search.small', {}, icon('search', 14), search), projects.length ? mode : null, sorter), box);
}

// ---------- 样品与数据 ----------

const FOLDED = {'树脂取样': '实验里的样品', '树脂批': '实验里的样品', '树脂物料': '物料', '原料': '物料', '预混液': '物料', '备料单': '物料',
  'GPC分析模板': '方法', 'GPC标准品批次': '方法', 'GPC标准曲线': '方法', 'GPC分子量分区': '方法', 'NK轮次': '整理用', 'NK分组': '整理用',
  '椭偏测试日': '整理用', '旧LAB记录': '历史', '旧工单汇总': '历史'};

export async function data(main, state, [kindName]) {
  if (!kindName || /^(exp|series|formula|gpc|other):/.test(kindName)) return projectsData(main, state, kindName, header, (text) => ask(state, text));
  if (kindName === 'kind') kindName = undefined;
  const {kinds} = await api(`/lib/${encodeURIComponent(state.library)}/kinds`);
  const groups = {}, folded = {};
  kinds.filter((kind) => kind.records && !EXPERIMENT_KINDS.includes(kind.name) && kind.name !== '课题').forEach((kind) =>
    FOLDED[kind.name] ? (folded[FOLDED[kind.name]] ||= []).push(kind) : (groups[kind.group || '其它'] ||= []).push(kind));
  const current = kindName || (groups['测试'] || Object.values(groups)[0] || [])[0]?.name;
  const link = (kind) => h('a' + (kind.name === current ? '.on' : ''), {href: '#/data/' + encodeURIComponent(kind.name)}, kind.name, h('small', {}, kind.records));
  const list = h('nav.kind-list', {'aria-label': '类型'}, h('a.pt-bykind', {href: '#/data'}, icon('projects', 14), '按项目看'),
    Object.entries(groups).map(([group, items]) => h('div.kind-group', {}, h('h3', {}, group), items.map(link))),
    Object.keys(folded).length ? h('details.kind-group.folded', {open: Object.values(folded).flat().some((kind) => kind.name === current)}, h('summary', {}, '物料、方法、整理用、历史'),
      Object.entries(folded).map(([group, items]) => [h('h3', {}, group), items.map(link)])) : null,
    h('button.btn.ghost.small', {type: 'button', onclick: () => ask(state, '我想加一类新的数据（说清是什么测试或记录、有哪些字段和单位），你帮我建好类型；有现成文件的话我拖进来，你导入。')}, icon('plus', 14), '新的一类'));
  if (!current) { main.replaceChildren(header('样品与数据', '还没有数据'), h('div.page-body', {}, h('p.quiet', {}, '导入文件或让助手建类型。'))); return; }
  state.anchor(`样品与数据 · ${current}`);
  main.replaceChildren(header('样品与数据', current), h('div.split', {}, list, h('div.split-main', {}, h('p.quiet', {}, '正在打开…'))));
  const {node, kind, total} = await kindView(state, current, {ask: (text) => ask(state, text)});
  main.replaceChildren(header('样品与数据', `${current}：${total} 条。${firstSentence(kind.about)}`,
    h('button.btn', {type: 'button', onclick: () => ask(state, `看一下「${current}」这一类数据，有什么值得注意的（异常、趋势、缺的）？`)}, icon('spark', 15), '让助手看这一类')),
  h('div.split', {}, list, h('div.split-main', {}, node)));
}

// ---------- 一条记录 ----------

const RECORD_TABS = {
  树脂实验: [['view', '总览'], ['design', '设计'], ['paper', 'SOP 与批记录'], ['samples', '样品与性能'], ['trace', '穿透'], ['fields', '详细信息']],
  配方: [['compose', '组成'], ['view', '性能'], ['orders', '工单'], ['trace', '穿透'], ['fields', '详细信息']],
  工单: [['weigh', '称量'], ['view', '计划和组成'], ['trace', '穿透'], ['fields', '详细信息']],
  GPC样品: [['baseline', '基线与区间'], ['view', '结果与分布'], ['trace', '穿透'], ['fields', '详细信息']],
  ...Object.fromEntries(['膜', 'NK分组', 'NK谱', '老化谱', '树脂批', '树脂取样'].map((kind) => [kind, [['view', '总览'], ['trace', '穿透'], ['fields', '详细信息']]])),
};

export async function record(main, state, [library, id, tab]) {
  const [item, view] = await Promise.all([api(`/lib/${encodeURIComponent(library)}/records/${encodeURIComponent(id)}?series=false`),
    api(`/present/${encodeURIComponent(library)}/${encodeURIComponent(id)}`)]);
  const tabs = RECORD_TABS[item.kind];
  tab = tab || tabs?.[0][0] || 'view';
  const NAV = {树脂合成: 'synthesis', 测试: 'testing', 配方: 'formulation', 课题: 'projects', 产出: 'outputs'};
  const nav = library === '知识库' ? 'knowledge' : NAV[item.group] || 'desk';
  document.querySelectorAll('.nav-item').forEach((link) => link.classList.toggle('on', link.dataset.page === nav));
  state.anchor(`${library} 的${item.kind}「${item.name}」（id ${item.id}）` + (tabs ? ` · ${tabs.find(([key]) => key === tab)?.[1] || ''}` : ''));
  const fieldsBox = h('div');
  const drawFields = (editing) => fieldsBox.replaceChildren(fieldEditor(state, library, item, editing, drawFields, tab === 'fields'));   // 详细信息页先给人看，点"修改"才变成输入框
  drawFields(false);
  const remove = async () => {
    await api(`/lib/${encodeURIComponent(library)}/records/${encodeURIComponent(item.id)}`, 'DELETE');
    toast(`「${item.name}」放进回收站了`, async () => { await api(`/lib/${encodeURIComponent(library)}/records/${encodeURIComponent(item.id)}/restore`, 'POST'); go('record', library, item.id); });
    history.back();
  };
  const picker = h('input', {type: 'file', multiple: true, hidden: true, onchange: async () => {
    for (const file of picker.files) await fetch(`/api/lib/${encodeURIComponent(library)}/records/${encodeURIComponent(item.id)}/files/${encodeURIComponent(file.name)}`, {method: 'PUT', body: file});
    toast(`${picker.files.length} 个文件挂上了`); render(main, state, library, id);
  }});
  const blocks = () => renderBlocks(view, {library, kind: item.kind,
    onAsk: () => ask(state, `改进「${item.kind}」这一类记录的呈现（现在看的是「${item.name}」）。先看现在的呈现缺什么、哪里不好用，提出改法，我同意后用 extend 写这一类的自定义呈现。`)});
  let body;
  if (tab === 'paper') body = await paperView(state, library, item);
  else if (tab === 'samples') {
    const page = await api(`/projects/${encodeURIComponent(library)}/${encodeURIComponent('exp:' + item.name)}`);
    body = renderBlocks({blocks: page.blocks.map((block, index) => ({...block, key: block.title || `${block.type}-${index}`})), hidden: [], source: '项目'}, {library, kind: '项目 · exp'});
  } else if (tab === 'orders') body = await ordersView(state, library, item);
  else if (tab === 'design') body = await designView(state, library, item);
  else if (tab === 'compose') body = await formulaEditor(state, library, item);
  else if (tab === 'baseline') body = await gpcAnalysis(state, item.id);
  else if (tab === 'weigh') body = h('div.view', {}, await weighingView(state, library, item));
  else if (tab === 'trace') body = await traceView(state, library, item.id);
  else if (tab === 'fields') body = fieldsBox;
  else body = h('div', {}, blocks(), tabs ? null : fieldsBox);
  const photos = (item.data || item)['现场照片'] || [];                       // 手机上拍的现场照片：排成一条小图，点开看大图
  if (photos.length && !['fields', 'trace'].includes(tab)) {
    const src = (photo) => `/api/lib/${encodeURIComponent(library)}/files/${encodeURIComponent(photo['照片'])}`;
    body = h('div', {}, h('section.block.wide.photo-strip', {}, h('header.block-head', {}, h('h3', {}, `现场照片（${photos.length}）`), h('span.quiet.small', {}, '手机现场页上拍的')),
      h('div.photo-row', {}, photos.slice().reverse().map((photo) => h('a.photo-item', {href: src(photo), target: '_blank', rel: 'noopener', title: photo['说明'] || ''},
        h('img', {src: src(photo), alt: photo['说明'] || '现场照片', loading: 'lazy'}), h('span', {}, `${(photo['时间'] || '').slice(5)} ${photo['谁'] && photo['谁'] !== '你' ? photo['谁'] + '：' : ''}${photo['说明'] || ''}`))))), body);
  }
  main.replaceChildren(header(item.name, `${item.kind} · ${library}`,
    h('div.head-actions', {},
      h('button.btn.primary', {type: 'button', onclick: () => ask(state, `解读这条记录：${item.kind}「${item.name}」。数据说明了什么，有没有异常，和同类比怎么样？`)}, icon('spark', 15), '解读'),
      h('button.btn', {type: 'button', onclick: () => ask(state, `把${item.kind}「${item.name}」和它前后的同类记录比一下，用表格和图。`)}, '和同类比'),
      h('button.btn', {type: 'button', onclick: () => ask(state, `给${item.kind}「${item.name}」出一份简短的 Word 报告，挂到这条记录上。`)}, '出报告'),
      testLink(library, item),
      h('button.btn.ghost.icon-btn', {type: 'button', title: '放进回收站', onclick: remove}, icon('trash', 17)))),
  tabs ? h('nav.tabs', {}, tabs.map(([key, label]) => h('a.tab' + (key === tab ? '.on' : ''), {href: `#/record/${encodeURIComponent(library)}/${encodeURIComponent(id)}/${key}`}, label))) : null,
  h('div.record', {},
    h('section.record-main', {},
      item.unconfirmed.length ? h('p.notice', {}, `待确认：${item.unconfirmed.join('、')}`) : null,
      item.note ? h('p.note-text', {}, item.note) : null,
      body),
    h('aside.record-side', {},
      h('h3', {}, '关联'), item.links.length || item.linked_from.length ? h('ul.links', {},
        item.links.map((link) => h('li', {}, h('span.rel', {}, link.relation + ' →'), linkTarget(library, link.to, link.to_name))),
        item.linked_from.slice(0, 60).map((link) => h('li', {}, h('span.rel', {}, '← ' + link.relation), linkTarget(library, link.from, link.from_name)))) : h('p.quiet', {}, '还没有关联'),
      h('h3', {}, '原始文件'), item.files.length ? item.files.map((file) => h('a.file-chip', {href: `/api/lib/${encodeURIComponent(library)}/files/${file.id}`, target: '_blank'}, icon('file', 14), file.path.split(/[\\/]/).pop())) : h('p.quiet', {}, '没有'),
      h('button.btn.small', {type: 'button', onclick: () => picker.click()}, icon('upload', 14), '挂文件'), picker,
      h('p.quiet.small', {}, `建立 ${item.created_at || ''}`, h('br'), `改动 ${item.updated_at || ''}`))));
}

async function render(main, state, library, id) { await record(main, state, [library, id]); }

// 测试类记录：直接跳到测试页里能拖、能勾的交互视图
function testLink(library, item) {
  const d = item.data;
  const href = item.kind === '膜' && d['测试日'] ? `#/testing/ell/${encodeURIComponent(d['测试日'])}`
    : item.kind === 'NK谱' ? `#/testing/nk/${encodeURIComponent(d['分组'] || item.name)}`
    : item.kind === 'NK分组' ? `#/testing/nk/${encodeURIComponent(item.name)}`
    : item.kind === '老化谱' && d['样品'] ? `#/testing/aging/${encodeURIComponent(d['样品'])}`
    : item.kind === 'GPC样品' ? `#/testing/gpc/${encodeURIComponent(item.id)}`
    : item.kind === 'GPC实验' ? `#/testing/gpc/${encodeURIComponent('实验:' + item.id)}` : null;
  return href ? h('a.btn', {href}, icon('testing', 15), item.kind === 'GPC实验' ? '打开实验演化' : '在测试页打开') : null;
}

// 详细信息：这条记录记了什么，写成人话。程序用的数据（版本、指纹、坐标、系数、英文结构）不出现；没填的收起来；点"修改"能直接改、存回库里。
function fieldEditor(state, library, item, editing, redraw, opened = false) {
  const shown = Object.entries(item.data).filter(([key, value]) => !technical(key, value));
  const flat = shown.filter(([, value]) => (value == null || typeof value !== 'object') && (editing || !empty(value)));
  const blank = shown.filter(([, value]) => empty(value) && !editing).length;
  const nested = shown.filter(([, value]) => value && typeof value === 'object' && !empty(value));
  const changes = {}, unsure = new Set(item.unconfirmed);
  const save = async () => {
    const row = {id: item.id, ...changes};
    if ([...unsure].sort().join() !== [...item.unconfirmed].sort().join()) row.unconfirmed = [...unsure];
    await api(`/lib/${encodeURIComponent(library)}/records`, 'POST', {kind: item.kind, rows: [row]});
    Object.assign(item.data, changes); item.unconfirmed = [...unsure];
    toast('字段已保存'); redraw(false);
  };
  const add = () => { const key = prompt('新的一项叫什么'); if (key) { item.data[key] = ''; redraw(true); } };
  const rows = flat.map(([key, value]) => {
    const flag = h('button.unsure-flag' + (unsure.has(key) ? '.on' : ''), {type: 'button', title: '标记 / 取消"待确认"', onclick: (event) => {
      unsure.has(key) ? unsure.delete(key) : unsure.add(key); event.currentTarget.classList.toggle('on'); if (!editing) save();
    }}, '待确认');
    const input = editing ? h('input', {type: 'text', value: value ?? '', oninput: (event) => {
      const text = event.target.value.trim();
      changes[key] = text === '' ? null : typeof value === 'number' || /^[-+]?\d*\.?\d+(e[-+]?\d+)?$/i.test(text) ? Number(text) : text;
    }}) : null;
    return [h('dt', {}, key), h('dd', {class: unsure.has(key) ? 'unsure-v' : null}, editing ? input : typeof value === 'number' ? (/大小/.test(key) ? fmt.size(value) : number(value)) : humanText(value), flag)];
  });
  // 列表：短的排成小标签（别名），长的一句一行；一组"键：值"排成一行一行；一串行排成小表
  const structured = nested.map(([key, value]) => {
    let body;
    if (Array.isArray(value) && value.every((v) => v == null || typeof v !== 'object')) {
      const items = value.filter((v) => !empty(v) && !looksId(v)).map(humanText);
      body = items.some((text) => text.length > 24) ? h('ul.detail-lines', {}, items.map((text) => h('li', {}, text)))
        : h('div.detail-chips', {}, items.map((text) => h('span.mini-chip', {}, text)));
    } else if (Array.isArray(value)) {
      const rows = value.filter((v) => v && typeof v === 'object');
      const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))].filter((column) => !technical(column, rows.find((row) => row[column] != null)?.[column]));
      body = h('div.tablewrap.flat', {}, h('table.data.compact', {}, h('thead', {}, h('tr', {}, columns.map((column) => h('th', {}, column)))),
        h('tbody', {}, rows.slice(0, 100).map((row) => h('tr', {}, columns.map((column) => h('td', {}, humanText(row[column]))))))));
    } else {
      body = h('dl.kv.detail-pairs', {}, Object.entries(value).filter(([inner, v]) => !technical(inner, v) && !empty(v)).flatMap(([inner, v]) => [h('dt', {}, inner), h('dd', {}, humanText(v))]));
    }
    return h('div.detail-group', {}, h('h4', {}, key), body);
  });
  return h('details.block.fields-block', {open: editing || opened},
    h('summary', {}, h('b', {}, '详细信息'), h('span.quiet', {}, ` ${flat.length + nested.length} 项` + (blank ? ` · 另有 ${blank} 项没填` : ''))),
    h('div.field-tools', {}, editing ? [h('button.btn.primary.small', {type: 'button', onclick: save}, '保存'), h('button.btn.small', {type: 'button', onclick: add}, icon('plus', 13), '加一项'),
      h('button.btn.ghost.small', {type: 'button', onclick: () => redraw(false)}, '不改了')] : h('button.btn.small', {type: 'button', onclick: () => redraw(true)}, icon('edit', 13), blank ? '修改 / 补没填的' : '修改')),
    h('dl.kv.fields-kv', {}, rows),
    structured);
}

function linkTarget(library, id, name) {
  const cross = /^([^:\/]{2,}):(.+)$/.exec(id);                // 别的库里的记录写成"库:id"
  if (cross) return h('a', {href: `#/record/${encodeURIComponent(cross[1])}/${encodeURIComponent(cross[2])}`}, name || cross[2]);
  return h('a', {href: `#/record/${encodeURIComponent(library)}/${encodeURIComponent(id)}`}, name || id);
}

// ---------- 搜索 ----------

export async function search(main, state, [q]) {
  const {rows, total} = await api(`/lib/${encodeURIComponent(state.library)}/records?q=${encodeURIComponent(q)}&limit=200`);
  state.anchor(`搜索「${q}」`);
  main.replaceChildren(header(`搜索：${q}`, `${total} 条`), h('div.page-body', {},
    table(['类型', '名字', '改动'], rows.map((row) => [row.kind, row.name, row.updated_at]), (index) => go('record', state.library, rows[index].id))));
}

// ---------- 知识 ----------

// 命题：按状态分组（待验证 / 成立 / 否定），把握条、证据和出处几条、属于哪些课题；上面是这个库预测的命中情况
async function claimsPage(state) {
  const {claims, tally} = await api('/claims/' + encodeURIComponent(state.library));
  const judged = (tally['中'] || 0) + (tally['部分'] || 0) + (tally['没中'] || 0);
  const TONE = {成立: 'done', 待验证: 'open', 否定: 'off'};
  const bar = (value) => value == null || value === '' ? null : h('span.sure', {}, h('span.sure-track', {}, h('i', {style: `width:${Math.max(0, Math.min(100, Number(value)))}%`})), h('small', {}, `把握 ${value}%`));
  const group = (state_) => {
    const list = claims.filter((claim) => claim['状态'] === state_);
    return list.length ? h('section.claim-group', {}, h('h4', {}, `${state_}（${list.length}）`), h('ul.claim-list', {}, list.map((claim) => h('li', {},
      h('span.st-tag.' + (TONE[claim['状态']] || 'todo'), {}, claim['状态']), h('a', {href: `#/record/${encodeURIComponent('知识库')}/${encodeURIComponent(claim.id)}`}, claim['陈述']), bar(claim['把握']),
      h('small.quiet', {}, [claim['证据'] ? `证据 ${claim['证据']}` : '', claim['出处'] ? `出处 ${claim['出处']}` : '', ...claim['课题']].filter(Boolean).join(' · ') || '还没有证据'))))) : null;
  };
  return h('div.view.claims-page', {},
    h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '预测命中')),
      judged || tally['等结果'] ? h('div.stat-row', {}, [['中', 'good'], ['部分', 'warn'], ['没中', 'bad'], ['等结果', '']].map(([key, tone]) => h('div.stat' + (tone ? '.' + tone : ''), {},
        h('div.stat-label', {}, key), h('div.stat-value', {}, String(tally[key] || 0)))),
        judged ? h('div.stat', {}, h('div.stat-label', {}, '命中率'), h('div.stat-value', {}, `${Math.round(((tally['中'] || 0) + 0.5 * (tally['部分'] || 0)) / judged * 100)}%`), h('div.stat-note', {}, '部分算半个')) : null)
        : h('p.quiet', {}, '还没有预测。做实验之前在课题页上"给下一个实验写预测"，结果进来后助手会核对。')),
    claims.length ? ['待验证', '成立', '否定'].map(group) : h('p.quiet', {}, '还没有命题。在课题页上点"让助手调研"，结论会写成命题挂到课题上。'));
}

export async function knowledge(main, state, [kindName, ref]) {
  const [{kinds}, notes] = await Promise.all([api('/lib/' + encodeURIComponent('知识库') + '/kinds'), api('/texts/notes')]);
  const current = kindName || 'notes';
  state.anchor(`知识 · ${current === 'notes' ? '笔记' : current}`);
  if (current === '约定口径') { location.hash = '#/agent/memory'; return; }      // 搬到"助手 → 记住的规矩"了
  const tabs = h('nav.tabs', {}, h('a.tab' + (current === 'notes' ? '.on' : ''), {href: '#/knowledge'}, '笔记', h('small', {}, notes.items.length)),
    kinds.filter((kind) => !['文献线索', '学习', '练习记录'].includes(kind.name)).map((kind) => h('a.tab' + (kind.name === current ? '.on' : ''), {href: '#/knowledge/' + encodeURIComponent(kind.name)}, kind.name, h('small', {}, kind.records))),
    h('a.tab' + (current === '学习' || current === '练习记录' ? '.on' : ''), {href: '#/knowledge/' + encodeURIComponent('学习')}, '学习', h('small', {}, (kinds.find((kind) => kind.name === '学习') || {}).records || 0)));
  let body;
  if (current === 'notes') {
    const editor = h('div.note-editor');
    body = h('div.split', {}, h('nav.kind-list', {}, h('div.kind-group', {}, notes.items.map((note) => h('a', {href: '#', onclick: (event) => { event.preventDefault(); openNote(editor, note.name); }}, note.name)),
      h('button.btn.ghost', {type: 'button', onclick: () => { const name = prompt('笔记名'); if (name) openNote(editor, name, true); }}, icon('plus', 14), '新笔记'))), h('div.split-main', {}, editor));
    if (notes.items[0]) openNote(editor, notes.items[0].name); else editor.append(h('p.quiet', {}, '还没有笔记。调研、学习得出的结论会记在这里，你也可以自己写。'));
  } else if (current === '命题') {
    body = await claimsPage(state);
  } else if (current === '文献' || current === '文献线索') {
    body = await literaturePage(state);
  } else if (current === '学习' || current === '练习记录') {
    body = await learnPage(main, state, ref);
  } else {
    const {node} = await kindPage(main, state, '知识库', current);
    body = node;
    body.querySelectorAll('tbody tr').forEach((row) => row.addEventListener('click', () => {}, {once: true}));
  }
  main.replaceChildren(header('知识', '文献、命题、推理链、笔记：调研和学习留下的东西，几个产品共用。',
    h('button.btn', {type: 'button', onclick: () => ask(state, '我想调研一个问题：', '知识')}, '开始调研')), tabs, h('div.page-body', {}, body));
}

async function openNote(editor, name, fresh) {
  const note = fresh ? {name, content: ''} : await api('/texts/notes/' + encodeURIComponent(name));
  const area = h('textarea.note-area', {}, note.content);
  const view = h('div.md.note-view', {html: md(note.content)});
  const saved = h('span.quiet');
  editor.replaceChildren(h('div.note-head', {}, h('h2', {}, name), saved,
    h('button.btn', {type: 'button', onclick: () => { area.hidden = !area.hidden; view.hidden = !view.hidden; }}, '改 / 看'),
    h('button.btn.primary', {type: 'button', onclick: async () => { await api('/texts/notes/' + encodeURIComponent(name), 'PUT', {content: area.value}); view.innerHTML = md(area.value); saved.textContent = '已保存'; }}, '保存')),
  view, area);
  area.hidden = true;
}

// ---------- 产出 ----------

export async function outputs(main, state) {
  const [{files}, pages] = await Promise.all([api('/outputs/' + encodeURIComponent(state.library)), api('/texts/pages')]);
  state.anchor('产出');
  const preview = h('div.preview');
  const show = async (url, name) => {
    const download = h('a.btn.small', {href: url, download: name}, icon('download', 14), '下载');
    if (/\.(png|jpe?g|svg|webp|gif)$/i.test(name)) { preview.replaceChildren(h('div.preview-bar', {}, h('b', {}, name), download), h('img', {src: url, alt: name})); return; }
    if (/\.(pdf|html?)$/i.test(name)) { preview.replaceChildren(h('div.preview-bar', {}, h('b', {}, name), h('a.btn.small', {href: url, target: '_blank'}, icon('open', 14), '新窗口打开'), download), h('iframe', {src: url, title: name})); return; }
    if (/\.(xlsx|xlsm|csv|tsv|docx)$/i.test(name)) {               // Excel、CSV、Word：在页面上直接排出来
      preview.replaceChildren(h('p.quiet', {}, '正在读…'));
      const data = await api(url.replace(/^\/api/, '') + '/preview');
      const {tabs, body} = previewContent(data);                     // 和导出预览同一套排法
      preview.replaceChildren(h('div.preview-bar', {}, h('b', {}, name), tabs, download), body);
      return;
    }
    preview.replaceChildren(h('div.preview-bar', {}, h('b', {}, name), download), h('p.quiet', {}, '这种文件在页面上看不了，请下载后打开。'));
  };
  main.replaceChildren(header('产出', '助手和你做出来的报告、PPT、表格、图，以及助手写的页面。'),
    h('div.split', {}, h('nav.kind-list', {}, h('div.kind-group', {}, h('h3', {}, '文件'), files.length ? files.map((file) => {
      const url = `/api/outputs/${encodeURIComponent(state.library)}/${encodeURIComponent(file.name)}`;
      return h('a', {href: '#', onclick: (event) => { event.preventDefault(); show(url, file.name); }}, file.name, h('small', {}, fmt.size(file.size)));
    }) : h('p.quiet', {}, '还没有')), h('div.kind-group', {}, h('h3', {}, '页面'), pages.items.length ? pages.items.map((page) =>
      h('a', {href: '#', onclick: (event) => { event.preventDefault(); show('/pages/' + encodeURIComponent(page.name) + '.html', page.name + '.html'); }}, page.name)) : h('p.quiet', {}, '还没有'))),
    h('div.split-main', {}, preview)));
  preview.append(h('p.quiet', {}, '在左边选一个看。'));
}

// ---------- 助手 ----------

export async function assistant(main, state, [laneId]) {
  const {lanes} = await api('/lanes');
  state.anchor('助手');
  const list = h('nav.lane-list', {}, h('button.btn.primary', {type: 'button', onclick: () => go('assistant')}, icon('plus', 15), '新对话'),
    lanes.map((lane) => h('a' + (lane.id === laneId ? '.on' : ''), {href: '#/assistant/' + encodeURIComponent(lane.id)},
      h('span.lane-title', {}, lane.title), h('small', {}, (lane.running ? '正在做 · ' : lane.card ? '等你回答 · ' : lane.unread ? '有新结果 · ' : '') + (lane.updated || '').slice(5, 16)))));
  const host = h('div.lane-main');
  main.replaceChildren(h('div.assistant-page', {}, list, host));
  state.panel.mount(host, laneId);
}

// ---------- 设置 ----------

export async function settings(main, state, [tab = ''] = []) {
  if (tab === 'rules') { location.hash = '#/agent/memory'; return; }           // 业务规矩是给助手用的，搬到"助手 → 记住的规矩"
  const nav = settingsTabs('');
  const data = await api('/state');
  state.anchor('设置');
  const value = data.settings;
  // 页面上的每个框都绑着 value 里的这些对象：存完只把服务端算的（有没有密钥、价表）补回去，不能整个换掉——换掉以后再改的都存不上（11-11 改模型存不住就是这个）
  const save = async () => {
    const fresh = await api('/settings', 'PUT', value);
    (fresh.profiles || []).forEach((item) => { const mine = value.profiles.find((one) => one.id === item.id); if (mine) Object.assign(mine, {has_key: item.has_key, known_price: item.known_price}); });
    saved.textContent = '已保存'; setTimeout(() => saved.textContent = '', 1500);
  };
  const redraw = () => settings(main, state, [tab]);
  const saved = h('span.quiet');
  const profiles = h('div.profiles', {}, value.profiles.map((profile) => profileForm(profile, value, data, save, redraw)));
  const askFirst = h('textarea', {rows: 5, oninput: () => { value.ask_first = askFirst.value.split('\n').map((line) => line.trim()).filter(Boolean); clearTimeout(askFirst.t); askFirst.t = setTimeout(save, 500); }}, value.ask_first.join('\n'));
  main.replaceChildren(header('设置', '模型接口、通知和推送、先问清单。', saved), nav, h('div.page-body.settings', {},
    h('section', {}, h('h2', {}, '模型接口'), h('p.quiet', {}, '本机网关上的模型都可以收研发库的真实数据。别的接口要发真实数据，先问清楚。'), profiles, newProfile(value, save, redraw)),
    pushSection(value, save),
    (() => { const box = h('section.block.wx-section'); import('./wechat.js').then(({wechatBox}) => wechatBox(box, {self: true})); return box; })(),
    h('section', {}, h('h2', {}, '先问我再做'), h('p.quiet', {}, '一行一条。清单里的事助手先发卡片问你，其它的直接做完。'), askFirst),
    h('section', {}, h('h2', {}, '规则和定时'), h('p.quiet', {}, '晨报、复盘、文献监测、文件夹自动导入这些主动干的活，在 ', h('a', {href: '#/agent/rules'}, '助手 → 规则和定时'), ' 里看和改。'))));
}

// 桌面通知：不在工作台前时，卡片和通知在 Windows 右下角弹出来
function pushSection(value, save) {
  value.push = value.push || {};
  const box = (key, label, hint) => h('label.check', {title: hint}, h('input', {type: 'checkbox', checked: value.push[key] !== false,
    onchange: (event) => { value.push[key] = event.target.checked; save(); }}), label);
  return h('section', {}, h('h2', {}, '桌面通知'),
    h('p.quiet', {}, '你不在工作台前的时候（页面不在前台超过 45 秒），这些事在 Windows 右下角弹一下，点一下直接打开那一页。你在看工作台时不弹。'),
    h('div.row', {}, box('卡片', '要你拍板的卡片', '助手发卡片等你定'), box('通知', '助手的通知', '晨报写好、导入完成、后台的活做完'),
      h('button.btn.small', {type: 'button', onclick: async () => { await api('/push/test', 'POST'); toast('发了一条试试，看右下角'); }}, '试一下')),
    phoneSection(value, save));
}

// 手机推送：同样这些事也推到手机。推送入口他自己建（群机器人、Bark、ntfy……），地址存在 auth.json，页面上只显示"设好了没有"
function phoneSection(value, save) {
  const phone = value.phone_push || {};
  const address = h('input', {type: 'password', placeholder: phone.set ? '已设好；要换就贴新地址' : '贴推送地址：群机器人 webhook / Bark / ntfy 主题地址 / Server酱', style: 'flex:1;min-width:280px'});
  const state = h('span.quiet.small', {}, phone.set ? `现在发到：${phone.channel || '推送地址'}` : '还没设：不会往外发任何东西');
  const link = h('input', {type: 'text', value: value.push.link || '', placeholder: '手机上点开工作台的地址（可不填），例 https://你的域名/lab', style: 'flex:1;min-width:280px',
    onchange: (event) => { value.push.link = event.target.value.trim(); save(); }});
  return h('div.phone-push', {}, h('h3', {}, '手机推送'),
    h('p.quiet', {}, '同样的卡片和通知，你不在电脑前时也推到手机。推送入口你自己建一个，把地址贴在这里：企业微信、钉钉、飞书的群机器人（在群设置里加"机器人"，复制 webhook 地址），iPhone 装 Bark，安卓可以用 ntfy，或 Server酱推到微信。地址里带着密钥，只存在本机的 auth.json。'),
    h('div.row', {}, address, h('button.btn.small', {type: 'button', onclick: async () => {
      if (!address.value.trim() && !confirm('地址是空的：要清掉现在的推送地址吗？')) return;
      await api('/key', 'PUT', {profile: 'push-phone', key: address.value.trim()}); address.value = '';
      const fresh = await api('/state'); value.phone_push = fresh.settings.phone_push;
      state.textContent = value.phone_push.set ? `现在发到：${value.phone_push.channel || '推送地址'}` : '已清掉：不会往外发';
      toast(value.phone_push.set ? '推送地址存好了' : '推送地址清掉了');
    }}, '存'), state),
    h('div.row', {}, link),
    h('div.row', {}, h('label.check', {}, h('input', {type: 'checkbox', checked: value.push.phone !== false, onchange: (event) => { value.push.phone = event.target.checked; save(); }}), '推到手机'),
      h('button.btn.small', {type: 'button', onclick: async () => { const result = await api('/push/phone-test', 'POST'); toast(result.note); }}, '推一条试试')));
}

function profileForm(profile, value, data, save, redraw) {
  const field = (label, input, hint) => h('label.form-row', {}, h('span', {}, label), input, hint ? h('small', {}, hint) : null);
  const text = (key, type = 'text') => h('input', {type, value: profile[key] ?? '', oninput: (event) => { profile[key] = type === 'number' ? Number(event.target.value) : event.target.value; clearTimeout(profile.t); profile.t = setTimeout(save, 500); }});
  const select = (key, options) => { const node = h('select', {onchange: (event) => { profile[key] = event.target.value === 'true' ? true : event.target.value === 'false' ? false : event.target.value; save(); }},
    options.map(([key2, label]) => h('option', {value: key2, selected: String(profile[key]) === String(key2)}, label))); return node; };
  const models = h('datalist', {id: 'm-' + profile.id});
  const modelInput = text('model'); modelInput.setAttribute('list', 'm-' + profile.id);
  api('/models?profile=' + encodeURIComponent(profile.id)).then((found) => models.replaceChildren(...found.models.map((name) => h('option', {value: name})))).catch(() => {});
  const key = h('input', {type: 'password', placeholder: profile.has_key ? '已存（不显示），要换就填新的' : '粘贴密钥', autocomplete: 'off'});
  return h('details.profile', {open: profile.id === value.active},
    h('summary', {}, h('b', {}, profile.name), profile.id === value.active ? h('span.state-tag', {}, '正在用') : h('button.btn.small', {type: 'button', onclick: (event) => { event.preventDefault(); value.active = profile.id; save().then(redraw); toast('助手改用 ' + profile.name); }}, '用这个')),
    h('div.form', {}, field('名称', text('name')), field('地址', text('base_url'), '写到 /v1 为止'),
      field('格式', select('format', Object.entries(data.formats))), field('密钥', select('auth', [['none', '不用密钥'], ['key', '要密钥']])),
      profile.auth === 'key' ? field('', h('div.row', {}, key, h('button.btn', {type: 'button', onclick: async () => { if (key.value.trim()) { await api('/key', 'PUT', {profile: profile.id, key: key.value}); key.value = ''; key.placeholder = '已存（不显示）'; } }}, '保存密钥')), '只存在这台电脑的 auth.json') : null,
      field('模型', h('div.row', {}, modelInput, models)), field('思考深度', select('effort', data.efforts.map((item) => [item, item || '默认']))),
      field('能看图', select('images', [['true', '能（图片、渲染页会送给模型看）'], ['false', '不能']])),
      field('显示思考', select('thinking', [['true', '要'], ['false', '不要']])),
      field('上下文预算', text('context_budget', 'number'), 'token'), field('最大输出', text('max_output', 'number'), 'token'),
      field('价格', h('div.row', {}, text('price_in'), text('price_out')), '输入 / 输出，每百万 token 多少美元；空着就按内置价表'),
      profile.id !== value.active ? h('button.btn.ghost', {type: 'button', onclick: () => { value.profiles = value.profiles.filter((item) => item !== profile); save().then(redraw); }}, '删掉这个接口') : null));
}

function newProfile(value, save, redraw) {
  return h('button.btn', {type: 'button', onclick: async () => {
    value.profiles.push({id: 'p-' + Math.random().toString(36).slice(2, 8), name: '新接口', base_url: '', format: 'openai', auth: 'key', model: '', extra_models: [],
      context_budget: 200000, max_output: 16000, effort: '', thinking: false, images: true, price_in: '', price_out: ''});
    await save(); redraw();
  }}, icon('plus', 14), '加一个接口');
}
