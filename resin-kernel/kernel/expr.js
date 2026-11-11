// AST: literal, ref (var/cell/raw), unary, binary, call, range.
const precedence = { '=':1, '<>':1, '<':1, '>':1, '<=':1, '>=':1, '&':2, '+':3, '-':3, '*':4, '/':4, '^':5, ':':7 };
export function tokenize(source, {a1 = false} = {}) {
  const tokens = []; let i = source.startsWith('=') ? 1 : 0;
  while (i < source.length) {
    const s = source.slice(i); let m;
    if (/^\s/.test(s)) { i++; continue; }
    if ((m = /^"((?:[^"]|"")*)"/.exec(s))) tokens.push({type:'literal', value:m[1].replaceAll('""','"')});
    else if ((m = /^(@?)\[([^\]]+)\]/.exec(s))) tokens.push({type:'ref', kind:m[1] ? 'cell' : 'var', key:m[2]});
    else if (a1 && (m = /^(?:(?:'((?:[^']|'')+)'|([\p{L}_][\p{L}\p{N}_.]*))!)?(\$?[A-Za-z]{1,3})(\$?\d+)(?![\w(])/u.exec(s))) {
      tokens.push({type:'ref', kind:'raw', sheet:(m[1]?.replaceAll("''", "'") ?? m[2]), addr:(m[3]+m[4]).toUpperCase()});
    } else if ((m = /^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?/.exec(s))) tokens.push({type:'literal', value:Number(m[0])});
    else if ((m = /^[A-Za-z_][A-Za-z_0-9.]*/.exec(s))) tokens.push({type:'name', value:m[0].toUpperCase().replace(/^_XLFN\.(?:_XLWS\.)?/,'')});
    else if ((m = /^(?:<>|<=|>=|[&+\-*/^=<>():,%])/.exec(s))) tokens.push({type:m[0]});
    else throw Error(`无法解析表达式：${s}`);
    i += m[0].length;
  }
  return tokens;
}
export function parseExpr(source, options) {
  const ts = tokenize(source, options); let i = 0;
  const take = t => { if (ts[i]?.type !== t) throw Error(`表达式缺少 ${t}：${source}`); return ts[i++]; };
  function expression(min = 0) {
    let n, t = ts[i++];
    if (!t) throw Error(`表达式不完整：${source}`);
    if (t.type === 'literal' || t.type === 'ref') n = t;
    else if (t.type === '+' || t.type === '-') n = {type:'unary', op:t.type, arg:expression(6)};
    else if (t.type === '(') { n = expression(); take(')'); }
    else if (t.type === 'name') {
      if (['TRUE','FALSE'].includes(t.value) && ts[i]?.type !== '(') n = {type:'literal', value:t.value === 'TRUE'};
      else { take('('); const args = []; if (ts[i]?.type !== ')') { do { args.push(expression()); } while (ts[i]?.type === ',' && ++i); } take(')'); n = {type:'call', name:t.value, args}; }
    } else throw Error(`表达式中意外的 ${t.type}`);
    while (ts[i]?.type === '%') { i++; n = {type:'binary', op:'/', left:n, right:{type:'literal',value:100}}; }
    while (precedence[ts[i]?.type] >= min) {
      const op = ts[i++].type, right = expression(precedence[op] + 1);
      n = {type:op === ':' ? 'range' : 'binary', op, left:n, right};
    }
    return n;
  }
  const ast = expression(); if (i !== ts.length) throw Error(`表达式有多余内容：${source}`); return ast;
}
export const parseA1Formula = source => parseExpr(source, {a1:true});
export function mapRefs(ast, fn) {
  if (ast.type === 'ref') return fn(ast);
  if (ast.left) return {...ast, left:mapRefs(ast.left,fn), right:mapRefs(ast.right,fn)};
  if (ast.arg) return {...ast, arg:mapRefs(ast.arg,fn)};
  if (ast.args) return {...ast, args:ast.args.map(x=>mapRefs(x,fn))};
  return ast;
}
export const quoteString = s => '"'+String(s).replaceAll('"','""')+'"';
export function serializeExpr(ast, resolver = ref => ref.kind === 'var' ? `[${ref.key}]` : ref.kind === 'cell' ? `@[${ref.key}]` : `${ref.sheet ? "'"+ref.sheet.replaceAll("'","''")+"'!" : ''}${ref.addr}`) {
  const rank = n => n.type === 'unary' ? 6 : n.left ? precedence[n.op] : Infinity;
  const child = (n, right=false) => {
    const text=serializeExpr(n,resolver), p=rank(n), parent=rank(ast);
    return p<parent || right && p===parent && ast.type!=='unary' ? `(${text})` : text;
  };
  switch (ast.type) {
    case 'literal': return typeof ast.value === 'string' ? quoteString(ast.value) : typeof ast.value === 'boolean' ? (ast.value?'TRUE':'FALSE') : String(ast.value);
    case 'ref': return resolver(ast);
    case 'unary': return `${ast.op}${child(ast.arg)}`;
    case 'binary': return `${child(ast.left)}${ast.op}${child(ast.right,true)}`;
    case 'range': return `${child(ast.left)}:${child(ast.right,true)}`;
    case 'call': return `${ast.name}(${ast.args.map(x=>serializeExpr(x,resolver)).join(',')})`;
    default: throw Error(`未知 AST：${ast.type}`);
  }
}
const blank = v => v === null || v === undefined;
const number = v => blank(v) || v === '' ? 0 : Number(v);
export const excelString = v => blank(v) ? '' : typeof v === 'boolean' ? (v ? 'TRUE':'FALSE') : String(v);
export function formatText(value, fmt, {excel=false}={}) {
  if(value==='待指定')return value;
  const time=/^(h{1,2}):mm$/i.exec(fmt.split(';')[0]);
  if (time) {
    const mins = ((Math.round(number(value)*1440)%1440)+1440)%1440;
    return `${String(Math.floor(mins/60)).padStart(time[1].length,'0')}:${String(mins%60).padStart(2,'0')}`;
  }
  const m = /^(0|#,##0)(?:\.([0#]+))?( ?%)?$/.exec(fmt);
  if (!m) throw Error(`不支持的 TEXT 格式：${fmt}`);
  const digits = m[2]?.length || 0, n = number(value)*(m[3]?100:1), scale = 10**digits;
  const rounded = Math.sign(n)*Math.round((Math.abs(n)+Number.EPSILON*Math.abs(n))*scale)/scale;
  let text=rounded.toFixed(digits);
  // Human display omits an orphan decimal point. Raw source TEXT formulas keep
  // Excel's exact behavior so importing a workbook does not rewrite its values.
  for(let i=digits-1;i>=0&&m[2][i]==='#'&&text.endsWith('0');i--)text=text.slice(0,-1);
  if(!excel&&text.endsWith('.'))text=text.slice(0,-1);
  if(m[1].includes(',')){const [integer,fraction]=text.split('.');text=integer.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(fraction===undefined?'':'.'+fraction);}
  return text+(m[3]??'');
}
// Functions Excel stores with the _xlfn. prefix; the model keeps the plain name.
export const FUTURE_FUNCTIONS = ['TEXTAFTER'];
export const excelFunctionNames = formula => formula.split(/("(?:[^"]|"")*")/).map((part,i)=>i%2?part:part.replace(/(?<![\w.])([A-Za-z][A-Za-z0-9.]*)\(/g,(call,name)=>FUTURE_FUNCTIONS.includes(name.toUpperCase())?`_xlfn.${name.toUpperCase()}(`:call)).join('');
// locate(ref) names the sheet a reference points to, without evaluating it (CELL needs only the address).
export function evaluateExpr(ast, resolve, resolveRange, locate) {
  const ev = n => evaluateExpr(n,resolve,resolveRange,locate);
  if (ast.type === 'literal') return ast.value;
  if (ast.type === 'ref') return resolve(ast);
  if (ast.type === 'range') { if (!resolveRange) throw Error('区域求值需要布局'); return resolveRange(ast.left,ast.right); }
  if (ast.type === 'unary') return ast.op === '-' ? -number(ev(ast.arg)) : number(ev(ast.arg));
  if (ast.type === 'binary') {
    let a = ev(ast.left), b = ev(ast.right);
    if (ast.op === '&') return excelString(a)+excelString(b);
    if (['=','<>','<','>','<=','>='].includes(ast.op)) {
      // A true blank compares equal to both numeric zero and the empty string.
      if (blank(a)) a = typeof b === 'number' ? 0 : '';
      if (blank(b)) b = typeof a === 'number' ? 0 : '';
      if (typeof a === 'string' && typeof b === 'string') { a=a.toLowerCase(); b=b.toLowerCase(); }
      const rank = x => typeof x === 'boolean' ? 2 : typeof x === 'string' ? 1 : 0;
      const cmp = typeof a !== typeof b ? Math.sign(rank(a)-rank(b)) : a === b ? 0 : a < b ? -1 : 1;
      return ({'=':cmp===0,'<>':cmp!==0,'<':cmp<0,'>':cmp>0,'<=':cmp<=0,'>=':cmp>=0})[ast.op];
    }
    if(a==='待指定'||b==='待指定')return '待指定';
    a=number(a); b=number(b);
    if (ast.op === '/' && b === 0) throw Error('公式除数为零');
    return ({'+':()=>a+b,'-':()=>a-b,'*':()=>a*b,'/':()=>a/b,'^':()=>a**b})[ast.op]();
  }
  const args = ast.args;
  if (ast.name === 'IF') return ev(args[0]) ? ev(args[1]) : args[2] ? ev(args[2]) : false;
  if (ast.name === 'CELL') {
    if (excelString(ev(args[0])).toLowerCase() !== 'filename' || args[1]?.type !== 'ref') throw Error('CELL 只支持 CELL("filename",引用)');
    if (!locate) throw Error('CELL 求值需要布局');
    // No file path is known to the model: "[]" stands in for Excel's "path\[book.xlsx]".
    return `[]${locate(args[1])}`;
  }
  const vs = args.map(ev), flat = vs.flat(Infinity), nums = flat.filter(v=>typeof v === 'number');
  switch (ast.name) {
    case 'AND': return flat.filter(v=>!blank(v) && typeof v !== 'string').every(Boolean);
    case 'OR': return flat.filter(v=>!blank(v) && typeof v !== 'string').some(Boolean);
    case 'NOT': return !vs[0];
    case 'ISNUMBER': return typeof vs[0] === 'number';
    case 'ISTEXT': return typeof vs[0] === 'string';
    case 'LEFT': {const count=args[1]?Math.trunc(number(vs[1])):1;if(count<0)throw Error('LEFT 字符数不能为负');return excelString(vs[0]).slice(0,count);}
    case 'TIME': {const parts=vs.map(v=>Math.trunc(number(v)));if(parts.some(v=>v<0))throw Error('TIME 参数不能为负');return (parts[0]*3600+parts[1]*60+parts[2])%86400/86400;}
    case 'CEILING': {const a=number(vs[0]),s=number(vs[1]);if(!s||!a)return 0;if(a>0&&s<0)throw Error('CEILING 符号不兼容');const q=a/s,nearest=Math.round(q);return (Math.abs(q-nearest)<=Number.EPSILON*Math.max(1,Math.abs(q))*2?nearest:Math.ceil(q))*s;}
    case 'INT': return Math.floor(number(vs[0]));
    case 'MOD': { const a=number(vs[0]), b=number(vs[1]); if (!b) throw Error('MOD 除数为零'); return a-b*Math.floor(a/b); }
    case 'ABS': return Math.abs(number(vs[0]));
    case 'ROUND': { const a=number(vs[0]), s=10**number(vs[1]); return Math.sign(a)*Math.round((Math.abs(a)+Number.EPSILON*Math.abs(a))*s)/s; }
    case 'MIN': return nums.length ? Math.min(...nums) : 0;
    case 'MAX': return nums.length ? Math.max(...nums) : 0;
    case 'SUM': return nums.reduce((a,b)=>a+b,0);
    case 'COUNT': return nums.length;
    case 'TEXT': return formatText(vs[0],vs[1],{excel:true});
    case 'EXP': return Math.exp(number(vs[0]));
    case 'SUBSTITUTE': {
      const text=excelString(vs[0]), old=excelString(vs[1]), next=excelString(vs[2]);
      if (!old) return text;
      if (args.length < 4) return text.split(old).join(next);
      const n=Math.trunc(number(vs[3])); if (n < 1) throw Error('SUBSTITUTE 序号必须为正');
      let at=-1; for (let k=0;k<n;k++) { at=text.indexOf(old,at+1); if (at<0) return text; }
      return text.slice(0,at)+next+text.slice(at+old.length);
    }
    case 'TEXTAFTER': {
      const text=excelString(vs[0]), delimiter=excelString(vs[1]), n=args.length>2&&!blank(vs[2])?Math.trunc(number(vs[2])):1;
      if ((args.length>3&&number(vs[3])) || (args.length>4&&number(vs[4]))) throw Error('TEXTAFTER 暂不支持 match_mode / match_end');
      if (!n) throw Error('TEXTAFTER 序号不能为 0');
      let at=-1;
      if (delimiter==='') at=n>0?0:text.length;
      else if (n>0) { for (let k=0,from=0;k<n;k++,from=at+delimiter.length) { at=text.indexOf(delimiter,from); if (at<0) break; } }
      else { for (let k=0,from=text.length;k<-n;k++,from=at-1) { at=from<0?-1:text.lastIndexOf(delimiter,from); if (at<0) break; } }
      if (at<0) { if (args.length>5) return vs[5]; throw Error('TEXTAFTER 找不到分隔符'); }
      return text.slice(at+delimiter.length);
    }
    default: throw Error(`不支持的函数：${ast.name}`);
  }
}
export const escapeTemplate = s => String(s).replaceAll('\\','\\\\').replaceAll('{','\\{').replaceAll('}','\\}');
export function parseTemplate(text) {
  const parts = []; let lit = '', i=0;
  const flush=()=>{ if(lit) parts.push({text:lit}); lit=''; };
  while (i<text.length) {
    if (text[i] === '\\' && /[{}\\]/.test(text[i+1] || ' ')) { lit+=text[i+1]; i+=2; continue; }
    if (text[i] !== '{') { lit+=text[i++]; continue; }
    flush(); let j=i+1, quoted=false, split=-1;
    for (;j<text.length;j++) {
      if (text[j] === '"') { if (quoted && text[j+1] === '"') { j++; continue; } quoted=!quoted; }
      if (!quoted && text[j] === '|') split=j;
      if (!quoted && text[j] === '}') break;
    }
    if (j===text.length) throw Error('模板缺少右花括号');
    parts.push({expr:parseExpr(text.slice(i+1,split<0?j:split)), ...(split<0?{}:{fmt:text.slice(split+1,j)})}); i=j+1;
  }
  flush(); return parts;
}
export function renderTemplate(text, resolve, range, locate) {
  return parseTemplate(text).map(p=>p.text ?? (p.fmt ? formatText(evaluateExpr(p.expr,resolve,range,locate),p.fmt) : excelString(evaluateExpr(p.expr,resolve,range,locate)))).join('');
}
export function templateToFormula(text, resolver) {
  const ps=parseTemplate(text); if (!ps.some(p=>p.expr)) return null;
  const formatted=p=>{
    const value=serializeExpr(p.expr,resolver),optional=/^(0|#,##0)\.(#+)( ?%)?$/.exec(p.fmt);
    const ordinary=`TEXT(${value},${quoteString(p.fmt)})`;
    // Excel TEXT(9,"0.###") retains a decimal point. Pick an integer format
    // after the same rounding, preserving both fractional values and precision.
    const display=optional?`IF(MOD(ROUND((${value})${optional[3]?'*100':''},${optional[2].length}),1)=0,TEXT(${value},${quoteString(optional[1]+(optional[3]??''))}),${ordinary})`:ordinary;
    return `IF(ISNUMBER(${value}),${display},${value})`;
  };
  const formula=ps.map(p=>p.text !== undefined ? quoteString(p.text) : p.fmt ? formatted(p) : p.expr.left && precedence[p.expr.op]<=2 ? `(${serializeExpr(p.expr,resolver)})` : serializeExpr(p.expr,resolver)).join('&');
  return ps.length===1 && ps[0].expr && !ps[0].fmt ? '""&'+formula : formula;
}
// loose: text blocks must become templates, so any other &-operand becomes an embedded {expression}.
export function formulaToTemplate(ast, {loose=false}={}) {
  // A bare reference can return a number or blank; a template always returns text.
  if(ast.type==='ref' && !loose) return null;
  const parts=[];
  function visit(n) {
    if (n.type === 'binary' && n.op === '&') return visit(n.left) && visit(n.right);
    if (n.type === 'literal' && typeof n.value === 'string') { parts.push(escapeTemplate(n.value)); return true; }
    if (n.type === 'ref') { parts.push(`{${serializeExpr(n)}}`); return true; }
    if (n.type === 'call' && n.name === 'TEXT' && n.args.length===2 && n.args[1].type==='literal' && typeof n.args[1].value==='string') {
      if(/^(0|#,##0)\.(#+)( ?%)?$/.test(n.args[1].value)){parts.push(`{${serializeExpr(n)}}`);return true;}
      parts.push(`{${serializeExpr(n.args[0])}|${n.args[1].value}}`); return true;
    }
    if (loose) { parts.push(`{${serializeExpr(n)}}`); return true; }
    return false;
  }
  return visit(ast) ? parts.join('') : null;
}
