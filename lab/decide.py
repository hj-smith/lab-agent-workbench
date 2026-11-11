"""卡片和定过的事。

一张卡片 = 一件要他拍板的事。卡片带：key（这件事的短名，去重和记住都靠它）、依据（几句话，数字带单位，记录写 [[名字]]）、关于哪些记录。
- 同一件事已经有卡片在等他：新的不再摆出来，挂在那张后面（same_as）；他答一次，等着的对话都收到答案。
- 他答过的：存成产品库里的一条"决定"记录（问题、回答、依据、关于哪些记录、来自哪段对话、状态）。以后助手再要问同一件事，
  直接告诉它答案，不再打扰他；情况真变了才能重问（again + 说清哪里变了）。
- 他点"不用问了"，或助手发现事情已经在别处解决了（结案）：卡片收起来，也记一条决定。
"""
import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from . import config, kernel, store

KIND = '决定'
ABOUT = '他在卡片上定过的事：问题、回答、依据、关于哪些记录。助手再遇到同一件事照着做，不再问。'
FIELDS = [{'key': '问题'}, {'key': '回答'}, {'key': '依据'}, {'key': '状态', 'about': '定了 / 暂缓 / 不用问 / 结案 / 撤销'},
          {'key': '来自对话'}, {'key': '时间'}, {'key': '规矩', 'about': '是：以后同类的事都照这个回答做（也记进约定口径）'}, {'key': '键'}]
DEFER = re.compile(r'以后再说|再说吧|稍后|先放|晚点|过几天')     # 这类回答只是往后放，三天后可以再问
BLOCKING = ('定了', '不用问', '结案')


def norm(text):
    """去掉标点空白、统一大小写，用来比较"是不是同一件事"。"""
    return re.sub(r'[\s，。、；：:,.;!！?？（）()【】\[\]“”"\'「」《》·\-—_/]+', '', str(text or '')).lower()


def same(a, b):
    """两张卡片（或卡片和决定）是不是同一件事：key 一样，或问题几乎一样。"""
    ka, kb = norm(a.get('key')), norm(b.get('key'))
    if ka and kb and ka == kb:
        return True
    ta, tb = norm(a.get('title')), norm(b.get('title'))
    return bool(ta and tb) and SequenceMatcher(None, ta, tb).ratio() >= 0.8


def _lib(library):
    lib = store.open(library or config.settings()['library'])
    if not lib.kind(KIND):
        lib.define(KIND, ABOUT, FIELDS, '课题')
    return lib


def _now():
    return datetime.now(kernel.BEIJING)


def decided(library, card):
    """同一件事他已经定过（还算数的那条）：返回决定记录，没有就 None。暂缓的三天内算数，撤销的不算。"""
    lib = _lib(library)
    for row in sorted(lib.rows(KIND), key=lambda row: row.get('时间') or '', reverse=True):
        if not same({'key': row.get('键'), 'title': row.get('问题')}, card):
            continue
        state = row.get('状态') or '定了'
        if state in BLOCKING:
            return row
        if state == '暂缓' and (row.get('时间') or '') >= (_now() - timedelta(days=3)).strftime('%Y-%m-%d %H:%M'):
            return row
        return None
    return None


def record(library, card, answer, state=None, lane_title='', by='人', lane_id=''):
    """把一张卡片的结果存成"决定"。同一个问题再定一次就更新那条（最新的为准）。"""
    lib = _lib(library)
    state = state or ('暂缓' if DEFER.search(answer or '') else '定了')
    row = {'name': (card.get('title') or '')[:60] or '没写问题的卡片', '问题': card.get('title') or '', '回答': answer, '依据': card.get('evidence') or [],
           '关于': card.get('refs') or [], '状态': state, '来自对话': lane_title, '对话id': lane_id, '时间': _now().strftime('%Y-%m-%d %H:%M'), '规矩': bool(card.get('rule')), '键': card.get('key') or '',
           'links': [{'relation': '关于', 'to': name} for name in card.get('refs') or []]}
    saved = lib.save(KIND, [row], source='卡片', by=by)
    if card.get('rule') and state == '定了':
        to_rule(library, card.get('title') or '', answer)
    return lib.get(saved[0]['id'])


def to_rule(library, question, answer):
    """记进约定口径（助手每次干活先读）。同一条已经在就更新说明。"""
    lib = store.open(library or config.settings()['library'])
    items = lib.setting('约定口径') or []
    item = next((item for item in items if norm(item.get('条目')) == norm(question)), None)
    if item:
        item['说明'] = answer
    else:
        items.append({'条目': question, '说明': answer, '定于': _now().strftime('%Y-%m-%d')})
    lib.set_setting('约定口径', items, '人定下的口径和规矩，助手每次干活都先读')


def recent(library, count=20):
    """最近定过的事（写进系统提示，助手先看这里再决定要不要问）。"""
    lib = store.open(library or config.settings()['library'])
    if not lib.kind(KIND):
        return []
    rows = [row for row in lib.rows(KIND) if (row.get('状态') or '定了') != '撤销']
    return sorted(rows, key=lambda row: row.get('时间') or '', reverse=True)[:count]


def prompt_lines(library):
    rows = recent(library)
    if not rows:
        return ''
    return ('\n# 定过的事（他在卡片上答过的，照着做，同一件事不要再问；ask_user 问到同一件事会直接拿到这里的答案）\n'
            + '\n'.join(f'- {row.get("时间", "")[:10]} {row.get("问题")} → {row.get("回答")}（{row.get("状态") or "定了"}）' for row in rows) + '\n')
