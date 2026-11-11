// 从旧树脂模块 kernel/ui/shared.js 只拿过程图要用的两个小函数（原样）
export const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
export const n=(v,d=2)=>typeof v==='number'&&Number.isFinite(v)?Number(v.toFixed(d)).toLocaleString('zh-CN',{maximumFractionDigits:d}):v==null||v===''?'未记录':typeof v==='number'?'待指定':String(v);
