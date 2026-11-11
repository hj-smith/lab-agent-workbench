// 树脂合成计算核心：常驻 node 小进程。stdin 一行一个 JSON 请求 → stdout 一行一个 JSON 回答。
// 计算全部用 kernel/ 里原样复制的树脂模块 2.11.0 代码（<本机目录>）；
// 这里只把请求接到原函数上，不改公式。kernel/hplc.js 是从界面文件 sample-workspace.js:297-347 挪进来的。
//
// 请求 {id, cmd, ...}；回答 {id, ok:true, result} 或 {id, ok:false, error}。
//   setup         {materials, modules, factors, preferences}    资料库（物料、步骤模板、因子规则、库偏好）；先发一次
//   evaluate      {design}                       投料（g、mmol、mL）、变量、时间表、温度程序
//   profile       {design}                       过程固含、体积、干基闭合（processProfile）+ 偏差（deviation）
//   yield         {design, experiment}           产率（yieldSummary）
//   factors       {design}                       批次因子（extractFactors）
//   hplc          {design}                       HPLC 含量（mg/g）和单体剩余 %
//   document      {design, records}              编译好的 SOP / 批记录文档（版面、公式），给预览用
//   export-xlsx   {design, records, path}        SOP 与批记录 xlsx（records=true 带实测）
//   export-docx   {design, path}                 SOP Word
//   import-record {design, experiment, path}     读回填好的批记录 xlsx → 新的设计 doc（actual 已合并）+ 预览
//   compile-procedure {design}                   工序（lab-procedure/3）改过以后重新编出 设计.steps 和 charges（原函数 compileProcedure）
import fs from 'node:fs/promises';
import vm from 'node:vm';
import readline from 'node:readline';

vm.runInThisContext(await fs.readFile(new URL('./vendor/jszip.min.js', import.meta.url), 'utf8'));
const k = await import('./kernel/index.js');
const {designState, processProfile, deviation} = await import('./kernel/design-analysis.js');
const {yieldSummary} = await import('./kernel/yield-summary.js');
const {extractFactors} = await import('./kernel/factors.js');
const {writeDocumentWord} = await import('./kernel/docx-write.js');
const {mergeProcedureSeeds} = await import('./kernel/procedure-library.js');
const {compileProcedure} = await import('./kernel/procedure.js');
const {hplcPoints, flaskModel, remaining} = await import('./kernel/hplc.js');
const {parseExpr, evaluateExpr, formatText, renderTemplate} = await import('./kernel/expr.js');
const {procedureCurve, procedureFlow} = await import('./kernel/ui/procedure-editor.js');
const prefs = await import('./library-preferences.js');
const seeds = JSON.parse(await fs.readFile(new URL('./procedure-templates.json', import.meta.url), 'utf8'));

let library = {materials: [], modules: [], registry: {version: 1, factors: []}};

// 和界面 store.library 一样：物料 + 步骤模板（并上程序自带的流程模板）+ 因子规则
function setup({materials = [], modules = [], factors, preferences = {}}) {
  library = {materials, modules: mergeProcedureSeeds(modules, seeds), registry: factors ?? {version: 1, factors: []}};
  prefs.useLibraryPreferences(preferences);
  return {materials: materials.length, modules: library.modules.length, factors: library.registry.factors?.length ?? 0,
    hplcMethods: prefs.hplcMethods().map(m => m.name)};
}

const plain = value => value instanceof Map ? Object.fromEntries(value) : value;

function evaluate({design}) {
  const state = designState(design, library), values = state.values, actual = design.actual ?? {};
  const charges = state.charges.map(c => ({
    id: c.id, name: c.name, materialId: c.materialId, role: c.role, unit: c.unit, mode: c.amount?.mode, amount: c.amount,
    g: values.get(c.keys.mass), mmol: values.get(c.keys.mmol), mL: values.get(c.keys.volume),
    actual: actual[`record-charges/${c.id}/实际值`] ?? null}));
  const vars = state.vars.map(v => ({key: v.key, label: v.label, unit: v.unit, expr: v.expr, role: v.role, value: values.get(v.key)}));
  return {charges, vars, schedule: k.scheduleDesign(design, library), temperature: k.tempProfile(design, library)};
}

function hplc({design}) {
  const methods = prefs.hplcMethods(), points = hplcPoints(design, methods), flask = flaskModel(design, library);
  const names = [...new Set(points.flatMap(p => Object.keys(p.conc)))];
  return {points, remaining: Object.fromEntries(names.map(name => [name, remaining(points, name, flask)])),
    removedG: flask?.removed ?? null, waterG: flask?.water ?? null};
}

// 和界面 kernel/ui/document-renderer.js:65 documentModel 一样地挑版本、编译
function documentModel(design, records) {
  const selected = records ? (design.actualRevisionUid ?? design.executions?.at(-1)?.revisionUid) : design.revisionUid;
  const frozen = design.frozenRevisions?.some(r => r.uid === selected);
  const plan = records || frozen ? k.executionDesign(design, selected) : design;
  if (records) { plan.records = structuredClone(design.records ?? plan.records); plan.recordFields = structuredClone(design.recordFields ?? plan.recordFields); }
  const snapshot = design.executions?.find(e => e.revisionUid === plan.revisionUid)?.library;
  return k.compile(plan, snapshot ? snapshot : library);
}

async function exportXlsx({design, records = false, path}) {
  const bytes = await k.writeWorkbook(documentModel(design, records));
  await fs.writeFile(path, bytes);
  return {path, bytes: bytes.length ?? bytes.byteLength};
}

async function exportDocx({design, path}) {
  const bytes = await writeDocumentWord(documentModel(design, false));
  await fs.writeFile(path, bytes);
  return {path, bytes: bytes.length ?? bytes.byteLength};
}

async function importRecord({design, experiment, path}) {
  const bytes = await fs.readFile(path);
  const imported = await k.readRecordedValues(bytes, {design, library, experiment});
  const values = Object.fromEntries(Object.entries(imported.values).filter(([, v]) => v !== null && v !== undefined && v !== ''));
  const next = k.applyDesignActual(design, values, {meta: imported.meta, flags: imported.flags,
    newRows: imported.newRows.filter(r => Object.keys(values).some(key => key.startsWith(r.block + '/' + r.id + '/')))});
  return {design: next, values, warnings: imported.warnings, flags: imported.flags, counts: imported.preview?.counts, meta: imported.meta};
}

// 纸面预览：用核心自己的公式引擎算文档里的格子（变量 [x] 取设计的值，@[记录键] 取已填的实测），和导出的 Excel 一样
function cells({design, formulas = []}) {
  const values = designState(design, library).values, actual = design.actual ?? {};
  const resolve = ref => ref.kind === 'cell' ? (actual[ref.key] ?? null) : (values.get(ref.key) ?? null);
  return formulas.map(({f, fmt, t}) => {
    try {
      if (t != null) return {value: null, text: renderTemplate(t, resolve)};     // 正文里的 {[反应内温]} °C 这类模板
      const value = evaluateExpr(parseExpr(f), resolve);
      let text = value == null ? '' : String(value);
      if (fmt && typeof value === 'number') { try { text = formatText(value, fmt, {excel: true}); } catch { text = ''; } }
      return {value, text};
    } catch (error) {
      return {value: null, text: '', error: String(error?.message ?? error)};
    }
  });
}

// 过程图：和旧版设计页、SOP 开头同一张（温度—时间曲线 + 投料和操作流程），见 kernel/ui/procedure-editor.js
function figure({design}) {
  const p = design.procedure;
  if (!p?.steps?.length) return {curve: '', flow: ''};
  const state = designState(design, library), start = p.initialRows?.length ? [{type: 'add', title: '反应前投料', rows: p.initialRows}] : [];
  return {curve: procedureCurve(design, library, state), flow: procedureFlow([...start, ...p.steps], library, {state, routes: p.routes, routeFrom: p.routeFrom})};
}

const commands = {
  setup, evaluate, hplc, cells, figure,
  profile: ({design}) => ({profile: processProfile(design, library), deviations: deviation(design, library)}),
  yield: ({design, experiment = {}}) => yieldSummary(experiment, design, library),
  factors: ({design}) => extractFactors(design, library.registry, library),
  document: ({design, records = false}) => documentModel(design, records),
  'export-xlsx': exportXlsx, 'export-docx': exportDocx, 'import-record': importRecord,
  'compile-procedure': ({design}) => compileProcedure(design, library),
};

const json = value => JSON.stringify(value, (key, v) => {
  v = plain(v);
  if (typeof v === 'number' && !Number.isFinite(v)) return Number.isNaN(v) ? null : (v > 0 ? 'Infinity' : '-Infinity');
  if (v instanceof Set) return [...v];
  return v;
});

const input = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
for await (const line of input) {
  if (!line.trim()) continue;
  let request = {};
  try {
    request = JSON.parse(line);
    const run = commands[request.cmd];
    if (!run) throw Error(`没有这个命令：${request.cmd}。可用：${Object.keys(commands).join('、')}`);
    process.stdout.write(json({id: request.id, ok: true, result: await run(request)}) + '\n');
  } catch (error) {
    process.stdout.write(json({id: request.id, ok: false, error: String(error?.message ?? error), stack: String(error?.stack ?? '').split('\n').slice(1, 4).join(' | ')}) + '\n');
  }
}
