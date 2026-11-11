import {ensureReferencedMaterials} from './upgrade-materials.js';
import {executionIssue} from './design-issues.js';
import {blankDesign,samplingNodes,flattenSteps,resolveSteps} from './design2.js';
import {stableUid,identifyDesign} from './identity.js';
import {convertDoc} from './convert.js';
import {compile} from './compile.js';
import {escapeTemplate,parseExpr,parseTemplate,mapRefs,serializeExpr} from './expr.js';
import {layoutDoc,resolvePlaced} from './layout.js';
import {evaluateDoc} from './evaluate.js';
import {findMaterial} from './materials.js';
import {projectToLegacy} from './projection.js';
import {syncSamples} from './sample-sync.js';
import {mergeMaterials} from './material-merge.js';
import {ensureStateDefaults} from './state-defaults.js';
import {backfillMeasuredProducts,bindHistoricalProductSamples} from './legacy-results.js';
import {prepareIntegrationDesign} from './integration.js';
import {convertV2Plan,hasHistoricalProvenance,isTrialExperiment} from './convert-v2-plan.js';
import {recoverWorkbookHistory} from './workbook-history-recovery.js';
import {enrichHistoricalDesign,enrichHistoricalLedger} from './history-enrichment.js';
import {preserveHistoricalIdentity,retainHistoricalExecutionIdentity,generatedHistoryRepair} from './history-repair.js';
import {enrichHistoricalPlatform,finishHistoricalPlatformRepair} from './history-platform.js';

const clone=structuredClone;
const list=value=>Array.isArray(value)?value:[];
const key=value=>String(value).replace(/[\[\]{}|@/]/g,'_');
const documents=state=>state.designs??state.kernel?.designs??{};
const document=value=>value?.doc??value;
// This is an in-memory cache, never a second persisted design store. Servers
// hydrate it from DATA_DIR/designs; callers can also mount a fetched document.
function cache(state,designs){Object.defineProperty(state,'designs',{value:designs,writable:true,configurable:true,enumerable:false});return state;}
export function mountedDesign(state,expId){
  const experiment=state.experiments?.find(e=>e.id===expId),mount=experiment?.design;
  if(!mount)return null;
  const values=documents(state),found=document(values[mount.uid])??Object.values(values).map(document).find(d=>d?.uid===mount.uid);
  return found?.uid===mount.uid&&found.revision===mount.revision?found:null;
}
export function requiresHistoricalSources(state){
  return state.experiments?.some(experiment=>{
    if(!experiment.design?.uid)return true;
    try{return generatedHistoryRepair({...experiment,archiveRecords:state.experimentGroupArchive?.find(g=>g.motherId===experiment.id)?.records??[]},mountedDesign(state,experiment.id)).eligible;}
    catch{return false;} // migrateAll records this design's diagnostic locally.
  })??false;
}
export function mountDesign(state,expId,design){
  if(design?.schema!=='lab-design/2')throw Error('只能挂载 v2 设计');
  const index=state.experiments?.findIndex(e=>e.id===expId);
  if(index==null||index<0)throw Error('实验不存在：'+expId);
  const out=clone(state),doc=identifyDesign(design),previous=out.experiments[index];
  const library={...(state.library??{}),materials:out.materials??state.library?.materials??[]};
  if(library.modules){const resolved=resolveSteps(doc,library),materialize=steps=>{for(const step of steps??[]){const samples=resolved.find(s=>s.id===step.id)?.samples;if(step.samples===undefined&&samples?.length)step.samples=clone(samples);for(const branch of step.branches??[])materialize(branch.steps);}};materialize(doc.steps);}
  let experiment=syncSamples(previous,doc);
  experiment.design={uid:doc.uid,revision:doc.revision,mountedAt:previous.design?.mountedAt??new Date().toISOString()};
  if(!experiment.archive){
    const projection=projectToLegacy(doc,library);
    experiment.plan={...experiment.plan,...projection.plan};
    // Minimum upgrades have no reconstructed charges or execution records.
    // An empty compatibility table must not replace the user's original rows.
    experiment.actual=doc.upgrade?.sourceType==='user'&&!doc.executions?.length?clone(previous.actual??{}):{...experiment.actual,...projection.actual};
  }
  out.experiments[index]=experiment;
  return cache(out,{...documents(state),[doc.uid]:doc});
}

/** Convert the old semantic plan/actual, without changing a byte of the archive.
 * Unknown quantities stay unknown. Source text and raw record values survive. */
export function convertHistory(experiment,library={}){
  const plan=experiment.plan??{},design=blankDesign(experiment.code),warnings=[];
  design.uid=stableUid('lab-mounted-experiment',experiment.id);
  design.revisionUid=stableUid(design.uid,'revision:1');
  design.batch={uid:stableUid(design.uid,'batch:1'),code:experiment.code,aliases:[experiment.code]};
  Object.assign(design,{id:design.uid,title:experiment.title??experiment.code,parentCode:experiment.parentCode??'',status:experiment.status??'历史资料',source:{experimentId:experiment.id,files:clone(experiment.sourceFiles??[])}});
  for(const field of ['scaleBasis','timeBasis','solidBasis'])design[field]=clone(plan[field]??null);
  const fields=[['scale','投料尺度',plan.scaleBasis==='monoB-mmol'?'mmol':'g'],['ratio','单体比','mol/mol'],['temp','反应温度','°C','tempC'],['holdHours','恒温时长','h'],['feedHours','滴加时长','h'],['catalystMolPct','催化剂用量','mol%'],['solidPct','反应固含','%'],['termination','终止方式',''],['quenchId','终止试剂',''],['quenchEq','终止当量','eq'],['quenchTemp','终止温度','°C'],['quenchSolutionPct','终止溶液浓度','%']];
  for(const [field,label,unit,role=field]of fields)if(plan[field]!=null)design.vars.push({key:label,label,unit,role,value:clone(plan[field])});
  const sourceCharges=list(plan.sourceCharges);
  // Source amounts are preferred to recalculation, which could invent purities
  // or reinterpret historical scale/solid-content conventions.
  for(const [i,row]of sourceCharges.entries()){
    const material=findMaterial(library,row.materialId??row.name),value=row.amount??row.value;
    if(!material||typeof value!=='number'||!Number.isFinite(value)||value<0||!['g','mL','mmol'].includes(row.unit)){
      warnings.push('未自动转换投料：'+(row.name??row.materialId??i));continue;
    }
    const id='history-charge-'+i;
    design.charges.push({id,material:material.id,label:row.name??material.name,role:/溶剂/.test(material.kind??'')?'solvent':/催化/.test(material.kind??'')?'catalyst':'reactant',amount:{mode:row.unit==='mL'?'volume':row.unit==='mmol'?'mmol':'mass',value,unit:row.unit},source:clone(row.source??null),legacyRowId:row.id});
    if(!design.materials.some(m=>m.ref===material.id))design.materials.push({ref:material.id,as:material.id});
  }
  for(const [i,step]of list(plan.steps).entries())design.steps.push({id:key(step.id??'history-step-'+i),module:'custom',category:'历史步骤',title:step.title??'历史步骤',stage:step.phase==='postprocess'||/workup|postprocess/.test(step.params?.section??'')?'workup':'reaction',args:{},text:escapeTemplate(step.text??''),sourceText:step.text??'',legacyStepId:step.id,source:clone(step.params?.source??step.params??{})});
  if(!design.steps.length)warnings.push('原档案未记录可转换的流程步骤，未补造 SOP。');
  const branches=list(plan.workupBranches).length?plan.workupBranches:list(plan.branches);
  if(branches.length)design.steps.push({id:'history-split',module:'custom',title:'原档案后处理分支',stage:'workup',args:{},text:'分支沿用原档案标识；分料比例未记录时不推定。',branches:branches.map(b=>({id:b.id,name:b.name,fraction:b.fraction??b.splitFraction??null,steps:[]}))});
  // Only a source-declared sampling event establishes a process node. Summary
  // films/results without a process association stay in 未归入节点.
  const declared=new Set(list(experiment.actual?.logs).map(row=>row.sampleId).filter(Boolean));
  design.samplingPlan.nodes=list(experiment.samples).filter(sample=>sample.nodeId||declared.has(sample.id)||declared.has(sample.name)).map((sample,i)=>({id:sample.nodeId??'history-sample-'+i,uid:stableUid(design.uid,'legacy-sample:'+sample.id),legacySampleId:sample.id,sampleId:sample.name??sample.id,label:sample.name??sample.id,kind:sample.kind,branch:sample.branchId,tests:[]}));
  addHistoricalRecords(design,experiment,warnings);
  design.notes=[plan.historicalConditionText,experiment.actual?.notes].filter(Boolean).map(escapeTemplate);
  design.conversion={sourceSchema:'legacy-experiment/1',sourceType:'history',warnings};
  return design;
}

function addHistoricalRecords(design,experiment,warnings){
  // Retain all semantic old records as input cells, including strings/ranges and
  // repeated readings; these remain visible in the generated batch record.
  for(const kind of ['charges','parameters','logs']){
    const rows=list(experiment.actual?.[kind]);if(!rows.length)continue;
    const id='history-'+kind,columns=[{key:'项目',label:'项目'},{key:'记录',label:'原始记录'}];
    design.records.push({kind:'custom',id,title:'原档案'+({charges:'投料',parameters:'参数',logs:'过程日志'}[kind]),columns,rows:rows.map((row,i)=>{
      const rowId='row-'+i,value=kind==='logs'?JSON.stringify(row):row.rawValue??row.value??row.amount??row.note??JSON.stringify(row);
      design.actual[`${id}/${rowId}/记录`]=clone(value);
      return {id:rowId,source:clone(row.source??null),cells:[{t:escapeTemplate(String(row.name??row.stage??row.id??i))},{input:true,type:typeof value==='number'?'number':'text'}]};
    })});
  }
  const charges=[...design.charges,...flattenSteps(design.steps).flatMap(s=>s.charges??[])],nodes=samplingNodes(design);
  const put=(key,value,source)=>{if(value==null||value==='')return;if(design.actual[key]!=null&&design.actual[key]!==value){
    (design.recordConflicts??=[]).push({key,workbookValue:clone(design.actual[key]),archiveValue:clone(value),workbookSource:clone(design.actualSources?.[key]??design.source??null),archiveSource:clone(source??null),resolution:'archive'});
    warnings.push('源工作簿转换值与原档案实测不一致，采用原档案实测并保留双方证据：'+key+'（工作簿转换 '+JSON.stringify(design.actual[key])+'；原档案 '+JSON.stringify(value)+'）');
  }design.actual[key]=clone(value);(design.actualSources??={})[key]=clone(source??'原档案');};
  const observations=list(experiment.actual?.charges).filter(row=>!row.includedIn);
  for(const row of observations){
    const candidates=charges.filter(c=>c.material===row.materialId||design.materials.some(m=>m.as===c.material&&m.ref===row.materialId));
    if(candidates.length===1&&observations.filter(other=>other.materialId===row.materialId).length===1)put(`record-charges/${candidates[0].id}/实际值`,row.rawValue??row.value??row.amount,row.source);
  }
  for(const row of list(experiment.actual?.logs)){
    const candidates=nodes.filter(n=>n.legacySampleId===row.sampleId||n.sampleId===row.sampleId||typeof row.time==='number'&&n.hours===row.time);
    if(candidates.length!==1)continue;
    for(const [field,column]of [['temp','内温'],['outerTemp','外温'],['rpm','搅拌速度'],['note','现象/备注']])put(`record-timeline/${candidates[0].id}/${column}`,row[field],row.source);
    if(typeof row.clock==='number')put(`record-timeline/${candidates[0].id}/实际钟点`,row.clock,row.source);
  }
}

function sourceCandidates(experiment,sources){
  const names=new Set([...list(experiment.sourceFiles),...list(experiment.plan?.sourceFiles),experiment.plan?.sourceSop?.file].filter(Boolean));
  const codes=[experiment.code,...list(experiment.archiveCodes)];
  const entries=Array.isArray(sources)?sources:(sources instanceof Map?[...sources]:Object.entries(sources??{})).map(([file,value])=>({file,...value?.schema?{doc:value}:value}));
  const matchesCode=s=>codes.some(code=>s.code===code||s.doc?.code===code||new RegExp('^'+code.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'(?:_|\\.|$)').test(s.file??''));
  const official=experiment.plan?.sourceSop?.file;
  const conflict=s=>/(?:-DESKTOP-|冲突|副本|(?:^|[\s_.()-])copy(?:[\s_.()-]|$))/i.test(s.file??'');
  const score=s=>(s.file===official?512:0)+(matchesCode(s)?32:0)+(/固含显式/.test(s.file??'')?8:0)+(/SOP/.test(s.file??'')?4:0)+(/实际记录/.test(s.file??'')?2:0);
  return entries.filter(s=>!conflict(s)&&(s.doc||s.error)&&(names.has(s.file)||matchesCode(s))).sort((a,b)=>score(b)-score(a)||String(a.file).localeCompare(String(b.file),'en'));
}
function preserveBranchIds(design,experiment){
  const branches=list(experiment.plan?.workupBranches).length?experiment.plan.workupBranches:list(experiment.plan?.branches),mapping={};
  for(const branch of branches){mapping[branch.id]=branch.id;const suffix=branch.archiveCode?.slice(experiment.code.length)??branch.id.slice(experiment.code.length);if(suffix)mapping[suffix.replace(/^-/,'')]=branch.id;}
  const walk=steps=>{for(const step of steps??[]){for(const b of step.branches??[]){b.id=mapping[b.id]??b.id;walk(b.steps);}for(const n of step.samples??[])if(n.branch)n.branch=mapping[n.branch]??n.branch;}};walk(design.steps);
  for(const node of design.samplingPlan?.nodes??[])if(node.branch)node.branch=mapping[node.branch]??node.branch;
  if(design.historicalBranches)design.historicalBranches=Object.fromEntries(Object.entries(design.historicalBranches).map(([id,b])=>[mapping[id]??id,{...b,id:mapping[id]??id}]));
  if(branches.length&&!design.steps.some(s=>s.branches?.length))design.steps.push({id:'archive-branch-identities',module:'custom',title:'原档案分支',stage:'workup',args:{},text:'沿用原档案分支编号与分料记录。',branches:branches.map(b=>({id:b.id,name:b.name,fraction:b.fraction??null,steps:[]}))});
}

function scopedSourceDoc(doc,experiment,warnings){
  const removed=new Set(doc.sheets.filter(s=>/^CP\d+-\d+/.test(s.name)&&!s.name.startsWith(experiment.code)&&!experiment.archiveCodes?.some(code=>s.name.startsWith(code))).map(s=>s.name));
  if(!removed.size)return doc;
  const original=layoutDoc(doc),values=evaluateDoc(doc),out=clone(doc),layout=layoutDoc(out),removedVars=new Set(doc.vars.filter(v=>removed.has(original.byAddr.get(original.varToAddr.get(v.key))?.sheet)).map(v=>v.key));
  const rewrite=(expr,context)=>serializeExpr(mapRefs(parseExpr(expr),ref=>{
    const target=resolvePlaced(original,ref,context);
    return removed.has(target?.sheet)||ref.kind==='var'&&removedVars.has(ref.key)?{type:'literal',value:values.get(target?.fullAddr)??null}:{type:'ref',...ref};
  }));
  out.vars=out.vars.filter(v=>!removedVars.has(v.key));
  for(const v of out.vars)if(v.expr)v.expr=rewrite(v.expr,original.byAddr.get(original.varToAddr.get(v.key)));
  for(const p of layout.byAddr.values())if(!removed.has(p.sheet)){
    if(p.cell.f)p.cell.f=rewrite(p.cell.f,original.byAddr.get(p.fullAddr));
    if(p.cell.t)p.cell.t=parseTemplate(p.cell.t).map(part=>part.text!==undefined?escapeTemplate(part.text):`{${rewrite(serializeExpr(part.expr),original.byAddr.get(p.fullAddr))}${part.fmt?'|'+part.fmt:''}}`).join('');
  }
  out.sheets=out.sheets.filter(s=>!removed.has(s.name));
  if(out.sourceWorkbook)out.sourceWorkbook.sheets=out.sourceWorkbook.sheets.filter(s=>!removed.has(s.name));
  out.code=experiment.code;
  warnings.push('多实验工作簿按实验编号限定工作表，排除：'+[...removed].join('、')+'；跨表依赖按来源值保留。');
  return out;
}

export function migrateAll(state,sources=[],library={}){
  let out=cache(ensureStateDefaults(clone(state)),clone(documents(state)));
  let sampleFlagsChanged=false;
  // Enrich once per source dataset. Later explicit clears/deletions are user
  // edits and must survive reloads and repeated mount migration.
  const enrichProperties=state.materialPropertiesVersion!==1;
  out.materials=state.experiments?.some(e=>!e.design?.uid)||enrichProperties?mergeMaterials(state.materials??[],library):clone(state.materials??[]);
  // Seed adoption may add aliases/new rows, but an upgrade must respect every
  // existing user field, including an explicit null or a custom material name.
  const originalMaterials=new Map((state.materials??[]).map(m=>[m.id,m]));
  out.materials=out.materials.map(m=>originalMaterials.has(m.id)?{...clone(originalMaterials.get(m.id)),...m.aliases?{aliases:m.aliases}:{}}:m);
  out.materialPropertiesVersion=1;
  const unified={...library,materials:out.materials},designs=[],report={total:out.experiments?.length??0,migrated:0,repaired:0,deferred:0,skipped:0,workbook:0,history:0,user:0,warnings:[],experiments:[],materialsAdded:[],failed:0};
  for(const material of out.materials)if(!originalMaterials.has(material.id))report.materialsAdded.push({id:material.id,name:material.name,recordedName:material.id,source:'seed',message:`物料库缺少 ${material.name}，已从随附种子库补入；请核对物性来源。`});
  const supplement=design=>{const added=ensureReferencedMaterials(out.materials,[design],library.seedMaterials??library);report.materialsAdded.push(...added);report.warnings.push(...added.map(m=>({message:m.message})));};
  // The original summary names MIBK before enrichment creates its charge.
  // Register that recorded identity through the same audited dependency path.
  if(out.experiments?.some(e=>/MIBK/.test(e.rawSummary?.['溶剂及用量']??'')))supplement({charges:[{material:'MIBK'}]});
  const defer=(experiment,previous,message)=>{
    const retained=clone(previous);retained.status='历史资料';retained.conversion={...retained.conversion,historicalFidelityDeferred:1};
    retained.historicalNotes=[...retained.historicalNotes??[],{message,severity:'warning',source:{experimentId:experiment.id,file:retained.conversion.sourceFile??'原档案'}}];
    out=mountDesign(out,experiment.id,retained);designs.push(mountedDesign(out,experiment.id));report.deferred++;report.skipped++;
    report.warnings.push({experimentId:experiment.id,message});report.experiments.push({id:experiment.id,code:experiment.code,sourceType:'retained',warnings:[message]});
  };
  for(const experiment of [...out.experiments??[]]){
    if(isTrialExperiment(experiment)&&(experiment.archive!==false||experiment.status==='历史资料')){experiment.archive=false;experiment.status='设计中';sampleFlagsChanged=true;}
    const previousDesign=mountedDesign(out,experiment.id),checkpoint=out,originalExperiment=clone(experiment);
    let repair=false;
    try {
    const historicalExperiment={...experiment,archiveRecords:out.experimentGroupArchive?.find(g=>g.motherId===experiment.id)?.records??[]};
    const disposition=generatedHistoryRepair(historicalExperiment,previousDesign);repair=disposition.eligible;
    if(disposition.reason){defer(experiment,previousDesign,disposition.reason);continue;}
    if(experiment.design?.uid&&!repair){
      // Upgrade the previous migration's explicit marker without touching the
      // archived values, sample IDs, design revisions or backing file locks.
      for(const sample of experiment.samples??[])if(sample.unassigned===true&&sample.unassignedNode!==true){sample.unassignedNode=true;sampleFlagsChanged=true;}
      report.skipped++;report.experiments.push({id:experiment.id,code:experiment.code,sourceType:'mounted',warnings:[]});continue;
    }
    supplement(experiment);
    const warnings=[];let design,sourceFile,sourceType='history';
    const historical=hasHistoricalProvenance(experiment);
    if(!historical){design=convertV2Plan(experiment,unified);sourceType='user';warnings.push(...design.upgrade.warnings);}
    for(const source of historical?sourceCandidates(experiment,sources):[]){
      if(source.error){warnings.push(`工作簿 ${source.file} 无法解析：${source.error}`);continue;}
      try{design=convertDoc(scopedSourceDoc(source.doc,experiment,warnings),unified);compile(design,unified);sourceType='workbook';sourceFile=source.file;warnings.push(...source.warnings??[],...design.conversion?.warnings??[]);break;}
      catch(error){warnings.push(`工作簿 ${source.file} 无法转换：${error.message}`);}
    }
    if(repair&&previousDesign.conversion?.sourceType==='workbook'&&sourceType!=='workbook'){
      report.skipped++;report.warnings.push({experimentId:experiment.id,message:'正式源工作簿暂不可读取，保留旧迁移投影，未用不完整档案覆盖。'});continue;
    }
    if(!design){design=convertHistory(experiment,unified);warnings.push(...design.conversion.warnings);}
    else if(historical){
      for(const source of sourceCandidates(experiment,sources))if(source.doc&&source.file!==sourceFile&&!/SOP/i.test(source.file)&&/批记录|实际记录/.test(source.file)){
        recoverWorkbookHistory(design,scopedSourceDoc(source.doc,experiment,warnings),{supplementary:true});
      }
      addHistoricalRecords(design,experiment,warnings);
    }
    // One anchor has one identity even when several archived branches shared a
    // workbook, or a source parser used the filename as its legacy design id.
    design.uid=stableUid('lab-mounted-experiment',experiment.id);design.id=design.uid;
    design.revisionUid=stableUid(design.uid,'revision:'+design.revision);design.batch={uid:stableUid(design.uid,'batch:1'),code:experiment.code,aliases:[experiment.code]};
    design.code=experiment.code;design.aliases=[...new Set([...design.aliases??[],experiment.code,...experiment.archiveCodes??[]])];
    if(historical)design.conversion={...design.conversion,sourceType,experimentId:experiment.id,...sourceFile?{sourceFile}:{},warnings:[...new Set([...design.conversion?.warnings??[],...warnings])]};
    else design.upgrade={...design.upgrade,experimentId:experiment.id};
    preserveBranchIds(design,experiment);
    if(historical){
      enrichHistoricalDesign(design,{...experiment,archiveRecords:out.experimentGroupArchive?.find(group=>group.motherId===experiment.id)?.records??[]},unified);
      enrichHistoricalLedger(design,experiment,sources.historicalLedger??[]);
      enrichHistoricalPlatform(design,{...experiment,archiveRecords:out.experimentGroupArchive?.find(group=>group.motherId===experiment.id)?.records??[]});
      supplement(design);
      bindHistoricalProductSamples(design,experiment,unified);
      if(repair)try{design=preserveHistoricalIdentity(previousDesign,design,historicalExperiment);}catch(error){if(error.code!=='HISTORY_REPAIR_REVIEW')throw error;defer(experiment,previousDesign,error.message);continue;}
    }
    const anchor=out.experiments.find(e=>e.id===experiment.id);
    if(historical)anchor.archive=true;
    else {anchor.archive=false;anchor.status=design.status;anchor.v2Archive??={schema:'lab-v2-original/1',plan:clone(experiment.plan??{}),actual:clone(experiment.actual??{}),status:experiment.status};}
    supplement(design);
    design=backfillMeasuredProducts(prepareIntegrationDesign(design),out.experiments.find(e=>e.id===experiment.id),unified);
    if(repair){retainHistoricalExecutionIdentity(previousDesign,design);report.repaired++;}
    if(historical){design.status='历史资料';design.conversion.historicalFidelityVersion=1;finishHistoricalPlatformRepair(design,historicalExperiment);}
    for(const execution of design.executions??[])execution.library??=clone(unified);
    out=mountDesign(out,experiment.id,design);design=mountedDesign(out,experiment.id);
    if(repair){
      const current=out.experiments.find(e=>e.id===experiment.id),oldNodes=samplingNodes(previousDesign),removed=[];
      current.samples=current.samples.filter(sample=>{
        const generated=oldNodes.some(node=>sample.id===stableUid(previousDesign.uid,'experiment-sample:'+node.uid));
        if(!generated||!sample.unassignedNode||sample.metrics?.length||sample.attachments?.length)return true;
        const rest={...out,experiments:out.experiments.map(e=>e.id===current.id?{...e,samples:e.samples.filter(s=>s.id!==sample.id)}:e)};
        if(JSON.stringify(rest).includes(sample.id))return true;
        removed.push(sample.name??sample.id);return false;
      });
      if(removed.length)design.historicalNotes.push({severity:'warning',message:'移除旧转换器生成、原档案未登记且无指标/附件/外部引用的空样品：'+removed.join('、')+'。所有原档案样品与指标均保留。',source:{file:design.conversion.sourceFile??'原档案'}});
    }
    compile(design,unified);
    designs.push(design);report.migrated++;report[sourceType]++;
    const entry={id:experiment.id,code:experiment.code,sourceType,...sourceFile?{sourceFile}:{},warnings};report.experiments.push(entry);
    report.warnings.push(...warnings.map(message=>({experimentId:experiment.id,message})));
    } catch(error) {
      // Discard only this experiment's partial projection. Its source records stay exact.
      out=checkpoint;
      out.experiments[out.experiments.findIndex(e=>e.id===experiment.id)]=originalExperiment;
      const retained=clone(previousDesign??blankDesign(experiment.code));
      retained.uid??=stableUid('lab-mounted-experiment',experiment.id);
      retained.executionIssues=[executionIssue(error)];
      if(repair||hasHistoricalProvenance(experiment))retained.conversion={...retained.conversion,historicalFidelityDeferred:1};
      const anchor=out.experiments.find(e=>e.id===experiment.id);
      anchor.design??={uid:retained.uid,revision:retained.revision};
      cache(out,{...documents(out),[retained.uid]:retained});designs.push(retained);
      report.failed++;report.warnings.push({experimentId:experiment.id,message:executionIssue(error).message});
    }
  }
  report.changed=Object.keys(out).some(key=>!Object.hasOwn(state,key))||report.failed>0||report.migrated>0||report.deferred>0||sampleFlagsChanged||enrichProperties||JSON.stringify(out.materials)!==JSON.stringify(state.materials);
  return {state:out,"state'":out,designs,report};
}
