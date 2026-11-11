"""学习：他想弄懂一样东西时，先摸底，再定最少的路线，每一步"先猜 → 讲 → 检查"；检查题带把握程度和思路，结果存成练习记录。

助手只出题和讲评（用模型）；做题、对答案、复习都在页面上（不用模型）。都存在知识库里：
- 「学习」：目标、为什么学、课题、状态、摸底 [题]、路线 [步]、小结。步：{id, 标题, 命题（id）, 先猜（题）, 讲解（markdown）, 检查 [题]}。
  题：{id, 题型 单选 / 判断 / 填数 / 简答, 题目, 选项, 答案, 容差, 单位, 解释}。
- 「练习记录」：每答一次一条（阶段 摸底 / 先猜 / 检查 / 复习），结果 对 / 错 / 部分 / 待讲评，关联 考的是 → 命题。
复习：每道检查题看自己的记录，连续答对 n 次后隔 1 → 3 → 7 → 16 → 35 天再考，答错了明天再考。
"""
import random
import re
import uuid
from datetime import date, datetime, timedelta

from . import store
from .present import clean

KIND = '学习'
PRACTICE = '练习记录'
INTERVALS = [1, 3, 7, 16, 35]
TYPES = ('单选', '判断', '填数', '简答')
ABOUT = '他想弄懂的一样东西：目标、摸底题、路线（每步：先猜 → 讲解 → 检查）、小结。做题记录在「练习记录」。'
FIELDS = [{'key': key} for key in ('目标', '为什么学', '课题', '状态', '摸底', '路线', '小结')]
STATES = ('摸底', '排路线', '学习中', '学完')


def _know():
    know = store.knowledge()
    if not know.kind(KIND):
        know.define(KIND, ABOUT, FIELDS, group='知识')
    return know


# ---------- 学习本身 ----------

def create(goal, why='', projects=None, by='你'):
    goal = str(goal or '').strip()
    if not goal:
        raise store.StoreError('写一句想弄懂什么。')
    saved = _know().save(KIND, {'id': 'learn-' + uuid.uuid4().hex[:8], 'name': goal[:40], '目标': goal, '为什么学': str(why or '').strip(),
                                '课题': list(projects or []), '状态': '摸底', '摸底': [], '路线': [], '小结': ''}, by=by)[0]
    return saved['id']


def _question(item, prefix):
    """把助手写的题理一理：补 id、题型；判断题的选项固定成 对 / 错。"""
    item = dict(item or {})
    item['id'] = str(item.get('id') or f'{prefix}-{uuid.uuid4().hex[:5]}')
    kind = item.get('题型') or ('单选' if item.get('选项') else '简答')
    item['题型'] = kind if kind in TYPES else '简答'
    if item['题型'] == '判断':
        item['选项'] = ['对', '错']
    options = item.get('选项') or []
    if item['题型'] == '单选' and item.get('答案') in options and not item.get('打乱过'):
        options = list(options)
        random.shuffle(options)                        # 模型爱把对的放第一个；答案存的是原文，打乱不影响判分
        item['选项'], item['打乱过'] = options, True
    return item


def save(ref, diagnose=None, route=None, summary=None, status=None, by='智能体'):
    """助手写摸底题、路线、小结或状态。route 每步：{标题, 命题, 先猜, 讲解, 检查: [题]}。"""
    know = _know()
    lesson = know.get(ref, KIND)
    data, change = lesson['data'], {'id': lesson['id']}
    if diagnose is not None:
        change['摸底'] = [_question(item, 'd') for item in diagnose]
        change['状态'] = '摸底'
    links = []
    if route is not None:
        steps = []
        for number, step in enumerate(route, 1):
            step = dict(step or {})
            step['id'] = str(step.get('id') or f's{number}')
            if step.get('先猜'):
                step['先猜'] = _question(step['先猜'], f'{step["id"]}-g')
            step['检查'] = [_question(item, f'{step["id"]}-c') for item in step.get('检查') or []]
            if step.get('命题'):
                claim = _claim(know, step['命题'])
                step['命题'] = claim['id'] if claim else step['命题']
                if claim:
                    links.append({'relation': '学的是', 'to': claim['id']})
            steps.append(step)
        change['路线'] = steps
        change['状态'] = '学习中'
    if summary is not None:
        change['小结'] = summary
    if status:
        change['状态'] = status if status in STATES else data.get('状态')
    if links:
        change['links'] = links
    know.save(KIND, change, by=by)
    return detail(lesson['id'])


def _claim(know, ref):
    try:
        found = know.get(ref, '命题')
    except store.StoreError:
        return None
    return {'id': found['id'], 'name': found['name'], '陈述': found['data'].get('陈述') or found['name'], '状态': found['data'].get('状态'),
            '把握': found['data'].get('把握')}


def _questions(data):
    """这次学习里所有的题：{题 id: (阶段, 步, 题)}。"""
    found = {item['id']: ('摸底', None, item) for item in data.get('摸底') or []}
    for step in data.get('路线') or []:
        if step.get('先猜'):
            found[step['先猜']['id']] = ('先猜', step, step['先猜'])
        for item in step.get('检查') or []:
            found[item['id']] = ('检查', step, item)
    return found


def _records(know, lesson_id=None):
    if not know.kind(PRACTICE):
        return []
    rows = know.rows(PRACTICE)
    rows = [row for row in rows if row.get('学习') == lesson_id] if lesson_id else [row for row in rows if row.get('学习')]
    rows.sort(key=lambda row: str(row.get('时间') or ''))
    return rows


def detail(ref):
    know = _know()
    lesson = know.get(ref, KIND)
    data = lesson['data']
    records = _records(know, lesson['id'])
    latest = {}
    for row in records:
        latest[row.get('题号')] = row
    claims = {}
    for step in data.get('路线') or []:
        if step.get('命题') and step['命题'] not in claims:
            claims[step['命题']] = _claim(know, step['命题'])
    steps = data.get('路线') or []
    done = sum(1 for step in steps if step.get('检查') and all(item['id'] in latest for item in step['检查']))
    return clean({'id': lesson['id'], 'name': lesson['name'], **{key: data.get(key) for key in ('目标', '为什么学', '课题', '状态', '摸底', '路线', '小结')},
                  'answers': {key: _record_view(row) for key, row in latest.items()}, 'claims': claims, 'progress': [done, len(steps)],
                  'history': [_record_view(row) for row in records], 'updated': lesson['updated_at']})


def _record_view(row):
    return {'id': row['id'], **{key: row.get(key) for key in ('题号', '阶段', '题目', '你的答案', '正确答案', '结果', '把握程度', '思路', '讲评', '时间', '提示级别')}}


def lessons():
    know = _know()
    rows = know.rows(KIND)
    records = _records(know)
    out = []
    for row in rows:
        mine = [item for item in records if item.get('学习') == row['id']]
        judged = [item for item in mine if item.get('结果') in ('对', '错', '部分')]
        steps = row.get('路线') or []
        answered = {item.get('题号') for item in mine}
        done = sum(1 for step in steps if step.get('检查') and all(item['id'] in answered for item in step['检查']))
        out.append({'id': row['id'], 'name': row['name'], '目标': row.get('目标'), '状态': row.get('状态'), '课题': row.get('课题') or [],
                    'progress': [done, len(steps)], 'answered': len(mine),
                    'right': round(sum(1 if item['结果'] == '对' else 0.5 if item['结果'] == '部分' else 0 for item in judged) / len(judged), 3) if judged else None,
                    'updated': row.get('updated_at')})
    out.sort(key=lambda item: str(item['updated'] or ''), reverse=True)
    return out


# ---------- 答题、判分 ----------

def _number(text):
    match = re.search(r'-?\d+(?:\.\d+)?(?:[eE]-?\d+)?', str(text or '').replace(',', ''))
    return float(match.group()) if match else None


def _grade(question, answer):
    kind, right = question.get('题型'), question.get('答案')
    if kind in ('单选', '判断'):
        norm = lambda value: re.sub(r'\s+', '', str(value or '')).lower()
        letter = lambda value: norm(value)[:1] if re.fullmatch(r'[a-d]([.、:：)）].*)?', norm(value)) else None
        options = question.get('选项') or []
        picked, wanted = norm(answer), norm(right)
        if picked == wanted or (letter(right) and options and 'abcd'.index(letter(right)) < len(options) and picked == norm(options['abcd'.index(letter(right))])):
            return '对'
        return '错'
    if kind == '填数':
        got, want = _number(answer), _number(right)
        if got is None or want is None:
            return '待讲评'
        tolerance = str(question.get('容差') or '5%')
        allowed = abs(want) * _number(tolerance) / 100 if '%' in tolerance else (_number(tolerance) or 0)
        return '对' if abs(got - want) <= allowed + 1e-12 else '错'
    return '待讲评'


def answer(ref, question_id, value, confidence=None, thinking='', hint=0, stage=None, by='你'):
    """他答一题：判分（简答题等讲评），存一条练习记录，返回结果、答案和解释。"""
    know = _know()
    lesson = know.get(ref, KIND)
    found = _questions(lesson['data']).get(question_id)
    if not found:
        raise store.StoreError(f'这个学习里没有题 {question_id}。')
    phase, step, question = found
    result = _grade(question, value)
    stamp = store.now()
    links = [{'relation': '学习', 'to': lesson['id']}]
    if step and step.get('命题'):
        links.append({'relation': '考的是', 'to': step['命题']})
    saved = know.save(PRACTICE, {'name': f'{lesson["name"][:20]} · {question_id} · {stamp[5:16]}', '题型': question.get('题型'), '题目': question.get('题目'),
                                 '你的答案': value, '正确答案': question.get('答案'), '结果': result,
                                 '把握程度': None if confidence in (None, '') else float(confidence), '思路': str(thinking or '').strip(),
                                 '提示级别': hint, '时间': stamp, '学习': lesson['id'], '题号': question_id, '阶段': stage or phase, '讲评': '',
                                 'links': links}, by=by)[0]
    return {'record': saved['id'], '结果': result, '正确答案': question.get('答案'), '解释': question.get('解释') or ''}


def grade(record_id, result, comment, by='智能体'):
    """助手讲评一条练习记录（简答题判分，或给思路写一句点评）。"""
    if result not in ('对', '错', '部分', '待讲评'):
        raise store.StoreError('结果写 对 / 错 / 部分。')
    _know().save(PRACTICE, {'id': record_id, '结果': result, '讲评': str(comment or '').strip()}, by=by)
    return {'record': record_id, '结果': result}


# ---------- 复习、把握校准 ----------

def _day(text):
    return datetime.strptime(str(text)[:10], '%Y-%m-%d').date()


def due(today=None):
    """该复习的检查题：答过的检查题按自己的记录排下次（连续答对 n 次 → 隔 INTERVALS[n-1] 天；最后一次错 → 明天）。"""
    know = _know()
    today = today or _day(store.now())
    records = _records(know)
    by_question = {}
    for row in records:
        if row.get('阶段') in ('检查', '复习'):
            by_question.setdefault((row['学习'], row['题号']), []).append(row)
    lessons_by_id = {row['id']: row for row in know.rows(KIND)}
    out = []
    for (lesson_id, question_id), rows in by_question.items():
        lesson = lessons_by_id.get(lesson_id)
        if not lesson:
            continue
        found = _questions(lesson).get(question_id)
        if not found:
            continue
        streak = 0
        for row in reversed(rows):
            if row.get('结果') != '对':
                break
            streak += 1
        last = rows[-1]
        if last.get('结果') == '待讲评':
            continue
        wait = INTERVALS[min(streak, len(INTERVALS)) - 1] if streak else 1
        next_day = _day(last['时间']) + timedelta(days=wait)
        if next_day <= today:
            phase, step, question = found
            out.append({'lesson': lesson_id, 'lesson_name': lesson['name'], 'step': step.get('标题') if step else '', 'question': question,
                        'last': last['结果'], 'last_at': last['时间'], 'streak': streak, 'due': next_day.isoformat()})
    out.sort(key=lambda item: (item['last'] == '对', item['due']))
    return out


def calibration():
    """把握校准：把握分五档，每档实际答对的比例（部分算半个）；和"高把握答错"的题（多半是误解）。"""
    records = [row for row in _records(_know()) if row.get('结果') in ('对', '错', '部分') and row.get('把握程度') is not None and row.get('阶段') != '先猜']
    bins = []
    for low in range(0, 100, 20):
        inside = [row for row in records if low <= float(row['把握程度']) < low + 20 or (low == 80 and float(row['把握程度']) == 100)]
        if inside:
            score = sum(1 if row['结果'] == '对' else 0.5 if row['结果'] == '部分' else 0 for row in inside) / len(inside)
            bins.append({'from': low, 'to': low + 20, 'n': len(inside), 'right': round(score, 3),
                         'confidence': round(sum(float(row['把握程度']) for row in inside) / len(inside), 1)})
    wrong = [_record_view(row) | {'学习': row.get('学习')} for row in records if row['结果'] == '错' and float(row['把握程度']) >= 70]
    return {'bins': bins, 'n': len(records), 'misconceptions': wrong[-12:][::-1]}


def view():
    from .literature import projects
    know = _know()
    claims = know.rows('命题') if know.kind('命题') else []
    _, links = know.sql("SELECT from_id, to_id FROM links WHERE relation='属于'")
    by_project = {}
    for source, target in links:
        by_project.setdefault(target, []).append(source)
    claim_ids = {row['id'] for row in claims}
    starters = [{'key': item['key'], 'name': item['name'], 'claims': len([cid for cid in by_project.get(item['key'], []) if cid in claim_ids])}
                for item in projects()]
    return clean({'lessons': lessons(), 'due': due(), 'calibration': calibration(), 'projects': starters, 'claims': len(claims)})
