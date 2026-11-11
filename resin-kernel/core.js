// Pure calculation and experimental design functions. All molecular weights and purities come from the material library.
import { calcGenericRecipe, validateGenericPlan } from './generic-recipe.js';
import { calcProcessAdditions } from './process-additions.js';
import { materialPurityFraction } from './kernel/materials.js';
export { calcGenericRecipe } from './generic-recipe.js';
const EPS = 1e-8;
const number = value => value === '' || value == null || !Number.isFinite(Number(value)) ? null : Number(value);
const clone = value => JSON.parse(JSON.stringify(value));
const issue = (severity, code, path, message) => ({ severity, code, path, message });
const sum = values => values.reduce((total, value) => total + value, 0);

export function validatePlan(plan = {}) {
  if (plan.calculationProfile === 'generic-mass') return validateGenericPlan(plan);
  const issues = [];
  const required = (key, lower, upper = Infinity, strict = false) => {
    const value = number(plan[key]);
    if (value == null || (strict ? value <= lower : value < lower) || value > upper) issues.push(issue('error', 'invalid-' + key, key, key + ' 数值无效：应' + (strict ? '大于 ' : '不小于 ') + lower + (upper < Infinity ? ' 且不大于 ' + upper : '') + '。'));
  };
  required('scale', 0, Infinity, true);
  required('ratio', 0, Infinity, true);
  required('catalystMolPct', 0);
  required('solidPct', 0, 100, true);
  if (!['mmol', 'g', 'monomer-g', 'monoB-g'].includes(plan.scaleBasis)) issues.push(issue('error', 'scale-basis', 'scaleBasis', '请明确规模口径：mmol 为单体B物质的量，g 为单体合计称量质量，monoB-g 为单体B称量质量。'));
  if (!['monomers', 'nonvolatile', 'monomers+catalyst'].includes(plan.solidBasis)) issues.push(issue('error', 'solid-basis', 'solidBasis', '请明确固含分子是否包含催化剂；不自动假定历史固含口径。'));
  if (!plan.monoBId) issues.push(issue('error', 'missing-monoB', 'monoBId', '请选择单体B原料。'));
  if (number(plan.catalystMolPct) > 0 && !plan.catalystId) issues.push(issue('error', 'missing-catalyst', 'catalystId', '请选择催化剂。'));
  const checkFractions = (key, requiredList) => {
    const items = plan[key];
    if (!Array.isArray(items) || !items.length) {
      if (requiredList) issues.push(issue('error', 'missing-' + key, key, '请至少选择一种' + (key === 'monoAs' ? '单体A原料' : '主溶剂') + '。'));
      return;
    }
    items.forEach((item, index) => {
      if (!item.materialId) issues.push(issue('error', 'missing-material', key + '.' + index, '物料未指定。'));
      const fraction = number(item.fraction);
      if (fraction == null || fraction < 0) issues.push(issue('error', 'invalid-fraction', key + '.' + index + '.fraction', '份额必须为非负数。'));
    });
    if (items.every(item => number(item.fraction) != null) && Math.abs(sum(items.map(item => Number(item.fraction))) - 1) > 1e-6) issues.push(issue('error', 'fraction-sum', key, (key === 'monoAs' ? '单体A摩尔份额' : '主溶剂质量份额') + '合计应为 1。'));
  };
  checkFractions('monoAs', true);
  checkFractions('solvents', false);
  if (Array.isArray(plan.feedSolvents)) {
    plan.feedSolvents.forEach((entry, index) => {
      if (!entry.materialId) issues.push(issue('error', 'feed-solvent-material', 'feedSolvents.' + index + '.materialId', '请指定固定载料溶剂物料。'));
      if (number(entry.mass) == null || Number(entry.mass) < 0) issues.push(issue('error', 'feed-solvent-mass', 'feedSolvents.' + index + '.mass', '固定载料溶剂质量必须为非负数。'));
    });
  } else if (plan.feedSolventMass != null && (number(plan.feedSolventMass) == null || Number(plan.feedSolventMass) < 0)) issues.push(issue('error', 'feed-solvent-mass', 'feedSolventMass', '固定载料溶剂质量必须为非负数。'));
  for (const key of ['holdHours', 'feedHours']) if (plan[key] != null && (number(plan[key]) == null || Number(plan[key]) < 0)) issues.push(issue('error', 'invalid-' + key, key, '时间必须为非负数。'));
  if (plan.quenchAcidFraction != null && (number(plan.quenchAcidFraction) == null || Number(plan.quenchAcidFraction) < 0 || Number(plan.quenchAcidFraction) > 1)) issues.push(issue('error', 'invalid-quench-acid-fraction', 'quenchAcidFraction', '名义终止酸份额必须在 0–1 之间；与实际反应液分样比例独立。'));
  if (plan.temp != null && number(plan.temp) == null) issues.push(issue('error', 'invalid-temp', 'temp', '温度必须为数值。'));
  if (plan.timeBasis === 'total-from-feed-start' && number(plan.holdHours) != null && number(plan.feedHours) != null && Number(plan.holdHours) < Number(plan.feedHours)) issues.push(issue('error', 'negative-post-feed-hold', 'holdHours', '从滴加开始计时的总时长不能短于滴加时长。'));
  if (Array.isArray(plan.steps)) {
    const ids = plan.steps.map(step => step.id).filter(Boolean);
    if (new Set(ids).size !== ids.length) issues.push(issue('error', 'duplicate-step-id', 'steps', '工艺阶段 ID 重复，无法可靠关联样品与记录。'));
  }
  return { valid: !issues.some(item => item.severity === 'error'), issues };
}

export function calcRecipe(plan = {}, materials = []) {
  const base = calcBaseRecipe(plan, materials);
  const additions = calcProcessAdditions(plan, base, materials);
  if (!additions.rows.length && !additions.issues.length) return base;
  const issues = [...base.issues, ...additions.issues];
  return { ...base, rows: [...base.rows, ...additions.rows], processAdditions: additions,
    workupFeedMass: base.totalMass == null || additions.motherMass == null ? null : base.totalMass + additions.motherMass,
    valid: !issues.some(issue => issue.severity === 'error'), issues };
}
function calcBaseRecipe(plan = {}, materials = []) {
  if (plan.calculationProfile === 'generic-mass') return calcGenericRecipe(plan, materials);
  const validation = validatePlan(plan);
  const issues = [...validation.issues];
  const library = Array.isArray(materials) ? new Map(materials.map(item => [item.id, item])) : new Map(Object.entries(materials));
  const rows = [];
  const getMaterial = (id, path, needsChemistry = true) => {
    const original = library.get(id);
    if (!original) { issues.push(issue('error', 'unknown-material', path, '物料 ' + id + ' 不在物料库中。')); return null; }
    const normalizedPurity=materialPurityFraction(original);
    const material = { ...original, purity:normalizedPurity==null?null:normalizedPurity*100, purityBasis: 'material-library' };
    if (needsChemistry && plan.materialPurities?.[id] != null && plan.materialPurities[id] !== '') {
      material.purity = plan.materialPurities[id];
      material.purityBasis = 'experiment-override';
    }
    // A missing purity is calculated as nominal 100% with a warning; only the explicit "strict" basis refuses to calculate.
    if (needsChemistry && number(material.purity) == null && plan.calculationPurityBasis !== 'strict') {
      material.purity = 100;
      material.purityBasis = plan.calculationPurityBasis === 'nominal-100' ? 'explicit-nominal-100' : 'default-nominal-100';
      issues.push(issue('warning', 'nominal-purity-assumption', path, material.name + ' 未填纯度，按名义 100% 计算；不是实测纯度。'));
    }
    if (needsChemistry && (number(material.mw) == null || Number(material.mw) <= 0)) issues.push(issue('error', 'missing-mw', path, material.name + ' 分子量缺失或无效；不能计算称量。'));
    if (needsChemistry && (number(material.purity) == null || Number(material.purity) <= 0 || Number(material.purity) > 100)) issues.push(issue('error', 'missing-purity', path, material.name + ' 纯度缺失或无效；请填入有依据的百分数。'));
    return material;
  };
  const usable = material => material && number(material.mw) > 0 && number(material.purity) > 0 && number(material.purity) <= 100;
  const massPerMmol = material => usable(material) ? Number(material.mw) / 1000 / (Number(material.purity) / 100) : null;
  const addRow = (role, material, mass, mmol, eq, extra = {}) => {
    const row = { role, materialId: material?.id ?? extra.materialId ?? null, name: material?.name ?? extra.name ?? '待指定物料', mass, mmol, eq, purityPct: number(material?.purity), purityBasis: material?.purityBasis ?? null, ...extra };
    if (mass != null && number(material?.density) > 0) row.volumeMl = mass / Number(material.density);
    rows.push(row);
    return row;
  };
  const monoB = getMaterial(plan.monoBId, 'monoBId');
  const monoAs = (plan.monoAs || []).map((entry, index) => ({ ...entry, material: getMaterial(entry.materialId, 'monoAs.' + index) }));
  const catalystPct = number(plan.catalystMolPct);
  const catalyst = catalystPct > 0 ? getMaterial(plan.catalystId, 'catalystId') : null;
  const ratio = number(plan.ratio);
  const scale = number(plan.scale);
  let monoBMmol = null;
  const chemistryValid = usable(monoB) && monoAs.length > 0 && monoAs.every(entry => usable(entry.material) && number(entry.fraction) != null) && ratio > 0 && scale > 0;
  if (chemistryValid) {
    const allMonomerMassPerMmol = massPerMmol(monoB) + ratio * sum(monoAs.map(entry => Number(entry.fraction) * massPerMmol(entry.material)));
    if (plan.scaleBasis === 'mmol') monoBMmol = scale;
    if (['g', 'monomer-g'].includes(plan.scaleBasis)) monoBMmol = scale / allMonomerMassPerMmol;
    if (plan.scaleBasis === 'monoB-g') monoBMmol = scale / massPerMmol(monoB);
  }
  addRow('monoB', monoB, monoBMmol == null ? null : monoBMmol * massPerMmol(monoB), monoBMmol, 1);
  monoAs.forEach(entry => {
    const mmol = monoBMmol == null ? null : monoBMmol * ratio * Number(entry.fraction);
    addRow('monoA', entry.material, mmol == null ? null : mmol * massPerMmol(entry.material), mmol, ratio == null ? null : ratio * Number(entry.fraction));
  });
  const catalystMmol = monoBMmol == null || catalystPct == null ? null : monoBMmol * catalystPct / 100;
  const catalystMass = catalystPct === 0 ? 0 : catalystMmol == null || !usable(catalyst) ? null : catalystMmol * massPerMmol(catalyst);
  if (catalystPct > 0) addRow('catalyst', catalyst, catalystMass, catalystMmol, catalystPct / 100);
  const monomerRows = rows.filter(row => ['monoB', 'monoA'].includes(row.role));
  const monomerMass = monomerRows.every(row => row.mass != null) ? sum(monomerRows.map(row => row.mass)) : null;
  const nonvolatileMass = monomerMass == null || catalystMass == null ? null : monomerMass + catalystMass;
  const solidNumeratorMass = plan.solidBasis === 'monomers' ? monomerMass : ['nonvolatile', 'monomers+catalyst'].includes(plan.solidBasis) ? nonvolatileMass : null;
  const fraction = number(plan.solidPct) == null ? null : Number(plan.solidPct) / 100;
  let solventMass = solidNumeratorMass == null || nonvolatileMass == null || !(fraction > 0) ? null : solidNumeratorMass / fraction - nonvolatileMass;
  const feedEntries = Array.isArray(plan.feedSolvents) ? plan.feedSolvents : null;
  const feedSolventMass = feedEntries ? feedEntries.every(entry => number(entry.mass) != null && Number(entry.mass) >= 0) ? sum(feedEntries.map(entry => Number(entry.mass))) : null : plan.feedSolventMass == null ? 0 : number(plan.feedSolventMass);
  let mainSolventMass = solventMass == null || feedSolventMass == null ? null : solventMass - feedSolventMass;
  if (solventMass != null && solventMass < -EPS) issues.push(issue('error', 'negative-solvent', 'solidPct', '该固含口径要求的总质量小于单体与催化剂质量，产生负溶剂量。'));
  if (mainSolventMass != null && mainSolventMass < -EPS) issues.push(issue('error', 'carrier-exceeds-solvent', 'feedSolventMass', '固定载料溶剂超过总反应溶剂额，剩余主溶剂为负；请降低载体量或固含。'));
  if (solventMass != null && Math.abs(solventMass) < EPS) solventMass = 0;
  if (mainSolventMass != null && Math.abs(mainSolventMass) < EPS) mainSolventMass = 0;
  if (mainSolventMass > EPS && !plan.solvents?.length) issues.push(issue('error', 'missing-solvents', 'solvents', '有待分配主溶剂质量，请指定溶剂及质量份额。'));
  if (feedEntries) {
    feedEntries.forEach((entry, index) => {
      const carrier = getMaterial(entry.materialId, 'feedSolvents.' + index + '.materialId', false);
      addRow('feed-solvent', carrier, number(entry.mass), null, null, { materialId: entry.materialId, name: carrier?.name || '待指定载料溶剂', carrierIndex: index + 1 });
    });
  } else if (feedSolventMass > 0) {
    const carrier = plan.feedSolventId ? getMaterial(plan.feedSolventId, 'feedSolventId', false) : null;
    addRow('feed-solvent', carrier, feedSolventMass, null, null, { materialId: plan.feedSolventId || null, name: carrier?.name || '固定载料溶剂（成分待指定）' });
    if (!plan.feedSolventId) issues.push(issue('warning', 'carrier-composition', 'feedSolventId', '固定载料溶剂成分未指定，质量已计入，但不能校核最终溶剂组成。'));
  }
  (plan.solvents || []).forEach((entry, index) => {
    const material = getMaterial(entry.materialId, 'solvents.' + index, false);
    const mass = mainSolventMass == null || mainSolventMass < 0 || number(entry.fraction) == null ? null : mainSolventMass * Number(entry.fraction);
    addRow('solvent', material, mass, null, null, { fraction: number(entry.fraction) });
  });
  const totalMass = nonvolatileMass == null || solventMass == null || solventMass < 0 ? null : nonvolatileMass + solventMass;
  const calculatedSolidPct = totalMass > 0 && solidNumeratorMass != null ? solidNumeratorMass / totalMass * 100 : null;
  // Neutralization is a separate addition after the reaction. Base equivalents refer to initially charged acid.
  const hasQuench = plan.quenchEnabled === true || ['neutralize', 'quench', 'base', 'TEA', 'DMAP'].includes(plan.termination) || (plan.termination && plan.termination === plan.quenchId);
  const quenchAcidFraction = plan.quenchAcidFraction == null ? 1 : number(plan.quenchAcidFraction);
  let quenchMmol = 0, quenchMass = 0, quenchSolutionMass = 0, quenchSolventMass = 0;
  if (hasQuench) {
    const base = getMaterial(plan.quenchId, 'quenchId');
    const equivalent = number(plan.quenchEq);
    const solutionPct = plan.quenchSolutionPct == null || plan.quenchSolutionPct === '' ? 100 : number(plan.quenchSolutionPct); // not stated = neat quench agent
    if (!(equivalent >= 0)) issues.push(issue('error', 'invalid-quench-eq', 'quenchEq', '中和当量应为相对投料酸的非负数。'));
    if (!(solutionPct > 0 && solutionPct <= 100)) issues.push(issue('error', 'invalid-quench-concentration', 'quenchSolutionPct', '终止剂溶液质量浓度应大于 0 且不大于 100%。'));
    quenchMmol = catalystMmol == null || equivalent == null || quenchAcidFraction == null || quenchAcidFraction < 0 || quenchAcidFraction > 1 ? null : catalystMmol * equivalent * quenchAcidFraction;
    quenchMass = quenchMmol == null || !usable(base) ? null : quenchMmol * massPerMmol(base);
    quenchSolutionMass = quenchMass == null || !(solutionPct > 0 && solutionPct <= 100) ? null : quenchMass / (solutionPct / 100);
    quenchSolventMass = quenchSolutionMass == null || quenchMass == null ? null : quenchSolutionMass - quenchMass;
    addRow('quench', base, quenchMass, quenchMmol, monoBMmol > 0 && quenchMmol != null ? quenchMmol / monoBMmol : null, { acidEq: equivalent });
    if (quenchSolventMass > 0) {
      const carrier = plan.quenchSolventId ? getMaterial(plan.quenchSolventId, 'quenchSolventId', false) : null;
      addRow('quench-solvent', carrier, quenchSolventMass, null, null, { materialId: plan.quenchSolventId || null, name: carrier?.name || '终止剂载体溶剂（成分待指定）' });
      if (!plan.quenchSolventId) issues.push(issue('warning', 'quench-carrier-composition', 'quenchSolventId', '终止剂载体溶剂成分未指定，不自动沿用主溶剂。'));
    }
  }
  const postQuenchTotalMass = totalMass == null || quenchSolutionMass == null ? null : totalMass + quenchSolutionMass;
  const postQuenchSolidPct = postQuenchTotalMass > 0 && solidNumeratorMass != null ? solidNumeratorMass / postQuenchTotalMass * 100 : null;
  const holdHours = number(plan.holdHours), feedHours = number(plan.feedHours);
  const postFeedHoldHours = plan.timeBasis === 'total-from-feed-start' ? holdHours == null || feedHours == null ? null : holdHours - feedHours : plan.timeBasis === 'after-feed' ? holdHours : null;
  const totalFromFeedStartHours = plan.timeBasis === 'total-from-feed-start' ? holdHours : plan.timeBasis === 'after-feed' && holdHours != null && feedHours != null ? holdHours + feedHours : null;
  return { valid: !issues.some(item => item.severity === 'error'), rows, monoBMmol, monoAMmol: monoBMmol == null || ratio == null ? null : monoBMmol * ratio, monomerMass, catalystMmol, catalystMass, nonvolatileMass, solidNumeratorMass, solventMass, feedSolventMass, mainSolventMass, totalMass, solidPct: calculatedSolidPct, hasQuench, quenchAcidFraction, quenchMmol, quenchMass, quenchSolutionMass, quenchSolventMass, postQuenchTotalMass, postQuenchSolventMass: solventMass == null || quenchSolventMass == null ? null : solventMass + quenchSolventMass, postQuenchSolidPct, postFeedHoldHours, totalFromFeedStartHours, issues,
    calculationBasis: { scale: plan.scaleBasis === 'mmol' ? '单体B物质的量 mmol' : plan.scaleBasis === 'monoB-g' ? '单体B称量质量 g' : '单体合计称量质量 g', ratio: '总单体A摩尔数 / 单体B摩尔数；单体A份额为摩尔份额', solid: plan.solidBasis === 'monomers' ? '单体称量质量 / 终止前全部反应投料质量' : '单体与催化剂称量质量 / 终止前全部反应投料质量', solvent: '主溶剂按扣除固定载料溶剂后的余额分配，份额为质量份额', quench: '碱当量相对投料催化酸×名义终止酸份额；该份额与实际分样占比独立，终止剂与载体在反应后独立增加', postQuenchSolid: '沿用终止前固含分子，除以终止后总投料质量；属于名义配料比例，不是实测非挥发物', time: plan.timeBasis === 'total-from-feed-start' ? 'holdHours 是从滴加开始的总时长，滴后时长为 holdHours-feedHours' : plan.timeBasis === 'after-feed' ? 'holdHours 是滴加结束后的单独保温时长' : '达温起计时；不假定升温或滴加与保温的重叠关系' }
  };
}

function hash32(text) { let value = 2166136261; for (const char of String(text)) { value ^= char.charCodeAt(0); value = Math.imul(value, 16777619); } return value >>> 0; }
function seededRandom(seed) { let state = hash32(seed); return () => { state = (Math.imul(1664525, state) + 1013904223) >>> 0; return state / 4294967296; }; }
function setPath(object, path, value) {
  const keys = String(path).replace(/\[(\d+)\]/g, '.$1').split('.');
  if (!keys.length || keys.some(key => !key || ['__proto__', 'prototype', 'constructor'].includes(key))) throw new Error('无效因子路径：' + path);
  let target = object;
  keys.slice(0, -1).forEach((key, index) => { if (target[key] == null) target[key] = /^\d+$/.test(keys[index + 1]) ? [] : {}; target = target[key]; });
  target[keys.at(-1)] = clone(value);
}

export function buildDOE(basePlan, factors = [], replicates = 1, options = {}) {
  const repeat = number(replicates);
  if (!(repeat >= 1 && Number.isInteger(repeat))) throw new Error('重复数必须为正整数。');
  if (!Array.isArray(factors)) throw new Error('因子必须为数组。');
  const paths = factors.map(factor => factor.path);
  if (new Set(paths).size !== paths.length) throw new Error('同一因子路径不能重复。');
  let combinations = 1;
  for (const factor of factors) {
    if (!factor.path || !Array.isArray(factor.levels) || !factor.levels.length) throw new Error('每个因子必须有路径和至少一个水平。');
    combinations *= factor.levels.length;
    if (combinations * repeat > 1000) throw new Error('全因子设计含重复最多 1000 个实验，请减少因子、水平或重复。');
  }
  if (combinations * repeat > 1000) throw new Error('全因子设计含重复最多 1000 个实验。');
  const treatments = [];
  const expand = (index, current, designFactors) => {
    if (index === factors.length) { treatments.push({ plan: current, designFactors }); return; }
    const factor = factors[index];
    factor.levels.forEach((level, levelIndex) => { const next = clone(current); setPath(next, factor.path, level); expand(index + 1, next, [...designFactors, { path: factor.path, label: factor.label || factor.path, value: clone(level), levelIndex: levelIndex + 1 }]); });
  };
  expand(0, clone(basePlan), []);
  const designId = options.designId || 'DOE-' + hash32(JSON.stringify({ basePlan, factors, replicates: repeat, seed: options.seed ?? 1 })).toString(36).toUpperCase();
  const runs = [];
  treatments.forEach((treatment, treatmentIndex) => {
    const check = validatePlan(treatment.plan);
    if (!check.valid) throw new Error('DOE 第 ' + (treatmentIndex + 1) + ' 个组合无效：' + check.issues.filter(item => item.severity === 'error').map(item => item.message).join(' '));
    for (let replicate = 1; replicate <= repeat; replicate += 1) {
      const designOrder = runs.length + 1;
      const id = designId + '-' + String(designOrder).padStart(3, '0');
      runs.push({ ...clone(treatment.plan), id, runId: id, designId, designOrder, treatmentOrder: treatmentIndex + 1, replicate, designFactors: clone(treatment.designFactors) });
    }
  });
  if (options.randomize) { const random = seededRandom(options.seed ?? 1); for (let index = runs.length - 1; index > 0; index -= 1) { const other = Math.floor(random() * (index + 1)); [runs[index], runs[other]] = [runs[other], runs[index]]; } }
  return runs.map((run, index) => ({ ...run, runOrder: index + 1 }));
}


// Workup quantities use masses throughout; the antisolvent basis is explicit.
export function calcWorkup(input = {}) {
  const issues = [];
  const checkMass = (value, path, required = true) => {
    const parsed = number(value);
    if (parsed == null || parsed < 0) {
      if (required) issues.push(issue('error', 'invalid-workup-' + path, path, path + ' 必须为非负质量或比例。'));
      return null;
    }
    return parsed;
  };
  let reactionPortionMass = null;
  if (input.reactionPortionMass != null && input.reactionPortionMass !== '') {
    reactionPortionMass = checkMass(input.reactionPortionMass, 'reactionPortionMass');
  } else {
    const sampleMass = checkMass(input.sampleMass, 'sampleMass');
    const branchFraction = number(input.branchFraction);
    if (branchFraction == null || branchFraction < 0 || branchFraction > 1) issues.push(issue('error', 'invalid-branch-fraction', 'branchFraction', '分样份额必须在 0–1 之间。'));
    if (sampleMass != null && branchFraction != null && branchFraction >= 0 && branchFraction <= 1) reactionPortionMass = sampleMass * branchFraction;
  }
  const dilutionMass = checkMass(input.dilutionMass, 'dilutionMass');
  const dilutedSolutionMass = reactionPortionMass == null || dilutionMass == null ? null : reactionPortionMass + dilutionMass;
  const antisolventRatio = checkMass(input.antisolventRatio, 'antisolventRatio');
  if (!['diluted-solution', 'reaction-portion'].includes(input.antisolventBasis)) issues.push(issue('error', 'unknown-antisolvent-basis', 'antisolventBasis', '请明确沉降剂质量比相对本路反应液还是稀释后溶液。'));
  const antisolventBasisMass = input.antisolventBasis === 'reaction-portion' ? reactionPortionMass : input.antisolventBasis === 'diluted-solution' ? dilutedSolutionMass : null;
  const antisolventMass = antisolventBasisMass == null || antisolventRatio == null ? null : antisolventBasisMass * antisolventRatio;
  const precipitationMixtureMass = dilutedSolutionMass == null || antisolventMass == null ? null : dilutedSolutionMass + antisolventMass;
  const washMassPerCycle = checkMass(input.washMassPerCycle, 'washMassPerCycle');
  const washCycles = number(input.washCycles);
  if (washCycles == null || washCycles < 0 || !Number.isInteger(washCycles)) issues.push(issue('error', 'invalid-wash-cycles', 'washCycles', '洗涤轮数必须为非负整数。'));
  const totalWashMass = washMassPerCycle == null || washCycles == null || washCycles < 0 || !Number.isInteger(washCycles) ? null : washMassPerCycle * washCycles;
  const workupAddedMass = dilutionMass == null || antisolventMass == null || totalWashMass == null ? null : dilutionMass + antisolventMass + totalWashMass;
  return {
    valid: !issues.some(item => item.severity === 'error'), reactionPortionMass, dilutionMass, dilutedSolutionMass, antisolventRatio, antisolventBasis: input.antisolventBasis, antisolventBasisMass, antisolventMass, precipitationMixtureMass, washMassPerCycle, washCycles, totalWashMass, workupAddedMass, issues,
    steps: [{type:'portion',mass:reactionPortionMass},{type:'dilution',mass:dilutionMass},{type:'antisolvent',mass:antisolventMass},{type:'wash',mass:totalWashMass,cycles:washCycles,massPerCycle:washMassPerCycle}],
    calculationBasis: { portion: 'reactionPortionMass 优先；否则 sampleMass（可分配反应液总质量）×branchFraction（0–1份额）', antisolvent: input.antisolventBasis === 'diluted-solution' ? '沉降剂质量 / 稀释后溶液质量' : '沉降剂质量 / 本路反应液质量', washing: '各轮洗涤溶剂质量相同；总量为每轮质量×轮数', output: '全部质量单位 g；不推定收率、滤饼持液或密度' }
  };
}
