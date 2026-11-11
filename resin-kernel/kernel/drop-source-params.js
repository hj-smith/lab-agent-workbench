import {quoteString} from './expr.js';
import {variableValues} from './design2.js';
// Old Excel imports hoisted unmapped cells into "来源参数-N" vars (e.g. the
// sampling-time chain). The user asked to drop them (2026-10-26): each reference
// becomes the value it computed, and vars that only existed to feed them go too.
// The sampling plan keeps its own time points. Frozen revisions and the parent
// snapshot are left as they were.
const SOURCE=/^来源参数-\d+$/,REF=/\[(来源参数-\d+)\]/g;
const literal=v=>typeof v==='number'?(v<0?`(${v})`:String(v)):typeof v==='boolean'?(v?'TRUE':'FALSE'):quoteString(v??'');
const live=doc=>Object.fromEntries(Object.entries(doc).filter(([k])=>k!=='frozenRevisions'&&k!=='parentDesign'));
function inline(value,values){
  if(typeof value==='string')return value.includes('[来源参数-')?value.replace(REF,(all,key)=>values.has(key)?literal(values.get(key)):all):value;
  if(Array.isArray(value))return value.map(v=>inline(v,values));
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,inline(v,values)]));
  return value;
}
export function dropSourceParams(doc){
  const vars=doc.vars??[],source=new Set(vars.filter(v=>SOURCE.test(v.key)).map(v=>v.key));
  if(!source.size)return {doc,removed:0};
  const {get}=variableValues(vars),values=new Map();
  for(const key of source){try{values.set(key,get(key));}catch{}}
  const feeders=new Set(vars.filter(v=>!source.has(v.key)&&[...String(v.expr??'').matchAll(REF)].length).map(v=>v.key));
  return dropUnusedVars({...doc,...inline(live(doc),values)},new Set([...source,...feeders]));
}
// Drops the given vars unless something outside them still refers to them.
export function dropUnusedVars(doc,keys){
  const rest=JSON.stringify({...live(doc),vars:doc.vars.filter(v=>!keys.has(v.key))});
  const used=key=>rest.includes(`[${key}]`)||rest.includes(`{{${key}`)||rest.includes(JSON.stringify(key)+':');
  const drop=new Set([...keys].filter(k=>!used(k)));
  return {doc:{...doc,vars:doc.vars.filter(v=>!drop.has(v.key))},removed:drop.size};
}
