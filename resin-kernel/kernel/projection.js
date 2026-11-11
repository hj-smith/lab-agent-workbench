import {compile} from './compile.js';
import {resolveSteps,variableValues,resolveCondition,scheduleDesign,flattenSteps} from './design2.js';
import {extractFactors} from './factors.js';
import {findMaterial,materialPurityFraction} from './materials.js';
import {renderTemplate} from './expr.js';
import {actualT0,relativeClock} from './record-values.js';
import {historicalFactorEvidence,historicalTiming} from './history-factors.js';

const copy=value=>structuredClone(value);
const number=value=>typeof value==='number'&&Number.isFinite(value)?value:null;
const sum=values=>values.length&&values.every(v=>number(v)!==null)?values.reduce((a,b)=>a+b,0):null;
const divide=(a,b)=>number(a)!==null&&number(b)!==null&&b>0?a/b:null;
const present=value=>value!==undefined&&value!==null&&value!=='';
const unitFor=key=>/钟点|^开始$|^结束$/.test(key)?'Excel day':/温/.test(key)?'°C':/含固率|纯度/.test(key)?'w/w':/时长/.test(key)?'h':/累计水量/.test(key)?'mL':/质量|取样量|^M[12]$|实际值/.test(key)?'g':'';

// This small fallback is semantic, like factor-seed.json; it contains no recipe
// setpoints. A supplied registry takes precedence and keeps custom factors live.
const fallbackRegistry={schema:'lab-factor-registry/1',version:1,factors:[
  ['ratio','number','mol/mol',{kind:'var',keys:['MonB:MonA摩尔比','单体摩尔比','历史单体比']}],
  ['temp','number','°C',{kind:'var',keys:['反应内温','反应温度'],fallbacks:[{kind:'condition',category:'反应',field:'tempC'}]}],
  ['holdHours','number','h',{kind:'var',keys:['恒温反应时间','反应时间','总反应时间(h)'],fallbacks:[{kind:'condition',category:'反应',field:'hours'}]}],
  ['solidFraction','number','w/w',{kind:'var',keys:['初始名义固含','名义总固含','投料固含','初始投料固含','反应投料固含','历史反应固含']}],
  ['catalystMolPct','number','mol%',{kind:'charge',role:'catalyst',field:'molpct',fallbacks:[{kind:'var',keys:['TsOH用量(mol%)','历史催化剂molpct'],scale:100}]}],
  ['timing','category','1',{kind:'module_param',name:'升温|反应|计时|滴加',key:'sourceText',map:[{pattern:'达.*温.*t=0|t=0.*达.*温|首次达到',value:'at-temperature'},{pattern:'滴加.*计时|滴加.*起|开始滴加',value:'total-from-feed-start'}]}],
  ['termination','category','1',{kind:'module_param',category:'^(终止|中和|终止/中和)$',roles:['quench','neutralise','neutralize'],exclude_category:'^(加料|补加|投料)',exclude:'GPC|分析|取样|样品|即时',key:'text',map:[{pattern:'不加DMAP|不中和|不加.*终止|不加碱',value:'none'},{pattern:'DMAP|TEA|三乙胺|吡啶|BaseQ|终止剂|中和剂',value:'neutralize'}]}]
].map(([id,type,unit,extract])=>({id:'legacy.'+id,type,unit,extract,active:true,version:1}))};
const factorIds={ratio:'monoA_monoB_ratio',temp:'reaction_temperature',holdHours:'reaction_hours',solidFraction:'reaction_solids',catalystMolPct:'catalyst_molpct',timing:'timing_basis',termination:'termination'};

/** Read-only compatibility view of the current v2 plan and its current execution.
 * The caller decides whether this may replace an experiment's fields: archives
 * must retain their original plan/actual. No objects in either input are edited.
 * Legacy percentages use 0..100; kernel fractions and purities use 0..1.
 */
export function projectToLegacy(design,library={},experiment={}){
  if(design?.schema!=='lab-design/2')throw Error('projectToLegacy 需要 lab-design/2');
  if(Array.isArray(library))library={materials:library};
  const doc=compile(design,library),steps=resolveSteps(design,library,{liftParams:true}),values=variableValues(doc.vars);
  const read=key=>{try{return values.get(key);}catch{return null;}};
  const role=(...roles)=>{const v=(design.vars??[]).find(v=>roles.includes(v.role));return v?read(v.key):null;};
  const fallback=extractFactors(design,fallbackRegistry,library);
  const supplied=library.factorRegistry??(Array.isArray(library.factors)?{version:library.version??1,factors:library.factors}:library.factors);
  const factors=supplied?.factors?extractFactors(design,supplied,library):{};
  const historical=historicalFactorEvidence(experiment,design),sourceTiming=historicalTiming(experiment,design);
  const factor=key=>historical['resin.factor.'+factorIds[key]]?.value??factors['resin.factor.'+factorIds[key]]?.value??fallback['legacy.'+key]?.value??null;
  const charges=doc.modular.charges.map(c=>({...c,massG:number(read(c.keys.mass)),volumeMl:number(read(c.keys.volume)),mmol:number(read(c.keys.mmol))}));
  const initialIds=new Set([...(design.charges??[]),...(design.procedure?steps.filter(s=>s.stage!=='workup').flatMap(s=>s.charges??[]):[])].map(c=>c.id)),initial=charges.filter(c=>initialIds.has(c.id));
  const material=c=>findMaterial(library,c.materialId),kind=(c,re)=>re.test(String(material(c)?.kind??''));
  const monoB=initial.find(c=>kind(c,/^(monoB|单体B)$/))??initial.find(c=>c.role==='monoB');
  const monoAs=initial.filter(c=>kind(c,/^(monoA|单体A)$/)||c.role==='monoA');
  const catalysts=initial.filter(c=>c.role==='catalyst'||kind(c,/^(acid|酸催化剂)$/));
  const solvents=initial.filter(c=>c.role==='solvent');
  const solids=initial.filter(c=>c.role!=='solvent'&&c.amount.mode!=='solidFill');
  const solventMass=sum(solvents.map(c=>c.massG)),monoAMmol=sum(monoAs.map(c=>c.mmol));
  const reaction=steps.filter(s=>s.stage==='reaction'&&(/反应|恒温/.test(s.category??'')||s.conditions?.kind==='hold'));
  const condition=(step,key)=>{try{return resolveCondition(step?.conditions?.[key],step?.args,doc.vars);}catch{return null;}};
  const feedSteps=steps.filter(s=>/滴加/.test(s.title)&&!/取样|分析/.test(s.title));
  const timing=factor('timing'),timeBasis=role('timeBasis')??sourceTiming.timeBasis?.value??(Object.hasOwn(design,'timeBasis')?design.timeBasis:({'首次达温为t0':'at-temperature','滴加起计时':'total-from-feed-start'}[timing]??timing));
  const solidVar=(design.vars??[]).find(v=>v.role==='solidPct');
  const solidFraction=number(factor('solidFraction'))??divide(sum(solids.map(c=>c.massG)),sum(initial.map(c=>c.massG)));
  const recordedSolids=historical['resin.factor.reaction_solids']?.value;
  const solidPct=number(recordedSolids)!==null?recordedSolids*100:solidVar?number(read(solidVar.key)):solidFraction===null?null:solidFraction*100;
  const catalystFraction=divide(sum(catalysts.map(c=>c.mmol)),monoB?.mmol);
  const plan={
    projectedFrom:`${design.uid}@${design.revision??1}`,
    scale:number(role('scale'))??monoB?.massG??null,
    scaleBasis:role('scaleBasis')??(Object.hasOwn(design,'scaleBasis')?design.scaleBasis:monoB?'monoB-g':null),
    ratio:number(role('ratio','monoARatio'))??number(factor('ratio'))??divide(monoAMmol,monoB?.mmol),
    temp:number(role('tempC'))??number(factor('temp'))??number(condition(reaction[0],'tempC')),
    holdHours:number(role('holdHours'))??number(sourceTiming.holdHours?.value)??(timeBasis==='after-feed'?null:number(factor('holdHours'))??sum(reaction.map(s=>number(condition(s,'hours'))))),
    reactionHours:number(historical['resin.factor.reaction_hours']?.value)??number(factor('holdHours')),
    feedHours:number(role('feedHours'))??number(sourceTiming.feedHours?.value)??sum(feedSteps.map(s=>number(condition(s,'hours')))),
    timeBasis:timeBasis??null,
    monoBId:monoB?.materialId??null,
    monoAs:monoAs.map(c=>({materialId:c.materialId,fraction:divide(c.mmol,monoAMmol),massG:c.massG})),
    catalystId:catalysts[0]?.materialId??null,
    catalystMolPct:number(role('catalystMolPct'))??number(factor('catalystMolPct'))??(catalystFraction===null?null:catalystFraction*100),
    solidPct,
    solidBasis:role('solidBasis')??(Object.hasOwn(design,'solidBasis')?design.solidBasis:initial.length&&solids.length?(catalysts.some(c=>solids.includes(c))?'nonvolatile':'monomers'):null),
    solvents:solvents.map(c=>({materialId:c.materialId,fraction:divide(c.massG,solventMass),massG:c.massG})),
    feedSolvents:[],materialPurities:{},
    calculationPurityBasis:charges.some(c=>c.purityAssumed)?'nominal':'strict',
    termination:null,quenchId:null,quenchEq:null,quenchTemp:null,quenchSolutionPct:null,
    charges:[],steps:[],workupBranches:[],factorEvidence:copy(historical),timingEvidence:copy(sourceTiming)
  };
  for(const c of charges){
    const purity=c.purity??materialPurityFraction(material(c));
    if(number(purity)!==null)plan.materialPurities[c.materialId]=purity*100;
  }
  const render=text=>{try{return renderTemplate(text??'',r=>read(r.key));}catch{return String(text??'');}};
  const quenchSteps=steps.filter(s=>/^(终止|中和|终止\/中和)$/.test(s.category??'')||['quench','neutralise','neutralize'].includes(s.role)).filter(s=>!/GPC|分析|取样|样品|即时/.test(s.title));
  const quenchCharges=quenchSteps.flatMap(s=>charges.filter(c=>s.charges.some(x=>x.id===c.id))).filter(c=>c.role!=='solvent');
  const q=quenchCharges[0],qStep=quenchSteps.find(s=>s.charges.some(c=>c.id===q?.id))??quenchSteps[0];
  const termination=factor('termination');
  plan.termination=termination==='none'||/无.*不中和/.test(termination??'')?'none':q||termination==='neutralize'||/DMAP|TEA|BaseQ|pyridine|吡啶/.test(termination??'')?'neutralize':quenchSteps.length||present(termination)?'custom':null;
  plan.quenchId=q?.materialId??(plan.termination==='neutralize'||plan.termination==='custom'?findMaterial(library,termination)?.id??(termination==='氨水'?'氨水':null):null);
  // A historical stated equivalent is a recorded setpoint, not a rounded
  // reconstruction from library MWs. The charge's mass/mmol remain available
  // independently and source inconsistencies stay visible in archive checks.
  plan.quenchEq=number(role('quenchEq'))??(q?(q.amount.mode==='eq'?number(read(q.keys.amount)):divide(q.mmol,sum(catalysts.map(c=>c.mmol)))):null);
  plan.quenchTemp=number(role('quenchTemp'))??number(condition(qStep,'tempC'));
  const quenchMaterial=q?findMaterial(library,q.materialId):null;
  plan.quenchSolutionPct=number(role('quenchSolutionPct'))??(number(q?.concentration)!==null&&(q.concentration!==1||quenchMaterial?.form==='溶液'||quenchMaterial?.solution)?q.concentration*100:null);
  // History conversion can know a termination setting without a reconstructible
  // operation/module. Preserve that explicit setting when deriving a new batch.
  if(!quenchSteps.length&&!historical['resin.factor.termination']&&present(role('termination'))){
    plan.termination=role('termination');plan.quenchId=role('quenchId')??null;
    plan.quenchEq=number(role('quenchEq'));plan.quenchTemp=number(role('quenchTemp'));plan.quenchSolutionPct=number(role('quenchSolutionPct'));
  }
  const stageFor=s=>s?.stage==='workup'?'后处理':s?.stage==='prep'?'准备':'反应';
  const operation=s=>/^(终止|中和|终止\/中和)$/.test(s?.category??'')?'quench':/稀释/.test(s?.category??'')?'dilution':/沉降/.test(s?.category??'')?'precipitation':/打浆|洗涤/.test(s?.category??'')?'wash':null;
  const chargeOwner=c=>steps.find(s=>s.charges.some(x=>x.id===c.id));
  const schedules=scheduleDesign(design,library,{steps,vars:doc.vars});
  for(const s of steps){
    const schedule=schedules.find(t=>t.stepId===s.id);
    plan.steps.push({id:s.id,type:s.category??'custom',title:s.title,text:render(s.text),phase:s.stage==='workup'?'postprocess':s.stage==='prep'?'prepare':'reaction',...s.branch?{branchId:s.branch}:{},params:{tempC:number(condition(s,'tempC')),hours:number(condition(s,'hours')),plannedStart:schedule?.plannedStart??null,plannedEnd:schedule?.plannedEnd??null}});
  }
  const projectedCharge=c=>{
    const owner=chargeOwner(c),workupKey=operation(owner),amount=c.unit==='mL'?c.volumeMl:c.massG;
    return {id:c.id,planRowId:c.id,materialId:c.materialId,name:c.name,role:c.role,amount,mass:amount,massG:c.massG,volumeMl:c.volumeMl,mmol:c.mmol,plannedMass:amount,unit:c.unit,stage:stageFor(owner),...owner?{stepId:owner.id}:{},...owner?.branch?{branchId:owner.branch}:{},...workupKey?{workupKey:workupKey==='wash'?'wash-'+owner.id:workupKey}:{},source:`charges/${c.id}`,notes:c.notes??''};
  };
  plan.charges=charges.map(projectedCharge);
  for(const c of charges.filter(c=>c.role==='solvent'&&feedSteps.some(s=>s.charges.some(x=>x.id===c.id))))plan.feedSolvents.push({materialId:c.materialId,mass:c.massG});
  // Branch identity is an existing design ID; no positional or generated aliases.
  const branches=flattenSteps(design.steps).flatMap(s=>s.branches??[]);
  for(const branch of branches){
    const owned=steps.filter(s=>s.branch===branch.id),qsteps=owned.filter(s=>quenchSteps.some(q=>q.id===s.id)),qc=charges.find(c=>qsteps.some(s=>s.charges.some(x=>x.id===c.id))&&c.role!=='solvent');
    const route={id:branch.id,code:branch.code??branch.id,name:branch.name??branch.id,fraction:branch.fraction??null,quench:{enabled:qsteps.length>0&&!qsteps.every(s=>/不中和|不加碱/.test(render(s.text))),materialId:qc?.materialId??null,eq:qc?.amount.mode==='eq'?number(read(qc.keys.amount)):null,tempC:number(condition(qsteps[0],'tempC'))},washes:[]};
    for(const s of owned){
      const cs=charges.filter(c=>s.charges.some(x=>x.id===c.id)),c=cs.find(c=>c.role==='solvent')??cs[0],op=operation(s);
      if(c&&['dilution','precipitation','wash'].includes(op)){const item={materialId:c.materialId,amount:c.unit==='mL'?c.volumeMl:c.massG,unit:c.unit};if(op==='wash')route.washes.push({id:s.id,...item,cycles:1});else route[op]=item;}
      if(/干燥/.test(s.category??s.title))route.dry={tempC:number(condition(s,'tempC')),hours:number(condition(s,'hours'))};
    }
    plan.workupBranches.push(route);
  }
  if(plan.workupBranches.length>1&&plan.workupBranches.some(b=>b.quench.enabled)&&plan.workupBranches.some(b=>!b.quench.enabled))plan.termination='split-control';
  const recorded=doc.actual??{},actual={charges:[],logs:[],parameters:[],workup:{branches:{}}},consumed=new Set();
  const entry=(block,id)=>Object.fromEntries(Object.entries(recorded).filter(([key,value])=>key.startsWith(`${block}/${id}/`)&&present(value)).map(([key,value])=>[key.slice(block.length+id.length+2),value]));
  const source=key=>copy(design.actualSources?.[key]??key);
  for(const c of charges){
    const row=entry('record-charges',c.id),key=`record-charges/${c.id}/实际值`;
    if(!Object.keys(row).length)continue;
    const observed=row['实际值']??row['实际称量']??row['实际用量'];
    actual.charges.push({...projectedCharge(c),id:`${design.uid}:charge:${c.id}`,mass:observed??null,amount:observed??null,massG:c.unit==='g'?number(observed):null,volumeMl:c.unit==='mL'?number(observed):null,mmol:null,lot:row['批号/纯度']??row['批号纯度']??row['批号']??'',notes:row['备注']??'',source:source(key),recordedFields:copy(row)});
    Object.keys(row).forEach(field=>consumed.add(`record-charges/${c.id}/${field}`));
  }
  const timelineIds=[...new Set(Object.keys(recorded).filter(k=>k.startsWith('record-timeline/')).map(k=>k.split('/')[1]))];
  const t0=actualT0({...design,actual:recorded}),nodes=doc.modular.nodes;
  for(const id of timelineIds){
    const row=entry('record-timeline',id);if(!Object.keys(row).length)continue;
    const node=nodes.find(n=>n.id===id),schedule=schedules.find(s=>id===s.stepId+'-start'||id===s.stepId+'-end');
    const planned=node?.hours??(id.endsWith('-end')?schedule?.plannedEnd:schedule?.plannedStart)??(/^t-\d/.test(id)?Number(id.slice(2).replace('_','.')):null);
    const clock=row['实际钟点']??null,hours=clock!==null&&t0!==null?relativeClock(clock,t0,planned):null;
    actual.logs.push({id:`${design.uid}:log:${id}`,clock,actualClock:clock,relativeHours:number(hours),time:number(hours),plannedHours:planned,stage:node?.label??schedule?.title??id,temp:row['内温']??null,outerTemp:row['外温']??null,rpm:row['搅拌速度']??row['搅拌']??null,water:row['累计水量']??null,observation:row['现象/备注']??row['现象']??row['备注']??'',sampleId:node?.legacySampleId??node?.sampleId??null,source:source(`record-timeline/${id}/实际钟点`),recordedFields:copy(row)});
    Object.keys(row).forEach(field=>consumed.add(`record-timeline/${id}/${field}`));
  }
  // Retain every operation observation as a parameter; never substitute a plan
  // value for a missing measured value, including missing split fractions.
  for(const [key,value]of Object.entries(recorded)){
    if(!present(value)||consumed.has(key)||key.startsWith('record-samples/'))continue;
    const [block,id,field]=key.split('/'),step=steps.find(s=>s.id===id),unit=step?.records?.find(r=>r.key===field)?.unit??unitFor(field??key);
    actual.parameters.push({id:`${design.uid}:parameter:${key}`,name:[step?.title??(block==='record-t0'?'实际起点':id),field].filter(Boolean).join(' / '),value:copy(value),unit,scope:step?.branch??'',source:source(key)});
    if(block==='record-operations'&&step?.stage==='workup'){
      const branchId=step.branch??'main',branch=actual.workup.branches[branchId]??={operations:{}};
      branch.operations[id]??={};branch.operations[id][field]=copy(value);
      if(field==='M2')branch.productMass=copy(value);
      // Only explicitly recorded split observations enter legacy mass accounting.
      if(/分料|split/.test(step.category??step.module??'')){if(field==='质量')branch.mass=copy(value);if(field==='比例')branch.fraction=copy(value);}
      actual.workup.branches[branchId]=branch;
    }
  }
  return {plan,actual};
}
