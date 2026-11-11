import {parseExpr, evaluateExpr, renderTemplate} from './expr.js';
import {layoutDoc, resolvePlaced, rangePlacements} from './layout.js';
export function evaluateDoc(doc, {errors}={}) {
  const layout=layoutDoc(doc), vars=new Map(doc.vars.map(v=>[v.key,v])), values=new Map(), active=new Set();
  function calculate(p) {
    if(!p) return null;
    if(values.has(p.fullAddr)) return values.get(p.fullAddr);
    if(active.has(p.fullAddr)) throw Error(`检测到循环引用：${[...active,p.fullAddr].join(' → ')}`);
    active.add(p.fullAddr);
    const resolve=ref=>calculate(resolvePlaced(layout,ref,p));
    const range=(a,b)=>rangePlacements(layout,a,b,p).map(calculate), locate=ref=>resolvePlaced(layout,ref,p).sheet;
    const expr=s=> { const n=parseExpr(s), v=evaluateExpr(n,resolve,range,locate); return v==null?0:v; };
    const c=p.cell; let value=null;
    if(c.input) value=doc.actual?.[p.key]??null;
    else if(c.var) {
      const v=vars.get(c.var); if(!v) throw Error(`找不到变量：${c.var}`);
      value=c.show==='label'?v.label:c.show==='unit'?v.unit??'':v.expr!==undefined?expr(v.expr):v.value;
    } else if(c.f!==undefined) value=expr(c.f);
    else if(c.t!==undefined) value=renderTemplate(c.t,resolve,range,locate);
    else if(c.v!==undefined) value=c.v;
    if(typeof value==='number' && !Number.isFinite(value)) throw Error(`公式结果不是有限数：${p.fullAddr}`);
    active.delete(p.fullAddr); values.set(p.fullAddr,value); return value;
  }
  for(const p of layout.byAddr.values()) {
    try { calculate(p); } catch(error) {
      if(!errors)throw error;
      errors.set(p.fullAddr,error.message);active.clear();
    }
  }
  return values;
}
export function readVarValues(doc, values=evaluateDoc(doc)) {
  const layout=layoutDoc(doc); return new Map(doc.vars.map(v=>[v.key,values.get(layout.varToAddr.get(v.key))]));
}
export function readRenderedTexts(doc, values=evaluateDoc(doc)) {
  return new Map([...values].filter(([,v])=>typeof v==='string'));
}
