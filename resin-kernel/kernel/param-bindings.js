import {roleVar} from './var-roles.js';
import {cleanKey} from './parse-workbook.js';
const normalizedLabels=new Map();
const norm=s=>{const text=String(s??'');if(normalizedLabels.has(text))return normalizedLabels.get(text);const normalized=text.normalize('NFKC').replace(/\((?:mL|g|h)\)/g,'').replace(/[\s/()·]/g,'').replace(/每支|每路|整批|总/g,'');if(normalizedLabels.size>=4096)normalizedLabels.delete(normalizedLabels.keys().next().value);normalizedLabels.set(text,normalized);return normalized;};
const compatible=(p,v)=>v&&(!['g','mL','mmol'].includes(p.unit)||p.unit===v.unit)?v:undefined;
export const displayParamTemplate=text=>String(text??'').replace(/\{\[(AddH初始MonA)\](?:\|[^}]+)?\}/g,'{[$1]|0.00} eq');
export const paramLabel=p=>{if(/沉降.*(?:MeOH.*THF|甲醇.*四氢呋喃)/.test(p.key+' '+p.label))return '沉降比（甲醇/四氢呋喃）';const label=p.label?.replace(/\s*\/\s*(数值|[A-Z])$/,'')??({material:'补加物料',of:'当量基准',eq:'当量',mass:'计划质量',tempC:'温度',hours:'时长',text:'操作正文'})[p.key]??p.key;return /^cell_\d+$/.test(label)?'待指定参数':label;};
export function paramBinding(design,p,step={}){
  if(!design.paramBindingVersion&&(design.executionUid||design.frozenRevisions?.some(r=>r.uid===design.revisionUid)))step={};
  if(step.paramBindings?.[p.key])return compatible(p,design.vars.find(v=>v.key===step.paramBindings[p.key]));
  const ordinal=/打浆\s*([12])/.exec(step.title??'')?.[1],instanceKey=ordinal?p.key.replace(/打浆[12]/g,'打浆'+ordinal):p.key;
  const label=paramLabel(p),key=norm(ordinal?label.replace(/打浆[12]/g,'打浆'+ordinal):label),exact=design.vars.find(v=>v.key===instanceKey);
  if(exact)return compatible(p,exact);
  const role=/^(?:THF体积|THF稀释体积|THF)$/.test(key)?'thfVolume':/^(?:预计产品)(?:g)?$/.test(key)?'expectedProduct':/MonB摩尔数|MonB物质的量/.test(key)?'monoBMmol':/^(?:单体摩尔比|单体比|MonB:MonA摩尔比)$/.test(key)?'monoARatio':/沉降MeOHTHF|MeOH沉降THF/.test(key)?'precipitationRatio':/THF预计产品/.test(key)?'thfPerProduct':/理论.*净树脂|理论树脂/.test(key)?'theoreticalResin':/理论生成水/.test(key)?'theoreticalWater':/预计分离收率/.test(key)?'yieldFraction':null;
  const global=role?roleVar(design.vars,role):design.vars.find(v=>norm(v.label??v.key)===key);if(global)return compatible(p,global);
  const charges=[...design.charges??[],...(design.steps??[]).flatMap(s=>s.charges??[])],charge=charges.find(c=>role==='thfVolume'?c.material==='THF':role==='monoBMmol'?c.material==='MonB':[c.material,c.as].filter(Boolean).some(name=>key===norm(name+'质量')||key===norm(name+'MW')||key===norm(name+'分子量')));
  if(charge){const suffix=role==='thfVolume'?'体积':role==='monoBMmol'?'_mmol':/MW|分子量/.test(key)?'_MW':'质量';return compatible(p,{key:cleanKey(charge.as??charge.id)+suffix,label:paramLabel(p),unit:suffix==='体积'?'mL':suffix==='_mmol'?'mmol':suffix==='_MW'?'g/mol':'g'});}
}
export function effectiveArgs(design,module,step={args:{}}){
  return Object.fromEntries((module.params??[]).map(p=>{const bound=paramBinding(design,p,step),automatic=step.bindingMode==='design'&&!(step.paramOverrides??[]).includes(p.key);return [p.key,bound&&(automatic||step.args?.[p.key]==='待指定')?`[${bound.key}]`:Object.hasOwn(step.args??{},p.key)?step.args[p.key]:bound?`[${bound.key}]`:p.default];}));
}
export function bindInsertedArgs(design,module,args){
  const out={},unbound=[],paramBindings={};
  for(const p of module.params??[]){
    if(Object.hasOwn(args,p.key)){out[p.key]=args[p.key];continue;}
    const bound=paramBinding(design,p);
    if(bound){out[p.key]=`[${bound.key}]`;paramBindings[p.key]=bound.key;continue;}
    // Recipe ratios/conditions belong to the module. Source batch amounts don't.
    const amount=['g','mg','mL','L','mmol','mol'].includes(p.unit)||/质量|摩尔数|预计产品|体积|用量/.test(paramLabel(p))&&!/比|分子量|MW|密度/.test(paramLabel(p));
    const pair=typeof p.default==='string'&&/^\[([^\]]+)\]\*\[([^\]]+)\]$/.exec(p.default),base=pair&&module.params.find(q=>q.key===pair[1]),ratio=pair&&module.params.find(q=>q.key===pair[2]);
    const relative=pair&&p.unit==='mL'&&((base?.unit==='mL'&&/\/THF|\/四氢呋喃|v\/v/.test((ratio?.label??'')+(ratio?.unit??'')))||(base?.unit==='g'&&/\/.*产品|mL\/g/.test((ratio?.label??'')+(ratio?.unit??''))));
    if(amount&&!relative||typeof p.default==='string'&&[...p.default.matchAll(/\[([^\]]+)\]/g)].some(m=>!module.params.some(q=>q.key===m[1])&&!design.vars.some(v=>v.key===m[1]))){out[p.key]='待指定';unbound.push(p.key);}else out[p.key]=p.default;
  }
  return {args:out,unboundParams:unbound,paramBindings,bindingMode:'design',paramOverrides:Object.keys(args)};
}
// A local override also rewrites dependent historical expressions in THIS step.
// Other steps and the global source retain their values.
export function localExpressionRewrite(design,module,step){
  const overrides=new Map();for(const p of module.params??[]){if(step.bindingMode==='design'&&!(step.paramOverrides??[]).includes(p.key))continue;const v=paramBinding(design,p,step),value=step.args?.[p.key];if(v&&value!==undefined&&value!==`[${v.key}]`){const replacement=typeof value==='number'?String(value):String(value);overrides.set(v.key,replacement);if(v.key!==p.key)overrides.set(p.key,replacement);}}
  const expand=(key,active=new Set())=>{if(overrides.has(key))return {text:`(${overrides.get(key)})`,changed:true};if(active.has(key))return {text:`[${key}]`,changed:false};const v=design.vars.find(v=>v.key===key);if(!v?.expr)return {text:`[${key}]`,changed:false};let changed=false;const next=new Set(active).add(key),text=v.expr.replace(/\[([^\]]+)\]/g,(_,ref)=>{const r=expand(ref,next);changed||=r.changed;return r.text;});return {text:changed?`(${text})`:`[${key}]`,changed};};
  return value=>typeof value==='string'?value.replace(/\[([^\]]+)\]/g,(_,key)=>expand(key).text):value;
}
