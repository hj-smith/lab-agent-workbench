// 知识 · 学习：他想弄懂一样东西 → 助手出摸底题 → 他在这里答（选、填把握、写思路）→ 助手按摸底排路线 → 一步步"先猜 → 讲解 → 检查" → 助手讲评。
// 复习（到期的检查题）和把握校准都在这页上，不用模型。算在后端（lab/learn.py）。
import {api, h, icon, listen, md, toast} from './core.js';
import {plotly} from './blocks.js';

const enc = encodeURIComponent;
const RESULT = {对: ['done', '对'], 错: ['off', '错'], 部分: ['open', '部分对'], 待讲评: ['', '等讲评']};
const STATE_TONE = {摸底: 'open', 排路线: 'open', 学习中: 'current', 学完: 'done'};
const sureText = (value) => value >= 90 ? '很有把握' : value >= 70 ? '比较有把握' : value >= 40 ? '一半一半' : value >= 20 ? '没什么把握' : '瞎猜';

// 一道题：没答过是表单（选 / 填 / 写，把握，思路）；答过了显示你的答案、对错、正确答案、解释和讲评
function qCard(question, prev, submit, {label} = {}) {
  const box = h('div.q-card');
  const answered = (record) => box.replaceChildren(
    h('div.q-head', {}, label ? h('span.q-label', {}, label) : null, h('span.q-type', {}, question['题型']), h('span.grow'),
      record['结果'] ? h('span.st-tag.' + (RESULT[record['结果']] || ['', ''])[0], {}, (RESULT[record['结果']] || ['', record['结果']])[1]) : null),
    h('p.q-text', {}, question['题目']),
    h('div.q-answer', {}, h('span.quiet', {}, '你答：'), h('b', {}, String(record['你的答案'] ?? '—')),
      record['把握程度'] != null ? h('span.quiet', {}, ` · 把握 ${Math.round(record['把握程度'])}%`) : null,
      record['结果'] !== '对' && question['答案'] != null ? [h('span.quiet', {}, ' · 答案：'), h('b', {}, String(question['答案']) + (question['单位'] ? ' ' + question['单位'] : ''))] : null),
    record['思路'] ? h('p.q-thinking', {}, h('span.quiet', {}, '你的思路：'), record['思路']) : null,
    question['解释'] ? h('div.q-explain.md', {html: md(question['解释'])}) : null,
    record['讲评'] ? h('p.q-review', {}, h('b', {}, '讲评：'), record['讲评']) : null);
  if (prev) { answered(prev); return box; }
  let picked = null;
  const send = h('button.btn.primary.small', {type: 'button', disabled: true}, '交');
  const ready = () => { send.disabled = picked == null || String(picked).trim() === ''; };
  let field;
  if (question['题型'] === '单选' || question['题型'] === '判断') {
    field = h('div.q-options' + (question['题型'] === '判断' ? '.tf' : ''), {}, (question['选项'] || []).map((option) => h('button.q-option', {type: 'button', onclick: (event) => {
      picked = option; field.querySelectorAll('.q-option').forEach((node) => node.classList.toggle('on', node === event.currentTarget)); ready();
    }}, option)));
  } else if (question['题型'] === '填数') {
    field = h('div.q-number', {}, h('input', {type: 'text', inputmode: 'decimal', placeholder: '填一个数', oninput: (event) => { picked = event.target.value; ready(); }}),
      question['单位'] ? h('span.quiet', {}, question['单位']) : null);
  } else {
    field = h('textarea.q-free', {rows: 3, placeholder: '用自己的话写', oninput: (event) => { picked = event.target.value; ready(); }});
  }
  const sure = h('input', {type: 'range', min: 0, max: 100, step: 10, value: 50});
  const sureLabel = h('span.q-sure-label', {}, '50% · 一半一半');
  sure.addEventListener('input', () => { sureLabel.textContent = `${sure.value}% · ${sureText(Number(sure.value))}`; });
  const thinking = h('textarea.q-think', {rows: 2, placeholder: '说说你怎么想的（可以不写；写了讲评更准）'});
  send.onclick = async () => {
    send.disabled = true;
    const done = await submit({answer: picked, confidence: Number(sure.value), thinking: thinking.value});
    answered({'你的答案': picked, '把握程度': Number(sure.value), '思路': thinking.value, '结果': done['结果']});
  };
  box.replaceChildren(h('div.q-head', {}, label ? h('span.q-label', {}, label) : null, h('span.q-type', {}, question['题型'])),
    h('p.q-text', {}, question['题目']), field,
    h('div.q-sure', {}, h('span.quiet.small', {}, '把握'), sure, sureLabel), thinking, h('div.q-foot', {}, send));
  return box;
}

const bar = (done, total) => h('span.learn-bar', {title: `${done} / ${total} 步`}, h('i', {style: `width:${total ? Math.round(done / total * 100) : 0}%`}));

// 等助手：它这一轮跑完（状态变 idle）就重新拿数据；页面换走了就不再听
function whenIdle(node, again) {
  const stop = listen((event) => {
    if (!document.body.contains(node)) { stop(); return; }
    if (event.type === 'status' && event.state === 'idle') again();
  });
}

// ---------- 学习首页 ----------
export async function learnPage(main, state, ref) {
  if (ref) return lessonPage(state, ref);
  const data = await api('/learn');
  const root = h('div.learn');

  // 开始学
  const goal = h('textarea.learn-goal', {rows: 2, placeholder: '想弄懂什么？例：添加剂H怎么让显色物种褪色，为什么配方里追加挡不住返色'});
  const why = h('input.learn-why', {placeholder: '为什么学（可以不写）：例：要决定 AddH 在合成里加还是在配方里加'});
  const picked = new Set();
  const start = async () => {
    if (!goal.value.trim()) { goal.focus(); return; }
    const {id} = await api('/learn', 'POST', {目标: goal.value.trim(), 为什么学: why.value.trim(), 课题: [...picked]});
    state.panel.ask(`按技能「学习」第一节，给学习「${goal.value.trim().slice(0, 40)}」（id ${id}）出摸底题：lesson get 看目标，用 literature find 和 records 找知识库里相关的命题、推理链、文献，出 3–5 道摸底题，lesson save diagnose。`, '知识 · 学习');
    location.hash = `#/knowledge/${enc('学习')}/${enc(id)}`;
  };
  root.append(h('section.block.wide.learn-start', {}, h('header.block-head', {}, h('h3', {}, '开始学一样东西'),
    h('span.quiet.small', {}, '助手先出几道摸底题看你懂到哪，再排最少的几步：每步先猜、再讲、再检查')),
  goal, why,
  data.projects.length ? h('div.learn-projects', {}, h('span.quiet.small', {}, '挂到课题：'), h('div.chip-set', {}, data.projects.map((item) =>
    h('button.chip-pick', {type: 'button', title: item.claims ? `这个课题有 ${item.claims} 条命题` : '', onclick: (event) => {
      if (picked.has(item.key)) picked.delete(item.key); else picked.add(item.key);
      event.currentTarget.classList.toggle('on');
      if (!goal.value.trim() && item.claims) goal.value = `弄懂「${item.name}」这个课题现在的证据：哪些结论站得住、哪些还没定、为什么`;
    }}, item.name, item.claims ? h('small', {}, ` ${item.claims}`) : null)))) : null,
  h('div.row', {}, h('span.grow'), h('button.btn.primary', {type: 'button', onclick: start}, icon('spark', 14), '让助手出摸底题'))));

  // 今天要复习
  const dueBox = h('section.block.wide.learn-due');
  let index = 0;
  const drawDue = () => {
    const item = data.due[index];
    dueBox.replaceChildren(h('header.block-head', {}, h('h3', {}, `今天要复习（${data.due.length} 题）`),
      h('span.quiet.small', {}, '答对的题隔 1、3、7、16、35 天再考，答错的明天再考')),
    item ? [h('p.quiet.small', {}, `第 ${index + 1} 题 · 来自「${item.lesson_name}」${item.step ? ' · ' + item.step : ''} · 上次${item.last}${item.streak ? `，连对 ${item.streak} 次` : ''}`),
      qCard(item.question, null, (body) => api(`/learn/${enc(item.lesson)}/answer`, 'POST', {...body, question: item.question.id, stage: '复习'}), {label: '复习'}),
      h('div.row', {}, h('span.grow'), index + 1 < data.due.length ? h('button.btn.small', {type: 'button', onclick: () => { index += 1; drawDue(); }}, '下一题') : h('span.quiet.small', {}, '今天的复习做完了'))]
      : h('p.quiet', {}, '今天没有要复习的。学过的检查题到期了会出现在这里。'));
  };
  drawDue();
  if (data.due.length || data.lessons.length) root.append(dueBox);

  // 我的学习
  root.append(h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, `我的学习（${data.lessons.length}）`)),
    data.lessons.length ? h('div.learn-cards', {}, data.lessons.map((item) => h('a.learn-card', {href: `#/knowledge/${enc('学习')}/${enc(item.id)}`},
      h('div.learn-card-top', {}, h('span.st-tag.' + (STATE_TONE[item['状态']] || ''), {}, item['状态']), h('span.grow'), h('small.quiet', {}, String(item.updated || '').slice(5, 16))),
      h('div.learn-card-goal', {}, item['目标']),
      h('div.learn-card-foot', {}, bar(...item.progress), h('small.quiet', {}, item.progress[1] ? `${item.progress[0]} / ${item.progress[1]} 步` : '还没排路线'),
        h('span.grow'), item.right != null ? h('small', {}, `答对 ${Math.round(item.right * 100)}%`) : h('small.quiet', {}, `答了 ${item.answered} 题`)))))
      : h('p.quiet', {}, '还没有。上面写一句想弄懂什么就能开始。')));

  // 把握校准
  const cal = data.calibration;
  const chart = h('div.plot.learn-cal-plot');
  root.append(h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '心里有没有数（把握校准）'),
    h('span.quiet.small', {}, '每个点是一档把握：横轴是你填的把握，纵轴是那一档实际答对的比例；落在对角线上 = 心里有数，在下面 = 太自信')),
  cal.n >= 5 ? h('div.learn-cal', {}, chart, h('div.learn-mis', {}, h('h4', {}, `高把握答错（${cal.misconceptions.length}）`),
    cal.misconceptions.length ? h('ul.plain-list', {}, cal.misconceptions.map((item) => h('li', {}, h('a', {href: `#/knowledge/${enc('学习')}/${enc(item['学习'])}`}, item['题目']),
      h('small.quiet', {}, ` 你答 ${item['你的答案']}，把握 ${Math.round(item['把握程度'])}%`)))) : h('p.quiet.small', {}, '没有。把握 70% 以上答错的题会列在这里，多半是误解，值得专门拆开讲。')))
    : h('p.quiet', {}, `做过的题还少（${cal.n} 题），多做一些再看。`)));
  if (cal.n >= 5) {
    const Plotly = await plotly();
    requestAnimationFrame(() => Plotly.newPlot(chart, [
      {x: [0, 100], y: [0, 100], mode: 'lines', line: {color: '#8f897c', dash: 'dot', width: 1}, hoverinfo: 'skip'},
      {x: cal.bins.map((b) => b.confidence), y: cal.bins.map((b) => b.right * 100), mode: 'markers+text', textposition: 'middle right', textfont: {color: '#6b665c', size: 11},
        texttemplate: cal.bins.map((b) => `  ${b.n} 题`),
        marker: {color: '#c96442', size: cal.bins.map((b) => 9 + Math.min(16, b.n * 2)), line: {color: '#fffefb', width: 2}},
        text: cal.bins.map((b) => `把握 ${b.from}–${b.to}%：${b.n} 题，答对 ${Math.round(b.right * 100)}%`), hovertemplate: '%{text}<extra></extra>'}],
    {paper_bgcolor: '#fbfaf7', plot_bgcolor: '#fbfaf7', height: 260, margin: {l: 52, r: 12, t: 8, b: 40}, showlegend: false, font: {family: 'Microsoft YaHei, sans-serif', size: 11, color: '#29261f'},
      xaxis: {title: {text: '你填的把握（%）'}, range: [0, 100], gridcolor: '#ebe6dc', zeroline: false}, yaxis: {title: {text: '实际答对（%）'}, range: [-6, 108], gridcolor: '#ebe6dc', zeroline: false}},
    {displaylogo: false, responsive: true}));
  }
  return root;
}

// ---------- 一个学习 ----------
async function lessonPage(state, ref) {
  let lesson = await api(`/learn/${enc(ref)}`);
  const root = h('div.learn.lesson');
  let current = null, shown = new Set();
  const answer = (question, stage) => (body) => api(`/learn/${enc(lesson.id)}/answer`, 'POST', {...body, question: question.id, stage});
  const reload = async () => { lesson = await api(`/learn/${enc(ref)}`); draw(); };
  const ask = (text) => { state.panel.ask(text, `知识 · 学习「${lesson.name}」`); toast('交给助手了，它做完这一页会自己刷新'); };

  function stepView(step) {
    const claim = lesson.claims?.[step['命题']];
    const guess = step['先猜'];
    const guessed = !guess || lesson.answers[guess.id] || shown.has(step.id);
    return h('div.step-view', {},
      h('h3.step-title', {}, step['标题']),
      claim ? h('a.step-claim', {href: `#/record/${enc('知识库')}/${enc(claim.id)}`}, h('span.st-tag.' + ({成立: 'done', 否定: 'off'}[claim['状态']] || 'open'), {}, claim['状态'] || '命题'), claim['陈述']) : null,
      guess ? h('div.step-part', {}, h('h4', {}, '① 先猜一下'), qCard(guess, lesson.answers[guess.id], async (body) => {
        const done = await answer(guess, '先猜')(body); shown.add(step.id); setTimeout(reload, 300); return done;
      }, {label: '先猜'}), guessed ? null : h('button.btn.ghost.small', {type: 'button', onclick: () => { shown.add(step.id); draw(); }}, '不猜了，直接看讲解')) : null,
      guessed ? h('div.step-part', {}, h('h4', {}, guess ? '② 讲解' : '① 讲解'), h('div.step-teach.md', {html: md(step['讲解'] || '（这一步没有讲解）')})) : null,
      guessed ? h('div.step-part', {}, h('h4', {}, guess ? '③ 检查' : '② 检查'), (step['检查'] || []).map((question, n) =>
        qCard(question, lesson.answers[question.id], async (body) => { const done = await answer(question, '检查')(body); setTimeout(reload, 300); return done; },
          {label: `检查 ${n + 1}`}))) : null);
  }

  function draw() {
    const steps = lesson['路线'] || [], diag = lesson['摸底'] || [];
    const stepDone = (step) => (step['检查'] || []).length && step['检查'].every((question) => lesson.answers[question.id]);
    if (!current || !steps.some((step) => step.id === current)) current = (steps.find((step) => !stepDone(step)) || steps[0] || {}).id;
    const diagDone = diag.length && diag.every((question) => lesson.answers[question.id]);
    const anyAnswer = Object.keys(lesson.answers || {}).length;
    const waiting = (text) => h('div.learn-wait', {}, h('span.learn-dot'), text);
    const at = steps.findIndex((step) => step.id === current);
    root.replaceChildren(
      h('a.learn-back.small', {href: `#/knowledge/${enc('学习')}`}, '← 全部学习'),
      h('header.lesson-head', {}, h('div', {}, h('h2', {}, lesson['目标']), lesson['为什么学'] ? h('p.quiet', {}, '为什么学：' + lesson['为什么学']) : null),
        h('div.lesson-meta', {}, h('span.st-tag.' + (STATE_TONE[lesson['状态']] || ''), {}, lesson['状态']),
          steps.length ? [bar(...lesson.progress), h('small.quiet', {}, `${lesson.progress[0]} / ${lesson.progress[1]} 步`)] : null)),
      // 摸底
      h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '摸底'), h('span.quiet.small', {}, '答错没关系，这是看你现在懂到哪；把握和思路填上，路线才排得准')),
        diag.length ? h('div.q-list', {}, diag.map((question, n) => qCard(question, lesson.answers[question.id], async (body) => {
          const done = await answer(question, '摸底')(body); setTimeout(reload, 300); return done;
        }, {label: `摸底 ${n + 1}`})))
          : waiting('助手在出摸底题（一两分钟），出好了这里会自己刷新。'),
        diag.length && !steps.length ? h('div.row', {}, h('span.grow'), diagDone
          ? h('button.btn.primary', {type: 'button', onclick: () => ask(`按技能「学习」第二节，给学习「${lesson.name}」（id ${lesson.id}）排路线：lesson get 读我的摸底答案、把握和思路，挑要教和要巩固的点，lesson save route。`)},
            icon('spark', 14), '按摸底排路线')
          : h('span.quiet.small', {}, `还有 ${diag.filter((question) => !lesson.answers[question.id]).length} 题没答`)) : null),
      // 路线
      steps.length ? h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, `路线（${steps.length} 步）`), h('span.quiet.small', {}, '每步先猜、再看讲解、再做检查题')),
        h('div.route', {}, h('ol.route-rail', {}, steps.map((step, n) => h('li', {class: (step.id === current ? 'on ' : '') + (stepDone(step) ? 'done' : '')},
          h('button', {type: 'button', onclick: () => { current = step.id; draw(); }}, h('span.route-num', {}, stepDone(step) ? '✓' : String(n + 1)), h('span', {}, step['标题']))))),
        h('div.route-main', {}, at >= 0 ? stepView(steps[at]) : null,
          h('div.row', {}, at > 0 ? h('button.btn.ghost.small', {type: 'button', onclick: () => { current = steps[at - 1].id; draw(); }}, '上一步') : null, h('span.grow'),
            at < steps.length - 1 ? h('button.btn.small', {type: 'button', onclick: () => { current = steps[at + 1].id; draw(); }}, '下一步') : null))))
        : diagDone ? h('section.block.wide', {}, waiting('等助手按摸底排路线（点了"按摸底排路线"之后一两分钟）。')) : null,
      // 讲评和小结
      anyAnswer ? h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '讲评和小结'), h('span.grow'),
        h('button.btn.small', {type: 'button', onclick: () => ask(`按技能「学习」第三节，讲评学习「${lesson.name}」（id ${lesson.id}）：lesson get 读全部记录，简答题判分，看思路写讲评，写小结。`)},
          icon('spark', 13), '让助手讲评')),
      lesson['小结'] ? h('div.md.lesson-summary', {html: md(lesson['小结'])}) : h('p.quiet', {}, '做完一些题后让助手讲评：简答题判分、看你的思路、点出误解、写小结。')) : null);
  }
  draw();
  whenIdle(root, reload);
  return root;
}
