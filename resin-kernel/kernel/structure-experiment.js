import {executionDesign,followStructuredEdits} from './identity.js';
import {procedureFromDesign,compileProcedure} from './procedure.js';
import {tidyProcedure} from './tidy-procedure.js';

// 整理成结构化实验 (user 2026-10-29: 「实验就是实验，不是历史资料」): the plan that was
// actually executed becomes the experiment's own editable design, edited on the
// same page as R100-39. Step, sample and batch identities stay, so recorded
// values, GPC results and resin lots stay attached. The converted archive
// (原档案 record blocks, notes, Excel wording) is not carried into it.
const ARCHIVED=/^(history-|workbook-evidence-)/;

export function structureExperiment(design,library={}){
  const executed=executionDesign(design);
  const base=compileProcedure({...executed,procedure:procedureFromDesign(executed,library)},library);
  const out=compileProcedure(tidyProcedure(base),library);
  out.actual=Object.fromEntries(Object.entries(out.actual??{}).filter(([key])=>!ARCHIVED.test(key)));
  // Values the old import put where no box shows them (originals stay): notes saved
  // under 现象/备注 belong in the 现象 box; a one-route batch's product-result
  // 干品质量 belongs to its last drying step.
  const dry=[...out.procedure.steps].reverse().find(s=>s.type==='dry'||/干燥|恒重/.test(s.title??''));
  const products=Object.keys(out.actual).filter(key=>/^record-operations\/history-product-result-[^/]+\/干品质量$/.test(key));
  for(const [key,value] of Object.entries(out.actual)){
    const note=/^(record-(?:timeline|operations)\/[^/]+)\/现象\/备注$/.exec(key);
    if(note&&out.actual[note[1]+'/现象']==null)out.actual[note[1]+'/现象']=value;
  }
  if(dry&&products.length===1&&out.actual[`record-operations/${dry.id}/干品质量`]==null)out.actual[`record-operations/${dry.id}/干品质量`]=out.actual[products[0]];
  out.records=(out.records??[]).filter(record=>!ARCHIVED.test(record.id??''));
  const clean=steps=>{for(const step of steps??[]){delete step.legacySourceText;step.legacyControls=[];step.legacyNotes=[];for(const branch of step.branches??[])clean(branch.steps);}};
  clean(out.procedure.steps);
  Object.assign(out,{authoring:'procedure',notes:[],archiveNotes:[],status:'完成',userEditedFields:{...out.userEditedFields,structure:true},
    revisionUid:design.actualRevisionUid??design.revisionUid,frozenRevisions:structuredClone(design.frozenRevisions??[]),executions:structuredClone(design.executions??[])});
  delete out.purposeTemplate;delete out.conversion;
  return followStructuredEdits(out);
}
