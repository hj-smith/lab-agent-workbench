import {moduleCategory} from './modules.js';
import {parseRecordedValue} from './record-values.js';
import {recoverWorkbookHistory} from './workbook-history-recovery.js';

/** Recover historical records from semantic row/column labels, including filled
 * cells that the old parser did not classify as input. No fixed cell addresses. */
export function recoverHistory(design,doc,layout,values,sourceMap){
  const branches={},records=[],provenance={};
  const get=p=>p?(doc.actual?.[p.key]??values.get(p.fullAddr)):undefined;
  const labels=[['历史单体比',/^单体A\/?单体B摩尔比$/,'mol/mol'],['历史催化剂molpct',/^(?:无水)?(?:p-)?TsOH.*(?:mol%|用量)/,'mol/mol'],['历史反应固含',/^(?:反应投料固含|初始名义固含|名义总固含)$/,'w/w']];
  for(const [key,pattern,unit]of labels){
    for(const sheet of layout.sheets)for(let i=0;i<sheet.cells.length-1;i++){
      const p=sheet.cells[i],label=get(p);if(typeof label!=='string'||!pattern.test(label))continue;
      const next=sheet.cells[i+1],value=get(next);if(typeof value!=='number'||p.row!==next.row)continue;
      const original=next.cell.var??design.vars.find(v=>v.sourceAddress===next.fullAddr)?.key;
      if(!design.vars.some(v=>v.key===key))design.vars.push({key,label:key==='历史催化剂molpct'?'催化剂历史摩尔比例':key,unit,...original?{expr:`[${original}]`}:{value},technical:true});
    }
  }
  const put=(key,p,type='number',unit='g')=>{
    const parsed=parseRecordedValue(get(p),{type,unit});sourceMap[p.fullAddr]=key;
    if(!parsed.skip){design.actual[key]=parsed.value;provenance[key]=p.fullAddr;}
  };
  const rows=[];
  for(const sheet of doc.sheets)for(const block of sheet.blocks.filter(b=>b.type==='table'))for(const row of block.rows){
    const cells=[...layout.byKey.values()].filter(p=>p.sheet===sheet.name&&p.block===block.id&&p.key.split('/')[1]===row.id);
    rows.push({sheet:sheet.name,block,row,cells,text:cells.map(get).filter(v=>typeof v==='string').join(' ')});
  }
  const escaped=design.code.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  const codePattern=new RegExp(escaped+'([A-Z]?)-(PRE(?:-AddH)?|PRECIP(?:-[CF])?|S\\d+(?:-[CF])?|FP|AddH-80C-1h|\\d+\\.\\d+h(?:-PRE-AddH)?)(?![A-Za-z0-9])','g');
  for(const r of rows.filter(r=>/后处理|检测/.test(r.sheet))){
    const codes=[...r.text.matchAll(codePattern)];
    for(const m of codes){const branch=m[1],suffix=m[2];if(!branch)continue;
      branches[branch]??={id:branch,fraction:null,steps:[]};
      const id=branch+'-'+suffix;
      if(!design.samplingPlan.nodes.some(n=>n.id===id))design.samplingPlan.nodes.push({id,branch,suffix,sampleId:m[0],tests:['GPC','NMR'],kind:suffix==='FP'?'product':'workup',stage:suffix});
    }
  }
  if(Object.keys(branches).length){
    // A shared FP/PRE inferred from prose is not a third physical product.
    design.samplingPlan.nodes=design.samplingPlan.nodes.filter(n=>n.branch||n.hours!=null||/AddH/.test(n.id));
    for(const b of Object.values(branches))b.fraction=/等分/.test(design.steps.map(s=>s.sourceText).join(' '))?1/Object.keys(branches).length:null;
  }
  for(const r of rows){
    const headers=r.block.header?.map(h=>h.label??h.key)??[];
    const col=p=>r.block.header?.find(h=>h.key===p.key.split('/').at(-1))?.label??p.key.split('/').at(-1);
    const sampleCode=r.text.match(new RegExp(escaped+'[A-Z]?-(?:\\d+\\.\\d+h(?:-PRE-AddH)?|PRE|PRECIP(?:-[CF])?|S\\d+(?:-[CF])?|FP|AddH-80C-1h)'))?.[0];
    const branch=/后处理([A-Z])/.exec(r.sheet)?.[1];
    const label=String(get(r.cells[0])??r.row.id),plan=String(get(r.cells[1])??'');
    if(branch&&branches[branch]&&headers.some(h=>/计划条件/.test(h))){
      const originalStep=design.steps.find(s=>/干燥/.test(label)?/干燥/.test(s.title):/恒重/.test(label)?/恒重/.test(s.title):false);
      branches[branch].steps.push({id:`history-${branch}-${r.row.id}`,title:label,stage:'workup',category:moduleCategory(label),text:plan,sourceText:plan,args:{},...originalStep?.conditions?{conditions:structuredClone(originalStep.conditions)}:{}});
    }
    const nodes=design.samplingPlan.nodes;
    const node=nodes.find(n=>n.sampleId===sampleCode)||nodes.find(n=>design.code+'-'+n.id===sampleCode);
    const suffix=sampleCode?.slice(design.code.length).replace(/^[A-Z]?-/,'');
    const step=design.steps.find(s=>s.id===node?.stepId)??design.steps.find(s=>/恒重/.test(label)?/恒重/.test(s.title):/干燥/.test(label)?/干燥/.test(s.title):label.replace(/[^\p{L}\p{N}]/gu,'')===s.title.replace(/[^\p{L}\p{N}]/gu,''));
    const operation=branch?`history-${branch}-${r.row.id}`:step?.id;
    if(node&&operation)node.stepId=operation;
    const hour=/-(\d+\.\d+)h/.exec(sampleCode??'');
    const timelineId=hour?(suffix?.includes('PRE-AddH')?'PRE-AddH':`t-${String(+hour[1]).replace('.','_')}`):null;
    for(const p of r.cells){const c=col(p);
      if(/实际值/.test(c)&&headers.some(h=>/计划值/.test(h))){
        const norm=t=>String(t).replace(/无水|^p-|\s|[（(].*/g,'').toLowerCase();
        const charge=design.charges.find(ch=>norm(label)===norm(ch.material)||norm(label).startsWith(norm(ch.material))||ch.material==='MonA'&&/单体A/.test(label)||ch.material==='xylene'&&/二甲苯/.test(label));
        if(charge)put(`record-charges/${charge.id}/实际值`,p);
      }
      if(/实际值/.test(c)&&/计划.*t=0|计划达温/.test(label))put('record-t0/actual/实际钟点',p,'time','Excel day');
      if(timelineId){
        const field=/实际钟点/.test(c)?'实际钟点':/^内温/.test(c)?'内温':/^外温/.test(c)?'外温':/累计水量/.test(c)?'累计水量':/GPC取样质量/.test(c)?'取样量':null;
        if(field)put(`record-timeline/${timelineId}/${field}`,p,field==='实际钟点'?'time':'number',field.includes('温')?'°C':'g');
      }
      if(/后处理/.test(r.sheet)&&operation){
        const field=/固相取样|样品质量|取样.*\(g\)/.test(c)?'取样量':/湿饼.*净重|质量.*湿饼/.test(c)?(/恒重.*M2/.test(label)?'M2':/干燥.*M1/.test(label)?'M1':'质量'):/固相含固率/.test(c)?'含固率':/液相总质量/.test(c)?'液相质量':/液相含固率/.test(c)?'液相含固率':null;
        if(field==='取样量'&&node)put(`record-samples/${node.id}/取样量`,p);
        else if(field)put(`record-operations/${operation}/${field}`,p,'number',/含固/.test(field)?'w/w':'g');
      }
    }
    // Final dry mass may be recorded in a summary grid rather than the M2 row.
    for(let i=0;i<r.cells.length-1;i++)if(/^(最终干品质量|最终干品净重)$/.test(String(get(r.cells[i])))){
      const fp=nodes.find(n=>n.branch===branch&&/(?:^|-)FP$/.test(n.id));
      if(fp)put(`record-operations/${fp.stepId??'history-'+(branch??'main')+'-FP'}/M2`,r.cells[i+1]);
    }
    if(/后处理|批记录|检测/.test(r.sheet))for(const p of r.cells){const v=get(p);if(p.cell.input||typeof v==='number'&&!p.cell.f&&!p.cell.var)records.push({source:p.fullAddr,key:p.key,value:v??null});}
  }
  if(!design.samplingPlan.nodes.some(n=>/(?:^|-)FP$/.test(n.id))){const dry=design.steps.findLast(s=>/干燥|恒重/.test(s.title));if(dry)design.samplingPlan.nodes.push({id:'FP',kind:'product',stepId:dry.id,tests:['GPC','NMR']});}
  // In CP38 the neutralising solution and its addition occupy separate rows.
  // Attach the declared reagent to the neutralisation operation itself, instead
  // of letting downstream factors search unrelated preparation/addition rows.
  for(const branch of Object.values(branches))for(const step of branch.steps.filter(s=>['中和','稀释'].includes(s.category))){
    const preparation=branch.steps.find(s=>/配液/.test(s.title)&&/THF/.test(s.text));
    if(preparation){step.text='加入'+preparation.text+'；'+step.text;step.quenchPreparationSource=preparation.id;}
  }
  design.historicalBranches=branches;design.historicalRecords=records;design.actualSources=provenance;
  return recoverWorkbookHistory(design,doc);
}
