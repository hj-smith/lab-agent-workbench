// 手机页（现场）：拍板（回答助手的卡片）、称量（工单的实称）、批记录（树脂实验批记录的格子）。
// 填了自动存，用的是和电脑上同一套接口（/api/orders、/api/paper、/api/lanes/…/answer），电脑上马上看得到。
const enc = encodeURIComponent;

function h(tag, attrs = {}, ...children) {
  const [name, ...classes] = tag.split('.');
  const node = document.createElement(name || 'div');
  if (classes.length) node.className = classes.join(' ');
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'class') node.className += ' ' + value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat(Infinity)) if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}

async function api(path, method = 'GET', body) {
  const response = await fetch('/api' + path, {method, headers: body ? {'Content-Type': 'application/json'} : {}, body: body ? JSON.stringify(body) : undefined});
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && data.login) { location.href = '/login?next=/m'; throw new Error('请先登录'); }
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
}

const statusBox = h('div.status');
document.body.append(statusBox);
let statusTimer;
const say = (text) => { statusBox.textContent = text; statusBox.classList.add('on'); clearTimeout(statusTimer); statusTimer = setTimeout(() => statusBox.classList.remove('on'), 1800); };

const app = document.getElementById('app');
let library = (() => { try { return localStorage.getItem('m-library') || '产品A'; } catch { return '产品A'; } })();
let libraries = ['产品A'];
let lists = {cards: [], orders: [], batches: []};

const go = new URLSearchParams(location.search).get('go');                  // 推送里点进来的：#/assistant/… → 拍板
if (go && !location.hash) location.hash = '#cards';

function route() {
  const [page, id] = (location.hash.replace(/^#/, '') || 'cards').split('/').map(decodeURIComponent);
  return {page: ['cards', 'weigh', 'batch'].includes(page) ? page : 'cards', id};
}

function top(title, back) {
  return h('header.top', {}, back ? h('button.back', {type: 'button', 'aria-label': '返回', onclick: () => { location.hash = '#' + back; }}, '‹') : null,
    h('b', {}, title), h('span.grow'),
    h('div.lib', {}, libraries.map((name) => h('button' + (name === library ? '.on' : ''), {type: 'button', onclick: async () => {
      library = name; try { localStorage.setItem('m-library', name); } catch { /* 私密模式存不了，没关系 */ }
      location.hash = '#' + route().page; await load(); render();
    }}, name))));
}

function tabs(page) {
  const tab = (key, title, note, count) => h('a' + (page === key ? '.on' : ''), {href: '#' + key}, h('b', {}, title), note, count ? h('span.badge', {}, count) : null);
  return h('nav.tabs', {}, tab('cards', '拍板', '助手问你的', lists.cards.length), tab('weigh', '称量', '工单实称', 0), tab('batch', '批记录', '树脂实验', 0));
}

async function load() {
  lists = await api(`/mobile/${enc(library)}`);
}

// ---------- 拍板 ----------
function cardsView() {
  const body = lists.cards.length ? lists.cards.map((item) => {
    const card = item.card || {};
    const free = h('textarea', {rows: 2, placeholder: '或者写你的意思'});
    const answer = async (text) => {
      if (!text.trim()) return;
      await api(`/lanes/${enc(item.lane)}/answer`, 'POST', {text});
      say('答好了，助手接着做'); await load(); render();
    };
    return h('section.card', {},
      h('div', {}, h('span.tag.now', {}, '等你定'), ' ', h('small', {style: 'color:var(--muted)'}, item.title || '')),
      h('h1', {style: 'font-size:17px;margin:0'}, card.title || ''),
      card.text ? h('p.note', {style: 'margin:0'}, card.text) : null,
      (card.evidence || []).length ? h('ul.evidence', {}, card.evidence.map((line) => h('li', {}, line.replace(/\[\[|\]\]/g, '')))) : null,
      (card.options || []).length ? h('div.options', {}, card.options.map((option, index) => h('button.btn' + (index === 0 ? '.primary' : ''), {type: 'button', onclick: () => answer(option)}, option))) : null,
      h('div.field', {}, free), h('button.btn.block', {type: 'button', onclick: () => answer(free.value)}, '发这句'));
  }) : [h('p.empty', {}, '现在没有要你拍板的事。')];
  return [h('h1', {}, '要你拍板'), h('p.sub', {}, '助手在等你定的事；点一个选项就算答了，它会接着做。'), ...body];
}

// ---------- 称量 ----------
function weighList() {
  const items = lists.orders.map((row) => h('a.item', {href: `#weigh/${enc(row.id)}`},
    h('div.main', {}, h('div.name', {}, row.name), h('div.meta', {}, [row['配方编号'], row['计划日期']].filter(Boolean).join(' · '))),
    h('span.tag' + (row['状态'] === '称量中' ? '.now' : ''), {}, row['状态'] || '待配样'), h('span.arrow', {}, '›')));
  return [h('h1', {}, '称量'), h('p.sub', {}, '还没称完的工单，新的在前。点进去边称边填。'), ...(items.length ? items : [h('p.empty', {}, '没有待称的工单。')])];
}

async function weighView(id) {
  const full = await api(`/lib/${enc(library)}/records/${enc(id)}`);
  const table = full.series['称量'];
  if (!table) return [h('p.empty', {}, '这张工单没有称量表。')];
  const at = Object.fromEntries(table.columns.map((name, index) => [name, index]));
  const changes = {};
  let timer;
  const operator = h('input', {type: 'text', value: (full.data['实称信息'] || {}).operator || '', placeholder: '你的名字'});
  const save = async (extra = {}) => {
    const result = await api(`/orders/${enc(library)}/${enc(id)}`, 'PUT', {rows: {...changes}, operator: operator.value, ...extra});
    Object.keys(changes).forEach((key) => delete changes[key]);
    say(`已存 · ${result.filled} / ${result.lines} 行称好`);
    return result;
  };
  const later = () => { clearTimeout(timer); timer = setTimeout(save, 600); };
  operator.addEventListener('change', () => save());
  const rows = table.rows.map((row) => {
    const plan = row[at['计划量']], line = row[at['行']];
    const dev = h('span.dev');
    const box = h('div.row-card');
    const show = (value) => {
      const pct = typeof value === 'number' && plan ? (value - plan) / plan * 100 : null;
      dev.textContent = pct == null ? '' : `偏差 ${pct >= 0 ? '+' : ''}${pct.toFixed(2)} %`;
      dev.className = 'dev tag' + (pct == null ? '' : Math.abs(pct) > 1 ? ' bad' : ' good');
      box.classList.toggle('done', value != null);
    };
    const actual = h('input', {type: 'text', inputmode: 'decimal', value: row[at['实称']] ?? '', placeholder: '实称', onchange: (event) => {
      const text = event.target.value.trim(), value = text === '' ? null : Number(text);
      changes[line] = {...(changes[line] || {}), 实称: Number.isNaN(value) ? text : value}; show(typeof value === 'number' && !Number.isNaN(value) ? value : null); later();
    }});
    const review = h('input', {type: 'text', value: row[at['复核']] ?? '', placeholder: '复核人', style: 'font-size:15px;height:40px', onchange: (event) => {
      changes[line] = {...(changes[line] || {}), 复核: event.target.value}; later();
    }});
    show(row[at['实称']]);
    box.append(h('div.row-head', {}, h('span.label', {}, row[at['名称']] || row[at['来源编号']]), h('span.plan', {}, plan != null ? `计划 ${Number(plan).toFixed(3)} g` : '')),
      h('div.field', {}, actual, h('span.unit', {}, 'g')), h('div.field', {}, dev, h('span', {style: 'flex:1'}), review));
    return box;
  });
  return [h('h1', {}, full.name), h('p.sub', {}, [full.data['配方编号'], full.data['计划日期']].filter(Boolean).join(' · ') + ' · 偏差超过 ±1 % 标红'),
    h('div.field', {}, h('span.what', {}, '操作人'), operator), ...rows,
    h('button.btn.primary.block', {type: 'button', onclick: async () => { await save({status: '已完成'}); say('标成已完成'); await load(); location.hash = '#weigh'; }}, '称完了，标成已完成'),
    photoCard(full.id)];
}

// ---------- 批记录 ----------
function batchList() {
  const search = h('input.search', {type: 'search', placeholder: '找实验编号或目的'});
  const box = h('div', {style: 'display:flex;flex-direction:column;gap:8px'});
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const found = lists.batches.filter((row) => !q || [row.name, row['目的'], row['实验名称']].some((value) => String(value || '').toLowerCase().includes(q)));
    box.replaceChildren(...found.slice(0, 30).map((row) => h('a.item', {href: `#batch/${enc(row.id)}`},
      h('div.main', {}, h('div.name', {}, row.name), h('div.meta', {}, row['目的'] || row['实验名称'] || '')),
      h('span.tag' + (row['状态'] === '设计中' || row['状态'] === '进行中' ? '.now' : ''), {}, row['状态'] || ''), h('span.arrow', {}, '›'))),
    found.length ? '' : h('p.empty', {}, '没找到。'));
  };
  search.addEventListener('input', draw);
  draw();
  return [h('h1', {}, '批记录'), h('p.sub', {}, '在做的实验排在前面；点进去按格子填，和电脑上的批记录是同一份。'), search, box];
}

async function batchView(id) {
  const doc = await api(`/paper/${enc(library)}/${enc(id)}`);
  const sheets = doc.sheets.filter((sheet) => sheet.blocks.some((block) => block.type === 'table' && block.rows.some((row) => row.some((cell) => cell.input))));
  let current = sheets[0]?.name;
  const changes = {};
  let timer;
  const flush = async () => {
    const values = {...changes};
    Object.keys(changes).forEach((key) => delete changes[key]);
    if (!Object.keys(values).length) return;
    const result = await api(`/paper/${enc(library)}/${enc(id)}`, 'PUT', {values});
    say(`已存 · 共填了 ${result.filled} 格`);
  };
  const body = h('div', {style: 'display:flex;flex-direction:column;gap:10px'});
  const drawSheet = () => {
    const sheet = sheets.find((item) => item.name === current);
    const parts = [];
    for (const block of sheet.blocks) {
      if (block.type === 'section') { parts.push(h('div.section-title', {}, block.text.replace(/^\d+\s*/, ''))); continue; }
      if (block.type !== 'table' || !block.rows.some((row) => row.some((cell) => cell.input))) continue;
      const header = [];
      for (const item of block.header || []) for (let k = 0; k < (item.span || 1); k++) header.push(item.label ?? item);
      for (const row of block.rows) {
        if (!row.some((cell) => cell.input)) continue;
        let col = 0;
        const texts = [], inputs = [];
        for (const cell of row) {
          (cell.input ? inputs : texts).push({cell, label: header[col] || ''});
          col += cell.span || 1;
        }
        const card = h('div.row-card');
        const filled = () => card.classList.toggle('done', inputs.every(({cell}) => cell.value != null && cell.value !== ''));
        const [first, ...rest] = texts;
        card.append(h('div.row-head', {}, h('span.label', {}, first ? first.cell.t : ''),
          h('span.plan', {}, rest.map(({cell, label}) => (label && !/单位/.test(label) ? label + ' ' : '') + (cell.t ?? '')).filter((text) => String(text).trim()).join(' · '))));
        for (const {cell, label} of inputs) {
          const numeric = cell.type === 'number' || /g|mL|°C|℃|min|h|%/.test(cell.unit || '');
          const field = numeric ? h('input', {type: 'text', inputmode: 'decimal', value: cell.value ?? '', placeholder: label || '填写'})
            : h('textarea', {rows: 1, placeholder: label || '填写'}, cell.value ?? '');
          field.addEventListener('change', (event) => {
            const text = event.target.value.trim();
            cell.value = text === '' ? null : numeric && !Number.isNaN(Number(text)) ? Number(text) : text;
            changes[cell.key] = cell.value; filled(); say('正在存…'); clearTimeout(timer); timer = setTimeout(flush, 700);
          });
          card.append(h('div.field', {}, label && inputs.length > 1 ? h('span.what', {}, label) : null, field, cell.unit ? h('span.unit', {}, cell.unit) : null));
        }
        filled();
        parts.push(card);
      }
    }
    body.replaceChildren(...parts);
  };
  const segs = h('div.segs', {}, sheets.map((sheet) => h('button' + (sheet.name === current ? '.on' : ''), {type: 'button', onclick: (event) => {
    current = sheet.name; segs.querySelectorAll('button').forEach((node) => node.classList.toggle('on', node === event.currentTarget)); drawSheet();
  }}, sheet.name)));
  if (current) drawSheet();
  return [h('h1', {}, doc.code || doc.title), h('p.sub', {}, `已填 ${doc.filled} 格 · 填了自动存，产率和偏差在电脑上马上按新值算`),
    sheets.length > 1 ? segs : null, current ? body : h('p.empty', {}, '这个实验还没有能填的批记录。'), photoCard(doc.id || id)];
}

// ---------- 拍照记一笔：照片挂到这条记录上，带一句说明 ----------
async function shrink(file) {                       // 手机照片先缩到长边 1600 px（走外网也快），JPEG
  const image = await createImageBitmap(file).catch(() => null);
  if (!image) return file;
  const scale = Math.min(1, 1600 / Math.max(image.width, image.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(image.width * scale); canvas.height = Math.round(image.height * scale);
  canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
  return await new Promise((resolve) => canvas.toBlob((blob) => resolve(blob || file), 'image/jpeg', 0.85));
}

function photoCard(recordId) {
  const list = h('div.photos');
  const draw = (photos) => list.replaceChildren(...photos.slice().reverse().map((item) => h('a.photo', {href: `/api/lib/${enc(library)}/files/${enc(item['照片'])}`, target: '_blank'},
    h('img', {src: `/api/lib/${enc(library)}/files/${enc(item['照片'])}`, alt: item['说明'] || '现场照片', loading: 'lazy'}),
    h('span', {}, `${(item['时间'] || '').slice(5)} ${item['说明'] || ''}`))));
  api(`/mobile/${enc(library)}/${enc(recordId)}/photos`).then(({photos}) => draw(photos)).catch(() => {});
  const note = h('textarea', {rows: 2, placeholder: '写一句：看到了什么（颜色、析出、粘度……）'});
  const picker = h('input', {type: 'file', accept: 'image/*', capture: 'environment', hidden: true, onchange: async () => {
    const file = picker.files[0]; picker.value = '';
    if (!file) return;
    say('正在存照片…');
    try {
      const body = await shrink(file);
      const response = await fetch(`/api/mobile/${enc(library)}/${enc(recordId)}/photo/${enc((file.name || 'photo').replace(/\.\w+$/, '') + '.jpg')}?note=${enc(note.value.trim())}`, {method: 'POST', body});
      const out = await response.json();
      if (!response.ok) throw new Error(out.detail || response.statusText);
      note.value = ''; draw(out.photos); say('照片存好了');
    } catch (error) { say('没存上：' + error.message); }
  }});
  return h('div.card', {}, h('div.section-title', {style: 'margin:0'}, '拍照记一笔'), note,
    h('button.btn.primary.block', {type: 'button', onclick: () => picker.click()}, '📷 拍照（或选一张）'), picker, list);
}

// ---------- 画 ----------
async function render() {
  const {page, id} = route();
  const titles = {cards: '拍板', weigh: '称量', batch: '批记录'};
  let content;
  try {
    content = page === 'weigh' && id ? await weighView(id) : page === 'batch' && id ? await batchView(id)
      : page === 'weigh' ? weighList() : page === 'batch' ? batchList() : cardsView();
  } catch (error) {
    content = [h('p.empty', {}, '没打开：' + error.message)];
  }
  app.replaceChildren(top(id ? titles[page] : '研发工作台 · 现场', id ? page : null), h('main', {}, content), tabs(page));
  window.scrollTo(0, 0);
}

window.addEventListener('hashchange', render);
(async () => {
  const state = await api('/state');
  libraries = state.libraries.filter((name) => name !== '知识库');
  if (!libraries.includes(library)) library = libraries[0];
  await load();
  render();
})();
