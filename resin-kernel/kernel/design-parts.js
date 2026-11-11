// The editable document is loaded without immutable execution snapshots and
// source ledgers. This marker is an explicit merge contract, never a new plan.
export const DESIGN_HEAVY_FIELDS=['workbookFacts','historicalLedger'];
const copy=value=>value===undefined?undefined:structuredClone(value);
export function designCore(design){
 const out=copy(design);out._partialDesign=true;
 for(const key of DESIGN_HEAVY_FIELDS)delete out[key];
 for(const key of ['frozenRevisions','executions'])if(Array.isArray(out[key]))out[key]=out[key].map(item=>Object.fromEntries(['uid','revision','revisionUid','code','aliases','exportedAt','importedAt'].filter(k=>Object.hasOwn(item,k)).map(k=>[k,copy(item[k])]).concat([['_lazy',true]])));
 return out;
}
export function designParts(design){
 return Object.fromEntries([...DESIGN_HEAVY_FIELDS,'frozenRevisions','executions'].filter(key=>Object.hasOwn(design,key)).map(key=>[key,copy(design[key])]));
}
/** Omitted parts are retained byte-for-byte as JSON values. Incoming execution
 * metadata may never erase actuals or a frozen plan. Full clients keep their
 * existing semantics; the revision guard still owns immutable-plan protection. */
export function mergeDesignParts(previous,next){
 const out=copy(next);delete out._summaryOnly;delete out._list;
 if(!next?._partialDesign){delete out._partialDesign;return out;}
 if(!previous||previous.uid!==next.uid)throw Error('设计主体与原记录不匹配，请重新打开实验。');
 for(const key of DESIGN_HEAVY_FIELDS)if(!Object.hasOwn(next,key)&&Object.hasOwn(previous,key))out[key]=copy(previous[key]);
 for(const key of ['frozenRevisions','executions']){
  if(!Object.hasOwn(next,key)){if(Object.hasOwn(previous,key))out[key]=copy(previous[key]);continue;}
  out[key]=(next[key]??[]).map(item=>{
   if(!item._lazy)return copy(item);
   const original=(previous[key]??[]).find(old=>old.uid===item.uid);
   if(!original)throw Error('执行记录版本已变化，请重新打开实验后重试。');
   return copy(original);
  });
  for(const old of previous[key]??[])if(!out[key].some(item=>item.uid===old.uid))out[key].push(copy(old));
 }
 delete out._partialDesign;return out;
}
