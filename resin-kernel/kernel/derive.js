export function deriveDoc(doc,{code,title}) {
  if(!code) throw Error('派生批号不能为空');
  const out=structuredClone(doc); out.id=code; out.code=code; out.parentCode=doc.code; out.revision=1; out.actual={};
  out.title=title??doc.title.replaceAll(doc.code,code);
  for(const v of out.vars) if(v.role==='code') { delete v.expr; v.value=code; }
  return out;
}
