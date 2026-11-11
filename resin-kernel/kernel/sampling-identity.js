/** Persist legacy numeric declarations before editing time parameters. Endpoint
 * aliases are fixed at normalization, never reselected from the evaluated time.
 * Existing explicit ids/uids/lotUids and source record-key components win. */
export function normalizeSamplingIdentity(design,{legacyAliases=false}={}) {
  const plan=design.samplingPlan??={points:[],nodes:[]},first=plan.identityVersion!==1;
  const used=new Set([...(plan.points??[]),...(plan.nodes??[])].filter(p=>p&&typeof p==='object'&&p.id).map(p=>p.id));
  const existing=[...new Set(Object.keys(design.actual??{}).map(key=>/^record-(?:samples|timeline)\/([^/]+)\//.exec(key)?.[1]).filter(Boolean))];
  plan.points=(plan.points??[]).map(point=>{
    const node=typeof point==='number'?{hours:point,label:`${point} h`}:{...point};
    if(!node.id){
      const known=existing.filter(id=>/^t-\d+(?:_\d+)?$/.test(id)&&Number(id.slice(2).replace('_','.'))===node.hours);
      const base=known.length===1?known[0]:`t-${String(node.hours).replace('.','_')}`;
      let id=base,index=2;while(used.has(id))id=base+'-'+index++;
      node.id=id;used.add(id);
    }
    return node;
  });
  if(first||legacyAliases)for(const point of plan.points){
    if(point.sameSampleAs)continue;
    // Preserve the previous compiler's alias between design declarations once;
    // this never assigns a historical sample to a node by its time.
    const candidates=(plan.nodes??[]).filter(node=>node.id&&node.hours!=null&&node.hours===point.hours&&(node.branch??'')===(point.branch??''));
    if(candidates.length===1)point.sameSampleAs=candidates[0].id;
  }
  plan.identityVersion=1;return design;
}
