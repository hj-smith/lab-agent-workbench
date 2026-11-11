import {escapeTemplate} from './expr.js';
import {layoutDoc} from './layout.js';
import {evaluateDoc} from './evaluate.js';
/** Locate SOP semantics in both dedicated steps2 and older generic table blocks. */
export function extractSopSteps(doc,layout=layoutDoc(doc),values=evaluateDoc(doc)) {
  const result=[];
  for(const sheet of layout.sheets){
    const dedicated=sheet.blocks.filter(b=>b.type==='steps2');
    if(dedicated.length){for(const block of dedicated)for(const side of ['left','right'])for(const step of block[side].steps){const p=layout.byKey.get(`${block.id}/${step.id}/text`);result.push({...step,number:result.length+1,right:side==='right',sheet:sheet.name,sourceKey:p.key,context:p});}continue;}
    const text=p=>String(values.get(p.fullAddr)??''),b=sheet.cells.find(p=>/^B[.．、]/.test(text(p))),c=sheet.cells.find(p=>/^C[.．、]/.test(text(p))),d=sheet.cells.find(p=>/^D[.．、]/.test(text(p)));
    if(!b||!c)continue;
    for(const p of sheet.cells){const number=values.get(p.fullAddr);if(![1,8].includes(p.col)||p.row<=b.row||p.row>=(d?.row??Infinity)||!Number.isInteger(number)||number<=0||number>=100)continue;
      const right=p.col===8,title=sheet.grid.get(`${right?'I':'B'}${p.row}`),body=sheet.grid.get(`${right?'K':'D'}${p.row}`);if(!title||!body||!/[\p{Script=Han}]/u.test(text(title))||!text(body))continue;
      result.push({id:`step${number}`,number,title:text(title),text:body.cell.t??(body.cell.f?`{${body.cell.f}}`:escapeTemplate(body.cell.v??'')),right,sheet:sheet.name,sourceKey:body.key,context:body});
    }
  }
  const order=new Map(doc.sheets.map((s,i)=>[s.name,i])),used=new Set();
  for(const s of result){if(used.has(s.id))s.id=`${s.id}-${order.get(s.sheet)+1}`;used.add(s.id);}
  return result.sort((a,b)=>order.get(a.sheet)-order.get(b.sheet)||a.number-b.number);
}
