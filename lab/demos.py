"""跟你学：他在平台上做的判断都记下来当"示范"，攒够了让助手归纳成做法，他同意了才写进技能。

只记学得到东西的判断（改前、改后、当时的情况），点击、翻页不记；学完的示范标"已学"，可以清掉。不是审计记录。
记在 内核.db 的 demos 表。哪些地方记（都只在人从网页上做的时候）：
- GPC 基线和区间：拖锚点、改区间、换基线连接方式，存下来的那一刻
- 椭偏剔点、椭偏参比：剔掉 / 放回读数，换参比
- n/k 纳入：勾掉 / 勾上"纳入均值""计入重复性"
- 配方修改：组成编辑器里存的改动
- 改记录、确认待确认：详细信息里改了值，或去掉"待确认"
他回答卡片定的事另有"定过的事"（lab/decide.py），这里不重复记。
"""
import json
import uuid

from . import kernel

KINDS = ['GPC 基线和区间', '椭偏剔点', '椭偏参比', 'n/k 纳入', '配方修改', '改记录', '确认待确认']


def note(kind, library, ref, name, before, after, context=None):
    """记一次示范。前后一样（没改）就不记。"""
    if json.dumps(before, sort_keys=True, default=str) == json.dumps(after, sort_keys=True, default=str):
        return None
    demo_id = 'd-' + uuid.uuid4().hex[:10]
    with kernel.connect() as db:
        db.execute('INSERT INTO demos(id, at, library, kind, ref, name, before, after, context) VALUES(?,?,?,?,?,?,?,?,?)',
                   (demo_id, kernel.now(), library, kind, ref, name, json.dumps(before, ensure_ascii=False, default=str),
                    json.dumps(after, ensure_ascii=False, default=str), json.dumps(context or {}, ensure_ascii=False, default=str)))
    return demo_id


def _row(row):
    item = dict(row)
    for key in ('before', 'after', 'context'):
        item[key] = json.loads(item[key] or '{}')
    item['text'] = describe(item) + (f'（为什么：{item["context"]["为什么"]}）' if item['context'].get('为什么') else '')
    return item


def rows(library, kind=None, limit=200, fresh_only=False):
    sql = 'SELECT * FROM demos WHERE library=?' + (' AND kind=?' if kind else '') + (" AND used=''" if fresh_only else '') + ' ORDER BY at DESC LIMIT ?'
    with kernel.connect() as db:
        return [_row(row) for row in db.execute(sql, (library, kind, limit) if kind else (library, limit))]


def summary(library):
    """每类：一共几次、还没学的几次、最近一次、最近三条的人话。"""
    with kernel.connect() as db:
        counts = {row[0]: (row[1], row[2], row[3]) for row in db.execute(
            "SELECT kind, count(*), sum(used=''), max(at) FROM demos WHERE library=? GROUP BY kind", (library,))}
    return [{'kind': kind, 'total': counts.get(kind, (0, 0, ''))[0], 'fresh': counts.get(kind, (0, 0, ''))[1] or 0,
             'last': counts.get(kind, (0, 0, ''))[2], 'recent': [item['text'] for item in rows(library, kind, 3)]} for kind in KINDS]


def why(demo_id, text):
    """给一条示范补一句"为什么这样改"（写进当时的情况里）。"""
    with kernel.connect() as db:
        row = db.execute('SELECT context FROM demos WHERE id=?', (demo_id,)).fetchone()
        if not row:
            return False
        context = {**json.loads(row[0] or '{}'), '为什么': text.strip()}
        db.execute('UPDATE demos SET context=? WHERE id=?', (json.dumps(context, ensure_ascii=False), demo_id))
    return True


def mark_used(ids, skill):
    with kernel.connect() as db:
        db.executemany('UPDATE demos SET used=? WHERE id=?', [(skill, demo_id) for demo_id in ids])
    return len(ids)


def _num(value, digits=2):
    return f'{value:.{digits}f}' if isinstance(value, (int, float)) else '—'


def describe(item):
    """一条示范写成一句人话（页面上看的）。"""
    b, a, c, kind, name = item['before'], item['after'], item['context'], item['kind'], item['name']
    if kind == 'GPC 基线和区间':
        parts = []
        ba, aa = b.get('基线锚点') or [], a.get('基线锚点') or []
        if len(ba) != len(aa):
            parts.append(f'锚点 {len(ba)} → {len(aa)} 个')
        if ba and aa and ba[-1][0] != aa[-1][0]:
            parts.append(f'最右锚点 {_num(ba[-1][0])} → {_num(aa[-1][0])} min')
        if ba and aa and ba[0][0] != aa[0][0]:
            parts.append(f'最左锚点 {_num(ba[0][0])} → {_num(aa[0][0])} min')
        span = lambda regions: '、'.join(f'{_num(r.get("起"))}–{_num(r.get("止"))}' for r in regions or [])
        if span(b.get('积分区间')) != span(a.get('积分区间')):
            parts.append(f'区间 {span(b.get("积分区间")) or "无"} → {span(a.get("积分区间"))} min')
        if b.get('基线方法') != a.get('基线方法') and a.get('基线方法'):
            from .present import word
            parts.append(f'基线连接 {word(b.get("基线方法")) or "默认"} → {word(a.get("基线方法"))}')
        if c.get('Mw 之前') != c.get('Mw 之后') and c.get('Mw 之后'):
            parts.append(f'Mw {_num(c.get("Mw 之前"), 0)} → {_num(c.get("Mw 之后"), 0)}')
        return f'{name}：' + ('；'.join(parts) or '调了基线')
    if kind == '椭偏剔点':
        return f'{name}：剔掉的读数 {len(b.get("剔掉") or [])} → {len(a.get("剔掉") or [])} 个' + (f'（{c["原因"]}）' if c.get('原因') else '')
    if kind == '椭偏参比':
        return f'{name}：参比 {b.get("参比") or "无"} → {a.get("参比") or "无"}'
    if kind == 'n/k 纳入':
        word = lambda v: '是' if v is True else '否' if v is False else str(v)
        return f'{name}：{c.get("字段", "")} {word(b.get("值"))} → {word(a.get("值"))}'
    if kind == '确认待确认':
        return f'{name}：确认了 {"、".join(c.get("确认") or [])}'
    changed = [key for key in a if a.get(key) != b.get(key)]
    shown = '；'.join(f'{key} {str(b.get(key))[:20] if b.get(key) not in (None, "") else "空"} → {str(a.get(key))[:20]}' for key in changed[:4])
    return f'{name}：{shown or "改了"}' + (f' 等 {len(changed)} 处' if len(changed) > 4 else '')
