// Structured designs, including an explicitly empty list, take precedence over source route labels.
import { calcRecipe } from './core.js';
import { calcBranches, branchSteps, applyBranchSteps, renderWorkupStep, workupSignature } from './workup.js';
const copy = value => value == null ? value : JSON.parse(JSON.stringify(value));
export const workupDesignFields = ['workupBranches', 'expectedYieldFraction', 'quenchTemp', 'workupDeductWater', 'theoreticalProductMass'];
export const hasWorkupDesign = plan => Array.isArray(plan?.workupBranches);
export const currentBranches = plan => hasWorkupDesign(plan) ? plan.workupBranches : Array.isArray(plan?.branches) ? plan.branches : [];
const aliases = branch => [branch.id, branch.code, branch.archiveCode].filter(Boolean);
export function manualWorkupSteps(plan) {
  let phase = 'reaction';
  return (plan.steps ?? []).filter(step => {
    const declared = step.phase ?? step.params?.section ?? step.sourcePhase;
    if (declared) phase = declared;
    else if (/^(后处理|沉降|过滤|洗涤|干燥|打浆|postprocess|precipitate|filter|wash|dry|slurry)$/i.test(step.type ?? '')) phase = 'postprocess';
    return step.params?.generatedBy !== 'workup-branches' && (phase === 'postprocess' || step.type === 'split' || step.sourceType === 'split');
  });
}
export function workupStructureChanged(before, after) {
  return JSON.stringify((before.workupBranches ?? []).map(b => [b.id, b.code])) !== JSON.stringify((after.workupBranches ?? []).map(b => [b.id, b.code]));
}
export function branchImpact(exp, branch) {
  const names = aliases(branch);
  return {
    actual: Object.hasOwn(exp.actual?.workup?.branches ?? {}, branch.id),
    charges: (exp.actual?.charges ?? []).filter(row => row.branchId === branch.id || row.stage === 'branch-' + branch.code).length,
    samples: (exp.samples ?? []).filter(sample => names.includes(sample.branchId ?? sample.branch) || names.includes(sample.branch)).length,
  };
}
export function canReuseWorkupRoute(before, after) {
  const prior = currentBranches(before), next = currentBranches(after);
  return prior.length > 1 && next.length === 1 && prior.some(branch => branch.id === next[0].id);
}
const escapePattern = value => String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
function scopeBranches(scope, branches) {
  if (scope == null || ['', 'all', 'mother', 'batch'].includes(scope)) return null;
  const values = Array.isArray(scope) ? scope : [String(scope)];
  const found = branches.filter(branch => values.some(value => aliases(branch).includes(value) || String(value).split(/\s*[/、,，]\s*/).some(part => aliases(branch).includes(part))));
  return found.length ? found : null;
}
function singleRouteText(text, prior, branch, branches) {
  if (typeof text !== 'string') return text;
  let value = text.replace(/两路(?:各|均)?|每路|各路/g, '本路线');
  // Only shared branch labels and paired sample labels are adapted; quantities and chemistry are literal.
  for (const left of branches) for (const right of branches) {
    if (left === right || !left.code || !right.code) continue;
    const a = escapePattern(left.code), b = escapePattern(right.code);
    value = value.replace(new RegExp(`\\b${a}-(PRE|FP)\\s*[/、,，]\\s*${b}-\\1\\b`, 'g'), (_match, suffix) => branch.code + '-' + suffix);
    if (['A', 'B'].includes(left.code) && ['A', 'B'].includes(right.code)) value = value.replace(new RegExp(`\\b${a}\\s*[/、]\\s*${b}\\b`, 'g'), '本路线');
  }
  value = value.replace(/本路线(?:本路线)+/g, '本路线');
  for (const key of ['archiveCode', 'id']) {
    if (prior[key] && branch[key] && prior[key] !== branch[key]) value = value.replace(new RegExp(`(?<![A-Za-z0-9_-])${escapePattern(prior[key])}(?![A-Za-z0-9_-])`, 'g'), () => branch[key]);
  }
  if (prior.code !== branch.code && prior.code) value = value.replace(new RegExp(`\\b${escapePattern(prior.code)}(?=路|支|-(?:PRE|FP)\\b)`, 'g'), () => branch.code);
  return value;
}
function rememberRouteSource(step, original) {
  step.params ??= {};
  step.params.originalText ??= original.text ?? '';
  step.params.sourceBaseline ??= copy(original);
}
function routeOperations(branch) {
  const operations = [];
  if (branch.quench?.enabled) operations.push('quench');
  for (const key of ['dilution', 'precipitation']) if (Number(branch[key]?.amount) > 0) operations.push(key);
  for (const wash of branch.washes ?? []) if (wash.cycles !== 0) operations.push('wash:' + wash.id);
  if (branch.dry?.tempC != null || branch.dry?.hours != null) operations.push('dry');
  return operations;
}
/** Pure conversion: retain source order/IDs and ancillary operations, binding only the selected route. */
export function reuseSingleWorkupRoute(before, plan, materials = []) {
  if (!canReuseWorkupRoute(before, plan)) throw Error('沿用流程仅适用于从多分支中保留一条已有路线。');
  const branches = currentBranches(before), branch = currentBranches(plan)[0], prior = branches.find(item => item.id === branch.id);
  const manual = new Set(manualWorkupSteps(before)), steps = [], removedSteps = [];
  let activeScope = null;
  for (const original of before.steps ?? []) {
    const params = original.params ?? {}, generated = params.generatedBy === 'workup-branches';
    const headingId = String(original.text ?? '').trim().match(/^第[一二三四五六七八九十0-9]+份[，,\s]*记录为\s*([A-Za-z0-9_-]+)[:：]?$/)?.[1];
    const heading = original.kind === 'branchHeading' || original.isHeading || ['heading', 'section', 'branch-heading', '小标题'].includes(original.type) || headingId;
    if (heading) activeScope = scopeBranches(original.branchScope ?? headingId ?? original.text?.match(/记录为\s*([A-Za-z0-9_-]+)/)?.[1], branches);
    const bound = generated ? scopeBranches(params.branchId, branches) ?? scopeBranches(original.branchScope, branches) ?? scopeBranches(params.branchCode, branches) : scopeBranches(original.branchScope ?? params.branchId ?? params.branchCode, branches);
    const scoped = bound ?? (original.branchScope == null && manual.has(original) ? activeScope : null);
    const legacySplit = original.sourceType === 'split' && (original.type == null || original.type === '后处理') && !params.boundFromSource;
    const split = params.operation === 'split' || !generated && (original.type === 'split' || legacySplit);
    const otherScope = (generated || manual.has(original)) && scoped && !scoped.some(item => item.id === branch.id);
    const unboundGenerated = generated && params.operation !== 'source-group' && !bound;
    const ineligiblePrepare = generated && params.operation === 'source-group' && params.operationKind === 'quench-prepare' && !branch.quench?.enabled;
    if (split || otherScope || unboundGenerated || ineligiblePrepare) {
      removedSteps.push(copy(original)); continue;
    }
    const step = copy(original);
    if (/终点取样与降温/.test(step.title ?? '')) {
      rememberRouteSource(step, original);
      step.params.processAnchor = 'after-treatment';
    }
    if (generated || manual.has(original)) {
      rememberRouteSource(step, original);
      step.branchScope = branch.id;
      step.title = singleRouteText(step.title, prior, branch, branches);
      if (generated) {
        step.params.branchId = branch.id; step.params.branchCode = branch.code;
        if (params.operation === 'wash' && !branch.washes?.some(wash => wash.id === params.washId)) { removedSteps.push(copy(original)); continue; }
      } else if (step.title === '后处理用量与基准') {
        Object.assign(step.params, { generatedBy: 'workup-branches', operation: 'route-summary', branchId: branch.id, branchCode: branch.code });
      } else step.text = singleRouteText(step.text, prior, branch, branches);
    }
    steps.push(step);
  }
  const current = { ...plan, steps }, result = calcBranches(current, calcRecipe(current, materials), materials);
  for (const step of steps) if (step.params?.generatedBy === 'workup-branches') step.text = renderWorkupStep(step, current, result, materials);
  const priorOperations = new Set(routeOperations(prior)), bindings = new Set();
  for (const step of steps) if (step.params?.generatedBy === 'workup-branches') {
    const p = step.params, operation = p.operation === 'source-group' ? p.operationKind : p.operation;
    bindings.add(operation === 'wash' ? 'wash:' + (p.operation === 'source-group' ? branch.washes?.[p.washIndex]?.id : p.washId) : operation);
  }
  const stepsStale = routeOperations(branch).some(operation => !priorOperations.has(operation) && !bindings.has(operation));
  return { steps, removedSteps, stepsStale };
}
export function commitWorkupDesign(exp, draft, materials = [], replaceManual = false) {
  const before = copy(exp.plan), old = currentBranches(before), next = copy(draft.workupBranches ?? []);
  const reuseRoute = replaceManual === 'reuse-route', replaceAll = replaceManual === true;
  if (reuseRoute && !canReuseWorkupRoute(before, draft)) throw Error('沿用流程仅适用于从多分支中保留一条已有路线。');
  const ids = new Set(), codes = new Set();
  for (const branch of next) {
    branch.code = String(branch.code ?? '').trim();
    if (!branch.id || ids.has(branch.id)) throw Error('分支标识重复，请重新复制或新增分支。');
    if (!branch.code || codes.has(branch.code)) throw Error('分支代号不能为空或重复。');
    ids.add(branch.id); codes.add(branch.code);
    const prior = old.find(item => item.id === branch.id);
    if (prior?.code !== branch.code && branch.archiveCode) branch.archiveCode = (exp.code ?? exp.id) + '-' + branch.code;
  }
  for (const key of workupDesignFields) {
    if (draft[key] === undefined) delete exp.plan[key]; else exp.plan[key] = copy(draft[key]);
  }
  exp.plan.workupBranches = next;
  const removed = old.filter(branch => !ids.has(branch.id));
  const result = calcBranches(exp.plan, calcRecipe(exp.plan, materials), materials);
  let manual = replaceAll ? manualWorkupSteps(exp.plan) : [], reuseStepsStale = false;
  const generated = (exp.plan.steps ?? []).filter(step => step.params?.generatedBy === 'workup-branches');
  if (reuseRoute) {
    const converted = reuseSingleWorkupRoute(before, exp.plan, materials);
    exp.plan.steps = converted.steps;
    reuseStepsStale = converted.stepsStale;
    manual = converted.removedSteps.filter(step => step.params?.generatedBy !== 'workup-branches');
    exp.needsStepReview = true;
  } else if (replaceAll) {
    exp.plan.steps = (exp.plan.steps ?? []).filter(step => !manual.includes(step));
    applyBranchSteps(exp.plan, branchSteps(exp.plan, result, materials));
  } else if (generated.length) {
    // Keep source-bound shared operations and custom steps; drop only obsolete generated bindings.
    exp.plan.steps = exp.plan.steps.filter(step => {
      if (step.params?.generatedBy !== 'workup-branches') return true;
      const p = step.params;
      if (!next.length || p.operation === 'split' && next.length < 2) return false;
      if (p.operation === 'source-group') return true;
      return next.some(branch => branch.id === p.branchId);
    });
    for (const step of exp.plan.steps) if (step.params?.generatedBy === 'workup-branches') {
      const branch = next.find(branch => branch.id === step.params.branchId);
      if (branch) step.params.branchCode = branch.code;
      step.text = renderWorkupStep(step, exp.plan, result, materials);
    }
  }
  if (reuseStepsStale) {
    delete exp.plan.workupGeneratedSignature;
    exp.plan.workupStepsStale = true;
  } else if (replaceAll || reuseRoute || !generated.length) {
    exp.plan.workupGeneratedSignature = workupSignature(exp.plan, materials);
    exp.plan.workupStepsStale = false;
  } else exp.plan.workupStepsStale = true;
  exp.sourcePlanModified = true;
  exp.needsStepReview ||= manualWorkupSteps(exp.plan).length > 0 && workupStructureChanged(before, exp.plan);
  return { before, removed, replacedSteps: copy(manual) };
}
// Revert this save's structural changes only; later manual text and actual records survive.
export function undoWorkupDesign(exp, before, after) {
  if (workupDesignFields.some(key => JSON.stringify(exp.plan[key]) !== JSON.stringify(after[key]))) return false;
  for (const key of workupDesignFields) {
    if (before[key] === undefined) delete exp.plan[key]; else exp.plan[key] = copy(before[key]);
  }
  const steps = exp.plan.steps ??= [], old = new Map((before.steps ?? []).map(step => [step.id, step]));
  const saved = new Map((after.steps ?? []).map(step => [step.id, step]));
  const equal = (a,b) => JSON.stringify(a) === JSON.stringify(b);
  for (const [id, step] of saved) {
    const prior = old.get(id), index = steps.findIndex(current => current.id === id);
    if (equal(prior,step) || index < 0 || !equal(steps[index],step)) continue;
    if (prior) steps[index] = copy(prior); else steps.splice(index,1);
  }
  for (const [index, step] of (before.steps ?? []).entries()) if (!saved.has(step.id) && !steps.some(current => current.id === step.id)) steps.splice(Math.min(index,steps.length),0,copy(step));
  exp.plan.workupStepsStale = true;
  return true;
}
export function restoreWorkupBranch(exp, payload, materials = []) {
  const branch = copy(payload.branch), branches = exp.plan.workupBranches ??= [];
  if (branches.some(item => item.id === branch.id || item.code === branch.code)) throw Error('相同标识或代号的分支已存在，请先修改现有分支代号。');
  branches.splice(Math.min(payload.index ?? branches.length, branches.length), 0, branch);
  const steps = exp.plan.steps ??= [];
  for (const item of payload.steps ?? []) {
    if (item.step.params?.operation === 'split' && branches.length < 2) continue;
    if (!steps.some(step => step.id === item.step.id)) steps.splice(Math.min(item.index, steps.length), 0, copy(item.step));
  }
  const result = calcBranches(exp.plan, calcRecipe(exp.plan, materials), materials);
  for (const step of steps) if (step.params?.generatedBy === 'workup-branches') step.text = renderWorkupStep(step, exp.plan, result, materials);
  exp.plan.workupStepsStale = true;
  exp.sourcePlanModified = true;
}
