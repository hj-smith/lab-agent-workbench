"""第一次启动时放进去的东西：课题类型和几条课题（由助手按以前的工作记忆整理，标"待确认"），知识库的类型，第一批卡片。
可以重复跑：已经有的不再放。
"""
import json

from . import config, kernel, store

STAGES = ['调研', '设计', '批次', '测试', '结论']
PROJECT_FIELDS = [
    {'key': '问题', 'about': '这个课题要回答的问题'},
    {'key': '当前站', 'about': '调研 / 设计 / 批次 / 测试 / 结论'},
    {'key': '一句话', 'about': '现在的状况'},
    {'key': '线路色', 'about': '线路图上的颜色'},
    {'key': '开始', 'type': 'date'},
    {'key': '站点', 'about': '[{站, 标题, 说明, 状态: 走过/当前/没到, 记录: [记录 id]}]，按顺序'},
]
NOTE = '由助手按以前的工作记忆整理（2026-11-10），请确认后去掉"待确认"。'


def _station(stage, title, about, state, records=()):
    return {'站': stage, '标题': title, '说明': about, '状态': state, '记录': list(records)}


PROJECTS = {}                       # 首次启动放进去的课题：按自己的项目填

KNOWLEDGE_KINDS = [
    ('文献', '调研找到的资料：论文、专利、技术资料、网页、内部报告。保留的和舍弃的都记，舍弃的写原因。',
     [{'key': '标题'}, {'key': '作者'}, {'key': '年份'}, {'key': '类型'}, {'key': '链接或路径'}, {'key': '取舍'}, {'key': '理由'}, {'key': '要点'}]),
    ('命题', '一句能单独读懂、讲事情本身的结论。出处用关联"出处"连到文献；实验证据用关联"证据"连到记录。',
     [{'key': '陈述'}, {'key': '适用范围'}, {'key': '状态', 'about': '文献结论 / 待实验验证 / 实验支持 / 实验反驳 / 存疑'}]),
    ('推理链', '几个前提合起来推出一个结论：关联"前提"连到几条命题，"结论"连到一条命题。', [{'key': '说明'}]),
    ('练习记录', '一次检查或练习的结果，关联"考的是"连到命题。', [{'key': '题型'}, {'key': '结果'}, {'key': '提示级别'}, {'key': '把握程度', 'unit': '%'}, {'key': '思路'}, {'key': '时间'}]),
    ('改进', '平台和研发流程里不顺手的地方，以及怎么改。助手复盘时记，人也可以记；小的、能撤回的助手直接改，大的发卡片。',
     [{'key': '问题'}, {'key': '类别', 'about': '呈现 / 页面 / 技能 / 数据 / 流程 / 实验'}, {'key': '来源', 'about': '哪段对话、哪条记录、谁提的'},
      {'key': '建议'}, {'key': '状态', 'about': '提议 / 在做 / 做完 / 不做'}, {'key': '结果'}]),
]

CONVENTIONS = [
    {'条目': 'GPC 分子量是聚苯乙烯（PS）当量读数，不是真实分子量', '说明': '和模拟或别的方法比之前先换算'},
    {'条目': '导出文件只用系统导出，命名 编号_实验名称_内容_版本'},
    {'条目': '时间一律北京时间'},
    {'条目': '原始数据、参数、结果分开存；原始文件不改'},
]

# 默认的主动规则：每天晨报、每周复盘。在"助手 → 规则"里能停、能改。
DEFAULT_RULES = [
    {'name': '研发晨报', 'when': {'source': '定时', 'every': 'daily 08:30'}, 'enabled': True, 'skill': '简报', 'library': '全部',
     'prompt': '做今天的研发晨报：先读技能「简报」，用 today 看全局，再用 brief 存成晨报（kind=晨报）。他看的是总览上那一句话和点开的那一页，要人话、有结论、有建议。'},
    {'name': '每周复盘', 'when': {'source': '定时', 'every': 'weekly fri 17:00'}, 'enabled': True, 'skill': '简报', 'library': '全部',
     'prompt': '做这一周的复盘：先读技能「简报」，看这一周的对话、跑过的轮、他做的修改和定的口径，用 brief 存成复盘（kind=复盘）；值得沉淀的做法、口径在建议里列出（带 task），能直接做的小改进直接做。跟你学：用 demos 看每类还没学的示范，攒够 5 次的那类就归纳成做法，发卡片问他要不要写进技能（key 写「跟你学：类名」）。'},
    {'name': '文献监测', 'when': {'source': '定时', 'every': 'weekly mon 08:00'}, 'enabled': True, 'skill': '文献问答', 'library': '知识库',
     'prompt': '做这周的文献监测：先读技能「文献问答」里"每周文献监测"一节。用 literature check 查一遍，再用 literature leads status=待看 看新到的，'
               '每条按标题和摘要写相关度（高/中/低）和一句意见（对着哪个课题、哪条命题）；留不留由他定。最后一两句说这周新到几篇、高相关的是哪几篇。'},
]

CARDS = []


def run():
    for library, projects in PROJECTS.items():
        lib = store.open(library)
        lib.define('课题', '一个要回答的研发问题，从调研走到结论。站点是它走过和要走的每一步，挂着那一步的记录。', PROJECT_FIELDS, group='课题', sort=0)
        for project in projects:
            if not lib.sql('SELECT 1 FROM records WHERE id=?', (project['id'],))[1]:
                lib.save('课题', [{**project, 'note': NOTE, 'unconfirmed': ['当前站', '站点']}], source='助手整理', by='助手')
    knowledge = store.knowledge()
    for name, about, fields in KNOWLEDGE_KINDS:
        if not knowledge.kind(name):
            knowledge.define(name, about, fields, group='知识')
    for library in PROJECTS:
        lib = store.open(library)
        if lib.setting('约定口径') is None:                         # 他以前定过的口径，作默认放进去，能改能删
            lib.set_setting('约定口径', CONVENTIONS, '人定下的口径和规矩，助手每次干活都先读')
    from .loop import rules
    current = rules.all()
    if not config.settings().get('seeded_rules'):                   # 只放一次：他删掉的不再加回来
        rules.save(current + [rule for rule in DEFAULT_RULES if rule['name'] not in {item['name'] for item in current}])
        config.save_settings({**config.settings(), 'seeded_rules': True})
    known = {item['id'] for item in kernel.lanes()}
    for item in CARDS:
        if item['lane'] in known:
            continue
        card = {**item['card'], 'id': 'seed-' + item['lane'], 'fields': [], 'lane': item['lane'], 'at': kernel.now(), 'results': []}
        kernel.append(item['lane'], 'lane', {'title': item['title'], 'library': item['library']})
        kernel.append(item['lane'], 'input', {'source': '事件', 'text': f'[新系统搬运旧数据时发现的事] {item["card"]["title"]}\n{item["card"]["text"]}'})
        kernel.append(item['lane'], 'assistant', {'parts': [{'type': 'text', 'text': item['card']['text']},
                                                           {'type': 'tool', 'id': card['id'], 'name': 'ask_user', 'input': item['card']}],
                                                 'raw': None, 'by': 'seed', 'model': '', 'usage': {}, 'stop': 'tool'})
        kernel.append(item['lane'], 'card', card)
        kernel.append(item['lane'], 'ended', {'status': 'waiting'})
    settings = config.settings()
    config.save_settings(settings)


if __name__ == '__main__':
    run()
