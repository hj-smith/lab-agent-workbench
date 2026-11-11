import {samplingNodes,variableValues} from './design2.js';
import {executionDesign,stableUid} from './identity.js';
import {normalizeSamplingIdentity} from './sampling-identity.js';
import {needsHistoricalPlatformRepair} from './history-platform.js';

const code=value=>String(value??'').normalize('NFKC').replace(/(\d)\.0+(?=h\b)/g,'$1').trim();
const copy=structuredClone;
function declarations(design){
  normalizeSamplingIdentity(design);const map=new Map();
  for(const node of [...design.samplingPlan.points??[],...design.samplingPlan.nodes??[]])map.set(node.id,{node,branch:node.branch,ownerKind:'plan'});
  const walk=(steps,branch)=>{for(const step of steps??[]){const scope=step.branch??branch;for(const node of step.samples??[])map.set(scope?scope+'-'+(node.id??node.suffix??'sample'):node.id??step.id+'-'+(node.suffix??'sample'),{node,branch:scope,ownerKind:'step'});for(const child of step.branches??[])walk(child.steps,child.id);}};
  walk(design.steps);return map;
}
export function generatedHistoryRepair(experiment,design){
  if(!design||experiment.archive!==true||design.conversion?.historicalFidelityDeferred===1||design.conversion?.historicalFidelityVersion===1&&!needsHistoricalPlatformRepair(experiment,design))return {eligible:false};
  const c=design.conversion;
  if(!['history','workbook'].includes(c?.sourceType)||c.experimentId!==experiment.id||design.uid!==stableUid('lab-mounted-experiment',experiment.id))return {eligible:false,reason:'挂载来源不能确认为旧版自动历史迁移，保留现有设计待人工核对。'};
  const steps=(items)=>(items??[]).flatMap(s=>[s,...(s.branches??[]).flatMap(b=>steps(b.steps))]);
  if(Object.values(design.userEditedFields??{}).some(Boolean)||steps(design.steps).some(s=>Object.values(s.userEditedFields??{}).some(Boolean))||Object.keys(design.studio??{}).some(key=>!['consumptions'].includes(key)))return {eligible:false,reason:'历史挂载包含人工编辑或导入记录，保留所有修订与执行，未自动重建。'};
  for(const revision of design.frozenRevisions??[])if(![stableUid(design.uid,'revision:'+revision.revision),stableUid(design.uid,'archive-product-revision:'+revision.revision)].includes(revision.uid))return {eligible:false,reason:'历史挂载包含非迁移器生成的冻结修订，保留原执行待核对。'};
  for(const execution of design.executions??[])if(![design.batch.uid,stableUid(design.batch.uid,'archive-product-execution:'+execution.revision)].includes(execution.uid))return {eligible:false,reason:'历史挂载包含人工执行记录，保留原执行待核对。'};
  if([...declarations(structuredClone(design)).values()].some(({node})=>/^archive-/.test(node.id)&&!node.historicalSampleSource&&!/原档案树脂汇总/.test(node.source?.reason??'')))return {eligible:false,reason:'历史挂载含人工归入的样品节点，保留其关系待核对。'};
  return {eligible:true};
}

/** One-time correction of a generated historical projection, never a user
 * execution. Source archives stay unchanged and persistence makes a complete
 * pre-repair backup. Match physical registrations, never the evaluated hours. */
export function preserveHistoricalIdentity(previous,next,experiment){
  const old=executionDesign(previous),oldNodes=samplingNodes(old),declared=declarations(next),renames=new Map();
  const used=new Set();
  for(const node of samplingNodes(next)){
    const compatible=prior=>(prior.branch??'')===(node.branch??'')&&!!prior.lotUid===!!node.lotUid;
    const physical=oldNodes.filter(prior=>compatible(prior)&&(
      prior.id===node.id||code(prior.sampleId)&&code(prior.sampleId)===code(node.sampleId)
    ));
    const matches=physical.length?physical:oldNodes.filter(prior=>compatible(prior)&&node.legacySampleId&&prior.legacySampleId===node.legacySampleId);
    if(matches.length!==1||used.has(matches[0].uid))continue;
    const prior=matches[0],entry=declared.get(node.id);if(!entry)continue;const target=entry.node;
    if(prior.legacySampleId&&node.legacySampleId&&prior.legacySampleId!==node.legacySampleId&&prior.legacySampleId!==stableUid(previous.uid,'experiment-sample:'+prior.uid))throw Object.assign(Error('原样品归入关系与本次来源绑定不同，保留旧挂载待核对。'),{code:'HISTORY_REPAIR_REVIEW'});
    used.add(prior.uid);renames.set(node.id,prior.id);
    // Codes printed by the earlier generated execution remain aliases/labels;
    // source registration is separately retained in node.source and workbook facts.
    target.id=entry.ownerKind==='step'&&entry.branch&&prior.id.startsWith(entry.branch+'-')?prior.id.slice(entry.branch.length+1):prior.id;target.uid=prior.uid;
    if(prior.lotUid)target.lotUid=prior.lotUid;
    target.aliases=[...new Set([...target.aliases??[],target.sampleId,prior.sampleId].filter(Boolean))];
    if(prior.sampleId)target.sampleId=prior.sampleId;
    target.legacySampleId??=prior.legacySampleId;
  }
  for(const {node:target}of declared.values())if(target.sameSampleAs&&renames.has(target.sameSampleAs))target.sameSampleAs=renames.get(target.sameSampleAs);
  const ids=samplingNodes(next).map(n=>n.id);if(new Set(ids).size!==ids.length)throw Object.assign(Error('来源恢复后的节点标识存在竞争，保留旧挂载待核对。'),{code:'HISTORY_REPAIR_REVIEW'});
  const rewrite=key=>String(key).replace(/^(record-(?:samples|timeline)\/)([^/]+)(\/)/,(_,a,id,b)=>a+(renames.get(id)??id)+b);
  for(const field of ['actual','actualSources'])if(next[field])next[field]=Object.fromEntries(Object.entries(next[field]).map(([key,value])=>[rewrite(key),value]));
  if(next.sourceMap)next.sourceMap=Object.fromEntries(Object.entries(next.sourceMap).map(([key,value])=>[key,typeof value==='string'?rewrite(value):copy(value)]));
  // Explicit manual assignments and attachments are user data, not migration
  // guesses. Retain declarations even when absent from the source conversion.
  for(const [id,{node}]of declarations(old))if(/^archive-/.test(id)&&!node.historicalSampleSource&&!samplingNodes(next).some(n=>n.legacySampleId===node.legacySampleId)&&experiment.samples?.some(sample=>sample.id===node.legacySampleId))next.samplingPlan.nodes.push(copy(node));
  next.uid=previous.uid;next.id=previous.id;next.batch=copy(previous.batch);
  next.revision=old.revision??previous.revision;next.revisionUid=old.revisionUid??previous.revisionUid;
  next.revisionCode=old.revisionCode??previous.revisionCode;next.revisionAliases=copy(old.revisionAliases??previous.revisionAliases??[]);
  for(const field of ['attachments','question','hypothesis','conclusion','roadmapNode'])if(previous[field]!=null)next[field]=copy(previous[field]);
  if(previous.studio?.consumptions)next.studio={...next.studio,consumptions:copy(previous.studio.consumptions)};
  next.recordConflicts=[...copy(previous.recordConflicts??[]),...next.recordConflicts??[]];
  for(const [key,value]of Object.entries(old.actual??{})){
    if(value==null||value===''||JSON.stringify(next.actual?.[key])===JSON.stringify(value))continue;
    next.recordConflicts??=[];
    const rawSource=old.actualSources?.[key]??previous.actualSources?.[key],archiveSource={kind:'旧版历史迁移投影',...(rawSource&&typeof rawSource==='object'?copy(rawSource):rawSource?{address:String(rawSource)}:{}),file:previous.conversion?.sourceFile??'原档案'};
    next.recordConflicts.push({key,workbookValue:copy(next.actual?.[key]??null),archiveValue:copy(value),workbookSource:copy(next.actualSources?.[key]??next.source??null),archiveSource,resolution:'source-fidelity-repair-preserve-both'});
  }
  const changes=[],before=variableValues(old.vars??[]),after=variableValues(next.vars??[]);
  for(const variable of next.vars??[]){let a,b;try{a=before.get(variable.key);b=after.get(variable.key);}catch{continue;}if(a!=null&&b!=null&&JSON.stringify(a)!==JSON.stringify(b))changes.push({label:variable.label??variable.key,unit:variable.unit??'',before:a,after:b});}
  const platformOnly=previous.conversion?.historicalFidelityVersion===1&&needsHistoricalPlatformRepair(experiment,previous);
  // R4's repair record and note are audit evidence, not a slot for the latest
  // migration. A platform-only follow-up must not replace or reissue them.
  if(platformOnly){
    if(previous.historicalRepair!==undefined)next.historicalRepair=copy(previous.historicalRepair);
    const notes=[...previous.historicalNotes??[],...next.historicalNotes??[]];
    next.historicalNotes=[...new Map(notes.map(note=>[JSON.stringify(note),copy(note)])).values()];
  }else{
    next.historicalRepair={version:1,previousSourceFile:previous.conversion?.sourceFile??null,sourceFile:next.conversion?.sourceFile??null,changedParameters:changes};
    next.historicalNotes??=[];next.historicalNotes.push({severity:'warning',message:'已更正旧版自动生成的历史迁移投影；原档案与原始指标未改写，升级前完整投影保存在迁移备份。正式来源：'+(next.conversion?.sourceFile??'原总表/档案')+'。'+(changes.length?'来源参数更正：'+changes.map(c=>c.label+' '+c.before+' → '+c.after+' '+c.unit).join('；'):'新增原文中已登记的操作、取样与记录。'),source:{file:next.conversion?.sourceFile??'R100_SOP与性能对比总表.xlsx',previousFile:previous.conversion?.sourceFile??null}});
  }
  if(needsHistoricalPlatformRepair(experiment,previous)){
    next.historicalPlatformRepair=copy(previous.historicalPlatformRepair??{version:1,kind:'round5-platform',branches:Object.keys(next.historicalBranchResults??{})});
    const note={severity:'warning',message:'第五轮平台历史投影修复：补齐分支产品身份与产率口径；既有历史修复记录和原始指标保留，修复前完整数据保存在迁移备份。',source:{experimentId:experiment.id,migration:'round5-platform',version:1}};
    if(!next.historicalNotes.some(value=>JSON.stringify(value)===JSON.stringify(note)))next.historicalNotes.push(note);
  }
  next.recordConflicts=[...new Map(next.recordConflicts.map(row=>[JSON.stringify(row),row])).values()];
  return next;
}

export function retainHistoricalExecutionIdentity(previous,next){
  const prior=previous.executions?.find(e=>e.revisionUid===(previous.actualRevisionUid??previous.revisionUid));
  if(prior&&next.executions?.length===1)next.executions[0].uid=prior.uid;
  return next;
}
