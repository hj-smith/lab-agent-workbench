// 项目改名、挪进项目、拖着排先后：合成（实验卡）、配方（配方卡）、测试 → GPC 左栏（样品）三处共用一个办法。
// 记录上的"所属项目"写了就按它归组，没写的按规则自动分；先后存在库设置里。
import {api, h, icon, toast} from './core.js';

const enc = encodeURIComponent;
const ITEMS = 'application/x-lab-items', GROUP = 'application/x-lab-project';
const opened = {};                                  // 每处哪些项目展开着（重画后照旧）

// 排过的项目按排的先后，没排过的（新出来的）放最前面，彼此照原来的顺序（和后台 workspace.ordered 一样）
export function orderBy(order, items, name = (item) => item.name) {
  const at = Object.fromEntries((order || []).map((value, index) => [value, index]));
  return items.map((item, index) => [item, index]).sort((a, b) => (at[name(a[0])] ?? -1) - (at[name(b[0])] ?? -1) || a[1] - b[1]).map(([item]) => item);
}

// 展开、收起记在页面里：改名、挪动后重画，刚才开着的还开着（force：比如正打开的样品在这组里，总是展开）
export function remember(details, scope, name, fallback, force = false) {
  const memory = opened[scope] ||= new Map();
  details.open = force || (memory.has(name) ? memory.get(name) : fallback);
  details.addEventListener('toggle', () => memory.set(name, details.open));
  return details;
}

// 一个小浮层：输名字（下拉里有已有的项目），回车 = 好，Esc 或点外面 = 取消
export function askName(anchor, {title, value = '', names = [], hint = '', clear = ''}) {
  return new Promise((resolve) => {
    document.querySelector('.name-pop')?.remove();
    const id = 'pop-names-' + Math.random().toString(36).slice(2, 7);
    const input = h('input', {type: 'text', value, list: id, placeholder: '项目名'});
    let pop;
    const done = (result) => { pop.remove(); document.removeEventListener('mousedown', outside, true); resolve(result); };
    const outside = (event) => { if (!pop.contains(event.target)) done(null); };
    pop = h('div.name-pop', {role: 'dialog', onclick: (event) => event.stopPropagation()},
      h('b', {}, title), hint ? h('p', {}, hint) : null, input, h('datalist', {id}, names.map((name) => h('option', {value: name}))),
      h('div.name-pop-actions', {}, clear ? h('button.btn.ghost.small', {type: 'button', onclick: () => done('')}, clear) : null, h('span.grow'),
        h('button.btn.ghost.small', {type: 'button', onclick: () => done(null)}, '取消'),
        h('button.btn.primary.small', {type: 'button', onclick: () => done(input.value.trim() || null)}, '好')));
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') done(input.value.trim() || null); if (event.key === 'Escape') done(null); });
    document.body.append(pop);
    const box = anchor.getBoundingClientRect();
    pop.style.left = Math.max(12, Math.min(box.left, innerWidth - pop.offsetWidth - 12)) + 'px';
    pop.style.top = (box.bottom + 6 + pop.offsetHeight > innerHeight ? Math.max(12, box.top - pop.offsetHeight - 6) : box.bottom + 6) + 'px';
    setTimeout(() => { document.addEventListener('mousedown', outside, true); input.focus(); input.select(); });
  });
}

// 一处的项目工具。scope：合成 / 配方 / GPC。names()：现在页面上项目的先后；after()：改完重画
export function projectTools({library, scope, names, after}) {
  const base = (path) => `/project-${path}/${enc(library)}/${enc(scope)}`;
  const move = async (items, name, label) => {
    await api(base('move'), 'POST', {ids: items.map((item) => item.id), name});
    after();
    // 撤销：每条回到它原来写的项目（原来没写的回到自动分组）
    toast(label || (name ? `挪到「${name}」了（${items.length} 条）` : `${items.length} 条回到自动分组`), async () => {
      const back = {};
      items.forEach((item) => (back[item.project || ''] ||= []).push(item.id));
      for (const [value, ids] of Object.entries(back)) await api(base('move'), 'POST', {ids, name: value});
      after();
    });
  };
  const reorder = async (dragged, target) => {      // 往下拖放到目标后面，往上拖放到目标前面
    const now = names(), down = now.indexOf(dragged) < now.indexOf(target);
    const list = now.filter((name) => name !== dragged);
    list.splice(list.indexOf(target) + (down ? 1 : 0), 0, dragged);
    await api(base('order'), 'PUT', {names: list});
    after();
  };
  const dragging = (on) => document.body.classList.toggle('dragging-items', on);

  // 卡片、样品行：能拖到项目标题上（勾了好几条时拖哪条都带上勾的那些）
  const item = (node, items) => {
    node.draggable = true;
    node.addEventListener('dragstart', (event) => {
      const list = typeof items === 'function' ? items() : items;
      event.dataTransfer.setData(ITEMS, JSON.stringify(list));
      event.dataTransfer.setData('text/plain', list.map((entry) => entry.name || entry.id).join('、'));
      event.dataTransfer.effectAllowed = 'move';
      dragging(true);
    });
    node.addEventListener('dragend', () => dragging(false));
    return node;
  };

  // 项目标题：拖它排先后；把卡片、样品拖到它上面就挪进来；笔 = 改名
  const head = (summary, project) => {
    summary.draggable = true;
    summary.classList.add('grp-head');
    summary.title = '拖标题排先后；把卡片或样品拖到这里就挪进这个项目';
    summary.addEventListener('dragstart', (event) => { event.dataTransfer.setData(GROUP, project.name); event.dataTransfer.effectAllowed = 'move'; document.body.classList.add('dragging-group'); });
    summary.addEventListener('dragend', () => document.body.classList.remove('dragging-group'));
    const accepts = (event) => event.dataTransfer.types.includes(ITEMS) || event.dataTransfer.types.includes(GROUP);
    summary.addEventListener('dragover', (event) => { if (!accepts(event)) return; event.preventDefault(); summary.classList.add('drop-on'); });
    summary.addEventListener('dragleave', () => summary.classList.remove('drop-on'));
    summary.addEventListener('drop', async (event) => {
      summary.classList.remove('drop-on');
      if (!accepts(event)) return;
      event.preventDefault();
      dragging(false);
      const group = event.dataTransfer.getData(GROUP);
      if (group) { if (group !== project.name) await reorder(group, project.name); return; }
      const items = JSON.parse(event.dataTransfer.getData(ITEMS) || '[]').filter((entry) => (entry.group ?? entry.project) !== project.name);
      if (items.length) await move(items, project.value === undefined ? project.name : project.value, project.value === null ? `${items.length} 条挪到「${project.name}」（不归项目）` : '');
    });
    if (project.fixed) return summary;              // "其它"这种不是真项目名的，不给改名
    const pen = h('button.grp-edit', {type: 'button', title: '改项目名', 'aria-label': '改项目名', onclick: async (event) => {
      event.preventDefault(); event.stopPropagation();
      const mine = project.mine;
      const value = await askName(pen, {title: '项目改名', value: project.name, names: [],
        hint: project.record ? '这个项目就是那条 DOE 研究，改的是它的名字。' : mine ? '' : '这个项目是按规则自动分的；改名后组里每条都记下新名字，以后不再自动分。',
        clear: mine && !project.record && scope !== 'GPC' ? '拆掉，回到自动分组' : ''});
      if (value == null || value === project.name) return;
      if (value === '') { await move(project.items, null); return; }
      await api(base('rename'), 'POST', {old: project.name, new: value, ids: project.items.map((entry) => entry.id), record: project.record || null});
      after();
      toast(`改名为「${value}」`);
    }}, icon('edit', 13));
    summary.append(pen);
    return summary;
  };

  // 拖着卡片时出现的"新建项目"落点
  const newZone = () => {
    const zone = h('div.grp-new', {}, icon('plus', 14), '拖到这里，新建一个项目');
    zone.addEventListener('dragover', (event) => { if (!event.dataTransfer.types.includes(ITEMS)) return; event.preventDefault(); zone.classList.add('drop-on'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drop-on'));
    zone.addEventListener('drop', async (event) => {
      zone.classList.remove('drop-on');
      if (!event.dataTransfer.types.includes(ITEMS)) return;
      event.preventDefault();
      dragging(false);
      const items = JSON.parse(event.dataTransfer.getData(ITEMS) || '[]');
      const name = await askName(zone, {title: `新项目（${items.length} 条挪进去）`, names: names()});
      if (name) await move(items, name);
    });
    return zone;
  };

  // 勾了几条后"挪到项目…"：选已有的或写个新名字
  const moveButton = (items, label = '挪到项目') => h('button.btn.small', {type: 'button', title: '勾上的挪到一个项目（选已有的，或写新名字）', onclick: async (event) => {
    const list = typeof items === 'function' ? items() : items;
    if (!list.length) { toast('先勾几条'); return; }
    const name = await askName(event.currentTarget, {title: `挪到项目（${list.length} 条）`, names: names(), clear: scope === 'GPC' ? '' : '回到自动分组'});
    if (name == null) return;
    await move(list, name || null);
  }}, icon('folder', 13), label);

  return {item, head, newZone, moveButton};
}
