import {executionIssue} from './design-issues.js';
import {resolveSteps,compileChargeVars,variableValues,resolveCondition,flattenSteps,samplingNodes,chargeKeys,scheduleDesign} from './design2.js';
import {findMaterial} from './materials.js';
import {materialTextWarnings} from './generated-material-text.js';
import {findModule} from './modules.js';
import {executionDesign} from './identity.js';
import {actualT0,parseClock,relativeClock,timelineClock} from './record-values.js';
import {roleVar} from './var-roles.js';
const number=v=>typeof v==='number'&&Number.isFinite(v)?v:null;
export function designState(design,library={}){
  const steps=resolveSteps(design,library),compiled=compileChargeVars(design,library,[...design.charges,...steps.flatMap(s=>s.charges??[])]),vars=[...design.vars,...compiled.vars],values=variableValues(vars);
  return {steps,charges:compiled.charges,vars,values,get:key=>values.get(key)};
}
export {scheduleDesign};
export function toleranceFor(design,kind,item={}){
  if(kind==='charge')return {relative:item.tolerance??design.tolerances?.charges??0.01};
  if(kind==='duration')return {relative:item.durationTolerance??design.tolerances?.durations??0.1};
  const match=(item.controls??[]).join(' ').match(/±\s*(\d+(?:\.\d+)?)\s*(?:°C|℃)/);
  return {absolute:item.tempTolerance??design.tolerances?.temperature??(match?+match[1]:2)};
}
export function outsideTolerance(actual,plan,tolerance){return typeof actual==='number'&&typeof plan==='number'&&Math.abs(actual-plan)>(tolerance.absolute??Math.abs(plan)*(tolerance.relative??0))+1e-10;}
export function deviation(design,library={}){
  design=executionDesign(design);const {steps,charges,values}=designState(design,library),schedule=scheduleDesign(design,library),results=[];
  const add=(key,planned,unit,tolerance,context={})=>{const actual=design.actual?.[key];if(!outsideTolerance(actual,planned,tolerance))return;const delta=actual-planned,percent=planned?delta/Math.abs(planned):null;results.push({key,planned,actual,unit,delta,percent,tolerance,...context,reason:design.actual?.[key.split('/').slice(0,-1).join('/')+'/备注']??design.actual?.[key.split('/').slice(0,-1).join('/')+'/现象/备注']??'',message:`实际 ${actual} ${unit}，${percent===null?'差 '+delta+' '+unit:(percent>=0?'+':'')+(percent*100).toFixed(1)+' %'}`});};
  for(const c of charges)add(`record-charges/${c.id}/实际值`,values.get(c.unit==='mL'?c.keys.volume:c.keys.mass),c.unit,toleranceFor(design,'charge',c),{chargeId:c.id});
  let previous=null;
  for(const [key,value]of Object.entries(design.actual??{}))if(key.startsWith('record-timeline/')&&/\/(内温|外温)$/.test(key)&&typeof value==='number'){
    const id=key.split('/')[1],node=samplingNodes(design,steps).find(n=>n.id===id),timed=schedule.find(p=>id===p.stepId+'-start'||id===p.stepId+'-end')??schedule.find(p=>node?.hours>=p.start&&node?.hours<=p.end&&p.hours>0),step=steps.find(s=>s.id===timed?.stepId);
    if(node?.hours===0||id==='t-0'||id.endsWith('-start')&&timed?.plannedStart===0)continue;
    const fraction=timed?.hours>0?node?.hours!=null?(node.hours-timed.start)/timed.hours:id.endsWith('-start')?0:1:1,planned=timed&&['ramp','cool'].includes(timed.kind)?timed.from+(timed.tempC-timed.from)*fraction:timed?.tempC;
    if(timed)add(key,planned,'°C',toleranceFor(design,'temperature',step),{stepId:step?.id});
  }
  for(const step of steps){const p=schedule.find(p=>p.stepId===step.id);if(!p?.hours)continue;
    const direct=design.actual?.[`record-operations/${step.id}/时长`];
    if(number(direct)!==null)add(`record-operations/${step.id}/时长`,p.hours,'h',toleranceFor(design,'duration',step),{stepId:step.id});
    const nodes=samplingNodes(design,steps),start=timelineClock(design,`${step.id}-start`,p.start,nodes)??design.actual?.[`record-operations/${step.id}/开始`],end=timelineClock(design,`${step.id}-end`,p.end,nodes)??design.actual?.[`record-operations/${step.id}/结束`];
    if(start!=null&&end!=null){const t0=actualT0(design)??parseClock(design.samplingPlan?.t0Clock??'10:00'),duration=relativeClock(end,t0,p.end)-relativeClock(start,t0,p.start);if(outsideTolerance(duration,p.hours,toleranceFor(design,'duration',step)))results.push({key:`duration/${step.id}`,stepId:step.id,planned:p.hours,actual:duration,unit:'h',delta:duration-p.hours,tolerance:toleranceFor(design,'duration',step),message:`实际 ${duration.toFixed(2)} h，${((duration/p.hours-1)*100).toFixed(1)} %`});}
  }
  return results;
}
/** Shared stage balances power both checks and profiles. Missing data stays null. */
export function processProfile(design,library={}){
  design=executionDesign(design);const {steps,charges,values}=designState(design,library),schedule=scheduleDesign(design,library),nodes=samplingNodes(design,steps),t0=actualT0(design),planT0=parseClock(design.samplingPlan?.t0Clock??'10:00');
  const get=c=>({mass:number(values.get(c.keys.mass)),volume:number(values.get(c.keys.volume))}),initial=charges.filter(c=>design.charges.some(x=>x.id===c.id));
  function balance(actual,seed){
    let mass=seed?.mass??0,solid=seed?.solid??0,volume=seed?.volume??0,massKnown=seed?.massKnown??true,volumeKnown=seed?.volumeKnown??true,missing=seed?.missing??[];
    const api={
      add(c){let {mass:m,volume:v}=get(c);const recorded=number(design.actual?.[`record-charges/${c.id}/实际值`]),density=number(c.density??findMaterial(library,c.materialId)?.density);
        if(actual){if(recorded===null)m=v=null;else if(c.unit==='mL'){v=recorded;m=density?recorded*density:null;}else{m=recorded;v=density?recorded/density:null;}}
        if(m===null)massKnown=false;else{mass+=m;if(c.role!=='solvent'&&c.amount.mode!=='solidFill')solid+=m*(c.concentration??1);}
        if(v===null){volumeKnown=false;missing.push(c.name);}else volume+=v;
      },
      remove(sample=0,water=0){const before=mass;if(before>0)solid-=sample*solid/before;if(volumeKnown&&before>0)volume-=sample*volume/before+water/waterDensity;mass-=sample+water;},
      read(){return {massG:massKnown?mass:null,nonvolatileG:massKnown?solid:null,solids:massKnown&&mass>0?solid/mass:null,volumeMl:volumeKnown?volume:null,volumeLowerBoundMl:volume,fill:volumeKnown&&design.flaskMl?volume/design.flaskMl:null,missingDensities:[...new Set(missing)]};},
      fork(fraction){return balance(actual,{mass:mass*fraction,solid:solid*fraction,volume:volume*fraction,massKnown,volumeKnown,missing:[...missing]});},
      invalidate(reason){massKnown=volumeKnown=false;volume=0;missing.push(reason);}
    };return api;
  }
  const waterDensity=findMaterial(library,'water')?.density??1;
  const plan=balance(false),actual=balance(true);initial.forEach(c=>{plan.add(c);actual.add(c);});
  const branches=new Map(),planned=[],temperature=[],flow=[],dryBasis=[];const streams=new Map([['main',{mass:actual.read().massG,dry:actual.read().nonvolatileG}]]);
  const snapshot=(state,step,time)=>planned.push({stepId:step.id,stage:step.stage??'reaction',branch:step.branch,hours:time,clock:(t0??planT0)+time/24,...state.read()});snapshot(plan,{id:'initial'},0);
  for(const step of steps){const stream=streams.get(step.branch??'main')??streams.get('main'),time=schedule.find(p=>p.stepId===step.id),state=step.branch?branches.get(step.branch)??plan:plan,added=charges.filter(c=>step.charges.some(s=>s.id===c.id));
    if(time&&added.length)snapshot(state,step,time.start);added.forEach(c=>state.add(c));if(time){if(step.stage!=='workup')temperature.push({stepId:step.id,hours:time.start,tempC:time.kind==='hold'?time.tempC:time.from},{stepId:step.id,hours:time.end,tempC:time.tempC});snapshot(state,step,time.start);if(time.end!==time.start)snapshot(state,step,time.end);}
    for(const branch of step.branches??[]){branches.set(branch.id,state.fork(branch.fraction));streams.set(branch.id,{mass:stream.mass===null?null:stream.mass*branch.fraction,dry:stream.dry===null?null:stream.dry*branch.fraction});}
    if(step.stage!=='workup'){
      for(const c of added){const value=number(design.actual?.[`record-charges/${c.id}/实际值`]),density=number(c.density??findMaterial(library,c.materialId)?.density),mass=value===null?null:c.unit==='g'?value:density?value*density:null;if(stream.mass!==null)stream.mass=mass===null?null:stream.mass+mass;if(c.role!=='solvent'&&stream.dry!==null)stream.dry=mass===null?null:stream.dry+mass*(c.concentration??1);}
      continue;
    }
    const prefix=`record-operations/${step.id}/`,record=k=>number(design.actual?.[prefix+k]),mass=record('质量')??record('湿饼质量')??record('液相质量'),fraction=record('含固率')??record('液相含固率'),massKind=step.records.find(r=>/质量$/.test(r.key))?.key,isLiquid=massKind==='液相质量'||/PRE|稀释/.test(step.title);
    const product=record('M2')??record('M1')??record('干品质量'),solidDry=product??(!isLiquid&&mass!==null&&fraction!==null?mass*fraction:null),liquidMass=record('液相质量')??(isLiquid?mass:null),liquidFraction=record('液相含固率')??(isLiquid?fraction:null),liquidDry=liquidMass!==null&&liquidFraction!==null?liquidMass*liquidFraction:null;
    const actualAdds=added.map(c=>{const value=number(design.actual?.[`record-charges/${c.id}/实际值`]),density=number(c.density??findMaterial(library,c.materialId)?.density);return value===null?null:c.unit==='g'?value:density?value*density:null;}),plannedAdded=added.length&&added.every(c=>get(c).mass!==null)?added.reduce((n,c)=>n+get(c).mass,0):added.length?null:0,actualAdded=actualAdds.every(v=>v!==null)?actualAdds.reduce((a,b)=>a+b,0):null;
    flow.push({stepId:step.id,label:step.title,branch:step.branch,incomingMassG:stream.mass,plannedAddedSolventG:plannedAdded,addedSolventG:actualAdded,actualAdded:record('实际用量'),wetCakeG:record('湿饼质量')??(!isLiquid?mass:null),liquidG:liquidMass,dryProductG:product});
    dryBasis.push({stepId:step.id,branch:step.branch,solidDryG:solidDry,liquidDryG:liquidDry,incomingDryG:stream.dry,closure:stream.dry>0&&solidDry!==null&&liquidDry!==null?(solidDry+liquidDry)/stream.dry:null});
    if(solidDry!==null)stream.dry=solidDry;else if(liquidDry!==null)stream.dry=liquidDry;
    if(mass!==null)stream.mass=mass;else if(product!==null)stream.mass=product;else if(actualAdded!==null&&stream.mass!==null)stream.mass+=actualAdded;else if(added.length)stream.mass=null;
    if(/过滤|分相/.test(step.title))state.invalidate('分相后剩余量未设定');
  }
  const timelineIds=[...new Set(Object.keys(design.actual??{}).filter(k=>k.startsWith('record-timeline/')).map(k=>k.split('/')[1]))],actualPoints=[];
  const nominalHours=id=>nodes.find(n=>n.id===id)?.hours??(id.endsWith('-end')?schedule.find(p=>p.stepId===id.slice(0,-4))?.end:schedule.find(p=>p.stepId===id.slice(0,-6))?.start)??design.importedRows?.find(r=>r.id===id)?.hours??null;
  timelineIds.sort((a,b)=>(nominalHours(a)??Infinity)-(nominalHours(b)??Infinity));let previous=0;
  for(const id of timelineIds){const r=k=>design.actual?.[`record-timeline/${id}/${k}`],clock=r('实际钟点'),hours=t0===null?nominalHours(id):clock==null?null:relativeClock(clock,t0,nominalHours(id));if(hours!==null)previous=hours;
    actualPoints.push({nodeId:id,hours,clock:clock??null,tempC:number(r('内温')),outerTempC:number(r('外温')),waterG:number(r('累计水量'))===null?null:number(r('累计水量'))*waterDensity,waterMl:number(r('累计水量')),solids:number(r('含固率'))});
  }
  const additions=steps.filter(s=>s.stage!=='workup').flatMap(step=>{const time=schedule.find(p=>p.stepId===step.id);return charges.filter(c=>step.charges.some(s=>s.id===c.id)).map(c=>({c,hours:time.start,timing:time.timing}));}).sort((a,b)=>a.hours-b.hours);
  let addition=0,lastWater=0;const actualBalance=[],nominal=[{hours:0,nominalSolids:actual.read().solids}];
  for(const point of [...actualPoints,...additions.map(a=>({hours:a.hours,additionEvent:true}))].sort((a,b)=>(a.hours??Infinity)-(b.hours??Infinity)||Number(!!a.additionEvent)-Number(!!b.additionEvent))){
    if(point.hours===null){point.nominalSolids=null;continue;}
    while(addition<additions.length&&additions[addition].hours<=point.hours){const event=additions[addition];nominal.push({hours:event.hours,nominalSolids:actual.read().solids});actual.add(event.c);nominal.push({hours:event.hours,nominalSolids:actual.read().solids,timing:event.timing,chargeId:event.c.id});addition++;}
    if(point.additionEvent)continue;
    const sample=number(design.actual?.[`record-timeline/${point.nodeId}/取样量`])??number(design.actual?.[`record-samples/${point.nodeId}/取样量`])??0,water=point.waterMl??lastWater;
    actual.remove(sample,Math.max(0,water-lastWater)*waterDensity);lastWater=water;point.nominalSolids=actual.read().solids;nominal.push({hours:point.hours,nominalSolids:point.nominalSolids});
    actualBalance.push({nodeId:point.nodeId,hours:point.hours,...actual.read()});
  }
  const theoretical=roleVar(design.vars,'theoreticalWater'),product=roleVar(design.vars,'expectedProduct'),resin=roleVar(design.vars,'theoreticalResin'),productNode=nodes.find(n=>n.lotUid),final=productNode?.stepId?flow.find(f=>f.stepId===productNode.stepId):flow.findLast(f=>f.dryProductG!==null);
  const measuredSolids=steps.filter(s=>s.stage==='workup').flatMap(s=>{const fraction=number(design.actual?.[`record-operations/${s.id}/含固率`]);return fraction===null?[]:[{stepId:s.id,solids:fraction,hours:null}];});
  return {axis:{unit:'h',t0:t0??planT0,actual:t0!==null},stages:planned,temperature:{planned:temperature,actual:actualPoints.filter(p=>p.tempC!==null||p.outerTempC!==null)},solids:{nominal,planned:planned.filter(p=>p.stage!=='workup').map(p=>({...p,value:p.solids})),actual:actualPoints.filter(p=>p.solids!==null||p.nominalSolids!==null),measuredWorkup:measuredSolids},water:{actual:actualPoints.filter(p=>p.waterG!==null),theoreticalCeilingMl:theoretical?number(values.get(theoretical.key))/waterDensity:null,theoreticalCeilingG:theoretical?number(values.get(theoretical.key)):null,ceilingOnly:true},volume:{planned:planned.filter(p=>p.stage!=='workup').map(p=>({...p,value:p.volumeMl})),actual:actualBalance,references:design.flaskMl?{flaskMl:design.flaskMl,warnMl:design.flaskMl*0.7,errorMl:design.flaskMl*0.9}:null},workup:{massFlow:flow,dryBasis,volume:planned.filter(p=>p.stage==='workup'),expectedProductG:product?number(values.get(product.key)):null,apparentYield:final?.dryProductG!==null&&final?.dryProductG!==undefined&&resin?final.dryProductG/values.get(resin.key):null,basis:'产率（未扣添加剂）'}};
}
export function checkDesign(design,library={}){
  try{return checkDesignUnsafe(design,library);}
  catch(error){return [executionIssue(error)];}
}
function checkDesignUnsafe(design,library={}){
  let validationSteps;try{validationSteps=resolveSteps(design,library);}catch{validationSteps=flattenSteps(design.steps);}
  const findings=[...design.executionIssues??[],...materialTextWarnings(design,library)],push=(level,message,context={})=>findings.push({level,message,...context}),all=[...design.charges,...validationSteps.flatMap(s=>s.charges??[])],aliases=new Map((design.materials??[]).map(m=>[m.as,m.ref]));
  if(!all.length)push('warn','投料 0 项：尚未登记投料；请在执行前核对。',{code:'execution-reminder'});
  for(const step of validationSteps){const text=String(step.text??'');const missing=[...text.matchAll(/未设置\s*(°C|℃|h|min)/g)].map(m=>/°C|℃/.test(m[1])?'温度':'时长');for(const item of new Set(missing))push('warn',`${step.title}：${item}未关联参数（未设置）；请在执行前核对。`,{stepId:step.id,code:'execution-reminder'});}
  for(const step of validationSteps)for(const [key,value]of Object.entries(step.args??{}))if(value==='待指定'){const p=findModule(library,step.module)?.params?.find(p=>p.key===key);push('error',`${step.title}：${p?.label&&!/^cell_/.test(p.label)?p.label:'参数'}待指定`,{stepId:step.id});}
  for(const s of flattenSteps(design.steps)){const m=findModule(library,s.module);if(s.recording!==false&&(s.stage??m?.stage)==='workup'&&!(s.records??m?.records??[]).length)push('info','后处理步骤未声明记录',{stepId:s.id});}
  for(const c of all){const m=findMaterial(library,aliases.get(c.material)??c.material);if(!m)push('error',`执行核对：物料库缺少 ${c.material}，请补建并补充物性。`,{chargeId:c.id});else if(m.propertyStatus==='待补物性'&&[m.mw,m.density].some(v=>v==null))push('error',`执行核对：物料库缺少 ${m.name}，已按原档名称补建，请补充物性（待补物性）。`,{chargeId:c.id});if(['mmol','eq','molpct'].includes(c.amount?.mode)&&!(c.mw??m?.mw))push('error',`${m?.name??c.material}缺少分子量，无法计算 ${c.amount.mode}`,{chargeId:c.id});if(['eq','molpct'].includes(c.amount?.mode)&&!all.some(x=>[x.id,x.as,x.material].includes(c.amount.of)))push('error',`当量基准 ${c.amount.of} 不在本设计中`,{chargeId:c.id});}
  const ids=new Set();for(const n of [...design.samplingPlan?.nodes??[],...(design.samplingPlan?.points??[]).map(x=>typeof x==='number'?{id:'t-'+String(x).replace('.','_')}:x),...flattenSteps(design.steps).flatMap(s=>(s.samples??[]).map(n=>({...n,id:n.id??s.id+'-'+n.suffix})))]){if(ids.has(n.id))push('info',`取样节点编号重复：${n.id}`);ids.add(n.id);}
  let state,profile,schedule;try{state=designState(design,library);schedule=scheduleDesign(design,library);profile=processProfile({...design,actualRevisionUid:null,frozenRevisions:[]},library);}catch(e){findings.push(executionIssue(e));return findings;}
  const t0=actualT0(design)??parseClock(design.samplingPlan?.t0Clock??'10:00');let previousSolid=null;
  const missingDensities=new Set(),checkedVolumes=new Set();let inReactionVessel=true;
  for(const p of profile.stages){const step=state.steps.find(s=>s.id===p.stepId)??(p.stepId==='initial'?state.steps.find(s=>s.category==='投料'||/室温.*投料/.test(s.title)):null);
    if(step?.vessel==='separate'||step?.stage==='workup'&&['沉降','过滤'].includes(step.category))inReactionVessel=false;
    const reactionVessel=step?.vessel==='reaction'||inReactionVessel,workup=!reactionVessel,capacity=design.vessels?.find(v=>v.id===step?.vesselId)?.capacityMl??step?.vesselMl??step?.flaskMl??(reactionVessel?(design.vessels?.find(v=>v.id===design.reactionVesselId)?.capacityMl??design.flaskMl):null),phase=reactionVessel?'reaction':step?.id,context={stepId:step?.id,phase:reactionVessel?'reaction':'workup'},signature=phase+'/'+p.volumeLowerBoundMl+'/'+capacity;
    for(const name of p.missingDensities)if(name!=='分相后剩余量未设定')missingDensities.add(name);
    if(capacity&&!checkedVolumes.has(signature)){
    checkedVolumes.add(signature);
    if(capacity&&p.volumeMl===null){
      if(p.volumeLowerBoundMl/capacity>0.7)push(p.volumeLowerBoundMl/capacity>0.9?'error':'warn',`${step?.title??'初始投料'}：${workup?'后处理液':'反应液'}已知体积至少 ${p.volumeLowerBoundMl.toFixed(0)} mL ${p.volumeLowerBoundMl>capacity?'>':'，接近'} ${capacity} mL ${workup?'容器':'烧瓶'}（容器装填率至少 ${(p.volumeLowerBoundMl/capacity*100).toFixed(1)} %；其余物料密度未齐）`,context);
    }else if(p.volumeMl/capacity>0.7)push(p.volumeMl/capacity>0.9?'error':'warn',`${step?.title??'初始投料'}：${workup?'后处理液':'反应液'}约 ${p.volumeMl.toFixed(0)} mL ${p.volumeMl>capacity?'>':'，接近'} ${capacity} mL ${workup?'容器':'烧瓶'}（容器装填率 ${(p.volumeMl/capacity*100).toFixed(1)} %）`,context);
    }
    if(previousSolid!==null&&p.solids!==null&&Math.abs(p.solids-previousSolid)>1e-10){const range=design.solidsRange??[0,1],outside=p.solids<range[0]||p.solids>range[1];push(outside?'warn':'info',`补加后名义固含 ${(previousSolid*100).toFixed(1)}→${(p.solids*100).toFixed(1)} %${outside?'，超出设定范围':''}`,{stepId:step?.id});}if(p.solids!==null)previousSolid=p.solids;
  }
  let solvents=state.charges.filter(c=>design.charges.some(x=>x.id===c.id)&&c.role==='solvent');
  for(const s of state.steps){solvents.push(...state.charges.filter(c=>s.charges.some(x=>x.id===c.id)&&c.role==='solvent'));const time=schedule.find(p=>p.stepId===s.id),principal=solvents.map(c=>({material:findMaterial(library,c.materialId),volume:state.values.get(c.keys.volume)??0})).sort((a,b)=>b.volume-a.volume)[0]?.material;
    if(s.conditions?.tempC!=null&&principal?.bp&&time.tempC>principal.bp)push('warn',`温度 ${time.tempC} °C 高于主要溶剂${principal.name}沸点 ${principal.bp} °C`,{stepId:s.id});
    if(s.handsOn!==false&&time){const hour=((t0*24+time.start)%24+24)%24;if(hour>=22||hour<7)push('warn',`夜间操作：${s.title}（${String(Math.floor(hour)).padStart(2,'0')}:${String(Math.round(hour%1*60)).padStart(2,'0')}）`,{stepId:s.id});}
    if(s.recording!==false&&s.stage==='workup'&&!(s.records?.length))push('info','后处理步骤未声明记录',{stepId:s.id});
  }
  // Avoid repeating the same finding at zero-duration boundaries.
  if(missingDensities.size)push('info',`缺少密度：${[...missingDensities].filter(n=>n!=='分相后剩余量未设定').join('、')}；体积仅报告已知部分，固体不虚构液体密度`);
  return findings.filter((f,i,a)=>a.findIndex(x=>x.level===f.level&&x.message===f.message&&x.stepId===f.stepId&&x.chargeId===f.chargeId)===i).sort((a,b)=>({error:0,warn:1,info:2})[a.level]-({error:0,warn:1,info:2})[b.level]);
}
export function explainValue(design,key,library={}){
  const {vars,values,charges}=designState(design,library),v=vars.find(v=>v.key===key);if(!v)throw Error(`找不到计算值：${key}`);
  const inputs=[...new Set([...String(v.expr??'').matchAll(/\[([^\]]+)\]/g)].map(m=>m[1]))].map(key=>({key,label:vars.find(v=>v.key===key)?.label??key,value:values.get(key),unit:vars.find(v=>v.key===key)?.unit??''}));
  const c=charges.find(c=>c.keys.mass===key),name=c?.name??v.label??v.key;let formula=v.expr??String(v.value);
  if(c?.amount.mode==='solidFill'){const names=c.amount.of.map(id=>charges.find(c=>c.id===id)?.material??id).join('+');formula=`(${names})/${c.amount.value} % − (${names})`;}
  else formula=formula.replace(/\[([^\]]+)\]/g,(_,key)=>vars.find(v=>v.key===key)?.label??key);
  const value=values.get(key),display=typeof value==='number'?value.toFixed(3):String(value);
  return {key,value,unit:v.unit,formula,inputs,text:`${name} = ${formula} = ${display}${v.unit?' '+v.unit:''}`};
}
