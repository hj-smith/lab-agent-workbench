import {appFetch} from './api-url.js';
// Historical files provide process evidence; generated SOP layout is independently standardized.
import {sourcePlanEligible} from './source-plan.js';
const list=value=>Array.isArray(value)?value:[];
const missing=value=>value==null||(typeof value==='string'&&!value.trim());
const normalize=value=>String(value??'').replace(/\s+/g,'');
const historical=exp=>['历史资料','imported','historical','source_document'].includes(exp.status);
const sourceFile=exp=>exp.plan?.sourceSop?.file??exp.sourceDocument?.file??list(exp.sourceFiles).find(file=>typeof file==='string'&&/\.(docx|xlsx)$/i.test(file));
const clone=value=>value==null?value:JSON.parse(JSON.stringify(value));
let seedPromise;
async function seedState(options){if(options.seedState)return options.seedState;if(options.seed)return options.seed;seedPromise??=appFetch('/seed.json').then(response=>{if(!response.ok)throw new Error('历史工艺内容基准未能载入。');return response.json();});return seedPromise;}
const numericFields=new Set(['scale','ratio','catalystMolPct','temp','holdHours','feedHours','quenchEq','quenchAcidFraction','quenchSolutionPct','feedSolventMass','solidPct']);
function normalizedField(key,value){
 if(missing(value))return null;
 if(numericFields.has(key)&&Number.isFinite(Number(value)))return Number(Number(value).toFixed(12));
 if(['monoAs','solvents'].includes(key))return list(value).map(row=>({materialId:row.materialId??null,fraction:missing(row.fraction)?null:Number(Number(row.fraction).toFixed(12))}));
 if(key==='feedSolvents')return list(value).map(row=>({materialId:row.materialId??null,mass:missing(row.mass)?null:Number(row.mass)}));
 return value;
}
function resolveText(step,exp,calc){
 const plan=exp.plan??{},params=step.params??{},values={...params,...plan,...calc,plan,calc,params};
 // Current design controls prevail over any legacy per-step duplicate settings.
 for(const key of ['temp','holdHours','feedHours','ratio','solidPct','catalystMolPct','quenchEq'])values[key]=plan[key];
 return String(step.text??'').replace(/\{\{\s*([^{}]+?)\s*\}\}/g,(_,key)=>{const value=key.split('.').reduce((result,part)=>result?.[part],values);return missing(value)?'【待填写 '+key+'】':typeof value==='object'?JSON.stringify(value):String(value);});
}
function phase(step){return step.phase??step.params?.section??step.sourcePhase??(/后处理|沉降|过滤|洗|干燥|打浆|postprocess|precipitate|filter|wash|dry/i.test(step.type??'')?'postprocess':'reaction');}
const fieldRules=[
 {keys:['scale','scaleBasis','materialPurities','calculationPurityBasis'],label:'投料规模／纯度口径',match:step=>/\d[\d.]*\s*(g|mL|ml|mol|mmol)/i.test(step.text)},
 {keys:['ratio','monoAs','monoBId'],label:'单体组成／单体比',match:step=>phase(step)==='reaction'&&/N-苯基|MonB|单体A|单体C/.test(step.text)},
 {keys:['catalystId','catalystMolPct'],label:'催化剂／投料酸量',match:step=>/对甲苯磺酸|甲磺酸|TsOH|TEA|吡啶|BaseQ/.test(step.text)},
 {keys:['solvents','feedSolvents','feedSolventId','feedSolventMass','solidPct','solidBasis'],label:'反应溶剂／固含／载料溶剂',match:step=>phase(step)==='reaction'&&/二甲苯|二氧六环|SolvC|SolvA/.test(step.text)},
 {keys:['temp'],label:'反应设温',match:step=>phase(step)==='reaction'&&/升温到|升温至|(?:°C|℃).*反应/.test(step.text)&&!/降温|放冷/.test(step.text)},
 {keys:['holdHours','timeBasis','stopCriterion','targetMolecularWeight','endpointTarget'],label:'反应时长／计时／终点',match:step=>phase(step)==='reaction'&&/反应|聚合/.test(step.text)&&/\d\s*h|小时|分子量|目标/.test(step.text)},
 {keys:['feedHours','feedMode'],label:'滴加时长／加料路线',match:step=>phase(step)==='reaction'&&/滴加|滴完|混合|逐渐滴/.test(step.text)},
 {keys:['termination','quenchId','quenchEq','quenchAcidFraction','quenchSolutionPct'],label:'终止／当量／终止剂浓度',match:step=>/TEA|吡啶|BaseQ|氨水/.test(step.text)}
];
export async function sourceSopBaseline(exp={},options={}){
 if(options.baselineExp)return clone(options.baselineExp);
 if(options.baselinePlan)return {code:options.baselineCode??exp.parentCode,plan:{...clone(options.baselinePlan),steps:clone(options.baselineSteps??options.baselinePlan.steps??[])}};
 const saved=exp.sopReviewBaseline;
 if(saved?.plan)return {code:saved.code??exp.parentCode,plan:{...clone(saved.plan),steps:clone(saved.steps??saved.plan.steps??[])}};
 const file=sourceFile(exp);if(!file)return null;
 const data=await seedState(options),experiments=list(data.experiments),candidateCode=options.baselineCode??exp.parentCode??exp.code;
 return experiments.find(item=>item.code===candidateCode&&sourceFile(item)===file)??experiments.find(item=>sourceFile(item)===file&&item.code===exp.plan?.branchCode)??experiments.find(item=>sourceFile(item)===file)??null;
}
function sourceKey(step){const source=step.params?.source??step.source;return source?.cell?[source.sheet??'',source.cell].join('!'):null;}
function matchBaselineStep(step,baselineSteps){
 return baselineSteps.find(previous=>previous.id===step.id)??baselineSteps.find(previous=>sourceKey(step)&&sourceKey(step)===sourceKey(previous))??baselineSteps.find(previous=>normalize(previous.text)===normalize(step.sourceText??step.params?.originalText??step.text));
}
function sourceParagraph(exp,step){
 const explicit=step.sourcePIndex??step.sourceParagraphIndex??step.params?.sourcePIndex??step.params?.sourceParagraphIndex;
 if(explicit!=null)return explicit;
 const sourceText=step.sourceText??step.params?.originalText??step.text;
 const index=list(exp.sourceDocument?.blocks).findIndex(block=>normalize(block.text)===normalize(sourceText));
 return index>=0?index:null;
}
export async function validateSourceSopContent(exp={},calc={},options={}){
 const baseline=await sourceSopBaseline(exp,options);if(!baseline)return [];
 const file=sourceFile(exp),wordEvidence=/\.docx$/i.test(file??'');
 if(/\.xlsx$/i.test(file??'')&&exp.sopTemplateMode!=='custom'&&!exp.plan?.workupBranches?.length){
  const current=options.nativeSopModel??options.model?.nativeSopModel??await import('./native-sop.js').then(module=>module.buildNativeSopModel(exp,calc,options));
  const conflicts=detectSopSourceConflicts(exp,current);
  // Historical evidence is retained with an explicit short note in the modern layout; current designs must resolve it.
  if(options.sourcePreserve===true||options.sourcePreserve!==false&&sourcePlanEligible(exp))return [];
  return conflicts.map(conflict=>({...conflict,fields:['quenchEq'],message:conflict.shortNote}));
 }
 if(!wordEvidence&&options.validateFixedText!==true)return [];
 const plan=exp.plan??{},baselinePlan=baseline.plan??{},steps=list(plan.steps),priorSteps=list(baselinePlan.steps),issues=new Map();
 const selected=steps.map((step,index)=>({step,index,previous:matchBaselineStep(step,priorSteps),text:resolveText(step,exp,calc)}));
 for(const rule of fieldRules){
  const changed=rule.keys.filter(key=>JSON.stringify(normalizedField(key,plan[key]))!==JSON.stringify(normalizedField(key,baselinePlan[key])));
  if(!changed.length)continue;
  for(const item of selected){if(!item.previous||!rule.match(item.previous)||normalize(item.text)!==normalize(resolveText(item.previous,baseline,calc)))continue;
   const id=item.step.id??'step-'+(item.index+1),issue=issues.get(id)??{code:'source-process-fixed-text',stepId:item.step.id??null,sourcePIndex:sourceParagraph(exp,item.step),sourceText:item.previous.text,fields:[],labels:[]};issue.fields.push(...changed);issue.labels.push(rule.label);issues.set(id,issue);
  }
  if(changed.some(key=>['monoBId','monoAs','catalystId','solvents','feedSolvents','feedSolventId','quenchId'].includes(key))){const current=plan.originalIngredients??plan.ingredients,prior=baselinePlan.originalIngredients??baselinePlan.ingredients;
   if(!missing(prior)&&normalize(current)===normalize(prior)){const issue=issues.get('ingredients')??{code:'source-process-fixed-header',stepId:null,sourcePIndex:null,sourceText:prior,fields:[],labels:[]};issue.fields.push(...changed);issue.labels.push(rule.label);issues.set('ingredients',issue);}
  }
 }
 if(!historical(exp)||exp.parentCode){const sourceCode=String(file??'').match(/CP\d+-\d+[AB]?/)?.[0];
  for(const item of selected){if(!item.previous||normalize(item.text)!==normalize(item.previous.text))continue;
   const known=sourceCode==='R100-10'&&/7\.80g.*0\.03mol/.test(item.text)?'原单体A质量与摩尔数矛盾':/^R100-(16|17)$/.test(sourceCode??'')&&/5g\s*TEA.*5\.0eq/.test(item.text)?'原 5 g TEA 与 5.0 eq／0.005 mol 酸基准不一致':null;
   if(known){const id=item.step.id??'step-'+(item.index+1),issue=issues.get(id)??{code:'source-process-original-quantity-conflict',stepId:item.step.id??null,sourcePIndex:sourceParagraph(exp,item.step),sourceText:item.previous.text,fields:[],labels:[]};issue.labels.push(known);issues.set(id,issue);}
  }
 }
 return [...issues.values()].map(issue=>({...issue,fields:[...new Set(issue.fields)],labels:[...new Set(issue.labels)],message:[...new Set(issue.labels)].join('、')+'已变化或存在原始疑点，但'+(issue.stepId?'操作 '+issue.stepId:'原料清单')+'仍保留固定旧内容：'+issue.sourceText+'。请修订正文或使用对应 {{参数}} 引用；后处理用量须按本轮设计明确。'}));
}
export function sopProvenanceFacts(exp={}){
 const plan=exp.plan??{},basis=plan.quenchEqBasis??plan.quenchAcidFractionBasis??null;
 const unresolved=missing(basis)||/not explicit|inferred|未明确|不明确|推断/i.test(String(basis));
 return {sourceFile:sourceFile(exp)??null,quenchEq:plan.quenchEq??null,quenchEqBasis:basis,quenchEqBasisConfirmed:!unresolved,quenchEqBasisNote:!missing(plan.quenchEq)&&unresolved?'源记录给出终止当量，但未明确当量基准；保留原标记，不自动解释为对酸当量。':null};
}
export function renameSopSampleReferences(text,exp={},baseline={}){
 const file=sourceFile(baseline)??sourceFile(exp),sourceCode=String(file??'').match(/CP\d+-\d+[AB]?/)?.[0];
 if(!sourceCode)return String(text??'');
 let code=exp.plan?.motherCode??exp.code??exp.id;if(!code||code===sourceCode)return String(text??'');
 const prior=baseline.code??exp.parentCode;if(prior?.startsWith(sourceCode+'-')){const suffix=prior.slice(sourceCode.length);if(code.endsWith(suffix))code=code.slice(0,-suffix.length);}
 const escaped=sourceCode.replace(/[.*+?^$()|[\]\\]/g,'\\$&');
 return String(text??'').replace(new RegExp(escaped+'(?=[-_/\\s:：)]|$)','g'),String(code));
}

function sourceMainParameterRows(model){
 if(list(model.parameters).length)return model.parameters;
 const main=model.main??model.nativeSopModel?.main;if(!main)return [];
 const entries=list(main.cells).length?main.cells:Object.values(main.cellMap??{}),rows=[];
 for(const cell of entries){
  const label=String(cell.value??cell.displayValue??'');
  if(!/^\s*(?:TEA|三乙胺)\s*[\/／]\s*(?:p\s*[-‐‑–−]?\s*TsOH|TsOH|对甲苯磺酸)\s*$/i.test(label))continue;
  const address=String(cell.cell??'').match(/^([A-Z]+)(\d+)$/);if(!address)continue;
  let index=[...address[1]].reduce((value,char)=>value*26+char.charCodeAt(0)-64,0)+1,name='';for(let value=index;value>0;value=Math.floor((value-1)/26))name=String.fromCharCode(65+(value-1)%26)+name;
  const valueCell=main.cellMap?.[name+address[2]]??entries.find(value=>value.row===cell.row&&value.col===cell.col+1);
  const unitCell=entries.find(value=>value.row===cell.row&&value.col===cell.col+2);
  rows.push({label,value:valueCell?.value,unit:unitCell?.value??unitCell?.displayValue??null,sourceCell:valueCell?.sourceCell??valueCell?.cell??null});
 }
 return rows;
}
export function detectSopSourceConflicts(exp={},model={}){
 if(!/\.xlsx$/i.test(sourceFile(exp)??''))return [];
 const parameters=sourceMainParameterRows(model),parameter=parameters.find(row=>/^\s*(?:TEA|三乙胺)\s*[\/／]\s*(?:p\s*[-‐‑–−]?\s*TsOH|TsOH|对甲苯磺酸)\s*$/i.test(String(row.label??row.name??'')));
 if(parameter?.unit!=null&&String(parameter.unit).trim()&&!/^eq(?:\b|\s*[/／])/i.test(String(parameter.unit).trim()))return [];
 const expected=parameter?.value??parameter?.rawValue;if(expected==null||expected===''||!Number.isFinite(Number(expected)))return [];
 const main=model.main??model.nativeSopModel?.main,entries=list(main?.cells).length?main.cells:Object.values(main?.cellMap??{});
 let steps=[...list(model.leftSteps),...list(model.rightSteps)];
 if(!steps.length)steps=list(exp.plan?.steps).map(step=>{const source=step.params?.source??step.source,cell=entries.find(value=>(value.sourceCell??value.cell)===source?.cell);return {id:step.id,text:cell?.displayValue??cell?.value??step.text,sourceCell:source?.cell,displayNo:step.params?.sourceStepNumber};});
 const result=[];
 for(const step of steps){const text=String(step.text??''),pattern=/(?:\bTEA\b|三乙胺)[^。；;\n]{0,35}?(\d+(?:\.\d+)?)\s*eq\s*[\/／]\s*(?:p\s*[-‐‑–−]?\s*TsOH|TsOH|对甲苯磺酸)/gi;
  for(const match of text.matchAll(pattern)){if(/(?:[<>≤≥±]|约)\s*\d+(?:\.\d+)?\s*eq|\d+(?:\.\d+)?\s*[-~～–]\s*\d+(?:\.\d+)?\s*eq/.test(match[0]))continue;const declared=Number(match[1]);if(Math.abs(declared-Number(expected))<=1e-8)continue;
   const source=step.sourceCell??step.params?.source?.cell??null,no=step.displayNo??step.params?.sourceStepNumber??null;
   const declaredText=declared.toFixed(2),expectedText=Number(expected).toFixed(2),location=no!=null?'步骤 '+no:source?'操作 '+source:'终止操作';
   result.push({code:'source-explicit-quench-eq-conflict',stepId:step.id??step.stepId??null,sourceCell:source,sourceNo:no,parameterCell:parameter.sourceCell??parameter.cell??null,expectedEq:Number(expected),declaredEq:declared,eqBasis:'TEA/p-TsOH',nativeStepText:text,shortNote:location+'：TEA '+declaredText+' eq/p-TsOH 与参数 '+expectedText+' eq 不一致，执行前需核对。'});
  }
 }
 return result;
}
