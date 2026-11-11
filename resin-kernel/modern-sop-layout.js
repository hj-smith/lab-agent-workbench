/** @deprecated Archived v2 compatibility layout. Unified preview/Excel/PDF use
 * kernel/document-layout.js. Preserve this adapter only for archived integrations
 * and explicit source replay; it is not the default unified export pipeline. */
// The later R100-38 Excel frame is the shared execution layout.
// Chemical content always comes from the selected route or the current design.
import {buildNativeSopModel,makeNativeSopXlsx} from './native-sop.js';
import {buildSopContent} from './sop-content.js';
import {actualWorkupRows} from './workup-actual.js';
import {findRecordedCharge} from './batch-record.js';
import {renameSopSampleReferences,sopProvenanceFacts,detectSopSourceConflicts} from './sop-source-validation.js';

const MIME='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
const list=x=>Array.isArray(x)?x:[];
const clone=x=>x==null?x:JSON.parse(JSON.stringify(x));
const present=x=>x!=null&&x!==''&&!(typeof x==='number'&&!Number.isFinite(x));
const numeric=x=>x==null||x===''||typeof x==='boolean'||!Number.isFinite(Number(x))?null:Number(x);
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
const addr=(row,col)=>{let name='';for(let n=col;n;n=Math.floor((n-1)/26))name=String.fromCharCode(65+(n-1)%26)+name;return name+row;};
const location=cell=>{const match=String(cell).match(/^([A-Z]+)(\d+)$/);if(!match)return null;return {row:+match[2],col:[...match[1]].reduce((n,c)=>n*26+c.charCodeAt(0)-64,0)};};
const border={left:{style:'thin',color:{rgb:'FFB7C9D6'}},right:{style:'thin',color:{rgb:'FFB7C9D6'}},top:{style:'thin',color:{rgb:'FFB7C9D6'}},bottom:{style:'thin',color:{rgb:'FFB7C9D6'}}};
const style=(background='FFFFFF',foreground='20364F',bold=false,size=9,center=false)=>({font:{name:'Microsoft YaHei',size,bold,color:{rgb:'FF'+foreground}},fill:{type:'solid',foreground:{rgb:'FF'+background}},alignment:{horizontal:center?'center':'left',vertical:'center',wrapText:true},border:clone(border),numFmt:'General'});
export const MODERN_SOP_STYLES={
  title:style('1E3F66','FFFFFF',true,18,true),
  section:style('1E3F66','FFFFFF',true,10),
  header:style('117A86','FFFFFF',true,9,true),
  label:style('FFFFFF','20364F',false,9),
  input:style('FFF2CC','C00000',false,9),
  computed:style('DCE6F1','24476E',true,9),
  body:style('FFFFFF','20364F',false,9),
  keyword:style('E8F2F5','24476E',true,9,true),
  number:style('E8F2F5','24476E',true,9,true),
  muted:style('F4F7FA','526579',false,9),
  samplingHeader:style('117A86','FFFFFF',true,9,true),
  note:style('FFF4CC','7F6000',false,9)
};
export const MODERN_SOP_COLUMN_WIDTHS=[11,10,8,12,10,8,12,10,8,12,10,8,15,12];
export const MODERN_SOP_SAMPLING_HEADERS=['样品','阶段/时间','即时处理','GPC','HPLC / NMR','称量','制样/过滤','关键比较','物料记录','重复性','结果','比较口径','编号','备注'];

function display(value,format='General'){
  if(value==null)return '';
  if(typeof value!=='number')return String(value);
  const f=String(format||'General').split(';')[0],decimals=f.match(/\.(0+)/)?.[1]?.length??0;
  if(f.includes('%'))return (value*100).toFixed(decimals)+'%';
  if(/^[#0,]+(?:\.0+)?$/.test(f))return value.toFixed(decimals);
  return Math.abs(value)>1e6||String(value).length>10?Number(value.toPrecision(8)).toString():String(value);
}
function textUnits(text){return [...String(text??'')].reduce((total,c)=>total+(/[\u2E80-\u9FFF\uFF00-\uFFEF]/.test(c)?1:/[il.,:;!|' ]/.test(c)?.28:/[MW@%]/.test(c)?.8:.54),0);}
function newSheet(name='SOP'){
  return {name,sourceName:name,role:'main',maxRow:0,maxCol:14,cellMap:{},cells:[],merges:[],columnWidths:[...MODERN_SOP_COLUMN_WIDTHS],rowHeights:{},defaultRowHeight:24,print:{orientation:'landscape',paperSize:9,fitToWidth:1,fitToHeight:0},showGridLines:false};
}
function setCell(sheet,row,col,value,role='body',span=1,extra={}){
  const cell=addr(row,col),cellStyle=clone(MODERN_SOP_STYLES[role]??MODERN_SOP_STYLES.body),format=extra.numFmt??'General';
  cellStyle.numFmt=format;
  const item={cell,row,col,value,displayValue:extra.displayValue??display(value,format),formula:extra.formula??null,style:cellStyle,modernRole:role,...extra};
  sheet.cellMap[cell]=item;sheet.maxRow=Math.max(sheet.maxRow,row);
  if(span>1)sheet.merges.push({r1:row,c1:col,r2:row,c2:col+span-1,range:cell+':'+addr(row,col+span-1)});
  return item;
}
function fullRow(sheet,row,value,role='body',height=24){setCell(sheet,row,1,value,role,14);sheet.rowHeights[row]=height;return row+1;}
function parameterSlot(index){const groups=[{label:1,value:2,unit:3},{label:4,value:5,unit:6},{label:7,value:8,unit:9},{label:10,value:11,unit:12},{label:13,value:14,unit:null}];return {...groups[index%5],row:5+Math.floor(index/5)};}
function adjustHeights(sheet){
  for(let row=1;row<=sheet.maxRow;row++){
    let height=sheet.rowHeights[row]??24;
    for(const cell of Object.values(sheet.cellMap).filter(c=>c.row===row)){
      const merge=sheet.merges.find(m=>m.r1===row&&m.c1===cell.col);
      const width=sheet.columnWidths.slice(cell.col-1,merge?.c2??cell.col).reduce((a,b)=>a+b,0)*5.25-8;
      const font=cell.style?.font?.size??9;
      if(typeof cell.value==='number'&&row>=5&&row<=10){
        cell.style.alignment.wrapText=false;cell.style.alignment.shrinkToFit=true;
        continue;
      }
      const lines=String(cell.displayValue??'').split('\n').reduce((a,line)=>a+Math.max(1,Math.ceil(textUnits(line)*font/Math.max(width,8))),0);
      height=Math.max(height,lines*font*1.4+8);
    }
    sheet.rowHeights[row]=Math.ceil(height*2)/2;
  }
  sheet.cells=Object.values(sheet.cellMap).sort((a,b)=>a.row-b.row||a.col-b.col);
  return sheet;
}
function finishGrid(sheet){
  const rows=[];
  for(let row=1;row<=sheet.maxRow;row++){
    const cells=[];
    for(let col=1;col<=14;col++){
      const merge=sheet.merges.find(m=>row>=m.r1&&row<=m.r2&&col>=m.c1&&col<=m.c2);
      if(merge&&(row!==merge.r1||col!==merge.c1))continue;
      const cell=sheet.cellMap[addr(row,col)]??{cell:addr(row,col),row,col,value:'',displayValue:'',style:clone(MODERN_SOP_STYLES.body),modernRole:'body'};
      cells.push({...cell,colSpan:merge?merge.c2-merge.c1+1:1,rowSpan:merge?merge.r2-merge.r1+1:1,styleName:cell.modernRole??'body'});
    }
    rows.push({index:row,height:sheet.rowHeights[row],cells});
  }
  const operation=sheet.cells.find(c=>c.col===1&&/^B[.、\s]/.test(String(c.value??'')))?.row;
  const sampling=sheet.cells.find(c=>c.col===1&&/^D[.、\s]/.test(String(c.value??'')))?.row;
  const groups=operation&&sampling?[{role:'parameters',start:1,end:operation-1,headers:4},{role:'operations',start:operation,end:sampling-1,headers:1},{role:'sampling',start:sampling,end:sheet.maxRow,headers:2}]:[{role:'grid',start:1,end:sheet.maxRow,headers:4}];
  return {cols:[...sheet.columnWidths],rows,styles:MODERN_SOP_STYLES,merges:sheet.merges,groups};
}
function sourceParameterRows(main){
  const output=[];
  for(let index=0;index<30;index++){
    const slot=parameterSlot(index),label=main.cellMap[addr(slot.row,slot.label)],value=main.cellMap[addr(slot.row,slot.value)],unit=slot.unit?main.cellMap[addr(slot.row,slot.unit)]:null;
    if(!label&&!value)continue;
    output.push({id:'source-'+addr(slot.row,slot.value),label:String(label?.value??''),value:value?.value??null,displayValue:value?.displayValue??'',unit:unit?.value??null,numFmt:value?.style?.numFmt??'General',formula:value?.formula??null,valueKind:value?.formula?'source-calculated-plan':'source-plan-input',sourceCell:{sheet:main.sourceName,cell:addr(slot.row,slot.value)},slot});
  }
  return output;
}
function modernNativeSheet(native,exp){
  const source=native.main,sheet=newSheet(source.name),sampleStart=source.cells.find(c=>c.col===1&&/^D[.、\s]/.test(String(c.value??'')))?.row??22+native.rowShift;
  sheet.sourceName=source.sourceName;sheet.merges=clone(source.merges).filter(m=>m.c1<=14).map(m=>({...m,c2:Math.min(m.c2,14),range:addr(m.r1,m.c1)+':'+addr(m.r2,Math.min(m.c2,14))}));
  sheet.maxRow=source.maxRow;sheet.rowHeights=clone(source.rowHeights);
  for(const old of source.cells.filter(c=>c.col<=14)){
    let role='body';
    if(old.row===1)role='title';
    else if(old.row===2)role=old.col===1?'header':old.col===2?'input':'computed';
    else if(old.row===3||old.row===11||old.row===sampleStart)role='section';
    else if(old.row===4||old.row===sampleStart+1)role='header';
    else if(old.row>=5&&old.row<=10){role=[2,5,8,11,14].includes(old.col)?old.formula||old.originalFormula||native.calcOwnedCells.includes(source.name+'!'+old.cell)?'computed':'input':'label';}
    else if(old.row>=12&&old.row<sampleStart){role=[1,8].includes(old.col)?'number':[2,9].includes(old.col)?'keyword':'body';}
    else if(old.row>sampleStart+1){role='body';}
    if(old.col===1&&source.merges.some(m=>m.r1===old.row&&m.c1===1&&m.c2===14)&&old.row>sampleStart+1)role='note';
    const item=setCell(sheet,old.row,old.col,old.value,role,1,{formula:old.formula??null,numFmt:old.style?.numFmt??'General',displayValue:old.displayValue??display(old.value,old.style?.numFmt),sourceCell:old.sourceCell??old.cell,originalValue:old.originalValue});
    item.style.font.size=role==='title'?18:role==='section'?10:9;
  }
  sheet.rowHeights[1]=34;sheet.rowHeights[2]=28;sheet.rowHeights[3]=28;sheet.rowHeights[4]=24;
  // Older Excel files used a whole-row objective banner. The current frame has
  // a stable batch code field and a separate condition summary.
  if(source.cellMap.A2&&source.merges.some(m=>m.r1===2&&m.c1===1&&m.c2===14)){
    const summary=source.cellMap.A2.displayValue??source.cellMap.A2.value;
    for(const key of Object.keys(sheet.cellMap))if(sheet.cellMap[key].row===2)delete sheet.cellMap[key];
    sheet.merges=sheet.merges.filter(m=>m.r1!==2);
    setCell(sheet,2,1,'批次编号','header');
    setCell(sheet,2,2,native.preserveSourcePlan?native.oldCode:exp.code??exp.id,'input');
    setCell(sheet,2,3,summary,'computed',12);
  }
  for(let row=5;row<=10;row++)sheet.rowHeights[row]=row===7?38:30;
  for(let row=12;row<sampleStart;row++)sheet.rowHeights[row]=Math.max(48,Math.min(sheet.rowHeights[row]??56,56));
  if(exp.sopTitle?.trim())sheet.cellMap.A1.value=exp.sopTitle.trim(),sheet.cellMap.A1.displayValue=exp.sopTitle.trim(),sheet.cellMap.A1.formula=null;
  return adjustHeights(sheet);
}
function quenchBasisUnknown(plan){return present(plan.quenchEq)&&!sopProvenanceFacts({plan}).quenchEqBasisConfirmed;}
function genericParameters(content,exp,calc,sourcePreserve){
  const p=exp.plan??{},parameters=[],charges=content.plannedCharges;
  if(sourcePreserve){
    for(const charge of charges)if(present(charge.amount))parameters.push({id:charge.id,label:charge.name+(charge.stage==='initial'?'（初始）':charge.stage==='acid carrier'?'（载酸）':charge.stage==='monoA carrier'?'（载单体A）':charge.stage==='neutralization'?'（终止）':charge.stage==='neutralization-carrier'?'（终止载液）':''),value:charge.amount,unit:charge.unit,numFmt:typeof charge.amount==='number'?'0.00':'General',valueKind:'source-planned-charge',scope:charge.scopeLabel});
    const conditions=[['总单体A/单体B摩尔比',p.ratio,'mol/mol'],['反应内温',p.temp,'°C'],['滴加时间',p.feedHours,'h'],['反应时长',p.holdHours,'h'],['催化剂/单体B（原记录）',p.catalystMolPct,'mol%'],['反应投料固含',p.solidPct,'wt%'],['终止剂用量（原文标记）',p.quenchEq,'eq'],['终止/转料温度',p.quenchTemp,'°C']];
    for(const [label,value,unit]of conditions)if(present(value))parameters.push({id:'source-'+label,label,value,unit,numFmt:'0.00',valueKind:'source-plan-input'});
    if(p.timeBasis==='endpoint')parameters.push({id:'source-time-basis',label:'反应终点控制',value:p.stopCriterion??p.endpointTarget??'监测终点（阈值待确认）',unit:null,valueKind:'source-plan-input'});
  }else{
    for(const row of content.parameters)parameters.push({...row,numFmt:typeof row.value==='number'?'0.00':'General'});
    for(const charge of charges)if(present(charge.amount))parameters.push({id:charge.id,label:charge.name+(charge.scopeLabel&&charge.scopeLabel!=='batch'?'（'+charge.scopeLabel+'）':''),value:charge.amount,unit:charge.unit,numFmt:typeof charge.amount==='number'?'0.00':'General',valueKind:charge.valueKind});
  }
  // Unknown source equivalence bases remain unknown, including for a derived design.
  if(quenchBasisUnknown(p))for(const row of parameters)if(/终止剂.*投料酸|终止名义酸份额/.test(row.label))row.label=row.label.replace('终止剂/投料酸','终止剂当量（基准待确认）').replace('终止名义酸份额','终止酸份额（基准待确认）');
  return parameters;
}
function keyword(step){
  if(step.isHeading)return '';
  const title=String(step.title??'');
  if(title&&!/^(合成|后处理)\s*\d+$/.test(title)&&title!=='custom')return title;
  const text=String(step.text??'');
  if(/干燥/.test(text))return '干燥';
  if(/打浆/.test(text))return '打浆 / 过滤';
  if(/沉降/.test(text))return '沉降 / 过滤';
  if(/监控|监测|取样/.test(text))return '过程监测';
  if(/滴加|滴完/.test(text))return '滴加';
  if(/停止反应|终止|中和|稀释/.test(text))return '终止 / 稀释';
  if(/升温/.test(text))return '升温 / 溶解';
  if(/溶解.*直接加入/.test(text))return '配制 / 加料';
  if(/加入|投料/.test(text))return '投料';
  return step.type&&step.type!=='custom'?step.type:'操作';
}
function stepItems(content){
  let number=0;
  return content.steps.map(step=>({...step,displayNo:step.isHeading?'':content.sourcePreserve?(step.sourceNo??++number):++number,keyword:keyword(step)}));
}
function placeSteps(sheet,row,left,right){
  setCell(sheet,row,1,'B. 树脂合成与过程控制','section',7);
  setCell(sheet,row,8,'C. 后处理与分支操作','section',7);
  sheet.rowHeights[row]=26;row++;
  const count=Math.max(left.length,right.length,1);
  for(let index=0;index<count;index++,row++){
    for(const [steps,col]of [[left,1],[right,8]]){
      const step=steps[index];
      if(step?.isHeading){setCell(sheet,row,col,step.text||step.title,'keyword',7);continue;}
      setCell(sheet,row,col,step?.displayNo??'','number');
      setCell(sheet,row,col+1,step?.keyword??'','keyword',2);
      setCell(sheet,row,col+3,step?.text??'','body',4);
    }
    sheet.rowHeights[row]=48;
  }
  return row;
}
function samplingForGeneric(content,exp){
  if(content.sampling?.allRows?.length)return clone(content.sampling);
  const p=exp.plan??{},method=p.sampling??{};
  let text=content.samplingPlan??'';
  const known=present(method.analyte)||present(method.solvent)||present(text);
  if(!known)return {title:'D. 取样与检测计划',columns:[...MODERN_SOP_SAMPLING_HEADERS],rows:[],allRows:[],planText:text};
  const row=Array(14).fill('');
  row[0]='反应液';row[1]=method.frequency??'节点待设计';row[3]=method.analyte??'';row[6]=[method.solvent,method.teaPercent!=null?method.teaPercent+'% TEA（浓度口径待确认）':''].filter(Boolean).join('；');row[13]=text||'按本路线监测步骤执行；缺失条件待补充';
  return {title:'D. 取样与检测计划',columns:[...MODERN_SOP_SAMPLING_HEADERS],rows:[{values:row}],allRows:[{values:[...MODERN_SOP_SAMPLING_HEADERS]},{values:row}],planText:text};
}
function placeSampling(sheet,row,sampling){
  const start=row;row=fullRow(sheet,row,String(sampling.title??'D. 取样与检测计划').replace(/^[A-C][.、]\s*/,'D. '),'section',26);
  const headers=sampling.columns?.length?sampling.columns:MODERN_SOP_SAMPLING_HEADERS;
  for(let col=1;col<=14;col++)setCell(sheet,row,col,headers[col-1]??'','samplingHeader');
  sheet.rowHeights[row]=32;row++;
  const rows=list(sampling.rows);
  for(const record of rows){
    for(let col=1;col<=14;col++){
      const c=record.cells?.[col-1];setCell(sheet,row,col,record.values?.[col-1]??c?.value??'','body',1,{displayValue:c?.displayValue??display(record.values?.[col-1]??c?.value??'')});
    }
    sheet.rowHeights[row]=44;row++;
  }
  if(!rows.length)row=fullRow(sheet,row,'本路线尚未建立完整取样矩阵；请在工作台补充节点、即时处理、方法、制样、编号及比较口径。','muted',26);
  return {row,start};
}
function genericSheet(content,exp,calc,sourcePreserve){
  const sheet=newSheet('SOP'),parameters=genericParameters(content,exp,calc,sourcePreserve),code=content.execution.code??exp.code??exp.id;
  const title=exp.sopTitle?.trim()||code+' 树脂合成SOP';
  fullRow(sheet,1,title,'title',34);
  setCell(sheet,2,1,'批次编号','header');setCell(sheet,2,2,code,'input');
  const p=exp.plan??{},time=p.timeBasis==='endpoint'?'按监测终点停止':present(p.holdHours)?p.holdHours+' h（'+({ 'after-feed':'滴后保温','at-temperature':'达温计时','total-from-feed-start':'从滴加起计时'}[p.timeBasis]??'计时口径待明确')+'）':'反应时长待明确';
  setCell(sheet,2,3,[present(p.temp)?p.temp+' °C':null,time,content.branchContext?.branches?.length>1?'保留所选路线完整分支操作':null].filter(Boolean).join('；'),'computed',12);sheet.rowHeights[2]=30;
  fullRow(sheet,3,'A. 批次设定与投料计算','section',26);
  for(const group of [{label:1,value:2,unit:3},{label:4,value:5,unit:6},{label:7,value:8,unit:9},{label:10,value:11,unit:12},{label:13,value:14,unit:null}]){
    setCell(sheet,4,group.label,'参数','header');setCell(sheet,4,group.value,'数值','header');if(group.unit)setCell(sheet,4,group.unit,'单位','header');
  }
  sheet.rowHeights[4]=24;
  const mainParameterCount=Math.min(30,Math.max(5,Math.ceil(parameters.length/5)*5));
  for(let index=0;index<mainParameterCount;index++){
    const item=parameters[index],slot=parameterSlot(index),unit=item?.unit??'',label=(item?.label??'')+(!slot.unit&&unit?'（'+unit+'）':'');
    setCell(sheet,slot.row,slot.label,label,'label');setCell(sheet,slot.row,slot.value,item?.value??'',/calculation|calculated/.test(item?.valueKind??'')?'computed':'input',1,{numFmt:item?.numFmt??'General',displayValue:typeof item?.value==='number'?display(item.value,item.numFmt):item?.displayValue??display(item?.value,item?.numFmt)});if(slot.unit)setCell(sheet,slot.row,slot.unit,unit,'label');sheet.rowHeights[slot.row]=30;
  }
  let row=5+mainParameterCount/5;
  for(let index=30;index<parameters.length;index+=5){
    for(let col=0;col<5;col++){
      const item=parameters[index+col],slot=parameterSlot(col),label=(item?.label??'')+(!slot.unit&&item?.unit?'（'+item.unit+'）':'');
      setCell(sheet,row,slot.label,label,'label');setCell(sheet,row,slot.value,item?.value??'',/calculation|calculated/.test(item?.valueKind??'')?'computed':'input',1,{numFmt:item?.numFmt??'General',displayValue:typeof item?.value==='number'?display(item.value,item.numFmt):item?.displayValue??display(item?.value,item?.numFmt)});if(slot.unit)setCell(sheet,row,slot.unit,item?.unit??'','label');
    }
    sheet.rowHeights[row]=30;row++;
  }
  if(content.ingredients)row=fullRow(sheet,row,'原料：'+content.ingredients,'muted',26);
  if(content.equipment&&!content.execution.equipmentEmbeddedInStep)row=fullRow(sheet,row,'装置：'+content.equipment,'muted',26);
  if(content.vessels)row=fullRow(sheet,row,'容器：'+content.vessels,'muted',26);
  const items=stepItems(content),left=items.filter(s=>s.phase!=='postprocess'),right=items.filter(s=>s.phase==='postprocess'),operationStart=row;
  row=placeSteps(sheet,row,left,right);
  const sampling=samplingForGeneric(content,exp),placed=placeSampling(sheet,row,sampling);row=placed.row;
  if(quenchBasisUnknown(p))row=fullRow(sheet,row,'原资料终止剂的 eq 基准未明确；保留原文标记和称量，未将其解释为相对投料酸的确定当量。','note',26);
  if(content.safetyNotes)row=fullRow(sheet,row,'注意事项：'+content.safetyNotes,'note',26);
  if(content.samplingPlan&&content.samplingPlan!==sampling.planText)row=fullRow(sheet,row,'补充取样计划：'+content.samplingPlan,'note',26);
  for(const [key,value]of Object.entries(p.customFields??{}))row=fullRow(sheet,row,key+'：'+(value&&typeof value==='object'?value.value??value.text??JSON.stringify(value):value),'note',26);
  sheet.maxRow=row-1;adjustHeights(sheet);
  return {sheet,parameters,left,right,sampling,operationStart,samplingStart:placed.start};
}
function batchRecordSheet(content, exp, includeActual, calc = {}, materials = []) {
  const sheet = newSheet('批记录'), actual = includeActual ? exp.actual ?? {} : {};
  sheet.role = 'batch';
  let row = fullRow(sheet, 1, (exp.code ?? exp.id) + ' 批记录', 'title', 34);
  row = fullRow(sheet, row, includeActual ? '本轮已录入数据；空白项留待实验中填写。' : '空白执行记录；实验中填写实际称量、时刻与观察。', 'muted');
  const section = title => { row = fullRow(sheet, row, title, 'section', 26); };
  const line = (values, spans, role = 'body', height = 30) => {
    let col = 1;
    values.forEach((value, index) => { setCell(sheet, row, col, value ?? '', role, spans[index]); col += spans[index]; });
    sheet.rowHeights[row++] = height;
  };
  section('A. 投料称量');
  const chargeSpans = [1, 3, 1, 1, 1, 2, 1, 1, 1, 2];
  line(['序号', '物料 / 分支', '计划量', '单位', '实际量', '批号 / 纯度', '称量人', '复核', '时间', '偏差 / 备注'], chargeSpans, 'header');
  const recorded = list(actual.charges), used = new Set();
  const actualAmount = r => r.mass ?? r.massG ?? r.actualMass ?? r.actual ?? r.amount ?? r.value;
  const writeCharge = (charge, index, record) => line([index + 1, charge.name ?? charge.materialId,
    charge.amount ?? record?.plannedMass ?? record?.planned ?? record?.plan, charge.unit ?? record?.unit,
    record ? actualAmount(record) : '', [record?.lot ?? record?.batch, record?.purity].filter(present).join(' / '),
    record?.operator, record?.reviewer, record?.time ?? record?.recordedAt,
    [charge.notes, record?.notes ?? record?.note].filter(present).join('；')], chargeSpans);
  content.plannedCharges.forEach((charge, index) => {
    const match = findRecordedCharge(charge, recorded, used);
    if (match >= 0) used.add(match);
    writeCharge(charge, index, recorded[match]);
  });
  let chargeCount = content.plannedCharges.length;
  recorded.forEach((record, index) => { if (!used.has(index)) writeCharge({name: record.name ?? record.material ?? record.item ?? record.materialId, unit: record.unit}, chargeCount++, record); });
  if (!chargeCount) line(['', '', '', '', '', '', '', '', '', ''], chargeSpans);
  section('B. 过程记录');
  const logSpans = [2, 1, 1, 1, 1, 2, 5, 1];
  line(['时刻', '相对 h', '内温 ℃', '外温 ℃', 'rpm', '样品编号', '现象 / 事件', '记录人'], logSpans, 'header');
  const logs = list(actual.logs);
  for (let index = 0; index < Math.max(12, logs.length); index++) {
    const log = logs[index] ?? {};
    line(typeof log === 'string' ? ['', '', '', '', '', '', log, ''] : [log.clock ?? log.date, log.relativeHours ?? log.time,
      log.temp, log.outerTemp, log.rpm, log.sampleId, [log.stage, log.observation ?? log.notes ?? log.text ?? log.note].filter(present).join('；'), log.operator], logSpans, 'body', 32);
  }
  section('C. 后处理与样品');
  const postSpans = [2, 6, 2, 2, 2];
  line(['分支 / 步骤', '计划操作', '实际量 / 样品', '现象', '记录人'], postSpans, 'header');
  for (const step of content.steps.filter(s => s.phase === 'postprocess')) line([step.branchScope ?? '', step.text, '', '', ''], postSpans, 'body', 38);
  if (includeActual) for (const sample of list(exp.samples)) line([sample.branch ?? sample.kind, sample.name ?? sample.id,
    sample.mass ?? '', sample.appearance ?? sample.notes ?? sample.note, ''], postSpans);
  section('D. 结果');
  for (const label of ['产品质量 / g', '分离收率 / %', '外观', '备注']) line([label, label === '备注' ? actual.notes : ''], [3, 11]);
  if (includeActual) {
    if (exp.actual?.workup && exp.plan?.workupBranches?.length) {
      section('E. 实际分料与后处理回算');
      line(['分支 / 项目','计划量','按实际分料重算 / 实测','单位','依据 / 备注'],[3,2,3,1,5],'header');
      for (const r of actualWorkupRows(exp,calc,materials)) line([(r.branchScope??'母批')+' · '+r.label,r.plannedAmount,r.amount,r.unit,[r.provisional?'暂估，未满足执行前提':'',r.note,r.actualRatio!=null?'实投反算 '+r.actualRatio+' '+r.ratioUnit+'；'+(r.ratioNote??''):'',r.perCycleAmount!=null?'每次 '+r.perCycleAmount+'，'+r.cycles+' 次':''].filter(Boolean).join('；')],[3,2,3,1,5]);
    }
    for (const parameter of list(actual.parameters)) line([parameter.name ?? parameter.item ?? parameter.parameter, [parameter.value ?? parameter.amount, parameter.unit, parameter.scope].filter(present).join(' ')], [3, 11]);
    for (const sample of list(exp.samples)) for (const metric of list(sample.metrics)) line([sample.name ?? sample.id, [metric.name, metric.value, metric.unit, metric.method, metric.condition, metric.notes ?? metric.note].filter(present).join('；')], [3, 11]);
  }
  sheet.maxRow = row - 1;
  return adjustHeights(sheet);
}

export async function buildModernSopModel(exp,calc={},opts={}){
  const facts=await import('./exports.js').then(module=>module.exportModel(exp,calc,'source'));
  const sourcePreserve=opts.sourcePreserve??facts.useSource,content=buildSopContent(exp,calc,{sourcePreserve,materials:opts.materials});
  if(!sourcePreserve){
    for(const step of content.steps){step.text=renameSopSampleReferences(step.text,exp);if(step.branchScope)step.branchScope=renameSopSampleReferences(step.branchScope,exp);}
    for(const branch of content.branchContext.branches){if(branch.id)branch.id=renameSopSampleReferences(branch.id,exp);if(branch.name)branch.name=renameSopSampleReferences(branch.name,exp);}
    if(content.branchContext.selectedBranch)content.branchContext.selectedBranch=renameSopSampleReferences(content.branchContext.selectedBranch,exp);
    content.branchContext.motherCode=exp.code??exp.id;
    content.execution.code=exp.code??exp.id;
  }
  let native=null,main,parameters,leftSteps,rightSteps,sampling,modernExtraSheets=[];
  if(content.sourceKind==='xlsx'&&exp.plan?.sourceSop?.file&&exp.sopTemplateMode!=='custom'&&!Array.isArray(exp.plan.workupBranches)&&!Array.isArray(exp.plan.designSamplingRows)&&!exp.plan.steps?.some(step=>step.additions?.length||step.conditions)&&!opts.forceCustom){
    native=await buildNativeSopModel(exp,calc,opts);main=modernNativeSheet(native,exp);parameters=sourceParameterRows(native.main);
    modernExtraSheets=native.sheets.filter(sheet=>sheet!==native.main&&/^R100-\d+(?:[AB])?$/.test(sheet.sourceName)).map(sheet=>modernNativeSheet({...native,main:sheet,oldCode:sheet.sourceName,preserveSourcePlan:true,calcOwnedCells:[]},{code:sheet.name}));
    const sampleStart=native.main.cells.find(c=>c.col===1&&/^D[.、\s]/.test(String(c.value??'')))?.row??22+native.rowShift;
    const collect=(no,title,body)=>{
      const phase=body===4?'reaction':'postprocess',steps=content.steps.filter(step=>(step.phase==='postprocess'?'postprocess':'reaction')===phase);
      return native.main.cells.filter(c=>c.col===body&&c.row>=12&&c.row<sampleStart&&present(c.value)).sort((a,b)=>a.row-b.row).map((c,index)=>({...steps[index],text:c.displayValue??String(c.value),displayNo:native.main.cellMap[addr(c.row,no)]?.value??'',keyword:native.main.cellMap[addr(c.row,title)]?.value??'',phase,sourceCell:c.sourceCell??c.cell}));
    };
    leftSteps=collect(1,2,4);rightSteps=collect(8,9,11);
    const headers=Array.from({length:14},(_,i)=>native.main.cellMap[addr(sampleStart+1,i+1)]?.value??'');
    const rows=[];for(let row=sampleStart+2;row<=native.main.maxRow;row++){if(native.main.merges.some(m=>m.r1===row&&m.c1===1&&m.c2===14))continue;const values=Array.from({length:14},(_,i)=>native.main.cellMap[addr(row,i+1)]?.value??'');if(values.some(present))rows.push({sourceRow:row,values});}
    sampling={title:native.main.cellMap[addr(sampleStart,1)]?.value,columns:headers,rows,allRows:[{values:headers},...rows]};
  }else{
    const generic=genericSheet(content,exp,calc,sourcePreserve);main=generic.sheet;parameters=generic.parameters;leftSteps=generic.left;rightSteps=generic.right;sampling=generic.sampling;
  }
  const sourceConflicts=detectSopSourceConflicts(exp,{parameters,leftSteps,rightSteps,main});
  for(const conflict of sourceConflicts)fullRow(main,main.maxRow+1,'源记录矛盾（保留原文）：'+conflict.shortNote,'note',26);
  for(const sheet of modernExtraSheets){
    const conflicts=detectSopSourceConflicts(exp,{main:sheet,parameters:sourceParameterRows(sheet)});
    for(const conflict of conflicts)fullRow(sheet,sheet.maxRow+1,'源记录矛盾（保留原文）：'+conflict.shortNote,'note',26);
    if(conflicts.length)adjustHeights(sheet);
  }
  if(sourceConflicts.length)adjustHeights(main);
  const title=main.cellMap.A1?.displayValue??main.cellMap.A1?.value??content.execution.title,grid=finishGrid(main);
  main.rows=grid.rows;main.sections=grid.groups;
  const batch = !native || opts.includeActual === true ? batchRecordSheet(content, exp, opts.includeActual === true, calc, opts.materials??[]) : null;
  if (native && batch) batch.name = batch.sourceName = '本轮批记录';
  return {schema:'lab-modern-sop/1',layoutFamily:'R100-38-Excel',title,sourceKind:content.sourceKind,sourcePreserve,content,parameters,plannedCharges:content.plannedCharges,leftSteps,rightSteps,sampling,sheets:[main,...(batch?[batch]:[])],batch,main,grid,modernExtraSheets,sourceConflicts,nativeSopModel:native,warnings:native?.warnings??[],historical:facts.historical,chemicalSourceIdentity:content.sourceIdentity};
}
function cssStyle(cell){
  const style=cell.style??{},font=style.font??{},fill=style.fill?.foreground?.rgb??'FFFFFFFF',fg=font.color?.rgb??'FF20364F';
  return 'background:#'+fill.slice(-6)+';color:#'+fg.slice(-6)+';font-size:'+(font.size??9)+'pt;font-weight:'+(font.bold?'700':'400')+';text-align:'+(style.alignment?.horizontal==='center'?'center':typeof cell.value==='number'?'right':style.alignment?.horizontal??'left')+';white-space:'+(typeof cell.value==='number'?'nowrap':'pre-wrap')+';';
}
export async function modernSopHtml(exp,calc={},opts={}){
  const model=opts.model??await buildModernSopModel(exp,calc,opts),total=model.grid.cols.reduce((a,b)=>a+b,0);
  const table='<table><colgroup>'+model.grid.cols.map(w=>'<col style="width:'+(w/total*100)+'%">').join('')+'</colgroup><tbody>'+model.grid.rows.map(row=>'<tr style="height:'+row.height+'pt">'+row.cells.map(cell=>'<td colspan="'+cell.colSpan+'" rowspan="'+cell.rowSpan+'" style="'+cssStyle(cell)+'">'+esc(cell.displayValue??cell.value)+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
  return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>'+esc(model.title)+'</title><style>@page{size:A4 landscape;margin:8mm}*{box-sizing:border-box}body{margin:0;background:#f2f5f8;font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif}.sop-page{width:1110px;max-width:calc(100% - 16px);margin:12px auto;padding:12px;background:white;box-shadow:0 1px 8px #1e3f6620}table{width:100%;table-layout:fixed;border-collapse:collapse}td{padding:3px 4px;border:1px solid #b7c9d6;vertical-align:middle;line-height:1.35;overflow-wrap:anywhere;word-break:normal}tr{break-inside:avoid}@media print{body{background:white}.sop-page{margin:0;padding:0;width:100%;max-width:none;box-shadow:none}}</style></head><body><main class="sop-page">'+table+'</main></body></html>';
}

function formatCell(cell,styleId){
  const ref=cell.cell,value=cell.value;
  if(cell.formula){
    const f='<f>'+esc(String(cell.formula).replace(/^=+/,''))+'</f>';
    if(typeof value==='string')return '<c r="'+ref+'" s="'+styleId+'" t="str">'+f+'<v>'+esc(value)+'</v></c>';
    if(typeof value==='boolean')return '<c r="'+ref+'" s="'+styleId+'" t="b">'+f+'<v>'+(value?1:0)+'</v></c>';
    return '<c r="'+ref+'" s="'+styleId+'">'+f+(typeof value==='number'?'<v>'+value+'</v>':'')+'</c>';
  }
  if(value==null||value==='')return '<c r="'+ref+'" s="'+styleId+'"/>';
  if(typeof value==='number')return '<c r="'+ref+'" s="'+styleId+'"><v>'+value+'</v></c>';
  return '<c r="'+ref+'" s="'+styleId+'" t="inlineStr"><is><t xml:space="preserve">'+esc(value)+'</t></is></c>';
}
function mainXml(sheet,ids){
  const cols='<cols>'+sheet.columnWidths.map((width,index)=>'<col min="'+(index+1)+'" max="'+(index+1)+'" width="'+width+'" customWidth="1"/>').join('')+'</cols>';
  const rows=[];for(let row=1;row<=sheet.maxRow;row++){
    const cells=[];for(let col=1;col<=14;col++){const cell=sheet.cellMap[addr(row,col)]??{cell:addr(row,col),value:null,style:MODERN_SOP_STYLES.body,modernRole:'body'};cells.push(formatCell(cell,ids.get(styleKey(cell))));}
    rows.push('<row r="'+row+'" ht="'+sheet.rowHeights[row]+'" customHeight="1">'+cells.join('')+'</row>');
  }
  return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr><dimension ref="A1:N'+sheet.maxRow+'"/><sheetViews><sheetView showGridLines="0" workbookViewId="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="24"/>'+cols+'<sheetData>'+rows.join('')+'</sheetData><mergeCells count="'+sheet.merges.length+'">'+sheet.merges.map(m=>'<mergeCell ref="'+m.range+'"/>').join('')+'</mergeCells><printOptions horizontalCentered="1"/><pageMargins left="0.25" right="0.25" top="0.3" bottom="0.3" header="0.15" footer="0.15"/><pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>';
}
const styleKey=cell=>JSON.stringify(cell.style??MODERN_SOP_STYLES.body);
function appendSection(xml,tag,items,emptyBefore=''){
  const expression=new RegExp('<'+tag+'\\b[^>]*>[\\s\\S]*?</'+tag+'>'),section=xml.match(expression)?.[0];
  if(section){const oldCount=Number(section.match(/\bcount="(\d+)"/)?.[1]??0),next=section.replace(/\bcount="\d+"/,'count="'+(oldCount+items.length)+'"').replace('</'+tag+'>',items.join('')+'</'+tag+'>');return {xml:xml.replace(section,next),offset:oldCount};}
  const next='<'+tag+' count="'+items.length+'">'+items.join('')+'</'+tag+'>';
  return {xml:emptyBefore?xml.replace(emptyBefore,next+emptyBefore):xml.replace('</styleSheet>',next+'</styleSheet>'),offset:0};
}
function appendModernStyles(original,sheet){
  let xml=original,ids=new Map(),definitions=new Map();
  for(const cell of sheet.cells)definitions.set(styleKey(cell),cell.style);
  definitions.set(styleKey({style:MODERN_SOP_STYLES.body}),MODERN_SOP_STYLES.body);
  const formats=[...new Set([...definitions.values()].map(s=>s.numFmt).filter(f=>f&&f!=='General'))],existingFormatIds=[...xml.matchAll(/<numFmt\b[^>]*numFmtId="(\d+)"/g)].map(m=>+m[1]),first=Math.max(164,...existingFormatIds)+1,formatIds=new Map(formats.map((f,i)=>[f,first+i]));
  const fmt=appendSection(xml,'numFmts',formats.map(f=>'<numFmt numFmtId="'+formatIds.get(f)+'" formatCode="'+esc(f)+'"/>'),'<fonts');xml=fmt.xml;
  const names=[...definitions.keys()],fonts=names.map(key=>{const f=definitions.get(key).font??{};return '<font><sz val="'+(f.size??9)+'"/><name val="'+esc(f.name??'Microsoft YaHei')+'"/><color rgb="'+(f.color?.rgb??'FF20364F')+'"/>'+(f.bold?'<b/>':'')+'</font>';});
  const fontSection=appendSection(xml,'fonts',fonts);xml=fontSection.xml;
  const fills=names.map(key=>'<fill><patternFill patternType="solid"><fgColor rgb="'+(definitions.get(key).fill?.foreground?.rgb??'FFFFFFFF')+'"/><bgColor indexed="64"/></patternFill></fill>');
  const fillSection=appendSection(xml,'fills',fills);xml=fillSection.xml;
  const borderSection=appendSection(xml,'borders',['<border>'+['left','right','top','bottom'].map(side=>'<'+side+' style="thin"><color rgb="FFB7C9D6"/></'+side+'>').join('')+'<diagonal/></border>']);xml=borderSection.xml;
  const xfs=names.map((key,index)=>{const s=definitions.get(key),a=s.alignment??{};return '<xf numFmtId="'+(formatIds.get(s.numFmt)??0)+'" fontId="'+(fontSection.offset+index)+'" fillId="'+(fillSection.offset+index)+'" borderId="'+borderSection.offset+'" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1" applyNumberFormat="1"><alignment horizontal="'+(a.horizontal??'left')+'" vertical="center" wrapText="'+(a.wrapText===false?0:1)+'"'+(a.shrinkToFit?' shrinkToFit="1"':'')+'/></xf>';});
  const xfSection=appendSection(xml,'cellXfs',xfs);xml=xfSection.xml;names.forEach((key,index)=>ids.set(key,xfSection.offset+index));
  return {xml,ids};
}
const emptyStyles='<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="1"><font><sz val="9"/><name val="Microsoft YaHei"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>';
async function selectMainWorksheet(zip,book,main){
  const sheets=[...book.matchAll(/<sheet\b[^>]*>/g)],index=sheets.findIndex(match=>match[0].match(/\bname="([^"]*)"/)?.[1]===esc(main.name));
  if(index<0)throw Error('Missing main SOP worksheet: '+main.name);
  const view='<workbookView activeTab="'+index+'" firstSheet="'+index+'"/>';
  if(/<workbookView\b/.test(book)){
    book=book.replace(/<workbookView\b[^>]*>/g,tag=>tag.replace(/\s+(?:activeTab|firstSheet)\s*=\s*(?:"[^"]*"|'[^']*')/g,'').replace(/\s*(\/?>)$/,' activeTab="'+index+'" firstSheet="'+index+'"$1'));
  }else if(/<bookViews\b/.test(book)){
    book=book.replace(/<bookViews\b[^>]*\/>|<bookViews\b[^>]*>[\s\S]*?<\/bookViews>/,'<bookViews>'+view+'</bookViews>');
  }else book=book.replace(/<sheets\b/,'<bookViews>'+view+'</bookViews><sheets');
  for(const file of zip.file(/^xl\/worksheets\/[^/]+\.xml$/)){
    const xml=await file.async('string'),selected=file.name===main.part;
    const next=xml.replace(/<sheetView\b[^>]*>/g,tag=>{
      const cleared=tag.replace(/\s+tabSelected\s*=\s*(?:"[^"]*"|'[^']*')/g,'');
      return selected?cleared.replace(/\s*(\/?>)$/,' tabSelected="1"$1'):cleared;
    });
    if(next!==xml)zip.file(file.name,next);
  }
  return book;
}
export async function makeModernSopXlsx(exp,calc={},opts={}){
  if(!globalThis.JSZip)throw Error('缺少本地 JSZip 导出组件');
  const model=opts.model??await buildModernSopModel(exp,calc,opts),native=model.nativeSopModel;
  let zip,part='xl/worksheets/sheet1.xml',book;
  if(native){
    const bytes=await (await makeNativeSopXlsx(exp,calc,opts)).arrayBuffer();zip=await globalThis.JSZip.loadAsync(bytes);
    book=await zip.file('xl/workbook.xml').async('string');
    const rels=await zip.file('xl/_rels/workbook.xml.rels').async('string'),sheetTag=[...book.matchAll(/<sheet\b([^>]*)\/>/g)].find(m=>m[1].includes('name="'+esc(model.main.name)+'"'))?.[1];
    const rid=sheetTag?.match(/\br:id="([^"]+)"/)?.[1],target=[...rels.matchAll(/<Relationship\b([^>]*)\/>/g)].find(m=>m[1].includes('Id="'+rid+'"'))?.[1]?.match(/\bTarget="([^"]+)"/)?.[1];
    if(!target)throw Error('找不到现代 SOP 主工作表部件：'+model.main.name);part=target.startsWith('/')?target.slice(1):'xl/'+target;
  }else{
    zip=new globalThis.JSZip();
    zip.file('[Content_Types].xml','<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>');
    zip.file('_rels/.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>');
    book='<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView/></bookViews><sheets><sheet name="SOP" sheetId="1" r:id="rId1"/></sheets><definedNames><definedName name="_xlnm.Print_Area" localSheetId="0">SOP!$A$1:$N$'+model.main.maxRow+'</definedName></definedNames><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>';
    zip.file('xl/_rels/workbook.xml.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>');
  }
  const oldStyles=zip.file('xl/styles.xml')?await zip.file('xl/styles.xml').async('string'):emptyStyles,styles=appendModernStyles(oldStyles,{cells:model.main.cells.concat(list(model.modernExtraSheets).flatMap(sheet=>sheet.cells),model.batch?.cells??[])});
  zip.file('xl/styles.xml',styles.xml);zip.file(part,mainXml(model.main,styles.ids));
  if(native&&model.modernExtraSheets.length){
    const rels=await zip.file('xl/_rels/workbook.xml.rels').async('string');
    for(const sheet of model.modernExtraSheets){
      const tag=[...book.matchAll(/<sheet\b([^>]*)\/>/g)].find(match=>match[1].includes('name="'+esc(sheet.name)+'"'))?.[1],rid=tag?.match(/\br:id="([^"]+)"/)?.[1],target=[...rels.matchAll(/<Relationship\b([^>]*)\/>/g)].find(match=>match[1].includes('Id="'+rid+'"'))?.[1]?.match(/\bTarget="([^"]+)"/)?.[1];
      if(target)zip.file(target.startsWith('/')?target.slice(1):'xl/'+target,mainXml(sheet,styles.ids));
    }
  }
  // Existing source-calculation coordinates are retained. The SOP display is always the later frame.
  if(native){
    const names=[...book.matchAll(/<sheet\b[^>]*\bname="([^"]+)"/g)].map(match=>match[1]);
    book=book.replace(/<definedName\b([^>]*name="_xlnm.Print_Area"[^>]*)>[\s\S]*?<\/definedName>/g,(match,a)=>{const name=names[Number(a.match(/\blocalSheetId="(\d+)"/)?.[1])],sheet=[model.main,...model.modernExtraSheets].find(sheet=>esc(sheet.name)===name);return sheet?'<definedName'+a+'>'+esc("'"+sheet.name.replaceAll("'","''")+"'!$A$1:$N$"+sheet.maxRow)+'</definedName>':match;});
  }
  if (model.batch) {
    let rels = await zip.file('xl/_rels/workbook.xml.rels').async('string');
    const sheetIds = [...book.matchAll(/<sheet\b[^>]*\bsheetId="(\d+)"/g)].map(m => Number(m[1]));
    const relIds = [...rels.matchAll(/\bId="rId(\d+)"/g)].map(m => Number(m[1]));
    const id = Math.max(0, ...sheetIds) + 1, rid = 'rId' + (Math.max(0, ...relIds) + 1);
    let number = id;
    while (zip.file('xl/worksheets/sheet' + number + '.xml')) number++;
    const path = 'xl/worksheets/sheet' + number + '.xml';
    const sheetIndex = sheetIds.length, name = model.batch.name;
    book = book.replace('</sheets>', '<sheet name="' + esc(name) + '" sheetId="' + id + '" r:id="' + rid + '"/></sheets>');
    const names = '<definedName name="_xlnm.Print_Area" localSheetId="' + sheetIndex + '">' + esc("'" + name.replaceAll("'", "''") + "'!$A$1:$N$" + model.batch.maxRow) + '</definedName>' +
      '<definedName name="_xlnm.Print_Titles" localSheetId="' + sheetIndex + '">' + esc("'" + name.replaceAll("'", "''") + "'!$1:$4") + '</definedName>';
    book = book.includes('</definedNames>') ? book.replace('</definedNames>', names + '</definedNames>') : book.replace('</sheets>', '</sheets><definedNames>' + names + '</definedNames>');
    rels = rels.replace('</Relationships>', '<Relationship Id="' + rid + '" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet' + number + '.xml"/></Relationships>');
    zip.file('xl/_rels/workbook.xml.rels', rels);
    const types = await zip.file('[Content_Types].xml').async('string');
    zip.file('[Content_Types].xml', types.replace('</Types>', '<Override PartName="/' + path + '" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'));
    zip.file(path, mainXml(model.batch, styles.ids));
  }
  // Select against the final sheet order, after all native and batch sheets exist.
  book=await selectMainWorksheet(zip,book,{name:model.main.name,part});
  zip.file('xl/workbook.xml',book);
  return new Blob([await zip.generateAsync({type:'uint8array',compression:'DEFLATE'})],{type:MIME});
}
