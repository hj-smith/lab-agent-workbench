// 助手：一段对话（事项）的显示和输入。右侧助手栏和"助手"页面用同一个。
import {api, fmt, h, icon, listen, md} from './core.js';
import {cardBox, showBlocks} from './blocks.js';

const LABEL = {overview: '看全局', skill: '读技能', find: '查找', records: '研发库', sql: '查库', sci: '科学计算', files: '文件', python: 'Python', pip: '装包',
  shell: '命令行', web: '查资料', chart: '画图', export: '出文件', outputs: '登记产出', notes: '笔记', extend: '改平台', ask_user: '问你', later: '约复查', show: '展示', look: '看图',
  notify: '通知', delegate: '分身', plan: '计划', cards: '卡片'};
const QUIET = new Set(['ask_user', 'plan']);       // 这两个不画进记录：卡片单独画，计划钉在输入框上面
const SOURCE = {'人': '', '事件': '事件', '复查': '复查到点', '卡片': '回答'};

function argText(name, input) {
  if (!input) return '';
  if (name === 'sci') return [input.domain, input.function].filter(Boolean).join(' · ');
  if (name === 'python') return (input.code || '').trim().split('\n')[0];
  if (name === 'sql') return input.query || '';
  if (name === 'records') return [input.action, input.kind, input.ref].filter(Boolean).join(' · ');
  return Object.values(input).filter((value) => typeof value !== 'object').join(' · ').slice(0, 120);
}

export class Conversation {
  constructor({compact = false, context = () => ({}), onLane = () => {}} = {}) {
    this.compact = compact; this.context = context; this.onLane = onLane;
    this.lane = null; this.running = false; this.cards = {}; this.live = null; this.pending = []; this.stick = true;
    this.log = h('div.convo-log');
    this.list = h('div.convo-inner');
    this.log.append(this.list);
    this.log.addEventListener('scroll', () => { this.stick = this.log.scrollHeight - this.log.scrollTop - this.log.clientHeight < 80; });
    this.input = h('textarea.composer-input', {rows: 1, placeholder: '说一句话，或把文件拖进来', 'aria-label': '给助手的话'});
    this.chips = h('div.chips');
    this.sendButton = h('button.btn.primary.send', {type: 'button', title: '发送（助手在干活时也能发：它做完手上这一步就看到，按你说的调整）', onclick: () => this.send()}, icon('send', 16));
    this.stopButton = h('button.btn.ghost.icon-btn.stop-btn', {type: 'button', title: '停下', hidden: true, onclick: () => this.stop()}, icon('stop', 16));
    this.picker = h('input', {type: 'file', multiple: true, hidden: true, onchange: () => { this.upload([...this.picker.files]); this.picker.value = ''; }});
    this.composer = h('div.composer', {},
      this.chips,
      h('div.composer-row', {}, this.input,
        h('button.btn.ghost.icon-btn', {type: 'button', title: '加文件', onclick: () => this.picker.click()}, icon('attach', 17)), this.stopButton, this.sendButton),
      this.picker);
    this.planBox = h('div.plan-strip', {hidden: true});
    this.root = h('div.convo' + (compact ? '.compact' : ''), {}, this.log, this.planBox, this.composer);
    this.input.addEventListener('input', () => this.grow());
    this.input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); this.send(); }
    });
    this.root.addEventListener('dragover', (event) => event.preventDefault());
    this.root.addEventListener('drop', (event) => { event.preventDefault(); if (event.dataTransfer.files.length) this.upload([...event.dataTransfer.files]); });
    listen((event) => { if (event.lane === this.lane) this.onEvent(event); });
  }

  grow() { this.input.style.height = 'auto'; this.input.style.height = Math.min(this.input.scrollHeight, 200) + 'px'; }
  scroll() { if (this.stick) this.log.scrollTop = this.log.scrollHeight; }
  setRunning(on) {
    this.running = on; this.stopButton.hidden = !on; this.root.classList.toggle('busy', on);
    this.input.placeholder = on ? '它在干活，也可以接着说：改方向、补要求、让它停（它做完手上这一步就看到）' : '说一句话，或把文件拖进来';
  }
  async stop() { if (this.lane) await api(`/lanes/${encodeURIComponent(this.lane)}/stop`, 'POST'); }

  async open(laneId) {
    this.lane = laneId || null; this.cards = {}; this.live = null; this.list.replaceChildren(); this.stick = true;
    if (!laneId) { this.empty(); this.drawPlan([]); this.setRunning(false); return; }
    const snap = await api('/lanes/' + encodeURIComponent(laneId));
    this.title = snap.title;
    snap.messages.forEach((message, index) => this.replay(message, snap.running ? null : index));
    this.drawPlan(snap.plan || []);
    if (snap.card) this.drawCard(snap.card);
    for (const timer of snap.timers) this.list.append(h('div.note.quiet', {}, icon('clock', 14), ` 约好 ${fmt.clock(timer.due)} 回来：${timer.about}`));
    this.setRunning(snap.running);
    this.scroll();
  }

  // 计划清单：做到第几步一眼看到；全做完就收成一行，点开能看
  drawPlan(steps) {
    this.planBox.hidden = !steps.length;
    if (!steps.length) return this.planBox.replaceChildren();
    const done = steps.filter((step) => step.state === '做完').length, current = steps.findIndex((step) => step.state === '在做');
    const finished = done === steps.length;
    const open = this.planBox.dataset.open ? this.planBox.dataset.open === '1' : !finished;
    const head = h('button.plan-head', {type: 'button', onclick: () => { this.planBox.dataset.open = open ? '0' : '1'; this.drawPlan(steps); }},
      h('b', {}, '计划'), h('span.plan-count', {}, finished ? `${steps.length} 步都做完了` : `第 ${current >= 0 ? current + 1 : done + 1} / ${steps.length} 步`),
      h('span.plan-bar', {}, h('i', {style: `width:${Math.round(done / steps.length * 100)}%`})),
      current >= 0 && !open ? h('span.plan-now', {}, steps[current].text) : null, icon(open ? 'collapse' : 'expand', 14));
    this.planBox.replaceChildren(head, open ? h('ol.plan-steps', {}, steps.map((step) => h('li.' + ({做完: 'done', 在做: 'doing'}[step.state] || 'todo'), {},
      h('span.plan-mark', {}, step.state === '做完' ? icon('check', 13) : null), h('span', {}, step.text)))) : '');
  }

  empty() {
    const hint = this.context().hint || '';
    this.list.append(h('div.convo-empty', {}, h('p', {}, '想查什么、算什么、做什么文件，直接说。'), hint ? h('p.quiet', {}, hint) : null));
  }

  turn() { if (!this.live) { this.live = {box: h('div.turn'), kind: '', block: null}; this.list.append(this.live.box); } return this.live; }

  user(message, index = null) {
    this.live = null;
    if (message.source && message.source !== '人') {
      this.list.append(h('div.event-line', {}, h('span.tag', {}, SOURCE[message.source] || message.source), h('span', {}, (message.text || '').replace(/^\[[^\]]*\]\s*/, '').split('\n')[0].slice(0, 160))));
      return;
    }
    const bubble = h('div.bubble', {}, message.text || '');
    if ((message.files || []).length) bubble.append(h('div.att', {}, '附件：' + message.files.join('、')));
    const row = h('div.user', {}, bubble);
    if (index != null && this.lane) row.prepend(h('button.redo', {type: 'button', title: '改了重发：在这里重来，或另开一段', onclick: () => this.editAt(row, message, index)}, icon('edit', 13)));
    this.list.append(row);
  }

  // 改了重发：在这里重来（这句之后换成新的）或另开一段（原来的整段留着，新对话从这里接着）
  editAt(row, message, index) {
    const area = h('textarea.redo-text', {rows: Math.min(8, (message.text || '').split('\n').length + 1)}, message.text || '');
    const go = async (mode) => {
      const text = area.value.trim();
      if (!text) { area.focus(); return; }
      const context = this.context(), files = message.files || [];
      let lane = this.lane;
      if (mode === 'fork') lane = (await api(`/lanes/${encodeURIComponent(this.lane)}/fork`, 'POST', {upto: index})).lane;
      else await api(`/lanes/${encodeURIComponent(this.lane)}/rewind`, 'POST', {to: index});
      await api('/say', 'POST', {lane, text, files, anchor: context.anchor || '', library: context.library});
      if (lane !== this.lane) { this.lane = lane; this.onLane(lane); }
      await this.open(lane);
    };
    const editor = h('div.redo-box', {}, area,
      h('p.redo-hint', {}, '在这里重来：这句之后的对话换成新的（已经写进库里的改动不会跟着撤）。另开一段：原来的整段留着，新开一段从这里接着。'),
      h('div.redo-actions', {}, h('button.btn.ghost.small', {type: 'button', onclick: () => editor.replaceWith(row)}, '取消'), h('span.grow'),
        h('button.btn.small', {type: 'button', onclick: () => go('fork')}, '另开一段'),
        h('button.btn.primary.small', {type: 'button', onclick: () => go('rewind')}, '在这里重来')));
    row.replaceWith(editor);
    area.focus();
  }

  replay(message, index = null) {
    if (message.role === 'user') this.user(message, index);
    else if (message.role === 'assistant') {
      for (const part of message.parts) {
        if (part.type === 'tool' && QUIET.has(part.name)) continue;
        if (part.type === 'tool') this.toolCall({id: part.id, name: part.name, input: part.input});
        else this.stream(part.type, part.text, true);
      }
    } else if (message.role === 'tool') {
      for (const result of message.results) {
        if (result.name === 'ask_user') this.askResult(result);
        else if (result.name !== 'plan') this.toolResult(result);
      }
    } else this.note(message);
    this.live = null;
  }

  stream(kind, text, replayed) {
    const now = this.turn();
    if (now.kind !== kind || !now.block) {
      if (kind === 'thinking') {
        const box = h('details.think', {open: !replayed && !this.compact}, h('summary', {}, '思考'), h('div'));
        now.box.append(box); now.block = {node: box.lastChild, text: '', box};
      } else { const box = h('div.md'); now.box.append(box); now.block = {node: box, text: ''}; }
      now.kind = kind;
    }
    now.block.text += text;
    if (kind === 'thinking') now.block.node.textContent = now.block.text;
    else if (!now.block.due) { now.block.due = true; const block = now.block; requestAnimationFrame(() => { block.due = false; block.node.innerHTML = md(block.text); this.scroll(); }); }
  }

  toolCall({id, name, input}) {
    const now = this.turn();
    const card = this.cards[id] || h('details.tool.run', {},
      h('summary', {}, h('span.dot'), h('span.what', {}, LABEL[name] || name), h('span.arg', {}, argText(name, input)), h('span.sec', {}, '运行中')),
      h('div.tool-body'));
    if (!this.cards[id]) {
      this.cards[id] = card; now.box.append(card);
      const shown = name === 'python' ? input?.code : name === 'sql' ? input?.query : JSON.stringify(input, null, 1);
      card.lastChild.append(h('label', {}, '参数'), h('pre', {}, shown || ''));
    } else card.querySelector('.arg').textContent = argText(name, input);
    now.kind = '';
  }

  toolResult(result) {
    if (result.blocks?.length) {             // "展示"：直接画成卡片、表、交互曲线
      this.cards[result.id]?.remove();
      const now = this.turn();
      for (const entry of result.blocks) now.box.append(showBlocks(entry.blocks, this.context().library || '', entry.title));
      now.kind = '';
      return;
    }
    const card = this.cards[result.id];
    if (!card) this.toolCall({id: result.id, name: result.name, input: null});
    const box = this.cards[result.id];
    box.classList.remove('run'); if (result.error) box.classList.add('bad');
    box.querySelector('.sec').textContent = (result.error ? '出错 · ' : '') + (result.seconds != null ? result.seconds + ' 秒' : '');
    box.lastChild.append(h('label', {}, result.error ? '错误（已交给助手处理）' : '结果'), h('pre', {}, result.text || ''));
    const sub = result.name === 'delegate' && (result.text || '').match(/事项 ([\w-]+)|\[\S+ ([\w-]+) 的结果\]/);
    if (sub) this.turn().box.append(h('a.file-chip', {href: '#/assistant/' + encodeURIComponent(sub[1] || sub[2])}, icon('layers', 14), '看成员的这件活'));
    for (const name of result.files || []) this.file(name);
    this.turn().kind = '';
  }

  file(name) {
    const url = `/api/ws/${encodeURIComponent(this.lane)}/${name.split(/[\\/]/).map(encodeURIComponent).join('/')}`, lower = name.toLowerCase();
    const now = this.turn();
    if (/\.(png|jpe?g|gif|svg|webp)$/.test(lower)) now.box.append(h('figure.made', {}, h('img', {src: url, alt: name, loading: 'lazy'}), h('figcaption', {}, h('a', {href: url, target: '_blank'}, name))));
    else if (/\.html?$/.test(lower)) now.box.append(h('figure.made', {}, h('iframe', {src: url, loading: 'lazy', title: name}), h('figcaption', {}, h('a', {href: url, target: '_blank'}, name, ' ', icon('open', 13)))));
    else if (!/answers[\\/]/.test(name)) now.box.append(h('a.file-chip', {href: url, target: '_blank', download: name.split(/[\\/]/).pop()}, icon('file', 14), name.split(/[\\/]/).pop()));
  }

  note(message) {
    this.live = null;
    const node = h('div.note.' + message.role, {}, message.text,
      message.ref ? h('a.btn.small.note-open', {href: message.ref.href || `#/record/${encodeURIComponent(message.ref.library)}/${encodeURIComponent(message.ref.id)}`}, message.ref.label || '打开') : null);
    if (message.role === 'error') node.append(h('button.btn.small', {type: 'button', onclick: () => { node.remove(); this.send('继续'); }}, '继续'));
    this.list.append(node);
  }

  drawCard(card) {
    const box = cardBox({lane: this.lane, card}, {free: true, open: false, answer: (text, values) => this.answer(text, values)});
    box.classList.add('ask');
    this.list.append(box); this.live = null; this.scroll();
  }

  async answer(text, values) {
    await api(`/lanes/${encodeURIComponent(this.lane)}/answer`, 'POST', {text, values: Object.keys(values).length ? values : null});
  }

  // ask_user 的结果：他的回答画成他说的话；"定过了没再问"画成一行小字；卡片被退回改写的不画（助手会改好再发）
  askResult(result) {
    if (result.text.startsWith('他的回答：')) return this.answered(result.id, result.text.replace(/^他的回答：/, ''));
    const known = result.text.match(/他 (\S+ \S+) 定过——「(.*?)」→ (.*)（(?:定了|暂缓|不用问|结案)，决定记录/);
    if (known) this.list.append(h('div.note.info.decided-note', {}, `没再问你：「${known[2]}」你 ${known[1]} 定过 → ${known[3]}`));
  }

  answered(id, text) {
    const box = this.list.querySelector(`[data-card="${CSS.escape(id)}"]`);
    if (box) { box.classList.add('done'); box.querySelectorAll('button,input').forEach((element) => { element.disabled = true; }); }
    this.live = null;
    this.list.append(h('div.user', {}, h('div.bubble', {}, text)));
    this.scroll();
  }

  onEvent(event) {
    const type = event.type;
    if (type === 'input') { this.list.querySelectorAll('.steer-note').forEach((node) => node.remove()); this.user(event.message); }
    else if (type === 'assistant_start') { this.cards = {...this.cards}; this.list.querySelector('.waiting')?.remove(); this.list.append(h('div.waiting', {}, '正在想')); }
    else if (type === 'text' || type === 'thinking') { this.list.querySelector('.waiting')?.remove(); this.stream(type, event.data); }
    else if (type === 'tool_start') { this.list.querySelector('.waiting')?.remove(); if (!QUIET.has(event.data.name)) this.toolCall({id: event.data.id || 'pending-' + event.data.name, name: event.data.name, input: null}); }
    else if (type === 'tool_call') {
      const pending = this.cards['pending-' + event.name];
      if (pending && !this.cards[event.id]) { this.cards[event.id] = pending; delete this.cards['pending-' + event.name]; }
      if (!QUIET.has(event.name)) this.toolCall(event);
    }
    else if (type === 'tool_result') { if (event.name === 'ask_user') this.askResult(event); else if (event.name !== 'plan') this.toolResult(event); }
    else if (type === 'plan') this.drawPlan(event.steps || []);
    else if (type === 'card') { this.list.querySelector('.waiting')?.remove(); this.drawCard(event.card); }
    else if (type === 'answered') this.answered(event.card, event.text);
    else if (type === 'note') this.note(event.message);
    else if (type === 'compacted') this.list.append(h('div.event-line', {}, h('span.tag', {}, '压缩'), '前面的记录太长，已压缩成摘要'));
    else if (type === 'status') {
      if (event.state === 'running') this.setRunning(true);
      if (event.state === 'retrying') { this.list.querySelector('.waiting')?.remove(); this.list.append(h('div.waiting', {}, event.text)); }
      if (event.state === 'idle') { this.setRunning(false); this.list.querySelector('.waiting')?.remove(); this.live = null; }
    }
    this.scroll();
  }

  drawChips() {
    this.chips.replaceChildren(...this.pending.map((name, index) => h('span.chip', {}, name, h('button', {type: 'button', title: '去掉', onclick: () => { this.pending.splice(index, 1); this.drawChips(); }}, icon('close', 12)))));
  }

  async upload(files) {
    if (!this.lane) { this.lane = 'lane-' + Date.now().toString(36); this.onLane(this.lane); }
    for (const file of files) {
      await fetch(`/api/lanes/${encodeURIComponent(this.lane)}/files/${encodeURIComponent(file.name)}`, {method: 'PUT', body: file});
      if (!this.pending.includes(file.name)) this.pending.push(file.name);
    }
    this.drawChips(); this.input.focus();
  }

  async send(text) {
    text = (text ?? this.input.value).trim();
    if (!text && !this.pending.length) return;
    const context = this.context();
    const files = this.pending; this.pending = []; this.drawChips(); this.input.value = ''; this.grow();
    this.list.querySelector('.convo-empty')?.remove();
    if (this.running) {                                // 它在干活：话先排着，做完手上这一步就接进去
      this.list.append(h('div.steer-note', {}, h('span.steer-text', {}, text), h('small', {}, '已送到，它做完手上这一步就看到')));
      this.stick = true; this.scroll();
    }
    this.stick = true; this.setRunning(true);
    const {lane} = await api('/say', 'POST', {lane: this.lane, text, files, anchor: context.anchor || '', library: context.library});
    if (lane !== this.lane) { this.lane = lane; this.onLane(lane); }
  }
}
