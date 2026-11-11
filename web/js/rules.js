// 业务规矩：原来写死在各个程序里的命名规则、别名、容差、默认条件……现在都是这个产品库的设置，这里看、这里改。
// 改了自动存，下一次导入、体检、计算就按新的来。只做说明用的（程序里是固定做法）单独列出，不给改，免得白改。
import {api, h} from './core.js';

const GROUPS = [
  ['命名和关联', '文件名、样品名怎么认成编号，记录之间怎么对上。导入和数据体检按这些走。',
    ['平台.命名规则', '平台.写法别名', '平台.人工对应', '平台.重复剔除', '树脂.实验别名', '树脂.GPC命名规则', '椭偏.配方别名规则', '老化.文件名规则', '平台.体检编号模式']],
  ['参比和判定', '默认拿谁比、差多少算超差、异常点怎么认。', ['平台.默认参比', '椭偏.容差', '椭偏.其它规则', '平台.分析默认值', 'NK.参比']],
  ['条件和参数', '没写条件时按什么算、分析用的参数。', ['椭偏.默认条件', 'NK.分析参数', '老化.分析设置', '树脂.HPLC方法', '树脂.库偏好']],
  ['因子', '研发台和实验卡片上的因子从设计里怎么抽出来。', ['平台.核心因子', '树脂.因子规则']],
];
// 原来的"固定做法"：能选的做成真规矩（GPC 和配方的做法），改了下一次计算就按新的；没有别的做法可选的，写明为什么、要改去哪里
GROUPS.push(['GPC 和配方的做法', '改了下一次计算、开工单就按新的走；已经存着的结果不会自己变，要重算点那一页的"重算"或批处理。',
  ['GPC.Mp口径', 'GPC.分布抽点上限', 'GPC.新区间默认外推', '配方.称量下限', '配方.建议预混浓度', '配方.工单编号规则']]);
const CHOICES = {'GPC.Mp口径': ['分布峰顶', '色谱峰高']};
const FIXED = {
  'GPC.积分算法': '算法包里只有这一种积分（精确边界梯形 v2），没有别的可选。',
  'GPC.标准曲线用存的系数': '要换曲线：到 测试 → GPC → 标准曲线 里改标准点、重新拟合，存成一条新曲线，再给样品换上。',
  'GPC.新样品自动基线': '自动基线还没做（在 GPC 旧功能清单里排着），现在新样品都是拖锚点。',
  'GPC.报告选项': '出 GPC 报告时在报告那里选（版式、图、DPI），选过的会记住。',
  '配方.剂量口径': '口径的名称；配方组成页里四种口径随时切换着看。',
  '配方.工单冻结': '业务规矩：开工单时冻结配方和计划，原料库以后改了不影响这张单。',
  '平台.关联规则': '旧平台自动连关联的规则，新系统按记录里的编号字段直接连，没用到它。',
  '平台.条件读取规则': '旧平台从样品名读条件的写法；新系统椭偏的条件由椭偏核心从表里读，没用到它。',
  '平台.产品系列': '旧平台认样品属于哪个产品的写法；新系统一个产品一个库，没用到它。',
  '平台.补充数据项': '旧平台补录性能项的模板，旧库一条都没录过；要补录时让助手按它建类型。'};
const ELSEWHERE = {'树脂.研发路线': ['课题', '#/projects'], '树脂.步骤模板': ['合成 · 实验设计', '#/synthesis'], '约定口径': ['知识 · 约定口径', '#/knowledge/约定口径'], 'GPC.报告模板': ['GPC 报告', '#/testing/gpc']};

// 旧程序里的英文键名 → 给人看的名字（存的还是原来的键，程序照常读）
const LABELS = {NIR: '近红外', UV: '紫外', a500: 'A500', absolute_max: '绝对上限', aliases: '别名', allow_extrapolation: '允许外推', anomaly_fraction: '异常占比',
  anomaly_min_delta_k: '异常最小 Δk', anomaly_min_delta_n: '异常最小 Δn', anomaly_min_group: '异常最少同组数', anomaly_min_points: '异常最少点数', anomaly_z_threshold: '异常 z 阈值',
  average_row: '平均行', bake_temp_c: '烘烤温度 °C', bake_time_s: '烘烤时间 s', bands: '波段', baseline: '基线范围 nm', basis: '基准', block_merge_fraction: '块合并比例',
  schema: '格式', version: '版本', factors: '因子', block_systematic_fraction: '块系统偏差比例', ceiling: '饱和上限', code: '代号', code_rules: '编号规则', columns: '列', components: '组分', conditions: '条件', copied_columns: '复制的列',
  custom_end_nm: '自定终点 nm', custom_start_nm: '自定起点 nm', dE: 'ΔE', dashboard_group_count: '看板组数', dashboard_groups: '看板分组', dashboard_max_samples: '看板最多样品',
  dashboard_repeatability_summary_groups: '看板重复性汇总组', dashboard_sigma_groups: '看板 σ 组', dashboard_sources: '看板来源', dashboard_summary_sources: '看板汇总来源',
  decimals: '小数位', default_group_by: '默认分组', diluentDensity: '稀释剂密度 g/mL', display: '显示', dissolution_markers: '溶解标记', duplicate_conflict: '重复冲突', enable_smoothing: '平滑',
  enabled: '启用', equal_tolerance: '视为相等的容差', exclusion_reasons: '剔除原因', expected_count: '应有读数', export_dpi: '导出 DPI', export_focus_report: '导出重点报告',
  export_formats: '导出格式', extraWavelengths: '另看的波长 nm', fields: '字段', fit: '峰拟合', fitH: '拟合高度', flag_empty_cell: '标出空格', flag_missing_row: '标出缺行',
  flag_text_numbers: '标出写成文字的数', from: '从', fwhm: '半高宽 nm', grid_mode: '波长网格', grid_step_nm: '网格步长 nm', high_cv_percent: 'CV 偏高 %', id: '编号',
  ignore_zero_pairs: '忽略零值对', intercept: '截距', interpolation_method: '插值方法', iqr_multiplier: 'IQR 倍数', jump_multiplier: '跳变倍数', jump_window: '跳变窗口',
  k_193_absolute: 'k193 允许差', k_248_absolute: 'k248 允许差', k_633_absolute: 'k633 允许差', k_zero_threshold: 'k 视为 0 的阈值', key: '因子', kind: '类', kinds: '种类',
  kinetics: '动力学', label: '名称', large_gap_multiplier: '大间隔倍数', mad_multiplier: 'MAD 倍数', mad_z_threshold: 'MAD z 阈值', manual_mappings: '人工对应', material: '物料',
  max_features_per_signal: '每条谱最多特征', method: '方法', min_absolute: '最小绝对差', min_absolute_relative_percent: '最小相对差 %', min_readings: '最少读数',
  min_relative_percent: '最小相对 %', minimum_count: '最少个数', mode: '模式', model: '模型', mse: 'MSE', mse_maximum: 'MSE 上限', mse_warning: 'MSE 提醒线',
  n_193_absolute: 'n193 允许差', n_248_absolute: 'n248 允许差', n_633_absolute: 'n633 允许差', name: '名称', nm: '波长 nm', note: '说明', optical_outliers: '光学异常点',
  outliers: '异常点', pattern: '匹配规则（正则）', peakWindow: '峰窗口 nm', peak_prominence_fraction: '峰显著度比例', solva_dashed: 'SolvA 用虚线', plot_grid: '图网格',
  plot_line_mode: '线型', plot_line_width: '线宽', plot_palette: '配色', plot_series_styles: '系列样式', plot_size: '图尺寸', plot_tick_count: '刻度数',
  post_bake_temp_c: '后烘温度 °C', post_bake_time_s: '后烘时间 s', predictT: '预测温度', quality_rules: '质量规则', range: '范围', readings: '读数',
  reference_match_fields: '参比匹配字段', reference_samples: '参比样品', relative_min_readings: '相对比较最少读数', relative_to_block_median: '相对块中位数',
  retention_percent_pp: '保留率允许差（百分点）', roomAfterDay1: '第 1 天后算室温', rows: '行', series: '系列', series_id: '系列', series_per_chart: '每张图系列数', slope: '斜率',
  smoothing_window: '平滑窗口', status: '状态', target_wavelengths_nm: '目标波长 nm', temps: '温度', thickness: '膜厚', thickness_after_relative_percent: '处理后膜厚允许差 %',
  thickness_before_relative_percent: '处理前膜厚允许差 %', thickness_cv_maximum: '膜厚 CV 上限 %', thickness_loss_percent_pp: '损失率允许差（百分点）', thickness_unit: '膜厚单位',
  thresholds: '阈值', to: '换成', token: '写法', tolerance: '容差', treatment_time: '处理时间', treatments: '处理方式', trendMetric: '趋势指标', type: '类型',
  unspecified_unit_assumption: '没写单位时按', unit: '单位', value: '值', value_tolerance: '数值容差', values: '取值', volumeFactor: '稀释体积倍数', wavelength: '波长 nm', words: '写法'};
const label = (key) => LABELS[key] || key;
// 迁移时写的说明里带着旧程序的文件名，给人看时去掉
const plain = (text) => String(text || '').replace(/[（(][^）)]*(?:\.py|\.js|\/)[^）)]*[）)]/g, '').trim();

const scalar = (value) => value === null || ['string', 'number', 'boolean'].includes(typeof value);
const flatRow = (item) => item && typeof item === 'object' && !Array.isArray(item) && Object.values(item).every(scalar);
const parse = (text, old) => typeof old === 'number' && text.trim() !== '' && isFinite(Number(text)) ? Number(text) : text;

// 一个值的编辑器：按它的样子选——一个数或一段字、一张表（每行一样的字段）、几项键值、更深的结构就分小节
function editor(value, change, choices = null) {
  if (scalar(value)) {
    if (choices) return h('select.rule-input', {onchange: (event) => change(event.target.value)}, choices.map((item) => h('option', {selected: item === value}, item)));
    if (typeof value === 'boolean') return h('input', {type: 'checkbox', checked: value, onchange: (event) => change(event.target.checked)});
    return h('input.rule-input', {type: 'text', value: value ?? '', oninput: (event) => change(parse(event.target.value, value))});
  }
  if (Array.isArray(value) && value.every(scalar)) {
    return h('textarea.rule-lines', {rows: Math.max(1, Math.min(8, value.length)), title: '一行一条', oninput: (event) => change(event.target.value.split('\n').map((line) => line.trim()).filter(Boolean))}, value.join('\n'));
  }
  if (Array.isArray(value) && value.every(flatRow)) {
    const columns = [...new Set(value.flatMap((item) => Object.keys(item)))];
    const body = h('tbody');
    const draw = () => body.replaceChildren(...value.map((item, index) => h('tr', {},
      columns.map((key) => h('td', {}, h('input.rule-cell', {value: item[key] ?? '', oninput: (event) => { item[key] = parse(event.target.value, item[key]); change(value); }}))),
      h('td.rule-x', {}, h('button.btn.ghost.small', {type: 'button', title: '删掉这一行', onclick: () => { value.splice(index, 1); draw(); change(value); }}, '×')))));
    draw();
    return h('div.rule-table', {}, h('div.tablewrap.flat', {}, h('table.data.compact', {}, h('thead', {}, h('tr', {}, columns.map((key) => h('th', {}, label(key))), h('th'))), body)),
      h('button.btn.ghost.small', {type: 'button', onclick: () => { value.push(Object.fromEntries(columns.map((key) => [key, '']))); draw(); change(value); }}, '+ 加一行'));
  }
  if (!Array.isArray(value) && Object.values(value).every(scalar)) {
    return h('dl.rule-kv', {}, Object.entries(value).map(([key, item]) => [h('dt', {}, label(key)), h('dd', {}, editor(item, (next) => { value[key] = next; change(value); }))]));
  }
  if (!Array.isArray(value)) {
    return h('div.rule-nest', {}, Object.entries(value).map(([key, item]) => h('div.rule-sub', {}, h('div.rule-sub-head', {}, label(key)), editor(item, (next) => { value[key] = next; change(value); }))));
  }
  // 每行结构不一样的列表：直接改 JSON（少见，例如因子规则）
  const area = h('textarea.code.rule-json', {rows: 14, oninput: () => { try { change(JSON.parse(area.value)); area.classList.remove('bad'); } catch (error) { area.classList.add('bad'); } }}, JSON.stringify(value, null, 1));
  return area;
}

function card(library, item) {
  const saved = h('span.rule-saved');
  let timer;
  const change = (next) => {
    clearTimeout(timer);
    saved.textContent = '…';
    timer = setTimeout(async () => { await api(`/lib/${encodeURIComponent(library)}/settings/${encodeURIComponent(item.key)}`, 'PUT', {value: next}); saved.textContent = '已存'; }, 600);
  };
  const [area, name] = item.key.split('.');
  return h('section.rule-card', {id: 'rule-' + item.key},
    h('header.rule-head', {}, h('span.rule-area', {}, area), h('h3', {}, name || item.key), saved, h('span.quiet.small', {}, item.updated_at ? '改于 ' + item.updated_at.slice(0, 16) : '')),
    item.about ? h('p.rule-about', {}, plain(item.about)) : null,
    editor(structuredClone(item.value), change, CHOICES[item.key]));
}

export async function businessRules(state) {
  const library = state.library;
  const {settings} = await api(`/lib/${encodeURIComponent(library)}/settings`);
  const byKey = Object.fromEntries(settings.map((item) => [item.key, item]));
  const fixed = Object.keys(FIXED).filter((key) => byKey[key]);
  const toc = h('nav.rule-toc', {}, GROUPS.map(([title]) => h('a', {href: '#', onclick: (event) => { event.preventDefault(); document.getElementById('rules-' + title)?.scrollIntoView({behavior: 'smooth'}); }}, title)),
    h('a', {href: '#', onclick: (event) => { event.preventDefault(); document.getElementById('rules-fixed')?.scrollIntoView({behavior: 'smooth'}); }}, '没有别的做法的（说明）'));
  return h('div.rules-page', {},
    h('p.quiet', {}, `这些是「${library}」的业务规矩，原来写死在旧平台和各个模块里，现在都能改。改了自动存，下一次导入、数据体检、计算就按新的走；树脂的规矩改了，树脂计算核心会重新读一遍。`),
    toc,
    GROUPS.map(([title, about, keys]) => h('div.rule-group', {id: 'rules-' + title}, h('h2', {}, title, h('small', {}, about)),
      keys.filter((key) => byKey[key]).map((key) => card(library, byKey[key])))),
    h('div.rule-group', {id: 'rules-fixed'}, h('h2', {}, '没有别的做法的（说明）', h('small', {}, '这几条现在没有别的做法可选，写明为什么、要改去哪里。')),
      h('div.rule-fixed', {}, fixed.map((key) => h('div', {}, h('b', {}, key), h('span', {}, FIXED[key]))))),
    h('div.rule-group', {}, h('h2', {}, '在别的页面改'), h('div.rule-fixed', {}, Object.entries(ELSEWHERE).filter(([key]) => byKey[key]).map(([key, [label, href]]) =>
      h('div', {}, h('b', {}, key), h('a', {href}, label))))));
}
