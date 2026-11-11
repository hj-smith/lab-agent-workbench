// 作图偏好：每类图默认怎么画（波长看哪段、按什么上色、看哪些指标……）。图上的"设为默认"存进产品库设置"作图偏好"，
// 下次打开这类图就这样画，助手作图前也会读。键和值都写成中文，助手读得懂（例：老化谱 → 颜色=按天数，显示=首末两天）。
import {api, h, icon, toast} from './core.js';

const enc = encodeURIComponent;

// 每次打开图都现读（别的窗口、助手刚改过的也能马上用上）
export async function chartPref(library, chart, defaults = {}) {
  const {preferences} = await api(`/chart-preference/${enc(library)}`);
  return {...defaults, ...((preferences || {})[chart] || {})};
}

// current()：现在的画法（只放要记的那几项）
export function defaultButton(library, chart, current) {
  return h('button.btn.ghost.small.pref-save', {type: 'button', title: '把现在这几项存成这类图的默认：以后都这样画，助手作图也照这个来', onclick: async () => {
    const value = current();
    await api(`/chart-preference/${enc(library)}/${enc(chart)}`, 'PUT', value);
    toast(`记下了：以后「${chart}」都这样画`);
  }}, icon('check', 13), '设为默认');
}

// 一排小按钮选一个（改了就重画）
export function segs(pref, key, options, redraw) {
  return h('div.segs', {}, options.map((value) => h('button.seg' + (pref[key] === value ? '.on' : ''), {type: 'button', onclick: (event) => {
    pref[key] = value;
    event.currentTarget.parentElement.querySelectorAll('.seg').forEach((button) => button.classList.toggle('on', button === event.currentTarget));
    redraw();
  }}, value)));
}

// 横轴从多少到多少：空 = 自动
export function span(pref, from, to, unit, redraw) {
  const input = (key, placeholder) => h('input.range-in', {type: 'text', inputmode: 'decimal', value: pref[key] ?? '', placeholder, onchange: (event) => {
    const value = parseFloat(event.target.value); pref[key] = Number.isFinite(value) ? value : null; redraw(); }});
  return h('span.pref-span', {}, input(from, '自动'), h('span.quiet.small', {}, '到'), input(to, '自动'), h('span.quiet.small', {}, unit));
}

// Plotly 的横轴范围：两头都填了才固定，否则自动
export const range = (pref, from, to) => pref[from] != null && pref[to] != null ? {range: [Math.min(pref[from], pref[to]), Math.max(pref[from], pref[to])], autorange: false} : {autorange: true};
