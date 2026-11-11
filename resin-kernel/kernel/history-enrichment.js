import {findMaterial} from './materials.js';
import {escapeTemplate} from './expr.js';
import {historicalSummaryRecords,historicalFactorEvidence,historyBranchMatches} from './history-factors.js';
import {stableUid} from './identity.js';

const list=value=>Array.isArray(value)?value:[];
const clone=value=>value==null?value:structuredClone(value);
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const present=value=>value!=null&&value!=='';
const safe=value=>String(value).replace(/[\[\]{}|@/]/g,'_');
const mutableSteps=steps=>list(steps).flatMap(step=>[step,...list(step.branches).flatMap(branch=>mutableSteps(branch.steps))]);
const sourceFor=(record,key)=>({experimentId:record.experimentId,path:'rawSummary['+JSON.stringify(key)+']',...clone(record.refs?.[key]??{})});
const originalSource=(experiment,step)=>clone(step?.params?.source??(step?.params?.sourceFile?{file:step.params.sourceFile}:experiment.plan?.sourceSop??experiment.sourceDocument??{}));

function addNote(design,message,source,severity='info'){
  if(!present(message))return;
  design.historicalNotes??=[];
  if(!design.historicalNotes.some(note=>note.message===message&&JSON.stringify(note.source??null)===JSON.stringify(source??null)))design.historicalNotes.push({message,...source?{source:clone(source)}:{},severity});
}
function ensureMaterial(design,library,query){
  const material=findMaterial(library,query);
  if(material&&!design.materials.some(row=>row.ref===material.id))design.materials.push({ref:material.id,as:material.id});
  return material;
}
function addCharge(design,library,target,{id,material:query,value,unit,source,role,sourceText}){
  if(!finite(value)||value<0)return;
  const material=ensureMaterial(design,library,query);
  if(!material){
    design.historicalCharges??=[];
    if(!design.historicalCharges.some(row=>row.id===id))design.historicalCharges.push({id,material:query,value,unit,state:'recorded',source:clone(source),sourceText:sourceText??null});
    addNote(design,'原文物料用量：'+query+' '+value+' '+unit+'。物料目录未登记该物料，未补设分子量、密度或纯度。',source);
    return;
  }
  if([...design.charges,...mutableSteps(design.steps).flatMap(step=>list(step.charges))].some(charge=>charge.id===id))return;
  target.push({id,material:material.id,label:material.name??material.id,role:role??(/溶剂|solvent/.test(material.kind??'')?'solvent':'reactant'),amount:{mode:unit==='mL'?'volume':'mass',value,unit},source:clone(source),sourceValue:value,sourceText,sourceBasis:'原文记录量；未据此重算纯度或原料形态'});
}

function addSummaryVariables(design,experiment,records){
  const evidence=historicalFactorEvidence(experiment,{historicalSummary:records});
  for(const [suffix,key,label,unit,scale]of [
    ['monoA_monoB_ratio','历史单体比','总表单体摩尔比','mol/mol',1],
    ['catalyst_molpct','历史催化剂molpct','总表催化剂摩尔比例','mol/mol',0.01],
    ['reaction_solids','历史反应固含','总表名义反应固含','w/w',1],
    ['reaction_temperature','历史反应温度','总表反应设温','°C',1],
    ['reaction_hours','总反应时间(h)','总表记录反应时间','h',1],
  ]){
    const field=evidence['resin.factor.'+suffix];
    if(!field||field.ambiguity||!finite(field.value)||design.vars.some(variable=>variable.key===key))continue;
    design.vars.push({key,label,unit,value:field.value*scale,source:clone(field.source),sourceValue:clone(field.sourceValue),sourceBasis:suffix==='reaction_hours'?'原总表反应时间；不替代滴后保温时间':'原总表记录'});
  }
  // Values absent from the source remain absent, including unknown zero-dose
  // and zero-time conditions. The complete source text is exposed below.
}

const solventTokens=[['SolvA','SolvA'],['SolvC(?!A)','SolvC'],['二甲苯|xylene','xylene'],['MIBK','MIBK'],['1,4-二氧六环|二氧六环|dioxane','dioxane'],['THF|四氢呋喃','THF']];
function summaryCharges(design,experiment,library,records){
  if(records.length!==1)return; // Merged branches share a mother charge, not additive recipes.
  const record=records[0],summary=record.summary;
  const all=()=>[...design.charges,...mutableSteps(design.steps).flatMap(step=>list(step.charges))];
  const materialId=charge=>findMaterial(library,design.materials.find(row=>row.as===charge.material)?.ref??charge.material)?.id??charge.material;
  for(const [key,query]of [['MonB (g)','MonB'],['MonA (g)','MonA'],['单体C (g)','单体C']]){
    const material=findMaterial(library,query),value=summary[key];
    if(material&&finite(value)&&!all().some(charge=>materialId(charge)===material.id))addCharge(design,library,design.charges,{id:'history-summary-'+material.id,material:material.id,value,unit:'g',source:sourceFor(record,key),role:material.id==='MonB'?'monoB':'monoA'});
  }
  const text=summary['溶剂及用量'];if(typeof text!=='string')return;
  for(const [pattern,material]of solventTokens){
    if(all().some(charge=>materialId(charge)===material))continue;
    const re=new RegExp('(?:'+pattern+')\\s*(\\d+(?:\\.\\d+)?)\\s*(g|mL)(?![A-Za-z])','giu');
    const matches=[...text.matchAll(re)];
    for(const [index,match]of matches.entries()){
      // A labelled total with a parenthesised decomposition is one quantity.
      // Carrier subdivisions have no second material token and are not added.
      addCharge(design,library,design.charges,{id:'history-summary-solvent-'+material+'-'+index,material,value:Number(match[1]),unit:match[2].toLowerCase()==='ml'?'mL':'g',source:sourceFor(record,'溶剂及用量'),role:'solvent',sourceText:match[0]});
    }
  }
}

function enrichSourceNotes(design,experiment,records,archives){
  design.historicalEquipment??=[];design.historicalFormulations??=[];
  const originals=[experiment,...archives];
  for(const record of records){
    const original=originals.find(row=>row.id===record.experimentId)??experiment;
    for(const key of ['主要变化','酸催化剂','溶剂及用量','加料方式','终止 / 稀释 / 沉降','洗涤 / 打浆','干燥条件','备注'])if(present(record.summary[key]))addNote(design,record.experimentId+' · '+key+'：'+record.summary[key],sourceFor(record,key),key==='备注'?'warning':'info');
    for(const issue of list(record.issues))addNote(design,typeof issue==='string'?issue:issue.message??JSON.stringify(issue),{experimentId:record.experimentId,path:'issues'},'warning');
    const codes=record.summary['LAB配方 样品编号'];
    if(present(codes)){
      const source=sourceFor(record,'LAB配方 样品编号');
      if(!design.historicalFormulations.some(row=>row.experimentId===record.experimentId&&row.codes===codes))design.historicalFormulations.push({experimentId:record.experimentId,codes:clone(codes),source});
      addNote(design,record.experimentId+' · LAB配方样品编号：'+codes,source);
      for(const key of ['配方配比简述 组分 / 配比 / 固含','共同烘烤条件 °C / min / 气氛'])if(present(record.summary[key]))addNote(design,record.experimentId+' · '+key+'：'+record.summary[key],sourceFor(record,key));
    }
    const plan=original.plan??{};
    if(present(plan.equipment)||present(plan.vessels)){
      const equipment={experimentId:record.experimentId,equipment:clone(plan.equipment??null),vessels:clone(plan.vessels??null),source:originalSource(original)};
      if(!design.historicalEquipment.some(row=>JSON.stringify(row)===JSON.stringify(equipment)))design.historicalEquipment.push(equipment);
      if(present(plan.equipment))addNote(design,record.experimentId+' · 反应装置：'+plan.equipment,equipment.source);
      if(present(plan.vessels))addNote(design,record.experimentId+' · 原文容器：'+plan.vessels,equipment.source);
    }
  }
  for(const original of originals)for(const step of list(original.plan?.steps)){
    if(present(step.text))addNote(design,(step.title??'原文条件')+'：'+step.text,originalSource(original,step));
    if(present(step.params?.originalText)&&step.params.originalText!==step.text)addNote(design,(step.title??'原文条件')+' · 完整原文：'+step.params.originalText,originalSource(original,step));
  }
  for(const original of originals){
    if(present(original.actual?.notes))addNote(design,original.code+' · 原记录说明：'+original.actual.notes,{experimentId:original.id,path:'actual.notes'});
    for(const key of ['historicalConditionText','sourceCarrierBasis','quenchAcidFractionBasis','quenchEqBasis','sourceTimeBasis'])if(present(original.plan?.[key]))addNote(design,original.code+' · 原条件口径：'+original.plan[key],{experimentId:original.id,path:'plan.'+key,...originalSource(original)});
    for(const field of list(original.groupPlanConflicts))addNote(design,'原分支的 '+field+' 记录不一致；各分支来源值分别保留。',{experimentId:original.id,path:'groupPlanConflicts'},'warning');
  }
  for(const step of mutableSteps(design.steps))if(present(step.sourceText)&&step.sourceText!==step.text)addNote(design,(step.title??'原文条件')+'：'+step.sourceText,step.source??design.source);
  for(const warning of list(design.conversion?.warnings))addNote(design,typeof warning==='string'?warning:warning.message??JSON.stringify(warning),design.source??originalSource(experiment),'warning');
  for(const conflict of list(design.recordConflicts)){
    const sides=[['workbookValue','workbookSource','工作簿记录'],['archiveValue','archiveSource','归档记录'],['supplementaryValue','supplementarySource','补充工作簿记录']].filter(([key])=>present(conflict[key]));
    addNote(design,'来源记录冲突 '+conflict.key+'：'+sides.map(([key,,label])=>label+' '+JSON.stringify(conflict[key])).join('；')+'。保留双方依据。',sides.map(([,key])=>clone(conflict[key])),'warning');
  }
}

function sourceSampling(design,experiment,records,archives){
  const originals=archives.length?archives:[experiment],nodes=design.samplingPlan.nodes;
  const represented=()=>new Set([...nodes,...mutableSteps(design.steps).flatMap(step=>list(step.samples))].flatMap(node=>[node.legacySampleId,node.sampleId]).filter(Boolean));
  for(const original of originals)for(const [index,sample]of list(original.samples).entries()){
    // An earlier converter's generated sample is not an original registration.
    // In particular CP33's erroneous 0/2 h placeholders must not become their
    // own source evidence during a repair of an already-mounted workspace.
    if(sample.generatedBy==='design-node'||sample.nodeUid&&sample.id===stableUid(design.uid,'experiment-sample:'+sample.nodeUid))continue;
    const match=/(?:^|-)(\d+(?:\.\d+)?)\s*h\b/i.exec(sample.name??'');
    if(!match||represented().has(sample.id)||represented().has(sample.name))continue;
    const branch=records.length>1?sample.branchId??original.id:null;
    nodes.push({id:'history-source-'+safe(sample.id),sampleId:sample.name,legacySampleId:sample.id,hours:Number(match[1]),kind:/PC|沉降|洗|打浆/.test(sample.name)?'workup':'process',stage:'过程 '+Number(match[1])+' h',tests:list(sample.metrics).some(metric=>metric.method?.includes('GPC'))?['GPC']:[],...branch?{branch}:{},source:{experimentId:original.id,sampleId:sample.id,path:'samples['+index+'].name'},sourceBasis:'原档案样品名称中的取样时点'});
  }
  // Summary-only records may explicitly list a sampling schedule even when a
  // sample result was never entered. They establish events, not measurements.
  if(!experiment.plan?.summarySourceOnly)return;
  for(const record of records){
    const text=record.summary['备注'];if(typeof text!=='string')continue;
    for(const match of text.matchAll(/(\d+(?:\.\d+)?(?:\s*\/\s*\d+(?:\.\d+)?)*)\s*h\s*取样/gi))for(const token of match[1].split('/')){
      const hours=Number(token.trim());
      if(nodes.some(node=>node.hours===hours)||list(design.samplingPlan.points).some(point=>(typeof point==='number'?point:point.hours)===hours))continue;
      nodes.push({id:'history-summary-time-'+String(hours).replace('.','_'),hours,sampleId:experiment.code+' · 总表记录 '+hours+' h取样',kind:'process',tests:[],source:sourceFor(record,'备注'),sourceIdentityRecorded:false,sourceBasis:'总表明确记录取样时点，原样品编号未记录'});
    }
  }
}

function branchEvidence(design,experiment,records){
  const sourceBranches=list(experiment.plan?.workupBranches).length?experiment.plan.workupBranches:list(experiment.plan?.branches);
  const physical=sourceBranches.length>1||records.length>1;
  const removed=new Set();
  for(const step of mutableSteps(design.steps)){
    if(!list(step.branches).length)continue;
    if(!physical&&step.branches.length===1&&['history-split','archive-branch-identities'].includes(step.id)){
      const only=step.branches[0];removed.add(only.id);
      // These wrappers are generated compatibility metadata. Keep their source
      // operation children and never discard source/user prose.
      design.steps.push(...list(only.steps));delete step.branches;
      continue;
    }
    for(const branch of step.branches){
      const record=records.find(row=>historyBranchMatches(row.experimentId,branch.id));
      const value=record?.summary['分样比例 未分样填100%'];
      if(finite(value)&&value>0&&value<=1){branch.fraction=value;branch.fractionSource=sourceFor(record,'分样比例 未分样填100%');branch.fractionBasis='原总表记录比例，非重算分料实测';}
      else if(branch.fraction==null)branch.fraction=null;
    }
    if(['history-split','archive-branch-identities'].includes(step.id)&&!step.userEditedFields?.text){
      step.text=escapeTemplate(step.branches.map(branch=>(branch.name??branch.id)+' '+(finite(branch.fraction)?(branch.fraction*100).toFixed(1)+'%':'未记录')).join('；')+'。比例沿用原始总表记录。');
    }
  }
  for(const node of design.samplingPlan.nodes)if(removed.has(node.branch))delete node.branch;
  if(design.historicalBranches&&!physical)for(const id of removed)delete design.historicalBranches[id];
  if(physical&&records.some(record=>/分样比例未注明|比例未记录|比例未注明/.test(record.summary['备注']??''))){
    const values=records.map(record=>record.experimentId+' '+(finite(record.summary['分样比例 未分样填100%'])?(record.summary['分样比例 未分样填100%']*100)+'%':'未记录')).join('；');
    addNote(design,'分支比例来源存在口径差异：原总表记录 '+values+'；docx/备注只记分成两份或称比例未注明。保留总表比例及原文差异，未将其认定为实测分料比例。',records.map(record=>sourceFor(record,'分样比例 未分样填100%')),'warning');
  }
}

function textAmounts(text,token){
  const aliases=token==='THF'?'THF|四氢呋喃':'TEA|三乙胺';
  const re=new RegExp('(?:(\\d+(?:\\.\\d+)?)\\s*(mL|g)\\s*('+aliases+')|('+aliases+')\\s*(\\d+(?:\\.\\d+)?)\\s*(mL|g))','giu');
  return [...text.matchAll(re)].map(match=>({value:Number(match[1]??match[5]),unit:(match[2]??match[6]).toLowerCase()==='ml'?'mL':'g',sourceText:match[0]}));
}
function docxWorkupCharges(design,experiment,library){
  if(!/\.docx$/i.test(experiment.plan?.sourceSop?.file??''))return;
  const originalSteps=list(experiment.plan?.steps),steps=mutableSteps(design.steps),totals={};
  for(const original of originalSteps){
    const text=original.text??'',step=steps.find(row=>row.legacyStepId===original.id||row.id===safe(original.id));
    if(!step||/GPC|配样|取样|样品即时/.test(text)||!(original.type==='quench'||/再次.*(?:溶解|THF)|二次.*溶解/.test(text)))continue;
    step.stage='workup';
    if(original.type==='quench'){step.category='终止';step.role='quench';}
    step.charges??=[];
    for(const token of ['THF','TEA'])for(const [index,amount]of textAmounts(text,token).entries()){
      const source=originalSource(experiment,original);source.stepId=original.id;
      const owned=step.charges.find(charge=>charge.material===token&&charge.amount?.value===amount.value&&charge.amount?.unit===amount.unit);
      if(!owned){
        const initial=design.charges.findIndex(charge=>charge.material===token&&charge.amount?.value===amount.value&&charge.amount?.unit===amount.unit);
        if(initial>=0){const [charge]=design.charges.splice(initial,1);Object.assign(charge,{role:token==='THF'?'solvent':'quench',source,sourceText:text});step.charges.push(charge);}
        else addCharge(design,library,step.charges,{id:'history-docx-'+safe(step.id)+'-'+token+'-'+index,material:token,...amount,source,role:token==='THF'?'solvent':'quench'});
      }
      totals[token]??={value:0,unit:amount.unit,count:0};totals[token].value+=amount.value;totals[token].count++;
    }
  }
  for(const [token,total]of Object.entries(totals))if(total.count>1)addNote(design,'原文重复后处理用量：'+token+' 共 '+total.value+' '+total.unit+'，分属 '+total.count+' 个明确记录的操作。',originalSource(experiment));
}

function yieldConflict(design,experiment,records,archives){
  design.historicalYieldChecks??=[];
  for(const record of records){
    const original=archives.find(row=>row.id===record.experimentId)??experiment,s=record.summary;
    const dry=list(original.samples).flatMap(sample=>list(sample.metrics)).filter(metric=>/^(?:干品(?:净)?质量|最终干品(?:质量)?)$/.test(metric.name??'')&&metric.unit==='g'&&finite(metric.value));
    const values=[...new Set(dry.map(metric=>metric.value))];
    if(values.length!==1||!finite(s['理论产品 整批(g)'])||!finite(s['分样比例 未分样填100%'])||s['分样比例 未分样填100%']<=0||!finite(s['最终产率 (%)']))continue;
    const denominator=s['理论产品 整批(g)']*s['分样比例 未分样填100%'];if(denominator<=0)continue;
    const recordedYieldPct=s['最终产率 (%)']*100,derivedYieldPct=values[0]/denominator*100;
    if(Math.abs(recordedYieldPct-derivedYieldPct)<0.02)continue;
    const check={experimentId:record.experimentId,recordedYieldPct,derivedYieldPct,finalDryMassG:values[0],theoreticalMassG:s['理论产品 整批(g)'],branchFraction:s['分样比例 未分样填100%'],source:[sourceFor(record,'最终产率 (%)'),...dry.map(metric=>clone(metric.sourceRef??metric.source))]};
    if(!design.historicalYieldChecks.some(row=>JSON.stringify(row)===JSON.stringify(check)))design.historicalYieldChecks.push(check);
    addNote(design,record.experimentId+' 产率与最终干品口径不一致：原总表产率 '+recordedYieldPct.toFixed(3)+'%；最终干品 '+values[0]+' g /（理论整批 '+s['理论产品 整批(g)'].toFixed(5)+' g × 分样比例 '+s['分样比例 未分样填100%']+'）对应 '+derivedYieldPct.toFixed(2)+'%。总表干品原值 '+String(s['干品 (g)']??'未记录')+' g。原记录与核算值分别保留，未改写原始指标。',check.source,'warning');
  }
}

/** Mutates only the supplied derived design. Call before freezing/integration;
 * pass merged archives as experiment.archiveRecords (the unchanged records from
 * state.experimentGroupArchive.find(g => g.motherId === experiment.id)). */
export function enrichHistoricalDesign(design,experiment,library={}){
  if(design?.schema!=='lab-design/2')throw Error('历史信息补齐需要 lab-design/2');
  const archives=list(experiment.archiveRecords??experiment.groupArchive?.records);
  const records=historicalSummaryRecords(experiment,design,archives);
  design.historicalSummary=records.map(record=>({experimentId:record.experimentId,summary:clone(record.summary),refs:clone(record.refs??{}),summaryRow:record.summaryRow??null,issues:clone(record.issues??[])}));
  design.sourcePlanBaseline=clone(experiment.sourcePlanBaseline??experiment.plan??{});
  design.status='历史资料';design.vars??=[];design.charges??=[];design.materials??=[];design.steps??=[];
  design.samplingPlan??={points:[],nodes:[]};design.samplingPlan.nodes??=[];
  if(records.length>1){
    const file=experiment.plan?.sourceSop?.file??experiment.sourceDocument?.file;
    if(file&&!design.userEditedFields?.title){const stem=String(file).replace(/\.(?:docx|xlsx)$/i,'').replace(new RegExp('^'+experiment.code+'[_ ]?'),'').replace(/_\d{8}$/,'').replace(/_SOP与批记录(?:表)?$/,'').replaceAll('_',' · ');design.title=experiment.code+' · '+stem;}
    const originals=archives.length?archives:[experiment];
    design.historicalObjective=originals.map(row=>row.code+'：'+(row.objective??row.rawSummary?.['主要变化']??'未记录')).join('；');
    if(!design.userEditedFields?.purpose)design.purpose=design.historicalObjective;
  }
  addSummaryVariables(design,experiment,design.historicalSummary);
  summaryCharges(design,experiment,library,design.historicalSummary);
  sourceSampling(design,experiment,design.historicalSummary,archives);
  branchEvidence(design,experiment,design.historicalSummary);
  docxWorkupCharges(design,experiment,library);
  enrichSourceNotes(design,experiment,design.historicalSummary,archives);
  yieldConflict(design,experiment,design.historicalSummary,archives);
  return design;
}

export function enrichHistoricalLedger(design,experiment,ledger=[]){
  const ids=new Set([experiment.id,experiment.code,...experiment.archiveCodes??[]]);
  design.historicalLedger=ledger.filter(row=>String(row.experimentId).split(/[、,，/；;\s]+/).some(id=>ids.has(id))).map(clone);
  for(const row of design.historicalLedger){
    if(row.type==='差异')addNote(design,'原始依据差异：'+row.item+' '+String(row.value??'')+(row.note?'；'+row.note:''),row.source,'warning');
    if(row.item==='反应时间'&&row.unit==='h'&&finite(row.value))for(const summary of design.historicalSummary??[]){
      const value=summary.summary['反应时间 (h)'];if(!finite(value)||value===row.value)continue;
      const message='反应时间来源冲突：原总表 '+value+' h；原始依据 '+row.value+' h。两值与出处均保留，未将其替换为新的保温时间。';
      addNote(design,message,[sourceFor(summary,'反应时间 (h)'),row.source],'warning');summary.issues??=[];if(!summary.issues.includes(message))summary.issues.push(message);
    }
  }
  return design;
}
