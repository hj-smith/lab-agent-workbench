"""数据体检：按库里的命名规则找断链、候选关联、异常点和待确认。

旧平台的 `_health` 把编号正则写死在 analysis/service.py 里；这里改成从设置读
`平台.命名规则`、`平台.写法别名`、`平台.体检编号模式`、`平台.分析默认值`。
关联规则只搬 R1（测量↔配方按编号）和 R4（GPC 样→树脂批）；R2/R3 需要拟合证据
或曲线数组，属于"算法核对"，不在体检里重跑。

用法：
    lib = store.open('产品A')
    report = health.check(lib)
    health.confirm(lib, report['candidates'][0]['items'], remember=True)
    health.dismiss(lib, report['candidates'][1]['items'])
"""
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

BEIJING = timezone(timedelta(hours=8))
FORMULA_REL = 'relates_to_formula'
RESIN_REL = 'possible_resin_source'
FALLBACK_OUTLIER = {'MAD 倍数': 3.5, '低 k 阈值': 0.05, '同组 k 中位数': 0.3}


# ---------- 编号归一 ----------

def _suffix_is_condition(text):
    """后缀是不是条件/工艺写法，而不是编号本身的一部分（如 T16C 的 C）。"""
    if text[:1] in '-_ (（':
        return True
    low = text.lower()
    return any(word in low for word in
               ('℃', '°c', 'rpm', 'solva', 'meoh', 'hep', 'pc', '稀释',
                '重复', 'rep', 'repeat', '模型', 'model'))


def _looks_like_code(text):
    """剥完还剩一个像编号的东西；防止 R100-38 被剥成 R100。"""
    return bool(re.match(r'(?:PA|R100|PB)[-_].*[A-Za-z0-9]', text))


def normalize(lib, code):
    """按 `平台.命名规则` 归一编号：前缀、别名、前导零、-FP、条件后缀。"""
    text = re.sub(r'\s+', '', str(code or '').strip())
    if not text:
        return ''
    aliases = (lib.setting('平台.写法别名') or {}).get('别名') or []
    for item in aliases:
        if text.casefold() == str(item.get('从', '')).casefold():
            text = str(item['到'])
            break
    rules = lib.setting('平台.命名规则') or {}
    for canonical, spellings in (rules.get('R1 产品前缀归一') or {}).items():
        if canonical == '去掉编号前导零' or not isinstance(spellings, list):
            continue
        for spelling in sorted(spellings, key=len, reverse=True):
            text = re.sub(re.escape(spelling), canonical, text, count=1, flags=re.I)
    text = re.sub(r'^(PA|R100|PB)([_-]?)0+(\d+)', r'\1\2\3', text)
    for suffix in rules.get('R6 FP 后缀剥离') or ['-FP']:
        if text.upper().endswith(str(suffix).upper()):
            text = text[:-len(str(suffix))]
    patterns = rules.get('R2 条件后缀剥离（正则，按顺序）') or []
    changed = True
    while changed:
        changed = False
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match or match.start() == 0:
                continue
            rest = text[:match.start()].rstrip('-_ ')
            if not _suffix_is_condition(match.group(0)) or not _looks_like_code(rest):
                continue
            text = rest
            changed = True
            break
    return text.upper()


def _outlier_rules(lib):
    defaults = lib.setting('平台.分析默认值') or {}
    rules = defaults.get('异常点') or {}
    return {
        'mad': float(rules.get('MAD 倍数', FALLBACK_OUTLIER['MAD 倍数'])),
        'low_k': float(rules.get('低 k 阈值', FALLBACK_OUTLIER['低 k 阈值'])),
        'group_k': float(rules.get('同组 k 中位数', FALLBACK_OUTLIER['同组 k 中位数'])),
    }


def _code_field(row, kind):
    if kind == '膜':
        return row.get('样品编号')
    if kind == 'NK分组':
        return row.get('分组')
    if kind == 'GPC样品':
        return row.get('样品名')
    if kind == '配方':
        return row.get('配方编号')
    if kind == '树脂批':
        return row.get('批号')
    if kind == '树脂实验':
        return row.get('编号')
    return row.get('name')


# ---------- 候选 ----------

def _dismissed(lib):
    items = lib.setting('体检.不是') or []
    return {(item.get('从'), item.get('到'), item.get('关系')) for item in items}


def _confirmed_pairs(lib):
    _, rows = lib.sql("SELECT from_id, to_id, relation FROM links WHERE status='confirmed'")
    return {(row[0], row[1], row[2]) for row in rows}


def _formulas(lib):
    """配方编号 → 该编号的记录（可能多个修订）。"""
    result = defaultdict(list)
    for row in lib.rows('配方'):
        result[normalize(lib, row.get('配方编号'))].append(row)
    for rows in result.values():
        rows.sort(key=lambda row: (row.get('修订') or 0))
    return result


def _formula_target(rows):
    return max(rows, key=lambda row: (row.get('修订') or 0))


def _measurements(lib):
    """体检用的测量：膜（椭偏块）和 NK分组（同组曲线算一次）。"""
    result = []
    reference = lib.setting('平台.默认参比') or 'REF01'
    for row in lib.rows('膜'):
        if row.get('对应状态') == '参比':
            continue
        if normalize(lib, row.get('样品编号')) == normalize(lib, reference):
            continue
        result.append({'id': row['id'], 'name': row['name'], 'kind': '膜',
                       'raw': row.get('样品编号'), 'status': row.get('对应状态')})
    for row in lib.rows('NK分组'):
        raw = row.get('分组')
        if str(row.get('轮次') or '').startswith('参比'):
            continue
        if normalize(lib, raw) == normalize(lib, reference):
            continue
        result.append({'id': row['id'], 'name': row['name'], 'kind': 'NK分组',
                       'raw': raw, 'status': None})
    for item in result:
        item['code'] = normalize(lib, item['raw'])
    return result


def _r1_candidates(lib, formulas, dismissed, confirmed):
    pattern = (lib.setting('平台.体检编号模式') or {}).get('有测量的配方编号')
    groups = defaultdict(list)
    for item in _measurements(lib):
        if pattern and not re.fullmatch(pattern, item['code']):
            continue
        if item['code'] not in formulas:
            continue
        target = _formula_target(formulas[item['code']])
        if item['status'] == '已确认':
            continue
        key = (item['id'], target['id'], FORMULA_REL)
        if key in confirmed or key in dismissed:
            continue
        groups[item['code']].append({
            'from': item['id'], 'from_name': item['name'],
            'to': target['id'], 'to_name': target['name'], 'relation': FORMULA_REL,
        })
    result = []
    for code, items in sorted(groups.items()):
        result.append({
            '组': code,
            '依据': f'编号归一后与配方编号相同（{len(items)} 条测量）',
            '规则': 'R1 测量↔配方按编号',
            'items': items,
        })
    return result


def _duplicate_samples(lib):
    """`平台.重复剔除`：项目里复制来的样品不算那个项目。"""
    return {(str(item.get('项目')), str(item.get('样品')))
            for item in lib.setting('平台.重复剔除') or []}


def _gpc_branch(lib, name):
    """GPC 名末尾的 A/B 分支；时间点 0H/15H 没有分支。"""
    text = normalize(lib, name)
    match = re.search(r'[-_ ]([AB])$', text)
    return match.group(1) if match else None


def _r4_candidates(lib, dismissed, confirmed):
    pattern = (lib.setting('平台.体检编号模式') or {}).get('GPC 要挂树脂批的项目')
    if not pattern:
        return [], {}
    formulas = {}
    for item in lib.rows('树脂实验'):
        formulas[str(item.get('编号'))] = item
    lots = defaultdict(list)
    for item in lib.rows('树脂批'):
        lots[str(item.get('实验编号'))].append(item)
    duplicates = _duplicate_samples(lib)
    groups = defaultdict(list)
    fixes = {}
    for row in lib.rows('GPC样品'):
        name = str(row.get('样品名') or '')
        if not re.search(pattern, name, re.I):
            continue
        if (str(row.get('所属项目')), name) in duplicates:
            continue
        if any(from_id == row['id'] and relation in (RESIN_REL, '属于')
               for from_id, _, relation in confirmed):
            continue
        match = re.search(r'R100-\d+', name)
        if not match:
            continue
        exp = match.group(0)
        branch = _gpc_branch(lib, name)
        target = None
        relation = RESIN_REL
        if branch:
            for lot in lots.get(exp, []):
                if normalize(lib, lot.get('批号')) == exp + branch:
                    target = lot
                    break
        if target:
            group = exp + branch
        elif exp in formulas:
            target = formulas[exp]
            relation = '属于'
            group = exp
        else:
            continue
        key = (row['id'], target['id'], relation)
        if key in dismissed:
            continue
        fixes[row['id']] = (group, target['name'])
        groups[group].append({
            'from': row['id'], 'from_name': row['name'],
            'to': target['id'], 'to_name': target['name'], 'relation': relation,
        })
    result = []
    for group, items in sorted(groups.items()):
        result.append({
            '组': group,
            '依据': (f'{len(items)} 个 GPC 样品；' +
                     ('按分支挂到树脂批' if any(i['relation'] == RESIN_REL for i in items) else '挂到树脂实验')),
            '规则': 'R4 GPC 样→树脂批/实验',
            'items': items,
        })
    return result, fixes


# ---------- 断链 ----------

def _break(kind, row, note):
    return {'类型': kind, '记录 id': row['id'], '名字': row['name'], '说明': note}


def _measurement_breaks(lib, formulas):
    pattern = (lib.setting('平台.体检编号模式') or {}).get('有测量的配方编号')
    found = defaultdict(list)
    for item in _measurements(lib):
        if pattern and not re.fullmatch(pattern, item['code']):
            continue
        found[item['code']].append(item)
    result = []
    for code in sorted(set(found) - set(formulas)):
        items = found[code]
        first = next((item for item in items if item['kind'] == '膜'), items[0])
        kinds = Counter(item['kind'] for item in items)
        more = f'；同编号共 {len(items)} 条：' + '、'.join(f'{k} {v} 条' for k, v in kinds.items()) if len(items) > 1 else ''
        result.append(_break('有测量没配方', first,
                             f'测量编号 {code} 在配方里找不到{more}'))
    measured = set(found)
    result.sort(key=lambda item: item['说明'])
    for code in sorted(set(formulas) - measured):
        rows = formulas[code]
        target = _formula_target(rows)
        lots = '、'.join(sorted({str(lot) for row in rows for lot in (row.get('树脂批') or [])}))
        note = f'配方 {code}（{len(rows)} 个修订）没有椭偏或 NK 测量'
        if lots:
            note += f'；树脂批：{lots}'
        result.append(_break('有配方没测量', target, note))
    return result


def _gpc_breaks(lib, fixes):
    pattern = (lib.setting('平台.体检编号模式') or {}).get('GPC 要挂树脂批的项目')
    if not pattern:
        return []
    confirmed = _confirmed_pairs(lib)
    result = []
    for row in lib.rows('GPC样品'):
        name = str(row.get('样品名') or '')
        if not re.search(pattern, name, re.I):
            continue
        linked = any(from_id == row['id'] and relation in (RESIN_REL, '属于')
                     for from_id, _, relation in confirmed)
        if linked:
            continue
        note = f'GPC 样品 {name} 还没挂到树脂批或实验'
        if row['id'] in fixes:
            group, target = fixes[row['id']]
            note += f'；按编号可挂到 {target}（候选组 {group}）'
        result.append(_break('GPC 没挂树脂批或实验', row, note))
    return result


def _resin_breaks(lib):
    lots = [str(row.get('批号')) for row in lib.rows('树脂批')]
    experiments = {str(row.get('编号')) for row in lib.rows('树脂实验')}
    requested = defaultdict(list)
    for row in lib.rows('配方'):
        for part in row.get('树脂来源') or row.get('树脂批') or []:
            code = re.sub(r'-\d+(?:\.\d+)?%$', '', str(part))
            requested[code].append(row)
    result = []
    for code, rows in sorted(requested.items()):
        hit = [lot for lot in lots if lot == code or lot.startswith(code + '-')]
        if hit:
            continue
        target = rows[0]
        note = f'配方（{len(rows)} 条）的树脂来源 {code} 在树脂批里找不到'
        if code in experiments:
            note += f'；有树脂实验 {code}，但没有对应的干品批'
        exp = re.match(r'(R100-\d+)', code)
        close = [lot for lot in lots
                 if exp and lot.startswith(exp.group(1) + '-')][:3]
        if close:
            note += '；相近的批：' + '、'.join(close)
        result.append(_break('配方树脂来源找不到', target, note))
    return result


# ---------- 异常点 ----------

def _film_values(lib):
    """每个膜块第一阶段的处理前 k193 和 SolvA 膜厚损失率。"""
    stats = {item['id']: item['value'] for item in lib.results('椭偏.块统计', '膜')}
    values = []
    for row in lib.rows('膜'):
        if row.get('对应状态') == '参比':
            continue
        stages = ((stats.get(row['id']) or {}).get('各阶段') or [])
        if not stages:
            continue
        stage = stages[0]
        k193 = ((stage.get('光学') or {}).get('k_193') or {}).get('处理前')
        values.append({'row': row, 'k193': k193, 'loss': stage.get('损失率 %')})
    return values


def _outliers(lib):
    rules = _outlier_rules(lib)
    groups = defaultdict(list)
    for item in _film_values(lib):
        groups[item['row'].get('测试日') or '未标测试日'].append(item)
    result = []
    for day, items in sorted(groups.items()):
        for field, key in (('k193（处理前）', 'k193'), ('膜厚损失率 %', 'loss')):
            numbers = [item[key] for item in items if isinstance(item[key], (int, float))]
            if len(numbers) < 3:
                continue
            center = median(numbers)
            mad = median(abs(value - center) for value in numbers)
            mad_rule = f'{rules["mad"]:g} MAD'
            for item in items:
                value = item[key]
                if not isinstance(value, (int, float)):
                    continue
                rules_hit = []
                if mad > 0 and abs(value - center) > rules['mad'] * mad:
                    rules_hit.append(mad_rule)
                if key == 'k193' and value < rules['low_k'] and center > rules['group_k']:
                    rules_hit.append('稀释块')
                if not rules_hit:
                    continue
                note = (f'{day} 组内中位数 {center:.6g}，MAD {mad:.6g}，'
                        f'{rules["mad"]:g}×MAD 界限 {rules["mad"] * mad:.6g}')
                if '稀释块' in rules_hit:
                    variant = item['row'].get('变体') or ''
                    note += (f'；变体={variant or "未写"}，k193 {value:.6g} < {rules["low_k"]:g}'
                             f' 且同组中位数 > {rules["group_k"]:g}，按稀释块标记')
                result.append({
                    '记录 id': item['row']['id'],
                    '名字': item['row']['name'],
                    '字段': field,
                    '值': round(value, 6),
                    '规则': ' + '.join(rules_hit),
                    '说明': note,
                })
    return result


# ---------- 待确认 ----------

def _pending(lib):
    found = {}

    def add(row, field):
        found.setdefault((row['id'], field), (row, field))

    for kind in lib.kinds():
        for row in lib.rows(kind['name']):
            for field in row.get('unconfirmed') or []:
                add(row, field)
    for row in lib.rows('老化谱'):
        if not row.get('条件'):
            add(row, '条件')
        if row.get('天数') is None:
            add(row, '天数')
        if not row.get('测试日'):
            add(row, '测试日')
    for row in lib.rows('膜'):
        if (row.get('条件来源') or {}).get('测试日') == '文件名':
            add(row, '测试日')
        if row.get('处理待确认'):
            add(row, '处理')
    return [{'记录 id': row['id'], '名字': row['name'], '字段': field}
            for row, field in sorted(found.values(), key=lambda item: (item[0]['id'], item[1]))]


# ---------- 总检查 ----------

def check(lib):
    """返回体检表：breaks / candidates / outliers / pending / counts。"""
    formulas = _formulas(lib)
    dismissed = _dismissed(lib)
    confirmed = _confirmed_pairs(lib)
    r1 = _r1_candidates(lib, formulas, dismissed, confirmed)
    r4, fixes = _r4_candidates(lib, dismissed, confirmed)
    candidates = r1 + r4
    breaks = (_measurement_breaks(lib, formulas) + _gpc_breaks(lib, fixes) +
              _resin_breaks(lib))
    outliers = _outliers(lib)
    pending = _pending(lib)
    break_kinds = Counter(item['类型'] for item in breaks)
    return {
        'breaks': breaks,
        'candidates': candidates,
        'outliers': outliers,
        'pending': pending,
        'counts': {
            'breaks': len(breaks),
            'candidate_groups': len(candidates),
            'candidate_items': sum(len(group['items']) for group in candidates),
            'outliers': len(outliers),
            'pending': len(pending),
            'break_kinds': dict(break_kinds),
            'records': {kind['name']: kind['records'] for kind in lib.kinds()},
        },
    }


# ---------- 确认 / 不是 ----------

def _items(items):
    """既收候选组，也收单条 item。"""
    if isinstance(items, dict):
        items = [items]
    result = []
    for item in items:
        result.extend(item.get('items') or [item])
    return result


def _raw_code(lib, record_id):
    record = lib.get(record_id)
    return str(_code_field(record['data'] | {'name': record['name']}, record['kind']) or record['name'])


def confirm(lib, items, remember=False):
    """把选中的候选写成 confirmed 关联，by='人'；remember 时记进 `平台.人工对应`。"""
    written = []
    for item in _items(items):
        relation = item.get('relation') or FORMULA_REL
        source = lib.get(item['from'])
        lib.save(source['kind'], [{'id': item['from'], 'links': [{
            'relation': relation, 'to': item['to'], 'status': 'confirmed', 'by': '人',
        }]}], by='人')
        written.append({'from': item['from'], 'relation': relation, 'to': item['to']})
        if not remember:
            continue
        target = lib.get(item['to'])
        code = _raw_code(lib, item['from'])
        target_code = str(_code_field(target['data'] | {'name': target['name']}, target['kind']) or target['name'])
        module = {'膜': '椭偏', 'NK分组': 'NK', 'GPC样品': 'gpc'}.get(source['kind'], source['kind'])
        mapping = lib.setting('平台.人工对应') or []
        if any(str(entry.get('从')) == code and str(entry.get('到')) == target_code for entry in mapping):
            continue
        mapping.append({
            '从': code, '到': target_code, '从的模块': module,
            '到的类型': target['kind'], '说明': '数据体检确认后记住',
            '依据': f'人确认：{source["name"]} → {target["name"]}',
        })
        lib.set_setting('平台.人工对应', mapping, '人工对应（数据体检确认时追加）')
    return written


def dismiss(lib, items):
    """人说"不是"的候选，记进 `体检.不是`，下次不再提。"""
    rejected = lib.setting('体检.不是') or []
    seen = {(item.get('从'), item.get('到'), item.get('关系')) for item in rejected}
    added = []
    for item in _items(items):
        relation = item.get('relation') or FORMULA_REL
        key = (item.get('from'), item.get('to'), relation)
        if key in seen:
            continue
        rejected.append({
            '从': item.get('from'), '从名': item.get('from_name', ''),
            '到': item.get('to'), '到名': item.get('to_name', ''),
            '关系': relation, '说明': '数据体检查过，不是',
            '时间': datetime.now(BEIJING).strftime('%Y-%m-%d %H:%M:%S'),
        })
        seen.add(key)
        added.append(key)
    lib.set_setting('体检.不是', rejected, '数据体检里人说"不是"的候选，下次不再提')
    return added
