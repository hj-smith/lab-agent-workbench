const materialKinds = {
  单体B: "monoB",
  单体A: "monoA",
  酸催化剂: "acid",
  终止剂: "base",
  溶剂: "solvent",
  反溶剂: "solvent",
  洗涤剂: "solvent",
  添加剂: "additive",
};
const materialLabels = { monoB: "单体B", monoA: "单体A", acid: "酸催化剂", base: "终止剂", solvent: "溶剂", additive: "添加剂" };
const stepLabels = {
  charge: "投料",
  feed: "滴加",
  reaction: "反应",
  sample: "取样",
  quench: "终止",
  split: "后处理",
  precipitate: "沉降",
  filter: "后处理",
  wash: "洗涤",
  dry: "干燥",
  custom: "自定义",
  prepare: "准备",
  test: "测试",
};
const enumLabels = {
  scaleBasis: { mmol: "单体B mmol", g: "单体总质量 g", "monomer-g": "单体总质量 g", "monoB-g": "单体B称量质量 g" },
  solidBasis: {
    monomers: "单体质量 / 全反应投料",
    nonvolatile: "单体与催化剂 / 全反应投料",
    "monomers+catalyst": "单体与催化剂 / 全反应投料（来源定义）",
  },
  timeBasis: {
    "total-from-feed-start": "滴加开始起的总时长",
    "after-feed": "滴加结束后的保温时长",
    "at-temperature": "达目标内温起的恒温时长",
    endpoint: "分子量终点停止；原文未规定时长",
    "source wording": "原文时间口径待确认",
  },
  termination: { none: "不中和 / 直接后处理", neutralize: "加入终止剂", "split-control": "同母批中和与不中和分支", custom: "自定义终止" },
};
export function sourceLabel(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value !== "object") return String(value);
  const f = value.file ?? value.filename ?? value.path ?? "";
  const location = [value.sheet, value.cell ?? value.range ?? (value.row != null ? "row " + value.row : "")].filter(Boolean).join("!");
  return [f, location].filter(Boolean).join(" / ") || JSON.stringify(value);
}
export function sourceEnumLabel(field, value) {
  return value == null || value === "" ? "原文未记录" : (enumLabels[field]?.[value] ?? "原文：" + String(value));
}
function planDisplay(plan) {
  if (!plan) return;
  plan.displayEnums = Object.fromEntries(Object.keys(enumLabels).map((field) => [field, sourceEnumLabel(field, plan[field])]));
  for (const step of plan.steps ?? []) {
    step.sourceType ??= step.type;
    const display = stepLabels[step.type] ?? step.type ?? "自定义";
    step.typeDisplay = display;
    if (stepLabels[step.type]) step.type = display;
  }
}
/** Add display aliases only. Values, units, plan timing, purities, source records and provenance remain unchanged. */
export function adaptStateForUi(state) {
  for (const material of state.materials ?? []) {
    const aliases={kindDisplay:materialLabels[materialKinds[material.kind]??material.kind]??material.kind,
      sourceDisplay:sourceLabel(material.source??material.mwSource??material.purityBasis)};
    for(const [key,value]of Object.entries(aliases))Object.defineProperty(material,key,{value,writable:true,configurable:true,enumerable:false});
  }
  for (const template of state.templates ?? []) planDisplay(template.plan);
  for (const experiment of state.experiments ?? []) {
    if(!experiment.archive&&!experiment.design){
    planDisplay(experiment.plan);
    for (const charge of experiment.actual?.charges ?? []) charge.sourceDisplay = sourceLabel(charge.source);
    for (const parameter of experiment.actual?.parameters ?? []) parameter.sourceDisplay = sourceLabel(parameter.source);
    for (const log of experiment.actual?.logs ?? []) {
      if (typeof log.time === "number" || (typeof log.time === "string" && /^\d+(\.\d+)?$/.test(log.time))) log.relativeHours ??= log.time;
      log.actualClock ??= log.clock ?? null;
      log.sourceDisplay = sourceLabel(log.source);
    }
    }
    for (const sample of experiment.samples ?? []) {
      Object.defineProperty(sample,'kindDisplay',{value:sample.kind,writable:true,configurable:true,enumerable:false});
      for (const metric of sample.metrics ?? []) Object.defineProperty(metric,'sourceDisplay',{value:sourceLabel(metric.source??metric.sourceRef),writable:true,configurable:true,enumerable:false});
    }
  }
  return state;
}
