import {parseExpr,evaluateExpr} from './expr.js';
/** Capacity variables are projections of their vessel, never independent
 * numeric settings. All variable editing paths share this invariant. */
export function syncVesselCapacity(design,variable){
 if(!variable?.vesselId)return design;
 const vessel=design.vessels?.find(vessel=>vessel.id===variable.vesselId);if(!vessel)throw Error('此容量参数对应的容器不存在，请在容器与装置中核对');
 const active=new Set(),get=key=>{if(active.has(key))throw Error('容器容量公式存在循环引用');const source=design.vars.find(item=>item.key===key);if(!source)throw Error('容器容量公式引用的参数不存在：'+key);active.add(key);const value=source.expr===undefined?source.value:evaluateExpr(parseExpr(source.expr),reference=>get(reference.key));active.delete(key);return value;};
 let value;try{value=get(variable.key);}catch(error){if(/^(容器容量|此容量)/.test(error.message))throw error;throw Error('容器容量公式无效，请在容器与装置中核对');}
 const unrecorded=variable.expr===undefined&&(value===null||value==='');if(unrecorded)value=null;
 if(!unrecorded&&(typeof value!=='number'||!Number.isFinite(value)||value<=0))throw Error('容器标称容量必须是大于 0 的数值，单位为 mL');
 if(variable.unit!=='mL')throw Error('容器容量单位须为 mL，请在容器与装置中修改');
 vessel.capacityMl=value;if(design.reactionVesselId===vessel.id)design.flaskMl=value;
 const walk=steps=>{for(const step of steps??[]){if(step.vesselId===vessel.id)step.vesselMl=value;for(const branch of step.branches??[])walk(branch.steps);}};walk(design.steps);return design;
}
export function syncVesselCapacities(design){for(const variable of design.vars??[])if(variable.vesselId)syncVesselCapacity(design,variable);return design;}
