import {executionIssue} from './design-issues.js';
import {executionMaterialLibrary} from './upgrade-materials.js';
import {freezeDesign,executionDesign,stableUid,aliases,identifyDesign} from './identity.js';
import {resolveSteps,samplingNodes,variableValues,applyDesignActual} from './design2.js';
import {compile} from './compile.js';
import {processProfile,deviation,checkDesign} from './design-analysis.js';
import {diffDesign} from './design-diff.js';
import {actualT0,relativeClock} from './record-values.js';
import {extractFactors} from './factors.js';
import {roleVar} from './var-roles.js';
import {repairStepReferences} from './repair.js';
import {projectLegacySamples,summarizeGpc} from './legacy-projection.js';
import {publicSource,publicFactors} from './public-provenance.js';

export const numeric=v=>typeof v==='number'&&Number.isFinite(v)?v:null;
export const quantity=(value,unit,state='computed',reason='未记录',source)=>({value:numeric(value),unit,state:numeric(value)===null?'missing':state,...numeric(value)===null?{reason}:{},...source!=null?publicSource(source):{}});
export function prepareIntegrationDesign(design){
  design=repairStepReferences(design);if(design.schema==='lab-design/2')design=identifyDesign(design);
  if(design.schema!=='lab-design/2'||!design.conversion||design.executions?.length)return design;
  const actual=design.actual??{},frozen=freezeDesign(design);
  return applyDesignActual(frozen,actual);
}
const identity=(source,d,code,uid)=>({schemaVersion:'1.0',source_instance_uid:source,uid,revision:1,code,aliases:aliases(d.aliases,code),updated_at:null});
const unitFor=key=>/钟点|\/开始$|\/结束$/.test(key)?'Excel day':/温/.test(key)?'°C':/含固率/.test(key)?'w/w':/时长/.test(key)?'h':/累计水量/.test(key)?'mL':/质量|取样量|\/M[12]$|实际值/.test(key)?'g':'1';
/** A product result belongs to that product's sampling node, never the latest
 * process sample. The source key remains visible to downstream consumers. */
export function recordedGpc(plan,node,design=plan,{metrics=[]}={}){
  const output={};for(const [field,key]of [['Mn','Mn'],['Mw','Mw'],['dispersity','Đ']]){const recordKey=`record-samples/${node.id}/${key}`,value=plan.actual?.[recordKey]??(key==='Đ'?plan.actual?.[`record-samples/${node.id}/dispersity`]:undefined);if(numeric(value)===null)continue;
    const file=[...(design.studio?.recordImports??[])].reverse().find(entry=>Object.hasOwn(entry.rawValues??{},recordKey))?.file;
    output[field]={...quantity(value,key==='Đ'?'1':'g/mol','measured'),source:'批记录导入',source_ref:{design_uid:design.uid,revision_uid:plan.revisionUid,record_key:recordKey,...file?{file}:{}}};
  }
  return {...output,...summarizeGpc(metrics)};
}
function actualRecord(value,key,units){
  const cellUnit=units.get(key);
  return typeof value==='string'&&!/^[+-]?\d/.test(value)&&unitFor(key)==='1'?{value,unit:'1',state:'measured'}:{...quantity(value,cellUnit||unitFor(key),'measured','未记录或无法识别的记录',key),...numeric(value)===null?{raw:value}:{}};
}
export function upsertConsumptions(existing,entries){
  if(!Array.isArray(entries)||entries.length>10000)throw Error('consumptions 必须是数组（最多10000条）');
  const out=structuredClone(existing??[]);
  for(const entry of entries){
    if(typeof entry.source_system!=='string'||!entry.source_system.trim()||typeof entry.external_id!=='string'||!entry.external_id.trim()||numeric(entry.amount_g)===null||entry.amount_g<0||typeof entry.purpose!=='string'||typeof entry.ref!=='string'||typeof entry.at!=='string'||!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(entry.at)||!Number.isFinite(Date.parse(entry.at)))throw Error('用量需 source_system/external_id、非负 amount_g、purpose/ref、ISO at');
    const clean=Object.fromEntries(['source_system','external_id','amount_g','purpose','ref','at'].map(k=>[k,entry[k]]));
    const i=out.findIndex(e=>e.source_system===clean.source_system&&e.external_id===clean.external_id);
    if(i<0)out.push(clean);else out[i]=clean;
  }
  return out;
}
export function availableMass(produced,consumptions,sampled,{reasons=[]}={}){
  reasons=[...reasons];
  if(numeric(produced.value)===null)return {value:null,unit:'g',state:'partial',reason:['产出质量缺失',...reasons].join('；')};
  if(produced.state!=='measured')return {value:null,unit:'g',state:'planned',reason:`计划产量 ${produced.value.toFixed(2)} g，未称量`};
  if(consumptions.some(c=>numeric(c.amount_g)===null))reasons.push('存在未知用量，仅扣除数值记录');
  if(numeric(sampled)===null)reasons.push('取样合计未知');
  const value=numeric(produced.value-consumptions.reduce((n,c)=>n+(numeric(c.amount_g)??0),0)-(numeric(sampled)??0));
  if(value===null)reasons.push('已知扣减计算超出有限数范围');
  return {value,unit:'g',state:reasons.length?'partial':'computed',...reasons.length?{reason:reasons.join('；')}:{}};
}
function seriesRecord(plan,library,base){
  const p=processProfile(plan,library),time=(hours,actual=false)=>quantity(actual&&!p.axis.actual?null:hours,'h',actual?'computed':'planned',actual?'实际t0或记录钟点缺失':'未声明相对时间');
  const points=(arr,valueKey,unit,state)=>arr.map(v=>({step_id:v.stepId??null,node_id:v.nodeId??null,branch:v.branch??null,time:time(v.hours,state==='measured'),value:quantity(v[valueKey],unit,state)}));
  return {...base,axis:{unit:'h',t0:quantity(p.axis.actual?p.axis.t0:null,'Excel day','measured','实际t0未记录'),basis:p.axis.actual?'actual_t0':'planned_relative_only'},
    temperature:{planned:points(p.temperature.planned,'tempC','°C','planned'),actual:points(p.temperature.actual,'tempC','°C','measured'),outer_actual:points(p.temperature.actual,'outerTempC','°C','measured')},
    solids:{planned:points(p.solids.planned,'value','w/w','planned'),actual:points(p.solids.actual,'nominalSolids','w/w','computed').map((v,i)=>({...v,time:time(p.solids.actual[i].hours,true)})),measured:points(p.solids.actual,'solids','w/w','measured'),workup:p.solids.measuredWorkup.map(v=>({step_id:v.stepId,value:quantity(v.solids,'w/w','measured')}))},
    water:{actual:points(p.water.actual,'waterMl','mL','measured'),theoretical_ceiling:quantity(p.water.theoreticalCeilingMl,'mL'),ceiling_only:true},
    volume:{planned:points(p.volume.planned,'volumeMl','mL','planned'),actual:points(p.volume.actual,'volumeMl','mL','computed').map((v,i)=>({...v,time:time(p.volume.actual[i].hours,true)})),fill_planned:points(p.volume.planned,'fill','w/w','planned'),fill_actual:points(p.volume.actual,'fill','w/w','computed').map((v,i)=>({...v,time:time(p.volume.actual[i].hours,true)}))},
    workup:{mass_flow:p.workup.massFlow.map(v=>({step_id:v.stepId,branch:v.branch??null,...Object.fromEntries(['incomingMassG','plannedAddedSolventG','addedSolventG','wetCakeG','liquidG','dryProductG'].map(k=>[k,quantity(v[k],'g',k.startsWith('planned')?'planned':k==='incomingMassG'?'computed':'measured')]))})),dry_basis:p.workup.dryBasis.map(v=>({step_id:v.stepId,branch:v.branch??null,solid:quantity(v.solidDryG,'g'),liquid:quantity(v.liquidDryG,'g'),closure:quantity(v.closure,'w/w')})),expected_product:quantity(p.workup.expectedProductG,'g','planned'),yield:quantity(p.workup.apparentYield,'w/w'),basis:p.workup.basis}};
}
/** Headless projection. Persistence assigns monotonic record revisions/timestamps.
 * Frozen execution dependencies are supplied per execution by the store. */
function buildIntegrationRecordsUnsafe(design,library,registry,{source_instance_uid,consumptions={},executionLibraries={},experiment}){
  const result={design:[],batch:[],lot:[],sample:[],legacy_sample:[]};
  if(design.schema!=='lab-design/2')return result;
  const experimentId=experiment?.id??design.source?.experimentId??null,legacy=projectLegacySamples(design,experiment,library,{source_instance_uid,executionLibraries});result.legacy_sample=legacy.legacy_sample;
  for(const [index,execution]of (design.executions??[]).entries()){
    const frozen=design.frozenRevisions?.find(r=>r.uid===execution.revisionUid);if(!frozen)throw Error('执行缺少冻结计划：'+execution.uid);
    const plan=executionDesign(design,execution.revisionUid),lib=executionMaterialLibrary(executionLibraries[execution.uid]??execution.library??library,library,plan),steps=resolveSteps(plan,lib),doc=compile(plan,lib),values=variableValues(doc.vars);
    const base=identity(source_instance_uid,execution,execution.code,execution.uid);
    const nodes=samplingNodes(plan,steps),batchNodes=nodes.map(n=>({...n,uid:index?stableUid(execution.uid,'sample:'+n.uid):n.uid,lotUid:n.lotUid?(index?stableUid(execution.uid,'lot:'+n.lotUid):n.lotUid):undefined}));
    const factors=publicFactors(extractFactors(plan,registry,lib)),profile=seriesRecord(plan,lib,base);
    // Cell units belong to this compiled execution. Index once, preserving the
    // first matching cell used by the previous per-value linear search.
    const units=new Map();for(const sheet of doc.sheets)for(const block of sheet.blocks)if(block.type==='table')for(const row of block.rows)row.cells.forEach((cell,i)=>{const key=block.id+'/'+row.id+'/'+block.header?.[i]?.key;if(!units.has(key))units.set(key,cell.unit);});
    const products=batchNodes.filter(n=>n.lotUid),source=Object.fromEntries(Object.entries(plan.actual??{}).map(([key,value])=>[key,actualRecord(value,key,units)]));
    result.design.push({...identity(source_instance_uid,frozen,plan.code,design.uid),revision:frozen.revision,revision_uid:frozen.uid,experiment_id:experimentId,code:plan.code,aliases:aliases(design.aliases,plan.code,frozen.code),plan:structuredClone(frozen.plan),charges:doc.modular.charges.map(c=>({id:c.id,material:c.materialId,role:c.role,mass:quantity(values.get(c.keys.mass),'g','planned'),mmol:quantity(values.get(c.keys.mmol),'mmol','planned'),volume:quantity(values.get(c.keys.volume),'mL','planned')})),parent_uid:plan.parentUid??null,diff:plan.parentDesign?diffDesign(plan.parentDesign,plan,lib):null});
    const batch={...base,experiment_id:experimentId,design_uid:design.uid,design_revision:frozen.revision,revision_uid:frozen.uid,status:design.status==='设计中'?'执行中':design.status,actual_t0:quantity(actualT0(plan),'Excel day','measured'),actual:source,events:structuredClone(plan.events??[]),deviations:deviation(plan,lib).map(d=>({key:d.key,step_id:d.stepId??null,planned:quantity(d.planned,d.unit,'planned'),actual:quantity(d.actual,d.unit,'measured'),delta:quantity(d.delta,d.unit),reason:d.reason??'',message:d.message})),factors,factor_registry_version:registry.version,lot_uids:products.map(n=>n.lotUid),sample_uids:batchNodes.map(n=>n.uid),series:profile,key_results:{yield:profile.workup.yield,closure:profile.workup.dry_basis.findLast(v=>v.closure.value!==null)?.closure??quantity(null,'w/w','missing','缺少完整固液干基数据'),gpc:products.length===1?recordedGpc(plan,products[0],design,{metrics:legacy.forNode(execution.revisionUid,products[0].id).metrics}):{}}};
    batch.plan=structuredClone(frozen.plan);result.batch.push(batch);
    for(const n of batchNodes){
      const product=products.find(p=>p.branch===n.branch),kind=n.lotUid?'product':n.hours!=null?'process':'workup';
      const clock=plan.actual?.[`record-timeline/${n.id}/实际钟点`],t0=actualT0(plan);
      result.sample.push({...identity(source_instance_uid,n,n.sampleId,n.uid),batch_uid:execution.uid,design_uid:design.uid,experiment_id:experimentId,legacy_ids:legacy.forNode(execution.revisionUid,n.id).legacy_ids,legacy_links:legacy.forNode(execution.revisionUid,n.id).legacy_links,metrics:legacy.forNode(execution.revisionUid,n.id).metrics,key_results:{gpc:recordedGpc(plan,n,design,{metrics:legacy.forNode(execution.revisionUid,n.id).metrics})},kind,node_id:n.id,branch:n.branch??null,rel_time_h:{planned:quantity(n.hours,'h','planned'),actual:quantity(t0!==null&&clock!=null?relativeClock(clock,t0,n.hours):null,'h','computed','实际t0或取样钟点未记录')},stage:n.stage??(n.hours==null?n.label??n.id:null),parent:kind==='product'?n.lotUid:kind==='workup'&&product?product.lotUid:execution.uid,planned_tests:n.plannedTests,sampled_mass:quantity(plan.actual?.[`record-samples/${n.id}/取样量`]??plan.actual?.[`record-timeline/${n.id}/取样量`],'g','measured')});
    }
    // A route of a split work-up (R100-36A …) records its product on the route's own row,
    // and its share is the portion weighed at the split (else the planned share).
    const routes=plan.procedure?.routes?.length>1?plan.procedure.routes:[],portions=routes.map(r=>numeric(plan.actual?.[`record-operations/${plan.procedure.routeFrom}@${r.id}/质量`]));
    const routeShare=id=>{const i=routes.findIndex(r=>r.id===id);return portions.every(v=>v>0)?portions[i]/portions.reduce((a,b)=>a+b,0):Number(routes[i].fraction??1/routes.length);};
    for(const n of products){
      const route=routes.find(r=>r.id===(n.route!==undefined?n.route:/^([A-Z])-/.exec(n.id)?.[1]))?.id;
      const prefix=`record-operations/${n.stepId}${route?'@'+route:''}/`,keys=Object.keys(plan.actual??{}),ownSteps=steps.filter(s=>!n.branch||s.branch===n.branch),finalKeys=[prefix+'M2',prefix+'干品质量',...!n.stepId&&products.length===1?ownSteps.filter(s=>/恒重/.test(s.title)).map(s=>'record-operations/'+s.id+'/M2'):[],...products.length===1?['record-results/干品质量']:[]];
      const measuredKey=finalKeys.find(k=>numeric(plan.actual?.[k])!==null),expected=roleVar(plan.vars,n.branch?'branchExpectedProduct':'expectedProduct');
      const produced=measuredKey?quantity(plan.actual[measuredKey],'g','measured','未记录',plan.actualSources?.[measuredKey]??measuredKey):quantity(expected?values.get(expected.key):null,'g','planned','最终干品未记录且没有预计产品',expected?'vars/'+expected.key:null);
      const samples=result.sample.filter(s=>s.batch_uid===execution.uid&&(!n.branch||s.branch===n.branch));
      // Include actual extra rows too. One weighing per node; samples table wins.
      const recorded=new Map();for(const block of ['record-timeline','record-samples'])for(const key of keys.filter(k=>k.startsWith(block+'/')&&k.endsWith('/取样量'))){const id=key.split('/')[1];recorded.set(id,{value:plan.actual[key],branch:batchNodes.find(s=>s.id===id)?.branch??null});}
      const own=[...recorded.values()].filter(s=>!n.branch||s.branch===n.branch),sampled=own.reduce((sum,s)=>sum+(numeric(s.value)??0),0),reasons=[];
      if(own.some(s=>numeric(s.value)===null))reasons.push('存在无法识别的取样质量，仅扣除数值记录');
      if(n.branch&&[...recorded.values()].some(s=>!s.branch&&numeric(s.value)!==null))reasons.push('共同反应取样未分摊到产品支路');
      const used=consumptions[n.lotUid]??[],theory=roleVar(plan.vars,'theoreticalResin'),fraction=route?routeShare(route):n.branch?plan.historicalBranches?.[n.branch]?.fraction??findFraction(plan.steps,n.branch):1;
      const historical=experiment?.archive===true&&['history','workbook'].includes(design.conversion?.sourceType);
      const historicalBasis=historical&&!route?plan.historicalBranchResults?.[n.branch]:undefined;
      const pendingBranchYield=!route&&experiment?.archive===true&&/^R100-(36|37)$/.test(experiment.code)&&n.branch&&!historicalBasis;
      const typedTheory=numeric(plan.actual?.['record-results/理论量']),theoryMass=typedTheory>0?typedTheory:theory?values.get(theory.key):null;
      const denominator=pendingBranchYield?null:historicalBasis?historicalBasis.theoretical_mass_g*historicalBasis.fraction:theoryMass!=null&&fraction!=null?theoryMass*fraction:null;
      const nominalDenominator=theory&&fraction!=null?values.get(theory.key)*fraction:null;
      const branchResults=route?{yield_basis:{theoretical_mass_g:theoryMass,fraction,basis:portions.every(v=>v>0)?'分料称重':'计划占比'}}:historicalBasis||pendingBranchYield?{...historicalBasis?{yield_basis:structuredClone(historicalBasis)}:{},nominal_apparent_isolation_ratio:{label:'名义表观分离比（等分口径）',...quantity(produced.state==='measured'&&nominalDenominator>0?produced.value/nominalDenominator:null,'w/w','computed','名义理论量或最终干品缺失')}}:{};
      const branchLinks=historicalBasis?.variant_legacy_links??[];
      const lotFactors=n.branch?publicFactors(extractFactors({plan,branch:n.branch},registry,lib)):factors;
      result.lot.push({...identity(source_instance_uid,n,n.sampleId,n.lotUid),batch_uid:execution.uid,design_uid:design.uid,experiment_id:experimentId,legacy_ids:aliases(legacy.forNode(execution.revisionUid,n.id).legacy_ids,...historicalBasis?.variant_legacy_ids??[]),legacy_links:[...legacy.forNode(execution.revisionUid,n.id).legacy_links,...branchLinks.filter(link=>!legacy.forNode(execution.revisionUid,n.id).legacy_links.some(old=>old.legacy_id===link.legacy_id))],lot_origin:'produced',design_revision:frozen.revision,revision_uid:frozen.uid,status:produced.state==='measured'?'produced':'planned',form:n.form??'粉末',produced_mass:produced,consumptions:structuredClone(used),sampled_mass_g:sampled,sampled_mass:quantity(sampled,'g','computed'),available:availableMass(produced,used,sampled,{reasons}),factors:lotFactors,factor_registry_version:registry.version,key_results:{...branchResults,yield:quantity(produced.state==='measured'&&denominator>0?produced.value/denominator:null,'w/w','computed',pendingBranchYield?'分支产率口径待核对：名义等分口径不作为产率':'最终干品或理论树脂基准缺失'),closure:profile.workup.dry_basis.findLast(v=>v.branch===(n.branch??null)&&v.closure.value!==null)?.closure??quantity(null,'w/w','missing','缺少完整固液干基数据'),gpc:recordedGpc(plan,n,design,{metrics:legacy.forNode(execution.revisionUid,n.id).metrics})},sample_uids:samples.map(s=>s.uid),product_sample_uid:n.uid,branch:n.branch??null});
    }
  }
  return result;
}
export function findFraction(steps,branch){for(const s of steps??[])for(const b of s.branches??[]){if(b.id===branch)return b.fraction;const nested=findFraction(b.steps,branch);if(nested!=null)return nested;}return null;}

/** A stored design can be incomplete. Publish its identity and a repair issue,
 * without publishing stale calculated quantities or aborting the entire index. */
export function failedIntegrationRecords(design,error,{source_instance_uid,experiment,factor_registry_version=1}={}){
  const issue=executionIssue(error),base=identity(source_instance_uid,design,design.code,design.uid);
  const unknown=unit=>quantity(null,unit,'missing',issue.message),results=()=>({yield:unknown('w/w'),closure:unknown('w/w'),gpc:{}});
  const series=base=>({...base,issues:[issue],axis:{unit:'h',t0:unknown('Excel day'),basis:'planned_relative_only'},temperature:{planned:[],actual:[],outer_actual:[]},solids:{planned:[],actual:[],measured:[],workup:[]},water:{actual:[],theoretical_ceiling:unknown('mL'),ceiling_only:true},volume:{planned:[],actual:[],fill_planned:[],fill_actual:[]},workup:{mass_flow:[],dry_basis:[],expected_product:unknown('g'),yield:unknown('w/w'),basis:'执行核对，未生成计算结果'}});
  return {failed:true,design:[{...base,revision:design.revision??1,revision_uid:design.revisionUid,experiment_id:experiment?.id??null,plan:structuredClone(design),charges:[],parent_uid:design.parentUid??null,diff:null,issues:[issue]}],batch:(design.executions??[]).map(e=>{const identityFields=identity(source_instance_uid,e,e.code,e.uid);return {...identityFields,design_uid:design.uid,experiment_id:experiment?.id??null,design_revision:e.revision??design.revision??1,revision_uid:e.revisionUid,status:'执行核对',actual_t0:unknown('Excel day'),actual:{},unprojected_actual:structuredClone(e.actual??{}),events:[],deviations:[],factors:{},factor_registry_version,lot_uids:[],sample_uids:[],key_results:results(),issues:[issue],series:series(identityFields)};}),lot:[],sample:[],legacy_sample:[],issues:[issue]};
}
export function buildIntegrationRecords(design,library,registry,options={}){
  options={...options,factor_registry_version:registry?.version??1};
  try{
    // DOE drafts publish planned records using the existing projection identities.
    // The temporary execution snapshot never enters the stored design.
    const draft=!!design.series?.doe_run_id&&!design.executions?.length;
    const result=buildIntegrationRecordsUnsafe(draft?freezeDesign(design):design,library,registry,options);
    const metadata=Object.fromEntries(['doe_id','doe_run_id'].filter(key=>design.series?.[key]!=null).map(key=>[key,design.series[key]]));
    for(const record of [...result.design,...result.batch,...result.lot])Object.assign(record,metadata);
    if(draft)for(const batch of result.batch)batch.status=design.status;
    const issues=checkDesign(design,library).filter(i=>i.message.startsWith('执行核对：'));
    if(issues.length){
      result.issues=issues;
      if(!result.design.length)result.design=failedIntegrationRecords(design,issues[0].message,options).design;
      for(const record of [...result.design,...result.batch])record.issues=issues;
    }
    return result;
  }
  catch(error){return failedIntegrationRecords(design,error,options);}
}
