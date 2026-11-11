import {layoutDoc} from './layout.js';
import {evaluateDoc} from './evaluate.js';
import {formatCellValue,formatValue} from './display.js';
import {escapeTemplate,parseExpr,evaluateExpr} from './expr.js';
import {chargeBasisMaterial} from './generated-material-text.js';
import {sopMaterialName} from './materials.js';
import {readableExpression} from './language.js';
import {documentTokens} from './document-tokens.js';
import {apparatusList} from './apparatus.js';
const T=text=>({t:escapeTemplate(String(text??''))});
const named=(id,text)=>({type:'section',id,text});

export function paperChargeBasis(charge,charges,vars,values,steps=[]){
 const amount=charge.displayBasis??charge.amount??{},mode=charge.displayMode??amount.mode,base=charges.find(x=>[x.id,x.as,x.materialId].includes(amount.of)),seen=new Set(),dependencies=[];
 const collect=expr=>{for(const match of String(expr??'').matchAll(/\[([^\]]+)\]/g)){if(seen.has(match[1]))continue;seen.add(match[1]);const v=vars.find(v=>v.key===match[1]);if(v){dependencies.push(v);collect(v.expr);}}};collect(charge.amount?.value);collect(charge.sourceVar?'['+charge.sourceVar+']':'');
 const valueOf=v=>{const owner=steps.find(s=>s.charges?.some(c=>c.id===charge.id)),param=Object.entries(owner?.paramBindings??{}).find(([,key])=>key===v.key)?.[0],value=param&&owner.args[param]!==undefined?owner.args[param]:values.get(v.key);try{return typeof value==='number'?value:evaluateExpr(parseExpr(String(value)),r=>values.get(r.key));}catch{return value;}};
 const solid=dependencies.find(v=>v.expr===undefined&&/固含/.test(v.label??v.key)),ratio=dependencies.find(v=>v.unit==='mL/g'||v.role==='thfPerProduct'),precipitation=dependencies.find(v=>v.role==='precipitationRatio');
 if(mode==='solidFill')return solid?'固含补足 '+formatValue(valueOf(solid)*100,'0.0')+' %':'按目标固含补足';
 if(mode==='perProduct')return ratio?formatValue(valueOf(ratio),'0.##').replace(/\.$/,'')+' mL / g 预计产品':'按预计产品计算';
 if(mode==='volume'&&precipitation)return formatValue(valueOf(precipitation),'0.##').replace(/\.$/,'')+' 倍 '+sopMaterialName(chargeBasisMaterial(charge,charges,vars)??{name:'稀释溶剂'});
 let value=amount.value;try{if(typeof value!=='number')value=evaluateExpr(parseExpr(String(value)),r=>values.get(r.key));}catch{value='待指定';}
 return ({mass:'质量基准',volume:'体积基准',mmol:'物质的量',expr:'参数联动'})[mode]??(['eq','molpct'].includes(mode)?formatValue(value,mode==='eq'?'0.00':'0.0')+' '+(mode==='eq'?'eq':'mol%')+' / '+(base?.name??readableExpression(amount.of??'基准')):'按计划投料');
}

/** Add the paper's semantic sections without changing any record/variable key.
 * Formula compilation and record import continue to use the kernel document. */
export function modernDocument(doc,design){
 const frozen=design.frozenRevisions?.find(r=>r.uid===design.revisionUid),executed=!!(frozen||design.executionUid),version=executed?'执行版 v'+design.revision:'草稿';
 doc.presentation={schema:'lab-paper/1',tokens:documentTokens,version,draft:!executed,code:design.code,title:design.title??'',date:design.date??'',purpose:doc.sheets.find(s=>s.name==='SOP')?.blocks.find(b=>b.id==='purpose')?.text??escapeTemplate(design.question||design.purpose||'')};
 for(const sheet of doc.sheets.filter(s=>!s.hidden&&['SOP','批记录','检测记录','参数'].includes(s.name))){
  const original=[...sheet.blocks],apparatus=apparatusList(design);
  if(apparatus.length&&['SOP','批记录'].includes(sheet.name))original.unshift({type:'table',id:'apparatus-'+sheet.name,title:'容器与装置',header:[{key:'名称',label:'容器 / 装置',span:3},{key:'类型',label:'类型',span:2},{key:'容积',label:'标称容积 / mL',span:2},{key:'用途',label:'使用位置',span:2},{key:'备注',label:'备注',span:sheet.widths.length-9}],rows:apparatus.map(a=>({id:a.id,cells:[{...T(a.name),span:3},{...T(({reaction:'反应瓶',addition:'滴液漏斗','water-separator':'分水器',receiver:'接收瓶',filter:'过滤器',other:'其他装置'})[a.type]??'其他装置'),span:2},{...a.nominalVolumeMl==null?T('未记录'):{v:a.nominalVolumeMl,fmt:'0.###'},span:2},{...T([(design.reactionVesselId??design.reactionApparatusId??(design.flaskMl>0?'legacy-flask':null))===a.id?'本批反应容器':'',...doc.modular.steps.filter(s=>(s.vesselId??s.apparatusId)===a.id).map(s=>s.title)].filter(Boolean).join('；')||'备用'),span:2},{...T(a.notes),span:sheet.widths.length-9}]}))});
  const ids=new Map(original.map(b=>[b.id,b])),sections=[];let number=0;
  const section=(label,blocks)=>{blocks=blocks.filter(Boolean);if(!blocks.length)return;const id='paper-'+sheet.name+'-'+(++number);sections.push({id,title:String(number).padStart(2,'0')+'　'+label,blockIds:blocks.map(b=>b.id)});};
  if(sheet.name==='SOP'){
   section('投料表',[ids.get('sop-charges')]);
   section('条件与参数',original.filter(b=>b.id.startsWith('params-')||b.id==='apparatus-SOP'));
   if(!sections.some(s=>s.title.includes('条件与参数')))section('条件与参数',[{type:'note',id:'empty-conditions',text:'尚未设置执行条件。'}]);
   section('操作流程',[ids.get('sop-flow')]);section('取样与检测',[ids.get('sop-samples'),ids.get('sop-sample-note')]);
  }else for(const block of original.filter(b=>!['title','section'].includes(b.type)&&b.id!=='record-deviations'))section(block.title??(block.id==='technical-vars'?'物料常数与计算参数':block.id==='execution-signature'?'签署':'执行说明'),[block]);
  const width=sheet.widths.length,prefix='paper-'+sheet.name;
  const meta=[{type:'note',id:prefix+'-topline',text:design.code.split('-')[0]+' 系列 · '+sheet.name+' · '+(executed?'受控执行文件':'受控文件草稿')},{type:'title',id:prefix+'-title',text:design.code+' · '+(sheet.name==='SOP'?'合成操作规程':sheet.name)},{type:'note',id:prefix+'-subtitle',text:escapeTemplate(design.title??'')},{type:'table',id:prefix+'-meta',rows:[{id:'identity',cells:[{...T('批号　'+design.code),span:Math.floor(width/3)},{...T('版本　'+version),span:Math.floor(width/3)},{...T('日期　'+(design.date||'____________')),span:width-2*Math.floor(width/3)}]},{id:'signatures',cells:[{...T('编制　____________'),span:Math.floor(width/3),style:'signature'},{...T('执行　____________'),span:Math.floor(width/3),style:'signature'},{...T('复核　____________'),span:width-2*Math.floor(width/3),style:'signature'}]},{id:'purpose',cells:[{t:'目的　'+doc.presentation.purpose,span:width}]}]}];
  const content=[];
  for(const section of sections){content.push(named(section.id,section.title));for(const id of section.blockIds){let block=ids.get(id);if(!block&&id==='empty-conditions')block={type:'note',id,text:'尚未设置执行条件。'};if(block){if(section.blockIds.length===1&&block.title){block.caption=block.title;delete block.title;}content.push(block);}}}
  // Keep nonsection notes (e.g. the parent experiment comparison) visible too.
  const rest=sheet.name==='SOP'?original.filter(b=>b.type==='note'&&!['purpose','sop-sample-note'].includes(b.id)):[];
  sheet.blocks=[...meta,...rest,...content];sheet.paperSections=sections;
 }
 return doc;
}

/** Pure shared layout: semantic sections and evaluated cells with stable keys.
 * It is the sole paper layout consumed by screen, print and XLSX. Numeric values
 * and formulas remain distinct, so a modern export is still a live workbook. */
export function buildDocumentLayout(model){
 const doc=model.doc??model,layout=model.layout??layoutDoc(doc),values=model.values??evaluateDoc(doc);
 const sheets=layout.sheets.map(sheet=>{
  const cells=sheet.cells.map(p=>({key:p.key,role:p.cell.input?'input':p.cell.var||p.cell.f?'calc':p.style==='label'?'label':'text',style:p.style,fmt:p.cell.fmt??doc.vars.find(v=>v.key===p.cell.var)?.fmt,span:p.cell.span??1,rows:p.cell.rows??1,value:values.get(p.fullAddr),text:formatCellValue(doc,p,values.get(p.fullAddr),layout),placement:p}));
  const sections=(sheet.paperSections??[]).map(section=>({...section,blocks:section.blockIds.map(id=>{const block=sheet.blocks.find(b=>b.id===id),selected=cells.filter(c=>c.placement.block===id),rows=[...new Set(selected.map(c=>c.placement.row))].map(row=>selected.filter(c=>c.placement.row===row));return {...block,rows,cells:selected};})}));
  return {...sheet,cells,sections};
 });
 return {schema:'lab-paper/1',meta:doc.presentation??{},tokens:documentTokens,doc,layout,values,sheets};
}
