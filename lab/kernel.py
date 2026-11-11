"""内核的账本：事件 → 事项 → 一轮 → 回执。做法照 agent-autoloop，代码自己写（Python，单进程）。

    事件 event   发生了一件事：来源（人、文件夹、定时、复查、卡片回答、后台任务）、类型、对象、一句话、详情。
    事项 lane    同一个对象的事归在一起（一段对话、一个课题、一个文件夹、一条定时规则）。卡片、复查、对话历史都挂在事项上。
    一轮 run     处理一批事件，交一份回执，然后结束。同一个事项一次只跑一轮；排队时来的新事件并进同一轮。
    回执         这一轮留下的：卡片（问你）、复查（约时间回来）、产出（文件挂到记录上）、通知、新事件。

账本：每件事记一条事实（只追加）。事项现在的样子（消息、卡片、复查、状态、用量）由事实折叠出来（evolve），
另有几张清单表（lanes、runs、timers、events）跟着事实在同一个事务里更新，丢了可以用 rebuild() 从账本重建。
流式出来的字不进账本，只在内存里给页面看；一次模型回答完整结束后才记一条事实。
"""
import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from . import config

BEIJING = timezone(timedelta(hours=8))
SCHEMA = '''
CREATE TABLE IF NOT EXISTS facts(seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, lane TEXT NOT NULL, type TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS facts_lane ON facts(lane, seq);
CREATE TABLE IF NOT EXISTS lanes(id TEXT PRIMARY KEY, title TEXT, rule TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '',
    library TEXT NOT NULL DEFAULT '', anchor TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'idle', unread INTEGER NOT NULL DEFAULT 0,
    card TEXT, cost REAL NOT NULL DEFAULT 0, created TEXT, updated TEXT, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, lane TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
    not_before REAL NOT NULL DEFAULT 0, events TEXT NOT NULL DEFAULT '[]', created TEXT, started TEXT, ended TEXT, result TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS runs_lane ON runs(lane, status);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, at TEXT NOT NULL, source TEXT NOT NULL, type TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}', lane TEXT NOT NULL DEFAULT '', run TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS timers(id TEXT PRIMARY KEY, lane TEXT NOT NULL, due REAL NOT NULL, about TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schedules(rule TEXT PRIMARY KEY, next_due REAL NOT NULL, last REAL);
CREATE TABLE IF NOT EXISTS demos(id TEXT PRIMARY KEY, at TEXT NOT NULL, library TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '',
  before TEXT NOT NULL DEFAULT '{}', after TEXT NOT NULL DEFAULT '{}', context TEXT NOT NULL DEFAULT '{}', used TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS intake(id TEXT PRIMARY KEY, at TEXT NOT NULL, library TEXT NOT NULL, path TEXT NOT NULL, file TEXT NOT NULL, source TEXT NOT NULL,
  status TEXT NOT NULL, items TEXT NOT NULL DEFAULT '[]', results TEXT NOT NULL DEFAULT '[]', lane TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', done_at TEXT);
'''
_lock = threading.RLock()


def now():
    return datetime.now(BEIJING).strftime('%Y-%m-%d %H:%M:%S')


def new_id(prefix):
    return f'{prefix}-' + datetime.now(BEIJING).strftime('%m%d%H%M%S') + '-' + uuid.uuid4().hex[:4]


_local = threading.local()


def connect():
    """每个线程留一个连接反复用；表结构只在第一次建。"""
    db = getattr(_local, 'db', None)
    if db is None:
        config.HOME.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(config.HOME / '内核.db', timeout=30, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)
        columns = {row[1] for row in db.execute('PRAGMA table_info(lanes)')}
        if 'plan' not in columns:                   # 老库补一列：这段对话的计划清单
            db.execute('ALTER TABLE lanes ADD COLUMN plan TEXT')
        if 'owner' not in columns:                  # 老库补一列：这段对话是谁的（空 = 管理员或系统）
            db.execute("ALTER TABLE lanes ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
        _local.db = db
    return db


# ---------- 折叠：一个事项现在的样子 ----------

def blank(lane_id):
    return {'id': lane_id, 'title': '新对话', 'rule': '', 'subject': '', 'library': '', 'anchor': '', 'owner': '', 'created': '', 'updated': '',
            'messages': [], 'summary': '', 'context_from': 0, 'last_input': 0, 'card': None, 'timers': [], 'outputs': [], 'plan': [],
            'status': 'idle', 'unread': False, 'seq': 0,
            'usage': {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'calls': 0, 'cost': 0, 'unpriced': 0}}


def _spend(state, usage, cost):
    total = state['usage']
    for key in ('input', 'output', 'cache_read', 'cache_write'):
        total[key] += usage.get(key, 0)
    total['calls'] += 1
    if cost is None:
        total['unpriced'] += 1
    else:
        total['cost'] = round(total['cost'] + cost, 6)


def evolve(state, kind, data, at=''):
    """把一条事实折进事项的状态。唯一改状态的地方。"""
    state['updated'] = at or state['updated']
    state['created'] = state['created'] or at
    if kind == 'lane':
        state.update({key: data[key] for key in ('title', 'rule', 'subject', 'library', 'anchor', 'owner') if key in data})
    elif kind == 'input':                       # 一轮开始时交给模型的输入：人说的话，或几条事件
        if state['title'] == '新对话' and data.get('text') and data.get('source') == '人':
            state['title'] = (data['text'].strip().splitlines() or ['新对话'])[0][:40]
        state['messages'].append({'role': 'user', 'time': at, **data})
        state['status'] = 'running'
    elif kind == 'assistant':
        state['messages'].append({'role': 'assistant', 'time': at, **{key: value for key, value in data.items() if key not in ('cost', 'last_input')}})
        state['last_input'] = data.get('last_input', state['last_input'])
        _spend(state, data.get('usage') or {}, data.get('cost'))
    elif kind == 'tools':
        state['messages'].append({'role': 'tool', 'results': data['results']})
    elif kind == 'note':
        state['messages'].append({'role': data['role'], 'text': data['text'], 'time': at, **({'ref': data['ref']} if data.get('ref') else {})})
    elif kind == 'compact':
        state['summary'], state['context_from'], state['last_input'] = data['summary'], data['upto'], 0
    elif kind == 'usage':
        _spend(state, data.get('usage') or {}, data.get('cost'))
    elif kind == 'card':
        state['card'] = data
    elif kind == 'answer':
        state['card'] = None
    elif kind == 'closed':
        state['card'], state['status'] = None, 'done'
    elif kind == 'plan':
        state['plan'] = data['steps']
    elif kind == 'timer':
        state['timers'].append(data)
    elif kind == 'timer_done':
        state['timers'] = [item for item in state['timers'] if item['id'] != data['id']]
    elif kind == 'output':
        state['outputs'].append({**data, 'time': at})
    elif kind == 'rewind':                      # 从某句话重来：这句和后面的收起来（数据上的改动不跟着撤）
        cut = int(data['to'])
        state['messages'] = state['messages'][:cut]
        if state['context_from'] > cut:
            state['summary'], state['context_from'] = '', 0
        state['card'], state['plan'], state['last_input'], state['status'] = None, [], 0, 'done'
    elif kind == 'seed':                        # 分叉出来的新对话：带上原对话到分叉点为止的内容
        state['messages'] = list(data['messages'])
        state['summary'], state['context_from'] = data.get('summary') or '', int(data.get('context_from') or 0)
    elif kind == 'ended':
        state['status'], state['unread'] = data['status'], True
    elif kind == 'seen':
        state['unread'] = False
    return state


def _project(db, lane, kind, data, at):
    """清单表跟着事实一起改。"""
    db.execute('INSERT OR IGNORE INTO lanes(id, title, created, updated) VALUES(?,?,?,?)', (lane, '新对话', at, at))
    db.execute('UPDATE lanes SET updated=? WHERE id=?', (at, lane))
    if kind == 'lane':
        for key in ('title', 'rule', 'subject', 'library', 'anchor', 'owner'):
            if key in data:
                db.execute(f'UPDATE lanes SET {key}=? WHERE id=?', (data[key], lane))
    elif kind == 'input':
        if data.get('source') == '人' and data.get('text'):
            db.execute("UPDATE lanes SET title=? WHERE id=? AND title='新对话'", ((data['text'].strip().splitlines() or ['新对话'])[0][:40], lane))
        db.execute("UPDATE lanes SET status='running' WHERE id=?", (lane,))
    elif kind in ('assistant', 'usage') and data.get('cost'):
        db.execute('UPDATE lanes SET cost=cost+? WHERE id=?', (data['cost'], lane))
    elif kind == 'card':
        db.execute('UPDATE lanes SET card=? WHERE id=?', (json.dumps(data, ensure_ascii=False), lane))
    elif kind == 'answer':
        db.execute('UPDATE lanes SET card=NULL WHERE id=?', (lane,))
    elif kind == 'closed':
        db.execute("UPDATE lanes SET card=NULL, status='done' WHERE id=?", (lane,))
    elif kind == 'rewind':
        db.execute("UPDATE lanes SET card=NULL, plan=NULL, status='done' WHERE id=?", (lane,))
    elif kind == 'plan':
        db.execute('UPDATE lanes SET plan=? WHERE id=?', (json.dumps(data['steps'], ensure_ascii=False), lane))
    elif kind == 'timer':
        db.execute('INSERT OR REPLACE INTO timers(id, lane, due, about) VALUES(?,?,?,?)', (data['id'], lane, data['due'], data['about']))
    elif kind == 'timer_done':
        db.execute('DELETE FROM timers WHERE id=?', (data['id'],))
    elif kind == 'ended':
        db.execute('UPDATE lanes SET status=?, unread=1 WHERE id=?', (data['status'], lane))
    elif kind == 'seen':
        db.execute('UPDATE lanes SET unread=0 WHERE id=?', (lane,))
    elif kind in ('deleted', 'restored'):
        db.execute('UPDATE lanes SET deleted_at=? WHERE id=?', (at if kind == 'deleted' else None, lane))
        if kind == 'deleted':
            db.execute('DELETE FROM timers WHERE lane=?', (lane,))


def append(lane, kind, data):
    """记一条事实，返回 (seq, at)。事实和清单表在同一个事务里。"""
    at = now()
    with _lock, connect() as db:
        seq = db.execute('INSERT INTO facts(at, lane, type, data) VALUES(?,?,?,?)', (at, lane, kind, json.dumps(data, ensure_ascii=False))).lastrowid
        _project(db, lane, kind, data, at)
    return seq, at


def fold(lane):
    state = blank(lane)
    with connect() as db:
        for row in db.execute('SELECT seq, at, type, data FROM facts WHERE lane=? ORDER BY seq', (lane,)):
            evolve(state, row['type'], json.loads(row['data']), row['at'])
            state['seq'] = row['seq']
    if state['status'] == 'running':            # 上次跑到一半程序停了：这一轮算被打断，历史都在，可以接着做
        state['status'] = 'stopped'
    return state


def timeline(lane, after=0):
    with connect() as db:
        return [{'seq': row['seq'], 'at': row['at'], 'type': row['type'], 'data': json.loads(row['data'])}
                for row in db.execute('SELECT * FROM facts WHERE lane=? AND seq>? ORDER BY seq', (lane, after))]


def rebuild():
    """清单表丢了或格式变了：清空，从账本重新算。"""
    with _lock, connect() as db:
        for table in ('lanes', 'timers'):
            db.execute(f'DELETE FROM {table}')
        for row in db.execute('SELECT at, lane, type, data FROM facts ORDER BY seq').fetchall():
            _project(db, row['lane'], row['type'], json.loads(row['data']), row['at'])
        db.execute("UPDATE lanes SET status='stopped' WHERE status='running'")


# ---------- 清单 ----------

def lanes(include_rules=True):
    with connect() as db:
        rows = db.execute('SELECT * FROM lanes WHERE deleted_at IS NULL ORDER BY updated DESC').fetchall()
    return [{**dict(row), 'card': json.loads(row['card']) if row['card'] else None, 'plan': json.loads(row['plan']) if row['plan'] else [],
             'unread': bool(row['unread'])}
            for row in rows if include_rules or not row['rule']]


def timers():
    with connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM timers ORDER BY due')]


# ---------- 事件和一轮 ----------

def record_event(source, kind, subject='', summary='', detail=None, lane=''):
    event = {'id': new_id('e'), 'at': now(), 'source': source, 'type': kind, 'subject': subject, 'summary': summary,
             'detail': detail or {}, 'lane': lane}
    with _lock, connect() as db:
        db.execute('INSERT INTO events(id, at, source, type, subject, summary, detail, lane) VALUES(?,?,?,?,?,?,?,?)',
                   (event['id'], event['at'], source, kind, subject, summary, json.dumps(detail or {}, ensure_ascii=False), lane))
    return event


def queue(lane, event):
    """把事件交给事项：有排队的一轮就并进去，没有就新开一轮。返回这一轮的 id。"""
    with _lock, connect() as db:
        row = db.execute("SELECT * FROM runs WHERE lane=? AND status='queued' ORDER BY created LIMIT 1", (lane,)).fetchone()
        if row:
            merged = json.loads(row['events']) + [event]
            # 人又说话了：等着几分钟后重试的那一轮马上跑（多半是他刚改好设置，或者就是想要回复），不让他干等
            soon = 0 if event.get('source') == '人' else row['not_before']
            db.execute('UPDATE runs SET events=?, not_before=? WHERE id=?', (json.dumps(merged, ensure_ascii=False), soon, row['id']))
            run_id = row['id']
        else:
            run_id = new_id('run')
            db.execute('INSERT INTO runs(id, lane, status, events, created) VALUES(?,?,?,?,?)',
                       (run_id, lane, 'queued', json.dumps([event], ensure_ascii=False), now()))
        db.execute('UPDATE events SET lane=?, run=? WHERE id=?', (lane, run_id, event['id']))
    return run_id


def take_queued(lane, source='人'):
    """助手正在干活时他又说的话：从这段对话排队的那一轮里拿出来（不等这一轮结束，下一步就接进去）。拿空了，那一轮就不用再跑。"""
    with _lock, connect() as db:
        row = db.execute("SELECT * FROM runs WHERE lane=? AND status='queued' ORDER BY created LIMIT 1", (lane,)).fetchone()
        if not row:
            return []
        events = json.loads(row['events'])
        taken = [event for event in events if event.get('source') == source and event.get('type') == 'message']
        if not taken:
            return []
        rest = [event for event in events if event not in taken]
        if rest:
            db.execute('UPDATE runs SET events=? WHERE id=?', (json.dumps(rest, ensure_ascii=False), row['id']))
        else:
            db.execute("UPDATE runs SET status='merged', ended=?, result='并进了正在跑的一轮' WHERE id=?", (now(), row['id']))
    return taken


def claim(limit=2):
    """领下一轮：排队中、到了时间、它的事项没有正在跑的；同时最多跑 limit 轮。"""
    with _lock, connect() as db:
        running = db.execute("SELECT count(*) FROM runs WHERE status='running'").fetchone()[0]
        if running >= limit:
            return None
        busy = {row[0] for row in db.execute("SELECT lane FROM runs WHERE status='running'")}
        for row in db.execute("SELECT * FROM runs WHERE status='queued' AND not_before<=? ORDER BY created", (time.time(),)):
            if row['lane'] in busy:
                continue
            db.execute("UPDATE runs SET status='running', started=?, attempts=attempts+1 WHERE id=?", (now(), row['id']))
            return {**dict(row), 'events': json.loads(row['events']), 'attempts': row['attempts'] + 1}
    return None


def finish(run_id, status, result=''):
    """一轮结束。failed 的按 5、10、20 分钟……（最长 1 小时）重新排队，第 10 次停下。"""
    with _lock, connect() as db:
        row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if status == 'failed' and row['attempts'] < 10:
            wait = min(300 * 2 ** (row['attempts'] - 1), 3600)
            db.execute("UPDATE runs SET status='queued', not_before=?, result=? WHERE id=?", (time.time() + wait, result, run_id))
            return wait
        db.execute('UPDATE runs SET status=?, ended=?, result=? WHERE id=?', (status, now(), result, run_id))
    return None


def runs(limit=100, lane=None):
    with connect() as db:
        rows = db.execute('SELECT * FROM runs' + (' WHERE lane=?' if lane else '') + ' ORDER BY created DESC LIMIT ?',
                          (lane, limit) if lane else (limit,)).fetchall()
    return [{**dict(row), 'events': json.loads(row['events'])} for row in rows]


def recover():
    """程序重启：上次没跑完的一轮放回队列（账本里做完的不会重做）。"""
    with _lock, connect() as db:
        db.execute("UPDATE runs SET status='queued', not_before=0 WHERE status='running'")
        db.execute("UPDATE lanes SET status='stopped' WHERE status='running'")


def next_due():
    """最早该醒的时间：复查、定时、等着重试的一轮。"""
    with connect() as db:
        values = [row[0] for row in db.execute(
            "SELECT min(due) FROM timers UNION ALL SELECT min(next_due) FROM schedules UNION ALL "
            "SELECT min(not_before) FROM runs WHERE status='queued' AND not_before>0") if row[0] is not None]
    return min(values) if values else None
