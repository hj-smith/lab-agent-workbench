"""给人看的"智能体全貌"：技能从哪来、用了多少；工具调了多少、出错多少；一段对话现在装着什么；跑过哪些轮。

都从账本（内核.db 的 facts）和文件现算，不另存。
"""
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import config, kernel, loop, mcp, team, tools

GROUPS = {'overview': '看和找', 'find': '看和找', 'records': '研发库', 'sql': '研发库', 'sci': '计算', 'python': '计算', 'pip': '计算',
          'shell': '计算', 'files': '文件', 'look': '文件', 'export': '产出', 'chart': '产出', 'outputs': '产出', 'web': '调研', 'literature': '调研', 'lesson': '知识',
          'notes': '知识', 'skill': '技能', 'extend': '技能', 'ask_user': '和人配合', 'later': '和人配合', 'notify': '和人配合', 'delegate': '和人配合'}


def _facts(kind):
    with kernel.connect() as db:
        return db.execute('SELECT lane, at, data FROM facts WHERE type=? ORDER BY seq', (kind,)).fetchall()


def _titles():
    return {item['id']: item['title'] for item in kernel.lanes()}


def _resolve_skill(name, found):
    if not name:
        return None
    if name in found:
        return name
    return next((key for key, value in found.items() if name.lower() in (key + value['name']).lower()), None)


def _meta(path):
    text = path.read_text(encoding='utf-8')
    head = re.search(r'^---\s*\n(.*?)\n---', text, re.S)
    return dict(re.findall(r'^(\S+?):\s*"?(.*?)"?\s*$', head[1], re.M)) if head else {}, text


# ---------- 技能 ----------

def skills():
    found = tools._skills()
    titles = _titles()
    used, written = {}, {}
    for lane, at, data in _facts('assistant'):
        for part in json.loads(data).get('parts') or []:
            if part.get('type') != 'tool':
                continue
            args = part.get('input') or {}
            if part.get('name') == 'skill':
                key = _resolve_skill(args.get('name'), found)
                if key:
                    item = used.setdefault(key, {'count': 0, 'last': '', 'lanes': {}})
                    item['count'] += 1
                    item['last'] = at
                    item['lanes'][lane] = titles.get(lane, lane)
            elif part.get('name') == 'extend' and args.get('what') == 'skill' and args.get('action') == 'write':
                written.setdefault(args.get('name') or '', []).append({'at': at, 'lane': lane, 'title': titles.get(lane, lane)})
    for rule in loop.rules.all():                   # 规则指定的技能也算用到
        key = _resolve_skill(rule.get('skill'), found)
        if key:
            used.setdefault(key, {'count': 0, 'last': '', 'lanes': {}})['rule'] = rule['name']
    out = []
    for key, item in found.items():
        mine = (config.skills_dirs()[0] / key / 'SKILL.md').is_file()
        builtin = (config.PROGRAM_SKILLS / key / 'SKILL.md').is_file()
        meta, text = _meta(item['path'])
        folder = item['path'].parent
        files = sorted(str(path.relative_to(folder)) for path in folder.rglob('*') if path.is_file() and path.name != 'SKILL.md' and '__pycache__' not in path.parts)
        usage = used.get(key, {'count': 0, 'last': '', 'lanes': {}})
        out.append({'key': key, 'name': item['name'], 'description': item['description'],
                    'source': '改过的内置' if mine and builtin else '沉积的' if mine else '内置',
                    'origin': meta.get('来源', ''), 'updated': meta.get('更新', '') or datetime.fromtimestamp(item['path'].stat().st_mtime, kernel.BEIJING).strftime('%Y-%m-%d %H:%M'),
                    'size': len(text), 'files': files, 'uses': usage['count'], 'last_used': usage['last'], 'rule': usage.get('rule', ''),
                    'lanes': [{'id': lane, 'title': title} for lane, title in list(usage['lanes'].items())[-6:]],
                    'writes': written.get(key, [])[-6:]})
    return out


def skill(key):
    item = tools._skills()[key]
    folder = item['path'].parent
    return {'key': key, 'text': item['path'].read_text(encoding='utf-8'), 'folder': str(folder),
            'builtin': (config.PROGRAM_SKILLS / key / 'SKILL.md').is_file(), 'mine': (config.skills_dirs()[0] / key / 'SKILL.md').is_file()}


def save_skill(key, text):
    tools.write_skill(key, text, '人（技能页面）')
    return skill(key)


def drop_skill(key):
    """自己的技能放进回收站；改过的内置技能删掉就是恢复成内置的。"""
    folder = config.skills_dirs()[0] / key
    bin_ = config.HOME / '回收站' / '技能' / f'{key}-{kernel.now()[:16].replace(":", "").replace(" ", "-")}'
    bin_.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(bin_))
    return {'moved_to': str(bin_)}


# ---------- 工具 ----------

def tool_list():
    stats = {}
    for lane, at, data in _facts('tools'):
        for result in json.loads(data).get('results') or []:
            item = stats.setdefault(result.get('name'), {'calls': 0, 'errors': 0, 'seconds': 0.0, 'last': ''})
            item['calls'] += 1
            item['errors'] += bool(result.get('error'))
            item['seconds'] += float(result.get('seconds') or 0)
            item['last'] = at
    def row(definition, group, source):
        stat = stats.get(definition['name'], {'calls': 0, 'errors': 0, 'seconds': 0.0, 'last': ''})
        return {'name': definition['name'], 'description': definition['description'], 'group': group, 'source': source,
                'params': list((definition.get('input_schema') or {}).get('properties') or {}),
                'calls': stat['calls'], 'errors': stat['errors'], 'avg_seconds': round(stat['seconds'] / stat['calls'], 1) if stat['calls'] else None,
                'last': stat['last']}
    out = [row(item, GROUPS.get(item['name'], '其它'), '自带') for item in tools.definitions() + loop.KERNEL_TOOLS]
    out += [row(item, 'MCP · ' + item['description'].split(']')[0].replace('[MCP · ', ''), 'MCP') for item in mcp.hub.definitions()]
    return out


# ---------- 一段对话的上下文 ----------

def _size(value):
    """粗估 token：中文约 1 字 1 个，英文和符号约 3 字节 1 个。"""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return max(1, round(len(text.encode('utf-8')) / 3))


def context(lane_id):
    lane = loop.lanes.get(lane_id)
    data = lane.data
    profile = config.profile()
    budget = int(profile.get('context_budget') or 200000)
    system, definitions = loop.system_prompt(), loop.definitions()
    parts = {'系统提示（身份、规矩、技能目录）': _size(system), f'工具说明（{len(definitions)} 个）': _size(definitions)}
    words, calls, results, pictures = 0, 0, 0, 0
    messages = data['messages'][data['context_from']:]
    for message in messages:
        if message['role'] == 'user':
            words += _size((message.get('context') or '') + message.get('text', ''))
        elif message['role'] == 'assistant':
            for part in message['parts']:
                if part['type'] == 'tool':
                    calls += _size(part.get('input') or {})
                else:
                    words += _size(part.get('text') or '')
        elif message['role'] == 'tool':
            for item in message['results']:
                results += _size(item.get('text') or '')
                pictures += len(item.get('images') or [])
    if data['context_from']:
        parts['前面工作的摘要'] = _size(data['summary'])
    parts['对话文字'] = words
    parts['工具调用'] = calls
    parts['工具结果'] = results
    if pictures:
        parts[f'图片（{pictures} 张）'] = pictures * 1500
    estimate = sum(parts.values())
    real = data['last_input'] or 0
    scale = real / estimate if real and not data['context_from'] else 1
    skills_read, tool_counts = [], {}
    found = tools._skills()
    for message in data['messages']:
        for part in message.get('parts') or []:
            if part.get('type') == 'tool':
                tool_counts[part['name']] = tool_counts.get(part['name'], 0) + 1
                key = _resolve_skill((part.get('input') or {}).get('name'), found) if part['name'] == 'skill' else None
                if key and key not in skills_read:
                    skills_read.append(key)
    folder = config.workspace(lane_id)
    files = [{'name': str(path.relative_to(folder)), 'size': path.stat().st_size}
             for path in sorted(folder.rglob('*'), key=lambda item: -item.stat().st_mtime)
             if path.is_file() and not any(part in ('answers', '.看图', '__pycache__') for part in path.relative_to(folder).parts)][:40] if folder.is_dir() else []
    member = team.get(team.of_lane(lane_id)) or {}
    if member.get('executor'):                       # 外部执行器：它在自己的进程里用自己设置的模型，上下文不在工作台这边
        profile = {'name': member['name'], 'model': f'{member["executor"].get("command") or "codex"}（本机网关，用它自己设置的模型）'}
    return {'budget': budget, 'used': real or estimate, 'measured': bool(real), 'compacted': bool(data['context_from']),
            'parts': [{'name': name, 'tokens': round(value * scale)} for name, value in parts.items()],
            'model': profile.get('model'), 'profile': profile.get('name'), 'library': data['library'] or config.settings()['library'],
            'anchor': data['anchor'], 'skills': skills_read, 'tools': sorted(tool_counts.items(), key=lambda item: -item[1]),
            'files': files, 'outputs': data['outputs'], 'timers': data['timers'], 'usage': data['usage'],
            'messages': len(data['messages']), 'summary': data['summary'][:4000], 'system': system}


# ---------- 跑过的轮 ----------

def runs(limit=200):
    titles = _titles()
    out = []
    for run in kernel.runs(limit):
        started, ended = run.get('started'), run.get('ended')
        seconds = (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds() if started and ended else None
        events = run['events']
        out.append({'id': run['id'], 'lane': run['lane'], 'title': titles.get(run['lane'], run['lane']), 'status': run['status'],
                    'attempts': run['attempts'], 'created': run['created'], 'started': started, 'ended': ended, 'seconds': seconds,
                    'result': run['result'][:300], 'sources': sorted({event.get('source', '') for event in events}),
                    'what': '；'.join((event.get('summary') or (event.get('detail') or {}).get('text') or '')[:60] for event in events[:3])})
    return out
