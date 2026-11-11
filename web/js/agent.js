// 助手：对话（带上下文面板）、技能、工具和连接（含 MCP）、规则和定时、跑过的轮、改进。让人看到智能体的全貌。
import {api, fmt, go, h, icon, listen, md, toast} from './core.js';
import {COLORS, chart, refChips} from './blocks.js';

const enc = encodeURIComponent;
const TABS = [['chat', '对话', 'assistant'], ['team', '团队', 'layers'], ['skills', '技能', 'skill'], ['memory', '记住的规矩', 'check'], ['learn', '跟你学', 'chart'], ['tools', '工具和连接', 'tool'], ['rules', '规则和定时', 'rule'], ['runs', '跑过的轮', 'runs'], ['improve', '改进', 'spark']];

// 跟你学：你做的判断助手都记下来当示范，攒够了让它归纳成做法，你同意了才写进技能
const LEARN_HINT = {
  'GPC 基线和区间': '在 测试 → GPC 里拖锚点、改区间、换基线连接方式并存下，每次记一条。库里已有样品的基线和区间（你过去在旧软件里定的）也能直接拿来学。',
  '椭偏剔点': '在 测试 → 椭偏膜厚 里剔掉或放回读数时记一条（连同原因）。',
  '椭偏参比': '给样品组换参比组时记一条。',
  'n/k 纳入': '在 测试 → n/k 里勾掉或勾上"纳入均值""计入重复性"时记一条。',
  '配方修改': '在配方的组成编辑器里改了存下时记一条。',
  '改记录': '在记录的"详细信息"里改了值时记一条。',
  '确认待确认': '把"待确认"的值确认掉时记一条（比如助手联网补的物性）。',
};
export async function learnPage(main, state) {
  state.anchor('助手 · 跟你学');
  const library = state.library;
  const data = await api(`/demos/${enc(library)}`);
  const all = async (kind, box) => {
    const {items} = await api(`/demos/${enc(library)}?kind=${enc(kind)}`);
    box.replaceChildren(h('ul.learn-all', {}, items.map((item) => h('li' + (item.used ? '.used' : ''), {}, h('small.quiet', {}, item.at.slice(5, 16)), ' ', item.text,
      item.used ? h('span.mini-chip', {}, '已学进「' + item.used + '」') : null))));
  };
  const card = (item) => {
    const more = h('div');
    const learnable = item.fresh || item.kind === 'GPC 基线和区间';
    return h('section.learn-card', {},
      h('div.lc-head', {}, h('b', {}, item.kind), h('span.grow'), h('span.lc-count', {}, item.total ? `${item.total} 次` : '还没有'),
        item.fresh ? h('span.mini-chip', {}, `${item.fresh} 次还没学`) : null),
      item.recent.length ? h('ul.learn-recent', {}, item.recent.map((text) => h('li', {}, text))) : h('p.quiet.small', {}, LEARN_HINT[item.kind] || ''),
      h('div.lc-actions', {},
        item.total ? h('button.btn.ghost.small', {type: 'button', onclick: () => all(item.kind, more)}, '看全部') : null, h('span.grow'),
        learnable ? h('button.btn.primary.small', {type: 'button', onclick: async () => {
          const {lane} = await api(`/demos/${enc(library)}/learn`, 'POST', {kind: item.kind});
          toast('助手开始归纳，有结论会发卡片问你'); state.panel.open(lane);
        }}, icon('spark', 13), item.fresh ? '让助手归纳' : '从历史样品里学') : null),
      more);
  };
  shell(main, state, 'learn', '跟你学', '你做的判断都记成示范；攒够了让助手归纳成做法，你同意了才写进技能', h('div.page-body', {},
    h('p.quiet', {}, '只记学得到东西的判断：改前、改后、当时的情况。点击和翻页不记。助手归纳出的做法先发卡片问你，你同意了才写进技能；之后新样品进来，它照学到的做法先替你做好，你看一眼确认或改（下一步做）。'),
    h('div.learn-grid', {}, data.kinds.map(card)),
    h('p.quiet.small', {}, `另外，你在卡片上定过的 ${data.decided} 件事在"记住的规矩 → 定过的事"里，同一件事助手不会再问。`)));
}

// 助手记住的规矩：你定过的口径（每次干活先读）+ 从旧程序搬来的命名、别名、容差这些业务规矩。
// 这些是给助手用的，平时不用管；要改直接跟助手说（"以后默认参比用 REF01"），它会改这里。
export async function memoryPage(main, state) {
  state.anchor('助手 · 记住的规矩');
  const [{conventions}, {businessRules}] = await Promise.all([import('./stages.js'), import('./rules.js')]);
  const rulesBox = h('div');
  const more = h('details.memory-more', {ontoggle: async (event) => { if (event.target.open && !rulesBox.childElementCount) rulesBox.append(await businessRules(state)); }},
    h('summary', {}, '业务规矩：从旧程序搬来的命名规则、别名、容差、默认条件（导入、体检、计算按它们走）'), rulesBox);
  shell(main, state, 'memory', '记住的规矩', '助手干活前先读这些；你不用记，要改直接跟它说', h('div.page-body', {},
    h('p.quiet', {}, '这里是助手自己的记性：你定过的口径，它每次干活都会先读、照着做、不再问。要改不用来这里，直接在对话里说一句，它会改好并告诉你改了什么。'),
    await conventions(state), await decidedList(state), more, await promptEditor()));
}

// 助手的系统提示词：看得见、能改。三处 {ask_first}{skills}{mcp} 由程序填（先问清单、技能目录、接进来的服务器和规矩）；改了下一轮就用
async function promptEditor() {
  const data = await api('/prompt');
  const text = h('textarea.prompt-edit', {rows: 24, spellcheck: false}, data.text);
  const state = h('span.quiet.small', {}, data.custom ? '现在用的是你改过的' : '现在用的是内置的');
  const save = async (value) => { const got = await api('/prompt', 'PUT', {text: value}); text.value = got.text; state.textContent = got.custom ? '已存：下一轮起用你改的' : '已回到内置的'; toast(state.textContent); };
  return h('details.memory-more.prompt-box', {},
    h('summary', {}, '助手的系统提示词（它每次干活前先读的那段话）'),
    h('p.quiet.small', {}, '花括号里的三处由程序填：{ask_first} 是"先问我再做"清单，{skills} 是技能目录，{mcp} 是接进来的服务器、约定口径、定过的事、作图偏好。别的地方随便改；改坏了点"恢复内置的"。'),
    text, h('div.row', {}, h('button.btn.primary.small', {type: 'button', onclick: () => save(text.value)}, icon('check', 13), '存'),
      h('button.btn.small', {type: 'button', onclick: () => save('')}, '恢复内置的'), state),
    h('details.prompt-full', {}, h('summary', {}, '看它最后拼成的全文（主助手）'), h('pre.prompt-full-text', {}, data.full)));
}

// 定过的事：他在卡片上答过的、点过"不用问了"的、助手结案的。助手再遇到同一件事照着做，不再问。
async function decidedList(state) {
  const library = state.library;
  const {items} = await api(`/decisions/${enc(library)}`);
  const box = h('section.decided');
  const change = async (item, action) => { await api(`/decisions/${enc(library)}/${enc(item.id)}`, 'POST', {action}); toast(action === 'rule' ? '记进约定口径了' : '撤销了，助手下次遇到会再问你'); box.replaceWith(await decidedList(state)); };
  const TONE = {定了: 'done', 暂缓: 'open', 不用问: 'todo', 结案: 'todo', 撤销: 'off'};
  box.append(h('h3', {}, `定过的事（${items.length}）`),
    h('p.quiet', {}, '你在卡片上答过的、点过"不用问了"的，助手在别处解决后结案的。同一件事助手不会再问；要它重新问，点"重新问"。'),
    items.length ? h('div.decided-list', {}, items.map((item) => h('div.decided-item' + (item['状态'] === '撤销' ? '.off' : ''), {},
      h('div.di-head', {}, h('span.st-tag.' + (TONE[item['状态']] || 'todo'), {}, item['状态'] || '定了'), h('b', {}, item['问题'] || item.name),
        h('span.grow'), h('small.quiet', {}, (item['时间'] || '') + (item['来自对话'] ? ' · ' : '')),
        item['来自对话'] ? h('a.small', {href: '#/assistant/' + enc(item['对话id'] || '')}, item['来自对话']) : null),
      h('div.di-answer', {}, '→ ', item['回答'] || ''),
      (item['关于'] || []).length ? refChips(item['关于']) : null,
      (item['依据'] || []).length ? h('details.di-ev', {}, h('summary', {}, '当时的依据'), h('ul.decision-ev', {}, item['依据'].map((line) => h('li.md', {html: md(line)})))) : null,
      h('div.di-actions', {}, item['状态'] !== '撤销' ? h('button.btn.ghost.small', {type: 'button', onclick: () => change(item, 'reopen')}, '重新问') : null,
        !item['规矩'] && item['状态'] === '定了' ? h('button.btn.ghost.small', {type: 'button', title: '以后同类的事都照这个做', onclick: () => change(item, 'rule')}, '设为规矩') : item['规矩'] ? h('span.mini-chip', {}, '已是规矩') : null))))
      : h('p.quiet', {}, '还没有。'));
  return box;
}
const TOOL_LABEL = {overview: '看全局', skill: '读技能', find: '查找', records: '研发库', sql: '查库', sci: '科学计算', files: '文件', look: '看图', python: 'Python', pip: '装包',
  shell: '命令行', web: '查资料', chart: '画图', export: '出文件', outputs: '登记产出', notes: '笔记', extend: '改平台', ask_user: '问你', later: '约复查', notify: '通知', delegate: '分身', plan: '计划', cards: '卡片', show: '展示', today: '看今天', brief: '写简报', literature: '文献', lesson: '学习'};

function shell(main, state, tab, title, sub, body, extra) {
  main.replaceChildren(h('header.page-head', {}, h('div.page-title', {}, h('h1', {}, '助手'), h('p.page-sub', {}, sub)), extra || null),
    h('nav.tabs', {}, TABS.map(([key, label, name]) => h('a.tab' + (key === tab ? '.on' : ''), {href: key === 'chat' ? '#/assistant' : '#/agent/' + key}, icon(name, 15), label))),
    body);
}

// ---------- 对话 + 上下文 ----------

export async function chatPage(main, state, laneId) {
  const {lanes} = await api('/lanes');
  state.anchor('助手');
  const list = h('nav.lane-list', {}, h('button.btn.primary', {type: 'button', onclick: () => go('assistant')}, icon('plus', 15), '新对话'),
    lanes.map((lane) => h('a' + (lane.id === laneId ? '.on' : ''), {href: '#/assistant/' + enc(lane.id)},
      h('span.lane-title', {}, (lane.rule ? '⏱ ' : '') + lane.title), h('small', {}, (lane.running ? '正在做' + (lane.plan?.length ? ` ${lane.plan.filter((step) => step.state === '做完').length}/${lane.plan.length}` : '') + ' · ' : lane.card ? '等你回答 · ' : lane.unread ? '有新结果 · ' : '') + (lane.updated || '').slice(5, 16)))));
  const host = h('div.lane-main'), side = h('aside.ctx-side');
  shell(main, state, 'chat', '助手', `${lanes.length} 段对话。右边是这段对话里助手手上有什么。`, h('div.assistant-page.with-ctx', {}, list, host, side));
  let current = laneId;
  state.panel.mount(host, laneId, (id) => { current = id; drawContext(side, id); });
  drawContext(side, laneId);
  const stop = listen((event) => { if (event.lane === current && event.type === 'status' && event.state === 'idle') drawContext(side, current); });
  window.addEventListener('hashchange', stop, {once: true});
}

export async function drawContext(side, laneId) {
  if (!laneId) { side.replaceChildren(h('div.ctx', {}, h('h3', {}, '上下文'), h('p.quiet', {}, '开始对话后，这里显示助手手上有什么：读了哪些技能、用了哪些工具、上下文用了多少、做出了哪些文件。'))); return; }
  const ctx = await api(`/lanes/${enc(laneId)}/context`).catch(() => null);
  if (!ctx) return;
  const total = ctx.parts.reduce((sum, part) => sum + part.tokens, 0) || 1;
  const pct = Math.min(100, Math.round(ctx.used / ctx.budget * 100));
  side.replaceChildren(h('div.ctx', {},
    h('h3', {}, icon('layers', 15), ' 上下文'),
    h('div.ctx-meter', {title: `${ctx.used.toLocaleString()} / ${ctx.budget.toLocaleString()} token`}, ctx.parts.map((part, index) =>
      h('span', {style: `width:${part.tokens / ctx.budget * 100}%;background:${COLORS[index % COLORS.length]}`, title: `${part.name}：约 ${part.tokens.toLocaleString()} token`}))),
    h('div.ctx-num', {}, h('b', {}, `${pct}%`), ` · ${ctx.used.toLocaleString()} / ${ctx.budget.toLocaleString()} token`, h('small', {}, ctx.measured ? '（上次实测，分项按字数估）' : '（按字数估）')),
    h('ul.ctx-parts', {}, ctx.parts.map((part, index) => h('li', {}, h('i', {style: `background:${COLORS[index % COLORS.length]}`}), part.name, h('span', {}, `${Math.round(part.tokens / total * 100)}%`)))),
    ctx.compacted ? h('details.ctx-sum', {}, h('summary', {}, '已压缩过：看摘要'), h('div.md', {html: md(ctx.summary)})) : null,
    h('dl.kv.ctx-kv', {}, h('dt', {}, '模型'), h('dd', {}, `${ctx.profile} · ${ctx.model}`), h('dt', {}, '产品库'), h('dd', {}, ctx.library),
      ctx.anchor ? [h('dt', {}, '在看'), h('dd', {}, ctx.anchor)] : null,
      h('dt', {}, '用量'), h('dd', {}, `${ctx.usage.calls} 次 · 进 ${(ctx.usage.input + ctx.usage.cache_read + ctx.usage.cache_write).toLocaleString()} · 出 ${ctx.usage.output.toLocaleString()}` + (ctx.usage.cost ? ` · $${ctx.usage.cost.toFixed(3)}` : ''))),
    h('h4', {}, '读过的技能'), ctx.skills.length ? h('div.linked', {}, ctx.skills.map((key) => h('a.mini-chip', {href: '#/agent/skills/' + enc(key)}, key))) : h('p.quiet.small', {}, '还没读'),
    h('h4', {}, '用过的工具'), ctx.tools.length ? h('ul.ctx-tools', {}, ctx.tools.map(([name, count]) => h('li', {}, h('span', {}, TOOL_LABEL[name] || name), h('span.bar', {style: `width:${Math.min(100, count / ctx.tools[0][1] * 100)}%`}), h('b', {}, count)))) : h('p.quiet.small', {}, '还没用'),
    ctx.files.length ? [h('h4', {}, `工作区文件（${ctx.files.length}）`), h('ul.ctx-files', {}, ctx.files.map((file) => h('li', {}, h('a', {href: `/api/ws/${enc(laneId)}/${file.name.split(/[\\/]/).map(enc).join('/')}`, target: '_blank'}, file.name), h('small', {}, fmt.size(file.size)))))] : null,
    ctx.outputs.length ? [h('h4', {}, '产出'), h('ul.ctx-files', {}, ctx.outputs.map((item) => h('li', {}, item.file || item.name || '')))] : null,
    ctx.timers.length ? [h('h4', {}, '约好的复查'), ctx.timers.map((timer) => h('p.small', {}, `${fmt.clock(timer.due)} · ${timer.about}`))] : null,
    h('details.ctx-sys', {}, h('summary', {}, '系统提示（助手的身份和规矩）'), h('pre', {}, ctx.system)),
    h('button.btn.small', {type: 'button', onclick: () => api('/say', 'POST', {lane: laneId, text: '复盘这段对话：摸索出的做法要不要存成技能（发卡片问我）、结论记进笔记或课题、平台不顺手的地方记成改进。', anchor: '助手页'})}, icon('spark', 14), '复盘这段：沉淀经验')));
}

// ---------- 团队：专职成员、派活、任务看板 ----------

// 计划做到哪：一根细条 + 现在这一步
function planLine(steps) {
  if (!steps?.length) return null;
  const done = steps.filter((step) => step.state === '做完').length, now = steps.find((step) => step.state === '在做');
  return h('div.task-plan', {}, h('span.plan-bar', {}, h('i', {style: `width:${Math.round(done / steps.length * 100)}%`})),
    h('small', {}, `${done}/${steps.length}` + (now ? ` · ${now.text}` : done === steps.length ? ' · 做完' : '')));
}

// 团队：上面是成员（在做什么、排了几件、做完几件），中间是所有活的看板，右边是选中成员的设置。
// 成员能加、复制、删；角色、模型、常用技能、能用的工具（默认全部）、另外交代的话都能改；同时跑几件活也能改。
const AVATAR = ['#c96442', '#3f7cc0', '#5f8c3e', '#8a63b8', '#b5841c', '#c25f8a', '#3f8a86', '#6b665c'];

export async function teamPage(main, state, selectedId) {
  const [data, {skills}, {tools}, current] = await Promise.all([api('/team'), api('/skills'), api('/tools'), api('/state')]);
  state.anchor('助手 · 团队');
  const members = data.members.map((m) => ({...m}));
  let selected = selectedId && members.find((m) => m.id === selectedId) ? selectedId : members[0]?.id;
  const stateOf = (lane) => lane.running ? '在做' : lane.queued ? '排队' : lane.card ? '等你' : lane.status === 'failed' ? '没跑成' : '做完';
  const save = async (note) => { await api('/team', 'PUT', {members}); toast(note || '已存'); };
  const color = (index) => AVATAR[index % AVATAR.length];

  const tile = (m, index) => {
    const lanes = data.lanes.filter((lane) => lane.agent === m.id), count = (label) => lanes.filter((lane) => stateOf(lane) === label).length;
    const doing = lanes.find((lane) => lane.running);
    return h('button.member-tile' + (m.id === selected ? '.on' : '') + (doing ? '.busy' : ''), {type: 'button', onclick: () => { selected = m.id; draw(); }},
      h('span.avatar', {style: `--c:${color(index)}`}, m.name.slice(0, 1)),
      h('span.mt-body', {}, h('b', {}, m.name), h('small', {}, doing ? '在做：' + doing.title : '空闲'),
        h('span.mt-counts', {}, ['排队', '等你', '做完'].map((label) => count(label) ? h('i', {}, `${label} ${count(label)}`) : null))));
  };
  const addTile = h('button.member-tile.add', {type: 'button', onclick: () => {
    const id = 'member' + Date.now().toString(36).slice(-4);
    members.push({id, name: '新成员', role: '说清它负责什么、交回什么。', skills: []}); selected = id; save('加了一个成员，改好名字和角色'); draw();
  }}, h('span.avatar.plus', {}, '+'), h('span.mt-body', {}, h('b', {}, '加成员'), h('small', {}, '或者让主助手帮你设计一个')));

  const board = () => {
    const mine = data.lanes.filter((lane) => !selected || lane.agent === selected);
    return h('div.board.team-board', {}, ['排队', '在做', '等你', '做完', '没跑成'].map((label) => {
      const list = mine.filter((lane) => stateOf(lane) === label).slice(0, label === '做完' ? 15 : 50);
      return h('div.board-col', {}, h('h3', {}, label, h('small', {}, list.length)),
        list.length ? list.map((lane) => h('a.task-card.task-link' + (lane.running ? '.running' : ''), {href: '#/assistant/' + enc(lane.id)},
          h('div.task-meta', {}, (lane.updated || '').slice(5, 16)), h('b', {}, lane.title), planLine(lane.plan))) : h('p.quiet.small', {}, '—'));
    }));
  };

  const panel = () => {
    const m = members.find((item) => item.id === selected);
    if (!m) return h('p.quiet', {}, '在上面选一个成员');
    const index = members.indexOf(m);
    const toggleIn = (list, key) => { const at = list.indexOf(key); at >= 0 ? list.splice(at, 1) : list.push(key); };
    const chipSet = (all, chosen, onToggle, empty) => h('div.chip-set', {}, all.map(([key, label]) => h('button.chip-pick' + (chosen.includes(key) ? '.on' : ''), {type: 'button', title: key,
      onclick: (event) => { onToggle(key); event.currentTarget.classList.toggle('on'); }}, label)), empty ? h('small.quiet', {}, empty) : null);
    m.skills ||= []; m.tools ||= [];
    const task = h('textarea', {rows: 3, placeholder: `给${m.name}派一件活：说清要什么、交回什么（它在后台做，做完通知你）`});
    const field = (label, node, hint) => h('div.mp-field', {}, h('span', {}, label), node, hint ? h('small', {}, hint) : null);
    return h('div.member-panel', {},
      h('div.mp-head', {}, h('span.avatar.big', {style: `--c:${color(index)}`}, m.name.slice(0, 1)),
        h('input.mp-name', {value: m.name, onchange: (e) => { m.name = e.target.value.trim() || m.name; save(); draw(); }}), h('code', {}, m.id)),
      h('div.mp-send', {}, task, h('button.btn.primary', {type: 'button', onclick: async () => {
        if (!task.value.trim()) return;
        await api(`/team/${enc(m.id)}/task`, 'POST', {text: task.value.trim(), library: state.library, anchor: '助手 · 团队'});
        toast(`派给${m.name}了，在后台做`); setTimeout(() => teamPage(main, state, m.id), 800);
      }}, icon('send', 14), '派活')),
      field('负责什么', h('textarea', {rows: 3, onchange: (e) => { m.role = e.target.value; save(); }}, m.role || '')),
      field('另外交代的做法和习惯', h('textarea', {rows: 2, placeholder: '例：报告先给结论；GPC 叠加按保留时间；数字带来源', onchange: (e) => { m.prompt = e.target.value; save(); }}, m.prompt || ''), '会加进它的系统提示词'),
      m.executor ? field('外部执行器', h('div.mp-exec', {},
        h('p', {}, `起一个 ${m.executor.command || 'codex'} 进程来做（走本机网关，用它自己设置里的模型），经工作台的 MCP 服务用同一套工具；它调了什么、交回什么都记在它那件活的对话里，发的卡片照样到收件箱。`),
        h('label.inline', {}, '推理强度', h('select', {onchange: (e) => { m.executor.effort = e.target.value; save(); }},
          ['medium', 'high', 'xhigh'].map((value) => h('option', {value, selected: value === (m.executor.effort || 'high')}, {medium: '一般', high: '高（默认）', xhigh: '很高（慢）'}[value]))))),
        `别的智能体（例如你自己的 Claude Code）也能接工作台：MCP 地址 http://127.0.0.1:${location.port || 8850}/mcp`)
        : field('用哪个模型', h('select', {onchange: (e) => { m.profile = e.target.value; save(); }}, h('option', {value: ''}, '跟主设置一样'),
        data.profiles.map((p) => h('option', {value: p.id, selected: p.id === m.profile}, `${p.name} · ${p.model || ''}`)))),
      field('常用技能', chipSet(skills.map((s) => [s.key, s.name]), m.skills, (key) => { toggleIn(m.skills, key); save(); }), '第一次碰到这类事先读它'),
      m.executor ? null : field('能用的工具', chipSet(tools.map((tool) => [tool.name, TOOL_LABEL[tool.name] || tool.name]), m.tools, (key) => { toggleIn(m.tools, key); save(m.tools.length ? '只给它勾上的工具' : '不限：全部工具都能用'); },
        m.tools.length ? '' : '一个都不勾 = 全部能用（默认）'), '问你、约复查、通知总是能用'),
      m.id === 'main' ? null : h('div.row', {},
        h('button.btn.ghost.small', {type: 'button', onclick: () => { const copy = {...structuredClone(m), id: m.id + '-' + Date.now().toString(36).slice(-3), name: m.name + ' 副本'}; members.splice(index + 1, 0, copy); selected = copy.id; save('复制了一个'); draw(); }}, '复制这个成员'),
        h('button.btn.ghost.small', {type: 'button', onclick: () => { members.splice(index, 1); selected = members[0]?.id; save(`${m.name} 不在团队里了（它做过的活还在）`); draw(); }}, icon('trash', 13), '删掉')));
  };

  const parallel = h('input.parallel-in', {type: 'number', min: 1, max: 12, value: data.parallel, style: 'width:58px;flex:none', onchange: async (e) => {
    await api('/settings', 'PUT', {...current.settings, parallel: Number(e.target.value) || 4}); toast(`同时最多跑 ${e.target.value} 件活`); }});
  const strip = h('div.member-strip'), boardBox = h('div'), side = h('aside');
  const draw = () => {
    strip.replaceChildren(...members.map(tile), addTile);
    boardBox.replaceChildren(h('h2.band-title', {}, selected ? `${members.find((m) => m.id === selected)?.name || ''} 的活` : '全部的活',
      selected ? h('button.btn.ghost.small', {type: 'button', onclick: () => { selected = null; draw(); }}, '看全部') : null), board());
    side.replaceChildren(panel());
  };
  draw();
  shell(main, state, 'team', '团队', `${members.length} 个成员。主助手协调，长的、专门的活派给成员在后台做，做完结果送回派活的对话`,
    h('div.page-body', {}, h('div.row', {}, h('span.quiet.small', {}, '同时最多跑'), parallel, h('span.quiet.small', {}, '件活'),
      h('button.btn.ghost.small', {type: 'button', onclick: () => state.panel.ask('帮我设计一个新的团队成员：先问我它要负责什么，再建议名字、角色、常用技能和工具，我同意后加进团队。', '助手 · 团队')}, icon('spark', 13), '让助手帮我设计成员')),
      strip, h('div.team-layout', {}, h('div', {}, boardBox), side)));
}

// ---------- 技能 ----------

export async function skillsPage(main, state, key) {
  const {skills} = await api('/skills');
  state.anchor('助手 · 技能');
  const groups = [['沉积的', '助手和你在干活中沉淀的'], ['改过的内置', '在内置的基础上改过'], ['内置', '平台自带']];
  const detail = h('div.skill-detail');
  const list = h('nav.skill-list', {}, groups.map(([source, about]) => {
    const items = skills.filter((item) => item.source === source);
    return h('div.kind-group', {}, h('h3', {}, `${source}（${items.length}）`), items.length ? items.map((item) => h('a' + (item.key === key ? '.on' : ''), {href: '#/agent/skills/' + enc(item.key)},
      h('span', {}, item.name), h('small', {}, item.uses ? `${item.uses} 次` : ''))) : h('p.quiet.small.pad', {}, source === '沉积的' ? '还没有。做完一件事后助手会提议把做法存成技能。' : about));
  }));
  const loop = h('div.skill-loop', {}, ['干活中摸索出做法', '复盘时助手提议', '你点"存成技能"或自己写', '下次碰到同类事自动用', '用的时候发现不对就更新'].map((text, index, all) =>
    [h('span.sl-step', {}, h('b', {}, index + 1), text), index < all.length - 1 ? h('span.sl-arrow', {}, '→') : null]));
  shell(main, state, 'skills', '技能', `${skills.length} 个技能 · ${skills.filter((item) => item.source !== '内置').length} 个是沉淀出来的`,
    h('div.page-body', {}, loop, h('div.split.skill-split', {}, list, detail)),
    h('button.btn', {type: 'button', onclick: () => state.panel.ask('我想把一个做法存成技能：', '助手 · 技能')}, icon('plus', 15), '新技能'));
  const pick = skills.find((item) => item.key === key) || skills.sort((a, b) => b.uses - a.uses)[0];
  if (pick) drawSkill(detail, state, pick);
}

async function drawSkill(box, state, item) {
  const full = await api('/skills/' + enc(item.key));
  const body = full.text.replace(/^---[\s\S]*?---\s*/, '');
  const view = h('div.md.skill-md', {html: md(body)});
  const area = h('textarea.code.skill-area', {hidden: true}, full.text);
  const saveButton = h('button.btn.primary.small', {type: 'button', hidden: true, onclick: async () => {
    await api('/skills/' + enc(item.key), 'PUT', {text: area.value}); toast(item.source === '内置' ? '已保存成"改过的内置"（内置原件不动）' : '技能已保存'); go('agent', 'skills', item.key);
  }}, '保存');
  box.replaceChildren(
    h('div.skill-head', {}, h('h2', {}, item.name), h('span.st-tag.' + (item.source === '内置' ? 'todo' : 'done'), {}, item.source),
      h('button.btn.small', {type: 'button', onclick: () => { area.hidden = !area.hidden; view.hidden = !area.hidden; saveButton.hidden = area.hidden; }}, icon('edit', 13), '改'), saveButton,
      item.source !== '内置' ? h('button.btn.ghost.small', {type: 'button', onclick: async () => { await api('/skills/' + enc(item.key), 'DELETE'); toast(item.source === '改过的内置' ? '恢复成内置的了' : '放进回收站了'); go('agent', 'skills'); }},
        item.source === '改过的内置' ? '恢复内置' : '删掉') : null),
    h('p.skill-desc', {}, item.description),
    h('div.stat-row', {}, [['用了', `${item.uses} 次`], ['最近用', item.last_used ? item.last_used.slice(5, 16) : '—'], ['更新', item.updated || '—'], ['文件', `${item.files.length + 1} 个`]].map(([label, value]) =>
      h('div.stat', {}, h('div.stat-label', {}, label), h('div.stat-value.small', {}, value)))),
    item.origin || item.writes.length || item.lanes.length || item.rule ? h('dl.kv.skill-meta', {},
      item.origin ? [h('dt', {}, '来源'), h('dd', {}, item.origin)] : null,
      item.rule ? [h('dt', {}, '规则在用'), h('dd', {}, item.rule)] : null,
      item.writes.length ? [h('dt', {}, '写过'), h('dd', {}, item.writes.map((write) => h('div', {}, write.at.slice(5, 16), ' · ', h('a', {href: '#/assistant/' + enc(write.lane)}, write.title))))] : null,
      item.lanes.length ? [h('dt', {}, '用在'), h('dd', {}, item.lanes.map((lane) => h('div', {}, h('a', {href: '#/assistant/' + enc(lane.id)}, lane.title))))] : null) : null,
    item.files.length ? h('div.linked', {}, item.files.map((file) => h('span.mini-chip', {}, file))) : null,
    view, area);
}

// ---------- 工具和连接 ----------

export async function toolsPage(main, state) {
  const [{tools}, mcp] = await Promise.all([api('/tools'), api('/mcp')]);
  state.anchor('助手 · 工具和连接');
  const groups = {};
  tools.forEach((tool) => (groups[tool.group] ||= []).push(tool));
  const most = Math.max(1, ...tools.map((tool) => tool.calls));
  const servers = h('div.mcp-list');
  const drawServers = (data) => servers.replaceChildren(...(data.servers.length ? data.servers.map((server) => mcpCard(server, data, drawServers)) :
    [h('p.quiet', {}, '还没有接 MCP 服务器。MCP 是一种通用的工具接口：接上后，别的软件（文献库、仪器、云盘、数据库……）提供的工具助手都能直接用。')]));
  drawServers(mcp);
  shell(main, state, 'tools', '工具', `${tools.filter((tool) => tool.source === '自带').length} 个自带工具 · ${mcp.servers.filter((server) => server.state === 'connected').length} 个 MCP 服务器已连上`,
    h('div.page-body', {},
      h('section', {}, h('h2', {}, icon('plug', 16), ' MCP 服务器', h('small', {}, '接外面的工具')), servers, mcpEditor(mcp, drawServers)),
      Object.entries(groups).map(([group, items]) => h('section', {}, h('h2', {}, group, h('small', {}, `${items.length} 个`)), h('div.tool-grid', {}, items.map((tool) =>
        h('div.tool-card', {},
          h('div.tool-top', {}, h('b', {}, TOOL_LABEL[tool.name] || tool.name), h('code', {}, tool.name)),
          h('p', {}, tool.description.slice(0, 160) + (tool.description.length > 160 ? '…' : '')),
          tool.params.length ? h('div.linked', {}, tool.params.slice(0, 8).map((param) => h('span.mini-chip', {}, param))) : null,
          h('div.tool-use', {}, h('span.bar', {style: `width:${tool.calls / most * 100}%`}),
            h('small', {}, tool.calls ? `用了 ${tool.calls} 次` + (tool.errors ? ` · 出错 ${tool.errors}` : '') + (tool.avg_seconds != null ? ` · 平均 ${tool.avg_seconds} 秒` : '') : '还没用过')))))))));
}

function mcpCard(server, data, redraw) {
  const state = {connected: ['已连上', 'done'], connecting: ['正在连', 'current'], error: ['连不上', 'off'], off: ['停用', 'todo']}[server.state] || [server.state, 'todo'];
  const toggle = async () => {
    const config = data.config; config[server.name].enabled = !server.enabled;
    redraw(await api('/mcp', 'PUT', {servers: config})); setTimeout(async () => redraw(await api('/mcp')), 2500);
  };
  return h('div.mcp-card', {},
    h('div.tool-top', {}, h('span.st-dot.' + state[1]), h('b', {}, server.name), h('span.st-tag.' + state[1], {}, state[0]), h('code', {}, server.kind === 'url' ? '网址' : '本机命令'),
      h('span.grow'), h('button.btn.ghost.small', {type: 'button', onclick: async () => { toast('正在重连…'); redraw({...data, servers: data.servers.map((item) => item.name === server.name ? {...item, state: 'connecting'} : item)}); await api(`/mcp/${enc(server.name)}/restart`, 'POST'); redraw(await api('/mcp')); }}, icon('restore', 13), '重连'),
      h('button.btn.ghost.small', {type: 'button', onclick: toggle}, server.enabled ? '停用' : '启用'),
      h('button.btn.ghost.small', {type: 'button', onclick: async () => { const config = data.config; delete config[server.name]; redraw(await api('/mcp', 'PUT', {servers: config})); toast(`去掉了 ${server.name}`); }}, icon('trash', 13))),
    h('div.mcp-where', {}, server.where),
    server.error ? h('p.error-text.small', {}, server.error) : null,
    server.info?.server?.name ? h('p.quiet.small', {}, `${server.info.server.name} ${server.info.server.version || ''} · 协议 ${server.info.protocol || ''}`) : null,
    server.tools.length ? h('details', {}, h('summary', {}, `${server.tools.length} 个工具`), h('ul.plain-list', {}, server.tools.map((tool) => h('li', {}, h('b', {}, tool.name), ' ', h('span.quiet', {}, tool.description.slice(0, 140)))))) : null,
    server.log.length ? h('details', {}, h('summary', {}, '服务器输出'), h('pre', {}, server.log.join('\n'))) : null);
}

function mcpEditor(data, redraw) {
  const form = {name: '', kind: 'command', command: '', args: '', url: '', env: ''};
  const input = (key, placeholder) => h('input', {type: 'text', placeholder, oninput: (event) => { form[key] = event.target.value; }});
  const paste = h('textarea.code', {rows: 6, placeholder: '{"mcpServers": {"名字": {"command": "npx", "args": ["-y", "…"]}}}'});
  const addOne = async () => {
    if (!form.name.trim()) { toast('先起个名字'); return; }
    const env = Object.fromEntries(form.env.split('\n').map((line) => line.split('=')).filter((pair) => pair[0]?.trim()).map(([key, ...rest]) => [key.trim(), rest.join('=').trim()]));
    const spec = form.kind === 'url' ? {url: form.url.trim(), headers: env} : {command: form.command.trim(), args: form.args.trim() ? form.args.trim().split(/\s+/) : [], env};
    redraw(await api('/mcp', 'PUT', {servers: {...data.config, [form.name.trim()]: {...spec, enabled: true}}}));
    toast(`加上了 ${form.name}，正在连`); setTimeout(async () => redraw(await api('/mcp')), 3000);
  };
  return h('details.block.mcp-add', {}, h('summary', {}, icon('plus', 14), ' 接一个 MCP 服务器'),
    h('div.form', {},
      h('label.form-row', {}, h('span', {}, '名字'), input('name', '例：文献库')),
      h('label.form-row', {}, h('span', {}, '方式'), h('select', {onchange: (event) => { form.kind = event.target.value; }}, h('option', {value: 'command'}, '本机命令（stdio）'), h('option', {value: 'url'}, '网址（HTTP）'))),
      h('label.form-row', {}, h('span', {}, '命令'), input('command', '例：npx、uvx、python')), h('label.form-row', {}, h('span', {}, '参数'), input('args', '空格分开')),
      h('label.form-row', {}, h('span', {}, '网址'), input('url', 'https://…/mcp')),
      h('label.form-row', {}, h('span', {}, '环境变量 / 请求头'), h('textarea', {rows: 2, placeholder: 'KEY=VALUE，一行一个（密钥存在本机 data\\mcp.json，页面上打码）', oninput: (event) => { form.env = event.target.value; }})),
      h('div.row', {}, h('button.btn.primary.small', {type: 'button', onclick: addOne}, '加上并连接'))),
    h('p.quiet.small', {}, '或者把别的软件（Claude、Cursor 等）的 MCP 配置整段贴进来：'), paste,
    h('div.row', {}, h('button.btn.small', {type: 'button', onclick: async () => {
      const parsed = JSON.parse(paste.value); const servers = parsed.mcpServers || parsed.servers || parsed;
      redraw(await api('/mcp', 'PUT', {servers: {...data.config, ...servers}})); toast(`加上了 ${Object.keys(servers).length} 个`); setTimeout(async () => redraw(await api('/mcp')), 3000);
    }}, '贴进来的都加上')));
}

// ---------- 规则和定时 ----------

export async function rulesPage(main, state) {
  const [{rules}, {inbox}, {skills}] = await Promise.all([api('/rules'), api('/state'), api('/skills').catch(() => ({skills: []}))]);
  state.anchor('助手 · 规则和定时');
  let timer = null;
  const save = (now) => { clearTimeout(timer); const go = async () => { await api('/rules', 'PUT', {rules}); toast('规则存好了'); }; if (now) return go(); timer = setTimeout(go, 700); };
  const DAYS = [['mon', '周一'], ['tue', '周二'], ['wed', '周三'], ['thu', '周四'], ['fri', '周五'], ['sat', '周六'], ['sun', '周日']];
  const skillNames = (skills || []).map((item) => item.key || item.name).filter(Boolean);
  const libraries = ['全部', ...state.libraries];

  // 定时的写法 ⇄ 人看的几个格子：每天几点 / 每周几几点 / 每隔几小时 / 某个时刻一次
  const parse = (every) => {
    const text = String(every || '');
    let m;
    if ((m = text.match(/^daily\s+(\d{1,2}:\d{2})$/))) return {kind: '每天', time: m[1].padStart(5, '0')};
    if ((m = text.match(/^weekly\s+(\w+)\s+(\d{1,2}:\d{2})$/))) return {kind: '每周', day: m[1], time: m[2].padStart(5, '0')};
    if ((m = text.match(/^at\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})$/))) return {kind: '一次', date: m[1], time: m[2].padStart(5, '0')};
    if ((m = text.match(/^(\d+(?:\.\d+)?)\s*([mhd])$/))) return {kind: '每隔', amount: m[1], unit: m[2]};
    return {kind: '每天', time: '09:00'};
  };
  const build = (f) => f.kind === '每天' ? `daily ${f.time || '09:00'}` : f.kind === '每周' ? `weekly ${f.day || 'mon'} ${f.time || '09:00'}`
    : f.kind === '一次' ? `at ${f.date || new Date().toISOString().slice(0, 10)} ${f.time || '09:00'}` : `${f.amount || 1}${f.unit || 'h'}`;

  const whenBox = (rule) => {
    const when = rule.when ||= {source: '定时', every: 'daily 09:00'};
    const box = h('div.rw-when');
    const draw = () => {
      const kind = h('select', {onchange: (e) => { if (e.target.value === '定时') rule.when = {source: '定时', every: 'daily 09:00'}; else rule.when = {source: '文件夹', folder: '', pattern: '*.csv', do: '导入', auto: true}; save(); draw(); }},
        ['定时', '文件夹'].map((value) => h('option', {value, selected: (rule.when.source || '定时') === value}, value === '定时' ? '到点就做' : '文件夹里来了新文件')));
      if ((rule.when.source || '定时') === '定时') {
        const f = parse(rule.when.every);
        const set = (key, value) => { f[key] = value; rule.when.every = build(f); save(); draw(); };
        box.replaceChildren(kind,
          h('select', {onchange: (e) => set('kind', e.target.value)}, ['每天', '每周', '每隔', '一次'].map((value) => h('option', {selected: f.kind === value}, value))),
          f.kind === '每周' ? h('select', {onchange: (e) => set('day', e.target.value)}, DAYS.map(([value, label]) => h('option', {value, selected: f.day === value}, label))) : null,
          f.kind === '一次' ? h('input', {type: 'date', value: f.date || '', onchange: (e) => set('date', e.target.value)}) : null,
          f.kind === '每隔' ? [h('input.rw-num', {type: 'number', min: 1, step: 'any', value: f.amount || 1, onchange: (e) => set('amount', e.target.value)}),
            h('select', {onchange: (e) => set('unit', e.target.value)}, [['m', '分钟'], ['h', '小时'], ['d', '天']].map(([value, label]) => h('option', {value, selected: f.unit === value}, label)))]
            : h('input', {type: 'time', value: f.time || '09:00', onchange: (e) => set('time', e.target.value)}),
          h('small.quiet', {}, '北京时间'));
      } else {
        box.replaceChildren(kind,
          h('input.rw-folder', {type: 'text', value: rule.when.folder || '', placeholder: '文件夹，例：D:\\仪器导出\\GPC', onchange: (e) => { rule.when.folder = e.target.value.trim(); save(); }}),
          h('input.rw-pattern', {type: 'text', value: rule.when.pattern || '', placeholder: '哪些文件，例：*.csv', title: '* 代表任意几个字', onchange: (e) => { rule.when.pattern = e.target.value.trim() || '*'; save(); }}),
          h('label.inline.small', {}, h('input', {type: 'checkbox', checked: !!rule.when.recursive, onchange: (e) => { rule.when.recursive = e.target.checked; save(); }}), '子文件夹也看'));
      }
    };
    draw();
    return box;
  };

  const card = (rule, index) => h('div.rule-card.rw' + (rule.enabled === false ? '.off' : ''), {},
    h('div.tool-top', {}, h('span.st-dot.' + (rule.enabled === false ? 'todo' : 'done')),
      h('input.rw-name', {type: 'text', value: rule.name, title: '规则的名字', onchange: (e) => { rule.name = e.target.value.trim() || rule.name; save(); }}),
      h('span.grow'),
      (rule.when?.source || '定时') === '定时' ? h('button.btn.small', {type: 'button', onclick: async () => { await save(true); await api('/rules/' + enc(rule.name) + '/run', 'POST'); toast(`「${rule.name}」开始跑了，跑完会通知你`); }}, '现在跑一次') : null,
      h('button.btn.ghost.small', {type: 'button', onclick: async () => { rule.enabled = rule.enabled === false; await save(true); rulesPage(main, state); }}, rule.enabled === false ? '启用' : '停用'),
      h('button.btn.ghost.small', {type: 'button', title: '删掉这条规则', onclick: async () => { if (!confirm(`删掉规则「${rule.name}」？`)) return; rules.splice(index, 1); await save(true); rulesPage(main, state); }}, icon('trash', 13))),
    h('div.rw-grid', {},
      h('span.rw-label', {}, '什么时候'), whenBox(rule),
      h('span.rw-label', {}, '在哪个库'), h('select', {onchange: (e) => { rule.library = e.target.value; save(); }},
        libraries.map((name) => h('option', {selected: (rule.library || state.library) === name}, name === '全部' ? '每个库各做一份' : name))),
      h('span.rw-label', {}, '先读技能'), h('div', {}, h('input', {type: 'text', list: 'rw-skills', value: rule.skill || '', placeholder: '不用就空着', onchange: (e) => { rule.skill = e.target.value.trim(); save(); }})),
      h('span.rw-label', {}, '要做的'), h('textarea.rule-prompt', {rows: 3, placeholder: (rule.when?.source === '文件夹') ? '来了新文件怎么处理（空着就按导入的做法读进库）' : '到点要助手做什么，像跟人交代一样写',
        oninput: (e) => { rule.prompt = e.target.value; save(); }}, rule.prompt || '')),
    h('div.rw-foot', {}, rule.by ? h('small.quiet', {}, rule.by + (rule.created ? ' · ' + rule.created.slice(0, 16) : '')) : null, h('span.grow'),
      h('a.small', {href: '#/assistant/' + enc('rule-' + rule.name)}, '看它跑过的记录 →')));

  shell(main, state, 'rules', '规则', `${rules.filter((rule) => rule.enabled !== false).length} 条规则在用 · ${inbox.timers.length} 个约好的复查`,
    h('div.page-body', {},
      h('p.quiet', {}, '规则让助手主动干活：到点了，或者文件夹里来了新文件，助手就照着做，做完通知你。改了自动存。'),
      h('datalist', {id: 'rw-skills'}, skillNames.map((name) => h('option', {value: name}))),
      h('div.rule-list', {}, rules.map(card)),
      h('div.row', {}, h('button.btn', {type: 'button', onclick: () => state.panel.ask('我想加一条规则：（说清什么时候、做什么）', '助手 · 规则')}, icon('plus', 14), '让助手帮我加一条'),
        h('button.btn.ghost', {type: 'button', onclick: async () => { rules.push({name: '新规则 ' + (rules.length + 1), when: {source: '定时', every: 'daily 09:00'}, prompt: '', enabled: false, library: state.library}); await save(true); rulesPage(main, state); }}, '加一条空的')),
      h('section', {}, h('h2', {}, '约好的复查'), inbox.timers.length ? h('ul.links', {}, inbox.timers.map((item) => h('li', {}, h('b', {}, item.at || fmt.clock(item.due)), ' ', item.about, ' ',
        h('a', {href: '#/assistant/' + enc(item.lane)}, '对话')))) : h('p.quiet', {}, '没有。助手约好以后回来看的事（例：老化 24 小时后读数）会列在这里。'))));
}

// ---------- 跑过的轮 ----------

export async function runsPage(main, state) {
  const {runs} = await api('/agent/runs');
  state.anchor('助手 · 跑过的轮');
  const days = {};
  runs.forEach((run) => { const day = (run.created || '').slice(5, 10); if (day) { days[day] ||= {done: 0, failed: 0, other: 0}; days[day][run.status === 'done' ? 'done' : run.status === 'failed' ? 'failed' : 'other']++; } });
  const keys = Object.keys(days).sort().slice(-21);
  const plot = h('div.plot');
  const label = {done: '做完', failed: '出错', waiting: '等你回答', stopped: '停了', paused: '暂停', running: '在跑', queued: '排队'};
  shell(main, state, 'runs', '跑过的轮', `${runs.length} 轮：每一轮是助手被叫醒做一次事（你说话、卡片回答、定时、文件夹、复查）`,
    h('div.page-body', {}, keys.length ? h('section.block', {}, h('header.block-head', {}, h('h3', {}, '每天跑了多少轮')), plot) : null,
      h('div.tablewrap', {}, h('table.data.compact', {}, h('thead', {}, h('tr', {}, ['时间', '对话', '叫醒它的', '做什么', '结果', '用时', '次数'].map((name) => h('th', {}, name)))),
        h('tbody', {}, runs.map((run) => h('tr', {}, h('td', {}, (run.created || '').slice(5, 16)), h('td', {}, h('a', {href: '#/assistant/' + enc(run.lane)}, run.title)),
          h('td', {}, run.sources.join('、')), h('td', {}, run.what), h('td', {}, h('span.st-tag.' + (run.status === 'done' ? 'done' : run.status === 'failed' ? 'off' : 'current'), {}, label[run.status] || run.status)),
          h('td.num', {}, run.seconds != null ? `${Math.round(run.seconds)} 秒` : ''), h('td.num', {}, run.attempts))))))));
  if (keys.length) requestAnimationFrame(() => chart(plot, {x: '日期', y: '轮数', height: 220, series: [
    {name: '做完', x: keys, y: keys.map((key) => days[key].done), mode: 'lines+markers'}, {name: '出错', x: keys, y: keys.map((key) => days[key].failed), mode: 'lines+markers', color: '#e34948'},
    {name: '其它', x: keys, y: keys.map((key) => days[key].other), mode: 'lines+markers', color: '#968f82'}]}));
}

// ---------- 改进 ----------

// 改动回看：生长循环的最后一环"落地 → 看用不用得上 → 留或撤"。技能、规则、定过的事、提示词落地后用了几次、替你少问几次
const RV_TONE = {用得上: '.done', 还没用上: '.open', 很久没用: '.open'};          // 其它（等着跑、等新文件、刚改、停用了、没再遇到）灰色
const RV_KINDS = [['技能', '技能（助手沉淀的、你改过的）'], ['规则', '规则和定时'], ['提示词', '系统提示词'], ['定过的事', '定过的事']];

function rvStrip(values, unit) {
  const top = Math.max(1, ...values);
  return h('div.rv-strip', {title: `近 ${values.length} ${unit}，每${unit}用了几次：${values.join('、')}`},
    values.map((value) => h('span' + (value ? '.on' : ''), {style: {height: value ? `${5 + 19 * value / top}px` : '2px'}})));
}

function rvItem(item, unit, undo) {
  const asked = item.kind === '技能' && item.asked_after != null
    ? h('div.rv-asked', {}, item.asked_before != null ? `每次用平均问你 ${item.asked_before} → ${item.asked_after} 次` : `改后每次用平均问你 ${item.asked_after} 次`) : null;
  const used = item.kind === '定过的事' ? (item.spared ? `挡下 ${item.spared} 次重问` : '还没再遇到') : item.used || `用了 ${item.uses} 次`;
  return h('div.rv-item', {},
    h('div.rv-main', {}, h('b', {}, item.name), h('div.rv-about', {}, item.about),
      h('div.rv-meta', {}, item.at ? `${item.at.slice(5, 16)} ${item.kind === '定过的事' ? '定的' : '起'}` : '', item.by ? ` · ${item.by}` : '', item.last ? ` · 最近 ${item.last.slice(5, 16)}` : '')),
    rvStrip(item.weeks, unit),
    h('div.rv-num', {}, h('div.rv-used', {}, used), asked,
      h('div.rv-state', {}, h('span.st-tag' + (RV_TONE[item.state] || ''), {}, item.state), item.hint ? h('small.quiet', {}, item.hint) : null)),
    h('div.rv-actions', {}, h('button.btn.ghost.small', {type: 'button', onclick: () => undo(item)}, item.undo.label),
      h('a.small', {href: item.link}, '去看')));
}

async function changeReview(state) {
  const library = state.library;
  const data = await api(`/review/${enc(library)}`);
  const box = h('section.review');
  const undo = async (item) => {
    await api(`/review/${enc(library)}/undo`, 'POST', {action: item.undo.action, key: item.key});
    toast({skill: '技能撤掉了（在回收站里）', rule: item.enabled ? '规则停用了' : '规则启用了', decision: '撤销了，助手下次遇到会再问你', prompt: '回到内置提示词了（你改的那段在回收站里）'}[item.undo.action]);
    box.replaceWith(await changeReview(state));
  };
  const t = data.totals;
  const kinds = Object.entries(t.by_kind).filter(([, count]) => count).map(([kind, count]) => `${kind} ${count}`).join(' · ');
  const plot = h('div.rv-plot');
  const groups = RV_KINDS.map(([kind, title]) => {
    const list = data.items.filter((item) => item.kind === kind);
    if (!list.length) return null;
    const live = kind === '定过的事' ? list.filter((item) => item.spared) : list;
    const rest = kind === '定过的事' ? list.filter((item) => !item.spared) : [];
    return h('div.rv-group', {}, h('h4', {}, title, h('small', {}, list.length)),
      live.map((item) => rvItem(item, data.unit, undo)),
      rest.length ? h('details.rv-rest', {}, h('summary', {}, `还没再遇到的 ${rest.length} 件（定过的事没再遇到很正常，不算没用）`), rest.map((item) => rvItem(item, data.unit, undo))) : null);
  });
  box.append(h('div.rv-head', {}, h('h3', {}, '改动回看'), h('span.quiet.small', {}, '落地的东西用得上吗：用了几次、替你少问了几次。改了一周还没用上的，可以撤；每周复盘时助手也会看这里，发卡片问你撤不撤。')),
    h('div.stat-row', {},
      h('div.stat', {}, h('div.stat-label', {}, '落地的改动'), h('div.stat-value', {}, t.changes), h('div.stat-note', {}, kinds || '还没有')),
      h('div.stat', {}, h('div.stat-label', {}, '改后用了'), h('div.stat-value', {}, t.uses, h('small', {}, ' 次')), h('div.stat-note', {}, '技能、规则、提示词')),
      h('div.stat' + (t.spared ? '.good' : ''), {}, h('div.stat-label', {}, '替你少问'), h('div.stat-value', {}, t.spared, h('small', {}, ' 次')),
        h('div.stat-note', {}, `定过的事挡下 ${t.spared_decided} · 同一件事并一张 ${t.spared_twin}`)),
      h('div.stat', {}, h('div.stat-label', {}, '到你面前的卡片'), h('div.stat-value', {}, t.asked, h('small', {}, ' 张')), h('div.stat-note', {}, `近 ${data.weeks.length} ${data.unit}`)),
      h('div.stat', {}, h('div.stat-label', {}, '自动导入'), h('div.stat-value', {}, t.files, h('small', {}, ' 个文件')), h('div.stat-note', {}, '没让你动手')),
      h('div.stat' + (t.idle ? '.warn' : ''), {}, h('div.stat-label', {}, '没用上的'), h('div.stat-value', {}, t.idle, h('small', {}, ' 条')), h('div.stat-note', {}, t.idle ? '看下面，不要就撤' : '都用得上'))),
    h('div.rv-chart', {}, h('div.rv-chart-title', {}, `助手想问你的，每${data.unit}多少（柱子总高 = 它想问的；彩色的部分替你挡下了）`), plot),
    h('div.rv-list', {}, groups));
  const keys = data.weeks.map((week) => data.unit === '周' ? week.week.slice(5) + ' 那周' : week.week.slice(5));
  requestAnimationFrame(() => chart(plot, {kind: 'bar', stack: true, y: '次', height: 200, series: [
    {name: '到你面前的', x: keys, y: data.weeks.map((week) => week.asked), color: '#6b665c'},
    {name: '定过的事挡下', x: keys, y: data.weeks.map((week) => week.decided), color: '#c96442'},
    {name: '同一件事并到一张', x: keys, y: data.weeks.map((week) => week.twin), color: '#3f7cc0'}]}));
  return box;
}

export async function improvePage(main, state) {
  const {rows} = await api(`/lib/${enc('知识库')}/records?kind=${enc('改进')}`);
  state.anchor('助手 · 改进');
  const review = await changeReview(state);
  const columns = [['提议', '提议'], ['在做', '在做'], ['做完', '做完'], ['不做', '不做']];
  const move = async (row, status) => { await api(`/lib/${enc('知识库')}/records`, 'POST', {kind: '改进', rows: [{id: row.id, 状态: status}]}); toast(`→ ${status}`); improvePage(main, state); };
  const add = async () => {
    const text = prompt('哪里不顺手？（一句话）'); if (!text) return;
    await api(`/lib/${enc('知识库')}/records`, 'POST', {kind: '改进', rows: [{name: text.slice(0, 30), 问题: text, 来源: '人', 状态: '提议', 类别: ''}]});
    improvePage(main, state);
  };
  shell(main, state, 'improve', '改进', `平台自己长：不顺手的地方记下来 → 改 → 回看用不用得上 → 留或撤。看板上 ${rows.length} 条。`,
    h('div.page-body', {}, review, h('h3.board-title', {}, '改进看板'), h('p.quiet.small', {}, '平台和流程里不顺手的地方。助手复盘时记，你也可以记；小的助手直接改，大的发卡片问你。'),
      h('div.row', {}, h('button.btn', {type: 'button', onclick: add}, icon('plus', 14), '记一条'),
      h('button.btn', {type: 'button', onclick: () => state.panel.ask('处理知识库"改进"里状态是"提议"的事：小的、能撤回的（呈现、页面、技能、笔记）直接做，做完改成"做完"写上结果；大的列给我选。', '助手 · 改进')}, icon('spark', 14), '让助手处理提议的')),
    h('div.board', {}, columns.map(([key, title]) => {
      const list = rows.filter((row) => (row['状态'] || '提议') === key);
      return h('div.board-col', {}, h('h3', {}, title, h('small', {}, list.length)), list.map((row) => h('div.task-card', {},
        h('div.task-head', {}, row['类别'] ? h('span.prio', {}, row['类别']) : null, h('b', {}, row.name)),
        row['问题'] && row['问题'] !== row.name ? h('p', {}, row['问题']) : null, row['建议'] ? h('p', {}, '建议：' + row['建议']) : null,
        row['结果'] ? h('p', {}, '结果：' + row['结果']) : null, row['来源'] ? h('div.task-meta', {}, '来源：' + row['来源']) : null,
        h('select.task-move', {onchange: (event) => move(row, event.target.value)}, columns.map(([value]) => h('option', {value, selected: value === key}, value))))));
    }))));
}
