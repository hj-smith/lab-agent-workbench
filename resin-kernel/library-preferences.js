import {recordFieldDefaults,configureRecordFields,configureHplcComponents} from './kernel/record-options.js';

export const preferenceDefaults={
 statuses:['草稿','待执行','已导入记录','已完成'],
 materialCategories:['单体A','单体B','催化剂','溶剂','添加剂','还原剂','后加试剂','补加试剂','其他'],
 materialForms:['未指定','无水','水合物','溶液'],
 agingModes:['室温老化','冷藏老化','冷冻老化','冻融','热老化'],
 // 样品与性能: how GPC sample names say which work-up step they are. Checked
 // top to bottom; 滤饼 / 滤液 and the reaction time (2H) are read separately.
 gpcNameRules:[
  {words:'打浆1、S1',code:'S1',label:'打浆 1'},{words:'打浆2、S2',code:'S2',label:'打浆 2'},{words:'打浆3、S3',code:'S3',label:'打浆 3'},
  {words:'沉降前、稀释后、PRE',code:'PRE',label:'沉降前'},{words:'沉降、PC、PRECIP',code:'PRECIP',label:'沉降'},{words:'干品、成品、FP',code:'FP',label:'干品'},
  {words:'正庚烷、庚烷、HEP',code:'HEP',label:'正庚烷'},{words:'异丙醇、IPA',code:'IPA',label:'IPA'},{words:'甲醇、MEOH',code:'MEOH',label:'甲醇'}],
 // 反应 HPLC 方法, kept as versions (启用日期): each has its components (峰面积 = 斜率 × mg/mL + 截距,
 // and the charged material the component is), the first dilution by mass (取样量 → 稀释后净重, its
 // volume from the diluent density: 0.1 wt% DMAP/THF ≈ THF) and the later ones by volume.
 hplcMethods:[{id:'m-2026-09',name:'外标法（示例，按自己的标定改）',from:'2026-09-01',diluentDensity:1,volumeFactor:1,
  components:[{name:'MonA',material:'MonA',slope:1,intercept:0},{name:'MonB',material:'MonB',slope:1,intercept:0}]}],
 recordFields:Object.fromEntries(Object.entries(recordFieldDefaults).map(([kind,fields])=>[kind,fields.map(({pattern,...field})=>({...field,type:field.type??'number'}))]))
};
let preferences={};
export const libraryChoices=key=>preferences[key]??preferenceDefaults[key];
export const currentPreferences=()=>structuredClone(preferences);
export function useLibraryPreferences(value={}){preferences=value;configureRecordFields(value.recordFields);configureHplcComponents([...new Set(hplcMethods().flatMap(m=>(m.components??[]).map(c=>c.name)))]);}
/** The HPLC methods in use; a library saved with the first single calibration reads as one version. */
export function hplcMethods(){
 if(Array.isArray(preferences.hplcMethods)&&preferences.hplcMethods.length)return preferences.hplcMethods;
 const old=preferences.hplcCalibration;
 if(Array.isArray(old?.curves))return [{id:'m-saved',name:'已保存的标准曲线',from:'',diluentDensity:old.diluentDensity??0.889,volumeFactor:old.dilution??50,components:old.curves.map(c=>({name:c.name,material:c.name,slope:c.slope,intercept:0}))}];
 return preferenceDefaults.hplcMethods;
}
