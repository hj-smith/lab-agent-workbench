import {distinctBranchSources} from './branch-records.js';
import {address} from './xml.js';
import {escapeTemplate} from './expr.js';
import {normalizeSamplingIdentity} from './sampling-identity.js';

const present=v=>v!==null&&v!==undefined&&v!=='';
const token=s=>String(s??'').replace(/[\s/／()（）°℃.-]/g,'');
const hash=s=>{let h=2166136261;for(const c of s)h=Math.imul(h^c.charCodeAt(0),16777619);return(h>>>0).toString(16);};
const clock=v=>typeof v==='number'?v:typeof v==='string'&&/^\d{1,2}:\d{2}(?::\d{2})?$/.test(v)?v.split(':').reduce((a,n,i)=>a+Number(n)/[24,1440,86400][i],0):null;
const flatSteps=steps=>(steps??[]).flatMap(s=>[s,...(s.branches??[]).flatMap(b=>flatSteps(b.steps))]);
const calculationRow=/干基|干固体|步骤闭合|理论净树脂|分离收率|批次结论|样品去向|产品编号|复核|批准/;
const endSection=/表观物料衡算|批次结论|分离收率|方法与判定|检测记录|设计依据/;
const mass=value=>typeof value==='number'&&Number.isFinite(value)?value:typeof value==='string'&&/^\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*g?\s*$/i.test(value)?Number(value.replace(/g\s*$/i,'').trim()):null;
const fraction=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=1?value:typeof value==='string'&&/^\s*\d+(?:\.\d+)?\s*%\s*$/.test(value)&&Number(value.replace('%','').trim())<=100?Number(value.replace('%','').trim())/100:null;
const rowGroups=sheet=>{const rows=new Map();for(const c of sheet.cells){const {row,col}=address(c.addr);if(!rows.has(row))rows.set(row,[]);rows.get(row).push({...c,col});}return [...rows].sort((a,b)=>a[0]-b[0]);};
function owningStep(design,label){
  const steps=flatSteps(design.steps),norm=token(label),exact=steps.find(s=>token(s.title)===norm);if(exact)return exact;
  const rules=[[/恒重M2|最终产品|最终干品|称M2/,/恒重|续干/],[/恒重M1|干燥.*M1/,/干燥/],[/打浆2|浆洗2|第二次.*打浆/,/打浆2|第二次.*打浆/],[/打浆1|浆洗1|第一次.*打浆/,/打浆1|第一次.*打浆/],[/IPA.*(?:打浆|洗|过滤)/,/IPA.*(?:打浆|洗|过滤)/],[/MeOH.*打浆/,/MeOH.*打浆/],[/HEP.*过滤|庚烷.*过滤/,/HEP.*过滤|庚烷.*冲洗/],[/沉降过滤/,/沉降过滤/],[/MeOH.*沉降/,/MeOH.*沉降/],[/正庚烷沉降/,/正庚烷沉降/],[/PRE|沉降前/,/PRE|沉降前/],[/干燥/,/干燥/],[/THF.*稀释/,/THF.*稀释/]];
  for(const [test,match]of rules)if(test.test(label)){const found=steps.find(s=>s.stage==='workup'&&match.test(s.title));if(found)return found;}
  return undefined;
}

/** Recover actual source registrations, including supplemental batch records.
 * The parser retains raw values and original addresses so merged captions and
 * filled cells cannot disappear just because they are not editable inputs.
 * Supplemental books add evidence; they never replace the selected SOP plan. */
export function recoverWorkbookHistory(design,doc,{supplementary=false}={}){
  const source=doc.sourceWorkbook;if(!source)return design;
  const escaped=design.code.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  const workupRE=new RegExp(escaped+'([A-Z]?)-(PRE(?:-AddH)?|PRECIP(?:-[CF])?|HEP-[CF]|IPA-[CF]|MeOH-[CF]|S\\d+(?:-[CF])?|FP|AddH-80C-1h)(?![A-Za-z0-9-])','g');
  const sourceFile=source.file??doc.source?.file??'',sourceId=hash(sourceFile),archiveId='workbook-evidence-'+sourceId;
  const sourceNumber=/^R100-(\d+)/.exec(design.code)?.[1],archiveRecords=sourceNumber&&Number(sourceNumber)<=38;
  const archive={kind:'custom',id:archiveId,title:'源工作簿记录：'+sourceFile,columns:[{key:'出处',label:'出处'},{key:'内容',label:'原始记录'}],rows:[]};
  design.actual??={};design.actualSources??={};design.sourceMap??={};design.records??=[];design.workbookFacts??=[];design.samplingPlan??={points:[],nodes:[]};
  const plan=design.samplingPlan,process=new Map(),timelineRows=[];let declaredOnly=false;
  const blocked=new Set();
  for(const sheet of source.sheets){let closed=false;for(const [,cells]of rowGroups(sheet)){
    const strings=cells.map(c=>c.value).filter(v=>typeof v==='string'&&v.trim()),unique=[...new Set(strings)],label=String(cells.find(c=>c.col===1)?.value??'');
    if(endSection.test(label)||unique.length===1&&endSection.test(unique[0]))closed=true;
    if(/^步骤$/.test(label)&&strings.some(v=>/湿(?:渣|饼)|干品.*(?:质量|净重|g)/.test(v))&&strings.some(v=>/开始|结束|计划条件/.test(v)))closed=false;
    if(closed||strings.some(v=>calculationRow.test(v)))for(const c of cells)blocked.add(sheet.name+'!'+c.addr);
  }}
  for(const [key,value]of Object.entries(design.actual))if(key.startsWith('record-operations/')){
    const ref=design.actualSources[key],at=typeof ref==='string'?ref:ref?.address??(ref?.sheet&&ref?.cell?ref.sheet+'!'+ref.cell:null);
    if(at&&blocked.has(at)){
      delete design.actual[key];delete design.actualSources[key];
      (design.historicalNotes??=[]).push({severity:'warning',message:'已取消旧解析器对衡算表/标题单元格的操作实测映射；原始填写值 '+JSON.stringify(value)+' 保留在源工作簿完整记录中，未作为湿质量或含固率使用。',source:{file:sourceFile,address:at}});
    }
  }
  const put=(key,value,cell,sheet)=>{
    if(!present(value))return;const provenance={file:sourceFile,sheet:sheet.name,cell:cell.addr,address:sheet.name+'!'+cell.addr};
    if(distinctBranchSources(design.actualSources[key],provenance)){const [block,id,field]=key.split('/'),scoped=id+'@'+encodeURIComponent(sheet.name);key=[block,scoped,field].join('/');design.importedRows??=[];if(!design.importedRows.some(r=>r.block===block&&r.id===scoped))design.importedRows.push({block,id:scoped,label:sheet.name+' · '+(flatSteps(design.steps).find(s=>s.id===id)?.title??field)});}
    if(present(design.actual[key])&&design.actual[key]!==value){
      const conflict={key,workbookValue:design.actual[key],supplementaryValue:value,workbookSource:design.actualSources[key]??null,supplementarySource:provenance,resolution:'preserve-both'};
      design.recordConflicts??=[];if(!design.recordConflicts.some(c=>c.key===key&&c.supplementaryValue===value&&c.supplementarySource?.file===sourceFile&&c.supplementarySource?.address===provenance.address))design.recordConflicts.push(conflict);return;
    }
    design.actual[key]=value;design.actualSources[key]=provenance;design.sourceMap[sheet.name+'!'+cell.addr]=key;
  };
  for(const sheet of source.sheets){
    if(/^CP\d+-\d+/.test(sheet.name)&&!new RegExp('^'+escaped+'(?:[A-Z]?(?:批|后|检)|$)').test(sheet.name))continue;
    if(!/批记录|后处理|检测/.test(sheet.name))continue;
    let headers=[],inWorkup=false,section='';
    for(const [row,cells]of rowGroups(sheet)){
      cells.sort((a,b)=>a.col-b.col);const a=cells.find(c=>c.col===1),text=cells.map(c=>String(c.value)).join('；');
      const unique=[...new Set(cells.map(c=>c.value).filter(v=>typeof v==='string'&&v.trim()))];
      if(endSection.test(String(a?.value??''))||unique.length===1&&endSection.test(unique[0])){headers=[];inWorkup=false;section=String(a?.value??unique[0]);}
      if(/不取中间GPC|不安排中间GPC|过程不取中间GPC/.test(text)&&/仅.*终点|仅\d+\s*h/.test(text))declaredOnly=true;
      if(typeof a?.value==='string'&&/^(相对时间|时间点|样品编号|步骤|项目|观察阶段)/.test(a.value)){
        headers=cells.map(c=>({col:c.col,label:String(c.value)}));inWorkup=/^步骤$/.test(a.value)&&headers.some(h=>/湿(?:渣|饼)|干品.*(?:质量|净重|g)/.test(h.label))&&headers.some(h=>/开始|结束|计划条件/.test(h.label));section=String(a.value);
      }else if(cells.length===1&&typeof a?.value==='string'){section=a.value;}
      const field=c=>headers.find(h=>h.col===c.col)?.label??'',label=String(a?.value??section);
      for(const c of cells)if(!design.workbookFacts.some(f=>f.file===sourceFile&&f.sheet===sheet.name&&f.cell===c.addr))design.workbookFacts.push({file:sourceFile,sheet:sheet.name,cell:c.addr,label,field:field(c),section,value:c.value,...c.formula!==undefined?{formula:c.formula}:{}});
      const id='s'+hash(sheet.name)+'-r'+row,archiveKey=archiveId+'/'+id+'/内容';
      if(archiveRecords)archive.rows.push({id,source:{file:sourceFile,sheet:sheet.name,row},cells:[{t:escapeTemplate(sheet.name+'!'+cells[0].addr)},{input:true,type:'text'}]});
      // Explicit field names and addresses preserve conflicting values verbatim.
      if(archiveRecords){design.actual[archiveKey]=cells.map(c=>c.addr+(field(c)?' '+field(c):'')+'='+String(c.value)).join('；');design.actualSources[archiveKey]={file:sourceFile,sheet:sheet.name,row};}
      const matches=cells.flatMap(c=>typeof c.value==='string'?[...c.value.matchAll(new RegExp(escaped+'-(?:t)?(\\d+(?:\\.\\d+)?)h(?:-HPLC)?(?![A-Za-z0-9])','g'))].map(m=>({cell:c,id:m[0],hours:Number(m[1])})):[]);
      for(const sample of matches){process.set(sample.hours,{hours:sample.hours,id:'t-'+String(sample.hours).replace('.','_'),sampleId:sample.id.replace(/-HPLC$/,''),source:{file:sourceFile,sheet:sheet.name,cell:sample.cell.addr}});}
      if(headers.some(h=>/相对时间|时间点/.test(h.label))&&typeof a?.value==='number'){
        const suggestion=cells.find(c=>/建议钟点|计划钟点/.test(field(c)));
        if(a.value===0&&suggestion){const value=clock(suggestion.value);if(value!==null){
          const evidence={value,file:sourceFile,sheet:sheet.name,cell:suggestion.addr};plan.t0Evidence??=[];if(!plan.t0Evidence.some(e=>e.file===sourceFile&&e.sheet===sheet.name&&e.cell===suggestion.addr))plan.t0Evidence.push(evidence);
          if(!supplementary||/批记录表|实际记录/.test(sourceFile)){plan.t0Clock=value;plan.t0Source={file:sourceFile,sheet:sheet.name,cell:suggestion.addr};}
        }}
        if(matches.length)timelineRows.push({sheet,cells,hours:matches[0].hours,field});
      }
      for(const c of cells)if(typeof c.value==='string'){
        // A caption may abbreviate the second identifier after '/'. Expand only
        // a declared suffix; never synthesize an unmentioned phase/sample.
        const expanded=c.value.replace(/(CP\d+-\d+[A-Z]?)-(?:PRECIP|HEP|IPA|MeOH|S\d+)-C\s*\/\s*(PRECIP|HEP|IPA|MeOH|S\d+)-F/g,'$& $1-$2-F');
        for(const m of expanded.matchAll(workupRE)){
          const branch=m[1],suffix=m[2],nodeId=(branch?branch+'-':'')+suffix;
          if(!plan.nodes.some(n=>n.id===nodeId))plan.nodes.push({id:nodeId,sampleId:m[0],...(branch?{branch}:{}),kind:suffix==='FP'?'product':'workup',tests:['GPC'],source:{file:sourceFile,sheet:sheet.name,cell:c.addr}});
        }
      }
      const branch=/后处理([A-Z])/.exec(sheet.name)?.[1],operation=owningStep(design,label);
      const operationRow=inWorkup&&!cells.some(c=>typeof c.value==='string'&&calculationRow.test(c.value));
      if(operationRow&&operation){
        // Branch operations remain distinct even when their SOP is shared.
        const own=branch?design.historicalBranches?.[branch]?.steps.find(s=>token(s.title)===token(label)):operation;
        const operationId=own?.id??'history-wb-'+sourceId+'-'+hash(sheet.name)+'-'+row;
        if(!own){design.importedRows??=[];if(!design.importedRows.some(r=>r.id===operationId))design.importedRows.push({block:'record-operations',id:operationId,label:label+'（原分支记录）',branch,source:{file:sourceFile,sheet:sheet.name,row}});}
        for(const c of cells){const f=field(c),key=/湿(?:渣|饼).*g|湿饼.*净重|湿饼.*质量|干品.*净重/.test(f)?(/M1/.test(label)?'M1':/M2|最终产品/.test(label)?'M2':'质量'):/固(?:相|\/液)含固率/.test(f)?'含固率':/液相总质量/.test(f)?'液相质量':/液相(?:固含率|含固率)/.test(f)?'液相含固率':null;
          if(key){const value=/含固率/.test(key)?fraction(c.value):mass(c.value);if(value===null)continue;put('record-operations/'+operationId+'/'+key,value,c,sheet);if(own){own.records??=[];if(!own.records.some(r=>r.key===key))own.records.push({key,type:'number',consumer:'calculation'});}}
        }
      }
      if(operationRow&&/^(?:(?:真空)?干燥(?:恒重)?|恒重|续干与?恒重|摊料与真空干燥)M[12](?:最终产品)?$/.test(token(label))){
        // Some completed sheets wrote M1 in the Start column rather than the
        // mass column. A single entered numeric value on an explicit M1/M2 row
        // is retained at its true address, without silently dropping it.
        const massCells=cells.filter(c=>c.col>2&&mass(c.value)!==null&&c.formula===undefined),massField=/M1/.test(label)?'M1':'M2';
        if(massCells.length===1){
          const target=operation&&((massField==='M1'&&/干燥/.test(operation.title)&&!/恒重/.test(operation.title))||(massField==='M2'&&/恒重/.test(operation.title)))?operation.id:'history-wb-'+sourceId+'-'+hash(sheet.name)+'-'+row;
          if(target!==operation?.id){design.importedRows??=[];if(!design.importedRows.some(r=>r.id===target))design.importedRows.push({block:'record-operations',id:target,label:label+'（源记录）',source:{file:sourceFile,sheet:sheet.name,row}});}
          put('record-operations/'+target+'/'+massField,mass(massCells[0].value),massCells[0],sheet);
        }
      }
    }
  }
  if(!supplementary&&process.size){
    // Actual sample registrations, not blank event slots, define the plan.
    const previous=plan.points;plan.points=[...process.values()].map(point=>({...previous.find(p=>typeof p==='object'&&p.hours===point.hours),...point})).sort((a,b)=>a.hours-b.hours);
    if(declaredOnly)plan.sourceSamplingRule='仅按源工作簿明确登记的取样点；过程事件不建立样品';
  }else for(const point of process.values())if(!plan.points.some(p=>(typeof p==='number'?p:p.hours)===point.hours))plan.points.push({...point,actualOnly:true});
  for(const node of plan.nodes){
    const suffix=node.suffix??node.id.replace(/^[AB]-/,''),steps=node.branch?design.historicalBranches?.[node.branch]?.steps??[]:design.steps;
    const match=suffix==='FP'?/恒重|最终留样/:suffix==='PRE'?/PRE称量|PRE取样|沉降前取样/:/^PRECIP-[CF]$/.test(suffix)?/沉降过滤/:/^HEP-[CF]$/.test(suffix)?/HEP.*过滤|庚烷.*冲洗/:/^IPA-[CF]$/.test(suffix)?/IPA/:/^MeOH-[CF]$/.test(suffix)?/MeOH.*打浆/:/^S1(?:-[CF])?$/.test(suffix)?/打浆1过滤|MeOH打浆1|第一次.*打浆/:/^S2(?:-[CF])?$/.test(suffix)?/末次过滤|打浆2|第二次.*打浆/:null;
    const step=match?(suffix==='FP'?steps.findLast(s=>match.test(s.title)):steps.find(s=>match.test(s.title))):null;if(step)node.stepId=step.id;
    if(suffix==='FP')node.kind='product';
  }
  for(const r of timelineRows)for(const c of r.cells){const f=r.field(c),field=/实际钟点/.test(f)?'实际钟点':/^内温/.test(f)?'内温':/^外温/.test(f)?'外温':/搅拌.*rpm/.test(f)?'搅拌速度':/取样质量/.test(f)?'取样量':/外观|现象/.test(f)?'现象/备注':null;if(field)put('record-timeline/t-'+String(r.hours).replace('.','_')+'/'+field,c.value,c,r.sheet);}
  if(archiveRecords&&archive.rows.length&&!design.records.some(r=>r.id===archiveId))design.records.push(archive);
  // Solid fractions and drying masses are enabled by the declared step fields;
  // only liquid-side columns need an explicit option beyond those defaults.
  const operationFields=['液相质量','液相含固率'];let options=design.records.find(r=>r.kind==='operations');if(!options){options={kind:'operations',optionalColumns:[]};design.records.push(options);}options.optionalColumns=[...new Set([...options.optionalColumns??[],...operationFields.filter(f=>Object.keys(design.actual).some(k=>k.startsWith('record-operations/')&&k.endsWith('/'+f)))])];
  normalizeSamplingIdentity(design,{legacyAliases:true});return design;
}
