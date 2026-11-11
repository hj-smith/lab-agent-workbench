import { materialPurityFraction } from './kernel/materials.js';
/**
 * Standalone, pure process dosing. Does not import the recipe engine or SOP builder.
 *
 * APIs:
 *   calcProcessAdditions(plan, baseCalc, materials)
 *     -> { rows, issues, motherMass, branchMasses, totalKnown }
 *   renderAdditionText(step, plan, baseCalc, materials) -> Chinese plain text
 *   ADDITIVE_H_MATERIAL -> a reference descriptor, not a chemistry recommendation
 *
 * plan.steps[].additions contain { id, materialId, amount, unit, reference,
 * referenceMmol?, referenceFraction?, solutionPct?, solventId?, purityCorrection? }.
 * Units: eq | mol% | g | mg | mmol | mL. Conditions are separately specified by
 * step.conditions.{tempC,hours}; temperature may be negative, hours may not.
 *
 * eq/mol% use INITIAL full-recipe mmol, multiplied ONLY by referenceFraction
 * (default 1). A branch's fraction, conversion, residual groups and earlier process
 * additions never change that basis. For material:<id>, all matching initial rows
 * must have known mmol; process rows are excluded. manual requires referenceMmol.
 * An omitted reference is allowed for fixed g/mg/mL/mmol; eq is then unknown.
 *
 * g/mg/mL specify TOTAL as-delivered mass/volume. eq/mol%/mmol specify ACTIVE solute
 * mmol. solutionPct is reagent mass / delivered mass, independent of reagent purity:
 *   addition.solutionPct -> library concentrationPct -> 100 (null/blank are missing).
 * purityCorrection defaults to false; if true, purity is
 *   plan.materialPurities[id] ?? material.purity ?? 100.
 * soluteMass includes reagent impurities; carrierMass is only the added carrier.
 * Thus mass = soluteMass + carrierMass, and active mass = soluteMass * purityFraction.
 * mL uses ONLY library density (g/mL), never addition.density or solvent density.
 * HE falls back to the exported descriptor only when the library has no HE entry.
 *
 * Numeric strings are accepted; blank/null concentration falls back as above. Other
 * blank/null/boolean/nonfinite/negative numeric inputs are unknown, never zero.
 * Missing MW prevents only molar conversion. Explicit delivered
 * mass remains known even if concentration/purity is invalid; unknown components
 * stay null and generate issues. Null means unknown, including zero * unknown.
 *
 * scope: absent/empty/mother/batch/mother-batch -> mother; otherwise it must uniquely
 * match workupBranches[].id/code/archiveCode/archive (string archive alias).
 * Ambiguous/unknown scopes have scope=null and are excluded from all mass totals.
 * branchMasses maps canonical branch IDs (fallback code/archive) to added mass.
 * motherMass/each branchMass are null if any assigned row has unknown mass.
 * totalKnown is the SUM OF KNOWN, uniquely assigned addition masses, not total recipe
 * mass or a completeness flag. Carrier is included once in the row, never a second row.
 * Rows carry notes as plain text. Issues use { severity, code, path, message }.
 * No input, library record or descriptor is modified. No DOM/UI exports.
 */
export const ADDITIVE_H_MATERIAL = Object.freeze({
  id: 'HE',
  name: 'HE · 添加剂H',
  kind: 'additive',
  mw: null,
  cas: '',
  source: '',
  purity: null,
  density: null,
});

const list = value => Array.isArray(value) ? value : [];
const numeric = value => {
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  if (typeof value === 'string' && !value.trim()) return null;
  return Number.isFinite(Number(value)) ? Number(value) : null;
};
const nonnegative = value => {
  const n = numeric(value);
  return n != null && n >= 0 ? n : null;
};
const identifier = value => typeof value === 'string' && value.trim() ? value : null;
const fmt = value => value == null ? '待核实' : String(Number(value.toPrecision(9)));
const UNITS = new Set(['eq', 'mol%', 'g', 'mg', 'mmol', 'mL']);
const MOTHER_SCOPES = new Set(['', 'mother', 'batch', 'mother-batch']);
const isProcessRow = row => row?.role === 'process-addition'
  || String(row?.stage ?? '').startsWith('process-')
  || String(row?.id ?? '').startsWith('step:');

function libraryOf(materials) {
  const library = materials instanceof Map ? new Map(materials)
    : Array.isArray(materials) ? new Map(materials.filter(Boolean).map(m => [m.id, m]))
      : new Map(Object.entries(materials ?? {}));
  if (!library.has('HE')) library.set('HE', ADDITIVE_H_MATERIAL);
  return library;
}

function branchesOf(plan) {
  return list(plan.workupBranches).filter(Boolean).map(branch => {
    const aliases = [...new Set([branch.id, branch.code, branch.archiveCode, branch.archive].map(identifier).filter(Boolean))];
    return { id: aliases[0], aliases, label: [branch.code ?? aliases[0], branch.name].filter(Boolean).join(' · ') };
  }).filter(branch => branch.id);
}

function scopeOf(step, branches) {
  const scope = step.branchScope ?? '';
  if (MOTHER_SCOPES.has(scope)) return { scope: 'mother', branchId: null, label: '母批' };
  const matches = branches.filter(branch => branch.aliases.includes(scope));
  return matches.length === 1
    ? { scope: matches[0].id, branchId: matches[0].id, label: '分支 ' + matches[0].label }
    : { scope: null, branchId: null, label: '未核实分支 ' + String(scope) };
}

function readConditions(step, report) {
  const conditions = step.conditions ?? {};
  const tempC = numeric(conditions.tempC), hours = nonnegative(conditions.hours);
  if (conditions.tempC !== undefined && tempC == null) report('error', 'invalid-temperature', 'conditions.tempC', '步骤温度必须是有限数值。');
  if (conditions.hours !== undefined && hours == null) report('error', 'invalid-hours', 'conditions.hours', '步骤时长必须是有限非负数。');
  return { tempC, hours };
}

function resolveReference(addition, baseCalc, library, report) {
  const fraction = addition.referenceFraction === undefined ? 1 : nonnegative(addition.referenceFraction);
  if (fraction == null) report('error', 'invalid-reference-fraction', 'referenceFraction', '参照份额必须是有限非负数；不自动采用分料比例。');
  const reference = addition.reference;
  const required = addition.unit === 'eq' || addition.unit === 'mol%';
  const severity = required ? 'error' : 'warning';
  let basis = null, label = '参照未指定';
  const named = { monoB: ['monoBMmol', '初始全配方单体B'], monoA: ['monoAMmol', '初始全配方单体A'], catalyst: ['catalystMmol', '初始全配方催化剂'] };
  if (Object.hasOwn(named, reference)) {
    const [key, name] = named[reference];
    basis = nonnegative(baseCalc[key]); label = name;
  } else if (reference === 'manual') {
    basis = nonnegative(addition.referenceMmol); label = '手动参照';
  } else if (typeof reference === 'string' && reference.startsWith('material:') && reference.length > 9) {
    const id = reference.slice(9);
    const matching = list(baseCalc.rows).filter(row => row?.materialId === id && !isProcessRow(row));
    label = '初始全配方物料 ' + (library.get(id)?.name ?? id);
    if (matching.length && matching.every(row => nonnegative(row.mmol) != null)) basis = matching.reduce((total, row) => total + nonnegative(row.mmol), 0);
    if (!Number.isFinite(basis)) basis = null;
  } else {
    if (required || (reference != null && reference !== '')) report(severity, 'invalid-reference', 'reference', '请指定初始配方参照，或手动提供参照 mmol。');
    return { mmol: null, label };
  }
  if (basis == null) report(severity, 'unknown-reference', reference === 'manual' ? 'referenceMmol' : 'reference', label + '的 mmol 缺失或无效；不推测剩余官能团。');
  const mmol = basis == null || fraction == null ? null : basis * fraction;
  if (mmol != null && !Number.isFinite(mmol)) report(severity, 'reference-overflow', 'referenceFraction', '参照用量超出有限数值范围。');
  return { mmol: Number.isFinite(mmol) ? mmol : null, label: label + (fraction != null && fraction !== 1 ? ' × ' + fmt(fraction) : '') };
}

function solveDose(addition, material, plan, reference, report) {
  const amount = nonnegative(addition.amount), unit = addition.unit;
  const molar = ['eq', 'mol%', 'mmol'].includes(unit);
  if (amount == null) report('error', 'invalid-amount', 'amount', '加料数量必须是有限非负数。');
  if (!UNITS.has(unit)) report('error', 'invalid-unit', 'unit', '加料单位必须为 eq、mol%、g、mg、mmol 或 mL。');
  const concentration = [addition.solutionPct, material?.concentrationPct, 100]
    .find(value => value != null && !(typeof value === 'string' && !value.trim()));
  const solutionPct = numeric(concentration);
  const solutionFraction = solutionPct > 0 && solutionPct <= 100 ? solutionPct / 100 : null;
  if (solutionFraction == null) report('error', 'invalid-concentration', 'solutionPct', '溶液质量浓度必须大于 0 且不大于 100%；与纯度独立。');
  let purityFraction = 1;
  if (addition.purityCorrection === true) {
    const libraryPurity=material?.purity==null?100:material.purity===''?'':materialPurityFraction(material)*100;
    const purity = numeric(plan.materialPurities?.[addition.materialId] ?? libraryPurity);
    purityFraction = purity > 0 && purity <= 100 ? purity / 100 : null;
    if (purityFraction == null) report('error', 'invalid-purity', 'purityCorrection', '启用校正时，纯度必须大于 0 且不大于 100%。');
  } else if (addition.purityCorrection !== undefined && addition.purityCorrection !== false) {
    purityFraction = null;
    report('error', 'invalid-purity-correction', 'purityCorrection', '纯度校正必须显式为 true 或 false。');
  }
  const mw = numeric(material?.mw);
  if (!(mw > 0)) report(molar ? 'error' : 'warning', 'missing-mw', 'materialId', '物料库分子量缺失或无效；无法进行摩尔与质量换算。');
  const finite = (value, field) => {
    if (value == null) return null;
    if (Number.isFinite(value) && value >= 0) return value;
    report('error', 'dose-overflow', field, '加料换算超出有限非负数值范围。');
    return null;
  };
  let mass = null, soluteMass = null, carrierMass = null, mmol = null;
  if (amount != null && UNITS.has(unit)) {
    if (molar) {
      mmol = finite(unit === 'mmol' ? amount : reference.mmol == null ? null : amount * (unit === 'mol%' ? reference.mmol / 100 : reference.mmol), 'amount');
      soluteMass = mmol == null || !(mw > 0) || purityFraction == null ? null : finite(mmol * (mw / 1000) / purityFraction, 'amount');
      mass = soluteMass == null || solutionFraction == null ? null : finite(soluteMass / solutionFraction, 'solutionPct');
    } else {
      if (unit === 'g') mass = amount;
      if (unit === 'mg') mass = amount / 1000;
      if (unit === 'mL') {
        const density = numeric(material?.density);
        if (!(density > 0)) report('error', 'missing-density', 'materialId', 'mL 换算必须使用该投加物料在物料库中的有效密度（g/mL）。');
        else mass = finite(amount * density, 'amount');
      }
      soluteMass = mass == null || solutionFraction == null ? null : finite(mass * solutionFraction, 'solutionPct');
      mmol = soluteMass == null || !(mw > 0) || purityFraction == null ? null : finite(soluteMass * purityFraction / mw * 1000, 'amount');
    }
    carrierMass = mass == null || soluteMass == null ? null : finite(mass - soluteMass, 'solutionPct');
  }
  const eq = amount == null || !UNITS.has(unit) ? null : unit === 'eq' ? amount : unit === 'mol%' ? amount / 100
    : mmol == null || !(reference.mmol > 0) ? null : finite(mmol / reference.mmol, 'reference');
  const notes = [];
  if (reference.label !== '参照未指定') notes.push(reference.label + '，有效参照 ' + fmt(reference.mmol) + ' mmol；不随分支比例自动缩放');
  notes.push(molar ? '输入为目标活性溶质用量' : '输入为实际投加总量');
  if (solutionFraction != null && solutionPct !== 100) notes.push('溶液质量浓度 ' + fmt(solutionPct) + '%；试剂 ' + fmt(soluteMass) + ' g，载体 ' + fmt(carrierMass) + ' g');
  if (addition.purityCorrection === true) notes.push('纯度校正 ' + fmt(purityFraction == null ? null : purityFraction * 100) + '%（独立于浓度）');
  if (addition.density !== undefined) notes.push('行内 density 不参与换算，仅采用物料库密度');
  return { mass, soluteMass, carrierMass, mmol, eq, notes };
}

/** Calculate process additions only; aggregate values are added physical masses in grams. */
export function calcProcessAdditions(plan = {}, baseCalc = {}, materials = []) {
  plan ??= {}; baseCalc ??= {};
  const library = libraryOf(materials), branches = branchesOf(plan), issues = [], rows = [];
  const steps = list(plan.steps);
  const stepIds = steps.map(step => step?.id);
  let motherMass = 0, totalKnown = 0;
  const branchMasses = Object.fromEntries(branches.map(branch => [branch.id, 0]));
  const aggregate = (current, mass, path) => {
    if (current == null || mass == null) return null;
    const total = current + mass;
    if (Number.isFinite(total)) return total;
    issues.push({ severity: 'error', code: 'mass-total-overflow', path, message: '已知加料质量合计超出有限数值范围。' });
    return null;
  };
  if (plan.steps != null && !Array.isArray(plan.steps)) issues.push({ severity: 'error', code: 'invalid-steps', path: 'steps', message: '工艺步骤必须是数组。' });
  steps.forEach((rawStep, stepIndex) => {
    const step = rawStep ?? {}, path = 'steps.' + stepIndex;
    const reportStep = (severity, code, field, message) => issues.push({ severity, code, path: path + '.' + field, message });
    readConditions(step, reportStep);
    const scope = scopeOf(step, branches);
    const additions = list(step.additions), additionIds = additions.map(addition => addition?.id);
    if (step.additions != null && !Array.isArray(step.additions)) reportStep('error', 'invalid-additions', 'additions', '步骤加料必须是数组。');
    additions.forEach((rawAddition, index) => {
      const addition = rawAddition ?? {}, rowPath = path + '.additions.' + index;
      const report = (severity, code, field, message) => issues.push({ severity, code, path: rowPath + '.' + field, message });
      const identified = identifier(step.id) && identifier(addition.id)
        && stepIds.filter(id => id === step.id).length === 1 && additionIds.filter(id => id === addition.id).length === 1;
      if (!identified) report('error', 'invalid-addition-id', 'id', '步骤和加料行必须具有唯一、非空的字符串 ID；此行不计入质量合计。');
      if (scope.scope == null) reportStep('error', 'unknown-branch-scope', 'branchScope', '加料范围未唯一匹配已定义分支；此行不计入母批或分支合计。');
      const material = library.get(addition.materialId);
      if (!material) report('error', 'unknown-material', 'materialId', '物料 ' + String(addition.materialId ?? '') + ' 不在物料库中。');
      const reference = resolveReference(addition, baseCalc, library, report);
      const dose = solveDose(addition, material, plan, reference, report);
      if (addition.solventId) {
        const solvent = library.get(addition.solventId);
        if (!solvent) report('warning', 'unknown-carrier', 'solventId', '载体溶剂不在物料库中；不影响已知投加总质量。');
        dose.notes.push('载体溶剂：' + (solvent?.name ?? addition.solventId));
      }
      if (scope.scope == null) dose.notes.push('分支范围未核实，未计入质量合计');
      if (!identified) dose.notes.push('ID 未核实，未计入质量合计');
      rows.push({
        id: 'step:' + step.id + ':' + addition.id, stepId: step.id, additionId: addition.id,
        branchId: scope.branchId, role: 'process-addition', materialId: addition.materialId,
        name: material?.name ?? addition.materialId ?? '待指定物料', mass: dose.mass,
        soluteMass: dose.soluteMass, carrierMass: dose.carrierMass, mmol: dose.mmol, eq: dose.eq,
        amount: dose.mass, unit: 'g', inputAmount: addition.amount, inputUnit: addition.unit,
        referenceMmol: reference.mmol, referenceLabel: reference.label,
        stage: 'process-' + step.id, scope: scope.scope, notes: dose.notes.join('；'),
      });
      if (scope.scope == null || !identified) return;
      if (scope.scope === 'mother') motherMass = aggregate(motherMass, dose.mass, rowPath);
      else branchMasses[scope.branchId] = aggregate(branchMasses[scope.branchId], dose.mass, rowPath);
      if (dose.mass != null) totalKnown = aggregate(totalKnown, dose.mass, rowPath);
    });
  });
  return { rows, issues, motherMass, branchMasses, totalKnown };
}

/** Resolve every entered dose and this step's independent conditions; preserve text as an operation note. */
export function renderAdditionText(step = {}, plan = {}, baseCalc = {}, materials = []) {
  step ??= {}; plan ??= {};
  const result = calcProcessAdditions({ ...plan, steps: [step] }, baseCalc, materials);
  const scope = scopeOf(step, branchesOf(plan));
  const parts = result.rows.map(row => {
    const details = [];
    if (row.inputUnit !== 'g') details.push('输入 ' + fmt(nonnegative(row.inputAmount)) + ' ' + String(row.inputUnit ?? '单位待核实'));
    if (row.mmol != null) details.push(fmt(row.mmol) + ' mmol');
    if (row.notes) details.push(row.notes);
    const dose = row.mass == null ? '【投加质量待核实】' : fmt(row.mass) + ' g';
    return '向' + scope.label + '加入 ' + row.name + ' ' + dose + (details.length ? '（' + details.join('；') + '）' : '') + '。';
  });
  const conditions = step.conditions ?? {}, conditionText = [];
  if (conditions.tempC !== undefined) conditionText.push(numeric(conditions.tempC) == null ? '【温度待核实】' : '温度 ' + fmt(numeric(conditions.tempC)) + ' °C');
  if (conditions.hours !== undefined) conditionText.push(nonnegative(conditions.hours) == null ? '【时长待核实】' : '时长 ' + fmt(nonnegative(conditions.hours)) + ' h');
  if (conditionText.length) parts.push('条件：' + conditionText.join('，') + '。');
  const problems = [...new Set(result.issues.filter(item => item.severity === 'error').map(item => item.message))];
  if (problems.length) parts.push('待核实：' + problems.join('；'));
  if (typeof step.text === 'string' && step.text.trim()) parts.push('操作备注：' + step.text.trim());
  return parts.join('\n');
}
