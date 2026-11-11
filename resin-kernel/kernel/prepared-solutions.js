// 预配溶液: a solute and a solvent made up beforehand and charged as one liquid.
// Two rows of one step, one set from the other through wt%:
// rule soluteWt (this row is the solute) or solventWt (this row is the solvent).
const rowsOf=steps=>(steps??[]).flatMap(step=>[...(step.rows??[]).map(row=>({step,row})),...(step.branches??[]).flatMap(branch=>rowsOf(branch.steps))]);
export function preparedSolutions(steps){
  const rows=rowsOf(steps),id=({step,row})=>row.chargeId??`${step.id}-${row.id}`;
  return rows.flatMap(item=>{
    const a=item.row.amount;if(a?.rule!=='soluteWt'&&a?.rule!=='solventWt')return [];
    const other=rows.find(x=>id(x)===a.of||x.row.id===a.of);if(!other)return [];
    const [solute,solvent]=a.rule==='soluteWt'?[item,other]:[other,item];
    return [{id:id(item),stepId:item.step.id,soluteId:id(solute),solventId:id(solvent),wtPct:a.knob?{knob:a.knob}:a.value,soluteFollows:a.rule==='soluteWt'}];
  });
}
// How much of the route's reactants a solute amounts to, e.g. 本路 MonB 的 5.2 mol%、MonA 的 7.4 mol%.
export function soluteDoseText(solute,charges,valueOf,routes=[]){
  const own=valueOf(solute.keys.mmol),route=routes.length>1?routes.find(r=>r.id===solute.branch):null,total=routes.reduce((sum,r)=>sum+Number(r.fraction??0),0);
  const share=route?(total>0?Number(route.fraction??0)/total:1/routes.length):1;
  if(typeof own!=='number')return '';
  const parts=charges.filter(c=>c.role==='reactant').flatMap(c=>{const mmol=valueOf(c.keys.mmol);return typeof mmol==='number'&&mmol>0?[`${c.name} 的 ${(own/(mmol*share)*100).toFixed(1)} mol%`]:[];});
  return parts.length?(route?'本路 ':'')+parts.join('、'):'';
}
