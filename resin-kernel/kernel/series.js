import {compileRunDesign} from './doe.js';
import {deriveDesign,flattenSteps,compileChargeVars,resolveSteps,variableValues} from './design2.js';
import {newUid,editRevision} from './identity.js';
import {roleValue} from './var-roles.js';
const clone=x=>structuredClone(x),same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
function location(design,factor){
  const t=factor.target??factor;
  if(t.kind==='stepPresence')return {parent:design,key:'steps',presence:t.stepId};
  if(t.kind==='variable'||t.kind==='var')return {parent:design.vars.find(v=>v.key===(t.key??factor.key)),key:t.field??'value'};
  if(t.kind==='stepArg')return {parent:flattenSteps(design.steps).find(s=>s.id===t.stepId)?.args,key:t.key};
  if(t.kind==='condition')return {parent:flattenSteps(design.steps).find(s=>s.id===t.stepId)?.conditions,key:t.key};
  if(t.kind==='charge')return {parent:[...design.charges,...flattenSteps(design.steps).flatMap(s=>s.charges??[])].find(c=>c.id===t.chargeId)?.amount,key:t.field??'value'};
  const path=(factor.path??t.path??'').split('.');let parent=design;
  for(const key of path.slice(0,-1))parent=Array.isArray(parent)?parent.find(v=>v.id===key||v.key===key):parent?.[key];
  return {parent,key:path.at(-1)};
}
export function readSeriesFactor(design,f){const p=location(design,f);return p.presence?p.parent.steps.some(s=>s.id===p.presence):p.parent?.[p.key];}
const readFactor=readSeriesFactor;
const levelText=value=>typeof value==='boolean'?(value?'有':'无'):typeof value==='number'&&Number.isFinite(value)?value.toFixed(2):String(value??'—');
export function seriesFactorUnit(design,factor){
  const unit=factor.unit??design.vars?.find(v=>v.key===factor.target?.key)?.unit??'';
  const addh=factor.registryId==='resin.factor.addh_eq'||/AddH/i.test([factor.label,factor.key,factor.target?.key].join(' '));
  return addh&&/^mol\s*\/\s*mol$/i.test(unit)?'eq':unit;
}
/** Member titles carry their own tested levels, including plain imported titles. */
export function seriesMemberTitle(base,factors,member){
  let title=String(base.title??base.code??member.code);
  for(const factor of factors){
    const old=readFactor(base,factor),value=readFactor(member,factor);
    if(factor.registryId==='resin.factor.addh_eq'||/AddH/.test(factor.label??''))title=title.replace(/AddH\s*\d+(?:\.\d+)?\s*(?:mol%|eq)/gi,'AddH '+(seriesFactorUnit(member,factor)==='eq'?Number((value*100).toPrecision(8))+'mol%':levelText(value)));
    if(typeof old==='number'&&typeof value==='number'){
      const number=String(old).replaceAll('.','\\.');
      if(factor.registryId==='resin.factor.reaction_hours'||/反应.*(?:时间|时长)|恒温.*(?:时间|时长)/.test(factor.label??''))title=title.replace(new RegExp('(?<![\\d.])'+number+'\\s*h\\b','i'),value+'h');
      if(factor.registryId==='resin.factor.reaction_temperature'||/反应.*(?:温度|内温)/.test(factor.label??''))title=title.replace(new RegExp('(?<![\\d.])'+number+'\\s*(?:℃|°C)','i'),value+'℃');
    }
  }
  const levels=factors.map(f=>{
    const unit=seriesFactorUnit(member,f);
    return (f.label??f.key??'因子')+' '+levelText(readFactor(member,f))+(unit&&unit!=='1'?' '+unit:'');
  });
  return title.replaceAll(base.code,member.code)+' · '+levels.join(' · ');
}
function writeFactor(design,f,value,base){const p=location(design,f);if(!p.parent||!p.key)throw Error(`找不到因子目标：${f.label??f.key}`);if(p.presence){p.parent.steps=p.parent.steps.filter(s=>s.id!==p.presence);if(value){const step=base.steps.find(s=>s.id===p.presence);if(!step)throw Error('有/无步骤因子需要基准步骤');p.parent.steps.splice(base.steps.indexOf(step),0,clone(step));}}else{p.parent[p.key]=clone(value);if(p.key==='value')delete p.parent.expr;}}
export function createSeries(base,factors,library={}, {baseCode=base.code}={}){
  if(factors.length<1||factors.length>2||factors.some(f=>f.levels.length<2||f.levels.length>4))throw Error('对照组选 1–2 个因子，每个因子 2–4 个水平');
  factors=factors.map(f=>({...f,unit:seriesFactorUnit(base,f)}));
  const uid=newUid(),members=[],combinations=factors.reduce((rows,f)=>rows.flatMap(row=>f.levels.map(v=>[...row,v])),[[]]);
  for(const [i,levels]of combinations.entries()){
    const member=String.fromCharCode(65+i),d=deriveDesign(base,{code:baseCode+member});
    factors.forEach((f,j)=>writeFactor(d,f,levels[j],base));d.series={id:uid,factors:clone(factors),member,levels:clone(levels),baseUid:base.uid};d.title=seriesMemberTitle(base,factors,d);members.push(d);
  }
  const series={uid,id:uid,base:clone(base),factors:clone(factors),members,library:clone(library)};return {...series,factorTable:seriesTable(series)};
}
/** Explicit rows preserve run order and allow any number of independent knobs. */
export function createSeriesFromRuns(base,runs,library={}, {baseCode=base.code,id=newUid(),name,doe_id}={}){
  if(!Array.isArray(runs)||!runs.length)throw Error('运行清单不能为空');
  const keys=[...new Set(runs.flatMap(run=>Object.keys(run.knobs)))];
  const factors=keys.map(key=>{const knob=base.procedure?.knobs.find(k=>k.key===key),v=base.vars.find(v=>v.key===key);if(!v||v.expr!==undefined)throw Error('找不到旋钮：'+key);return {key,label:knob?.label??v.label??key,unit:knob?.unit??v.unit??'',target:{kind:'var',key},levels:[...new Set(runs.map(run=>run.knobs[key]??v.value))]};});
  const memberName=index=>{let name='';for(let n=index+1;n;n=Math.floor((n-1)/26))name=String.fromCharCode(65+(n-1)%26)+name;return name;};
  const members=runs.map((run,i)=>{
    let d=deriveDesign(base,{code:baseCode+memberName(i)});delete d.conversion;
    // Conversion keeps declarations in procedure.legacyNodes; drop inherited
    // sample identities just as deriveDesign does for generated sampling nodes.
    for(const node of d.procedure?.legacyNodes??[])for(const key of ['uid','lotUid','sampleId','aliases','legacySampleId','historicalSampleSource'])delete node[key];
    for(const [key,value]of Object.entries(run.knobs))writeFactor(d,{target:{kind:'var',key}},value,base);
    d=compileRunDesign(d,library);
    d.series={id,name:name??doe_id??baseCode,factors:clone(factors),member:memberName(i),levels:factors.map(f=>readFactor(d,f)),baseUid:base.uid,mode:'runs',...doe_id?{doe_id,doe_run_id:run.run_id}:{}};
    d.title=seriesMemberTitle(base,factors,d);return d;
  });
  const series={uid:id,id,base:clone(base),factors,members,library:clone(library)};return {...series,factorTable:seriesTable(series)};
}
export const executedMember=d=>!!d.executions?.length||!!d.frozenRevisions?.length||['执行中','完成','异常终止'].includes(d.status);
export function applyToSeries(series,op){
  const out={...series,members:series.members.map(d=>{
    const levels=outLevels(d,series.factors),next=typeof op==='function'?op(editRevision(d)):applyPath(editRevision(d),op);
    if(series.factors.some((f,i)=>!same(readFactor(next,f),levels[i])))throw Error('因子水平只能在对照组表修改');
    return next;
  })};out.factorTable=seriesTable(out);return out;
}
const outLevels=(d,factors)=>factors.map(f=>readFactor(d,f));
function applyPath(d,op){const p=location(d,{path:op.path});if(!p.parent)throw Error('编辑路径不存在');p.parent[p.key]=clone(op.value);return d;}
export function seriesTable(series,library=series.library??{}){
  return series.members.map(d=>{
    let derived={},error;
    try{const steps=resolveSteps(d,library),c=compileChargeVars(d,library,[...d.charges,...steps.flatMap(s=>s.charges)]),v=variableValues([...d.vars,...c.vars]);derived={charges:Object.fromEntries(c.charges.map(c=>[c.id,{massG:v.get(c.keys.mass),volumeMl:v.get(c.keys.volume)}])),hehMassG:c.charges.find(c=>c.materialId==='AddH')?v.get(c.charges.find(c=>c.materialId==='AddH').keys.mass):null,totalVolumeMl:c.charges.every(c=>typeof v.get(c.keys.volume)==='number')?c.charges.reduce((n,c)=>n+v.get(c.keys.volume),0):null,volumeReason:'缺少'+c.charges.filter(c=>typeof v.get(c.keys.volume)!=='number').map(c=>c.name).join('、')+'密度或用量；已知部分 '+Math.round(c.charges.reduce((n,c)=>n+(typeof v.get(c.keys.volume)==='number'?v.get(c.keys.volume):0),0))+' mL',expectedProduct:roleValue(d.vars,v,'expectedProduct')};}catch(e){error=e.message;}
    return {uid:d.uid,code:d.code,factors:Object.fromEntries(series.factors.map((f,i)=>[f.key??f.label??String(i),readFactor(d,f)])),derived,status:d.status,...error?{unavailable:error}:{}};
  });
}
