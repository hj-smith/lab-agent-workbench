import {normalizeName} from './materials.js';
import {parseExpr,parseTemplate,serializeExpr,mapRefs,escapeTemplate,quoteString} from './expr.js';
import {paramBinding} from './param-bindings.js';
export const moduleList=library=>Array.isArray(library)?library:library?.modules??[];
export const findModule=(library,id)=>moduleList(library).find(m=>[m.id,m.name,...m.aliases??[]].some(k=>normalizeName(k)===normalizeName(id)));
// Only the operation's own controls belong in its inspector/factor picker.
// Transitive formula inputs remain available in global parameters and explanations.
export function meaningfulParams(module){
 if(!module)return [];if(module.id==='add-and-hold')return module.params??[];
 return (module.params??[]).filter(p=>!/MW|分子量|密度|纯度/.test(p.key)&&(
  String(module.text).includes('{{'+p.key)||String(module.text).includes('['+p.key+']')||
  JSON.stringify(module.controls??[]).includes('{{'+p.key)||JSON.stringify(module.conditions??{}).includes('{{'+p.key)||
  module.category==='沉降'&&/沉降.*THF|HEP\/THF/.test(p.label??p.key)));
}
export function stepParams(design,module,step){
 const params=[...new Map([...meaningfulParams(module),...step.amountParams??[],...step.durationParams??[]].map(p=>[p.key,p])).values()];if(step.text===undefined)return params;
 const source=[step.text,...step.controls??[]].join('\n'),refs=new Set(),visit=key=>{if(refs.has(key))return;refs.add(key);const v=design.vars.find(v=>v.key===key);for(const m of String(v?.expr??'').matchAll(/\[([^\]]+)\]/g))visit(m[1]);};for(const m of source.matchAll(/\[([^\]]+)\]/g))visit(m[1]);
 return params.filter(p=>source.includes('{{'+p.key)||JSON.stringify(step.conditions??{}).includes('{{'+p.key)||refs.has(paramBinding(design,p,step)?.key));
}
export function declaredModuleCharges(module){
 if(module.charges)return module.charges;
 if(module.stage!=='workup'||!['沉降','稀释','打浆'].includes(module.category))return [];
 const text=module.text??'',material=/正庚烷/.test(text)?'heptane':/MeOH|甲醇/.test(text)?'MeOH':/THF/.test(text)?'THF':/IPA|异丙醇/.test(text)?'IPA':null;
 const volume=(module.params??[]).find(p=>p.unit==='mL'&&text.includes('{{'+p.key));
 return material&&volume?[{id:'solvent',material,role:'solvent',amount:{mode:'volume',value:'{{'+volume.key+'}}'}}]:[];
}
export function normalizeModuleTitle(title, text='') {
  let name=String(title).normalize('NFKC')
    .replace(/CP\d+-\d+|[AB](?:路|支)|两路|各路|整批|批号\S*|第[一二三四五六七八九十]+次/g,'')
    .replace(/\d+(?:\.\d+)?\s*(?:°C|℃|mol%|wt%|mL|mmol|min|rpm|mm|mg|g|h|%|倍)?/gi,'')
    .replace(/浆洗/g,'打浆').replace(/配催化剂液/,'配制催化剂液')
    .replace(/过滤\s*[/／]\s*(?:庚烷)?冲洗/g,'过滤')
    .replace(/PRE称量与(?:取样|留样)/g,'PRE取样')
    .replace(/室温直接投料/,'室温一锅投料')
    .replace(/(?:分析样|样品|GPC取样)即时?终止|GPC取样终止/,'GPC样即时终止')
    .replace(/恒重(?:及|与)最终留样/,'恒重留样')
    .replace(/[\s/、:：()（）±–—-]/g,'');
  const synonyms=[
    [/^釜底装料与置换$/,'装料与置换'],[/^氮气保护并立即升温$/,'氮气保护与升温'],
    [/^(?:准备THF与MeOH|准备MeOH)$/,'溶剂准备'],[/^夜间恒温反应$/,'恒温反应'],
    [/^溶解与反应观察$/,'反应观察'],[/^终点取样与停止加热$/,'终点取样'],
    [/^(?:MeOH打浆过滤|打浆过滤)$/,'MeOH打浆'],[/^真空干燥并称M$/,'真空干燥'],
    [/^摊料与真空干燥$/,'真空干燥'],[/^续干称M并恒重$/,'续干恒重'],
    [/^THF直接稀释$/,'THF稀释'],[/^终止$/,'趁热终止'],
    [/^降温并转入后处理$/,'降温'],[/^均匀分料称量$/,'分料称量']
  ];
  for(const [pattern,replacement] of synonyms)name=name.replace(pattern,replacement);
  if(!/[\p{Script=Han}]/u.test(name)) name=String(text).replace(/\{[^}]*\}|\d+(?:\.\d+)?/g,'').match(/[\p{Script=Han}]{2,12}/u)?.[0]??'自定义操作';
  return name;
}
export function templateSkeleton(text){return String(text).replace(/\{\{[^}]+\}\}|(?<!\\)\{[^}]+\}/g,'#').replace(/CP\d+-\d+/gi,'#').replace(/\d+(?:\.\d+)?/g,'#').replace(/[AB](?:路|支)|[AB]-/g,'支').replace(/\s+/g,'');}
const categories=[['恒重',/恒重/],['干燥',/干燥|摊料/],['打浆',/打浆|浆洗/],['过滤',/过滤|终滤/],['沉降',/沉降/],['终止',/即时终止|取样终止|样.*终止|终止液|趁热终止/],['装置',/装置|玻璃仪器/],['准备',/准备|预配|预溶|配制/],['投料',/投料|装料/],['升温',/升温|达.*内温/],['反应',/恒温|保温|反应/],['取样',/取样|留样/],['降温',/降温|冷却|降至/],['中和',/中和/],['稀释',/稀释/],['洗涤',/洗涤|冲洗/],['加料',/加料|滴加|加AddH|分料/],['记录',/记录|观察|归档/]];
export const moduleCategory=title=>categories.find(([,re])=>re.test(title))?.[0]??'其他';
export function moduleDefaults(module,args={}){return Object.fromEntries((module.params??[]).map(p=>[p.key,args[p.key]??p.default]));}
export function argumentExpr(value,type){
  if(typeof value==='number')return String(value);
  if(typeof value==='boolean')return value?'TRUE':'FALSE';
  if(type==='text'||type==='material')return quoteString(value??'');
  const text=String(value??'');try{parseExpr(text);return text.replace(/^=/,'');}catch{return quoteString(text);}
}
// Result is a v1 template, not rendered text. Expressions remain live Excel formulas.
export function instantiateModule(module,args={}){
  const defaults=moduleDefaults(module,args),params=new Map((module.params??[]).map(p=>[p.key,p]));
  const expand=(key,active=new Set())=>{
    if(active.has(key))throw Error(`模块参数循环引用：${key}`);
    const next=new Set(active).add(key),p=params.get(key);
    return mapRefs(parseExpr(argumentExpr(defaults[key],p?.type)),r=>{
      if(r.kind!=='var'||!params.has(r.key)||r.key===key&&Object.hasOwn(args,key))return r;
      return expand(r.key,next);
    });
  };
  let source=module.text??'';
  const placeholders=[];
  source=source.replace(/\{\{[^{}]+\}\}/g,token=>{placeholders.push(token);return `MODULETOKEN${placeholders.length-1}END`;});
  source=parseTemplate(source).map(part=>part.text!==undefined?escapeTemplate(part.text):`{${serializeExpr(mapRefs(part.expr,r=>r.kind==='var'&&params.has(r.key)?expand(r.key):r))}${part.fmt?'|'+part.fmt:''}}`).join('');
  source=source.replace(/MODULETOKEN(\d+)END/g,(_,i)=>placeholders[+i]);
  // Existing v1 template pieces can refer to parameters too.
  source=source.replace(/\{\{([^{}|]+)(?:\|([^{}]+))?\}\}/g,(_,key,fmt)=>{
    if(!(key in defaults))throw Error(`缺少模块参数：${key}`);
    const p=params.get(key),value=defaults[key];
    return p?.type==='text'||p?.type==='material'?escapeTemplate(value):`{${serializeExpr(expand(key))}${fmt?'|'+fmt:''}}`;
  });
  // Substituted pieces are already fully expanded; process original single-brace pieces first.
  return source;
}
export const builtinModules=[
  {id:'split',name:'分料',category:'加料',stage:'workup',params:[],text:'保持搅拌，按分支计划分料。',controls:[],records:[{key:'各支净质量',type:'number'},{key:'转移残留',type:'number'}],notes:[]},
  {id:'custom',name:'自定义步骤',category:'其他',stage:'reaction',params:[{key:'text',type:'text',default:'填写操作内容'}],text:'{{text}}'},
  {id:'add-and-hold',name:'补加试剂/继续反应',category:'加料',stage:'reaction',params:[{key:'material',label:'补加物料',type:'material',default:'待指定'},{key:'of',label:'当量基准',type:'material',default:'MonA'},{key:'eq',label:'当量',unit:'eq',default:'待指定'},{key:'mass',label:'计划质量',unit:'g',default:0},{key:'tempC',label:'温度',unit:'°C',default:80},{key:'hours',label:'时长',unit:'h',default:1}],text:'加入{{material}} {{mass|0.000}}g，降至{{tempC|0}}°C，累计{{hours|0.0}}h。',controls:['从加入起计时，含降温时间'],records:[{key:'加入开始',type:'time'},{key:'到温时刻',type:'time'},{key:'结束',type:'time'},{key:'内温',type:'number'},{key:'外温',type:'number'}],notes:[],conditions:{kind:'ramp',tempC:'{{tempC}}',hours:'{{hours}}'},charges:[{id:'addition',material:'{{material}}',role:'addition',amount:{mode:'eq',value:'{{eq}}',of:'{{of}}'}}],record:{kind:'event',columns:['开始','结束','内温','外温','现象']}}
];
