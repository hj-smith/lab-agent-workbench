// 课题线路图：每个课题一条线，站点按顺序，换乘 = 两个课题的站点挂着同一条记录。
import {svg} from './core.js';

const STAGE_LABEL = {'走过': 'done', '当前': 'current', '没到': 'todo'};
const PALETTE = ['#c96442', '#3f7cc0', '#b5841c', '#5f8c3e', '#8a63b8', '#c25f8a'];
const esc = (text) => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;');

// 以前存进课题记录的线路色是旧的那套亮色，换成现在配色里对应的颜色
const OLD_LINE = {'#e34948': '#c96442', '#2a78d6': '#3f7cc0', '#eda100': '#b5841c', '#1baf7a': '#5f8c3e', '#6250d6': '#8a63b8', '#e87ba4': '#c25f8a'};
export function lineColor(project, index) { const ink = project['线路色'] || PALETTE[index % PALETTE.length]; return OLD_LINE[String(ink).toLowerCase()] || ink; }

export function transitMap(projects, {width = 760, onStation, focus} = {}) {
  const rowGap = 150, left = 64, right = width - 64, top = 34;
  const stations = projects.map((project) => project['站点'] || []);
  const owners = {};                               // 记录 id → 哪些 [课题, 站]
  stations.forEach((list, row) => list.forEach((station, col) => (station['记录'] || []).forEach((id) => (owners[id] ||= []).push([row, col]))));
  const shared = new Set(Object.values(owners).filter((places) => new Set(places.map((p) => p[0])).size > 1).flat().map(([row, col]) => `${row}:${col}`));
  const height = top + projects.length * rowGap;
  const parts = [];
  projects.forEach((project, row) => {
    const list = stations[row], color = lineColor(project, row), y = top + row * rowGap + 34;
    const step = list.length > 1 ? (right - left) / (list.length - 1) : 0;
    const room = Math.max(4, Math.floor((step || 200) / 13.5) - 1);      // 两站之间放得下几个字
    const cut = (text, n) => text.length > n ? text.slice(0, n - 1) + '…' : text;
    const xs = list.map((_, col) => left + col * step);
    const current = Math.max(0, list.findIndex((station) => station['状态'] === '当前'));
    const dim = focus && focus.row !== row ? ' dim' : '';
    parts.push(`<g class="line${dim}" data-row="${row}">`);
    parts.push(`<text class="t-line" x="${left - 30}" y="${y - 32}">${esc(project.name)}</text>`);
    if (project['一句话']) parts.push(`<text class="t-sub" x="${left - 30}" y="${y - 14}">${esc(cut(project['一句话'], Math.floor((right - left) / 12.5)))}</text>`);
    if (xs.length > 1) {
      parts.push(`<path d="M${xs[0]} ${y}H${xs[current]}" stroke="${color}" class="track"/>`);
      parts.push(`<path d="M${xs[current]} ${y}H${xs[xs.length - 1]}" stroke="${color}" class="track ahead"/>`);
    }
    list.forEach((station, col) => {
      const state = STAGE_LABEL[station['状态']] || 'todo', x = xs[col];
      const interchange = shared.has(`${row}:${col}`);
      const label = esc(station['站']) + (station['标题'] ? ' · ' + esc(station['标题']) : '');
      parts.push(`<g class="station ${state}${interchange ? ' x' : ''}" data-row="${row}" data-col="${col}" tabindex="0" role="button" aria-label="${esc(project.name)} ${label}">`);
      if (state === 'current') parts.push(`<circle cx="${x}" cy="${y}" r="17" fill="${color}" class="halo"/>`);
      if (interchange) parts.push(`<circle cx="${x}" cy="${y}" r="12" class="ring"/>`);
      parts.push(`<circle cx="${x}" cy="${y}" r="${state === 'current' ? 9 : 6.5}" stroke="${color}" fill="${state === 'current' ? color : '#fff'}" class="dot"/>`);
      const edge = col === 0 ? ` style="text-anchor:start" x="${x - 10}"` : col === list.length - 1 && list.length > 1 ? ` style="text-anchor:end" x="${x + 10}"` : ` x="${x}"`;
      parts.push(`<text${edge} y="${y + 27}" class="t-stop"><title>${esc(station['站'])}</title>${esc(cut(station['站'], room + 1))}</text>`);
      if (station['标题']) parts.push(`<text${edge} y="${y + 44}" class="t-title"><title>${esc(station['标题'])}</title>${esc(cut(station['标题'], room + 2))}</text>`);
      if (station['说明'] && state === 'current') parts.push(`<text${edge} y="${y + 60}" class="t-note"><title>${esc(station['说明'])}</title>${esc(cut(station['说明'], room + 3))}</text>`);
      parts.push('</g>');
    });
    parts.push('</g>');
  });
  const node = svg(`<svg class="transit" viewBox="0 0 ${width} ${height}" width="100%" preserveAspectRatio="xMinYMin meet">${parts.join('')}</svg>`);
  node.querySelectorAll('.station').forEach((element) => {
    const pick = () => onStation?.(+element.dataset.row, +element.dataset.col);
    element.addEventListener('click', pick);
    element.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); pick(); } });
  });
  return node;
}
