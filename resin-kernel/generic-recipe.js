// Explicit charge quantities, with mass in g and library density in g/mL.
// This profile does not derive reaction chemistry from material names or roles.
const num = value => value == null || value === '' || typeof value === 'boolean' || !Number.isFinite(Number(value)) ? null : Number(value);
const units = { g: 1, mg: 0.001, kg: 1000, mL: 1, L: 1000 };
const issue = (severity, code, path, message) => ({ severity, code, path, message });

export function validateGenericPlan(plan = {}) {
  const issues = [];
  if (!Array.isArray(plan.charges) || !plan.charges.length) issues.push(issue('error', 'missing-charges', 'charges', '请至少填写一项投料。'));
  const ids = new Set();
  for (const [index, row] of (Array.isArray(plan.charges) ? plan.charges : []).entries()) {
    const path = 'charges.' + index;
    if (!row || typeof row !== 'object' || Array.isArray(row)) {
      issues.push(issue('error', 'invalid-charge', path, '投料项必须为对象。'));
      continue;
    }
    if (typeof row.id !== 'string' || !row.id.trim()) issues.push(issue('error', 'missing-charge-id', path + '.id', '请为投料项指定 ID。'));
    else if (ids.has(row.id)) issues.push(issue('error', 'duplicate-charge-id', path + '.id', '投料项 ID 重复。'));
    ids.add(row.id);
    if (!row.materialId && !(typeof row.name === 'string' && row.name.trim())) issues.push(issue('error', 'missing-material', path, '请指定物料或填写物料名称。'));
    if (typeof row.role !== 'string') issues.push(issue('error', 'invalid-charge-role', path + '.role', '物料用途应为文本，可留空。'));
    if (!Object.hasOwn(units, row.unit)) issues.push(issue('error', 'invalid-charge-unit', path + '.unit', '投料单位应为 g、mg、kg、mL 或 L。'));
    if (row.amount == null || row.amount === '') issues.push(issue('warning', 'missing-charge-amount', path + '.amount', '投料量尚未填写；总质量暂不可计算。'));
    else if (num(row.amount) == null || num(row.amount) < 0) issues.push(issue('error', 'invalid-charge-amount', path + '.amount', '投料量必须为非负数。'));
    if (row.solidPct != null && row.solidPct !== '' && (num(row.solidPct) == null || num(row.solidPct) < 0 || num(row.solidPct) > 100)) issues.push(issue('error', 'invalid-charge-solid-pct', path + '.solidPct', '物料固体质量分数必须在 0–100% 之间。'));
  }
  for (const key of ['holdHours', 'feedHours', 'theoreticalProductMass']) if (plan[key] != null && plan[key] !== '' && (num(plan[key]) == null || num(plan[key]) < 0)) issues.push(issue('error', 'invalid-' + key, key, key + ' 必须为非负数。'));
  if (plan.temp != null && plan.temp !== '' && num(plan.temp) == null) issues.push(issue('error', 'invalid-temp', 'temp', '温度必须为数值。'));
  if (plan.timeBasis === 'total-from-feed-start' && num(plan.holdHours) != null && num(plan.feedHours) != null && num(plan.holdHours) < num(plan.feedHours)) issues.push(issue('error', 'negative-post-feed-hold', 'holdHours', '从滴加开始计时的总时长不能短于滴加时长。'));
  if (Array.isArray(plan.steps)) {
    const stepIds = plan.steps.map(step => step.id).filter(Boolean);
    if (new Set(stepIds).size !== stepIds.length) issues.push(issue('error', 'duplicate-step-id', 'steps', '工艺阶段 ID 重复，无法可靠关联样品与记录。'));
  }
  return { valid: !issues.some(item => item.severity === 'error'), issues };
}

export function calcGenericRecipe(plan = {}, materials = []) {
  const { issues: validationIssues } = validateGenericPlan(plan);
  const issues = [...validationIssues];
  const library = Array.isArray(materials) ? new Map(materials.map(item => [item.id, item])) : new Map(Object.entries(materials));
  const rows = (Array.isArray(plan.charges) ? plan.charges : []).map((entry, index) => {
    const row = entry && typeof entry === 'object' && !Array.isArray(entry) ? entry : {};
    const material = library.get(row.materialId), amount = num(row.amount);
    const isVolume = ['mL', 'L'].includes(row.unit);
    const quantity = amount != null && amount >= 0 && Object.hasOwn(units, row.unit) ? amount * units[row.unit] : null;
    const density = num(material?.density);
    const mass = quantity == null ? null : !isVolume || quantity === 0 ? quantity : density > 0 ? quantity * density : null;
    if (isVolume && quantity > 0 && !(density > 0)) issues.push(issue('warning', 'missing-density', 'charges.' + index + '.materialId', (row.name || material?.name || row.materialId || '物料') + ' 缺少有效的物料库密度；保留体积，质量及总量暂不可计算。'));
    return { ...row, name: row.name || material?.name || row.materialId || '待指定物料', mass, mmol: null, eq: null, ...(isVolume ? { volumeMl: quantity } : {}) };
  });
  const knownMass = rows.reduce((total, row) => total + (row.mass ?? 0), 0);
  const totalMass = rows.every(row => row.mass != null) ? knownMass : null;
  const solidsKnown = totalMass != null && rows.every(row => row.mass === 0 || num(row.solidPct) != null && num(row.solidPct) >= 0 && num(row.solidPct) <= 100);
  const solidNumeratorMass = solidsKnown ? rows.reduce((total, row) => total + (row.mass === 0 ? 0 : row.mass * num(row.solidPct) / 100), 0) : null;
  if (rows.some(row => row.mass !== 0 && (row.solidPct == null || row.solidPct === ''))) issues.push(issue('warning', 'missing-charge-solid-pct', 'charges', '非零投料的固体质量分数未全部填写，混合物固含暂不可计算。'));
  const holdHours = num(plan.holdHours) >= 0 ? num(plan.holdHours) : null;
  const feedHours = num(plan.feedHours) >= 0 ? num(plan.feedHours) : null;
  const postFeedHoldHours = plan.timeBasis === 'total-from-feed-start' ? holdHours != null && feedHours != null && holdHours >= feedHours ? holdHours - feedHours : null : plan.timeBasis === 'after-feed' ? holdHours : null;
  const totalFromFeedStartHours = plan.timeBasis === 'total-from-feed-start' ? holdHours : plan.timeBasis === 'after-feed' && holdHours != null && feedHours != null ? holdHours + feedHours : null;
  const theoreticalProductMass = num(plan.theoreticalProductMass) >= 0 ? num(plan.theoreticalProductMass) : null;
  return {
    calculationProfile: 'generic-mass', valid: !issues.some(item => item.severity === 'error'), rows, issues,
    totalMass, knownMass, solidNumeratorMass, solidPct: totalMass > 0 && solidNumeratorMass != null ? solidNumeratorMass / totalMass * 100 : null,
    theoreticalProductMass, theoreticalProductMassBasis: theoreticalProductMass == null ? null : 'user-entered',
    monoBMmol: null, monoAMmol: null, monomerMass: null, catalystMmol: null, catalystMass: null, nonvolatileMass: null,
    solventMass: null, feedSolventMass: null, mainSolventMass: null,
    hasQuench: false, quenchAcidFraction: null, quenchMmol: null, quenchMass: null, quenchSolutionMass: null, quenchSolventMass: null,
    postQuenchTotalMass: null, postQuenchSolventMass: null, postQuenchSolidPct: null,
    holdHours, feedHours, postFeedHoldHours, totalFromFeedStartHours,
    calculationBasis: { mass: '逐项显式投料量；体积仅按物料库密度换算为 g', solid: '逐项显式固体质量分数加权 / 全部投料质量', theoreticalProductMass: theoreticalProductMass == null ? '未提供理论产品质量基准' : '用户显式输入的理论产品质量（g）', time: plan.timeBasis === 'total-from-feed-start' ? 'holdHours 为从滴加开始的总时长' : plan.timeBasis === 'after-feed' ? 'holdHours 为滴加结束后的保温时长' : '按用户指定计时口径记录' },
  };
}
