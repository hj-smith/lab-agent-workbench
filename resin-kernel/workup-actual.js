import { calcBranches } from "./workup.js";

// Actual observations live only in actual.workup. This module never fills them from feed charges,
// normalizes a split, or changes the design. All chemistry continues to use the nominal recipe.
const EPS = 1e-6;
const own = (o, key) => Object.prototype.hasOwnProperty.call(o ?? {}, key);
const missing = (v) => v == null || (typeof v === "string" && v.trim() === "");
const number = (v) => {
  if (missing(v) || !["number", "string"].includes(typeof v)) return null;
  if (typeof v === "string" && !/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(v.trim())) return null;
  return Number.isFinite(Number(v)) ? Number(v) : null;
};
const nonnegative = (v) => { const n = number(v); return n != null && n >= 0 ? n : null; };
const finite = (v) => typeof v === "number" && Number.isFinite(v) ? v : null;
const fraction = (v) => { const n = nonnegative(v); return n != null && n <= 1 ? n : null; };
const sum = (values) => finite(values.reduce((a, v) => a + v, 0));
const clone = (v) => {
  if (Array.isArray(v)) return v.map(clone);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, clone(x)]));
  return v;
};
const cleanFinite = (v) => {
  if (typeof v === "number") return finite(v);
  if (Array.isArray(v)) return v.map(cleanFinite);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, cleanFinite(x)]));
  return v;
};
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmt = (v, d = 3) => number(v) == null ? "—" : Number(Number(v).toFixed(d)).toString();
const pct = (v) => v == null ? "—" : fmt(v * 100, 2) + "%";
const sourceText = (v) => typeof v === "string" ? v : v == null ? "" : JSON.stringify(v);
const selected = new WeakMap();

const amountUnit = (unit) => ({ g: "g", 克: "g", ml: "mL", 毫升: "mL" })[String(unit ?? "").trim().toLowerCase()] ?? null;
function observedChargeAmount(row) {
  for (const key of ["mass", "massG", "amount", "value", "actualMass", "actual"]) if (own(row, key)) return nonnegative(row[key]);
  return null;
}
function operationFromLabel(row) {
  const probe = [row.name, row.label, row.sourceName, row.source?.name, row.source?.label, typeof row.source === "string" ? row.source : ""].filter(Boolean).join(" ");
  const declared = row.operation ?? row.stage;
  if (["quench", "quench-solvent", "dilution", "precipitation"].includes(declared)) return declared;
  if (declared === "wash" || String(declared ?? "").startsWith("wash-")) return "wash";
  const matches = [
    [/终止载液|预溶载液|quench.solvent/i, "quench-solvent"],
    [/洗涤|打浆|浆洗|wash/i, "wash"],
    [/沉降|反溶剂|precip/i, "precipitation"],
    [/稀释|THF总量|dilution/i, "dilution"],
    [/终止|中和|淬灭|quench|^DMAP(?:用量|总量|质量)?(?:\s|$)/i, "quench"],
  ].filter(([re]) => re.test(probe)).map(([, kind]) => kind);
  const unique = [...new Set(matches)].filter((kind) => kind !== "quench" || !matches.includes("quench-solvent"));
  return unique.length === 1 ? unique[0] : null;
}
function compareCharges(charges, design, rows, derived, add, path) {
  const scoped = (Array.isArray(charges) ? charges : []).filter((row) => row?.branchId === design.id);
  const operationRows = rows.filter((r) => ["quench", "quench-solvent", "dilution", "precipitation"].includes(r.key) || r.key.startsWith("wash-"));
  const materialFor = (key) => key === "quench" ? design.quench?.materialId : key === "quench-solvent" ? design.quench?.solventId : key === "dilution" ? design.dilution?.materialId : key === "precipitation" ? design.precipitation?.materialId : design.washes?.find((w) => "wash-" + w.id === key)?.materialId;
  return operationRows.map((r) => {
    const materialId = materialFor(r.key), kind = r.key.startsWith("wash-") ? "wash" : r.key;
    const unit = amountUnit(r.unit.replace(/\s*×.*/, ""));
    const wash = r.key.startsWith("wash-") ? derived.washes.find((w) => "wash-" + w.id === r.key) : null;
    const cycles = wash ? wash.cycles : 1;
    const recommendedAmount = r.amount == null || cycles == null ? null : finite(r.amount * cycles);
    const candidates = scoped.filter((charge) => {
      if (charge.workupKey != null) return charge.workupKey === r.key && (charge.materialId == null || charge.materialId === materialId);
      if (!materialId || charge.materialId !== materialId || operationFromLabel(charge) !== kind) return false;
      // Repeated washes with the same material cannot be distinguished by a generic label.
      return operationRows.filter((x) => (x.key.startsWith("wash-") ? "wash" : x.key) === kind && materialFor(x.key) === materialId).length === 1;
    });
    const usable = candidates.filter((charge) => amountUnit(charge.unit ?? (own(charge, "massG") ? "g" : null)) === unit && unit != null);
    const match = candidates.length === 1 && usable.length === 1 ? usable[0] : null;
    if (candidates.length > 1) add("warning", "ambiguous-actual-charge", path, (design.code ?? "") + " 支 " + r.label + " 存在重复实际投料记录，未求和或选取。", design.id);
    else if (candidates.length && !match) add("warning", "charge-unit-mismatch", path, (design.code ?? "") + " 支 " + r.label + " 实际投料单位与重算单位不同或缺失，未换算比较。", design.id);
    else if (!candidates.length && scoped.some((charge) => charge.workupKey == null && charge.materialId === materialId && operationFromLabel(charge) === kind))
      add("warning", "ambiguous-actual-charge", path, (design.code ?? "") + " 支 " + r.label + " 的实际记录不能唯一对应设计操作，未作分摊。", design.id);
    const actualAmount = match ? observedChargeAmount(match) : null;
    if (match && actualAmount == null && ["mass", "massG", "amount", "value", "actualMass", "actual"].some((key) => own(match, key) && !missing(match[key])))
      add("warning", "invalid-actual-charge", path, (design.code ?? "") + " 支 " + r.label + " 实际投料数值无效，保持未知。", design.id);
    return { key: r.key, label: r.label, materialId, recommendedAmount, actualAmount, deviationAmount: actualAmount != null && recommendedAmount != null ? finite(actualAmount - recommendedAmount) : null, deviationFraction: actualAmount != null && recommendedAmount > 0 ? finite((actualAmount - recommendedAmount) / recommendedAmount) : null, unit: unit ?? "", matched: match != null, chargeId: match?.id ?? null, provisional: r.provisional, note: "实际投入只匹配同一稳定分支 ID 与操作；单位须相同，洗涤建议量按全部次数合计。" + (match ? "匹配来源：" + (match.workupKey != null ? "操作键" : "唯一物料及操作名称") + "。" : "尚无唯一可比实际投入。") };
  });
}

function inferActualRatios(comparisons, design, derived, plan, materials, chemistryReady) {
  const lib = new Map(materials.map((m) => [m.id, m]));
  const base = comparisons.find((r) => r.key === "quench"), carrier = comparisons.find((r) => r.key === "quench-solvent");
  const actualSolutionMass = base?.actualAmount != null && carrier?.actualAmount != null ? finite(base.actualAmount + carrier.actualAmount) : null;
  const actualSolutionPct = actualSolutionMass > 0 ? finite(base.actualAmount / actualSolutionMass * 100) : null;
  const actualDilution = comparisons.find((r) => r.key === "dilution");
  const convertActual = (row, wanted) => {
    if (row?.actualAmount == null) return null;
    if (row.unit === wanted) return row.actualAmount;
    const density = nonnegative(lib.get(row.materialId)?.density);
    if (!(density > 0)) return null;
    return wanted === "g" && row.unit === "mL" ? finite(row.actualAmount * density) : wanted === "mL" && row.unit === "g" ? finite(row.actualAmount / density) : null;
  };
  for (const row of comparisons) {
    row.actualRatio = null;
    row.ratioUnit = "";
    row.ratioNote = "";
    if (row.key === "quench") {
      const m = lib.get(row.materialId), override = plan.materialPurities?.[row.materialId];
      let purity = number(missing(override) ? m?.purity : override);
      if (purity == null && plan.calculationPurityBasis !== "strict") purity = 100;
      const mw = nonnegative(m?.mw), acid = derived.quench?.acidMmol;
      if (row.actualAmount != null && row.unit === "g" && chemistryReady && mw > 0 && purity > 0 && purity <= 100 && acid > 0)
        row.actualRatio = finite(row.actualAmount * (purity / 100) / mw * 1000 / acid);
      row.ratioUnit = "eq";
      row.ratioNote = "实际终止剂投入按同一纯度/MW换算，相对" + (design.quench?.acidBasis === "total" ? "完整母批" : "本支") + "名义酸；均匀分料前提须确认，未测定残余酸。";
      row.actualSolutionPct = actualSolutionPct;
      continue;
    }
    if (row.key === "quench-solvent") {
      row.actualRatio = actualSolutionPct;
      row.actualSolutionPct = actualSolutionPct;
      row.ratioUnit = "wt% 终止剂溶液";
      row.ratioNote = "本支实际终止剂质量 /（实际终止剂 + 实际载液质量）；仅在两项均有唯一同单位记录时反算。";
      continue;
    }
    const wash = row.key.startsWith("wash-") ? design.washes?.find((w) => "wash-" + w.id === row.key) : null;
    const spec = wash ?? design[row.key];
    const unit = spec?.unit;
    if (!unit) continue;
    row.ratioUnit = unit;
    const cycles = wash ? derived.washes.find((w) => w.id === wash.id)?.cycles : 1;
    if (!(cycles > 0)) { row.ratioNote = "洗涤总量反算单次比率需要有效正整数次数。"; continue; }
    let denominator = null, wanted = "mL";
    if (unit === "mL" || unit === "g") { denominator = 1; wanted = unit; }
    else if (unit === "mL/g-product") { denominator = derived.productMass; row.ratioNote = "分母为按实际分料重算的预计产品，仍含设计收率假设。"; }
    else if (unit === "mL/g-theoretical") { denominator = derived.theoreticalMass; row.ratioNote = "分母为名义理论树脂，依赖均匀分料与名义化学口径。"; }
    else if (unit === "g/g-portion") { denominator = derived.portionMass; wanted = "g"; row.ratioNote = "分母为本支实测质量（比例口径下为比例 × 实测母液总量）。"; }
    else if (unit === "v/v-dilution") { denominator = convertActual(actualDilution, "mL"); row.ratioNote = "分母为同支实际稀释液体积；质量转体积仅在材料库有有效密度时进行。"; }
    else if (unit === "g/g-diluted") {
      wanted = "g";
      const dilutionMass = derived.dilution ? convertActual(actualDilution, "g") : 0;
      const quenchMass = design.quench?.enabled ? (design.quench.solutionPct == null || number(design.quench.solutionPct) === 100 ? base?.actualAmount : actualSolutionMass) : 0;
      denominator = derived.portionMass != null && dilutionMass != null && quenchMass != null ? finite(derived.portionMass + dilutionMass + quenchMass) : null;
      row.ratioNote = "分母为本支反应液 + 实际稀释剂 + 实际终止溶液质量；缺少任一投入则未知。";
    }
    const numerator = convertActual(row, wanted);
    if (numerator != null && denominator > 0) row.actualRatio = finite(numerator / cycles / denominator);
    if (wash) row.ratioNote += " 实际投入按全部洗涤总量记录，除以 " + (cycles ?? "待定") + " 次后反算单次比率。";
    if (row.provisional) row.ratioNote += " 前提未确认，仅为暂估比率。";
  }
  return comparisons;
}

function chemistryInputs(plan, calc, materials) {
  const p = clone(plan), c = clone(calc), lib = clone(materials);
  // calcBranches accepts numeric strings; reject JS coercions (false/[]/whitespace) here.
  for (const key of ["monomerMass", "monoAMmol", "catalystMmol", "totalMass"]) c[key] = nonnegative(c[key]);
  p.expectedYieldFraction = fraction(p.expectedYieldFraction);
  for (const m of lib) for (const key of ["mw", "density", "purity"]) m[key] = number(m[key]);
  p.materialPurities = Object.fromEntries(Object.entries(p.materialPurities ?? {}).map(([id, v]) => [id, missing(v) ? null : number(v) ?? 0]));
  // Preserve invalid explicit purities as invalid, rather than turning them into a nominal default.
  for (let i = 0; i < lib.length; i++) if (!missing(materials[i]?.purity) && number(materials[i].purity) == null) lib[i].purity = 0;
  for (const b of p.workupBranches ?? []) {
    b.fraction = fraction(b.fraction);
    if (b.quench) b.quench.eq = number(b.quench.eq);
    for (const item of [b.dilution, b.precipitation, ...(b.washes ?? [])]) if (item) {
      item.amount = number(item.amount);
      if (own(item, "cycles")) item.cycles = missing(item.cycles) ? null : number(item.cycles) ?? -1;
    }
  }
  return { p, c, lib };
}

/** Pure actual split / workup calculation. Null means unknown; zero is an observation. */
export function calcActualWorkup(exp = {}, calc = {}, materials = []) {
  const designs = Array.isArray(exp.plan?.workupBranches) ? exp.plan.workupBranches : [];
  const workup = exp.actual?.workup ?? {};
  const { p, c, lib } = chemistryInputs({ ...exp.plan, workupBranches: designs }, calc ?? {}, Array.isArray(materials) ? materials : []);
  const issues = [];
  const add = (severity, code, path, message, branchId) => {
    const issue = { severity, code, path, message, ...(branchId == null ? {} : { branchId }) };
    issues.push(issue);
    return issue;
  };
  const read = (raw, path, label, { positive = false, share = false, branchId } = {}) => {
    const n = number(raw);
    if (missing(raw)) return null;
    if (n == null || (positive ? n <= 0 : n < 0) || (share && n > 1)) {
      add("error", "invalid-number", path, label + (share ? "须为 0–100% 的有限数；无效值不参与核算。" : positive ? "须为大于 0 的有限数（g）。" : "须为非负有限数（g）。"), branchId);
      return null;
    }
    return n;
  };
  const totalMass = read(workup.totalMass, "actual.workup.totalMass", "分料前母液实测总质量", { positive: true });
  const retainedMass = read(workup.retainedMass, "actual.workup.retainedMass", "实测留存质量");
  const basis = workup.splitBasis === "fraction" ? "fraction" : "mass";
  const homogeneousConfirmed = workup.homogeneousConfirmed === true;
  if (workup.splitBasis != null && !["mass", "fraction"].includes(workup.splitBasis))
    add("error", "invalid-basis", "actual.workup.splitBasis", "分料口径无效，暂按实测质量口径显示。");
  if (designs.length && totalMass == null)
    add("warning", "missing-total", "actual.workup.totalMass", "分料前母液实测总质量未知；不会用投料合计代替。" + (basis === "mass" ? "各支占母液比例尚不可计算。" : "比例仍可核算，分支反应液质量未知。"));
  if (designs.length && !homogeneousConfirmed)
    add("warning", "homogeneity-unconfirmed", "actual.workup.homogeneousConfirmed", "尚未确认分料前母液均匀：按比例分配的名义化学量仅为暂估，终止剂用量不作为可执行称量。");
  if (designs.length && retainedMass == null)
    add("info", "missing-retained", "actual.workup.retainedMass", "留存质量未记录（确无留存请填 0）；质量闭合差暂不能确定。");
  const ids = new Set();
  const observations = designs.map((b) => {
    const id = b.id;
    if (id == null || id === "" || ids.has(id)) add("error", "invalid-branch-id", "plan.workupBranches", "设计分支缺少唯一稳定 ID；请在设计中修正。", id);
    ids.add(id);
    const record = own(workup.branches, id) ? workup.branches[id] ?? {} : {};
    const path = "actual.workup.branches." + id;
    const mass = read(record.mass, path + ".mass", (b.code ?? "") + " 支实测反应液质量", { branchId: id });
    const enteredFraction = read(record.fraction, path + ".fraction", (b.code ?? "") + " 支占完整母液比例", { share: true, branchId: id });
    const productMass = read(record.productMass, path + ".productMass", (b.code ?? "") + " 支实测产品质量", { branchId: id });
    const rawShare = basis === "mass" ? (mass != null && totalMass != null ? finite(mass / totalMass) : null) : nonnegative(record.fraction);
    const actualFraction = fraction(rawShare);
    const portionMass = basis === "mass" ? mass : (enteredFraction != null && totalMass != null ? finite(enteredFraction * totalMass) : null);
    if (rawShare != null && rawShare > 1 && basis === "mass") add("error", "branch-over-total", path + ".mass", (b.code ?? "") + " 支质量超过实测母液总质量；比例无效，未作归一化。", id);
    if ((basis === "mass" ? mass : enteredFraction) == null) add("warning", "missing-split", path + (basis === "mass" ? ".mass" : ".fraction"), (b.code ?? "") + " 支尚无有效" + (basis === "mass" ? "实测分料质量。" : "分料比例。"), id);
    if (basis === "fraction" && mass != null && portionMass != null && Math.abs(mass - portionMass) > EPS)
      add("warning", "mass-fraction-mismatch", path + ".mass", (b.code ?? "") + " 支历史/录入质量与当前比例 × 实测母液总质量不一致；比例口径下未采用该质量。", id);
    if (record.source != null) add("info", "imported-fraction", path + ".source", (b.code ?? "") + " 支含历史分料来源（可能为缓存公式推定）：" + sourceText(record.source) + "。该来源仅说明分料比例；酸量仍为配方名义假设。", id);
    return { id, record, mass, fraction: actualFraction, accountingFraction: rawShare, portionMass, productMass, path };
  });
  for (const id of Object.keys(workup.branches ?? {})) if (!ids.has(id))
    add("warning", "orphan-branch", "actual.workup.branches." + id, "实际记录对应的设计分支已不存在：" + id + "；记录已保留，未计入当前合计。", id);
  const knownBranchMassSum = sum(observations.map((o) => o.portionMass ?? 0));
  const branchMassSum = observations.length && observations.every((o) => o.portionMass != null) ? knownBranchMassSum : null;
  const knownFractionSum = sum(observations.map((o) => o.accountingFraction ?? 0));
  const fractionSum = observations.length && observations.every((o) => o.accountingFraction != null) ? knownFractionSum : null;
  const retainedFraction = retainedMass != null && totalMass != null ? finite(retainedMass / totalMass) : null;
  const remainingFraction = fractionSum != null ? finite(1 - fractionSum) : null;
  const residualMass = totalMass != null && branchMassSum != null && retainedMass != null ? finite(totalMass - branchMassSum - retainedMass) : null;
  const residualFraction = residualMass != null ? finite(residualMass / totalMass) : null;
  const overallocated = (knownFractionSum != null && knownFractionSum > 1 + EPS) || (totalMass != null && knownBranchMassSum != null && knownBranchMassSum + (retainedMass ?? 0) > totalMass + EPS);
  if (overallocated) add("error", "split-overallocated", "actual.workup", "已记录分支与留存超过完整母液（分支比例合计 " + pct(knownFractionSum) + "）；未自动归一化，需核对分料记录。");
  if (residualMass != null && residualMass > EPS)
    add("warning", "mass-residual", "actual.workup", "质量闭合差 " + fmt(residualMass) + " g 尚未说明；未分摊给任何分支。");
  if (basis === "fraction" && remainingFraction != null && remainingFraction > EPS)
    add("info", "fraction-leftover", "actual.workup", "分支以完整母液为分母，分支之外尚有 " + pct(remainingFraction) + "（包含留存及未分配部分）。");
  const nominalWater = p.workupDeductWater === false ? 0 : c.monoAMmol == null ? null : finite(c.monoAMmol * 18.015 / 1000);
  if (c.monomerMass != null && nominalWater != null && c.monomerMass < nominalWater) {
    add("error", "negative-theoretical", "plan", "理论净树脂质量为负；请核对配方名义化学量。相关理论产品和按产品/理论树脂计算的用量保持未知。");
    c.monomerMass = null;
  }
  if (designs.length && (p.calculationProfile === 'generic-mass' ? nonnegative(p.theoreticalProductMass) == null : (c.monomerMass == null || (p.workupDeductWater !== false && c.monoAMmol == null))))
    add("warning", "missing-chemistry", "plan", "名义反应物核算不足，理论树脂/产品估计未知；仍可按有效实测分料质量计算质量基准的稀释、沉降。");
  const plannedCalculation = cleanFinite(calcBranches(p, c, lib));
  const branches = observations.map((o, i) => {
    const design = p.workupBranches[i];
    const one = { ...p, workupBranches: [{ ...clone(design), fraction: o.fraction }] };
    const actualCalc = { ...c, totalMass };
    delete actualCalc.workupFeedMass; delete actualCalc.processAdditions;
    const derived = calcBranches(one, actualCalc, lib).branches[0];
    // A measured portion is usable even when the mother total / chemistry is unknown.
    // Only replace mass-dependent operations; acidBasis='total' always retains the FULL nominal acid.
    const portionChemistry = (v) => v != null && o.fraction != null ? finite(v * o.fraction) : null;
    const massOnly = calcBranches({ ...one, ...(p.calculationProfile === 'generic-mass' ? {theoreticalProductMass:portionChemistry(nonnegative(p.theoreticalProductMass))} : {}), workupBranches: [{ ...clone(design), fraction: 1 }] }, { totalMass: o.portionMass, monomerMass: portionChemistry(c.monomerMass), monoAMmol: portionChemistry(c.monoAMmol), catalystMmol: design.quench?.acidBasis === "total" ? c.catalystMmol : portionChemistry(c.catalystMmol) }, lib).branches[0];
    derived.portionMass = o.portionMass;
    derived.rows = derived.rows.filter((r) => r.key !== "portion");
    if (o.portionMass != null) derived.rows.unshift({ key: "portion", label: "本支反应液", amount: o.portionMass, unit: "g", note: basis === "mass" ? "实测分料质量" : "录入比例 × 实测母液总质量" });
    const replaceOperation = (key) => {
      derived[key] = massOnly[key];
      const row = massOnly.rows.find((r) => r.key === key);
      if (row) derived.rows = derived.rows.map((r) => r.key === key ? row : r);
    };
    if (design.dilution?.unit === "g/g-portion") replaceOperation("dilution");
    const precipUnit = design.precipitation?.unit;
    if (["g/g-portion", "g/g-diluted"].includes(precipUnit) || (precipUnit === "v/v-dilution" && design.dilution?.unit === "g/g-portion")) replaceOperation("precipitation");
    if (!(derived.theoreticalMass >= 0)) derived.theoreticalMass = null;
    if (derived.productMass != null && derived.productMass < 0) derived.productMass = null;
    const chemistryReady = homogeneousConfirmed && o.fraction != null && !overallocated;
    const provisional = !chemistryReady;
    if (derived.quench) {
      const q = derived.quench;
      q.estimatedMass = finite(q.mass);
      q.estimatedAcidMmol = finite(q.acidMmol);
      q.estimatedMmol = finite(q.mmol);
      q.estimatedCarrierMass = finite(q.carrierMass);
      q.estimatedSolutionMass = finite(q.solutionMass);
      q.actionable = chemistryReady && q.mass != null && (!own(q, "solutionMass") || q.solutionMass != null);
      q.acidBasis = design.quench.acidBasis === "total" ? "total" : "branch";
      q.assumption = "配方名义酸；未测定分料后残余酸";
      if (!q.actionable) { q.mass = null; q.acidMmol = null; q.mmol = null; q.carrierMass = null; q.solutionMass = null; }
      derived.rows = derived.rows.map((r) => ["quench", "quench-solvent"].includes(r.key) ? { ...r, amount: r.key === "quench" ? q.mass : q.carrierMass, estimatedAmount: r.key === "quench" ? q.estimatedMass : q.estimatedCarrierMass, actionable: q.actionable, provisional: !q.actionable, note: r.note + "；" + q.assumption + (!q.actionable ? "；暂估，不可作为执行称量" : "；按已确认均匀分料假设") } : r);
      if (c.catalystMmol == null) add("warning", "missing-nominal-acid", o.path, (design.code ?? "") + " 支名义催化酸未知，无法核算终止剂。", o.id);
    }
    const provisionalYieldFraction = o.productMass != null && derived.theoreticalMass > 0 ? finite(o.productMass / derived.theoreticalMass) : null;
    const yieldFraction = chemistryReady ? provisionalYieldFraction : null;
    if (provisionalYieldFraction > 1 + EPS) add("warning", "yield-over-100", o.path + ".productMass", (design.code ?? "") + " 支产品质量超过本支名义理论树脂（收率 " + pct(provisionalYieldFraction) + "）；请核对干燥、纯度与理论口径，未截断数值。", o.id);
    for (const message of derived.issues) add("warning", "branch-calculation", o.path, (design.code ?? "") + " 支：" + message, o.id);
    const planned = plannedCalculation.branches[i];
    const actualRows = derived.rows.map((r) => {
      const chemistryDependent = r.key === "product" || (r.key === "dilution" && /g-(?:product|theoretical)/.test(design.dilution?.unit)) || (r.key === "precipitation" && (/g-(?:product|theoretical)/.test(precipUnit) || (["v/v-dilution", "g/g-diluted"].includes(precipUnit) && /g-(?:product|theoretical)/.test(design.dilution?.unit)) || (precipUnit === "g/g-diluted" && design.quench?.enabled))) || r.key.startsWith("wash-") && /g-(?:product|theoretical)/.test(design.washes?.find((w) => "wash-" + w.id === r.key)?.unit);
      return { ...r, provisional: r.provisional ?? (chemistryDependent && provisional), assumption: chemistryDependent ? "名义配方按均匀分料比例分配；预计产品仍使用设计收率假设" : "" };
    });
    const keys = [...new Set([...(planned?.rows ?? []).map((r) => r.key), ...actualRows.map((r) => r.key)])];
    const rows = keys.map((key) => {
      const a = actualRows.find((r) => r.key === key), pl = planned?.rows.find((r) => r.key === key);
      const isProduct = key === "product";
      return { key, label: isProduct ? "产品质量" : a?.label ?? pl?.label, plannedAmount: pl?.amount ?? null, plannedUnit: pl?.unit ?? a?.unit ?? "", amount: isProduct ? o.productMass : a?.amount ?? null, estimatedAmount: a?.estimatedAmount ?? null, unit: a?.unit ?? pl?.unit ?? "", note: isProduct ? "设计列为预计产品假设，实际列仅为实测产品" : [a?.note, a?.assumption].filter(Boolean).join("；"), provisional: isProduct ? false : a?.provisional === true, actionable: a?.actionable ?? false };
    });
    if (!rows.some((r) => r.key === "product")) rows.push({ key: "product", label: "产品质量", plannedAmount: null, plannedUnit: "g", amount: o.productMass, estimatedAmount: null, unit: "g", note: "实测产品；设计预计收率未填写", provisional: false, actionable: false });
    const chargeComparisons = inferActualRatios(compareCharges(exp.actual?.charges, designs[i], rows, derived, add, o.path), designs[i], derived, p, lib, chemistryReady);
    return { chargeComparisons, id: o.id, code: designs[i].code, name: designs[i].name, plannedFraction: fraction(designs[i].fraction), fraction: o.fraction, measuredMass: o.mass, portionMass: o.portionMass, productMass: o.productMass, theoreticalMass: derived.theoreticalMass, expectedProductMass: derived.productMass, yieldFraction, provisionalYieldFraction, provisional, source: clone(o.record.source ?? null), notes: String(o.record.notes ?? ""), planned, calculation: { ...derived, rows: actualRows }, rows, issues: issues.filter((issue) => issue.branchId === o.id) };
  });
  const complete = branches.length > 0 && branches.every((b) => b.fraction != null);
  const split = { basis, totalMass, retainedMass, knownBranchMassSum, branchMassSum, knownFractionSum, fractionSum, remainingFraction, retainedFraction, residualMass, residualFraction, homogeneousConfirmed, complete, overallocated, balanced: residualMass == null ? null : Math.abs(residualMass) <= EPS };
  const calculation = { ...plannedCalculation, totalMass, branches: branches.map((b) => b.calculation), issues };
  return cleanFinite({ available: branches.length > 0, branches, issues, split, calculation, plannedCalculation });
}

/** Pure Excel/report rows. Numeric cells remain null when unknown; materials are per-cycle + totals. */
export function actualWorkupRows(exp = {}, calc = {}, materials = []) {
  const result = calcActualWorkup(exp, calc, materials);
  const mother = [
    ["mother-total", "分料前母液实测总质量", result.split.totalMass],
    ["retained", "实测留存质量", result.split.retainedMass],
    ["residual", "未说明质量闭合差", result.split.residualMass],
  ].map(([key, label, amount]) => ({ branchId: null, branchScope: "mother", key, label, plannedAmount: null, amount, unit: "g", note: "仅使用实测母液口径", provisional: false }));
  return [...mother, ...result.branches.flatMap((b) => {
    const common = { branchId: b.id, branchScope: b.code, branchName: b.name, source: clone(b.source), homogeneousConfirmed: result.split.homogeneousConfirmed };
    const rows = b.rows.map((r) => {
      const wash = r.key.startsWith("wash-") ? b.calculation.washes.find((w) => "wash-" + w.id === r.key) : null;
      const plannedWash = r.key.startsWith("wash-") ? b.planned.washes.find((w) => "wash-" + w.id === r.key) : null;
      return { ...common, ...r, unit: r.unit.replace(/\s*×.*/, ""), perCycleAmount: wash ? r.amount : null, cycles: wash?.cycles ?? null, amount: wash ? (r.amount == null || wash.cycles == null ? null : finite(r.amount * wash.cycles)) : r.amount, plannedAmount: plannedWash ? (r.plannedAmount == null || plannedWash.cycles == null ? null : finite(r.plannedAmount * plannedWash.cycles)) : r.plannedAmount };
    });
    return [
      { ...common, key: "fraction", label: "占完整母液比例", plannedAmount: b.plannedFraction, amount: b.fraction, unit: "fraction", provisional: false },
      ...rows,
      ...b.chargeComparisons.map((r) => ({ ...common, key: "actual-charge:" + r.key, label: r.label + "（实际投入）", amount: r.actualAmount, recommendedAmount: r.recommendedAmount, deviationAmount: r.deviationAmount, deviationFraction: r.deviationFraction, actualRatio: r.actualRatio, ratioUnit: r.ratioUnit, ratioNote: r.ratioNote, actualSolutionPct: r.actualSolutionPct ?? null, unit: r.unit, provisional: r.provisional, note: r.note, chargeId: r.chargeId })),
      { ...common, key: "theoretical", label: "本支名义理论树脂", plannedAmount: b.planned.theoreticalMass, amount: b.theoreticalMass, unit: "g", provisional: b.provisional, note: "名义反应物按均匀分料比例分配；缩合水遵循设计口径" },
      { ...common, key: "actual-yield", label: "实测产品 / 本支名义理论树脂", plannedAmount: result.plannedCalculation.yieldFraction, amount: b.yieldFraction, estimatedAmount: b.yieldFraction == null ? b.provisionalYieldFraction : null, unit: "fraction", provisional: b.yieldFraction == null, note: "实测产品质量为分子；名义理论树脂、均匀分料和产品干燥/纯度为收率解释前提" },
    ];
  })];
}

const branchButton = (b) => `<button type="button" class="wa-branch-button" data-workup-actual-select="${esc(b.id)}"><strong>${esc(b.code)} ${esc(b.name)}</strong><span>设计 ${pct(b.plannedFraction)} · 实际 ${pct(b.fraction)}</span></button>`;
function overview(result) {
  return `<div class="wa-mother-flow"><button type="button" class="wa-mother" data-workup-actual-select="mother"><strong>分料前母液</strong><span>实测 ${fmt(result.split.totalMass)} g</span><small>${result.split.basis === "mass" ? "质量分料口径" : "完整母液比例口径"}</small></button><span class="wa-arrow" aria-hidden="true">→</span><div class="wa-branch-buttons">${result.branches.map(branchButton).join("")}</div></div>`;
}
function comparison(result) {
  const scale = Math.max(1, ...result.branches.flatMap((b) => [b.plannedFraction ?? 0, b.fraction ?? 0]));
  const bar = (share, planned, label) => `<span class="wa-mark-hit" tabindex="0" aria-label="${esc(label)} ${pct(share)}"><span class="wa-track"><span class="wa-bar ${planned ? "wa-plan" : "wa-observed"}" style="width:${share == null ? 0 : share / scale * 100}%"></span></span><span class="wa-direct">${pct(share)}</span><span class="wa-tooltip" role="tooltip">${esc(label)} · ${pct(share)}</span></span>`;
  return `<div class="wa-legend"><span><i class="wa-plan"></i>设计比例</span><span><i class="wa-observed"></i>实际比例</span><small>共同基准：完整母液 = 100%；未知显示 —</small></div><div class="wa-table-wrap"><table class="wa-table wa-fractions"><caption>设计与实际分料对照（% 均相对于完整母液）</caption><thead><tr><th>分支</th><th>设计比例</th><th>实际比例</th><th>实际反应液 / g</th><th>实测产品 / g</th></tr></thead><tbody>${result.branches.map((b) => `<tr><th><button type="button" data-workup-actual-select="${esc(b.id)}">${esc(b.code)} ${esc(b.name)}</button></th><td>${bar(b.plannedFraction, true, (b.code ?? "") + " 设计比例")}</td><td>${bar(b.fraction, false, (b.code ?? "") + " 实际比例")}</td><td>${fmt(b.portionMass)}</td><td>${fmt(b.productMass)}</td></tr>`).join("")}</tbody></table></div>`;
}
function splitSummary(result) {
  const s = result.split;
  return `<p class="wa-balance">分支质量合计 <strong>${fmt(s.branchMassSum)} g</strong>${s.branchMassSum == null ? `（已知项 ${fmt(s.knownBranchMassSum)} g）` : ""} · 分支比例合计 <strong>${pct(s.fractionSum)}</strong>${s.fractionSum == null ? `（已知项 ${pct(s.knownFractionSum)}）` : ""}<br>分支以外 ${pct(s.remainingFraction)}（包含留存及未分配部分） · 质量闭合差 <strong>${fmt(s.residualMass)} g</strong> = 母液总质量 − 各支 − 留存</p>`;
}
function issuesHtml(result) {
  return result.issues.length ? `<ul class="wa-issues">${result.issues.map((i) => `<li data-severity="${esc(i.severity)}"><strong>${i.severity === "error" ? "需修正" : i.severity === "warning" ? "待核对" : "说明"}</strong> ${esc(i.message)}</li>`).join("")}</ul>` : '<p class="wa-note">当前分料记录已闭合；化学量仍遵循名义配方假设。</p>';
}
function chargeTable(b) {
  if (!b.chargeComparisons.length) return "";
  return `<p class="wa-note">本支实际投入偏差：建议用量来自上述重算，实际投入来自批记录；只比较同一分支、唯一操作与相同单位。</p><div class="wa-table-wrap"><table class="wa-table"><thead><tr><th>材料 / 操作</th><th>重算建议用量（总量）</th><th>实际投入</th><th>相对建议偏差</th></tr></thead><tbody>${b.chargeComparisons.map((r) => `<tr><th>${esc(r.label)}</th><td>${fmt(r.recommendedAmount)} ${esc(r.unit)}${r.provisional ? '<small class="wa-estimate">暂估，前提待确认</small>' : ""}</td><td>${fmt(r.actualAmount)} ${esc(r.unit)}</td><td>${r.deviationFraction == null ? "—" : (r.deviationFraction > 0 ? "+" : "") + pct(r.deviationFraction)}<small class="wa-note">${esc(r.note)}</small><small class="wa-note">实投反算 ${fmt(r.actualRatio)} ${esc(r.ratioUnit)}${r.key === "quench" && r.actualSolutionPct != null ? `；实配 ${fmt(r.actualSolutionPct)} wt%` : ""}。${esc(r.ratioNote)}</small></td></tr>`).join("")}</tbody></table></div>`;
}
function resultsHtml(b, result) {
  if (!b) return "";
  return `<div class="wa-result-head"><strong>${esc(b.code)} ${esc(b.name)} · 用量对照</strong><span class="wa-status">${b.provisional ? "暂估 · 母液均匀性未确认" : "已确认均匀 · 名义配方估算"}</span></div><p class="wa-note">本支名义理论树脂 ${fmt(b.theoreticalMass)} g；预计产品 ${fmt(b.expectedProductMass)} g（设计收率假设 ${pct(result.calculation.yieldFraction)}）。<br>实测产品 / 名义理论树脂：${b.yieldFraction == null ? "—" + (b.provisionalYieldFraction == null ? "" : `（暂估 ${pct(b.provisionalYieldFraction)}，前提待确认）`) : pct(b.yieldFraction)}。理论量依赖均匀分料与名义化学口径；产品干燥/纯度须另行核对。</p><div class="wa-table-wrap"><table class="wa-table"><thead><tr><th>项目</th><th>设计用量</th><th>按实际分料重算 / 实测产品</th><th>依据</th></tr></thead><tbody>${b.rows.map((r) => `<tr><th>${esc(r.label)}</th><td>${fmt(r.plannedAmount)} ${esc(r.plannedUnit)}</td><td>${fmt(r.amount)} ${esc(r.unit)}${r.provisional ? '<small class="wa-estimate">暂估，均匀性待确认</small>' : ""}${r.estimatedAmount != null && r.amount == null ? `<small class="wa-estimate">暂估 ${fmt(r.estimatedAmount)} ${esc(r.unit)}；不可作为执行称量</small>` : ""}</td><td>${esc(r.note)}</td></tr>`).join("")}</tbody></table></div>${b.source == null ? "" : `<p class="wa-note">历史分料来源 ${esc(sourceText(b.source))}；该来源不构成酸含量实测证据。</p>`}${chargeTable(b)}`;
}
function numericInput(label, path, value, { branchId, unit = "g", max, scale = 1, readonly = false } = {}) {
  const n = number(value);
  return `<label class="wa-field"><span>${esc(label)}</span><span class="wa-input-unit"><input type="number" step="any" min="0" ${max == null ? "" : `max="${max}"`} data-workup-actual="${esc(path)}" ${branchId == null ? "" : `data-workup-actual-branch="${esc(branchId)}"`} ${scale === 1 ? "" : `data-workup-actual-scale="${scale}"`} value="${n == null ? "" : esc(n / scale)}" ${readonly ? "readonly" : ""}><span>${esc(unit)}</span></span></label>`;
}

function removedBranchRecords(exp) {
  const active = new Set((exp.plan?.workupBranches ?? []).map(branch => branch.id));
  const records = Object.entries(exp.actual?.workup?.branches ?? {}).filter(([id]) => !active.has(id));
  if (!records.length) return '';
  return `<details class="fold" data-removed-branch-records><summary>已移除分支的实际记录（${records.length}）</summary><p class="wa-note">这些记录不计入当前方案。恢复原分支可重新关联，也可继续修订或删除记录。</p>${records.map(([id, record]) => `<section class="wa-branch-editor"><h4>${esc(id)}</h4><div class="wa-branch-fields">${numericInput('实际分料净质量', 'branches.mass', record.mass, {branchId:id})}${numericInput('占完整母液比例', 'branches.fraction', record.fraction, {branchId:id,unit:'%',max:100,scale:.01})}${numericInput('实测产品质量', 'branches.productMass', record.productMass, {branchId:id})}<label class="wa-field">备注<textarea data-workup-actual="branches.notes" data-workup-actual-branch="${esc(id)}">${esc(record.notes)}</textarea></label></div><button type="button" class="btn small ghost danger" data-delete-workup-record="${esc(id)}">删除此实际记录</button></section>`).join('')}</details>`;
}
/** String renderer; no IDs and no state mutation, safe to mount in multiple experiment panels. */
export function renderActualWorkup(exp = {}, calc = {}, materials = [], { compact = false } = {}) {
  const result = calcActualWorkup(exp, calc, materials), w = exp.actual?.workup ?? {};
  const active = selected.get(exp) ?? result.branches[0]?.id;
  if (!result.available) return `<section class="workup-actual ${compact ? "wa-compact" : ""}" data-workup-actual-root="${esc(exp.id)}"><h3>实际后处理分料</h3><p>尚无后处理分支设计。</p><button type="button" data-tab="branches">建立分支设计</button>${removedBranchRecords(exp)}</section>`;
  return `<section class="workup-actual ${compact ? "wa-compact" : ""}" data-workup-actual-root="${esc(exp.id)}"><div class="wa-heading"><h3>母批分料 · 设计与实际</h3><button type="button" data-tab="branches">编辑分支设计</button></div><div data-wa-slot="overview">${overview(result)}</div><div class="wa-global-controls"><label class="wa-field"><span>实际分料口径</span><select data-workup-actual="splitBasis"><option value="mass" ${result.split.basis === "mass" ? "selected" : ""}>实测质量（g）</option><option value="fraction" ${result.split.basis === "fraction" ? "selected" : ""}>占完整母液比例（%）</option></select></label>${numericInput("分料前母液实测总质量", "totalMass", w.totalMass)}${numericInput("实测留存质量（无留存填 0）", "retainedMass", w.retainedMass)}<label class="wa-homogeneous"><input type="checkbox" data-workup-actual="homogeneousConfirmed" ${result.split.homogeneousConfirmed ? "checked" : ""}>已确认分料前母液均匀，名义化学量可按比例分配</label></div><p class="wa-note">母液总质量须在分料前实测。质量模式按本支净质量 / 母液总质量；比例模式保留录入占比。切换口径保留各自原始记录，缺失值保持未知。</p><div data-wa-slot="split">${splitSummary(result)}</div><div data-wa-slot="comparison">${comparison(result)}</div><div data-wa-slot="issues" aria-live="polite">${issuesHtml(result)}</div><div class="wa-branch-editors">${result.branches.map((b) => {
    const record = own(w.branches, b.id) ? w.branches[b.id] ?? {} : {};
    return `<section class="wa-branch-editor" data-workup-actual-panel="${esc(b.id)}" ${b.id === active ? "" : "hidden"}><h4>${esc(b.code)} ${esc(b.name)} · 实际记录</h4><div class="wa-branch-fields"><span data-wa-basis="mass" ${result.split.basis === "mass" ? "" : "hidden"}>${numericInput("本支实测分料净质量", "branches.mass", record.mass, { branchId: b.id })}</span><span data-wa-basis="fraction" ${result.split.basis === "fraction" ? "" : "hidden"}>${numericInput("本支占完整母液", "branches.fraction", record.fraction, { branchId: b.id, unit: "%", max: 100, scale: 0.01 })}</span>${numericInput("本支实测产品质量", "branches.productMass", record.productMass, { branchId: b.id })}<label class="wa-field wa-notes"><span>本支实际备注</span><textarea rows="2" data-workup-actual="branches.notes" data-workup-actual-branch="${esc(b.id)}">${esc(record.notes)}</textarea></label></div><div data-wa-results="${esc(b.id)}">${resultsHtml(b, result)}</div></section>`;
  }).join("")}</div>${removedBranchRecords(exp)}</section>`;
}

const valueOf = (v) => typeof v === "function" ? v() : v;
function context(ctx) {
  return { exp: valueOf(ctx.experiment), calc: valueOf(ctx.calc) ?? {}, materials: valueOf(ctx.state)?.materials ?? [] };
}
function rootFor(target, exp) {
  const root = target?.closest?.("[data-workup-actual-root]");
  return root && root.dataset.workupActualRoot === String(exp?.id ?? "") ? root : null;
}
function setSelection(root, id) {
  for (const panel of root.querySelectorAll("[data-workup-actual-panel]")) panel.hidden = panel.dataset.workupActualPanel !== id;
  for (const button of root.querySelectorAll("[data-workup-actual-select]")) button.setAttribute("aria-pressed", String(button.dataset.workupActualSelect === id));
}
function refresh(root, ctx) {
  const { exp, calc, materials } = context(ctx), result = calcActualWorkup(exp, calc, materials);
  const content = { overview: overview(result), split: splitSummary(result), comparison: comparison(result), issues: issuesHtml(result) };
  // Only output slots change: numeric inputs, textareas, selection and caret remain the same nodes.
  for (const slot of root.querySelectorAll("[data-wa-slot]")) slot.innerHTML = content[slot.dataset.waSlot] ?? "";
  for (const output of root.querySelectorAll("[data-wa-results]")) output.innerHTML = resultsHtml(result.branches.find((b) => b.id === output.dataset.waResults), result);
  for (const area of root.querySelectorAll("[data-wa-basis]")) area.hidden = area.dataset.waBasis !== result.split.basis;
  setSelection(root, selected.get(exp) ?? result.branches[0]?.id);
}

/** Parent calls before its own edit handling. Only whitelisted paths inside this root are accepted. */
export function actualWorkupEdit(target, ctx) {
  if (!target?.matches?.("[data-workup-actual]")) return false;
  const { exp } = context(ctx), root = rootFor(target, exp);
  if (!exp || !root) return false;
  const rawPath = target.dataset.workupActual;
  const path = /^(mass|fraction|productMass|notes)$/.test(rawPath) ? "branches." + rawPath : rawPath;
  const branchField = /^branches\.(mass|fraction|productMass|notes)$/.exec(path);
  if (!branchField && !["splitBasis", "totalMass", "retainedMass", "homogeneousConfirmed"].includes(path)) return false;
  const id = target.dataset.workupActualBranch ?? target.dataset.workupBranch;
  if (branchField && !(exp.plan?.workupBranches ?? []).some((b) => b.id === id) && !own(exp.actual?.workup?.branches, id)) return false;
  let value, invalid = false;
  if (path === "splitBasis") {
    if (!["mass", "fraction"].includes(target.value)) return true;
    value = target.value;
  } else if (path === "homogeneousConfirmed") value = target.checked === true;
  else if (branchField?.[1] === "notes") value = target.value;
  else {
    const raw = target.value, n = number(raw), isFraction = branchField?.[1] === "fraction";
    value = n == null ? null : finite(n * (isFraction ? 0.01 : 1));
    invalid = (!missing(raw) && n == null) || target.validity?.badInput === true || (value != null && (value < 0 || (path === "totalMass" && value === 0) || (isFraction && value > 1)));
    if (invalid) value = null;
    target.setCustomValidity?.(invalid ? "请输入有效有限数；质量非负、母液总质量大于 0、比例在 0–100%。" : "");
    target.setAttribute?.("aria-invalid", String(invalid));
  }
  exp.actual ??= {};
  exp.actual.workup ??= { splitBasis: "mass", totalMass: null, retainedMass: null, homogeneousConfirmed: false, branches: {} };
  const w = exp.actual.workup;
  if (branchField) {
    w.branches ??= {};
    if (!own(w.branches, id) || !w.branches[id] || typeof w.branches[id] !== "object") Object.defineProperty(w.branches, id, { value: { mass: null, fraction: null, productMass: null, notes: "" }, enumerable: true, writable: true, configurable: true });
    w.branches[id][branchField[1]] = value;
    // A manual fraction edit supersedes a formula-imported fraction. Keep the evidence as provenance.
    if (branchField[1] === "fraction" && w.branches[id].source != null && w.branches[id].source.kind !== "manual-edit") w.branches[id].source = { kind: "manual-edit", previous: clone(w.branches[id].source) };
  } else w[path] = value;
  ctx.mark?.("修改实际后处理分料", branchField ? id + "." + branchField[1] : path);
  refresh(root, ctx);
  if (invalid) {
    const slot = root.querySelector('[data-wa-slot="issues"]');
    if (slot) { const message = slot.ownerDocument.createElement("p"); message.className = "wa-input-error"; message.textContent = "输入无效：该实际数值保持未知，请修正输入。"; slot.prepend(message); }
  }
  return true;
}

/** Selection is UI-only and never marks or changes the experiment. data-tab navigation stays with parent. */
export function actualWorkupClick(target, ctx) {
  const removal = target?.closest?.('[data-delete-workup-record]');
  if (removal) {
    const { exp } = context(ctx), id = removal.dataset.deleteWorkupRecord;
    if (!rootFor(removal, exp) || !own(exp.actual?.workup?.branches, id)) return false;
    const record = clone(exp.actual.workup.branches[id]);
    delete exp.actual.workup.branches[id];
    const entry = ctx.trash?.('workupRecord', { id, record }, (exp.code ?? '') + ' · ' + id + ' 实际分料', {experimentId:exp.id,code:exp.code});
    ctx.mark?.('删除实际分料记录', id); ctx.render?.();
    ctx.removed?.('实际分料记录已移入回收站', () => {
      Object.defineProperty(exp.actual.workup.branches, id, {value:record,enumerable:true,writable:true,configurable:true});
      const state = valueOf(ctx.state); if (entry?.id && state) state.trash = state.trash.filter(item => item.id !== entry.id);
    });
    return true;
  }
  const button = target?.closest?.("[data-workup-actual-select]");
  if (!button) return false;
  const { exp } = context(ctx), root = rootFor(button, exp);
  if (!exp || !root) return false;
  const id = button.dataset.workupActualSelect;
  if (id === "mother") {
    root.querySelector('[data-workup-actual="totalMass"]')?.focus();
    return true;
  }
  if (!(exp.plan?.workupBranches ?? []).some((b) => b.id === id)) return false;
  selected.set(exp, id);
  setSelection(root, id);
  root.querySelectorAll("[data-workup-actual-panel]").forEach((panel) => { if (panel.dataset.workupActualPanel === id) panel.scrollIntoView?.({ block: "nearest", behavior: "smooth" }); });
  return true;
}
