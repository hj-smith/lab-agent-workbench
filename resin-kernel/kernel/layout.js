import {address, colName} from './xml.js';
export function layoutDoc(doc) {
  const byKey=new Map(), byAddr=new Map(), varToAddr=new Map(), inputs=[], sheets=[];
  for(const sheet of doc.sheets) {
    let row=1, bottom=0; const cells=[], merges=[], rowBreaks=[], grid=new Map(), covered=new Map(), coveredRows=new Set(), width=sheet.widths.length;
    function place(cell, block, rowId, colKey, col, r, style='body', label='') {
      const addr=colName(col)+r, key=`${block.id}/${rowId}/${colKey}`, fullAddr=`${sheet.name}!${addr}`;
      if(byKey.has(key) || grid.has(addr) || covered.has(addr)) throw Error(`布局单元格重复：${key}`);
      const p={cell,key,addr,fullAddr,sheet:sheet.name,block:block.id,row:r,col,style:cell.style??style,label};
      cells.push(p); grid.set(addr,p); byKey.set(key,p); byAddr.set(fullAddr,p);
      if(cell.var && (!cell.show || cell.show==='value')) {
        if(varToAddr.has(cell.var)) throw Error(`变量有多个家：${cell.var}`);
        varToAddr.set(cell.var,fullAddr);
      }
      if(cell.input) inputs.push({sheet:sheet.name,cell:addr,key,type:cell.type??'text',label:label||key});
      const span=cell.span??1, rows=cell.rows??1;
      if(span>1 || rows>1) merges.push(`${addr}:${colName(col+span-1)}${r+rows-1}`);
      if(col+span-1>width) throw Error(`单元格超出列宽：${fullAddr}`);
      // Covered cells of a merge belong to its top-left placement; later rows skip them.
      for(let y=r;y<r+rows;y++) for(let x=col;x<col+span;x++) if(y>r || x>col) {
        const at=colName(x)+y; if(grid.has(at) || covered.has(at)) throw Error(`合并区域重叠：${fullAddr}`);
        covered.set(at,p); if(y>r) coveredRows.add(y);
      }
      bottom=Math.max(bottom,r+rows-1);
    }
    // Columns of the current row covered by a merge from a row above are skipped.
    const free=(c,r)=>{ while(covered.has(colName(c)+r)) c++; return c; };
    for(const block of sheet.blocks) {
      if(bottom>=row) throw Error(`纵向合并超出所在块：${sheet.name}!${block.id}`);
      row+=block.gapBefore??0;if(block.pageBreakBefore&&row>1)rowBreaks.push(row-1);
      if(['title','section','note'].includes(block.type)) {
        const rows=block.rows??1;
        place({t:block.text,span:width,...(rows>1?{rows}:{})},block,'text','A',1,row,block.type); row+=rows; continue;
      }
      if(block.type==='steps2') {
        const half=width/2;
        place({t:block.left.title,span:half},block,'header','left',1,row,'section');
        place({t:block.right.title,span:half},block,'header','right',half+1,row++,'section');
        const count=Math.max(block.left.steps.length,block.right.steps.length);
        for(let i=0;i<count;i++,row++) { for(const [side,start,offset] of [['left',1,0],['right',half+1,block.left.steps.length]]) {
          const step=block[side].steps[i], id=step?.id??`${side}_empty${i+1}`;
          place(step?{v:offset+i+1}:{},block,id,'number',start,row,'stepNumber');
          place({t:step?.title??'',span:2},block,id,'title',start+1,row,'stepTitle');
          place({t:step?.text??'',span:4},block,id,'text',start+3,row,'body');
        }
          const details=['left','right'].some(side=>block[side].steps[i]?.controls?.length||block[side].steps[i]?.recordHint);
          if(details){row++;for(const [side,start]of [['left',1],['right',half+1]]){const step=block[side].steps[i],text=[...(step?.controls??[]),step?.recordHint?'→记录：'+step.recordHint:''].filter(Boolean).join('；');place({t:text,span:half},block,(step?.id??side+'_empty'+i)+'-detail','detail',start,row,'smallPrint');}}
        }
        continue;
      }
      if(block.type!=='table') throw Error(`未知块类型：${block.type}`);
      if(block.title) place({t:block.title,span:width},block,'title','A',1,row++,'section');
      const keys=new Map(), labels=new Map();
      if(block.header) {
        let c=1;
        for(const h of block.header) { c=free(c,row); place({t:h.label,span:h.span??1,...(h.rows>1?{rows:h.rows}:{})},block,'header',h.key,c,row,'header'); keys.set(c,h.key); labels.set(c,h.label); c+=h.span??1; }
        row++;
      }
      for(const r of block.rows) {
        let c=1;
        const rowLabel=r.cells[0]?.t??r.cells[0]?.v??r.id;
        for(const cell of r.cells) {
          c=free(c,row);
          const key=keys.get(c)??colName(c), label=`${rowLabel} / ${labels.get(c)??key}`;
          place(cell,block,r.id,cell.key??key,c,row,cell.input?'input':cell.f?'calculated':c===1?'label':'body',label); c+=cell.span??1;
        }
        row++;
      }
    }
    if(bottom>=row) throw Error(`纵向合并超出所在块：${sheet.name}`);
    sheets.push({...sheet,cells,grid,covered,coveredRows,merges,rowBreaks,maxRow:row-1});
  }
  return {sheets,byKey,byAddr,varToAddr,inputs};
}
export function resolvePlaced(layout, ref, context) {
  if(ref.kind==='var') { const addr=layout.varToAddr.get(ref.key); if(!addr) throw Error(`变量没有家：${ref.key}`); return layout.byAddr.get(addr); }
  if(ref.kind==='raw') return layout.byAddr.get(`${ref.sheet??context.sheet}!${ref.addr.replaceAll('$','')}`);
  const key=ref.key.split('/').length===2 ? `${context.block}/${ref.key}` : ref.key;
  const p=layout.byKey.get(key); if(!p) throw Error(`找不到符号引用：${key}`); return p;
}
export function rangePlacements(layout, left, right, context) {
  const a=resolvePlaced(layout,left,context), b=resolvePlaced(layout,right,context);
  if(!a||!b||a.sheet!==b.sheet) throw Error('区域端点必须位于同一工作表');
  const out=[];
  for(let r=Math.min(a.row,b.row);r<=Math.max(a.row,b.row);r++) for(let c=Math.min(a.col,b.col);c<=Math.max(a.col,b.col);c++) out.push(layout.byAddr.get(`${a.sheet}!${colName(c)}${r}`));
  return out;
}
export function excelResolver(layout, context) {
  return ref=>{
    const p=resolvePlaced(layout,ref,context); if(!p) throw Error('引用不存在');
    const {col,row}=address(p.addr), prefix=p.sheet===context.sheet?'':(/^[A-Za-z_][A-Za-z0-9_]*$/.test(p.sheet)?p.sheet:"'"+p.sheet.replaceAll("'","''")+"'")+ '!';
    return `${prefix}$${colName(col)}$${row}`;
  };
}
