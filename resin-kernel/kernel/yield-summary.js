import {compileChargeVars,resolveSteps,samplingNodes,variableValues} from './design2.js';
import {executionDesign} from './identity.js';
import {findFraction} from './integration.js';
import {roleVar} from './var-roles.js';
import {historicalYieldFor,presentationLots} from './yield-presentation.js';

// One yield for every view: dry product ÷ theoretical net resin (the monomers minus the water formed).
export const YIELD_NOTE='干品 ÷ 理论净树脂，未扣添加剂';
// Batches without a product step keep their result here; a theory typed here wins over the computed one.
export const DRY_RESULT='record-results/干品质量',THEORY_RESULT='record-results/理论量';
const HISTORY_GAP=.005;

const number=value=>{if(value===''||value===null||value===undefined)return null;const n=Number(value);return Number.isFinite(n)?n:null;};
const list=value=>Array.isArray(value)?value:[];
const short=value=>value===null||value===undefined?'—':Number(Number(value).toFixed(2)).toString();
const isProduct=s=>s.nodeKind==='product'||s.kind==='product'||/成品|干品|树脂汇总/.test(s.kind??'')||/(?:^|-)FP$/.test(s.name??'')||/(?:^|-)FP$/.test(s.nodeId??'')||/-summary-resin$/.test(s.id??'');

function formula(variable,values,theory){
 if(!variable||typeof variable.expr!=='string')return '';
 const text=variable.expr.split(/(\[[^\]]+\])/).map(part=>{
  const key=/^\[(.+)\]$/.exec(part)?.[1];
  return key?`${key.replace(/^(?:charge|history-summary)-/,'')} ${short(values.get(key))}`:part.replace(/-/g,' − ').replace(/\+/g,' + ').replace(/\*/g,' × ').replace(/\//g,' ÷ ');
 }).join('');
 return `${text} = ${short(theory)} g`;
}

function designTheory(plan,library){
 const steps=resolveSteps(plan,library),compiled=compileChargeVars(plan,library,[...list(plan.charges),...steps.flatMap(s=>s.charges??[])]);
 const values=variableValues([...plan.vars,...compiled.vars]),variable=roleVar(plan.vars,'theoreticalResin'),planned=roleVar(plan.vars,'yieldFraction');
 return {steps,values,variable,computed:variable?number(values.get(variable.key)):null,planned:planned?number(values.get(planned.key)):null};
}

export function yieldSummary(experiment={},design,library={},{history=true}={}){
 if(!design)return null;
 const plan=executionDesign(design),actual=plan.actual??{};
 let theoryData={steps:[],values:new Map(),variable:null,computed:null,planned:null};
 try{theoryData=designTheory(plan,library);}catch{/* A draft with incomplete materials still takes a typed theory. */}
 const {steps,values,variable,computed,planned}=theoryData,manual=number(actual[THEORY_RESULT]);
 const theory=manual>0?manual:computed>0?computed:null;
 let nodes=[];try{nodes=samplingNodes(plan,steps).filter(n=>n.kind==='product'||n.lotUid);}catch{/* No product step: one row, kept in the result keys. */}
 const routes=plan.procedure?.routes?.length>1?plan.procedure.routes:[],portions=routes.map(r=>number(actual[`record-operations/${plan.procedure.routeFrom}@${r.id}/质量`]));
 const routeShare=id=>{const i=routes.findIndex(r=>r.id===id);return portions.every(v=>v>0)?portions[i]/portions.reduce((a,b)=>a+b,0):number(routes[i].fraction)??1/routes.length;};
 const sampleDry=()=>{const samples=list(experiment.samples),products=samples.filter(isProduct);return number(list((products.length?products:samples).flatMap(s=>list(s.metrics))).find(m=>/干品质量|干品净质量|最终干品/.test(m.name??m.id??'')&&number(m.value)!==null)?.value);};
 const rows=nodes.length?nodes.map(n=>{
  const route=routes.find(r=>r.id===(n.route!==undefined?n.route:/^([A-Z])-/.exec(n.id)?.[1]))?.id,prefix=`record-operations/${n.stepId}${route?'@'+route:''}/`;
  const own=steps.filter(s=>!n.branch||s.branch===n.branch),keys=[...n.stepId?[prefix+'M2',prefix+'干品质量']:[],...!n.stepId&&nodes.length===1?own.filter(s=>/恒重/.test(s.title)).map(s=>'record-operations/'+s.id+'/M2'):[],...nodes.length===1?[DRY_RESULT]:[]];
  const key=keys.find(k=>number(actual[k])!==null)??(n.stepId?prefix+'M2':DRY_RESULT);
  const fraction=route?routeShare(route):n.branch?plan.historicalBranches?.[n.branch]?.fraction??findFraction(plan.steps,n.branch):1;
  return {id:n.id,route:route??null,branch:n.branch??null,label:nodes.length>1?/(Hep|MeOH|[A-Z])$/.exec(n.branch??'')?.[1]??route??n.branch??n.id:'',dryKey:key,dry:number(actual[key]),fraction:number(fraction)};
 }):[{id:'batch',route:null,branch:null,label:'',dryKey:DRY_RESULT,dry:number(actual[DRY_RESULT])??sampleDry(),fraction:1}];
 for(const row of rows){
  row.theory=theory!==null&&row.fraction!==null?theory*row.fraction:null;
  row.yield=row.dry!==null&&row.theory>0?row.dry/row.theory:null;
 }
 if(history){
  let lots=[];try{lots=presentationLots(design,experiment,library);}catch{/* The history comparison is only a note. */}
  rows.forEach((row,index)=>{
   const lot=lots.length===rows.length?lots[index]:{code:experiment.code,branch:row.branch,legacy_ids:[]};
   let past=null;try{past=historicalYieldFor(lot,design,experiment);}catch{/* No summary sheet for this batch. */}
   row.historical=past;
   row.differs=row.yield!==null&&past?.value!==undefined&&Math.abs(row.yield-past.value)>=HISTORY_GAP;
  });
 }
 const filled=rows.filter(r=>r.dry!==null),complete=rows.length>1&&filled.length===rows.length&&rows.every(r=>r.theory>0);
 const total=complete?(()=>{const dry=rows.reduce((sum,r)=>sum+r.dry,0),own=rows.reduce((sum,r)=>sum+r.theory,0);return {dry,theory:own,yield:dry/own};})():null;
 return {rows,total,theory,computedTheory:computed>0?computed:null,manualTheory:manual>0?manual:null,theoryKey:THEORY_RESULT,formula:formula(variable,values,computed),planned,note:YIELD_NOTE,revisionUid:plan.revisionUid,
  missing:filled.length<rows.length?'dry':theory===null?'theory':null};
}
