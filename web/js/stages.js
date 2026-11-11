// 按研发环节的页面：合成、配方、测试、研发台；实验设计的投料表、配方组成编辑、约定口径。
import {api, go, h, icon, listen, toast} from './core.js';
import {COLORS, chart, number, tableBlock} from './blocks.js';
import {kindView} from './grid.js';
import {agingImport, agingSample, ellDay, gpcAnalysis, gpcBatch, nkGroup} from './testing.js';
import {orderBy, projectTools, remember} from './grouping.js';
import {reportBar} from './reports.js';
import {traceDesk} from './trace.js';
import {processFigure} from './work.js';

const enc = encodeURIComponent;
const tabs = (base, items, current) => h('nav.tabs', {}, items.map(([key, label, count]) => h('a.tab' + (key === current ? '.on' : ''), {href: `#/${base}/${enc(key)}`}, label, count != null ? h('small', {}, count) : null)));
const focusMatch = (state, ...texts) => !state.focus || texts.some((text) => String(text || '').toUpperCase().includes(state.focus.toUpperCase()));

// ---------- 合成 ----------

export async function synthesis(main, state, header, [tab = 'experiments'], resinCards) {
  const ask = (text) => state.panel.ask(text, '合成');
  const head = header('合成', '树脂合成：实验、树脂批、物料', h('button.btn.primary', {type: 'button', onclick: () => ask('我要设计一个新的树脂合成实验。先看最近几个实验的结果和研发路线上的待办，建议下一个实验怎么做（路线、投料比、温度时间、要验证什么），我确认后建设计。')}, icon('plus', 16), '设计新实验'));
  const nav = tabs('synthesis', [['experiments', '实验'], ['lots', '树脂批'], ['materials', '物料']], tab);
  if (tab === 'experiments') {
    const {rows, projects} = await api('/experiments/' + enc(state.library));
    state.anchor('合成 · 实验');
    main.replaceChildren(head, nav, h('div.page-body', {}, resinCards(state, rows.filter((row) => focusMatch(state, row.code, row.title, row.parent)), projects)));
    return;
  }
  const kind = tab === 'lots' ? '树脂批' : '树脂物料';
  state.anchor('合成 · ' + kind);
  const {node} = await kindView(state, kind, {ask});
  main.replaceChildren(head, nav, h('div.page-body', {}, node));
}

// 实验的"设计"页：投料方式和数量、变量，改了用原核心全表重算
// 步骤卡片左边的颜色说明这一步是干什么的（和旧版设计页一样：加料蓝、加热红、取样青、后处理土黄、干燥绿）
const KIND_TONE = {装置: 'prep', 准备: 'prep', 投料: 'add', 加料: 'add', 中和: 'add', 终止: 'add', 稀释: 'add', 升温: 'heat', 反应: 'heat', 降温: 'heat',
  取样: 'sample', 沉降: 'workup', 过滤: 'workup', 打浆: 'workup', 洗涤: 'workup', 干燥: 'dry', 恒重: 'dry'};

export async function designView(state, library, record) {
  let data = await api(`/design/${enc(library)}/${enc(record.id)}`);
  const box = h('div.view');
  const pending = {charges: {}, vars: {}};
  let draft = (data.editable_steps || []).map((step) => ({...step})), editing = null, order = false;
  const stepBar = h('div.step-bar');
  const dirtyBar = () => {
    const dirty = order || draft.some((step) => step.changed?.size);
    stepBar.replaceChildren(...(dirty ? [h('span', {}, '步骤改过了，还没存'), h('span.grow'),
      h('button.btn.ghost.small', {type: 'button', onclick: () => { draft = (data.editable_steps || []).map((step) => ({...step})); order = false; editing = null; dirtyBar(); draw(); }}, '不要这些改动'),
      h('button.btn.primary.small', {type: 'button', onclick: saveSteps}, icon('check', 13), '存步骤')] : []));
    stepBar.hidden = !dirty;
  };
  const saveSteps = async () => {
    const steps = draft.map((step) => ({id: step.id.startsWith('new-') ? 'new' : step.id, type: step.type,
      ...Object.fromEntries([...(step.changed || [])].map((key) => [key, step[key] ?? '']))}));
    data = await api(`/design/${enc(library)}/${enc(record.id)}/steps`, 'PUT', {steps});
    draft = (data.editable_steps || []).map((step) => ({...step})); order = false; editing = null; dirtyBar();
    toast('步骤存好了：过程图、SOP、批记录都跟着变'); draw();
  };
  const derive = async () => {
    const {code} = await api(`/design/${enc(library)}/${enc(record.id)}/next-code`);
    const next = prompt(`从 ${record.name} 派生一个新实验：投料、变量、步骤、取样计划都抄过去（实测和结论不抄），${record.name} 本身不动。新编号：`, code);
    if (!next) return;
    const out = await api(`/design/${enc(library)}/${enc(record.id)}/derive`, 'POST', {code: next});
    toast(`建好了 ${out.code}，父编号记成 ${record.name}`); location.hash = `#/record/${enc(library)}/${enc(out.id)}`;
  };
  dirtyBar();
  const draw = () => {
    const names = data.charges.map((c) => [c.id, c.name]);
    // 步骤卡：点一下就地改（标题、阶段、温度、时间、说明），能上移下移、删；改完点"存步骤"（工序和 SOP 一起改）
    const shown = Object.fromEntries(data.steps.map((step) => [step.id, step]));
    const values = Object.fromEntries([...data.vars, ...data.computed_vars].map((v) => [v.key, v.value]));
    const known = (text) => String(text).replace(/^\[(.+)\]$/, (whole, key) => typeof values[key] === 'number' ? `${number(values[key], 4)}（${key}）` : whole);   // [反应内温] → 100（反应内温）
    const card = (step) => {
      const index = draft.indexOf(step), view = shown[step.id] || {}, kind = view.kind || {text: '说明', ramp: '升温', hold: '反应', cool: '降温'}[step.type] || '';
      const set = (key, value) => { step[key] = value; (step.changed ||= new Set()).add(key); dirtyBar(); };
      if (editing !== step.id) {
        return h('li.step-card.editable.k-' + (KIND_TONE[kind] || 'other') + (step.changed?.size || step.id.startsWith('new') ? '.changed' : ''), {title: '点一下改这一步', onclick: () => { editing = step.id; draw(); }},
          h('div.sc-head', {}, h('span.sc-no', {}, String(index + 1).padStart(2, '0')), h('b', {}, step.title || '（没起名）'), kind ? h('span.sc-kind', {}, kind) : null),
          h('p', {}, step.changed?.has('text') || !view.text ? step.text : view.text),
          step.temp != null || step.hours ? h('div.sc-cond', {}, [step.temp ? `${known(step.temp)} °C` : '', step.hours && step.hours !== '0' ? `${known(step.hours)} h` : ''].filter(Boolean).join(' · ')) : null);
      }
      const move = (by) => { const at = draft.indexOf(step), to = at + by; if (to < 0 || to >= draft.length) return; draft.splice(at, 1); draft.splice(to, 0, step); order = true; dirtyBar(); draw(); };
      return h('li.step-card.editing', {},
        h('div.sc-edit', {},
          h('label', {}, '标题', h('input', {type: 'text', value: step.title, oninput: (e) => set('title', e.target.value)})),
          h('label', {}, '阶段', h('select', {onchange: (e) => set('stage', e.target.value)}, ['准备', '反应', '后处理'].map((name) => h('option', {selected: step.stage === name}, name)))),
          step.temp != null ? h('label', {}, '温度 °C', h('input', {type: 'text', value: step.temp, placeholder: '数或 [变量]', oninput: (e) => set('temp', e.target.value)})) : null,
          step.hours != null ? h('label', {}, '时间 h', h('input', {type: 'text', value: step.hours, placeholder: '数或 [变量]', oninput: (e) => set('hours', e.target.value)})) : null,
          h('label.wide', {}, '说明（SOP 里照这段话出）', h('textarea', {rows: 3, oninput: (e) => set('text', e.target.value)}, step.text))),
        h('div.sc-actions', {}, h('button.btn.ghost.small', {type: 'button', onclick: () => move(-1)}, '上移'), h('button.btn.ghost.small', {type: 'button', onclick: () => move(1)}, '下移'),
          h('button.btn.ghost.small', {type: 'button', onclick: () => { draft.splice(draft.indexOf(step), 1); order = true; editing = null; dirtyBar(); draw(); }}, '删掉这一步'),
          h('span.grow'), h('button.btn.small', {type: 'button', onclick: () => { editing = null; draw(); }}, '收起')));
    };
    const lane = (title, items) => h('div.step-lane', {}, h('h4', {}, title, h('small', {}, `${items.length} 步`)), h('ol.step-cards', {}, items.map(card)));
    box.replaceChildren(
      data.figure?.curve ? h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '过程图'), h('span.quiet.small', {}, '温度、加料、取样在一条时间轴上；下面是投料和操作的流程')), processFigure(data.figure)) : null,
      h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '关键参数'), h('span.quiet.small', {}, '改了点"重算并存"，投料、SOP、批记录都跟着变'), h('span.grow'),
        h('button.btn.small', {type: 'button', title: '抄这个设计建下一个实验（新编号），原来的不动', onclick: derive}, icon('plus', 13), '派生新实验')),
        h('div.param-strip', {}, data.vars.map((v) => h('label.param', {}, h('small', {}, v.label), h('span', {},
          h('input', {type: 'text', value: v.expr ?? '', onchange: (e) => { pending.vars[v.key] = e.target.value; }}), v.unit ? h('em', {}, v.unit) : null))))),
      h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '投料表（改方式或数量后点"重算并存"）'),
        h('button.btn.primary.small', {type: 'button', onclick: async () => {
          data = await api(`/design/${enc(library)}/${enc(record.id)}`, 'PUT', pending); pending.charges = {}; pending.vars = {}; toast('设计已存，投料表重算了'); draw();
        }}, icon('check', 13), '重算并存')),
        h('div.tablewrap.flat', {}, h('table.data.compact.edit-table', {}, h('thead', {}, h('tr', {}, ['物料', '角色', '方式', '数量或公式', '对哪个料', '计划 / g', 'mmol', 'mL', '实测 / g'].map((c) => h('th', {}, c)))),
          h('tbody', {}, data.charges.map((c) => {
            const change = (key, value) => { pending.charges[c.id] = {...(pending.charges[c.id] || {}), [key]: value}; };
            const a = c.amount || {};
            return h('tr', {}, h('td', {}, h('b', {}, c.name)), h('td', {}, c.role || ''),
              h('td', {}, h('select', {onchange: (e) => change('mode', e.target.value)}, Object.entries(data.modes).map(([key, label]) => h('option', {value: key, selected: a.mode === key}, label)))),
              h('td', {}, h('input', {type: 'text', value: a.value ?? '', style: 'width:150px', onchange: (e) => change('value', e.target.value)})),
              h('td', {}, ['eq', 'molpct'].includes(a.mode) ? h('select', {onchange: (e) => change('of', e.target.value)}, names.filter(([id]) => id !== c.id).map(([id, name]) => h('option', {value: id, selected: a.of === id}, name))) : ''),
              h('td.num', {}, number(c.g, 4)), h('td.num', {}, number(c.mmol, 4)), h('td.num', {}, typeof c.mL === 'number' ? number(c.mL, 4) : ''), h('td.num', {}, typeof c.actual === 'number' ? number(c.actual, 4) : ''));
          }))))),
      draft.length ? h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, `流程（${draft.length} 步）`), h('span.quiet.small', {}, '点一张卡就地改；温度、时间能写数或 [变量]'), h('span.grow'),
          h('select.add-step', {onchange: (e) => { if (!e.target.value) return; const type = e.target.value; e.target.value = '';
            const step = {id: 'new-' + Math.random().toString(36).slice(2, 7), type, title: {text: '新的一步', ramp: '升温', hold: '保温', cool: '降温'}[type], text: '', stage: '反应',
              temp: type === 'text' ? null : '25', hours: '0', changed: new Set(['title', 'text', 'stage', 'temp', 'hours'])};
            draft.push(step); editing = step.id; order = true; dirtyBar(); draw(); }},
            h('option', {value: ''}, '+ 加一步…'), [['text', '说明'], ['ramp', '升温'], ['hold', '保温'], ['cool', '降温']].map(([value, label]) => h('option', {value}, label)))),
        stepBar, h('div.step-lanes', {}, lane('准备与反应', draft.filter((step) => step.stage !== '后处理')), lane('后处理', draft.filter((step) => step.stage === '后处理')))) : null,
      h('details.block.wide.computed-box', {}, h('summary', {}, `由参数算出来的量（${data.computed_vars.length} 个）`),
        h('div.param-strip.readonly', {}, data.computed_vars.map((v) => h('div.param', {}, h('small', {}, v.label), h('span', {}, h('b', {}, number(v.value, 4)), v.unit ? h('em', {}, v.unit) : null))))),
      h('p.block-note', {}, '方式：质量、体积、mmol、当量（对某个料）、mol%（对某个料）、按目标固含补足、每克预计产品、公式（可写 [变量名]）。改完点"重算并存"，SOP 和批记录跟着变。'));
  };
  draw();
  return box;
}

// ---------- 配方 ----------

export async function formulation(main, state, header, [tab = 'formulas']) {
  const ask = (text) => state.panel.ask(text, '配方');
  const head = header('配方', '配方系列、工单与称量、备料与预混、原料、DOE',
    h('button.btn.primary', {type: 'button', onclick: () => ask('我要设计一个新配方（说清目标膜厚、n/k、用哪批树脂），你先建议组成，我确认后建配方。')}, icon('plus', 16), '新配方'));
  const nav = tabs('formulation', [['formulas', '配方'], ['orders', '工单'], ['prep', '备料与预混'], ['materials', '原料'], ['doe', 'DOE'], ['thickness', '膜厚曲线']], tab);
  state.anchor('配方 · ' + tab);
  if (tab === 'formulas') {
    // 按项目：旧配方程序里的实验系列（带着它的配方），其余按编号系列；每个项目能收起
    const {projects} = await api('/formula-projects/' + enc(state.library));
    const counts = (item) => Object.entries(item.counts).filter(([, v]) => v).map(([k, v]) => `${k} ${v}`).join(' · ');
    const total = (items, key) => items.reduce((sum, item) => sum + (item.counts[key] || 0), 0);
    // 项目标题拖着排先后、笔改名；配方卡拖到别的项目标题上就挪过去，拖到最下面"新建项目"就开一个新项目
    const tools = projectTools({library: state.library, scope: '配方', names: () => projects.map((project) => project.name), after: () => state.render()});
    const entry = (child, project) => ({id: child.record, name: child.name, project: project.mine ? project.name : '', group: project.name});
    const card = (child, project) => tools.item(h('a.series-item', {href: `#/record/${enc(state.library)}/${enc(child.record)}`},
      h('b', {}, child.name.replace(/^PA-|^PB-/, '')), h('span', {}, child.sub), h('small', {}, counts(child) || '还没有测试')), [entry(child, project)]);
    const shown = projects.map((project) => ({...project, items: project.items.filter((item) => focusMatch(state, item.name, project.name))})).filter((project) => project.items.length);
    const sections = shown.map((project, index) => remember(h('details.exp-project', {},
      tools.head(h('summary', {}, h('b', {}, project.name), h('span.mini-chip', {}, project.kind),
        h('span.quiet.small', {}, `${project.items.length} 个配方 · 膜 ${total(project.items, '膜')} · n/k ${total(project.items, 'n/k')} · 老化 ${total(project.items, '老化')} · 工单 ${total(project.items, '工单')}`),
        project.record ? h('a.small', {href: `#/record/${enc(state.library)}/${enc(project.record)}`, onclick: (event) => event.stopPropagation()}, '看系列') : null),
        {name: project.name, mine: project.mine, record: project.record, items: project.items.map((child) => entry(child, project))}),
      h('div.series-items', {}, project.items.map((child) => card(child, project)))), '配方', project.name, index < 3, !!state.focus));
    main.replaceChildren(head, nav, h('div.page-body', {}, h('p.grp-tip', {}, '拖项目标题排先后，点标题旁的笔改名；把配方卡拖到别的项目标题上就挪过去。'), sections, tools.newZone()));
    return;
  }
  if (tab === 'prep') {
    const {rows} = await api(`/lib/${enc(state.library)}/records?kind=${enc('工单')}`);
    const open = rows.filter((row) => row['状态'] !== '历史导入' && row['状态'] !== '已完成');
    const picked = new Set();
    const out = h('div');
    const plan = async () => {
      if (!picked.size) { toast('先勾工单'); return; }
      const result = await api(`/premix/${enc(state.library)}`, 'POST', {orders: [...picked]});
      out.replaceChildren(tableBlock({columns: ['预混液', '名称', '需求 / g', '库存 / g', '余量 / g', '要配 / g'], rows: (result.plans || []).map((p) => [p.source_id, p.name, p.demand_mass_g, p.stock_mass_g, p.extra_mass_g, p.make_mass_g ?? p.net_need_mass_g])}, state.library),
        (result.errors || []).length ? h('p.notice', {}, result.errors.map((e) => e.message).join('；')) : null);
    };
    const {node: premixes} = await kindView(state, '预混液', {ask});
    const {node: preps} = await kindView(state, '备料单', {ask});
    main.replaceChildren(head, nav, h('div.page-body', {},
      h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '算备料：勾几张要配的工单，合起来算每种预混液要配多少'), h('button.btn.primary.small', {type: 'button', onclick: plan}, '算备料')),
        open.length ? h('div.pick-row', {}, open.map((row) => h('label.pick-item', {}, h('input', {type: 'checkbox', onchange: (e) => { e.target.checked ? picked.add(row.id) : picked.delete(row.id); }}), `${row.name}（${row['配方编号'] || ''}）`))) : h('p.quiet', {}, '没有待配样的工单。'), out),
      h('h3.band-title', {}, '预混液'), premixes, h('h3.band-title', {}, '备料单'), preps));
    return;
  }
  if (tab === 'thickness') {
    const {thicknessPage} = await import('./thickness.js');
    const box = h('div.page-body', {}, h('p.quiet', {}, '正在读…'));
    main.replaceChildren(head, nav, box);
    await thicknessPage(state, box);
    return;
  }
  const kind = {orders: '工单', materials: '原料', doe: 'DOE研究'}[tab];
  const {node} = await kindView(state, kind, {ask});
  main.replaceChildren(head, nav, h('div.page-body', {}, node));
}

// 配方的"组成"页：每行可改，三项平衡和剂量四种口径实时算
export async function formulaEditor(state, library, record) {
  const data = await api(`/formula/${enc(library)}/${enc(record.id)}`);
  const fields = JSON.parse(JSON.stringify(data.fields));
  let calc = data.calc, dose = data.dose, basis = 'resin';
  const box = h('div.view');
  const sources = data.sources;
  const recalc = async () => { const out = await api(`/formula/${enc(library)}/calc`, 'POST', {fields}); calc = out.calc; dose = out.dose; draw(); };
  let timer = null;
  const later = () => { clearTimeout(timer); timer = setTimeout(recalc, 400); };
  const source = (row) => h('select', {onchange: (e) => { row.source_id = e.target.value; row.source_type = sources.find((s) => s.id === e.target.value)?.type; later(); }},
    sources.map((s) => h('option', {value: s.id, selected: s.id === row.source_id}, `${s.id}${s.name && s.name !== s.id ? ' · ' + s.name : ''}`)));
  const num = (row, key, width = 80) => h('input', {type: 'number', step: 'any', value: row[key] ?? '', style: `width:${width}px`, oninput: (e) => { row[key] = e.target.value === '' ? null : Number(e.target.value); later(); }});
  const section = (title, key, amountKey, unit) => h('section.block', {}, h('header.block-head', {}, h('h3', {}, title),
    h('button.btn.ghost.small', {type: 'button', onclick: () => { (fields[key] ||= []).push({id: `${key}-${Date.now()}`, source_id: sources[0]?.id, [amountKey]: 0, order: (fields[key] || []).length + 1}); draw(); later(); }}, icon('plus', 13), '加一行')),
    h('table.data.compact.edit-table', {}, h('tbody', {}, (fields[key] || []).map((row, index) => h('tr', {}, h('td', {}, source(row)), h('td', {}, num(row, amountKey)), h('td.quiet', {}, unit),
      key === '添加剂' ? h('td', {}, h('select', {onchange: (e) => { row.basis = e.target.value; later(); }}, [['solid', '占固体'], ['resin_solid', '占树脂固体'], ['total', '占总量'], ['ppm', 'ppm']].map(([v, l]) => h('option', {value: v, selected: (row.basis || 'solid') === v}, l)))) : null,
      h('td', {}, h('input', {type: 'text', value: row.note || '', placeholder: '备注', style: 'width:110px', onchange: (e) => { row.note = e.target.value; }})),
      h('td', {}, h('button.btn.ghost.small', {type: 'button', onclick: () => { fields[key].splice(index, 1); draw(); later(); }}, '×')))))));
  const draw = () => {
    const totals = calc?.totals || {}, checks = calc?.checks || {};
    const balance = [['质量差', checks.mass_delta_g], ['固体差', checks.solid_delta_g], ['溶剂差', checks.solvent_delta_g]];
    const doseRows = (dose?.rows || []).map((row) => [row.name || row.source_id, row.design_value ?? '', row.values?.[basis] ?? row[basis] ?? '', row.effective_mass_g, row.weigh_mass_g ?? row.planned_mass_g]);
    box.replaceChildren(
      h('section.block.wide.b-stats', {}, h('div.stat-row', {},
        h('div.stat', {}, h('div.stat-label', {}, '总量 / g'), num(fields, '总量', 100)),
        h('div.stat', {}, h('div.stat-label', {}, '固含（0–1）'), num(fields, '固含', 100)),
        ...balance.map(([label, value]) => h('div.stat' + (Math.abs(value || 0) < 1e-6 ? '.good' : '.warn'), {}, h('div.stat-label', {}, label), h('div.stat-value.small', {}, number(value, 4), h('small', {}, ' g')))),
        h('div.stat', {}, h('div.stat-label', {}, '补加溶剂'), h('div.stat-value.small', {}, number(totals.solvent_addition_g, 4), h('small', {}, ' g'))))),
      (calc?.errors || []).length ? h('p.notice', {}, calc.errors.map((e) => e.message).join('；')) : null,
      h('div.blocks', {}, section('树脂', '树脂', 'parts', '份'), section('添加剂', '添加剂', 'percent', '%'), section('溶剂', '溶剂', 'parts', '份'),
        h('section.block', {}, h('header.block-head', {}, h('h3', {}, '称量计划（现算）')), tableBlock({columns: ['原料', '名称', '计划 / g', '固体 / g', '溶剂 / g'], rows: (calc?.lines || []).map((l) => [l.source_id, l.name, l.planned_mass_g, l.solid_mass_g, l.solvent_mass_g])}, library))),
      dose?.rows ? h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '剂量'), h('div.segs', {}, [['resin', '占树脂固体'], ['solid', '占总固体'], ['total', '占总量'], ['ppm', 'ppm']].map(([key, label]) =>
        h('button.seg' + (basis === key ? '.on' : ''), {type: 'button', onclick: () => { basis = key; draw(); }}, label)))),
        tableBlock({columns: ['原料', '原设计值', '这个口径', '有效份 / g', '要称 / g'], rows: doseRows}, library)) : null,
      h('div.row', {}, h('button.btn.primary', {type: 'button', onclick: async () => { await api(`/formula/${enc(library)}/${enc(record.id)}`, 'PUT', {fields}); toast('配方已存（修订号加 1）；已开的工单不受影响'); }}, icon('check', 14), '存配方'),
        h('span.quiet.small', {}, '改动随时重算；点"存配方"才写回。已开的工单是冻结的，不跟着变。')));
  };
  draw();
  return box;
}

// ---------- 测试 ----------

export async function testing(main, state, header, [tab = 'gpc', item]) {
  const library = state.library;
  const head = header('测试', 'GPC、椭偏膜厚、n/k、储存老化；拖、勾、改都在这里，结果用原算法算、存下来');
  const nav = tabs('testing', [['gpc', 'GPC'], ['ell', '椭偏膜厚'], ['nk', 'n/k'], ['aging', '储存老化'], ['import', '导入']], tab);
  state.anchor(`测试 · ${tab}${item ? ' · ' + item : ''}`);
  const layout = (list, body) => h('div.split.test-split' + (item ? '.has-item' : ''), {}, h('nav.kind-list.test-list', {}, list),
    h('div.split-main', {}, item ? h('a.mobile-back', {href: `#/testing/${tab}`}, '‹ 回到列表') : null, body));
  const waiting = {gpc: '正在读这个样品的色谱、基线和积分区间…', ell: '正在算这一天每块膜的统计、分组和参比判定…', nk: '正在算这一组的均值和 ±σ…', aging: '正在读这个样品的谱和动力学…'}[tab];
  const body = h('div', {}, item ? h('div.loading-note', {}, h('span.spinner'), waiting || '正在打开…', h('div.skeleton', {}, h('i.mid'), h('i.big'), h('i'))) : h('p.quiet', {}, '在左边选一个。'));
  if (tab === 'gpc') {
    // 每条样品前能勾，跨组、跨实验挑几条，底下"叠加所选"出色谱叠加图；地址里记着挑了哪些（#/testing/gpc/叠加:id,id…），刷新、退回都还在
    const [{rows}, {rows: experiments}, {curves}, {order}] = await Promise.all([api(`/lib/${enc(library)}/records?kind=${enc('GPC样品')}`), api(`/lib/${enc(library)}/records?kind=${enc('GPC实验')}`),
      api(`/gpc-cal/${enc(library)}`), api(`/project-order/${enc(library)}/GPC`)]);
    const expId = item?.startsWith('实验:') ? item.slice(3) : null;
    const curveId = item?.startsWith('曲线:') ? item.slice(3) : null;
    const overlayIds = item?.startsWith('叠加:') ? item.slice(3).split(',').filter(Boolean) : null;
    const keepOwn = item?.startsWith('核对锚点:');                   // 各自现在的做法、锚点贴到谱上：核对锚点不在谱上的样
    const batchIds = item?.startsWith('批处理:') || keepOwn ? item.slice(item.indexOf(':') + 1).split(',').filter(Boolean) : null;
    const picked = state.gpcPicked ||= new Set(overlayIds || batchIds || []);
    const groups = {};
    rows.filter((row) => focusMatch(state, row.name, row['所属项目'])).forEach((row) => (groups[row['所属项目'] || '其它'] ||= []).push(row));
    // 项目：标题拖着排先后、笔改名；样品拖到别的项目标题上就挪过去（勾了几条，拖哪条都一起挪）
    const sortedGroups = orderBy(order, Object.entries(groups).sort((a, b) => b[0].localeCompare(a[0], 'zh-CN', {numeric: true})), ([name]) => name);
    const tools = projectTools({library, scope: 'GPC', names: () => sortedGroups.map(([name]) => name), after: () => state.render()});
    const entry = (row) => ({id: row.id, name: row.name, project: row['所属项目'] || '', group: row['所属项目'] || '其它'});
    const pickedEntries = () => rows.filter((row) => picked.has(row.id)).map(entry);
    const bar = h('div.pick-bar');
    const drawBar = () => bar.replaceChildren(picked.size ? h('b', {}, `已挑 ${picked.size} 条`) : h('span.quiet.small', {}, '勾几条样品（可以跨组）就能叠在一起比，或一起批处理'),
      picked.size ? h('a.btn.primary.small', {href: `#/testing/gpc/${enc('叠加:' + [...picked].join(','))}`}, icon('compare', 13), '叠加所选') : null,
      picked.size ? h('a.btn.small', {href: `#/testing/gpc/${enc('批处理:' + [...picked].join(','))}`, title: '给勾上的样品一起套一个分析模板（或抄某个样品的做法、换标准曲线），先预览再存'}, icon('layers', 13), '批处理所选') : null,
      picked.size > 1 ? h('button.btn.small', {type: 'button', title: '勾上的样品按反应时间排成一个实验（时间从名字里读，3H → 3 h），看分子量怎么长、分布怎么变', onclick: async () => {
        const name = prompt('实验叫什么？', '新的 GPC 实验'); if (!name) return;
        const out = await api(`/gpc-exp/${enc(library)}`, 'POST', {name, ids: [...picked]}); picked.clear(); location.hash = `#/testing/gpc/${enc('实验:' + out.id)}`;
      }}, icon('chart', 13), '建成实验') : null,
      picked.size ? tools.moveButton(pickedEntries) : null,
      picked.size ? h('button.btn.ghost.small', {type: 'button', onclick: () => { picked.clear(); list.forEach((group) => group.querySelectorAll('input').forEach((box) => { box.checked = false; })); drawBar(); }}, '清空') : null);
    const tick = (row) => h('input', {type: 'checkbox', checked: picked.has(row.id), title: '挑出来叠加', onchange: (event) => { event.target.checked ? picked.add(row.id) : picked.delete(row.id); drawBar(); }});
    const list = sortedGroups.map(([group, items]) => remember(h('details.pick-group', {},
      tools.head(h('summary', {}, h('span.grp-name', {}, group), h('small', {}, items.length)), {name: group, mine: group !== '其它', fixed: group === '其它', value: group === '其它' ? null : undefined, items: items.map(entry)}),
      h('button.pick-link.as-button', {type: 'button', onclick: () => { items.forEach((row) => picked.add(row.id)); location.hash = `#/testing/gpc/${enc('叠加:' + [...picked].join(','))}`; }}, icon('compare', 12), '这一组都加进来叠加'),
      h('button.pick-link.as-button', {type: 'button', onclick: () => { location.hash = `#/testing/gpc/${enc('批处理:' + items.map((row) => row.id).join(','))}`; }}, icon('layers', 12), '这一组批处理'),
      items.map((row) => tools.item(h('div.pick-row' + (row.id === item ? '.on' : ''), {}, tick(row), h('a.pick-link', {href: `#/testing/gpc/${enc(row.id)}`}, row.name)),
        () => picked.has(row.id) && picked.size > 1 ? pickedEntries() : [entry(row)]))), 'GPC', group, items.some((row) => picked.has(row.id)), items.some((row) => row.id === item)));
    list.push(tools.newZone());
    drawBar();
    const expList = experiments.length ? h('details.pick-group.exp-group', {open: true}, h('summary', {}, '实验（看演化）', h('small', {}, experiments.length)),
      experiments.map((row) => h('div.pick-row' + (row.id === expId ? '.on' : ''), {}, h('a.pick-link', {href: `#/testing/gpc/${enc('实验:' + row.id)}`}, icon('chart', 12), row.name)))) : null;
    const curveList = curves.length ? h('details.pick-group.exp-group', {open: !!curveId}, h('summary', {}, '标准曲线', h('small', {}, curves.length)),
      curves.map((row) => h('div.pick-row' + (row.id === curveId ? '.on' : ''), {}, h('a.pick-link', {href: `#/testing/gpc/${enc('曲线:' + row.id)}`, title: `${row.copies} 份副本，用在 ${row.uses} 个样`},
        icon('chart', 12), row.name.replace('GPC标准曲线-', ''), h('small.quiet', {}, ` ${row.uses}`), row.off ? h('span.st-tag', {}, '停用') : row.expired ? h('span.st-tag.open', {}, '过期') : row.confirmed ? h('span.st-tag.done', {}, '确认') : null)))) : null;
    main.replaceChildren(head, nav, layout([bar, expList, curveList, ...list], body));
    if (curveId) {
      const {gpcCalibration} = await import('./calibration.js');
      body.replaceChildren(h('div.test-head', {}, h('h2', {}, curves.find((row) => row.id === curveId)?.name || '标准曲线'), h('span.quiet.small', {}, '分子量可不可信全看它：标准点、拟合、每个点的影响、试改、和别的曲线比')),
        await gpcCalibration(state, curveId));
    } else if (expId) {
      const {gpcExperiment} = await import('./experiment.js');
      body.replaceChildren(h('div.test-head', {}, h('h2', {}, experiments.find((row) => row.id === expId)?.name || '实验'), h('span.quiet.small', {}, '一组样品按反应时间排开：分子量怎么长、分布怎么变、各区段占多少、像哪种动力学曲线')),
        reportBar(library, 'experiment', [expId]), await gpcExperiment(state, expId));
    } else if (batchIds) {
      body.replaceChildren(h('div.test-head', {}, h('h2', {}, keepOwn ? `核对锚点 ${batchIds.length} 条` : `批处理 ${batchIds.length} 条`), h('span.quiet.small', {}, keepOwn ? '各自现在的做法，只把锚点贴到自己的谱上：看贴上去对不对，对的勾上存' : '一起套同一套做法：先预览现在和套用后的分子量，勾了再存')),
        await gpcBatch(state, batchIds, rows, {keep: keepOwn}));
    } else if (overlayIds) {
      const {gpcOverlay} = await import('./overlay.js');
      body.replaceChildren(h('div.test-head', {}, h('h2', {}, `叠加 ${overlayIds.length} 条`), h('span.quiet.small', {}, '左边接着勾，再点"叠加所选"就更新')),
        reportBar(library, 'compare', overlayIds), await gpcOverlay(state, library, overlayIds, {title: 'GPC叠加', all: rows}));
    } else if (item) body.replaceChildren(h('div.test-head', {}, h('h2', {}, rows.find((row) => row.id === item)?.name || ''), h('a.btn.small', {href: `#/record/${enc(library)}/${enc(item)}`}, '看结果和分布')), reportBar(library, 'sample', [item]), await gpcAnalysis(state, item));
    return;
  }
  if (tab === 'ell') {
    const {days} = await api(`/ell/${enc(library)}`);
    const list = days.map((d) => h('a.pick-link' + (d.day === item ? '.on' : ''), {href: `#/testing/ell/${enc(d.day)}`}, d.day, h('small', {}, ` ${d.films} 块`)));
    main.replaceChildren(head, nav, layout(list, body));
    if (item) body.replaceChildren(await ellDay(state, item));
    return;
  }
  if (tab === 'nk') {
    const [{groups}, {pairs}] = await Promise.all([api(`/nk/${enc(library)}`), api(`/nk-pairs/${enc(library)}`)]);
    const pairKey = item?.startsWith('配对:') ? item.slice(3) : null;            // 配对:处理前组|处理后组
    const rounds = {};
    groups.filter((g) => focusMatch(state, g.group)).forEach((g) => (rounds[g.round || '其它'] ||= []).push(g));
    const list = Object.entries(rounds).sort((a, b) => b[0].localeCompare(a[0], 'zh', {numeric: true})).map(([round, items]) => h('details.pick-group', {open: items.some((g) => g.group === item)},
      h('summary', {}, round, h('small', {}, items.length)), items.map((g) => h('a.pick-link' + (g.group === item ? '.on' : ''), {href: `#/testing/nk/${enc(g.group)}`}, g.group, h('small', {}, ` ${g.included}/${g.count}`)))));
    const shownPairs = pairs.filter((pair) => focusMatch(state, pair.before));
    const pairList = shownPairs.length ? h('details.pick-group.exp-group', {open: !!pairKey}, h('summary', {}, '处理前后配对（Δn、Δk）', h('small', {}, shownPairs.length)),
      shownPairs.map((pair) => { const key = `${pair.before}|${pair.after}`;
        return h('a.pick-link' + (key === pairKey ? '.on' : ''), {href: `#/testing/nk/${enc('配对:' + key)}`}, icon('compare', 12), pair.before.replace(/^PA-|^PB-/, ''), h('small.quiet', {}, ' 前 → 后')); })) : null;
    // 两谱对比：任选两组（不一定是浸泡前后），同一页看 Δn、Δk
    const names = groups.map((g) => g.group);
    const [firstPick, secondPick] = pairKey ? pairKey.split('|') : ['', ''];
    const pickA = h('select', {}, h('option', {value: ''}, '第一组…'), names.map((name) => h('option', {value: name, selected: name === firstPick}, name)));
    const pickB = h('select', {}, h('option', {value: ''}, '第二组…'), names.map((name) => h('option', {value: name, selected: name === secondPick}, name)));
    const pickTwo = h('div.pick-two', {}, h('b.small', {}, '任选两组比'), pickA, pickB,
      h('button.btn.small', {type: 'button', onclick: () => { if (pickA.value && pickB.value && pickA.value !== pickB.value) location.hash = `#/testing/nk/${enc('配对:' + pickA.value + '|' + pickB.value)}`; else toast('选两组不一样的'); }}, icon('compare', 13), '比'));
    main.replaceChildren(head, nav, layout([pickTwo, pairList, ...list], body));
    if (pairKey) {
      const [before, after] = pairKey.split('|');
      const {nkPair} = await import('./nkpair.js');
      body.replaceChildren(h('div.test-head', {}, h('h2', {}, `${before} → ${after}`), h('span.quiet.small', {}, 'Δ = 第二组 − 第一组（浸泡前后配对时就是处理后 − 处理前）')), await nkPair(state, before, after));
    } else if (item) body.replaceChildren(h('div.test-head', {}, h('h2', {}, item)), await nkGroup(state, item));
    return;
  }
  if (tab === 'aging') {
    const {samples} = await api(`/aging/${enc(library)}`);
    const list = samples.filter((s) => focusMatch(state, s.sample)).map((s) => h('a.pick-link' + (s.sample === item ? '.on' : ''), {href: `#/testing/aging/${enc(s.sample)}`}, s.sample, h('small', {}, ` ${s.count} 条 · ${s.conditions.join('、')}`)));
    main.replaceChildren(head, nav, layout(list, body));
    body.replaceChildren(agingImport(state, () => state.rerender?.()));
    if (item) body.append(h('div.test-head', {}, h('h2', {}, item)), await agingSample(state, item));
    return;
  }
  // 导入：拖进仪器文件，认出是什么、挂到哪，人改对后一键导入
  main.replaceChildren(head, nav, h('div.page-body', {}, importQueue(state)));
}

// 导入：拖进来的文件、自动导入的文件夹里来的文件都在这里。认得的走原读法（GPC、标准曲线、n/k、椭偏、UV），
// 认得又没有拿不准的可以自动入库；拿不准的在"等你确认"里改好再导入；认不出的交给"数据导入"成员在后台处理。
const DOUBT = /要人|多个|请填写|可改 existing/;
const doubtsOf = (item) => [item.problem, item.type === '不认识' ? '认不出是什么文件' : null, ...(item.notes || []).filter((note) => DOUBT.test(note))].filter(Boolean);
const STATUS_TONE = {已入库: 'done', 交给助手: 'current', 跳过: 'todo', 读不了: 'off', 待确认: 'open'};

function importQueue(state) {
  const library = state.library;
  const box = h('div.intake');
  const known = (item) => item.type && item.type !== '不认识' && !item.problem;
  const kindList = h('datalist', {id: 'import-kinds'});
  api(`/lib/${enc(library)}/kinds`).then(({kinds}) => kindList.replaceChildren(...kinds.map((kind) => h('option', {value: kind.name}))));
  let data = {waiting: [], done: [], folders: []};

  const upload = async (files) => {
    toast(`正在认 ${files.length} 个文件…`);
    for (const file of files) data = await (await fetch(`/api/importq/${enc(library)}/${enc(file.name)}`, {method: 'PUT', body: file})).json();
    draw();
  };
  const picker = h('input', {type: 'file', multiple: true, hidden: true, onchange: () => { upload([...picker.files]); picker.value = ''; }});

  // 等你确认：每个文件一组，椭偏工作簿一块一行
  const waitingBlock = () => {
    const rows = data.waiting;
    if (!rows.length) return null;
    const start = async () => {
      data = await api(`/intake/${enc(library)}/go`, 'POST', {rows: rows.map((row) => ({id: row.id, items: row.items.map((item) => ({...item, route: item.route || (known(item) ? 'fast' : 'agent')}))}))});
      toast('开始了：直接导入的已入库并算好，交给助手的在后台做'); draw();
    };
    const lines = rows.flatMap((row) => row.items.map((item, index) => {
      const doubts = doubtsOf(item);
      return h('tr' + (doubts.length ? '.doubt' : ''), {},
        h('td', {}, h('select', {onchange: (e) => { item.route = e.target.value; }}, (known(item) ? [['fast', '直接导入'], ['agent', '交给助手'], ['skip', '不要']] : [['agent', '交给助手'], ['skip', '不要']])
          .map(([v, l]) => h('option', {value: v, selected: (item.route || (known(item) ? 'fast' : 'agent')) === v}, l)))),
        h('td', {}, index ? '' : h('div', {}, h('b', {}, row.file), h('div.quiet.small', {}, `${row.source} · ${row.at.slice(5, 16)}`))),
        h('td', {}, known(item) ? item.type : '（助手来认）'),
        h('td', {}, h('input', {type: 'text', value: item.kind || '', placeholder: '你来定，或留空让助手判断', style: 'width:120px', list: 'import-kinds', onchange: (e) => { item.kind = e.target.value; item.kindByPerson = true; }})),
        h('td', {}, h('input', {type: 'text', value: item.name || '', style: 'width:150px', onchange: (e) => { item.name = e.target.value; }})),
        h('td', {}, item.existing ? h('a', {href: `#/record/${enc(library)}/${enc(item.existing)}`}, '更新它') : '新建'),
        h('td', {}, (item.links || []).map((link) => h('div.small', {}, `${link.relation} → ${link.to_name || link.to}`))),
        h('td', {}, doubts.length ? h('div.doubt-text', {}, doubts.join('；')) : null,
          h('div.quiet.small', {}, known(item) ? (item.notes || []).filter((note) => !DOUBT.test(note)).join('；') : '交给"数据导入"成员处理；拿不准的会发卡片让你定'),
          h('input', {type: 'text', value: item.hint || '', placeholder: '写给助手的话（可不写）', style: 'width:170px', onchange: (e) => { item.hint = e.target.value; }})));
    }));
    const unsure = rows.reduce((n, row) => n + row.items.filter((item) => doubtsOf(item).length).length, 0);
    return h('section.block.wide', {},
      h('header.block-head', {}, h('h3', {}, `等你确认（${rows.length} 个文件）`), unsure ? h('span.mini-chip.warn', {}, `${unsure} 处拿不准`) : null, h('span.grow'),
        h('button.btn.primary.small', {type: 'button', onclick: start}, icon('check', 13), '开始')),
      h('div.tablewrap.flat', {}, h('table.data.compact.edit-table.intake-table', {},
        h('thead', {}, h('tr', {}, ['怎么处理', '文件', '认出是', '进哪一类', '名字', '已有同名', '挂到', '要看的'].map((c) => h('th', {}, c)))), h('tbody', {}, lines))));
  };

  // 自动导入的文件夹：仪器导出的地方；新文件、改过的文件约 30 秒内被看到
  const folderBlock = async () => {
    const {rules} = await api('/rules');
    const mine = rules.filter((rule) => rule.when?.source === '文件夹' && rule.when?.do === '导入' && (rule.library || null) === library);
    const save = async () => { await api('/rules', 'PUT', {rules}); data = await api(`/intake/${enc(library)}`); draw(); };
    const info = Object.fromEntries((data.folders || []).map((item) => [item.name, item]));
    const toggle = (rule, key, label, title) => h('label.check', {title}, h('input', {type: 'checkbox', checked: rule.when[key] !== false, onchange: async (e) => { rule.when[key] = e.target.checked; await save(); }}), label);
    const form = {folder: h('input', {type: 'text', placeholder: '文件夹路径，例 D:\\仪器导出\\GPC', style: 'width:340px'}),
      pattern: h('input', {type: 'text', value: '*', title: '只看这样的文件，例 *.csv、*.xlsx', style: 'width:90px'}),
      recursive: h('input', {type: 'checkbox'}), backfill: h('input', {type: 'checkbox'})};
    const add = async () => {
      const folder = form.folder.value.trim();
      if (!folder) return;
      const short = folder.replace(/[\\/]+$/, '').split(/[\\/]/).pop();
      rules.push({name: `自动导入 ${short}`, library, enabled: true, when: {source: '文件夹', do: '导入', folder, pattern: form.pattern.value.trim() || '*',
        recursive: form.recursive.checked, backfill: form.backfill.checked, auto: true, agent: true}});
      await save(); await api('/intake/scan', 'POST'); toast('加好了，马上看一遍');
    };
    return h('section.block.wide', {},
      h('header.block-head', {}, h('h3', {}, '自动导入的文件夹'), h('span.grow'),
        mine.length ? h('button.btn.small', {type: 'button', onclick: async () => { await api('/intake/scan', 'POST'); toast('正在看一遍，进来的文件会出现在下面'); setTimeout(async () => { data = await api(`/intake/${enc(library)}`); draw(); }, 4000); }}, icon('refresh', 13), '现在看一遍') : null),
      h('p.quiet.small', {}, '把仪器导出数据的文件夹加在这里：里面出现新文件或文件改过，约 30 秒内自动认出来。认得、没有拿不准的直接入库并算好；拿不准的放进"等你确认"；认不出的交给助手。原文件只读，不挪不改。'),
      mine.length ? h('div.folder-list', {}, mine.map((rule) => {
        const item = info[rule.name] || {};
        return h('div.folder-item' + (rule.enabled === false ? '.off' : ''), {},
          h('div.fi-head', {}, icon('folder', 15), h('b', {}, rule.when.folder), h('span.mini-chip', {}, rule.when.pattern || '*'), rule.when.recursive ? h('span.mini-chip', {}, '含子文件夹') : null,
            h('span.grow'), item.exists === false ? h('span.mini-chip.bad', {}, '找不到这个文件夹') : h('small.quiet', {}, `看过 ${item.seen || 0} 个文件 · 导入过 ${item.imported || 0} 个`)),
          h('div.fi-actions', {}, toggle(rule, 'auto', '认得的直接入库', '关掉：认得的也放进"等你确认"'), toggle(rule, 'agent', '认不出的交给助手', '关掉：认不出的也放进"等你确认"'),
            h('span.grow'), h('button.btn.ghost.small', {type: 'button', onclick: async () => { rule.enabled = rule.enabled === false; await save(); }}, rule.enabled === false ? '启用' : '停用'),
            h('button.btn.ghost.small', {type: 'button', title: '不再看这个文件夹（已导入的数据不动）', onclick: async () => { rules.splice(rules.indexOf(rule), 1); await save(); }}, icon('trash', 13))));
      })) : null,
      h('div.folder-add', {}, form.folder, form.pattern, h('label.check', {}, form.recursive, '含子文件夹'),
        h('label.check', {title: '不勾：只管以后新来的（以前的多半已经搬进来了）'}, form.backfill, '已有的文件也导入'),
        h('button.btn.small', {type: 'button', onclick: add}, icon('plus', 13), '加这个文件夹')));
  };

  // 最近导入
  const doneBlock = () => data.done.length ? h('section.block.wide', {},
    h('header.block-head', {}, h('h3', {}, '最近导入')),
    h('div.intake-done', {}, data.done.map((row) => h('div.done-item', {},
      h('span.st-tag.' + (STATUS_TONE[row.status] || 'todo'), {}, row.status),
      h('div.di-main', {}, h('div', {}, h('b', {}, row.file), h('small.quiet', {}, ` ${row.source} · ${(row.done_at || row.at).slice(5, 16)}`)),
        row.results.length ? h('div.di-records', {}, row.results.map((r) => r.id
          ? h('a.ref-chip' + (r.problem ? '.warn' : ''), {href: `#/record/${enc(library)}/${enc(r.id)}`, title: r.problem || (r.computed ? '已用原算法算好' : '')}, (r.name || r.file) + (r.problem ? ` · ${r.problem}` : r.computed ? ' · 算好了' : ''))
          : h('span.doubt-text', {}, `${r.file}：${r.problem || '没导入'}`))) : null,
        row.note ? h('div.doubt-text', {}, row.note) : null,
        row.lane ? h('a.small', {href: '#/assistant/' + enc(row.lane)}, '看助手在做什么 →') : null)))))
    : null;

  const draw = async () => {
    const zone = h('section.block.wide.drop-zone', {},
      h('header.block-head', {}, h('h3', {}, '导入文件'), h('button.btn.small', {type: 'button', onclick: () => picker.click()}, icon('upload', 13), '选文件'), picker),
      h('p.quiet', {}, '什么文件都可以拖进来，一次拖很多也行：仪器数据、实验记录、报告、文献 PDF、Excel、照片、核磁数据……认得的格式用原读法，其它的交给"数据导入"成员。'));
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('over'));
    zone.addEventListener('drop', (e) => { e.preventDefault(); zone.classList.remove('over'); upload([...e.dataTransfer.files]); });
    box.replaceChildren(zone, waitingBlock() || '', await folderBlock(), doneBlock() || '', kindList);
  };
  api(`/intake/${enc(library)}`).then((got) => { data = got; draw(); });
  const stop = listen((event) => { if (event.type === 'notice' && /进来 \d+ 个文件/.test(event.title || '')) api(`/intake/${enc(library)}`).then((got) => { data = got; draw(); }); });
  window.addEventListener('hashchange', stop, {once: true});
  return box;
}

// ---------- 研发台 ----------

export const DESK_TABS = [['lots', '树脂批总表'], ['factors', '因子对比'], ['next', '下一轮怎么做'], ['propose', '下一轮提议'], ['trace', '产品穿透'], ['health', '数据体检'], ['projects', '按项目'], ['gpc', 'GPC 对比']];

export async function desk(main, state, header, [tab = 'lots', ...rest]) {
  const library = state.library;
  if (tab === 'projects') { location.hash = '#/data'; return; }
  if (tab === 'gpc') { location.hash = '#/analysis'; return; }
  const head = header('研发台', '跨环节看：树脂批总表、因子对比、产品穿透、数据体检、按项目、GPC 对比');
  const nav = tabs('desk', DESK_TABS, tab);
  state.anchor('研发台 · ' + tab);
  if (tab === 'propose') {
    const {proposalsPage} = await import('./proposals.js');
    main.replaceChildren(head, nav, await proposalsPage(state));
    return;
  }
  if (tab === 'next') {
    const {scanAdvice} = await import('./scanadvice.js');
    main.replaceChildren(head, nav, await scanAdvice(state));
    return;
  }
  if (tab === 'trace') {
    main.replaceChildren(head, nav, h('div.page-body', {}, await traceDesk(state, library, rest[0] ? decodeURIComponent(rest[0]) : '')));
    return;
  }
  if (tab === 'lots') {
    const data = await api(`/desk/${enc(library)}/lots`);
    main.replaceChildren(head, nav, h('div.page-body', {}, h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, `树脂批总表（${data.rows.length} 批）`),
      h('button.btn.small', {type: 'button', onclick: () => state.panel.ask('看一下树脂批总表：哪些因子和结果关系最明显，哪些批的数据缺，下一步该补什么？', '研发台 · 树脂批总表')}, icon('spark', 13), '让助手看')),
      tableBlock({columns: data.columns.map((c) => c + (data.units?.[c] ? ` / ${data.units[c]}` : '')), rows: data.rows}, library, {limit: 200}))));
    return;
  }
  if (tab === 'factors') {
    const meta = await api(`/desk/${enc(library)}/factors`);
    const [factor = meta.factors[0], response = meta.responses[0]] = rest;
    const NAMES = {monoA_monoB_ratio: '单体比', catalyst: '催化剂', catalyst_molpct: '催化剂 mol%', main_solvent: '主溶剂', reaction_solids: '反应固含', addition_mode: '加料方式',
      reaction_temp: '反应温度', temperature: '反应温度', reaction_time: '反应时间', hold_time: '恒温时间', addh_eq: 'AddH 当量', quench: '终止', precipitation: '沉降', neutralizer: '中和',
      yield_pct: '产率 %', mn: 'Mn', mw: 'Mw', pdi: 'PDI', n193: 'n193', k193: 'k193', thickness: '膜厚', loss_pct: 'SolvA 损失 %', a500: 'A500'};
    const label = (key) => meta.labels?.[key] || NAMES[String(key).replace(/^resin\.factor\./, '')] || String(key).replace(/^resin\.factor\./, '');
    const pick = (list, value, onchange) => h('select', {onchange}, list.map((v) => h('option', {value: v, selected: v === value}, label(v))));
    const out = h('div.view', {}, h('p.quiet', {}, '正在算…'));
    main.replaceChildren(head, nav, h('div.page-body', {}, h('div.row.wrap', {},
      h('label.inline', {}, '因子', pick(meta.factors, factor, (e) => go('desk', 'factors', e.target.value, response))),
      h('label.inline', {}, '结果', pick(meta.responses, response, (e) => go('desk', 'factors', factor, e.target.value)))),
      meta.recommendations?.items?.length ? h('div.linked', {title: meta.recommendations.reason}, h('span.quiet.small', {}, '值得看：'),
        meta.recommendations.items.map((r) => h('a.mini-chip', {href: `#/desk/factors/${enc(r.factor_id)}/${enc(r.result_id)}`, title: r.reason}, r.title))) : null, out));
    const r = await api(`/desk/${enc(library)}/factors/${enc(factor)}/${enc(response)}`);
    const plot = h('div.plot');
    const pairs = new Set((r.pairs || []).flatMap((p) => [p.a, p.b]));
    const control = new Set(r.control || []);
    requestAnimationFrame(() => chart(plot, {x: label(factor), y: label(response), height: 380, series: [
      {name: '其它批', x: r.points.filter((p) => !control.has(p.lot)).map((p) => p.x), y: r.points.filter((p) => !control.has(p.lot)).map((p) => p.y), mode: 'markers', color: '#968f82'},
      {name: '可比对照组', x: r.points.filter((p) => control.has(p.lot)).map((p) => p.x), y: r.points.filter((p) => control.has(p.lot)).map((p) => p.y), mode: 'markers', color: '#3f7cc0'}]}));
    out.replaceChildren(r.reading ? h('p.reading', {}, r.reading) : null, h('div.blocks', {},
      h('section.block', {}, h('header.block-head', {}, h('h3', {}, `${label(factor)} → ${label(response)}`)), plot),
      h('section.block', {}, h('header.block-head', {}, h('h3', {}, `配对（${(r.pairs || []).length} 对）`)),
        tableBlock({columns: ['批 A', '批 B', `Δ${label(factor)}`, `Δ${label(response)}`], rows: (r.pairs || []).map((p) => [p.a, p.b, p.dx, p.dy])}, library),
        r.summary ? h('p.block-note', {}, Object.entries(r.summary).map(([k, v]) => `${k} ${typeof v === 'number' ? number(v) : v}`).join(' · ')) : null)));
    return;
  }
  // 数据体检
  const data = await api(`/desk/${enc(library)}/health`);
  const confirm = async (group, remember) => { await api(`/desk/${enc(library)}/health/confirm`, 'POST', {items: group.items, remember}); toast(`确认了 ${group.items.length} 条关联`); state.rerender?.(); };
  const dismiss = async (group) => { await api(`/desk/${enc(library)}/health/dismiss`, 'POST', {items: group.items}); toast('记下了：不是'); state.rerender?.(); };
  main.replaceChildren(head, nav, h('div.page-body', {},
    h('div.stat-row', {}, [['breaks', '断链', '一端在库里找不到'], ['candidate_items', '待确认关联', '按编号猜的对应，等你整组确认'], ['outliers', '异常点', '偏离同组太多的读数'], ['pending', '待确认的字段', '读出来但还没人确认的数']]
      .filter(([key]) => key in (data.counts || {})).map(([key, label, note]) => h('div.stat' + (data.counts[key] ? '.warn' : '.good'), {},
        h('div.stat-label', {}, label), h('div.stat-value', {}, data.counts[key]), h('div.stat-note', {}, note)))),
    h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, `候选关联（按编号成组，${(data.candidates || []).length} 组）`)),
      (data.candidates || []).length ? (data.candidates || []).map((group) => h('div.cand-group', {}, h('div.cand-head', {}, h('b', {}, group['组']), h('span.quiet.small', {}, `${group['依据'] || ''} · ${group['规则'] || ''}`),
        h('button.btn.primary.small', {type: 'button', onclick: () => confirm(group, false)}, '这组都对'), h('button.btn.small', {type: 'button', onclick: () => confirm(group, true)}, '都对，并记住成规则'),
        h('button.btn.ghost.small', {type: 'button', onclick: () => dismiss(group)}, '不是')),
        h('ul.links', {}, group.items.map((it) => h('li', {}, h('a', {href: `#/record/${enc(library)}/${enc(it.from)}`}, it.from_name || it.from), ` —${it.relation}→ `, h('a', {href: `#/record/${enc(library)}/${enc(it.to)}`}, it.to_name || it.to)))))) : h('p.quiet', {}, '没有待确认的关联。')),
    ...[['breaks', '断链'], ['outliers', '异常点'], ['pending', '待确认的字段']].map(([key, title]) => (data[key] || []).length ? h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, `${title}（${data[key].length}）`)),
      tableBlock({columns: Object.keys(data[key][0]).filter((k) => k !== '记录 id'), rows: data[key].map((row) => Object.entries(row).filter(([k]) => k !== '记录 id').map(([k, v]) => k === '名字' && row['记录 id'] ? {v, id: row['记录 id']} : v))}, library, {limit: 50})) : null)));
}

// ---------- 约定口径 ----------

export async function conventions(state) {
  const library = state.library;
  const {items} = await api(`/conventions/${enc(library)}`);
  const list = items.map((item) => ({...item}));
  const box = h('div.view');
  const save = async () => { await api(`/conventions/${enc(library)}`, 'PUT', {items: list.filter((item) => item['条目'])}); toast('约定已存；助手下一次干活就按这个'); };
  const draw = () => box.replaceChildren(
    h('p.quiet', {}, '你定下的口径和规矩。助手每次干活都会先读这里，照着做，不再问；改了马上生效。'),
    h('table.data.compact.edit-table.conv', {}, h('thead', {}, h('tr', {}, ['条目', '说明', '定于', ''].map((c) => h('th', {}, c)))),
      h('tbody', {}, list.map((item, index) => h('tr', {},
        h('td', {}, h('textarea', {rows: 2, onchange: (e) => { item['条目'] = e.target.value; }}, item['条目'] || '')),
        h('td', {}, h('textarea', {rows: 2, onchange: (e) => { item['说明'] = e.target.value; }}, item['说明'] || '')),
        h('td', {}, h('input', {type: 'text', value: item['定于'] || '', style: 'width:96px', onchange: (e) => { item['定于'] = e.target.value; }})),
        h('td', {}, h('button.btn.ghost.small', {type: 'button', onclick: () => { list.splice(index, 1); draw(); }}, '×')))))),
    h('div.row', {}, h('button.btn.small', {type: 'button', onclick: () => { list.push({条目: '', 说明: '', 定于: new Date().toLocaleDateString('sv-SE', {timeZone: 'Asia/Shanghai'})}); draw(); }}, icon('plus', 13), '加一条'),
      h('button.btn.primary.small', {type: 'button', onclick: save}, icon('check', 13), '存')));
  draw();
  return box;
}
