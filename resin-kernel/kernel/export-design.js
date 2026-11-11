import {appFetch} from '../api-url.js';
/** Group export may start from a lightweight list model. Read the preserved
 * execution/evidence parts before compiling; never write a partial design. */
export async function fullExportDesign(design){
 if(!design._partialDesign&&!design._summaryOnly)return design;
 const response=await appFetch('/api/designs/'+encodeURIComponent(design.id));
 if(!response.ok)throw Error('读取完整设计未完成，请重新打开实验后导出');
 const {doc}=await response.json();
 if(doc.uid!==design.uid)throw Error('设计身份已变化，请重新打开对照组后导出');
 return doc;
}
