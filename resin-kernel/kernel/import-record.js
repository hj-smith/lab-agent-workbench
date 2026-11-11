import {readWorkbook} from './xlsx-read.js';
import {layoutDoc} from './layout.js';
import {recordLayout} from './record-layout.js';
import {parseRecordedValue,isBlank} from './record-values.js';
import {stableUid,executionDesign} from './identity.js';
import {applyDesignActual} from './design2.js';
import {compile} from './compile.js';
import {address} from './xml.js';
import {recordedMetricConflicts} from './sample-sync.js';
const norm=s=>String(s??'').normalize('NFKC').replace(/\s+/g,'').replace(/\(([^)]*)\)/g,(token,content)=>/沉降|打浆/.test(content)?token:'').replace(/相对时间/,'计划时间').replace(/操作阶段|阶段事件/,'事件').replace(/^项目$/,'物料').replace(/^计划条件$/,'计划').replace(/^现象$/,'现象/备注').replace(/^编号$/,'样品编号').replace(/^阶段$/,'时间/阶段').replace(/^补加\/处理开始$/,'操作起点').replace(/^固相含固率$/,'含固率').replace(/^滤液含固率$/,'液相含固率').replace(/^滤液g$/,'液相g').replace(/^Mn.*$/,'Mn').replace(/^Mw.*$/,'Mw').replace(/^NMR结果\/文件$/,'NMR');
const hash=s=>stableUid('record-row',s).replaceAll('-','').slice(0,16);
function inferTables(book){
  const known=new Set(['操作起点','物料','计划值','实际值','备注','计划时间','建议钟点','事件','实际钟点','内温','外温','累计水量','现象/备注','步骤','计划','实际用量','质量','含固率','M1','M2','样品编号','时间/阶段','取样量','批号/纯度','液相质量','液相含固率','Mn','Mw','Đ','NMR']);
  const tables=[];
  for(const sheet of book.sheets.filter(s=>s.name!=='_meta')){
    const rows=new Map();for(const c of sheet.cells){const p=address(c.addr);if(!rows.has(p.row))rows.set(p.row,[]);rows.get(p.row).push({...c,col:p.col});}
    for(const [row,cells]of rows){const keys=cells.map(c=>norm(c.value)),kind=keys.includes('实际值')&&keys.includes('计划值')?'charges':keys.includes('事件')&&keys.includes('实际钟点')?'timeline':keys.includes('步骤')&&keys.includes('实际用量')?'operations':keys.includes('样品编号')&&keys.includes('时间/阶段')?'samples':null;
      if(!kind)continue;tables.push({id:'record-'+kind,sheet:sheet.name,headerRow:row,columns:cells.filter(c=>known.has(norm(c.value))).map(c=>({key:norm(c.value),label:c.value,col:c.col})),rows:[]});
    }
  }return tables;
}
function oldMetaTables(meta){
  const groups=new Map();for(const i of meta.inputs??[]){const [block,id,colKey]=i.key.split('/');if(!groups.has(block))groups.set(block,{id:block,sheet:i.sheet,columns:[],rows:[]});const t=groups.get(block),p=address(i.cell),header=i.anchors?.at(-1);if(!t.columns.some(c=>c.key===colKey))t.columns.push({key:colKey,label:header?.value??colKey,col:p.col});let r=t.rows.find(r=>r.id===id);if(!r){r={id,label:i.anchors?.[0]?.value??id,anchors:(i.anchors??[]).slice(0,1).map(a=>({key:colKey,value:a.value,col:address(a.cell).col})),inputs:[]};t.rows.push(r);}r.inputs.push({...i,colKey,col:p.col});}return [...groups.values()];
}
export async function readRecordedValues(bytes,{doc,design,library={},experiment}={}){
  const book=await readWorkbook(bytes,{allowErrors:true,allowDate1904:true}),metaSheet=book.sheets.find(s=>s.name==='_meta');let meta=null;const warnings=[];
  if(metaSheet)try{meta=JSON.parse(metaSheet.cells.filter(c=>/^A\d+$/.test(c.addr)).sort((a,b)=>address(a.addr).row-address(b.addr).row).map(c=>c.value??'').join(''));}catch{warnings.push('_meta 无法解析，已按表头和行标签匹配');}
  let revisionUid=meta?.revisionUid;
  if(design&&meta?.designUid&&meta.designUid!==design.uid){
    const candidate=design.frozenRevisions?.find(r=>r.revision===meta.revision),local=candidate?compile(executionDesign(design,candidate.uid),library):null,sourceRows=meta.tables?.find(t=>t.id==='record-charges')?.rows??[],localRows=local?recordLayout(local).tables.find(t=>t.id==='record-charges')?.rows??[]:[];
    const plans=sourceRows.filter(r=>typeof r.anchors?.find(a=>a.key==='计划值')?.value==='number');
    const samePlan=plans.length>0&&plans.every(r=>{const a=r.anchors.find(a=>a.key==='计划值').value,b=localRows.find(x=>x.id===r.id)?.anchors?.find(a=>a.key==='计划值')?.value;return typeof b==='number'&&Math.abs(a-b)<1e-7*Math.max(1,Math.abs(a));});
    if(meta.code!==design.code||!candidate||!samePlan)throw Error('工作簿身份与本批不符，或执行版本的计划投料不同');
    warnings.push('工作簿来自同编号的独立副本；批号、修订和全部计划投料核对一致，已对应到本地执行版本');meta={...meta,sourceDesignUid:meta.designUid,sourceRevisionUid:meta.revisionUid,designUid:design.uid,revisionUid:candidate.uid};revisionUid=candidate.uid;
  }
  if(!meta&&design?.frozenRevisions?.length){
    const physical=inferTables(book).find(t=>t.id==='record-charges');
    if(physical){const sheet=book.sheets.find(s=>s.name===physical.sheet),byRow=new Map();for(const c of sheet.cells){const a=address(c.addr);if(!byRow.has(a.row))byRow.set(a.row,new Map());byRow.get(a.row).set(a.col,c.value);}
      const labelCol=physical.columns.find(c=>norm(c.label)==='物料')?.col,planCol=physical.columns.find(c=>norm(c.label)==='计划值')?.col;
      const candidates=design.frozenRevisions.map(rev=>{const d=compile(executionDesign(design,rev.uid),library),expected=recordLayout(d).tables.find(t=>t.id==='record-charges');let hits=0,misses=0;
        for(const row of expected?.rows??[]){const planned=row.anchors.find(a=>norm(a.key)==='计划值')?.value,physicalRow=[...byRow].find(([r,c])=>r>physical.headerRow&&norm(c.get(labelCol))===norm(row.label));if(!physicalRow||typeof planned!=='number')continue;const actual=physicalRow[1].get(planCol);if(typeof actual==='number'&&Math.abs(actual-planned)<1e-8*Math.max(1,Math.abs(planned)))hits++;else misses++;}
        return {uid:rev.uid,revision:rev.revision,hits,misses};}).filter(r=>r.hits>0).sort((a,b)=>a.misses-b.misses||b.hits-a.hits||b.revision-a.revision);
      if(candidates.length){revisionUid=candidates[0].uid;if(candidates[1]?.misses===candidates[0].misses&&candidates[1]?.hits===candidates[0].hits)warnings.push('缺少 _meta，多个冻结版本的计划投料相同；当前按最近执行版本预览，请核对版本。');}
    }
  }
  const target=design?compile(executionDesign(design,revisionUid),library):doc;
  const inferred=inferTables(book),descriptors=meta?.tables??(target?recordLayout(target).tables:meta?.inputs?oldMetaTables(meta):inferred);
  if(!inferred.length&&!meta?.inputs?.length)throw Error('无法导入：此文件不是批记录，未找到“计划值/实际值”或“事件/实际钟点”等记录表头。');
  meta??={schema:'lab-design/1',id:target?.id,code:target?.code,revision:target?.revision,revisionUid:target?.revisionUid,designUid:target?.uid,inferred:true};
  const values={},flags={},rawValues={},newRows=[],seen=new Set(),remapped=[];
  const set=(key,cell,descriptor)=>{if(!cell||seen.has(key))return;const parsed=parseRecordedValue(book.date1904&&descriptor.type==='time'&&typeof cell.value==='number'&&cell.value>=1?cell.value+1462:cell.value,descriptor);if(parsed.skip)return;seen.add(key);values[key]=parsed.value;rawValues[key]=cell.value;const oldNote=meta?.actualFlags?.[key];if(oldNote?.note&&parseRecordedValue(oldNote.raw,descriptor).value===parsed.value)parsed.note??=oldNote.note;if(parsed.flag||parsed.note)flags[key]={kind:parsed.flag??'note',message:parsed.message,note:parsed.note,raw:cell.value};};
  for(const table of descriptors){
    const sheets=book.sheets.filter(s=>s.name!=='_meta').sort((a,b)=>Number(b.name===table.sheet)-Number(a.name===table.sheet));let found;
    for(const sheet of sheets){const rows=new Map();for(const cell of sheet.cells){const p=address(cell.addr);if(!rows.has(p.row))rows.set(p.row,new Map());rows.get(p.row).set(p.col,cell);}
      const headers=[...rows].map(([row,cells])=>{const columns=table.columns.flatMap(c=>{const actual=[...cells].find(([,cell])=>norm(cell.value)===norm(c.label));return actual?[{...c,col:actual[0]}]:[];});return {row,columns,score:columns.length};}).filter(h=>h.score>=Math.min(2,table.columns.length)).sort((a,b)=>b.score-a.score||Math.abs(a.row-(table.headerRow??0))-Math.abs(b.row-(table.headerRow??0)));
      if(headers.length){found={sheet,rows,header:headers[0]};break;}
    }
    if(!found){warnings.push(`未找到记录表：${table.title??table.id}`);continue;}
    const {sheet,rows,header}=found,columns=new Map(header.columns.map(c=>[c.key,c.col]));
    const otherHeaders=[...inferred.filter(t=>t.sheet===sheet.name&&t.headerRow>header.row).map(t=>t.headerRow),...[...rows].filter(([r,cells])=>r>header.row&&descriptors.some(t=>t.id!==table.id&&t.columns.filter(c=>[...cells.values()].some(cell=>norm(cell.value)===norm(c.label))).length>=Math.min(3,t.columns.length))).map(([r])=>r)],end=Math.min(...otherHeaders,Infinity),matchedRows=new Set();
    for(const row of table.rows??[]){
      const candidates=[...rows].filter(([r])=>r>header.row&&r<end).map(([r,cells])=>{let score=0,total=0;for(const anchor of row.anchors??[]){const col=columns.get(anchor.key);if(col===undefined)continue;total++;if(norm(cells.get(col)?.value)===norm(anchor.value))score+=typeof anchor.value==='string'?4:1;}return {r,cells,score,total};}).filter(c=>c.score>0&&!matchedRows.has(c.r)&&(typeof row.label!=='string'||!(row.anchors??[]).some(a=>a.value===row.label)||(row.anchors??[]).some(a=>a.value===row.label&&norm(c.cells.get(columns.get(a.key))?.value)===norm(row.label)))).sort((a,b)=>b.score-a.score);
      let candidate=candidates[0];
      if(!candidate&&row.inputs?.length&&meta.inputs&&!meta.tables){const old=row.inputs[0],p=address(old.cell),cells=rows.get(p.row);if(cells&&(old.anchors??[]).every(a=>sheet.cells.find(c=>c.addr===a.cell)?.value===a.value))candidate={r:p.row,cells};}
      if(!candidate){if(row.inputs?.length)warnings.push(`行标签未匹配，已跳过：${row.label}`);continue;}matchedRows.add(candidate.r);
      for(const field of row.inputs??[]){const col=columns.get(field.colKey),cell=col===undefined?null:candidate.cells.get(col);if(cell&&cell.addr!==field.cell)remapped.push(field.key);set(field.key,cell,field);}
    }
    for(const [row,cells]of rows){if(table.id==='record-t0')continue;if(row<=header.row||row>=end||matchedRows.has(row))continue;
      const labelCol=header.columns.find(c=>/物料|事件|步骤|样品编号/.test(norm(c.label)))??header.columns[0],label=cells.get(labelCol.col)?.value;
      if([...cells.values()].filter(c=>!isBlank(c.value)).length<2||isBlank(label)||typeof label==='string'&&/执行人|日期|签名|实际初始|实际物质|判断|分离比/.test(label))continue;
      if(table.rows?.length&&[...cells.values()].some(c=>c.formula!==undefined))continue;
      const inputColumns=new Set((table.rows??[]).flatMap(r=>r.inputs??[]).map(i=>i.colKey));
      const fields=header.columns.filter(c=>c.col!==labelCol.col&&(!table.rows?.length||inputColumns.has(c.key))&&!['物料','事件','步骤','样品编号','计划值','计划时间','建议钟点','计划','时间/阶段'].includes(norm(c.label))).map(c=>{
        const descriptor=table.rows?.flatMap(r=>r.inputs??[]).find(i=>i.colKey===c.key);return {column:c,descriptor:descriptor??{type:/钟点/.test(c.key)?'time':/温|量|率|M[12]|实际值/.test(c.key)?'number':'text',unit:/温/.test(c.key)?'°C':/率/.test(c.key)?'w/w':'',label:c.label}};
      });
      if(!fields.some(f=>!isBlank(cells.get(f.column.col)?.value)))continue;
      const hoursCol=header.columns.find(c=>norm(c.label)==='计划时间'),hoursRaw=hoursCol?cells.get(hoursCol.col)?.value:null,hours=parseRecordedValue(hoursRaw,{type:'number'}),id='extra-'+hash(table.id+'\0'+String(label)+'\0'+String(hoursRaw??'')+'\0'+newRows.filter(r=>r.block===table.id&&r.label===String(label)).length);
      newRows.push({block:table.id,id,label:String(label),hours:typeof hours.value==='number'?hours.value:null});for(const f of fields)set(`${table.id}/${id}/${f.column.key}`,cells.get(f.column.col),f.descriptor);
    }
  }
  if(remapped.length)warnings.push(`已按表头和行标签重新定位 ${remapped.length} 格`);
  const preview=previewActual(target??{actual:{}},values,flags);
  const conflicts=experiment&&design?recordedMetricConflicts(experiment,{...design,actualRevisionUid:meta.revisionUid??design.actualRevisionUid},values):[];
  if(conflicts.length){const keys=new Set(conflicts.map(item=>item.key));for(const key of Object.keys(preview.groups))preview.groups[key]=preview.groups[key].filter(item=>!keys.has(item.key));preview.groups['人工值冲突']=conflicts;preview.counts=Object.fromEntries(Object.entries(preview.groups).map(([key,items])=>[key,items.length]));preview.total=Object.values(preview.groups).reduce((sum,items)=>sum+items.length,0);}
  const rowHours=new Map(descriptors.flatMap(t=>(t.rows??[]).map(r=>{const raw=r.anchors?.find(a=>/计划时间|相对时间/.test(a.key))?.value;return [r.id,typeof raw==='number'?raw:Number.parseFloat(raw)];})));for(const r of newRows)rowHours.set(r.id,r.hours);
  for(const [id,hours]of rowHours)if(!Number.isFinite(hours)){const match=/^t-(\d+(?:[_.]\d+)?)/.exec(id);rowHours.set(id,match?Number(match[1].replace('_','.')):Infinity);}
  const blockOrder={'record-t0':0,'record-charges':1,'record-timeline':2,'record-operations':3,'record-samples':4};for(const group of Object.values(preview.groups))group.sort((a,b)=>{const [ab,ai]=a.key.split('/'),[bb,bi]=b.key.split('/');return (blockOrder[ab]??5)-(blockOrder[bb]??5)||(ab==='record-timeline'&&bb===ab?(rowHours.get(ai)??Infinity)-(rowHours.get(bi)??Infinity):0);});
  return {meta,values,flags,rawValues,newRows,warnings,preview};
}
export function diffActual(doc,values,{clearKeys=[]}={}){
  const labels=new Map((doc.schema==='lab-design/1'?layoutDoc(doc).inputs:[]).map(i=>[i.key,i.label]));
  return Object.entries(values).flatMap(([key,after])=>{const before=doc.actual?.[key]??null;if(isBlank(after)&&!clearKeys.includes(key)||Object.is(before,after)||isBlank(before)&&isBlank(after))return [];return [{key,label:labels.get(key)??key,before,after,kind:isBlank(after)?'clear':isBlank(before)?'add':'change'}];});
}
export function previewActual(doc,values,flags={}){
  const groups={'新增':[],'修改':[],'无法识别':[],'疑似填错':[]};
  for(const item of diffActual(doc,values)){const flag=flags[item.key],group=flag?.kind==='unparsed'?'无法识别':flag?.kind==='suspect'?'疑似填错':item.kind==='add'?'新增':'修改';groups[group].push({...item,flag:flag?.kind,raw:flag?.raw,note:flag?.note,message:flag?.message,selected:true,importable:true});}
  return {groups,counts:Object.fromEntries(Object.entries(groups).map(([k,v])=>[k,v.length])),total:Object.values(groups).reduce((n,g)=>n+g.length,0)};
}
export function applyActual(doc,values,options={}){
  if(values?.values){options={...values,...options};values=values.values;}
  if(doc.schema==='lab-design/2')return applyDesignActual(doc,values,options);
  const out=structuredClone(doc),keys=new Set(layoutDoc(doc).inputs.map(i=>i.key));out.actual??={};
  for(const row of options.newRows??[]){const block=out.sheets.flatMap(s=>s.blocks).find(b=>b.id===row.block);if(!block||block.rows.some(r=>r.id===row.id))continue;block.rows.push({id:row.id,cells:block.header.map((h,i)=>i===0?{t:row.label,span:h.span??1}:{input:true,type:/温|量|率|M[12]|实际值/.test(h.key)?'number':/钟点/.test(h.key)?'time':'text',span:h.span??1})});for(const h of block.header)keys.add(`${block.id}/${row.id}/${h.key}`);}
  for(const [key,value]of Object.entries(values)){if(!keys.has(key)){(out.unmatchedActual??={})[key]=value;continue;}if(isBlank(value)){if(options.clearKeys?.includes(key))delete out.actual[key];}else out.actual[key]=value;}
  out.actualFlags={...out.actualFlags};for(const key of Object.keys(values))if(!isBlank(values[key]))delete out.actualFlags[key];Object.assign(out.actualFlags,options.flags);return out;
}
