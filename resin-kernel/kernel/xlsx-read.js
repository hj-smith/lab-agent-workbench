import {tags, unesc, textRuns, address, colName} from './xml.js';
import {parseA1Formula, mapRefs, serializeExpr} from './expr.js';
export const BUILTIN_FORMATS = {
  0:'General',1:'0',2:'0.00',3:'#,##0',4:'#,##0.00',
  5:'"$"#,##0_);("$"#,##0)',6:'"$"#,##0_);[Red]("$"#,##0)',7:'"$"#,##0.00_);("$"#,##0.00)',8:'"$"#,##0.00_);[Red]("$"#,##0.00)',
  9:'0%',10:'0.00%',11:'0.00E+00',12:'# ?/?',13:'# ??/??',14:'mm-dd-yy',15:'d-mmm-yy',16:'d-mmm',17:'mmm-yy',18:'h:mm AM/PM',19:'h:mm:ss AM/PM',20:'h:mm',21:'h:mm:ss',22:'m/d/yy h:mm',
  27:'[$-404]e/m/d',28:'[$-404]e"年"m"月"d"日"',29:'[$-404]e"年"m"月"d"日"',30:'m/d/yy',31:'yyyy"年"m"月"d"日"',32:'h"时"mm"分"',33:'h"时"mm"分"ss"秒"',34:'yyyy"年"m"月"',35:'m"月"d"日"',36:'[$-404]e/m/d',
  37:'#,##0 ;(#,##0)',38:'#,##0 ;[Red](#,##0)',39:'#,##0.00;(#,##0.00)',40:'#,##0.00;[Red](#,##0.00)',
  41:'_(* #,##0_);_(* (#,##0);_(* "-"_);_(@_)',42:'_("$"* #,##0_);_("$"* (#,##0);_("$"* "-"_);_(@_)',43:'_(* #,##0.00_);_(* (#,##0.00);_(* "-"??_);_(@_)',44:'_("$"* #,##0.00_);_("$"* (#,##0.00);_("$"* "-"??_);_(@_)',
  45:'mm:ss',46:'[h]:mm:ss',47:'mmss.0',48:'##0.0E+0',49:'@'
};
function shifted(formula, from, to) {
  const a=address(from), b=address(to);
  return serializeExpr(mapRefs(parseA1Formula(formula), r=>{
    const m=/^(\$?)([A-Z]+)(\$?)(\d+)$/.exec(r.addr), p=address(r.addr);
    return {...r,addr:`${m[1]}${colName(p.col+(m[1]?0:b.col-a.col))}${m[3]}${p.row+(m[3]?0:b.row-a.row)}`};
  }));
}
export async function readWorkbook(bytes, {allowErrors=false,allowDate1904=false}={}) {
  const zip=await globalThis.JSZip.loadAsync(bytes);
  const read=async p=> { const f=zip.file(p); if(!f) throw Error(`工作簿缺少 ${p}`); return f.async('string'); };
  const [book, rels, styles, strings]=await Promise.all([read('xl/workbook.xml'),read('xl/_rels/workbook.xml.rels'),zip.file('xl/styles.xml')?.async('string')??'',zip.file('xl/sharedStrings.xml')?.async('string')??'']);
  const date1904=tags(book,'workbookPr')[0]?.a.date1904 === '1';
  if (date1904&&!allowDate1904) throw Error('暂不支持 1904 日期系统');
  const shared=tags(strings,'si').map(x=>textRuns(x.body)), formats={...BUILTIN_FORMATS};
  for(const n of tags(styles,'numFmt')) formats[n.a.numFmtId]=n.a.formatCode;
  const xfs=tags(tags(styles,'cellXfs')[0]?.body??'','xf').map(x=>formats[x.a.numFmtId]??'General');
  const targets=Object.fromEntries(tags(rels,'Relationship').map(x=>[x.a.Id,x.a.Target]));
  const sheets=[];
  for(const sh of tags(book,'sheet')) {
    const target=targets[sh.a['r:id']], path=target.startsWith('/')?target.slice(1):'xl/'+target.replace(/^\.\//,'');
    const xml=await read(path), merges=tags(xml,'mergeCell').map(x=>x.a.ref), widths=[], cells=[], sharedFormulas=new Map();
    for(const c of tags(xml,'col')) for(let n=+c.a.min;n<=+c.a.max;n++) widths[n-1]=Number(c.a.width??8.43);
    for(const c of tags(xml,'c')) {
      const type=c.a.t??'n', raw=tags(c.body,'v')[0]?.body, f=tags(c.body,'f')[0]; let value=null;
      if(type==='inlineStr') value=textRuns(tags(c.body,'is')[0]?.body??'');
      // Some producers omit <v> for an empty cached string, including formula cells.
      else if(type==='s') value=raw===undefined||raw===''?'':shared[Number(raw)];
      else if(type==='str') value=unesc(raw??'');
      else if(type==='b') value=raw==='1';
      else if(type==='e') {if(!allowErrors)throw Error(`源工作簿公式错误：${sh.a.name}!${c.a.r} ${unesc(raw??'')}`);value=unesc(raw??'');}
      else if(raw!==undefined && raw!=='') value=Number(raw);
      let formula;
      if(f) { formula=unesc(f.body).replace(/^=/,''); if(f.a.t==='shared' && formula) sharedFormulas.set(f.a.si,{formula,addr:c.a.r}); }
      cells.push({addr:c.a.r,value, ...(f?{formula}:{}),numFmt:xfs[c.a.s??0]??'General',type,...(f?.a.t==='shared'?{sharedIndex:f.a.si}:{})});
    }
    for(const c of cells) if(c.sharedIndex!==undefined) {
      if(!c.formula) { const master=sharedFormulas.get(c.sharedIndex); if(!master) throw Error('共享公式缺少主公式'); c.formula=shifted(master.formula,master.addr,c.addr); } delete c.sharedIndex;
    }
    const maxCol=Math.max(widths.length,...cells.map(c=>address(c.addr).col),...merges.map(m=>address(m.split(':')[1]).col),1);
    for(let i=0;i<maxCol;i++) widths[i]??=8.43;
    const setup=tags(xml,'pageSetup')[0]?.a??{};
    sheets.push({name:sh.a.name,state:sh.a.state??'visible',cells,merges,widths,orientation:setup.orientation??'portrait',fitToWidth:Number(setup.fitToWidth??1),fitToHeight:Number(setup.fitToHeight??1),path});
  }
  return {sheets,date1904};
}
