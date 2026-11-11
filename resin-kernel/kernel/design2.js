import {syncVesselCapacities} from './vessel-capacity.js';
import {identifyDesign,editRevision,newUid,stableUid,aliases} from './identity.js';
import {leanRecords} from './record-spec.js';
import {actualT0,relativeClock,timelineClock,parseClock} from './record-values.js';
import {findMaterial,materialPurityFraction,sopMaterialName,materialAbbreviation} from './materials.js';
import {findModule,moduleDefaults,argumentExpr,instantiateModule,moduleCategory,declaredModuleCharges} from './modules.js';
import {cleanKey} from './parse-workbook.js';
import {effectiveArgs,bindInsertedArgs,localExpressionRewrite} from './param-bindings.js';
import {roleVar} from './var-roles.js';
import {plannedNodeHour,renderPlanTemplate,holdTimeVariable,activeTitleTemplate,refreshTemplateTitles} from './schedule-bindings.js';
import {parseExpr,parseTemplate,evaluateExpr,mapRefs,serializeExpr} from './expr.js';
import {normalizeSamplingIdentity} from './sampling-identity.js';
import {generatedMaterialText} from './generated-material-text.js';
const clone=value=>structuredClone(value);
export function blankDesign(code='R100-1'){
  return identifyDesign({schema:'lab-design/2',paramBindingVersion:2,id:code,code,parentCode:'',title:`${code}树脂合成SOP`,purpose:'',question:'',hypothesis:'',conclusion:{verdict:'不确定',text:'',date:''},status:'设计中',roadmapNode:null,flaskMl:null,attachments:[],events:[],notes:[],revision:1,vars:[{key:'批次编号',label:'批次编号',role:'code',value:code,unit:''}],materials:[],charges:[],steps:[],samplingPlan:{t0Clock:'10:00',points:[],nodes:[]},records:[],overview:{panels:['flow','charges','temperature','params','samples']},actual:{},extras:[]});
}
export function setVariable(design,variable){const out=editRevision(design),old=out.vars.find(item=>item.key===variable.key),v={label:variable.key,unit:'',...old,...variable};if(!v.key)throw Error('变量 key 不能为空');const i=out.vars.findIndex(x=>x.key===v.key);if(i<0)out.vars.push(v);else out.vars[i]=v;syncVesselCapacities(out);return refreshTemplateTitles(out);}
export function useMaterial(design,ref,as=ref){const out=editRevision(design);if(out.materials.some(m=>m.as===as&&m.ref!==ref))throw Error(`物料别名冲突：${as}`);if(!out.materials.some(m=>m.as===as))out.materials.push({ref,as});return out;}
export function addCharge(design,charge,stepId){const out=editRevision(design);if(!charge.id||/[\[\]{}|@/]/.test(charge.id)||flattenSteps(out.steps).flatMap(s=>s.charges??[]).concat(out.charges).some(c=>c.id===charge.id))throw Error('投料 id 无效或重复');(stepId?locateStep(out,stepId).step.charges??=[]:out.charges).push(clone(charge));return out;}
export function flattenSteps(steps){return (steps??[]).flatMap(s=>[s,...(s.branches??[]).flatMap(b=>flattenSteps(b.steps).map(st=>({...st,branch:st.branch??b.id}))) ]);}
// The stage lanes and SOP define execution order. Keep order within each lane,
// including explicit insertions/moves; branches retain their own ordered lanes.
export function orderedSteps(steps,library={}){const rank={prep:0,reaction:1,workup:2};return [...steps??[]].sort((a,b)=>(rank[a.stage??findModule(library,a.module)?.stage]??1)-(rank[b.stage??findModule(library,b.module)?.stage]??1)).map(s=>({...s,...s.branches?{branches:s.branches.map(b=>({...b,steps:orderedSteps(b.steps,library)}))}:{}}));}
function locateStep(design,id){function walk(steps){for(let index=0;index<steps.length;index++){if(steps[index].id===id)return {steps,index,step:steps[index]};for(const b of steps[index].branches??[]){const found=walk(b.steps);if(found)return found;}}}const found=walk(design.steps);if(!found)throw Error(`找不到步骤：${id}`);return found;}
const freshId=(base,used)=>{let id=base,n=2;while(used.has(id))id=`${base}_${n++}`;return id;};
export function insertFromModule(design,library,moduleId,{id,index,afterId,beforeId,args={},...overrides}={}){
  const module=findModule(library,moduleId);if(!module)throw Error(`找不到模块：${moduleId}`);
  const out=editRevision(design),used=new Set(flattenSteps(out.steps).map(s=>s.id));id??=freshId(module.id,used);if(used.has(id)||/[\[\]{}|@/]/.test(id))throw Error('步骤 id 无效或重复');
  const endpoint=/^终点取样/.test(module.name),hold=endpoint?holdTimeVariable(out):null;
  if(hold)for(const p of module.params??[])if(p.unit==='h'&&/总反应时间|恒温反应时间/.test(p.key)&&!Object.hasOwn(args,p.key))args={...args,[p.key]:`[${hold.key}]`};
  const step={id,module:module.id,title:module.name,stage:module.stage,...bindInsertedArgs(out,module,args),...module.conditions?{conditions:clone(module.conditions)}:{},...overrides};
  if(afterId||beforeId){const where=locateStep(out,afterId??beforeId);where.steps.splice(where.index+(afterId?1:0),0,step);}else out.steps.splice(index??out.steps.length,0,step);return out;
}
export function moveStep(design,id,index){const out=editRevision(design),where=locateStep(out,id);if(!Number.isInteger(index)||index<0||index>=where.steps.length)throw Error('步骤位置无效');where.steps.splice(where.index,1);where.steps.splice(index,0,where.step);return out;}
export function deleteStep(design,id){const out=editRevision(design),where=locateStep(out,id);where.steps.splice(where.index,1);return out;}
export function duplicateStep(design,id,{newId,index}={}){
  const out=editRevision(design),where=locateStep(out,id),used=new Set(flattenSteps(out.steps).map(s=>s.id)),copy=clone(where.step),renames=new Map();
  for(const s of flattenSteps([copy])){const next=s.id===id?(newId??freshId(s.id,used)):freshId(s.id,used);if(used.has(next))throw Error('步骤 id 重复');used.add(next);renames.set(s.id,next);}
  const variableRenames=new Map(),chargeRenames=new Map();
  const rewrite=steps=>{for(const s of steps){const old=s.id;s.id=renames.get(old);for(const c of s.charges??[]){const before=chargeKeys(c),oldCharge=c.id;c.id=`${s.id}_${oldCharge}`;chargeRenames.set(oldCharge,c.id);if(c.as)c.as=`${s.id}_${c.as}`;const after=chargeKeys(c);for(const key of Object.keys(before))variableRenames.set(before[key],after[key]);}for(const n of s.samples??[]){n.id=`${s.id}_${n.id??n.suffix}`;delete n.uid;delete n.lotUid;delete n.sampleId;delete n.aliases;};for(const b of s.branches??[])rewrite(b.steps);}};rewrite([copy]);
  const rewriteValues=value=>{if(typeof value==='string')return value.replace(/\[([^\]]+)\]/g,(token,key)=>variableRenames.has(key)?`[${variableRenames.get(key)}]`:token);if(Array.isArray(value))return value.map(rewriteValues);if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,v])=>[key,key==='of'&&chargeRenames.has(v)?chargeRenames.get(v):rewriteValues(v)]));return value;};
  // Copy the plan only. Existing actual entries stay attached to their original IDs.
  where.steps.splice(index??where.index+1,0,rewriteValues(copy));return out;
}
export function setStepArg(design,id,key,value){const out=editRevision(design),step=locateStep(out,id).step;step.args[key]=value;step.paramOverrides=[...new Set([...step.paramOverrides??[],key])];if(step.module==='add-and-hold'&&key==='eq')step.paramOverrides=step.paramOverrides.filter(k=>k!=='mass');step.syncedParams=(step.syncedParams??[]).filter(k=>k!==key);return out;}
export function overrideStepText(design,id,text){const out=editRevision(design),s=locateStep(out,id).step;s.userEditedFields={...s.userEditedFields,text:text!==undefined};if(text===undefined)delete s.text;else s.text=text;return out;}
export function setStepConditions(design,id,conditions){const out=editRevision(design);locateStep(out,id).step.conditions=clone(conditions);return out;}
export function splitIntoBranches(design,id,branches){const out=editRevision(design),step=locateStep(out,id).step;if(step.module!=='split')throw Error('只有分料步骤可以建立分支');if(branches.length<2||new Set(branches.map(b=>b.id)).size!==branches.length)throw Error('至少需要两个不同分支');if(branches.some(b=>!Number.isFinite(b.fraction)||b.fraction<=0)||Math.abs(branches.reduce((s,b)=>s+b.fraction,0)-1)>1e-9)throw Error('分支比例之和必须为 1');step.branches=clone(branches).map(b=>({...b,steps:b.steps??[]}));const ids=flattenSteps(out.steps).map(s=>s.id);if(new Set(ids).size!==ids.length)throw Error('分支步骤 id 重复');return out;}
export function keepBranchAndMerge(design,id,branchId){const out=editRevision(design),where=locateStep(out,id),branch=where.step.branches?.find(b=>b.id===branchId);if(!branch)throw Error('找不到分支');where.steps.splice(where.index,1,...branch.steps.map(s=>{delete s.branch;return s;}));return out;}
export function setSamplingPlan(design,plan){return normalizeSamplingIdentity({...editRevision(design),samplingPlan:clone(plan)});}
export function applyDesignActual(design,values,{meta,flags={},newRows=[],clearKeys=[]}={}){
  const out=clone(design);out.actual??={};
  const revisionUid=meta?.revisionUid??out.revisionUid;
  if(meta?.designUid&&meta.designUid!==out.uid)throw Error('批记录属于其他设计，不能写入本批');
  if(revisionUid!==out.revisionUid&&!out.frozenRevisions?.some(r=>r.uid===revisionUid))throw Error('找不到批记录执行时的设计版本');
  const execution=out.executions?.find(e=>e.revisionUid===revisionUid);
  const recorded=execution?(execution.actual??={}):out.actual;
  for(const [key,value]of Object.entries(values)){if(value!==null&&value!==undefined&&value!=='')recorded[key]=value;else if(clearKeys.includes(key))delete recorded[key];}
  out.actual={...recorded};out.actualRevisionUid=revisionUid;out.actualFlags={...out.actualFlags};for(const key of Object.keys(values))if(values[key]!=null&&values[key]!=='')delete out.actualFlags[key];Object.assign(out.actualFlags,flags);
  out.importedRows=[...out.importedRows??[],...newRows.filter(r=>!out.importedRows?.some(x=>x.id===r.id&&x.block===r.block))];
  if(execution){execution.actualFlags=out.actualFlags;execution.importedRows=out.importedRows;}
  return out;
}
export function deriveDesign(design,{code,title}){
  if(!code)throw Error('派生批号不能为空');const out=clone(design);out.id=out.code=code;out.parentCode=design.code;out.parentUid=design.uid;
  const parentPlan=clone(design);for(const key of ['parentDesign','frozenRevisions','executions','actual'])delete parentPlan[key];out.parentDesign=parentPlan;
  out.title=title??design.title.replaceAll(design.code,code);out.uid=newUid();out.aliases=[code];out.revision=1;out.revisionUid=newUid();out.revisionCode=`${code}@r1`;out.revisionAliases=[out.revisionCode];out.batch={uid:newUid(),code,aliases:[code]};out.status='设计中';out.paramBindingVersion=2;out.actual={};
  // The new batch asks its own question; the parent's purpose and conclusion stay with the parent.
  Object.assign(out,{purpose:'',question:'',hypothesis:'',conclusion:{verdict:'不确定',text:'',date:''}});
  for(const key of ['frozenRevisions','executions','actualRevisionUid','actualFlags','actualSources','recordConflicts','historicalRecords','importedRows','series'])delete out[key];
  // Historical evidence describes the parent's executed batch, not the new
  // recipe. Keep it in parentDesign, never as active factors or filled records
  // on a derived experiment whose conditions and materials can now change.
  for(const key of ['historicalBranches','historicalSummary','historicalLedger','historicalNotes','historicalEquipment','historicalFormulations','historicalCharges','historicalYieldChecks','historicalObjective','historicalRepair','historicalBranchResults','sourcePlanBaseline','workbookFacts'])delete out[key];
  for(const key of Object.keys(out))if(key.startsWith('historicalPlatform'))delete out[key];
  if(out.conversion)for(const key of Object.keys(out.conversion))if(key.startsWith('historicalPlatform'))delete out.conversion[key];
  // 原档案投料/参数/过程日志 are the parent's converted Excel record, not fields of the new batch.
  out.records=(out.records??[]).filter(record=>!/^(workbook-evidence-|history-)/.test(record.id??''));
  // So do the parent's workbook notes (A/B, DMAP …); the new batch writes its own on the design page.
  out.notes=(Array.isArray(out.notes)?out.notes:[]).filter(note=>!(out.archiveNotes??[]).includes(note));out.archiveNotes=[];
  // The parent's original Excel wording stays with the parent.
  const dropSource=steps=>{for(const step of steps??[]){delete step.legacySourceText;for(const branch of step.branches??[])dropSource(branch.steps);}};
  dropSource(out.procedure?.steps);
  // The parent snapshot retains source evidence. A child carries the recipe and
  // record-field choices, while imports, inventory use and visibility belong to
  // the parent's execution and must not make the new batch appear recorded.
  if(out.studio){for(const key of ['recordImports','consumptions','hiddenLots'])delete out.studio[key];if(!Object.keys(out.studio).length)delete out.studio;}
  out.events=[];
  // A route's samples and lot are named after the new batch (R100-42A), not the parent's.
  for(const n of [...out.samplingPlan.nodes??[],...out.procedure?.legacyNodes??[]])if(typeof n.branch==='string'&&n.branch.startsWith(design.code))n.branch=code+n.branch.slice(design.code.length);
  for(const n of [...out.samplingPlan.nodes??[],...out.samplingPlan.points.filter(x=>typeof x==='object')]){delete n.uid;delete n.lotUid;delete n.sampleId;delete n.aliases;delete n.legacySampleId;delete n.historicalSampleSource;}
  const walk=steps=>{for(const s of steps){for(const n of s.samples??[]){delete n.uid;delete n.lotUid;delete n.sampleId;delete n.aliases;delete n.legacySampleId;delete n.historicalSampleSource;}for(const b of s.branches??[])walk(b.steps);}};walk(out.steps);
  for(const v of out.vars)if(v.role==='code'){delete v.expr;v.value=code;}return out;
}

// Human sample codes may inherit source batch/branch labels. Physical node IDs
// remain stable; only generated display aliases use the current design code.
export function plannedSampleCode(code,node){
  const batch=/^([A-Z]{2,}\d+-\d+)([A-Z]?)(?:-|$)(.*)$/i;
  let branch=String(node.branch??''),suffix=String(node.suffix??node.id??'sample');
  const branchMatch=/^[A-Z]{2,}\d+-\d+([A-Z]*)$/i.exec(branch);if(branchMatch)branch=branchMatch[1];
  const prefixed=/^([A-Z])-([A-Z]{2,}\d+-\d+.*)$/i.exec(suffix);if(prefixed){branch||=prefixed[1];suffix=prefixed[2];}
  const source=batch.exec(suffix);if(source){branch||=source[2];suffix=source[3]||'sample';}
  if(branch&&suffix.startsWith(branch+'-'))suffix=suffix.slice(branch.length+1);
  return `${code}${branch}-${suffix}`;
}
export function samplingNodes(design,steps=flattenSteps(design.steps)){
  const plan=normalizeSamplingIdentity({samplingPlan:clone(design.samplingPlan??{}),actual:design.actual}).samplingPlan,nodes=(plan.points??[]).map(point=>({tests:['GPC'],...clone(point)}));
  nodes.push(...clone(plan.nodes??[]));for(const step of steps)for(const sample of step.samples??[]){const suffix=sample.id??sample.suffix??'sample';nodes.push({tests:['GPC','NMR'],...sample,id:step.branch?`${step.branch}-${suffix}`:sample.id??`${step.id}-${suffix}`,stepId:step.id,...step.branch?{branch:step.branch,suffix}:{}});}
  const timeVars=[...(design.vars??[]),...steps.flatMap(s=>s.paramVars??[])];
  for(const node of nodes){if(node.hoursExpr)node.hours=plannedNodeHour(node,timeVars);if(node.sampleCodeBinding==='time'&&node.hours!=null){node.aliases=aliases(node.aliases,node.sampleId);node.sampleId=`${design.code}-${node.hours.toFixed(1)}h${node.id==='PRE-AddH'?'-PRE-AddH':''}`;}if(node.labelTemplate)node.label=renderPlanTemplate(node.labelTemplate,timeVars);else if(/^t-/.test(node.id)&&node.hoursExpr)node.label=`${node.hours} h`;}
  for(let i=nodes.length-1;i>=0;i--){const generic=nodes[i];if(!generic.sameSampleAs)continue;const physical=nodes.find(n=>n!==generic&&n.id===generic.sameSampleAs);if(physical){physical.tests=[...new Set([...physical.tests??[],...generic.tests??[]])];physical.aliases=aliases(physical.aliases,generic.sampleId,`${design.code}-${generic.hours.toFixed(1)}h`);nodes.splice(i,1);}}
  const ids=new Set();for(const n of nodes){if(!n.id||/[\[\]{}|@/]/.test(n.id)||ids.has(n.id))throw Error(`样品节点 id 无效或重复：${n.id}`);if(n.hours!=null&&(!Number.isFinite(n.hours)||n.hours<0))throw Error('取样时间必须是非负小时数');n.uid??=stableUid(design.uid??design.id,'sample:'+n.id);if(/(?:^|-)FP$/.test(n.id)||n.kind==='product')n.lotUid??=stableUid(design.uid??design.id,'lot:'+n.id);n.aliases=aliases(n.aliases,n.sampleId,...(design.aliases??[design.code]).map(code=>plannedSampleCode(code,n)));n.tests=clone(n.plannedTests??n.tests??['GPC','NMR']);n.plannedTests=clone(n.tests);n.sampleId??=n.id.startsWith(design.code+'-')?n.id:/^t-/.test(n.id)&&n.hours!=null?`${design.code}-${n.hours.toFixed(1)}h`:n.id==='PRE-AddH'&&n.hours!=null?`${design.code}-${n.hours.toFixed(1)}h-PRE-AddH`:plannedSampleCode(design.code,n);n.aliases=aliases(n.aliases,n.sampleId,...(design.aliases??[]).map(code=>/^t-/.test(n.id)&&n.hours!=null?`${code}-${n.hours.toFixed(1)}h`:plannedSampleCode(code,n)));ids.add(n.id);}return nodes;
}
export function resinLots(design,library={}){
  return samplingNodes(design,resolveSteps(design,library)).filter(n=>n.lotUid).map(n=>({uid:n.lotUid,code:n.sampleId,aliases:n.aliases,sampleUid:n.uid,designUid:design.uid,revisionUid:design.revisionUid,batchUid:design.batch?.uid,kind:'lot',nodeId:n.id}));
}
export function renameSampleNode(design,nodeId,code){
  const out=editRevision(design),resolved=samplingNodes(out),node=resolved.find(n=>n.id===nodeId);if(!node)throw Error(`找不到样品节点：${nodeId}`);
  const explicit={...node,sampleId:code,sampleCodeBinding:'manual',aliases:aliases(node.aliases,node.sampleId,code)};
  const pointIndex=out.samplingPlan.points.findIndex(p=>typeof p==='object'?p.id===nodeId:`t-${String(p).replace('.','_')}`===nodeId);
  if(pointIndex>=0)out.samplingPlan.points[pointIndex]=explicit;
  else{const old=out.samplingPlan.nodes.findIndex(n=>n.id===nodeId);if(old>=0)out.samplingPlan.nodes[old]=explicit;else{const step=flattenSteps(out.steps).find(s=>s.id===node.stepId),sample=step?.samples?.find(n=>(n.id??n.suffix)===nodeId||n.suffix===node.suffix);if(sample)Object.assign(sample,{uid:node.uid,lotUid:node.lotUid,sampleId:code,aliases:explicit.aliases});else throw Error('请先在设计中显式声明此模块样品，再修改编号');}}
  return out;
}
export const chargeKeys=charge=>{const base=cleanKey(charge.as??charge.id);return {mass:`${base}质量`,mmol:`${base}_mmol`,volume:`${base}体积`,mw:`${base}_MW`,density:`${base}_density`,purity:`${base}_purity`,amount:`${base}_amount`};};
export function compileChargeVars(design,library,charges=design.charges){
  const vars=[],metadata=[],all=charges??[],aliases=new Map((design.materials??[]).map(m=>[m.as,m.ref]));
  const lookup=of=>{const matches=all.filter(c=>c.id===of||c.as===of||c.material===of);if(matches.length!==1)throw Error(`当量基准不唯一或不存在：${of}`);return chargeKeys(matches[0]);};
  for(const c of all){
    const original=findMaterial(library,aliases.get(c.material)??c.material),m=original?{...original,...design.materialOverrides?.[original.id]}:(c.material==='待指定'?{id:'待指定',name:'待指定'}:null);if(!m)throw Error(`找不到物料：${c.material}`);
    const k=chargeKeys(c),a=c.amount;if(!a)throw Error(`缺少投料量：${c.id}`);
    const mw=c.mw??m.mw,density=c.density??m.density,purity=c.purity??materialPurityFraction(m)??1,materialWt=m.solution?.wt??m.wt,concentration=c.concentration??((m.form==='溶液'||m.solution)&&materialWt!=null?Number(materialWt)/100:1);
    for(const [key,value]of Object.entries({mw,density,concentrationM:c.concentrationM}))if(value!=null&&(!Number.isFinite(value)||value<=0))throw Error(`投料 ${c.id} 的 ${key} 必须为正数`);
    if(!Number.isFinite(purity)||!Number.isFinite(concentration)||purity<=0||purity>1||concentration<=0||concentration>1)throw Error('纯度和溶液质量分数必须在 (0,1]');
    if(typeof a.value==='number'&&(!Number.isFinite(a.value)||a.value<0))throw Error('投料量必须为非负有限数');
    const humanLabel=(c.label??sopMaterialName(m)).replace(/整批|每支|\(mL\)|\(g\)/g,'').replace('打浆2','第二次打浆').replace('打浆1','第一次打浆');const labels={mass:'质量',volume:'体积',mmol:'物质的量',mw:'分子量',density:'密度',purity:'纯度',amount:'计划用量'};const add=(key,unit,value,expr=false)=>vars.push({key,label:`${humanLabel}${Object.values(k).indexOf(key)>=0?'·'+labels[Object.keys(k).find(x=>k[x]===key)]:''}`,unit,fmt:unit==='mL'?'0.0':'0.000',...(expr?{expr:value}:{value})});
    add(k.mw,'g/mol',mw??'');add(k.density,'g/mL',density??'');add(k.purity,'w/w',purity);add(k.amount,a.unit??'',a.value,typeof a.value==='string'&&a.value!=='待指定');
    if(a.value==='待指定'||a.value===''){for(const [key,unit]of [[k.mass,'g'],[k.mmol,'mmol'],[k.volume,'mL']])add(key,unit,'待指定');metadata.push({...c,concentration,materialId:m.id,name:sopMaterialName(m),abbr:materialAbbreviation(m),keys:k,unit:a.unit??(a.mode==='volume'?'mL':'g')});continue;}
    const v=`[${k.amount}]`,pure=`[${k.purity}]*${concentration}`,requireMW=()=>{if(!mw||mw<=0)throw Error(`物料缺少分子量：${m.id}`);};
    let mass,volume,mmol;
    switch(a.mode){
      case 'mass':mass=v;break;
      case 'volume':volume=v;if(density)mass=`${volume}*[${k.density}]`;if(c.concentrationM)mmol=`${volume}*${c.concentrationM}`;break;
      case 'mmol':requireMW();mmol=v;break;
      case 'eq':case 'molpct':requireMW();mmol=`[${lookup(a.of).mmol}]*${v}${a.mode==='molpct'?'/100':''}`;break;
      case 'solidFill':{
        const solids=a.of??all.filter(other=>other!==c&&other.role!=='solvent'&&other.amount.mode!=='solidFill').map(c=>c.id);
        if(!Array.isArray(solids)||!solids.length)throw Error('固含补足需要固体投料 id 列表');
        const sum=solids.map(id=>`[${lookup(id).mass}]`).join('+');mass=`(${sum})*(100/${v}-1)`;break;
      }
      case 'perProduct':volume=`${v}*(${a.of??`[${roleVar(design.vars,'expectedProduct')?.key??'预计产品'}]`})`;break;
      case 'expr':if(a.unit==='mL')volume=v;else if(a.unit==='mmol'){requireMW();mmol=v;}else mass=v;break;
      default:throw Error(`未知投料模式：${a.mode}`);
    }
    if(!mass&&mmol&&a.mode!=='volume'&&!(a.mode==='expr'&&a.unit==='mL'))mass=`(${mmol})*[${k.mw}]/1000/(${pure})`;
    if(!mass&&volume&&density)mass=`(${volume})*[${k.density}]`;
    if(!mmol&&mass&&mw)mmol=`(${mass})*(${pure})/[${k.mw}]*1000`;
    if(!mmol&&volume&&mw&&density)mmol=`(${volume})*[${k.density}]*(${pure})/[${k.mw}]*1000`;
    if(!volume&&mass&&density)volume=`(${mass})/[${k.density}]`;
    add(k.mass,'g',mass??'',!!mass);add(k.mmol,'mmol',mmol??'',!!mmol);add(k.volume,'mL',volume??'',!!volume);
    metadata.push({...c,concentration,materialId:m.id,name:sopMaterialName(m),abbr:materialAbbreviation(m),keys:k,unit:['volume','perProduct'].includes(a.mode)||a.unit==='mL'?'mL':'g',purityAssumed:c.purity==null&&m.purity==null});
  }return {vars,charges:metadata};
}
// Small variable evaluator for planned conditions, with explicit cycle/missing checks.
export function variableValues(vars){const definitions=new Map(vars.map(v=>[v.key,v])),values=new Map(),active=new Set();const get=key=>{if(values.has(key))return values.get(key);if(active.has(key))throw Error(`参数循环引用：${key}`);const v=definitions.get(key);if(!v)throw Error(`缺少参数：${key}`);active.add(key);const result=v.expr!==undefined?evaluateExpr(parseExpr(v.expr),r=>{if(r.kind!=='var')throw Error('计划条件不能引用表格');return get(r.key);}):v.value;active.delete(key);values.set(key,result);return result;};return {get,values};}
export function resolveCondition(value,args={},vars=[]){
  if(value==null)return null;if(typeof value==='number')return value;
  const expression=String(value).replace(/\{\{([^}]+)\}\}/g,(_,key)=>`(${argumentExpr(args[key])})`);
  const defs=[...vars,...Object.entries(args).filter(([key])=>!vars.some(v=>v.key===key)).map(([key,value])=>({key,expr:argumentExpr(value)}))];
  const result=evaluateExpr(parseExpr(expression),r=>variableValues(defs).get(r.key));return result==='待指定'?'待指定':Number(result);
}
/** One schedule for every consumer. Planned expressions stay live in Excel;
 * recorded clocks override effective times without rewriting the original plan. */
export function scheduleDesign(design,library={},options={}){
  const steps=options.steps??(library.modules?resolveSteps(design,library):flattenSteps(orderedSteps(design.steps,library)).map(s=>({...s,args:s.args??{},charges:s.charges??[]}))),vars=options.vars??[...design.vars,...(library.materials?compileChargeVars(design,library,[...design.charges,...steps.flatMap(s=>s.charges??[])]).vars:[])],resolved=new Map(steps.map(s=>[s.id,s])),nodes=samplingNodes(design,steps),t0=actualT0(design)??parseClock(design.samplingPlan?.t0Clock??'10:00'),result=[];
  const walk=(list,planned=0,cursor=0,temp=25,expr='0',branch)=>{for(const raw of orderedSteps(list.map(s=>({...s,stage:resolved.get(s.id)?.stage??s.stage})),library)){
    const s=resolved.get(raw.id),condition=s.conditions??{},value=key=>resolveCondition(condition[key],s.args,vars),hours=value('hours')??0,target=value('tempC')??temp,from=value('from')??temp;
    if(!Number.isFinite(hours)||hours<0||!Number.isFinite(target)||!Number.isFinite(from))throw Error('计划温度或时长无效');
    const startExpr=expr,hoursExpr=String(condition.hours??0).replace(/\{\{([^}]+)\}\}/g,(_,key)=>'('+argumentExpr(s.args[key])+')'),endExpr=`(${expr})+(${hoursExpr})`,plannedStart=planned,plannedEnd=planned+hours;
    const startClock=design.actual?.[`record-operations/${s.id}/开始`]??timelineClock(design,s.id+'-start',plannedStart,nodes),endClock=design.actual?.[`record-operations/${s.id}/结束`]??timelineClock(design,s.id+'-end',plannedEnd,nodes),actualStart=startClock==null?null:relativeClock(startClock,t0,plannedStart),actualEnd=endClock==null?null:relativeClock(endClock,t0,plannedEnd),start=actualStart??cursor,recordedHours=design.actual?.[`record-operations/${s.id}/时长`],end=actualEnd??start+(Number.isFinite(recordedHours)&&recordedHours>=0?recordedHours:hours);
    result.push({stepId:s.id,title:s.title,stage:s.stage,branch:s.branch??branch,plannedStart,plannedEnd,startExpr,endExpr,hoursExpr,start,end,hours,actualStart,actualEnd,timing:actualStart!=null||actualEnd!=null?'actual':'planned',tempC:target,from,kind:condition.kind??condition.type});planned=plannedEnd;cursor=end;temp=target;expr=endExpr;
    if(raw.branches?.length){const tails=raw.branches.map(b=>walk(b.steps,planned,cursor,temp,expr,b.id));planned=Math.max(planned,...tails.map(t=>t.planned));cursor=Math.max(cursor,...tails.map(t=>t.cursor));expr=`MAX(${[expr,...tails.map(t=>t.expr)].join(',')})`;}
  }return {planned,cursor,expr};};walk(design.steps);return result;
}
export function tempProfile(design,library={}){return scheduleDesign(design,library).flatMap(s=>{const raw=flattenSteps(design.steps).find(r=>r.id===s.stepId),module=findModule(library,raw.module);if(!Object.keys(raw.conditions??module?.conditions??{}).length)return [];const ramp=['ramp','cool'].includes(s.kind)||s.kind!=='hold'&&s.from!==s.tempC;return [{t0:s.start,t1:s.end,tempStart:ramp?s.from:s.tempC,tempEnd:s.tempC,label:s.title,stepId:s.stepId,branch:s.branch}];});}
export function actualTempSeries(design,library={}){
  const ids=new Set(Object.keys(design.actual??{}).filter(k=>k.startsWith('record-timeline/')).map(k=>k.split('/')[1]));
  if(!ids.size)return [];
  const times=new Map(samplingNodes(design).map(n=>[n.id,n.hours]));
  for(const s of scheduleDesign(design,library)){times.set(s.stepId+'-start',s.start);times.set(s.stepId+'-end',s.end);}
  const t0=actualT0(design);let previous=0;
  return [...ids].sort((a,b)=>(times.get(a)??Infinity)-(times.get(b)??Infinity)).flatMap(id=>{const prefix=`record-timeline/${id}/`,get=k=>design.actual?.[prefix+k];if(get('内温')==null&&get('外温')==null)return [];const clock=get('实际钟点'),hours=t0===null?times.get(id)??null:clock==null?null:relativeClock(clock,t0,times.get(id));if(hours!==null)previous=hours;return [{nodeId:id,hours,clock:clock??null,tempC:get('内温')??null,outerTempC:get('外温')??null}];});
}
/** Resolve a module instance without mutating the plan or library. */
export function resolveSteps(design,library={}, {liftParams=false}={}) {
  return flattenSteps(orderedSteps(design.steps,library)).map(step=>{
    const found=findModule(library,step.module);if(!found&&step.text===undefined)throw Error(`找不到步骤模块：${step.module}`);
    const module={...(found??{text:'',params:[],stage:step.stage??'reaction'}),params:[...(found?.params??[]),...(step.amountParams??[]),...(step.durationParams??[])]},rewrite=localExpressionRewrite(design,module,step),args=Object.fromEntries(Object.entries({...step.args,...effectiveArgs(design,module,step)}).map(([key,value])=>[key,rewrite(value)]));
    const substitute=value=>{
      if(typeof value==='string'){
        const exact=/^\{\{([^}|]+)\}\}$/.exec(value);if(exact){const p=module.params?.find(p=>p.key===exact[1]);if(p&&!['text','material'].includes(p.type)){const token=instantiateModule({...module,text:value},args),expr=parseTemplate(token).find(p=>p.expr)?.expr;return expr?.type==='literal'?expr.value:expr?serializeExpr(expr):args[exact[1]]??value;}return args[exact[1]]??value;}
        return value.replace(/\{\{([^}|]+)\}\}/g,(_,k)=>String(args[k]??''));
      }
      if(Array.isArray(value))return value.map(substitute);
      if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,substitute(v)]));return value;
    };
    const moduleCharges=()=>(!module.charges&&step.text!==undefined?[]:declaredModuleCharges(module)).map((c,i)=>({...substitute(c),role:c.role??(module.id==='add-and-hold'?'addition':undefined),id:`${step.id}-${c.id??i+1}`,as:`${step.id}-${c.id??i+1}`}));
    let charges=step.charges??moduleCharges();
    if(module.id==='add-and-hold'&&(step.paramOverrides??[]).includes('mass')&&typeof step.args.mass==='number'&&step.args.mass>0)charges=charges.map(c=>({...c,amount:{mode:'mass',value:step.args.mass}}));
    if(module.id==='add-and-hold'&&charges.length&&(!(step.paramOverrides??[]).includes('mass')||step.args?.mass==null||step.args.mass===0))args.mass=`[${chargeKeys(charges[0]).mass}]`;
    // Each numeric argument has one Excel home. Text, badges and record plans
    // reference it, so editing an exported parameter changes every occurrence.
    const paramVars=[];
    if(liftParams&&(step.text===undefined||step.durationParams?.length||step.amountParams?.length)){
      const numeric=new Map();
      for(const p of step.text===undefined?module.params??[]:[...step.amountParams??[],...step.durationParams??[]]){
        if(['text','material'].includes(p.type))continue;
        const ast=parseExpr(argumentExpr(args[p.key],p.type));
        if(ast.type==='literal'&&typeof ast.value!=='number')continue;
        numeric.set(p.key,{param:p,ast,key:`${step.id}~${cleanKey(p.key)}`});
      }
      const substitutions={};
      for(const [name,{param:p,ast,key}]of numeric){
        const expr=mapRefs(ast,r=>r.kind==='var'&&r.key!==name&&numeric.has(r.key)?{...r,key:numeric.get(r.key).key}:r);
        paramVars.push({key,label:`${step.title??module.name}·${p.label??p.key}`,unit:p.unit??'',...(expr.type==='literal'?{value:expr.value}:{expr:serializeExpr(expr)}),fmt:'0.000',technical:!JSON.stringify(step.conditions??module.conditions??{}).includes(`{{${p.key}}}`)});
        substitutions[name]=`[${key}]`;
      }
      Object.assign(args,substitutions);
    }
    if(liftParams&&step.charges===undefined)charges=moduleCharges();
    charges=charges.map(c=>({...c,amount:{...c.amount,value:rewrite(c.amount.value)},label:c.label??`${step.title??module.name}·${sopMaterialName(findMaterial(library,c.material)??{material:c.material})}`}));
    const displayArgs={...args};for(const p of module.params??[])if(p.type==='material'){
      const ref=args[p.key],declared=declaredModuleCharges(module).findIndex(c=>c.material==='{{'+p.key+'}}'),charge=(declared>=0?charges[declared]:null)??[...design.charges,...charges].find(c=>[c.id,c.as].includes(ref)),alias=(design.materials??[]).find(m=>m.as===(charge?.material??ref));
      displayArgs[p.key]=sopMaterialName(findMaterial(library,alias?.ref??charge?.material??ref)??{material:ref});
    }
    const instantiate=text=>instantiateModule({...module,text},displayArgs),override=text=>text.replace(/\{\{[^{}]+\}\}/g,token=>instantiate(token));
    const generated=(text,field)=>generatedMaterialText(design,text,'steps/'+step.id+'/'+field,library);
    let text=step.text!==undefined?override(rewrite(generated(step.text,'text'))):instantiate(generated(module.text,'text'));
    if(step.bindingMode==='design'&&step.text===undefined&&module.id.startsWith('ref-'))text=text.replace(/(?:HEP|AddH|CP\d+-\d+[A-Z]?)[-\s]*(?=湿饼|滤饼|滤液|树脂液)/g,'').replace(/沿用CP\d+-\d+[A-Z]?已筛得/g,'采用本步设定的').replace(/与CP\d+-\d+[A-Z]?比较/g,'与基准批次比较');
    if(step.branches?.length)text+='；'+step.branches.map(b=>{const key=`${step.id}~fraction~${cleanKey(b.id)}`;if(liftParams)paramVars.push({key,label:`分料·${b.name??b.id}比例`,unit:'w/w',value:b.fraction,fmt:'0.0%'});return `${b.name??b.id} ${liftParams?`{[${key}]|0.0%}`:b.fraction*100+'%'}`;}).join('、');
    return {...step,titleTemplate:activeTitleTemplate(step)?generated(step.titleTemplate,'titleTemplate'):undefined,category:step.category??(module.id==='custom'?moduleCategory(step.title):module.category),title:(step.branch?`[${step.branch}] `:'')+(activeTitleTemplate(step)?renderPlanTemplate(generated(step.titleTemplate,'titleTemplate'),design.vars):generated(step.title??module.name,'title')),stage:step.stage??module.stage,args,charges,paramVars,conditions:Object.fromEntries(Object.entries(step.conditions??(step.text===undefined?module.conditions:undefined)??{}).map(([key,value])=>[key,rewrite(value)])),text,controls:step.controls?step.controls.map((t,i)=>override(rewrite(generated(t,'controls/'+i)))):(module.controls??[]).map((t,i)=>instantiate(generated(t,'controls/'+i))),records:leanRecords(step.records??module.records??[],{title:step.title??module.name,stage:step.stage??module.stage,sourceText:step.sourceText??module.sourceText??module.text}),notes:(step.notes??module.notes??[]).map((n,i)=>override(generated(n,'notes/'+i))),sourceText:step.sourceText??module.sourceText??module.text,samples:step.samples??substitute(module.samples??[])};
  });
}
