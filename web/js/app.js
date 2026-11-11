// 工作台外壳：左边导航，中间页面，右边助手栏。
import {$, api, connect, fmt, go, h, icon, listen, route} from './core.js';
import {Conversation} from './assistant.js';
import * as views from './views.js';
import * as agent from './agent.js';
import * as analysis from './analysis.js';
import * as stages from './stages.js';
import {onTask} from './blocks.js';
import {watchSplits} from './splitter.js';
import {mePage, usersPage} from './users.js';

// 按研发环节：课题 → 合成 → 配方 → 测试 → 研发台
const NAV = [['overview', '总览'], ['projects', '课题'], ['synthesis', '合成'], ['formulation', '配方'], ['testing', '测试'], ['desk', '研发台'],
  ['knowledge', '知识'], ['outputs', '产出'], ['assistant', '助手'], ['settings', '设置']];
const AGENT_TABS = {team: agent.teamPage, skills: agent.skillsPage, memory: agent.memoryPage, learn: agent.learnPage, tools: agent.toolsPage, rules: agent.rulesPage, runs: agent.runsPage, improve: agent.improvePage};
const PAGES = {overview: views.overview, projects: views.projects, project: views.project, data: views.data,
  record: views.record, search: views.search, knowledge: views.knowledge, outputs: views.outputs,
  settings: (main, state, args) => args[0] === 'users' ? usersPage(main, state) : views.settings(main, state, args), me: (main, state) => mePage(main, state),
  synthesis: (main, state, args) => stages.synthesis(main, state, views.header, args, views.resinCards),
  experiments: (main, state, [kind]) => kind && kind !== '树脂实验' ? views.experiments(main, state, [kind]) : stages.synthesis(main, state, views.header, [], views.resinCards),
  formulation: (main, state, args) => stages.formulation(main, state, views.header, args),
  testing: (main, state, args) => stages.testing(main, state, views.header, args),
  desk: (main, state, args) => stages.desk(main, state, views.header, args),
  assistant: (main, state, args) => agent.chatPage(main, state, args[0]),
  analysis: (main, state, args) => args[0] ? analysis.gpcCompare(main, state, views.header, args) : analysis.analysisList(main, state, views.header),
  agent: (main, state, [tab, id]) => (AGENT_TABS[tab] || agent.skillsPage)(main, state, id),
  find: async (main, state, [name]) => {               // [[记录名]] 点进来：找到那条记录就过去，找不到就搜
    const found = await api(`/resolve/${encodeURIComponent(state.library)}?name=${encodeURIComponent(name)}`);
    location.replace(!found.id ? `#/search/${encodeURIComponent(name)}`
      : found.kind === '课题' && found.library === state.library ? `#/project/${encodeURIComponent(found.id)}`        // 课题打开它的路线页
      : `#/record/${encodeURIComponent(found.library)}/${encodeURIComponent(found.id)}`);
  }};
const ACTIVE = {project: 'projects', record: 'synthesis', search: 'desk', agent: 'assistant', experiments: 'synthesis', data: 'desk', analysis: 'desk'};
const allowed = (page) => {                       // 导航只给能用的：设置只给管理员（别人是"我的"）；没开助手的不出现助手
  const me = state.me || {admin: true};
  if (page === 'settings') return me.admin;
  if (page === 'me') return !me.admin || me.public;
  if (page === 'assistant') return me.admin || me.rights?.assistant;
  if (page === 'knowledge') return me.admin || state.libraries.includes('知识库');
  return true;
};

const store = (key, value) => { try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (error) { return null; } };

const state = {library: '产品A', libraries: [], here: '总览', anchor: (text) => { state.here = text; panel.where(text); }};

// ---------- 助手栏 ----------

const panel = {
  convo: new Conversation({compact: true, context: () => ({anchor: state.here, library: state.library}),
    onLane: (lane) => { store('lab.lane', lane); panel.title(); }}),
  root: null, open_: store('lab.panel') === 'open',
  build() {
    this.whereText = h('span.panel-where');
    this.titleText = h('span.panel-title', {}, '助手');
    this.badge = h('span.badge', {hidden: true});
    this.dot = h('span.badge-dot', {hidden: true});
    this.strip = h('button.panel-strip', {type: 'button', title: '打开助手', onclick: () => this.toggle(true)}, icon('assistant', 20), h('span.strip-label', {}, '助手'), this.badge, this.dot);
    this.body = h('div.panel-body', {},
      h('div.panel-head', {}, this.titleText,
        h('button.btn.ghost.icon-btn', {type: 'button', title: '新对话', onclick: () => { store('lab.lane', ''); this.convo.open(null); this.title(); }}, icon('plus', 17)),
        h('button.btn.ghost.icon-btn', {type: 'button', title: '在助手页打开', onclick: () => this.convo.lane ? go('assistant', this.convo.lane) : go('assistant')}, icon('open', 16)),
        h('button.btn.ghost.icon-btn', {type: 'button', title: '收起', onclick: () => this.toggle(false)}, icon('collapse', 18))),
      this.whereText, this.convo.root);
    // 左边缘能拖：拖宽拖窄，记住宽度；双击回到默认宽度
    const saved = Number(store('lab.panelWidth'));
    if (saved) document.documentElement.style.setProperty('--panel', saved + 'px');
    const grip = h('div.panel-grip', {title: '拖动改宽窄，双击回到默认', ondblclick: () => { document.documentElement.style.removeProperty('--panel'); store('lab.panelWidth', ''); }});
    grip.addEventListener('pointerdown', (event) => {
      event.preventDefault(); grip.setPointerCapture(event.pointerId); document.body.classList.add('resizing');
      const move = (e) => {
        const width = Math.round(Math.min(Math.max(window.innerWidth - e.clientX, 320), window.innerWidth - 480));
        document.documentElement.style.setProperty('--panel', width + 'px');
      };
      const up = () => {
        grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up); document.body.classList.remove('resizing');
        store('lab.panelWidth', parseInt(getComputedStyle(document.documentElement).getPropertyValue('--panel'), 10) || '');
      };
      grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up);
    });
    this.root = h('aside.panel', {'aria-label': '助手'}, grip, this.strip, this.body);
    this.convo.open(store('lab.lane') || null).then(() => this.title()).catch(() => this.convo.open(null));
    this.toggle(this.open_ && !matchMedia('(max-width: 760px)').matches);    // 手机上助手是盖在页面上的，打开时先收着
    return this.root;
  },
  toggle(open) { this.open_ = open; document.body.classList.toggle('panel-open', open); store('lab.panel', open ? 'open' : 'closed'); if (open) setTimeout(() => this.convo.input.focus(), 50); },
  where(text) { if (this.whereText) this.whereText.textContent = '在看：' + text; },
  title() { this.titleText.textContent = this.convo.title || '助手'; },
  async ask(text, anchor) { if (anchor) state.anchor(anchor); this.toggle(true); if (!this.convo.lane) await this.convo.open(null); this.convo.send(text); },
  async open(lane) { this.toggle(true); store('lab.lane', lane); await this.convo.open(lane); this.title(); },
  mount(host, lane, onLane) {   // 助手页：用一段完整的对话（单独一个实例）
    const page = new Conversation({context: () => ({anchor: '助手页', library: state.library}), onLane: (id) => { history.replaceState(null, '', '#/assistant/' + encodeURIComponent(id)); onLane?.(id); }});
    host.append(page.root); page.open(lane || null);
  },
};
state.panel = panel;
state.focus = store('lab.focus') || '';
state.rerender = () => render();
onTask((task, title) => panel.ask(task, '简报 · ' + (title || '')));                       // 简报里点"交给助手"
const here = () => { if (document.visibilityState === 'visible' && document.hasFocus()) fetch('/api/presence', {method: 'POST'}).catch(() => {}); };
setInterval(here, 20000); window.addEventListener('focus', here); here();             // 你在工作台前时不弹桌面通知
watchSplits();                                                                        // 所有固定宽的栏都能拖宽拖窄

// ---------- 导航 ----------

function rail() {
  const links = [...NAV, ['me', '我的']].filter(([page]) => allowed(page)).map(([page, label]) => h('a.nav-item', {href: '#/' + (page === 'overview' ? '' : page), 'data-page': page}, icon(page, 18), h('span', {}, label)));
  const libs = h('div.lib-switch', {role: 'group', 'aria-label': '产品库'});
  const clock = h('div.rail-clock', {}, '北京时间 ' + fmt.today());
  setInterval(() => { clock.textContent = '北京时间 ' + fmt.today(); }, 30000);
  // 聚焦：输一个编号（R100-41、PA-T21、REF01…），合成、配方、测试的列表都只看和它有关的
  const focus = h('input.focus-input', {type: 'search', placeholder: '聚焦一个项目（编号）', value: state.focus || '', title: '合成、配方、测试的列表只看和这个编号有关的；清空看全部',
    onchange: (event) => { state.focus = event.target.value.trim(); store('lab.focus', state.focus); render(); }});
  return {node: h('nav.rail', {'aria-label': '主导航'},
    h('div.brand', {}, h('span.brand-mark', {html: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 8h7l5 8h6"/><path d="M3 16h6"/><circle cx="12" cy="12" r="2.6" fill="#262522"/></svg>'}), h('span', {}, '研发工作台')),
    libs, h('label.focus-box', {}, icon('search', 14), focus), h('div.nav-list', {}, links),
    h('button.btn.ghost.rail-toggle', {type: 'button', title: '收起导航', onclick: () => { document.body.classList.toggle('rail-mini'); store('lab.rail', document.body.classList.contains('rail-mini') ? 'mini' : ''); }}, icon('menu', 18)),
    clock), libs};
}

const libHosts = [];                              // 库切换：左栏一个，手机"更多"里一个
function drawLibraries(libs) {
  if (libs && !libHosts.includes(libs)) libHosts.push(libs);
  libHosts.forEach((host) => host.replaceChildren(...state.libraries.filter((name) => name !== '知识库').map((name) =>
    h('button' + (name === state.library ? '.on' : ''), {type: 'button', onclick: async () => {
      state.library = name; store('lab.library', name); if (state.me?.admin && !state.me.public) await api('/settings', 'PUT', {library: name});
      document.body.classList.remove('more-open'); drawLibraries(); render();
    }}, name))));
}

// 手机：底下一条（总览、合成、配方、测试、更多），"更多"拉出一张单子：库切换、聚焦编号、别的页面、手机现场页
const MOBILE_TABS = ['overview', 'synthesis', 'formulation', 'testing'];
function mobileNav() {
  const close = () => document.body.classList.remove('more-open');
  const href = (page) => '#/' + (page === 'overview' ? '' : page);
  const pages = [...NAV, ['me', '我的']].filter(([page]) => allowed(page));
  const bar = h('nav.mobile-bar', {'aria-label': '主导航'},
    pages.filter(([page]) => MOBILE_TABS.includes(page)).map(([page, label]) => h('a.mb-item', {href: href(page), 'data-page': page, onclick: close}, icon(page, 20), h('span', {}, label))),
    h('button.mb-item.mb-more', {type: 'button', onclick: () => document.body.classList.toggle('more-open')}, icon('menu', 20), h('span', {}, '更多')));
  const libs = h('div.lib-switch.ms-libs', {role: 'group', 'aria-label': '产品库'});
  const focus = h('input.focus-input', {type: 'search', placeholder: '聚焦一个项目（编号）', value: state.focus || '',
    onchange: (event) => { state.focus = event.target.value.trim(); store('lab.focus', state.focus); close(); render(); }});
  const sheet = h('div.more-sheet', {onclick: (event) => { if (event.target === sheet) close(); }},
    h('div.ms-card', {},
      h('div.ms-head', {}, h('b', {}, '更多'), h('button.btn.ghost.icon-btn', {type: 'button', 'aria-label': '关上', onclick: close}, icon('close', 18))),
      libs, h('label.focus-box.ms-focus', {}, icon('search', 14), focus),
      h('div.ms-grid', {}, pages.filter(([page]) => !MOBILE_TABS.includes(page)).map(([page, label]) => h('a.ms-item', {href: href(page), 'data-page': page, onclick: close}, icon(page, 20), h('span', {}, label))),
        h('a.ms-item', {href: '/m'}, icon('testing', 20), h('span', {}, '现场页'))),
      h('p.ms-note', {}, '现场页：称量、批记录、拍板，按手机排好的。')));
  return {bar, sheet, libs};
}

// ---------- 页面 ----------

let rendering = 0;
async function render() {
  const {page, args} = route();
  const main = $('#main');
  document.querySelectorAll('.nav-item, .mb-item, .ms-item').forEach((link) => link.classList.toggle('on', link.dataset.page === (ACTIVE[page] || page)));
  document.querySelector('.mb-more')?.classList.toggle('on', !MOBILE_TABS.includes(ACTIVE[page] || page));
  document.body.dataset.page = ACTIVE[page] || page;
  const ticket = ++rendering;
  main?.classList.add('loading');
  const before = main.firstChild;
  const drawn = new MutationObserver(() => { main.classList.remove('loading'); drawn.disconnect(); });   // 页面画出东西了就不再发灰
  drawn.observe(main, {childList: true});
  const slow = setTimeout(() => {                 // 超过 0.2 秒页面还一点没画：先给骨架屏（页面已经画了一部分就别盖掉它）
    if (ticket === rendering && main.firstChild === before) main.replaceChildren(h('div.skeleton', {}, h('i.mid'), h('i.big'), h('i'), h('i'), h('i.big')));
  }, 200);
  try {
    await (PAGES[page] || views.overview)(main, state, args);
    clearTimeout(slow);
  } catch (error) {
    clearTimeout(slow);
    if (ticket === rendering) main.replaceChildren(h('div.page-body', {}, h('p.error-text', {}, '这一页没打开：' + error.message), h('button.btn', {type: 'button', onclick: render}, '再试一次')));
  }
  drawn.disconnect();
  if (ticket === rendering) { main.classList.remove('loading'); main.focus({preventScroll: true}); }
}

// 红色数字只数"要你定的事"；新结果、通知只亮一个小蓝点，不催人
async function badge() {
  const {inbox} = await api('/state');
  const decide = inbox.cards.length, fresh = inbox.unread.length;
  panel.badge.hidden = !decide;
  panel.badge.textContent = decide > 99 ? '99+' : decide;
  panel.dot.hidden = !fresh || !!decide;
  panel.strip.title = [decide ? `${decide} 件事等你拍板` : '', fresh ? `${fresh} 段对话有新结果` : ''].filter(Boolean).join('，') || '打开助手';
  document.title = (decide ? `(${decide > 99 ? '99+' : decide}) ` : '') + '研发工作台';
}

async function start() {
  const data = await api('/state');
  state.libraries = data.libraries;
  state.me = data.me;
  if (!(data.me.admin || data.me.rights?.assistant)) document.body.classList.add('no-assistant');
  state.render = render;                          // 页面改了东西（项目改名、挪动）后就地重画，不动地址
  state.library =data.libraries.includes(store('lab.library')) ? store('lab.library') : data.library;
  if (store('lab.rail') === 'mini') document.body.classList.add('rail-mini');
  const {node, libs} = rail();
  const phone = mobileNav();
  document.body.replaceChildren(h('div.shell', {}, node, h('main#main', {tabindex: -1}), panel.build()), phone.bar, phone.sheet);
  drawLibraries(libs); drawLibraries(phone.libs);
  connect(data.seq);
  listen((event) => { if (event.type === 'status' && event.state === 'idle' || event.type === 'card' || event.type === 'card_closed' || event.type === 'notice') { badge(); if (route().page === 'overview') render(); } });
  window.addEventListener('hashchange', () => { document.body.classList.remove('more-open'); window.scrollTo(0, 0); $('#main').scrollTop = 0; render(); });
  render(); badge();
}

start();
