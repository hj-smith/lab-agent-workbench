import {excelString,formatText} from './expr.js';

export function formatValue(value,fmt){
  if(value==null)return '';
  if(typeof value==='number'&&fmt&&fmt!=='General'){
    const unit=/^([0#,.]+%?)"([^"]*)"$/.exec(fmt);
    if(unit)try{return formatText(value,unit[1])+unit[2];}catch{/* Unknown numeric pattern falls through below. */}
    try{return formatText(value,fmt);}catch{/* Preserve readable data for unsupported display formats. */}
  }
  return excelString(value);
}
export function formatCellValue(doc,p,value,layout){
  const variable=p?.cell.var?doc.vars.find(v=>v.key===p.cell.var):null;
  const text=formatValue(value,p?.cell.type==='time'?'hh:mm':p?.cell.fmt??variable?.fmt);
  const header=p?.col===1&&layout?.sheets.find(s=>s.name===p.sheet)?.cells.find(q=>q.block===p.block&&q.style==='header'&&q.col===1);
  return typeof value==='number'&&/^相对时间\s*[（(]h[）)]$/i.test(header?.cell?.t??'')?text+' h':text;
}
export function recordItemLabel(doc,layout,values,p){
  if(!p)return '';
  const sheet=layout.sheets.find(s=>s.name===p.sheet),block=sheet.blocks.find(b=>b.id===p.block);
  const display=q=>q?formatCellValue(doc,q,values.get(q.fullAddr),layout):'';
  const row=sheet.cells.find(q=>q.col===1&&q.row<=p.row&&p.row<q.row+(q.cell.rows??1));
  const header=sheet.cells.find(q=>q.block===p.block&&q.style==='header'&&q.col===p.col);
  const title=block.title?sheet.cells.find(q=>q.block===p.block&&q.key===`${p.block}/title/A`):null;
  const section=title??sheet.cells.filter(q=>q.row<p.row&&(['section','note','title'].includes(q.style)||q.col===1&&(q.cell.span??1)>=sheet.widths.length/2)&&display(q).trim()).at(-1);
  return [display(section)||p.sheet,display(row),display(header)||p.addr.replace(/\d+$/,'')].filter(Boolean).join(' · ');
}
// Blank record rows still contain inputs, so they remain available for entry.
export const isSpacerRow=cells=>!cells?.length||cells.every(({cell:c})=>!c.input&&!c.var&&c.f===undefined&&c.v===undefined&&(c.t===undefined||c.t===''));
