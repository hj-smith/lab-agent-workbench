import {compile} from './compile.js';
import {chargeKeys,flattenSteps,resolveSteps,samplingNodes,variableValues,resolveCondition} from './design2.js';
import {findMaterial,sopMaterialName} from './materials.js';
import {moduleCategory,argumentExpr} from './modules.js';

const clone=value=>structuredClone(value);
export const procedureExpression=value=>value&&typeof value==='object'&&value.knob?`[${value.knob}]`:String(value??0);
const expr=procedureExpression,token=(value,fmt='0.0')=>`{${expr(value)}|${fmt}}`;
const names={add:'加料',ramp:'升温',hold:'反应',cool:'降温',sample:'取样',filter:'过滤',slurry:'打浆',wash:'洗涤',dry:'干燥',split:'分料',text:'自由步骤'};
const roles={单体:'reactant',单体B:'reactant',单体A:'reactant',催化剂:'catalyst',溶剂:'solvent',载体溶剂:'solvent',后处理:'solvent',后处理溶剂:'solvent',终止剂:'quench',中和剂:'quench',添加剂:'addition'};
export function procedureRows(procedure){
  const result=(procedure.initialRows??[]).map(row=>({step:null,row,chargeId:row.chargeId??row.id}));
  // A row of one route of a split work-up is sized on that route's share.
  const routes=procedure.routes?.length>1?procedure.routes:[],total=routes.reduce((sum,r)=>sum+Number(r.fraction??0),0);
  const share=row=>{const route=routes.find(r=>r.id===(row.route??row.legacyCharge?.branch));return route?(total>0?Number(route.fraction??0)/total:1/routes.length):1;};
  const walk=(steps,fraction=1,branchId)=>{
    for(const step of steps??[]){
      for(const row of step.rows??[])result.push({step,row,
        chargeId:row.chargeId??`${step.id}-${row.id}`,fraction:steps===procedure.steps?fraction*share(row):fraction,branchId});
      const branches=step.branches??[];
      const total=branches.reduce((sum,b)=>sum+Number(b.fraction??b.massShare??0),0);
      for(const branch of branches){
        const share=total>0?Number(branch.fraction??branch.massShare??0)/total:1/branches.length;
        walk(branch.steps,fraction*share,branch.id);
      }
    }
  };
  walk(procedure.steps);
  return result;
}
const categoryFor=(step,index)=>step.category??(step.type==='add'?(step.target==='precipitation'?'沉降':step.target==='quench'?'终止':index===0?'投料':'加料'):step.type==='text'?moduleCategory(step.title??step.text??''):step.type==='split'?'加料':names[step.type]);
const workupStep=(step,category)=>['filter','slurry','wash','dry','split'].includes(step.type)||['终止','中和','稀释','沉降','过滤','打浆','洗涤','干燥','恒重'].includes(category)||['precipitation','quench'].includes(step.target);

function variablesForProcedure(out){
  const p=out.procedure,keys=new Set((p.knobs??[]).map(k=>k.key));
  const previous=new Map((out.vars??[]).map(v=>[v.key,v]));
  out.vars=(out.vars??[]).filter(v=>v.source!=='procedure'||keys.has(v.key)||
    v.key===p.scale.knob||v.key===p.expectedFactor?.knob);
  const define=(key,definition)=>{
    const index=out.vars.findIndex(v=>v.key===key);
    const variable={key,label:key,unit:'',...definition,source:'procedure'};
    if(index<0)out.vars.push(variable);else out.vars[index]=variable;
    return variable;
  };
  for(const knob of p.knobs??[]){
    const existing=previous.get(knob.key);
    define(knob.key,{...knob,...existing,key:knob.key,label:knob.label??existing?.label??knob.key,
      unit:knob.unit??existing?.unit??'',role:knob.role??existing?.role,
      ...existing?.expr!==undefined?{expr:existing.expr}:{value:existing?.value??knob.default??knob.value??''}});
  }
  if(!out.vars.some(v=>v.key===p.scale.knob)){
    define(p.scale.knob,{role:'scale',unit:p.scale.unit??'g',value:p.scale.referenceValue??''});
  }
  out.vars.find(v=>v.key===p.scale.knob).role='scale';
  const factorKey=p.expectedFactor?.knob??'预计分离系数';
  if(!out.vars.some(v=>v.key===factorKey))define(factorKey,{unit:'w/w',role:'yieldFraction',value:''});
  out.vars.find(v=>v.key===factorKey).role='yieldFraction';
  return {define,factorKey};
}

function amountContext(out,library){
  const p=out.procedure,rows=procedureRows(p),lookup=new Map();
  for(const info of rows){
    lookup.set(info.chargeId,info);
    lookup.set(`${info.step?.id??''}-${info.row.id}`,info);
    if(!lookup.has(info.row.id))lookup.set(info.row.id,info);
  }
  const aliases=new Map((out.materials??[]).map(m=>[m.as,m.ref]));
  const getMaterial=row=>findMaterial(library,aliases.get(row.material)??row.material);
  const normalizedRole=row=>roles[row.role]??(['reactant','catalyst','solvent','addition','quench'].includes(row.role)?row.role:'addition');
  const resolveOf=value=>Array.isArray(value)?value.map(resolveOf):lookup.get(value)?.chargeId??value;
  const firstThermal=p.steps.findIndex(s=>['ramp','hold'].includes(s.type)),firstHold=p.steps.findIndex(s=>s.type==='hold');
  const workupIndex=p.steps.findIndex((s,i)=>s.legacyStage==='workup'||
    s.legacyStage===undefined&&workupStep(s,categoryFor(s,i)));
  const reactionRows=rows.filter(({step,row})=>{
    if(['quench','addition'].includes(normalizedRole(row)))return false;
    if(!step)return true;
    const index=p.steps.indexOf(step);
    if(index<0||step.legacyStage==='workup'||workupIndex>=0&&index>=workupIndex)return false;
    // A solution dropped in while heating up counts in the initial solids; one dropped in after
    // the first hold (e.g. AddH after 5 h) is a later addition with its own carrier.
    return firstThermal<0||index<=firstThermal||step.mode==='dropwise'&&(firstHold<0||index<firstHold);
  });
  const solids=reactionRows.filter(i=>['reactant','catalyst'].includes(normalizedRole(i.row))&&
    i.row.amount.rule!=='solids').map(i=>i.chargeId);
  return {out,p,library,rows,lookup,aliases,getMaterial,normalizedRole,resolveOf,reactionRows,solids};
}

function compileAmounts(context){
  const {p,rows,lookup,getMaterial,normalizedRole,resolveOf,reactionRows,solids}=context;
  const result=new Map();
  const keys=id=>chargeKeys({id,...lookup.get(id)?.row.legacyCharge});
  for(const {row,chargeId,fraction=1,branchId} of rows){
    const a=row.amount??{rule:'mass',value:0};
    const value=a.knob?`[${a.knob}]`:expr(a.value),of=resolveOf(a.of??p.scale.basisRow);
    const basis=p.scale.basisRow===chargeId||p.scale.basisRow===row.id;
    let amount={mode:{molRatio:'eq',molPct:'molpct',solids:'solidFill',volRatio:'expr',perBasis:'expr',perResin:'perProduct'}[a.rule]??a.rule,value};
    if(a.unit)amount.unit=a.unit;
    if(basis)amount={mode:p.scale.unit==='mmol'?'mmol':'mass',value:`[${p.scale.knob}]`};
    if(['molRatio','molPct'].includes(a.rule)){amount.of=of;
      // 1.1 eq of this route's TsOH: a route holds only its share of a material charged before the split.
      if(a.ofRoute&&fraction!==1)amount.value=`(${value})*(${fraction})`;}
    // A solution made up beforehand: one part is set from the other through wt%.
    if(a.rule==='soluteWt')amount={mode:'expr',unit:'g',value:`(${value})/(100-(${value}))*[${keys(of).mass}]`};
    if(a.rule==='solventWt')amount={mode:'expr',unit:'g',value:`(100/(${value})-1)*[${keys(of).mass}]`};
    const shareOfBase=a.ofRoute&&fraction!==1?`*(${fraction})`:'';
    if(a.rule==='perBasis')amount={mode:'expr',unit:a.unit??'g',value:`(${value})*[${keys(of).mass}]${shareOfBase}`};
    const routeProduct=typeof a.of==='string'&&/^\[?(每[支路]|整批)?预计产品(\(g\))?\]?$/.test(a.of)&&fraction!==1;
    if(a.rule==='perProduct')amount.of=a.of&&!routeProduct?expr(a.of):fraction===1?'[预计产品]':`[预计产品]*${fraction}`;
    if(a.rule==='perResin')amount.of=fraction!==1?`(${expr(a.of??'[理论净树脂]')})*${fraction}`:expr(a.of??'[理论净树脂]');
    // x 倍料液质量: x g per g of everything charged before this step (e.g. 5 g MeOH per g of
    // reaction liquid); at the bench the weighed liquid is used.
    if(a.rule==='perFeed'){const at=step=>step?p.steps.indexOf(step):-1,own=rows.find(i=>i.chargeId===chargeId)?.step;
      const routeOf=item=>item.row?.route??item.row?.legacyCharge?.branch??null,mine=routeOf({row}),earlier=rows.filter(i=>i.chargeId!==chargeId&&at(i.step)<at(own)&&(!mine||!routeOf(i)||routeOf(i)===mine));
      amount={mode:'expr',unit:'g',value:`(${value})*(${earlier.map(i=>`[${keys(i.chargeId).mass}]${mine&&!routeOf(i)&&fraction!==1?`*(${fraction})`:''}`).join('+')||'0'})`};}
    if(a.rule==='volRatio'){
      const ids=Array.isArray(of)?of:[of];
      amount={mode:'expr',unit:'mL',value:`(${value})*(${ids.map(id=>`[${keys(id).volume}]`).join('+')})${shareOfBase}`};
    }
    if(a.rule==='solids'){
      amount.of=a.of?resolveOf(a.of):solids;
      const total=amount.of.map(id=>`[${keys(id).mass}]`).join('+')||'0';
      const others=reactionRows.filter(i=>i.chargeId!==chargeId&&normalizedRole(i.row)==='solvent'&&
        i.row.amount.rule!=='solids').map(i=>i.chargeId);
      others.push(...reactionRows.filter(i=>i.row.solution?.solvent).map(i=>i.chargeId+'-carrier'));
      if(others.length){
        const solventMass=others.map(id=>`[${keys(id).mass}]`).join('+');
        amount.value=`100/(100/(${value})-(${solventMass})/(${total}))`;
      }
    }
    if(a.expr)amount={mode:'expr',value:a.expr,unit:a.unit??(['volume','volRatio','perProduct','perResin'].includes(a.rule)?'mL':'g')};
    if(['eq','molpct','mmol'].includes(amount.mode)&&!(row.legacyCharge?.mw??getMaterial(row)?.mw))amount.value='待指定';
    const charge={...row.legacyCharge,id:chargeId,material:row.material,role:normalizedRole(row),
      ...row.role?{roleLabel:row.role}:{},amount,...(row.route??row.branch??branchId)?{branch:row.route??row.branch??branchId}:{}};
    // Display bases from the old document cannot override the new driving rule.
    delete charge.displayBasis;delete charge.displayMode;
    if(a.rule==='solids')charge.displayMode='expr';
    if(row.label)charge.label=row.label;
    result.set(chargeId,charge);
    if(row.solution?.solvent){
      const solution=row.solution,id=chargeId+'-carrier',k=chargeKeys(charge);
      const carrierAmount=solution.wtPct===undefined&&solution.concentration!==undefined?
        {mode:'volume',value:`[${k.mmol}]/(${expr(solution.concentration)})`}:
        {mode:'mass',value:`[${k.mass}]*(100/(${expr(solution.wtPct??10)})-1)`};
      result.set(id,{id,material:solution.solvent,role:'solvent',amount:carrierAmount});
    }
  }
  return result;
}

function bindOriginalVariables(context,charges,define){
  const {p,rows,out}=context;
  for(const {row,chargeId} of rows){
    const key=row.legacyCharge?.sourceVar;
    if(!key||key===p.scale.knob)continue;
    // The row's amount is that variable itself (R100-33: TEA质量（不使用） = 0): binding it back would be a loop.
    if(row.amount?.knob===key||String(row.amount?.expr??row.amount?.value??'').includes(`[${key}]`))continue;
    const unit=row.originalAmount?.unit??(row.amount.unit==='mL'?'mL':'g');
    define(key,{expr:`[${chargeKeys(charges.get(chargeId))[unit==='mL'?'volume':'mass']}]`,unit});
  }
  // A binding derived from a deleted row's knob goes with that row.
  const known=new Set([...out.vars.map(v=>v.key),...Object.keys(p.bindings??{}),...[...charges.values()].flatMap(c=>Object.values(chargeKeys(c)))]);
  for(const [key,binding]of Object.entries(p.bindings??{})){
    const definition=typeof binding==='string'?{expr:binding}:binding;
    if(references(definition.expr).every(ref=>known.has(ref)))define(key,definition);
  }
}

function compileProductsAndFactors(context,charges,define,factorKey){
  const {out,p,rows,getMaterial,normalizedRole,reactionRows,solids}=context;
  const basis=rows.find(i=>i.chargeId===p.scale.basisRow||i.row.id===p.scale.basisRow);
  const keys=info=>chargeKeys(charges.get(info.chargeId));
  const materialRole=row=>{
    if(['单体','单体B','单体A'].includes(row.role))return row.role;
    const kind=getMaterial(row)?.kind??'';
    return /单体A|monoA/i.test(kind)?'单体A':/单体B|monoB/i.test(kind)?'单体':'';
  };
  const monomers=rows.filter(i=>normalizedRole(i.row)==='reactant'&&materialRole(i.row));
  const monoAs=monomers.filter(i=>materialRole(i.row)==='单体A');
  const sum=(items,key)=>items.map(i=>`[${keys(i)[key]}]`).join('+')||'0';
  // 理论树脂 is set per experiment (procedure.theory): which material lines count and
  // which small molecules leave, e.g. 水 18.015 per mol of each monoA, 甲醇, HCl,
  // or none for an addition polymer. Without a setting the reactant monomers count
  // and each mol of monoA gives 1 mol of water.
  const t=p.theory??{},byproducts=Array.isArray(t.byproducts)?t.byproducts:null;
  const included=Array.isArray(t.include)?rows.filter(i=>t.include.includes(i.chargeId)):monomers;
  const perRows=per=>per==='@monoAs'?monoAs:rows.filter(i=>i.chargeId===per);
  const lost=byproducts?byproducts.map(b=>`(${sum(perRows(b.per),'mmol')})*${Number(b.coef)||0}*${Number(b.mw)||0}/1000`).join('+')||'0':`(${sum(monoAs,'mmol')})*18.015/1000`;
  define('理论生成水',{role:'theoreticalWater',unit:'g',expr:lost,...byproducts?{label:byproducts.length?'理论脱去 '+byproducts.map(b=>b.name||'副产物').join('、'):'理论脱去（无）'}:{}});
  define('理论净树脂',{role:'theoreticalResin',unit:'g',expr:`(${sum(included,'mass')})-[理论生成水]`});
  const factor=out.vars.find(v=>v.key===factorKey);
  define('预计产品',{role:'expectedProduct',unit:'g',...(numeric(factor?.value)||factor?.expr!==undefined?
    {expr:`[理论净树脂]*[${factorKey}]`}:{value:'待指定'})});
  if(basis){
    const denominator=`[${keys(basis).mmol}]`;
    if(monoAs.length)define('工序单体比',{role:'monoARatio',unit:'mol/mol',expr:`(${sum(monoAs,'mmol')})/${denominator}`});
    const catalysts=reactionRows.filter(i=>normalizedRole(i.row)==='catalyst');
    if(catalysts.length){
      define('工序催化剂mol%',{role:'catalystMolPct',unit:'mol%',expr:`(${sum(catalysts,'mmol')})/${denominator}*100`});
      define('历史催化剂molpct',{unit:'w/w',expr:'[工序催化剂mol%]/100'});
    }
    if(reactionRows.length&&solids.length){
      const solidMass=reactionRows.filter(i=>solids.includes(i.chargeId));
      const carrierMass=reactionRows.filter(i=>i.row.solution?.solvent)
        .map(i=>`[${chargeKeys({id:i.chargeId+'-carrier'}).mass}]`);
      const total=[sum(reactionRows,'mass'),...carrierMass].join('+');
      define('初始名义固含',{unit:'w/w',expr:`(${sum(solidMass,'mass')})/(${total})`,technical:true});
      define('工序固含%',{role:'solidPct',unit:'%',expr:'[初始名义固含]*100'});
    }
  }else{
    const oldCatalyst=out.vars.find(v=>v.role==='catalystMolPct');
    if(oldCatalyst)define('历史催化剂molpct',{unit:'w/w',expr:`[${oldCatalyst.key}]${/%/.test(oldCatalyst.fmt??'')?'':'/100'}`});
    const oldSolids=out.vars.find(v=>v.role==='solidPct'||v.unit==='w/w'&&/固含/.test(v.key));
    if(oldSolids)define('初始名义固含',{unit:'w/w',expr:`[${oldSolids.key}]${oldSolids.unit==='w/w'||/%/.test(oldSolids.fmt??'')?'':'/100'}`,technical:true});
  }
  const monoA=out.vars.find(v=>v.key==='工序单体比')??out.vars.find(v=>['monoARatio','ratio'].includes(v.role));
  if(monoA&&monoA.key!=='MonB:MonA摩尔比'&&!p.knobs?.some(k=>k.key==='MonB:MonA摩尔比'))define('MonB:MonA摩尔比',{expr:`[${monoA.key}]`,unit:'mol/mol'});
  // Canonical factors precede old aliases; input knobs keep their own units.
  const primary=['工序单体比','工序催化剂mol%','工序固含%','历史催化剂molpct','初始名义固含','MonB:MonA摩尔比'];
  out.vars.sort((a,b)=>{const rank=v=>primary.includes(v.key)?primary.indexOf(v.key):-1;return (rank(a)<0?99:rank(a))-(rank(b)<0?99:rank(b))||a.key.localeCompare(b.key); });
}

function materialSentence(row,rowCharges,getMaterial,library){
  const charge=rowCharges(row)[0];
  const unit=row.amount.unit==='mL'||['volume','volRatio','perProduct','perResin'].includes(row.amount.rule)?'mL':'g';
  const key=chargeKeys(charge)[unit==='g'?'mass':'volume'];
  const material=sopMaterialName(getMaterial(row)??{id:row.material});
  let text=`${material} {[${key}]|${unit==='g'?'0.000':'0.0'}} ${unit}`;
  if(row.amount.rule==='solids')text+=`（目标固含 ${token(row.amount.knob?{knob:row.amount.knob}:row.amount.value)} %）`;
  if(['soluteWt','solventWt'].includes(row.amount.rule))text+=`（配成 ${token(row.amount.knob?{knob:row.amount.knob}:row.amount.value)} wt% 溶液）`;
  const solution=row.solution;
  if(solution?.solvent){
    const molar=solution.wtPct===undefined&&solution.concentration!==undefined;
    const solvent=sopMaterialName(findMaterial(library,solution.solvent)??{id:solution.solvent});
    text+=`（以 ${solvent} 配成 ${token(molar?solution.concentration:solution.wtPct??10)} ${molar?'mol/L':'wt%'} 溶液）`;
  }
  return text;
}

function sentenceForStep(s,hours,p,rowCharges,getMaterial,library){
  const materials=(s.rows??[]).map(row=>materialSentence(row,rowCharges,getMaterial,library)).join('、');
  const temp=s.tempC!==undefined?token(s.tempC):'',time=`{${hours}|0.0} h`;
  let duration='';
  if(s.duration){
    const d=s.duration;
    duration=token(d.min??d.hours??d.fixed??d.max??0);
    if(d.max!==undefined&&d.min!==undefined)duration+='–'+token(d.max);
    duration+=' '+(d.unit??'h');
  }
  let text;
  switch(s.type){
    case 'add':
      if(s.mode==='reverse')text=`将本批料液${duration?'于 '+duration+' 内':''}滴入 ${materials} 中。`;
      else if(s.mode==='dropwise')text=`${s.atTemp!==undefined?token(s.atTemp)+' °C 下':'室温'}将 ${materials} 于 ${duration} 内匀速滴入。`;
      else text=`${s.atTemp!==undefined?token(s.atTemp)+' °C 下':'室温'}${s.mode==='slow'?'缓慢加入':'依次加入'} ${materials}。`;
      break;
    case 'ramp':text=`升温至 ${temp} °C${s.duration?'，用时 '+time:''}。`;break;
    case 'hold':
      text=`${temp} ± 2 °C 保温 ${time}`;
      if(s.duration?.kind==='untilMw')text+='，监控分子量至 '+(s.duration.target??'目标');
      if(s.waterRemoval)text+='（Dean–Stark 分水）';
      if(s.atmosphere)text+='，'+s.atmosphere+' 保护';
      text+='。';break;
    case 'cool':text=`冷却至 ${temp} °C${s.duration?'，用时 '+time:''}。`;break;
    case 'sample':
      text=`按计划取样，检测 ${(s.tests??['GPC']).join('、')}${s.quench?'；即时终止液：'+s.quench:''}。`;break;
    case 'filter':
      text='过滤，分别收集 '+(s.outputs??['湿饼','滤液']).map(o=>typeof o==='string'?o:o.name).join('、');
      text+=(s.weigh?'，称重':'')+(s.solids?'，测固含':'')+(s.keepSample?'，留样':'')+'。';break;
    case 'slurry':
      text=`用 ${materials} 打浆${s.durationMin!==undefined?' '+token(s.durationMin)+' min':'，时间未记录'}`;
      text+=(s.times!==undefined?'，共 '+token(s.times,'0')+' 次':'')+'，随后过滤。';break;
    case 'wash':
      text=`用 ${materials} 洗涤${s.times!==undefined?'，共 '+token(s.times,'0')+' 次':''}，随后过滤。`;break;
    case 'dry':
      text=(s.tempC!==undefined?token(s.tempC)+' °C ':'')+(s.vacuum?'真空':'')+'干燥';
      text+=s.hours!==undefined?' '+token(s.hours)+' h':'，时长未记录';
      if(s.tempC===undefined)text+='，温度未记录';
      if(s.constantWeight?.extraH!==undefined)text+='，续干 '+token(s.constantWeight.extraH)+' h';
      if(s.constantWeight?.maxDeltaPct!==undefined)text+='，恒重差 ≤ '+token(s.constantWeight.maxDeltaPct)+'%';
      text+='。';break;
    case 'split':text='按设定比例分料，各路继续后处理。';break;
    default:text=s.text??s.note??'';
  }
  if(p.t0?.stepId===s.id){
    text+=({'reach-temp':'首次达温记为 t=0。','feed-start':'从开始滴加起计时 t=0。',
      'first-drop':'第一滴加入记为 t=0。','charge-done':'加料结束记为 t=0。'}[p.t0.event]??'');
  }
  if(s.note&&s.type!=='text')text+=' '+s.note;
  return text;
}

function compileSteps(out,p,initialStep,moveInitial,rowCharges,getMaterial,library){
  let clock='0';
  const stepEnds=new Map(),stepStarts=new Map(),eventHours=new Map(),nodes=clone(p.legacyNodes??[]);
  const durationHours=duration=>{
    if(duration==null)return '0';
    if(typeof duration==='number'||duration.knob)return expr(duration);
    if(duration.min!==undefined||duration.max!==undefined){
      return `(${expr(duration.max??duration.min)})${duration.unit==='min'?'/60':''}`;
    }
    return expr(duration.hours??duration.fixed??duration.estimatedHours??0);
  };
  // A work-up split into routes (A / B): the split step and every step after it are
  // done once per route (a step may belong to some routes only); the split starts the work-up.
  const routeIds=(p.routes?.length>1?p.routes:[]).map(r=>r.id),routeAt=routeIds.length?p.steps.findIndex(s=>s.id===p.routeFrom):-1;
  const build=(list,inheritedWorkup=false)=>{let localWorkup=inheritedWorkup;return list.map((s,index)=>{
    stepStarts.set(s.id,clock);
    const category=s===initialStep&&!p.converted?'投料':categoryFor(s,index);
    const routed=list===p.steps&&routeAt>=0&&index>=routeAt;
    localWorkup||=routed||s.legacyStage==='workup'||s.legacyStage===undefined&&workupStep(s,category);
    const stage=routed?'workup':s.legacyStage??(localWorkup?'workup':index===0&&s.type==='add'?'prep':'reaction');
    let hours=durationHours(s.duration);
    if(s.type==='dry')hours=expr(s.hours??s.legacyConditions?.hours??0);
    if(s.type==='slurry'||s.type==='wash')hours=s.durationMin!==undefined?`(${expr(s.durationMin)})/60*(${expr(s.times??1)})`:durationHours(s.duration??s.legacyConditions?.hours);
    if(s.duration?.kind==='relative')hours=`MAX(0,(${stepEnds.get(s.duration.stepId)??'0'})+(${expr(s.duration.hours)})-(${clock}))`;
    const conditions={...s.legacyConditions};
    if(['ramp','hold','cool'].includes(s.type)||s.duration||['dry','slurry','wash'].includes(s.type)){
      Object.assign(conditions,{
        kind:s.legacyConditions?.kind??(['ramp','cool'].includes(s.type)?s.type:'hold'),
        ...!p.converted||s.duration!==undefined||s.hours!==undefined||s.durationMin!==undefined||s.legacyConditions?.hours!==undefined?{hours}:{},
        ...s.tempC!==undefined?{tempC:expr(s.tempC)}:{},
        ...s.atTemp!==undefined?{tempC:expr(s.atTemp)}:{},
        ...s.from!==undefined?{from:expr(s.from)}:{}
      });
    }
    const charges=s===moveInitial?[]:(s.rows??[]).flatMap(rowCharges);
    const text=sentenceForStep(s,hours,p,rowCharges,getMaterial,library);
    const title=(s.title??names[s.type])+
      (s.waterRemoval&&!String(s.title??'').includes('分水')?'（分水）':'');
    const result={id:s.id,module:s.type==='split'?'split':'custom',category,title,stage,args:{},
      text:s.textOverride??text,sourceText:s.legacySourceText??s.textOverride??text,charges,conditions,
      controls:clone(s.legacyControls??[]),records:clone(s.legacyRecords??[]),notes:clone(s.legacyNotes??[]),
      ...s.vesselId?{vesselId:s.vesselId}:{},
      ...routed?{routes:index===routeAt?routeIds:routeIds.filter(id=>!Array.isArray(s.routes)||s.routes.includes(id))}:{}
    };
    for(const point of s.points??[]){
      const node={...point,id:point.id,stepId:s.id,tests:clone(s.tests??point.tests??['GPC']),
        ...point.at==='event'?{kind:'workup'}:{hoursExpr:expr(point.hours??point.hoursExpr??0)}};
      delete node.at;
      if(!nodes.some(n=>n.id===node.id)){
        nodes.push(node);
        if(point.at==='event')eventHours.set(node.id,clock);
      }
    }
    clock=`(${clock})+(${hours})`;
    stepEnds.set(s.id,clock);
    if(s.type==='dry'&&!p.converted&&!nodes.some(n=>n.id===(s.productNodeId??`${s.id}-FP`)))nodes.push({id:s.productNodeId??`${s.id}-FP`,stepId:s.id,kind:'product',stage:'成品',label:'最终产品 FP',tests:['GPC']});
    if(s.branches?.length){
      const total=s.branches.reduce((sum,b)=>sum+Number(b.fraction??b.massShare??0),0);
      const branchStart=clock,tails=[];
      result.branches=s.branches.map(b=>{
        clock=branchStart;
        const steps=build(b.steps,true);
        tails.push(clock);
        return {...b,fraction:total>0?Number(b.fraction??b.massShare??0)/total:1/s.branches.length,steps};
      });
      clock=`MAX(${tails.join(',')})`;
    }
    return result;
  });};
  out.steps=build(p.steps);
  const t0Clock=(['reach-temp','charge-done'].includes(p.t0?.event)?stepEnds:stepStarts).get(p.t0?.stepId)??'0';
  for(const [id,hours]of eventHours)nodes.find(n=>n.id===id).hoursExpr=`(${hours})-(${t0Clock})`;
  out.samplingPlan={...out.samplingPlan,points:[],nodes,identityVersion:1};
  out.scaleBasis=p.scale.unit==='mmol'?'monoB-mmol':'monoB-g';
  out.timeBasis=p.t0?.event?(['feed-start','first-drop'].includes(p.t0.event)?'total-from-feed-start':'at-temperature'):out.timeBasis??null;
}

/** Compile only the generated area. Identity, records and executions stay intact. */
// Old workbooks wrote solvent volumes as a formula, e.g. [理论净树脂]*[MeOH沉降理论净树脂]
// (mL per g of theoretical resin). That is the plain rule "30 mL/g 理论树脂":
// the number shows and is edited directly instead of "公式联动".
function plainAmounts(p,vars){
  // Old workbooks also call the expected product 预计分离产品.
  const role=key=>{const v=vars.find(x=>x.key===key);return v?.role??(/^预计(?:分离)?产品$/.test(v?.label??key)?'expectedProduct':undefined);},perGram=key=>/^mL\/g$/.test(vars.find(v=>v.key===key)?.unit??'');
  for(const {row} of procedureRows(p)){
    const a=row.amount,parts=/^\[([^\]]+)\]\s*\*\s*\[([^\]]+)\]$/.exec(String(a?.expr??'').trim());
    if(!parts||a.knob)continue;
    for(const [base,knob] of [[parts[1],parts[2]],[parts[2],parts[1]]]){
      const rule=role(base)==='theoreticalResin'?'perResin':role(base)==='expectedProduct'?'perProduct':null;
      if(rule&&perGram(knob)){row.amount={rule,knob,of:`[${base}]`};break;}
    }
  }
}
export function compileProcedure(design,library={}){
  const out=clone(design),p=out.procedure;
  if(!p||p.schema!=='lab-procedure/3')throw Error('需要 lab-procedure/3 工序');
  plainAmounts(p,out.vars??[]);
  out.authoring='procedure';
  const used=usedProcedureKeys(p,out.vars??[]);
  p.knobs=(p.knobs??[]).filter(k=>used.has(k.key));
  const {define,factorKey}=variablesForProcedure(out);
  const context=amountContext(out,library);
  const {rows,getMaterial,normalizedRole,aliases}=context;
  const compiledCharges=compileAmounts(context);
  const rowCharges=row=>{
    const id=row.chargeId??rows.find(i=>i.row===row)?.chargeId;
    return [compiledCharges.get(id),compiledCharges.get(id+'-carrier')].filter(Boolean);
  };
  const initialStep=p.steps.find((s,index)=>s.type==='add'&&(s.mode??'once')==='once'&&
    !workupStep(s,categoryFor(s,index))&&!p.steps.slice(0,index).some((step,i)=>
      ['ramp','hold'].includes(step.type)||workupStep(step,categoryFor(step,i))));
  const moveInitial=!p.converted?initialStep:null;
  out.charges=[...(p.initialRows??[]),...(moveInitial?.rows??[])].flatMap(rowCharges);
  out.materials=[...new Map([...(out.materials??[]),...[...compiledCharges.values()].map(c=>
    ({as:c.material,ref:aliases.get(c.material)??getMaterial({material:c.material})?.id??c.material}))].map(m=>[m.as,m])).values()];
  bindOriginalVariables(context,compiledCharges,define);
  compileProductsAndFactors(context,compiledCharges,define,factorKey);
  compileSteps(out,p,initialStep,moveInitial,rowCharges,getMaterial,library);
  compile(out,library);
  return out;
}

const numeric=value=>typeof value==='number'&&Number.isFinite(value);

function references(value){
  return [...String(value??'').matchAll(/\[([^\]]+)\]/g)].map(match=>match[1]);
}

function usedProcedureKeys(procedure,vars){
  const keys=new Set(),definitions=new Map(vars.map(v=>[v.key,v]));
  const add=key=>{if(keys.has(key))return;keys.add(key);for(const ref of references(definitions.get(key)?.expr))add(ref);};
  const scan=value=>{
    if(Array.isArray(value)){value.forEach(scan);return;}
    if(value&&typeof value==='object'){
      if(value.knob)add(value.knob);
      for(const [key,item]of Object.entries(value))if(!key.startsWith('legacy')&&
        !['text','note','originalAmount','knobs','bindings'].includes(key))scan(item);
    }else if(typeof value==='string')references(value).forEach(add);
  };
  scan(procedure.steps);scan(procedure.initialRows);
  if(procedure.scale.basisRow)add(procedure.scale.knob);
  if(procedureRows(procedure).some(i=>i.row.amount.rule==='perProduct'))add(procedure.expectedFactor.knob);
  return keys;
}

/** Derivation calls this on a copy. Recorded quantities remain visible on each row. */
export function procedureFromDesign(design,library={}){
  if(design.procedure)return clone(design.procedure);
  const compiled=compile(design,library),values=variableValues(compiled.vars);
  const resolved=resolveSteps(design,library),byId=new Map(resolved.map(s=>[s.id,s]));
  const definitions=new Map(design.vars.map(v=>[v.key,v])),knobs=[],bindings={};
  const aliases=new Map((design.materials??[]).map(m=>[m.as,m.ref]));
  const material=c=>findMaterial(library,aliases.get(c.material)??c.material);
  const classification=c=>{
    const kind=material(c)?.kind??'';
    if(c.role==='catalyst')return '催化剂';
    if(c.role==='quench'||/base|终止|中和|碱/i.test(kind))return '终止剂';
    if(c.role==='solvent'||/solvent|溶剂/i.test(kind))return '溶剂';
    if(/monoA|单体A/i.test(kind))return '单体A';
    if(/monoB|单体B/i.test(kind)||c.role==='reactant')return '单体';
    return c.role==='addition'?'添加剂':c.role;
  };
  const charges=new Map(compiled.modular.charges.map(c=>[c.id,c]));
  const quantity=(c,kind)=>values.get(c.keys[kind]);
  const addKnob=(key,label,unit,value,role,exposed=true)=>{
    const existing=definitions.get(key);
    if(existing&&(existing.expr!==undefined||existing.value!==value||existing.unit!==unit))key+='（工序）';
    if(!knobs.some(k=>k.key===key))knobs.push({key,label,unit,default:value,...role?{role}:{},exposed});
    return key;
  };
  const rowsFor=list=>(list??[]).map(raw=>{
    const c=charges.get(raw.id)??raw;
    const unit=c.amount?.unit==='mL'||c.amount?.mode==='volume'?'mL':'g';
    const value=quantity(c,unit==='mL'?'volume':'mass');
    return {id:c.id,chargeId:c.id,material:c.material,role:classification(c),
      amount:{rule:unit==='mL'?'volume':'mass',value:numeric(value)?value:''},
      originalAmount:{value,unit},legacyCharge:clone(c)};
  });
  const field=(value,step,role)=>{
    if(typeof value!=='string')return value;
    const expression=value.replace(/\{\{([^}]+)\}\}/g,(_,key)=>`(${argumentExpr(step.args?.[key])})`);
    const match=/^\[([^\]]+)\]$/.exec(expression),variable=match&&definitions.get(match[1]);
    if(variable&&numeric(variable.value)){
      const key=addKnob(variable.key,variable.label??variable.key,variable.unit??'',variable.value,role??variable.role);
      return {knob:key};
    }
    return expression;
  };
  const convert=list=>(list??[]).map(raw=>{
    const step=byId.get(raw.id)??raw,category=step.category??moduleCategory(step.title??'');
    const type=raw.branches?.length?'split':
      ({升温:'ramp',反应:'hold',降温:'cool',取样:'sample',过滤:'filter',打浆:'slurry',洗涤:'wash',干燥:'dry',恒重:'dry'})[category]??
      (step.charges?.length?'add':'text');
    const temperatureRole=step.stage!=='workup'&&['升温','反应'].includes(category)?'tempC':undefined;
    const conditions=Object.fromEntries(Object.entries(step.conditions??{}).map(([key,value])=>[key,
      key==='kind'?value:expr(field(value,step,key==='tempC'?temperatureRole:undefined))]));
    return {id:raw.id,type,title:step.title,category,legacyStage:step.stage,textOverride:step.text,textOverrideOrigin:'archive',
      legacySourceText:step.sourceText??step.text,rows:rowsFor(step.charges),
      ...step.conditions?.tempC!==undefined?{tempC:field(step.conditions.tempC,step,temperatureRole)}:{},
      ...step.conditions?.hours!==undefined?{duration:{kind:'fixed',hours:field(step.conditions.hours,step,category==='反应'?'holdHours':undefined)}}:{},
      ...type==='dry'&&step.conditions?.hours!==undefined?{hours:field(step.conditions.hours,step)}:{},
      legacyConditions:conditions,legacyControls:clone(step.controls??[]),legacyRecords:clone(step.records??[]),
      legacyNotes:clone(step.notes??[]),...['slurry','wash'].includes(type)&&step.conditions?.hours!==undefined?
        {durationMin:resolveCondition(step.conditions.hours,step.args,compiled.vars)*60}:{},
      ...step.vesselId?{vesselId:step.vesselId}:{},
      ...raw.branches?{branches:raw.branches.map(b=>({...b,steps:convert(b.steps)}))}:{}};
  });
  const initialRows=rowsFor(design.charges),steps=convert(design.steps);
  const rows=[...initialRows,...flattenSteps(steps).flatMap(s=>s.rows??[])];
  const basis=rows.find(row=>row.role==='单体'&&numeric(quantity(charges.get(row.chargeId),'mass')));
  const oldScale=design.vars.find(v=>v.role==='scale'),scaleValue=basis?quantity(charges.get(basis.chargeId),'mass'):oldScale?.value??'';
  const scaleUnit=basis?'g':oldScale?.unit==='mmol'?'mmol':'g';
  const scaleKey=addKnob('投料尺度','投料尺度',scaleUnit,scaleValue,'scale',!!basis);
  const expected=design.vars.find(v=>v.role==='yieldFraction');
  const factorKey=expected?.key??'预计分离系数';
  addKnob(factorKey,'预计分离系数',expected?.unit??'w/w',expected?values.get(expected.key):'','yieldFraction',false);
  const p={schema:'lab-procedure/3',converted:true,scale:{knob:scaleKey,basisRow:basis?.chargeId??'',unit:scaleUnit},
    expectedFactor:{knob:knobs.find(k=>k.label==='预计分离系数').key,...!expected?{note:'原档未记录预计分离系数'}:{}},
    theory:{rule:'condensation-water'},knobs,steps,initialRows,bindings,blocksUsed:[],
    legacyNodes:samplingNodes(design,resolved).map(n=>({...clone(n),...n.hoursExpr===undefined&&numeric(n.hours)?{hoursExpr:String(n.hours)}:{}}))};
  const dependencyKeys=expression=>{
    const result=new Set();
    const visit=key=>{if(result.has(key))return;result.add(key);references(definitions.get(key)?.expr).forEach(visit);};
    references(expression).forEach(visit);return result;
  };
  const sourceExpression=c=>{
    const value=c.amount?.value,match=/^\[([^\]]+)\]$/.exec(String(value));
    return match&&definitions.get(match[1])?.expr!==undefined?definitions.get(match[1]).expr:value;
  };
  const bindInput=(keys,pattern,knob,recorded)=>{
    const old=[...keys].map(key=>definitions.get(key)).find(v=>v&&numeric(v.value)&&pattern.test(v.key));
    if(old&&old.key!==knob&&recorded!==0)bindings[old.key]={expr:`[${knob}]*${old.value/recorded}`,unit:old.unit??'',label:old.label??old.key,...old.fmt?{fmt:old.fmt}:{}};
  };
  const catalyst=rows.find(row=>row.role==='催化剂');
  if(basis){
    const base=charges.get(basis.chargeId),baseMmol=quantity(base,'mmol');
    basis.amount={rule:'mass',knob:scaleKey};
    for(const row of rows){
      if(row===basis)continue;
      const c=charges.get(row.chargeId),mmol=quantity(c,'mmol'),deps=dependencyKeys(c.amount?.value);
      const molRule=target=>numeric(mmol)&&numeric(quantity(target,'mmol'))&&quantity(target,'mmol')>0;
      if(['单体','单体A','催化剂'].includes(row.role)&&numeric(baseMmol)&&baseMmol>0&&numeric(mmol)){
        const percent=row.role==='催化剂',value=mmol/baseMmol*(percent?100:1);
        const key=addKnob(percent?'催化剂用量 mol%':material(c).id+'/基准摩尔比',
          percent?'催化剂用量':material(c).id+' / 基准摩尔比',percent?'mol%':'mol/mol',value);
        row.amount={rule:percent?'molPct':'molRatio',knob:key,of:basis.chargeId};
        bindInput(deps,percent?/用量.*mol%/:/摩尔比|单体比/,key,value);
      }else if(row.role==='终止剂'&&catalyst&&molRule(charges.get(catalyst.chargeId))&&
        (deps.has(charges.get(catalyst.chargeId).sourceVar)||[...deps].some(key=>/TEA.*TsOH|DMAP.*TsOH/.test(key))||
          c.amount?.mode==='eq'&&[catalyst.chargeId,charges.get(catalyst.chargeId).as,catalyst.material].includes(c.amount.of))){
        const value=mmol/quantity(charges.get(catalyst.chargeId),'mmol');
        const key=addKnob(row.chargeId+'当量',material(c).id+' / 催化剂当量','eq',value);
        row.amount={rule:'molRatio',knob:key,of:catalyst.chargeId};
        bindInput(deps,/TEA.*TsOH|DMAP.*TsOH/,key,value);
      }else if(c.amount?.mode==='expr'){
        const volume=row.originalAmount.unit==='mL',recorded=row.originalAmount.value;
        const sourceRows=rows.filter(other=>other!==row&&other.legacyCharge?.sourceVar&&
          other.legacyCharge.sourceVar!==c.sourceVar&&
          deps.has(other.legacyCharge.sourceVar)&&other.originalAmount.unit==='mL');
        const volumeBase=sourceRows.find(other=>other.legacyCharge.branch===c.branch)??sourceRows[0];
        const product=[...deps].map(key=>definitions.get(key)).find(v=>v&&
          ['expectedProduct','branchExpectedProduct'].includes(v.role)&&numeric(values.get(v.key))&&values.get(v.key)>0);
        if(volume&&volumeBase&&numeric(recorded)&&volumeBase.originalAmount.value>0){
          const value=recorded/volumeBase.originalAmount.value;
          const old=[...deps].map(key=>definitions.get(key)).find(v=>v&&v.unit==='v/v'&&numeric(v.value));
          const key=addKnob(old?.key??row.chargeId+'体积比',material(c).id+' / '+volumeBase.material+' 体积比','v/v',value);
          row.amount={rule:'volRatio',knob:key,of:volumeBase.chargeId};
          if(old&&old.key!==key)bindings[old.key]={expr:`[${key}]*${old.value/value}`,unit:old.unit,label:old.label??old.key};
        }else if(volume&&product&&numeric(recorded)){
          const value=recorded/values.get(product.key);
          const old=[...deps].map(key=>definitions.get(key)).find(v=>v&&v.unit==='mL/g'&&numeric(v.value));
          const key=addKnob(old?.key??row.chargeId+'每克产品',material(c).id+' / 预计产品','mL/g',value);
          row.amount={rule:'perProduct',knob:key,of:`[${product.key}]`};
          if(old&&old.key!==key)bindings[old.key]={expr:`[${key}]*${old.value/value}`,unit:old.unit,label:old.label??old.key};
        }else if(row.role==='溶剂'&&catalyst&&numeric(recorded)&&
          deps.has(charges.get(catalyst.chargeId).sourceVar)&&quantity(charges.get(catalyst.chargeId),'mass')>0){
          const value=recorded/quantity(charges.get(catalyst.chargeId),'mass');
          const key=addKnob(row.chargeId+'载体质量比',material(c).id+' / 催化剂质量比','g/g',value);
          row.amount={rule:'perBasis',knob:key,of:catalyst.chargeId,unit:'g'};
          const concentration=[...deps].map(key=>definitions.get(key)).find(v=>v&&/浓度/.test(v.key)&&numeric(v.value));
          if(concentration)bindings[concentration.key]={expr:`1/(1+[${key}])`,unit:concentration.unit,label:concentration.label??concentration.key};
        }else row.amount={rule:volume?'volume':'mass',expr:sourceExpression(c),unit:row.originalAmount.unit};
      }
    }
    const context=amountContext({...design,procedure:p},library);
    const reaction=context.reactionRows,solids=reaction.filter(i=>['单体','单体A','催化剂'].includes(i.row.role));
    const solvent=reaction.filter(i=>i.row.role==='溶剂').sort((a,b)=>quantity(charges.get(b.chargeId),'mass')-quantity(charges.get(a.chargeId),'mass'))[0];
    const solidMass=solids.reduce((sum,i)=>sum+quantity(charges.get(i.chargeId),'mass'),0);
    const total=reaction.reduce((sum,i)=>sum+quantity(charges.get(i.chargeId),'mass'),0);
    if(solvent&&numeric(solidMass)&&numeric(total)&&total>solidMass&&solidMass>0){
      const percent=solidMass/total*100,key=addKnob('目标固含 %','目标固含','%',percent);
      const c=charges.get(solvent.chargeId),deps=dependencyKeys(c.amount?.value);
      solvent.row.amount={rule:'solids',knob:key,of:solids.map(i=>i.chargeId)};
      bindInput(deps,/固含/,key,percent);
    }
  }
  // Keep real input dependencies, excluding molecular weights and Excel cells.
  const sourceKeys=new Set([...charges.values()].map(c=>c.sourceVar).filter(Boolean));
  for(const row of rows)for(const key of dependencyKeys(row.amount.expr)){
    const variable=definitions.get(key);
    if(variable&&numeric(variable.value)&&!variable.technical&&!sourceKeys.has(key)&&!/^批次/.test(key)&&! /MW|分子量|密度|^来源参数|^cell_/.test(key)&&!bindings[key]){
      addKnob(key,variable.label??key,variable.unit??'',variable.value,variable.role);
    }
  }
  const t0Text=resolved.map(s=>s.text+' '+s.sourceText).join(' ');
  const feed=design.timeBasis==='total-from-feed-start'||/滴加.*(?:计时|t\s*=\s*0)|开始滴加/.test(t0Text);
  const reach=design.timeBasis==='at-temperature'||/达.*温.*(?:计时|t\s*=\s*0)/.test(t0Text);
  p.t0={event:feed?'feed-start':reach?'reach-temp':undefined,
    stepId:(feed?steps.find(s=>/滴加/.test(s.title)):steps.find(s=>['ramp','hold'].includes(s.type)))?.id};
  const used=usedProcedureKeys(p,[...design.vars,...knobs.map(k=>({key:k.key,value:k.default}))]);
  p.knobs=knobs.filter(k=>used.has(k.key)||k.key===scaleKey||k.key===p.expectedFactor.knob);
  return p;
}
