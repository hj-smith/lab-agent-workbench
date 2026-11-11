import {blankDesign} from './design2.js';
import {compileProcedure} from './procedure.js';

const clone=value=>structuredClone(value);
const walk=steps=>(steps??[]).flatMap(step=>[step,...(step.branches??[]).flatMap(branch=>walk(branch.steps))]);
const fresh=(base,used)=>{let id=base,index=2;while(used.has(id))id=base+'-'+index++;used.add(id);return id;};
const metadata=(id,kind,name)=>({id,kind,name,rev:1,text:'',params:[],controls:[],records:[]});
const savedId=kind=>'procedure-'+kind+'-'+crypto.randomUUID();

/** Seed entries share the existing module store, so library saves retain them. */
export function mergeProcedureSeeds(modules,seeds){
  const items=Array.isArray(seeds)?seeds:seeds?.modules??[];
  const used=new Set((modules??[]).map(item=>item.id));
  return [...modules??[],...clone(items.filter(item=>!used.has(item.id)))];
}
export const procedureSeeds=mergeProcedureSeeds;

export function createFromTemplate(template,{code,title,values={}}={},library={}){
  const design=blankDesign(code);
  design.title=title||template.name;
  design.notes=clone(template.notes??[]);
  design.authoring='procedure';
  design.procedure=clone(template.procedure);
  design.vars.push(...clone(template.vars??[]).filter(variable=>variable.key!=='批次编号'));
  for(const knob of design.procedure.knobs??[]){
    let variable=design.vars.find(item=>item.key===knob.key);
    if(!variable){variable={key:knob.key,label:knob.label??knob.key,unit:knob.unit??'',value:knob.value??0,...knob.role?{role:knob.role}:{}};design.vars.push(variable);}
    if(Object.hasOwn(values,knob.key)){delete variable.expr;variable.value=Number(values[knob.key]);}
  }
  // Absolute historical quantities retain the template's original scale basis.
  const basis=design.procedure.scale;
  if(basis)basis.referenceValue??=Number(template.vars?.find(item=>item.key===basis.knob)?.value??1);
  return compileProcedure(design,library);
}

/** Insert is a copy. All identities inside the copy are allocated in list order. */
export function insertBlock(design,block,at=design.procedure.steps.length){
  const out=clone(design),procedure=out.procedure,steps=clone(block.steps??[]),renames=new Map();
  const used=new Set(walk(procedure.steps).flatMap(step=>[step.id,...(step.points??[]).map(point=>point.id),...(step.branches??[]).map(branch=>branch.id),...(step.outputs??[]).filter(output=>typeof output==='object').map(output=>output.id)]));
  for(const step of walk(steps)){
    const old=step.id,id=fresh(old,used);renames.set(old,id);
    for(const row of step.rows??[]){renames.set(old+'-'+row.id,id+'-'+row.id);if(row.chargeId)renames.set(row.chargeId,id+'-'+row.id);delete row.chargeId;}
    for(const point of step.points??[])renames.set(point.id,fresh(point.id,used));
    for(const branch of step.branches??[])renames.set(branch.id,fresh(branch.id,used));
    for(const output of step.outputs??[])if(output&&typeof output==='object'&&output.id)renames.set(output.id,fresh(output.id,used));
  }
  if(block.scale?.basisRow&&!renames.has(block.scale.basisRow)&&procedure.scale?.basisRow)renames.set(block.scale.basisRow,procedure.scale.basisRow);
  const referenceFields=new Set(['stepId','of','basisRow','chargeId','premixWith','productNodeId','sameSampleAs']);
  const expressionFields=new Set(['expr','value','hoursExpr','hours','tempC','from']);
  const rewrite=(value,field='')=>{
    if(Array.isArray(value))return value.map(item=>rewrite(item,field));
    if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,rewrite(item,key)]));
    if(typeof value!=='string')return value;
    if(referenceFields.has(field)&&renames.has(value))return renames.get(value);
    if(referenceFields.has(field)||expressionFields.has(field))return value.replace(/\[([^\]]+)\]/g,(token,key)=>'['+(renames.get(key)??key)+']');
    return value;
  };
  const copied=rewrite(steps);
  for(const step of walk(copied)){
    step.id=renames.get(step.id)??step.id;
    for(const point of step.points??[])point.id=renames.get(point.id)??point.id;
    for(const branch of step.branches??[])branch.id=renames.get(branch.id)??branch.id;
    for(const output of step.outputs??[])if(output&&typeof output==='object')output.id=renames.get(output.id)??output.id;
    step.fromBlock={id:block.id,rev:block.rev??1};
  }
  procedure.steps.splice(at,0,...copied);
  procedure.knobs??=[];out.vars??=[];
  for(const knob of block.knobs??[])if(!procedure.knobs.some(item=>item.key===knob.key))procedure.knobs.push(clone(knob));
  for(const variable of block.vars??[])if(!out.vars.some(item=>item.key===variable.key))out.vars.push(clone(variable));
  if(!procedure.scale?.basisRow&&block.scale)procedure.scale=rewrite(clone(block.scale));
  if(!procedure.t0?.stepId&&block.t0)procedure.t0=rewrite(clone(block.t0));
  if(!procedure.theory&&block.theory)procedure.theory=clone(block.theory);
  if(!procedure.expectedFactor&&block.expectedFactor)procedure.expectedFactor=clone(block.expectedFactor);
  procedure.blocksUsed??=[];if(!procedure.blocksUsed.some(item=>item.id===block.id&&item.rev===(block.rev??1)))procedure.blocksUsed.push({id:block.id,rev:block.rev??1});
  return out;
}

export function blockFromSteps(design,selectedIds,name){
  const wanted=new Set(selectedIds),selected=steps=>(steps??[]).flatMap(step=>wanted.has(step.id)?[step]:(step.branches??[]).flatMap(branch=>selected(branch.steps))),steps=clone(selected(design.procedure.steps)),keys=new Set();
  const collect=value=>{if(Array.isArray(value))value.forEach(collect);else if(value&&typeof value==='object'){if(value.knob)keys.add(value.knob);Object.values(value).forEach(collect);}else if(typeof value==='string')for(const match of value.matchAll(/\[([^\]]+)\]/g))keys.add(match[1]);};
  collect(steps);collect(design.procedure.scale);collect(design.procedure.expectedFactor);
  // Include referenced variable dependencies, keeping a block usable on a blank design.
  let size;do{size=keys.size;for(const variable of design.vars??[])if(keys.has(variable.key))collect(variable.expr);}while(size!==keys.size);
  const knobs=clone((design.procedure.knobs??[]).filter(knob=>keys.has(knob.key))),knobKeys=new Set(knobs.map(knob=>knob.key));
  return {...metadata(savedId('block'),'block',name),steps,knobs,vars:clone((design.vars??[]).filter(variable=>keys.has(variable.key)&&(variable.source!=='procedure'||knobKeys.has(variable.key)))),scale:clone(design.procedure.scale),t0:clone(design.procedure.t0),theory:clone(design.procedure.theory),expectedFactor:clone(design.procedure.expectedFactor)};
}

export function templateFromDesign(design,name){
  const knobKeys=new Set((design.procedure.knobs??[]).map(knob=>knob.key));
  return {...metadata(savedId('template'),'template',name),procedure:clone(design.procedure),knobs:clone(design.procedure.knobs??[]),vars:clone((design.vars??[]).filter(variable=>variable.role!=='code'&&(variable.source!=='procedure'||knobKeys.has(variable.key)))),notes:clone(design.notes??[])};
}
