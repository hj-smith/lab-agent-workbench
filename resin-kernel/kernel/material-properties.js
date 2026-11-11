// User-supplied, source-backed research from the integration review (2026-10-17).
// Existing scientific values win; unknown properties remain unknown.
export const materialProperties = [];

const missing=value=>value==null||value==='';
export function supplementMaterialProperties(materials) {
  return materials.map(material=>{
    const evidence=materialProperties.find(item=>item.id===material.id||material.aliases?.includes(item.id));
    if(!evidence)return structuredClone(material);
    const next=structuredClone(material);
    // The supplied TsOH evidence describes the anhydrous material. Never attach
    // its CAS or thermal data to an explicitly hydrated library entry.
    if(evidence.id==='TsOH'&&(next.form==='水合物'||/水合/.test(next.name??'')))return next;
    for(const field of ['cas','formula','mw','density','bp','mp']) {
      if(field==='mw'&&evidence.id==='TsOH')continue;
      const value=evidence[field];
      if(missing(value))continue;
      const added=missing(next[field]);
      if(added)next[field]=value;
      // Sources and density temperature only describe the same published value.
      if(added||next[field]===value) {
        const url=evidence.sources[field];
        if(url&&missing(next[field+'Source']))next[field+'Source']=url;
        if(field==='density'&&missing(next.densityTempC)&&evidence.densityTempC!=null)next.densityTempC=evidence.densityTempC;
      }
    }
    if(missing(next.propertyNotes))next.propertyNotes=evidence.notes;
    if(evidence.id==='BaseQ'&&/^BaseQ[（(]源SOP标25%溶液[）)]$/.test(next.name)) {
      next.aliases=[...new Set([...(next.aliases??[]),next.name,'BaseQ'])];
      next.name='BaseQ';
      if(missing(next.form)||next.form==='未指定')next.form='溶液';
      if(missing(next.wt))next.wt=next.concentrationPct??25;
      if(missing(next.concentrationPct))next.concentrationPct=next.wt;
    }
    // The laboratory explicitly confirmed this concentration and aqueous form.
    // This is identity/context metadata, not an inferred density or hydrate.
    if(evidence.id==='BaseQ') {
      next.name='BaseQ';next.form='溶液';next.wt=25;next.concentrationPct=25;
      next.solvent='水';next.solventSource='用户确认：25 wt% 水溶液（2026-10-17）';
    }
    return next;
  });
}
