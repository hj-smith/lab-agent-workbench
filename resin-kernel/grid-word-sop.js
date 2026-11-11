// SOP grids become Word tables using explicit columns, merged cells and shared content.
// Active LAB SOP layout authority is the late Excel format, irrespective of original file type.
const WORD='http://schemas.openxmlformats.org/wordprocessingml/2006/main';
const MIME='application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[char])).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g,'');
const number=value=>value==null||value===''||!Number.isFinite(Number(value))?null:Number(value);
function location(address){const match=String(address||'').match(/^([A-Z]+)(\d+)$/i);if(!match)return null;let col=0;for(const char of match[1].toUpperCase())col=col*26+char.charCodeAt(0)-64;return {row:Number(match[2]),col};}
function parseMerge(merge){if(merge.range){const [start,end]=merge.range.split(':').map(location);if(start&&end)return {r1:start.row,c1:start.col,r2:end.row,c2:end.col};}return {r1:merge.r1,c1:merge.c1,r2:merge.r2,c2:merge.c2};}
function color(value,fallback='000000'){
  if(typeof value==='object'&&value){if(value.resolvedRgb||value.rgb)return color(value.resolvedRgb??value.rgb,fallback);if(value.theme!=null)return ['FFFFFF','000000','E7E6E6','44546A','4472C4','ED7D31','A5A5A5','FFC000','5B9BD5','70AD47'][value.theme]||fallback;return fallback;}
  const match=String(value??'').match(/(?:^#?|rgb=['"]?)([A-Fa-f0-9]{8}|[A-Fa-f0-9]{6})(?:$|['"])/);return match?match[1].slice(-6).toUpperCase():fallback;
}
function visible(cell){return cell?.displayValue!=null?String(cell.displayValue):cell?.value!=null?String(cell.value):'';}
function cellStyle(cell){return cell?.style||{};}
function fontSize(cell,options){return Math.max(options.minFontPt??9,number(cellStyle(cell).font?.size)??9);}
function normalizeGrid(sheet,options){
  const sourceCells=sheet.cells||Object.values(sheet.cellMap||{}),cells=new Map();
  for(const cell of sourceCells){const point=location(cell.cell)||{row:cell.row,col:cell.col??cell.column};if(point.row&&point.col)cells.set(point.row+':'+point.col,{...cell,row:point.row,col:point.col});}
  // SOP execution frames are A:N. Existing styled but empty columns beyond N are not extra execution fields.
  const columns=Math.min(options.maxColumns??14,sheet.maxCol??Math.max(1,...[...cells.values()].map(cell=>cell.col)));
  const rows=sheet.maxRow??Math.max(1,...[...cells.values()].map(cell=>cell.row));
  const widthInput=Array.from({length:columns},(_,index)=>Math.max(1,number(sheet.columnWidths?.[index])??10));
  const total=widthInput.reduce((a,b)=>a+b,0),available=options.tableWidthTwips??15398;
  const widths=widthInput.map(value=>Math.floor(value/total*available));widths[widths.length-1]+=available-widths.reduce((a,b)=>a+b,0);
  const merges=(sheet.merges||[]).map(parseMerge).filter(merge=>merge.r1&&merge.c1<=columns&&merge.r2<=rows).map(merge=>({...merge,c2:Math.min(columns,merge.c2)}));
  return {sheet,cells,columns,rows,widths,merges,available};
}
function getCell(grid,row,col){return grid.cells.get(row+':'+col)||{row,col,value:'',displayValue:'',style:{}};}
function rowHeight(grid,row){const value=grid.sheet.rowHeights?.[row];return number(typeof value==='object'?value.height:value)??18;}
function border(style){const values=style.borders??style.border??{},pieces=[];for(const side of ['top','left','bottom','right']){const value=values[side],kind=typeof value==='string'?value:value?.style;const wordKind=kind==='double'?'double':kind==='dashed'?'dashed':kind==='dotted'?'dotted':kind==='none'||kind==null?'nil':'single';const size=kind==='thick'?12:kind==='medium'?8:4;pieces.push('<w:'+side+' w:val="'+wordKind+'" w:sz="'+size+'" w:color="'+color(value?.color,'B7B7B7')+'"/>');}return '<w:tcBorders>'+pieces.join('')+'</w:tcBorders>';}
function fill(style){const value=style.fill;if(typeof value==='string')return color(value,'FFFFFF');if(value&&(value.type==='solid'||value.patternType==='solid'||value.color||value.rgb))return color(value.foreground??value.color??value.rgb??value,'FFFFFF');return null;}
function paragraph(cell,options){
  const style=cellStyle(cell),font=style.font||{},alignment=style.alignment||{};
  const horizontal=alignment.horizontal==='center'?'center':alignment.horizontal==='right'?'right':alignment.horizontal==='justify'?'both':typeof cell.value==='number'?'right':'left';
  const rpr='<w:rPr><w:rFonts w:ascii="'+escape(font.name||'Microsoft YaHei')+'" w:hAnsi="'+escape(font.name||'Microsoft YaHei')+'" w:eastAsia="'+escape(font.name||'Microsoft YaHei')+'"/>'+(font.bold?'<w:b/>':'')+(font.italic?'<w:i/>':'')+'<w:sz w:val="'+Math.round(fontSize(cell,options)*2)+'"/><w:szCs w:val="'+Math.round(fontSize(cell,options)*2)+'"/><w:color w:val="'+color(font.color)+'"/></w:rPr>';
  const text=visible(cell),runs=text.split('\n').map((line,index)=>(index?'<w:r><w:br/></w:r>':'')+'<w:r>'+rpr+'<w:t xml:space="preserve">'+escape(line)+'</w:t></w:r>').join('');
  return '<w:p><w:pPr><w:pStyle w:val="GridText"/><w:spacing w:before="0" w:after="0" w:line="230" w:lineRule="auto"/><w:jc w:val="'+horizontal+'"/><w:widowControl w:val="0"/></w:pPr>'+runs+'</w:p>';
}
function tableCell(grid,row,col,merge,continuation,options){
  const span=merge?merge.c2-merge.c1+1:1,anchor=merge?getCell(grid,merge.r1,merge.c1):getCell(grid,row,col),cell=continuation?{...anchor,value:'',displayValue:''}:getCell(grid,row,col),style=cellStyle(anchor),background=fill(style),width=grid.widths.slice(col-1,col-1+span).reduce((a,b)=>a+b,0),vertical=style.alignment?.vertical==='top'?'top':style.alignment?.vertical==='bottom'?'bottom':'center';
  const properties='<w:tcPr><w:tcW w:w="'+width+'" w:type="dxa"/>'+(span>1?'<w:gridSpan w:val="'+span+'"/>':'')+(merge&&merge.r2>merge.r1?'<w:vMerge'+(continuation?'':' w:val="restart"')+'/>':'')+(background?'<w:shd w:val="clear" w:color="auto" w:fill="'+background+'"/>':'')+border(style)+'<w:vAlign w:val="'+vertical+'"/></w:tcPr>';
  return '<w:tc>'+properties+(continuation?'<w:p/>':paragraph(cell,options))+'</w:tc>';
}
function estimateRowHeight(grid,row,options){
  let required=rowHeight(grid,row);
  for(let col=1;col<=grid.columns;col++){
    const merge=grid.merges.find(value=>value.r1<=row&&value.r2>=row&&value.c1<=col&&value.c2>=col);
    if(merge&&(col!==merge.c1||row!==merge.r1))continue;
    const cell=getCell(grid,row,col),span=merge?merge.c2-merge.c1+1:1,width=grid.widths.slice(col-1,col-1+span).reduce((a,b)=>a+b,0)/20-4,points=fontSize(cell,options),capacity=Math.max(1,width/(points*.53));
    const lines=visible(cell).split('\n').reduce((n,line)=>n+Math.max(1,Math.ceil([...line].reduce((length,char)=>length+(char.charCodeAt(0)>255?2:1),0)/capacity)),0);
    required=Math.max(required,lines*points*1.15+4);
  }return required;
}
function groups(grid){
  if(grid.sheet.sections?.length){return grid.sheet.sections.map(section=>({start:section.start,end:section.end,headers:section.headers??1,role:section.role??'grid'})).filter(section=>section.start>=1&&section.end<=grid.rows&&section.end>=section.start);}
  let operation=null,sampling=null;for(let row=1;row<=grid.rows;row++){const text=visible(getCell(grid,row,1));if(operation==null&&/^B[.、\s]/.test(text))operation=row;if(sampling==null&&/^D[.、\s]/.test(text))sampling=row;}
  const result=[];if(operation&&sampling&&sampling>operation){result.push({start:1,end:operation-1,headers:Math.min(4,operation-1),role:'parameters'},{start:operation,end:sampling-1,headers:1,role:'operations'},{start:sampling,end:grid.rows,headers:Math.min(2,grid.rows-sampling+1),role:'sampling'});}else result.push({start:1,end:grid.rows,headers:1,role:'grid'});
  for(let index=result.length-1;index>0;index--)if(grid.merges.some(merge=>merge.r1<result[index].start&&merge.r2>=result[index].start)){result[index-1].end=result[index].end;result.splice(index,1);}return result;
}
function gridTable(grid,group,options){
  let rows='';for(let row=group.start;row<=group.end;row++){
    const height=rowHeight(grid,row),estimated=estimateRowHeight(grid,row,options),keepRow=estimated<340;
    let cells='';for(let col=1;col<=grid.columns;col++){
      const merge=grid.merges.find(value=>value.r1<=row&&value.r2>=row&&value.c1<=col&&value.c2>=col);if(merge&&col!==merge.c1)continue;
      cells+=tableCell(grid,row,col,merge,!!(merge&&row>merge.r1),options);if(merge)col=merge.c2;
    }
    rows+='<w:tr><w:trPr>'+((row-group.start)<group.headers?'<w:tblHeader/>':'')+'<w:trHeight w:val="'+Math.round(height*20)+'" w:hRule="atLeast"/>'+(keepRow?'<w:cantSplit/>':'')+'</w:trPr>'+cells+'</w:tr>';
  }
  return '<w:tbl><w:tblPr><w:tblW w:w="'+grid.available+'" w:type="dxa"/><w:tblLayout w:type="fixed"/><w:tblCellMar><w:top w:w="30" w:type="dxa"/><w:left w:w="40" w:type="dxa"/><w:bottom w:w="30" w:type="dxa"/><w:right w:w="40" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>'+grid.widths.map(width=>'<w:gridCol w:w="'+width+'"/>').join('')+'</w:tblGrid>'+rows+'</w:tbl>';
}
function selectSheets(model,options){if(options.sheetNames?.length)return model.sheets.filter(sheet=>options.sheetNames.includes(sheet.name)||options.sheetNames.includes(sheet.sourceName));const selected=model.sheets.filter(sheet=>/^(sop|SOP计划|执行SOP)$/i.test(sheet.role||'')||/^SOP$|^R100-\d+[AB]?$/.test(sheet.sourceName||sheet.name));return selected.length?selected:[model.sheets[0]];}
export function buildGridWordParts(model,options={}){
  if(!model?.sheets?.length)throw new Error('没有可转换的源SOP单元格网格。');
  const sheets=selectSheets(model,options),report={sheets:[],tableCount:0,contentMode:model.exactReplay?'source-exact':'patched-design'};let body='';
  for(let index=0;index<sheets.length;index++){
    if(index)body+='<w:p><w:r><w:br w:type="page"/></w:r></w:p>';
    const grid=normalizeGrid(sheets[index],options),sections=groups(grid);
    report.sheets.push({name:sheets[index].name,sourceName:sheets[index].sourceName,rows:grid.rows,columns:grid.columns,columnWidthsTwips:grid.widths,merges:grid.merges.length,groups:sections});
    for(const section of sections){body+=gridTable(grid,section,options)+'<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="20" w:lineRule="exact"/></w:pPr><w:r><w:rPr><w:sz w:val="2"/></w:rPr><w:t></w:t></w:r></w:p>';report.tableCount++;}
  }
  const section='<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/><w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720" w:header="360" w:footer="360" w:gutter="0"/></w:sectPr>';
  const parts={
    'word/document.xml':'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="'+WORD+'"><w:body>'+body+section+'</w:body></w:document>',
    'word/styles.xml':'<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="'+WORD+'"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Microsoft YaHei" w:hAnsi="Microsoft YaHei" w:eastAsia="Microsoft YaHei"/><w:sz w:val="18"/><w:color w:val="000000"/></w:rPr></w:rPrDefault></w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style><w:style w:type="paragraph" w:styleId="GridText"><w:name w:val="SOP source cell"/><w:basedOn w:val="Normal"/></w:style></w:styles>',
    'word/settings.xml':'<?xml version="1.0" encoding="UTF-8"?><w:settings xmlns:w="'+WORD+'"><w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat></w:settings>',
    'word/_rels/document.xml.rels':'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rIdSettings" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/></Relationships>',
    '_rels/.rels':'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>',
    'docProps/core.xml':'<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>'+escape(model.title||'树脂合成SOP')+'</dc:title><dc:creator>LAB实验设计工作台</dc:creator></cp:coreProperties>',
    '[Content_Types].xml':'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>'
  };return {parts,report};
}
export async function makeGridWordSop(exp,calc={},options={}){
  const model=options.model??await (await import('./native-sop.js')).buildNativeSopModel(exp,calc,options);
  const {parts}=buildGridWordParts(model,options),Constructor=options.zipConstructor??globalThis.JSZip;
  if(!Constructor)throw new Error('本地Word导出组件尚未加载。');
  const zip=new Constructor();for(const [file,text]of Object.entries(parts))zip.file(file,text);
  return new Blob([await zip.generateAsync({type:'uint8array',compression:'DEFLATE'})],{type:MIME});
}


const MODERN_LAYOUT_ID='late-excel-38';
function modernGridModel(model){
  if(!model?.grid&&!model?.sheets?.length)throw new Error('缺少后期Excel版式的SOP网格。');
  if(model.sheets?.length){
    const sheet=model.main??model.sheets[0];
    if((sheet.maxCol??14)!==14)throw new Error('现代SOP必须使用14栏执行网格。');
    return {...model,sheets:[{...sheet,role:'SOP计划',sections:sheet.sections??model.grid?.groups??model.grid?.sections??model.sections}],exactReplay:false};
  }
  const grid=model.grid,cells=[],merges=[],rowHeights={},columnWidths=grid.cols.map(column=>number(typeof column==='object'?column.width:column)??10);
  if(columnWidths.length!==14)throw new Error('现代SOP必须使用14栏执行网格。');
  const occupied=new Map();
  for(const row of grid.rows){
    const rowNumber=number(row.index);
    if(!Number.isInteger(rowNumber)||rowNumber<1)throw new Error('SOP网格行号必须为正整数。');
    rowHeights[rowNumber]=number(row.height)??18;
    for(const item of row.cells){
      const col=number(item.col),rowSpan=number(item.rowSpan)??1,colSpan=number(item.colSpan)??1;
      if(!Number.isInteger(col)||col<1||col+colSpan-1>14||!Number.isInteger(rowSpan)||rowSpan<1||!Number.isInteger(colSpan)||colSpan<1)throw new Error('SOP单元格跨度无效。');
      for(let r=rowNumber;r<rowNumber+rowSpan;r++)for(let c=col;c<col+colSpan;c++){const key=r+':'+c;if(occupied.has(key))throw new Error('SOP合并单元格重叠：'+key);occupied.set(key,true);}
      const style=typeof item.style==='string'?grid.styles?.[item.style]??model.styles?.[item.style]:item.style;
      if(typeof item.style==='string'&&!style)throw new Error('SOP单元格样式未定义：'+item.style);
      const cell={row:rowNumber,col,value:item.value??'',displayValue:item.displayValue??item.value??'',style:style??{}};
      cells.push(cell);
      if(rowSpan>1||colSpan>1)merges.push({r1:rowNumber,c1:col,r2:rowNumber+rowSpan-1,c2:col+colSpan-1});
    }
  }
  const maxRow=Math.max(1,...grid.rows.map(row=>row.index),...merges.map(merge=>merge.r2));
  return {...model,exactReplay:false,sheets:[{name:'SOP',sourceName:'SOP',role:'SOP计划',cells,merges,rowHeights,columnWidths,maxRow,maxCol:14,sections:grid.groups??grid.sections??model.sections}]};
}
/** Convert the common modern SOP model to Word OOXML without inheriting legacy Word styles. */
export function buildModernGridWordParts(model,options={}){
  const nativeModel=modernGridModel(model);
  const result=buildGridWordParts(nativeModel,{...options,maxColumns:14,minFontPt:options.minFontPt??9});
  result.report.contentMode='modern-design';
  result.report.layoutAuthority=MODERN_LAYOUT_ID;
  result.report.inputSourceType=model.sourceType??model.sourceKind??null;
  result.report.tableWidthTwips=options.tableWidthTwips??15398;
  return result;
}
/** Generate the optional Word output from the same late Excel grid as preview and XLSX. */
export async function makeModernWordSop(exp,calc={},options={}){
  const model=options.model??await (await import('./modern-sop-layout.js')).buildModernSopModel(exp,calc,options);
  const {parts}=buildModernGridWordParts(model,options),Constructor=options.zipConstructor??globalThis.JSZip;
  if(!Constructor)throw new Error('本地Word导出组件尚未加载。');
  const zip=new Constructor();for(const [file,text]of Object.entries(parts))zip.file(file,text);
  return new Blob([await zip.generateAsync({type:'uint8array',compression:'DEFLATE'})],{type:MIME});
}
