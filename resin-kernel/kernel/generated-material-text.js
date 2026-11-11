import {findMaterial,sopMaterialName,materialAbbreviation} from './materials.js';
import {distillStep} from './distill.js';
import {extractAmounts,extractDurations} from './durations.js';

const walk=steps=>(steps??[]).flatMap(s=>[s,...(s.branches??[]).flatMap(b=>walk(b.steps))]);
const allCharges=design=>[...design.charges??[],...walk(design.steps).flatMap(s=>s.charges??[])];
const escape=value=>String(value).replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
const token=id=>'⟦charge:'+encodeURIComponent(id)+'⟧';
const aliasPattern=aliases=>new RegExp('(?<![A-Za-z0-9-])(?:'+aliases.map(escape).sort((a,b)=>b.length-a.length).join('|')+')(?![A-Za-z])','g');
// Material names inside formulas are stable variable identifiers, not prose.
function tokenize(text,binding){
  const protectedParts=[];
  return String(text??'').replace(/⟦charge:[^⟧]+⟧|\{\{[^{}]*\}\}|\{[^{}]*\}/g,value=>{protectedParts.push(value);return 'MATERIALPROTECTED'+(protectedParts.length-1)+'END';})
    .replace(aliasPattern(binding.aliases),token(binding.chargeId)).replace(/MATERIALPROTECTED(\d+)END/g,(_,i)=>protectedParts[+i]);
}
function sourceBaseline(step){
  if(!step.sourceText)return null;
  const distilled=distillStep(step.sourceText,{stage:step.stage,category:step.category}),originalText=distilled.text;
  if(/降至THF稀释温度/.test(step.title)){const clauses=distilled.text.split(/[，,]/),start=clauses.findIndex(c=>/继续冷却|冷却至|降至/.test(c));if(start>=0)distilled.text=clauses[start];}
  if(/整批溶剂准备/.test(step.title))distilled.text='按投料表分装THF及沉降、两次打浆用MeOH；沉降MeOH控制20–25 °C';
  if(/固液记录与样品归档/.test(step.title))distilled.text='汇总各阶段固/液相质量与含固率、干品及取样损失；按节点留样，归档谱图';
  const separator='\n---CONTROL---\n',amounts=extractAmounts([distilled.text,...distilled.controls].join(separator),p=>`{{${p.key}|0.0}}`),timed=extractDurations(amounts.text,p=>`{{${p.key}|0}}`),parts=timed.text.split(separator);
  return {...distilled,originalText,text:parts[0],controls:parts.slice(1)};
}
const knownSourceTitle=/^(?:整批直接THF稀释|降至THF稀释温度|MeOH沉降|MeOH打浆[12]\/过滤|THF直接稀释|THF稀释)$/;
const edited=(owner,field)=>owner?.userEditedFields?.[field]===true;
function fieldIsEdited(design,path){
  const [kind,id,field]=path.split('/');
  if(kind==='steps')return edited(walk(design.steps).find(step=>step.id===id),field);
  if(kind==='vars')return edited(design.vars?.find(variable=>variable.key===id),field);
  return edited(design,kind);
}

/** Capture generated sources before changing the charge. The stored prose stays
 * intact; equality with each captured source invalidates the binding after an
 * edit. Source-derived imports are recognised by replaying their distillation,
 * rather than assuming every field on an imported experiment is generated. */
export function bindGeneratedMaterialText(design,charge,material,library={}){
  const state=design.generatedMaterialText??={version:1,bindings:{},fields:{}};
  if(state.bindings[charge.id])return state;
  const aliases=[...new Set([material?.id,material?.name,materialAbbreviation(material),sopMaterialName(material),...(material?.aliases??[]).filter(a=>/[\u3400-\u9fff]/.test(a)||a===materialAbbreviation(material))].filter(v=>typeof v==='string'&&v.trim()))];
  const binding={chargeId:charge.id,materialId:material?.id??charge.material,label:sopMaterialName(material??{material:charge.material}),aliases};state.bindings[charge.id]=binding;
  const steps=walk(design.steps),owner=steps.find(s=>s.charges?.some(c=>c.id===charge.id));
  const dilution=owner?.stage==='workup'&&(owner.category==='稀释'||/稀释/.test(owner.title))&&charge.role==='solvent';
  const capture=(path,value,generated)=>{
    if(!generated||typeof value!=='string')return;
    const previous=state.fields[path];if(previous&&previous.source!==value)return;
    const template=tokenize(previous?.template??value,binding);if(template===(previous?.template??value))return;
    state.fields[path]={source:value,template};
  };
  for(const step of steps){
    const module=library.modules?.find(m=>m.id===step.module),baseline=sourceBaseline(step),own=step.id===owner?.id;
    // A dilution solvent is shared by solvent preparation and cooling to its
    // dilution temperature. Analytical quench liquids are separate operations.
    const related=own||dilution&&(/稀释|溶剂准备/.test(step.title)||/稀释/.test(step.sourceText??'')&&!/GPC样|即时终止/.test(step.title));
    if(!related)continue;
    capture('steps/'+step.id+'/title',step.title,!edited(step,'title')&&(step.title===module?.name||design.source&&knownSourceTitle.test(step.title)));
    capture('steps/'+step.id+'/titleTemplate',step.titleTemplate,!edited(step,'title')&&step.titleTemplate!=null);
    const source=step.text??module?.text;
    capture('steps/'+step.id+'/text',source,!edited(step,'text')&&(step.text===undefined||baseline?.text===step.text||step.generatedTextBaseline===step.text));
    for(const [i,text]of (step.controls??module?.controls??[]).entries())capture('steps/'+step.id+'/controls/'+i,text,!edited(step,'controls')&&(step.controls===undefined||baseline?.controls.includes(text)));
    for(const [i,text]of (step.notes??module?.notes??[]).entries())capture('steps/'+step.id+'/notes/'+i,text,!edited(step,'notes')&&(step.notes===undefined||baseline?.notes.includes(text)||step.sourceText?.includes(text)||text===baseline?.text||text===baseline?.originalText));
  }
  if(dilution){
    capture('purpose',design.purpose,!!design.source&&design.purposeTemplate&&!edited(design,'purpose'));
    for(const [i,text]of (Array.isArray(design.notes)?design.notes:[]).entries())capture('notes/'+i,text,!edited(design,'notes')&&design.archiveNotes?.includes(text));
    for(const variable of design.vars??[])capture('vars/'+variable.key+'/label',variable.label,!!design.source&&!edited(variable,'label'));
  }
  return state;
}

export function generatedMaterialText(design,text,path,library={}){
  const state=design.generatedMaterialText,field=state?.fields?.[path];if(fieldIsEdited(design,path)||!field||field.source!==text)return String(text??'');
  return field.template.replace(/⟦charge:([^⟧]+)⟧/g,(_,encoded)=>{
    const id=decodeURIComponent(encoded),charge=allCharges(design).find(c=>c.id===id),alias=(design.materials??[]).find(m=>m.as===charge?.material);
    return charge?sopMaterialName(findMaterial(library,alias?.ref??charge.material)??{material:charge.material}):'待指定物料';
  });
}

/** A displayed volume ratio follows the charge whose volume is referenced. */
export function chargeBasisMaterial(charge,charges=[],vars=[]){
  const seen=new Set(),definitions=new Map(vars.map(v=>[v.key,v]));let found;
  const visit=expression=>{for(const [,key]of String(expression??'').matchAll(/\[([^\]]+)\]/g)){
    if(seen.has(key))continue;seen.add(key);
    const source=charges.find(c=>c.id!==charge.id&&(c.sourceVar===key||Object.values(c.keys??{}).includes(key)));if(source){found??=source;continue;}
    visit(definitions.get(key)?.expr);
  }};
  visit(charge.amount?.value);visit(charge.sourceVar?'['+charge.sourceVar+']':'');return found;
}

export function materialTextWarnings(design,library={}){
  const state=design.generatedMaterialText;if(!state)return [];
  const warnings=[];
  for(const binding of Object.values(state.bindings??{})){
    const charge=allCharges(design).find(c=>c.id===binding.chargeId),current=findMaterial(library,charge?.material);if(!charge||current?.id===binding.materialId||charge.material===binding.materialId)continue;
    const inspect=(text,path,context={})=>{if(typeof text!=='string'||state.fields[path]?.source===text&&!fieldIsEdited(design,path))return;if(aliasPattern(binding.aliases).test(text))warnings.push({level:'warn',message:'文字仍提到 '+binding.label+'；本批投料已改为 '+sopMaterialName(current??{material:charge.material})+'，请核对自定义文字。',chargeId:charge.id,...context});};
    inspect(design.title,'title');inspect(design.purpose,'purpose');inspect(design.question,'question');
    for(const [i,text]of (Array.isArray(design.notes)?design.notes:[]).entries())inspect(text,'notes/'+i);
    const owner=walk(design.steps).find(step=>step.charges?.some(c=>c.id===charge.id));
    for(const step of walk(design.steps))for(const field of ['title','text']){
      const path='steps/'+step.id+'/'+field,baseline=sourceBaseline(step);
      const userText=edited(step,field)||field==='text'&&step.text!==undefined&&(!step.sourceText||baseline?.text!==step.text&&step.generatedTextBaseline!==step.text);
      if(userText||step.id===owner?.id||state.fields[path])inspect(step[field],path,{stepId:step.id});
    }
  }
  return warnings;
}
