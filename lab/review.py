"""改动回看：平台自己长出来的东西（技能、定过的事、规则、提示词）落地以后用得上吗、替他少问了几次。

生长循环的最后一环："落地 → 看用不用得上 → 留或撤"。都从账本（内核.db）和文件现算；
只有"定过的事挡下一次重问"要记一条事实 spared（loop 里记）。
约定口径、自定义呈现、作图偏好每轮都读，数不出"用了几次"，不列。
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta

from . import config, decide, kernel, loop, store
from .insight import _resolve_skill, skills
from .tools import _skills

WEEKS = 8                # 按周画时看近几周
IDLE_DAYS = 7            # 改了这么多天一次没用 → 还没用上
STALE_DAYS = 21          # 最后一次用在这么多天前 → 很久没用


def _now():
    return datetime.now(kernel.BEIJING)


def _day(text):
    return datetime.strptime(text[:16], '%Y-%m-%d %H:%M').replace(tzinfo=kernel.BEIJING)


def _monday(when):
    return (when - timedelta(days=when.weekday())).strftime('%Y-%m-%d')


class Buckets:
    """图的格子：数据不到三周按天（从第一天到今天），够了按周（近 8 周，周一开头）。"""

    def __init__(self, first):
        now = _now()
        start = _day(first) if first else now
        self.unit = '天' if (now - start).days < 21 else '周'
        if self.unit == '天':
            days = max((now.date() - start.date()).days, 6)
            self.keys = [(now - timedelta(days=days - i)).strftime('%Y-%m-%d') for i in range(days + 1)]
        else:
            self.keys = [_monday(now - timedelta(weeks=WEEKS - 1 - i)) for i in range(WEEKS)]

    def key(self, at):
        return at[:10] if self.unit == '天' else _monday(_day(at))

    def strip(self, times):
        counts = defaultdict(int)
        for at in times:
            counts[self.key(at)] += 1
        return [counts.get(key, 0) for key in self.keys]


def _verdict(at, uses):
    now = _now()
    if uses:
        last = max(uses)
        return ('很久没用', f'最后一次用在 {last[5:10]}') if (now - _day(last)).days >= STALE_DAYS else ('用得上', '')
    days = (now - _day(at)).days if at else 0
    return ('还没用上', f'改了 {days} 天一次没用，不需要可以撤') if days >= IDLE_DAYS else ('刚改', '还没遇到要用的时候')


def _facts(kind):
    """账本里这种事实（删掉的对话不算：那多半是试的）。"""
    with kernel.connect() as db:
        return db.execute('SELECT f.lane, f.at, f.data FROM facts f LEFT JOIN lanes l ON l.id = f.lane '
                          'WHERE f.type = ? AND l.deleted_at IS NULL ORDER BY f.seq', (kind,)).fetchall()


def _cards():
    """每张卡片：(对话, 时间, 是否并到别的卡片)。"""
    return [(lane, at, bool(json.loads(data).get('same_as'))) for lane, at, data in _facts('card')]


def _skill_items(cards, weeks):
    found = _skills()
    calls = defaultdict(list)                                        # 技能 → [(对话, 时间)]
    for lane, at, data in _facts('assistant'):
        for part in json.loads(data).get('parts') or []:
            if part.get('type') == 'tool' and part.get('name') == 'skill':
                key = _resolve_skill((part.get('input') or {}).get('name'), found)
                if key:
                    calls[key].append((lane, at))
    rule_runs = defaultdict(list)                                    # 规则指定的技能：那几轮也算用到
    for rule in loop.rules.all():
        key = _resolve_skill(rule.get('skill'), found)
        if key:
            rule_runs[key] += [(lane, at) for lane, at in _rule_runs(rule['name'])]
    by_lane = defaultdict(list)
    for lane, at, _ in cards:
        by_lane[lane].append(at)
    out = []
    for item in skills():
        if item['source'] == '内置':
            continue
        key, changed = item['key'], item['updated']
        uses = sorted(set(calls[key] + rule_runs[key]), key=lambda use: use[1])
        before = [use for use in uses if use[1] < changed]
        after = [use for use in uses if use[1] >= changed]

        def asked(group):
            """这几次用到它的时候，每次平均问了他几张卡（这次用到下次用之间，最多 24 小时）。"""
            if not group:
                return None
            total = 0
            for index, (lane, at) in enumerate(uses):
                if (lane, at) not in group:
                    continue
                later = [other for other_lane, other in uses[index + 1:] if other_lane == lane]
                limit = (_day(at) + timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
                end = min(later[0], limit) if later else limit
                total += sum(1 for card_at in by_lane[lane] if at <= card_at < end)
            return round(total / len(group), 2)
        state, hint = _verdict(changed, [at for _, at in after])
        out.append({'kind': '技能', 'key': key, 'name': item['name'], 'at': changed, 'by': item['origin'] or ('改过的内置技能' if item['source'] == '改过的内置' else '助手沉淀'),
                    'about': item['description'][:80], 'uses': len(after), 'uses_before': len(before), 'last': after[-1][1] if after else '',
                    'asked_before': asked(before), 'asked_after': asked(after), 'spared': None, 'weeks': weeks.strip([at for _, at in after]),
                    'state': state, 'hint': hint, 'undo': {'action': 'skill', 'label': '恢复内置的' if item['source'] == '改过的内置' else '撤掉（进回收站）'},
                    'link': '#/agent/skills'})
    return out


DAY = {'mon': '一', 'tue': '二', 'wed': '三', 'thu': '四', 'fri': '五', 'sat': '六', 'sun': '日'}


def _every(spec):
    """定时写法换成人话：daily 08:30 → 每天 08:30；weekly fri 17:00 → 每周五 17:00；2h → 每 2 小时。"""
    spec = str(spec or '').strip()
    parts = spec.split()
    if parts[:1] == ['daily']:
        return '每天 ' + ' '.join(parts[1:])
    if parts[:1] == ['weekly'] and len(parts) > 1:
        return f'每周{DAY.get(parts[1], parts[1])} ' + ' '.join(parts[2:])
    if parts[:1] == ['at']:
        return ' '.join(parts[1:]) + ' 一次'
    unit = {'m': '分钟', 'h': '小时', 'd': '天'}.get(spec[-1:])
    return f'每 {spec[:-1]} {unit}' if unit and spec[:-1].isdigit() else spec


def _rule_runs(name):
    """规则跑完的那几轮：[(对话, 时间)]。"""
    with kernel.connect() as db:
        return db.execute("SELECT lane, COALESCE(ended, started, created) FROM runs WHERE (lane = ? OR lane LIKE ?) AND status = 'done' ORDER BY 2",
                          ('rule-' + name, 'rule-' + name + ' · %')).fetchall()


def _rule_items(weeks):
    with kernel.connect() as db:
        next_due = dict(db.execute('SELECT rule, next_due FROM schedules').fetchall())
        imported = defaultdict(list)
        for source, at in db.execute("SELECT source, COALESCE(done_at, at) FROM intake WHERE status = '已入库'"):
            imported[source].append(at)
    out = []
    for rule in loop.rules.all():
        runs = [at for _, at in _rule_runs(rule['name'])]
        files = imported.get(f'文件夹「{rule["name"]}」', [])
        when = rule.get('when') or {}
        uses = sorted(runs + files)
        changed = rule.get('created') or (uses[0] if uses else '')
        state, hint = _verdict(changed, uses) if rule.get('enabled', True) else ('停用了', '')
        if rule.get('enabled', True) and not uses:
            due = next_due.get(rule['name'])
            state, hint = ('等着跑', '下次 ' + datetime.fromtimestamp(due, kernel.BEIJING).strftime('%m-%d %H:%M')) if due else                 ('等新文件', '文件夹里来了新文件就导入') if when.get('do') == '导入' else ('等着跑', '')
        if when.get('do') == '导入':
            about = f'看「{when.get("folder", "")}」里的 {when.get("pattern") or "所有文件"}'
            used = f'自动导入 {len(files)} 个文件' + (f'，助手跑了 {len(runs)} 轮' if runs else '')
        else:
            about = (_every(when['every']) if when.get('every') else when.get('source', '')) + (f'，用技能「{rule["skill"]}」' if rule.get('skill') else '')
            used = f'跑了 {len(runs)} 轮'
        out.append({'kind': '规则', 'key': rule['name'], 'name': rule['name'], 'at': changed, 'by': rule.get('by') or '', 'about': about,
                    'uses': len(uses), 'used': used, 'files': len(files), 'last': uses[-1] if uses else '', 'spared': None,
                    'weeks': weeks.strip(uses), 'state': state, 'hint': hint, 'enabled': rule.get('enabled', True),
                    'undo': {'action': 'rule', 'label': '停用' if rule.get('enabled', True) else '启用'}, 'link': '#/agent/rules'})
    return out


def _decision_items(library, spared, weeks):
    lib = store.open(library)
    if not lib.kind(decide.KIND):
        return []
    out = []
    for row in lib.rows(decide.KIND):
        if (row.get('状态') or '定了') == '撤销':
            continue
        hits = sorted(spared.get(row['id'], []))
        out.append({'kind': '定过的事', 'key': row['id'], 'name': row.get('问题') or row['name'], 'at': row.get('时间') or '',
                    'by': '你在卡片上答的' if row.get('状态') in (None, '定了', '暂缓') else '你点了"不用问了"' if row.get('状态') == '不用问' else '助手结案',
                    'about': '→ ' + str(row.get('回答') or ''), 'uses': len(hits), 'last': hits[-1] if hits else '', 'spared': len(hits),
                    'weeks': weeks.strip(hits), 'state': '用得上' if hits else '没再遇到', 'hint': '',
                    'undo': {'action': 'decision', 'label': '重新问'}, 'link': '#/agent/memory'})
    out.sort(key=lambda item: item['at'], reverse=True)          # 挡下得多的在前，一样多的新的在前
    out.sort(key=lambda item: -item['spared'])
    return out


def _prompt_item(weeks):
    path = loop.prompt_path()
    if not path.is_file():
        return []
    changed = datetime.fromtimestamp(path.stat().st_mtime, kernel.BEIJING).strftime('%Y-%m-%d %H:%M:%S')
    turns = [at for _, at, data in _facts('input') if at >= changed]
    state, hint = _verdict(changed, turns)
    return [{'kind': '提示词', 'key': 'prompt', 'name': '助手的系统提示词（你改过的）', 'at': changed, 'by': '你在页面上改的', 'about': '每一轮都先读它',
             'uses': len(turns), 'used': f'改后跑了 {len(turns)} 轮', 'last': turns[-1] if turns else '', 'spared': None,
             'weeks': weeks.strip(turns), 'state': state, 'hint': hint, 'undo': {'action': 'prompt', 'label': '恢复内置的'}, 'link': '#/agent/memory'}]


def changes(library=None):
    """改进页上面的回看：每条改动用了几次、少问几次，加每周"助手想问的"里替他挡下了多少。"""
    library = library or config.settings()['library']
    cards = _cards()
    spared, spared_times = defaultdict(list), []
    for _, at, data in _facts('spared'):
        item = json.loads(data)
        spared[item.get('decision')].append(at)
        spared_times.append(at)
    weeks = Buckets(min([at for _, at, _ in cards] + spared_times, default=''))
    items = _skill_items(cards, weeks) + _rule_items(weeks) + _decision_items(library, spared, weeks) + _prompt_item(weeks)
    week = {key: {'asked': 0, 'decided': 0, 'twin': 0} for key in weeks.keys}
    for _, at, twin in cards:
        slot = week.get(weeks.key(at))
        if slot:
            slot['twin' if twin else 'asked'] += 1
    for at in spared_times:
        slot = week.get(weeks.key(at))
        if slot:
            slot['decided'] += 1
    twins = sum(1 for *_, twin in cards if twin)
    used = [item for item in items if item['kind'] != '定过的事']
    return {'items': items, 'unit': weeks.unit, 'weeks': [{'week': key, **value} for key, value in week.items()],
            'totals': {'changes': len(used) + sum(1 for item in items if item['kind'] == '定过的事'),
                       'by_kind': {kind: sum(1 for item in items if item['kind'] == kind) for kind in ('技能', '规则', '定过的事', '提示词')},
                       'uses': sum(item['uses'] for item in used), 'spared': len(spared_times) + twins, 'spared_decided': len(spared_times), 'spared_twin': twins,
                       'asked': sum(1 for *_, twin in cards if not twin), 'files': sum(item.get('files', 0) for item in items),
                       'idle': sum(1 for item in items if item['state'] in ('还没用上', '很久没用'))}}


def facts_lines(library=None):
    """给复盘看的：近 14 天落地的改动用得上吗；改了 7 天还没用上的。"""
    data = changes(library)
    since = (_now() - timedelta(days=14)).strftime('%Y-%m-%d')
    fresh = [item for item in data['items'] if item['at'] >= since and item['kind'] != '定过的事']
    idle = [item for item in data['items'] if item['state'] in ('还没用上', '很久没用')]
    spared = [item for item in data['items'] if item['kind'] == '定过的事' and item['spared']]
    return {'近两周落地的': [{'类型': item['kind'], '名字': item['name'], '改的时间': item['at'][:16], '改后用了': item['uses'],
                         **({'改前每次问他': item['asked_before'], '改后每次问他': item['asked_after']} if item['kind'] == '技能' else {})} for item in fresh],
            '没用上的': [{'类型': item['kind'], '名字': item['name'], '情况': item['state'] + ('：' + item['hint'] if item['hint'] else '')} for item in idle],
            '定过的事挡下的重问': [{'问题': item['name'], '次数': item['spared']} for item in spared],
            '同一件事并到一张': data['totals']['spared_twin']}
