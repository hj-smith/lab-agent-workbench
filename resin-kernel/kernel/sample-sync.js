import {samplingNodes,flattenSteps} from './design2.js';
import {stableUid,executionDesign} from './identity.js';
import {normalizeSamplingIdentity} from './sampling-identity.js';

const copy=value=>structuredClone(value);
const key=value=>typeof value==='string'?value.normalize('NFKC').trim():'';
const tokens=values=>new Set(values.flat().map(key).filter(Boolean));

// Keep references to declarations: samplingNodes deliberately returns detached,
// normalised nodes. Only identity/link metadata is written back to the design.
function declarations(design){
  const result=new Map(),plan=design.samplingPlan??{};
  normalizeSamplingIdentity(design);
  for(const node of [...plan.points??[],...plan.nodes??[]])result.set(node.id,node);
  const walk=(steps,branch)=>{for(const step of steps??[]){
    const ownBranch=step.branch??branch;
    for(const node of step.samples??[]){
      const suffix=node.id??node.suffix??'sample';
      result.set(ownBranch?`${ownBranch}-${suffix}`:node.id??`${step.id}-${suffix}`,node);
    }
    for(const child of step.branches??[])walk(child.steps,child.id);
  }};
  walk(design.steps);return result;
}

/** Synchronise the experiment's sample anchors without moving test results.
 *
 * The returned experiment is a clone. The supplied design receives node uid,
 * sampleId and legacySampleId metadata (numeric sampling points become explicit
 * declarations), so callers must persist it together with the experiment.
 * Matching is deliberately conservative: an exact, mutually unique identity or
 * name/alias is required. A shared time, product kind or array position cannot
 * establish that two records describe the same physical sample.
 */
export function syncSamples(experiment,design){
  const out=copy(experiment),sources=declarations(design),nodes=samplingNodes(design);
  // The first executed plan that declares a physical node owns its printed
  // sample code. Later draft times may move, while that execution's anchors
  // retain their labels and test/attachment identity.
  const executedNodes=new Map();
  for(const revision of design.frozenRevisions??[])for(const node of samplingNodes(revision.plan))if(!executedNodes.has(node.uid))executedNodes.set(node.uid,node);
  const samples=out.samples??=[],designUid=design.uid??design.id??experiment.id;
  const available=new Set(samples.map((_,index)=>index)),pending=new Set(nodes.map((_,index)=>index)),matches=new Map();
  const nodeTokens=nodes.map(node=>tokens([node.sampleId,node.id,node.name,node.aliases??[]]));
  const intersects=(left,right)=>[...left].some(value=>right.has(value));
  const sameDesign=sample=>sample.designUid===designUid;
  const rules=[
    (node,sample)=>!!node.legacySampleId&&sample.id===node.legacySampleId,
    (node,sample)=>sameDesign(sample)&&sample.unassignedNode!==true&&(sample.nodeUid===node.uid||sample.nodeId===node.id),
    (node,sample,index)=>nodeTokens[index].has(key(sample.id)),
    (node,sample,index)=>intersects(nodeTokens[index],tokens([sample.name,sample.sampleId,sample.aliases??[]]))
  ];
  for(const rule of rules){
    const choices=new Map(),uses=new Map();
    for(const ni of pending){
      const found=[...available].filter(si=>rule(nodes[ni],samples[si],ni));choices.set(ni,found);
      for(const si of found)uses.set(si,(uses.get(si)??0)+1);
    }
    for(const [ni,found]of choices)if(found.length===1&&uses.get(found[0])===1){
      matches.set(ni,found[0]);pending.delete(ni);available.delete(found[0]);
    }
  }

  const usedIds=new Set(samples.map(sample=>sample.id));
  for(const [index,node]of nodes.entries()){
    const printed=executedNodes.get(node.uid)??node;
    let sample=matches.has(index)?samples[matches.get(index)]:null;
    if(!sample){
      const base=stableUid(designUid,'experiment-sample:'+node.uid);let id=base,suffix=2;
      while(usedIds.has(id))id=`${base}-${suffix++}`;
      usedIds.add(id);sample={id,name:printed.sampleId??printed.name??printed.label??printed.id,kind:node.kind??'过程样',metrics:[],generatedBy:'design-node'};
      if(node.branch!=null)sample.branchId=node.branch;
      if(node.parentSampleId!=null)sample.parentSampleId=node.parentSampleId;
      samples.push(sample);
    }
    // The test/attachment/parent payload belongs to the existing sample anchor.
    // Draft nodes may update their code; executed nodes retain the printed one.
    const nextName=printed.name??printed.sampleId??printed.label??printed.id;
    if(sample.name&&sample.name!==nextName)sample.aliases=[...new Set([...sample.aliases??[],sample.name])];
    sample.name=nextName;
    sample.designUid=designUid;sample.nodeUid=node.uid;sample.nodeId=node.id;
    if(node.branch!=null)sample.branchId=node.branch;else delete sample.branchId;
    sample.nodeKind=node.lotUid?'product':node.kind??(flattenSteps(design.steps).find(s=>s.id===node.stepId)?.stage==='workup'?'workup':'process');
    sample.plannedTests=copy(node.plannedTests??node.tests??[]);
    sample.unassigned=false;delete sample.unassignedNode;delete sample.nodeGroup;
    sample.metrics??=[];
    const declaration=sources.get(node.id);
    if(declaration){declaration.uid=node.uid;declaration.sampleId=printed.sampleId;declaration.legacySampleId=sample.id;}
  }
  // Removed nodes and unrecognised historical samples remain usable by aging,
  // comparisons and trash references. Their last link also allows restoration.
  // A card the plan made and that never got a result or file is just a
  // placeholder: when its node is removed from the plan, it goes too.
  const placeholder=sample=>sample.generatedBy==='design-node'&&!sample.metrics?.length&&!sample.attachments?.length&&!sample.files?.length;
  for(const index of available){samples[index].unassigned=true;samples[index].unassignedNode=true;samples[index].nodeGroup='未归入节点';}
  out.samples=samples.filter((sample,index)=>!(available.has(index)&&placeholder(sample)));return projectRecordedMetrics(out,design);
}

const recordedActual=design=>design.executions?.find(e=>e.revisionUid===(design.actualRevisionUid??design.revisionUid))?.actual??design.actual??{};
const metricName=name=>name==='dispersity'?'Đ':name;
function recordNode(design,nodeId){
  const plan=executionDesign(design),nodes=samplingNodes(plan),exact=nodes.find(node=>node.id===nodeId);if(exact)return exact;
  // The source's generic endpoint row is merged into the named physical node.
  const point=(plan.samplingPlan?.points??[]).find(p=>typeof p==='object'&&p.id===nodeId);
  return point?nodes.find(node=>node.id===point.sameSampleAs||node.timeBinding?.sourceAddress&&node.timeBinding.sourceAddress===point.sourceAddress||node.hoursExpr&&node.hoursExpr===point.hoursExpr):null;
}
/** Imported analytical values follow the stable physical sample anchor. Each
 * value remembers its last imported value so subsequent hand edits are kept. */
export function projectRecordedMetrics(experiment,design,{now}={}){
  const out=copy(experiment),actual=recordedActual(design),revisionUid=design.actualRevisionUid??design.revisionUid;
  for(const [recordKey,value]of Object.entries(actual)){
    const match=/^record-samples\/([^/]+)\/(Mn|Mw|Đ|dispersity)$/.exec(recordKey);if(!match||typeof value!=='number'||!Number.isFinite(value))continue;
    const node=recordNode(design,match[1]),sample=out.samples?.find(sample=>sample.designUid===(design.uid??design.id)&&sample.nodeUid===node?.uid);if(!sample)continue;
    sample.metrics??=[];const name=metricName(match[2]),file=[...(design.studio?.recordImports??[])].reverse().find(entry=>Object.hasOwn(entry.rawValues??{},recordKey))?.file;
    let metric=sample.metrics.find(metric=>metric.recordKey===recordKey);
    if(!metric){
      // An independent handwritten result takes priority over a fresh import.
      const manual=sample.metrics.find(metric=>metricName(metric.name)===name&&metric.recordKey==null&&typeof metric.value==='number');
      if(manual){if(manual.value!==value)manual.recordConflict={recordKey,value,source:'批记录导入',revisionUid};continue;}
      metric={id:stableUid(sample.id,'record-metric:'+recordKey),name,unit:name==='Đ'?'':'g/mol',method:'GPC（批记录）',condition:'批记录检测表',date:null,source:'批记录导入',valueKind:'recorded_import',recordKey,recordValue:value,value,sourceRef:{designUid:design.uid,revisionUid,key:recordKey,...file?{file}:{}},...now?{recordedAt:now}:{}};sample.metrics.push(metric);
    }else if(metric.value===metric.recordValue){metric.value=value;metric.recordValue=value;metric.source='批记录导入';metric.sourceRef={designUid:design.uid,revisionUid,key:recordKey,...file?{file}:{}};delete metric.recordConflict;}
    else if(metric.value!==value)metric.recordConflict={recordKey,value,source:'批记录导入',revisionUid};
    else delete metric.recordConflict;
  }
  return out;
}
/** Preview conflicts even when a workbook repeats the previous import verbatim. */
export function recordedMetricConflicts(experiment,design,incoming){
  const conflicts=[];
  for(const [recordKey,value]of Object.entries(incoming??{})){
    const match=/^record-samples\/([^/]+)\/(Mn|Mw|Đ|dispersity)$/.exec(recordKey);if(!match||typeof value!=='number')continue;
    const node=recordNode(design,match[1]),sample=experiment?.samples?.find(sample=>sample.nodeUid===node?.uid&&sample.designUid===(design.uid??design.id));if(!sample)continue;
    const metric=sample.metrics?.find(metric=>metric.recordKey===recordKey)??sample.metrics?.find(metric=>metricName(metric.name)===metricName(match[2])&&metric.recordKey==null&&typeof metric.value==='number');
    if(metric&&metric.value!==value&&(metric.recordKey==null||metric.value!==metric.recordValue))conflicts.push({key:recordKey,before:metric.value,after:value,kind:'conflict',flag:'conflict',selected:false,importable:true,message:'样品中的结果已人工修改；保留人工值。导入记录可单独留存，请核对后在样品中处理。'});
  }
  return conflicts;
}

/** Explicit user assignment changes only the node link, never sample/results IDs. */
export function assignSampleToNode(design,sample,nodeId='new') {
  const out=copy(design),sources=declarations(out);
  let target=sources.get(nodeId);
  if(nodeId==='new') {
    out.samplingPlan??={};out.samplingPlan.nodes??=[];
    target={id:'archive-'+sample.id,uid:stableUid(out.uid,'manual-sample:'+sample.id),
      sampleId:sample.name??sample.id,label:sample.name??sample.id,kind:sample.kind,tests:[]};
    if(sources.has(target.id))throw Error('该样品已有原档案节点，请刷新后重试');
    out.samplingPlan.nodes.push(target);
  }
  if(!target)throw Error('样品节点不存在，请刷新后重试');
  for(const declaration of sources.values())if(declaration.legacySampleId===sample.id)delete declaration.legacySampleId;
  target.legacySampleId=sample.id;
  return out;
}
