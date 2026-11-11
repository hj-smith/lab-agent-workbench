import {buildDocumentLayout} from './document-layout.js';
import {buildGridWordParts} from '../grid-word-sop.js';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
export const DOCX_MIME='application/vnd.openxmlformats-officedocument.wordprocessingml.document';

/** Real Office Open XML Word tables, using the same evaluated section/cell model
 * as the live preview, print and Excel. Uses only the bundled JSZip runtime. */
export async function writeDocumentWord(doc,{sheetName='SOP'}={}){
 if(!globalThis.JSZip)await import('../vendor/jszip.min.js');
 const paper=buildDocumentLayout(doc),sheet=paper.sheets.find(s=>s.name===sheetName);
 if(!sheet)throw Error('没有可导出的 SOP 预览，请先打开实验');
 const tokens=paper.tokens;
 const cells=sheet.cells.map(c=>{
  const heading=['section','header','stepTitle','stepNumber'].includes(c.style),title=c.style==='title';
  return {cell:c.placement.addr,row:c.placement.row,col:c.placement.col,value:c.value,displayValue:c.text,style:{font:{name:tokens.font,size:title?16:heading?10:9,bold:heading||title,color:title?'FFFFFF':heading?tokens.accent:tokens.ink},fill:title?tokens.accent:c.role==='input'?tokens.input:heading?tokens.section:tokens.paper,alignment:{vertical:'top',horizontal:title?'center':typeof c.value==='number'?'right':'left'},borders:Object.fromEntries(['top','bottom','left','right'].map(side=>[side,{style:'thin',color:tokens.rule}]))}};
 });
 const model={title:paper.meta.title,sheets:[{name:sheet.name,role:'SOP',maxRow:sheet.maxRow,maxCol:sheet.widths.length,columnWidths:sheet.widths,cells,merges:sheet.merges.map(range=>({range}))}]};
 const {parts}=buildGridWordParts(model,{sheetNames:[sheetName],minFontPt:9});
 if(paper.meta.draft){
  parts['word/header1.xml']='<?xml version="1.0" encoding="UTF-8"?><w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:pPr><w:jc w:val="right"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="'+esc(tokens.font)+'" w:eastAsia="'+esc(tokens.font)+'"/><w:color w:val="ABC5B7"/><w:sz w:val="20"/></w:rPr><w:t>草稿 · 执行前核对</w:t></w:r></w:p></w:hdr>';
  parts['word/document.xml']=parts['word/document.xml'].replace('<w:document xmlns:w=', '<w:document xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:w=').replace('<w:sectPr>','<w:sectPr><w:headerReference w:type="default" r:id="rIdDraft"/>');
  parts['word/_rels/document.xml.rels']=parts['word/_rels/document.xml.rels'].replace('</Relationships>','<Relationship Id="rIdDraft" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/></Relationships>');
  parts['[Content_Types].xml']=parts['[Content_Types].xml'].replace('</Types>','<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/></Types>');
 }
 const zip=new globalThis.JSZip();for(const [name,text]of Object.entries(parts))zip.file(name,text);
 return zip.generateAsync({type:'uint8array',compression:'DEFLATE'});
}
