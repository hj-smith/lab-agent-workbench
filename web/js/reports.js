// GPC 报告和期刊图：一条小工具栏，放在单样品、叠加比较、实验演化三个页面上。
// 报告出 PDF / Word / Excel；图按期刊习惯画（600 dpi 或 SVG，单栏 89 mm / 双栏 183 mm），都先在右边预览再下载。
import {h, icon} from './core.js';
import {previewExport} from './viewer.js';

const enc = encodeURIComponent;
const FIGURES = {
  sample: [['chromatogram', '色谱图'], ['distribution', '分子量分布']],
  compare: [['overlay', '分布叠加'], ['chrom_overlay', '色谱叠加']],
  experiment: [['trend', 'Mn、Mw、Đ 趋势'], ['waterfall', '瀑布图'], ['bands', '区段质量分数'], ['kinetics', '动力学拟合']],
};
const NAME = {sample: 'GPC 样品报告', compare: 'GPC 比较报告', experiment: 'GPC 实验报告'};

export function reportBar(library, kind, refs) {
  const list = () => (typeof refs === 'function' ? refs() : refs).join(',');
  const report = (fmt, label) => previewExport(`/api/gpc-report/${enc(library)}?kind=${kind}&refs=${enc(list())}&fmt=${fmt}`, `${NAME[kind]} ${label}`);
  const figure = h('select', {}, FIGURES[kind].map(([value, label]) => h('option', {value}, label)));
  const size = h('select', {title: '期刊常用尺寸'}, [['single', '单栏 89 mm'], ['double', '双栏 183 mm'], ['wide', '宽幅 183×80 mm']].map(([value, label]) => h('option', {value}, label)));
  const fmt = h('select', {}, [['png', 'PNG 600 dpi'], ['svg', 'SVG（矢量，可改）'], ['tiff', 'TIFF 600 dpi']].map(([value, label]) => h('option', {value}, label)));
  return h('div.report-bar', {},
    h('span.report-label', {}, icon('download', 13), NAME[kind]),
    h('button.btn.small', {type: 'button', onclick: () => report('pdf', 'PDF')}, 'PDF'),
    h('button.btn.small', {type: 'button', onclick: () => report('docx', 'Word')}, 'Word'),
    h('button.btn.small', {type: 'button', title: '结果、参数和曲线数据，一张表一个工作表', onclick: () => report('xlsx', 'Excel')}, 'Excel'),
    h('span.report-sep'),
    h('span.report-label', {}, '期刊图'), figure, size, fmt,
    h('button.btn.small', {type: 'button', onclick: () => previewExport(`/api/gpc-figure/${enc(library)}?name=${figure.value}&refs=${enc(list())}&fmt=${fmt.value}&size=${size.value}`,
      figure.selectedOptions[0].textContent)}, '出图'));
}
