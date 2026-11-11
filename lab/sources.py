"""把外面的世界变成事件：定时、文件夹里出现新文件。规则写在 data\\rules.json。

定时的写法（北京时间，错过的只补最近一次）：
    "every": "30m" / "2h" / "1d"           每隔多久
    "every": "daily 08:30"                 每天几点
    "every": "weekly mon 09:00"            每周几几点（mon tue wed thu fri sat sun，也认 周一…周日）
    "every": "at 2026-11-12 09:00"         某个时刻一次
文件夹：{"source": "文件夹", "folder": "D:/仪器导出/GPC", "pattern": "*.csv"}，新文件或改过的文件各发一个事件。
"""
import fnmatch
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import config, kernel

DAYS = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6,
        '周一': 0, '周二': 1, '周三': 2, '周四': 3, '周五': 4, '周六': 5, '周日': 6, '周天': 6}


def next_time(spec, after):
    """spec 之后的下一个时刻（秒）。不认识的写法返回 None。"""
    spec = spec.strip().lower()
    base = datetime.fromtimestamp(after, kernel.BEIJING)
    unit = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(m|min|h|d)', spec)
    if unit:
        return after + float(unit[1]) * {'m': 60, 'min': 60, 'h': 3600, 'd': 86400}[unit[2]]
    daily = re.fullmatch(r'daily\s+(\d{1,2}):(\d{2})', spec)
    if daily:
        when = base.replace(hour=int(daily[1]), minute=int(daily[2]), second=0, microsecond=0)
        return (when if when.timestamp() > after else when + timedelta(days=1)).timestamp()
    weekly = re.fullmatch(r'weekly\s+(\S+)\s+(\d{1,2}):(\d{2})', spec)
    if weekly and weekly[1] in DAYS:
        when = base.replace(hour=int(weekly[2]), minute=int(weekly[3]), second=0, microsecond=0) + timedelta(days=(DAYS[weekly[1]] - base.weekday()) % 7)
        return (when if when.timestamp() > after else when + timedelta(days=7)).timestamp()
    once = re.fullmatch(r'at\s+(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})', spec)
    if once:
        when = datetime.strptime(once[1], '%Y-%m-%d %H:%M').replace(tzinfo=kernel.BEIJING).timestamp()
        return when if when > after else None
    return None


def fire_schedules(rules):
    """到点的定时规则发一个事件（错过好几次也只补最近一次），再排下一次。"""
    from .loop import rules as rulebook
    now = time.time()
    with kernel.connect() as db:
        known = {row['rule']: row for row in db.execute('SELECT * FROM schedules')}
        for rule in rules:
            spec = (rule.get('when') or {}).get('every')
            if not rule.get('enabled', True) or (rule.get('when') or {}).get('source') != '定时' or not spec:
                continue
            row = known.get(rule['name'])
            if row is None:
                due = next_time(spec, now)
                if due:
                    db.execute('INSERT OR REPLACE INTO schedules(rule, next_due, last) VALUES(?,?,NULL)', (rule['name'], due))
                continue
            if row['next_due'] > now:
                continue
            event = kernel.record_event('定时', 'tick', rule['name'], f'定时「{rule["name"]}」到点', {'every': spec})
            for library in rulebook.libraries(rule):
                kernel.queue(rulebook.lane(rule, library), event)
            due = next_time(spec, now)
            if due:
                db.execute('UPDATE schedules SET next_due=?, last=? WHERE rule=?', (due, now, rule['name']))
            else:
                db.execute('DELETE FROM schedules WHERE rule=?', (rule['name'],))
        for name in set(known) - {rule['name'] for rule in rules}:
            db.execute('DELETE FROM schedules WHERE rule=?', (name,))


def _seen_path():
    return config.HOME / '文件夹来源.json'


def scan_folders(rules):
    """文件夹规则：看过的文件记在 data\\文件夹来源.json（路径 → 修改时间）；第一次看一个文件夹只记下现有文件，不发事件。"""
    from .loop import rules as rulebook
    from . import intake
    seen = json.loads(_seen_path().read_text(encoding='utf-8')) if _seen_path().is_file() else {}
    changed = False
    for rule in rules:
        when = rule.get('when') or {}
        if not rule.get('enabled', True) or when.get('source') != '文件夹' or not when.get('folder'):
            continue
        folder = Path(when['folder'])
        if not folder.is_dir():
            continue
        key = f'{rule["name"]}|{folder}'
        first = key not in seen and not when.get('backfill')          # 第一次看：只记下现有文件；写了"已有文件也导入"就照样导入
        changed = changed or key not in seen                          # 空文件夹第一次看也要记下"看过了"，不然下次还当第一次
        known = seen.setdefault(key, {})
        arrived = []
        for path in folder.rglob('*') if when.get('recursive') else folder.iterdir():
            if not path.is_file() or not fnmatch.fnmatch(path.name.lower(), (when.get('pattern') or '*').lower()):
                continue
            stamp = path.stat().st_mtime
            if known.get(str(path)) == stamp:
                continue
            known[str(path)] = stamp
            changed = True
            if first:
                continue
            if when.get('do') == '导入':                              # 自动导入：认文件、能入库的直接入库，其它进队列或交给助手
                arrived.append(path)
                continue
            lane = rulebook.lane(rule)
            event = kernel.record_event('文件夹', 'file.new', str(path), f'{folder.name} 里有新文件：{path.name}',
                                        {'path': str(path), 'size': path.stat().st_size})
            kernel.queue(lane, event)
        if arrived:
            intake.arrive_later(rule, arrived)
    if changed:
        _seen_path().write_text(json.dumps(seen, ensure_ascii=False), encoding='utf-8')
