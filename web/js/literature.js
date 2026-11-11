// 知识 · 文献：问文献（在库里的文献、命题、推理链里找，要成段的回答交给助手）、文献监测（主题、查新、新到的线索：留 / 舍）、
// 文献库（留的和舍的、各自被哪些命题引用）。算在后端（lab/literature.py）；外面只去 OpenAlex，发出去的只有主题里的检索式。
import {api, h, icon, toast} from './core.js';

const enc = encodeURIComponent;
const KNOW = '知识库';
const recordHref = (id) => `#/record/${enc(KNOW)}/${enc(id)}`;
const pdfHref = (fileId, page) => `/api/lib/${enc(KNOW)}/files/${enc(fileId)}${page ? '#page=' + page : ''}`;
const REL = {高: ['current', '高相关'], 中: ['open', '中相关'], 低: ['', '低相关']};
const DROP_REASONS = ['不相关', '体系不同，不能外推', '只是用了同样的词', '库里已经有了'];
const KIND_TONE = {文献: 'todo', 命题: 'current', 推理链: 'open'};

// 命中词标出来：英文词、中文双字在原文里出现的位置都盖上，再连成段
function marked(text, tokens) {
  const source = String(text || ''), lower = source.toLowerCase(), hit = new Array(source.length).fill(false);
  for (const token of tokens) {
    let at = lower.indexOf(token);
    while (at >= 0 && token) { for (let i = at; i < at + token.length; i++) hit[i] = true; at = lower.indexOf(token, at + 1); }
  }
  const parts = [];
  let start = 0;
  for (let i = 1; i <= source.length; i++) {
    if (i === source.length || hit[i] !== hit[start]) { const piece = source.slice(start, i); parts.push(hit[start] ? h('mark', {}, piece) : piece); start = i; }
  }
  return parts;
}

const choiceTag = (value) => {
  const text = String(value || ''), head = text.slice(0, 1);
  const tone = head === '留' ? 'done' : head === '舍' ? 'off' : 'open';
  return h('span.st-tag.' + tone, {title: text}, head === '留' || head === '舍' ? head : (text.slice(0, 6) || '—'));
};

export async function literaturePage(state) {
  let data = await api('/literature');
  const root = h('div.lit');
  const askBox = h('section.block.wide.lit-ask'), watchBox = h('section.block.wide.lit-watch'), shelfBox = h('section.block.wide.lit-shelf');
  let leadTab = '待看', shelfFilter = '全部', shelfQuery = '', lastCheck = null;
  const refresh = async () => { data = await api('/literature'); drawWatch(); drawShelf(); };

  // ---------- 问文献 ----------
  const input = h('input.lit-q', {type: 'search', placeholder: '问一句，例：添加剂H在光照下稳定吗？DMAP 中和对颜色有什么影响？'});
  const hitsBox = h('div.lit-hits');
  let hits = [];
  const find = async () => {
    const q = input.value.trim();
    if (!q) return;
    const found = await api(`/literature/find?q=${enc(q)}&limit=10`);
    hits = found.hits;
    hitsBox.replaceChildren(hits.length ? h('ol.lit-hit-list', {}, hits.map((hit) => h('li.lit-hit', {},
      h('div.lit-hit-head', {}, h('span.st-tag.' + (KIND_TONE[hit.kind] || 'todo'), {}, hit.kind),
        h('a', {href: recordHref(hit.id)}, hit.name),
        hit.page ? h('a.st-tag.current', {href: pdfHref(hit.pdf, hit.page), target: '_blank', rel: 'noopener', title: '打开 PDF 的这一页'}, `第 ${hit.page} 页`) : null,
        hit.kind === '文献' ? choiceTag(hit.state) : hit.state ? h('span.st-tag.' + ({成立: 'done', 否定: 'off'}[hit.state] || 'open'), {}, hit.state) : null,
        hit['把握'] != null && hit['把握'] !== '' ? h('small.quiet', {}, `把握 ${hit['把握']}%`) : null,
        h('span.grow'), h('span.lit-score', {title: '和问题的相关程度（最相关的一条 = 1）'}, h('i', {style: `width:${Math.round(hit.score * 100)}%`}))),
      hit.snippet ? h('p.lit-snippet', {}, marked(hit.snippet, found.tokens)) : null,
      h('div.lit-hit-meta', {}, [hit.year ? String(hit.year) : '', hit.cited_by ? `被 ${hit.cited_by} 条命题当出处` : '', hit.evidence ? `${hit.evidence} 条证据` : ''].filter(Boolean).join(' · ')))))
      : h('p.quiet', {}, `库里 ${found.total} 条文献、命题、推理链里没有和这句话对上的。可以换个说法（化学名的英文写法常常更准），或者让助手去外面调研。`),
    hits.length > 5 ? h('button.btn.ghost.small.lit-more', {type: 'button', onclick: (event) => { hitsBox.querySelector('.lit-hit-list').classList.add('all'); event.currentTarget.remove(); }},
      `再看 ${hits.length - 5} 条`) : null);
  };
  input.addEventListener('keydown', (event) => { if (event.key === 'Enter') find(); });
  const answer = async () => {
    const q = input.value.trim();
    if (!q) { input.focus(); return; }
    if (!hits.length) await find();
    const refs = hits.slice(0, 6).map((hit) => `[[${hit.name}]]（${hit.kind}，id ${hit.id}${hit.page ? `，全文第 ${hit.page} 页` : ''}）`).join('；');
    state.panel.ask(`按技能「文献问答」回答这个问题：${q}\n` + (refs ? `先看库里这几条：${refs}。` : '页面上没找到相关的，你再用 literature find 换几个说法找找。')
      + '只用库里的，每句标出处，事实和推断分开；库里不够就说缺什么，要不要外搜我来定。', '知识 · 问文献');
  };
  askBox.append(h('header.block-head', {}, h('h3', {}, '问文献'),
    h('span.quiet.small', {}, `在库里 ${data.library.length} 份文献、${data.claims} 条命题和推理链里找；要成段的回答交给助手，每句都标出处`)),
  h('div.lit-ask-row', {}, input,
    h('button.btn', {type: 'button', onclick: find}, icon('search', 14), '找'),
    h('button.btn.primary', {type: 'button', onclick: answer}, icon('spark', 14), '让助手回答')), hitsBox);

  // ---------- 文献监测 ----------
  function topicEditor() {
    const rows = data.topics.map((topic) => ({...topic, 课题: [...(topic['课题'] || [])]}));
    const projectName = Object.fromEntries(data.projects.map((item) => [item.key, item.name]));
    const body = h('tbody');
    const draw = () => body.replaceChildren(...rows.map((topic, index) => h('tr', {},
      h('td', {}, h('input', {type: 'checkbox', checked: topic.on !== false, title: '开着的主题才查', onchange: (event) => { topic.on = event.target.checked; }})),
      h('td', {}, h('input.lit-topic-name', {value: topic.name, oninput: (event) => { topic.name = event.target.value; }})),
      h('td', {}, h('textarea.lit-query', {rows: 2, spellcheck: 'false', oninput: (event) => { topic.query = event.target.value; }}, topic.query || '')),
      h('td', {}, h('div.chip-set', {}, data.projects.map((item) => h('button.chip-pick' + (topic['课题'].includes(item.key) ? '.on' : ''), {type: 'button', title: item.library,
        onclick: (event) => { const at = topic['课题'].indexOf(item.key); if (at >= 0) topic['课题'].splice(at, 1); else topic['课题'].push(item.key); event.currentTarget.classList.toggle('on'); }}, item.name)))),
      h('td.small.quiet', {}, topic.last ? `${topic.last.slice(5, 16)} · 新 ${topic.found ?? 0} / 共 ${topic.total ?? 0}` : '还没查过', topic.error ? h('div.warn-ink', {}, '上次出错：' + topic.error.slice(0, 60)) : null),
      h('td', {}, h('button.btn.ghost.small', {type: 'button', title: '去掉这个主题', onclick: () => { rows.splice(index, 1); draw(); }}, '×')))));
    draw();
    const save = async () => { await api('/literature/topics', 'PUT', {items: rows}); toast('监测主题已存'); refresh(); };
    const reset = async () => { await api('/literature/topics', 'PUT', {reset: true}); toast('已恢复默认的 8 个主题'); refresh(); };
    return h('details.lit-topics', {}, h('summary', {}, `监测主题（${rows.filter((topic) => topic.on !== false).length} 个开着）`,
      h('span.quiet.small', {}, ' · 检索式用 OpenAlex 的写法：AND、OR、引号里是短语；只写化学名、CAS 和通用词，它会发到外面')),
    h('table.data.compact.lit-topic-table', {}, h('thead', {}, h('tr', {}, ['查', '主题', '检索式', '挂到课题', '上次查', ''].map((c) => h('th', {}, c)))), body),
    h('div.row', {}, h('button.btn.ghost.small', {type: 'button', onclick: () => { rows.push({name: '新主题', query: '', 课题: [], on: true}); draw(); }}, icon('plus', 13), '加一个主题'),
      h('button.btn.ghost.small', {type: 'button', onclick: reset}, icon('restore', 13), '恢复默认'), h('span.grow'),
      h('button.btn.primary.small', {type: 'button', onclick: save}, '存主题')),
    Object.keys(projectName).length ? null : h('p.quiet.small', {}, '还没有课题。'));
  }

  function leadCard(lead) {
    const [tone, relText] = REL[lead['相关度']] || [];
    const act = async (action, reason) => {
      const done = await api(`/literature/lead/${enc(lead.id)}`, 'POST', {action, reason});
      toast(action === 'keep' ? `留下了，存成文献「${done.name || lead['标题'].slice(0, 20)}」` : action === 'drop' ? '舍了' : '改回待看',
        action === 'reopen' ? null : async () => { await api(`/literature/lead/${enc(lead.id)}`, 'POST', {action: 'reopen'}); refresh(); });
      refresh();
    };
    const abs = h('p.lead-abs', {title: '点一下展开 / 收起', onclick: (event) => event.currentTarget.classList.toggle('open')}, lead['摘要'] || '（OpenAlex 没有这篇的摘要，看标题和期刊判断，或点链接去看）');
    const drop = h('details.lead-drop', {}, h('summary.btn.small', {}, '舍'), h('div.lead-drop-menu', {},
      DROP_REASONS.map((reason) => h('button', {type: 'button', onclick: () => act('drop', reason)}, reason)),
      h('button', {type: 'button', onclick: () => { const reason = prompt('为什么舍？'); if (reason != null) act('drop', reason); }}, '写原因…')));
    const actions = lead['状态'] === '待看' ? [
      h('button.btn.small', {type: 'button', onclick: () => act('keep', lead['意见'] || ''), title: '存成一条文献（取舍 = 留），摘要放进要点'}, icon('check', 13), '留'), drop,
      h('button.btn.ghost.small', {type: 'button', onclick: () => state.panel.ask(`看一下文献监测找到的这篇：《${lead['标题']}》（${lead['年份'] || ''}，${lead['期刊'] || lead['类型'] || ''}，线索 id ${lead.id}）。按摘要（能取到全文就读全文的关键段）说说它和我们哪个课题、哪条命题有关，值不值得留；用 literature screen 写相关度和意见。留不留我来定。`, '知识 · 文献监测')}, icon('spark', 13), '让助手看')]
      : [lead['状态'] === '留' && lead['文献'] ? h('a.btn.ghost.small', {href: recordHref(lead['文献'])}, icon('open', 13), '打开文献') : null,
        lead['理由'] ? h('span.quiet.small', {}, '理由：' + lead['理由']) : null, h('span.grow'),
        h('button.btn.ghost.small', {type: 'button', onclick: () => act('reopen')}, '改回待看')];
    return h('article.lit-lead' + (lead['相关度'] === '高' ? '.high' : ''), {},
      h('div.lead-top', {}, relText ? h('span.st-tag.' + tone, {}, relText) : h('span.st-tag', {}, '助手还没筛'), h('span.lead-topic', {}, lead['主题']),
        lead['课题'] ? h('span.quiet.small', {}, '→ ' + lead['课题']) : null, h('span.grow'), h('span.quiet.small', {}, (lead['发现于'] || '').slice(5, 10) + ' 查到')),
      h('a.lead-title', {href: lead['链接'] || '#', target: '_blank', rel: 'noopener'}, lead['标题']),
      h('div.lead-meta', {}, [lead['期刊'], lead['年份'], lead['类型'], lead['被引'] ? `被引 ${lead['被引']}` : '', lead['作者'] ? String(lead['作者']).split('、').slice(0, 3).join('、') + (String(lead['作者']).split('、').length > 3 ? ' 等' : '') : ''].filter(Boolean).join(' · '),
        lead['免费全文'] ? h('a', {href: lead['免费全文'], target: '_blank', rel: 'noopener'}, ' · 免费全文') : null),
      lead['意见'] ? h('p.lead-opinion', {}, h('b', {}, '助手：'), lead['意见']) : null, abs,
      h('div.lead-actions', {}, actions));
  }

  function drawWatch() {
    const count = data.lead_count || {};
    const on = data.topics.filter((topic) => topic.on !== false);
    const last = data.topics.map((topic) => topic.last).filter(Boolean).sort().pop();
    const list = data.leads.filter((lead) => lead['状态'] === leadTab);
    const checkBtn = h('button.btn.primary.small', {type: 'button'}, icon('refresh', 13), '现在查一遍');
    checkBtn.onclick = async () => {
      checkBtn.disabled = true;
      checkBtn.replaceChildren(icon('refresh', 13), `正在查 ${on.length} 个主题…`);
      try {
        lastCheck = await api('/literature/check', 'POST', {});
        leadTab = '待看';
      } finally { checkBtn.disabled = false; }
      refresh();
    };
    watchBox.replaceChildren(
      h('header.block-head', {}, h('h3', {}, '文献监测'),
        h('span.quiet.small', {}, `${on.length} 个主题 · 每周一 08:00 自动查一次（规则「文献监测」，助手按摘要筛）· ${last ? '上次查 ' + last.slice(5, 16) : '还没查过'}`), h('span.grow'),
        h('button.btn.ghost.small', {type: 'button', onclick: () => state.panel.ask('按技能「文献问答」里"每周文献监测"那一节，筛一遍待看的文献线索（literature leads status=待看）：每条写相关度和一句意见，最后说哪几篇值得读。留不留我来定。', '知识 · 文献监测')},
          icon('spark', 13), '让助手筛待看的'), checkBtn),
      lastCheck ? h('div.lit-check', {}, h('b', {}, `刚查完：新到 ${lastCheck.new.length} 篇`),
        lastCheck.topics.map((item) => h('span.lit-check-item' + (item.error ? '.bad' : item.new ? '.fresh' : ''), {title: item.error || `检索式命中 ${item.matched ?? '—'} 篇（${item.since} 以后发表），只取最相关的`},
          `${item.name} ${item.error ? '没查成' : '+' + item.new}`))) : null,
      topicEditor(),
      h('div.lit-lead-tabs', {}, ['待看', '留', '舍'].map((key) => h('button.chip-pick' + (key === leadTab ? '.on' : ''), {type: 'button', onclick: () => { leadTab = key; drawWatch(); }}, `${key} ${count[key] || 0}`)),
        leadTab === '待看' && list.length ? h('span.quiet.small', {}, '助手说高相关的排在前面；"留"存成文献，"舍"只记理由') : null),
      list.length ? h('div.lit-leads', {}, list.map(leadCard))
        : h('p.quiet', {}, leadTab === '待看' ? (last ? '没有待看的。新论文每周一自动查，也可以点"现在查一遍"。' : '还没查过。点"现在查一遍"：每个主题往回看一年，取最相关的 10 篇。') : `还没有${leadTab}的。`));
  }

  // ---------- 文献库 ----------
  function drawShelf() {
    const all = data.library;
    const head = (row) => String(row['取舍'] || '').slice(0, 1);
    const count = {留: all.filter((row) => head(row) === '留').length, 舍: all.filter((row) => head(row) === '舍').length};
    const search = h('input', {type: 'search', placeholder: '在文献库里找', value: shelfQuery, oninput: (event) => { shelfQuery = event.target.value; drawList(); }});
    const listBox = h('ul.lit-books');
    const drawList = () => {
      const q = shelfQuery.toLowerCase();
      const shown = all.filter((row) => (shelfFilter === '全部' || head(row) === shelfFilter)
        && (!q || [row.name, row['标题'], row['作者'], row['要点'], row['理由']].some((value) => String(value || '').toLowerCase().includes(q))));
      listBox.replaceChildren(...shown.map((row) => h('li.lit-book', {},
        choiceTag(row['取舍']),
        h('div.lit-book-main', {}, h('a', {href: recordHref(row.id)}, row.name),
          row.PDF ? h('a.lit-pdf', {href: pdfHref(row.PDF), target: '_blank', rel: 'noopener', title: '打开全文'}, icon('file', 12), `PDF · ${row['全文页数'] || '?'} 页`) : null,
          h('div.lit-book-meta', {}, [row['年份'], row['类型'], row['取舍'] && row['取舍'].length > 1 ? row['取舍'].slice(1).replace(/^[（(]|[）)]$/g, '') : ''].filter(Boolean).join(' · ')),
          row['理由'] ? h('p.lit-book-why', {}, row['理由']) : null),
        h('div.lit-book-claims', {}, row['命题'].length ? [h('small.quiet', {}, `被 ${row['命题'].length} 条命题当出处`),
          ...row['命题'].slice(0, 2).map((claim) => h('a.small', {href: recordHref(claim.id), title: claim.name}, claim.name.length > 22 ? claim.name.slice(0, 21) + '…' : claim.name))]
          : h('small.quiet', {}, '还没有命题引用')))));
      if (!shown.length) listBox.append(h('li.quiet', {}, '没有对上的。'));
    };
    // 把手上的 PDF 拖进来：存原文件和每页文字，有 DOI 就补上题录；问文献能按页找到
    const status = h('div.lit-drop-status');
    const upload = async (files) => {
      const pdfs = [...files].filter((file) => /\.pdf$/i.test(file.name));
      if (!pdfs.length) { toast('只收 PDF'); return; }
      for (const file of pdfs) {
        const line = h('div', {}, h('span.spinner'), ` ${file.name}：正在读…`);
        status.append(line);
        try {
          const response = await fetch(`/api/literature/pdf/${enc(file.name)}`, {method: 'PUT', body: file});
          const out = await response.json();
          if (!response.ok) throw new Error(out.detail || response.statusText);
          line.replaceChildren(h('b', {}, out['已有'] ? '更新了 ' : '存好了 '), h('a', {href: recordHref(out.id)}, out.name),
            ` · ${out['页数']} 页${out.DOI ? ' · DOI ' + out.DOI + '（题录从 OpenAlex 补上了）' : ' · 没找到 DOI，标题是从 PDF 里猜的，可以改'}${out['字数'] < 200 ? ' · 读不出文字，像是扫描件' : ''}`);
        } catch (error) { line.replaceChildren(h('b.bad-ink', {}, `${file.name} 没存上：`), error.message); }
      }
      refresh();
    };
    const picker = h('input', {type: 'file', accept: '.pdf,application/pdf', multiple: true, hidden: true, onchange: () => { upload(picker.files); picker.value = ''; }});
    const drop = h('div.lit-drop', {onclick: () => picker.click(),
      ondragover: (event) => { event.preventDefault(); drop.classList.add('on'); }, ondragleave: () => drop.classList.remove('on'),
      ondrop: (event) => { event.preventDefault(); drop.classList.remove('on'); upload(event.dataTransfer.files); }},
      icon('upload', 18), h('div', {}, h('b', {}, '把论文 PDF 拖到这里（可以一次几篇）'),
        h('small', {}, '存原文件和每一页的文字，"问文献"能按页找到；有 DOI 就自动补上标题、作者、期刊、摘要（发出去的只有 DOI）')), picker);
    shelfBox.replaceChildren(
      h('header.block-head', {}, h('h3', {}, `文献库（${all.length}）`), h('span.quiet.small', {}, '调研和监测留下的、你放进来的全文；舍掉的也在，写着为什么舍'), h('span.grow'),
        h('div.chip-set', {}, [['全部', all.length], ['留', count['留']], ['舍', count['舍']]].map(([key, n]) =>
          h('button.chip-pick' + (key === shelfFilter ? '.on' : ''), {type: 'button', onclick: () => { shelfFilter = key; drawShelf(); }}, `${key} ${n}`))), search),
      drop, status, listBox);
    drawList();
  }

  drawWatch();
  drawShelf();
  root.append(askBox, watchBox, shelfBox);
  return root;
}
