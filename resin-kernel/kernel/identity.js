import {bindSourceSchedule} from './schedule-bindings.js';
import {normalizeSamplingIdentity} from './sampling-identity.js';
// UUIDs are object identity; codes are editable human aliases. No I/O here.
export const newUid=()=>globalThis.crypto.randomUUID();
export function stableUid(namespace,key){
  // UUIDv8: four independently seeded 32-bit hashes, used only for generated children.
  const text=namespace+'\0'+key,words=[2166136261,2246822519,3266489917,668265263];
  for(let i=0;i<text.length;i++)for(let j=0;j<4;j++)words[j]=Math.imul(words[j]^(text.charCodeAt(i)+j*257),16777619+j*2);
  let hex=words.map(x=>(x>>>0).toString(16).padStart(8,'0')).join('');
  hex=hex.slice(0,12)+'8'+hex.slice(13,16)+(8+(parseInt(hex[16],16)&3)).toString(16)+hex.slice(17);
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
export const aliases=(existing,...codes)=>[...new Set([...(existing??[]),...codes].filter(Boolean))];
export function identifyDesign(design){
  const out=structuredClone(design);out.uid??=newUid();out.aliases=aliases(out.aliases,out.id,out.code);
  out.revision??=1;out.revisionUid??=stableUid(out.uid,'revision:'+out.revision);
  out.revisionCode??=`${out.code}@r${out.revision}`;out.revisionAliases=aliases(out.revisionAliases,out.revisionCode);
  out.batch??={uid:stableUid(out.uid,'batch:1'),code:out.code,aliases:[out.code]};
  out.question??='';out.hypothesis??='';out.conclusion??={verdict:'不确定',text:'',date:''};out.status??='设计中';out.roadmapNode??=null;out.attachments??=[];out.events??=[];
  if(out.schema==='lab-design/2'){normalizeSamplingIdentity(out);bindSourceSchedule(out,{migrate:true});}
  return out;
}
export function editRevision(design){
  const out=identifyDesign(design);
  // A structured experiment is edited as executed, in place (followStructuredEdits).
  if(out.userEditedFields?.structure)return out;
  if(out.frozenRevisions?.some(r=>r.uid===out.revisionUid)){
    out.revision=Math.max(out.revision,...out.frozenRevisions.map(r=>r.revision))+1;out.revisionUid=newUid();out.revisionCode=`${out.code}@r${out.revision}`;out.revisionAliases=[out.revisionCode];
  }
  return out;
}
export function renameDesign(design,code){
  const out=editRevision(design),old=out.code;out.code=out.id=code;out.aliases=aliases(out.aliases,old,code);
  out.title=out.title?.replaceAll(old,code);out.batch.aliases=aliases(out.batch.aliases,out.batch.code,code);out.batch.code=code;
  out.revisionCode=`${code}@r${out.revision}`;out.revisionAliases=aliases(out.revisionAliases,out.revisionCode);
  for(const v of out.vars??[])if(v.role==='code'){delete v.expr;v.value=code;}
  const renameNode=n=>{if(n.sampleId){n.aliases=aliases(n.aliases,n.sampleId);n.sampleId=n.sampleId.replace(old,code);}if(n.code)n.code=n.code.replace(old,code);};
  for(const n of [...out.samplingPlan?.nodes??[],...(out.samplingPlan?.points??[]).filter(x=>typeof x==='object')])renameNode(n);
  const walk=steps=>{for(const s of steps??[]){for(const n of s.samples??[])renameNode(n);for(const b of s.branches??[])walk(b.steps);}};walk(out.steps);
  return out;
}
export function freezeDesign(design){
  if(design._partialDesign)throw Error('执行记录尚未加载，请重新打开 SOP 与批记录页后导出。');
  const out=identifyDesign(design);out.frozenRevisions??=[];
  if(!out.frozenRevisions.some(r=>r.uid===out.revisionUid)){
    const plan=structuredClone(out);delete plan.frozenRevisions;delete plan.executions;delete plan.actual;delete plan.actualRevisionUid;delete plan.studio;
    // Source ledgers describe the source document, never a mutable execution
    // plan. Existing snapshots stay unchanged; new snapshots avoid duplicating
    // their hundreds of kilobytes in every frozen revision.
    delete plan.workbookFacts;delete plan.historicalLedger;
    out.frozenRevisions.push({uid:out.revisionUid,revision:out.revision,code:out.revisionCode,aliases:out.revisionAliases,plan});
  }
  out.executions??=[];
  if(!out.executions.some(e=>e.revisionUid===out.revisionUid)){
    const uid=out.executions.length?newUid():out.batch.uid;
    const ownActual=!out.actualRevisionUid||out.actualRevisionUid===out.revisionUid;
    out.executions.push({uid,code:out.code,aliases:[out.code],revision:out.revision,revisionUid:out.revisionUid,actual:ownActual?structuredClone(out.actual??{}):{},...ownActual&&out.actualFlags?{actualFlags:structuredClone(out.actualFlags)}:{},...ownActual&&out.importedRows?{importedRows:structuredClone(out.importedRows)}:{}});
  }
  out.status='执行中';return out;
}
/** Resolve records against the exact exported plan, even after later design edits. */
export function executionDesign(design,revisionUid=design.actualRevisionUid??design.revisionUid){
  const frozen=design.frozenRevisions?.find(r=>r.uid===revisionUid);
  const execution=design.executions?.find(e=>e.revisionUid===revisionUid);
  // The editable core carries execution identities, not their immutable plan.
  // Design-only capacity/schedule checks use the current editable plan. Exact
  // execution/SOP/record views explicitly load the parts before resolving one.
  if(frozen&&!frozen.plan)return {...structuredClone(design),executionUid:execution?.uid??design.batch?.uid};
  return frozen?{...structuredClone(frozen.plan),executionUid:execution?.uid??frozen.plan.batch?.uid,actual:structuredClone(execution?.actual??design.actual??{}),actualFlags:structuredClone(execution?.actualFlags??design.actualFlags??{}),importedRows:structuredClone(execution?.importedRows??design.importedRows??[])}:structuredClone(design);
}
// The executed copy of a structured experiment is the experiment itself: it is
// replaced by the current plan and records on every save (same revision id, so
// change history and platform links keep resolving).
export function executedSnapshot(design){
  const plan=structuredClone(design);
  for(const key of ['frozenRevisions','executions','actual','actualRevisionUid','studio','workbookFacts','historicalLedger'])delete plan[key];
  return plan;
}
// The old archive's notes, record sheets, equipment and source maps are not part
// of a structured experiment (「不要有历史包袱」). Branch results stay: an A/B
// batch's two resin lots are still computed from them.
const ARCHIVE_FIELDS=['historicalNotes','historicalRecords','historicalSummary','historicalLedger','historicalEquipment','historicalFormulations','historicalCharges','historicalYieldChecks','historicalObjective','historicalRepair','sourceMap','sourcePlanBaseline','recordConflicts','workbookFacts'];
export function followStructuredEdits(design){
  const out=structuredClone(design);
  for(const key of ARCHIVE_FIELDS)delete out[key];
  // An edit made before editRevision knew this started a draft; the experiment is its executed revision again.
  const executed=(out.frozenRevisions??[]).at(-1);
  if(executed&&!out.frozenRevisions.some(r=>r.uid===out.revisionUid)){out.revision=executed.revision;out.revisionUid=executed.uid;out.revisionCode=`${out.code}@r${executed.revision}`;out.revisionAliases=[out.revisionCode];}
  const uid=out.revisionUid;
  out.frozenRevisions=(out.frozenRevisions??[]).map(r=>r.uid===uid?{...r,plan:executedSnapshot(out)}:r);
  out.executions=(out.executions??[]).map(e=>e.revisionUid===uid?{...e,actual:structuredClone(out.actual??{}),actualFlags:structuredClone(out.actualFlags??{}),importedRows:structuredClone(out.importedRows??[])}:e);
  out.actualRevisionUid=uid;
  return out;
}

/** Persistence boundary: old frozen plans cannot be overwritten by a stale editor. */
export function protectDesignRevisions(previous,next){
  if(next.userEditedFields?.structure&&next.frozenRevisions?.length)return identifyDesign(followStructuredEdits(next));
  if(!previous?.frozenRevisions?.length)return identifyDesign(next);

  const out=structuredClone(next),known=new Set(previous.frozenRevisions.map(r=>r.uid));
  const added=(next.frozenRevisions??[]).filter(r=>!known.has(r.uid));
  out.frozenRevisions=[...structuredClone(previous.frozenRevisions),...structuredClone(added)];
  // Execution UID and revision binding survive stale saves and editor omissions.
  const incoming=next.executions??[];
  out.executions=[...(previous.executions??[]).map(old=>{const candidate=incoming.find(e=>e.revisionUid===old.revisionUid);return {...structuredClone(candidate??old),uid:old.uid,revision:old.revision,revisionUid:old.revisionUid,...old.library?{library:structuredClone(old.library)}:{}};}),...incoming.filter(e=>!(previous.executions??[]).some(old=>old.revisionUid===e.revisionUid))];
  const plan=d=>{const copy=structuredClone(d);for(const key of ['actual','actualFlags','actualRevisionUid','importedRows','executions','frozenRevisions','status','studio'])delete copy[key];return JSON.stringify(copy);};
  return out.revisionUid===previous.revisionUid&&plan(previous)!==plan(out)?editRevision(out):identifyDesign(out);
}
