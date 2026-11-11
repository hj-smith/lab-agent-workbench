import {historicalSummaryRecords} from './history-factors.js';
import {stableUid} from './identity.js';

const list=value=>Array.isArray(value)?value:[];
const finite=value=>typeof value==='number'&&Number.isFinite(value);
export const HISTORICAL_PLATFORM_VERSION=2;
export function historicalPlatformSignature(experiment,design){
  const originals=list(experiment.archiveRecords).map(row=>({id:row.id,code:row.code,aliases:row.aliases,rawSummary:row.rawSummary,rawSummaryRefs:row.rawSummaryRefs}));
  return stableUid('lab-history-platform-input/2',JSON.stringify([experiment.plan?.workupBranches??experiment.plan?.branches,experiment.branchPlans,experiment.rawSummary,experiment.rawSummaryRefs,originals,historicalSummaryRecords(experiment,design,list(experiment.archiveRecords))]));
}
export const needsHistoricalPlatformRepair=(experiment,design)=>experiment.archive===true&&/^R100-(21|22|36|37|38)$/.test(experiment.code)&&design?.conversion?.historicalPlatformVersion!==HISTORICAL_PLATFORM_VERSION&&design?.conversion?.historicalPlatformPending?.signature!==historicalPlatformSignature(experiment,design);

export function finishHistoricalPlatformRepair(design,experiment){
  const branches=list(experiment.plan?.workupBranches??experiment.plan?.branches);
  const complete=branches.length>=2&&branches.every(b=>design.historicalBranchResults?.[b.id]?.metadata_version===HISTORICAL_PLATFORM_VERSION);
  if(complete){design.conversion.historicalPlatformVersion=HISTORICAL_PLATFORM_VERSION;delete design.conversion.historicalPlatformPending;}
  else if(/^R100-(21|22|36|37|38)$/.test(experiment.code)){
    delete design.conversion.historicalPlatformVersion;
    design.conversion.historicalPlatformPending={version:HISTORICAL_PLATFORM_VERSION,signature:historicalPlatformSignature(experiment,design)};
  }
}

/** Add source-backed result metadata without rewriting original summary cells,
 * metrics, or nominal SOP fractions. Called only by the backed-up migration. */
export function enrichHistoricalPlatform(design,experiment){
  const archives=list(experiment.archiveRecords),records=historicalSummaryRecords(experiment,design,archives);
  const branches=list(experiment.plan?.workupBranches??experiment.plan?.branches);
  if(branches.length<2)return design;
  design.historicalBranchResults={};
  const warn=(branch,message)=>{
    design.historicalNotes??=[];
    const note={severity:'warning',message:branch.id+'：'+message,source:{experimentId:experiment.id,branchId:branch.id,migration:'round5-platform-pending'}};
    if(!design.historicalNotes.some(row=>JSON.stringify(row)===JSON.stringify(note)))design.historicalNotes.push(note);
  };
  for(const branch of branches){
    const record=records.find(row=>[branch.id,branch.archiveCode].includes(row.experimentId));
    if(!record){warn(branch,'缺少原总表分支记录，未补造产品身份或产率口径；来源更新后重试。');continue;}
    const summary=record.summary,theory=summary['理论产品 整批(g)'],summaryFraction=summary['分样比例 未分样填100%'];
    const original=archives.find(row=>row.id===branch.id||row.code===branch.archiveCode);
    const variantCode=branch.archiveCode??original?.code??branch.id;
    const source=key=>({experimentId:record.experimentId,...structuredClone(record.refs?.[key]??{}),path:'rawSummary['+JSON.stringify(key)+']'});
    let fraction=finite(summaryFraction)&&summaryFraction>0&&summaryFraction<=1?summaryFraction:null;
    let fractionSource=source('分样比例 未分样填100%'),basis='原总表记录份额',fractionConfirmed=false;
    const side=/A$/.test(variantCode)?'A':/B$/.test(variantCode)?'B':null;
    const ids=[...new Set([branch.id,branch.archiveCode,original?.id,original?.code,...list(original?.aliases)].filter(Boolean))];
    design.historicalBranchResults[branch.id]={metadata_version:HISTORICAL_PLATFORM_VERSION,theoretical_mass_g:finite(theory)&&theory>0?theory:null,fraction,fraction_confirmed:fractionConfirmed,source_ref:{theoretical:source('理论产品 整批(g)'),fraction:fractionSource},basis,summary_fraction:summaryFraction??null,summary_fraction_source:source('分样比例 未分样填100%'),variant_legacy_ids:ids,variant_legacy_links:ids.map(id=>({legacy_id:id,kind:'historical-variant-archive',source_ref:{archive_record:{id:original?.id??branch.id,code:variantCode},summary:source('分样比例 未分样填100%')}}))};
  }
  const owners=new Map();
  for(const [branchId,result]of Object.entries(design.historicalBranchResults))for(const id of result.variant_legacy_ids){if(!owners.has(id))owners.set(id,new Set());owners.get(id).add(branchId);}
  for(const [branchId,result]of Object.entries(design.historicalBranchResults)){
    const shared=result.variant_legacy_ids.filter(id=>owners.get(id).size>1);
    if(shared.length)warn({id:branchId},'分支间共享的旧别名未加入 lot：'+shared.join('、')+'。');
    result.variant_legacy_ids=result.variant_legacy_ids.filter(id=>owners.get(id).size===1);
    result.variant_legacy_links=result.variant_legacy_links.filter(link=>result.variant_legacy_ids.includes(link.legacy_id));
  }
  return design;
}
