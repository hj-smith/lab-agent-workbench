// Existing v1 `source` fields are text. Structured source evidence belongs in
// additive source_ref, never in a changed JSON type or an [object Object] label.
const copy=structuredClone;
function label(value){
  if(typeof value==='string')return value;
  if(Array.isArray(value))return [...new Set(value.map(label).filter(Boolean))].join('；');
  if(!value||typeof value!=='object')return '';
  if(value.file)return [value.file,value.sheet,value.cell??(!value.sheet?value.address:null)].filter(Boolean).join('!');
  if(value.address)return String(value.address);
  if(value.sheet&&value.cell)return value.sheet+'!'+value.cell;
  if(value.source)return label(value.source);
  if(value.readings)return label(value.readings);
  return '';
}
export function publicSource(source,fallback='原始记录'){
  if(typeof source==='string')return {source};
  if(source==null)return {};
  const source_ref=Array.isArray(source)?{readings:copy(source)}:typeof source==='object'?copy(source):{original_source:copy(source)};
  return {source:label(source)||fallback,source_ref};
}
export function publicFactors(factors){
  return Object.fromEntries(Object.entries(factors).map(([key,value])=>{
    const {source,evidence,...rest}=value,provenance=publicSource(source,'历史档案');
    if(evidence)provenance.source_ref={...provenance.source_ref,evidence:copy(evidence)};
    return [key,{...rest,...provenance,...['conflict','mixed'].includes(rest.state)?{value:null,state:'unknown',reason:rest.reason??'来源值不一致，请核对原始记录'}:{}}];
  }));
}
