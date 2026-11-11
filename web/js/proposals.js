// 研发台 · 下一轮提议：用做过的树脂批给每个结果建模型（高斯过程），先看能不能信（留一验证），再按你定的目标给几组下一轮条件。
// 算在后端（lab/surrogate.py）；这里选目标、画可信度和提议。提议能交给助手写成设计（派生新实验）。
import {api, h, icon} from './core.js';
import {number, plotly} from './blocks.js';

const enc = encodeURIComponent;
const GOALS = [['max', '越大越好'], ['min', '越小越好'], ['target', '接近']];
const RESPONSES = ['产率', 'Mn', 'Mw', 'Đ', 'n 193 nm', 'k 193 nm', '膜厚', 'SolvA膜厚损失率', '老化 A500'];

export async function proposalsPage(state) {
  const box = h('div.page-body');
  let objectives = [{response: '产率', goal: 'max'}, {response: 'Mw', goal: 'target', value: 1800}];
  let extrapolate = 0, kappa = 1;
  const goalsBox = h('div.prop-goals'), result = h('div');

  const drawGoals = () => goalsBox.replaceChildren(
    ...objectives.map((item, index) => h('div.prop-goal', {},
      h('select', {onchange: (e) => { item.response = e.target.value; }}, RESPONSES.map((name) => h('option', {selected: name === item.response}, name))),
      h('select', {onchange: (e) => { item.goal = e.target.value; drawGoals(); }}, GOALS.map(([value, label]) => h('option', {value, selected: value === item.goal}, label))),
      item.goal === 'target' ? h('input', {type: 'number', step: 'any', value: item.value ?? '', style: 'width:90px', onchange: (e) => { item.value = Number(e.target.value); }}) : null,
      h('button.btn.ghost.small', {type: 'button', onclick: () => { objectives.splice(index, 1); drawGoals(); }}, '×'))),
    h('button.btn.ghost.small', {type: 'button', onclick: () => { objectives.push({response: 'Mn', goal: 'max'}); drawGoals(); }}, icon('plus', 13), '加一个目标'),
    h('label.inline', {title: '在做过的范围外放宽多少（0 = 只在做过的范围里找）'}, '放宽', h('select', {onchange: (e) => { extrapolate = Number(e.target.value); }},
      [[0, '不放宽'], [0.1, '10 %'], [0.2, '20 %']].map(([value, label]) => h('option', {value}, label)))),
    h('label.inline', {title: '越大越保守：宁可选把握大的，不选看起来最好但不确定的'}, '保守', h('select', {onchange: (e) => { kappa = Number(e.target.value); }},
      [[0.5, '少一点'], [1, '一般'], [2, '多一点']].map(([value, label]) => h('option', {value, selected: value === 1}, label)))),
    h('button.btn.primary.small', {type: 'button', onclick: run}, icon('spark', 13), '算'));

  async function run() {
    result.replaceChildren(h('p.quiet', {}, '正在给每个结果建模型、留一验证、找条件（约 5 秒）…'));
    const data = await api(`/surrogate/${enc(state.library)}`, 'POST', {objectives, extrapolate, kappa});
    const Plotly = await plotly();
    const trust = h('table.data.compact', {}, h('thead', {}, h('tr', {}, ['结果', '批数', '留一 R²', '误差（RMSE）', '能不能信', '最要紧的因子'].map((c) => h('th', {}, c)))),
      h('tbody', {}, data.models.map((m) => h('tr', {}, h('td', {}, m.response), h('td.num', {}, m.n), h('td.num', {}, m.r2 == null ? '—' : number(m.r2, 2)),
        h('td.num', {}, m.rmse == null ? '—' : number(m.rmse, 3)),
        h('td', {}, h('span.st-tag' + (m.state === '能用' ? '.done' : m.state === '不太可信' ? '.open' : ''), {}, m.state), m.note ? h('small.quiet', {}, ' ' + m.note) : null),
        h('td.small', {}, (m.importance || []).slice(0, 3).map(([name, w]) => `${name}（${Math.round(w * 100)} %）`).join('、'))))));
    const scatter = h('div.prop-scatter');
    const DIGITS = {单体比: 2, 催化剂用量: 1, 反应固含: 3, 反应温度: 0, 反应时间: 1, 'AddH/初始MonA': 2};       // 按实际能做到的精度写
    const params = (p) => Object.entries(p.x).map(([k, v]) => `${k} ${number(v, DIGITS[k] ?? 2)}`).join(' · ') + ' · ' + Object.entries(p.c).filter(([, v]) => v !== '（没写）').map(([k, v]) => `${k} ${v}`).join(' · ');
    const shown = [...new Set([...data.used, '产率', 'Mw', 'Mn'])].filter((name) => data.models.some((m) => m.response === name && m.r2 != null));
    const props = h('table.data.compact.prop-table', {}, h('thead', {}, h('tr', {}, ['#', '条件', ...shown.map((name) => `${name} 预测`), '最像哪批', ''].map((c) => h('th', {}, c)))),
      h('tbody', {}, data.proposals.map((p, index) => h('tr', {}, h('td', {}, index + 1), h('td.small', {}, params(p)),
        ...shown.map((name) => h('td.num', {}, p.predicted[name] ? `${number(p.predicted[name].mean, name === 'Đ' ? 2 : 0)} ± ${number(p.predicted[name].sd, name === 'Đ' ? 2 : 0)}` : '—')),
        h('td', {}, p.nearest),
        h('td', {}, h('button.btn.ghost.small', {type: 'button', onclick: () => state.panel.ask(`照这组条件写下一个实验的设计（从最像的 ${p.nearest} 派生，改这些条件；写之前先说说你觉得这组条件靠不靠谱）：${params(p)}。模型预测：${shown.map((name) => p.predicted[name] ? `${name} ${Math.round(p.predicted[name].mean)} ± ${Math.round(p.predicted[name].sd)}` : '').filter(Boolean).join('，')}。`, '研发台 · 下一轮提议')}, icon('spark', 13), '让助手写设计'))))));
    result.replaceChildren(
      data.skipped.length ? h('p.warn-ink.small', {}, `这些目标的模型不可信或点太少，没拿来找条件：${data.skipped.join('、')}`) : null,
      h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, `模型能不能信（${data.lots} 个树脂批）`), h('span.quiet.small', {}, '留一验证：每批都用其它批的模型去猜它；R² 越接近 1 越准，低于 0.3 不拿来提议')), trust),
      h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '下一轮提议'), h('span.quiet.small', {}, '± 是模型的不确定度；条件在做过的范围里（选了放宽就略超出）。只是建议，不是保证')), props),
      h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '模型猜得准不准（留一：预测 vs 实测）')), scatter));
    requestAnimationFrame(() => data.models.filter((m) => m.loo).forEach((m) => {
      const node = h('div.plot');
      scatter.append(h('div', {}, h('div.small.quiet', {}, `${m.response}（R² ${number(m.r2, 2)}）`), node));
      const all = m.loo.flatMap((p) => [p.measured, p.predicted]), lo = Math.min(...all), hi = Math.max(...all);
      Plotly.newPlot(node, [{x: m.loo.map((p) => p.measured), y: m.loo.map((p) => p.predicted), mode: 'markers', marker: {color: m.state === '能用' ? '#c96442' : '#968f82', size: 7},
        error_y: {type: 'data', array: m.loo.map((p) => p.sd), visible: true, color: '#d4cdbe'}, text: m.loo.map((p) => p.name), hovertemplate: '%{text}<br>实测 %{x:.4g} · 预测 %{y:.4g}<extra></extra>'},
      {x: [lo, hi], y: [lo, hi], mode: 'lines', line: {color: '#8f897c', dash: 'dot', width: 1}, hoverinfo: 'skip'}],
      {paper_bgcolor: '#fbfaf7', plot_bgcolor: '#fbfaf7', height: 220, margin: {l: 50, r: 8, t: 6, b: 34}, showlegend: false, font: {family: 'Microsoft YaHei, sans-serif', size: 11, color: '#29261f'},
        xaxis: {title: {text: '实测'}, gridcolor: '#ebe6dc', zeroline: false}, yaxis: {title: {text: '预测'}, gridcolor: '#ebe6dc', zeroline: false}}, {displaylogo: false, responsive: true});
    }));
  }

  box.append(h('p.quiet', {}, '用研发台树脂批总表里做过的批，给每个结果建一个高斯过程模型（数学和 BayBE 默认的一样）。先看模型能不能信，再按你定的目标在做过的范围里找最可能达到的条件。批少、结果缺得多时，建议只当参考。'),
    h('section.block.wide', {}, h('header.block-head', {}, h('h3', {}, '目标')), goalsBox), result);
  drawGoals();
  run();
  return box;
}
