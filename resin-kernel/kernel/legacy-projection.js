import {samplingNodes,resolveSteps} from './design2.js';
import {executionDesign,stableUid,aliases} from './identity.js';
import {sourceLabel} from '../data-adapter.js';
import {publicSource} from './public-provenance.js';

const list=x=>Array.isArray(x)?x:[],copy=structuredClone,finite=x=>typeof x==='number'&&Number.isFinite(x);
const resinMetric=m=>/^(?:Mn|Mw|Đ|dispersity|干品(?:净)?质量|最终干品质量|理论产品整批|分样比例|最终产率)$/.test(m.name??m.label??'');
const summarySheet=source=>typeof source==='object'?source?.sheet==='实验总表':/\/\s*实验总表![A-Z]+\d+$/.test(String(source??''));
const sourceAddress=source=>typeof source==='object'?sourceLabel(source):String(source??'');

/** A summary-to-product link requires migration provenance: an original summary
 * cell in this experiment, or an exact branch anchor recorded by group migration.
 * Codes, suffixes, times and similar names never establish a link. */
function summaryEvidence(experiment,sample,node,products){
  if(!experiment?.archive||!node.lotUid||!list(sample.metrics).some(m=>resinMetric(m)&&summarySheet(m.source)))return null;
  const branch=sample.branchId??sample.branch;
  if(node.branch){
    if(experiment.groupMigrationVersion!==1||sample.sourceExperimentId!==node.branch||branch!==node.branch||!Object.hasOwn(experiment.branchPlans??{},node.branch)||!list(experiment.plan?.workupBranches).some(b=>b.id===node.branch))return null;
    return {kind:'group-migration-summary',source_experiment_id:sample.sourceExperimentId,branch_id:node.branch};
  }
  if(products.length!==1||branch!==experiment.id)return null;
  const refs=new Set(Object.values(experiment.rawSummaryRefs??{}).map(sourceAddress));
  if(!list(sample.metrics).some(m=>resinMetric(m)&&refs.has(sourceAddress(m.source))))return null;
  return {kind:'archive-summary-cells',experiment_id:experiment.id};
}
function evidenceFor(design,experiment,sample,node,products){
  if(sample.designUid===design.uid&&sample.nodeUid&&sample.unassignedNode!==true)return sample.nodeUid===node.uid?{priority:0,...summaryEvidence(experiment,sample,node,products)??{kind:'persisted-node-link'}}:null;
  if(node.legacySampleId===sample.id||node.source?.sampleId===sample.id)return {priority:1,...summaryEvidence(experiment,sample,node,products)??{kind:'declared-legacy-sample'}};
  const readings=Object.entries(design.actualSources??{}).filter(([key,source])=>node.stepId&&key.startsWith('record-operations/'+node.stepId+'/')&&source?.kind==='archive-product-weighing'&&source.experimentId===experiment?.id).flatMap(([,source])=>source.readings??[]);
  if(readings.some(r=>r.sampleId===sample.id))return {priority:2,kind:'archive-product-weighing'};
  const summary=summaryEvidence(experiment,sample,node,products);return summary?{priority:3,...summary}:null;
}
function metricRecord(metric,index,sample,design,experiment){
  const historical=experiment?.archive===true&&!metric.recordKey&&!metric.sourceRef?.revisionUid;
  return {...copy(metric),uid:stableUid(design.uid,'legacy-metric:'+sample.id+':'+index),state:'measured',source:historical?'历史档案':publicSource(metric.source??'样品实测','样品实测').source,source_ref:{experiment_id:experiment?.id??null,legacy_sample_id:sample.id,metric_index:index,metric_id:metric.id??null,original_source:copy(metric.source??null),...metric.sourceRef?{measurement:copy(metric.sourceRef)}:{}},original:copy(metric)};
}
/** Preserve separate readings. A scalar is available only when method,
 * condition, unit and value agree; no averaging or first-row selection. */
export function summarizeGpc(metrics){
  const out={};
  for(const [field,names,unit]of [['Mn',['Mn'],'g/mol'],['Mw',['Mw'],'g/mol'],['dispersity',['Đ','dispersity'],'1']]){
    const selected=metrics.filter(m=>names.includes(m.name??m.label)&&finite(m.value)&&!/comput|calculat|theor|planned|design/i.test(m.valueKind??''));if(!selected.length)continue;
    const conditions=new Set(selected.map(m=>JSON.stringify([m.method??'',m.condition??'',m.unit??unit]))),values=new Set(selected.map(m=>m.value)),single=conditions.size===1&&values.size===1;
    out[field]={value:single?selected[0].value:null,unit,state:single?'measured':'conflict',source:selected.every(m=>m.source==='历史档案')?'历史档案':'样品实测',...single?{}:{reason:'存在不同条件或不同数值的多条 GPC 记录，保留原始测量，未取平均'},source_refs:selected.map(m=>copy(m.source_ref)),measurements:selected.map(m=>({value:m.value,unit:m.unit??unit,method:m.method??'',condition:m.condition??'',source_ref:copy(m.source_ref)}))};
  }
  return out;
}

/** Partition each source metric into exactly one projected physical sample or
 * one legacy sample. Historical readings belong to the first declared execution;
 * an explicit imported revision reference can select a later execution. */
export function projectLegacySamples(design,experiment,library,{source_instance_uid,executionLibraries={}}={}){
  const contexts=list(design.executions).map((execution,index)=>{const plan=executionDesign(design,execution.revisionUid),lib=executionLibraries[execution.uid]??execution.library??library,nodes=samplingNodes(plan,resolveSteps(plan,lib));return {execution,index,plan,nodes};}),mapped=new Map(),unassigned=[];
  const slot=(revisionUid,nodeId)=>revisionUid+'\0'+nodeId;
  const get=(revisionUid,nodeId)=>{const key=slot(revisionUid,nodeId);if(!mapped.has(key))mapped.set(key,{legacy_ids:[],metrics:[],legacy_links:[]});return mapped.get(key);};
  // Declarations remain useful even for a standalone design with no hydrated
  // experiment anchor. They never imply any measurement values.
  const declared=new Set(),existingIds=new Set(list(experiment?.samples).map(s=>s.id));
  for(const {execution,nodes}of contexts)for(const node of nodes){if(node.legacySampleId&&!declared.has(node.uid)&&!existingIds.has(node.legacySampleId))get(execution.revisionUid,node.id).legacy_ids.push(node.legacySampleId);declared.add(node.uid);}
  for(const sample of list(experiment?.samples)){
    const candidates=[];
    for(const context of contexts)for(const node of context.nodes){const evidence=evidenceFor(design,experiment,sample,node,context.nodes.filter(n=>n.lotUid));if(evidence)candidates.push({...context,node,evidence});}
    const priority=Math.min(...candidates.map(c=>c.evidence.priority)),best=candidates.filter(c=>c.evidence.priority===priority),firstIndex=Math.min(...best.map(c=>c.index)),first=best.filter(c=>c.index===firstIndex),owner=first.length===1?first[0]:null;
    if(!owner){
      const metrics=list(sample.metrics).map((m,i)=>metricRecord(m,i,sample,design,experiment));
      unassigned.push({schemaVersion:'1.0',source_instance_uid,uid:stableUid(design.uid,'legacy-record:'+sample.id),revision:1,updated_at:null,code:sample.name??sample.id,aliases:aliases(sample.aliases,sample.id,sample.name),design_uid:design.uid,experiment_id:experiment?.id??null,legacy_ids:[sample.id],kind:sample.kind??'legacy',unassigned_node:true,branch:sample.branchId??sample.branch??null,metrics,key_results:{gpc:summarizeGpc(metrics)}});continue;
    }
    const target=get(owner.execution.revisionUid,owner.node.id),{priority:_,...evidence}=owner.evidence;target.legacy_ids.push(sample.id);target.legacy_links.push({legacy_id:sample.id,...evidence});
    for(const [index,metric]of list(sample.metrics).entries()){
      const revisionUid=metric.sourceRef?.revisionUid,explicit=revisionUid?best.filter(c=>c.execution.revisionUid===revisionUid&&c.node.uid===owner.node.uid):[],destination=revisionUid?(explicit.length===1?explicit[0]:null):owner;
      if(destination)get(destination.execution.revisionUid,destination.node.id).metrics.push(metricRecord(metric,index,sample,design,experiment));
      else {
        // A retained measurement for a no-longer-present imported revision still
        // needs one public home; it is not copied into a different execution.
        const key=sample.id+'\0'+revisionUid;let record=unassigned.find(r=>r._partition===key);
        if(!record){record={schemaVersion:'1.0',source_instance_uid,uid:stableUid(design.uid,'legacy-record:'+key),revision:1,updated_at:null,code:sample.name??sample.id,aliases:aliases(sample.aliases,sample.id,sample.name),design_uid:design.uid,experiment_id:experiment?.id??null,legacy_ids:[sample.id],kind:sample.kind??'legacy',unassigned_node:true,branch:sample.branchId??sample.branch??null,metrics:[],key_results:{gpc:{}},_partition:key};unassigned.push(record);}record.metrics.push(metricRecord(metric,index,sample,design,experiment));
      }
    }
  }
  for(const value of mapped.values())value.legacy_ids=[...new Set(value.legacy_ids)];
  for(const record of unassigned){delete record._partition;record.key_results.gpc=summarizeGpc(record.metrics);}
  return {forNode:(revisionUid,nodeId)=>mapped.get(slot(revisionUid,nodeId))??{legacy_ids:[],metrics:[],legacy_links:[]},legacy_sample:unassigned};
}
