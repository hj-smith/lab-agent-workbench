import {recordColumns,recordConsumers} from './record-spec.js';
import {resolveCondition,scheduleDesign} from './design2.js';
import {toleranceFor} from './design-analysis.js';
import {escapeTemplate} from './expr.js';
import {roleVar} from './var-roles.js';
import {recordOption,enabledRecordOptions,hplcComponents} from './record-options.js';
import {unitFormat,samplingTimeText} from './language.js';
import {preparedSolutions} from './prepared-solutions.js';
const T=t=>({t}),F=f=>({f}),number=(value)=>typeof value==='number'&&Number.isFinite(value);
// A split work-up reads route by route: shared rows first, then all of A, then all of B, each under its own band.
export const routeName=(design,id)=>{const r=design.procedure?.routes?.find(r=>r.id===id);return r?.label?`${id} 路 · ${r.label}`:`${id} 路`;};
// After the split each route is read on its own, so its steps are numbered A1, A2 … B1, B2 ….
export function routeStepNumbers(steps){
  const first=steps.find(s=>s.routes?.length),out=new Map();if(!first)return out;
  for(const route of first.routes){let no=0;for(const s of steps)if(s!==first&&s.routes?.includes(route))out.set(`${s.id}@${route}`,`${route}${++no}`);}
  return out;
}
export function routeBlocks(design,rows,routeOf,span){
  const routes=[...new Set(rows.map(routeOf).filter(Boolean))].sort();
  return !routes.length?rows:[...rows.filter(r=>!routeOf(r)),...routes.flatMap(route=>[{id:'route-'+route,cells:[{...T(escapeTemplate(routeName(design,route))),span,style:'section',route}]},...rows.filter(r=>routeOf(r)===route)])];
}
// The batch record is an 18-unit grid (检测记录 keeps 12). Columns take width by
// what goes in them: event and remark text wide, single numbers narrow.
export const BATCH_WIDTH=18,SAMPLE_WIDTH=12;
// Work-up sample names: known codes, and any step code with -C / -F (HEP-C → 正庚烷滤饼).
const STAGE_WORDS={PRE:'沉降前',PRECIP:'沉降',S1:'打浆1',S2:'打浆2',S3:'打浆3',HEP:'正庚烷',IPA:'IPA',MEOH:'甲醇',FP:'干品'};
export function stageLabel(code){
  const c=String(code??'');if(SAMPLE_STAGES[c])return SAMPLE_STAGES[c];
  const m=/^(.+?)-([CF])$/i.exec(c),word=STAGE_WORDS[(m?m[1]:c).toUpperCase()];
  return word===undefined?undefined:m?word+(m[2].toUpperCase()==='C'?'滤饼':'滤液'):word;
}
export const SAMPLE_STAGES={PRE:'沉降前',PRECIP:'沉降液','PRECIP-C':'沉降滤饼','PRECIP-F':'沉降滤液',S1:'打浆1','S1-C':'打浆1滤饼','S1-F':'打浆1滤液',S2:'打浆2','S2-C':'打浆2滤饼','S2-F':'打浆2滤液',FP:'干品'};
const WEIGHTS={物料:4,项目:4,计划值:3,实际值:3,备注:4,'批号/纯度':3,批号纯度:3,偏差:2,单位:1.5,计划时间:1,相对时间:1,建议钟点:1.1,事件:6,实际钟点:1.5,操作起点:1.6,内温:1.05,外温:1.05,累计水量:1.4,'现象/备注':5,现象:5,搅拌速度:1.2,转速:1.2,步骤:2.4,计划:5,计划条件:5,实际用量:1.3,质量:2,湿饼质量:1.6,含固率:1.5,M1:1.3,M2:1.3,液相质量:1.5,液相含固率:1.5,开始:1.3,结束:1.3,时长:1.2,样品编号:3.5,'时间/阶段':2.2,NMR:2.5,节点编号:2.5};
function spread(columns,total){
  const weight=columns.map(c=>WEIGHTS[c.key]??WEIGHTS[c.label]??(/^w[\d.]+$/.test(c.label??'')?Number(c.label.slice(1)):1.4)),sum=weight.reduce((a,b)=>a+b,0),raw=weight.map(w=>w/sum*total);
  const spans=raw.map(x=>Math.max(1,Math.floor(x)));let left=total-spans.reduce((a,b)=>a+b,0);
  // Width left over goes to the text columns first (事件, 现象/备注 …).
  const order=weight.map((w,i)=>[w,i]).sort((a,b)=>b[0]-a[0]);
  for(let k=0;left>0;k=(k+1)%order.length,left--)spans[order[k][1]]++;
  while(left<0){spans[spans.indexOf(Math.max(...spans))]--;left++;}
  return spans;
}
export function weighingFormat(value,unit,settings={}){
  return unitFormat(unit,'0.000');
}
export function compileRecords(design,{steps,charges,nodes,vars,values,clockFormula}){
  const hints={water:steps.some(s=>/(?<!不用|不|无)分水/.test(s.title+s.sourceText)||(s.records??[]).some(r=>r.key==='累计水量')),solids:steps.some(s=>s.stage==='workup'&&s.records.some(r=>/含固率/.test(r.key))),drying:steps.some(s=>/干燥|恒重/.test(s.title))};
  const configs=Object.fromEntries(['charges','timeline','operations','samples'].map(k=>[k,recordColumns(design,k,hints)])),declarationMap={},rules=[],checkRows=[];
  const stepReagent=c=>c.role!=='solvent'&&steps.some(s=>s.stage==='reaction'&&s.charges.some(x=>x.id===c.id));
  if(charges.some(stepReagent)&&!configs.charges.some(c=>c.key==='批号纯度'||c.key==='批号/纯度'))configs.charges.push({key:'批号纯度',label:'批号/纯度'});
  if(steps.some(s=>s.stage==='reaction'&&s.charges.length)&&!configs.timeline.some(c=>c.key==='操作起点'))configs.timeline.splice(configs.timeline.findIndex(c=>c.key==='实际钟点')+1,0,{key:'操作起点',label:'补加/处理开始'});
  if(steps.some(s=>s.records.some(r=>r.key==='液相质量')&&/过滤/.test(s.title))&&!configs.operations.some(c=>c.key==='液相质量'))configs.operations.push({key:'液相质量',label:'液相 g'});
  if(steps.some(s=>s.records.some(r=>r.key==='液相含固率'))&&!configs.operations.some(c=>c.key==='液相含固率'))configs.operations.push({key:'液相含固率',label:'液相含固率'});
  // A box that already holds a recorded value always shows, also when the step no longer asks for it.
  const recorded=(id,key)=>{const v=design.actual?.[`record-operations/${id}/${key}`];return v!==undefined&&v!==null&&v!=='';};
  // A split work-up records each route on its own row: step16@A, step16@B.
  const routeLabel=id=>routeName(design,id);
  const rowsOf=s=>s.routes?.length?s.routes.map(route=>({s,route,id:`${s.id}@${route}`})):[{s,id:s.id}];
  // 液相 = filtrate, supernatant or the whole liquid, as the workbooks call it.
  const RECORDED_LABELS={液相质量:'液相 g',液相含固率:'液相含固率',干品质量:'干品 g',湿饼质量:'湿饼 g',质量:'湿饼 / 稀释液 g',含固率:'固相含固率',M1:'M1',M2:'M2'};
  for(const {id} of steps.filter(s=>s.stage==='workup').flatMap(rowsOf))for(const key of Object.keys(RECORDED_LABELS))
    if(recorded(id,key)&&!configs.operations.some(c=>c.key===key))configs.operations.push({key,label:RECORDED_LABELS[key]});
  for(const c of configs.operations)if(c.key==='含固率')c.label='固相含固率';
  // A recorded weighing always has its box: 取样量 of a time point or a sample.
  if(Object.keys(design.actual??{}).some(key=>/^record-timeline\/[^/]+\/取样量$/.test(key))&&!configs.timeline.some(c=>c.key==='取样量'))configs.timeline.push({key:'取样量',label:'取样量 g'});
  if(Object.keys(design.actual??{}).some(key=>/^record-samples\/[^/]+\/取样量$/.test(key))&&!configs.samples.some(c=>c.key==='取样量'))configs.samples.push({key:'取样量',label:'取样量 g'});
  if(nodes.some(n=>n.tests.includes('GPC')))for(const key of ['Mn','Mw','Đ'])if(!configs.samples.some(c=>c.key===key))configs.samples.push({key,label:key+(key==='Đ'?'':' (g/mol)')});
  if(nodes.some(n=>n.tests.includes('NMR'))&&!configs.samples.some(c=>c.key==='NMR'))configs.samples.push({key:'NMR',label:'NMR 结果 / 文件'});
  for(const c of configs.operations)if(c.key==='质量')c.label='湿饼 / 稀释液 g';
  const input=(key,type='text',unit='',extra={})=>({input:true,type,unit,consumer:recordConsumers[key]??(type==='text'?'comparison':'calculation'),...type==='time'?{fmt:'hh:mm'}:type==='number'&&unit?{fmt:['Mn','Mw'].includes(key)?'#,##0':unitFormat(unit,'0.###')}:{},...extra});
  const colAlias={项目:'物料',相对时间:'计划时间',计划条件:'计划',现象:'现象/备注',搅拌:'搅拌速度',编号:'样品编号',阶段:'时间/阶段',批号纯度:'批号/纯度'};
  function table(kind,id,title,rows){const columns=configs[kind];if(!columns.length)return null;const config=design.records?.find(r=>r.kind===kind);if(columns.length>12)throw Error('记录表最多 12 列');if(new Set(columns.map(c=>c.key)).size!==columns.length)throw Error('记录列 key 重复');
    const widths=spread(columns,kind==='samples'?SAMPLE_WIDTH:BATCH_WIDTH),spans=columns.map((c,i)=>({...c,span:widths[i]}));
    return {type:'table',id,title:config?.title??title,header:spans,rows:rows.map(r=>({id:r.id,...r.sourceText?{sourceText:r.sourceText}:{},cells:spans.map(c=>({...r.cell(colAlias[c.key]??c.key,c.key),span:c.span}))}))};
  }
  const has=(kind,key)=>configs[kind].some(c=>(colAlias[c.key]??c.key)===key),actualCol=kind=>configs[kind].find(c=>(colAlias[c.key]??c.key)==='实际值')?.key??'实际值';
  function devCell(key,plan,unit,tolerance,cell,actualExpr){
    const a=actualExpr??`@[${key}]`,limit=tolerance.absolute!==undefined?String(tolerance.absolute):`ABS(${plan})*${tolerance.relative}`,test=`IF(AND(ISNUMBER(${a}),ISNUMBER(${plan})),ABS(${a}-(${plan}))>MAX(${limit},0.0000000001),FALSE)`;
    const id='check-'+checkRows.length,flag=`@[record-deviations/${id}/超差]`,f=`IF(${flag},"实际 "&TEXT(${a},"0.###")&" ${unit}，"&IF(${a}>=(${plan}),"+","")&IF((${plan})=0,TEXT(${a}-(${plan}),"0.###")&" ${unit}",TEXT((${a}-(${plan}))/ABS(${plan}),"0.0%")),"")`;
    rules.push({key,test,plan,unit,tolerance});checkRows.push({id,recordKey:key,cells:[T(escapeTemplate((charges.find(c=>key.startsWith('record-charges/'+c.id+'/'))?.name??steps.find(s=>key.includes('/'+s.id+'-')||key.includes('/'+s.id+'/'))?.title??'过程节点')+' / '+key.split('/').at(-1))),F(f),F(test)]});return {...cell,deviation:{test,plan,unit,tolerance},deviationFormula:f};
  }
  // Which route a weighed row belongs to, and the solutions weighed up beforehand.
  const routeTag=c=>/^[A-Z]$/.test(c.branch??'')?`（${c.branch} 路）`:'';
  const solutions=preparedSolutions(design.procedure?.steps),solutionOf=c=>solutions.find(s=>s.soluteId===c.id||s.solventId===c.id);
  const solutionName=s=>`${charges.find(c=>c.id===s.soluteId)?.name}/${charges.find(c=>c.id===s.solventId)?.name} 溶液`;
  const weights=charges.map(c=>({id:c.id,cell:(key,original)=>{
    const planned=`[${c.unit==='mL'?c.keys.volume:c.keys.mass}]`,fmt=weighingFormat(values.get(c.unit==='mL'?c.keys.volume:c.keys.mass),c.unit,design.displayRounding);
    if(key==='物料'){const owner=steps.find(s=>s.charges.some(x=>x.id===c.id)),stage=owner?(/打浆\s*1/.test(owner.title)?'打浆1':/打浆\s*2/.test(owner.title)?'打浆2':/沉降/.test(owner.title)?'沉降':null):null;const made=solutionOf(c);return T(escapeTemplate(c.name+(stage?'（'+stage+'）':'')+(made?'（配 '+solutionName(made)+'）':'')+routeTag(c)));}if(key==='计划值')return {f:planned,fmt:fmt+`" ${c.unit}"`};if(key==='实际值')return devCell(`record-charges/${c.id}/${original}`,planned,c.unit,toleranceFor(design,'charge',c),input(key,'number',c.unit,{fmt:fmt+`" ${c.unit}"`}));
    if(key==='批号/纯度')return stepReagent(c)||(design.records?.find(r=>r.kind==='charges')?.optionalColumns??[]).includes('批号/纯度')?input(key):{};
    if(key==='备注')return input(key);if(key==='单位')return T(c.unit);if(key==='偏差')return F(rules.find(r=>r.key===`record-charges/${c.id}/实际值`)?checkRows.find(r=>r.recordKey===`record-charges/${c.id}/实际值`).cells[1].f:'""');
    if(key==='纯度')return input(key,'number','w/w');if(['开始','结束'].includes(key))return input(key,'time');return input(key);
  }}));
  // The solution itself: how much of it went into the flask.
  for(const s of solutions){
    const solute=charges.find(c=>c.id===s.soluteId),solvent=charges.find(c=>c.id===s.solventId);if(!solute||!solvent)continue;
    // Weighed together, so the two parts and the solution stay next to each other, route by route.
    const parts=[solute,solvent].map(c=>weights.find(r=>r.id===c.id)),at=Math.min(...parts.map(r=>weights.indexOf(r)));
    for(const part of parts)weights.splice(weights.indexOf(part),1);
    weights.splice(at,0,...parts,{id:'solution-'+s.id,cell:key=>key==='物料'?T(escapeTemplate(solutionName(s)+' 加入量'+routeTag(solute))):key==='计划值'?{f:`[${solute.keys.mass}]+[${solvent.keys.mass}]`,fmt:'0.00" g"'}:key==='实际值'?input(key,'number','g',{fmt:'0.00" g"'}):key==='备注'?input(key):key==='单位'?T('g'):{}});
  }
  const initial=charges.filter(c=>design.charges.some(x=>x.id===c.id)),massRef=c=>{const ref=`@[record-charges/${c.id}/${actualCol('charges')}]`;return c.unit==='g'?ref:`(${ref}*[${c.keys.density}])`;};
  if(initial.length&&has('charges','实际值')&&initial.every(c=>c.unit==='g'||number(values.get(c.keys.density)))){
    const refs=initial.map(c=>`@[record-charges/${c.id}/${actualCol('charges')}]`),solids=initial.filter(c=>c.role!=='solvent'&&c.amount.mode!=='solidFill'),sum=cs=>cs.map(massRef).join('+')||'0';
    weights.push({id:'actual-solid-fill',cell:key=>key==='物料'?T('实际初始固含'):key==='实际值'?{f:`IF(COUNT(${refs.join(',')})<${refs.length},"",IF((${sum(initial)})=0,"",(${sum(solids)})/(${sum(initial)})))`,fmt:'0.0%'}:{}});
  }
  if(design.recordCalculations?.includes('mmol')&&has('charges','实际值'))for(const c of charges.filter(c=>c.unit==='g'&&number(values.get(c.keys.mw)))){const purity=has('charges','纯度')?`IF(ISNUMBER(@[record-charges/${c.id}/纯度]),@[record-charges/${c.id}/纯度],[${c.keys.purity}])`:`[${c.keys.purity}]`;weights.push({id:c.id+'~actual-mmol',cell:key=>key==='物料'?T(escapeTemplate(c.name)+'实际物质的量'):key==='实际值'?{f:`IF(ISNUMBER(@[record-charges/${c.id}/${actualCol('charges')}]),@[record-charges/${c.id}/${actualCol('charges')}]*(${purity})*${c.concentration??1}/[${c.keys.mw}]*1000,"")`,fmt:'0.000" mmol"'}:{}});}
  const events=[],segments=[],schedule=scheduleDesign(design,{}, {steps,vars});let lastTemp='25';
  const t0Plan=design.procedure?.t0,t0Time=schedule.find(t=>t.stepId===t0Plan?.stepId),late=['reach-temp','charge-done'].includes(t0Plan?.event);
  const shift=t0Time?(late?t0Time.plannedEnd:t0Time.plannedStart):0,shiftExpr=late?t0Time?.endExpr:t0Time?.startExpr,onT0=(h,expr)=>Math.abs(shift)>1e-9?[h-shift,`(${expr})-(${shiftExpr})`]:[h,expr];
  const conditionExpr=(s,key)=>String(s.conditions?.[key]??0).replace(/\{\{([^}]+)\}\}/g,(_,k)=>`(${s.args[k]})`);
  for(const s of steps.filter(s=>s.stage!=='workup')){
    if(!s.conditions&&!s.charges.length&&!s.records.some(r=>['number','time'].includes(r.type)))continue;
    const time=schedule.find(t=>t.stepId===s.id),h=time.hours,[start,startExpr]=onT0(time.plannedStart,time.startExpr),[end,endExpr]=onT0(time.plannedEnd,time.endExpr),target=s.conditions?.tempC!=null?conditionExpr(s,'tempC'):lastTemp,from=s.conditions?.from!=null?conditionExpr(s,'from'):lastTemp,ramp=['ramp','cool'].includes(s.conditions?.kind)&&h>0;
    events.push({id:s.id+'-start',hours:start,hoursExpr:startExpr,label:s.title,step:s,tempExpr:ramp?from:target});
    if(h>0){events.push({id:s.id+'-end',hours:end,hoursExpr:endExpr,label:s.title+'结束',step:s,tempExpr:target});segments.push({s,start,end,from:ramp?from:target,target});}
    lastTemp=target;
  }
  for(const n of nodes.filter(n=>n.hours!=null)){const segment=segments.find(s=>n.hours>=s.start&&n.hours<=s.end),fraction=segment?(n.hours-segment.start)/(segment.end-segment.start):0;events.push({id:n.id,hours:n.hours,hoursExpr:n.hoursExpr??String(n.hours),label:n.id==='PRE-AddH'?'取 PRE-AddH 样（GPC）':n.id==='AddH-80C-1h'?'取 AddH-80C-1h 样（GPC）':(n.route?n.route+' 路 ':'')+'取样（'+(n.tests??['GPC']).join('/')+'）',node:n,conditionStep:segment?.s,tempExpr:segment?`(${segment.from})+((${segment.target})-(${segment.from}))*${fraction}`:undefined});}events.sort((a,b)=>a.hours-b.hours);
  const merged=[],timelineAliases=new Map();
  for(const event of events){let row=merged.find(r=>Math.abs(r.hours-event.hours)<1e-9&&(!event.node||!r.events.some(e=>e.node&&e.node.id!==event.node.id)));if(!row){row={...event,events:[]};merged.push(row);}row.events.push(event);}
  for(const row of merged){const preferred=row.events.find(e=>e.node?.id.startsWith('t-'))??row.events.find(e=>e.node)??row.events[0],sourcePoint=preferred.node?(design.samplingPlan.points??[]).find(p=>typeof p==='object'&&p.sameSampleAs===preferred.node.id):null;row.id=sourcePoint?.id??preferred.id;row.node=preferred.node;row.conditionStep=preferred.conditionStep??row.conditionStep;row.hoursExpr=preferred.node?.hoursExpr??row.hoursExpr??preferred.hoursExpr;
    const samples=row.events.filter(e=>e.node),operations=row.events.filter(e=>!e.node&&e.step?.category!=='取样');
    const pre=samples.find(e=>e.node?.id==='PRE-AddH');
    const isEndpointAddition=e=>e.step?.charges?.some(c=>c.material==='AddH')&&/后直接加\s*AddH/.test(e.step?.titleTemplate??e.step?.title??'');
    const descriptions=[...samples.filter(e=>e.node?.id!=='PRE-AddH'||e===pre).filter(e=>!pre||!e.node.id.startsWith('t-')).map(e=>e.label),...operations.map(e=>isEndpointAddition(e)?'加 AddH':e.label)];
    row.label=[...new Set(descriptions)].join(' → ');if(row.hours===0)row.label='升温 → 首次达温 t0'+(row.node?' → 取样（'+(row.node.tests??['GPC']).join('/')+'）':'')+' → 开始恒温';else if(pre)row.label='取 PRE-AddH 样（GPC） → 加 AddH → 降温处理';else if(samples.some(e=>e.node?.id==='AddH-80C-1h'))row.label='取 AddH-80C-1h 样（GPC） → 降至稀释温度';
    for(const event of operations.filter(e=>e.id.endsWith('-start')&&e.step?.charges.length&&!isEndpointAddition(e)))if(!row.label.includes(event.label))row.label+=' → '+event.label;
    row.tempExpr=preferred.tempExpr??row.tempExpr;for(const e of row.events)timelineAliases.set(e.id,row.id);
  }
  const timeline=merged.map(e=>({id:e.id,cell:(key,original)=>{
    if(key==='计划时间')return {f:e.hoursExpr,fmt:'0.###" h"'};if(key==='建议钟点')return F(clockFormula(e.hoursExpr));if(key==='事件')return T(escapeTemplate(e.label));if(key==='节点编号')return T(e.node?.sampleId??'');
    if(key==='实际钟点')return e.node||e.step?.records.length||e.step?.conditions?input(key,'time'):{};
    if(key==='操作起点')return e.events.some(e=>e.step?.charges.length)?input(key,'time'):{};
    // An observation can come at any point of the process, so every row takes one.
    if(key==='现象/备注')return input(key);
    if(['内温','外温'].includes(key)){
      if(!e.node&&!e.step?.records.some(r=>r.key===key)&&!(design.records?.find(r=>r.kind==='timeline')?.optionalColumns??[]).includes(key))return {};
      const s=e.step??e.conditionStep,planned=e.tempExpr;
      const cell=input(key,'number','°C');return planned===undefined||e.hours===0?cell:devCell(`record-timeline/${e.id}/${original}`,typeof planned==='number'?String(planned):String(planned).replace(/\{\{([^}]+)\}\}/g,(_,k)=>`(${s.args[k]})`),'°C',toleranceFor(design,'temperature',s),cell);
    }
    if(key==='累计水量')return e.node||e.step?.records.some(r=>r.key===key)?input(key,'number','mL'):{};
    const option=recordOption('timeline',key,design);return input(key,option?.type??(['搅拌速度','取样量','含固率','GPC取样质量'].includes(key)?'number':'text'),option?.unit??(key==='含固率'?'w/w':key==='取样量'?'g':''));
  }}));
  const workup=steps.filter(s=>s.stage==='workup'),massField=s=>s.records.find(r=>/质量$/.test(r.key)&&!/^M/.test(r.key))?.key;
  const nodeRoute=n=>n.route!==undefined?n.route:/^([A-Z])-/.exec(n.id)?.[1];
  const operations=workup.flatMap(rowsOf).map(({s,route,id})=>({id,cell:(key,original)=>{
    const own=c=>s.charges.some(x=>x.id===c.id)&&(!route||!c.branch||c.branch===route);
    if(key==='步骤')return T(escapeTemplate(route?`${s.title}（${routeLabel(route)}）`:s.title));if(key==='计划')return T(s.text);if(key==='节点编号')return T(nodes.filter(n=>n.stepId===s.id&&(!route||nodeRoute(n)===route)).map(n=>n.sampleId).join(' / '));if(key==='现象/备注')return s.recording===false?{}:input(key);
    if(s.recording===false)return {};
    if(key==='实际用量'){const c=charges.find(own);return c&&has('charges','实际值')?F(`IF(ISNUMBER(@[record-charges/${c.id}/${actualCol('charges')}]),@[record-charges/${c.id}/${actualCol('charges')}],"")`):s.records.some(r=>r.key===key)?input(key,'number'):{};}
    if(key==='质量')return massField(s)?input(key,'number','g',{recordField:massField(s)}):recorded(id,key)?input(key,'number','g'):{};
    if(key==='含固率')return s.records.some(r=>r.key==='含固率')||recorded(id,key)?input(key,'number','w/w'):{};
    // A step that asks for M1/M2 gets the box; older designs still go by the 干燥/恒重 titles.
    if(key==='M1')return s.records.some(r=>r.key==='M1')||recorded(id,key)||/干燥/.test(s.title)&&!/恒重/.test(s.title)?input(key,'number','g'):{};
    if(key==='M2')return s.records.some(r=>r.key==='M2')||recorded(id,key)||/恒重/.test(s.title)?input(key,'number','g'):{};
    if(key==='时长'){const h=s.conditions?.hours;if(h!=null)return devCell(`record-operations/${id}/${original}`,conditionExpr(s,'hours'),'h',toleranceFor(design,'duration',s),input(key,'number','h'));return {};}
    if(['开始','结束'].includes(key))return input(key,'time');
    if(['湿饼质量','干品质量','液相质量','液相含固率'].includes(key))return recorded(id,key)?input(key,'number',/含固/.test(key)?'w/w':'g'):s.recording!==false&&(s.records.some(r=>r.key===key)||key==='液相含固率'&&s.records.some(r=>r.key==='液相质量')&&/过滤/.test(s.title))&&!(key==='液相质量'&&massField(s)==='液相质量')?input(key,'number',/含固/.test(key)?'w/w':'g'):{};
    return input(key);
  }}));
  // Preserve handwritten extra timepoints as ordinary semantic rows on subsequent exports.
  // A parameter change may separate events previously sharing a display row.
  // Keep every already-recorded timeline key addressable under its original id.
  for(const id of new Set(Object.keys(design.actual??{}).map(key=>/^record-timeline\/([^/]+)\//.exec(key)?.[1]).filter(Boolean)))if(!timeline.some(row=>row.id===id)){
    const event=events.find(e=>e.id===id);timeline.push({id,cell:key=>key==='事件'?T(escapeTemplate(event?.label??'原始过程记录（计划已调整）')):key==='计划时间'&&event?{f:event.hoursExpr,fmt:'0.###" h"'}:key==='建议钟点'&&event?F(clockFormula(event.hoursExpr)):['事件','计划时间','建议钟点','节点编号'].includes(key)?{}:input(key,/温|量|率|取样/.test(key)?'number':/钟点/.test(key)?'time':'text',/温/.test(key)?'°C':/率/.test(key)?'w/w':/量/.test(key)?'g':'' )});
  }
  for(const row of design.importedRows??[]){const rows=row.block==='record-timeline'?timeline:row.block==='record-operations'?operations:weights;if(rows.some(r=>r.id===row.id))continue;rows.push({id:row.id,cell:key=>['事件','步骤','物料'].includes(key)?T(escapeTemplate(row.label)):key==='计划时间'&&row.hours!=null?{v:row.hours,fmt:'0.###" h"'}:key==='建议钟点'&&row.hours!=null?F(clockFormula(String(row.hours))):input(key,/温|量|率|M[12]|实际值/.test(key)?'number':/钟点/.test(key)?'time':'text',/温/.test(key)?'°C':/率/.test(key)?'w/w':'')});}
  timeline.sort((a,b)=>(merged.find(e=>e.id===a.id)?.hours??design.importedRows?.find(r=>r.id===a.id)?.hours??Infinity)-(merged.find(e=>e.id===b.id)?.hours??design.importedRows?.find(r=>r.id===b.id)?.hours??Infinity));
  const chargeTable=table('charges','record-charges','称量记录',weights),timeTable=table('timeline','record-timeline','过程记录（取样与步骤事件）',timeline),opTable=table('operations','record-operations','后处理记录',operations);
  // Duration checks use actual clocks, including explicit overnight/day serials.
  const t0=timeTable?.rows.some(row=>row.id==='t-0')&&timeTable.header.some(c=>c.key==='实际钟点')?'@[record-timeline/t-0/实际钟点]':'""',face=clock=>`MOD(${clock},1)`,relativeExpr=(clock,planned)=>{
    const day=`MAX(CEILING(${face(t0)}-0.5-${face(clock)},1),MIN(INT(${face(t0)}+1.5-${face(clock)}),ROUND(${face(t0)}+(${planned})/24-${face(clock)},0)))`;
    return `IF(AND(${clock}>=1,${clock}<100),(${clock}-${face(t0)})*24,IF(AND(${clock}>=100,${t0}>=100),(${clock}-${t0})*24,(${face(clock)}+${day}-${face(t0)})*24))`;
  };
  const durationExpr=(start,end,plannedStart,plannedEnd)=>`IF(COUNT(${start},${end})<2,"",IF(ISNUMBER(${t0}),(${relativeExpr(end,plannedEnd)})-(${relativeExpr(start,plannedStart)}),MOD(${end}-${start},1)*24))`;
  for(const s of steps){if(!(s.conditions&&resolveCondition(s.conditions.hours??0,s.args,vars)>0))continue;const block=s.stage==='workup'?opTable:timeTable;if(!block)continue;
    const key=s.stage==='workup'?'结束':'实际钟点',startKey=s.stage==='workup'?'开始':'实际钟点',rowId=s.stage==='workup'?s.id:timelineAliases.get(s.id+'-end'),startId=s.stage==='workup'?s.id:timelineAliases.get(s.id+'-start'),endCol=block.header.findIndex(h=>h.key===key),startCol=block.header.findIndex(h=>h.key===startKey),row=block.rows.find(r=>r.id===rowId),startRow=block.rows.find(r=>r.id===startId);
    if(endCol<0||startCol<0||!row?.cells[endCol]?.input||!startRow?.cells[startCol]?.input)continue;
    const opStartCol=block.header.findIndex(c=>c.key==='操作起点'),operationStart=s.stage!=='workup'&&opStartCol>=0&&startRow.cells[opStartCol]?.input,operation=`@[${block.id}/${startId}/操作起点]`,ordinary=`@[${block.id}/${startId}/${startKey}]`;
    const end=`@[${block.id}/${rowId}/${key}]`,start=operationStart?`IF(ISNUMBER(${operation}),${operation},${ordinary})`:ordinary,plannedStart=merged.find(r=>r.id===startId)?.hoursExpr??'0',plannedEnd=`(${plannedStart})+(${conditionExpr(s,'hours')})`;row.cells[endCol]=devCell(`${block.id}/${rowId}/${key}`,conditionExpr(s,'hours'),'h',toleranceFor(design,'duration',s),row.cells[endCol],durationExpr(start,end,plannedStart,plannedEnd));
  }
  for(const s of steps){const block=s.stage==='workup'?opTable:timeTable,rowId=s.stage==='workup'?rowsOf(s)[0].id:s.id+'-start';if(!block)continue;const row=block.rows.find(r=>r.id===(timelineAliases.get(rowId)??rowId));
    for(const r of s.records){if(s.stage!=='workup'&&['质量','实际用量'].includes(r.key)){const charge=charges.find(c=>s.charges.some(x=>x.id===c.id));if(charge&&has('charges','实际值'))declarationMap[s.id+'/'+r.key]=`record-charges/${charge.id}/${actualCol('charges')}`;}const key=r.key==='M1'||r.key==='M2'?r.key:r.key==='液相含固率'?'液相含固率':/含固率/.test(r.key)?'含固率':r.key==='液相质量'&&massField(s)!=='液相质量'?'液相质量':/质量$/.test(r.key)&&s.stage==='workup'?'质量':r.key;const i=block.header.findIndex(c=>(colAlias[c.key]??c.key)===key);if(i>=0&&row?.cells[i]?.input)declarationMap[s.id+'/'+r.key]=`${block.id}/${row.id}/${block.header[i].key}`;}
  }
  const dry=workup.find(s=>/干燥/.test(s.title)&&!/恒重/.test(s.title)),constant=workup.findLast(s=>/恒重/.test(s.title)||s.records.some(r=>r.key==='M2')),resin=roleVar(vars,'theoreticalResin');
  if(opTable&&dry&&constant&&has('operations','M1')&&has('operations','M2')){
    // Per route the theory is the route's share: the weighed portions at the split, else the planned share.
    const split=workup.find(s=>s.id===design.procedure?.routeFrom),massCol=opTable.header.find(c=>c.key==='质量')?.key;
    const portion=route=>massCol&&opTable.rows.find(r=>r.id===`${split?.id}@${route}`)?.cells[opTable.header.findIndex(c=>c.key===massCol)]?.input?`@[record-operations/${split.id}@${route}/${massCol}]`:null;
    const share=route=>{const planned=String(design.procedure.routes.find(r=>r.id===route)?.fraction??1/split.routes.length),all=split.routes.map(portion);
      return all.every(Boolean)?`IF(COUNT(${all.join(',')})=${all.length},${portion(route)}/(${all.join('+')}),${planned})`:planned;};
    for(const route of constant.routes??[null]){
      const at=id=>route&&dry.routes?.includes(route)?`${id}@${route}`:id,suffix=route?`@${route}`:'',name=route?`（${routeLabel(route)}）`:'';
      const m1=`@[record-operations/${at(dry.id)}/M1]`,m2=`@[record-operations/${at(constant.id)}/M2]`,theory=resin?(route&&split?`[${resin.key}]*(${share(route)})`:`[${resin.key}]`):null;
      opTable.rows.push({id:'constant-check'+suffix,cells:opTable.header.map(c=>({...((colAlias[c.key]??c.key)==='步骤'?T('恒重判断'+name):c.key==='M2'?F(`IF(COUNT(${m1},${m2})<2,"",IF(${m2}=0,"",IF(ABS(${m2}-${m1})/${m2}<=0.002,"恒重","需续干")))`):{}),span:c.span}))});
      if(theory)opTable.rows.push({id:'final-yield'+suffix,cells:opTable.header.map(c=>({...((colAlias[c.key]??c.key)==='步骤'?T('产率（未扣添加剂）'+name):c.key==='M2'?{f:`IF(ISNUMBER(${m2}),IF((${theory})=0,"",${m2}/(${theory})),"")`,fmt:'0.0 %'}:{}),span:c.span}))});
    }
  }
  const samples=nodes.map(n=>({id:n.id,cell:key=>key==='样品编号'?T(escapeTemplate(n.sampleId)):key==='时间/阶段'?n.hoursExpr?T(`{${n.hoursExpr}|0.###} h`):T(escapeTemplate(stageLabel(n.stage??n.id)??samplingTimeText(n,steps))):['Mn','Mw','Đ'].includes(key)?n.tests.includes('GPC')?input(key,'number',key==='Đ'?'1':'g/mol'):{}:key==='NMR'?n.tests.includes('NMR')?input(key):{}:key==='取样量'?input(key,'number','g'):recordOption('samples',key,design)?input(key,recordOption('samples',key,design).type??'number',recordOption('samples',key,design).unit):input(key)}));
  // 反应 HPLC as the lab's own sheet: date, weighed sample, diluted net mass, one peak area per
  // component of the HPLC methods and the method used (blank = the one in use on that date).
  // Contents are worked out on 样品与性能 from the method.
  const hplcNodes=nodes.filter(n=>n.tests.includes('HPLC')).sort((a,b)=>(a.hours??Infinity)-(b.hours??Infinity)),hk=(n,key)=>`@[record-hplc/${n.id}/${key}]`;
  const hplcColumns=[['样品编号','样品编号',4],['时间/阶段','时间',1.4],['检测日期','检测日期',2.2],['取样量','取样量 g',1.8],['稀释后净重','稀释后净重 g',2],...hplcComponents().map(name=>[name+'峰面积',name+' 峰面积',2.1]),['方法','方法',2.4],['备注','其它峰 / 备注',3.4]];
  const hplcSpans=spread(hplcColumns.map(([key,label,w])=>({key:'hplc-'+key,label:'w'+w})),SAMPLE_WIDTH).map((x,i)=>x);
  const hplcTable=hplcNodes.length?{type:'table',id:'record-hplc',title:'反应 HPLC',header:hplcColumns.map(([key,label],i)=>({key,label,span:hplcSpans[i]})),rows:hplcNodes.map(n=>({id:n.id,cells:hplcColumns.map(([key],i)=>({...(key==='样品编号'?T(escapeTemplate(n.sampleId+'-HPLC')):key==='时间/阶段'?(n.hoursExpr?T(`{${n.hoursExpr}|0.###} h`):T(escapeTemplate(samplingTimeText(n,steps)))):['备注','检测日期','方法'].includes(key)?input(key):input(key,'number',/峰面积/.test(key)?'':'g',{fmt:/峰面积/.test(key)?'0.000':'0.000#'})),span:hplcSpans[i]}))}))}:null;
  const context=enabledRecordOptions(design,'context').map(key=>recordOption('context',key,design)).filter(Boolean),contextTable=context.length?{type:'table',id:'record-context',title:'执行补充记录',header:[{key:'项目',label:'项目',span:8},{key:'实际值',label:'实际值',span:6},{key:'单位',label:'单位',span:4}],rows:context.map(field=>({id:field.key,cells:[{...T(field.label),span:8},{...input(field.key,field.type??'number',field.unit??''),span:6},{...T(field.unit??(field.type==='time'?'钟点':'')),span:4}]}))}:null;
  const blocks=(block,routeOf)=>{if(block)block.rows=routeBlocks(design,block.rows,routeOf,block.header.reduce((sum,h)=>sum+(h.span??1),0));};
  const chargeRoute=new Map([...charges.map(c=>[c.id,c.branch]),...solutions.map(s=>['solution-'+s.id,charges.find(c=>c.id===s.soluteId)?.branch])]);
  blocks(chargeTable,r=>{const route=chargeRoute.get(r.id);return /^[A-Z]$/.test(route??'')?route:null;});
  blocks(opTable,r=>/@([A-Z])$/.exec(r.id)?.[1]??null);
  const batch=[{type:'title',id:'batch-title',text:'{[批次编号]}批记录'},chargeTable,contextTable,timeTable,opTable,{type:'note',id:'execution-signature',text:'执行人：________________    日期：________________'}].filter(Boolean);
  const operationStarts=Object.fromEntries(merged.filter(row=>row.events.some(e=>e.step?.charges.length)).map(row=>[row.id,[...row.events.filter(e=>e.id.endsWith('-start')&&e.step?.conditions?.hours),...row.events.filter(e=>e.id.endsWith('-start')&&e.step?.charges.length)].map(e=>e.id)]));
  const sampleTable=table('samples','record-samples','样品与计划检测',samples);
  blocks(sampleTable,r=>{const n=nodes.find(x=>x.id===r.id);return n?(n.route!==undefined?n.route:/^([A-Z])-/.exec(n.id)?.[1])??null:null;});
  return {batch,operationStarts,timelineAliases:Object.fromEntries(timelineAliases),sampleTable,hplcTable,checks:{name:'偏差检查',hidden:true,landscape:true,widths:[35,60,12],blocks:[{type:'table',id:'record-deviations',header:[{key:'记录项',label:'记录项'},{key:'偏差',label:'偏差'},{key:'超差',label:'超差（条件格式核对）'}],rows:checkRows}]},declarationMap,rules};
}
