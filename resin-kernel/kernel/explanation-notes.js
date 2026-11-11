/** Source explanations contain revision history and vendor identity evidence.
 * Keep that evidence on the design, while the execution sheet contains only
 * current instructions and scientific interpretation boundaries. */
export function executionNotes(notes,{sourced=false}={}){
  const seen=new Set(),result=[];
  for(const raw of notes){let text=typeof raw==='string'?raw:raw?.text??'';
    if(sourced&&/^\d{2}相对\d{2}/.test(text)&&text.includes('变化不能'))text='AddH 加入和热历程改变同时存在，'+text.slice(text.indexOf('变化不能'));
    if(sourced&&/^\s*(?:基线[:：]|历史方向[:：]|\d{2}[:：]|\d{2}相对\d{2}|AddH身份[:：]|AddH[:：]用户确认|水[:：]沿用|顺序[:：])/.test(text))continue;
    for(let sentence of text.split(/(?<=[。！？])\s*/)){
      sentence=sentence.trim();if(!sentence)continue;
      if(sourced&&/https?:\/\/|www\.|Sigma|用户确认|规格表/.test(sentence))continue;
      const key=sentence.replace(/[\s，,；;。:：]/g,'').replace(/理论上|充分|设计上限/g,'');
      if(seen.has(key))continue;seen.add(key);result.push(sentence);
    }
  }
  return result;
}
/** Formula homes on the explanation sheet exist only for reachable constants. */
export function referencedVariables(value,vars){
  const found=new Set();const visit=value=>{if(typeof value==='string'){for(const match of value.matchAll(/\[([^\]]+)\]/g))found.add(match[1]);}else if(Array.isArray(value))value.forEach(visit);else if(value&&typeof value==='object')for(const [key,item]of Object.entries(value)){if(key==='var'&&typeof item==='string')found.add(item);else if(key!=='sourceText')visit(item);}};visit(value);
  const definitions=new Map(vars.map(v=>[v.key,v]));for(const key of found)visit(definitions.get(key)?.expr);return found;
}
