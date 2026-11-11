import {recordLayout} from './record-layout.js';
import {NS, esc, xmlDoc, colName, address} from './xml.js';
import {parseExpr, serializeExpr, templateToFormula, excelFunctionNames} from './expr.js';
import {layoutDoc, excelResolver} from './layout.js';
import {evaluateDoc} from './evaluate.js';
import {buildDocumentLayout} from './document-layout.js';
import {documentTokens} from './document-tokens.js';
const REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships';
const PACKAGE_REL='http://schemas.openxmlformats.org/package/2006/relationships';
const palette={smallPrint:{fill:0,font:4},signature:{fill:4,font:0},badge:{fill:3,font:4},title:{fill:2,font:2},section:{fill:3,font:3},header:{fill:3,font:1},label:{fill:0,font:1},input:{fill:4,font:0},calculated:{fill:5,font:0},body:{fill:0,font:0},stepNumber:{fill:3,font:1},stepTitle:{fill:3,font:1},note:{fill:0,font:0}};
export function formulaForCell(p, doc, layout) {
  const formula=modelFormula(p,doc,layout); return formula===null?null:excelFunctionNames(formula);
}
function modelFormula(p, doc, layout) {
  const c=p.cell, resolver=excelResolver(layout,p);
  if(c.var && (!c.show || c.show==='value')) { const v=doc.vars.find(v=>v.key===c.var); return v.expr!==undefined?serializeExpr(parseExpr(v.expr),resolver):null; }
  if(c.f!==undefined) return serializeExpr(parseExpr(c.f),resolver);
  if(c.t!==undefined) return templateToFormula(c.t,resolver);
  return null;
}
function cellXml(addr,value,formula,style) {
  const attr=`r="${addr}" s="${style}"`;
  if(formula!==null) {
    const t=typeof value==='string'?' t="str"':typeof value==='boolean'?' t="b"':'';
    return `<c ${attr}${t}><f>${esc(formula)}</f><v>${esc(typeof value==='boolean'?Number(value):value??0)}</v></c>`;
  }
  if(value===null || value===undefined) return `<c ${attr}/>`;
  if(typeof value==='string') return `<c ${attr} t="inlineStr"><is><t xml:space="preserve">${esc(value)}</t></is></c>`;
  return `<c ${attr}${typeof value==='boolean'?' t="b"':''}><v>${esc(typeof value==='boolean'?Number(value):value)}</v></c>`;
}
export async function writeWorkbook(doc) {
  if(doc.sheets.some(s=>s.name==='_meta')) throw Error('_meta 是保留工作表名');
  const zip=new globalThis.JSZip(), paper=buildDocumentLayout(doc),layout=paper.layout, values=paper.values, vars=new Map(doc.vars.map(v=>[v.key,v]));
  const formats=new Map(), styleIds=new Map(), xfs=[];
  function styleId(role,fmt='General') {
    const key=role+'|'+fmt;
    if(styleIds.has(key)) return styleIds.get(key);
    if(fmt!=='General' && !formats.has(fmt)) formats.set(fmt,164+formats.size);
    const p=palette[role], id=xfs.length; styleIds.set(key,id);
    xfs.push(`<xf numFmtId="${formats.get(fmt)??0}" fontId="${p.font}" fillId="${p.fill}" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyNumberFormat="1" applyAlignment="1"><alignment vertical="center" wrapText="1"${['title','header','stepNumber'].includes(role)?' horizontal="center"':''}/></xf>`);
    return id;
  }
  styleId('body');
  for(const [i,paperSheet] of paper.sheets.entries()) {
    const s={...paperSheet,cells:paperSheet.cells.map(c=>c.placement)};
    const rows=new Map(), row=r=>{ if(!rows.has(r)) rows.set(r,{cells:[],height:24}); return rows.get(r); }, styles=new Map();
    for(const p of s.cells) {
      const formula=formulaForCell(p,doc,layout), v=values.get(p.fullAddr), fmt=p.cell.fmt??(p.cell.var?vars.get(p.cell.var)?.fmt:undefined)??'General';
      const inputVar=p.cell.var && (!p.cell.show||p.cell.show==='value') && vars.get(p.cell.var)?.expr===undefined;
      const role=p.cell.input||inputVar?'input':formula!==null && !['title','section','header','body','note'].includes(p.style)?'calculated':formula!==null && p.style==='body'?'calculated':p.style;
      const style=styleId(role,fmt), rowsSpanned=p.cell.rows??1; styles.set(p,style);
      const available=s.widths.slice(p.col-1,p.col-1+(p.cell.span??1)).reduce((a,b)=>a+b,0);
      const length=String(v??'').split('\n').reduce((n,t)=>n+Math.max(1,Math.ceil([...t].reduce((n,c)=>n+(c.charCodeAt(0)>255?2:1),0)/Math.max(available,1))),0);
      // A vertically merged cell shares its estimated height across the rows it covers.
      const height=Math.ceil((p.style==='title'?34:Math.min(240*rowsSpanned,length*15+8))/rowsSpanned);
      for(let r=p.row;r<p.row+rowsSpanned;r++) row(r).height=Math.max(row(r).height,height);
      row(p.row).cells.push([p.col,cellXml(p.addr,v,formula,style)]);
    }
    // Covered cells repeat the merge's style so Excel draws the merged range's full border.
    for(const [at,p] of s.covered) { const {col,row:r}=address(at); row(r).cells.push([col,`<c r="${at}" s="${styles.get(p)}"/>`]); }
    const conditional=s.cells.filter(p=>p.cell.input&&p.cell.deviation).map((p,j)=>`<conditionalFormatting sqref="${p.addr}"><cfRule type="expression" dxfId="0" priority="${j+1}"><formula>${esc(excelFunctionNames(serializeExpr(parseExpr(p.cell.deviation.test),excelResolver(layout,p))))}</formula></cfRule></conditionalFormatting>`).join('');
    const body=`<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr><dimension ref="A1:${colName(s.widths.length)}${Math.max(1,s.maxRow)}"/><sheetViews><sheetView workbookViewId="0"${i===0?' tabSelected="1"':''}/></sheetViews><sheetFormatPr defaultRowHeight="24"/><cols>${s.widths.map((w,c)=>`<col min="${c+1}" max="${c+1}" width="${w}" customWidth="1"/>`).join('')}</cols><sheetData>${[...rows].sort((a,b)=>a[0]-b[0]).map(([r,x])=>`<row r="${r}" ht="${x.height}" customHeight="1">${x.cells.sort((a,b)=>a[0]-b[0]).map(c=>c[1]).join('')}</row>`).join('')}</sheetData>${s.merges.length?`<mergeCells count="${s.merges.length}">${s.merges.map(m=>`<mergeCell ref="${m}"/>`).join('')}</mergeCells>`:''}${conditional}<printOptions horizontalCentered="1"/><pageMargins left="0.3" right="0.3" top="0.3" bottom="0.3" header="0.15" footer="0.15"/><pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/>${s.rowBreaks?.length?`<rowBreaks count="${s.rowBreaks.length}" manualBreakCount="${s.rowBreaks.length}">${s.rowBreaks.map(id=>`<brk id="${id}" max="16383" man="1"/>`).join('')}</rowBreaks>`:''}`;
    zip.file(`xl/worksheets/sheet${i+1}.xml`,xmlDoc(`<worksheet xmlns="${NS}">${body}</worksheet>`));
  }
  const meta={actualFlags:doc.actualFlags??{},schema:doc.schema,id:doc.id,code:doc.code,revision:doc.revision,revisionUid:doc.revisionUid,revisionCode:doc.revisionCode,revisionAliases:doc.revisionAliases,designUid:doc.uid,batchUid:doc.batchUid,tables:recordLayout(doc).tables,planVars:doc.vars,generatedAt:new Date().toISOString(),inputs:layout.inputs.map(input=>{
    const p=layout.byAddr.get(`${input.sheet}!${input.cell}`), row=layout.sheets.find(s=>s.name===p.sheet).cells.filter(x=>x.row<=p.row && p.row<x.row+(x.cell.rows??1) && x.col<p.col && typeof values.get(x.fullAddr)==='string' && values.get(x.fullAddr)!=='');
    // Formula labels are checked using their cached value; static row/column anchors detect moved cells.
    const anchors=row.filter(x=>formulaForCell(x,doc,layout)===null).slice(0,1).map(x=>({cell:x.addr,value:values.get(x.fullAddr)}));
    const header=layout.sheets.find(s=>s.name===p.sheet).cells.find(x=>x.block===p.block && x.style==='header' && x.col<=p.col && x.col+(x.cell.span??1)>p.col);
    if(header) anchors.push({cell:header.addr,value:values.get(header.fullAddr)});
    return {...input,unit:p.cell.unit??'',anchors};
  })};
  const json=JSON.stringify(meta), chunks=[];
  for(let start=0;start<json.length;) { let end=Math.min(start+30000,json.length); if(/[\uD800-\uDBFF]/.test(json[end-1])) end--; chunks.push(json.slice(start,end)); start=end; }
  const metaIndex=layout.sheets.length+1;
  zip.file(`xl/worksheets/sheet${metaIndex}.xml`,xmlDoc(`<worksheet xmlns="${NS}"><sheetData>${chunks.map((s,i)=>`<row r="${i+1}">${cellXml(`A${i+1}`,s,null,0)}</row>`).join('')}</sheetData></worksheet>`));
  const names=[...doc.sheets.map(s=>({name:s.name,...s.hidden?{state:'hidden'}:{}})),{name:'_meta',state:'veryHidden'}];
  zip.file('xl/workbook.xml',xmlDoc(`<workbook xmlns="${NS}" xmlns:r="${REL}"><bookViews><workbookView activeTab="0" firstSheet="0"/></bookViews><sheets>${names.map((s,i)=>`<sheet name="${esc(s.name)}" sheetId="${i+1}" r:id="rId${i+1}"${s.state?` state="${s.state}"`:''}/>`).join('')}</sheets><definedNames>${doc.sheets.map((s,i)=>`<definedName name="_xlnm.Print_Titles" localSheetId="${i}">${esc("'"+s.name.replaceAll("'","''")+"'!$1:$1")}</definedName>`).join('')}</definedNames><calcPr calcId="191029" fullCalcOnLoad="1" forceFullCalc="1"/></workbook>`));
  zip.file('xl/_rels/workbook.xml.rels',xmlDoc(`<Relationships xmlns="${PACKAGE_REL}">${names.map((s,i)=>`<Relationship Id="rId${i+1}" Type="${REL}/worksheet" Target="worksheets/sheet${i+1}.xml"/>`).join('')}<Relationship Id="styles" Type="${REL}/styles" Target="styles.xml"/></Relationships>`));
  zip.file('_rels/.rels',xmlDoc(`<Relationships xmlns="${PACKAGE_REL}"><Relationship Id="rId1" Type="${REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>`));
  const colors=['FF'+documentTokens.paper,'FF'+documentTokens.paper,'FF'+documentTokens.accent,'FF'+documentTokens.section,'FF'+documentTokens.input,'FF'+documentTokens.calculated];
  zip.file('xl/styles.xml',xmlDoc(`<styleSheet xmlns="${NS}"><numFmts count="${formats.size}">${[...formats].map(([fmt,id])=>`<numFmt numFmtId="${id}" formatCode="${esc(fmt)}"/>`).join('')}</numFmts><fonts count="5"><font><sz val="10"/><name val="${documentTokens.font}"/></font><font><b/><sz val="11"/><name val="${documentTokens.font}"/></font><font><b/><sz val="18"/><color rgb="FFFFFFFF"/><name val="${documentTokens.font}"/></font><font><b/><sz val="12"/><name val="${documentTokens.font}"/></font><font><sz val="8"/><color rgb="FF${documentTokens.muted}"/><name val="${documentTokens.font}"/></font></fonts><fills count="6"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>${colors.slice(2).map(c=>`<fill><patternFill patternType="solid"><fgColor rgb="${c}"/><bgColor indexed="64"/></patternFill></fill>`).join('')}</fills><borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border>${['left','right','top','bottom'].map(s=>`<${s} style="thin"><color rgb="FF${documentTokens.rule}"/></${s}>`).join('')}<diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="${xfs.length}">${xfs.join('')}</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles><dxfs count="1"><dxf><fill><patternFill patternType="solid"><fgColor rgb="FFFFC7CE"/><bgColor indexed="64"/></patternFill></fill></dxf></dxfs></styleSheet>`));
  zip.file('[Content_Types].xml',xmlDoc(`<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${names.map((s,i)=>`<Override PartName="/xl/worksheets/sheet${i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')}</Types>`));
  return zip.generateAsync({type:'uint8array',compression:'DEFLATE'});
}
