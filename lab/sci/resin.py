r"""树脂合成的计算：调常驻的 node 计算核心（workbench2\resin-kernel\main.mjs，树脂模块 2.11.0 的 kernel 原样复制）。
算法一行没改，这里只负责：从研发库取设计 → 发给 node → 把回答整理成带单位的字典。

给智能体用的函数（ref = 树脂实验的 id 或编号，例 'R100-38'；lib 不给就是 产品A）：
    charges(ref)              投料计算：每个料的 模式、g、mmol、mL、实测 g；变量（单体比、理论净树脂…）
    schedule(ref)             时间表：每步计划/实际起止 h、温度
    yield_(ref)               产率：每条路线 干品 g、理论净树脂 g、分料比、产率 %；合计
    profile(ref)              过程：过程固含 w/w、体积 mL、干基闭合（后处理每步）；偏差（投料 ±1 %、时长 ±10 %、温度 ±2 °C）
    closure(ref)              只要干基闭合：每个后处理步骤 进入干重 g、固相干重 g、液相干重 g、闭合
    factors(ref)              批次因子：25 个因子的值、单位、状态（known / missing / to_confirm）
    hplc(ref)                 HPLC：每个取样点 MonA/MonB 含量 mg/g 和单体剩余 %（以理论投料为 100 %）
    document(ref, records)    编译好的 SOP / 批记录文档（版面和公式，给预览）
    export_sop(ref, fmt='xlsx', records=False, path=None)   导出 SOP 与批记录 xlsx（records=True 带实测）或 SOP Word（fmt='docx'）→ 文件路径
    import_record(ref, path, save=True)                     读回操作员填好的批记录 xlsx，把实测值并进 `实测` 表 → 读到几格、警告
    compute_all(lib, report)  把所有树脂实验的 产率、闭合、因子、HPLC 算一遍，存 set_result

口径：产率 = 干品 ÷（理论净树脂 × 分料比）；理论净树脂 = MonB + MonA − 生成水；分料比 = 分料称重比，没称用计划比；
     手填 record-results/理论量 优先。HPLC 含量 = (峰面积 − 截距)/斜率 × 体积倍数 × (稀释后净重/取样量) ÷ 稀释液密度。
"""
import atexit
import json
import subprocess
import threading
from pathlib import Path

from lab import config, store

KERNEL = config.ROOT / 'resin-kernel' / 'main.mjs'
VERSION = 'resin-kernel 2.11.0'
LIBRARY = '产品A'
_proc = {}


class KernelError(Exception):
    pass


def _lib(lib):
    return lib if isinstance(lib, store.Library) else store.open(lib or LIBRARY)


# 物料页上看得见的字段 → 计算核心要的物料原文的键。人改了、确认了的字段算数；还"待确认"的不进计算（照旧用原文）。
MATERIAL_KEYS = {'名称': 'name', '简称': 'abbr', '分子量': 'mw', '密度': 'density', '纯度': 'purity', '溶液浓度': 'concentrationPct', '溶剂': 'solvent',
                 '种类': 'kind', '形态': 'form', 'CAS': 'cas', '分子式': 'formula', '沸点': 'bp', '熔点': 'mp', '别名': 'aliases', '已归档': 'archived'}


def material(row):
    """一条 树脂物料 记录 → 计算核心的物料：原文打底，页面上改过且已确认的字段盖上去。"""
    out = dict(row.get('原文') or {})
    pending = set(row.get('unconfirmed') or [])
    for field, key in MATERIAL_KEYS.items():
        if field in row and field not in pending and not (row[field] is None and key not in out):     # 空字段不往原文里加没有的键
            out[key] = row[field]
    out['id'] = out.get('id') or row.get('物料id') or row['name']
    return out


def _start(lib):
    key = lib.name
    if key in _proc and _proc[key].poll() is None:
        return _proc[key]
    proc = subprocess.Popen([config.NODE, str(KERNEL)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8',
                            cwd=str(KERNEL.parent), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))   # 正式服务是 pythonw 起的：不加这个会弹一个黑窗口，一关计算就断
    _proc[key] = proc
    atexit.register(proc.kill)
    preferences = {**(lib.setting('树脂.库偏好') or {}), 'hplcMethods': lib.setting('树脂.HPLC方法') or [],
                   'gpcNameRules': lib.setting('树脂.GPC命名规则') or []}
    modules = (lib.setting('树脂.步骤模板') or {}).get('modules') or []
    materials = [material(row) for row in lib.rows('树脂物料')]
    _send(proc, 'setup', materials=materials, modules=modules, factors=lib.setting('树脂.因子规则'), preferences=preferences)
    return proc


def _send(proc, cmd, **kw):
    proc.stdin.write(json.dumps({'id': 1, 'cmd': cmd, **kw}, ensure_ascii=False) + '\n')
    proc.stdin.flush()
    answer = json.loads(proc.stdout.readline())
    if not answer['ok']:
        raise KernelError(f'树脂计算核心（{cmd}）：{answer["error"]}')
    return answer['result']


_turn = threading.Lock()                 # 一个常驻进程一问一答：同时来的请求排队，不然答案会串


def call(cmd, lib=None, **kw):
    """直接调 node 核心的一个命令（setup 以外的：evaluate、profile、yield、factors、hplc、document、export-xlsx、export-docx、import-record）。"""
    with _turn:
        return _send(_start(_lib(lib)), cmd, **kw)


def reset(library):
    """规矩（因子规则、HPLC 方法、GPC 命名规则、步骤模板）改了：关掉这个库的核心，下次调用时按新的规矩重新启动。"""
    with _turn:
        proc = _proc.pop(library, None)
        if proc:
            proc.kill()


def design(ref, lib=None):
    """库里的一个树脂实验 → 计算核心要的 (design doc, experiment doc, 记录)。"""
    lib = _lib(lib)
    record = lib.get(ref, kind='树脂实验', with_series=True)
    data = record['data']
    actual = {key: value for key, value in record['series'].get('实测', {}).get('rows', [])}
    samples = sorted(lib.rows('树脂取样', where=f"json_extract(data, '$.实验编号') = '{data['编号']}'"), key=lambda row: row.get('序号') or 0)
    experiment = {**(data.get('实验原文') or {}), 'samples': [row['原文'] for row in samples]}
    return {**data['设计'], 'actual': actual}, experiment, record


def charges(ref, lib=None):
    doc, _, record = design(ref, lib)
    out = call('evaluate', lib, design=doc)
    return {'实验': record['name'],
            '投料': [{'id': c['id'], '物料': c['materialId'], '名称': c['name'], '角色': c['role'], '模式': c['mode'], '计量单位': c['unit'],
                      '质量_g': c['g'], '物质的量_mmol': c['mmol'], '体积_mL': c['mL'], '实测': c['actual']} for c in out['charges']],
            '变量': {v['key']: {'值': v['value'], '单位': v.get('unit') or '', '公式': v.get('expr')} for v in out['vars']
                     if not str(v['key']).startswith('charge-')}}


def schedule(ref, lib=None):
    doc, _, record = design(ref, lib)
    out = call('evaluate', lib, design=doc)
    return {'实验': record['name'], '时间表': [{'步骤': s.get('stepId'), '名称': s.get('title'), '阶段': s.get('stage'), '路线': s.get('branch'),
                                               '计划起_h': s.get('plannedStart'), '计划止_h': s.get('plannedEnd'), '起_h': s.get('start'), '止_h': s.get('end'),
                                               '时长_h': s.get('hours'), '温度_C': s.get('tempC'), '来源': s.get('timing')} for s in out['schedule']]}


def yield_(ref, lib=None):
    doc, experiment, record = design(ref, lib)
    out = call('yield', lib, design=doc, experiment=experiment)
    pct = lambda value: None if value is None else value * 100
    return {'实验': record['name'], '口径': out['note'], '理论净树脂_g': out['theory'], '理论来源': '手填' if out['manualTheory'] else '设计公式',
            '公式': out['formula'], '计划产率_%': pct(out['planned']), '缺': out['missing'],
            '各路': [{'产品': row['id'], '路线': row['route'], '干品_g': row['dry'], '干品来自': row['dryKey'], '分料比': row['fraction'],
                      '理论_g': row['theory'], '产率_%': pct(row['yield']),
                      '历史总表产率_%': pct((row.get('historical') or {}).get('value')), '和历史不一致': row.get('differs')} for row in out['rows']],
            '合计': out['total'] and {'干品_g': out['total']['dry'], '理论_g': out['total']['theory'], '产率_%': pct(out['total']['yield'])}}


def profile(ref, lib=None):
    doc, _, record = design(ref, lib)
    out = call('profile', lib, design=doc)
    p = out['profile']
    return {'实验': record['name'], '单位': {'hours': 'h', 'massG': 'g', 'solids': 'w/w', 'volumeMl': 'mL', 'tempC': '°C', 'waterMl': 'mL'},
            '计划过程': p['stages'], '温度': p['temperature'], '固含': p['solids'], '分水': p['water'], '体积': p['volume'],
            '后处理流向': p['workup']['massFlow'], '干基闭合': p['workup']['dryBasis'], '后处理其它': {k: v for k, v in p['workup'].items() if k not in ('massFlow', 'dryBasis')},
            '偏差': [{'项': d['key'], '计划': d['planned'], '实际': d['actual'], '单位': d['unit'], '说明': d['message'], '原因': d.get('reason')}
                     for d in out['deviations']]}


def closure(ref, lib=None):
    out = profile(ref, lib)
    return {'实验': out['实验'], '口径': '闭合 = (固相干重 + 液相干重) ÷ 进入干重',
            '步骤': [{'步骤': d['stepId'], '路线': d.get('branch'), '进入干重_g': d['incomingDryG'], '固相干重_g': d['solidDryG'],
                      '液相干重_g': d['liquidDryG'], '闭合': d['closure']} for d in out['干基闭合'] or []]}


def factors(ref, lib=None):
    doc, _, record = design(ref, lib)
    return {'实验': record['name'], '因子': call('factors', lib, design=doc)}


def hplc(ref, lib=None):
    doc, _, record = design(ref, lib)
    out = call('hplc', lib, design=doc)
    rest = {name: {point['x']: point for point in points} for name, points in out['remaining'].items()}
    return {'实验': record['name'], '口径': '含量 mg/g（样品）；剩余 % = 含量 × 当时液重 ÷ 当时该单体量（理论投料 = 100 %，扣取样、分水，滴加按时间）',
            '取样点': [{'节点': p['id'], '时间_h': p['hours'], '样品': p['sampleId'], '取样量_g': p['mass'], '稀释后净重_g': p['net'],
                        '方法': (p.get('method') or {}).get('name'), '检测日期': p['date'],
                        '组分': {name: {'峰面积': c['area'], '含量_mg_per_g': c['value'],
                                        '剩余_%': (rest.get(name, {}).get(p['hours']) or {}).get('y')} for name, c in p['conc'].items()}}
                       for p in out['points']],
            '累计取样_g': out['removedG'], '分水_g': out['waterG']}


def document(ref, records=False, lib=None):
    doc, _, _ = design(ref, lib)
    return call('document', lib, design=doc, records=records)


def export_sop(ref, fmt='xlsx', records=False, path=None, lib=None):
    lib = _lib(lib)
    doc, _, record = design(ref, lib)
    kind = 'SOP与批记录' + ('_已填' if records else '') if fmt == 'xlsx' else 'SOP'
    path = Path(path) if path else config.outputs_dir(lib.name) / f'{record["name"]}_{record["data"].get("标题") or ""}_{kind}.{fmt}'.replace('/', '-')
    out = call('export-xlsx' if fmt == 'xlsx' else 'export-docx', lib, design=doc, records=records, path=str(path))
    return {'文件': out['path'], '字节': out['bytes']}


def import_record(ref, path, save=True, lib=None):
    """读回填好的批记录 xlsx（系统导出的那种，带 _meta 页），实测值并进 `实测` 表。"""
    lib = _lib(lib)
    doc, experiment, record = design(ref, lib)
    out = call('import-record', lib, design=doc, experiment=experiment, path=str(path))
    new = out['design']
    if save:
        actual = new.pop('actual', {}) or {}
        file_id = lib.add_file(path, kind='树脂', note=f'{record["name"]} 导回的批记录', subfolder='树脂')
        lib.save('树脂实验', {'id': record['id'], '设计': new, 'files': [file_id],
                              'series': {'实测': {'columns': ['键', '值'], 'rows': [[k, v] for k, v in actual.items()]}}}, by='导入')
    return {'实验': record['name'], '读到': len(out['values']), '分组': out.get('counts'), '警告': out['warnings'],
            '标记': out.get('flags'), '已存': save}


def compute_all(lib=None, report=print):
    lib = _lib(lib)
    done = {'产率': 0, '闭合': 0, '因子': 0, 'HPLC': 0, '出错': 0}
    for row in lib.rows('树脂实验'):
        for method, run in (('产率', yield_), ('闭合', closure), ('因子', factors), ('HPLC', hplc)):
            try:
                value = run(row['id'], lib)
            except KernelError as error:
                done['出错'] += 1
                report(f'  {row["name"]} {method}：{error}')
                continue
            if method == 'HPLC' and not value['取样点']:
                continue
            if method == '闭合' and not value['步骤']:
                continue
            lib.set_result(row['id'], f'树脂.{method}', value, {}, VERSION)
            done[method] += 1
    report(f'树脂计算：{done}')
    return done
