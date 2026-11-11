import {recordOption} from './record-options.js';
export const recordConsumers={操作起点:'curve',实际值:'calculation',实际钟点:'curve',内温:'curve',外温:'curve',累计水量:'curve',搅拌速度:'comparison',取样量:'calculation',实际用量:'calculation',质量:'calculation',湿饼质量:'calculation',液相质量:'calculation',干品质量:'calculation',含固率:'calculation',液相含固率:'calculation',M1:'calculation',M2:'calculation',时长:'comparison','现象/备注':'observation',备注:'observation','批号/纯度':'comparison'};
export const leanColumns={charges:['物料','计划值','实际值','备注'],timeline:['计划时间','事件','实际钟点','内温','现象/备注'],operations:['步骤','计划','实际用量','质量','现象/备注'],samples:['样品编号','时间/阶段']};
export const optionalColumns={charges:['批号/纯度','偏差'],timeline:['外温','搅拌速度'],operations:['含固率','M1','M2','开始','结束','时长','液相质量','液相含固率','湿饼质量','干品质量','偏差'],samples:['取样量']};
export const canonicalRecordKey=key=>/^(现象|观察|备注)$/.test(key)?'现象/备注':/^(净质量|总净质量)$/.test(key)?'质量':key==='实际加入量'?'实际用量':/样品质量|取样质量/.test(key)?'取样量':key==='温度'?'内温':key==='搅拌'?'搅拌速度':key;
/** Every numeric field a step declares is kept (ordered core first); filtration adds
 * the filtrate solids. All observations share one cell. 质量 next to 湿饼质量, or next to
 * 液相质量 on a PRE / dilution step, is the same weighing and is dropped; on a filter or
 * slurry step it is the wet cake and stays. */
export function leanRecords(records=[],{title='',stage='',sourceText=''}={}){
  const source=sourceText,items=records.map(r=>typeof r==='string'?{key:r,type:'text'}:{...r}).map(r=>({...r,key:canonicalRecordKey(r.key)}));
  if(/PRE|稀释/.test(title)&&items.some(r=>r.key==='液相质量')&&!items.some(r=>r.key==='湿饼质量'))for(const r of items)if(r.key==='含固率')r.key='液相含固率';
  const add=(key,type='number')=>{if(!items.some(r=>r.key===key))items.push({key,type});};
  if(/过滤/.test(title)&&items.some(r=>r.key==='液相质量'))add('液相含固率');
  if(/干燥|恒重/.test(title)&&/M1|M2|恒重/.test(source+title)){add('M1');add('M2');}
  const rank=r=>{
    if(/干燥|恒重/.test(title))return ['M1','M2'].indexOf(r.key)>=0?100-['M1','M2'].indexOf(r.key):0;
    if(stage!=='workup')return ({内温:100,累计水量:90,取样量:80,外温:50})[r.key]??0;
    if(/PRE|稀释/.test(title))return ({液相质量:100,质量:99,液相含固率:90,含固率:89,实际用量:30})[r.key]??0;
    return ({湿饼质量:100,质量:99,含固率:90,液相质量:80,实际用量:70})[r.key]??0;
  };
  const numeric=items.filter(r=>['number','time'].includes(r.type)&&recordConsumers[r.key]&&!(r.key==='质量'&&items.some(x=>x.key==='湿饼质量'||x.key==='液相质量'&&/PRE|稀释/.test(title)))).sort((a,b)=>rank(b)-rank(a)).filter((r,i,a)=>a.findIndex(x=>x.key===r.key)===i).map(r=>({...r,consumer:recordConsumers[r.key]}));
  return [...numeric,...items.some(r=>!['number','time'].includes(r.type))||stage==='workup'||items.length>numeric.length?[{key:'现象/备注',type:'text',consumer:'observation'}]:[]];
}
export function recordColumns(design,kind,{water=false,solids=false,drying=false}={}){
  const configurations=(design.records??[]).filter(r=>r.kind===kind),config=configurations.length?configurations.reduce((previous,row)=>({...previous,...row,optionalColumns:[...new Set([...previous.optionalColumns??[],...row.optionalColumns??[]])]}),{}):kind==='timeline'?(design.records??[]).find(r=>r.kind==='events'):null;
  if(config?.enabled===false||config?.disabled)return [];
  const defaults=[...leanColumns[kind]??[]];
  if(kind==='timeline'&&water)defaults.splice(defaults.length-1,0,'累计水量');
  if(kind==='operations'){if(solids)defaults.splice(defaults.length-1,0,'含固率');if(drying)defaults.splice(defaults.length-1,0,'M1','M2');}
  return (config?.columns?[...config.columns,...(config.optionalColumns??[]).filter(key=>!config.columns.some(c=>(typeof c==='string'?c:c.key)===key))]:[...defaults,...config?.optionalColumns??[]]).map(c=>{const col=typeof c==='string'?{key:c,label:recordOption(kind,c,design)?.label??c,consumer:recordConsumers[c]}:{...c,label:recordOption(kind,c.key,design)?.label??c.label};col.key=({'现象/备注':'现象','批号/纯度':'批号纯度','时间/阶段':'阶段'})[col.key]??col.key;return col;})
    // 建议钟点 (plan start + plan time) is not printed (user 2026-10-29); the actual clock is what gets recorded.
    .filter(c=>c.key!=='建议钟点');
}
