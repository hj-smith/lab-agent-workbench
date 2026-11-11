// Source values may be replayed only while the editable plan still matches its baseline.
export const SOURCE_PLAN_KEYS = [
  "scale", "scaleBasis", "ratio", "monoBId", "monoAs", "catalystId", "catalystMolPct", "solvents",
  "feedSolvents", "feedSolventMass", "feedSolventId", "solidPct", "solidBasis", "quenchId", "quenchEq",
  "quenchAcidFraction", "quenchSolutionPct", "quenchTemp", "temp", "holdHours", "feedHours", "timeBasis",
  "termination", "materialPurities", "calculationPurityBasis", "expectedYieldFraction", "workupDeductWater",
  "workupBranches", "designSamplingRows", "sourceParamOverrides", "waterRemoval", "feedMode", "stopCriterion", "endpointTarget",
];
export function sourcePlanSnapshot(plan = {}) {
  return JSON.parse(JSON.stringify(Object.fromEntries(SOURCE_PLAN_KEYS.map(key => [key, plan[key] ?? null]))));
}
export function sourcePlanEligible(exp = {}) {
  if (exp.plan?.steps?.some(step=>step.additions?.length || step.conditions)) return false;
  if (exp.sourcePlanModified || exp.needsStepReview || exp.parentCode || exp.doe || exp.sopTemplateMode === "custom") return false;
  const historical = ["历史资料", "imported", "historical", "source_document"].includes(exp.status);
  const baseline = exp.sourcePlanBaseline;
  const matches = baseline && Object.keys(baseline).length > 0 && SOURCE_PLAN_KEYS.every(key =>
    JSON.stringify(baseline[key] ?? null) === JSON.stringify(exp.plan?.[key] ?? null));
  if (baseline) return Boolean(matches && (historical || exp.sourcePlanUnchanged === true));
  return historical && (exp.revision == null || Number(exp.revision) === 1);
}
