// 研发台 · 下一轮怎么做：每条单因素扫描（只改一个用量、≥3 个水平）一张建议卡——下一轮配哪几个水平、要不要先重测、要不要往外扫。
// 建议用旧平台的 scan_advice 原样算（lab/scans.py 取数）；卡片下面是点的小图（每个指标一张，虚线是参比）。
import {api, h, icon} from './core.js';
import {number, plotly} from './blocks.js';

const enc = encodeURIComponent;
const COLORS = {'ellipsometry:n193': '#3f7cc0', 'ellipsometry:k193': '#c96442', 'ellipsometry:thickness_nm': '#b5841c', 'ellipsometry:thickness_loss_pct@SolvA': '#8a63b8'};

export async function scanAdvice(state) {
  const data = await api(`/scan-advice/${enc(state.library)}`);
  const Plotly = await plotly();
  const factorName = (factor) => factor.replace(/:solid$/, '（占树脂固体 %）').replace(/:volatile$/, '（易挥发有效份 %）');
  const cards = data.cards.map((item) => {
    const card = item.card, plots = h('div.scan-plots');
    const conf = card.confidence ? `把握：${card.confidence.level || ''}${(card.confidence.reasons || []).length ? '（' + card.confidence.reasons.join('；') + '）' : ''}` : '';
    const box = h('section.block.scan-card', {},
      h('header.block-head', {}, h('h3', {}, `${item.group.replace(/^PA-|^PB-/, '')} · ${factorName(item.factor)}`), h('span.grow'),
        h('button.btn.ghost.small', {type: 'button', onclick: () => state.panel.ask(`看一下 ${item.group} 这条扫描（${item.factor}）的下一轮建议：${card.suggestion || ''}。结合配方和测试结果，说说要不要照做、先做哪几个。`, '研发台 · 下一轮')}, icon('spark', 13), '让助手看')),
      h('p.scan-suggestion' + (card.insufficient ? '.quiet' : ''), {}, card.suggestion || '没有建议'),
      (card.facts || []).length ? h('ul.scan-facts', {}, card.facts.map((text) => h('li', {}, text))) : null,
      (card.inferences || []).length ? h('ul.scan-facts.infer', {}, card.inferences.map((text) => h('li', {}, '推断：' + text))) : null,
      conf ? h('p.quiet.small', {}, conf) : null, plots);
    requestAnimationFrame(() => {
      Object.entries(data.metrics).forEach(([rid, label]) => {
        const pts = item.points.filter((p) => p[rid] != null);
        if (pts.length < 2) return;
        const node = h('div.plot');
        plots.append(h('div.scan-plot', {}, h('div.small.quiet', {}, label), node));
        const ref = item.reference[rid];
        Plotly.newPlot(node, [{x: pts.map((p) => p.x), y: pts.map((p) => p[rid]), mode: 'lines+markers', line: {color: COLORS[rid], width: 1.8}, marker: {size: 7},
          text: pts.map((p) => `${p.code}（${p.films} 块膜）`), hovertemplate: '%{text}<br>用量 %{x} · %{y:.4g}<extra></extra>'}],
        {paper_bgcolor: '#fbfaf7', plot_bgcolor: '#fbfaf7', height: 170, margin: {l: 48, r: 8, t: 6, b: 30}, showlegend: false, font: {family: 'Microsoft YaHei, sans-serif', size: 11, color: '#29261f'},
          xaxis: {gridcolor: '#ebe6dc', zeroline: false}, yaxis: {gridcolor: '#ebe6dc', zeroline: false},
          shapes: ref != null ? [{type: 'line', xref: 'paper', x0: 0, x1: 1, y0: ref, y1: ref, line: {color: '#8f897c', width: 1, dash: 'dash'}}] : []},
        {displaylogo: false, responsive: true});
      });
    });
    return box;
  });
  return h('div.page-body', {},
    h('p.quiet', {}, `从配方里自动找出"只改一个用量、其它不变、≥3 个水平"的一组（${data.cards.length} 条），按旧平台的规则给下一轮建议：关键区间里加密、往外再走一步、或者先重测。小图里的虚线是参比（${data.reference} 的膜，同测试日优先）。损失率越小越好，n、k、膜厚越接近参比越好。`),
    h('div.scan-cards', {}, cards));
}
