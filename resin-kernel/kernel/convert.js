import {stableUid} from './identity.js';
import {blankDesign} from './design2.js';
import {findMaterial,materialList,materialFromLabel} from './materials.js';
import {moduleList,normalizeModuleTitle,moduleCategory} from './modules.js';
import {distillStep} from './distill.js';
import {layoutDoc,resolvePlaced,rangePlacements} from './layout.js';
import {evaluateDoc} from './evaluate.js';
import {parseExpr,mapRefs,serializeExpr,parseTemplate,escapeTemplate,evaluateExpr} from './expr.js';
import {extractSopSteps} from './sop-source.js';
import {compile} from './compile.js';
import {recoverHistory} from './convert-history.js';
import {assignVarRoles} from './var-roles.js';
import {extractDurations,extractAmounts} from './durations.js';
import {paramBinding} from './param-bindings.js';
import {bindSourceSchedule} from './schedule-bindings.js';
import {normalizeSamplingIdentity} from './sampling-identity.js';
import {sourceRecordOptions} from './record-options.js';
import {dropUnusedVars} from './drop-source-params.js';
const clone=x=>structuredClone(x);
/** Historical coordinates are provenance only. No sheets are carried into the plan. */
export function convertDoc(doc,library={}) {
  const layout=layoutDoc(doc),values=evaluateDoc(doc),canonical=/^(CP\d+-\d+[A-Z]?)(?:_|$)/.exec(doc.code)?.[1]??/^(CP\d+-\d+[A-Z]?)/.exec(doc.title)?.[1]??doc.code,design=blankDesign(canonical),sourceMap={},warnings=[];
  Object.assign(design,{id:doc.id===doc.code?canonical:doc.id,aliases:[canonical,doc.id,doc.code],parentCode:doc.parentCode??'',title:doc.title??'',source:clone(doc.source),vars:clone(doc.vars),notes:[]});
  design.uid=doc.uid??stableUid('lab-legacy-design',canonical);design.revisionUid=doc.revisionUid??stableUid(design.uid,'revision:'+design.revision);design.batch={uid:doc.batchUid??stableUid(design.uid,'batch:1'),code:design.code,aliases:[design.code]};
  const inputMap=new Map(),sourceStepMap=new Map(),vars=new Map(doc.vars.map(v=>[v.key,v])),hoisted=new Map(),fromCells=new Set(),sourceSteps=extractSopSteps(doc,layout,values);
  let inlined=false;
  const sopSheets=new Set(sourceSteps.map(s=>s.sheet)),extraSheets=doc.sheets.filter(s=>!sopSheets.has(s.name)&&!/^SOP$|批记录|后处理|检测|依据|说明/.test(s.name));
  for(const p of layout.inputs)if(extraSheets.some(s=>s.name===p.sheet))inputMap.set(p.key,p.key);
  function symbolize(ast,context){
    if(ast.type==='call'&&(/\bCELL\(/.test(serializeExpr(ast))||containsRange(ast))){
      const value=evaluateExpr(ast,r=>values.get(resolvePlaced(layout,r,context)?.fullAddr),(a,b)=>rangePlacements(layout,a,b,context).map(p=>values.get(p?.fullAddr)),r=>resolvePlaced(layout,r,context)?.sheet);
      warnings.push(`工作表环境或区域计算按来源值保留：${context.fullAddr}`);return {type:'literal',value};
    }
    if(ast.type==='ref')return expandRef(ast,context);
    if(ast.left)return {...ast,left:symbolize(ast.left,context),right:symbolize(ast.right,context)};
    if(ast.arg)return {...ast,arg:symbolize(ast.arg,context)};
    if(ast.args)return {...ast,args:ast.args.map(a=>symbolize(a,context))};
    return ast;
  }
  function containsRange(ast){return ast.type==='range'||!!ast.args?.some(containsRange)||!!ast.left&&containsRange(ast.left)||!!ast.right&&containsRange(ast.right);}
  // Rewrite a source reference into vars, a generated record key or the cell value, never old addresses.
  function expandRef(ref,context,active=new Set()) {
    if(ref.kind==='var')return {type:'ref',...ref};
    const p=resolvePlaced(layout,ref,context);if(!p)return {type:'literal',value:0};
    if(p.cell.var)return {type:'ref',kind:'var',key:p.cell.var};
    if(inputMap.has(p.key))return {type:'ref',kind:'cell',key:inputMap.get(p.key)};
    // Unmapped cells keep the value they had in the workbook (no 来源参数 vars).
    inlined=true;return {type:'literal',value:values.get(p.fullAddr)??''};
  }
  const rewrite=(text,context)=>parseTemplate(text).map(p=>p.text!==undefined?escapeTemplate(p.text):`{${serializeExpr(symbolize(p.expr,context))}${p.fmt?'|'+p.fmt:''}}`).join('');
  for(const s of sourceSteps){
    const side=s.right?'right':'left';
    const category=moduleCategory(s.title),name=normalizeModuleTitle(s.title,s.text),module=moduleList(library).find(m=>m.category===category&&normalizeModuleTitle(m.name)===name),context=s.context;
    const sourceText=rewrite(s.text,context),distilled=distillStep(sourceText,{stage:side==='right'?'workup':'reaction',category});
    const step={id:s.id,module:module?.id??'custom',category,title:s.title,stage:side==='right'?'workup':['装置','准备'].includes(category)?'prep':'reaction',args:{},...distilled};
    if(/降至THF稀释温度/.test(s.title)){
      const clauses=step.text.split(/[，,]/),start=clauses.findIndex(c=>/继续冷却|冷却至|降至/.test(c));
      if(start>=0){step.notes.push('后处理依次完成稀释、甲醇沉降、过滤、两次打浆和干燥');step.text=clauses[start];}
    }
    if(/整批溶剂准备/.test(s.title)){step.notes.push(step.text);step.text='按投料表分装THF及沉降、两次打浆用MeOH；沉降MeOH控制20–25 °C';}
    if(/固液记录与样品归档/.test(s.title)){step.notes.push(step.text);step.text='汇总各阶段固/液相质量与含固率、干品及取样损失；按节点留样，归档谱图';}
    // Conditions are extracted from the complete source, before routing constraints.
    const ownConditions=/降温|降至|冷却/.test(s.title)?sourceText.slice(Math.max(0,sourceText.search(/继续冷却|冷却至|降至/))):sourceText;
    const temp=/(\{[^{}]+\}|\d+(?:\.\d+)?)(?:±\d+)?\s*(?:°C|℃)/.exec(ownConditions),hours=/(\{[^{}]+\}|\d+(?:\.\d+)?)\s*h\b/.exec(ownConditions);
    const numeric=token=>token?.startsWith('{')?token.slice(1,-1).split('|')[0]:token===undefined?undefined:Number(token);
    if(/升温|恒温|降温|降至|冷却|干燥|续干|恒重/.test(s.title)&&temp)step.conditions={kind:/升温|降温|降至|冷却/.test(s.title)?'ramp':'hold',tempC:numeric(temp[1]),hours:numeric(hours?.[1])??0};
    // Historical SOP conditions remain visible in the step itself, including
    // ramp ranges and feed/hold timing that sentence distillation can compress.
    step.text=sourceText;
    const separator='\n---CONTROL---\n',amounts=extractAmounts([step.text,...step.controls].join(separator),p=>`{{${p.key}|0.0}}`),timed=extractDurations(amounts.text,p=>`{{${p.key}|0}}`),parts=timed.text.split(separator);step.text=parts[0];step.controls=parts.slice(1);step.amountParams=amounts.params;step.durationParams=timed.params;for(const p of [...amounts.params,...timed.params])step.args[p.key]=p.default;
    step.generatedTextBaseline=step.text;
    const primary=timed.params.find(p=>/打浆时长|续搅时长|干燥时长/.test(p.label))??timed.params.find(p=>p.label==='时长');
    if(primary&&(/打浆时长|续搅时长|干燥时长/.test(primary.label)||step.conditions?.hours))step.conditions={...step.conditions,kind:step.conditions?.kind??'hold',hours:`{{${primary.key}}}${primary.unit==='min'?'/60':''}`};
    design.steps.push(step);sourceStepMap.set(context.key,`sop-flow/${s.id}/text`);
  }
  if(new Set(sourceSteps.map(s=>s.sheet)).size>1)warnings.push('来源含多张 SOP：按工作表顺序保留步骤，未自动推断为平行分支。');
  // Source variables remain authoritative. Charge expressions point at those vars;
  // compiled charge keys have a namespace so the original formulas never collide.
  const materialLabel=v=>v.label.replace(/^(?:反应|[AB](?:支|路)?(?:预溶)?)/,'');
  const massVars=doc.vars.filter(v=>v.unit==='g'&&!/总理论|满料|累计|净树脂|MW|分子量/.test(v.key)&&materialFromLabel(library,materialLabel(v)));
  for(const v of massVars){
    const match=materialFromLabel(library,materialLabel(v));if(!match)continue;
    const branch=/^([AB])(?:支|路|预溶)/.exec(v.label)?.[1];
    const mw=doc.vars.find(x=>/MW|分子量/.test(x.label)&&[match.id,match.name,...match.aliases??[]].some(a=>x.label.replace(/无水/g,'').replace(/MW|分子量/g,'').trim().toLowerCase()===a.replace(/无水/g,'').toLowerCase())),isAddition=/AddH/.test(v.key),step=branch?design.steps.find(s=>new RegExp(branch+'.*DMAP.*预溶').test(s.title)):isAddition?design.steps.find(s=>/加AddH/.test(s.title)&&!/取样/.test(s.title)):null;
    const charge={displayMode:match.id==='xylene'?'solidFill':v.expr===undefined?'mass':undefined,id:`charge-${match.id}${branch?'-'+branch:''}`,label:v.label,material:match.id,...branch?{branch}:{},role:match.kind==='溶剂'||/xylene|THF|MeOH/.test(match.id)?'solvent':branch&&match.id==='DMAP'?'quench':isAddition?'addition':/TsOH|催化/.test(match.id+match.kind)?'catalyst':'reactant',amount:{mode:'expr',value:`[${v.key}]`,unit:'g'},...(mw?{mw:values.get(layout.varToAddr.get(mw.key))}:{}),...(isAddition&&vars.has('计划纯度')?{purity:values.get(layout.varToAddr.get('计划纯度'))}:{}),sourceVar:v.key};
    const usedIds=new Set([...design.charges,...design.steps.flatMap(s=>s.charges??[])].map(c=>c.id));if(usedIds.has(charge.id)){let n=2;while(usedIds.has(charge.id+'-'+n))n++;charge.id+='-'+n;}
    if(match.id==='TsOH'&&vars.has('TsOH用量(mol%)'))charge.displayBasis={mode:'molpct',of:'MonB',value:'[TsOH用量(mol%)]*100'};
    if(match.id==='AddH'&&vars.has('AddH初始MonA'))charge.displayBasis={mode:'eq',of:'MonA',value:'[AddH初始MonA]'};
    if(match.id==='MonA'&&vars.has('MonB:MonA摩尔比'))charge.displayBasis={mode:'eq',of:'MonB',value:'[MonB:MonA摩尔比]'};
    if(step)(step.charges??=[]).push(charge);else design.charges.push(charge);if(!design.materials.some(m=>m.ref===match.id))design.materials.push({ref:match.id,as:match.id});
  }
  for(const v of doc.vars.filter(v=>v.unit==='mL'&&/THF|MeOH|打浆/.test(v.label)&&!(/温|密度/.test(v.label)))){
    const material=/THF/.test(v.label)?'THF':'MeOH',slurry=/打浆|浆洗/.test(v.label),number=/2/.test(v.label)?2:1;
    const step=design.steps.find(s=>s.stage==='workup'&&(/THF/.test(v.label)?/THF.*稀释|稀释.*中和/.test(s.title):slurry?(number===2?/打浆2|第二次.*打浆/:/打浆1|第一次.*打浆/).test(s.title):/MeOH沉降/.test(s.title)));
    if(!step)continue;let id=`charge-${material}-${slurry?'slurry'+number:'main'}`;
    const existing=design.steps.flatMap(s=>s.charges??[]).find(c=>c.id===id);
    if(existing){if(values.get(layout.varToAddr.get(existing.sourceVar))===values.get(layout.varToAddr.get(v.key)))continue;warnings.push(`存在不同用量的同类溶剂，保留独立投料：${v.label}`);id+='-'+design.steps.flatMap(s=>s.charges??[]).length;}
    const perBranch=/每支/.test(v.label)?[...new Set(doc.sheets.map(s=>/后处理([A-Z])/.exec(s.name)?.[1]).filter(Boolean))]:[];
    for(const branch of perBranch.length?perBranch:[null])(step.charges??=[]).push({id:id+(branch?'-'+branch:''),displayMode:slurry||material==='THF'?'perProduct':'volume',label:(branch?branch+'支 ':'')+v.label,material,...branch?{branch}:{},role:'solvent',amount:{mode:'expr',value:`[${v.key}]`,unit:'mL'},sourceVar:v.key});if(!design.materials.some(m=>m.ref===material))design.materials.push({ref:material,as:material});
  }
  // Read the historical plan's evaluated relative times and clock, then regenerate
  // clocks from the plan. Empty overnight slots are intentionally omitted.
  for(const sheet of doc.sheets)for(const block of sheet.blocks.filter(b=>b.type==='table')){
    const keys=block.header?.map(h=>h.key)??[],timeIndex=keys.findIndex(k=>/相对时间/.test(k));
    if(timeIndex>=0){const col=keys[timeIndex];for(const r of block.rows){const p=layout.byKey.get(`${block.id}/${r.id}/${col}`),hour=values.get(p?.fullAddr);if(typeof hour==='number'&&!design.samplingPlan.points.some(point=>(typeof point==='number'?point:point.hours)===hour)){
      const ast=p?.cell.f?parseExpr(p.cell.f):null,simple=ast?.type==='ref'||ast?.type==='binary'&&ast.op==='+'&&ast.left.type==='ref'&&ast.right.type==='ref';
      const hoursExpr=p?.cell.var?`[${p.cell.var}]`:simple?serializeExpr(symbolize(ast,p)):undefined;
      design.samplingPlan.points.push(hoursExpr?{id:`t-${String(hour).replace('.','_')}`,hours:hour,hoursExpr,sourceAddress:p.fullAddr}:hour);
    }}}
    for(const r of block.rows)if(/计划达温时刻|计划.*t=0/.test(r.id)){const p=layout.byKey.get(`${block.id}/${r.id}/${keys[1]}`);if(typeof values.get(p?.fullAddr)==='number')design.samplingPlan.t0Clock=values.get(p.fullAddr);}
  }
  const hourOf=point=>typeof point==='number'?point:point?.hours;
  design.samplingPlan.points.sort((a,b)=>hourOf(a)-hourOf(b));
  const addNode=(id,step,label=id,hours)=>{if(!design.samplingPlan.nodes.some(n=>n.id===id))design.samplingPlan.nodes.push({id,label,tests:['GPC'],...(step?{stepId:step.id}:{}),...(hours!=null?{hours}:{})});};
  // Explicit identifiers in the source are retained verbatim (not inferred from addresses).
  const sourceTexts=design.steps.map(s=>s.sourceText).join('\n');
  for(const id of [...new Set(sourceTexts.match(/PRE-AddH|AddH-80C-1h|\bPRE\b|\bPRECIP\b|\bS1\b|\bS2\b|\bFP\b/g)??[])]){const step=design.steps.find(s=>id==='PRE'?/\bPRE\b(?!-)/.test(s.sourceText):s.sourceText.includes(id));addNode(id,step,id,id==='PRE-AddH'?hourOf(design.samplingPlan.points.at(-1)):undefined);}
  for(const sheet of doc.sheets.filter(s=>/后处理|检测/.test(s.name)))for(const p of layout.sheets.find(s=>s.name===sheet.name).cells){const value=values.get(p.fullAddr);if(typeof value!=='string')continue;for(const match of value.matchAll(new RegExp(doc.code.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'-(PRE(?:-AddH)?|PRECIP(?:-[CF])?|S[12](?:-[CF])?|FP|AddH-80C-1h)\\b','g'))){const id=match[1],base=id.replace(/-[CF]$/,'');const step=design.steps.find(s=>base==='PRE'?/PRE称/.test(s.title):base==='PRECIP'?/沉降过滤/.test(s.title):base==='S1'?/打浆1/.test(s.title):base==='S2'?/打浆2/.test(s.title):base==='FP'?/恒重/.test(s.title):false);addNode(id,step);}}
  // All original variable expressions are rewritten; unusual cell-based expressions
  // remain live when mapped, otherwise their evaluated constants are explicit.
  for(const v of design.vars)if(v.expr&&vars.has(v.key)){const context=layout.byAddr.get(layout.varToAddr.get(v.key));inlined=false;v.expr=serializeExpr(symbolize(parseExpr(v.expr),context));if(inlined)fromCells.add(v.key);}
  // Only explanatory material goes to notes. Old record grids never enter extras.
  for(const sheet of doc.sheets.filter(s=>/依据|说明/.test(s.name)))for(const block of sheet.blocks){
    if(block.type==='note')design.notes.push(rewrite(block.text,layout.byKey.get(`${block.id}/text/A`)));
    if(block.type==='table')for(const row of block.rows){const explanation=row.cells.filter(c=>c.t!==undefined&&!c.t.includes('{')&&c.t.length>10).map(c=>c.t).filter(t=>!/^n\s*[（(]MonA[）)]\s*[×*]\s*18\.015/.test(t));design.notes.push(...explanation);}
  }
  // Truly unclassified data sheets are preserved, with their references lowered
  // to globals/hoisted values. SOP and record layouts never enter this path.
  for(const sheet of extraSheets){const extra=clone(sheet);for(const block of extra.blocks){
    if(['title','section','note'].includes(block.type))block.text=rewrite(block.text,layout.byKey.get(`${block.id}/text/A`));
    if(block.type==='table')for(const row of block.rows)for(const cell of row.cells){const p=layout.sheets.find(s=>s.name===sheet.name).cells.find(p=>p.block===block.id&&p.key.split('/')[1]===row.id&&p.cell===sheet.blocks.find(b=>b.id===block.id).rows.find(r=>r.id===row.id).cells[row.cells.indexOf(cell)]);
      if(cell.var){const v=vars.get(cell.var);if(cell.show==='label')cell.t=escapeTemplate(v.label);else if(cell.show==='unit')cell.t=escapeTemplate(v.unit??'');else{cell.f=`[${cell.var}]`;cell.fmt??=v.fmt;}delete cell.var;delete cell.show;}
      else if(cell.f&&p)cell.f=serializeExpr(symbolize(parseExpr(cell.f),p));else if(cell.t&&p)cell.t=rewrite(cell.t,p);
    }
  }design.extras.push(extra);}
  const sourceBook=layout.sheets.find(s=>s.name==='SOP');
  const description=sourceBook?.cells.find(p=>p.row===2&&p.col>1&&typeof values.get(p.fullAddr)==='string'&&values.get(p.fullAddr).length>20);
  if(description){design.purpose=rewrite(description.cell.t??String(values.get(description.fullAddr)),description);design.purposeTemplate=true;}
  for(const sheet of layout.sheets){let nmr=false;for(const p of sheet.cells){const text=values.get(p.fullAddr);if(typeof text!=='string')continue;
    if(p.style==='section'&&/NMR/.test(text)||/^[A-Z]\. .*NMR/.test(text))nmr=true;
    if(/变化不能|未扣添加剂|表观分离比不等于|不得仅凭/.test(text))design.notes.push(rewrite(p.cell.t??text,p));
    if(nmr)for(const n of design.samplingPlan.nodes)if(text.startsWith(design.code+'-')&&text.endsWith('-'+n.id))n.tests=['GPC','NMR'];
  }}
  for(const n of design.samplingPlan.nodes)if(n.id==='AddH-80C-1h')n.hours=(hourOf(design.samplingPlan.points.at(-1))??8)+(design.vars.find(v=>v.key==='AddH加入起计时(h)')?.value??1);
  for(const s of design.steps){if(['准备','记录'].includes(s.category)&&/整批溶剂准备|固液记录与样品归档/.test(s.title)){s.recording=false;s.records=[];}
    if(/过滤/.test(s.title)&&/湿饼.*滤液/.test(s.sourceText))for(const key of ['湿饼质量','液相质量'])if(!s.records.some(r=>r.key===key))s.records.push({key,type:'number',consumer:'calculation'});
  }
  if(design.vars.some(v=>v.key==='沉降MeOHTHF'))design.notes=design.notes.map(text=>text.replace(/沉降MeOH\s*[=＝]\s*THF\s*[×*]\s*\d+(?:\.\d+)?/g,'沉降MeOH = THF×{[沉降MeOHTHF]|0.0}'));
  for(const step of design.steps){const module=moduleList(library).find(m=>m.id===step.module);step.paramBindings=Object.fromEntries((module?.params??[]).flatMap(p=>{const bound=paramBinding(design,p,step);return bound?[[p.key,bound.key]]:[];}));}
  // Vars built on inlined workbook cells (e.g. the old node-code column) go unless used.
  design.vars=dropUnusedVars(design,fromCells).doc.vars;
  normalizeSamplingIdentity(design,{legacyAliases:true});bindSourceSchedule(design,{source:true});
  design.records=sourceRecordOptions(doc,design.records);
  design.archiveNotes=clone(design.notes);
  const compiled=compile(design,library),target=layoutDoc(compiled);
  for(const p of layout.byAddr.values()){
    let key=sourceStepMap.get(p.key);if(p.cell.var||hoisted.has(p.key)){const varKey=p.cell.var??hoisted.get(p.key);key=p.cell.show&&p.cell.show!=='value'?[...target.byKey.values()].find(q=>q.cell.var===varKey&&q.cell.show===p.cell.show)?.key:target.byAddr.get(target.varToAddr.get(varKey))?.key;}
    if(inputMap.has(p.key))key=p.key;
    if(key)sourceMap[p.fullAddr]=key;
  }
  // Input mapping is best effort by semantic charge name and record field; unmapped
  // source inputs are reported rather than silently pretending they migrated.
  for(const p of layout.inputs){const placement=layout.byKey.get(p.key),rowName=placement.label.split(' / ')[0],col=p.key.split('/').at(-1),charge=compiled.modular.charges.find(c=>rowName.includes(c.materialId)||rowName.includes(c.name)||c.materialId==='MonA'&&rowName.includes('单体A')||c.materialId==='xylene'&&rowName.includes('二甲苯')||c.materialId==='TsOH'&&rowName.includes('TsOH'));
    const colMap={实际值:'实际值',批号纯度:'批号-纯度',称量操作:'称量人',开始时间:'开始',结束时间:'结束',复核:'复核',偏差:'偏差'};
    if(charge&&colMap[col])sourceMap[placement.fullAddr]=`record-charges/${charge.id}/${colMap[col]}`;
    if(doc.actual?.[p.key]!=null){const key=sourceMap[placement.fullAddr];if(key)design.actual[key]=doc.actual[p.key];else warnings.push(`未自动迁移记录：${placement.fullAddr} ${placement.label}`);}
  }
  recoverHistory(design,doc,layout,values,sourceMap);
  assignVarRoles(design.vars);design.sourceMap=sourceMap;design.conversion={sourceSchema:doc.schema,warnings};return design;
}
