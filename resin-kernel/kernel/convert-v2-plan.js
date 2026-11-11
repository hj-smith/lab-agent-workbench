import {blankDesign} from './design2.js';
import {escapeTemplate} from './expr.js';
import {stableUid} from './identity.js';
import {sourceEnumLabel} from '../data-adapter.js';

const rows=value=>Array.isArray(value)?value:[];
const fields=[['scale','投料尺度',null,'scale'],['ratio','单体比','mol/mol','ratio'],['temp','反应温度','°C','tempC'],['holdHours','恒温时长','h','holdHours'],['feedHours','滴加时长','h','feedHours'],['catalystMolPct','催化剂用量','mol%','catalystMolPct'],['solidPct','反应固含','%','solidPct']];
const stepLabels={charge:'投料',prepare:'准备',feed:'滴加',reaction:'反应',sample:'取样',quench:'终止',split:'后处理',precipitate:'沉降',filter:'过滤',wash:'洗涤',dry:'干燥',custom:'自定义',test:'测试'};
// R100-40 and its lettered variants are test data, even if an old entry claims
// archive provenance. Keep the entry and its raw records; only classification changes.
export const isTrialExperiment=value=>/^R100-40[A-Z]*$/i.test(String(typeof value==='object'?value?.code??value?.id:value??'').trim());
export function hasHistoricalProvenance(experiment={}){
  if(isTrialExperiment(experiment))return false;
  if(experiment.archive===false)return false;
  if(experiment.archive!==true&&/^\w{8}-\w{4}-\w{4}-\w{4}-\w{12}$/.test(experiment.id??''))return false;
  return experiment.archive===true||rows(experiment.sourceFiles).length>0||rows(experiment.sourcePaths).length>0||!!experiment.sourceDocument||!!experiment.rawSummary||!!experiment.plan?.sourceSop||rows(experiment.plan?.sourceCharges).length>0||experiment.plan?.summarySourceOnly===true;
}
export function v2EnumLabel(field,value){
  if(value==null||value==='')return '未设置';
  if(field==='termination'&&value==='none')return '无（不中和）';
  const label=sourceEnumLabel(field,value);
  return label.startsWith('原文：')||label==='原文未记录'?'未设置':label;
}
export function v2DisplayText(value){
  const text=String(value??'');
  return stepLabels[text]??text.replace(/\{\{[^{}]*\}\}/g,'未设置').replace(/\b(?:none|neutralize|split-control|custom|at-temperature|total-from-feed-start|after-feed|monomers|nonvolatile|[a-z]+(?:[-_][a-z]+)+)\b/g,'未设置');
}

/** Minimal safe mount for old non-archive experiments. No recipe calculation,
 * purity assumptions or reconstruction: the caller retains the original plan. */
export function convertV2Plan(experiment){
  const plan=experiment.plan??{},design=blankDesign(experiment.code),text=value=>escapeTemplate(v2DisplayText(value));
  design.uid=stableUid('lab-mounted-experiment',experiment.id);design.id=design.uid;
  design.revisionUid=stableUid(design.uid,'revision:1');design.batch={uid:stableUid(design.uid,'batch:1'),code:experiment.code,aliases:[experiment.code]};
  Object.assign(design,{title:v2DisplayText(experiment.title??experiment.code),status:'设计中',date:experiment.date??'',purpose:text(experiment.objective),source:{experimentId:experiment.id,origin:'v2-user-plan'}});
  for(const [field,label,unit,role]of fields)if(typeof plan[field]==='number'&&Number.isFinite(plan[field]))design.vars.push({key:label,label,unit:unit??(['mmol','monoB-mmol'].includes(plan.scaleBasis)?'mmol':['g','monomer-g','monoB-g'].includes(plan.scaleBasis)?'g':''),role,value:plan[field],fmt:'0.###'});
  for(const [field,label]of [['scaleBasis','尺度基准'],['timeBasis','计时口径'],['solidBasis','固含口径'],['termination','终止方式']])if(plan[field]!=null)design.vars.push({key:label,label,unit:'',value:v2EnumLabel(field,plan[field])});
  for(const [i,step]of rows(plan.steps).entries()){
    const category=(stepLabels[step.type]??(/^[\x00-\x7f]+$/.test(step.type??'')?'未设置':v2DisplayText(step.type)))||'自定义';
    design.steps.push({id:String(step.id??'v2-step-'+i).replace(/[\[\]{}|@/]/g,'_'),module:'custom',category,title:v2DisplayText(step.title??category),stage:step.phase==='postprocess'?'workup':step.phase==='prepare'?'prep':'reaction',args:{},text:text(step.text)});
  }
  design.samplingPlan.nodes=rows(experiment.samples).filter(s=>s.nodeId).map(s=>({id:s.nodeId,legacySampleId:s.id,sampleId:s.name??s.id,kind:s.kind,tests:[]}));
  design.notes=[plan.safetyNotes].filter(Boolean).map(text);
  design.upgrade={sourceSchema:'lab-user-plan/2',sourceType:'user',warnings:['旧版非档案实验按草稿挂载；未重算投料，未设置的字段需补充。']};
  return design;
}
