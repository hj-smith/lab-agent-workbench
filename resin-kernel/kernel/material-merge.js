import {normalizeName} from './materials.js';
import {supplementMaterialProperties} from './material-properties.js';

const copy=value=>structuredClone(value);
const list=value=>Array.isArray(value)?value:Array.isArray(value?.materials)?value.materials:Object.entries(value??{}).filter(([,entry])=>entry&&typeof entry==='object'&&!Array.isArray(entry)).map(([id,entry])=>({...entry,id:entry.id??id}));
const names=material=>[material.id,material.name,material.abbr,...material.aliases??[]].filter(value=>typeof value==='string'&&value.trim());
const missing=value=>value==null||value==='';
const formMissing=value=>missing(value)||value==='未指定';
const addAliases=(material,values)=>{material.aliases=[...new Set([...names(material),...values].filter(Boolean))];};

/** Merge the kernel library into the legacy material anchors.
 * Existing state ids and values take precedence because the legacy calculator
 * resolves charges by their literal materialId. Kernel ids become aliases of
 * the same row, so findMaterial continues to resolve pre-migration designs.
 * Purity is intentionally not inferred: the two callers historically use
 * different purity units, and enrichment must not change a recorded value.
 */
export function mergeMaterials(stateMaterials,library){
  const out=list(stateMaterials).map(copy);
  for(const material of out)addAliases(material,[]);
  for(const incoming of list(library)){
    const incomingNames=new Set(names(incoming).map(normalizeName));
    // Exact ids win over aliases. Do not arbitrarily collapse distinct old ids
    // when an ambiguous common name occurs in the incoming library.
    let found=out.find(material=>material.id===incoming.id);
    if(!found){
      const candidates=out.filter(material=>names(material).some(name=>incomingNames.has(normalizeName(name)))&&(
        formMissing(material.form)||formMissing(incoming.form)||normalizeName(material.form)===normalizeName(incoming.form)
      ));
      if(candidates.length===1)found=candidates[0];
    }
    if(!found){const next=copy(incoming);addAliases(next,names(incoming));out.push(next);continue;}
    for(const [field,value]of Object.entries(incoming)){
      if(['id','aliases','purity'].includes(field)||missing(value))continue;
      if(field==='form'?formMissing(found[field]):missing(found[field]))found[field]=copy(value);
    }
    addAliases(found,names(incoming));
  }
  return supplementMaterialProperties(out);
}
