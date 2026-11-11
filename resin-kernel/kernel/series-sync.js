import {flattenSteps} from './design2.js';
import {editRevision} from './identity.js';
import {executedMember} from './series.js';
import {paramBinding,paramLabel} from './param-bindings.js';
import {resolveCondition} from './design2.js';
import {chinese} from './language.js';
const clone=x=>structuredClone(x),same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const privateFields=new Set(['id','uid','code','title','question','hypothesis','revision','revisionUid','revisionCode','revisionAliases','batch','series','parentDesign','parentCode','parentUid','actual','actualFlags','executions','frozenRevisions','_updated']);
const changed=(a,b)=>[...new Set([...Object.keys(a??{}),...Object.keys(b??{})])].filter(key=>!same(a?.[key],b?.[key]));
function patch(target,before,after){for(const key of changed(before,after)){
 const a=before?.[key],b=after?.[key],identity=x=>x?.id??x?.key;
 if(b===undefined)delete target[key];
 else if(Array.isArray(a)&&Array.isArray(b)&&[...a,...b].every(x=>x&&typeof x==='object'&&identity(x)!=null)){
  const list=target[key]??=[];for(const old of a)if(!b.some(x=>identity(x)===identity(old))){const i=list.findIndex(x=>identity(x)===identity(old));if(i>=0)list.splice(i,1);}
  for(const item of b){const old=a.find(x=>identity(x)===identity(item)),found=list.find(x=>identity(x)===identity(item));if(!old){if(!found)list.push(clone(item));}else if(found)patch(found,old,item);}
 }else if(a&&b&&typeof b==='object'&&!Array.isArray(b)){target[key]??={};patch(target[key],a,b);}
 else target[key]=clone(b);
}}
/** Replay a semantic edit, never a closure over DOM elements or another member's IDs. */
export function syncSeriesEdit(members,before,after,library={}){
 const synced=[],skipped=[],overrides=[];
 const oldSteps=flattenSteps(before.steps),newSteps=flattenSteps(after.steps),affected=oldSteps.filter(s=>!same(s,newSteps.find(t=>t.id===s.id))),added=newSteps.filter(s=>!oldSteps.some(t=>t.id===s.id));
 const output=members.map(member=>{
  if(member.id===after.id||member.series?.id!==after.series?.id)return clone(member);
  if(executedMember(member)){skipped.push({code:member.code,reason:'已执行'});return clone(member);}
  const targets=flattenSteps(member.steps),missing=affected.filter(s=>!targets.some(t=>t.id===s.id));
  if(missing.length){skipped.push({code:member.code,reason:'无此步骤'});return clone(member);}
  const next=editRevision(member),factorKeys=new Set((member.series?.factors??[]).filter(f=>['var','variable'].includes(f.target?.kind)).map(f=>f.target.key));
  for(const key of changed(before,after).filter(key=>!privateFields.has(key)&&!['steps','vars'].includes(key)))patch(next,{[key]:before[key]},{[key]:after[key]});
  for(const v of after.vars){if(v.role==='code'||factorKeys.has(v.key)||same(v,before.vars.find(x=>x.key===v.key)))continue;const at=next.vars.findIndex(x=>x.key===v.key);if(at<0)next.vars.push(clone(v));else patch(next.vars[at],before.vars.find(x=>x.key===v.key),v);}
  const findContainer=(list,id)=>{for(const s of list){if(s.id===id)return list;for(const b of s.branches??[]){const found=findContainer(b.steps,id);if(found)return found;}}};
  for(const old of affected){const now=newSteps.find(s=>s.id===old.id),list=findContainer(next.steps,old.id),target=list.find(s=>s.id===old.id);if(!now){list.splice(list.indexOf(target),1);continue;}
   const local=new Set((target.paramOverrides??[]).filter(key=>!target.syncedParams?.includes(key))),blocked=changed(old.args,now.args).filter(key=>local.has(key));
   const a=clone(old),b=clone(now);for(const key of ['title','paramOverrides','syncedParams','branches']){delete a[key];delete b[key];}for(const key of blocked){delete a.args[key];delete b.args[key];const p=library.modules?.find(m=>m.id===target.module)?.params?.find(p=>p.key===key);overrides.push(member.code+' 保留本步单独设定 '+target.args[key]+(p?.unit==='v/v'?' 倍':p?.unit?' '+p.unit:'')+'，未改');}
   patch(target,a,b);
   const keys=changed(old.args,now.args).filter(key=>!local.has(key));if(keys.length){target.paramOverrides=[...new Set([...target.paramOverrides??[],...keys])];target.syncedParams=[...new Set([...target.syncedParams??[],...keys])];}
  }
  for(const step of added){if(flattenSteps(next.steps).some(s=>s.id===step.id))continue;const index=newSteps.indexOf(step),anchor=newSteps.slice(0,index).findLast(s=>flattenSteps(next.steps).some(t=>t.id===s.id));const list=anchor?findContainer(next.steps,anchor.id):next.steps;list.splice(anchor?list.findIndex(s=>s.id===anchor.id)+1:0,0,clone(step));}
  // Reordering compares shared steps only: an insertion is not a move.
  const common=oldSteps.filter(s=>newSteps.some(t=>t.id===s.id)).map(s=>s.id),order=newSteps.filter(s=>common.includes(s.id)).map(s=>s.id);
  if(!same(common,order)&&!next.steps.some(s=>s.branches?.length)){const ranks=new Map(newSteps.map((s,i)=>[s.id,i]));next.steps.sort((a,b)=>(ranks.get(a.id)??Infinity)-(ranks.get(b.id)??Infinity));}
  const changedVars=after.vars.filter(v=>!same(v,before.vars.find(x=>x.key===v.key))).map(v=>v.key);
  for(const step of flattenSteps(member.steps))for(const key of step.paramOverrides??[]){if(step.syncedParams?.includes(key))continue;const p=library.modules?.find(m=>m.id===step.module)?.params?.find(p=>p.key===key),bound=p?paramBinding(member,p,step):null;if(!bound||!changedVars.includes(bound.key))continue;let value=step.args[key];try{value=resolveCondition(value,step.args,member.vars);}catch{}overrides.push(member.code+' 保留本步单独设定 '+value+(p.unit==='v/v'?' 倍':p.unit?' '+p.unit:'')+'，未改');}
  const notices=[...new Set(overrides.filter(text=>text.startsWith(member.code+' ')))];if(notices.length){next.syncNotices=notices;}
  synced.push(member.code);return next;
 });
 synced.sort((a,b)=>a.localeCompare(b));skipped.sort((a,b)=>a.code.localeCompare(b.code));overrides.sort();
 const message=[synced.length?'已同步 '+synced.join('、'):'没有可同步的成员',...skipped.map(s=>s.code+' '+s.reason+'，已跳过'),...new Set(overrides)].join('；');
 return {members:output,synced,skipped,overrides,message};
}
export function canSyncEdit(before,after){return !!after.series&&changed(before,after).some(key=>!privateFields.has(key)&&(key!=='steps'||!same(flattenSteps(before.steps).map(({title,...s})=>s),flattenSteps(after.steps).map(({title,...s})=>s))));}
