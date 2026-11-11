import {historicalSummaryRecords,historyBranchMatches} from './history-factors.js';
import {backfillMeasuredProducts} from './legacy-results.js';
import {buildIntegrationRecords,prepareIntegrationDesign} from './integration.js';

export function presentationLots(design,experiment={},library={}){
 if(!design)return [];
 const prepared=prepareIntegrationDesign(backfillMeasuredProducts(design,experiment,library));
 return buildIntegrationRecords(prepared,library,library.registry??library.factorRegistry??{version:1,factors:[]},{source_instance_uid:'presentation',experiment}).lot;
}
export function historicalYieldFor(lot,design,experiment={}){
 const records=historicalSummaryRecords(experiment,design).filter(r=>(lot.legacy_ids??[]).includes(r.experimentId)||historyBranchMatches(r.experimentId,lot.branch)||r.experimentId===lot.code?.replace(/-FP$/,''));
 const record=records.length===1?records[0]:null,value=record?.summary?.['最终产率 (%)'];
 // The historical summary's yield column is recorded as 0.00%; retain any
 // explicit source format when available rather than comparing cached digits.
 const source_ref=record?.refs?.['最终产率 (%)']??{cell:'T'+record?.summaryRow},format=source_ref.numFmt??'0.00%',precision=(/\.([0#]+)/.exec(format)?.[1]??'').length;
 return typeof value==='number'?{value,original_value:value,source_ref,precision,source:'历史总表'}:null;
}
// Half a percentage point: below that the two are the same number rounded differently.
export const yieldDiffers=(lot,historical)=>typeof lot.key_results?.yield?.value==='number'&&typeof historical?.value==='number'&&Math.abs(lot.key_results.yield.value-historical.value)>=.005;
export const yieldSource=value=>value?.source_ref?.cell?'总表 '+value.source_ref.cell:'历史总表';
