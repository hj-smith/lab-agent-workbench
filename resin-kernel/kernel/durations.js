// Preserve the full source separately. Every literal duration in executable prose
// gets its own parameter, including addition windows and their tolerances.
export function extractDurations(text,make){
  const params=[];let index=0;
  const output=String(text??'').replace(/(?<![\w.}\]-])(\d+(?:\.\d+)?)(?:\s*([±–—-])\s*(\d+(?:\.\d+)?))?\s*(min|h)\b/g,(token,nominal,operator,other,unit,offset)=>{
    const prefix=String(text).slice(Math.max(0,offset-14),offset),tail=String(text).slice(offset+token.length,offset+token.length+8),label=/内加入|内加|内滴/.test(tail)?'加入时限':/打浆/.test(prefix)?'打浆时长':/续搅|搅拌/.test(prefix)?'续搅时长':/干燥|续干/.test(prefix)?'干燥时长':'时长';
    if(/^(?:节点|时点|取样点)/.test(tail)||/t\s*=\s*$/.test(prefix))return token;
    const create=(value,suffix='')=>{const p={key:'duration'+(++index),label:label+suffix,unit,default:Number(value),duration:true};params.push(p);return make(p);};
    return create(nominal)+(other?operator+create(other,operator==='±'?'容差':'上限'):'')+' '+unit;
  });return {text:output,params};
}
export const durationMinutes=hours=>Math.round(hours*60);
export function extractAmounts(text,make){const params=[];const output=String(text??'').replace(/(?<![\d./}\]])(\d+(?:\.\d+)?)\s*(mL|mmol|mg|g|L)\b(?!\s*\/)/g,(_,value,unit)=>{const p={key:'sourceAmount'+(params.length+1),label:'用量 '+(params.length+1),unit,default:Number(value),sourceAmount:true};params.push(p);return make(p)+' '+unit;});return {text:output,params};}
export const durationText=hours=>Number.isFinite(hours)?(hours<2?durationMinutes(hours)+' min':Number(hours.toFixed(3))+' h'):'待指定';
