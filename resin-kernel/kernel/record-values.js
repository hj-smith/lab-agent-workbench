export const isBlank=value=>value===null||value===undefined||typeof value==='string'&&!value.trim();
export function parseClock(value){
  if(typeof value==='number'&&Number.isFinite(value))return value;
  if(value instanceof Date)return value.getTime()/86400000+25569;
  let text=String(value??'').normalize('NFKC').trim(),day=0;
  if(/^次日/.test(text)){day=1;text=text.replace(/^次日\s*/,'');}
  const d=/^第(\d+)日\s*/.exec(text);if(d){day=+d[1]-1;text=text.slice(d[0].length);}
  // Excel datetime strings, including an ISO date copied from another workbook.
  const dated=/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?/.exec(text);
  if(dated)return Date.UTC(+dated[1],+dated[2]-1,+dated[3],+dated[4],+dated[5],+(dated[6]??0))/86400000+25569;
  let match=/^(\d{1,2})[:.点时](\d{2})(?:[:分](\d{2}))?(?:分)?$/.exec(text);
  if(!match&&/^\d{3,4}$/.test(text))match=[text,text.slice(0,-2),text.slice(-2),'0'];
  if(match){const h=+match[1],m=+match[2],s=+(match[3]??0);return h<24&&m<60&&s<60?day+(h*3600+m*60+s)/86400:null;}
  if(/^\d*\.\d+$/.test(text))return +text;
  return null;
}
/** Lenient input parser. Unparsed and suspect data remain importable. */
export function parseRecordedValue(raw,{type='text',unit='',label='',min,max}={}){
  if(isBlank(raw))return {skip:true};
  if(type==='text')return {value:raw,raw};
  let value;const note=typeof raw==='string'?/^(约|大约|≈|~)\s*/.exec(raw.normalize('NFKC').trim())?.[1]:undefined;
  if(type==='time')value=parseClock(raw);
  else if(typeof raw==='number')value=raw;
  else {
    let text=String(raw).normalize('NFKC').trim().replace(/^(约|大约|≈|~)\s*/,'').replace(/\s/g,'');
    // A decimal comma differs from a grouping comma: 30,1 vs 1,173.7 / 1,000.
    text=text.replace(/(?<=\d),(?=\d)/g,match=>match);
    if(/^[-+]?\d{1,3}(,\d{3})+(\.\d+)?(?:[^\d].*)?$/.test(text))text=text.replaceAll(',','');
    else if(!text.includes('.')&&(text.match(/,/g)??[]).length===1)text=text.replace(',','.');
    const match=/^([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?)(%|g|kg|mg|mL|ml|L|°C|℃|h|min|rpm|mmol|mol|w\/w|wt%)?$/i.exec(text);
    if(match){value=+match[1];const suffix=match[2]??'';if(/kg/i.test(suffix)&&unit==='g')value*=1000;if(suffix==='mg'&&unit==='g')value/=1000;if(suffix==='L'&&unit==='mL')value*=1000;if(/%/.test(suffix))value/=100;}
  }
  if(value===null||value===undefined||!Number.isFinite(value))return {value:raw,raw,flag:'unparsed',message:'未能识别为'+(type==='time'?'时间':'数值')};
  if(type!=='time'&&/%|w\/w|含固|纯度/.test(unit+' '+label)&&value>1&&!/%/.test(String(raw)))value/=100;
  const temperature=/内温|外温|温度|°C|℃/.test(label+unit),fraction=/%|w\/w|含固|纯度/.test(label+unit);
  min??=temperature?-100:type==='time'?-Infinity:0;max??=temperature?400:fraction?1:Infinity;
  return {value,raw,...note?{note}:{},...value<min||value>max?{flag:'suspect',message:`${label.split('/').at(-1).trim()||'记录值'} ${value} 超出合理范围 ${min}–${max}${unit?' '+unit:''}，请核对原始记录`}:{}};
}
export function actualT0(design){
  const value=design.actual?.['record-t0/actual/实际钟点']??design.actual?.['record-timeline/t-0/实际钟点']??design.actualT0;
  return isBlank(value)?null:parseClock(value);
}
export function timelineClock(design,id,plannedHour,nodes=[]){
  const direct=design.actual?.[`record-timeline/${id}/实际钟点`];if(!isBlank(direct))return direct;
  const aliases=nodes.filter(n=>n.hours===plannedHour).sort((a,b)=>Number(b.id.startsWith('t-'))-Number(a.id.startsWith('t-')));
  // The generic time row can outlive its merged physical sample node.
  const generic='t-'+String(plannedHour).replace('.','_');if(!aliases.some(n=>n.id===generic)&&(design.samplingPlan?.points??[]).some(p=>typeof p==='number'?p===plannedHour:p.hours===plannedHour))aliases.unshift({id:generic,hours:plannedHour});
  if(id.endsWith('-start'))for(const n of aliases){const clock=design.actual?.[`record-timeline/${n.id}/操作起点`];if(!isBlank(clock))return clock;}
  for(const n of aliases){const clock=design.actual?.[`record-timeline/${n.id}/实际钟点`];if(!isBlank(clock))return clock;}
  return plannedHour===0?design.actual?.['record-t0/actual/实际钟点']:undefined;
}
export function relativeClock(value,t0,plannedHours=0){
  const clock=parseClock(value);if(clock===null||t0===null)return null;
  const base=t0-Math.floor(t0),qualified=/^(次日|第\d+日)/.test(String(value).trim());
  if(qualified||clock>=1&&clock<100)return (clock-base)*24;
  if(clock>=100&&t0>=100)return (clock-t0)*24;
  const face=(clock-Math.floor(clock)-base)*24,target=Number.isFinite(plannedHours)?plannedHours:0;
  const candidates=[-2,-1,0,1,2,3].map(day=>face+24*day).filter(h=>h>=-12-1e-8&&h<=36+1e-8);
  return candidates.sort((a,b)=>Math.abs(a-target)-Math.abs(b-target))[0]??face;
}
