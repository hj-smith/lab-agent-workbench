"""给人看的简报（晨报、复盘、调研简报、实验小结）和"今天的全局"。

两种写法分开：
- 写给智能体的（派活、交回结果、压缩摘要、技能正文、卡片的 about）：准确完整，可以带编号、字段名、代码。
- 写给人的（对话、卡片、通知、笔记、简报）：人话。plain() 找出不像人话的地方，退回给智能体改写。
简报是产品库里的一种记录"简报"：页头一句话 + 一组块；关键数和新进库的图由程序算（facts），智能体只写判断。
"""
import re
from collections import Counter
from datetime import datetime, timedelta

from . import config, kernel, store

KIND = '简报'
ABOUT = '写给人看的简报：晨报、复盘、调研简报、实验小结。页头一句话，下面是数字、图、发现、建议、等你拍板的事。'
FIELDS = [{'key': '日期'}, {'key': '类型', 'about': '晨报 / 复盘 / 调研 / 小结'}, {'key': '一句话'}, {'key': '块'}, {'key': '数'}]
MOVED = ('迁移', '旧', '储存与老化工作台')           # 这些来源是从旧库搬进来的，不是新做的
TASK = {'doing': '进行中', 'todo': '待办', 'later': '以后', 'done': '做完了'}

# 不像人话的东西（左右用"不是字母数字"来界，中文挨着也认得出）
_EDGE = r'(?<![A-Za-z0-9_])'
NOT_PLAIN = [
    (_EDGE + r'[a-z]+(?:_[a-z0-9]+)+(?![A-Za-z0-9_])', '程序里的字段名'),
    (_EDGE + r'(?:SELECT|FROM|WHERE|JOIN|json_extract|records\.\w+)(?![A-Za-z0-9_])', 'SQL'),
    (_EDGE + r'(?:ell-SI|nk|card|lane|proj|seed|run|frm|res|gpc-sample|r|e|t)-[0-9A-Za-z_-]{2,}', '内部编号'),
    (_EDGE + r'[0-9a-f]{16,}(?![A-Za-z0-9_])', '哈希'),
    (_EDGE + r'(?:doing|todo|tracking|planned|pending|provisional|hypothesis|queued|sourceModel|true|false|null|None)(?![A-Za-z0-9_])', '英文状态词'),
    (r'[A-Za-z]:\\|\.py(?![A-Za-z])|\.db(?![A-Za-z])|\.jsonl?(?![A-Za-z])', '文件路径'),
]


def plain(*texts):
    """写给人的文字里不像人话的地方：[(片段, 原因)]，最多 8 处。"""
    found = []
    for text in texts:
        for pattern, reason in NOT_PLAIN:
            for match in re.finditer(pattern, str(text or '')):
                if (match.group(), reason) not in found:
                    found.append((match.group(), reason))
    return found[:8]


def human_texts(blocks):
    """一组块里给人看的字（不含图的数据、记录引用、交给助手的话）。"""
    out = []

    def walk(value, key=''):
        if key in ('series', 'x', 'y', 'refs', 'task', 'ids', 'id'):
            return
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            for inner, item in value.items():
                walk(item, inner)
        elif isinstance(value, list):
            for item in value:
                walk(item, key)
    walk(blocks)
    return out


def _now():
    return datetime.now(kernel.BEIJING)


def last_brief(lib, kind='晨报'):
    rows = [row for row in lib.rows(KIND) if row.get('类型') == kind] if lib.kind(KIND) else []
    return max(rows, key=lambda row: row.get('日期') or '', default=None)


def facts(library, since=None):
    """程序算好的今天的事实（给智能体写简报用，也存进简报的"数"画图）。"""
    from . import loop, present
    lib = store.open(library)
    previous = last_brief(lib)
    since = since or (previous or {}).get('日期') and f'{previous["日期"]} 00:00:00' or (_now() - timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')
    _, rows = lib.sql('SELECT kind, source, created_at, id, name, COALESCE(json_extract(data,"$.测试日"), json_extract(data,"$.日期")) FROM records '
                      'WHERE deleted_at IS NULL AND created_at >= ?', (since,))
    fresh, moved = Counter(), Counter()
    for kind, source, *_ in rows:
        if kind == KIND:                                   # 简报自己不算进库的数据
            continue
        (moved if str(source or '').startswith(MOVED) else fresh)[kind] += 1
    day = since[:10]
    _, dated = lib.sql('SELECT kind, name, id FROM records WHERE deleted_at IS NULL AND COALESCE(json_extract(data,"$.测试日"), json_extract(data,"$.日期")) >= ? '
                       'ORDER BY 1, 2', (day,))
    inbox = loop.inbox.view()
    road = present.roadmap(library) or {}
    tasks = [task for task in road.get('tasks') or [] if task.get('status') == 'doing' or (task.get('status') == 'todo' and task.get('priority') in ('P0', 'P1'))]
    projects = [{'名字': row['name'], '当前站': row.get('当前站'), '一句话': row.get('一句话') or row.get('问题')} for row in lib.rows('课题')] if lib.kind('课题') else []
    from .server import _health_counts
    time_now = _now().timestamp()
    timers = [{'about': item['about'], 'at': datetime.fromtimestamp(item['due'], kernel.BEIJING).strftime('%m-%d %H:%M')}
              for item in kernel.timers() if item['due'] - time_now < 86400]
    know = store.knowledge()
    from . import insight
    growth = {'自己沉淀的技能': sum(1 for item in insight.skills() if item['source'] != '内置'),
              '笔记': len(list(config.notes_dir().glob('*.md'))) if config.notes_dir().is_dir() else 0,
              **{name: count for name, count in ((item['name'], item['records']) for item in know.kinds()) if name in ('命题', '文献', '改进')},
              '自定义呈现': len(list(present.views_dir().glob('*.py'))) if present.views_dir().is_dir() else 0}
    return {'从': since, '到': _now().strftime('%Y-%m-%d %H:%M'),
            '新进库': dict(fresh), '搬入旧数据': dict(moved),
            '这段时间做的实验和测试': [{'类型': kind, '名字': name, 'id': record_id} for kind, name, record_id in dated][:40],
            '等你拍板': [{'问题': item['card']['title'], '事项': item['lane'], '选项': item['card'].get('options') or []} for item in inbox['cards']],
            '在跑的活': [item['title'] for item in inbox['running']],
            '待办': [{'编号': task.get('id'), '标题': task.get('title'), '状态': TASK.get(task.get('status'), task.get('status')), '优先级': task.get('priority')} for task in tasks],
            '课题': projects, '数据体检': _health_counts(lib), '24 小时内的复查': timers, '沉淀': growth,
            '等着核对的预测': __import__('lab.predict', fromlist=['waiting']).waiting(library),
            '新文献': _new_literature(projects),
            '今天要复习': len(__import__('lab.learn', fromlist=['due']).due()),
            '改动回看': __import__('lab.review', fromlist=['facts_lines']).facts_lines(library)}


def facts_text(f):
    """事实写成一段给智能体看的资料（可以带编号，它要用来查和链接）。"""
    lines = [f'# 今天的全局（{f["从"]} 到 {f["到"]}，北京时间）',
             '新进库（新做或新导入）：' + ('、'.join(f'{k} {v}' for k, v in f['新进库'].items()) or '没有'),
             '搬进来的旧数据：' + ('、'.join(f'{k} {v}' for k, v in f['搬入旧数据'].items()) or '没有') + '（不是新实验，别当成新结果）',
             '按记录自己的日期落在这段时间的实验和测试：' + ('、'.join(f'{item["类型"]} {item["名字"]}' for item in f['这段时间做的实验和测试']) or '没有'),
             f'等你拍板（{len(f["等你拍板"])}）：' + ('；'.join(item['问题'] for item in f['等你拍板']) or '没有'),
             '在跑的活：' + ('、'.join(f['在跑的活']) or '没有'),
             '进行中和 P0/P1 待办：' + ('；'.join(f'{item["编号"]} {item["标题"]}（{item["状态"]}，{item["优先级"] or ""}）' for item in f['待办']) or '没有'),
             '课题：' + ('；'.join(f'{item["名字"]}（当前站 {item["当前站"]}）' for item in f['课题']) or '没有'),
             '数据体检：' + '、'.join(f'{k} {v}' for k, v in f['数据体检'].items()),
             '24 小时内约好的复查：' + ('；'.join(f'{item["at"]} {item["about"]}' for item in f['24 小时内的复查']) or '没有'),
             '沉淀（技能、笔记、命题、改进、自定义呈现的数量）：' + '、'.join(f'{k} {v}' for k, v in f['沉淀'].items()),
             f'等着核对的预测（{len(f["等着核对的预测"])}，实验结果已经有了的按技能「预测和验证」核对）：' + ('；'.join(f'{item["实验"]} 的 {item["指标"]} 预测 {item["预测"]}（把握 {item["把握"]}%）' for item in f['等着核对的预测']) or '没有'),
             _review_text(f['改动回看']),
             f'文献监测（知识 → 文献）：待看 {f["新文献"]["待看"]} 篇；助手说高相关、和这个库课题有关的：'
             + ('；'.join(f'{item["标题"]}（{item["课题"] or "没挂课题"}：{item["意见"]}）' for item in f['新文献']['高相关']) or '没有'),
             f'学习（知识 → 学习）：今天到期要复习的检查题 {f["今天要复习"]} 道']
    return '\n'.join(lines)


def _new_literature(projects):
    """文献监测新到、还没看的：总数，和助手筛成高相关、挂在这个库课题上（或没挂课题）的几篇。"""
    from . import literature
    waiting = literature.leads('待看')
    mine = {item['名字'] for item in projects}
    high = [item for item in waiting if item['相关度'] == '高' and (not item['课题'] or mine & set(str(item['课题']).split('、')))]
    return {'待看': len(waiting), '高相关': [{'标题': item['标题'], '课题': item['课题'], '意见': item['意见']} for item in high[:5]]}


def _review_text(review):
    fresh = '；'.join(f'{item["类型"]}「{item["名字"]}」{item["改的时间"][5:10]} 改，改后用了 {item["改后用了"]} 次'
                     + (f'（每次问他 {item["改前每次问他"]} → {item["改后每次问他"]} 张卡）' if item.get('改后每次问他') is not None and item.get('改前每次问他') is not None else '')
                     for item in review['近两周落地的']) or '没有'
    idle = '；'.join(f'{item["类型"]}「{item["名字"]}」{item["情况"]}' for item in review['没用上的']) or '没有'
    spared = sum(item['次数'] for item in review['定过的事挡下的重问'])
    return (f'改动回看：近两周落地的 {fresh}。没用上的（复盘时发卡片问他撤不撤）：{idle}。'
            f'定过的事挡下重问 {spared} 次，同一件事并到一张 {review["同一件事并到一张"]} 次')


def save(library, kind, headline, blocks, lane=None, date=None, title=None):
    """存一份简报（同类型同一天的覆盖），返回 (记录, 不像人话的地方)。不像人话的时候照样存，但不通知人。"""
    lib = store.open(library)
    if not lib.kind(KIND):
        lib.define(KIND, ABOUT, FIELDS, '产出')
    date = date or _now().strftime('%Y-%m-%d')
    problems = plain(headline, *human_texts(blocks))
    numbers = facts(library) if kind in ('晨报', '复盘') else {}
    name = title or f'{kind} {date}'
    done = lib.save(KIND, [{'name': name, '日期': date, '类型': kind, '一句话': headline, '块': blocks,
                            '数': {key: numbers.get(key) for key in ('新进库', '搬入旧数据', '数据体检', '沉淀')} if numbers else {},
                            'note': '' if not problems else '有不像人话的地方，等助手改写'}],
                    source=f'助手（{(lane or "命令行").removeprefix("rule-")}）', by='智能体')
    record = lib.get(done[0]['id'])
    if not problems and lane:
        from .loop import inbox
        inbox.notify(lane, name, headline, ref={'library': library, 'id': record['id']})
    return record, problems
