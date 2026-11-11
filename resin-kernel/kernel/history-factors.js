// Read-only adapters for recorded historical evidence. Raw summaries and plans
// remain archives; these functions never rewrite them or invent missing values.
const rows = value => Array.isArray(value) ? value : [];
const copy = value => value == null ? value : structuredClone(value);
const present = value => value != null && value !== '';
const numeric = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
const id = suffix => 'resin.factor.' + suffix;
export const historyBranchMatches = (left, right) => present(left) && present(right) && (left === right || String(left).endsWith('-' + right) || String(right).endsWith('-' + left) || /^[A-Z]$/.test(String(left)) && String(right).endsWith(left) || /^[A-Z]$/.test(String(right)) && String(left).endsWith(right));

/** Optional refs is the unchanged rawSummaryRefs dictionary. experimentId is
 * the original archive/branch id, not the merged mother id. */
export function historicalSummaryRecords(experiment = {}, design = {}, archives = []) {
  const originals = [experiment, ...rows(archives)];
  const records = [...rows(design.historicalSummary), ...rows(experiment.historicalSummary), ...originals.filter(e => e.rawSummary).map(e => ({experimentId:e.id, summary:e.rawSummary, refs:e.rawSummaryRefs, summaryRow:e.summaryRow, issues:e.issues}))];
  const byId = new Map();
  for (const record of records) {
    if (!record?.summary || typeof record.summary !== 'object') continue;
    const experimentId = record.experimentId ?? experiment.id ?? design.source?.experimentId ?? design.code;
    const original = originals.find(e => e.id === experimentId);
    const prior = byId.get(experimentId);
    // A merged mother keeps one branch's rawSummary. Its real archive id is
    // written in the source row and must not create another physical branch.
    const sourceId = record.summary['实验/样品'] ?? experimentId;
    byId.set(sourceId, {...prior, ...record, experimentId:sourceId, refs:record.refs ?? original?.rawSummaryRefs ?? prior?.refs, issues:[...new Set([...rows(prior?.issues),...rows(record.issues??original?.issues)])]});
  }
  return [...byId.values()];
}

function sourceFor(record, key) {
  return {experimentId:record.experimentId, path:'rawSummary[' + JSON.stringify(key) + ']', ...copy(record.refs?.[key] ?? {}), ...record.summaryRow != null ? {summaryRow:record.summaryRow} : {}};
}
function recorded(record, key, value, extra = {}) {
  return {value, state:'set', origin:'historical_summary', path:'rawSummary[' + JSON.stringify(key) + ']', source:sourceFor(record,key), sourceValue:copy(record.summary[key]), ...extra};
}

/** Only a source statement about the bulk process establishes termination.
 * Mentioning TEA in a GPC preparation or an added reagent is not sufficient. */
export function recordedTermination(text) {
  if (typeof text !== 'string' || !text.trim()) return null;
  const bulk = text.split(/[；;。\n]/).filter(part => !/GPC|分析(?:小?样|样品)|取样|配样|即时终止/i.test(part)).join('；');
  if (!bulk) return null;
  if (/不中和|未中和|不加(?:入)?\s*(?:TEA|DMAP|BaseQ|三乙胺|吡啶|碱)|无\s*(?:TEA|DMAP|BaseQ)|不加.*终止/i.test(bulk)) return {value:'无（不中和）'};
  for (const [pattern,value] of [[/DMAP|二甲氨基吡啶/i,'DMAP'],[/BaseQ/i,'BaseQ'],[/TEA|三乙胺/i,'TEA'],[/吡啶|pyridine/i,'pyridine']]) if (pattern.test(bulk)) return {value};
  if (/氨水/.test(bulk)) return {value:'氨水', ambiguity:true, reason:'原文记录氨水参与沉降；保留试剂记录，未推定独立中和操作'};
  return null;
}

function summarySolvents(text) {
  if (typeof text !== 'string') return null;
  const found = [[/SolvA/i,'SolvA'],[/SolvC(?!A)/i,'SolvC'],[/二甲苯|xylene/i,'xylene'],[/MIBK/i,'MIBK'],[/二氧六环|dioxane/i,'dioxane'],[/THF|四氢呋喃/i,'THF']].map(([pattern,material]) => ({material,index:text.search(pattern)})).filter(value => value.index>=0).sort((a,b)=>a.index-b.index).map(value=>value.material);
  return found.length ? found.join(' + ') : null;
}

function recordFactors(record) {
  const out = {}, s = record.summary;
  for (const [suffix,key,scale] of [['monoA_monoB_ratio','总单体A/MonB (mol/mol)',1],['catalyst_molpct','酸/MonB (mol%)',100],['reaction_solids','名义固含 (w/w)',1],['reaction_temperature','反应设温 (°C)',1],['reaction_hours','反应时间 (h)',1]]) {
    if (numeric(s[key]) == null) continue;
    const extra = suffix === 'reaction_hours' ? {basis:'source-recorded-reaction-time'} : suffix === 'reaction_solids' ? {basis:'source-recorded-nominal-solids'} : {};
    const notes = [s['备注'], ...rows(record.issues)].filter(Boolean).join('；');
    if (suffix === 'monoA_monoB_ratio' && /摩尔比.*(?:待核|不一致)|MonA.*(?:不一致|矛盾)/.test(notes) || suffix === 'reaction_hours' && /(?:时间|时长|\d\s*h).*(?:冲突|未注明|是否|矛盾)/.test(notes)) Object.assign(extra,{ambiguity:true,reason:notes});
    out[id(suffix)] = recorded(record,key,s[key]*scale,extra);
  }
  const solvents = summarySolvents(s['溶剂及用量']);
  if (solvents) out[id('main_solvent')] = recorded(record,'溶剂及用量',solvents);
  const termination = recordedTermination(s['终止 / 稀释 / 沉降']);
  if (termination) out[id('termination')] = recorded(record,'终止 / 稀释 / 沉降',termination.value,termination);
  return out;
}

export function historicalFactorEvidence(experiment = {}, design = {}, branch, archives = []) {
  let records = historicalSummaryRecords(experiment,design,archives);
  if (branch) records = records.filter(record => historyBranchMatches(record.experimentId,branch));
  const factors = records.map(recordFactors), result = {};
  for (const key of new Set(factors.flatMap(value => Object.keys(value)))) {
    const evidence = factors.map(value => value[key]).filter(Boolean);
    const values = [...new Set(evidence.map(value => value.value))];
    result[key] = values.length === 1 ? {...copy(evidence[0]), evidence:copy(evidence), ...evidence.some(e => e.ambiguity) ? {ambiguity:true, reason:evidence.filter(e=>e.reason).map(e=>e.reason).join('；')} : {}}
      : {value:null,state:'conflict',origin:'historical_summary',reason:'原始分支的记录值不同，请按树脂批核对',values,evidence:copy(evidence),source:evidence.map(e=>copy(e.source))};
  }
  return result;
}

/** Source plans provide timing semantics absent from many old workbooks. The
 * summary's total time is deliberately never copied into holdHours. */
export function historicalTiming(experiment = {}, design = {}, branch) {
  const branchPlan = branch && Object.entries(experiment.branchPlans ?? {}).find(([key]) => historyBranchMatches(key,branch))?.[1];
  const plan = branchPlan ?? experiment.sourcePlanBaseline ?? experiment.plan ?? design.sourcePlanBaseline ?? {};
  return Object.fromEntries(['holdHours','feedHours','timeBasis'].filter(key => present(plan[key])).map(key => [key,{value:copy(plan[key]),origin:'historical_plan',path:(branchPlan?'branchPlans/'+branch:experiment.sourcePlanBaseline?'sourcePlanBaseline':'plan')+'.'+key,source:copy(plan.sourceSop ?? design.source ?? null)}]));
}

export function historicalDryMass(experiment = {}, design = {}, branch, archives = []) {
  let records = historicalSummaryRecords(experiment,design,archives);
  if (branch) records = records.filter(record => historyBranchMatches(record.experimentId,branch));
  const readings = records.filter(record => numeric(record.summary['干品 (g)']) != null);
  // With multiple archived physical branches every branch must have a mass.
  if (!readings.length || readings.length !== records.length) return null;
  const value = readings.reduce((total,record) => total + record.summary['干品 (g)'],0);
  return {value,unit:'g',state:'measured',source:readings.map(record=>sourceFor(record,'干品 (g)')),basis:readings.length>1?'sum-of-recorded-branches':'recorded-product-mass'};
}
