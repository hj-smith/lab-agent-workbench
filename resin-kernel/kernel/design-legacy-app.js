import {availableName,mergeEdits} from './merge-edits.js';
import {fileSource,retainExport} from '../file-source.js';
import {appFetch} from '../api-url.js';
import {renderPaper} from './ui/document-renderer.js';
import {parseWorkbook,layoutDoc,evaluateDoc,parseExpr,parseTemplate,evaluateExpr,serializeExpr,resolvePlaced,rangePlacements,escapeTemplate,deriveDoc,writeWorkbook,readRecordedValues,diffActual,applyActual,blankDoc,addVar,deleteVar,referencesToVar,editTableRow} from './index.js';
import {formatValue as format,formatCellValue,recordItemLabel,isSpacerRow} from './display.js';

const $=id=>document.getElementById(id), esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
let savedDoc,doc,version=0,designs=[],activeSheet='SOP',layout,values=new Map(),errors=new Map(),history=[],future=[],saveTimer,saving,dirty=false,conflict=false,selectedKey,previousValues=new Map();
const status=(text,error=false)=>{$('save-status').textContent=text;$('save-status').classList.toggle('error',error);};
let toastTimer;function toast(text){$('toast').textContent=text;$('toast').style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').style.display='none',3500);}
async function api(url,options){const res=await appFetch(url,options),body=await res.json();if(!res.ok){const e=Error(body.error||'请求失败');e.status=res.status;throw e;}return body;}
const safe=fn=>(...args)=>Promise.resolve().then(()=>fn(...args)).catch(e=>toast(e.message));
function refreshEvaluation(){previousValues=values;layout=layoutDoc(doc);errors=new Map();values=evaluateDoc(doc,{errors});}
function contextTemplate(text,p){
  try{return parseTemplate(text).map(part=>{
    if(part.text!==undefined)return esc(part.text);
    const resolve=r=>{const q=resolvePlaced(layout,r,p);if(errors.has(q?.fullAddr))throw Error(errors.get(q.fullAddr));return values.get(q?.fullAddr);};
    const value=evaluateExpr(part.expr,resolve,(a,b)=>rangePlacements(layout,a,b,p).map(q=>values.get(q?.fullAddr)),r=>resolvePlaced(layout,r,p).sheet);
    const expr=serializeExpr(part.expr),label=part.expr.kind==='var'?(doc.vars.find(v=>v.key===part.expr.key)?.label??expr):expr;
    return `<span class="linked" title="${esc(`${label} = ${format(value,part.fmt)} (${expr})`)}">${esc(format(value,part.fmt))}</span>`;
  }).join('');}catch(e){return `<span class="cell-error">${esc(e.message)}</span>`;}
}
function cellHtml(p){
  if(errors.has(p.fullAddr))return `<span class="cell-error" title="${esc(errors.get(p.fullAddr))}">${esc(errors.get(p.fullAddr))}</span>`;
  if(p.cell.t!==undefined)return contextTemplate(p.cell.t,p);
  return esc(formatCellValue(doc,p,values.get(p.fullAddr),layout));
}
function paperHtml(sheet,printing=false){return renderPaper(doc,sheet.name,{printing,selectedKey,previousValues});}
function fit(){const paper=$('canvas').querySelector('.paper');if(!paper)return;const width=parseFloat(paper.style.width),scale=$('zoom').value==='fit'?Math.min(1,($('workspace').clientWidth-48)/width):1;paper.style.transform=`scale(${scale})`;$('canvas').style.width=`${width*scale}px`;$('canvas').style.height=`${paper.offsetHeight*scale}px`;}
function renderCanvas(){if(!doc)return;const sheet=layout.sheets.find(s=>s.name===activeSheet)??layout.sheets[0];activeSheet=sheet.name;$('canvas').innerHTML=paperHtml(sheet);$('print-pages').innerHTML=layout.sheets.map(s=>`<section class="print-sheet" aria-label="打印 ${esc(s.name)}">${paperHtml(s,true)}</section>`).join('');fit();}
function renderTabs(){$('sheet-tabs').innerHTML=doc.sheets.map(s=>`<button class="${s.name===activeSheet?'active':''}" data-sheet="${esc(s.name)}">${esc(s.name)}</button>`).join('');}
function render(){if(!doc)return;refreshEvaluation();$('doc-code').value=doc.code;$('doc-title').value=doc.title;renderTabs();renderCanvas();if(!$('variables').hidden)renderVars();$('undo').disabled=!history.length;$('redo').disabled=!future.length;}
function change(fn,{inspector=false}={}){
  if(!doc)return;const before=structuredClone(doc);
  try{const result=fn(doc);if(result)doc=result;layoutDoc(doc);}catch(e){doc=before;render();if(selectedKey)inspect(layout.byKey.get(selectedKey));throw e;}
  if(JSON.stringify(before)===JSON.stringify(doc))return;
  history.push(before);if(history.length>150)history.shift();future=[];dirty=true;status('待保存…');render();if(inspector&&selectedKey)inspect(layout.byKey.get(selectedKey));scheduleSave();
}
function scheduleSave(){clearTimeout(saveTimer);if(!conflict)saveTimer=setTimeout(()=>void save().catch(e=>status(`保存失败：${e.message}`,true)),800);}
async function save(){
  clearTimeout(saveTimer);if(saving){await saving;if(dirty&&!conflict)return save();return;}
  if(!doc||!dirty||conflict)return;
  const snapshot=structuredClone(doc),sent=JSON.stringify(snapshot),sentVersion=version;
  status('保存中…');
  saving=(async()=>{
    try{const result=await api('/api/designs/'+encodeURIComponent(snapshot.id),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({doc:snapshot,base:savedDoc,version:sentVersion})});
      if(result.doc){savedDoc=structuredClone(result.doc);doc=mergeEdits(snapshot,doc,result.doc);}if(result.warning)toast(result.warning);version=result.version;dirty=JSON.stringify(doc)!==sent;status(dirty?'待保存…':`已保存 ${new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}`);localStorage.setItem('lab-design-id',doc.id);await loadList();
    }catch(e){if(e.status===409){status('并行修改，未保存的编辑仍保留；'+e.message);}else{status(`保存失败：${e.message}`,true);throw e;}}
  })();try{await saving;}finally{saving=null;}if(dirty&&!conflict)scheduleSave();
}
async function loadList(){designs=(await api('/api/designs')).filter(d=>d.schema!=='lab-design/2');renderList();}
function renderList(){const q=$('design-search').value.toLowerCase();$('design-list').innerHTML=designs.filter(d=>`${d.code} ${d.title}`.toLowerCase().includes(q)).map(d=>`<button data-design="${esc(d.id)}"><b>${esc(d.code)}</b><small>${esc(d.title)} · ${esc(d.updatedAt?.slice(0,16).replace('T',' '))}</small></button>`).join('')||'<p class="muted">没有匹配的设计</p>';}
function useDoc(next,v){savedDoc=structuredClone(next);doc=next;version=v;dirty=false;conflict=false;history=[];future=[];selectedKey=null;previousValues=new Map();values=new Map();$('reload').hidden=true;$('inspector').hidden=true;activeSheet=doc.sheets.some(s=>s.name===activeSheet)?activeSheet:doc.sheets[0].name;render();localStorage.setItem('lab-design-id',doc.id);status('已保存');}
async function openDesign(id){await save();if(conflict)throw Error('请先重新加载冲突的设计');const result=await api('/api/designs/'+encodeURIComponent(id));useDoc(result.doc,result.version);$('design-picker').open=false;}
async function createDesign(next){next.id=crypto.randomUUID();await save();if(conflict)throw Error('请先重新加载冲突的设计');const result=await api('/api/designs/'+encodeURIComponent(next.id),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({doc:next,version:0})});useDoc(next,result.version);await loadList();toast('已创建 '+next.code);}
function nextCode(base=doc?.code||'R100-0'){const match=/^(.*?)(\d+)$/.exec(base);let stem=match?.[1]??'CP',n=Number(match?.[2]??0)+1;while(designs.some(d=>d.code===stem+n))n++;return stem+n;}
function validCode(code){return availableName(code,designs.map(d=>d.code));}
function modal(html,setup){const dialog=$('modal');if(dialog.open)dialog.close();dialog.innerHTML=html;dialog.showModal();dialog.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>dialog.close());setup?.(dialog);}
function codeModal(title,initial,onApply,extra=''){
  modal(`<h2>${esc(title)}</h2>${extra}<form id="code-form"><label class="modal-label">新批号<input id="new-code" value="${esc(initial)}" required></label><label class="modal-label">标题<input id="new-title" value="${esc(`${initial}树脂合成SOP`)}"></label><p id="modal-error" class="error"></p><div class="actions"><button type="button" data-close>取消</button><button class="primary" type="submit">创建并打开</button></div></form>`,dialog=>{
    $('new-code').oninput=()=>{$('new-title').value=`${$('new-code').value}树脂合成SOP`;};
    $('code-form').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{const code=validCode($('new-code').value.trim());await onApply(code,$('new-title').value.trim());dialog.close();}catch(e){$('modal-error').textContent=e.message;}finally{button.disabled=false;}};
  });
}
function undo(){if(!history.length)return;future.push(structuredClone(doc));doc=history.pop();dirty=true;selectedKey=null;$('inspector').hidden=true;render();scheduleSave();status('待保存…');}
function redo(){if(!future.length)return;history.push(structuredClone(doc));doc=future.pop();dirty=true;selectedKey=null;$('inspector').hidden=true;render();scheduleSave();status('待保存…');}
function sourceCell(p){const block=doc.sheets.find(s=>s.name===p.sheet)?.blocks.find(b=>b.id===p.block);if(!block)return {};const [,rowId,colKey]=p.key.split('/');return {block,rowId,colKey,row:block.rows?.find(r=>r.id===rowId)};}
function nextRecord(key){const cells=[...$('canvas').querySelectorAll('[data-record-input]')],i=cells.findIndex(c=>c.dataset.key===key),next=cells[i+1]??cells[0];next?.click();}
function inline(p,td){
  const v=p.cell.var?doc.vars.find(v=>v.key===p.cell.var):null;
  if(td.querySelector('input'))return;
  const current=values.get(p.fullAddr),input=document.createElement('input');input.className='cell-editor';input.setAttribute('aria-label',v?.label??p.label);input.value=format(current,p.cell.type==='time'?'hh:mm':undefined);input.inputMode=typeof current==='number'||p.cell.type==='number'?'decimal':'text';td.replaceChildren(input);input.focus();input.select();let done=false;
  const commit=()=>{if(done)return true;try{let value=input.value;if(p.cell.type==='time'&&value!==''){const m=/^(\d{1,2}):(\d{2})$/.exec(value);if(!m||+m[1]>23||+m[2]>59)throw Error('时间格式为 hh:mm');value=(+m[1]*60+ +m[2])/1440;}else if((typeof current==='number'||p.cell.type==='number')&&value!==''){value=Number(value);if(!Number.isFinite(value))throw Error('请输入有效数字');}
      done=true;change(d=>{if(v){const item=d.vars.find(x=>x.key===v.key);item.value=value;if(item.role==='code'){const old=d.code;d.code=String(value);d.title=d.title.replaceAll(old,d.code);}}else{if(value==='')delete d.actual[p.key];else d.actual[p.key]=value;}});renderCanvas();return true;
    }catch(e){input.setCustomValidity(e.message);input.reportValidity();done=false;return false;}};
  input.oninput=()=>input.setCustomValidity('');input.onblur=()=>{if(!done&&!commit()){done=true;renderCanvas();toast('未保存：请输入有效数值或时间');}};
  input.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();done=true;renderCanvas();}if(e.key==='Enter'){e.preventDefault();commit();}if(e.key==='Tab'&&p.cell.input){e.preventDefault();if(commit())nextRecord(p.key);}};
}
function inspectorShell(title,html){$('variables').hidden=true;$('inspector').hidden=false;$('inspector').innerHTML=`<button class="close" aria-label="关闭检查器">×</button><h2>${esc(title)}</h2>${html}`;$('inspector').querySelector('.close').onclick=()=>{$('inspector').hidden=true;fit();};fit();}
function field(label,id,value,type='input'){return `<label>${esc(label)}${type==='textarea'?`<textarea id="${id}">${esc(value)}</textarea>`:`<input id="${id}" value="${esc(value)}">`}</label>`;}
function expressionHtml(source){return source.split(/(@?\[[^\]]+\])/g).map(part=>{const match=/^(@?)\[([^\]]+)\]$/.exec(part);if(!match)return esc(part);return `<span class="chip">${esc(match[1]?match[2]:doc.vars.find(v=>v.key===match[2])?.label??match[2])}</span>`;}).join('');}
function liveEdit(id,fn,validate){$(id).oninput=()=>{try{validate?.($(id).value);change(d=>fn(d,$(id).value));$('inspect-error').textContent='';}catch(e){$('inspect-error').textContent=e.message;}};}
function formulaEditor(target,p,isVar){
  const value=isVar?target.expr:p.cell.f;
  inspectorShell(isVar?'计算变量':'公式单元格',`${isVar?field('标签','expr-label',target.label)+field('单位','expr-unit',target.unit):''}${field('格式','expr-fmt',target.fmt??'General')}<label>表达式</label><div id="expr-chips" class="chips"></div>${field('原始表达式','expr-source',value,'textarea')}<p id="inspect-error" class="error"></p><div id="expr-preview" class="preview"></div>${isVar?'<button id="to-input">转为输入变量</button>':''}`);
  const get=()=>isVar?doc.vars.find(v=>v.key===target.key):layout.byKey.get(p.key).cell;
  const draw=()=>{$('expr-chips').innerHTML=expressionHtml($('expr-source').value);$('expr-preview').textContent=errors.get(p.fullAddr)??`当前值：${format(values.get(p.fullAddr),get().fmt)}`;};draw();
  $('expr-source').oninput=()=>{try{parseExpr($('expr-source').value);change(()=>{get()[isVar?'expr':'f']=$('expr-source').value;});$('inspect-error').textContent='';draw();}catch(e){$('inspect-error').textContent=e.message;}};
  for(const [id,key] of [['expr-label','label'],['expr-unit','unit'],['expr-fmt','fmt']])if($(id))liveEdit(id,(_,text)=>{get()[key]=text;});
  if(isVar)$('to-input').onclick=()=>{change(()=>{const v=get();delete v.expr;v.value=values.get(p.fullAddr)??0;});inspectVariable(get(),p);};
}
function inspectVariable(v,p){
  $('variables').hidden=true;
  p=layout.byAddr.get(layout.varToAddr.get(v.key))??p;
  if(v.expr!==undefined)return formulaEditor(v,p,true);
  inspectorShell('输入变量',`<small>${esc(v.key)} · ${esc(p.fullAddr)}</small>${field('标签','var-label',v.label)}${field('值','var-value',v.value)}${field('单位','var-unit',v.unit)}${field('格式','var-fmt',v.fmt??'General')}<p id="inspect-error" class="error"></p><button id="to-computed">转为计算变量</button>`);
  for(const [id,key]of [['var-label','label'],['var-unit','unit'],['var-fmt','fmt']])liveEdit(id,(_,text)=>{doc.vars.find(x=>x.key===v.key)[key]=text;});
  $('var-value').onchange=()=>{try{const value=typeof v.value==='number'?Number($('var-value').value):$('var-value').value;if(typeof value==='number'&&!Number.isFinite(value))throw Error('请输入有效数字');change(()=>{doc.vars.find(x=>x.key===v.key).value=value;});}catch(e){$('inspect-error').textContent=e.message;}};
  $('to-computed').onclick=()=>{change(()=>{const item=doc.vars.find(x=>x.key===v.key);item.expr=typeof item.value==='number'?String(item.value):'"'+String(item.value).replaceAll('"','""')+'"';delete item.value;});formulaEditor(doc.vars.find(x=>x.key===v.key),p,true);};
  if(v.role==='code'){$('to-computed').disabled=true;$('to-computed').title='批号通过顶部修改';}
}
function stepInspector(block,step,side,p){
  inspectorShell('步骤 '+values.get(layout.sheets.find(s=>s.name===p.sheet).cells.find(q=>q.block===block.id&&q.key.split('/')[1]===step.id&&q.key.endsWith('/number'))?.fullAddr),`${field('标题','step-title',step.title)}${field('正文模板','step-template',step.text,'textarea')}<p id="inspect-error" class="error"></p><label>实时预览</label><div class="preview" id="step-preview"></div><label>插入变量</label><div class="variable-picker"><input id="token-search" placeholder="搜索变量"><select id="token-format" aria-label="变量格式">${['原样','0','0.0','0.00','0.000','0%','hh:mm'].map(f=>`<option>${f}</option>`).join('')}</select></div><div class="variable-results" id="token-results"></div><div class="tools">${['上移','下移','在下方插入','复制','删除',side==='left'?'移到后处理':'移到反应'].map((t,i)=>`<button data-step-action="${i}">${t}</button>`).join('')}</div>`);
  const getBlock=()=>doc.sheets.find(s=>s.name===p.sheet).blocks.find(b=>b.id===block.id),getStep=()=>[...getBlock().left.steps,...getBlock().right.steps].find(s=>s.id===step.id);
  const preview=()=>{$('step-preview').innerHTML=contextTemplate($('step-template').value,p);};preview();
  liveEdit('step-title',(_,text)=>{getStep().title=text;},parseTemplate);
  const edit=()=>{try{parseTemplate($('step-template').value);change(()=>{getStep().text=$('step-template').value;});$('inspect-error').textContent='';preview();}catch(e){$('inspect-error').textContent=e.message;preview();}};$('step-template').oninput=edit;
  let caret=$('step-template').value.length,selection=caret;const remember=()=>{caret=$('step-template').selectionStart;selection=$('step-template').selectionEnd;};$('step-template').onselect=remember;$('step-template').onkeyup=remember;$('step-template').onclick=remember;$('step-template').onblur=remember;
  const picker=()=>{const q=$('token-search').value.toLowerCase();$('token-results').innerHTML=doc.vars.filter(v=>`${v.label} ${v.key}`.toLowerCase().includes(q)).map(v=>`<button data-token="${esc(v.key)}">${esc(v.label)} <small>${esc(v.unit)}</small></button>`).join('');};picker();$('token-search').oninput=picker;
  $('token-results').onclick=e=>{const button=e.target.closest('[data-token]');if(!button)return;const area=$('step-template'),fmt=$('token-format').value,token=`{[${button.dataset.token}]${fmt==='原样'?'':'|'+fmt}}`;area.setRangeText(token,caret,selection,'end');caret=area.selectionEnd;selection=caret;area.focus();edit();};
  $('inspector').querySelectorAll('[data-step-action]').forEach(button=>button.onclick=safe(()=>{
    const action=+button.dataset.stepAction;change(()=>{const b=getBlock(),steps=b[side].steps,index=steps.findIndex(s=>s.id===step.id),used=new Set([...b.left.steps,...b.right.steps].map(s=>s.id));
      if(action===0&&index>0)[steps[index-1],steps[index]]=[steps[index],steps[index-1]];
      if(action===1&&index<steps.length-1)[steps[index+1],steps[index]]=[steps[index],steps[index+1]];
      if(action===2||action===3){let id='step'+Date.now();while(used.has(id))id+='_2';steps.splice(index+1,0,action===2?{id,title:'新步骤',text:''}:{...structuredClone(steps[index]),id});}
      if(action===4)steps.splice(index,1);if(action===5)b[side==='left'?'right':'left'].steps.push(...steps.splice(index,1));
    });if(action===4){$('inspector').hidden=true;}else{const b=getBlock(),newSide=action===5?(side==='left'?'right':'left'):side;stepInspector(b,b[newSide].steps.find(s=>s.id===step.id),newSide,layout.byKey.get(p.key)??p);}
  }));
}
function inspect(p){
  if(!p)return;selectedKey=p.key;const {block,rowId,colKey,row}=sourceCell(p);
  if(p.cell.var){inspectVariable(doc.vars.find(v=>v.key===p.cell.var),p);return;}
  if(block.type==='steps2'){
    const side=block.left.steps.some(s=>s.id===rowId)?'left':block.right.steps.some(s=>s.id===rowId)?'right':null;
    if(side){stepInspector(block,block[side].steps.find(s=>s.id===rowId),side,p);return;}
    inspectorShell('流程栏标题',`${field('标题','block-text',colKey==='left'?block.left.title:block.right.title)}<p id="inspect-error" class="error"></p>`);liveEdit('block-text',(_,text)=>{block[colKey==='left'?'left':'right'].title=text;},parseTemplate);return;
  }
  if(['title','section','note'].includes(block.type)){
    inspectorShell('标题 / 说明',`${field('文字或模板','block-text',block.text,'textarea')}<p id="inspect-error" class="error"></p><div class="preview" id="block-preview"></div>`);$('block-preview').innerHTML=contextTemplate(block.text,p);liveEdit('block-text',(_,text)=>{block.text=text;$('block-preview').innerHTML=contextTemplate(text,p);},parseTemplate);return;
  }
  if(rowId==='header'){
    const header=block.header.find(h=>h.key===colKey);inspectorShell('表头',`${field('表头标签','header-label',header.label)}<p id="inspect-error" class="error"></p>`);liveEdit('header-label',(_,text)=>{header.label=text;});return;
  }
  if(!row){inspectorShell('表格标题',`${field('标题','table-title',block.title)}<p id="inspect-error" class="error"></p>`);liveEdit('table-title',(_,text)=>{block.title=text;},parseTemplate);return;}
  const cell=p.cell,kind=cell.f!==undefined?'f':cell.t!==undefined?'t':cell.v!==undefined?'v':cell.input?'input':'empty';
  inspectorShell('表格单元格',`<small>${esc(p.fullAddr)} · ${esc(p.label)}</small><label>单元格类型<select id="cell-kind">${[['t','文字/模板'],['f','公式'],['v','常量'],['input','待填格'],['empty','空']].map(([k,t])=>`<option value="${k}" ${kind===k?'selected':''}>${t}</option>`).join('')}</select></label>${field('内容','cell-content',cell.f??cell.t??cell.v??'','textarea')}${field('格式','cell-format',cell.fmt??'General')}<label>待填类型<select id="cell-type">${['text','number','time'].map(t=>`<option ${cell.type===t?'selected':''}>${t}</option>`).join('')}</select></label><p id="inspect-error" class="error"></p><div id="cell-preview" class="preview">${cellHtml(p)}</div><div class="tools">${['在下方插入行','复制行','删除行','上移','下移'].map((t,i)=>`<button data-row-action="${i}">${t}</button>`).join('')}</div>`);
  const chips=document.createElement('div');chips.className='chips';$('cell-content').parentElement.before(chips);
  const readable=()=>{chips.innerHTML=$('cell-kind').value==='f'?expressionHtml($('cell-content').value):'';};readable();
  const sheetCells=layout.sheets.find(s=>s.name===p.sheet).cells,unitHeader=sheetCells.find(q=>q.block===p.block&&q.style==='header'&&q.cell.t==='单位');
  if(unitHeader){const unitCell=sheetCells.find(q=>q.block===p.block&&q.row===p.row&&q.col===unitHeader.col),unit=document.createElement('p');unit.className='muted';unit.textContent='单位：'+(unitCell?values.get(unitCell.fullAddr)??'—':'—');chips.before(unit);}
  const update=()=>{readable();try{const k=$('cell-kind').value,text=$('cell-content').value;if(k==='f')parseExpr(text);if(k==='t')parseTemplate(text);if(k==='v'&&text!==''&&!Number.isFinite(Number(text)))throw Error('常量请输入数字，文字请选择文字/模板');change(()=>{const {span,rows}=cell;for(const key of Object.keys(cell))delete cell[key];if(span)cell.span=span;if(rows)cell.rows=rows;if(k==='t')cell.t=text;if(k==='f')cell.f=text;if(k==='v')cell.v=text===''?0:Number(text);if(k==='input'){cell.input=true;cell.type=$('cell-type').value;}cell.fmt=$('cell-format').value;});$('inspect-error').textContent='';$('cell-preview').innerHTML=cellHtml(layout.byKey.get(p.key));}catch(e){$('inspect-error').textContent=e.message;}};
  for(const id of ['cell-kind','cell-type'])$(id).onchange=update;for(const id of ['cell-content','cell-format'])$(id).oninput=update;
  $('inspector').querySelectorAll('[data-row-action]').forEach(button=>button.onclick=safe(()=>{
    change(d=>editTableRow(d,block.id,row.id,['insert','duplicate','delete','up','down'][+button.dataset.rowAction]));$('inspector').hidden=true;fit();
  }));
}
function renderVars(){
  const q=$('var-search')?.value??'';$('variables').innerHTML=`<button class="close" aria-label="关闭变量">×</button><h2>变量 <small>${doc.vars.length} 项</small></h2><div class="vars-toolbar"><input id="var-search" placeholder="搜索变量" value="${esc(q)}"><button id="add-var">新增变量</button></div><div id="vars-body"></div>`;
  const rows=()=>{const search=$('var-search').value.toLowerCase();$('vars-body').innerHTML=`<table class="vars-table"><thead><tr>${['key','标签','值或表达式','单位','格式','当前值','所在位置',''].map(t=>`<th>${t}</th>`).join('')}</tr></thead><tbody>${doc.vars.filter(v=>`${v.key} ${v.label}`.toLowerCase().includes(search)).map(v=>`<tr data-variable="${esc(v.key)}"><td>${esc(v.key)}</td><td><input data-v-label value="${esc(v.label)}" aria-label="标签 ${esc(v.key)}"></td><td><button data-v-edit>${esc(v.expr??v.value)}</button></td><td>${esc(v.unit)}</td><td>${esc(v.fmt)}</td><td>${esc(format(values.get(layout.varToAddr.get(v.key)),v.fmt))}</td><td>${esc(layout.varToAddr.get(v.key))}</td><td><button data-v-delete class="danger">删除</button></td></tr>`).join('')}</tbody></table>`;};rows();$('var-search').oninput=rows;$('variables').querySelector('.close').onclick=()=>{$('variables').hidden=true;fit();};
  $('vars-body').onchange=e=>{if(e.target.matches('[data-v-label]'))change(()=>{doc.vars.find(v=>v.key===e.target.closest('[data-variable]').dataset.variable).label=e.target.value;});};
  $('vars-body').onclick=safe(e=>{const tr=e.target.closest('[data-variable]');if(!tr)return;const v=doc.vars.find(v=>v.key===tr.dataset.variable);if(e.target.closest('[data-v-edit]')){const p=layout.byAddr.get(layout.varToAddr.get(v.key));inspectVariable(v,p);}if(e.target.closest('[data-v-delete]')){const refs=referencesToVar(doc,v.key);if(v.role==='code')throw Error('批次编号用于文档标识，不能删除');if(refs.length){modal(`<h2>变量仍被引用</h2><p>${esc(v.label)}</p><ul>${refs.map(r=>`<li>${esc(r)}</li>`).join('')}</ul><div class="actions"><button data-close>关闭</button></div>`);return;}change(d=>deleteVar(d,v.key));}});
  $('add-var').onclick=()=>modal(`<h2>新增变量</h2><form id="add-var-form">${field('标签','add-label','')}${field('初始值','add-value',0)}${field('单位','add-unit','')}${field('格式','add-format','0.000')}<p class="help">自动放入 SOP 参数表的下一个空位。</p><p id="add-error" class="error"></p><div class="actions"><button type="button" data-close>取消</button><button class="primary">添加</button></div></form>`,dialog=>{$('add-var-form').onsubmit=e=>{e.preventDefault();try{const label=$('add-label').value.trim();if(!label)throw Error('请输入标签');const raw=$('add-value').value,value=raw.trim()!==''&&Number.isFinite(Number(raw))?Number(raw):raw;change(d=>addVar(d,{label,value,unit:$('add-unit').value,fmt:$('add-format').value}));dialog.close();}catch(error){$('add-error').textContent=error.message;}};});
}
async function importRecord(file){
  if(!doc)throw Error('请先打开实验设计');const source=await fileSource(file),imported=await readRecordedValues(await file.arrayBuffer()),diff=diffActual(doc,imported.values),mismatch=imported.meta.id!==doc.id||imported.meta.code!==doc.code;
  const proceed=()=>modal(`<h2>导入批记录 · ${diff.length} 处差异</h2>${imported.warnings.length?`<p class="error">${esc(imported.warnings.join('；'))}</p>`:''}<label><input id="diff-all" type="checkbox" checked> 全选</label><div class="diff-scroll"><table class="diff-table"><thead><tr><th>应用</th><th>位置</th><th>项目</th><th>当前值</th><th>导入值</th></tr></thead><tbody>${diff.map((d,i)=>`<tr><td><input type="checkbox" data-diff="${i}" checked></td><td>${esc(layout.byKey.get(d.key)?.fullAddr??d.key)}</td><td>${esc(recordItemLabel(doc,layout,values,layout.byKey.get(d.key))||d.label)}</td><td>${esc(formatCellValue(doc,layout.byKey.get(d.key),d.before))}</td><td>${esc(formatCellValue(doc,layout.byKey.get(d.key),d.after))}</td></tr>`).join('')}</tbody></table></div><p id="diff-error" class="error"></p><div class="actions"><button data-close>取消</button><button id="apply-record" class="primary">应用</button></div>`,dialog=>{
    $('diff-all').onchange=()=>dialog.querySelectorAll('[data-diff]').forEach(c=>c.checked=$('diff-all').checked);
    $('apply-record').onclick=async()=>{try{const selected=[...dialog.querySelectorAll('[data-diff]:checked')].map(c=>diff[+c.dataset.diff]);change(d=>{const next=applyActual(d,Object.fromEntries(selected.map(d=>[d.key,d.after])));next.studio={...next.studio,recordImports:[...(next.studio?.recordImports??[]),{...source,file:file.name,at:new Date().toISOString(),selected:selected.length}]};return next;});await save();dialog.close();toast(`已应用 ${selected.length} 处：新增 ${selected.filter(d=>d.kind==='add').length}，修改 ${selected.filter(d=>d.kind==='change').length}，清空 ${selected.filter(d=>d.kind==='clear').length}`);}catch(e){$('diff-error').textContent=e.message;}};
  });
  if(mismatch)modal(`<h2>批号不一致</h2><p>工作簿：${esc(imported.meta.code)}；当前设计：${esc(doc.code)}</p><div class="actions"><button data-close>取消</button><button id="continue-record">继续查看差异</button></div>`,()=>$('continue-record').onclick=proceed);else proceed();
}
$('canvas').onclick=safe(e=>{const td=e.target.closest('td[data-key]');if(!td)return;const p=layout.byKey.get(td.dataset.key);selectedKey=p.key;const v=doc.vars.find(v=>v.key===p.cell.var);if(p.cell.input||v&&(!p.cell.show||p.cell.show==='value')&&v.expr===undefined)inline(p,td);else inspect(p);});
$('canvas').onkeydown=e=>{if(e.key==='Enter'&&e.target.matches('[data-record-input]'))e.target.click();};
$('sheet-tabs').onclick=e=>{const button=e.target.closest('[data-sheet]');if(!button)return;activeSheet=button.dataset.sheet;selectedKey=null;$('inspector').hidden=true;renderTabs();renderCanvas();};
$('design-search').oninput=renderList;$('design-list').onclick=safe(e=>{const button=e.target.closest('[data-design]');if(button)return openDesign(button.dataset.design);});
$('toggle-vars').onclick=()=>{if(!doc)return;$('variables').hidden=!$('variables').hidden;if(!$('variables').hidden){$('inspector').hidden=true;renderVars();}fit();};
$('zoom').onchange=fit;new ResizeObserver(fit).observe($('workspace'));
$('new-blank').onclick=()=>{$('new-menu').open=false;codeModal('空白实验',nextCode(),async(code,title)=>{const next=blankDoc(code);next.title=title;next.sheets[0].blocks[0].text=escapeTemplate(title).replaceAll(code,'{[批次编号]}');await createDesign(next);});};
$('import-design').onclick=()=>{$('new-menu').open=false;$('design-file').click();};$('empty-import').onclick=()=>$('import-design').click();$('empty-new').onclick=()=>$('new-blank').click();
$('design-file').onchange=safe(async()=>{const file=$('design-file').files[0];$('design-file').value='';if(!file)return;status('正在解析工作簿…');const bytes=await file.arrayBuffer(),source=await fileSource(file,bytes),{doc:parsed,report}=await parseWorkbook(bytes,{file:file.name});status(doc?'已保存':'请选择或新建设计');codeModal('导入工作簿设计',parsed.code,async(code,title)=>{const next=deriveDoc(parsed,{code,title});next.parentCode='';next.importSource=source;await createDesign(next);},`<p>已解析 ${parsed.sheets.length} 张表、${parsed.vars.length} 个变量。未符号化公式 ${report.unsymbolizedFormulas.length}，提示 ${report.warnings.length}。</p>${report.unsymbolizedFormulas.length||report.warnings.length?`<details><summary>查看解析报告</summary><div class="help">${esc([...report.unsymbolizedFormulas.map(x=>`${x.sheet}!${x.cell} ${x.error}`),...report.warnings].join('\n'))}</div></details>`:''}`);});
$('derive').onclick=()=>{if(!doc)return;codeModal('派生新实验',nextCode(),async(code,title)=>{await createDesign(deriveDoc(doc,{code,title}));});};
$('export').onclick=safe(async()=>{if(!doc)return;const bytes=await writeWorkbook(doc),url=URL.createObjectURL(new Blob([bytes],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})),a=document.createElement('a');a.href=url;a.download=`${doc.code}_SOP与批记录.xlsx`;retainExport(bytes,a.download);a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);toast('Excel 已导出');});
$('print').onclick=()=>{if(doc){renderCanvas();window.print();}};window.addEventListener('beforeprint',()=>{if(doc)renderCanvas();});
$('import-record').onclick=()=>{if(doc)$('record-file').click();};$('record-file').onchange=safe(async()=>{const file=$('record-file').files[0];$('record-file').value='';if(file)await importRecord(file);});
$('undo').onclick=undo;$('redo').onclick=redo;$('reload').onclick=safe(async()=>{const result=await api('/api/designs/'+encodeURIComponent(doc.id));useDoc(result.doc,result.version);});
$('doc-code').onchange=safe(()=>{if(!doc)return;const code=$('doc-code').value.trim();if(!/^[\w\-.\u4e00-\u9fa5]+$/.test(code))throw Error('批号格式无效');change(d=>{const old=d.code;d.code=code;d.title=d.title.replaceAll(old,code);const v=d.vars.find(v=>v.role==='code');if(v){delete v.expr;v.value=code;}});});
$('doc-title').onchange=()=>{if(doc)change(d=>{d.title=$('doc-title').value;const b=d.sheets.find(s=>s.name==='SOP')?.blocks.find(b=>b.type==='title');if(b)b.text=escapeTemplate(d.title).replaceAll(d.code,`{[${d.vars.find(v=>v.role==='code')?.key??'批次编号'}]}`);});};
document.addEventListener('keydown',e=>{const editing=e.target.matches('input,textarea,[contenteditable]');if(e.key==='Escape'){$('inspector').hidden=true;$('variables').hidden=true;fit();}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();void save().catch(error=>toast(error.message));}if(!editing&&(e.ctrlKey||e.metaKey)){if(e.key.toLowerCase()==='z'){e.preventDefault();e.shiftKey?redo():undo();}if(e.key.toLowerCase()==='y'){e.preventDefault();redo();}}});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
await loadList();const last=localStorage.getItem('lab-design-id');if(designs.some(d=>d.id===last))await openDesign(last);else if(designs.length)await openDesign(designs[0].id);
