import {extractAmounts,extractDurations} from './durations.js';
import {paramBinding} from './param-bindings.js';
const separator='\n---CONTROL---\n';
function extract(target){
 const used=new Set((target.params??[]).concat(target.amountParams??[],target.durationParams??[]).map(p=>p.key)),args={};
 const token=p=>{let key=p.key,i=2;while(used.has(key))key=p.key+'_'+i++;p.key=key;used.add(key);args[key]=p.default;return `{{${key}|${p.duration?'0':'0.0'}}}`;};
 const amounts=extractAmounts([target.text??'',...target.controls??[]].join(separator),token),timed=extractDurations(amounts.text,token),parts=timed.text.split(separator);
 return {text:parts[0],controls:parts.slice(1),amounts:amounts.params,durations:timed.params,args};
}
export function upgradeModuleParams(module){const next=extract(module);if(!next.amounts.length&&!next.durations.length)return module;const out=structuredClone(module);out.sourceText??=module.text;out.text=next.text;out.controls=next.controls;out.params=[...out.params,...next.amounts,...next.durations];const primary=next.durations.find(p=>/打浆时长|续搅时长|干燥时长/.test(p.label));if(primary&&!out.conditions?.hours)out.conditions={...out.conditions,kind:out.conditions?.kind??'hold',hours:`{{${primary.key}}}${primary.unit==='min'?'/60':''}`};return out;}
// Draft upgrades are visible and undoable in the UI. Frozen revisions and their
// library snapshots are left intact; editing a frozen plan first creates a draft.
export function upgradeDraftParams(design,library){
 if(design.schema!=='lab-design/2'||design.frozenRevisions?.some(r=>r.uid===design.revisionUid))return design;
 const out=structuredClone(design);let count=0,changed=!design.paramBindingVersion;out.paramBindingVersion=2;
 const walk=steps=>{for(const step of steps){if(step.text!==undefined){const next=extract(step);if(next.amounts.length||next.durations.length){step.sourceText??=step.text;step.text=next.text;step.controls=next.controls;step.amountParams=[...step.amountParams??[],...next.amounts];step.durationParams=[...step.durationParams??[],...next.durations];step.args={...step.args,...next.args};count+=next.amounts.length+next.durations.length;changed=true;const primary=next.durations.find(p=>/打浆时长|续搅时长|干燥时长/.test(p.label));if(primary){if(typeof step.conditions?.hours==='number'&&step.conditions.hours>0)step.args[primary.key]=step.conditions.hours*(primary.unit==='min'?60:1);step.conditions={...step.conditions,kind:step.conditions?.kind??'hold',hours:`{{${primary.key}}}${primary.unit==='min'?'/60':''}`};}}}
   if(!step.paramBindings){const module=library.modules.find(m=>m.id===step.module),bindings=Object.fromEntries((module?.params??[]).flatMap(p=>{const bound=paramBinding(out,p,step);return bound?[[p.key,bound.key]]:[];}));if(Object.keys(bindings).length){step.paramBindings=bindings;changed=true;}}for(const branch of step.branches??[])walk(branch.steps);}};walk(out.steps);
 if(!changed)return design;out.parameterUpgrade=count?`已升级旧版步骤参数：${count} 个正文用量/时长转为独立参数；原文已保留，步骤绑定按本步实例对应。`:'已按步骤实例升级旧参数绑定；原文已保留。';return out;
}
