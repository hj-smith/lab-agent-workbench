// Shared execution content for SOP preview, Word, and Excel. Historical observations are annexes.
import {plannedWorkupCharges,calcBranches,renderWorkupStep} from './workup.js';
import {hasWorkupDesign} from './workup-design.js';
import {renderAdditionText} from './process-additions.js';
const list=value=>Array.isArray(value)?value:[];
const object=value=>value&&typeof value==='object'?value:{};
const copy=value=>value==null?value:JSON.parse(JSON.stringify(value));
const present=value=>value!=null&&!(typeof value==='string'&&!value.trim());
const numeric=value=>value==null||value===''||typeof value==='boolean'||!Number.isFinite(Number(value))?null:Number(value);
const columnIndex=address=>{let value=0;for(const char of String(address).match(/^[A-Z]+/i)?.[0]?.toUpperCase()||'A')value=value*26+char.charCodeAt(0)-64;return value-1;};
const columnName=index=>{let result='';for(let value=index+1;value>0;value=Math.floor((value-1)/26))result=String.fromCharCode(65+(value-1)%26)+result;return result;};
const sourceOf=step=>copy(object(step.params).source??step.source??null);
const sourceFilename=exp=>exp.plan?.sourceSop?.file??exp.sourceDocument?.file??list(exp.sourceFiles).find(file=>typeof file==='string'&&/\.(xlsx|docx)$/i.test(file))??null;
function sopSheet(exp){const sheets=list(exp.sourceSheets);return sheets.find(sheet=>sheet.sheet===exp.plan?.sourceSop?.sheet)||sheets.find(sheet=>/SOP|计划/.test(sheet.role||''))||sheets[0]||null;}
function sourceCell(exp,source){if(!source?.cell)return null;const sheet=list(exp.sourceSheets).find(value=>(!source.sheet||value.sheet===source.sheet)&&(!source.file||value.file===source.file))||sopSheet(exp);return list(sheet?.rows).flatMap(row=>list(row.cells)).find(cell=>cell.cell===source.cell)||null;}
function displayValue(value,numberFormat){
  if(value==null)return '';
  if(typeof value!=='number'||!Number.isFinite(value))return typeof value==='object'?JSON.stringify(value):String(value);
  const pattern=String(numberFormat||'General').split(';')[0];
  if(pattern==='General')return String(value);
  const decimals=pattern.match(/\.(0+)/)?.[1]?.length??0;
  if(pattern.includes('%'))return (value*100).toFixed(decimals)+'%';
  if(/^[#0,]+(?:\.0+)?$/.test(pattern))return pattern.includes(',')?value.toLocaleString('en-US',{minimumFractionDigits:decimals,maximumFractionDigits:decimals}):value.toFixed(decimals);
  return String(value);
}
function wordField(exp,prefix){const row=list(exp.sourceDocument?.blocks).find(block=>typeof block.text==='string'&&block.text.startsWith(prefix));return row?row.text.slice(prefix.length):null;}
function isBranchHeading(step,exp){
  if(step.kind==='branchHeading'||step.isHeading===true||['heading','section','branch-heading','小标题'].includes(step.type))return true;
  const text=String(step.text||'').trim();
  if(!/^第[一二三四五六七八九十0-9]+份[，,\s]*记录为[^。；;\n]+[:：]?$/.test(text))return false;
  const paragraph=list(exp.sourceDocument?.blocks).find(block=>block.text?.trim()===text);
  return !paragraph?.format?.numPr;
}
function getValue(values,path){return String(path).split('.').reduce((value,key)=>value?.[key],values);}
function currentStepText(step,plan,calc){const params=object(step.params),values={...params,...plan,...calc,plan,calc,params,step:{...step,params}};for(const key of ['temp','holdHours','feedHours','ratio','solidPct','catalystMolPct','quenchEq'])values[key]=plan[key];return String(step.text??'').replace(/\{\{\s*([^{}]+?)\s*\}\}/g,(_,key)=>present(getValue(values,key))?String(getValue(values,key)):'【待填写 '+key+'】');}
function buildBranches(exp,sourcePreserve,steps){
  const plan=object(exp.plan),designed=!sourcePreserve&&hasWorkupDesign(plan),branches=copy(designed?plan.workupBranches.map(b=>({id:b.id,code:b.code,name:b.name,splitFraction:b.fraction})):list(plan.branches));
  for(const step of (designed?[]:steps).filter(value=>value.kind==='branchHeading')){const id=step.text.match(/记录为\s*([A-Za-z0-9_-]+)/)?.[1];if(id&&!branches.some(branch=>branch.id===id))branches.push({id,name:id});}
  const paired=branches.length>1;
  const fileCode=sourceFilename(exp)?.match(/R100-\d+(?:[AB])?/i)?.[0]??null;
  const motherCode=plan.motherCode??(paired?fileCode:exp.code??exp.id);
  return {mode:sourcePreserve&&paired?'mother-batch-with-branches':paired?'designed-branches':'single',motherCode,selectedArchiveCode:exp.code??exp.id,selectedBranch:plan.branchCode??null,branches,includesAllBranches:sourcePreserve&&paired,scopeNote:sourcePreserve&&paired?'执行内容保留源母批及完整分支对照；所选档案编号用于关联记录，不把全母批改称单支投料。':null};
}
function buildSteps(exp,calc,sourcePreserve,materials=[]){
  let phase='reaction',operationIndex=0,activeBranch=null,paragraphCursor=0;
  const steps=list(exp.plan?.steps).map((original,index)=>{
    const params=object(original.params),declared=original.phase??params.section??original.sourcePhase;
    if(declared==='reaction'||declared==='postprocess')phase=declared;
    else if(/^(后处理|沉降|过滤|洗涤|干燥|打浆|postprocess|precipitate|filter|wash|dry|slurry)$/i.test(original.type||''))phase='postprocess';
    const heading=isBranchHeading(original,exp),resolvedText=currentStepText(original,object(exp.plan),object(calc)),text=sourcePreserve?String(original.text??''):(original.additions?.length||original.conditions?renderAdditionText({...original,text:resolvedText},object(exp.plan),object(calc),materials):resolvedText);
    const branch=heading?text.match(/记录为\s*([A-Za-z0-9_-]+)/)?.[1]??null:null;
    if(branch)activeBranch=branch;
    const source=sourceOf(original),number=original.sourceNumber??original.sourceNo??params.sourceStepNumber??null;
    const paragraphBlocks=list(exp.sourceDocument?.blocks);let paragraphIndex=original.sourcePIndex??params.sourcePIndex??original.sourceParagraphIndex??null;
    if(paragraphIndex==null)paragraphIndex=paragraphBlocks.findIndex((block,position)=>position>=paragraphCursor&&block.text===(original.sourceText??params.originalText??original.text));
    if(paragraphIndex>=0)paragraphCursor=Math.max(paragraphCursor,paragraphIndex+1);
    if(!heading)operationIndex++;
    return {...copy(original),id:original.id??'step-'+(index+1),kind:heading?(branch?'branchHeading':'heading'):'operation',isHeading:heading,title:original.title??original.type??'',text,sourceText:original.sourceText??params.originalText??original.text??'',params:copy(params),sourceNo:number,sourceNumber:number,sourceCell:source?{...source,sheet:source.sheet??exp.plan?.sourceSop?.sheet??null,cell:source.cell??null}:null,sourceParagraphIndex:paragraphIndex>=0?paragraphIndex:null,sourcePIndex:paragraphIndex>=0?paragraphIndex:null,phase,branchScope:original.branchScope??(activeBranch&&phase==='postprocess'?activeBranch:phase==='reaction'?'mother':list(exp.plan?.branches).length>1?'A/B':'batch'),operationIndex:heading?null:operationIndex,sourceOrder:index+1};
  });
  return steps;
}
function sourceParameters(exp){return list(exp.plan?.parameterEvidence).map((row,index)=>{const cell=sourceCell(exp,row.source),value=row.value??null;return {...copy(row),id:row.id??'source-parameter-'+(index+1),label:row.name??row.label??'',value,rawValue:value,unit:row.unit??null,numberFormat:cell?.numberFormat??null,displayValue:displayValue(value,cell?.numberFormat),sourceCell:copy(row.source??null),valueKind:row.valueKind??(/预计|理论|名义/.test(row.name||'')?'source-design-assumption':row.formula?'source-calculated-plan':'source-plan-input')};});}
function designParameters(plan,calc){
  const generic=plan.calculationProfile==='generic-mass';
  const definitions=[['scale','投料尺度',plan.scale,({mmol:'mmol 单体B','monoB-g':'g 单体B',g:'g 单体合计','monomer-g':'g 单体合计'})[plan.scaleBasis]??null],['ratio','总单体A/单体B摩尔比',plan.ratio,'mol/mol'],['temp','反应温度',plan.temp,'°C'],['feedHours','滴加时间',plan.feedHours,'h'],['holdHours',plan.timeBasis==='after-feed'?'滴后保温时间':plan.timeBasis==='total-from-feed-start'?'从滴加开始的总时长':plan.timeBasis==='at-temperature'?'达温后反应时长':'终点控制记录时长',plan.holdHours,'h'],['timeBasis','计时口径',plan.timeBasis,null],['catalystMolPct','催化剂/单体B',plan.catalystMolPct,'mol%'],['solidPct','反应投料固含',plan.solidPct,'wt%'],['solidBasis','固含分子定义',plan.solidBasis,null],['quenchTemp','终止/分料温度',plan.quenchTemp,'°C'],['quenchEq','终止剂/投料酸',plan.quenchEq,'eq'],['quenchAcidFraction','终止名义酸份额',plan.quenchAcidFraction,null],['expectedYieldFraction','预计分离收率（设计假设）',plan.expectedYieldFraction,null],['endpointMetric','终点指标与方法',plan.endpointMetric,null],['endpointTarget','终点阈值/判据',plan.endpointTarget??plan.stopCriterion,null]];
  const applicable=generic?definitions.filter(([key])=>['temp','feedHours','holdHours','timeBasis','quenchTemp'].includes(key)||key==='expectedYieldFraction'&&numeric(plan.theoreticalProductMass)!=null&&numeric(plan.theoreticalProductMass)>=0):definitions;
  if(generic)applicable.push(['theoreticalProductMass','理论产品质量（用户输入）',plan.theoreticalProductMass,'g']);
  const rows=applicable.filter(([, ,value])=>present(value)).map(([key,label,value,unit])=>({id:key,key,label,value,rawValue:value,unit,displayValue:String(value),valueKind:key==='expectedYieldFraction'?'design-assumption':'design-input',sourceCell:null}));
  const calculated=[['totalFromFeedStartHours','从滴加开始的总时长','h'],['postFeedHoldHours','滴后保温时长','h'],...(generic?[['totalMass','投料总质量','g'],['knownMass','已知投料质量','g'],['solidNumeratorMass','显式固体质量合计','g'],['solidPct','投料固体质量分数','wt%']]:[['totalMass','终止前反应投料总质量','g'],['solventMass','反应溶剂总质量','g'],['monomerMass','单体称量总质量','g'],['catalystMass','催化剂称量质量','g'],['quenchMass','终止剂称量质量','g']])];
  for(const [key,label,unit]of calculated)if(present(calc[key])&&!rows.some(row=>row.label===label))rows.push({id:'calc-'+key,key,label,value:calc[key],rawValue:calc[key],unit,displayValue:String(calc[key]),valueKind:calc.valid===false?'design-calculation-pending':'design-calculation',sourceCell:null});
  for(const [key,value]of Object.entries(object(plan.customFields)))rows.push({id:'custom-'+key,key,label:key,value,rawValue:value,unit:typeof value==='object'?value?.unit??null:null,displayValue:typeof value==='object'?String(value?.value??value?.text??JSON.stringify(value)):String(value??''),valueKind:'design-custom',sourceCell:null});
  for(const parameter of list(plan.customParameters))rows.push({id:'parameter-'+parameter.id,key:parameter.id,label:parameter.label,value:parameter.value,rawValue:parameter.value,unit:parameter.unit??null,displayValue:String(parameter.value??''),valueKind:'design-custom',sourceCell:null});
  for(const branch of calc.workupBranches?.branches??[])for(const row of branch.rows){
    if(row.amount==null)continue;
    rows.push({id:'branch-'+branch.id+'-'+row.key,key:'branch-'+branch.code+'-'+row.key,label:branch.code+'支 '+row.label,value:row.amount,rawValue:row.amount,unit:row.unit,displayValue:String(row.amount),valueKind:row.key==='product'?'design-assumption':'design-calculation',sourceCell:null});
  }
  return rows;
}
function buildSampling(exp,sourcePreserve){
  const plan=object(exp.plan),rawRows=sourcePreserve?list(plan.samplingRows):list(plan.designSamplingRows??plan.samplingRows);
  if(!rawRows.length)return {title:null,columns:[],rows:[],allRows:[],rawRows:[],planText:plan.samplingPlan??null};
  const width=Math.max(14,...rawRows.flatMap(row=>list(row.cells).map(cell=>columnIndex(cell.cell)+1)));
  const allRows=rawRows.map(row=>{const values=Array(width).fill(null),cells=Array(width).fill(null);for(const cell of list(row.cells)){const index=columnIndex(cell.cell),reference={file:plan.sourceSop?.file,sheet:plan.sourceSop?.sheet,cell:cell.cell},original=sourceCell(exp,reference);values[index]=cell.value??cell.cachedValue??null;cells[index]={...copy(cell),column:index,columnName:columnName(index),rawValue:values[index],displayValue:displayValue(values[index],original?.numberFormat??cell.numberFormat),sourceCell:reference};}return {sourceRow:row.row,values,cells};});
  const sheet=sopSheet(exp),heading=list(sheet?.rows).filter(row=>row.row<rawRows[0].row).flatMap(row=>list(row.cells)).filter(cell=>/^D[.、\s]|取样|共同反应/.test(String(cell.value??''))).at(-1)?.value??null;
  return {title:heading,columns:allRows[0].values.map(value=>value==null?'':String(value)),rows:allRows.slice(1),allRows,rawRows:copy(rawRows),planText:plan.samplingPlan??null};
}
function plannedCharges(exp,calc,sourcePreserve,context){
  const plan=object(exp.plan),rows=copy(sourcePreserve?list(plan.sourceCharges):plan.workupBranches?.length||plan.calculationProfile==='generic-mass'&&list(calc.rows).length?plannedWorkupCharges(plan,calc):list(calc.rows).length?list(calc.rows):list(plan.charges));
  if(sourcePreserve&&context.includesAllBranches)for(const parameter of list(plan.parameterEvidence)){const match=String(parameter.name||'').match(/^([AB])支(.+?)质量/);if(match&&!rows.some(row=>row.scope==='branch-'+match[1]&&row.name===match[2]))rows.push({materialId:match[2],name:match[2],amount:parameter.value,unit:parameter.unit??null,stage:'neutralization',scope:'branch-'+match[1],source:copy(parameter.source),fromSourceParameter:true});}
  return rows.map((row,index)=>{
    const amount=sourcePreserve?(row.amount??row.value??row.massG??row.mass??null):(row.mass??row.massG??row.amount??row.value??null),unit=row.unit??(present(row.mass)||present(row.massG)?'g':null),stage=row.stage??row.role??null;
    const branchScope=row.branchScope??(row.scope==='branch-A'?'A':row.scope==='branch-B'?'B':row.scope==='branch-all'?'A/B':context.includesAllBranches&&/reaction|initial|monoB|monoA|acid carrier|monoA carrier|catalyst|solvent/.test(stage||'')?'mother':row.scope==='mother-batch'?'mother':row.scope??'batch');
    return {...copy(row),id:row.id??'charge-'+(index+1),name:row.name??row.materialId??'',amount,unit,stage,scope:row.scope??null,branchScope,scopeLabel:branchScope==='mother'?'共用母批':branchScope==='A/B'?'A、B每支各用':branchScope==='A'||branchScope==='B'?branchScope+'支':branchScope,valueKind:sourcePreserve?'source-planned-charge':calc.rows?.length?(calc.valid===false?'design-calculation-pending':'design-calculation'):'design-manual',sourceCell:copy(row.source??null)};
  });
}
function sections(exp,steps,sourcePreserve){
  const output=[];let current;
  const sourceSheet=sopSheet(exp),headerRow=list(sourceSheet?.rows).find(row=>row.row===11);
  const sourceHeadings=!sourcePreserve&&exp.plan?.calculationProfile==='generic-mass'?{reaction:'实验操作',postprocess:'后处理'}:{reaction:list(headerRow?.cells).find(cell=>/^A11$/i.test(cell.cell))?.value??'树脂合成',postprocess:list(headerRow?.cells).find(cell=>/^H11$/i.test(cell.cell))?.value??'后处理'};
  for(const step of steps){
    if(step.isHeading){current={id:'section-'+output.length,phase:step.phase,title:step.text||step.title,kind:step.kind,heading:copy(step),branchScope:step.branchScope,steps:[]};output.push(current);continue;}
    if(!current||current.phase!==step.phase){current={id:'section-'+output.length,phase:step.phase,title:sourceHeadings[step.phase],kind:'phase',branchScope:step.phase==='reaction'?'mother':step.branchScope,steps:[]};output.push(current);}
    current.steps.push(step);
  }return output;
}

export function buildSopContent(exp={},calc={},options={}){
  const plan=object(exp.plan),sourcePreserve=options.sourcePreserve===true,filename=sourceFilename(exp),kind=/\.docx$/i.test(filename||'')?'docx':/\.xlsx$/i.test(filename||'')?'xlsx':'design';
  const steps=buildSteps(exp,calc,sourcePreserve,options.materials??[]),branchContext=buildBranches(exp,sourcePreserve,steps),sheet=sopSheet(exp);
  if(!sourcePreserve&&hasWorkupDesign(plan))for(const step of steps)if(step.params?.generatedBy==='workup-branches'&&step.params.operation)step.text=renderWorkupStep(step,plan,calc.workupBranches??calcBranches(plan,calc,options.materials??[]),options.materials??[]);
  const originalTitle=list(exp.sourceDocument?.blocks).find(block=>present(block.text))?.text??list(sheet?.rows).find(row=>row.row===1)?.cells?.find(cell=>present(cell.value))?.value??null;
  const ingredients=sourcePreserve?(wordField(exp,'反应原料：')??plan.originalIngredients??plan.ingredients??null):((plan.originalIngredients??plan.ingredients??[...new Set((list(calc.rows).length?calc.rows:list(plan.charges)).map(row=>row.name??row.materialId).filter(Boolean))].join('，'))||null);
  const equipment=sourcePreserve?(wordField(exp,'反应装置：')??plan.equipment??null):(plan.equipment??null),vessels=sourcePreserve?(wordField(exp,'反应容器：')??plan.vessels??plan.vessel??null):(plan.vessels??plan.vessel??null);
  const evidence=sourceParameters(exp),parameters=sourcePreserve?evidence:designParameters(plan,calc),sampling=buildSampling(exp,sourcePreserve);
  const executionTitle=sourcePreserve?(present(originalTitle)?String(originalTitle):(exp.code??exp.id??'')+' 树脂合成SOP'):present(exp.sopTitle)?String(exp.sopTitle):(exp.code??exp.id??'')+(plan.calculationProfile==='generic-mass'?' 实验SOP':' 树脂合成SOP');
  const execution={code:sourcePreserve&&branchContext.includesAllBranches?branchContext.motherCode:exp.code??exp.id,title:executionTitle,ingredients,equipment,vessels,equipmentEmbeddedInStep:kind==='xlsx'&&steps.some(step=>step.text===equipment),vesselsEmbeddedInStep:kind==='xlsx'&&!present(vessels),parameters,plannedCharges:plannedCharges(exp,calc,sourcePreserve,branchContext),steps,operations:steps.filter(step=>!step.isHeading),sections:sections(exp,steps,sourcePreserve),sampling,samplingPlan:plan.samplingPlan??null,safetyNotes:plan.safetyNotes??null,branchContext,sourceWordBlocks:sourcePreserve?copy(list(exp.sourceDocument?.blocks)):[],sourceWordProfileFile:exp.sourceWordProfileFile??plan.sourceWordProfileFile??(kind==='docx'?filename:null),readiness:sourcePreserve?'source-record':exp.needsStepReview||calc.valid===false?'draft-needs-review':'design'};
  const annexes={actual:copy(object(exp.actual)),performance:{samples:copy(list(exp.samples))},provenance:{experimentCode:exp.code??exp.id,revision:exp.revision??1,date:exp.date??null,sourceFiles:copy(list(exp.sourceFiles)),sourcePaths:copy(list(exp.sourcePaths)),sourceSop:copy(plan.sourceSop??null),originalTitle,sourcePlanParameterEvidence:evidence,sourcePlanCharges:copy(list(plan.sourceCharges)),sourceSamplingRows:copy(list(plan.samplingRows)),issues:copy(list(exp.issues)),designIssues:copy(list(exp.designIssues)),issueResolutions:copy(list(exp.issueResolutions)),history:copy(list(exp.history??exp.revisions)),quenchEqBasis:plan.quenchEqBasis??null,quenchAcidFractionBasis:plan.quenchAcidFractionBasis??null,sourceWord:copy(exp.sourceDocument??null),sourceSheets:copy(list(exp.sourceSheets))}};
  return {schema:'lab-sop-content/1',sourcePreserve,sourceKind:kind,sourceIdentity:{file:filename,originalTitle,archiveCode:exp.code??exp.id,motherCode:branchContext.motherCode},execution,annexes,ingredients,equipment,vessels,parameters,plannedCharges:execution.plannedCharges,steps,sections:execution.sections,sampling,branchContext};
}
