import {availableName} from './merge-edits.js';
// Library operations are immutable; callers own persistence and optimistic versions.
const normalizedNames=new Map();
export const normalizeName = value => {const text=String(value??'');if(normalizedNames.has(text))return normalizedNames.get(text);const normalized=text.normalize('NFKC').toLocaleLowerCase('en').replace(/\s+/g,'').trim();if(normalizedNames.size>=4096)normalizedNames.delete(normalizedNames.keys().next().value);normalizedNames.set(text,normalized);return normalized;};
export const materialList = library => Array.isArray(library) ? library : library?.materials ?? [];
export const materialDisplayName=m=>m?.name??m?.material??m?.id??'';
// A library id is a stable reference, not necessarily a chemical abbreviation.
// Older imports filled `abbr` with ids such as xylene and heptane. Keep those
// stored values intact, but never promote them to an operator-facing label.
const materialNames={xylene:'二甲苯',heptane:'正庚烷',dioxane:'1,4-二氧六环',pyridine:'吡啶',water:'水'};
export function materialAbbreviation(material){
  const candidates=[material?.abbr,material?.materialId??material?.material??material?.id,...material?.aliases??[]];
  return candidates.find(value=>typeof value==='string'&&/^(?:\d+-)?[A-Za-z\d-]+$/.test(value.trim())&&!materialNames[value.trim().toLowerCase()]&&(value.match(/[A-Z]/g)?.length??0)>=2)?.trim()??'';
}
export const sopMaterialName=m=>materialAbbreviation(m)||(/[\u3400-\u9fff]/.test(m?.name??'')?m.name:materialNames[String(m?.materialId??m?.material??m?.id??m?.name??'').toLowerCase()]??materialDisplayName(m));
export const materialState=m=>({form:m?.form??(/无水/.test(m?.name??'')?'无水':m?.id==='BaseQ'&&/25%.*溶液/.test(m?.name??'')?'溶液':'未指定'),wt:m?.wt??(m?.id==='BaseQ'&&/25%.*溶液/.test(m?.name??'')?25:null)});
export const materialStateText=m=>{const {form,wt}=materialState(m);return form+(form==='溶液'&&wt!=null?' '+wt+' wt%':'')+(form==='溶液'&&m?.solvent?' · 溶剂 '+m.solvent:'');};
// Read adapters normalize units without changing the historical stored value.
export const materialPurityFraction=material=>material?.purity==null||material.purity===''?null:material.purityUnit==='%'||material.purity>1?Number(material.purity)/100:Number(material.purity);
export function normalizeMaterial(material) {
  const id=String(material.id??material.abbr??material.name??'').normalize('NFKC').trim();
  if(!id || /[\[\]{}|@/]/.test(id))throw Error('物料 id 无效');
  const state=materialState(material);
  const out={...material,...state,id,name:materialDisplayName({...material,id}),abbr:material.abbr??materialAbbreviation({id}),cas:material.cas??'',mw:material.mw??null,density:material.density??null,purity:material.purity??null,bp:material.bp??null,kind:material.kind??'其他',notes:material.notes??'',source:material.source??'',archived:!!material.archived};
  if(out.bp!==null&&!Number.isFinite(out.bp))throw Error(`物料 ${id} 的 bp 无效`);
  for(const key of ['mw','density','purity'])if(out[key]!==null && (!Number.isFinite(out[key]) || out[key]<=0 || key==='purity'&&materialPurityFraction(out)>1))throw Error(`物料 ${id} 的 ${key} 无效`);
  out.aliases=[...new Set([id,out.name,out.abbr,...material.aliases??[]].filter(Boolean))];return out;
}
export function findMaterial(library, query) {
  const key=normalizeName(query);return materialList(library).find(m=>[m.id,m.name,m.abbr,...m.aliases??[]].some(a=>normalizeName(a)===key));
}
// Workbook headings append quantities to chemical names. A chemical token must
// end before that suffix: SolvC is never an abbreviation for SolvA.
export function materialFromLabel(library,label){
  const normalize=value=>normalizeName(value).replace(/无水|^p-/g,'');
  const text=normalize(label),matches=materialList(library).flatMap(material=>[material.id,material.name,material.abbr,...material.aliases??[]].filter(Boolean).map(alias=>({material,key:normalize(alias)}))).filter(({key})=>key&&text.startsWith(key)&&!/[a-z0-9]/i.test(text.slice(key.length,key.length+1)));
  matches.sort((a,b)=>b.key.length-a.key.length);return matches[0]?.material;
}
export function addMaterial(library, material) {
  const next=normalizeMaterial(material);next.id=availableName(next.id,materialList(library).map(m=>m.id));
  return [...materialList(library),next];
}
export function updateMaterial(library,id,patch) {
  const found=findMaterial(library,id);if(!found)throw Error(`找不到物料：${id}`);
  if(patch.id!==undefined&&patch.id!==found.id)throw Error('物料 id 不可更改');
  return materialList(library).map(m=>m===found?normalizeMaterial({...m,...patch}):structuredClone(m));
}
export const archiveMaterial=(library,id,archived=true)=>updateMaterial(library,id,{archived});
export function referencingDesigns(library,id,designs=[]) {
  const material=findMaterial(library,id);if(!material)return [];
  return designs.filter(design=>{
    const resolve=value=>typeof value==='string'?findMaterial(library,value.replace(/^material:/,'')):null;
    const aliases=new Set((design.materials??[]).filter(m=>resolve(m.ref)?.id===material.id).map(m=>m.as));
    const matches=value=>aliases.has(value)||resolve(value)?.id===material.id;
    const walk=steps=>(steps??[]).some(s=>(s.charges??[]).some(c=>matches(c.material))||Object.values(s.args??{}).some(v=>typeof v==='string'&&matches(v))||(s.branches??[]).some(b=>walk(b.steps)));
    return aliases.size>0||(design.charges??[]).some(c=>matches(c.material))||walk(design.steps);
  }).map(d=>({id:d.id??d.uid,uid:d.uid,code:d.code,title:d.title}));
}
export function deleteMaterial(library,id,designs=[]) {
  const references=referencingDesigns(library,id,designs),found=findMaterial(library,id);
  return {deleted:!!found&&!references.length,materials:references.length?structuredClone(materialList(library)):materialList(library).filter(m=>m!==found).map(m=>structuredClone(m)),references};
}
export function mergeMaterialSeed(library,seed) {
  let out=materialList(library).map(normalizeMaterial);
  for(const item of materialList(seed)){
    const old=findMaterial(out,item.id)??findMaterial(out,item.name);
    if(!old){out.push(normalizeMaterial(item));continue;}
    out=updateMaterial(out,old.id,{...Object.fromEntries(Object.entries(item).filter(([k,v])=>old[k]==null&&v!=null)),aliases:[...old.aliases,...item.aliases??[],item.id,item.name]});
  }return out;
}
