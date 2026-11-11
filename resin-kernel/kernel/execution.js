import {freezeDesign,executionDesign} from './identity.js';
import {compile} from './compile.js';
import {writeWorkbook} from './xlsx-write.js';
/** Persist returned design atomically before handing bytes to an execution workflow. */
export async function exportForExecution(design,library={}){
  const frozen=freezeDesign(design),execution=frozen.executions.find(e=>e.revisionUid===frozen.revisionUid);execution.library??=structuredClone(library);
  const doc=compile(executionDesign(frozen,frozen.revisionUid),execution.library);
  return {design:frozen,doc,bytes:await writeWorkbook(doc)};
}
