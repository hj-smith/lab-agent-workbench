// Derived and imported experiments start as a copy of an Excel SOP: ~21 archive passages,
// three sampling steps and a historical branch record. The user asked
// (2026-10-26) to tidy them into short editable steps; the full original text
// stays on each step as legacySourceText. Amounts, knobs and rows are untouched.
const sentence=text=>{
  const parts=String(text??'').split(/(?<=。)/).filter(Boolean);
  let out=parts[0]??'';
  if(out.replace(/\{[^}]*\}/g,'').length<16&&parts[1])out+=parts[1];
  return out.trim();
};
const samplePrep=step=>step.type==='text'&&/终止|稀释|称量/.test(step.title??'')&&/GPC|HPLC|样品/.test(step.textOverride??'');
const observation=step=>step.type==='text'&&/观察/.test(step.title??'')&&!(step.rows??[]).length;
const preparation=step=>step.type==='text'&&/溶剂准备/.test(step.title??'')&&!(step.rows??[]).length;

export function tidyProcedure(design){
  const p=design.procedure;
  if(!p?.converted||p.tidied)return design;
  const out=structuredClone(design),q=out.procedure,notes=[],observed=[];
  let sampled=false;
  q.steps=q.steps.filter(step=>{
    if(step.type==='split'&&step.id==='archive-branch-identities'&&(step.branches??[]).every(b=>!(b.steps??[]).length))return false;
    if(samplePrep(step)){notes.push(sentence(step.textOverride));return false;}
    if(preparation(step))return false;
    if(observation(step)&&q.steps.some(s=>s.type==='hold')){observed.push(sentence(step.textOverride));return false;}
    if(step.type==='sample'&&!step.tempC){if(sampled&&!/PRE|留样/.test(step.title??''))return false;sampled=true;}
    return true;
  });
  for(const step of q.steps){
    if(step.type==='sample'&&step.tempC){step.type='cool';step.title='终点取样后降温';}
    if(step.textOverride&&step.textOverrideOrigin==='archive'){
      step.textOverride=sentence(step.textOverride);
      step.textOverrideOrigin='tidied';
    }
  }
  const hold=q.steps.find(s=>s.type==='hold');
  if(hold&&observed.length)hold.textOverride=[hold.textOverride,...observed].filter(Boolean).join('');
  if(notes.length)out.samplingPlan={...out.samplingPlan,note:[out.samplingPlan?.note,...notes].filter(Boolean).join(' ')};
  q.tidied=true;
  return out;
}
