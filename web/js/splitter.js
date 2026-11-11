// 分割条：所有"一栏固定宽 + 一栏占满"的布局，固定那栏的里侧边缘都能拖宽拖窄；记住每种布局的宽度，双击回到默认。
// 每种布局用自己的 CSS 变量（app.css 里 var(--xx, 默认宽)），嵌套的布局互不影响。助手栏单独在 app.js 里。
const SPLITS = [                                                                    // 具体的放前面（.test-split 也带 .split）
  {sel: '.test-split', side: 'left', name: '--test-l', min: 170, max: 600},         // 测试页左边的列表
  {sel: '.record', side: 'right', name: '--record-r', min: 220, max: 640},          // 记录页右边的关联
  {sel: '.overview', side: 'right', name: '--overview-r', min: 280, max: 760},      // 总览右边：等你拍板、新进来的数据
  {sel: '.assistant-page', side: 'left', name: '--lanes-l', min: 180, max: 520},    // 助手页的对话列表
  {sel: '.assistant-page.with-ctx', side: 'right', name: '--ctx-r', min: 220, max: 620},
  {sel: '.skill-split', side: 'left', name: '--skill-l', min: 160, max: 520},
  {sel: '.project-split', side: 'left', name: '--project-l', min: 180, max: 600},
  {sel: '.ana', side: 'left', name: '--ana-l', min: 180, max: 600},                 // GPC 对比的样品列表
  {sel: '.road', side: 'right', name: '--road-r', min: 260, max: 760},              // 课题路线点开一站
  {sel: '.overlay-body', side: 'left', name: '--overlay-l', min: 150, max: 520},    // 叠加图左边的样品
  {sel: '.team-layout', side: 'right', name: '--team-r', min: 260, max: 700},
  {sel: '.split', side: 'left', name: '--split-l', min: 150, max: 560},            // 记录列表、产出
];
const keep = (key, value) => { try { if (value === undefined) return localStorage.getItem(key); if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch (error) { return null; } };

function attach(box, spec) {
  const mark = 'split' + spec.side;
  if (box.dataset[mark]) return;
  const kids = [...box.children].filter((node) => !node.classList.contains('split-grip'));
  const pane = spec.side === 'left' ? kids[0] : kids[kids.length - 1];
  if (!pane) return;
  box.dataset[mark] = '1';
  const saved = Number(keep('lab.split' + spec.name));
  if (saved) box.style.setProperty(spec.name, saved + 'px');
  const grip = document.createElement('div');
  grip.className = 'split-grip ' + spec.side;
  grip.title = '拖动改宽窄，双击回到默认';
  if (getComputedStyle(box).position === 'static') box.style.position = 'relative';
  box.append(grip);                                   // 挂在布局上、对准那栏的边：那栏自己滚动时分割条不跟着跑
  const place = () => {
    const shown = pane.offsetWidth > 40 &&            // 收起来的栏（宽 0 或只剩一条边框）不显示分割条
      getComputedStyle(box).gridTemplateColumns.trim().split(/\s+/).length > 1;
    grip.hidden = !shown;
    if (shown) grip.style.left = (spec.side === 'left' ? pane.offsetLeft + pane.offsetWidth : pane.offsetLeft) - 3 + 'px';
  };
  new ResizeObserver(place).observe(pane);
  new ResizeObserver(place).observe(box);
  place();
  grip.addEventListener('dblclick', () => { box.style.removeProperty(spec.name); keep('lab.split' + spec.name, null); });
  grip.addEventListener('pointerdown', (event) => {
    event.preventDefault(); grip.setPointerCapture(event.pointerId); document.body.classList.add('resizing');
    const move = (e) => {
      const rect = box.getBoundingClientRect();
      const want = spec.side === 'left' ? e.clientX - rect.left : rect.right - e.clientX;
      box.style.setProperty(spec.name, Math.round(Math.min(Math.max(want, spec.min), spec.max, rect.width - 320)) + 'px');
    };
    const up = () => {
      grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up); document.body.classList.remove('resizing');
      keep('lab.split' + spec.name, parseInt(box.style.getPropertyValue(spec.name), 10) || null);
    };
    grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up);
  });
}

function scan(root) {
  for (const spec of SPLITS) {
    if (root.matches?.(spec.sel)) attach(root, spec);
    root.querySelectorAll?.(spec.sel).forEach((box) => attach(box, spec));
  }
}

export function watchSplits() {
  scan(document.body);
  new MutationObserver((records) => { for (const record of records) for (const node of record.addedNodes) if (node.nodeType === 1) scan(node); })
    .observe(document.body, {childList: true, subtree: true});
}
