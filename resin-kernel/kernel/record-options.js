/** Explicit per-design switches. A new design stays lean; conversion enables
 * only fields actually present in its source record sheets. Derivation carries
 * the parent's choices without consulting an unrelated module's defaults. */
export const recordFieldDefaults={
  timeline:[{key:'外温',label:'外温',unit:'°C',pattern:/外温/},{key:'搅拌速度',label:'转速',unit:'rpm',pattern:/搅拌.*rpm|转速/},{key:'TsOH溶解相态',label:'TsOH 溶解/相态',type:'text',pattern:/TsOH.*溶解.*相态/},{key:'GPC取样质量',label:'GPC 取样质量',unit:'g',pattern:/GPC.*取样质量|GPC样品.*g/}],
  context:[{key:'首次冷凝回流时刻',label:'首次冷凝回流时刻',type:'time',pattern:/首次冷凝回流时刻/},{key:'升温前初始水层',label:'升温前初始水层',unit:'mL',pattern:/升温前初始水层/},{key:'分水器预装二甲苯',label:'分水器预装二甲苯',unit:'g',pattern:/^分水器预装二甲苯$/}],
  samples:[{key:'GPC样品质量',label:'GPC 样品质量',unit:'g',pattern:/^(?:GPC)?样品(?:固体)?质量|样品\/固体质量/},{key:'终止液质量',label:'终止液质量',unit:'g',pattern:/终止液质量|DMAP.*THF.*g/},{key:'终配浓度',label:'终配浓度',unit:'mg/mL',pattern:/终配浓度/},{key:'RI面积',label:'RI 面积',unit:'',pattern:/RI.*面积/}]
};
export const recordFieldOptions=Object.fromEntries(Object.entries(recordFieldDefaults).map(([kind,fields])=>[kind,fields.map(field=>({...field}))]));
export function configureRecordFields(fields={}){for(const [kind,defaults]of Object.entries(recordFieldDefaults))recordFieldOptions[kind]=(fields?.[kind]??defaults).map(field=>({...defaults.find(d=>d.key===field.key),...field}));}
export const recordOption=(kind,key,design)=>recordFieldOptions[kind]?.find(field=>field.key===key)??design?.recordFields?.[kind]?.find(field=>field.key===key);
export function fieldsForDesign(design){return Object.fromEntries(Object.entries(recordFieldOptions).map(([kind,fields])=>[kind,[...fields,...(design.recordFields?.[kind]??[]).filter(f=>!fields.some(x=>x.key===f.key))]]));}
// 反应 HPLC: one 峰面积 column per component of the lab's HPLC methods (library setting).
let hplcComponentNames=['MonA','MonB'];
export const hplcComponents=()=>hplcComponentNames;
export function configureHplcComponents(names){if(Array.isArray(names)&&names.length)hplcComponentNames=[...new Set(names.map(String).filter(Boolean))];}
export function enabledRecordOptions(design,kind){return design.records?.find(r=>r.kind===kind)?.optionalColumns??[];}
export function setRecordOptions(design,selected={}){
  const out=structuredClone(design);out.records??=[];
  for(const [kind,options]of Object.entries(fieldsForDesign(design))){let row=out.records.find(r=>r.kind===kind);if(!row){row={kind};out.records.push(row);}const known=new Set(options.map(o=>o.key));if(row.columns)row.columns=row.columns.filter(c=>!known.has(typeof c==='string'?c:c.key)||(selected[kind]??[]).includes(typeof c==='string'?c:c.key));row.optionalColumns=[...(row.optionalColumns??[]).filter(key=>!known.has(key)),...options.filter(field=>(selected[kind]??[]).includes(field.key)).map(field=>field.key)];}
  out.recordFields=Object.fromEntries(Object.entries(fieldsForDesign(out)).map(([kind,fields])=>[kind,fields.map(({pattern,...field})=>field)]));return out;
}
export function sourceRecordOptions(doc,records=[]){
  const selected={};
  for(const [kind,options]of Object.entries(recordFieldOptions)){
    const sheets=doc.sheets.filter(s=>kind==='samples'?/检测/.test(s.name):/批记录/.test(s.name)),labels=sheets.flatMap(s=>s.blocks.flatMap(b=>[...(Array.isArray(b.header)?b.header:[]).map(h=>h.label??h.key),...(Array.isArray(b.rows)?b.rows:[]).flatMap(r=>(r.cells??[]).filter(c=>c.t!==undefined).map(c=>c.t))]));
    selected[kind]=options.filter(field=>labels.some(label=>field.pattern?.test(String(label).replace(/\s/g,'')))).map(field=>field.key);
  }
  return setRecordOptions({records},selected).records;
}
