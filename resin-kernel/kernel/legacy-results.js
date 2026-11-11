import {samplingNodes,resolveSteps,applyDesignActual} from './design2.js';
import {executionDesign,editRevision,freezeDesign,stableUid} from './identity.js';
import {availableMass} from './integration.js';
import {publicSource} from './public-provenance.js';

const finite=x=>typeof x==='number'&&Number.isFinite(x);
const list=x=>Array.isArray(x)?x:[];
const branchOf=s=>s.branchId??s.branch??null;
const sameBranch=(a,b)=>a===b||a&&b&&(/^[A-Z]$/.test(a)&&b.endsWith(a)||/^[A-Z]$/.test(b)&&a.endsWith(b));
const dryMetric=metric=>/^(?:干品(?:净)?质量|最终干品(?:质量)?)$/.test(metric.name??metric.label??'')&&metric.unit==='g'&&finite(metric.value)&&!/planned|theor|computed|calculat/i.test(metric.valueKind??'');
const resinSummary=sample=>!/(?:配方|film)/i.test(sample.kind??'')&&(/-summary-resin$/.test(sample.id??'')||/树脂汇总$/.test(sample.name??''));
const sourceSummaries=experiment=>list(experiment?.samples).filter(sample=>resinSummary(sample)&&list(sample.metrics).some(dryMetric));
const rawSteps=steps=>list(steps).flatMap(step=>[step,...list(step.branches).flatMap(branch=>rawSteps(branch.steps))]);
const safeId=value=>String(value).replace(/[\[\]{}|@/]/g,'_');

/** Bind an explicitly recorded resin product to one physical FP declaration.
 * The supplied design is a fresh mutable historical conversion. Original
 * samples/metrics and frozen executions are never edited or copied. */
export function bindHistoricalProductSamples(design,experiment,library={}){
  if(!experiment||experiment.archive===false||!(experiment.archive===true||experiment.rawSummary||experiment.plan?.sourceSop||experiment.plan?.summarySourceOnly))return design;
  if(design.frozenRevisions?.some(revision=>revision.uid===design.revisionUid)||design.executions?.length)return design;
  const summaries=sourceSummaries(experiment);if(!summaries.length)return design;
  design.samplingPlan??={points:[],nodes:[]};design.samplingPlan.nodes??=[];
  const steps=resolveSteps(design,library),nodes=samplingNodes(design,steps),products=nodes.filter(node=>node.lotUid);
  const declaredBranches=[...new Set([
    ...list(experiment.plan?.workupBranches??experiment.plan?.branches).map(branch=>branch.id),
    ...rawSteps(design.steps).flatMap(step=>list(step.branches).map(branch=>branch.id)),
  ].filter(Boolean))];
  const physicalBranches=declaredBranches.length>1?declaredBranches:[];
  const candidates=node=>node.branch?summaries.filter(sample=>sameBranch(branchOf(sample),node.branch)):products.length===1&&summaries.length===1&&!physicalBranches.length?summaries:[];
  const selected=products.map(node=>({node,samples:candidates(node)})),used=new Set();
  const declarationFor=node=>{
    const direct=[...list(design.samplingPlan.points),...design.samplingPlan.nodes].find(item=>typeof item==='object'&&item.id===node.id);
    if(direct)return direct;
    // Module-owned declarations are materialised on their own source step,
    // rather than duplicated as another sampling-plan node.
    const owner=rawSteps(design.steps).find(step=>step.id===node.stepId),resolved=steps.find(step=>step.id===node.stepId);
    if(!owner)return null;
    owner.samples??=structuredClone(resolved?.samples??[]);
    return owner.samples.find(sample=>(node.branch?node.branch+'-'+(sample.id??sample.suffix??'sample'):sample.id??owner.id+'-'+(sample.suffix??'sample'))===node.id);
  };
  const bind=(declaration,node,sample)=>{
    if(!declaration)return;
    declaration.kind='product';declaration.stage='成品';
    declaration.uid=node.uid;declaration.lotUid=node.lotUid;
    declaration.legacySampleId=sample.id;
    declaration.aliases=[...new Set([...declaration.aliases??[],sample.name,...list(sample.aliases),sample.id].filter(Boolean))];
    declaration.historicalSampleSource={experimentId:experiment.id,sampleId:sample.id,path:'samples['+list(experiment.samples).indexOf(sample)+']',kind:'recorded-resin-summary',readings:list(sample.metrics).filter(dryMetric).map(metric=>structuredClone(metric.sourceRef??metric.source??null))};
    used.add(sample.id);
  };
  for(const {node,samples}of selected)if(samples.length===1&&selected.filter(choice=>choice.samples.some(sample=>sample.id===samples[0].id)).length===1)bind(declarationFor(node),node,samples[0]);
  const create=(sample,branch)=>{
    const scope=branch??null;
    if(products.some(node=>(node.branch??null)===scope))return;
    const scoped=scope?summaries.filter(row=>sameBranch(branchOf(row),scope)):summaries;
    if(scoped.length!==1||used.has(sample.id))return;
    const id=scope?safeId(scope)+'-archive-FP':'archive-FP';
    if(nodes.some(node=>node.id===id))return;
    const ownDry=steps.findLast(step=>scope?step.branch===scope&&/干燥|恒重/.test((step.category??'')+' '+step.title):!step.branch&&/干燥|恒重/.test((step.category??'')+' '+step.title));
    const node={id,kind:'product',stage:'成品',sampleId:(scope??experiment.code)+'-FP',uid:stableUid(design.uid,'archive-product:'+sample.id),lotUid:stableUid(design.uid,'archive-lot:'+sample.id),stepId:ownDry?.id??'history-product-result-'+safeId(sample.id),...scope?{branch:scope}:{},source:{sampleId:sample.id,experimentId:experiment.id,reason:'原档案树脂汇总中的明确干品称量'}};
    bind(node,node,sample);design.samplingPlan.nodes.push(node);
  };
  if(physicalBranches.length){
    for(const branch of physicalBranches){const matches=summaries.filter(sample=>sameBranch(branchOf(sample),branch));if(matches.length===1)create(matches[0],branch);}
  }else if(!products.length&&summaries.length===1)create(summaries[0],null);
  return design;
}

function sourcesFor(experiment,node,products){
  const samples=list(experiment?.samples),summaries=samples.filter(resinSummary);
  const explicit=samples.filter(s=>s.nodeUid===node.uid||s.uid===node.uid||s.nodeId===node.id||s.id===node.legacySampleId||s.name===node.sampleId);
  const scoped=node.branch?summaries.filter(s=>sameBranch(branchOf(s),node.branch)):products.length===1&&summaries.length===1?summaries:[];
  return [...new Map([...explicit,...scoped].map(s=>[s.id,s])).values()];
}
export function historicalProductReadings(design,experiment,library){
  if(!experiment)return [];
  const plan=executionDesign(design),nodes=samplingNodes(plan,resolveSteps(plan,library)),products=nodes.filter(n=>n.lotUid);
  return products.map(node=>({node,readings:sourcesFor(experiment,node,products).flatMap(sample=>list(sample.metrics).flatMap((metric,index)=>dryMetric(metric)?[{value:metric.value,source:metric.source??null,sampleId:sample.id,path:`samples[${list(experiment.samples).indexOf(sample)}].metrics[${index}]`}]:[]))}));
}
/** Only exact product-node / archived summary evidence is accepted. Original
 * samples and frozen plans remain untouched. Writes enter the actual execution. */
export function backfillMeasuredProducts(design,experiment,library){
  if(!experiment?.archive)return design;
  let out=design;
  const plan=executionDesign(out),products=samplingNodes(plan,resolveSteps(plan,library)).filter(n=>n.lotUid);
  // Older migrated archives may predate source-product binding. Repair only
  // the mutable current revision, retaining every frozen source execution.
  if(!products.length){
    const repaired=bindHistoricalProductSamples(structuredClone(plan),experiment,library);
    if(samplingNodes(repaired,resolveSteps(repaired,library)).some(node=>node.lotUid)){
      out=editRevision(out);out.samplingPlan=structuredClone(repaired.samplingPlan);out.steps=structuredClone(repaired.steps);
      // The same old file may be projected repeatedly before its startup repair
      // is persisted. Keep that repair revision/execution identity deterministic.
      out.revisionUid=stableUid(out.uid,'archive-product-revision:'+out.revision);out.revisionCode=out.code+'@r'+out.revision;out.revisionAliases=[out.revisionCode];
      out.actual=structuredClone(plan.actual??{});out.actualRevisionUid=out.revisionUid;
      out=freezeDesign(out);
      out.executions.find(e=>e.revisionUid===out.revisionUid).uid=stableUid(out.batch.uid,'archive-product-execution:'+out.revision);
    }
  }
  for(const {node,readings}of historicalProductReadings(out,experiment,library)){
    if(!readings.length||new Set(readings.map(r=>r.value)).size!==1)continue;
    const owner=out.executions?.find(execution=>{const original=executionDesign(out,execution.revisionUid);return samplingNodes(original,resolveSteps(original,execution.library??library)).some(n=>n.uid===node.uid);});
    const revisionUid=owner?.revisionUid??out.actualRevisionUid??out.revisionUid,plan=executionDesign(out,revisionUid),prefix=`record-operations/${node.stepId}/`,key=prefix+'干品质量';
    if([prefix+'M2',key].some(k=>finite(plan.actual?.[k])))continue;
    if(owner&&revisionUid!==(out.actualRevisionUid??out.revisionUid)){out=structuredClone(out);const execution=out.executions.find(e=>e.revisionUid===revisionUid);execution.actual={...execution.actual,[key]:readings[0].value};}
    else out=applyDesignActual(out,{[key]:readings[0].value},{meta:{designUid:out.uid,revisionUid}});
    out.actualSources={...out.actualSources,[key]:{kind:'archive-product-weighing',experimentId:experiment.id,revisionUid,readings:structuredClone(readings)}};
  }
  return out;
}
/** Integration records use measured archive evidence too; conflicting readings
 * remain a visible conflict rather than silently selecting a number. */
export function overlayHistoricalResults(records,design,experiment,library){
  if(!experiment?.archive)return records;
  for(const lot of records.lot){
    if(!(lot.legacy_ids??[]).some(id=>experiment.samples?.some(s=>s.id===id)))continue;
    const key='resin.factor.termination',current=lot.factors?.[key],branch=list(experiment.plan?.workupBranches??experiment.plan?.branches).find(b=>sameBranch(b.id,lot.branch));
    if(current?.value==null&&branch?.quench&&(branch.quench.enabled===false||branch.quench.materialId))lot.factors[key]={...current,value:branch.quench.enabled===false?'无（不中和）':branch.quench.materialId,state:'set',origin:'legacy_plan',path:'plan.workupBranches/'+branch.id+'/quench',reason:'采用原档案中明确记录的终止方式'};
  }
  for(const {node,readings}of historicalProductReadings(design,experiment,library)){
    const ids=new Set(readings.map(r=>r.sampleId)),lots=records.lot.filter(l=>(l.legacy_ids??[]).some(id=>ids.has(id)));
    for(const lot of lots){
      const distinct=[...new Set(readings.map(r=>r.value))];
      if(!distinct.length)continue;
      if(distinct.length>1)lot.produced_mass={value:null,unit:'g',state:'partial',reason:'原档案存在不同的干品质量，请核对来源',...publicSource(readings,'历史档案')};
      else if(lot.produced_mass?.state!=='measured')lot.produced_mass={value:distinct[0],unit:'g',state:'measured',...publicSource(readings,'历史档案')};
      else if(lot.produced_mass.value===distinct[0])lot.produced_mass={...lot.produced_mass,archiveSource:structuredClone(readings)};
      const reasons=(lot.available?.reason??'').split('；').filter(x=>x&&!/计划产量|产出质量缺失|产出是计划值/.test(x));
      lot.available=availableMass(lot.produced_mass,lot.consumptions??[],lot.sampled_mass_g,{reasons});
      lot.status=lot.produced_mass.state==='measured'?'produced':'planned';
    }
  }
  return records;
}
