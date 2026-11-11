"""Editable v2 settings and their translation to the unchanged v1 core config.

搬自 ellipsometer-2-v7ui（提交 ddbc78a4）；只把 `from src.` 改成 `from ..src.`。
"""
from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Mapping

from .conditions import CONDITION_FIELDS
from .treatments import DEFAULT_TREATMENTS, validate_treatments
from ..src.sheet_detector import STANDARD_FIELDS
from ..src.parser import NUMERIC_FIELDS

MAPPING_FIELDS = {*STANDARD_FIELDS, 'header_row'}

RESULT_KEYS = {'thresholds', 'tolerances', 'outliers', 'treatments', 'dissolution_markers', 'series', 'manual_mappings'}
DEFAULT_GROUP = ['formulation', 'spin_rpm', 'bake_temp_c', 'bake_time_s', 'treatment', 'test_date', 'variant']
EXCLUSION_REASONS = ['拟合失败/MSE过高', '片子边缘', '污染或划伤', '算法标记的离群点', '录入错误', '其他']
REFERENCE_MATCH_FIELDS = ('spin_rpm', 'bake_temp_c', 'bake_time_s', 'treatment', 'treatment_time', 'post_bake_temp_c', 'post_bake_time_s')
GROUP_KEYS = {'formulation', 'test_date', *CONDITION_FIELDS}
TOLERANCE_FIELDS = {
    'thickness_before_relative_percent', 'thickness_after_relative_percent',
    'thickness_loss_percent_pp', 'retention_percent_pp', 'n_193_absolute',
    'k_193_absolute', 'n_248_absolute', 'k_248_absolute', 'n_633_absolute',
    'k_633_absolute', 'mse_maximum', 'thickness_cv_maximum',
}

QUALITY_RULE_DEFAULTS = {
    'copied_columns': {'enabled': True, 'equal_tolerance': 0.0, 'ignore_zero_pairs': True,
                       'block_systematic_fraction': 0.5},
    'mse': {'enabled': True, 'absolute_max': 20.0, 'relative_to_block_median': 2.0,
            'relative_min_readings': 3, 'block_merge_fraction': 0.5},
    'optical_outliers': {
        'enabled': True, 'mad_multiplier': 3.5, 'min_readings': 3,
        'rows': [
            {'name': 'n', 'label': 'n（折射率）', 'fields': ['n_193', 'n_248', 'n_633'], 'enabled': True,
             'min_relative_percent': 1.0, 'min_absolute': 0.0, 'min_absolute_relative_percent': 0.0},
            {'name': 'k', 'label': 'k（消光系数）', 'fields': ['k_193', 'k_248', 'k_633'], 'enabled': True,
             'min_relative_percent': 1.0, 'min_absolute': 0.001, 'min_absolute_relative_percent': 2.0},
            {'name': 'thickness', 'label': '膜厚（第二道）', 'fields': ['thickness'], 'enabled': True,
             'min_relative_percent': 1.5, 'min_absolute': 1.0, 'min_absolute_relative_percent': 0.0},
        ],
    },
    'readings': {'enabled': True, 'expected_count': 6, 'flag_text_numbers': True},
    'average_row': {'enabled': True, 'flag_empty_cell': True, 'flag_missing_row': True,
                    'tolerance': {'thickness': 0.01, 'mse': 0.01, 'n': 0.0005, 'k': 0.0005}},
    'duplicate_conflict': {'enabled': True, 'value_tolerance': 0.0},
}

V5_DEFAULT_CONDITIONS = {'rows': [
    {'series_id': '*', 'label': '所有产品（未单独设置时）',
     'values': {'bake_temp_c': 250, 'bake_time_s': 120},
     'note': '用户确认 2026-10-22：没写条件的都是 250℃/120s'},
]}


def backfill_quality_rules(values: Mapping, defaults: Mapping | None = None) -> dict:
    """Fill absent keys without restoring rows deliberately removed by the user."""
    template = defaults if defaults is not None else QUALITY_RULE_DEFAULTS

    def fill(value, default):
        if isinstance(value, Mapping) and isinstance(default, Mapping):
            result = deepcopy(dict(value))
            for key, item in default.items():
                result[key] = fill(value[key], item) if key in value else deepcopy(item)
            return result
        if isinstance(value, list) and isinstance(default, list):
            named = {row.get('name'): row for row in default if isinstance(row, Mapping)}
            return [fill(row, named.get(row.get('name'), {
                'name': row.get('name'), 'label': str(row.get('name') or '读数'),
                'fields': [row['name']] if row.get('name') in NUMERIC_FIELDS else [],
                'enabled': True, 'min_relative_percent': 0.0,
                'min_absolute': 0.0, 'min_absolute_relative_percent': 0.0}))
                    if isinstance(row, Mapping) else deepcopy(row) for row in value]
        return deepcopy(value)

    return fill(values, template)


def _validate_quality_rules(values):
    def validate(value, template, label):
        if isinstance(template, Mapping):
            if not isinstance(value, Mapping):
                raise ValueError(f'{label} 必须为对象')
            for key, item in value.items():
                if key in template:
                    validate(item, template[key], f'{label}.{key}')
        elif isinstance(template, bool):
            if not isinstance(value, bool):
                raise ValueError(f'{label} 必须为布尔值')
        elif isinstance(template, (int, float)):
            _number(value, label, integer=label.endswith(('.relative_min_readings', '.min_readings', '.expected_count')))
            if label.endswith('_fraction') and value > 1:
                raise ValueError(f'{label} 必须在 0–1 之间')
        elif isinstance(template, list):
            if label.endswith('.rows'):
                if not isinstance(value, list):
                    raise ValueError(f'{label} 必须为列表')
                for row in value:
                    validate(row, template[0], label)
            else:
                _strings(value, label)
        elif isinstance(template, str) and not isinstance(value, str):
            raise ValueError(f'{label} 必须为字符串')

    validate(values, QUALITY_RULE_DEFAULTS, 'quality_rules')


def default_settings(config: Mapping) -> dict:
    quality = config.get('quality', {})
    return {
        'display': {'decimals': config.get('visualization', {}).get('decimal_places', 4)},
        'thresholds': {'mse_warning': quality.get('mse_warning_threshold', 20),
                       'high_cv_percent': quality.get('high_cv_threshold_percent', 2.0)},
        'tolerances': deepcopy(config['reference_comparison']['tolerances']),
        'outliers': {'method': quality.get('outlier_method', 'iqr'),
                     'iqr_multiplier': quality.get('iqr_multiplier', 1.5),
                     'mad_z_threshold': quality.get('mad_z_threshold', 3.5),
                     'minimum_count': quality.get('minimum_outlier_detection_count', 4)},
        'quality_rules': deepcopy(QUALITY_RULE_DEFAULTS),
        'default_conditions': {'rows': [
            *deepcopy(V5_DEFAULT_CONDITIONS['rows']),
            {'series_id': '*', 'label': '所有产品（未单独设置时）',
             'values': {'treatment_time': '2min'},
             'note': '用户确认 2026-10-23：没写处理时间的都是 2 分钟'},
            {'series_id': '*', 'label': '所有产品（未单独设置时）',
             'values': {'post_bake_temp_c': 250, 'post_bake_time_s': 60},
             'note': '用户确认 2026-10-23：没写后烘的都是 250℃/60s'},
        ]},
        'treatments': deepcopy(config.get('treatments', DEFAULT_TREATMENTS)),
        'dissolution_markers': deepcopy(config['stage_rules'].get('dissolution_markers', [])),
        'series': [
            {'series_id': 'product-a', 'name': '产品A', 'reference_samples': ['REF01'],
             'code_rules': [{'pattern': r'^(?:PA|R100)', 'kind': 'regex'}, {'pattern': r'^REF01$', 'kind': 'regex'}]},
            {'series_id': 'product-b', 'name': '产品B', 'reference_samples': ['PB-POR'],
             'code_rules': [{'pattern': r'^PB', 'kind': 'regex'}]},
            {'series_id': 'unassigned', 'name': '未分配', 'reference_samples': [], 'code_rules': []},
        ],
        'exclusion_reasons': list(EXCLUSION_REASONS),
        'default_group_by': list(DEFAULT_GROUP),
        'reference_match_fields': list(REFERENCE_MATCH_FIELDS),
        'data_prefs': {'backup_keep': 10, 'export_dir': None, 'copy_imports': True},
        'thickness_unit': {'value': 'nm', 'status': 'confirmed', 'basis': '用户 2026-10-22 确认'},
        'hierarchy': {'library': '项目库', 'project': '项目', 'batch': '批次',
                      'sample': '样品块', 'reading': '读数', 'hide_batch': True},
        'manual_mappings': deepcopy(config.get('manual_mappings', {})),
    }


def _number(value, label: str, minimum=0, integer=False, nullable=False):
    if value is None and nullable:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} 必须为有限数值' + ('或 null' if nullable else ''))
    if value < minimum or (integer and not isinstance(value, int)):
        raise ValueError(f'{label} 必须为大于等于 {minimum} 的' + ('整数' if integer else '数值'))


def _strings(value, label, allow_empty=True):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError(f'{label} 必须为非空字符串列表')
    if not allow_empty and not value:
        raise ValueError(f'{label} 不能为空')


def validate_settings(values: Mapping, defaults: Mapping | None = None) -> dict:
    """Validate a complete value object and return an independent copy."""
    if not isinstance(values, Mapping):
        raise ValueError('values 必须为对象')
    for key, item in values.items():
        if key in {'display', 'thresholds', 'tolerances', 'outliers'}:
            if not isinstance(item, Mapping):
                raise ValueError(f'{key} 必须为对象')
            valid_keys = {
                'display': {'decimals'}, 'thresholds': {'mse_warning', 'high_cv_percent'},
                'outliers': {'method', 'iqr_multiplier', 'mad_z_threshold', 'minimum_count'},
                'tolerances': TOLERANCE_FIELDS,
            }[key]
            for field, value in item.items():
                if field not in valid_keys:
                    continue
                label = f'{key}.{field}'
                if key == 'display':
                    _number(value, label, integer=True)
                    if value > 8:
                        raise ValueError('显示小数位 decimals 必须为 0–8 的整数')
                elif key == 'outliers' and field == 'method':
                    if not isinstance(value, str) or value not in {'iqr', 'mad'}:
                        raise ValueError('离群方法 method 必须为 iqr 或 mad')
                elif key == 'outliers' and field == 'minimum_count':
                    _number(value, label, minimum=2, integer=True)
                else:
                    _number(value, label, nullable=key == 'tolerances')
                    if key == 'outliers' and value == 0:
                        raise ValueError(f'{label} 必须大于 0')
        elif key == 'quality_rules':
            _validate_quality_rules(item)
        elif key == 'default_conditions':
            if not isinstance(item, Mapping) or not isinstance(item.get('rows'), list):
                raise ValueError('default_conditions.rows 必须为列表')
            for row in item['rows']:
                if not isinstance(row, Mapping) or not isinstance(row.get('series_id'), str) or not row['series_id'].strip():
                    raise ValueError('默认条件行必须包含非空 series_id')
                fields = row.get('values', {})
                if not isinstance(fields, Mapping) or set(fields) - set(REFERENCE_MATCH_FIELDS):
                    raise ValueError('默认条件 values 只能包含 7 项工艺条件')
                for field, value in fields.items():
                    if value in (None, ''):
                        continue
                    if field in {'treatment', 'treatment_time'} and isinstance(value, str):
                        continue
                    _number(value, 'default_conditions.' + field)
        elif key == 'treatments':
            errors = validate_treatments(item)
            if errors:
                raise ValueError('；'.join(errors))
        elif key == 'dissolution_markers':
            _strings(item, '溶解标记')
            for pattern in item:
                try:
                    re.compile(pattern)
                except re.error as error:
                    raise ValueError(f'溶解标记正则无效：{pattern}（{error}）') from error
        elif key == 'series':
            if not isinstance(item, list):
                raise ValueError('series 必须为系列列表')
            seen = set()
            for row in item:
                if not isinstance(row, Mapping) or not isinstance(row.get('series_id'), str) or not row['series_id'].strip() or not isinstance(row.get('name'), str) or not row['name'].strip():
                    raise ValueError('系列必须包含非空 series_id 和 name')
                if row['series_id'] in seen:
                    raise ValueError(f'系列编号重复：{row["series_id"]}')
                seen.add(row['series_id'])
                _strings(row.get('reference_samples', []), '标准参比编号')
                if not isinstance(row.get('code_rules', []), list):
                    raise ValueError('系列 code_rules 必须为列表')
                for rule in row.get('code_rules', []):
                    if not isinstance(rule, Mapping) or not isinstance(rule.get('pattern'), str) or not rule['pattern'].strip():
                        raise ValueError('系列编号规则必须包含非空 pattern')
                    kind = rule.get('kind', 'regex')
                    if not isinstance(kind, str) or kind not in {'regex', 'prefix', 'suffix', 'exact', 'alias'}:
                        raise ValueError('系列编号规则 kind 必须为 regex/prefix/suffix/exact/alias')
                    if kind == 'regex':
                        try:
                            re.compile(rule['pattern'])
                        except re.error as error:
                            raise ValueError(f'系列编号正则无效：{rule["pattern"]}（{error}）') from error
        elif key == 'exclusion_reasons':
            _strings(item, '常用剔除原因')
        elif key == 'default_group_by':
            _strings(item, '默认分组依据', allow_empty=False)
            if len(item) != len(set(item)) or set(item) - GROUP_KEYS:
                raise ValueError('默认分组依据含重复或不支持的字段')
        elif key == 'reference_match_fields':
            _strings(item, '参比同条件字段', allow_empty=False)
            if len(item) != len(set(item)) or set(item) - set(CONDITION_FIELDS):
                raise ValueError('参比同条件字段含重复或不支持的字段')
        elif key == 'data_prefs':
            if not isinstance(item, Mapping):
                raise ValueError('data_prefs 必须为对象')
            _number(item.get('backup_keep', 10), 'backup_keep', minimum=1, integer=True)
            if not isinstance(item.get('copy_imports', True), bool):
                raise ValueError('copy_imports 必须为布尔值')
            if item.get('export_dir') is not None and (not isinstance(item['export_dir'], str) or not item['export_dir'].strip()):
                raise ValueError('export_dir 必须为非空路径或 null')
        elif key == 'thickness_unit':
            if not isinstance(item, Mapping):
                raise ValueError('thickness_unit 必须为对象')
            for field in ('value', 'status', 'basis'):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    raise ValueError(f'thickness_unit.{field} 必须为非空字符串')
        elif key == 'hierarchy':
            if not isinstance(item, Mapping) or not isinstance(item.get('hide_batch'), bool):
                raise ValueError('hierarchy 必须含布尔值 hide_batch')
            for field in ('library', 'project', 'batch', 'sample', 'reading'):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    raise ValueError(f'hierarchy.{field} 不能为空')
        elif key == 'manual_mappings':
            if not isinstance(item, Mapping):
                raise ValueError('manual_mappings 必须为对象')
            for sheet, mapping in item.items():
                if sheet == 'source':
                    continue
                if not isinstance(mapping, Mapping):
                    continue  # Metadata written by a newer version is opaque.
                for column, index in mapping.items():
                    if column in MAPPING_FIELDS:
                        _number(index, f'{sheet}.{column}', minimum=1, integer=True)
    return deepcopy(dict(values))


def merge_settings(current: Mapping, patch: Mapping, defaults: Mapping | None = None) -> dict:
    if not isinstance(patch, Mapping):
        raise ValueError('values 必须为对象')
    if 'default_conditions' in patch:
        validate_settings({'default_conditions': patch['default_conditions']})
    def merge(old, new):
        if isinstance(old, Mapping) and isinstance(new, Mapping):
            result = deepcopy(dict(old))
            for key, value in new.items():
                result[key] = merge(result.get(key), value)
            return result
        if isinstance(old, list) and isinstance(new, list):
            # Preserve extension fields on known settings rows that survive an
            # edit. The submitted list still determines membership and order.
            def identity(row):
                if not isinstance(row, Mapping):
                    return None
                for field in ('series_id', 'name', 'pattern'):
                    if isinstance(row.get(field), str):
                        return field, row[field]
                return None
            indexed = {identity(row): row for row in old if identity(row) is not None}
            return [merge(indexed[identity(row)], row) if identity(row) in indexed else deepcopy(row) for row in new]
        return deepcopy(new)
    result = merge(current, patch)
    if 'default_conditions' in patch:
        # Rows are independent groups; a series may have multiple distinct rows.
        result['default_conditions'] = deepcopy(patch['default_conditions'])
    if isinstance(patch.get('manual_mappings'), Mapping):
        previous = current.get('manual_mappings', {})
        # Mappings are an editable collection; an empty object restores automatic
        # detection. Keep provenance and opaque metadata alongside that collection.
        mappings = {k: deepcopy(v) for k, v in previous.items() if not isinstance(v, Mapping)}
        for sheet, mapping in patch['manual_mappings'].items():
            if isinstance(mapping, Mapping):
                opaque = {k: deepcopy(v) for k, v in previous.get(sheet, {}).items() if k not in MAPPING_FIELDS} if isinstance(previous.get(sheet), Mapping) else {}
                mappings[sheet] = {**opaque, **deepcopy(mapping)}
            else:
                mappings[sheet] = deepcopy(mapping)
        result['manual_mappings'] = mappings
    if 'quality_rules' in result:
        result['quality_rules'] = backfill_quality_rules(result['quality_rules'])
    return validate_settings(result, defaults or current)


def apply_to_config(config: Mapping, values: Mapping) -> dict:
    """Translate every computational setting to the existing core's keys."""
    result = deepcopy(dict(config))
    quality = result['quality']
    quality.update(mse_warning_threshold=values['thresholds']['mse_warning'],
                   high_cv_threshold_percent=values['thresholds']['high_cv_percent'],
                   outlier_method=values['outliers']['method'],
                   iqr_multiplier=values['outliers']['iqr_multiplier'],
                   mad_z_threshold=values['outliers']['mad_z_threshold'],
                   minimum_outlier_detection_count=values['outliers']['minimum_count'])
    result['analysis']['decimal_places'] = values['display']['decimals']
    result['analysis']['thickness_unit'] = values['thickness_unit']['value']
    result['visualization']['decimal_places'] = values['display']['decimals']
    result['reference_comparison']['tolerances'] = deepcopy(values['tolerances'])
    result['treatments'] = deepcopy(values['treatments'])
    result['stage_rules']['dissolution_markers'] = deepcopy(values['dissolution_markers'])
    result['manual_mappings'] = {k: {field: deepcopy(index) for field, index in v.items() if field in MAPPING_FIELDS}
                                 for k, v in values.get('manual_mappings', {}).items() if isinstance(v, Mapping)}
    patterns = list(result['stage_rules']['after_markers'])
    # All explicit named markers must reach the parser's existing after-stage
    # dissolution logic. Known and pending names are separated in the v2 layer.
    patterns.append(r'^\s*\d+\s*[（(]\s*[^()（）]+\s*[）)]\s*$')
    for treatment in values['treatments']:
        for alias in [treatment['name'], *treatment.get('aliases', [])]:
            pattern = r'\s*'.join(re.escape(char) for char in re.sub(r'\s+', '', alias))
            patterns.extend([rf'^\s*{pattern}\s*(?:后|处理后)\s*$',
                             rf'^\s*After\s*{pattern}\s*$', rf'^\s*{pattern}\s*after\s*$'])
    result['stage_rules']['after_markers'] = list(dict.fromkeys(patterns))
    return result


def restore_settings(current: Mapping, defaults: Mapping, keys=None) -> dict:
    if keys is not None and (not isinstance(keys, list) or any(not isinstance(key, str) for key in keys)):
        raise ValueError('恢复默认 keys 必须为设置名称列表')
    selected = list(keys or defaults)
    if any(key not in defaults for key in selected):
        raise ValueError('恢复默认时包含未知设置键')
    patch = {key: defaults[key] for key in selected}
    result = merge_settings(current, patch, defaults)
    for key in ('quality_rules', 'default_conditions'):
        if key in selected:
            result[key] = deepcopy(defaults[key])
    return result


def affects_results(before: Mapping, after: Mapping) -> bool:
    return any(before.get(key) != after.get(key) for key in RESULT_KEYS)


# Short aliases keep Store integration readable.
defaults = default_settings
validate = validate_settings
apply_config = apply_to_config
