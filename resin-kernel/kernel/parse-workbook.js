import {readWorkbook} from './xlsx-read.js';
import {address, colName} from './xml.js';
import {parseA1Formula, mapRefs, serializeExpr, formulaToTemplate, escapeTemplate} from './expr.js';
import {layoutDoc} from './layout.js';
export const cleanKey = label => String(label).replace(/[\[\]{}|@/]/g,'').trim().replace(/\s+/g,'_') || 'value';
const unique = (base, used) => { let key=base,n=2; while(used.has(key)) key=`${base}_${n++}`; used.add(key); return key; };
const isText = c => c && c.formula===undefined && typeof c.value==='string' && c.value!=='';
export async function parseWorkbook(bytes, {file='',allowErrors=false}={}) {
  const source=await readWorkbook(bytes,{allowErrors}), sheets=source.sheets.filter(s=>s.name!=='_meta'), report={unsymbolizedFormulas:[],warnings:[],coveredCells:[]};
  const originals=new Map(sheets.flatMap(s=>s.cells.map(c=>[`${s.name}!${c.addr}`,c]))), vars=[], homes=new Map(), usedVars=new Set();
  const mergedCovered=new Set();
  for(const sheet of sheets)for(const merge of sheet.merges){const [a,b]=merge.split(':').map(address);for(let row=a.row;row<=b.row;row++)for(let col=a.col;col<=b.col;col++)if(row!==a.row||col!==a.col)mergedCovered.add(sheet.name+'!'+colName(col)+row);}
  function addVar(sheet, addr, label, unit='') {
    const full=`${sheet}!${addr}`, c=originals.get(full); if(mergedCovered.has(full)||homes.has(full) || !c || c.value==null || c.value==='') return;
    const v={key:unique(cleanKey(label),usedVars),label,unit,fmt:c.numFmt,sourceAddress:full};
    if(c.formula===undefined) v.value=c.value; else v.expr=c.formula;
    if(sheet==='SOP' && addr==='B2') v.role='code';
    vars.push(v); homes.set(full,v);
  }
  const sop=sheets.find(s=>s.name==='SOP'), sopMap=new Map(sop?.cells.map(c=>[c.addr,c])??[]);
  const parameterRanges=new Map();
  for(const sheet of sheets){
    const sheetMap=new Map(sheet.cells.map(c=>[c.addr,c])),parameterHeader=sheet.cells.find(c=>/^A\d+$/.test(c.addr)&&c.value==='参数');
    const paramStart=parameterHeader?address(parameterHeader.addr).row:0;let paramEnd=paramStart;
    if(!paramStart)continue;
    for(let r=paramStart+1;isText(sheetMap.get(`A${r}`))&&!/^[A-Z][.．、]/.test(sheetMap.get(`A${r}`)?.value);r++) {
      paramEnd=r;
      for(const c of [2,5,8,11,14]) {
        const label=sheetMap.get(`${colName(c-1)}${r}`)?.value;
        if(typeof label!=='string') continue;
        const unit=c===14 ? /[（(]([^()（）]+)[）)]/.exec(label)?.[1]??(/体积|THF总量/.test(label)?'mL':/质量/.test(label)?'g':'') : sheetMap.get(`${colName(c+1)}${r}`)?.value??'';
        addVar(sheet.name,`${colName(c)}${r}`,label,String(unit));
      }
    }
    parameterRanges.set(sheet.name,{paramStart,paramEnd});
  }
  // Formula traversal also discovers variables outside the SOP parameter table.
  const asts=new Map();
  for(const s of sheets) for(const c of s.cells.filter(c=>c.formula!==undefined)) {
    try {
      const ast=parseA1Formula(c.formula); asts.set(`${s.name}!${c.addr}`,ast);
      mapRefs(ast,r=>{
        if(r.sheet && r.sheet!==s.name) {
          const pos=address(r.addr), addr=r.addr.replaceAll('$',''), target=originals.get(`${r.sheet}!${addr}`), left=originals.get(`${r.sheet}!${colName(pos.col-1)}${pos.row}`);
          if(isText(left) && target && target.value!==null) addVar(r.sheet,addr,left.value,String(originals.get(`${r.sheet}!${colName(pos.col+1)}${pos.row}`)?.value??''));
        }
        return r;
      });
    } catch(e) { report.unsymbolizedFormulas.push({sheet:s.name,cell:c.addr,formula:c.formula,error:e.message}); }
  }
  // Units may only come from short constant text, never another variable home.
  for(const [full,v] of homes) {
    const split=full.lastIndexOf('!'), sheet=full.slice(0,split), p=address(full.slice(split+1));
    const range=parameterRanges.get(sheet);if(range&&p.col===14&&p.row>range.paramStart&&p.row<=range.paramEnd)continue;
    const right=`${sheet}!${colName(p.col+1)}${p.row}`, c=originals.get(right);
    v.unit=isText(c) && [...c.value].length<=12 && !homes.has(right)?c.value:'';
  }
  const varLabels=new Map(),varUnits=new Map();
  for(const [full,v]of homes){
    const split=full.lastIndexOf('!'),sheet=full.slice(0,split),p=address(full.slice(split+1));
    const left=`${sheet}!${colName(p.col-1)}${p.row}`,right=`${sheet}!${colName(p.col+1)}${p.row}`;
    if(isText(originals.get(left))&&originals.get(left).value===v.label&&!homes.has(left))varLabels.set(left,v.key);
    if(v.unit&&originals.get(right)?.value===v.unit&&!homes.has(right))varUnits.set(right,v.key);
  }
  const codeVar=vars.find(v=>v.role==='code'), code=codeVar?.value??file.replace(/\.xlsx$/i,'')??'design';
  const doc={schema:'lab-design/1',id:code,code,parentCode:'',title:sopMap.get('A1')?.value??code,revision:1,source:{file,sha256:''},vars,sheets:[],actual:{}};
  const data=bytes instanceof Uint8Array?bytes:new Uint8Array(bytes);
  doc.source.sha256=[...new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256',data))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const blockIds=new Set();
  for(const s of sheets) {
    const {paramStart=0,paramEnd=0}=parameterRanges.get(s.name)??{};
    const model={name:s.name,landscape:s.orientation==='landscape',widths:s.widths,blocks:[]}; doc.sheets.push(model);
    const map=new Map(s.cells.map(c=>[c.addr,c])), spans=new Map(), heights=new Map(), covered=new Set(), coveredRows=new Set();
    // Merges are rectangles: the top-left cell carries span (columns) and rows; covered cells are dropped like Excel does.
    for(const merge of s.merges) {
      const [a,b]=merge.split(':').map(address), home=colName(a.col)+a.row;
      spans.set(home,b.col-a.col+1); if(b.row>a.row) heights.set(home,b.row-a.row+1);
      for(let r=a.row;r<=b.row;r++) for(let c=a.col;c<=b.col;c++) if(r>a.row || c>a.col) covered.add(colName(c)+r);
      for(let r=a.row+1;r<=b.row;r++) coveredRows.add(r);
    }
    const maxRow=Math.max(...s.cells.map(c=>address(c.addr).row),...s.merges.map(m=>address(m.split(':')[1]).row));
    const n=s.widths.length;
    const active=r=>s.cells.some(c=>address(c.addr).row===r && (c.value!==null && c.value!=='' || c.formula!==undefined)) || spans.has(`A${r}`) || coveredRows.has(r);
    const rowCells=r=>{ const out=[]; for(let col=1;col<=n;col++) { const addr=colName(col)+r; if(!covered.has(addr)) out.push({col,addr,src:map.get(addr),span:spans.get(addr)??1,rows:heights.get(addr)??1}); } return out; };
    // A block must contain every row of the merges that start inside it.
    const blockEnd=(start,end)=>{ for(let r=start;r<=end;r++) for(const x of rowCells(r)) end=Math.max(end,r+x.rows-1); return end; };
    const full=r=>spans.get(`A${r}`)===n;
    const isHeader=r=> { const cs=rowCells(r); return /^(项目|相对时间|操作事件|参数|样品编号|变量|步骤|样品)/.test(cs[0]?.src?.value??'') && cs.length>=3 && cs.every(x=>isText(x.src) && x.src.value.length<=40); };
    const partialMerge=r=>rowCells(r).some(x=>x.span>1) && !full(r);
    let next=1, preceding='';
    function block(type, start, fields, height=1) {
      const text=['title','section','note'].includes(type)?map.get(`A${start}`)?.value:preceding;
      const raw=String(text??'').replace(/[/\]\s]/g,'');
      const base=raw?(['title','section','note'].includes(type)?raw.slice(0,16)+'_'+type:raw).slice(0,20):'';
      let id=base||cleanKey(`${s.name}_${type}_${start}`), suffix=2;
      while(blockIds.has(id)) { const tail=`_${suffix++}`; id=base?base.slice(0,20-tail.length)+tail:`${s.name}_${type}_${start}${tail}`; }
      blockIds.add(id);
      const b={type,id,...(start>next?{gapBefore:start-next}:{}),...fields}; model.blocks.push(b); next=start+height;
      if(['title','section','note'].includes(type) && typeof text==='string' && text.trim()) preceding=text;
      return b;
    }
    function cellAt(info, header) {
      const {src,addr,span,rows}=info, home=homes.get(`${s.name}!${addr}`); let cell={};
      if(home) cell={var:home.key,show:'value'};
      else if(varLabels.has(`${s.name}!${addr}`))cell={var:varLabels.get(`${s.name}!${addr}`),show:'label'};
      else if(varUnits.has(`${s.name}!${addr}`))cell={var:varUnits.get(`${s.name}!${addr}`),show:'unit'};
      else if(src?.formula!==undefined) cell={f:src.formula};
      else if(src?.value!==null && src?.value!==undefined) cell=typeof src.value==='string'?{t:escapeTemplate(src.value)}:{v:src.value};
      else if(header?.label) {
        const h=header.label, unit=map.get(`D${address(addr).row}`)?.value;
        const time=/时间|钟点|时刻|开始|结束/.test(h) || h==='实际值' && unit==='hh:mm';
        const numeric=/实际值|纯度|质量|净重|温|rpm|含固率|水量|用量|面积|浓度|Mn|Mw|Đ|组分|n=|取样|mg|mmol|\(%\)/i.test(h);
        cell={input:true,type:time?'time':numeric?'number':'text',...(time?{fmt:'hh:mm'}:{})};
      }
      if(src?.numFmt && src.numFmt!=='General' && !cell.fmt) cell.fmt=src.numFmt;
      if(span>1) cell.span=span;
      if(rows>1) cell.rows=rows;
      return cell;
    }
    function table(start,end, forceHeader=false, noHeader=false) {
      const first=rowCells(start), text=first.filter(x=>isText(x.src));
      const hasHeader=!noHeader && (forceHeader || text.length>=3 && first.every(x=>isText(x.src) && x.src.value.length<=40));
      const used=new Set(), header=hasHeader?first.map(x=>({key:unique(cleanKey(x.src?.value??colName(x.col)),used),label:x.src?.value??'',...(x.span>1?{span:x.span}:{}),...(x.rows>1?{rows:x.rows}:{})})):undefined;
      const headers=new Map(first.map((x,i)=>[x.col,header?.[i]])), ids=new Set(), counts=new Map();
      for(let r=start+(hasHeader?1:0);r<=end;r++) {const c=map.get(`A${r}`); if(isText(c)) counts.set(cleanKey(c.value),(counts.get(cleanKey(c.value))??0)+1);}
      const rows=[];
      for(let r=start+(hasHeader?1:0);r<=end;r++) {
        const a=map.get(`A${r}`), label=isText(a)?cleanKey(a.value):'', id=unique(label && counts.get(label)===1?label:`r${r}`,ids);
        rows.push({id,cells:rowCells(r).map(x=>cellAt(x,headers.get(x.col)))});
      }
      const result=block('table',start,{...(header?{header}:{}),rows},end-start+1);
      // A partially merged caption row (e.g. A15:H15) also labels the next table.
      if(first[0]?.span>=n/2 && typeof first[0].src?.value==='string') preceding=first[0].src.value.replace(/[（(].*$/,'');
      return result;
    }
    // Like Excel's merge command, content under a merge (outside its top-left cell) is not part of the document.
    for(const addr of covered) { const c=map.get(addr); if(c && (c.formula!==undefined || c.value!==null && c.value!=='')) report.coveredCells.push(`${s.name}!${addr}`); }
    for(let r=1;r<=maxRow;) {
      if(!active(r)) { r++; continue; }
      if(s.name==='SOP' && r===2) { table(r,r,false,true); r++; continue; }
      if(r===paramStart) { table(r,paramEnd,true); r=paramEnd+1; continue; }
      if(s.name==='SOP' && spans.get(`A${r}`)===7 && spans.get(`H${r}`)===7) {
        const start=r, left=[],right=[]; r++;
        while(r<=maxRow && spans.get(`B${r}`)===2 && spans.get(`I${r}`)===2) {
          for(const [side,num,title,body] of [[left,'A','B','D'],[right,'H','I','K']]) if(map.get(`${num}${r}`)?.value!=null) side.push({id:`step${map.get(`${num}${r}`).value}`,title:escapeTemplate(map.get(`${title}${r}`)?.value??''),text:map.get(`${body}${r}`)?.formula!==undefined?`{${map.get(`${body}${r}`).formula}}`:escapeTemplate(map.get(`${body}${r}`)?.value??'')});
          r++;
        }
        block('steps2',start,{left:{title:escapeTemplate(map.get(`A${start}`)?.value??''),steps:left},right:{title:escapeTemplate(map.get(`H${start}`)?.value??''),steps:right}},r-start); continue;
      }
      const populated=rowCells(r).filter(x=>x.src?.formula!==undefined || x.src?.value!=null && x.src?.value!=='');
      const height=heights.get(`A${r}`)??1;
      if(full(r) || height===1 && populated.length===1 && isText(populated[0].src) && populated[0].col===1) {
        const c=map.get(`A${r}`), value=c?.value??'', type=r===1?'title':String(value).length<65 && /^[A-Z][.．、]/.test(value)?'section':'note';
        block(type,r,{text:c?.formula!==undefined?`{${c.formula}}`:escapeTemplate(value),...(height>1?{rows:height}:{})},height); r+=height; continue;
      }
      const start=r; r++;
      if(!partialMerge(start)) while(r<=maxRow && active(r) && !full(r) && !partialMerge(r) && !isHeader(r)) r++;
      r=blockEnd(start,r-1)+1;
      table(start,r-1);
    }
  }
  const layout=layoutDoc(doc), srcMap={};
  const symbolic=(ast,sheet)=>mapRefs(ast,r=>{
    const addr=`${r.sheet??sheet}!${r.addr.replaceAll('$','')}`, home=homes.get(addr), placed=layout.byAddr.get(addr);
    if(home) return {type:'ref',kind:'var',key:home.key};
    if(!placed) { report.warnings.push(`引用超出模型：${addr}`); throw Error(`引用超出模型：${addr}`); }
    if(originals.get(addr)?.value==null && originals.get(addr)?.formula===undefined) report.warnings.push(`引用空格：${addr}`);
    return {type:'ref',kind:'cell',key:placed.key};
  });
  // Embedded batch codes are symbolic too, including the original K23 literal.
  function symbolizeCode(n) {
    if(n.type==='literal' && typeof n.value==='string' && codeVar && n.value.includes(code)) {
      const parts=n.value.split(code); let out={type:'literal',value:parts[0]};
      for(const part of parts.slice(1)) out={type:'binary',op:'&',left:{type:'binary',op:'&',left:out,right:{type:'ref',kind:'var',key:codeVar.key}},right:{type:'literal',value:part}};
      return out;
    }
    if(n.left) return {...n,left:symbolizeCode(n.left),right:symbolizeCode(n.right)};
    if(n.arg) return {...n,arg:symbolizeCode(n.arg)};
    if(n.args) return {...n,args:n.args.map(symbolizeCode)};
    return n;
  }
  for(const p of layout.byAddr.values()) {
    const src=originals.get(p.fullAddr); if(src) srcMap[p.key]=p.fullAddr;
    if(src?.formula===undefined) continue;
    try {
      const ast=symbolizeCode(symbolic(asts.get(p.fullAddr)??parseA1Formula(src.formula),p.sheet)), home=homes.get(p.fullAddr), template=formulaToTemplate(ast);
      if(home) home.expr=serializeExpr(ast);
      else if(p.cell.f!==undefined) { delete p.cell.f; if(template!==null) p.cell.t=template; else p.cell.f=serializeExpr(ast); }
      else {
        // Title/section/note/step text is always a template; non-template pieces become {expression}.
        const template=formulaToTemplate(ast,{loose:true});
        const b=doc.sheets.find(s=>s.name===p.sheet).blocks.find(b=>b.id===p.block);
        if(b.type==='steps2') { const step=[...b.left.steps,...b.right.steps].find(st=>`${b.id}/${st.id}/text`===p.key); if(step) step.text=template; }
        else b.text=template;
      }
    } catch(e) { report.unsymbolizedFormulas.push({sheet:p.sheet,cell:p.addr,formula:src.formula,error:e.message}); }
  }
  report.warnings=[...new Set(report.warnings)];
  report.unsymbolizedFormulas=[...new Map(report.unsymbolizedFormulas.map(item=>[`${item.sheet}!${item.cell}`,item])).values()];
  // Read-only source cache for semantic history recovery, including captions and
  // filled record cells that were never classified as editable inputs.
  doc.sourceWorkbook={file,sheets:sheets.map(s=>({name:s.name,cells:s.cells.filter(c=>c.value!==null&&c.value!==undefined&&c.value!=='').map(c=>({addr:c.addr,value:c.value,...c.formula!==undefined?{formula:c.formula}:{},...(c.numFmt?{numFmt:c.numFmt}:{})}))}))};
  return {doc,report,srcMap};
}
