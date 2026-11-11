"""研发库：一个产品库一个 SQLite 文件，任何种类的实验、配方、样品、测试、文献都放得下，不用改程序。

    类型 kinds      一种记录是什么、有哪些字段（单位、说明）、归在哪一组。人和智能体都能新建、加字段。
    记录 records    一条数据。字段的值放在 data（JSON）里；不在类型里的字段照收，并自动补进类型。
    曲线 series     挂在记录上的曲线或明细表（色谱、n/k 谱、UV 谱、时间序列、称量明细）：列名 + 行。
    结果 results    算法算出来的结果，记着用的哪个算法、哪个版本、哪些参数。参数变了就重算覆盖。
    关联 links      记录之间的关系，记着是谁定的（人 / 规则 / 智能体 / 迁移）、确认了没有。
    文件 files      原始文件：复制进本库的"原始文件"文件夹，原样存放，记指纹和来处。
    设置 settings   原来写死在代码里的业务规矩（命名规则、默认参比、容差……），能改。

每种类型自动有一张同名视图（字段摊成列），可以直接 SQL：SELECT name, 接触角 FROM "接触角测试"。
你自己从图片、手写、口述里读出来的数，把字段名记进 unconfirmed，显示时标"待确认"。删除进回收站（deleted_at）。

用法：
    from lab import store
    lib = store.open('产品A')
    lib.define('接触角测试', '水接触角，静态，3 次平均', [{'key': '接触角', 'unit': '°'}])
    lib.save('接触角测试', [{'name': 'CA-001', '接触角': 72.4, 'unconfirmed': ['接触角']}])
"""
import hashlib
import json
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

BEIJING = timezone(timedelta(hours=8))
RESERVED = {'id', 'name', 'kind', 'note', 'source', 'unconfirmed', 'series', 'links', 'data', 'files'}
TABLES = {'kinds', 'records', 'series', 'results', 'links', 'files', 'file_records', 'settings', 'meta'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS kinds(name TEXT PRIMARY KEY, about TEXT NOT NULL DEFAULT '', fields TEXT NOT NULL DEFAULT '[]',
    grp TEXT NOT NULL DEFAULT '', sort INTEGER NOT NULL DEFAULT 0, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}',
    unconfirmed TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    created_at TEXT, updated_at TEXT, deleted_at TEXT);
CREATE INDEX IF NOT EXISTS records_kind_name ON records(kind, name);
CREATE INDEX IF NOT EXISTS records_name ON records(name);
CREATE TABLE IF NOT EXISTS series(record_id TEXT NOT NULL, name TEXT NOT NULL, columns TEXT NOT NULL, rows TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(record_id, name));
CREATE TABLE IF NOT EXISTS results(record_id TEXT NOT NULL, method TEXT NOT NULL, version TEXT NOT NULL DEFAULT '',
    params TEXT NOT NULL DEFAULT '{}', value TEXT NOT NULL, computed_at TEXT, PRIMARY KEY(record_id, method));
CREATE TABLE IF NOT EXISTS links(from_id TEXT NOT NULL, relation TEXT NOT NULL, to_id TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
    by TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'confirmed', created_at TEXT, PRIMARY KEY(from_id, relation, to_id));
CREATE INDEX IF NOT EXISTS links_to ON links(to_id);
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, path TEXT NOT NULL, origin TEXT NOT NULL DEFAULT '', size INTEGER,
    kind TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', imported_at TEXT);
CREATE TABLE IF NOT EXISTS file_records(file_id TEXT NOT NULL, record_id TEXT NOT NULL, PRIMARY KEY(file_id, record_id));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, about TEXT NOT NULL DEFAULT '', updated_at TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
'''


class StoreError(Exception):
    """说给模型和人听的错误：读了就知道怎么改。"""


def now():
    return datetime.now(BEIJING).strftime('%Y-%m-%d %H:%M:%S')


def _dump(value):
    return json.dumps(value, ensure_ascii=False)


def _quote(name):
    return '"' + str(name).replace('"', '""') + '"'


class Library:
    def __init__(self, name):
        self.name = name
        self.folder = config.library_dir(name)
        self.path = self.folder / '库.db'
        self._local = threading.local()
        with self.connect() as db:
            db.executescript(SCHEMA)

    def connect(self):
        """每个线程留一个连接反复用（这台机器上打开一次要 0.1 秒，每次查都开会很慢）。"""
        db = getattr(self._local, 'db', None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=60)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            self._local.db = db
        return db

    def reader(self):
        """只读连接，给 SQL 查询用（同样每个线程留一个）。"""
        db = getattr(self._local, 'ro', None)
        if db is None:
            db = sqlite3.connect(f'file:{self.path.as_posix()}?mode=ro', uri=True, timeout=60)
            db.row_factory = sqlite3.Row
            self._local.ro = db
        return db

    # ----- 类型 -----

    def _view(self, db, kind, fields):
        title = kind if kind not in TABLES else kind + '_'
        columns = ''.join(', json_extract(data, \'$."%s"\') AS %s' % (item['key'].replace('"', '').replace("'", "''"), _quote(item['key']))
                          for item in fields if item['key'] not in ('id', 'name', 'source', 'note', 'unconfirmed', 'updated_at'))
        db.execute(f'DROP VIEW IF EXISTS {_quote(title)}')
        db.execute(f'CREATE VIEW {_quote(title)} AS SELECT id, name{columns}, unconfirmed, source, note, updated_at FROM records '
                   f"WHERE kind='{kind.replace(chr(39), chr(39) * 2)}' AND deleted_at IS NULL")

    def kinds(self):
        """所有类型：说明、字段、分组、现有多少条记录。"""
        with self.connect() as db:
            counts = dict(db.execute('SELECT kind, count(*) FROM records WHERE deleted_at IS NULL GROUP BY kind').fetchall())
            return [{'name': row['name'], 'about': row['about'], 'fields': json.loads(row['fields']), 'group': row['grp'],
                     'records': counts.get(row['name'], 0)} for row in db.execute('SELECT * FROM kinds ORDER BY grp, sort, name')]

    def kind(self, name):
        return next((item for item in self.kinds() if item['name'] == name), None)

    def define(self, name, about=None, fields=None, group=None, replace=False, sort=None):
        """新建或修改一种类型。fields: [{key, label, unit, type, about}]。默认只增改不删，replace=True 整个换掉字段清单。"""
        name = str(name).strip()
        if not name:
            raise StoreError('类型要有名字。')
        with self.connect() as db:
            old = db.execute('SELECT * FROM kinds WHERE name=?', (name,)).fetchone()
            merged = [] if replace or not old else json.loads(old['fields'])
            for item in fields or []:
                item = {'key': item} if isinstance(item, str) else dict(item)
                item['key'] = str(item.get('key') or item.get('label') or '').strip()
                if not item['key']:
                    raise StoreError(f'字段要有 key：{item}')
                at = next((index for index, other in enumerate(merged) if other['key'] == item['key']), None)
                if at is None:
                    merged.append(item)
                else:
                    merged[at] = {**merged[at], **item}
            db.execute('INSERT INTO kinds(name, about, fields, grp, sort, created_at, updated_at) VALUES(?,?,?,?,?,?,?) '
                       'ON CONFLICT(name) DO UPDATE SET about=excluded.about, fields=excluded.fields, grp=excluded.grp, '
                       'sort=excluded.sort, updated_at=excluded.updated_at',
                       (name, about if about is not None else (old['about'] if old else ''), _dump(merged),
                        group if group is not None else (old['grp'] if old else ''),
                        sort if sort is not None else (old['sort'] if old else 0), now(), now()))
            self._view(db, name, merged)
        return {'name': name, 'fields': merged}

    def drop_kind(self, name):
        """去掉一种类型（它下面没有记录时）。"""
        with self.connect() as db:
            left = db.execute('SELECT count(*) FROM records WHERE kind=? AND deleted_at IS NULL', (name,)).fetchone()[0]
            if left:
                raise StoreError(f'类型 {name} 下面还有 {left} 条记录。先把记录删掉（进回收站），再去掉类型。')
            db.execute('DELETE FROM kinds WHERE name=?', (name,))
            db.execute(f'DROP VIEW IF EXISTS {_quote(name)}')
        return name

    # ----- 记录 -----

    def _find(self, db, ref, kind=None, deleted=False):
        row = db.execute('SELECT * FROM records WHERE id=?', (ref,)).fetchone()
        if row:
            return row
        alive = 'deleted_at IS NOT NULL' if deleted else 'deleted_at IS NULL'
        found = db.execute(f'SELECT * FROM records WHERE name=? AND {alive}' + (' AND kind=?' if kind else ''),
                           (ref, kind) if kind else (ref,)).fetchall()
        if len(found) > 1:
            raise StoreError(f'名字 {ref} 有 {len(found)} 条（类型：{"、".join(row["kind"] for row in found)}），请给 kind 或用 id。')
        return found[0] if found else None

    def save(self, kind, rows, source='', replace=False, by='智能体'):
        """新建或更新记录。rows 里每一项：{id?, name, 字段…, note, unconfirmed: [字段名], series: {表名: {columns, rows, meta}},
        links: [{relation, to, note, status}], files: [文件 id]}。

        有 id 就改那一条（没有就用这个 id 新建）；没有 id 时同类型同名的算同一条：只改给出的字段，replace=True 整条换掉。
        类型不存在会自动建；新出现的字段自动补进类型。返回 [{id, name, created}]。
        """
        kind = str(kind).strip()
        rows = [rows] if isinstance(rows, dict) else list(rows or [])
        done, stamp = [], now()
        with self.connect() as db:
            known_row = db.execute('SELECT fields FROM kinds WHERE name=?', (kind,)).fetchone()
            fields = json.loads(known_row['fields']) if known_row else None
            known = {item['key'] for item in fields or []}
            fresh = []
            count = db.execute('SELECT count(*) FROM records WHERE kind=?', (kind,)).fetchone()[0]
            for number, row in enumerate(rows, 1):
                row = dict(row)
                data = {**(row.get('data') if isinstance(row.get('data'), dict) else {}),
                        **{key: value for key, value in row.items() if key not in RESERVED}}
                old = db.execute('SELECT * FROM records WHERE id=?', (row['id'],)).fetchone() if row.get('id') else None
                name = str(row.get('name') or (old['name'] if old else '')).strip()
                if not old and name and not row.get('id'):
                    old = db.execute('SELECT * FROM records WHERE kind=? AND name=? AND deleted_at IS NULL', (kind, name)).fetchone()
                if not name:
                    name = f'{kind}-{count + number:03d}'
                flags = [str(item) for item in row.get('unconfirmed') or []]
                if old:
                    merged = data if replace else {**json.loads(old['data']), **data}
                    marks = flags if replace or 'unconfirmed' in row else json.loads(old['unconfirmed'])
                    db.execute('UPDATE records SET kind=?, name=?, data=?, unconfirmed=?, source=?, note=?, updated_at=?, deleted_at=NULL WHERE id=?',
                               (kind, name, _dump(merged), _dump(marks), row.get('source') or source or old['source'],
                                row['note'] if 'note' in row else old['note'], stamp, old['id']))
                    record_id = old['id']
                else:
                    record_id = row.get('id') or 'r-' + uuid.uuid4().hex[:10]
                    db.execute('INSERT INTO records(id, kind, name, data, unconfirmed, source, note, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                               (record_id, kind, name, _dump(data), _dump(flags), row.get('source') or source, row.get('note') or '', stamp, stamp))
                for title, table in (row.get('series') or {}).items():
                    self._put_series(db, record_id, title, table.get('columns') or [], table.get('rows') or [], table.get('meta') or {})
                for item in row.get('links') or []:
                    db.execute('INSERT OR REPLACE INTO links(from_id, relation, to_id, note, by, status, created_at) VALUES(?,?,?,?,?,?,?)',
                               (record_id, item.get('relation') or '关联', str(item['to']), item.get('note') or '', item.get('by') or by,
                                item.get('status') or 'confirmed', stamp))
                for file_id in row.get('files') or []:
                    db.execute('INSERT OR IGNORE INTO file_records(file_id, record_id) VALUES(?,?)', (file_id, record_id))
                fresh += [key for key in data if key not in known and key not in fresh]
                done.append({'id': record_id, 'name': name, 'created': not old})
            if fields is None or fresh:
                merged = (fields or []) + [{'key': key} for key in fresh]
                db.execute('INSERT INTO kinds(name, about, fields, created_at, updated_at) VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET '
                           'fields=excluded.fields, updated_at=excluded.updated_at', (kind, '', _dump(merged), stamp, stamp))
                self._view(db, kind, merged)
        return done

    def get(self, ref, kind=None, with_series=False):
        """一条记录的全部：字段、待确认的字段、曲线（默认只给名字和大小）、结果、两个方向的关联、原始文件。"""
        with self.connect() as db:
            row = self._find(db, ref, kind)
            if not row:
                raise StoreError(f'{self.name} 里没有 {ref}。用 find 或 records 的 list 找。')
            tables = {item['name']: {'columns': json.loads(item['columns']), 'rows': json.loads(item['rows']), 'meta': json.loads(item['meta'])}
                      for item in db.execute('SELECT * FROM series WHERE record_id=?', (row['id'],))}
            names = lambda ids: dict(db.execute(f'SELECT id, name FROM records WHERE id IN ({",".join("?" * len(ids))})', ids).fetchall()) if ids else {}
            out_links = [dict(item) for item in db.execute('SELECT * FROM links WHERE from_id=?', (row['id'],))]
            in_links = [dict(item) for item in db.execute('SELECT * FROM links WHERE to_id IN (?, ?)', (row['id'], row['name']))]
            known = names([item['to_id'] for item in out_links] + [item['from_id'] for item in in_links])
            return {
                'id': row['id'], 'kind': row['kind'], 'name': row['name'], 'data': json.loads(row['data']),
                'unconfirmed': json.loads(row['unconfirmed']), 'source': row['source'], 'note': row['note'],
                'created_at': row['created_at'], 'updated_at': row['updated_at'], 'deleted_at': row['deleted_at'],
                'series': tables if with_series else {title: f'{len(table["rows"])} 行 × {len(table["columns"])} 列：{"、".join(map(str, table["columns"]))}'
                                                      for title, table in tables.items()},
                'results': {item['method']: {'value': json.loads(item['value']), 'version': item['version'], 'params': json.loads(item['params']),
                                             'computed_at': item['computed_at']}
                            for item in db.execute('SELECT * FROM results WHERE record_id=?', (row['id'],))},
                'links': [{'relation': item['relation'], 'to': item['to_id'], 'to_name': known.get(item['to_id'], ''), 'note': item['note'],
                           'by': item['by'], 'status': item['status']} for item in out_links],
                'linked_from': [{'relation': item['relation'], 'from': item['from_id'], 'from_name': known.get(item['from_id'], ''),
                                 'by': item['by'], 'status': item['status']} for item in in_links],
                'files': [dict(item) for item in db.execute(
                    'SELECT f.* FROM files f JOIN file_records r ON r.file_id=f.id WHERE r.record_id=?', (row['id'],))]}

    def rows(self, kind, deleted=False, where=None):
        """一种类型的全部记录，每条一个字典（id、name、各字段……），给计算和导出用。"""
        with self.connect() as db:
            found = db.execute('SELECT * FROM records WHERE kind=? AND deleted_at IS ' + ('NOT NULL' if deleted else 'NULL') +
                               (f' AND ({where})' if where else '') + ' ORDER BY name', (kind,))
            return [{'id': row['id'], 'name': row['name'], **json.loads(row['data']), 'unconfirmed': json.loads(row['unconfirmed']),
                     'source': row['source'], 'note': row['note'], 'updated_at': row['updated_at']} for row in found]

    def delete(self, refs, kind=None, restore=False):
        """放进回收站（restore=True 是拿回来）。"""
        refs = [refs] if isinstance(refs, str) else list(refs)
        names = []
        with self.connect() as db:
            for ref in refs:
                row = self._find(db, ref, kind, deleted=restore)
                if not row:
                    raise StoreError(f'没有找到 {ref}。')
                db.execute('UPDATE records SET deleted_at=? WHERE id=?', (None if restore else now(), row['id']))
                names.append(row['name'])
        return names

    # ----- 曲线 -----

    def _put_series(self, db, record_id, title, columns, rows, meta):
        db.execute('INSERT OR REPLACE INTO series(record_id, name, columns, rows, meta) VALUES(?,?,?,?,?)',
                   (record_id, title, _dump([str(item) for item in columns]), json.dumps([list(item) for item in rows]), _dump(meta or {})))

    def put_series(self, record_id, title, columns, rows, meta=None):
        with self.connect() as db:
            self._put_series(db, record_id, title, columns, rows, meta)

    def table(self, ref, title=None, kind=None):
        """记录上挂的一张曲线或表：(列名, 行, meta)。只有一张时可以不给名字。"""
        record = self.get(ref, kind, with_series=True)
        if not record['series']:
            raise StoreError(f'{record["name"]} 上没有挂曲线或表。')
        if title is None and len(record['series']) == 1:
            title = next(iter(record['series']))
        if title not in record['series']:
            raise StoreError(f'{record["name"]} 上有：{"、".join(record["series"])}。')
        item = record['series'][title]
        return item['columns'], item['rows'], item['meta']

    # ----- 结果 -----

    def set_result(self, record_id, method, value, params=None, version=''):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO results(record_id, method, version, params, value, computed_at) VALUES(?,?,?,?,?,?)',
                       (record_id, method, version, _dump(params or {}), _dump(value), now()))

    def results(self, method, kind=None):
        """某个算法对一批记录的结果：[{id, name, value, params, computed_at}]。"""
        with self.connect() as db:
            found = db.execute('SELECT r.id, r.name, x.value, x.params, x.computed_at FROM results x JOIN records r ON r.id=x.record_id '
                               'WHERE x.method=? AND r.deleted_at IS NULL' + (' AND r.kind=?' if kind else '') + ' ORDER BY r.name',
                               (method, kind) if kind else (method,))
            return [{'id': row['id'], 'name': row['name'], 'value': json.loads(row['value']), 'params': json.loads(row['params']),
                     'computed_at': row['computed_at']} for row in found]

    # ----- 关联 -----

    def link(self, source, relation, target, note='', by='智能体', status='confirmed', remove=False):
        """source 是记录的 id 或名字；target 是另一条记录的 id / 名字，或别的库的 "库名:id"。"""
        with self.connect() as db:
            start = self._find(db, source)
            if not start:
                raise StoreError(f'没有找到 {source}。')
            end = self._find(db, target) if ':' not in str(target) else None
            to_id = end['id'] if end else str(target)
            if remove:
                db.execute('DELETE FROM links WHERE from_id=? AND relation=? AND to_id=?', (start['id'], relation, to_id))
            else:
                db.execute('INSERT OR REPLACE INTO links(from_id, relation, to_id, note, by, status, created_at) VALUES(?,?,?,?,?,?,?)',
                           (start['id'], relation, to_id, note, by, status, now()))
        return {'from': start['name'], 'relation': relation, 'to': end['name'] if end else to_id}

    # ----- 文件 -----

    def add_file(self, source, kind='', note='', subfolder=''):
        """把一个原始文件复制进本库的"原始文件"文件夹（同样内容只存一份），返回文件 id（内容指纹的前 16 位）。"""
        source = Path(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        with self.connect() as db:
            row = db.execute('SELECT * FROM files WHERE id=?', (digest,)).fetchone()
            if row:
                return digest
            folder = config.raw_dir(self.name) / (subfolder or kind or '其它')
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / source.name
            if target.exists():
                target = folder / f'{source.stem}-{digest[:6]}{source.suffix}'
            shutil.copy2(source, target)
            db.execute('INSERT INTO files(id, path, origin, size, kind, note, imported_at) VALUES(?,?,?,?,?,?,?)',
                       (digest, str(target.relative_to(config.library_dir(self.name))), str(source), target.stat().st_size, kind, note, now()))
        return digest

    def file_path(self, file_id):
        with self.connect() as db:
            row = db.execute('SELECT path FROM files WHERE id=?', (file_id,)).fetchone()
        if not row:
            raise StoreError(f'没有文件 {file_id}。')
        return config.library_dir(self.name) / row['path']

    # ----- 设置 -----

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def set_setting(self, key, value, about=None):
        with self.connect() as db:
            old = db.execute('SELECT about FROM settings WHERE key=?', (key,)).fetchone()
            db.execute('INSERT OR REPLACE INTO settings(key, value, about, updated_at) VALUES(?,?,?,?)',
                       (key, _dump(value), about if about is not None else (old['about'] if old else ''), now()))

    def settings(self):
        with self.connect() as db:
            return [{'key': row['key'], 'value': json.loads(row['value']), 'about': row['about'], 'updated_at': row['updated_at']}
                    for row in db.execute('SELECT * FROM settings ORDER BY key')]

    # ----- 查找和查询 -----

    def search(self, text, limit=30, kind=None):
        """按名字、类型、备注、字段内容找记录。几个词用空格分开，要都出现；不分大小写和连接符。"""
        norm = lambda value: re.sub(r'[\s_\-·/\\()（）\[\]【】，,、:：%]+', '', re.sub(r'\.0+(?![0-9])', '', str(value).lower()))
        words = [norm(word) for word in str(text).split() if norm(word)]
        with self.connect() as db:
            found = [row for row in db.execute('SELECT id, kind, name, note, data, updated_at FROM records WHERE deleted_at IS NULL'
                                               + (' AND kind=?' if kind else '') + ' ORDER BY updated_at DESC', (kind,) if kind else ())
                     if all(word in norm(f'{row["name"]} {row["kind"]} {row["note"]} {row["data"][:2000]}') for word in words)]
            found.sort(key=lambda row: (not all(word in norm(row['name']) for word in words), len(row['name'])))
            return [{'id': row['id'], 'kind': row['kind'], 'name': row['name'], 'updated_at': row['updated_at']} for row in found[:limit]], len(found)

    def sql(self, query, params=()):
        db = self.reader()
        try:
            cursor = db.execute(query, params)
            return [item[0] for item in cursor.description or []], [tuple(row) for row in cursor.fetchall()]
        except sqlite3.Error as error:
            raise StoreError(f'查询出错：{error}')


_open = {}


def open(name=None):                                            # noqa: A001  和 open() 同名是故意的：store.open('产品A')
    """打开一个产品库（不给名字就是当前的）。第一次打开时建表。"""
    name = name or config.settings()['library']
    if name not in _open:
        _open[name] = Library(name)
    return _open[name]


def knowledge():
    return open(config.KNOWLEDGE)
