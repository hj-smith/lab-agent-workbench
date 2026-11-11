import {compileProcedure,procedureRows} from './procedure.js';

// The same exposed, live procedure inputs feed the series picker and DOE API.
export function procedureKnobs(design,registry={factors:[]}){
 const rows=procedureRows(design.procedure),roles={tempC:'reaction_temperature',holdHours:'reaction_hours',monoARatio:'monoA_monoB_ratio',catalystMolPct:'catalyst_molpct',solidPct:'reaction_solids',precipitationRatio:'precipitation_ratio',thfPerProduct:'dilution_ratio'};
 return (design.procedure.knobs??[]).filter(k=>k.exposed!==false).flatMap(k=>{
  const v=design.vars.find(v=>v.key===k.key);if(!v||v.expr!==undefined||!['number','string'].includes(typeof v.value))return [];
  const row=rows.find(({row})=>row.amount?.knob===k.key)?.row;
  const semantic=roles[k.role??v.role]??(row?.amount.rule==='molPct'&&row.role==='催化剂'?'catalyst_molpct':row?.amount.rule==='solids'?'reaction_solids':row?.amount.rule==='molRatio'&&row.role==='单体A'?'monoA_monoB_ratio':/AddH/.test(k.key)?'addh_eq':null);
  const varMatch=rule=>rule?.kind==='var'&&[rule.key,...rule.keys??[]].includes(k.key)||(rule?.fallbacks??[]).some(varMatch);
  const f=registry.factors.find(f=>f.active!==false&&(f.id==='resin.factor.'+semantic||varMatch(f.extract)));
  return [{knob_key:k.key,...f?{factor_id:f.id}:{},name:k.label??v.label??k.key,unit:k.unit??v.unit??'',type:typeof v.value==='number'?'number':'category',current_value:v.value,...k.min!=null?{min:k.min}:{},...k.max!=null?{max:k.max}:{}}];
 });
}
const factorKey=key=>key.replace(/^resin:/,'').replace(/^resin\.factor\./,'');
export function resolveRunList(design,runs,registry){
 const knobs=procedureKnobs(design,registry),fail=(message,code)=>Object.assign(Error(message),{status:400,...code?{code}:{},available_knobs:knobs});
 if(!Array.isArray(runs)||!runs.length)throw fail('runs 需为非空运行清单。');
 const ids=new Set();
 return runs.map(run=>{
  if(!run||typeof run.run_id!=='string'||!run.run_id.trim()||ids.has(run.run_id))throw fail('每行 run_id 需为不同的非空文本。');ids.add(run.run_id);
  if(!run.knobs||typeof run.knobs!=='object'||Array.isArray(run.knobs))throw fail('每行 knobs 需为旋钮键值对象。');
  const applied=[],values={};
  for(const [key,value]of Object.entries(run.knobs)){
   const knob=knobs.find(k=>k.factor_id&&factorKey(k.factor_id)===factorKey(key))??knobs.find(k=>k.knob_key===key);
   if(!knob)throw fail('未知旋钮：'+key+'。请使用 available_knobs 中的旋钮。','UNKNOWN_KNOB');
   if(knob.type==='number'?(typeof value!=='number'||!Number.isFinite(value)||knob.min!=null&&value<knob.min||knob.max!=null&&value>knob.max):(typeof value!=='string'||!value.trim()))throw fail('旋钮 '+key+' 的值无效。');
   if(Object.hasOwn(values,knob.knob_key))throw fail('同一运行重复指定旋钮：'+knob.knob_key);
   values[knob.knob_key]=value;applied.push({key,knob_key:knob.knob_key,from:knob.current_value,to:value});
  }
  return {run_id:run.run_id,knobs:values,knobs_applied:applied};
 });
}
export const compileRunDesign=(design,library)=>design.procedure?compileProcedure(design,library):design;
