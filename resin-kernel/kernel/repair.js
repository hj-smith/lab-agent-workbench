import {flattenSteps} from './design2.js';
/** Quarantine obsolete references, retaining original data for audit. Idempotent. */
export function repairStepReferences(design){
 if(design.schema!=='lab-design/2')return design;
 const rawSteps=list=>(list??[]).flatMap(s=>[s,...(s.branches??[]).flatMap(b=>rawSteps(b.steps))]);
 const out=structuredClone(design),ids=new Set([...flattenSteps(out.steps),...Object.values(out.historicalBranches??{}).flatMap(b=>flattenSteps(b.steps??[]))].map(s=>s.id)),removed=[];
 // Historical summary grids also have semantic operation IDs (not process steps).
 for(const key of Object.keys(out.actual??{})){const [block,id]=key.split('/');if(block==='record-operations'&&id?.startsWith('history-'))ids.add(id);}
 for(const node of out.samplingPlan?.nodes??[])if(node.kind==='product'&&node.stepId==='archive-product-result'&&node.source?.sampleId)ids.add(node.stepId);
 const filter=(list,path)=>(list??[]).filter(item=>{if(item?.stepId&&!ids.has(item.stepId)&&!item.stepId.startsWith('history-')){removed.push({path,value:item});return false;}return true;});
 if(out.samplingPlan){out.samplingPlan.nodes=filter(out.samplingPlan.nodes,'samplingPlan.nodes');out.samplingPlan.points=filter(out.samplingPlan.points,'samplingPlan.points');}
 for(const field of ['events','records'])if(out[field])out[field]=filter(out[field],field);
 for(const step of rawSteps(out.steps))for(const key of ['afterId','beforeId','sourceStepId'])if(step[key]&&!ids.has(step[key])){removed.push({path:'steps/'+step.id+'/'+key,value:step[key]});delete step[key];}
 const badKeys=new Set(out.vars.filter(v=>/^s-[\da-f-]+~/.test(v.key)&&!ids.has(v.key.split('~')[0])).map(v=>v.key));
 const broken=value=>typeof value==='string'&&[...value.matchAll(/\[([^\]]+)\]/g)].some(m=>badKeys.has(m[1])||/^s-[\da-f-]+~/.test(m[1])&&!ids.has(m[1].split('~')[0]));
 let changed=true;while(changed){changed=false;for(const v of out.vars)if(!badKeys.has(v.key)&&broken(v.expr)){badKeys.add(v.key);changed=true;}}
 out.vars=out.vars.filter(v=>{if(!badKeys.has(v.key))return true;removed.push({path:'vars',value:v});return false;});
 for(const step of rawSteps(out.steps)){
  for(const key of Object.keys(step.args??{}))if(broken(step.args[key])){removed.push({path:'steps/'+step.id+'/args/'+key,value:step.args[key]});delete step.args[key];step.paramOverrides=(step.paramOverrides??[]).filter(k=>k!==key);}
  for(const key of Object.keys(step.conditions??{}))if(broken(step.conditions[key])){removed.push({path:'steps/'+step.id+'/conditions/'+key,value:step.conditions[key]});delete step.conditions[key];}
  if(broken(step.text)){removed.push({path:'steps/'+step.id+'/text',value:step.text});step.text=step.text.replace(/\{[^{}]*\}/g,token=>broken(token)?'待指定':token);}
 }
 for(const c of [...out.charges,...rawSteps(out.steps).flatMap(s=>s.charges??[])])if(broken(c.amount?.value)){removed.push({path:'charges/'+c.id+'/amount',value:c.amount});c.amount.value='待指定';}
 if(!removed.length)return design;
 out.repairArchive=[...out.repairArchive??[],...removed];out.notes=[...Array.isArray(out.notes)?out.notes:out.notes?[out.notes]:[],`数据修复：已移除 ${removed.length} 处指向已删除步骤的悬空引用，原值保留在修复档案中。`];return out;
}
