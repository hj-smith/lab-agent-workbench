// HPLC 含量和单体剩余 %：从界面文件 app/sample-workspace.js:297-347（树脂模块 2.11.0）原样挪进来，公式一字不动。
// 只改了两处接口：hplcPoints 的方法表由参数 methods 传入（原来读界面的 hplcMethods()），各函数加了 export。
import {designState} from './design-analysis.js';
import {scheduleDesign} from './design2.js';

const list=value=>Array.isArray(value)?value:[];
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const num=value=>finite(value)?value:typeof value==='string'&&value.trim()!==''&&Number.isFinite(Number(value))?Number(value):null;

export function methodFor(name,date,all){
  const named=all.find(m=>m.name===name);if(named)return named;
  const started=all.filter(m=>!date||!m.from||String(m.from)<=date).sort((a,b)=>String(b.from??'').localeCompare(String(a.from??'')));
  return started[0]??all[0];
}
export function hplcPoints(design,methods){
  const all=list(methods),a=design?.actual??{};
  return list(design?.samplingPlan?.nodes).filter(n=>list(n.tests).includes('HPLC')).map(n=>{
    const v=key=>a[`record-hplc/${n.id}/${key}`],mass=num(v('取样量')),net=num(v('稀释后净重')),date=String(v('检测日期')??'').trim(),method=methodFor(String(v('方法')??'').trim(),date,all);
    const rho=num(method?.diluentDensity),factor=finite(mass)&&finite(net)&&mass>0?net/mass:null,volume=num(method?.volumeFactor)??1;
    const conc=Object.fromEntries(list(method?.components).map(c=>{
      const area=num(v(c.name+'峰面积')),slope=num(c.slope),vial=finite(area)&&slope?(area-(num(c.intercept)??0))/slope:null;
      return [c.name,{area,material:c.material,value:finite(vial)&&finite(factor)&&rho?vial*volume*factor/rho:null,sheet:finite(vial)&&finite(factor)?vial*volume*factor:null}];
    }));
    return {id:n.id,hours:num(n.hours),sampleId:n.sampleId,mass,net,factor,conc,date,method,note:v('备注')??''};
  }).filter(p=>finite(p.hours)&&Object.values(p.conc).some(c=>finite(c.value))).sort((x,y)=>x.hours-y.hours);
}
// 剩余 = 实测含量 ÷ 没反应时应有的含量 (user 2026-10-30: 以理论投料为 100 %). The batch is walked in time:
// charges at their recorded mass (a dropwise one by how long it has run), samples taken out (they take
// monomer and liquid alike, which counts once more liquid is added afterwards) and water taken off.
export function flaskModel(design,library){
  if(!design?.procedure||!library?.materials)return null;
  try{
    const {steps,charges,values}=designState(design,library),schedule=scheduleDesign(design,library),p=design.procedure,a=design.actual??{};
    const t0=schedule.find(x=>x.stepId===p.t0?.stepId),shift=t0?(['reach-temp','charge-done'].includes(p.t0?.event)?t0.plannedEnd:t0.plannedStart):0;
    const density=c=>num(c.density??library.materials.find(m=>m.id===c.materialId)?.density);
    const massOf=c=>{const rec=num(a[`record-charges/${c.id}/实际值`]);if(rec!==null)return c.unit==='mL'?(density(c)?rec*density(c):null):rec;const m=num(values.get(c.keys.mass)),v=num(values.get(c.keys.volume));return m??(v!==null&&density(c)?v*density(c):null);};
    const dropwise=new Set(list(p.steps).filter(s=>s.mode==='dropwise').map(s=>s.id));
    const adds=charges.filter(c=>list(design.charges).some(x=>x.id===c.id)).map(c=>({c,start:-Infinity,end:-Infinity}));
    for(const s of steps.filter(s=>s.stage!=='workup')){const t=schedule.find(x=>x.stepId===s.id);if(t)for(const ref of list(s.charges)){const c=charges.find(x=>x.id===ref.id);if(c)adds.push({c,start:t.plannedStart-shift,end:dropwise.has(s.id)?t.plannedEnd-shift:t.plannedStart-shift});}}
    // when each record row happened, on the t = 0 clock
    const when=new Map(list(design.samplingPlan?.nodes).filter(n=>finite(num(n.hours))).map(n=>[n.id,num(n.hours)]));
    for(const t of schedule){when.set(t.stepId+'-start',t.plannedStart-shift);when.set(t.stepId+'-end',t.plannedEnd-shift);}
    const taken=new Map(),water=[];
    for(const [key,value] of Object.entries(a)){
      const m=/^record-(timeline|hplc|samples)\/([^/]+)\/(取样量|GPC取样质量|累计水量)$/.exec(key),h=m?when.get(m[2]):null,x=num(value);
      if(!m||!finite(h)||!finite(x))continue;
      if(m[3]==='累计水量'){if(m[1]==='timeline')water.push({h,g:x});}else taken.set(h,(taken.get(h)??0)+x);
    }
    water.sort((x,y)=>x.h-y.h);
    const is=(c,material)=>!!material&&[c.materialId,c.abbr,c.name].some(x=>String(x??'').toUpperCase()===String(material).toUpperCase());
    const share=(add,t)=>t===-Infinity?0:add.end===add.start?(t>=add.start?1:0):Math.min(1,Math.max(0,(t-add.start)/(add.end-add.start)));
    const at=(h,material)=>{
      let liquid=0,monomer=0,prev=-Infinity,off=0;
      for(const t of [...[...new Set([...taken.keys(),...water.map(w=>w.h)])].filter(t=>t<h).sort((x,y)=>x-y),h]){
        for(const add of adds){const m=massOf(add.c);if(m===null)continue;const part=m*(share(add,t)-share(add,prev));liquid+=part;if(is(add.c,material))monomer+=part;}
        const w=water.filter(w=>w.h<=t).at(-1)?.g??0;liquid-=w-off;off=w;
        if(t<h&&taken.has(t)&&liquid>0){monomer*=1-taken.get(t)/liquid;liquid-=taken.get(t);}
        prev=t;
      }
      return {liquid,monomer};
    };
    return {at,removed:[...taken.values()].reduce((x,y)=>x+y,0),water:water.at(-1)?.g??0};
  }catch{return null;}
}
export const remaining=(pts,name,flask)=>flask?pts.filter(p=>finite(p.conc[name]?.value)&&p.conc[name].material).flatMap(p=>{const s=flask.at(p.hours,p.conc[name].material);return s.monomer>0?[{x:p.hours,y:p.conc[name].value*s.liquid/s.monomer/10,liquid:s.liquid,monomer:s.monomer}]:[];}):[];
