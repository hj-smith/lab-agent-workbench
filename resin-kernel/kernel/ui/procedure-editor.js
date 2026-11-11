import {scheduleDesign, samplingNodes, resolveCondition, flattenSteps} from '../design2.js';
import {findMaterial, sopMaterialName} from '../materials.js';
import {esc, n} from './shared.js';
import {preparedSolutions} from '../prepared-solutions.js';

export const TYPES=[['add','加料'],['ramp','升温'],['hold','保温'],['cool','降温'],['sample','取样'],['filter','过滤'],['slurry','打浆'],['wash','洗涤'],['dry','干燥'],['split','分叉'],['text','自由步骤']];
export const RULES=[['mass','质量 g'],['volume','体积 mL'],['mmol','物质的量 mmol'],['molRatio','摩尔倍数'],['molPct','mol%'],['solids','达到固含 %'],['volRatio','体积倍数'],['perProduct','mL/g 预计产品'],['perResin','mL/g 理论树脂'],['perBasis','每克基准用量'],['perFeed','倍料液质量'],['soluteWt','溶质：配成 wt%'],['solventWt','溶剂：配成 wt%']];
export const MODES=[['once','一次投入'],['dropwise','匀速滴加'],['slow','缓慢加入'],['reverse','反向滴入']];
export const ROLES=['单体','单体A','催化剂','溶剂','载体溶剂','添加剂','终止剂','中和剂','后处理'];
const typeName=type=>TYPES.find(x=>x[0]===type)?.[1]??type;
const id=prefix=>prefix+'-'+crypto.randomUUID().slice(0,8);
const allSteps=steps=>(steps??[]).flatMap(step=>[step,...(step.branches??[]).flatMap(branch=>allSteps(branch.steps))]);
export function rowsOf(procedure){return [...(procedure.initialRows??[]).map((row,index)=>({step:null,row,path:['procedure','initialRows',index],chargeId:row.chargeId??row.id})),...walkRows(procedure.steps,['procedure','steps'])];}
function walkRows(steps,path){return (steps??[]).flatMap((step,index)=>[...(step.rows??[]).map((row,rowIndex)=>({step,row,path:[...path,index,'rows',rowIndex],chargeId:row.chargeId??`${step.id}-${row.id}`})),...(step.branches??[]).flatMap((branch,branchIndex)=>walkRows(branch.steps,[...path,index,'branches',branchIndex,'steps']))]);}
// A converted batch keeps its workbook sampling nodes, so its new sampling steps start without the 0 h point.
export function freshStep(type,procedure){const step={id:id('p'),type,title:typeName(type)};if(['add','slurry','wash'].includes(type))step.rows=[];if(type==='add')Object.assign(step,{mode:'once',target:'reactor'});if(['ramp','hold','cool'].includes(type))Object.assign(step,{tempC:type==='cool'?65:110,duration:{kind:'fixed',hours:type==='hold'?8:0.5}});if(type==='sample')Object.assign(step,{points:Array.isArray(procedure?.legacyNodes)?[]:[{id:id('sample'),at:'hours',hours:0,label:'反应起点'}],tests:['GPC']});if(type==='filter')Object.assign(step,{outputs:['湿饼','滤液'],weigh:true,solids:true,keepSample:true});if(['slurry','wash'].includes(type))Object.assign(step,{times:1,durationMin:30});if(type==='dry')Object.assign(step,{tempC:50,vacuum:true,hours:24,constantWeight:{extraH:2,maxDeltaPct:0.2}});if(type==='split')step.branches=[{id:id('branch'),name:'A 路',fraction:0.5,steps:[]},{id:id('branch'),name:'B 路',fraction:0.5,steps:[]}];if(type==='text')step.text='';return step;}
export function addStepKnobs(design,step){
 const ensure=(key,label,unit,role,value)=>{let variable=design.vars.find(item=>item.role===role&&typeof item.value==='number')??design.vars.find(item=>item.key===key);if(!variable){variable={key,label,unit,role,value};design.vars.push(variable);}design.procedure.knobs??=[];if(!design.procedure.knobs.some(item=>item.key===variable.key))design.procedure.knobs.push({key:variable.key,label:variable.label??label,unit:variable.unit??unit,role,exposed:true});return {knob:variable.key};};
 if(['ramp','hold'].includes(step.type))step.tempC=ensure('反应内温','反应温度','°C','tempC',110);
 if(step.type==='hold')step.duration.hours=ensure('恒温反应时间','反应时间','h','holdHours',8);
 return step;
}

const niceStep=(range,count)=>{const raw=range/count||1,p=10**Math.floor(Math.log10(raw)),f=raw/p;return (f<=1?1:f<=2?2:f<=5?5:10)*p;};
// Whole hours from t=0 on the readable parts of the axis; a compressed stretch shows only its two ends.
function timeTicks(compressed,offset,maximum,displayMaximum,x){
  const step=Math.max(1,niceStep(displayMaximum,6)),inside=t=>compressed.some(seg=>t>seg.start+1e-6&&t<seg.end-1e-6),candidates=[];
  for(let rel=Math.ceil(-offset/step)*step;rel+offset<=maximum+1e-6;rel+=step)if(!inside(rel+offset))candidates.push(rel);
  for(const seg of compressed)candidates.push(Math.round(seg.start-offset),Math.round(seg.end-offset));
  candidates.push(Math.round(maximum-offset));
  const ticks=[];for(const rel of [...new Set(candidates)].sort((a,b)=>a-b)){const at=x(Math.min(maximum,Math.max(0,rel+offset)));if(!ticks.length||at-ticks.at(-1).x>=34)ticks.push({x:at,label:rel});}
  return ticks;
}

/** Temperature, additions and samples on one time axis (also shown on the simple design page). */
export function procedureCurve(design,library={},state=null){
  let schedule;try{schedule=scheduleDesign({...design,actual:{}},library);}catch(e){return `<p class="pe-warning">${esc(e.message)}</p>`;}
  if(!schedule.length)return '<p class="pe-empty">加上温度、加料或取样步骤后，曲线会自动生成。</p>';
  // A split work-up: after the split each route runs on its own clock and is drawn as its own line.
  const pr=design.procedure,routes=pr.routes?.length>1?pr.routes:[],routeAt=routes.length?pr.steps.findIndex(s=>s.id===pr.routeFrom):-1;
  if(routeAt>=0){
    const later=pr.steps.slice(routeAt+1),laterIds=new Set(later.map(s=>s.id)),shared=schedule.filter(s=>!laterIds.has(s.stepId)),last=shared.at(-1),original=schedule;
    const runs=routes.flatMap(route=>{let clock=last?.end??0,temp=last?.tempC??25;
      return later.filter(step=>!Array.isArray(step.routes)||step.routes.includes(route.id)).flatMap((step,index)=>{
        const item=original.find(x=>x.stepId===step.id);if(!item)return [];
        const heat=step.tempC!==undefined&&Number.isFinite(item.tempC),next=heat?item.tempC:temp,run={...item,branch:route.id,routeRun:true,no:route.id+(index+1),heat,start:clock,end:clock+(item.end-item.start),from:temp,tempC:next};
        clock=run.end;temp=next;return [run];
      });
    });
    schedule=[...shared,...runs];
  }
  const ROUTE_COLOR=['#1d5ca8','#d9601f','#6a4fa3','#2f7d74'],routeColor=id=>ROUTE_COLOR[routes.findIndex(r=>r.id===id)]??'#b8553f',routeTitle=id=>{const r=routes.find(r=>r.id===id);return r?.label?`${id} 路 · ${r.label}`:`${id} 路`;};
  const procedureSteps=allSteps(design.procedure.steps),anchor=schedule.find(s=>s.stepId===design.procedure.t0?.stepId),offset=['reach-temp','charge-done'].includes(design.procedure.t0?.event)?anchor?.end??0:anchor?.start??0;
  const maximum=Math.max(1,...schedule.map(s=>s.end));
  // Only the drawing axis changes. Real hours, schedule, additions and samples
  // all use the same piecewise mapping, including parallel branches.
  const compressed=[];
  for(const scheduled of [...schedule].sort((a,b)=>a.start-b.start)){
   const step=procedureSteps.find(s=>s.id===scheduled.stepId),dry=step?.type==='dry';
   if(!(scheduled.hours>(dry?2:4))||!['dry','hold'].includes(step?.type))continue;
   const label=(dry?'干燥':'保温')+' '+n(scheduled.hours,1)+' h',last=compressed.at(-1);
   if(last&&scheduled.start<last.end){last.end=Math.max(last.end,scheduled.end);if(!last.labels.includes(label))last.labels.push(label);last.shown=Math.max(last.shown,dry?0.75:2);}
   else compressed.push({start:scheduled.start,end:scheduled.end,shown:dry?0.75:2,labels:[label]});
  }
  const displayTime=time=>compressed.reduce((value,segment)=>value-Math.max(0,Math.min(time-segment.start,segment.end-segment.start))*(1-segment.shown/(segment.end-segment.start)),time);
  // Layout: temperature on top, then a 加料 lane and a 取样 lane on the same time axis.
  const L=58,R=636,TOP=34,TEMP_BOTTOM=176,ADD_TOP=188,ADD_ROW=19,SAMPLE_Y=258,AXIS=274;
  const displayMaximum=displayTime(maximum),x=time=>L+displayTime(time)/displayMaximum*(R-L);
  const temps=schedule.flatMap(s=>[s.from,s.tempC]).filter(Number.isFinite),tStep=niceStep(Math.max(...temps,40)-Math.min(...temps,20),4);
  const low=Math.floor(Math.min(20,...temps)/tStep)*tStep,high=Math.ceil((Math.max(...temps)+tStep*0.4)/tStep)*tStep,y=temp=>TEMP_BOTTOM-(temp-low)/(high-low)*(TEMP_BOTTOM-TOP);
  const workupAt=schedule.filter(s=>s.stage==='workup').reduce((at,s)=>Math.min(at,s.start),Infinity);
  let svg=`<svg viewBox="0 0 680 304" role="img" aria-label="工序温度、加料和取样共用时间轴，长保温和干燥压缩显示">`;
  // 反应 / 后处理 bands, the same colours as the material flow lanes.
  const split=Number.isFinite(workupAt)?x(workupAt):R;
  svg+=`<rect x="${L}" y="${TOP-14}" width="${split-L}" height="${AXIS-TOP+14}" fill="#f2f8f6"/><text class="pe-band" x="${L+6}" y="${TOP-3}" fill="#2f7d74">反应</text>`;
  if(split<R)svg+=`<rect x="${split}" y="${TOP-14}" width="${R-split}" height="${AXIS-TOP+14}" fill="#fbf6ea"/><text class="pe-band" x="${split+6}" y="${TOP-3}" fill="#a5762a">后处理</text>`;
  for(const segment of compressed){const left=x(segment.start),right=x(segment.end);svg+=`<g data-axis-break><rect x="${left}" y="${TOP}" width="${right-left}" height="${AXIS-TOP}" fill="#e6ecea" opacity=".7"/><text class="pe-break" x="${(left+right)/2}" y="${TOP+12}" text-anchor="middle">${esc(segment.labels.join(' / '))}</text></g>`;}
  for(let temp=low;temp<=high+1e-9;temp+=tStep){svg+=`<line x1="${L}" y1="${y(temp)}" x2="${R}" y2="${y(temp)}" stroke="#e3e8ea"/><text x="${L-5}" y="${y(temp)+4}" text-anchor="end">${n(temp,0)} °C</text>`;}
  const branches=[...new Set(schedule.map(s=>s.branch??'main'))];
  // Each route's line: solid while its temperature is set, dashed once the steps no longer set one.
  const line=list=>list.flatMap(s=>[[x(s.start),y(['ramp','cool'].includes(s.kind)?s.from:s.tempC)],[x(s.end),y(s.tempC)]]).map(p=>p.join(',')).join(' ');
  for(const route of routes){const list=schedule.filter(s=>s.routeRun&&s.branch===route.id);if(!list.length)continue;const color=routeColor(route.id),lastHeat=list.findLastIndex(s=>s.heat),solid=list.slice(0,lastHeat+1),rest=list.slice(lastHeat+1),tail=list.at(-1);
    if(solid.length)svg+=`<polyline points="${line(solid)}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round"/>`;
    if(rest.length)svg+=`<polyline points="${line(rest)}" fill="none" stroke="${color}" stroke-width="1.5" stroke-dasharray="4 4" opacity=".7"/>`;
    // A long hold of one route says what it is, right on the line (B 路 140 °C 回流分水).
    for(const run of solid){const step=procedureSteps.find(s=>s.id===run.stepId),left=x(run.start),right=x(run.end),title=step?.title??'';if(step?.type==='hold'&&title&&right-left>=title.length*8)svg+=`<text x="${(left+right)/2}" y="${y(run.tempC)-6}" text-anchor="middle" fill="${color}" font-weight="600">${esc(title)}</text>`;}
    const index=routes.indexOf(route);svg+=`<text x="${x(tail.end)-3}" y="${y(tail.tempC)+(index%2?14:-5)}" text-anchor="end" fill="${color}" font-weight="600">${esc(route.id)} 路</text>`;}
  for(const [branchIndex,branch]of branches.filter(b=>!routes.some(r=>r.id===b)).entries()){const list=schedule.filter(s=>(s.branch??'main')===branch),points=list.flatMap(s=>[[x(s.start),y(['ramp','cool'].includes(s.kind)?s.from:s.tempC)],[x(s.end),y(s.tempC)]]).map(p=>p.join(',')).join(' ');svg+=`<polyline points="${points}" fill="none" stroke="${branchIndex?'#d08a5a':'#b8553f'}" stroke-width="2.5" stroke-linejoin="round"${branchIndex?' stroke-dasharray="6 4"':''}/>`;}
  // Lanes: names in the left margin, rows staggered so labels never overlap.
  svg+=routes.length?routes.slice(0,2).map((r,i)=>`<text class="pe-lane-label" x="${L-5}" y="${ADD_TOP+14+i*28}" text-anchor="end" fill="${routeColor(r.id)}">${esc(r.id)} 路加料</text>`).join('')+`<text class="pe-lane-label" x="${L-5}" y="${SAMPLE_Y+4}" text-anchor="end">取样</text>`:`<text class="pe-lane-label" x="${L-5}" y="${ADD_TOP+12}" text-anchor="end">加料</text><text class="pe-lane-label" x="${L-5}" y="${SAMPLE_Y+4}" text-anchor="end">取样</text>`;
  const rowsEnd=[],routeRows=new Map();
  for(const scheduled of schedule){const step=procedureSteps.find(s=>s.id===scheduled.stepId);if(step?.type!=='add')continue;
    const left=x(scheduled.start),width=Math.max(5,x(scheduled.end)-left),label=step.title??'加料',textWidth=label.length*11+8;
    const anchorEnd=left+width+textWidth>R;let yy,fill='#3d6fa8';
    if(scheduled.routeRun){
      // One row for each route, in its colour, labelled with the step number used in the flow and the SOP.
      const index=Math.max(0,routes.findIndex(r=>r.id===scheduled.branch));yy=ADD_TOP+6+index*28;fill=routeColor(scheduled.branch);
      svg+=`<rect x="${left}" y="${yy}" width="${width}" height="10" rx="2" fill="${fill}"><title>${esc(routeTitle(scheduled.branch))} · ${esc(scheduled.no)} ${esc(label)}</title></rect><text x="${left+width/2}" y="${yy-2}" text-anchor="middle" font-size="10" fill="${fill}">${esc(scheduled.no)}</text>`;
      continue;
    }
    let row=rowsEnd.findIndex(end=>left>end+6);if(row<0||row>2){row=rowsEnd.length<3?rowsEnd.length:rowsEnd.indexOf(Math.min(...rowsEnd));}
    yy=ADD_TOP+row*ADD_ROW;rowsEnd[row]=anchorEnd?left+width:left+width+textWidth;
    svg+=`<rect x="${left}" y="${yy}" width="${width}" height="9" rx="2" fill="${fill}"><title>${esc(label)}</title></rect><text x="${anchorEnd?left-4:left+width+4}" y="${yy+8}"${anchorEnd?' text-anchor="end"':''}>${esc(label)}</text>`;}
  let samples=0;
  try{for(const node of samplingNodes(design,state?.steps??flattenSteps(design.steps))){const hours=node.hoursExpr!==undefined?resolveCondition(node.hoursExpr,{},state?.vars??design.vars):node.hours;if(typeof hours!=='number')continue;const absolute=node.kind==='product'?hours:hours+offset;samples++;svg+=`<circle cx="${x(absolute)}" cy="${SAMPLE_Y}" r="4.5" fill="${node.route&&routes.some(r=>r.id===node.route)?routeColor(node.route):'#2f7d74'}" stroke="#fff" stroke-width="1.5"><title>${esc(node.sampleId??node.label??'')} · ${n(hours,2)} h</title></circle>`;}}catch{}
  if(!samples)svg+=`<text x="${L+6}" y="${SAMPLE_Y+4}" fill="#9aa9a6">（只取后处理和成品样）</text>`;
  svg+=`<line x1="${L}" y1="${AXIS}" x2="${R}" y2="${AXIS}" stroke="#9baaad"/>`;
  for(const tick of timeTicks(compressed,offset,maximum,displayMaximum,x))svg+=`<text x="${tick.x}" y="${AXIS+18}" text-anchor="middle"${tick.label===0?' class="pe-t0"':''}>${tick.label===0?'t = 0':tick.label+' h'}</text>`;
  for(const segment of compressed){const mid=(x(segment.start)+x(segment.end))/2;svg+=`<path d="M ${mid-8} ${AXIS+5} l 5 -10 m 2 10 l 5 -10" stroke="#52666b" stroke-width="2.5" fill="none"/>`;}
  svg+=`<line x1="${x(offset)}" y1="${TOP}" x2="${x(offset)}" y2="${AXIS}" stroke="#799b92" stroke-dasharray="3 3"/></svg>`;
  return `<div class="pe-curve">${svg}</div><p class="pe-subtle"><span class="pe-key pe-key-temp"></span>温度 <span class="pe-key pe-key-add"></span>加料 <span class="pe-key pe-key-sample"></span>取样${routes.map(r=>`<span class="pe-key" style="background:${routeColor(r.id)}"></span>${esc(routeTitle(r.id))}`).join('')}${routes.length?' · 虚线：这段步骤没有规定温度':''}${compressed.length?' · 灰色区段和 // 是压缩显示的长时间段，标注的是实际时长':''}</p>`;
}

// Material flow: reaction and work-up in their own colours, what goes in with its
// amount, and the dry product at the end. A work-up split into routes (A / B)
// runs in side-by-side columns from the step where the batch is divided.
const FLOW_KIND={add:'add',ramp:'heat',hold:'heat',cool:'heat',sample:'sample',filter:'workup',slurry:'workup',wash:'workup',dry:'dry',split:'split'};
const WORKUP_TYPES=new Set(['filter','slurry','wash','dry']);
export function procedureFlow(steps,library={},{state,routes=[],routeFrom}={}){
  const name=row=>sopMaterialName(findMaterial(library,row.material)??{material:row.material});
  const amount=row=>{
    const charge=state?.charges?.find(c=>c.id===(row.chargeId??row.id));if(!charge)return '';
    const unit=charge.unit??'g';let value;try{value=state.values.get(unit==='mL'?charge.keys.volume:charge.keys.mass);}catch{return '';}
    return typeof value==='number'?` ${n(value,unit==='mL'?1:value<10?3:2)} ${unit}`:'';
  };
  const rowRoute=row=>row.route??row.legacyCharge?.branch??null,solutions=preparedSolutions(steps);
  const stageOf=(()=>{let workup=false;const map=new Map();for(const step of steps){const compiled=state?.steps?.find(s=>s.id===step.id);if(compiled?.stage==='workup'||WORKUP_TYPES.has(step.type))workup=true;map.set(step,workup?'workup':'reaction');}return step=>map.get(step)??'reaction';})();
  const node=(step,route=null,no=null)=>{
    const rows=(step.rows??[]).filter(row=>!route||!rowRoute(row)||rowRoute(row)===route);
    const kind=FLOW_KIND[step.type]??'text',title=step.title??typeName(step.type);
    // A solution made up beforehand goes in as one liquid.
    const idOf=row=>row.chargeId??row.id,part=id=>state?.charges?.find(c=>c.id===id);
    const chip=(row,index)=>{
      const s=solutions.find(x=>x.soluteId===idOf(row)||x.solventId===idOf(row));
      if(!s)return `<span class="pe-chip">${esc(name(row))}${esc(amount(row))}</span>`;
      if(rows.findIndex(r=>[s.soluteId,s.solventId].includes(idOf(r)))<index)return '';
      const solute=part(s.soluteId),solvent=part(s.solventId);let total='';
      try{const sum=state.values.get(solute.keys.mass)+state.values.get(solvent.keys.mass);if(Number.isFinite(sum))total=` ${n(sum,2)} g`;}catch{}
      return `<span class="pe-chip">${esc(solute?.name??'')}/${esc(solvent?.name??'')} 溶液${esc(total)}</span>`;
    };
    const chips=rows.map(chip).join('');
    const outputs=step.type==='filter'?`<span class="pe-out">→ 湿饼 / 滤液</span>`:'';
    return `<li class="pe-node pe-${kind}"><b>${no?esc(no)+' ':''}${esc(title)}</b>${chips}${outputs}</li>`;
  };
  const product=label=>`<li class="pe-node pe-product"><b>${esc(label)}</b></li>`;
  const ends=steps.some(step=>step.type==='dry');
  const list=(items,route=null)=>items.map((step,i)=>node(step,route,route?route+(i+1):null)).join('');
  // Lanes: 反应 then 后处理 (the work-up may fork into routes).
  const reaction=steps.filter(step=>stageOf(step)==='reaction'),workup=steps.filter(step=>stageOf(step)==='workup');
  const forkAt=routes.length>1?steps.findIndex(step=>step.id===routeFrom):-1;
  let body=`<div class="pe-lane pe-lane-reaction"><span class="pe-lane-name">反应</span><ol>${list(reaction)}</ol></div>`;
  if(forkAt>=0){
    const shared=steps.slice(0,forkAt+1).filter(step=>stageOf(step)==='workup'),after=steps.slice(forkAt+1);
    const own=route=>after.filter(step=>!Array.isArray(step.routes)||step.routes.includes(route.id));
    body+=`<div class="pe-lane pe-lane-workup"><span class="pe-lane-name">后处理</span>${shared.length?`<ol>${list(shared)}</ol>`:''}
      <div class="pe-fork">${routes.map(route=>`<div class="pe-route route-${esc(route.id.toLowerCase())}"><span class="pe-route-name">${esc(route.id)} 路${route.label?' · '+esc(route.label):''}</span><ol>${list(own(route),route.id)}${ends?product(`干品 ${route.id}`):''}</ol></div>`).join('')}</div></div>`;
  }else if(workup.length)body+=`<div class="pe-lane pe-lane-workup"><span class="pe-lane-name">后处理</span><ol>${list(workup)}${ends?product('干品 FP'):''}</ol></div>`;
  return `<div class="pe-flow2">${body}</div>`;
}
