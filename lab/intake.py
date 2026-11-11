"""收件：文件进来以后怎么办。

文件从两处来：他拖进"测试 → 导入"，或者"自动导入的文件夹"里出现了新文件、改过的文件（规则：when.source='文件夹'、when.do='导入'）。
每个文件先用原读法认一遍、提议字段和关联（importq.preview），然后：
- 认得、没有拿不准的地方、规则写了"认得的直接入库" → 直接导入并用原算法算好（importq.commit），记"已入库"。
- 认得但有拿不准的（同名多条、要人选关联、要人填）→ 放进队列"等你确认"，页面上改好再导入。
- 认不出是什么 → 交给"数据导入"成员在后台读、判断、建类型、挂好（拿不准的它会发卡片）。
每一批做完发一条通知，点开就是导入页。队列存在 内核.db 的 intake 表里，关掉页面、重启都还在。
"""
import json
import re
import threading
import uuid
from pathlib import Path

from . import config, kernel, store

DOUBT = re.compile(r'要人|多个|请填写|可改 existing')
_turn = threading.Lock()                 # 同一时间只做一批（导入会跑原算法、写库）


def _commit(lib, items):
    from . import importq
    results = importq.commit(lib, items)
    for item, result in zip(items, results):
        result['name'], result['kind'] = item.get('name'), item.get('kind')
    return results


def doubts(item):
    """一项预览里要人看的地方。只是说明情况的备注（比如"原件指纹相同，沿用"）不算。"""
    found = [item['problem']] if item.get('problem') else []
    if item.get('type') == '不认识':
        found.append('认不出是什么文件')
    return found + [note for note in item.get('notes') or [] if DOUBT.search(note)]


def _row(row):
    item = dict(row)
    for key in ('items', 'results'):
        item[key] = json.loads(item[key] or '[]')
    return item


def _put(library, path, source, status, items, results=None, lane='', note=''):
    row_id = 'in-' + uuid.uuid4().hex[:10]
    with kernel.connect() as db:
        db.execute('INSERT INTO intake(id, at, library, path, file, source, status, items, results, lane, note, done_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (row_id, kernel.now(), library, str(path), _name(path), source, status, json.dumps(items, ensure_ascii=False, default=str),
                    json.dumps(results or [], ensure_ascii=False, default=str), lane, note, kernel.now() if status != '待确认' else None))
    return row_id


def _set(row_id, **values):
    if values.get('status') and values['status'] != '待确认':
        values.setdefault('done_at', kernel.now())
    for key in ('items', 'results'):
        if key in values:
            values[key] = json.dumps(values[key], ensure_ascii=False, default=str)
    with kernel.connect() as db:
        db.execute(f'UPDATE intake SET {", ".join(f"{key}=?" for key in values)} WHERE id=?', (*values.values(), row_id))


def _name(path):
    return str(path).replace('\\', '/').rsplit('/', 1)[-1]


def listing(library, recent=60):
    with kernel.connect() as db:
        waiting = [_row(row) for row in db.execute("SELECT * FROM intake WHERE library=? AND status='待确认' ORDER BY at", (library,))]
        done = [_row(row) for row in db.execute("SELECT * FROM intake WHERE library=? AND status!='待确认' ORDER BY done_at DESC LIMIT ?", (library, recent))]
        counts = dict(db.execute("SELECT source, count(*) FROM intake WHERE library=? AND status='已入库' GROUP BY source", (library,)).fetchall())
    return {'waiting': waiting, 'done': done, 'folders': folders(library, counts)}


def folders(library, counts=None):
    """本库的自动导入文件夹：在不在、看过几个文件、导入过几个。"""
    from . import loop, sources
    seen = json.loads(sources._seen_path().read_text(encoding='utf-8')) if sources._seen_path().is_file() else {}
    out = []
    for rule in loop.rules.all():
        when = rule.get('when') or {}
        if when.get('source') != '文件夹' or when.get('do') != '导入' or (rule.get('library') or config.settings()['library']) != library:
            continue
        folder = Path(when.get('folder') or '')
        out.append({'name': rule['name'], 'folder': str(folder), 'exists': folder.is_dir(), 'enabled': rule.get('enabled', True),
                    'seen': len(seen.get(f'{rule["name"]}|{folder}', {})), 'imported': (counts or {}).get(f'文件夹「{rule["name"]}」', 0)})
    return out


def agent_task(library, files, note=''):
    """不认得的文件交给"数据导入"成员在后台处理。返回那段对话的 id。"""
    from . import loop
    text = ('有一批文件要导入研发库。每个文件：先读内容，判断它是什么（实验记录、测试数据、报告、文献、照片、表格……），'
            '决定进哪一类（没有合适的就新建类型）、挂到哪些记录上；能用科学算法包算的就算好；从图片、手写、口述里读出的数放进 unconfirmed；'
            '原文件用 records 的 files 挂上存档。他在下面指定了的照他说的办。拿不准的（是什么、进哪一类、挂到哪、读出的数对不对）发卡片让他定：'
            '推荐选项放第一个，写清理由；同一批里同类的问题合成一张卡。他定过的规矩记进约定口径，下次不再问。做完用几句话交回：每个文件进了哪、建了或改了哪些记录。\n文件：\n'
            + '\n'.join(f'- {path}' for path in files) + (f'\n他的说明：{note}' if note else ''))
    return loop.say(kernel.new_id('intake'), text, (), '测试 · 导入', library)


def add(library, paths, source='手动拖入'):
    """他拖进来的文件：先认一遍，放进"等你确认"。"""
    from . import importq
    lib = store.open(library)
    with _turn:
        for path in paths:
            try:
                items = importq.preview(lib, [path])
            except Exception as error:                          # 原读法读不了：记下原因，交给助手或不要
                items = [{'file': _name(path), 'path': str(path), 'type': '不认识', 'kind': '', 'name': _name(path), 'fields': {},
                          'existing': None, 'links': [], 'notes': [], 'problem': f'读不了：{error}'}]
            _put(library, path, source, '待确认', items)
    return listing(library)


def go(library, rows):
    """他在页面上定好了：rows = [{id, items: [{…预览项, route: fast|agent|skip}]}]。直接导入的导入并算好，交给助手的一起交给"数据导入"成员。"""
    from . import importq
    lib = store.open(library)
    handed, said = [], []
    with _turn:
        for row in rows:
            items = row['items']
            fast = [item for item in items if item.get('route', 'fast') == 'fast']
            slow = [item for item in items if item.get('route') == 'agent']
            results = _commit(lib, fast) if fast else []
            if slow:
                handed.append((row['id'], [item['path'] for item in slow][:1]))
                said += [f'{item["file"]}：{item.get("hint") or ""}' for item in slow if item.get('hint') or item.get('kindByPerson')]
            status = '已入库' if fast else '交给助手' if slow else '跳过'
            _set(row['id'], status=status, items=items, results=results)
    if handed:
        lane = agent_task(library, sorted({path for _, paths in handed for path in paths}), '\n'.join(said))
        for row_id, _ in handed:
            _set(row_id, lane=lane)
    return listing(library)


def arrive(rule, paths):
    """自动导入的文件夹里来了新文件（或改过的文件）。在后台线程里做，做完发一条通知。"""
    from . import importq, loop
    library = rule.get('library') or config.settings()['library']
    lib = store.open(library)
    source = f'文件夹「{rule["name"]}」'
    when = rule.get('when') or {}
    auto, to_agent = when.get('auto', True), when.get('agent', True)
    counts = {'已入库': 0, '待确认': 0, '交给助手': 0, '读不了': 0}
    unknown, rows, problems = [], [], []
    with _turn:
        for path in paths:
            try:
                items = importq.preview(lib, [path])
            except Exception as error:
                _put(library, path, source, '读不了', [], note=str(error))
                counts['读不了'] += 1
                continue
            if all(item['type'] == '不认识' for item in items):
                if to_agent:
                    unknown.append(str(path))
                    rows.append(_put(library, path, source, '交给助手', items))
                    counts['交给助手'] += 1
                else:
                    _put(library, path, source, '待确认', items)
                    counts['待确认'] += 1
                continue
            doubtful = [item for item in items if doubts(item)]
            if auto and not doubtful:
                results = _commit(lib, items)
                _put(library, path, source, '已入库', items, results)
                counts['已入库'] += 1
                problems += [f'{result["file"]}：{result["problem"]}' for result in results if result.get('problem')]
            else:
                _put(library, path, source, '待确认', items)
                counts['待确认'] += 1
    if unknown:
        lane = agent_task(library, unknown, f'这些文件来自自动导入的文件夹「{rule["name"]}」')
        for row_id in rows:
            _set(row_id, lane=lane)
    parts = [f'{label} {count} 个' for label, count in counts.items() if count]
    if parts:
        text = '，'.join(parts) + '。' + (f'有 {len(problems)} 处要看（例如 GPC 还没定基线）。' if problems else '')
        loop.inbox.notify(loop.rules.lane(rule, library), f'{rule["name"]}：进来 {len(paths)} 个文件', text,
                          ref={'href': '#/testing/import', 'label': '去导入页'})


def arrive_later(rule, paths):
    threading.Thread(target=arrive, args=(rule, paths), daemon=True, name='lab-intake').start()
