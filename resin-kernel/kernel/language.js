// Material abbreviations and user prose are authoritative. Translate fixed UI
// labels at their call sites; a global text rewrite corrupts 2-MeTHF and DIPA.
import {formatText} from './expr.js';
export const chinese=text=>String(text??'');
export const chineseTemplate=text=>String(text??'').replace(/\{\{[^{}]*\}\}|\{[^{}]*\}|[^{}]+/g,part=>part.startsWith('{')?part:chinese(part));
export const readableLabel=label=>/沉降.*(?:MeOH\s*\/\s*THF$|甲醇\s*\/\s*四氢呋喃$)/.test(label)?'沉降比（甲醇/四氢呋喃）':chinese(String(label??'').replace(/(?:质量|计划称量)·(?=分子量|纯度|密度|计划用量|体积)/g,'').replace(/·分子量/g,'分子量').replace(/·质量·/g,'·').replace(/MW\b/g,'分子量'));
export function readableExpression(expression,vars=[],charges=[]){return String(expression??'').replace(/\[([^\]]+)\]/g,(_,key)=>vars.find(v=>v.key===key)?.label??charges.find(c=>Object.values(c.keys??{}).includes(key))?.name??key).replace(/charge-/g,'').replace(/~mass/g,'质量').replace(/\*/g,' × ').replace(/整批\s*(?:THF|四氢呋喃)/g,'整批 THF').replace(/\s+/g,' ').trim();}
// One format per physical unit across the design, paper and workbook. Values
// keep full precision; this is presentation only.
export const unitFormat=(unit,fallback='0.###')=>({g:'0.00',mL:'0.0',eq:'0.00','mol/mol':'0.00','v/v':'0.00','°C':'0.###',h:'0.###',min:'0.###','%':'0.0','mol%':'0.0','w/w':'0.0%','g/mL':'0.###'})[unit]??fallback;
export const unitNumber=(value,unit='',fallback='0.###')=>typeof value==='number'&&Number.isFinite(value)?formatText(value,unitFormat(unit,fallback)):value==null||value===''?'—':typeof value==='number'?'待指定':String(value);
export const isExecutionParameter=v=>v?.role!=='code'&&v?.expr===undefined&&!v?.technical&&!['theoreticalWater','theoreticalResin','yieldFraction'].includes(v?.role)&&!/(?:MW\b|分子量|密度|纯度|理论|路线|假设|分离系数|_density|_purity|_amount|~)/i.test((v?.key??'')+' '+(v?.label??''));
export function samplingTimeText(node,steps=[]){
  if(typeof node?.hours==='number'&&Number.isFinite(node.hours))return unitNumber(node.hours,'h')+' h';
  const step=steps.find(s=>s.id===node?.stepId);
  return step?.stage==='workup'?'按步骤：'+(step.title??'后处理'):step?'按步骤：'+(step.title??'取样'):'按取样阶段';
}
