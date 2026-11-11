// Post-reaction branch design. The reaction liquor is split into branches (A/B/…); each branch sizes its own
// neutralization, dilution, precipitation, washes and drying. Amounts follow the later Excel SOPs (R100-26…38):
//   LAB theoretical resin = monomer mass − n(monoA) × 18.015 g/mol (one water per condensation, optional)
//   generic theoretical product = explicitly entered plan.theoreticalProductMass only
//   expected product per branch = theoretical resin × expected yield × branch fraction
//   quench = catalyst mmol × branch acid share × eq
const WATER_MW = 18.015;
const num = (v) => (v === "" || v == null || typeof v === "boolean" || !Number.isFinite(Number(v)) ? null : Number(v));
const uid = () => (globalThis.crypto?.randomUUID ? crypto.randomUUID() : "b-" + Math.random().toString(36).slice(2));
const r = (v, d = 3) => (v == null || !Number.isFinite(v) ? "—" : Number(v.toFixed(d)).toString());

export const DILUTION_UNITS = [
  ["mL/g-product", "mL / g 预计产品"],
  ["mL/g-theoretical", "mL / g 理论净树脂"],
  ["g/g-portion", "g / g 本支反应液"],
  ["mL", "mL（固定量）"],
  ["g", "g（固定量）"],
];
export const PRECIP_UNITS = [
  ["v/v-dilution", "倍体积（相对稀释液）"],
  ["mL/g-product", "mL / g 预计产品"],
  ["mL/g-theoretical", "mL / g 理论净树脂"],
  ["g/g-portion", "g / g 本支反应液"],
  ["g/g-diluted", "g / g 稀释后溶液"],
  ["mL", "mL（固定量）"],
  ["g", "g（固定量）"],
];
export const WASH_UNITS = [
  ["mL/g-product", "mL / g 预计产品"],
  ["mL/g-theoretical", "mL / g 理论净树脂"],
  ["mL", "mL（固定量）"],
  ["g", "g（固定量）"],
];

export function newBranch(code = "A", name = "", fraction = 1, extra = {}) {
  return {
    id: uid(),
    code,
    name,
    fraction,
    quench: { enabled: false, materialId: "", eq: 1.05, acidBasis: "branch", solutionPct:100, solventId:"", tempC:null, stirMinutes:null },
    dilution: { materialId: "", amount: null, unit: "mL/g-product" },
    precipitation: { materialId: "", amount: null, unit: "v/v-dilution", temp: "20–25 °C" },
    washes: [],
    dry: { tempC: null, hours: null, vacuum: true },
    notes: "",
    ...extra,
  };
}

/** Two-branch neutralized / non-neutralized control, the most common late-route design. */
export function pairedBranches(plan = {}) {
  const quenchId = plan.quenchId || (plan.calculationProfile === "generic-mass" ? "" : "DMAP");
  const a = newBranch("A", "中和", 0.5),
    b = newBranch("B", "不中和", 0.5);
  a.quench = { enabled: true, materialId: quenchId, eq: num(plan.quenchEq) ?? (plan.calculationProfile === "generic-mass" ? null : 1.05), acidBasis: "branch" };
  return [a, b];
}

export function singleBranch(plan = {}) {
  const branch = newBranch("A", "主路线", 1);
  if (["neutralize", "quench"].includes(plan.termination))
    branch.quench = { enabled: true, materialId: plan.quenchId || (plan.calculationProfile === "generic-mass" ? "" : "TEA"), eq: num(plan.quenchEq) ?? (plan.calculationProfile === "generic-mass" ? null : 1.05), acidBasis: "branch" };
  const w = plan.workup || {};
  if (num(w.dilutionMass) != null) branch.dilution = { materialId: "", amount: num(w.dilutionMass), unit: "g" };
  if (num(w.antisolventRatio) != null)
    branch.precipitation = {
      materialId: "",
      amount: num(w.antisolventRatio),
      unit: w.antisolventBasis === "diluted-solution" ? "g/g-diluted" : "g/g-portion",
      temp: "",
    };
  if (num(w.washMassPerCycle) != null)
    branch.washes.push({ id: uid(), label: "洗涤", materialId: "", amount: num(w.washMassPerCycle), unit: "g", cycles: num(w.washCycles) ?? 1 });
  if (w.branch) branch.name = w.branch;
  return [branch];
}

/** Branches already named in a historical route (plan.branches) without quantities. */
export function branchesFromRoute(plan = {}) {
  const list = Array.isArray(plan.branches) ? plan.branches : [];
  if (list.length < 2) return singleBranch(plan);
  return list.map((b, i) => {
    const code = b.code ?? String(b.id ?? "").match(/-([^\s-]+)$/)?.[1]?.replace(/^\d+/, "") ?? String.fromCharCode(65 + i);
    const branch = newBranch(code || String.fromCharCode(65 + i), b.name ?? "", Object.hasOwn(b, "splitFraction") ? num(b.splitFraction) : 1 / list.length);
    branch.id = b.id ?? branch.id;
    if (/中和/.test(b.name ?? "") && !/不中和/.test(b.name ?? ""))
      branch.quench = { enabled: true, materialId: plan.quenchId || (plan.calculationProfile === "generic-mass" ? "" : "DMAP"), eq: num(plan.quenchEq) ?? (plan.calculationProfile === "generic-mass" ? null : 1.05), acidBasis: "branch" };
    return branch;
  });
}

const materialToken = (text, materials) => {
  // Only the numerator names the material: “沉降MeOH/THF” is methanol measured against the THF volume.
  const t = String(text).split("/")[0];
  const aliases = [
    [/SolvA/i, "SolvA"],
    [/SolvC(?!A)/i, "SolvC"],
    [/THF/i, "THF"],
    [/MeOH|甲醇/i, "MeOH"],
    [/HEP|正庚烷|庚烷/i, "heptane"],
    [/IPA|异丙醇/i, "IPA"],
    [/DMAP/i, "DMAP"],
    [/TEA|三乙胺/i, "TEA"],
    [/吡啶|pyridine/i, "pyridine"],
    [/BaseQ/i, "BaseQ"],
  ];
  for (const [pattern, id] of aliases) if (pattern.test(t) && materials.some((m) => m.id === id)) return id;
  return "";
};
const denominatorUnit = (name, unit) =>
  /\/THF/.test(name) || /v\/v/i.test(unit ?? "")
    ? "v/v-dilution"
    : /理论净树脂/.test(name)
      ? "mL/g-theoretical"
      : /\/(预计)?(分离)?产品/.test(name)
        ? "mL/g-product"
        : null;

/**
 * Read the source SOP parameter table (plan.parameterEvidence) and build branches with the same ratios.
 * Returns {branches, yieldFraction, notes} — nothing is written to the plan here.
 */
export function recognizeFromSource(plan = {}, materials = []) {
  const evidence = Array.isArray(plan.parameterEvidence) ? plan.parameterEvidence : [];
  const branches = branchesFromRoute(plan),
    notes = [];
  let yieldFraction = null,
    dilution = null,
    precipitation = null;
  const washes = [],
    quenchByCode = {};
  let quenchAll = null;
  for (const row of evidence) {
    const name = String(row.name ?? row.label ?? ""),
      value = num(row.value),
      unit = row.unit ?? "";
    if (value == null) continue;
    if (/预计分离收率|分离收率/.test(name)) {
      if (value > 0 && value <= 1) yieldFraction = value;
      continue;
    }
    if (/体积|质量|^每支|预计产品\(g\)|预计分离产品|预计产品$/.test(name) && !/\//.test(name)) continue;
    const branchQuench = name.match(/^([A-Z])支(.+?)\/(p-)?TsOH/i);
    if (branchQuench) {
      quenchByCode[branchQuench[1]] = { materialId: materialToken(branchQuench[2], materials), eq: value };
      continue;
    }
    const allQuench = name.match(/^(.+?)\/(p-)?TsOH/i);
    if (allQuench) {
      if (value > 0) quenchAll = { materialId: materialToken(allQuench[1], materials), eq: value };
      continue;
    }
    const den = denominatorUnit(name, unit);
    if (!den) continue;
    if (/打浆|浆洗/.test(name) || /^(IPA|MeOH|甲醇|异丙醇)\/(预计)?产品/.test(name)) {
      const label = name.match(/(打浆|浆洗)\s*\d*/)?.[0] ?? (/(IPA|异丙醇)/.test(name) ? "IPA洗涤" : "MeOH洗涤");
      washes.push({
        id: uid(),
        label,
        materialId: materialToken(name, materials) || (materials.some((m) => m.id === "MeOH") ? "MeOH" : ""),
        amount: value,
        unit: den === "v/v-dilution" ? "mL/g-product" : den,
        cycles: 1,
      });
      continue;
    }
    if (/沉降|HEP\/|正庚烷\//.test(name)) {
      precipitation = { materialId: materialToken(name, materials), amount: value, unit: den, temp: "20–25 °C" };
      continue;
    }
    if (/^THF\//.test(name) || /稀释/.test(name)) {
      dilution = { materialId: "THF", amount: value, unit: den === "v/v-dilution" ? "mL/g-product" : den };
    }
  }
  for (const branch of branches) {
    if (dilution) branch.dilution = { ...dilution };
    if (precipitation) branch.precipitation = { ...precipitation };
    branch.washes = washes.map((w) => ({ ...w, id: uid() }));
    const q = quenchByCode[branch.code] ?? (branches.length === 1 ? quenchAll : null);
    if (q) branch.quench = { enabled: true, materialId: q.materialId || plan.quenchId || "", eq: q.eq, acidBasis: "branch" };
  }
  const baseSolution = evidence.find(row => /(?:DMAP|TEA|终止剂).*预溶.*固含|终止剂.*液.*浓度/.test(row.name ?? row.label ?? ""));
  if (baseSolution) for (const branch of branches.filter(branch => branch.quench?.enabled)) {
    const value = num(baseSolution.value);
    if (value > 0) branch.quench.solutionPct = value <= 1 ? value * 100 : value;
    const carrier = evidence.find(row => /(?:SolvA|THF).*预溶|预溶.*(?:SolvA|THF)/.test(row.name ?? row.label ?? ""));
    branch.quench.solventId = carrier ? materialToken(carrier.name??carrier.label,materials) : /SolvA/.test(plan.sourceSop?.file??"") ? "SolvA" : "THF";
  }
  if (!evidence.length) notes.push("该路线没有源参数表，已按路线分支建立空白方案。");
  return { branches, yieldFraction, notes };
}

function convert(amount, from, material) {
  // Returns {mL, g}; mass↔volume only when the material library has a density (g/mL).
  const density = num(material?.density);
  if (amount == null) return { mL: null, g: null };
  if (from === "mL") return { mL: amount, g: density > 0 ? amount * density : null };
  return { g: amount, mL: density > 0 ? amount / density : null };
}

/** Size every branch from the current recipe calculation. Pure function. */
export function calcBranches(plan = {}, calc = {}, materials = []) {
  const generic = plan.calculationProfile === "generic-mass";
  const branches = Array.isArray(plan.workupBranches) ? plan.workupBranches : [];
  const lib = new Map((materials || []).map((m) => [m.id, m]));
  const issues = [];
  const totalMass = num(Object.hasOwn(calc, 'workupFeedMass') ? calc.workupFeedMass : calc.totalMass),
    monomerMass = num(calc.monomerMass),
    monoAMmol = num(calc.monoAMmol),
    catalystMmol = generic ? null : num(calc.catalystMmol);
  const deductWater = !generic && plan.workupDeductWater !== false;
  const waterMass = deductWater ? (monoAMmol == null ? null : (monoAMmol * WATER_MW) / 1000) : 0;
  const explicitTheoretical = num(plan.theoreticalProductMass);
  const theoretical = generic ? explicitTheoretical != null && explicitTheoretical >= 0 ? explicitTheoretical : null : monomerMass == null || waterMass == null ? null : monomerMass - waterMass;
  const yieldFraction = num(plan.expectedYieldFraction);
  const validYield = yieldFraction != null && yieldFraction > 0 && yieldFraction <= 1;
  const expectedProduct = theoretical != null && validYield ? theoretical * yieldFraction : null;
  const fractionSum = branches.reduce((a, b) => a + (num(b.fraction) ?? 0), 0);
  if (branches.length && Math.abs(fractionSum - 1) > 1e-6)
    issues.push({ severity: "warning", message: "各分支分料比例合计为 " + r(fractionSum * 100, 1) + "%，不是 100%。" });
  if (branches.length && totalMass == null) issues.push({ severity: "warning", message: "投料核算未完成，暂不能计算各分支反应液质量。" });
  const name = (id) => lib.get(id)?.name ?? (id || "未指定溶剂");
  const results = branches.map((branch) => {
    const out = { id: branch.id, code: branch.code, name: branch.name, issues: [], rows: [] };
    const rawFraction = num(branch.fraction);
    const fraction = rawFraction != null && rawFraction >= 0 && rawFraction <= 1 ? rawFraction : null;
    if (fraction == null) out.issues.push("分料比例必须在 0–100% 之间。");
    out.fraction = fraction;
    const branchAddition = calc.processAdditions?.branchMasses?.[branch.id];
    const hasBranchAddition = Object.hasOwn(calc.processAdditions?.branchMasses ?? {}, branch.id);
    out.portionMass = totalMass != null && fraction != null && (!hasBranchAddition || branchAddition != null) ? totalMass * fraction + (hasBranchAddition ? branchAddition : 0) : null;
    out.productMass = expectedProduct != null && fraction != null ? expectedProduct * fraction : null;
    out.theoreticalMass = theoretical != null && fraction != null ? theoretical * fraction : null;
    const basisFor = (unit) =>
      unit === "mL/g-product" ? out.productMass : unit === "mL/g-theoretical" ? out.theoreticalMass : unit === "g/g-portion" ? out.portionMass : null;
    if (out.portionMass != null)
      out.rows.push({ key: "portion", label: "本支反应液", amount: out.portionMass, unit: "g", note: r((fraction ?? 0) * 100, 1) + "% 分料" });
    // Neutralization relative to the nominal acid share of this branch.
    if (branch.quench?.enabled) {
      const m = lib.get(branch.quench.materialId);
      const acidShare = branch.quench.acidBasis === "total" ? 1 : fraction;
      const acid = catalystMmol == null || acidShare == null ? null : catalystMmol * acidShare;
      const eq = num(branch.quench.eq);
      const mmol = acid != null && eq != null && eq >= 0 ? acid * eq : null;
      const mw = num(m?.mw), override = plan.materialPurities?.[branch.quench.materialId];
      let purity = num(override == null || override === "" ? m?.purity : override);
      if (!generic && purity == null && plan.calculationPurityBasis !== "strict") {
        purity = 100;
        out.issues.push((m?.name ?? "终止剂") + " 未填纯度，按名义 100% 计算；不是实测纯度。");
      }
      const validPurity = purity != null && purity > 0 && purity <= 100;
      const mass = mmol != null && mw > 0 && validPurity ? (mmol * mw) / 1000 / (purity / 100) : null;
      if (!m) out.issues.push("请选择终止剂。");
      else if (!generic && !(mw > 0)) out.issues.push(m.name + " 缺少分子量，无法计算称量。");
      if (!generic && !validPurity) out.issues.push("终止剂纯度须为 0–100% 之间的正数；严格口径下不能留空。");
      if (generic) out.issues.push("通用质量模式尚未提供明确的酸当量计算基准，终止用量待核算。");
      if (eq == null || eq < 0) out.issues.push("终止当量必须为非负数。");
      const solutionPct = num(branch.quench.solutionPct) ?? 100;
      const validSolution = solutionPct > 0 && solutionPct <= 100;
      const solutionMass = validSolution && mass != null ? mass / (solutionPct / 100) : null;
      const carrierMass = solutionMass == null ? null : solutionMass - mass;
      if (!validSolution) out.issues.push("终止剂溶液质量分数必须大于0且不大于100%。");
      if (solutionPct < 100 && !lib.has(branch.quench.solventId)) out.issues.push("请指定终止剂预溶载液。");
      out.quench = { materialId: branch.quench.materialId, solventId:branch.quench.solventId, acidMmol: acid, eq, mmol, mass, solutionPct, solutionMass, carrierMass };
      if (solutionPct < 100) out.rows.push({key:"quench-solvent",materialId:branch.quench.solventId,label:name(branch.quench.solventId)+"（终止载液）",amount:carrierMass,unit:"g",note:"终止剂质量 / 溶液总质量 = "+solutionPct+" wt%"});
      out.rows.push({
        key: "quench",
        materialId: branch.quench.materialId,
        label: (m?.name ?? "终止剂") + "（终止）",
        amount: mass,
        unit: "g",
        note: generic ? "酸当量计算基准待明确" : (eq ?? "—") + " eq，相对" + (branch.quench.acidBasis === "total" ? "母批全部" : "本支名义") + "酸 " + r(acid, 3) + " mmol",
      });
    }
    const dil = branch.dilution ?? {};
    const dilAmount = num(dil.amount);
    if (dil.amount != null && dil.amount !== "" && !(dilAmount >= 0)) out.issues.push("稀释剂用量必须为非负数。");
    if (dilAmount > 0 && !lib.has(dil.materialId)) out.issues.push("请选择稀释剂物料。");
    if (dilAmount != null && dilAmount > 0) {
      const m = lib.get(dil.materialId);
      let mL = null,
        g = null;
      if (dil.unit === "mL") ({ mL, g } = convert(dilAmount, "mL", m));
      else if (dil.unit === "g") ({ mL, g } = convert(dilAmount, "g", m));
      else if (dil.unit === "g/g-portion") ({ mL, g } = convert(out.portionMass == null ? null : dilAmount * out.portionMass, "g", m));
      else {
        const base = basisFor(dil.unit);
        if (base == null) out.issues.push(generic ? "稀释量基准尚不可计算；请填写理论产品质量，按预计产品计量时还需填写预计分离收率。" : dil.unit === "mL/g-product" ? "稀释量按预计产品计算，请先填写预计分离收率。" : "稀释量基准尚不可计算。");
        ({ mL, g } = convert(base == null ? null : dilAmount * base, "mL", m));
      }
      out.dilution = { materialId: dil.materialId, mL, g };
      out.rows.push({ key: "dilution", label: name(dil.materialId) + "（稀释）", amount: mL ?? g, unit: mL != null ? "mL" : "g", note: unitText(dil) });
    }
    const pre = branch.precipitation ?? {};
    const preAmount = num(pre.amount);
    if (pre.amount != null && pre.amount !== "" && !(preAmount >= 0)) out.issues.push("沉降剂用量必须为非负数。");
    if (preAmount > 0 && !lib.has(pre.materialId)) out.issues.push("请选择沉降剂物料。");
    if (preAmount != null && preAmount > 0) {
      const m = lib.get(pre.materialId);
      let mL = null,
        g = null;
      if (pre.unit === "v/v-dilution") {
        if (out.dilution?.mL == null) out.issues.push("沉降剂按稀释液体积计算，但稀释液体积未知（稀释量以 g 计时需在材料库填写密度）。");
        ({ mL, g } = convert(out.dilution?.mL == null ? null : preAmount * out.dilution.mL, "mL", m));
      } else if (pre.unit === "g/g-portion") ({ mL, g } = convert(out.portionMass == null ? null : preAmount * out.portionMass, "g", m));
      else if (pre.unit === "g/g-diluted") {
        const quenchAdded = out.quench ? out.quench.solutionMass : 0;
        const dilutedMass = out.portionMass == null || quenchAdded == null || (out.dilution && out.dilution.g == null) ? null : out.portionMass + (out.dilution?.g ?? 0) + quenchAdded;
        if (out.dilution && out.dilution.g == null) out.issues.push("稀释后溶液质量未知（稀释剂以 mL 计时需填写密度）。");
        ({ mL, g } = convert(dilutedMass == null ? null : preAmount * dilutedMass, "g", m));
      } else if (pre.unit === "mL") ({ mL, g } = convert(preAmount, "mL", m));
      else if (pre.unit === "g") ({ mL, g } = convert(preAmount, "g", m));
      else {
        const base = basisFor(pre.unit);
        if (base == null) out.issues.push(generic ? "沉降剂基准尚不可计算；请填写理论产品质量，按预计产品计量时还需填写预计分离收率。" : "沉降剂按预计产品计算，请先填写预计分离收率。");
        ({ mL, g } = convert(base == null ? null : preAmount * base, "mL", m));
      }
      out.precipitation = { materialId: pre.materialId, mL, g };
      out.rows.push({
        key: "precipitation",
        label: name(pre.materialId) + "（沉降）",
        amount: mL ?? g,
        unit: mL != null ? "mL" : "g",
        note: unitText(pre),
      });
    }
    out.washes = (branch.washes ?? []).map((w) => {
      const m = lib.get(w.materialId),
        amount = num(w.amount),
        rawCycles = w.cycles == null || w.cycles === "" ? 1 : num(w.cycles),
        cycles = Number.isInteger(rawCycles) && rawCycles >= 0 ? rawCycles : null;
      if (cycles == null) out.issues.push((w.label || "洗涤") + "次数必须为非负整数。");
      if (amount != null && amount < 0) out.issues.push((w.label || "洗涤") + "用量不能为负数。");
      let per = { mL: null, g: null };
      if (amount != null && amount >= 0 && cycles != null) {
        if (w.unit === "mL") per = convert(amount, "mL", m);
        else if (w.unit === "g") per = convert(amount, "g", m);
        else {
          const base = basisFor(w.unit);
          if (base == null) out.issues.push(generic ? (w.label || "洗涤") + "基准尚不可计算；请填写理论产品质量，按预计产品计量时还需填写预计分离收率。" : (w.label || "洗涤") + "按预计产品计算，请先填写预计分离收率。");
          per = convert(base == null ? null : amount * base, "mL", m);
        }
      }
      const item = { id: w.id, label: w.label || "洗涤", materialId: w.materialId, cycles, perCycle: per };
      out.rows.push({
        key: "wash-" + w.id,
        label: name(w.materialId) + "（" + (w.label || "洗涤") + "）",
        amount: per.mL ?? per.g,
        unit: (per.mL != null ? "mL" : "g") + (cycles > 1 ? " × " + cycles : ""),
        note: unitText(w),
      });
      return item;
    });
    const dry = branch.dry ?? {};
    if (num(dry.tempC) != null || num(dry.hours) != null)
      out.rows.push({
        key: "dry",
        label: "干燥",
        amount: null,
        unit: "",
        note: [num(dry.tempC) != null ? dry.tempC + " °C" : null, dry.vacuum ? "真空" : null, num(dry.hours) != null ? dry.hours + " h" : null]
          .filter(Boolean)
          .join(" "),
      });
    if (out.productMass != null)
      out.rows.push({ key: "product", label: "预计产品（设计假设）", amount: out.productMass, unit: "g", note: "收率 " + r(yieldFraction * 100, 1) + "%" });
    return out;
  });
  return { theoretical, ...(generic ? { theoreticalBasis: theoretical == null ? null : "user-entered" } : {}), waterMass, deductWater, yieldFraction: validYield ? yieldFraction : null, expectedProduct, totalMass, branches: results, issues };
}

// The same rows feed the on-screen weighing plan and exported batch records.
export function plannedWorkupCharges(plan = {}, calc = {}) {
  const result = calc.workupBranches;
  const branched = Boolean(plan.workupBranches?.length);
  const generic = plan.calculationProfile === "generic-mass";
  const rows = (calc.rows ?? []).filter(row => generic || !branched || !["quench", "quench-solvent"].includes(row.role))
    .map(row => ({ ...row, amount: generic && row.mass == null ? row.amount : row.mass, unit: generic && row.mass == null ? row.unit : "g", stage: row.stage ?? row.role, ...(row.role==='process-addition'?{workupKey:row.id,planRowId:row.id}:{}), branchScope: row.branchScope ?? row.scope ?? "mother" }));
  if (branched) for (const branch of result?.branches ?? []) {
    for (const row of branch.rows.filter(row => ["quench", "quench-solvent", "dilution", "precipitation"].includes(row.key) || row.key.startsWith("wash-"))) {
      const wash = row.key.startsWith("wash-") ? branch.washes.find(w => "wash-" + w.id === row.key) : null;
      const cycles = wash ? wash.cycles : 1;
      rows.push({ id: branch.id + ":" + row.key, name: branch.code + "路 " + row.label,
        branchId:branch.id, workupKey:row.key, materialId: row.materialId ?? (row.key==='dilution'?branch.dilution?.materialId:row.key==='precipitation'?branch.precipitation?.materialId:wash?.materialId), amount: row.amount == null || cycles == null ? null : row.amount * cycles,
        unit: row.unit.replace(/\s*×.*/, ""), stage: "branch-" + branch.code, branchScope: branch.code,
        notes: [row.note, wash ? "每次 " + r(row.amount) + " " + row.unit.replace(/\s*×.*/, "") + "，共 " + (cycles ?? "待定") + " 次" : ""].filter(Boolean).join("；") });
    }
  }
  return rows;
}

export function workupSignature(plan = {}, materials = []) {
  const keys = ["calculationProfile", "charges", "theoreticalProductMass", "scale", "scaleBasis", "ratio", "monoBId", "monoAs", "catalystId", "catalystMolPct", "solidPct", "solidBasis", "solvents", "feedSolvents", "feedSolventMass", "feedSolventId", "materialPurities", "calculationPurityBasis", "expectedYieldFraction", "workupDeductWater", "quenchTemp", "workupBranches"];
  return JSON.stringify([keys.map(key => plan[key] ?? null), (plan.steps ?? []).filter(step => step.additions?.length).map(step => [step.id,step.branchScope,step.additions]), materials.map(m => [m.id, m.name, m.mw, m.purity, m.density])]);
}

export function unitText(item = {}) {
  const all = [...DILUTION_UNITS, ...PRECIP_UNITS, ...WASH_UNITS];
  const label = all.find(([k]) => k === item.unit)?.[1] ?? item.unit ?? "";
  return item.amount == null || item.amount === "" ? "" : item.amount + " " + label;
}

const fmtAmount = (mLg) => (mLg?.mL != null ? r(mLg.mL, 1) + " mL" : mLg?.g != null ? r(mLg.g, 3) + " g" : "【用量待核算】");

/** Resolve generated operation text from the current structured design; manual paragraphs stay editable. */
export function renderWorkupStep(step, plan = {}, result = {}, materials = []) {
  const id = step.params?.branchId, kind = step.params?.operation;
  if (kind === "source-group") {
    const operation = step.params.operationKind, eligible = (plan.workupBranches ?? []).filter(branch => !step.params.branchId || branch.id===step.params.branchId).filter(branch => operation.startsWith('quench') ? branch.quench?.enabled || operation==='quench' && plan.workupBranches.length===1 : operation==='dilution' ? num(branch.dilution?.amount)>0 : operation==='wash' ? (branch.washes??[]).length>step.params.washIndex : true);
    return eligible.map(branch => renderWorkupStep({...step,params:{...step.params,operation,branchId:branch.id,branchCode:branch.code,...(operation==='wash'?{washId:branch.washes[step.params.washIndex]?.id}:{})}},plan,result,materials)).join('\n') || '本轮该操作未设计，请在后处理分支中填写条件。';
  }
  const branch = (plan.workupBranches ?? []).find(b => b.id === id || b.code === step.params?.branchCode);
  const name = value => materials.find(m => m.id === value)?.name ?? value ?? "【物料待指定】";
  if (kind === "split") return "反应结束后" + (num(plan.quenchTemp) != null ? "降温至 " + plan.quenchTemp + " °C，" : "") + "均匀混合并称量可分料总质量，按计划分为 " + (plan.workupBranches ?? []).map(b => b.code + " " + (num(b.fraction) == null ? "【比例待明确】" : r(b.fraction * 100, 3) + "%")).join("、") + "；记录各路实际净质量、留样及残留，用实际份额回算后处理用量。";
  if (!branch) return "【该分支已删除，请更新流程】";
  const res = (result.branches ?? []).find(b => b.id === branch.id) ?? {}, label = branch.code + "路" + (branch.name ? "（" + branch.name + "）" : "");
  if (kind === 'route-summary') return label + '：' + (res.rows ?? []).filter(row=>['quench','quench-solvent','dilution','precipitation'].includes(row.key)||row.key.startsWith('wash-')).map(row=>row.label+' '+(row.amount==null?'【用量待核算】':r(row.amount,3)+' '+row.unit)).join('；') + '。按当前单路线设计核算，分别记录实际投入。';
  const duration = item => num(item?.stirMinutes) == null ? "" : "，搅拌 " + item.stirMinutes + " min";
  const temp = item => num(item?.tempC) == null ? "" : "（" + item.tempC + " °C）";
  if (kind === "quench-prepare") {
    const q=res.quench??{},design=branch.quench??{};
    if (plan.calculationProfile === "generic-mass" && q.mass == null) return label+'：'+name(design.materialId)+'【用量待核算：酸当量计算基准待明确】；请填写终止剂用量及预溶条件。';
    return label+'：称取 '+name(design.materialId)+' '+(q.mass==null?'【用量待核算】':r(q.mass,4)+' g')+(q.solutionPct<100?'，加入 '+name(design.solventId)+' '+(q.carrierMass==null?'【载液待核算】':r(q.carrierMass,3)+' g')+'，配成 '+q.solutionPct+' wt%预溶液。':'，在本支稀释溶剂中预溶；该部分计入本支溶剂总量。');
  }
  if (kind === "quench") {
    const q = res.quench ?? {}, design = branch.quench ?? {};
    if (!design.enabled) return label + "：不加入终止剂，按本路后续条件执行。";
    if (plan.calculationProfile === "generic-mass" && q.mass == null) return label + "：终止剂 " + name(design.materialId) + "【用量待核算：酸当量计算基准待明确】" + temp(design) + duration(design) + "；请填写明确的终止用量及配制条件，记录实际称量、温度及外观。";
    return label + "：加入 " + name(design.materialId) + " " + (q.mass == null ? "【用量待核算】" : r(q.mass,4) + " g") + "（" + r(design.eq) + " eq，相对" + (design.acidBasis === "total" ? "母批全部" : "本支名义") + "投料酸 " + r(q.acidMmol) + " mmol）" + (q.solutionPct < 100 ? "，以 " + name(design.solventId) + " " + (q.carrierMass == null ? "【载液待核算】" : r(q.carrierMass,3) + " g") + " 配成 " + q.solutionPct + " wt%预溶液（总量 " + r(q.solutionMass,3) + " g）" : "，按纯品加入") + temp(design) + duration(design) + "；记录实际称量、温度及外观。";
  }
  if (kind === "dilution") return label + "：加入 " + name(branch.dilution?.materialId) + " " + fmtAmount(res.dilution) + temp(branch.dilution) + duration(branch.dilution) + "，混合均匀。";
  if (kind === "precipitation") return label + "：将" + (plan.calculationProfile === "generic-mass" ? "本支溶液" : "树脂液") + "缓慢加入 " + name(branch.precipitation?.materialId) + " " + fmtAmount(res.precipitation) + " 中" + temp(branch.precipitation) + (branch.precipitation?.temp ? "（" + branch.precipitation.temp + "）" : "") + duration(branch.precipitation) + "后过滤；记录实际用量、浑浊、颗粒及滤液。";
  if (kind === "wash") {
    const w = (branch.washes ?? []).find(w => w.id === step.params?.washId), amount = (res.washes ?? []).find(w => w.id === step.params?.washId);
    if (!w) return "【洗涤已删除，请更新流程】";
    return label + "：" + (w.label ?? "洗涤") + "，每次加入 " + name(w.materialId) + " " + fmtAmount(amount?.perCycle) + "，共 " + (w.cycles ?? 1) + " 次" + temp(w) + duration(w) + "后过滤，逐次记录实际量并留样。";
  }
  if (kind === "dry") return label + "：滤饼摊开" + temp(branch.dry) + (branch.dry?.vacuum ? "真空" : "") + "干燥 " + (num(branch.dry?.hours) == null ? "【时长待填】" : branch.dry.hours + " h") + "，记录质量至恒重。";
  return step.text ?? "";
}

/** Generated steps carry parameter bindings; editing a ratio recalculates text rather than freezing quantities. */
export function branchSteps(plan = {}, result = {}, materials = []) {
  const steps = [], branches = plan.workupBranches ?? [];
  const add = (branch, operation, title, washId = null) => {
    const code = branch?.code ?? "all", id = branch?.id ?? "mother";
    const step = {id:uid(),type:"后处理",title,phase:"postprocess",branchScope:branch?.id ?? "all",text:"",
      params:{section:"postprocess",generatedBy:"workup-branches",branchCode:code,branchId:id,operation,...(washId?{washId}:{})}};
    step.text = renderWorkupStep(step,plan,result,materials);steps.push(step);
  };
  if (branches.length > 1) add(null,"split","均匀分料与实际称量");
  for (const branch of branches) {
    if (branch.quench?.enabled || branches.length > 1) add(branch,"quench",branch.code+"路终止 / 中和");
    if (num(branch.dilution?.amount) > 0) add(branch,"dilution",branch.code+"路稀释");
    if (num(branch.precipitation?.amount) > 0) add(branch,"precipitation",branch.code+"路沉降 / 过滤");
    for (const wash of branch.washes ?? []) if (wash.cycles !== 0) add(branch,"wash",branch.code+"路"+(wash.label??"洗涤"),wash.id);
    if (num(branch.dry?.tempC) != null || num(branch.dry?.hours) != null) add(branch,"dry",branch.code+"路干燥");
  }
  return steps;
}

/** Replace previously generated branch steps; manual steps stay untouched. Returns the number of steps written. */
export function applyBranchSteps(plan, steps) {
  plan.steps ??= [];
  const first = plan.steps.findIndex((s) => s.params?.generatedBy === "workup-branches");
  plan.steps = plan.steps.filter((s) => s.params?.generatedBy !== "workup-branches");
  const at = first >= 0 ? first : plan.steps.length;
  plan.steps.splice(at, 0, ...steps);
  return steps.length;
}
