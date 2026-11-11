"""先预测，后核对：实验做之前写下"会怎样、多有把握、凭什么"，结果出来再对照（技能「预测和验证」）。

预测是产品库里的一种记录（类型"预测"），挂到课题和实验上；依据连到知识库里的命题。
晨报会列出"等着核对的预测"，提醒助手顺手核对。
"""
from . import store

KIND = '预测'
ABOUT = '做实验之前写下的预测：哪个实验、什么指标、预测值或范围、把握多少、凭什么；结果出来后核对命中没有、学到什么。'
FIELDS = [{'key': '课题'}, {'key': '实验'}, {'key': '指标'}, {'key': '预测'}, {'key': '把握', 'unit': '%'}, {'key': '依据'},
          {'key': '状态', 'about': '等结果 / 核对完'}, {'key': '实测'}, {'key': '命中', 'about': '中 / 部分 / 没中'}, {'key': '学到什么'}, {'key': '来源', 'about': '助手 / 他'}]


def ensure(lib):
    if not lib.kind(KIND):
        lib.define(KIND, ABOUT, FIELDS, '课题')
    return lib


def waiting(library):
    """还在等结果的预测（实验名、指标、预测、把握）。"""
    lib = store.open(library)
    if not lib.kind(KIND):
        return []
    return [{'名字': row['name'], '实验': row.get('实验'), '指标': row.get('指标'), '预测': row.get('预测'), '把握': row.get('把握'), '课题': row.get('课题')}
            for row in lib.rows(KIND) if (row.get('状态') or '等结果') == '等结果']


def for_project(library, project):
    """一个课题挂着的预测：等结果的和核对完的（命中情况）。"""
    lib = store.open(library)
    if not lib.kind(KIND):
        return []
    return [{'id': row['id'], '名字': row['name'], '实验': row.get('实验'), '指标': row.get('指标'), '预测': row.get('预测'), '把握': row.get('把握'),
             '状态': row.get('状态') or '等结果', '实测': row.get('实测'), '命中': row.get('命中')}
            for row in lib.rows(KIND) if row.get('课题') in (project.get('id'), project.get('name'))]
