"""文献监测和文献问答。都存在知识库里（几个产品共用）。

监测：几个"主题"，每个是一条 OpenAlex 检索式（只用化学名、CAS、通用词；会发到外面）。查一遍 = 每个开着的主题
按相关度取发表日期在"上次查 − 30 天"之后的前 10 篇（第一次往回一年），按 OpenAlex id、DOI、标题去重，
新的存成「文献线索」（待看）。助手筛的时候只写相关度和一句意见；留不留由人定：留 → 新建一条「文献」，舍 → 只在线索上记理由。

问答：在 文献、命题、推理链 上做 BM25（英文词 + 中文双字），马上列出最相关的几条和命中的那句话；
要成段的回答交给助手（技能「文献问答」），它只用库里的、每句标出处，库里没有就明说。
"""
import math
import re
from datetime import date, timedelta
from pathlib import Path

from . import config, store
from .present import clean
from .research import _get

LEAD = '文献线索'
TOPICS_KEY = '文献监测.主题'
LOOKBACK_DAYS = 365      # 第一次查往回看多久
OVERLAP_DAYS = 30        # 之后从上次查的那天再往前多看多久（OpenAlex 收录有延迟）
PER_FIRST = 10           # 第一次（往回一年）每个主题只取最相关的这么多篇
PER_TOPIC = 25           # 之后每次窗口里最多取这么多篇
SKIP_TYPES = ('peer-review', 'paratext', 'erratum', 'retraction')
TYPE_NAMES = {'article': '论文', 'review': '综述', 'preprint': '预印本', 'book-chapter': '书的章节', 'book': '书',
              'dissertation': '学位论文', 'report': '报告', 'letter': '快报', 'dataset': '数据集'}

DEFAULT_TOPICS = [
    {'name': '旋涂膜厚和溶剂', 'query': '"spin coating" AND thickness AND (solvent OR evaporation OR viscosity)', '课题': []},
]

LEAD_ABOUT = '文献监测查到的新论文，等人看：待看 / 留（已存成文献）/ 舍（写了理由）。助手筛的时候写相关度和意见。'
LEAD_FIELDS = [{'key': key} for key in ('标题', '作者', '年份', '日期', '期刊', '类型', 'DOI', '链接', '免费全文', '被引', '摘要', '主题', '课题',
                                        '状态', '相关度', '意见', '理由', '文献', '发现于')]


# ---------- 主题 ----------

def projects():
    """所有产品库的课题：[{key: '库:id', name, library}]，主题挂课题用。"""
    out = []
    for name in config.libraries():
        if name == config.KNOWLEDGE:
            continue
        lib = store.open(name)
        if lib.kind('课题'):
            out += [{'key': f'{name}:{row["id"]}', 'name': row['name'], 'library': name} for row in lib.rows('课题')]
    return out


def topics():
    know = store.knowledge()
    items = know.setting(TOPICS_KEY)
    if items is None:
        items = [{**item, 'on': True} for item in DEFAULT_TOPICS]
    return items


def save_topics(items):
    """页面上改主题：按名字留住每个主题的"上次查""查到过几篇"。"""
    old = {item['name']: item for item in topics()}
    kept = []
    for item in items:
        name = str(item.get('name') or '').strip()
        if not name:
            continue
        before = old.get(name, {})
        kept.append({'name': name, 'query': str(item.get('query') or '').strip(), '课题': list(item.get('课题') or []), 'on': bool(item.get('on', True)),
                     **{key: before[key] for key in ('last', 'found', 'total', 'error') if key in before}})
    store.knowledge().set_setting(TOPICS_KEY, kept, '文献监测的主题：OpenAlex 检索式（只用化学名和通用词），挂到哪些课题，上次查的时间')
    return kept


def reset_topics():
    """恢复默认主题（自己加的主题去掉；默认主题"上次查"的记录留着）。"""
    old = {item['name']: item for item in topics()}
    items = [{**item, 'on': True, **{key: old[item['name']][key] for key in ('last', 'found', 'total') if key in old.get(item['name'], {})}}
             for item in DEFAULT_TOPICS]
    store.knowledge().set_setting(TOPICS_KEY, items)
    return items


# ---------- 查新 ----------

def _norm_title(text):
    return re.sub(r'[^a-z0-9一-鿿]+', '', str(text or '').lower())


def _seen(know):
    ids, dois, titles = set(), set(), set()
    for row in (know.rows(LEAD) + know.rows(LEAD, deleted=True)) if know.kind(LEAD) else []:
        ids.add(row['id'])
        if row.get('DOI'):
            dois.add(str(row['DOI']).lower())
        titles.add(_norm_title(row.get('标题')))
    for row in know.rows('文献') if know.kind('文献') else []:
        dois.update(item.lower().rstrip('.,;)') for item in re.findall(r'10\.\d{4,9}/[^\s"<>]+', str(row.get('链接或路径') or '')))
        titles.add(_norm_title(row.get('标题')))
    return ids, dois, titles


def _abstract(work):
    words = sorted((place, word) for word, places in (work.get('abstract_inverted_index') or {}).items() for place in places)
    return ' '.join(word for _, word in words)


def _query(topic, since, count):
    query = topic['query'].replace(',', ' ')         # 逗号在 OpenAlex 的 filter 里是分隔符
    filters = [f'title_and_abstract.search:{query}', f'from_publication_date:{since}'] + [f'type:!{name}' for name in SKIP_TYPES]
    params = {'filter': ','.join(filters), 'per-page': count,
              'select': 'id,title,publication_year,publication_date,authorships,doi,primary_location,open_access,cited_by_count,abstract_inverted_index,type'}
    if config.key_for('openalex'):                   # 有自己的免费密钥（openalex.org/settings/api）就用，每天额度大很多；没有也能查
        params['api_key'] = config.key_for('openalex')
    return _get('https://api.openalex.org/works', params=params).json()


def check(names=None):
    """查一遍：返回 {new: [线索], topics: [{name, new, matched, since, error}]}。"""
    know = store.knowledge()
    if not know.kind(LEAD):
        know.define(LEAD, LEAD_ABOUT, LEAD_FIELDS, group='知识')
    ids, dois, titles = _seen(know)
    names_by_key = {item['key']: item['name'] for item in projects()}
    items, report, fresh = topics(), [], []
    stamp, today = store.now(), date.fromisoformat(store.now()[:10])
    for topic in items:
        if not topic.get('on', True) or (names and topic['name'] not in names) or not topic.get('query'):
            continue
        last = topic.get('last')
        since = (date.fromisoformat(last[:10]) - timedelta(days=OVERLAP_DAYS)) if last else today - timedelta(days=LOOKBACK_DAYS)
        try:
            found = _query(topic, since.isoformat(), PER_TOPIC if last else PER_FIRST)
        except store.StoreError as error:
            topic['error'] = str(error)
            report.append({'name': topic['name'], 'new': 0, 'matched': None, 'since': since.isoformat(), 'error': str(error)})
            continue
        rows = []
        for work in found.get('results', []):
            lead_id = 'lead-' + work['id'].rsplit('/', 1)[-1]
            doi = (work.get('doi') or '').replace('https://doi.org/', '').lower()
            title = work.get('title') or ''
            if lead_id in ids or (doi and doi in dois) or _norm_title(title) in titles or not title:
                continue
            ids.add(lead_id)
            dois.add(doi)
            titles.add(_norm_title(title))
            location = work.get('primary_location') or {}
            authors = [item['author']['display_name'] for item in (work.get('authorships') or [])]
            rows.append({'id': lead_id, 'name': title[:90], '标题': title, '作者': '、'.join(authors[:6]) + (' 等' if len(authors) > 6 else ''),
                         '年份': work.get('publication_year'), '日期': work.get('publication_date'), '期刊': (location.get('source') or {}).get('display_name') or '',
                         '类型': TYPE_NAMES.get(work.get('type'), work.get('type') or ''), 'DOI': doi,
                         '链接': work.get('doi') or location.get('landing_page_url') or work['id'],
                         '免费全文': (work.get('open_access') or {}).get('oa_url') or '', '被引': work.get('cited_by_count') or 0,
                         '摘要': _abstract(work), '主题': topic['name'], '课题': '、'.join(names_by_key.get(key, key) for key in topic.get('课题') or []),
                         '状态': '待看', '相关度': '', '意见': '', '理由': '', '文献': '', '发现于': stamp})
        if rows:
            know.save(LEAD, rows, source=f'文献监测：{topic["name"]}', by='文献监测')
        topic.update({'last': stamp, 'found': len(rows), 'total': int(topic.get('total') or 0) + len(rows)})
        topic.pop('error', None)
        report.append({'name': topic['name'], 'new': len(rows), 'matched': found.get('meta', {}).get('count'), 'since': since.isoformat()})
        fresh += rows
    know.set_setting(TOPICS_KEY, items)
    return clean({'new': [_lead_view(row) for row in fresh], 'topics': report, 'at': stamp})


# ---------- 线索：看、留、舍 ----------

def _lead_view(row):
    keys = ('标题', '作者', '年份', '日期', '期刊', '类型', 'DOI', '链接', '免费全文', '被引', '摘要', '主题', '课题', '状态', '相关度', '意见', '理由', '文献', '发现于')
    return {'id': row['id'], **{key: row.get(key) for key in keys}}


def leads(status=None, limit=None):
    know = store.knowledge()
    rows = know.rows(LEAD) if know.kind(LEAD) else []
    rank = {'高': 0, '中': 1, '低': 2}
    rows.sort(key=lambda row: (str(row.get('发现于') or ''), row.get('被引') or 0), reverse=True)        # 新到的在前
    rows.sort(key=lambda row: (row.get('状态') != '待看', rank.get(row.get('相关度'), 1.5)))          # 待看的在前，助手说高相关的更前
    rows = [row for row in rows if not status or row.get('状态') == status]
    return [_lead_view(row) for row in rows[:limit]]


def decide(lead_id, action, reason='', by='你'):
    """action：keep（留，存成文献）/ drop（舍，写理由）/ reopen（改回待看；留过的那条文献进回收站）。"""
    know = store.knowledge()
    lead = know.get(lead_id, LEAD)
    data = lead['data']
    reason = str(reason or '').strip()
    if action == 'keep':
        if data.get('文献'):                          # 留过又改回待看的：把那条文献从回收站拿回来
            if _deleted(know, data['文献']):
                know.delete(data['文献'], restore=True)
            know.save(LEAD, {'id': lead_id, '状态': '留', '理由': reason or data.get('理由') or ''}, by=by)
            return {'lead': lead_id, '文献': data['文献']}
        year = data.get('年份')
        links = [{'relation': '属于', 'to': key} for key in _project_keys(data.get('课题'))]
        saved = know.save('文献', {'name': f'{str(data.get("标题"))[:70]}｜{year}' if year else str(data.get('标题'))[:80],
                                   '标题': data.get('标题'), '作者': data.get('作者'), '年份': year, '类型': f'{data.get("类型") or "论文"}（文献监测，摘要）',
                                   '链接或路径': '\n'.join(item for item in (data.get('链接'), data.get('免费全文')) if item),
                                   '取舍': '留', '理由': reason or data.get('意见') or '', '要点': f'摘要（还没读全文）：{data.get("摘要") or "（OpenAlex 没有摘要）"}',
                                   '期刊': data.get('期刊'), 'DOI': data.get('DOI'), 'links': links},
                          source=f'文献监测：{data.get("主题")}', by=by)[0]
        know.save(LEAD, {'id': lead_id, '状态': '留', '理由': reason, '文献': saved['id']}, by=by)
        return {'lead': lead_id, '文献': saved['id'], 'name': saved['name']}
    if action == 'drop':
        if data.get('文献') and not _deleted(know, data['文献']):
            know.delete(data['文献'])
        know.save(LEAD, {'id': lead_id, '状态': '舍', '理由': reason}, by=by)
        return {'lead': lead_id}
    if action == 'reopen':
        if data.get('文献') and not _deleted(know, data['文献']):
            know.delete(data['文献'])                  # 进回收站，能拿回来
        know.save(LEAD, {'id': lead_id, '状态': '待看'}, by=by)
        return {'lead': lead_id}
    raise store.StoreError(f'不认识的动作 {action}：keep / drop / reopen。')


def _deleted(know, record_id):
    _, rows = know.sql('SELECT deleted_at FROM records WHERE id=?', (record_id,))
    return bool(rows and rows[0][0])


def _project_keys(text):
    wanted = {item.strip() for item in re.split(r'[、,，]', str(text or '')) if item.strip()}
    return [item['key'] for item in projects() if item['name'] in wanted]


def screen(lead_id, relevance, note, by='智能体'):
    """助手筛线索：写相关度（高 / 中 / 低）和一句意见。留不留由人定。"""
    if relevance not in ('高', '中', '低'):
        raise store.StoreError('相关度写 高 / 中 / 低。')
    store.knowledge().save(LEAD, {'id': lead_id, '相关度': relevance, '意见': str(note or '').strip()}, by=by)
    return {'lead': lead_id, '相关度': relevance}


# ---------- 问答：BM25 ----------

STOP_WORDS = {'the', 'and', 'of', 'in', 'on', 'for', 'to', 'with', 'by', 'is', 'are', 'an', 'as', 'at', 'or', 'from', 'that', 'this', 'be', 'it', 'its'}
STOP_PAIRS = {'什么', '怎么', '为什', '哪个', '如何', '可以', '能不', '不能', '是不', '不是', '有没', '没有', '我们', '这个', '那个', '一个', '是否',
              '的话', '有什', '么样', '么影', '个问', '问题', '之间', '还是', '会不', '不会'}
FIELDS = {
    '文献': [('标题', 2.0), ('name', 1.0), ('要点', 1.0), ('理由', 0.6), ('作者', 0.3)],
    '命题': [('陈述', 2.0), ('适用范围', 0.8), ('说明', 1.0), ('验证缺口', 0.6), ('name', 1.0)],
    '推理链': [('name', 1.5), ('说明', 1.0)],
}
BODY = {'文献': ('要点', '理由'), '命题': ('陈述', '说明', '适用范围', '验证缺口'), '推理链': ('说明',)}      # 摘句从这些里挑（标题已经显示了）
FUNCTION_CHARS = set('的了吗呢在是')                                                           # 带这些字的双字不算词


def _tokens(text):
    text = str(text or '').lower()
    out = []
    for word in re.findall(r'[a-z][a-z0-9\-]*[a-z0-9]|\d+(?:\.\d+)?', text):
        if len(word) > 3 and word.endswith('s') and not word.endswith('ss'):
            word = word[:-1]
        if word not in STOP_WORDS:
            out.append(word)
    for run in re.findall(r'[一-鿿]+', text):
        out += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)
                                            if run[i:i + 2] not in STOP_PAIRS and not FUNCTION_CHARS & set(run[i:i + 2])]
    return out


def _docs(know):
    docs = []
    for kind, fields in FIELDS.items():
        if not know.kind(kind):
            continue
        for row in know.rows(kind):
            bag, text = {}, []
            for key, weight in fields:
                value = row.get(key) or ''
                text.append(str(value))
                for token in _tokens(value):
                    bag[token] = bag.get(token, 0) + weight
            body = '\n'.join(str(row.get(key) or '') for key in BODY[kind]).strip() or '\n'.join(text)
            docs.append({'row': row, 'kind': kind, 'bag': bag, 'length': sum(bag.values()), 'text': body})
            if kind == '文献' and row.get('全文页数'):                     # 拖进来的 PDF：每一页单独当一条，命中时说第几页
                try:
                    _, pages, _ = know.table(row['id'], '全文')
                except store.StoreError:
                    pages = []
                for number, page in pages:
                    page_bag = {}
                    for token in _tokens(page):
                        page_bag[token] = page_bag.get(token, 0) + 1
                    if page_bag:
                        docs.append({'row': row, 'kind': kind, 'bag': page_bag, 'length': sum(page_bag.values()), 'text': page, 'page': int(number)})
    return docs


def _snippet(text, wanted):
    parts = [part.strip() for part in re.split(r'(?<=[。；;！？!?])|\n|(?<=\.)\s', text) if len(part.strip()) > 4]
    if not parts:
        return ''
    best = max(parts, key=lambda part: len(set(_tokens(part)) & wanted))
    return best if len(best) <= 160 else best[:158] + '…'


def find(question, limit=10):
    """问一句话：返回最相关的文献、命题、推理链（得分、命中句、状态 / 取舍、把握、出处几条）。"""
    know = store.knowledge()
    docs = _docs(know)
    wanted = [token for token in _tokens(question)]
    if not docs or not wanted:
        return {'hits': [], 'tokens': wanted, 'total': len(docs)}
    average = sum(doc['length'] for doc in docs) / len(docs) or 1.0
    df = {}
    for doc in docs:
        for token in set(doc['bag']):
            df[token] = df.get(token, 0) + 1
    k1, b = 1.4, 0.75
    for doc in docs:
        score = 0.0
        for token in set(wanted):
            tf = doc['bag'].get(token)
            if not tf:
                continue
            idf = math.log(1 + (len(docs) - df[token] + 0.5) / (df[token] + 0.5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * doc['length'] / average))
        doc['score'] = score
    _, links = know.sql("SELECT from_id, relation, to_id FROM links WHERE relation IN ('出处', '证据')")
    cites = {}
    for source, relation, target in links:
        cites.setdefault(target if relation == '出处' else source, []).append(source if relation == '出处' else target)
    ranked, seen = [], {}
    for doc in sorted((doc for doc in docs if doc['score'] > 0), key=lambda doc: -doc['score']):    # 同一篇最多出两处（要点或两页）
        seen[doc['row']['id']] = seen.get(doc['row']['id'], 0) + 1
        if seen[doc['row']['id']] <= 2:
            ranked.append(doc)
        if len(ranked) >= limit:
            break
    top = ranked[0]['score'] if ranked else 1.0
    hits = []
    for doc in ranked:
        row = doc['row']
        hits.append({'id': row['id'], 'kind': doc['kind'], 'name': row['name'], 'score': round(doc['score'] / top, 3),
                     'snippet': _snippet(doc['text'], set(wanted)),
                     'state': row.get('状态') if doc['kind'] == '命题' else row.get('取舍'), '把握': row.get('把握'),
                     'year': row.get('年份'), 'cited_by': len(cites.get(row['id'], [])) if doc['kind'] == '文献' else None,
                     'evidence': len(cites.get(row['id'], [])) if doc['kind'] == '命题' else None,
                     'page': doc.get('page'), 'pdf': row.get('PDF') if doc['kind'] == '文献' else None})
    return clean({'hits': hits, 'tokens': sorted(set(wanted)), 'total': len(docs)})


# ---------- 把手上的 PDF 存成文献 ----------
# PDF 原样存进知识库的原始文件；每页的文字存成这条文献上的"全文"表（页、文字），问文献按页找、说第几页。
# 有 DOI 就去 OpenAlex 按 DOI 取标题、作者、年份、期刊、摘要（发出去的只有 DOI）；没有就从 PDF 自己的信息和第一页猜标题。

DOI = re.compile(r'\b(10\.\d{4,9}/[^\s"<>]+)', re.I)


def _pdf_meta(path, pages):
    from pypdf import PdfReader
    info = {}
    try:
        meta = PdfReader(str(path)).metadata or {}
        info['标题'] = str(meta.get('/Title') or '').strip()
        info['作者'] = str(meta.get('/Author') or '').strip()
    except Exception:
        pass
    head = '\n'.join(text for _, text in pages[:2])
    found = DOI.search(head)
    if found:
        info['DOI'] = found[1].rstrip('.,;)]')
    year = re.search(r'(?:©|received|published|accepted|copyright)[^\n]{0,40}?((?:19|20)\d{2})', head, re.I) or re.search(r'\b((?:19|20)\d{2})\b', head)
    if year:
        info['年份'] = int(year[1])
    first = pages[0][1] if pages else ''
    meta = info.get('标题') or ''
    words = [word for word in re.findall(r'[a-z]{4,}', meta.lower())]
    believable = len(meta) >= 8 and not meta.lower().endswith(('.doc', '.docx', '.pdf')) and 'microsoft' not in meta.lower() \
        and (not words or sum(word in first.lower() for word in words) >= 0.6 * len(words))      # PDF 自带的标题常是错的（别的文章、模板名）：要在第一页里对得上
    if not believable:
        info['标题'] = _first_page_title(first)
    return info


def _first_page_title(text):
    """第一页前十几行里最长的那行当标题；下一行像是标题的续行（不是作者、单位、邮箱）就接上。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()][:14]
    stop = next((index for index, line in enumerate(lines) if re.search(r'^abstract|^received|@|^keywords', line, re.I)), len(lines))
    lines = lines[:stop]                                                    # 标题在摘要、收稿日期、邮箱之前
    candidates = [index for index, line in enumerate(lines) if 15 <= len(line) <= 220 and not re.match(r'[§*†‡\d]', line)
                  and not re.search(r'https?://|www\.|ISSN|doi|©|\d{4}, \d|journal|vol\.', line, re.I)]
    if not candidates:
        return ''
    index = next((i for i in candidates if len(lines[i]) >= 25 and len(lines[i].split()) >= 4), max(candidates, key=lambda i: len(lines[i])))   # 头一行像样的长句（最长的常是脚注）
    title = lines[index]
    if index + 1 < len(lines):
        follow = lines[index + 1]
        if 8 <= len(follow) <= 160 and follow.count(',') < 2 and not re.search(r'@|\d{3,}|university|institute|department', follow, re.I):
            title = f'{title} {follow}'
    return re.sub(r'[\s§*†‡#]+$', '', title)


def _openalex_by_doi(doi):
    try:
        work = _get('https://api.openalex.org/works/doi:' + doi, params={'api_key': config.key_for('openalex')} if config.key_for('openalex') else None).json()
    except Exception:
        return {}
    source = ((work.get('primary_location') or {}).get('source') or {})
    return {'标题': work.get('title') or '', '年份': work.get('publication_year'), '期刊': source.get('display_name') or '',
            '作者': '、'.join(item['author']['display_name'] for item in (work.get('authorships') or [])[:6] if item.get('author')),
            '摘要': _abstract(work), '链接': work.get('doi') or ''}


def add_pdf(path, filename='', projects=(), by='你'):
    """一篇 PDF → 一条文献（留）：原文件、每页文字、标题作者年份期刊 DOI、摘要当要点，挂到课题。返回 {id, name, 页数, DOI}。"""
    from .research import pdf_pages
    know = store.knowledge()
    pages = [(number, (text or '').strip()) for number, text in pdf_pages(path)]
    file_id = know.add_file(path, kind='文献', note=filename or Path(path).name, subfolder='文献PDF')
    info = _pdf_meta(path, pages)
    online = _openalex_by_doi(info['DOI']) if info.get('DOI') else {}
    title = online.get('标题') or info.get('标题') or Path(filename or path).stem
    year = online.get('年份') or info.get('年份')
    words = sum(len(text) for _, text in pages)
    abstract = online.get('摘要') or ''
    points = f'摘要：{abstract}' if abstract else ('（这份 PDF 读不出文字，多半是扫描件；要先做文字识别）' if words < 200 else '全文已存，按页能问；要点还没写，让助手读了再补。')
    title_words = lambda text: set(re.findall(r'[a-z]{4,}', str(text or '').lower()))
    mine = title_words(title)
    same_title = lambda row: bool(mine) and str(row.get('年份') or '') == str(year or '') and \
        len(mine & (title_words(row.get('标题')) | title_words(row.get('name')))) >= 0.7 * len(mine)
    existing = [row for row in (know.rows('文献') if know.kind('文献') else [])
                if row.get('PDF') == file_id or (info.get('DOI') and str(row.get('DOI') or '').lower() == info['DOI'].lower()) or same_title(row)]
    # 同一份文件、同一个 DOI、或同年份标题对得上（调研时记过这篇）：把全文挂到那一条上，不另建；那一条原来的名字、要点、理由留着
    if existing:
        old = existing[0]
        saved = know.save('文献', {'id': old['id'], 'PDF': file_id, '全文页数': len(pages), 'DOI': old.get('DOI') or info.get('DOI') or '',
                                  '标题': old.get('标题') or title, '年份': old.get('年份') or year, '作者': old.get('作者') or online.get('作者') or info.get('作者') or '',
                                  'files': [file_id], 'links': [{'relation': '属于', 'to': key} for key in projects],
                                  'series': {'全文': {'columns': ['页', '文字'], 'rows': [[number, text] for number, text in pages]}}},
                          source='PDF 拖进来', by=by)[0]
        return {'id': saved['id'], 'name': saved['name'], '页数': len(pages), 'DOI': info.get('DOI') or '', '字数': words_count(pages), '已有': True}
    saved = know.save('文献', {'name': f'{title[:70]}｜{year}' if year else title[:80],
                              '标题': title, '作者': online.get('作者') or info.get('作者') or '', '年份': year, '期刊': online.get('期刊') or '',
                              'DOI': info.get('DOI') or '', '类型': '论文（全文 PDF）', '取舍': '留', '理由': f'你放进来的全文（{filename or Path(path).name}）',
                              '要点': points, '链接或路径': online.get('链接') or '', 'PDF': file_id, '全文页数': len(pages),
                              'files': [file_id], 'links': [{'relation': '属于', 'to': key} for key in projects],
                              'series': {'全文': {'columns': ['页', '文字'], 'rows': [[number, text] for number, text in pages]}}},
                      source='PDF 拖进来', by=by)[0]
    return {'id': saved['id'], 'name': saved['name'], '页数': len(pages), 'DOI': info.get('DOI') or '', '字数': words, '已有': False}


def words_count(pages):
    return sum(len(text) for _, text in pages)


# ---------- 页面 ----------

def view():
    """知识 → 文献 一页：主题、线索、文献库（每条被几条命题引用）、几个数。"""
    know = store.knowledge()
    books = know.rows('文献') if know.kind('文献') else []
    _, links = know.sql("SELECT l.from_id, l.to_id, r.name FROM links l JOIN records r ON r.id=l.from_id WHERE l.relation='出处' AND r.deleted_at IS NULL")
    cited = {}
    for source, target, name in links:
        cited.setdefault(target, []).append({'id': source, 'name': name})
    library = [{'id': row['id'], 'name': row['name'], '标题': row.get('标题'), '作者': row.get('作者'), '年份': row.get('年份'), '类型': row.get('类型'),
                '取舍': row.get('取舍'), '理由': row.get('理由'), '要点': row.get('要点'), '链接': row.get('链接或路径'), 'source': row.get('source'),
                'updated': row.get('updated_at'), '命题': cited.get(row['id'], []), 'PDF': row.get('PDF'), '全文页数': row.get('全文页数')} for row in books]
    library.sort(key=lambda row: str(row['updated'] or ''), reverse=True)
    all_leads = leads()
    count = {}
    for row in all_leads:
        count[row['状态']] = count.get(row['状态'], 0) + 1
    return clean({'topics': topics(), 'projects': projects(), 'leads': all_leads, 'lead_count': count, 'library': library,
                  'claims': len(know.rows('命题')) if know.kind('命题') else 0})
