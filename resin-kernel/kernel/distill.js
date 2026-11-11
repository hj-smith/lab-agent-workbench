import {leanRecords} from './record-spec.js';
// Deterministic sentence routing shared by library extraction and historical conversion.
// Keep the complete original separately: this function never uses a language model.
export function distillStep(sourceText, {stage='reaction',category='其他'}={}) {
  const tokens=[];
  const protectedText=String(sourceText??'').replace(/\{\{[^{}]*\}\}|\{[^{}]*\}/g,t=>{tokens.push(t);return `ZZTOKEN${tokens.length-1}ZZ`;});
  const restore=s=>s.replace(/ZZTOKEN(\d+)ZZ/g,(_,i)=>tokens[+i]);
  const operations=[],constraints=[],notes=[],records=[];
  const addRecord=label=>{label=label.replace(/^(?:并|同时|需|应)?(?:观察并记录|称量并记录|记录|观察)/,'').replace(/[。；;]+$/,'').trim();if(!label)return;for(const part of label.split(/[、，,]|及|和|并记录/).filter(Boolean)){const key=restore(part).replace(/^[并及和]/,'').trim();if(key&&!records.some(r=>r.key===key))records.push({key,type:/时刻|时间|开始|结束/.test(key)?'time':/质量|用量|温度|内温|外温|转速|水量|含固率|真空度/.test(key)?'number':'text'});}};
  for(let sentence of protectedText.split(/[。；;\n]/).map(s=>s.trim()).filter(Boolean)) {
    // Route record clauses even when they follow an operation in the same sentence.
    const recordAt=sentence.search(/(?:(?<!批)(?<!检测)记录|观察)(?!样品归档)/);
    if(recordAt>=0){let tail=sentence.slice(recordAt);const actionAt=tail.search(/[，,](?:结束后取|随后取|再取|取[^，,]*样)/);if(actionAt>=0){operations.push(tail.slice(actionAt+1));tail=tail.slice(0,actionAt);}const constraintAt=tail.search(/[，,](?:确认|避免|不得|不为|控制)/);addRecord(constraintAt>=0?tail.slice(0,constraintAt):tail);if(constraintAt>=0)constraints.push(restore(tail.slice(constraintAt+1)));sentence=sentence.slice(0,recordAt).replace(/(?:[，,]|并|且)+$/,'').trim();}
    if(!sentence)continue;
    if(/^从开始.*即计时[，,]/.test(sentence)&&/降至|搅拌/.test(sentence)){
      const at=sentence.search(/[，,]/),timing=sentence.slice(0,at),parenthetical=sentence.match(/（[^）]*计入[^）]*）/g)??[];
      constraints.push(restore([timing,...parenthetical.map(s=>s.slice(1,-1))].join('；')));operations.push(sentence.slice(at+1).replace(/（[^）]*计入[^）]*）/g,''));continue;
    }
    if(/^(?:不为|不得|避免|确认|控制)|≤|恒重|降温时间计入|含降温|计入.*总量|计时|记t=0/.test(sentence))constraints.push(restore(sentence));
    else if(/加入|升温|维持|降温|冷却|降至|过滤|打浆|干燥|称|取样|取GPC|取HPLC|留样|装配|搅拌|投料|预置|预装|分料|摊|混匀|稀释|终止|回流|氮气|THF|MeOH/.test(sentence))operations.push(sentence);
    else notes.push(restore(sentence));
  }
  const source=String(sourceText??'');
  const inferred=[[/称.*(?:湿饼|固液)|湿饼.*称/,'湿饼质量','number'],[/滤液|液相/,'液相质量','number'],[/含固率/,'含固率','number'],[/滤液.*含固率|含固率.*滤液|固.*液.*含固率/,'液相含固率','number'],[/称M1|称M2|称量|称总净质量/,'净质量','number'],[/取样|留样|GPC样/,'取样质量','number']];
  for(const [re,key,type]of inferred)if(re.test(source)&&!records.some(r=>r.key===key))records.push({key,type});
  if((stage==='workup'||category==='取样')&&!records.length)records.push({key:'现象',type:'text'});
  let text=operations.join('；').replace(/分别称量?/g,'称').replace(/继续搅拌/g,'续搅').replace(/充分打浆/g,'打浆').replace(/整批|直接|逐渐|随后|分别/g,'').replace(/；+/g,'；');
  text=restore(text);
  if(!text&&constraints.some(s=>/降至|升温|取样|干燥/.test(s)))text=constraints.find(s=>/降至|升温|取样|干燥/.test(s));
  if(!text)text=category==='记录'?'汇总质量与含固率，按节点留样并归档谱图':category==='取样'?'按节点取样并留存':source.split(/[。；;\n]/).find(Boolean)??'按本步条件执行';
  // Prefer concrete limits and timing rules; overflow remains inspectable in notes.
  const ranked=constraints.map((text,index)=>({text,index,score:(/\d|\{|≤|±/.test(text)?2:0)+(/计时|计入|不得|不为/.test(text)?2:0)})).sort((a,b)=>b.score-a.score||a.index-b.index);
  const chosen=new Set(ranked.slice(0,2).map(c=>c.index));
  const normalize=s=>s.replace(/[\s，,；;。]/g,'');
  const controls=constraints.filter((s,i)=>chosen.has(i)&&!normalize(text).includes(normalize(s)));notes.push(...constraints.filter((_,i)=>!chosen.has(i)));
  // Numeric readings are for calculations/curves/comparison, never an observation checklist.
  for(const [re,key]of [[/内外温|最高内温|记录[^。；]*内温/,'内温'],[/累计水相|集水量|累计水量/,'累计水量'],[/称总净质量/,'液相质量'],[/测含固率/,'含固率']])if(re.test(source)&&!records.some(r=>r.key===key))records.push({key,type:'number'});
  return {text,controls,records:leanRecords(records,{stage,title:category,sourceText:source}),notes,sourceText:source};
}
