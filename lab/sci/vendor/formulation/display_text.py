"""Chinese presentation helpers; no persistence or calculation transforms."""
from __future__ import annotations
import math
import re
from .formula_core import normalize_dose_basis
from .beijing_time import format_time

FIELDS = dict(code='配方设计编号', total_mass_g='参考配样量', solid_fraction='目标总固含',
              note='备注', resins='树脂', additives='添加剂', solvents='溶剂', parts='份数',
              percent='添加剂设计值', dose_basis='用量基准', basis='有效份基准', order='投料顺序', source_type='来源类型',
              source_id='来源编号', process_factors='工艺与类别条件', equivalents='当量设置',
              mode='计算方式', reference_row_id='参照添加剂', rows='当量依据', row_id='添加剂',
              molar_mass_g_mol='摩尔质量', functionality='每分子官能团数',
              equivalent_weight_g_eq='当量质量', purity_fraction='纯度', purity_applied='使用纯度修正',
              active_fraction='有效成分比例', species_name='有效成分', species_id='有效成分编号',
              species_confirmed='已确认有效成分', fraction_mode='有效份依据', fraction='占比',
              name='名称', low='下限', high='上限', levels='水平', value='取值', unit='单位', label='变量名')
FIELDS.update(design_start='起算方式', resin_solid_g='起算树脂固体量', start_row_id='起算来源',
              start_amount_g='可用来源量', parts_basis='树脂份数基准', solvent_mode='溶剂分配方式',
              fixed_g='固定最终量', target_thickness_nm='目标膜厚', thickness_curve_id='膜厚曲线')
ENUMS = dict(solid='固体有效份', volatile='易挥发有效份', material='原料', premix='预混液',
             molar='摩尔比', functional='官能团当量比', component='按预混组分', explicit='明确指定')
ENUMS.update(total='按总量起算', resin_solid='按树脂固体起算', source_amount='按可用来源量起算',
             solution='溶液质量份数', ratio='按比例分配', fixed_g='固定最终量')
STATUSES = dict(planned='待配样', completed='已完成', void='已作废', archived='历史导入')
METHODS = dict(d_optimal='D 最优设计', full_factorial='全因子设计', fractional_half='二水平半分数因子设计',
               orthogonal_l9='三水平正交设计', central_composite='中心复合设计', box_behnken='响应面设计',
               latin_hypercube='拉丁超立方设计', simplex_lattice='单纯形格点设计', simplex_centroid='单纯形重心设计')

DOSE_UNITS = {
    'resin_solid_pct': '%（树脂固体）',
    'total_solid_pct': '%（总固体）',
    'total_pct': '%（总体系）',
    'total_ppm': 'ppm（总体系）',
    'fixed_g': 'g（固定有效份）',
    'mmol_per_g_resin': 'mmol/g 树脂',
}


def dose_unit(value=None):
    return DOSE_UNITS[normalize_dose_basis(value)]


def dose_text(value, basis=None):
    return number(value) + ' ' + dose_unit(basis)


def design_start_text(formula):
    mode = formula.get('design_start', 'total')
    if mode == 'resin_solid':
        return '按树脂固体 ' + number(formula.get('resin_solid_g')) + ' g 起算'
    if mode == 'source_amount':
        source = next((r.get('source_id') for group in ('resins', 'additives', 'solvents')
                       for r in formula.get(group, []) if r.get('id') == formula.get('start_row_id')), '未记录')
        return '按 ' + str(source) + ' 可用 ' + number(formula.get('start_amount_g')) + ' g 起算'
    return '按总量起算'


def design_total_mass(formula, calculation):
    if formula.get('design_start', 'total') == 'total':
        return formula.get('total_mass_g')
    totals = (calculation or {}).get('totals') or {}
    return totals.get('derived_total_mass_g', totals.get('total_mass_g'))


def row_design_text(row, group):
    if group == 'additives':
        return dose_text(row.get('percent'), row.get('dose_basis'))
    if group == 'solvents' and row.get('solvent_mode', 'ratio') == 'fixed_g':
        return str(row.get('source_id', '')) + ' 固定 ' + number(row.get('fixed_g')) + ' g'
    basis = '溶液质量份数' if row.get('parts_basis', 'solid') == 'solution' else '固体份数'
    return number(row.get('parts')) + ' ' + (basis if group == 'resins' else '相对份数')


def variable_unit(variable, formula):
    path = variable.get('key', '').split('.')
    additives = formula.get('additives') or []
    if (variable.get('type', 'numeric') == 'numeric' and len(path) == 3
            and path[0] == 'additives' and path[1].isdigit() and path[2] == 'percent'
            and int(path[1]) < len(additives)):
        return dose_unit(additives[int(path[1])].get('dose_basis'))
    return variable.get('unit', '')


def number(value, digits=6):
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return '—'
    text = f'{value:.{digits}f}'
    if digits:
        text = text.rstrip('0').rstrip('.')
    return '0' if text == '-0' else text


def readable(value):
    if value is None or value == '':
        return '未记录'
    if isinstance(value, bool):
        return '是' if value else '否'
    if isinstance(value, (int, float)):
        return number(value)
    if isinstance(value, (list, tuple)):
        return '；'.join(readable(v) for v in value) or '未记录'
    if isinstance(value, dict):
        if 'value' in value:
            return readable(value['value'])
        return '；'.join(FIELDS[k] + '：' + (dose_text(v, value.get('dose_basis')) if k == 'percent' else dose_unit(v) if k == 'dose_basis' else ('固体份数' if v == 'solid' and k == 'parts_basis' else ENUMS.get(v,'其他')) if k in ('basis','source_type','mode','fraction_mode','design_start','parts_basis','solvent_mode') and isinstance(v,str) else readable(v)) for k, v in value.items() if k in FIELDS) or '未记录'
    return format_time(value)


def condition_name(key, variables=(), index=0):
    label = next((v.get('label') or v.get('name') for v in variables if v.get('key') == key), None)
    if label == key:
        label = None
    return label or (f'条件 {index + 1}' if '.' in key or re.fullmatch(r'[a-z]+(?:_[a-z]+)*', key) else key)


def message_text(value, fallback='记录不完整，请检查设计条件后重试。'):
    text = value.get('message') if isinstance(value, dict) else value
    return text if isinstance(text, str) and re.search(r'[\u3400-\u9fff]', text) and not re.search(
        r'[{}]|\b[a-z]+_[a-z_]+\b|\b(?:undefined|null|NaN|JSON)\b|[0-9a-f]{16,}|\[object', text) else fallback


def changes(before, after, path=(), record=None):
    if before == after:
        return []
    record = record or {}
    if isinstance(before, (dict, list)) or isinstance(after, (dict, list)):
        def mapping(value):
            return dict(enumerate(value)) if isinstance(value, list) else value if isinstance(value, dict) else {}
        left, right = mapping(before), mapping(after)
        result = []
        if ('percent' in left or 'percent' in right) and ('添加剂' in path or 'dose_basis' in left or 'dose_basis' in right):
            old_basis = normalize_dose_basis(left.get('dose_basis'))
            new_basis = normalize_dose_basis(right.get('dose_basis'))
            if left.get('percent') != right.get('percent') or old_basis != new_basis:
                label = '占树脂固体 %' if old_basis == new_basis == 'resin_solid_pct' else FIELDS['percent']
                # Legacy correction captions already contain the complete dose unit.
                values = ((readable(left.get('percent')), readable(right.get('percent')))
                          if old_basis == new_basis == 'resin_solid_pct' else
                          (dose_text(left.get('percent'), old_basis), dose_text(right.get('percent'), new_basis)))
                result.append((' · '.join((*path, label)), *values))
            left = {k: v for k, v in left.items() if k not in ('percent', 'dose_basis')}
            right = {k: v for k, v in right.items() if k not in ('percent', 'dose_basis')}
        for key in dict.fromkeys([*left, *right]):
            label = (f'第 {key + 1} 行' if isinstance(key, int) else
                     '溶液质量份数' if key == 'parts' and '树脂' in path and (left.get('parts_basis') == 'solution' or right.get('parts_basis') == 'solution') else
                     '固体份数' if key == 'parts' and '树脂' in path else
                     '相对份数' if key == 'parts' and '溶剂' in path else FIELDS.get(key))
            if not label and path and path[-1] == '工艺与类别条件':
                label = condition_name(key, record.get('variables', []))
            if label:
                result += changes(left.get(key), right.get(key), (*path, label), record)
        return result
    def value(v):
        if path and path[-1] == '用量基准':
            return dose_unit(v)
        if path and path[-1] in ('有效份基准', '来源类型', '计算方式', '有效份依据', '起算方式', '树脂份数基准', '溶剂分配方式'):
            return '固体份数' if path[-1] == '树脂份数基准' and v == 'solid' else ENUMS.get(v, '其他')
        if path and path[-1] == '起算来源':
            return next((r.get('source_id', '未记录') for group in ('resins', 'additives', 'solvents')
                         for r in record.get('data', {}).get(group, []) if r.get('id') == v), '未记录')
        if path and path[-1] in ('参照添加剂', '添加剂'):
            return next((r['source_id'] for r in record.get('data', {}).get('additives', []) if r.get('id') == v), '未记录')
        if path and path[-1] in ('目标总固含', '纯度', '有效成分比例'):
            return number(v*100,4)+'%' if isinstance(v,(int,float)) else '未记录'
        return readable(v)
    return [(' · '.join(path), value(before), value(after))]
