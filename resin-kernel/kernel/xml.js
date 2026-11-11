// Small OOXML helpers, shared by the reader and writer (no DOM dependency).
export const NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main';
export const esc = v => String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&apos;');
export const unesc = v => v.replace(/&(#x[\da-f]+|#\d+|amp|lt|gt|quot|apos);/gi,(_,s)=>s[0]==='#'?String.fromCodePoint(s[1].toLowerCase()==='x'?parseInt(s.slice(2),16):Number(s.slice(1))):({amp:'&',lt:'<',gt:'>',quot:'"',apos:"'"})[s]);
export const attrs = s => Object.fromEntries([...s.matchAll(/([\w:]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)].map(m=>[m[1],unesc(m[2]??m[3])]));
export function tags(xml, name) { return [...xml.matchAll(new RegExp(`<${name}\\b([^>]*?)(?:\\/>|>([\\s\\S]*?)<\\/${name}>)`,'g'))].map(m=>({a:attrs(m[1]),body:m[2]??''})); }
export const textRuns = xml => tags(xml.replace(/<rPh\b[\s\S]*?<\/rPh>/g,''),'t').map(t=>unesc(t.body)).join('');
export function colName(n) { let s=''; for (;n;n=Math.floor((n-1)/26)) s=String.fromCharCode(65+(n-1)%26)+s; return s; }
export function address(s) { const m=/^\$?([A-Z]+)\$?(\d+)$/i.exec(s); if(!m) throw Error(`无效地址：${s}`); return {col:[...m[1].toUpperCase()].reduce((n,c)=>n*26+c.charCodeAt(0)-64,0),row:Number(m[2])}; }
export const xmlDoc = body => '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'+body;
