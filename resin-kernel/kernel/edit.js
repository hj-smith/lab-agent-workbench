import {cleanKey} from './parse-workbook.js';
import {editRevision} from './identity.js';
import {validateVariables} from './variable-editing.js';
import {layoutDoc} from './layout.js';
import {parseExpr,parseTemplate,mapRefs,serializeExpr,escapeTemplate} from './expr.js';

const uid=(base,used)=>{let id=base,i=2;while(used.has(id))id=`${base}_${i++}`;return id;};
export function referencesToVar(doc,key) {
  const out=[];
  const scan=(text,template,where)=>{
    const asts=template?parseTemplate(text).filter(p=>p.expr).map(p=>p.expr):[parseExpr(text)];
    for(const ast of asts) mapRefs(ast,r=>{if(r.kind==='var' && r.key===key && !out.includes(where))out.push(where);return r;});
  };
  for(const v of doc.vars) if(v.expr!==undefined)scan(v.expr,false,`变量 ${v.label}`);
  if(doc.schema==='lab-design/2'){
    const walk=(value,where)=>{if(typeof value==='string'){if(value.includes(`[${key}]`)||value.includes('{{'+key+'}}'))out.push(where);}else if(Array.isArray(value))value.forEach((item,i)=>walk(item,where+' '+(item?.title??item?.label??(i+1))));else if(value&&typeof value==='object')for(const [name,item]of Object.entries(value)){if(['sourceText','generatedTextBaseline','userEditedFields','aliases','uid','id','label'].includes(name))continue;if(name==='paramBindings'){for(const [param,binding]of Object.entries(item??{}))if(binding===key)out.push(where+' · 参数 '+param);}else if(['sourceVar','of','key','knob'].includes(name)&&item===key)out.push(where+' · 投料依据');else walk(item,where+' · '+({text:'执行文字',titleTemplate:'标题',amount:'用量',value:'数值',conditions:'条件',hours:'时长',timeExpr:'取样时间',hoursExpr:'取样时间',args:'本步参数',controls:'控制点',samples:'取样',target:'因子目标',kind:'类型',from:'起始温度'}[name]??name));}};
    for(const [name,part]of [['工序',doc.procedure],['投料',doc.charges],['步骤',doc.steps],['取样计划',doc.samplingPlan],['记录项',doc.records],['对照组',doc.series?.factors]])walk(part,name);
    return [...new Set(out)];
  }
  for(const p of layoutDoc(doc).byKey.values()) {
    if(p.cell.f!==undefined)scan(p.cell.f,false,`${p.sheet}!${p.addr}`);
    if(p.cell.t!==undefined)scan(p.cell.t,true,`${p.sheet}!${p.addr}`);
  }
  return out;
}
export function deleteVar(doc,key) {
  if(doc.vars.find(v=>v.key===key)?.vesselId)throw Error('容器容量参数请在容器与装置中修改或删除');
  const refs=referencesToVar(doc,key);if(refs.length)throw Error(`变量仍被引用：${refs.join('、')}`);
  if(doc.vars.find(v=>v.key===key)?.role==='code')throw Error('批次编号不能删除');
  const out=doc.schema==='lab-design/2'?editRevision(doc):structuredClone(doc);out.vars=out.vars.filter(v=>v.key!==key);
  if(doc.schema==='lab-design/2')return out;
  for(const s of out.sheets)for(const b of s.blocks)if(b.rows)for(const r of b.rows)for(const c of r.cells)if(c.var===key){delete c.var;delete c.show;}
  return out;
}
export function addVar(doc, variable) {
  if(doc.schema==='lab-design/2'){
    const out=editRevision(doc),v={label:variable.label||variable.key||'新参数',unit:'',fmt:'General',...variable};
    v.key=uid(cleanKey(v.key||v.label)||'parameter',new Set(out.vars.map(v=>v.key)));
    if(v.expr!==undefined&&String(v.expr).trim())delete v.value;else{delete v.expr;v.value??=0;}
    out.vars.push(v);validateVariables(out.vars);return out;
  }
  const out=structuredClone(doc), sop=out.sheets.find(s=>s.name==='SOP');
  if(!sop)throw Error('缺少 SOP 参数表');
  const table=sop.blocks.find(b=>b.type==='table' && b.header && (b.header[0]?.label==='参数'||b.header.some(h=>h.label==='数值')));
  if(!table)throw Error('缺少 SOP 参数表');
  const v={label:variable.label||variable.key||'新变量',unit:'',fmt:'General',value:0,...variable};
  v.key=uid(cleanKey(v.key||v.label),new Set(out.vars.map(v=>v.key)));
  const slots=[];for(let col=0;col+1<sop.widths.length;col+=3)slots.push(col);
  let row,slot;
  const free=c=>!c || (c.span??1)===1 && !c.var && !c.input && c.f===undefined && c.v==null && (c.t===undefined||c.t==='');
  for(const r of table.rows) {for(const col of slots)if([r.cells[col],r.cells[col+1],r.cells[col+2]].every(free)){row=r;slot=col;break;}if(row)break;}
  if(!row){row={id:uid('parameter',new Set(table.rows.map(r=>r.id))),cells:Array.from({length:sop.widths.length},()=>({}))};table.rows.push(row);slot=slots[0];}
  row.cells[slot]={var:v.key,show:'label'};row.cells[slot+1]={var:v.key,show:'value'};
  if(slot+2<sop.widths.length)row.cells[slot+2]={var:v.key,show:'unit'};
  out.vars.push(v);return out;
}
export function editTableRow(doc,blockId,rowId,action) {
  const out=structuredClone(doc),block=out.sheets.flatMap(s=>s.blocks).find(b=>b.id===blockId&&b.type==='table');
  const index=block?.rows.findIndex(r=>r.id===rowId),row=block?.rows[index];if(!row)throw Error('找不到表格行');
  // Rows joined by a vertical merge move as one shape; row edits must not split or duplicate it.
  const tied=i=>block.rows.some((r,j)=>j<=i&&r.cells.some(c=>(c.rows??1)>1&&j+c.rows>i));
  if(tied(index)||action==='up'&&index>0&&tied(index-1)||action==='down'&&index+1<block.rows.length&&tied(index+1))throw Error('该行属于纵向合并区域，不能单独插入、复制、删除或移动');
  const prefix=`${blockId}/${rowId}/`,homes=new Set(row.cells.filter(c=>c.var&&(!c.show||c.show==='value')).map(c=>c.var));
  if(action==='up'||action==='down') {const next=index+(action==='up'?-1:1);if(next>=0&&next<block.rows.length)[block.rows[index],block.rows[next]]=[block.rows[next],block.rows[index]];return out;}
  if(action==='delete'){
    const refs=[],layout=layoutDoc(doc);
    const scan=(source,template,where,context)=>{
      const asts=template?parseTemplate(source).filter(p=>p.expr).map(p=>p.expr):[parseExpr(source)];
      for(const ast of asts)mapRefs(ast,r=>{if(r.kind==='var'&&homes.has(r.key)||r.kind==='cell'&&(r.key.startsWith(prefix)||context.block===blockId&&r.key.startsWith(`${rowId}/`)))refs.push(where);return r;});
    };
    for(const v of doc.vars)if(v.expr!==undefined&&!homes.has(v.key))scan(v.expr,false,`变量 ${v.label}`,layout.byAddr.get(layout.varToAddr.get(v.key))??{});
    for(const p of layout.byKey.values())if(!p.key.startsWith(prefix)){
      if(p.cell.var&&homes.has(p.cell.var))refs.push(p.fullAddr);
      if(p.cell.f!==undefined)scan(p.cell.f,false,p.fullAddr,p);if(p.cell.t!==undefined)scan(p.cell.t,true,p.fullAddr,p);
    }
    if(refs.length)throw Error(`该行仍被引用：${[...new Set(refs)].join('、')}`);
    block.rows.splice(index,1);out.vars=out.vars.filter(v=>!homes.has(v.key));for(const key of Object.keys(out.actual))if(key.startsWith(prefix))delete out.actual[key];return out;
  }
  if(!['insert','duplicate'].includes(action))throw Error('未知行操作');
  const id=uid('row',new Set(block.rows.map(r=>r.id)));
  if(action==='insert'){block.rows.splice(index+1,0,{id,cells:row.cells.map(c=>({...c.span?{span:c.span}:{},...c.input?{input:true,type:c.type,...c.fmt?{fmt:c.fmt}:{}}:{}}))});return out;}
  const used=new Set(out.vars.map(v=>v.key)),renames=new Map();
  for(const key of homes){const v=out.vars.find(v=>v.key===key);if(v.role==='code')throw Error('批号栏不能复制');const next=uid(key,used);used.add(next);renames.set(key,next);}
  const rewrite=ast=>mapRefs(ast,r=>{
    if(r.kind==='var'&&renames.has(r.key))return {...r,key:renames.get(r.key)};
    if(r.kind==='cell'&&r.key.startsWith(prefix))return {...r,key:r.key.replace(prefix,`${blockId}/${id}/`)};
    if(r.kind==='cell'&&r.key.startsWith(`${rowId}/`))return {...r,key:r.key.replace(`${rowId}/`,`${id}/`)};return r;
  });
  const expr=s=>serializeExpr(rewrite(parseExpr(s))),template=s=>parseTemplate(s).map(p=>p.text!==undefined?escapeTemplate(p.text):`{${serializeExpr(rewrite(p.expr))}${p.fmt?'|'+p.fmt:''}}`).join('');
  for(const [old,key]of renames){const v=structuredClone(out.vars.find(v=>v.key===old));v.key=key;v.label+='（副本）';if(v.expr!==undefined)v.expr=expr(v.expr);out.vars.push(v);}
  const copy=structuredClone(row);copy.id=id;for(const c of copy.cells){if(c.var&&renames.has(c.var))c.var=renames.get(c.var);if(c.f!==undefined)c.f=expr(c.f);if(c.t!==undefined)c.t=template(c.t);}
  block.rows.splice(index+1,0,copy);for(const [key,value]of Object.entries(out.actual))if(key.startsWith(prefix))out.actual[key.replace(prefix,`${blockId}/${id}/`)]=value;
  return out;
}
export function blankDoc(code='R100-1') {
  const widths=[15,12,8,15,12,8,15,12,8,15,12,8,15,12];
  return {schema:'lab-design/1',id:code,code,title:`${code}树脂合成SOP`,parentCode:'',revision:1,actual:{},vars:[{key:'批次编号',label:'批次编号',role:'code',value:code,unit:'',fmt:'General'}],sheets:[
    {name:'SOP',landscape:true,widths,blocks:[
      {type:'title',id:'SOP标题',text:'{[批次编号]}树脂合成SOP'},
      {type:'table',id:'批号',rows:[{id:'code',cells:[{t:'批次编号'},{var:'批次编号'},{t:'树脂合成实验设计与批记录',span:12}]}]},
      {type:'section',id:'参数节',text:'A. 参数表'},
      {type:'table',id:'参数表',header:widths.map((_,i)=>({key:`p${i}`,label:i%3===0?'参数':i%3===1?'数值':'单位'})),rows:[{id:'parameter',cells:widths.map(()=>({}))}]},
      {type:'steps2',id:'实验步骤',left:{title:'B. 反应操作',steps:[{id:'step1',title:'投料',text:'填写反应操作'}]},right:{title:'C. 后处理',steps:[{id:'step2',title:'后处理',text:'填写后处理操作'}]}},
      {type:'section',id:'检测节',text:'D. 取样检测'},
      {type:'table',id:'取样检测',header:[{key:'样品',label:'样品编号',span:3},{key:'项目',label:'检测项目',span:4},{key:'方法',label:'方法与要求',span:7}],rows:[{id:'sample1',cells:[{t:'',span:3},{t:'',span:4},{t:'',span:7}]}]}
    ]},
    {name:'批记录',landscape:true,widths:Array(12).fill(13),blocks:[{type:'title',id:'批记录标题',text:'{[批次编号]}批记录'},{type:'table',id:'投料',header:'项目|计划值|实际值|单位|批号/纯度|称量/操作|复核|开始时间|结束时间|器具/设备ID|偏差|备注'.split('|').map(label=>({key:cleanKey(label),label})),rows:[{id:'material1',cells:[{t:'物料'},{v:0},{input:true,type:'number'},{t:'g'},...Array.from({length:8},(_,i)=>({input:true,type:i===3||i===4?'time':'text'}))]}]}]}
  ]};
}
