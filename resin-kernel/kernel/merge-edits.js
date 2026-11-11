const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
// Save both windows' independent edits; the server's later value wins only
// where both windows changed the same field.
export function mergeEdits(base,incoming,current,conflicts=[],path=''){
 if(same(incoming,base))return structuredClone(current);
 if(same(current,base)||same(current,incoming))return structuredClone(incoming);
 if(Array.isArray(incoming)&&Array.isArray(current)){
  const key=x=>x?.uid??x?.id??x?.key;
  if([...incoming,...current,...base??[]].every(x=>key(x)!=null)){
   const before=new Map((base??[]).map(x=>[key(x),x])),local=new Map(incoming.map(x=>[key(x),x])),latest=new Map(current.map(x=>[key(x),x]));
   return [...new Set([...latest.keys(),...local.keys()])].map(id=>mergeEdits(before.get(id),local.get(id),latest.get(id),conflicts,path+'/'+id)).filter(x=>x!==undefined);
  }
 }else if(incoming&&current&&typeof incoming==='object'&&typeof current==='object'){
  return Object.fromEntries([...new Set([...Object.keys(current),...Object.keys(incoming),...Object.keys(base??{})])].map(key=>[key,mergeEdits(base?.[key],incoming[key],current[key],conflicts,path+'/'+key)]).filter(([,value])=>value!==undefined));
 }
 conflicts.push(path);return structuredClone(current);
}

export function availableName(name,existing){const values=new Set(existing);let candidate=name,n=2;while(values.has(candidate))candidate=name+'-'+n++;return candidate;}
