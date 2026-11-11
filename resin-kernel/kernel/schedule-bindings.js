import {parseTemplate,parseExpr,evaluateExpr,formatText} from './expr.js';

/** A sampling time is a kernel expression, not a frozen copy of its value.
 * Node IDs remain stable when a duration changes. Source addresses are evidence
 * only; the expression is always expressed in the owning design parameters. */
export function plannedNodeHour(node,vars=[]){
  if(!node.hoursExpr)return node.hours;
  const definitions=new Map(vars.map(v=>[v.key,v])),active=new Set(),cache=new Map();
  const get=key=>{if(cache.has(key))return cache.get(key);if(active.has(key))throw Error('取样时间存在循环参数');const v=definitions.get(key);if(!v)throw Error('取样时间引用的参数未设置');active.add(key);const value=v.expr===undefined?v.value:evaluateExpr(parseExpr(v.expr),r=>get(r.key));active.delete(key);cache.set(key,value);return value;};
  return evaluateExpr(parseExpr(node.hoursExpr),r=>get(r.key));
}
export function renderPlanTemplate(text,vars=[]){
  const definitions=new Map(vars.map(v=>[v.key,v])),get=key=>{const v=definitions.get(key);if(!v)return '待指定';return v.expr===undefined?v.value:evaluateExpr(parseExpr(v.expr),r=>get(r.key));};
  return parseTemplate(text??'').map(p=>p.text!==undefined?p.text:formatText(evaluateExpr(p.expr,r=>get(r.key)),p.fmt)).join('');
}
export function holdTimeVariable(design){
  const steps=(design.steps??[]).flatMap(s=>[s,...(s.branches??[]).flatMap(b=>b.steps??[])]);
  const refs=steps.filter(s=>s.conditions?.kind==='hold'&&s.stage!=='workup').flatMap(s=>[...String(s.conditions.hours??'').matchAll(/\[([^\]]+)\]/g)].map(m=>m[1]));
  const candidates=design.vars?.filter(v=>refs.includes(v.key)&&v.unit==='h'&&/反应|恒温/.test(v.label??v.key))??[];
  return candidates.length===1?candidates[0]:undefined;
}
export function activeTitleTemplate(step){return step.titleTemplate&&(step.titleTemplateValue==null||step.title===step.titleTemplateValue)?step.titleTemplate:undefined;}
export function refreshTemplateTitles(design){for(const step of design.steps??[]){const template=activeTitleTemplate(step);if(template){step.title=renderPlanTemplate(template,design.vars);step.titleTemplateValue=step.title;}}return design;}
/** Bind only known imported source operations. Never search/replace a user's
 * free text, and never infer unrelated historical module parameters. */
export function bindSourceSchedule(design,{source=false,migrate=false}={}){
  const hold=holdTimeVariable(design);if(!hold)return design;
  const token=`{[${hold.key}]|0.#}`,expr=`[${hold.key}]`,points=design.samplingPlan?.points??[],nodes=design.samplingPlan?.nodes??[];
  const transparent=value=>{let current=value,seen=new Set();while(typeof current==='string'){const ref=/^\[([^\]]+)\]$/.exec(current);if(!ref||seen.has(ref[1]))break;seen.add(ref[1]);const variable=design.vars.find(v=>v.key===ref[1]);if(variable?.expr===undefined)break;current=variable.expr;}return current;};
  for(const point of points)if(typeof point==='object'&&point.hoursExpr)point.hoursExpr=transparent(point.hoursExpr);
  const pre=nodes.find(n=>n.id==='PRE-AddH'),addh=nodes.find(n=>n.id==='AddH-80C-1h');
  if(migrate&&pre&&design.source&&!pre.hoursExpr){const matches=points.map((p,index)=>({p,index})).filter(({p})=>(typeof p==='number'?p:p.hours)===pre.hours);if(matches.length===1){const {p,index}=matches[0];points[index]={...(typeof p==='number'?{id:`t-${String(p).replace('.','_')}`,hours:p}:p),hoursExpr:expr};}}
  const sourceEnd=points.find(p=>typeof p==='object'&&p.hoursExpr===expr);
  if(sourceEnd&&pre)sourceEnd.sameSampleAs??=pre.id;
  if(sourceEnd&&pre){pre.hoursExpr=sourceEnd.hoursExpr;pre.timeBinding={kind:'source-formula',parameter:hold.key,sourceAddress:sourceEnd.sourceAddress};if(pre.sampleCodeBinding!=='manual'&&(!pre.sampleId||new RegExp('^'+design.code+'-\\d+(?:\\.\\d+)?h-PRE-AddH$').test(pre.sampleId)))pre.sampleCodeBinding='time';}
  if(pre?.hoursExpr&&addh){const owner=design.steps.find(s=>s.id===addh.stepId),hours=owner?.conditions?.hours;
    if(typeof hours==='string'&&/^\[[^\]]+\]$/.test(hours)){addh.hoursExpr=`(${pre.hoursExpr})+(${hours})`;addh.timeBinding={kind:'module-parameter',stepId:owner.id};}}
  if((source||migrate&&design.source)&&sourceEnd&&pre){const value=String(pre.hours??hold.value).replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),pattern=new RegExp(`(?<![\\d.])${value}(?:\\.0)?\\s*h(?=\\s*(?:节点|后))`,'g');
    for(const step of design.steps){
      // These exact source operations share the explicit PRE-AddH endpoint.
      if(/PRE-AddH/.test(step.sourceText??'')&&/节点|取样/.test(step.title+' '+step.text))step.text=step.text.replace(pattern,token+' h');
      if(/后直接加\s*AddH/.test(step.title)&&!step.titleTemplate){step.titleTemplate=step.title.replace(pattern,token+' h');step.title=renderPlanTemplate(step.titleTemplate,design.vars);step.titleTemplateValue=step.title;}
    }
  }
  return design;
}
