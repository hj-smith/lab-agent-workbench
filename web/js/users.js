// 用户和权限（设置 → 用户和权限，管理员用）和"我的"（每个人自己的账号）。设计见 设计-用户和权限.md。
// 每个库（包括知识库）一个等级：不给 / 看 / 填 / 改；再加三个开关：用助手、助手能动这台电脑、导出和下载。管理员什么都能，没有限制。
import {api, fmt, h, icon, toast} from './core.js';

const enc = encodeURIComponent;
const LEVELS = ['', '看', '填', '改'];
const LEVEL_TEXT = {'': '不给', 看: '看', 填: '填', 改: '改'};
const SWITCHES = [['assistant', '用助手', '能跟助手说话；助手按他的库权限干活（"看"的库只读，"改"的库能写）'],
  ['machine', '助手能动这台电脑', '命令行、Python、装包、读写库以外的文件、改平台、派外部执行器'],
  ['export', '导出和下载', '下载原始文件，导出 Excel、Word、PDF']];
const when = (seconds) => seconds ? fmt.clock(seconds) : '—';

export function settingsTabs(current) {
  return h('nav.tabs', {}, [['', '常用'], ['users', '用户和权限']].map(([key, label]) => h('a.tab' + (key === current ? '.on' : ''), {href: '#/settings' + (key ? '/' + key : '')}, label)));
}

// 按身份给一套默认（和后台 people.rights_for 一样）
function preset(roles, role, libraries, knowledge) {
  const base = roles[role] || roles['只看'];
  if (base.all) return {libraries: {}, assistant: true, machine: true, export: true, home: '总览'};
  const levels = Object.fromEntries(libraries.filter((name) => name !== knowledge).map((name) => [name, base.level]));
  if (base.knowledge) levels[knowledge] = base.knowledge;
  return {libraries: levels, assistant: base.assistant, machine: base.machine, export: base.export, home: base.home};
}

// 一个人的权限编辑：身份、各库等级、开关、打开后先到、有效期
function editor(data, start, {title, submit, onDone, onCancel}) {
  const draft = {name: start.name || '', note: start.note || '', role: start.role || '研发工程师', rights: JSON.parse(JSON.stringify(start.rights || preset(data.roles, '研发工程师', data.libraries, data.knowledge))),
    expires: start.expires || 0};
  const box = h('div.user-editor');
  const draw = () => {
    const admin = draft.role === '管理员';
    box.replaceChildren(
      h('div.ue-head', {}, h('b', {}, title)),
      h('div.ue-grid', {},
        h('label', {}, '名字', h('input', {type: 'text', value: draft.name, placeholder: '可以留空，他开通时自己写', oninput: (e) => { draft.name = e.target.value; }})),
        h('label', {}, '备注', h('input', {type: 'text', value: draft.note, placeholder: '例：合成组实验员', oninput: (e) => { draft.note = e.target.value; }})),
        h('label', {}, '身份', h('select', {onchange: (e) => { draft.role = e.target.value; draft.rights = preset(data.roles, draft.role, data.libraries, data.knowledge); draw(); }},
          Object.keys(data.roles).map((role) => h('option', {selected: role === draft.role}, role)))),
        h('label', {}, '有效期到', h('input', {type: 'date', value: draft.expires ? new Date(draft.expires * 1000 + 8 * 3600e3).toISOString().slice(0, 10) : '',
          onchange: (e) => { draft.expires = e.target.value ? Math.round(new Date(e.target.value + 'T23:59:59+08:00').getTime() / 1000) : 0; }}), h('small', {}, '留空 = 一直有效'))),
      admin ? h('p.ue-admin', {}, icon('check', 14), '管理员什么都能做：所有库（包括以后新建的）、用户和权限、所有设置、助手的全部本事。')
        : h('div.ue-rights', {},
          h('div.ue-libs', {}, data.libraries.map((name) => h('div.ue-lib', {},
            h('span.ue-lib-name', {}, name === data.knowledge ? '知识库（文献、命题、学习）' : name),
            h('div.segs', {}, LEVELS.map((level) => h('button.seg' + ((draft.rights.libraries[name] || '') === level ? '.on' : ''), {type: 'button',
              title: {'': '看不到这个库', 看: '所有页面、图、报告都能看，不能改', 填: '看 + 现场填写：称量、批记录实测、导入测试文件', 改: '填 + 建和改设计、配方、记录、分析做法，拍板卡片'}[level],
              onclick: () => { if (level) draft.rights.libraries[name] = level; else delete draft.rights.libraries[name]; draw(); }}, LEVEL_TEXT[level])))))),
          h('div.ue-switches', {}, SWITCHES.map(([key, label, about]) => h('label.ue-switch', {title: about},
            h('input', {type: 'checkbox', checked: !!draft.rights[key], onchange: (e) => { draft.rights[key] = e.target.checked; }}), h('span', {}, label), h('small', {}, about)))),
          h('label.ue-home', {}, '登录后先到', h('div.segs', {}, [['总览', '总览'], ['现场', '手机现场页（称量、批记录）']].map(([value, label]) =>
            h('button.seg' + ((draft.rights.home || '总览') === value ? '.on' : ''), {type: 'button', onclick: () => { draft.rights.home = value; draw(); }}, label))))),
      h('div.ue-actions', {}, h('button.btn.primary', {type: 'button', onclick: () => onDone(draft)}, submit), h('button.btn.ghost', {type: 'button', onclick: onCancel}, '取消')));
  };
  draw();
  return box;
}

// 邀请链接：链接 + 二维码（手机一扫就打开）+ 复制
async function linkCard(link, who) {
  const qr = h('div.invite-qr');
  const card = h('div.invite-card', {},
    h('div', {}, h('b', {}, `给${who || '他'}的邀请链接`), h('p.quiet.small', {}, '7 天内有效，只能用一次。发给他（微信、邮件都行），或者让他用手机扫右边的码。他打开后自己设账号、密码，扫验证器。'),
      h('div.invite-link', {}, h('code', {}, link), h('button.btn.small', {type: 'button', onclick: async () => { await navigator.clipboard.writeText(link).catch(() => {}); toast('链接复制好了'); }}, '复制'))),
    qr);
  if (!window.qrcode) await new Promise((resolve) => { const s = h('script', {src: '/web/vendor/qrcode.js'}); s.onload = resolve; document.head.append(s); });
  const code = window.qrcode(0, 'M'); code.addData(link); code.make();
  qr.innerHTML = code.createImgTag(4, 0);
  return card;
}

export async function usersPage(main, state) {
  state.anchor('设置 · 用户和权限');
  const data = await api('/users');
  const host = h('div');
  const out = h('div');
  let editing = null;
  const reload = async () => Object.assign(data, await api('/users')) && draw();
  const levelChips = (person) => person.role === '管理员' ? [h('span.st-tag.done', {}, '全部')] :
    Object.entries(person.rights.libraries || {}).map(([name, level]) => h('span.st-tag' + (level === '改' ? '.done' : level === '填' ? '.current' : ''), {}, `${name === data.knowledge ? '知识库' : name} ${level}`));
  const status = (person) => person.status === 'disabled' ? h('span.st-tag.off', {}, '已停用')
    : person.status === 'invited' ? h('span.st-tag.open', {}, person.invite_expires * 1000 > Date.now() ? `待开通 · 邀请到 ${when(person.invite_expires)}` : '邀请过期了')
    : person.expires && person.expires * 1000 < Date.now() ? h('span.st-tag.off', {}, '过了有效期')
    : person.online ? h('span.st-tag.done', {}, '在线') : h('span.st-tag', {}, '已开通');
  const row = (person) => {
    if (editing === person.id) {
      return editor(data, person, {title: `改 ${person.name || person.username || '这个人'} 的权限`, submit: '存', onCancel: () => { editing = null; draw(); },
        onDone: async (draft) => { await api(`/users/${enc(person.id)}`, 'PUT', draft); editing = null; toast('改好了，马上生效'); reload(); }});
    }
    return h('div.user-row' + (person.status === 'disabled' ? '.off' : ''), {},
      h('div.ur-who', {}, h('span.ur-avatar', {}, (person.name || person.username || '新')[0]),
        h('div', {}, h('b', {}, person.name || '（还没开通）'), h('div.quiet.small', {}, [person.username, person.note].filter(Boolean).join(' · ') || '等他打开邀请链接'))),
      h('div.ur-role', {}, h('span.mini-chip', {}, person.role), status(person)),
      h('div.ur-rights', {}, ...levelChips(person),
        ...(person.role === '管理员' ? [] : SWITCHES.filter(([key]) => person.rights[key]).map(([, label]) => h('span.st-tag', {}, label)))),
      h('div.ur-seen', {}, h('small.quiet', {}, '最近登录'), h('span', {}, when(person.last_login)), person.last_ip ? h('small.quiet', {}, person.last_ip) : null),
      h('div.ur-actions', {},
        h('button.btn.small', {type: 'button', onclick: () => { editing = person.id; draw(); }}, '改权限'),
        h('details.ur-more', {}, h('summary.btn.ghost.small', {}, '更多'),
          h('div.ur-menu', {},
            h('button', {type: 'button', title: '忘了密码、换了手机：发一个新邀请，重设密码、重新扫验证器', onclick: async () => {
              const result = await api(`/users/${enc(person.id)}/reinvite`, 'POST'); out.replaceChildren(await linkCard(result.link, person.name)); reload(); }}, '重发邀请（忘密码、换手机）'),
            h('button', {type: 'button', onclick: async () => { await api(`/users/${enc(person.id)}/kick`, 'POST'); toast('他所有设备都退出了，要重新登录'); }}, '所有设备退出登录'),
            h('button', {type: 'button', onclick: async () => { await api(`/users/${enc(person.id)}`, 'PUT', {enabled: person.status === 'disabled'}); reload(); }}, person.status === 'disabled' ? '启用' : '停用'),
            h('button.danger', {type: 'button', onclick: async () => { if (!confirm(`删掉 ${person.name || '这个邀请'}？他就登录不了了（库里的数据不动）。`)) return; await api(`/users/${enc(person.id)}`, 'DELETE'); reload(); }}, '删掉')))));
  };
  const draw = () => {
    const pub = h('input', {type: 'text', value: data.public_url, placeholder: '例：https://lab.你的域名.com', style: 'min-width:280px'});
    host.replaceChildren(
      h('section.block.wide.door-card', {},
        h('header.block-head', {}, h('h3', {}, '外网访问'), h('span.grow'),
          data.public_port ? h('span.st-tag.done', {}, `外网访问口在 127.0.0.1:${data.public_port}`) : h('span.st-tag.off', {}, '外网访问口没开')),
        h('div.door-grid', {},
          h('div', {}, h('b', {}, '内网穿透指向'), h('code', {}, `127.0.0.1:${data.public_port || 8860}`), h('p.quiet.small', {}, '外面来的每个请求都要登录（账号 + 密码 + 验证器）。不要把 8850 做穿透：8850 是本机管理端，不用登录。')),
          h('div', {}, h('b', {}, '外网地址'), h('div.row', {}, pub, h('button.btn.small', {type: 'button', onclick: async () => {
            await api('/settings', 'PUT', {public_url: pub.value.trim()}); data.public_url = pub.value.trim(); toast('存好了：邀请链接和手机推送里的链接都用它'); }}, '存')),
            h('p.quiet.small', {}, '内网穿透给的域名。邀请链接、推送到手机的链接都用它；不填就只能在这台电脑上试。')))),
      !data.admin_ready ? h('div.notice-card', {}, icon('lock', 18), h('div', {}, h('b', {}, '先给自己开一个管理员账号'), h('p.quiet.small', {}, '在外面登录要用。点"邀请"，身份选"管理员"，用手机打开链接，设密码、扫验证器。在这台电脑上用不需要登录。'))) : null,
      h('section.block.wide', {},
        h('header.block-head', {}, h('h3', {}, `用户（${data.people.length}）`), h('span.grow'),
          h('button.btn.primary.small', {type: 'button', onclick: () => { editing = 'new'; draw(); }}, icon('plus', 13), '邀请')),
        editing === 'new' ? editor(data, {role: data.admin_ready ? '研发工程师' : '管理员', rights: data.admin_ready ? null : {libraries: {}}}, {title: '邀请一个人', submit: '出邀请链接',
          onCancel: () => { editing = null; draw(); },
          onDone: async (draft) => { const result = await api('/users', 'POST', draft); editing = null; out.replaceChildren(await linkCard(result.link, draft.name)); reload(); }}) : null,
        out,
        data.people.length ? h('div.user-list', {}, data.people.map(row)) : h('p.quiet', {}, '还没有用户。点"邀请"给自己或同事开账号。'),
        h('p.block-note', {}, '等级：看 = 所有页面能看；填 = 再加现场填写（称量、批记录实测、导入测试文件）；改 = 再加建和改设计、配方、记录、分析做法，拍板卡片。改权限马上生效，他下一步操作就按新的。')));
  };
  draw();
  main.replaceChildren((await import('./views.js')).header('设置', '用户和权限、外网访问'), settingsTabs('users'), h('div.page-body', {}, host));
}

export async function mePage(main, state) {
  state.anchor('我的');
  const {header} = await import('./views.js');
  const me = await api('/auth/me');
  const who = me.person;
  if (!me.public) {
    main.replaceChildren(header('我的', '账号'), h('div.page-body', {}, h('section.block.wide', {}, h('p', {}, '你在这台电脑上，就是管理员，不用登录。'),
      h('a.btn', {href: '#/settings/users'}, '用户和权限'))));
    return;
  }
  const old = h('input', {type: 'password', autocomplete: 'current-password'}), fresh = h('input', {type: 'password', autocomplete: 'new-password'}),
    code = h('input.code-in', {inputmode: 'numeric', maxlength: 6, placeholder: '验证码'});
  const rights = who.admin ? [h('span.st-tag.done', {}, '管理员：什么都能做')]
    : [...Object.entries(who.rights.libraries || {}).map(([name, level]) => h('span.st-tag', {}, `${name} ${level}`)),
      ...SWITCHES.filter(([key]) => who.rights[key]).map(([, label]) => h('span.st-tag.done', {}, label))];
  const logout = async (forget) => { await api('/auth/logout', 'POST', {forget}); location.href = '/login'; };
  main.replaceChildren(header('我的', `${who.name} · ${who.role}`), h('div.page-body', {}, h('div.me-grid', {},
    h('section.block', {}, h('header.block-head', {}, h('h3', {}, '我的账号')),
      h('div.me-who', {}, h('span.ur-avatar.big', {}, (who.name || '我')[0]), h('div', {}, h('b', {}, who.name), h('div.quiet', {}, `账号 ${who.username} · ${who.role}`))),
      h('div.me-rights', {}, rights),
      h('p.quiet.small', {}, `备用码还剩 ${me.recovery_left ?? 0} 个（手机不在身边时代替验证码）。用完了、手机丢了：找管理员重发邀请。`)),
    h('section.block', {}, h('header.block-head', {}, h('h3', {}, '改密码')),
      h('div.me-form', {}, h('label', {}, '原密码', old), h('label', {}, '新密码（至少 8 位）', fresh), h('label', {}, '验证器里的 6 位数', code),
        h('button.btn.primary', {type: 'button', onclick: async () => { await api('/auth/password', 'POST', {old: old.value, new: fresh.value, code: code.value}); old.value = fresh.value = code.value = ''; toast('密码改好了'); }}, '改密码'))),
    h('section.block', {}, h('header.block-head', {}, h('h3', {}, '登录')),
      h('div.me-actions', {},
        h('button.btn', {type: 'button', onclick: async () => { await api('/auth/logout-others', 'POST'); toast('别的设备都退出了，这台接着用'); }}, '让别的设备都退出'),
        h('button.btn', {type: 'button', onclick: () => logout(false)}, '退出登录'),
        h('button.btn.ghost', {type: 'button', onclick: () => logout(true)}, '退出，并忘记这台设备')),
      h('p.quiet.small', {}, '"记住这台设备"30 天内只免输密码，验证码每次都要。一次登录管 8 小时。')),
    h('section.block', {id: 'wechat-box'}, h('header.block-head', {}, h('h3', {}, '微信推送')), h('p.quiet', {}, '正在读…')))));
  import('./wechat.js').then(({wechatBox}) => wechatBox(main.querySelector('#wechat-box'), {self: true})).catch(() => {});
}
