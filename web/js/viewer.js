// 文件预览：导出的 Excel、Word、PDF 先在右边看一眼，再下载或打印。产出页也用这里的排法。
import {api, h, icon, toast} from './core.js';
import {number} from './blocks.js';

// Excel / CSV 排成表（每张表一个按钮），Word 排成段落和表。data 来自服务器的 preview（tabular.preview_file）。
export function previewContent(data) {
  if (data.kind !== 'sheets') {
    return {tabs: null, body: h('article.doc-preview', {}, data.parts.map((part) => part.type === 'heading' ? h('h3', {}, part.text) : part.type === 'text' ? h('p', {}, part.text)
      : h('div.tablewrap.flat', {}, h('table.data.compact', {}, h('tbody', {}, part.rows.map((row) => h('tr', {}, row.map((value) => h('td', {}, value)))))))))};
  }
  let at = 0;
  const body = h('div');
  const draw = () => {
    const sheet = data.sheets[at];
    body.replaceChildren(h('div.tablewrap.flat.sheet-preview', {}, h('table.data.compact', {}, h('tbody', {}, sheet.rows.map((row) => h('tr', {},
      row.map((value) => h('td' + (typeof value === 'number' ? '.num' : ''), {}, typeof value === 'number' ? number(value) : value ?? ''))))))),
    sheet.total > sheet.rows.length ? h('p.quiet.small', {}, `只显示前 ${sheet.rows.length} 行，共 ${sheet.total} 行；完整的请下载。`) : null);
  };
  const tabs = data.sheets.length > 1 ? h('div.segs', {}, data.sheets.map((sheet, index) => h('button.seg' + (index ? '' : '.on'), {type: 'button', onclick: () => {
    at = index; tabs.querySelectorAll('.seg').forEach((button, i) => button.classList.toggle('on', i === at)); draw();
  }}, sheet.name))) : null;
  draw();
  return {tabs, body};
}

function fileName(disposition, fallback) {
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition || '');
  if (star) return decodeURIComponent(star[1]);
  const plain = /filename="?([^";]+)"?/i.exec(disposition || '');
  return plain ? plain[1] : fallback;
}

// 导出按钮点了先预览：右边拉出一栏，看对了再下载；PDF 能直接打印。Esc 或"关上"收起来。
let open = null;
export async function previewExport(url, label = '导出') {
  open?.close();
  const title = h('b', {}, label), actions = h('div.viewer-actions'), body = h('div.viewer-body', {}, h('p.quiet', {}, '正在生成…'));
  const panel = h('aside.viewer', {role: 'dialog', 'aria-label': label + ' 预览'}, h('header.viewer-head', {}, title, actions,
    h('button.btn.ghost.small', {type: 'button', onclick: () => close()}, icon('close', 14), '关上')), body);
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  let href = null;
  const close = () => { panel.remove(); document.removeEventListener('keydown', onKey); if (href) URL.revokeObjectURL(href); open = null; };
  open = {close};
  document.addEventListener('keydown', onKey);
  document.body.append(panel);
  const response = await fetch(url);
  if (!response.ok) { body.replaceChildren(h('p.doubt-text', {}, '没生成出来：' + (await response.text()).slice(0, 300))); return; }
  const blob = await response.blob();
  const name = fileName(response.headers.get('content-disposition'), label);
  href = URL.createObjectURL(blob);
  title.textContent = name;
  const download = h('a.btn.primary.small', {href, download: name}, icon('download', 14), '下载');
  if (/\.pdf$/i.test(name) || blob.type === 'application/pdf') {
    const frame = h('iframe.viewer-frame', {src: href, title: name});
    actions.replaceChildren(download, h('button.btn.small', {type: 'button', onclick: () => frame.contentWindow?.print()}, '打印'));
    body.replaceChildren(frame);
    return;
  }
  actions.replaceChildren(download);
  if (/\.(png|svg|jpe?g)$/i.test(name)) { body.replaceChildren(h('div.viewer-picture', {}, h('img', {src: href, alt: name}))); return; }   // 图直接看
  if (!/\.(xlsx|xlsm|csv|tsv|docx)$/i.test(name)) { body.replaceChildren(h('p.quiet', {}, '这种文件在页面上看不了，请下载后打开。')); return; }
  try {
    const data = await (await fetch(`/api/preview-bytes?name=${encodeURIComponent(name)}`, {method: 'POST', body: blob})).json();
    const {tabs, body: content} = previewContent(data);
    if (tabs) actions.prepend(tabs);
    body.replaceChildren(content);
  } catch (error) {
    body.replaceChildren(h('p.quiet', {}, '预览没排出来，可以直接下载看。'));
    toast('预览没排出来');
  }
}
