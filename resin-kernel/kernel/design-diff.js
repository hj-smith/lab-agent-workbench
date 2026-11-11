import {stepParams} from './modules.js';
import {flattenSteps,resolveSteps,compileChargeVars,variableValues,resolveCondition,samplingNodes} from './design2.js';
import {paramLabel,paramBinding} from './param-bindings.js';
import {durationMinutes} from './durations.js';
import {renderTemplate} from './expr.js';
import {chinese,readableLabel,readableExpression,unitNumber} from './language.js';
import {sopMaterialName,findMaterial} from './materials.js';
const equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b),num=(v,d=2)=>typeof v==='number'?String(Number(v.toFixed(d))):v??'未设定';
function changes(before=[],after=[],id=x=>x.id,{moves=false}={}){
 const old=new Map(before.map((x,i)=>[id(x),{value:x,index:i}])),now=new Map(after.map((x,i)=>[id(x),{value:x,index:i}]));
 const commonOld=[...old.keys()].filter(key=>now.has(key)),commonNow=[...now.keys()].filter(key=>old.has(key)),moved=new Set();
 // Longest common subsequence isolates real moves from insertion/deletion shifts.
 if(moves){const dp=Array.from({length:commonOld.length+1},()=>Array(commonNow.length+1).fill(0));for(let i=commonOld.length-1;i>=0;i--)for(let j=commonNow.length-1;j>=0;j--)dp[i][j]=commonOld[i]===commonNow[j]?1+dp[i+1][j+1]:Math.max(dp[i+1][j],dp[i][j+1]);let i=0,j=0;const kept=new Set();while(i<commonOld.length&&j<commonNow.length){if(commonOld[i]===commonNow[j]){kept.add(commonOld[i]);i++;j++;}else if(dp[i+1][j]>=dp[i][j+1])i++;else j++;}for(const key of commonOld)if(!kept.has(key))moved.add(key);}
 return [...new Set([...old.keys(),...now.keys()])].flatMap(key=>{const a=old.get(key),b=now.get(key);return !a?[{id:key,kind:'add',after:b.value}]:!b?[{id:key,kind:'remove',before:a.value}]:!equal(a.value,b.value)||moved.has(key)?[{id:key,kind:equal(a.value,b.value)?'move':'change',before:a.value,after:b.value,from:a.index,to:b.index}]:[];});
}
export function diffDesign(parent,child,library={}){
 const resolve=d=>{try{const steps=resolveSteps(d,library),compiled=compileChargeVars(d,library,[...d.charges,...steps.flatMap(s=>s.charges)]);return {steps,charges:compiled.charges,vars:[...d.vars,...compiled.vars],values:variableValues([...d.vars,...compiled.vars])};}catch{return {steps:flattenSteps(d.steps),charges:[...d.charges,...flattenSteps(d.steps).flatMap(s=>s.charges??[])],vars:d.vars,values:variableValues(d.vars)};}};
 const a=resolve(parent),b=resolve(child),steps=d=>d.steps.map(raw=>{const step=structuredClone(raw);for(const key of ['paramOverrides','syncedParams','paramVars'])delete step[key];return step;});
 const result={parameters:changes(parent.vars.filter(v=>v.role!=='code'),child.vars.filter(v=>v.role!=='code'),v=>v.key),charges:changes(a.charges,b.charges),steps:changes(steps(a),steps(b),s=>s.id,{moves:true}),sampling:changes([...(parent.samplingPlan?.points??[]).map(x=>typeof x==='number'?{id:'t-'+x,hours:x}:x),...parent.samplingPlan?.nodes??[]],[...(child.samplingPlan?.points??[]).map(x=>typeof x==='number'?{id:'t-'+x,hours:x}:x),...child.samplingPlan?.nodes??[]]),records:changes(parent.records,child.records,r=>r.kind)};
 const sampling=(d,resolved)=>samplingNodes(d,resolved.steps).map(n=>({id:n.id,hours:n.hours??null,tests:n.plannedTests??n.tests,stepId:n.stepId??null,...n.sampleCodeBinding==='manual'?{sampleId:n.sampleId?.replace(d.code+'-','')}: {}}));
 result.sampling=changes(sampling(parent,a),sampling(child,b));
 const details=[],notes=[];
 for(const change of result.charges){const old=change.before?.materialId??change.before?.material,now=change.after?.materialId??change.after?.material;if(old&&now&&old!==now)details.push('投料物料 '+sopMaterialName(findMaterial(library,old)??change.before)+'→'+sopMaterialName(findMaterial(library,now)??change.after));}
 for(const v of result.parameters){if(/~condition-(?:tempC|hours)$/.test(v.id))continue;const uses=flattenSteps(child.steps).flatMap(s=>stepParams(child,library.modules?.find(m=>m.id===s.module),s).filter(p=>paramBinding(child,p,s)?.key===v.id).map(p=>({s,p})));if(uses.length&&uses.every(({s,p})=>s.paramOverrides?.includes(p.key)))continue;const old=v.before?.expr??v.before?.value,now=v.after?.expr??v.after?.value;if(equal(old,now))continue;details.push(`${readableLabel(v.after?.label??v.before?.label??v.id)} ${num(old)}→${num(now)}${v.after?.unit?' '+(/AddH.*MonA/.test(v.id)?'eq':v.after.unit==='v/v'?'倍':v.after.unit):''}`);}
 for(const change of result.steps.filter(s=>s.kind==='change')){
  const old=change.before,now=change.after,module=library.modules?.find(m=>m.id===now.module),parts=[];
  for(const p of stepParams(child,module,flattenSteps(child.steps).find(s=>s.id===now.id)??now)){if(['mL','g'].includes(p.unit)&&typeof p.default!=='number')continue;if(equal(old.args?.[p.key],now.args?.[p.key]))continue;let av,bv;try{av=resolveCondition(old.args?.[p.key],old.args,a.vars);bv=resolveCondition(now.args?.[p.key],now.args,b.vars);}catch{av=old.args?.[p.key];bv=now.args?.[p.key];}if(equal(av,bv)||av==null||bv==null)continue;const bound=paramBinding(child,p,flattenSteps(child.steps).find(s=>s.id===now.id)??now);if(bound&&details.some(t=>t.startsWith(readableLabel(bound.label??bound.key)+' ')))continue;parts.push(`${paramLabel(p)} ${num(av)}→${num(bv)}${p.unit==='v/v'?' 倍':p.unit?' '+p.unit:''}`);}
  for(const [key,label,unit]of [['tempC','温度','°C'],['hours','时长','h']]){try{let av=resolveCondition(old.conditions?.[key],old.args,a.vars),bv=resolveCondition(now.conditions?.[key],now.args,b.vars);if(equal(av,bv))continue;if(key==='hours'){parts.splice(0,parts.length,...parts.filter(p=>!/时长/.test(p)));av=av==null?'未设定':durationMinutes(av);bv=bv==null?'未设定':durationMinutes(bv);}parts.push(`${label} ${num(av)}→${num(bv)} ${key==='hours'?'min':unit}`);}catch{}}
  for(const c of b.charges.filter(c=>now.charges?.some(x=>x.id===c.id))){const previous=a.charges.find(x=>x.id===c.id),field=c.unit==='mL'?'volume':'mass';if(!previous?.keys||!c.keys)continue;const av=a.values.get(previous.keys[field]),bv=b.values.get(c.keys[field]);if(typeof av==='number'&&typeof bv==='number'&&Math.abs(av-bv)>1e-8)parts.push(`${sopMaterialName(findMaterial(library,c.materialId??c.material)??c)} ${unitNumber(av,c.unit)}→${unitNumber(bv,c.unit)} ${c.unit}`);}
  if(parts.length)details.push(`${now.title}：${parts.join('，')}`);
  else if(!equal(old.text,now.text)){const excerpt=(s,state)=>{try{return renderTemplate(s.text,r=>state.values.get(r.key)).slice(0,70);}catch{return String(s.text).replace(/\{[^{}]+\}/g,'…').slice(0,70);}};details.push(`${now.title}：正文「${excerpt(old,a)}」→「${excerpt(now,b)}」`);}
  else if(!equal(old.titleTemplate??old.title,now.titleTemplate??now.title))details.push(`步骤标题 ${old.title}→${now.title}`);
 }
 for(const c of result.charges){if(result.steps.some(s=>s.after?.charges?.some(x=>x.id===c.id)))continue;details.push(`${c.after?.name??c.before?.name??c.id} ${c.kind==='add'?'新增投料':c.kind==='remove'?'删除投料':num(c.before?.amount?.value)+'→'+num(c.after?.amount?.value)}`);}
 for(const kind of ['add','remove','move']){const steps=result.steps.filter(s=>s.kind===kind);if(steps.length)details.push(`${{add:'新增',remove:'删除',move:'移动'}[kind]}：${steps.map(s=>s.after?.title??s.before?.title).join('、')}`);}
 notes.push(...child.syncNotices??[]);for(let i=0;i<details.length;i++)details[i]=chinese(readableExpression(details[i],child.vars,b.charges));
 if(result.sampling.length)notes.push(`取样节点 ${result.sampling.length} 处变化`);if(result.records.length)notes.push(`记录列 ${result.records.length} 处变化`);
 if(!equal(parent.flaskMl,child.flaskMl))details.push(`烧瓶容量 ${num(parent.flaskMl)}→${num(child.flaskMl)} mL`);
 if(!equal(parent.samplingPlan?.t0Clock,child.samplingPlan?.t0Clock))details.push(`计划 t0 ${parent.samplingPlan?.t0Clock}→${child.samplingPlan?.t0Clock}`);
 return {...result,details,notes:notes.map(chinese),summary:`相对 ${parent.code}：${details.length?details.slice(0,5).join('；'):'无设计变化'}`};
}
