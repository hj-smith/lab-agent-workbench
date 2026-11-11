import {designState} from './design-analysis.js';
import {resolveCondition} from './design2.js';
import {renderTemplate} from './expr.js';
// Verify the effective value and executable prose before recording a successful edit.
export function verifyParamEdit(before,after,library,stepId,key,expected){
  const a=designState(before,library),b=designState(after,library),old=a.steps.find(s=>s.id===stepId),now=b.steps.find(s=>s.id===stepId);if(!now)return false;
  const value=typeof expected==='number'?resolveCondition(now.args[key],now.args,b.vars):now.args[key];
  if(typeof expected==='number'?!Number.isFinite(value)||Math.abs(value-expected)>1e-8*Math.max(1,Math.abs(expected)):value!==expected)return false;
  let previous;try{previous=typeof expected==='number'?resolveCondition(old.args[key],old.args,a.vars):old.args[key];}catch{}
  if(previous===value)return true;
  const render=(s,state)=>renderTemplate([s.text,...s.controls??[]].join('\n'),r=>state.get(r.key));
  return render(old,a)!==render(now,b);
}
