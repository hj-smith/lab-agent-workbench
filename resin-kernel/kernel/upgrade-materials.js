import {stableUid} from './identity.js';

const rows=value=>Array.isArray(value)?value:[];
const clean=value=>typeof value==='string'?value.replace(/^material:/,''):'';
// Do not enrich an existing row: explicitly cleared physical properties are user data.
export function ensureReferencedMaterials(materials,designs,seed={}){
  const added=[],seedRows=rows(seed.materials??seed);
  const exact=(values,query)=>values.find(m=>m.id===query)??values.find(m=>m.name===query||m.abbr===query);
  const ensure=query=>{
    query=clean(query);if(!query||query==='待指定')return;
    if(exact(materials,query)||materials.some(m=>m.aliases?.includes(query)))return;
    const shipped=exact(seedRows,query);
    const existing=shipped&&(exact(materials,shipped.id)??materials.find(m=>[shipped.name,shipped.abbr].filter(Boolean).some(n=>m.name===n||m.abbr===n)));
    if(existing){
      existing.aliases=[...new Set([...existing.aliases??[],query])];
      added.push({id:existing.id,name:existing.name,recordedName:query,source:'existing-alias',message:`原档物料 ${query} 已按相同名称或缩写关联到 ${existing.name}；仅补充引用别名，原物性保留。`});
      return;
    }
    const id=/[\[\]{}|@/]/.test(query)?stableUid('lab-recorded-material',query):query;
    const material=shipped?structuredClone(shipped):{id,name:query,abbr:query,mw:null,density:null,purity:null,propertyStatus:'待补物性',source:'升级补建：仅保留原档记录名称，物性未记录。'};
    if(!shipped)material.notes='待补物性';
    materials.push(material);
    added.push({id:material.id,name:material.name,recordedName:query,source:shipped?'seed':'recorded-name',message:shipped?`物料库缺少 ${query}，已从随附种子库补入；请核对物性来源。`:`物料库缺少 ${query}，已按原档名称补建，请补充物性（待补物性）。`});
  };
  const visit=design=>{
    for(const material of rows(design?.materials))ensure(material.ref);
    const aliases=new Map(rows(design?.materials).map(m=>[m.as,m.ref]));
    const charges=items=>{for(const c of rows(items))ensure(aliases.get(c.material)??c.material??c.materialId??c.name);};
    const steps=items=>{for(const s of rows(items)){charges(s.charges);for(const b of rows(s.branches))steps(b.steps);}};
    charges(design?.charges);steps(design?.steps);charges(design?.plan?.sourceCharges);
    for(const frozen of rows(design?.frozenRevisions))visit(frozen.plan);
  };
  for(const design of designs)visit(design);
  return added;
}

// A frozen library is retained verbatim on disk. Only absent dependencies are
// supplied in the calculation view; existing execution constants always win.
export function executionMaterialLibrary(library,current,design){
  const effective={...library,materials:structuredClone(library?.materials??[])};
  ensureReferencedMaterials(effective.materials,[design],current);
  return effective;
}
