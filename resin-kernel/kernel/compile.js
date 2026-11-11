import {compileRecords,BATCH_WIDTH,stageLabel,routeBlocks,routeName,routeStepNumbers} from './compile-records.js';
import {variableValues} from './design2.js';
import {parseTemplate,serializeExpr} from './expr.js';
import {resolveSteps,compileChargeVars,samplingNodes,resolveCondition,chargeKeys,scheduleDesign} from './design2.js';
import {escapeTemplate} from './expr.js';
import {argumentExpr,instantiateModule} from './modules.js';
import {cleanKey} from './parse-workbook.js';
import {paramBinding,displayParamTemplate} from './param-bindings.js';
import {chinese,chineseTemplate,readableLabel,unitFormat,isExecutionParameter,samplingTimeText} from './language.js';
import {sopMaterialName,findMaterial} from './materials.js';
import {referencedVariables} from './explanation-notes.js';
import {generatedMaterialText} from './generated-material-text.js';
import {modernDocument,paperChargeBasis} from './document-layout.js';
import {preparedSolutions,soluteDoseText} from './prepared-solutions.js';
const clone=x=>structuredClone(x), T=t=>({t}), F=f=>({f}), input=(type='text')=>({input:true,type,...type==='time'?{fmt:'hh:mm'}:{}});
const widths14=[10,11,8,10,11,8,10,11,8,10,11,8,10,11],widths12=[15,16,13,11,12,12,12,12,12,14,14,16],widthsBatch=Array(BATCH_WIDTH).fill(10);
const title=(id,text)=>({type:'title',id,text});
const hash=s=>{let h=2166136261;for(const c of s)h=Math.imul(h^c.charCodeAt(0),16777619);return (h>>>0).toString(16);};
export const recordRowId=(stepId,key)=>`${stepId}~${hash(key)}`;
const nodeText=n=>escapeTemplate(n.sampleId);
export function clockFormula(hours,t0='[计划起点钟点]'){
  const time=`(${t0}+(${hours})/24)`;
  return `IF(INT(${time})=0,"",IF(INT(${time})=1,"次日","第"&(INT(${time})+1)&"日"))&TEXT(MOD(${time},1),"hh:mm")`;
}
/** Compile only semantic Design fields. Historical cell positions are never used. */
export function compile(design,library={}) {
  if(design.schema!=='lab-design/2')throw Error('compile 需要 lab-design/2');
  if(design.actualRevisionUid&&design.actualRevisionUid!==design.revisionUid)design={...design,actual:design.executions?.find(e=>e.revisionUid===design.revisionUid)?.actual??{},importedRows:[]};
  const steps=resolveSteps(design,library,{liftParams:true}),allCharges=[...design.charges,...steps.flatMap(s=>s.charges??[])],compiled=compileChargeVars(design,library,allCharges);
  const vars=clone(design.vars??[]).map(v=>({...v,label:generatedMaterialText(design,v.label??v.key,'vars/'+v.key+'/label',library)})),defined=new Map(vars.map(v=>[v.key,v]));
  for(const v of [...compiled.vars,...steps.flatMap(s=>s.paramVars??[])]){if(defined.has(v.key))throw Error(`编译变量与全局变量冲突：${v.key}`);defined.set(v.key,v);vars.push(v);}
  // A step text may still name an amount whose material line was removed
  // (A支DMAP质量 after the work-up became one route): it reads as missing
  // instead of stopping the whole SOP.
  for(const s of steps)for(const field of ['text','sourceText'])
    if(typeof s[field]==='string')s[field]=s[field].replace(/\{\[([^\]{}]+)\][^{}]*\}/g,(whole,key)=>defined.has(key)?whole:`〔缺：${key}〕`);
  const effectiveHomes=new Map();
  for(const v of design.vars.filter(v=>v.expr===undefined&&typeof v.value==='number'&&!v.technical)){
    const candidates=[];for(const s of steps.filter(s=>s.charges.length)){
      const module=library.modules?.find(m=>m.id===s.module),p=module?.params?.find(p=>paramBinding(design,p,s)?.key===v.key);if(!p)continue;
      const lifted=vars.find(v=>v.key===s.id+'~'+cleanKey(p.key));
      let local=lifted;if(!local){const template=instantiateModule({...module,text:'{{'+p.key+'}}'},s.args),expr=parseTemplate(template).find(p=>p.expr)?.expr;if(!expr)continue;local={key:s.id+'~effective~'+cleanKey(p.key),label:v.label??v.key,unit:v.unit,expr:serializeExpr(expr),technical:true,fmt:v.fmt};}
      const testValues=variableValues(lifted?vars:[...vars,local]),current=testValues.get(local.key),global=testValues.get(v.key);if(typeof current==='number'&&typeof global==='number'&&Math.abs(current-global)<1e-12||current===global)continue;if(!lifted)vars.push(local);
      candidates.push({...local,label:v.label??v.key,unit:v.unit,fmt:v.unit==='v/v'?'0.00':v.fmt});
    }if(candidates.length)effectiveHomes.set(v.key,candidates);
  }
  if(!defined.has('批次编号'))vars.unshift({key:'批次编号',label:'批次编号',role:'code',value:design.code});
  const clock=design.samplingPlan?.t0Clock??'10:00',parts=typeof clock==='string'?clock.split(':').map(Number):null;
  vars.push({key:'计划起点钟点',label:'计划起点钟点',unit:'hh:mm',fmt:'hh:mm',value:parts?(parts[0]*60+parts[1])/1440:clock});
  for(const v of [...vars])if(v.unit==='mol%'&&/%/.test(v.fmt??'')&&typeof v.value==='number'){const key=v.key+'~display-molpct';vars.push({key,label:'催化剂用量',unit:'mol%',fmt:'0',value:v.value*100});delete v.value;v.expr=`[${key}]/100`;v.label='催化剂摩尔比例（计算用）';v.unit='mol/mol';v.fmt='0.000';v.technical=true;}
  const planned=variableValues(vars);
  const baselineLabel=v=>{if(v&&!v.label?.includes('全局基准'))v.label=(v.label??v.key)+'（全局基准，本步已覆盖）';};
  for(const key of effectiveHomes.keys())baselineLabel(vars.find(v=>v.key===key));
  for(const c of compiled.charges){const source=vars.find(v=>v.key===c.sourceVar);if(!source)continue;const a=planned.get(source.key),b=planned.get(source.unit==='mL'?c.keys.volume:c.keys.mass);if(typeof a==='number'&&typeof b==='number'&&Math.abs(a-b)>1e-8)baselineLabel(source);}
  for(const v of vars){v.fmt=unitFormat(v.unit,v.fmt);if(/AddH.*(?:MonA|当量)/.test(v.key)){v.unit='eq';v.fmt='0.00';}}
  // Rewrite display formats only; values and expressions retain their full precision.
  const formatText=text=>{const parts=parseTemplate(text);return parts.map((p,i)=>{
    if(p.text!==undefined)return escapeTemplate(p.text);
    let fmt=p.fmt;if(p.expr.type==='ref'&&p.expr.kind==='var'){const v=vars.find(v=>v.key===p.expr.key);if(v)fmt=unitFormat(v.unit,fmt??v.fmt);}
    const quantityUnit=/^\s*(mL|g|°C|h|min|eq|mol%|%)(?![A-Za-z])/.exec(parts[i+1]?.text??'')?.[1];if(quantityUnit)fmt=unitFormat(quantityUnit,fmt);
    return `{${serializeExpr(p.expr)}${fmt?'|'+fmt:''}}`;
  }).join('').replace(/(\}|(?<![-A-Za-z\d])\d+(?:\.\d+)?)(?=°C|mL\b|g\b|h\b|min\b|mm\b)/g,'$1 ');};
  const normalized=text=>text.replace(/[\s，,；;。]/g,'');
  for(const step of steps){step.text=formatText(step.text).replace(/\{([^{}]+)\|0(?:\.0+)?%\}\s*mol%/g,'{($1)*100|0.0} mol%');step.controls=(step.controls??[]).map(formatText).filter(c=>!normalized(step.text).includes(normalized(c))).slice(0,2);
    for(const charge of compiled.charges.filter(c=>typeof planned.get(c.keys.volume)==='number')){
      for(const ref of [charge.keys.mass,charge.sourceVar,...vars.filter(v=>v.expr===`[${charge.keys.mass}]`).map(v=>v.key)].filter(Boolean)){const pattern=new RegExp('\\{\\['+ref.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\](?:\\|[^}]+)?\\}\\s*g');const token=step.text.match(pattern);if(token){step.text=step.text.replace(token[0],token[0]+`（约 {[${charge.keys.volume}]|${vars.find(v=>v.key===charge.keys.volume)?.fmt??'0.0'}} mL）`);break;}}
    }
    for(const c of compiled.charges){
      const mode=c.displayBasis?.mode??c.amount.mode,ofId=c.displayBasis?.of??c.amount.of,of=compiled.charges.find(c=>[c.id,c.materialId,c.as].includes(ofId))?.name??sopMaterialName(findMaterial(library,ofId)??{material:ofId}),basisValue=c.displayBasis?.value??c.amount.value;
      if(!['eq','molpct'].includes(mode))continue;
      const expr=argumentExpr(basisValue),basis=`（{${expr}|${mode==='eq'?'0.00':'0.0'}} ${mode==='eq'?'eq':'mol%'}/${of}）`;
      for(const ref of [c.keys.mass,c.sourceVar,...vars.filter(v=>v.expr===`[${c.keys.mass}]`).map(v=>v.key)].filter(Boolean)){
        const token=step.text.match(new RegExp('\\{\\['+ref.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\](?:\\|[^}]+)?\\}\\s*g'));
        if(token){step.text=step.text.replace(token[0],token[0]+basis).replace(/（初始MonA的\{[^}]+\}）/,'');break;}
      }
    }
  }
  for(const s of steps)s.text=s.text.replace(/g(（[^（）]*eq[^（）]*）)(（约[^（）]*mL）)/g,'g$2$1').replace(/加入(?=TES|AddH)/g,'加入 ');
  const nodes=samplingNodes(design,steps),homes=new Set();
  const home=(key,show)=>{if(!show){if(homes.has(key))return F(`[${key}]`);homes.add(key);}return {var:key,...show?{show}:{}};};
  // The research question written on the design page is the SOP purpose; the stored purpose is the fallback.
  const purpose=design.question?.trim()?design.question:generatedMaterialText(design,design.purpose,'purpose',library);
  const sop=[title('sop-title','{[批次编号]}树脂合成SOP'),{type:'table',id:'sop-header',rows:[{id:'strip',cells:[{t:'批号',span:1},{...T('{[批次编号]}'),span:2},{...T(escapeTemplate(design.title??'')),span:5},{...T('日期：'+(design.date??'')),span:2},{...T('设计签名'),span:2,style:'signature'},{...T('审核签名'),span:2,style:'signature'}]}]}, {type:'note',id:'purpose',text:design.purposeTemplate?formatText(displayParamTemplate(purpose)):escapeTemplate(purpose)}];
  // 投料表: each material once, in the unit weighed at the bench (solvents in mL),
  // how the amount is set, and the step it goes in. mmol only where it means something.
  const procedureRows=[...(design.procedure?.initialRows??[]),...(design.procedure?.steps??[]).flatMap(function walk(step){return [...step.rows??[],...(step.branches??[]).flatMap(b=>(b.steps??[]).flatMap(walk))];})];
  const ruleText=c=>{
    const row=procedureRows.find(r=>(r.chargeId??r.id)===c.id),a=row?.amount;if(!a)return paperChargeBasis(c,compiled.charges,vars,planned,steps);
    if(design.procedure?.scale?.basisRow===c.id)return '规模基准';
    const raw=a.knob?planned.get(a.knob):a.value,v=typeof raw==='number'?Number(raw.toPrecision(6)):raw,base=compiled.charges.find(x=>x.id===a.of)?.name??'';
    const routeShare=(()=>{const routes=design.procedure?.routes??[],route=routes.find(r=>r.id===c.branch),total=routes.reduce((sum,r)=>sum+Number(r.fraction??0),0);return route&&total>0?Number(route.fraction??0)/total:1;})();
    // Written as a share of the whole batch's base: say it per route (0.525 eq of all the TsOH at 50 % = 1.05 eq of this route's).
    const wholeBase=!a.ofRoute&&routeShare!==1&&['molRatio','molPct'].includes(a.rule)&&typeof v==='number'&&!compiled.charges.find(x=>x.id===a.of)?.branch;
    if(wholeBase)return a.rule==='molRatio'?`${Number((v/routeShare).toPrecision(4))} eq / 本路 ${base}`:`${Number((v/routeShare).toPrecision(4))} mol% / 本路 ${base}`;
    const own=a.ofRoute?'本路 ':'';
    return ({mass:'定量',volume:'定量',mmol:'定量',molRatio:`${v} eq / ${own}${base}`,molPct:`${v} mol% / ${own}${base}`,soluteWt:`配成 ${v} wt%`,solventWt:`配成 ${v} wt%`,solids:`固含 ${v} %`,volRatio:`${v} 倍 ${base} 体积`,perProduct:`${v} mL / g 预计产品`,perBasis:`${v} g / g ${base}`})[a.rule]??paperChargeBasis(c,compiled.charges,vars,planned,steps);
  };
  const routeNo=routeStepNumbers(steps);
  const chargeStep=c=>{const s=steps.find(s=>s.charges?.some(x=>x.id===c.id));if(!s)return '反应前投料';const no=routeNo.get(`${s.id}@${c.branch}`);return (no?no+' ':'')+(s.title??'');},inMl=c=>c.unit==='mL';
  const routeTag=c=>/^[A-Z]$/.test(c.branch??'')?`（${c.branch} 路）`:'';
  const chargeRow=(c,part=null)=>({id:c.id,cells:[{...T(escapeTemplate(part?`　其中 ${c.name}`:c.name+routeTag(c))),span:2},{...T(escapeTemplate(part?'':c.roleLabel??({reactant:'反应物',solvent:'溶剂',addition:'补加试剂',catalyst:'催化剂'})[c.role]??c.role??'')),span:2},{...T(escapeTemplate(part?.basis??ruleText(c))),span:3},{...home(inMl(c)?c.keys.volume:c.keys.mass),span:2},T(inMl(c)?'mL':'g'),c.role==='solvent'?{span:2}:{...home(c.keys.mmol),span:2},{...T(escapeTemplate(part?'':chargeStep(c))),span:2}]});
  // A solution made up beforehand is what goes in: one line for the solution, then its two parts, each with its own basis.
  const solutions=preparedSolutions(design.procedure?.steps),solutionOf=c=>solutions.find(s=>s.soluteId===c.id||s.solventId===c.id),byId=id=>compiled.charges.find(c=>c.id===id);
  const solutionRows=s=>{
    const solute=byId(s.soluteId),solvent=byId(s.solventId),wt=s.wtPct?.knob?planned.get(s.wtPct.knob):s.wtPct,dose=soluteDoseText(solute,compiled.charges,key=>planned.get(key),design.procedure?.routes??[]);
    return [{id:'solution-'+s.id,route:solute.branch,cells:[{...T(escapeTemplate(`${solute.name}/${solvent.name} 溶液${routeTag(solute)}`)),span:2},{...T(escapeTemplate(solute.roleLabel??'溶液')),span:2},{...T(escapeTemplate(`预先配好，${wt} wt% ${solute.name}`)),span:3},{f:`[${solute.keys.mass}]+[${solvent.keys.mass}]`,fmt:'0.00',span:2},T('g'),{span:2},{...T(escapeTemplate(chargeStep(solute))),span:2}]},
      chargeRow(solute,{basis:s.soluteFollows?dose:ruleText(solute)}),chargeRow(solvent,{basis:ruleText(solvent)})];
  };
  const chargesTable={type:'table',id:'sop-charges',title:'投料表',header:[['物料',2],['作用',2],['用量依据',3],['用量',2],['单位',1],['mmol',2],['加入步骤',2]].map(([key,span])=>({key,label:key,span})),rows:routeBlocks(design,compiled.charges.flatMap(c=>{const s=solutionOf(c);return !s?[chargeRow(c)]:compiled.charges.find(x=>solutionOf(x)===s)===c?solutionRows(s):[];}),r=>{const route=r.route??byId(r.id)?.branch;return /^[A-Z]$/.test(route??'')?route:null;},14)};
  // 物料参数 (说明): the constants behind the amounts, one row per material, so the
  // live workbook keeps every value it calculates with in a readable place.
  const constantCell=(key,fmt)=>{if(!vars.some(v=>v.key===key))return {};const cell=home(key);return cell.f?{...cell,fmt:vars.find(v=>v.key===key)?.fmt??fmt}:cell;};
  const basisUnit=c=>({mass:'g',volume:'mL',mmol:'mmol',eq:'eq',molpct:'mol%',solidFill:'%',perProduct:'mL/g'})[c.amount?.mode]??c.unit??'';
  const materialTable={type:'table',id:'material-constants',title:'物料参数',header:[['物料',2],['依据数值',2],['单位',1],['分子量 g/mol',2],['密度 g/mL',2],['纯度',1],['质量 g',2],['体积 mL',2]].map(([key,span])=>({key,label:key,span})),rows:routeBlocks(design,compiled.charges.map(c=>({id:c.id,cells:[{...T(escapeTemplate(c.name+(/^[A-Z]$/.test(c.branch??'')?`（${c.branch} 路）`:''))),span:2},{...constantCell(c.keys.amount,'0.###'),span:2},T(basisUnit(c)),{...constantCell(c.keys.mw,'0.00'),span:2},{...constantCell(c.keys.density,'0.000'),span:2},constantCell(c.keys.purity,'0.0%'),{...constantCell(c.keys.mass,'0.00'),span:2},{...constantCell(c.keys.volume,'0.0'),span:2}]})),r=>{const c=byId(r.id);return /^[A-Z]$/.test(c?.branch??'')?c.branch:null;},14)};
  sop.push(chargesTable);
  const planRefs=new Set(),scan=value=>{for(const m of JSON.stringify(value??'').matchAll(/\[([^\]\[]+)\]/g))planRefs.add(m[1]);};
  scan(compiled.charges.map(c=>c.amount));scan(steps.map(s=>[s.conditions,s.text,s.controls,s.args]));scan(nodes.map(n=>n.hoursExpr));
  for(const key of planRefs)scan(vars.find(v=>v.key===key)?.expr);
  const technical=[],groups=new Map([['配比',[]],['反应条件',[]],['后处理比例',[]]]),explanationVars=new Set(design.vars.filter(v=>v.expr===undefined&&!v.technical&&v.role!=='code'&&!isExecutionParameter(v)).map(v=>v.key));
  for(const v of vars.filter(v=>!homes.has(v.key))){if(!isExecutionParameter(v)||planned.get(v.key)===''||!planRefs.has(v.key)){if(planned.get(v.key)!==''&&planned.get(v.key)!=null)technical.push(v);}else groups.get(/THF|MeOH|打浆|产品|干燥|分离|稀释/.test(v.label??v.key)?'后处理比例':/温|时间|钟点|反应|AddH/.test(v.label??v.key)?'反应条件':'配比').push(v);}
  // Two parameters per row. Two knobs with the same label are told apart by the step that uses them.
  const usedBy=v=>steps.find(s=>JSON.stringify([s.conditions,s.charges.map(c=>compiled.charges.find(x=>x.id===c.id)?.amount)]).includes('['+v.key+']'))?.title;
  function paramGrid(id,label,list){const shown=list.map(v=>readableLabel(v.label??v.key)),rows=[];
    const labelOf=(v,i)=>shown.filter(x=>x===shown[i]).length>1&&usedBy(v)?`${shown[i]} · ${usedBy(v)}`:shown[i];
    const unitOf=v=>v.unit==='w/w'||/%/.test(v.fmt??'')||['1','1x','1:x','—'].includes(v.unit)?'':v.unit==='mol/mol'?'eq':v.unit??'';
    for(let i=0;i<list.length;i+=2){const chunk=list.slice(i,i+2),cells=[];
      for(let j=0;j<2;j++){const v=chunk[j];cells.push(v?{...T(escapeTemplate(labelOf(v,i+j))),span:3}:{span:3},v?{...home(v.key),span:2}:{span:2},v?{...T(unitOf(v)),span:2}:{span:2});}
      rows.push({id:`vars-${hash(chunk.map(v=>v.key).join('|'))}`,cells});}
    return {type:'table',id,title:label,rows};}
  for(const [group,list]of groups)if(list.length){
    const effective=list.flatMap(v=>{const candidates=effectiveHomes.get(v.key);if(candidates?.length&&!technical.some(t=>t.key===v.key))technical.push(v);return candidates??[v];});
    sop.push(paramGrid('params-'+group,group,effective));
  }
  const flowRows=[],routedRows=[],recordCells=new Map(),schedule=scheduleDesign(design,library,{steps,vars});let stage=null;
  for(const [index,s] of steps.entries()){let badge='';if(s.conditions){const c=s.conditions;const conditionText=value=>{if(value==null)return '';return String(value).replace(/\{\{([^}]+)\}\}/g,(_,k)=>`{${typeof s.args[k]==='number'?s.args[k]:s.args[k]??0}}`).replace(/^\[([^\]]+)\]$/,'{[$1]}');};badge=[c.tempC!=null?conditionText(c.tempC)+' °C':'',c.hours!=null&&resolveCondition(c.hours,s.args,vars)>0?(()=>{const hours=resolveCondition(c.hours,s.args,vars),expr=String(c.hours).replace(/\{\{([^}]+)\}\}/g,(_,key)=>'('+argumentExpr(s.args[key])+')');return '{('+expr+')'+(hours<2?'*60':'')+'|'+(hours<2?'0':Number.isInteger(hours)?'0':'0.0')+'} '+(hours<2?'min':'h');})():''].filter(Boolean).join(' · ');}
    // Reaction steps show when they start on the t=0 clock; work-up runs as it comes.
    const time=schedule.find(t=>t.stepId===s.id);if(time&&s.stage!=='workup'&&time.start>0)badge+=(badge?'\n':'')+'t = '+(time.timing==='actual'?Number(time.start.toFixed(1)):'{ROUND('+time.startExpr+',1)|0.#}')+' h';
    // A split work-up: the split says so; the steps after it are printed once per route, below.
    const firstRouted=steps.find(x=>x.routes?.length);
    if(s===firstRouted)badge+=(badge?'\n':'')+`分成 ${s.routes.join('、')} ${s.routes.length} 路`;
    const name=s.stage==='workup'?'后处理':'准备与反应';if(name!==stage){stage=name;flowRows.push({id:'stage-'+(s.stage==='workup'?'workup':'reaction'),cells:[{...T(name),span:14,style:'section'}]});}
    const record={...T(''),span:2};recordCells.set(s.id,record);
    const points=(s.controls??[]).length?['要点：'+s.controls.join('；')]:[];
    const flowRow=(id,number)=>({id,...s.sourceText?{sourceText:s.sourceText}:{},cells:[{v:number,style:'stepNumber'},{...T(s.titleTemplate?formatText(s.titleTemplate):escapeTemplate(s.title??'')),span:3,style:'stepTitle'},{...T(badge),span:2},{...T([s.text,...points].filter(Boolean).join('\n')),span:6},record]});
    if(s.routes?.length&&s!==firstRouted)for(const route of s.routes)routedRows.push({route,row:flowRow(`${s.id}@${route}`,routeNo.get(`${s.id}@${route}`))});
    else flowRows.push(flowRow(s.id,index+1));
  }
  // Each route is a complete procedure of its own, read top to bottom without skipping.
  for(const route of steps.find(x=>x.routes?.length)?.routes??[]){
    flowRows.push({id:'route-'+route,cells:[{...T(escapeTemplate(routeName(design,route))),span:14,style:'section',route}]});
    flowRows.push(...routedRows.filter(r=>r.route===route).map(r=>r.row));
  }
  const flow={type:'table',id:'sop-flow',title:'操作流程',header:[{key:'序号',label:'序号',span:1},{key:'title',label:'步骤',span:3},{key:'条件',label:'条件',span:2},{key:'text',label:'操作',span:6},{key:'记录',label:'记录',span:2}],rows:flowRows};
  const sampleNote=design.samplingPlan?.note?.trim()?{type:'note',id:'sop-sample-note',text:'制样：'+design.samplingPlan.note.trim()}:null;
  sop.push(flow,{type:'table',id:'sop-samples',header:[['编号',5],['时间 / 阶段',4],['检测',5]].map(([key,span])=>({key,label:key,span})),rows:routeBlocks(design,nodes.map(n=>({id:n.id,route:n.route!==undefined?n.route:/^([A-Z])-/.exec(n.id)?.[1]??null,cells:[{...T(nodeText(n)),span:5},{...T(n.hoursExpr?`{${n.hoursExpr}|0.#} h`:escapeTemplate(stageLabel(n.stage??n.id)??samplingTimeText(n,steps))),span:4},{...T((n.tests??[]).join(' / ')),span:5}]})),r=>r.route,14)},...sampleNote?[sampleNote]:[]);
  const records=compileRecords(design,{steps,charges:compiled.charges,nodes,vars,values:planned,clockFormula});
  const RECORD_NAMES={质量:'称重',湿饼质量:'湿饼',液相质量:'液相',液相含固率:'液相含固率'};
  for(const s of steps){const water=s.routes?.length?s.records.filter(r=>r.key==='累计水量'):[];s.records=s.records.filter(r=>records.declarationMap[s.id+'/'+r.key]);const cell=recordCells.get(s.id);if(cell)cell.t=escapeTemplate([...water,...s.records.filter(r=>r.type==='number'&&!water.includes(r))].map(r=>RECORD_NAMES[r.key]??r.key).join('、'));}
  const batch=records.batch;
  for(const r of (design.records??[]).filter(r=>r.kind==='custom'&&r.enabled!==false&&!r.disabled)){
    const header=r.columns.map(c=>typeof c==='string'?{key:c,label:c}:{...c});
    // A two-column retained source table uses the page, not two narrow columns
    // at the left edge. Stable table/row/column keys are unchanged.
    if(header.every(c=>c.span==null))for(const [i,c]of header.entries())c.span=Math.floor(BATCH_WIDTH/header.length)+(i===header.length-1?BATCH_WIDTH%header.length:0);
    batch.push({type:'table',id:r.id??'custom-'+hash(r.title??''),title:r.title,header,rows:clone(r.rows??[]).map(row=>({...row,cells:row.cells.map((c,i)=>({...c,span:c.span??header[i]?.span??1}))}))});
  }
  const sheets=[{name:'SOP',landscape:true,widths:widths14,blocks:sop},{name:'批记录',landscape:true,widths:widthsBatch,blocks:batch},{name:'检测记录',landscape:true,widths:widths12,blocks:[title('tests-title','{[批次编号]}检测记录'),records.sampleTable,records.hplcTable].filter(Boolean)}];
  const used=referencedVariables([sheets,materialTable],vars),noteBlocks=[materialTable],neededTechnical=technical.filter(v=>used.has(v.key));if(neededTechnical.length)noteBlocks.push(paramGrid('technical-vars','计算参数',neededTechnical));
  if(noteBlocks.length)sheets.push({name:'参数',landscape:true,widths:widths14,blocks:noteBlocks});
  const unusedTechnical=technical.filter(v=>!used.has(v.key));
  if(unusedTechnical.length)sheets.push({name:'参数归档',hidden:true,landscape:true,widths:widths14,blocks:[paramGrid('archived-vars','来源保留参数（当前执行文件未引用）',unusedTechnical)]});
  if(records.rules.length)sheets.push(records.checks);
  sheets.push(...clone(design.extras??[]));
  const actual=clone(design.actual??{});for(const s of steps)if(s.records.some(r=>r.key==='液相含固率')&&!s.records.some(r=>r.key==='含固率')){const old=actual['record-operations/'+s.id+'/含固率'];if(old!=null&&actual['record-operations/'+s.id+'/液相含固率']==null)actual['record-operations/'+s.id+'/液相含固率']=old;}for(const [id,starts]of Object.entries(records.operationStarts)){const source=starts.map(id=>actual[`record-timeline/${id}/实际钟点`]).find(v=>v!=null);if(source!=null&&actual[`record-timeline/${id}/操作起点`]==null)actual[`record-timeline/${id}/操作起点`]=source;}for(const [key,value]of Object.entries(actual)){const [block,id,col]=key.split('/'),alias=records.timelineAliases[id];if(block==='record-timeline'&&alias&&actual[`${block}/${alias}/${col}`]==null)actual[`${block}/${alias}/${col}`]=value;}if(actual['record-t0/actual/实际钟点']!=null)actual['record-timeline/t-0/实际钟点']=actual['record-t0/actual/实际钟点'];
  for(const v of vars){v.label=readableLabel(v.label??v.key);if(v.expr){const refs=[...new Set([...v.expr.matchAll(/\[([^\]]+)\]/g)].map(m=>m[1]))];if(refs.length)v.expr=`IF(OR(${refs.map(key=>`[${key}]="待指定"`).join(',')}),"待指定",${v.expr})`;}}
  const localize=value=>{if(Array.isArray(value)){value.forEach(localize);return;}if(!value||typeof value!=='object')return;for(const [key,item]of Object.entries(value))if(['t','text','title','label'].includes(key)&&typeof item==='string')value[key]=chineseTemplate(item);else if(key!=='sourceText')localize(item);};sheets.forEach(localize);compiled.charges.forEach(c=>c.name=chinese(c.name));
  return modernDocument({schema:'lab-design/1',uid:design.uid,revisionCode:design.revisionCode,revisionAliases:clone(design.revisionAliases??[]),revisionUid:design.revisionUid,batchUid:design.executionUid??design.executions?.find(e=>e.revisionUid===design.revisionUid)?.uid??design.batch?.uid,id:design.id,code:design.code,parentCode:design.parentCode,title:design.title,revision:design.revision??1,vars,sheets,actual,modular:{schema:'lab-design/2',steps:steps.map(s=>({id:s.id,title:s.title,text:s.text,controls:s.controls,records:s.records,sourceText:s.sourceText,...s.vesselId?{vesselId:s.vesselId}:s.apparatusId?{apparatusId:s.apparatusId}:{}})),nodes,charges:compiled.charges,declarationMap:records.declarationMap,deviationRules:records.rules},sourceMap:clone(design.sourceMap??{}),actualFlags:clone(design.actualFlags??{}),importedRows:clone(design.importedRows??[])},design);
}
