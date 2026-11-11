"""配方计算：从研发库取数 → 调原样复制的配方算法（lab.sci.vendor.formulation，1.7.3）→ 结果用 set_result 存。

给智能体用的函数（ref 是记录 id 或名字；lib 是库名或 store.open() 的结果，默认当前库）：
    registry(lib)                              原料 + 预混液 → 算法用的原料库 {materials, premixes}
    calculate_formula(ref, lib, formula=None)   配方核算：每行要称多少 g、固体/溶剂/易挥发 g、溶剂补加、三项平衡、错误。
                                               ref 是配方（用现在的原料库）或工单（用冻结的组成）；也可以直接给 formula 字典。
    dose_report(ref, lib)                      剂量报告：每行原设计值、四种口径（占树脂固体 %、占总固体 %、占总量 %、ppm）、
                                               有效份 g、要称的 g；工单有实称时再给实称回算（实际固含、实际剂量）。
    formula_components(ref, lib)               把预混液拆回有效成分（DMAP、PPTS…），每种给 6 种口径（设计和实称）。
    equivalents(ref, lib)                      摩尔当量（例 DMAP/PPTS 0.300 eq）。
    suggest_equivalent(ref, target_row_id, ratio, lib)   反过来：要某个当量比，该行用量填多少 %。
    weighing_suggestions(ref, lib, min_weigh_g=0.05)     称量小于下限时，建议改用更稀的预混液或新配 10/1/0.1 %。
    premix_plan(工单列表, lib, settings=None)  几张工单合起来要配多少预混液（库存、余量、最低配制量、取整）。
    doe_methods() / doe_design(variables, method, options) / doe_analyze(runs, responses, model, variables, options)
    doe_predict(model_result, points) / doe_contour(model_result, x_key, y_key, fixed, grid) / doe_optimize(model_results, goals, options)
    doe_apply_row(base_ref, row, variables, lib)  DOE 的一行 → 一个独立的配方字典（再用 calculate_formula 核算）。
    thickness_fit(points, model='linear')       膜厚–固含曲线拟合（点：固含 0–1，膜厚 nm）；thickness_invert(fit, nm) 反算固含。
    export_orders(工单列表, lib, fmt='xlsx'|'pdf') / export_preparation(备料单, lib, fmt) / export_doe_report(snapshot, lib, fmt)
                                               用原来的模板和版式导出，文件写到 产出\\<库>\\，返回路径。
    compute_all(lib, report=print)             把库里的配方、工单都算一遍，结果存进 results。

输出都是 JSON 能表示的字典。单位：键名以 _g 结尾是 g，_fraction 是质量分数 0–1，_pct / percent 是 %，_ppm 是 ppm，
mmol_per_g 是 mmol/g 树脂。配方的行（树脂 / 添加剂 / 溶剂）保持算法原来的英文键，说明见 skills\\配方\\SKILL.md。
"""
from copy import deepcopy

from lab import config, store
from lab.sci.vendor.formulation import (doe, doe_analysis, dose_report as _dose, equivalents as _eq, excel_io, formula_core,
                                        pdf_export, pilot_fit, weighing_suggest)
from lab.sci.vendor.formulation import formula_components as _fc
from lab.sci.vendor.formulation.experiment_service import apply_series_row
from lab.sci.vendor.formulation.preparation_actuals import group_summary

VERSION = 'formulation-1.7.3'

# ---------- 字段对照：旧库英文键 ↔ 新库中文字段（迁移和取数共用） ----------
MATERIAL_KEYS = {'id': '编号', 'name': '名称', 'category': '类别', 'solid_fraction': '固体分数', 'solvent_fraction': '溶剂分数',
                 'volatile_fraction': '易挥发分数', 'solvent_id': '溶剂编号', 'molar_mass': '摩尔质量', 'unit': '单位',
                 'source_status': '来源状态', 'calculation_role': '计算角色', 'retired': '停用',
                 'source_sheet': '旧工作簿表', 'source_row': '旧工作簿行'}
PREMIX_KEYS = {'id': '编号', 'name': '名称', 'purpose': '用途', 'usage': '用法', 'solid_fraction': '固体分数',
               'solvent_fraction': '溶剂分数', 'volatile_fraction': '易挥发分数', 'solvents': '内含溶剂',
               'composition_total': '组成合计', 'composition_complete': '组成完整', 'source_status': '来源状态',
               'components': '组分', 'retired': '停用', 'source_sheet': '旧工作簿表', 'source_row': '旧工作簿行'}
COMPONENT_KEYS = {'id': '行id', 'source_type': '来源类型', 'source_id': '来源编号', 'fraction': '质量分数', 'name': '名称',
                  'category': '类别', 'note': '备注', 'source_sheet': '旧工作簿表', 'source_row': '旧工作簿行'}
FORMULA_KEYS = {'code': '配方编号', 'total_mass_g': '总量', 'solid_fraction': '固含', 'resins': '树脂', 'additives': '添加剂',
                'solvents': '溶剂', 'equivalents': '当量', 'process_factors': '工艺因素',
                'equivalent_design_results': '当量设计结果', 'frozen_solvent_carry': '冻结溶剂携带',
                'frozen_design_fingerprint': '冻结设计指纹', 'historical_composition_known': '历史组成已知',
                'design_start': '起算方式'}
STATUS = {'planned': '待配样', 'completed': '已完成', 'void': '已作废', 'archived': '历史导入'}
STATUS_BACK = {value: key for key, value in STATUS.items()}


def to_cn(item, keys, skip=()):
    return {keys.get(key, key): value for key, value in item.items() if key not in skip}


def to_en(record, keys, extra=()):
    back = {cn: en for en, cn in keys.items()}
    return {back[cn]: record[cn] for cn in back if record.get(cn) is not None} | {key: record[key] for key in extra if key in record}


def _lib(lib):
    return lib if isinstance(lib, store.Library) else store.open(lib)


# ---------- 从库里取数，拼成算法要的形状 ----------

def registry(lib=None):
    lib = _lib(lib)
    materials = {}
    for row in lib.rows('原料'):
        item = to_en(row, MATERIAL_KEYS)
        item['note'] = row.get('note') or ''
        materials[item['id']] = item
    premixes = {}
    for row in lib.rows('预混液'):
        item = to_en(row, PREMIX_KEYS)
        item['note'] = row.get('note') or ''
        item['components'] = [to_en(c, COMPONENT_KEYS) for c in row.get('组分') or []]
        premixes[item['id']] = item
    return {'materials': materials, 'premixes': premixes}


def formula_of(record):
    """配方记录（lib.get 或 lib.rows 的一项）→ 算法用的 formula 字典。"""
    data = record.get('data', record)
    formula = to_en(data, FORMULA_KEYS)
    formula['note'] = record.get('note') or ''
    for key in ('resins', 'additives', 'solvents'):
        formula.setdefault(key, [])
    return formula


def _same_composition(current, comp):
    keys = ('solid_fraction', 'solvent_fraction', 'volatile_fraction')
    return all(abs(current[k] - comp[k]) <= 1e-12 for k in keys) and \
        set(current['solvents']) == set(comp.get('solvents') or {}) and \
        all(abs(current['solvents'][k] - comp['solvents'][k]) <= 1e-12 for k in current['solvents'])


def frozen_registry(result, current=None):
    """工单冻结的每行来源组成 → 原料库补丁。今天的原料库里同一来源组成没变的，用今天的定义（保留组分，
    有效成分拆分才认得出 DMAP、PPTS…）；变了或已经没有的，用冻结的组成。"""
    materials, premixes = {}, {}
    for line in result.get('lines') or []:
        comp = line.get('source_composition')
        if not comp or not comp.get('composition_known', True):
            continue
        if current is not None:
            try:
                if _same_composition(formula_core.resolve_source(line.get('source_type'), line['source_id'], current), comp):
                    continue
            except formula_core.CalculationError:
                pass
        sid, key = line['source_id'], '__frozen__' + line['source_id']
        materials[key] = {'id': key, 'name': comp.get('name', sid), 'solid_fraction': comp['solid_fraction'],
                          'solvent_fraction': comp['solvent_fraction'], 'volatile_fraction': comp['volatile_fraction']}
        materials[key]['solvents'] = dict(comp.get('solvents') or {})
        if line.get('source_type') == 'premix':
            premixes[sid] = {'id': sid, 'name': comp.get('name', sid),
                             'components': [{'source_type': 'material', 'source_id': key, 'fraction': 1}]}
        else:
            materials[sid] = {**materials[key], 'id': sid}
    return {'materials': materials, 'premixes': premixes}


def batch_of(lib, record):
    """工单记录 → 旧代码的 batch 字典（formula_data、result 冻结的计划量、actual 实称、预混实称组成）。"""
    data = record.get('data', record)
    full = lib.get(record['id'], with_series=True)
    actual = {'lines': {}, **deepcopy(data.get('实称信息') or {})}
    table = full['series'].get('称量')
    if table:
        cols = table['columns']
        for values in table['rows']:
            row = dict(zip(cols, values))
            if row.get('实称') is not None:
                actual['lines'][row['行']] = {'mass_g': row['实称'], 'review': row.get('复核') or ''}
    batch = {'id': data.get('旧id') or record['id'], 'batch_code': data.get('工单号'), 'formula_code': data.get('配方编号'),
             'revision': data.get('修订'), 'status': STATUS_BACK.get(data.get('状态'), data.get('状态')),
             'planned_date': data.get('计划日期'), 'formula_data': deepcopy(data.get('冻结配方') or {}),
             'result': deepcopy(data.get('冻结计划') or {}), 'actual': actual, 'registry_snapshot': {},
             'source_sheet': data.get('旧工作簿表'), 'edit_version': 1}
    if data.get('分装'):
        batch['dispensing_plan'] = data['分装']
    bindings = data.get('预混实称关联') or []
    if bindings:
        batch['actual_source_compositions'] = {}
        for bind in bindings:
            prep = lib.get(bind['备料单'], kind='备料单')
            plan = prep_of(lib, prep)
            group = next(g for g in plan['groups'] if g['key'] == bind['分组'])
            summary = group_summary(plan, group, plan['actual']['groups'].get(group['key'], {}))
            batch['actual_source_compositions'][bind['行']] = summary['composition']
    return batch


def prep_of(lib, record):
    data = record.get('data', record)
    full = lib.get(record['id'], with_series=True)
    info = deepcopy(data.get('实称信息') or {})
    actual = {'groups': {key: dict(value, components={}) for key, value in (info.get('groups') or {}).items()},
              'operator': info.get('operator', ''), 'note': info.get('note', '')}
    table = full['series'].get('实称')
    if table:
        cols = table['columns']
        for values in table['rows']:
            row = dict(zip(cols, values))
            if row.get('实称') is not None:
                actual['groups'].setdefault(row['分组'], {'components': {}})['components'][row['组分']] = {
                    'mass_g': row['实称'], 'review': row.get('复核') or ''}
    batches = {r['id']: r for r in lib.rows('工单')}
    return {'id': data.get('旧id') or record['id'], 'code': data.get('备料单号') or record.get('name'),
            'batch_ids': [batches[i].get('旧id') for i in data.get('工单id') or [] if i in batches],
            'groups': deepcopy(data.get('分组') or []), 'orders': deepcopy(data.get('订单') or []),
            'total_required_g': data.get('合计需求'), 'total_make_g': data.get('合计配制'), 'note': record.get('note') or '',
            'created_at': data.get('建立时间'), 'actual': actual}


class _Adapter:
    """formula_components 要的小 store：get_formula / get_batch / registry。"""

    def __init__(self, lib):
        self.lib = lib

    def registry(self):
        return registry(self.lib)

    def get_formula(self, ref):
        record = self.lib.get(ref, kind='配方')
        return {'data': formula_of(record), 'revision': record['data'].get('修订')}

    def get_batch(self, ref):
        return batch_of(self.lib, self.lib.get(ref, kind='工单'))


def _inputs(ref, lib, formula=None):
    """ref → (formula, registry, batch)。工单用冻结的组成，配方用现在的原料库。"""
    lib = _lib(lib)
    if formula is not None:
        return formula, registry(lib), None
    record = lib.get(ref)
    if record['kind'] == '工单':
        batch = batch_of(lib, record)
        reg = registry(lib)
        frozen = frozen_registry(batch['result'], reg)
        merged = {'materials': {**reg['materials'], **frozen['materials']}, 'premixes': {**reg['premixes'], **frozen['premixes']}}
        return batch['formula_data'], merged, batch
    return formula_of(record), registry(lib), None


# ---------- 给智能体用的计算 ----------

def calculate_formula(ref=None, lib=None, formula=None):
    formula, reg, batch = _inputs(ref, lib, formula)
    return formula_core.calculate_formula(formula, reg)


def dose_report(ref=None, lib=None, formula=None):
    formula, reg, batch = _inputs(ref, lib, formula)
    return _dose.dose_report(formula, reg, batch)


def formula_components(ref=None, lib=None, formula=None):
    lib = _lib(lib)
    formula, reg, batch = _inputs(ref, lib, formula)
    return _fc.formula_components({'formula': formula, 'registry': reg, 'batch': batch}, _Adapter(lib))


def equivalents(ref=None, lib=None, formula=None):
    formula, reg, batch = _inputs(ref, lib, formula)
    return _eq.calculate_equivalents(formula, reg)


def suggest_equivalent(ref, target_row_id, ratio, lib=None, mode=None, formula=None):
    formula, reg, batch = _inputs(ref, lib, formula)
    return _eq.suggest_equivalent_percent(formula, reg, target_row_id, ratio, mode)


def weighing_suggestions(ref=None, lib=None, min_weigh_g=None, formula=None, solvent=None):
    """称量建议：下限和预混浓度档按库设置"配方.称量下限""配方.建议预混浓度"（没设就用原件的 0.05 g 和 10/1/0.1 %）。"""
    formula, reg, batch = _inputs(ref, lib, formula)
    store_ = _lib(lib)
    limit = (store_.setting('配方.称量下限') or {}).get('value')
    levels = (store_.setting('配方.建议预混浓度') or {}).get('value')
    if levels:
        weighing_suggest.CONCENTRATIONS_PCT = tuple(float(level) for level in levels)
    return weighing_suggest.suggest_weighing(formula, reg, min_weigh_g if min_weigh_g not in (None, '') else limit, solvent)


def premix_plan(refs, lib=None, settings=None):
    lib = _lib(lib)
    items = []
    for ref in refs:
        record = lib.get(ref, kind='工单')
        items.append({'batch_id': record['id'], 'result': record['data'].get('冻结计划') or {}})
    return formula_core.plan_premix_preparation(items, registry(lib), settings)


def doe_methods():
    return doe.methods()


def doe_design(variables, method, options=None):
    return doe.design(variables, method, options)


def doe_analyze(runs, responses, model='linear', variables=None, options=None):
    return doe_analysis.analyze(runs, responses, model, variables, options)


def doe_predict(model_result, points, level=0.95):
    return doe_analysis.predict(model_result, points, level)


def doe_contour(model_result, x_key, y_key, fixed=None, grid=25):
    return doe_analysis.contour(model_result, x_key, y_key, fixed, grid)


def doe_optimize(model_results, goals, options=None):
    return doe_analysis.optimize(model_results, goals, options)


def doe_apply_row(base_ref, row, variables, lib=None, base_formula=None, balance=None):
    formula, reg, batch = _inputs(base_ref, lib, base_formula)
    return apply_series_row(formula, row, variables, balance, reg)


def thickness_fit(points, model='linear'):
    return pilot_fit.fit_model(points, model)


def thickness_invert(fit, target_nm):
    return pilot_fit.invert(fit, target_nm)


# ---------- 导出（原模板、原版式） ----------

def _product(lib):
    return {'id': lib.name, 'name': lib.name}


def _write(lib, name, data):
    path = config.outputs_dir(lib.name) / name
    path.write_bytes(data)
    return str(path)


def export_orders(refs, lib=None, fmt='xlsx', name=None):
    """工单 → 配样工单 xlsx（套 orders.xlsx 模板）或 PDF，和原模块导出的一样。"""
    lib = _lib(lib)
    batches = [batch_of(lib, lib.get(ref, kind='工单')) for ref in refs]
    data = excel_io.orders_xlsx(batches, _product(lib)) if fmt == 'xlsx' else pdf_export.orders_pdf(batches, _product(lib))
    stem = name or (batches[0]['batch_code'] or batches[0]['formula_code'] if len(batches) == 1 else f'配样工单_{len(batches)}张')
    return _write(lib, f'{stem}.{fmt}', data)


def export_preparation(ref, lib=None, fmt='xlsx'):
    lib = _lib(lib)
    prep = prep_of(lib, lib.get(ref, kind='备料单'))
    data = excel_io.preparation_xlsx(prep, _product(lib)) if fmt == 'xlsx' else pdf_export.preparation_pdf(prep, _product(lib))
    return _write(lib, f'{prep["code"]}.{fmt}', data)


def doe_snapshot(ref, lib=None, responses=None, measured=None, analysis=None, optimum=None):
    """DOE研究记录 → doe_report 要的 snapshot。
    responses: [{response_id, name, unit}]；measured: {配方编号: {response_id: {mean, n}}}（响应来自测试记录，由调用方给）；
    analysis: {response_id: doe_analyze 的结果}。"""
    lib = _lib(lib)
    record = lib.get(ref, kind='DOE研究', with_series=True)
    data = record['data']
    cols, points, _ = (lambda t: (t['columns'], t['rows'], t['meta']))(record['series']['实验点'])
    by_code = {row.get('code'): row for row in data.get('实验行') or []}
    orders = {r['name']: r for r in lib.rows('工单')}
    runs = []
    for values in points:
        point = dict(zip(cols, values))
        code = point['配方编号']
        work = [{'batch_code': name, 'status': STATUS_BACK.get(orders.get(name, {}).get('状态'))}
                for name in (point['工单号'] or '').split('、') if name]
        runs.append({'run_id': point['点id'], 'run': point['行号'], 'code': code,
                     'values': (by_code.get(code) or {}).get('values') or {}, 'work_orders': work,
                     'responses': (measured or {}).get(code) or {}})
    snapshot = {'series': {'id': record['id'], 'name': record['name'], 'data': {'purpose': data.get('目的')}},
                'factors': data.get('因子') or [], 'runs': runs, 'responses': responses or [], 'analysis': analysis or {}}
    if optimum:
        snapshot['optimum'] = optimum
    return snapshot


def export_doe_report(snapshot, lib=None, fmt='pdf'):
    """snapshot 可以用 doe_snapshot() 从 DOE研究记录拼；形状见 vendor/formulation/doe_report.py。"""
    from lab.sci.vendor.formulation import doe_report
    lib = _lib(lib)
    return _write(lib, doe_report.filename(snapshot, fmt), doe_report.build_report(snapshot, fmt))


# ---------- 全部算一遍 ----------

def _same_plan(fresh, frozen):
    old = {line['id']: line for line in frozen.get('lines') or []}
    diffs = [abs((line.get('planned_mass_g') or 0) - (old.get(line['id'], {}).get('planned_mass_g') or 0))
             for line in fresh.get('lines') or []]
    return {'一致': bool(diffs) and len(diffs) == len(old) and max(diffs) == 0, '最大差_g': max(diffs) if diffs else None,
            '行数': len(diffs)}


def compute_all(lib=None, report=print):
    lib = _lib(lib)
    reg = registry(lib)
    counts = {'配方核算': 0, '当量': 0, '工单重算对照': 0, '实称回算': 0, '出错': 0}
    for row in lib.rows('配方'):
        formula = formula_of(row)
        calc = formula_core.calculate_formula(formula, reg)
        lib.set_result(row['id'], '配方核算', calc, {'原料库': '当前'}, VERSION)
        counts['配方核算'] += 1
        counts['出错'] += not calc.get('ok')
        if (formula.get('equivalents') or {}).get('rows'):
            lib.set_result(row['id'], '当量', _eq.calculate_equivalents(formula, reg, calc), {}, VERSION)
            counts['当量'] += 1
    for row in lib.rows('工单'):
        if (row.get('冻结计划') or {}).get('historical'):
            continue                                         # 旧工作簿工单：只有计划量，原样保存，不重算
        formula, merged, batch = _inputs(row['id'], lib)
        fresh = formula_core.calculate_formula(formula, merged)
        lib.set_result(row['id'], '工单重算对照', _same_plan(fresh, batch['result']), {'组成': '冻结'}, VERSION)
        counts['工单重算对照'] += 1
        if batch['actual']['lines']:
            summary = _dose.actual_summary(batch)
            lib.set_result(row['id'], '实称回算', summary, {}, VERSION)
            counts['实称回算'] += 1
    report(f'{lib.name}：{counts}')
    return counts
