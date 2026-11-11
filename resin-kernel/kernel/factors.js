import {availableName} from './merge-edits.js';
import {flattenSteps,resolveSteps,compileChargeVars,variableValues,resolveCondition,scheduleDesign} from './design2.js';
import {moduleCategory} from './modules.js';
import {parseExpr,evaluateExpr,renderTemplate} from './expr.js';
import {executionDesign} from './identity.js';
import {paramBinding} from './param-bindings.js';
import {findMaterial} from './materials.js';
import {historicalFactorEvidence,recordedTermination} from './history-factors.js';

const kinds=['var','charge','module_exists','module_param','condition','timing','custom'];
const match=(text,pattern)=>pattern==null||new RegExp(pattern,'iu').test(String(text??''));
const finite=v=>typeof v!=='number'||Number.isFinite(v);
export function unfilledFactorPlaceholder(plan,value){
  if(value?.state!=='set'||typeof value.value!=='number')return false;
  const [,id,section,key]=value.path?.split('/')??[];
  const step=flattenSteps([...plan.steps??[],...Object.values(plan.historicalBranches??{}).flatMap(b=>b.steps??[])]).find(s=>s.id===id);
  const raw=step?.[section]?.[key];if(typeof raw!=='string')return false;
  return [...raw.matchAll(/\{\{([^}|]+)(?:\|[^}]*)?\}\}/g)].some(([,name])=>step.args?.[name]==null&&!(plan.vars??[]).some(v=>v.key===name&&(v.value!=null||v.expr!=null)));
}
// Presentation only: extraction and stored/v1 factor values remain unchanged.
export const factorPresentation=(plan,value)=>unfilledFactorPlaceholder(plan,value)?{...value,value:null,state:'to_confirm',reason:'模板占位默认值，未填写'}:value;
export function validateFactor(f){
  if(!/^resin\.factor\.[a-z][a-z0-9_]*$/.test(f.id??''))throw Error('因子 id 必须是稳定的 resin.factor.* 标识');
  if(!f.label||typeof f.unit!=='string'||!['number','category','bool'].includes(f.type))throw Error('因子 label/unit/type 无效');
  for(const r of [f.extract,...f.extract?.fallbacks??[]]){
    if(!r||!kinds.includes(r.kind))throw Error('因子提取规则 kind 无效');
    for(const key of ['pattern','label_match','category','name','text_pattern','exclude','exclude_category'])if(r[key]!=null)new RegExp(r[key],'iu');
    if(r.kind==='custom'){
      const allowed=new Set(['IF','IFERROR','AND','OR','NOT','ISNUMBER','ISTEXT','LEFT','TIME','CEILING','INT','MOD','ABS','ROUND','MIN','MAX','SUM','COUNT','TEXT','EXP','SUBSTITUTE','TEXTAFTER']);
      const check=ast=>{if(ast.type==='ref'&&ast.kind!=='var'||ast.type==='range'||ast.type==='call'&&!allowed.has(ast.name))throw Error('因子表达式仅支持设计变量和内核函数');for(const child of [ast.left,ast.right,ast.arg,...ast.args??[]])if(child)check(child);};
      check(parseExpr(r.expression));
    }
  }
  return structuredClone({...f,active:f.active!==false,version:f.version??1,notes:f.notes??''});
}
/** Immutable registry API. Storage/UI callers supply an optimistic registry version. */
export function editFactorRegistry(registry,op,factor){
  const out=structuredClone(registry),id=typeof factor==='string'?factor:factor.id,index=out.factors.findIndex(f=>f.id===id);
  if(op==='add'){out.factors.push(validateFactor({...factor,id:availableName(factor.id,out.factors.map(f=>f.id)),version:1}));}
  else {if(index<0)throw Error('因子不存在');out.factors[index]=validateFactor({...out.factors[index],...op==='deactivate'?{active:false}:factor,id,version:out.factors[index].version+1});}
  out.version++;return out;
}
export const listFactors=(registry,{includeInactive=true}={})=>structuredClone(registry.factors.filter(f=>includeInactive||f.active));
export const addFactor=(r,f)=>editFactorRegistry(r,'add',f);
export const updateFactor=(r,id,patch)=>editFactorRegistry(r,'update',{...patch,id});
export const deactivateFactor=(r,id)=>editFactorRegistry(r,'deactivate',id);

/** All expressions use the kernel expression parser; never JS eval/Function.
 * A branch selects its workup only; reaction setpoints stay shared. */
export function extractFactors(designOrBatch,registry,library={}){
  const input=designOrBatch.plan??designOrBatch.design??designOrBatch;
  const design=executionDesign(input,designOrBatch.revision_uid??input.revisionUid),branch=designOrBatch.branch;
  let steps=flattenSteps(design.steps),charges=[...design.charges??[],...steps.flatMap(s=>s.charges??[])],vars=design.vars??[];
  if(library.materials){steps=resolveSteps(design,library);const c=compileChargeVars(design,library,[...design.charges,...steps.flatMap(s=>s.charges??[])]);charges=c.charges;vars=[...vars,...c.vars];}
  const values=variableValues(vars),evaluate=value=>{
    if(typeof value!=='string')return value;
    try{return evaluateExpr(parseExpr(value),r=>values.get(r.key));}catch{return value;}
  };
  if(branch){
    const history=design.historicalBranches?.[branch];
    steps=history?[...steps.filter(s=>s.stage!=='workup'),...history.steps]:steps.filter(s=>!s.branch||s.branch===branch);
  }
  const choose=r=>steps.filter(s=>{
    if(r.stepId&&s.id!==r.stepId)return false;
    const module=library.modules?.find(m=>m.id===s.module),category=s.category??module?.category??moduleCategory(s.title);
    // Category gates are semantic boundaries. A reagent mentioned in another
    // operation's source text must never turn that operation into a quench.
    const categoryOK=match(category,r.category)||r.roles?.includes(s.role??module?.role);
    return categoryOK&&(!r.exclude_category||!match(category,r.exclude_category))&&match(s.title,r.name)&&match(s.text+' '+s.sourceText,r.text_pattern)&&(!r.exclude||!match(s.title,r.exclude))&&(!r.stage||s.stage===r.stage);
  });
  function run(r,definition){
    if(r.kind==='var'){
      const v=vars.find(v=>(r.key===undefined||v.key===r.key)&&(r.keys===undefined||r.keys.includes(v.key))&&match(v.label??v.key,r.label_match));
      if(!v)return {state:'unknown',reason:'设计未声明此参数'};
      const effective=steps.filter(s=>s.charges?.length||s.stage==='reaction'&&s.conditions).flatMap(s=>{
        const module=library.modules?.find(m=>m.id===s.module),p=module?.params?.find(p=>paramBinding(design,p)?.key===v.key);
        return p&&s.args?.[p.key]!=null?[{value:resolveCondition(s.args[p.key],s.args,vars),path:'steps/'+s.id+'/args/'+p.key}]:[];
      });
      if(effective.length)return effective[0];
      return {value:values.get(v.key),path:'vars/'+v.key};
    }
    if(r.kind==='custom')return {value:evaluateExpr(parseExpr(r.expression),ref=>values.get(ref.key)),path:'expression:'+r.expression};
    if(r.kind==='charge'){
      const cs=charges.filter(c=>(!r.chargeId||c.id===r.chargeId)&&(!r.material||match(c.materialId??c.material,r.material))&&(!r.role||c.role===r.role)&&(!r.initial_only||design.charges.some(d=>d.id===c.id)));
      if(!cs.length)return {state:'absent',reason:'无匹配投料',value:r.absent_value??null};
      const c=cs[0],basis=c.displayBasis??c.amount,field=r.field;
      if(field==='material')return {value:[...new Set(cs.map(c=>c.materialId??c.material))].join(' + '),path:'charges/'+cs.map(c=>c.id).join(',')+'/material'};
      let value;
      if(['eq','molpct'].includes(field)){
        const baseMaterial=r.of_material??(r.material==='^AddH$'?'MonA':null),reference=baseMaterial&&charges.find(c=>design.charges.some(x=>x.id===c.id)&&(c.materialId??c.material)===baseMaterial),referenceMmol=reference?.keys?values.get(reference.keys.mmol):null;
        const quantities=cs.map(c=>{if(referenceMmol>0&&c.keys){const mmol=values.get(c.keys.mmol);return typeof mmol==='number'?mmol/referenceMmol*(field==='molpct'?100:1):null;}const b=c.displayBasis??c.amount;return b.mode===field?evaluate(b.value):null;});
        value=quantities.every(v=>typeof v==='number'&&Number.isFinite(v))?quantities.reduce((a,b)=>a+b,0):null;
      }
      else if(c.keys)value=values.get(c.keys[field]);
      if(value==null)return {state:'unknown',reason:'投料未声明所需计算基准'};
      return {value,path:'charges/'+c.id+'/'+field,...basis.of?{basis:basis.of}:{}};
    }
    const selected=choose(r),paths=selected.map(s=>'steps/'+s.id);
    if(r.kind==='module_exists')return {value:selected.length>0,state:selected.length?'set':'absent',path:paths.join(',')||'steps',...selected.length?{}:{reason:'无匹配步骤'}};
    if(r.kind==='module_param'){
      if(r.aggregate==='count')return {value:selected.length,path:paths.join(',')||'steps'};
      // Material maps in older registries read prose. An operation's actual
      // solvent charge is stronger evidence than a retained source sentence.
      if(r.map?.length&&(r.map.every(mapping=>findMaterial(library,mapping.value))||['resin.factor.dilution_solvent','resin.factor.precipitation_solvent'].includes(definition?.id))){
        const materialSteps=selected.length?selected:choose({...r,name:undefined,text_pattern:undefined}),owned=new Set(materialSteps.flatMap(step=>(step.charges??[]).map(charge=>charge.id))),actual=charges.filter(charge=>owned.has(charge.id)&&charge.role==='solvent');
        if(actual.length)return {value:[...new Set(actual.map(charge=>charge.materialId??charge.material))].join(' + '),path:'charges/'+actual.map(charge=>charge.id).join(',')+'/material'};
      }
      if(!selected.length){
        if(definition?.id==='resin.factor.termination'){
          const declared=steps.find(step=>step.stage==='workup'&&/^(?:稀释|中和|终止|终止\/中和)$/.test(step.category??moduleCategory(step.title))&&!match(step.title,r.exclude??'GPC|分析|取样|样品')&&recordedTermination(step.text??'')?.value==='无（不中和）');
          return declared?{value:'无（不中和）',state:'set',path:'steps/'+declared.id+'/text',reason:'原文明确声明不中和/不加终止剂'}:{value:null,state:'unknown',reason:'未记录主体终止/中和方式'};
        }
        return {state:r.absent_state??'absent',value:r.absent_value??null,reason:'无匹配步骤'};
      }
      let pairs=selected.map(s=>({value:['text','sourceText'].includes(r.key)?s[r.key]??s.text:s.args?.[r.key],path:'steps/'+s.id+'/'+(['text','sourceText'].includes(r.key)?r.key:'args/'+r.key)}));
      if(r.map){const text=pairs.map(p=>p.value).join(' '),mapped=r.map.find(m=>match(text,m.pattern)),termination=definition?.id==='resin.factor.termination'?recordedTermination(text):null;return termination?{...termination,path:pairs.map(p=>p.path).join(',')}:mapped?{value:mapped.value,path:pairs.map(p=>p.path).join(',')}:{state:'unknown',reason:'步骤存在，但无法识别其参数'};}
      pairs=pairs.filter(p=>p.value!=null).map(p=>({...p,value:evaluate(p.value)}));
      if(!pairs.length)return {state:'unknown',reason:'模块参数未声明'};
      return r.aggregate==='join'?{value:pairs.map(p=>p.value).join('；'),path:pairs.map(p=>p.path).join(',')}:pairs[0];
    }
    if(r.kind==='condition'){
      const step=selected.find(s=>s.conditions?.[r.field]!=null);
      if(!step)return {state:'unknown',reason:'未声明该步骤条件'};
      return {value:resolveCondition(step.conditions[r.field],step.args,vars),path:'steps/'+step.id+'/conditions/'+r.field};
    }
    if(r.kind==='timing'){
        const target=selected[r.index??0];if(!target)return {state:'absent',value:null,reason:'无此补加步骤'};
      return {value:scheduleDesign(design,library).find(s=>s.stepId===target.id)?.start,path:'steps/'+target.id+'/start'};
    }
  }
  const historical=historicalFactorEvidence({},design,branch);
  return Object.fromEntries(registry.factors.filter(f=>f.active!==false).map(f=>{
    let result;
    try{
      for(const r of [f.extract,...f.extract.fallbacks??[]]){const candidate=run(r,f);if(candidate.value!=null){result=candidate;if(r.scale!=null)result.value*=r.scale;break;}if(!result||result.state!=='absent')result=candidate;}
      const raw=result?.value??null;
      const value=typeof raw==='string'&&/\{[^{}]+\}/.test(raw)?renderTemplate(raw,r=>values.get(r.key)):raw;
      if(!finite(value)||value===null||f.type==='number'&&typeof value!=='number'||f.type==='bool'&&typeof value!=='boolean'||f.type==='category'&&typeof value!=='string')result={...result,value:null,state:result?.state??'unknown',reason:result?.reason??'值缺失、类型不符或非有限数'};
      else result={...result,value,state:result.state??'set'};
    }catch(e){result={value:null,state:'unknown',reason:e.message};}
    // Historical recorded factors describe the source batch. They are separate
    // evidence, with the exact summary cell retained even when a reconstructed
    // charge list or variable has a different basis.
    result=historical[f.id]??result;
    return [f.id,{origin:'design_setpoint',...result,unit:f.unit,path:result.path??null,registry_version:registry.version,factor_version:f.version}];
  }));
}
export const previewFactors=(batches,registry,library={})=>batches.map(b=>({uid:b.uid??b.batch?.uid,code:b.code,registry_version:registry.version,factors:extractFactors(b,registry,b.library??library)}));
