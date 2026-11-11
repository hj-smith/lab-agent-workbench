import {layoutDoc} from './layout.js';
import {evaluateDoc} from './evaluate.js';
/** Semantic anchors survive row insertion, column moves, and loss of _meta. */
export function recordLayout(doc){
  const layout=layoutDoc(doc),values=evaluateDoc(doc),tables=[];
  for(const sheet of doc.sheets)for(const block of sheet.blocks.filter(b=>b.type==='table'&&b.header)){
    if(!block.rows.some(r=>r.cells.some(c=>c.input)))continue;
    const columns=block.header.map(h=>{const p=layout.byKey.get(`${block.id}/header/${h.key}`);return {key:h.key,label:h.label,col:p.col,cell:p.addr,unit:h.unit};});
    const rows=block.rows.map(r=>{
      const cells=[...layout.byKey.values()].filter(p=>p.block===block.id&&p.key.split('/')[1]===r.id);
      const anchors=cells.filter(p=>!p.cell.input&&values.get(p.fullAddr)!==null&&values.get(p.fullAddr)!=='').map(p=>({key:p.key.split('/')[2],value:values.get(p.fullAddr),col:p.col}));
      const inputs=cells.filter(p=>p.cell.input).map(p=>({key:p.key,colKey:p.key.split('/')[2],cell:p.addr,col:p.col,type:p.cell.type??'text',unit:p.cell.unit??'',label:p.label,min:p.cell.min,max:p.cell.max}));
      const preferred=anchors.find(a=>/物料|项目|事件|步骤|样品编号|编号/.test(a.key))??anchors[0];return {id:r.id,label:preferred?.value??r.id,anchors,inputs};
    });
    tables.push({id:block.id,sheet:sheet.name,title:block.title,headerRow:layout.byKey.get(`${block.id}/header/${block.header[0].key}`).row,columns,rows});
  }
  return {layout,values,tables};
}
