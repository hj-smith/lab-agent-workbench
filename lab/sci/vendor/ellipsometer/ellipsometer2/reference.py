"""Group-level adapters for the unchanged v1 reference comparison engine.

搬自 ellipsometer-2-v7ui/ellipsometer2/reference.py（提交 ddbc78a4）。改动只有取数：
  - import 改成包内相对路径（..src.、.memstore）；
  - _read_choices 读 store.reference_choices（调用方传入），不再读 SQLite 表；
  - _controls 里不再查 projects 表（新库没有项目，测试日就是日期）。
写参比选择的函数（_write_choices、put_choice、put_choices_bulk、put_choices_batch）原样留着但不用：
新系统里参比选择存在新库的设置 `椭偏.参比选择` 里，由 lab.sci.ellipsometry 传进来。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from functools import lru_cache

import pandas as pd

from ..src.analyzer import _series_stats, safe_relative_change
from ..src.comparison_summary import generate_comparison_summary
from ..src.reference_comparator import compare_to_reference
from ..src.reference_scope import OPTICAL_FIELDS

from .memstore import clean, dumps
from .read_views import cached_view

GROUP_STATUS_FIELDS=('n_samples','dissolved_count','after_count','dissolution_notes')


def _group_status(group):
    return {key:group.get(key) for key in GROUP_STATUS_FIELDS}


def group_optics(optical_rows, sample_ids, after_stage, measurements=None,statistics=None):
    """Mean = mean of sample means; SD/CV/n = repeat measurements, as for thickness (API_R2 F)."""
    ids = set(sample_ids)
    statistics=statistics or (lambda values:_series_stats(pd.Series(values,dtype=float)))
    # Custom groups may combine ordinary single-stage samples with paired
    # samples. Keep the former's before statistics alongside the chosen stage.
    exact_ids = {row['sample_instance_id'] for row in optical_rows
                 if row['sample_instance_id'] in ids and row.get('after_stage') == after_stage}
    selected = [row for row in optical_rows if row['sample_instance_id'] in ids and
                (row.get('after_stage') == after_stage or
                 (row.get('after_stage') is None and row['sample_instance_id'] not in exact_ids))]
    result = {}
    for parameter in (*OPTICAL_FIELDS, 'mse'):
        matching = [row for row in selected if row['parameter'] == parameter]
        sides = {}
        for side in ('before', 'after'):
            stats = statistics([row.get(side + '_mean') for row in matching])
            sides[side] = dict(mean=stats['mean'], sd=stats['std'], cv=stats['cv'], n=stats['count'])
            if measurements is not None:
                repeat = _repeat_stats(measurements, ids, parameter, side, after_stage,statistics)
                mean = sides[side]['mean']
                sides[side].update(sd=repeat['std'], n=repeat['count'],
                                   cv=repeat['std'] / mean * 100 if repeat['std'] is not None and mean else None)
        before, after = sides['before']['mean'], sides['after']['mean']
        result[parameter] = dict(**sides, drift=after - before if before is not None and after is not None else None)
    return clean(result)


def _repeat_stats(measurements, ids, parameter, side, after_stage,statistics=None):
    """Pool only readings whose selected optical parameter remains included."""
    after_code = 'after_' + after_stage.rsplit('_', 1)[1] if after_stage and after_stage.startswith('after_solva_') else None
    wanted = ('before', 'single') if side == 'before' else (after_code,)
    values = [m.get('effective_values', m).get(parameter) for m in measurements
              if m['sample_id'] in ids and m['stage'] in wanted
              and not (m.get('excluded') and (m['excluded'].get('scope') == 'all' or
                       m['excluded'].get('scope') == 'thickness' and parameter == 'thickness' or
                       m['excluded'].get('scope') == 'fields' and parameter in m['excluded'].get('fields', [])))]
    return statistics(values) if statistics else _series_stats(pd.Series(values, dtype=float))


@cached_view
def _contexts(store, **filters):
    summary = store.groups_summary(**filters)
    samples = {sample['sample_id']: sample for sample in store.samples()}
    memberships = {}
    for row in summary.get('samples', []):
        memberships.setdefault(row['group_key'], set()).add(row['sample_id'])
    result = []
    for group in summary['groups']:
        group = dict(group)
        ids = group.get('sample_ids') or memberships.get(group['group_key'], ())
        members = [samples[sid] for sid in ids if sid in samples]
        group['_members'] = members
        group.setdefault('series_id', members[0]['series_id'] if members and len({m['series_id'] for m in members}) == 1 else None)
        group.setdefault('test_date', members[0]['test_date'] if members and len({m['test_date'] for m in members}) == 1 else None)
        group.setdefault('link_status', 'reference' if members and all(m['link']['status'] == 'reference' for m in members) else None)
        result.append(group)
    return result


def _date_key(value):
    """Date ordering must handle both ISO storage and legacy slash dates."""
    if value is None:
        return datetime.min
    return _parsed_date(str(value))


@lru_cache(maxsize=256)
def _parsed_date(text):
    parsed = pd.to_datetime(text, errors='coerce')
    return parsed.to_pydatetime().replace(tzinfo=None) if pd.notna(parsed) else datetime.min


@cached_view
def _sample_groups(store, series_id=''):
    """Every sample as a one-sample group: {sample_id: [group, ...]} (one per post-treatment stage)."""
    from .memstore import api_stage
    result = {}
    for group in _contexts(store, series_id=series_id, group_by=['formulation'], per_sample=True):
        if not group.get('sample_ids') or len(group['sample_ids']) != 1:
            continue
        sample_id = group['sample_ids'][0]
        member = group['_members'][0] if group['_members'] else {}
        code = member.get('sample_code') or group['label']
        label = code + (' · ' + str(group['test_date']) if group.get('test_date') else '')
        stage = group.get('after_stage')
        if stage and stage != 'after_solva_1':
            label += ' · ' + api_stage(stage)[1]
        group.update(group_key='sample:' + sample_id, label=label, kind='sample', sample_code=code, link_status=None)
        result.setdefault(sample_id, []).append(group)
    return result


def _kind(group):
    return group.get('kind') or ('standard' if group.get('link_status') == 'reference' else 'group')


def _has_reference_data(group, require_after=True):
    has_data = group.get('before_mean') is not None or any(
        _side(group.get('optics', {}), parameter, side)['mean'] is not None
        for parameter in (*OPTICAL_FIELDS, 'mse') for side in ('before', 'after'))
    return has_data and (not require_after or group.get('after_mean') is not None)


def _option(group):
    item = dict(reference_key=group['group_key'], label=group['label'], kind=_kind(group),
                series_id=group.get('series_id'), test_date=group.get('test_date'),
                conditions=group['conditions'], condition_sources=group.get('condition_sources', {}),
                default_note=group.get('default_note'), n_samples=group.get('n_samples'),
                has_valid_data=_has_reference_data(group, group.get('after_stage') is not None))
    if group.get('sample_code'):
        item['sample_code'] = group['sample_code']
    return item


def reference_options(store, series_id=''):
    groups = _contexts(store, series_id=series_id)
    options = [_option(g) for g in groups]
    standards = [item for item in options if item['kind'] == 'standard' and item['has_valid_data']]
    latest = max(standards, key=lambda item: (_date_key(item['test_date']), item['reference_key']), default=None)
    for stages in _sample_groups(store, series_id).values():
        options.append(_option(stages[0]))
    return clean(dict(default_key=latest['reference_key'] if latest else None, options=options))


def _side(optics, parameter, side):
    value = optics.get(parameter, {}).get(side)
    # Opening a step-1 group remains supported while persisted data is upgraded.
    return value if isinstance(value, dict) else dict(mean=value, sd=None, cv=None, n=0 if value is None else 1)


def _difference(left, right):
    return left - right if left is not None and right is not None else None


def _engine_tables(groups, selected_stage, near_zero):
    thickness, optical = [], []
    for group in groups:
        conditions = group.get('conditions', {})
        optics = group.get('optics', {})
        before, after = group.get('before_mean'), group.get('after_mean')
        before_mse = _side(optics, 'mse', 'before')['mean']
        after_mse = _side(optics, 'mse', 'after')['mean']
        stage = group.get('after_stage')
        single = stage is None and after is None
        completeness = ('常规单阶段' if single else '完整' if before is not None and after is not None
                        else '缺少处理前数据' if before is None else '缺少处理后数据')
        thickness.append(dict(
            sample_instance_id=group['group_key'], sample_id=group['group_key'], sample_raw=group['label'],
            source_file='组均值', source_sheet=group['label'], source_file_hash=None,
            test_date=group.get('test_date'), after_stage=selected_stage, model=group.get('model'),
            process_condition=dumps({k: conditions.get(k) for k in ('spin_rpm', 'bake_temp_c', 'bake_time_s', 'post_bake_temp_c', 'post_bake_time_s')}),
            solva_condition=dumps({k: conditions.get(k) for k in ('treatment', 'treatment_time')}),
            bake_temperature=conditions.get('bake_temp_c'), bake_time=conditions.get('bake_time_s'),
            solva_time=conditions.get('treatment_time'),
            before_thickness_mean=before, after_thickness_mean=after,
            before_thickness_std=group.get('before_sd'), after_thickness_std=group.get('after_sd'),
            before_thickness_cv=group.get('before_cv'), after_thickness_cv=group.get('after_cv'),
            before_measurement_count=group.get('n_before', 0), after_measurement_count=group.get('n_after', 0),
            delta_thickness=_difference(after, before), thickness_loss=group.get('loss'),
            loss_percent=group.get('loss_percent'), retention_percent=group.get('retention_percent'),
            change_percent=-group['loss_percent'] if group.get('loss_percent') is not None else None,
            thickness_cv_change=_difference(group.get('after_cv'), group.get('before_cv')),
            before_mse_mean=before_mse, after_mse_mean=after_mse,
            mse_change=_difference(after_mse, before_mse), quality_warning_count=0,
            after_dissolved=bool(group.get('dissolved_count')), data_completeness_status=completeness,
            before_source_rows='', after_source_rows='', warning_message='',
        ))
        for parameter in (*OPTICAL_FIELDS, 'mse'):
            before_opt = _side(optics, parameter, 'before')
            after_opt = _side(optics, parameter, 'after')
            optical.append(dict(
                sample_instance_id=group['group_key'], parameter=parameter, after_stage=selected_stage,
                before_mean=before_opt['mean'], after_mean=after_opt['mean'],
                before_std=before_opt.get('sd'), after_std=after_opt.get('sd'),
                before_cv_percent=before_opt.get('cv'), after_cv_percent=after_opt.get('cv'),
                before_count=before_opt.get('n'), after_count=after_opt.get('n'),
                absolute_change=_difference(after_opt['mean'], before_opt['mean']),
                relative_change_percent=safe_relative_change(after_opt['mean'], before_opt['mean'], near_zero),
            ))
    return pd.DataFrame(thickness), pd.DataFrame(optical)


def _verdict(detail):
    if detail['reference_value'] is None or detail['sample_value'] is None:
        return 'not_evaluable'
    if detail['evaluation_status'] == 'not_configured':
        return 'not_configured'
    if detail['evaluation_status'] not in ('evaluated', 'evaluated_absolute_only'):
        return 'not_evaluable'
    if detail.get('absolute_limit_status') == '超过配置最大值':
        return 'worse'
    classification = str(detail['comparison_class'])
    if '优于' in classification:
        return 'better'
    if classification in ('与参考样基本相当', '符合配置最大值') or detail.get('absolute_limit_status') == '符合配置最大值':
        return 'within'
    return 'worse'


def _inapplicable_reason(candidate, reference, metric):
    if metric in ('before_thickness_cv', 'after_thickness_cv') and any(
            group.get('n_samples') == 1 for group in (candidate, reference)):
        return '单个样品块没有重复组 CV'
    after_optics = metric == 'after_mse_mean' or metric in {p + '_after_mean' for p in (*OPTICAL_FIELDS, 'mse')}
    if after_optics and any(group.get('dissolved_count', 0) > 0 and
                            group['dissolved_count'] == (group.get('after_count') or group.get('n_samples'))
                            for group in (candidate, reference)):
        return '已溶解，处理后无膜可测'
    return None


def _overall_verdict(verdicts, reasons):
    statuses = set(verdicts.values())
    verdict = next((s for s in ('worse', 'not_evaluable', 'not_configured', 'within', 'better') if s in statuses),
                   'not_applicable')
    missing = sum(value == 'not_evaluable' for value in verdicts.values())
    if verdict == 'worse':
        reason = f"{sum(value == 'worse' for value in verdicts.values())} 项超出容差"
        if missing:
            reason += f'；另有 {missing} 项无法评价'
    elif verdict == 'not_evaluable':
        reason = '；'.join(dict.fromkeys(part for key, value in verdicts.items() if value == 'not_evaluable'
                                       for part in reasons[key].split('；')))
    else:
        reason = '没有适用的比较项' if verdict == 'not_applicable' else '综合适用项判断'
    return verdict, reason


def _reference_payload(group):
    return {**_group_status(group), 'has_valid_data': _has_reference_data(group, group.get('after_stage') is not None),
            **{k: group.get(k) for k in ('conditions', 'condition_sources', 'default_note')},
            **{key: group.get(source) for key, source in (
        ('reference_key', 'group_key'), ('label', 'label'), ('before_mean', 'before_mean'),
        ('after_mean', 'after_mean'), ('loss_percent', 'loss_percent'),
        ('retention_percent', 'retention_percent'), ('optics', 'optics'))}}


def _stage(body):
    stage = str(body.get('after_stage') or 'after_1')
    if stage.startswith('after_') and stage[6:].isdigit():
        stage = 'after_solva_' + stage[6:]
    if not stage.startswith('after_solva_') or not stage.rsplit('_', 1)[1].isdigit():
        raise ValueError('after_stage 必须是 after_1 等处理后阶段')
    return stage


def _comparison(store, body):
    reference_key = body.get('reference_key')
    candidate_keys = body.get('candidate_group_keys', [])
    if not reference_key or not isinstance(candidate_keys, list):
        raise ValueError('reference_key 必填，candidate_group_keys 必须是列表')
    stage = _stage(body)
    # Optional grouping selectors let report exports reuse the exact displayed scope.
    filters = {key: body[key] for key in ('series_id', 'group_by', 'formulation_keys', 'custom_group_ids') if body.get(key)}
    groups = {group['group_key']: group for group in _contexts(store, **filters)}
    wanted = list(dict.fromkeys([reference_key, *candidate_keys]))
    if any(key.startswith('sample:') for key in wanted if isinstance(key, str)):
        singles = {'sample:' + sid: items for sid, items in _sample_groups(store).items()}
        for key in wanted:
            if key in singles and key not in groups:
                # A sample with several post-treatment stages: take the requested stage.
                groups[key] = next((g for g in singles[key] if g.get('after_stage') in (None, stage)), singles[key][0])
    for key in wanted:
        if key not in groups:
            raise KeyError('分组不存在或已没有可分析测量：' + str(key))
        if groups[key].get('after_stage') not in (None, stage):
            raise ValueError('分组的处理后阶段与 after_stage 不一致：' + str(key))
    reference = groups[reference_key]
    config = deepcopy(store.config)
    config['reference_comparison']['after_stage'] = stage
    near_zero = float(config['reference_comparison'].get('near_zero_threshold', 1e-8))
    thickness, optical = _engine_tables([groups[key] for key in wanted], stage, near_zero)
    engine = compare_to_reference(reference_key, thickness, optical, pd.DataFrame(), pd.DataFrame(), config)
    return reference, groups, config, engine


def compare_tables(store, body):
    """Return untouched core wide/long tables and reference metadata for exporters."""
    reference, _, _, engine = _comparison(store, body)
    return engine, clean(_reference_payload(reference))


def _build(store, comparison):
    reference, groups, config, engine = comparison
    near_zero = float(config['reference_comparison'].get('near_zero_threshold', 1e-8))
    summary_details = engine.long.copy()
    missing = summary_details['reference_value'].isna() | summary_details['sample_value'].isna()
    summary_details.loc[missing, 'evaluation_status'] = 'not_evaluable'
    summary_details.loc[missing, 'comparison_class'] = '无法评价'
    summary_details.loc[missing, 'absolute_limit_status'] = None
    for index, detail in summary_details.iterrows():
        if _inapplicable_reason(groups[detail['sample_instance_id']], reference, detail['metric_name']):
            summary_details.loc[index, ['evaluation_status', 'comparison_class', 'absolute_limit_status', 'tolerance_configured']] = [
                'not_applicable', '不适用', None, False]
    summary = generate_comparison_summary(engine.wide, summary_details, config)
    rows = []
    for _, wide in engine.wide.iterrows():
        key = wide['sample_instance_id']
        details = {r['metric_name']: r for r in clean(engine.long[engine.long['sample_instance_id'] == key].to_dict('records'))}
        row = dict(_group_status(groups[key]),group_key=key, label=groups[key]['label'],
                   conditions=groups[key]['conditions'], condition_sources=groups[key].get('condition_sources', {}),
                   default_note=groups[key].get('default_note'),
                   before_mean=wide['before_thickness_sample'], after_mean=wide['after_thickness_sample'],
                   before_diff=wide['before_thickness_difference'], before_rel_percent=wide['before_thickness_relative_difference'],
                   after_diff=wide['after_thickness_difference'], after_rel_percent=wide['after_thickness_relative_difference'],
                   loss_percent=wide['loss_percent_sample'], loss_pp=wide['loss_percent_pp_difference'],
                   retention_percent=wide['retention_percent_sample'], retention_pp=wide['retention_percent_pp_difference'],
                   nk_total_abs_dev=wide['optical_constant_total_deviation'], mse_after_diff=wide['mse_after_difference'],
                   cv_before_diff=wide['thickness_cv_before_difference'], cv_after_diff=wide['thickness_cv_after_difference'],
                   optics={}, verdicts={}, verdict_reasons={})
        names = dict(thickness_before='before_thickness_mean', thickness_after='after_thickness_mean',
                     loss_percent='loss_percent', retention_percent='retention_percent',
                     mse_before='before_mse_mean', mse_after='after_mse_mean',
                     cv_before='before_thickness_cv', cv_after='after_thickness_cv')
        for parameter in (*OPTICAL_FIELDS, 'mse'):
            before_detail, after_detail = details[parameter + '_before_mean'], details[parameter + '_after_mean']
            row['optics'][parameter] = dict(
                before_diff=before_detail['absolute_difference'], before_rel_percent=before_detail['relative_difference_percent'],
                after_diff=after_detail['absolute_difference'], after_rel_percent=after_detail['relative_difference_percent'],
                drift_diff=details[parameter + '_absolute_change']['absolute_difference'])
            if parameter != 'mse':
                names[parameter + '_before'] = parameter + '_before_mean'
                names[parameter + '_after'] = parameter + '_after_mean'
        for public, metric in names.items():
            detail = details[metric]
            inapplicable = _inapplicable_reason(groups[key], reference, metric)
            if inapplicable:
                row['verdicts'][public] = 'not_applicable'
                row['verdict_reasons'][public] = inapplicable
                continue
            row['verdicts'][public] = _verdict(detail)
            missing_reasons = [text for field, text in (('reference_value', '参比没有该项数据'),
                                                       ('sample_value', '样品没有该项数据')) if detail[field] is None]
            if missing_reasons:
                row['verdict_reasons'][public] = '；'.join(missing_reasons)
                continue
            reasons = [detail.get('missing_reason'), detail.get('judgment_rule'), detail.get('comparison_class')]
            if detail.get('relative_difference_percent') is None and detail.get('reference_value') is not None and abs(detail['reference_value']) <= near_zero:
                reasons.append('参考值接近零，无法计算相对差')
            row['verdict_reasons'][public] = '；'.join(dict.fromkeys(str(reason) for reason in reasons if reason))
        row['verdict'], row['verdict_reason'] = _overall_verdict(row['verdicts'], row['verdict_reasons'])
        rows.append(row)
    require_after = any(groups[row['group_key']].get('after_stage') is not None for row in rows)
    warnings = [dict(reference_key=reference['group_key'], label=reference['label'],
                     message=f"参比 {reference['label']} 没有有效数据，无法比较，请换一个参比")] if not _has_reference_data(reference, require_after) else []
    return clean(dict(reference=_reference_payload(reference), rows=rows, summary=summary, warnings=warnings))


# ---- Per-candidate reference pairing (API_R4) -------------------------------------------

PAIR_FIELDS = (('spin_rpm', '旋涂转速', 'rpm'), ('bake_temp_c', '烘烤温度', '℃'), ('bake_time_s', '烘烤时间', 's'),
               ('treatment', '处理方式', ''), ('treatment_time', '处理时间', ''),
               ('post_bake_temp_c', '后烘温度', '℃'), ('post_bake_time_s', '后烘时间', 's'))
CHOICES_DDL = ('CREATE TABLE IF NOT EXISTS reference_choices (candidate_group_key TEXT NOT NULL, group_by_sig TEXT NOT NULL, '
               'reference_key TEXT NOT NULL, chosen_at TEXT NOT NULL, method TEXT NOT NULL DEFAULT \'manual\', '
               'PRIMARY KEY (candidate_group_key, group_by_sig))')
FIELD_LABELS = {field: (label, unit) for field, label, unit in PAIR_FIELDS}
FIELD_LABELS['variant'] = ('变体', '')
METHODS = ('manual', 'recommended')
SOURCE_LABELS = dict(auto='自动', nearest='自动', manual='手选', recommended='按推荐统一选择', single='手选')


def match_fields(store):
    """Which conditions must be equal for 'same conditions' (a setting that travels with the data file)."""
    fields = store._values.get('reference_match_fields')
    return [f for f in fields if f in FIELD_LABELS] if fields else [f for f, _, _ in PAIR_FIELDS]
FILTER_KEYS = ('series_id', 'group_by', 'formulation_keys', 'custom_group_ids', 'project_id')


def _blank(value):
    return None if value in (None, '') else value


def _condition_diffs(candidate, reference, fields=None):
    """The process conditions (by default the seven) that differ; empty equals empty."""
    result = []
    for field in fields or [f for f, _, _ in PAIR_FIELDS]:
        label, unit = FIELD_LABELS[field]
        left, right = _blank(candidate['conditions'].get(field)), _blank(reference['conditions'].get(field))
        if left != right:
            from .conditions import CONDITION_SOURCE_LABELS
            left_source = candidate.get('condition_sources', {}).get(field)
            right_source = reference.get('condition_sources', {}).get(field)
            result.append(dict(field=field, label=label, candidate=left, reference=right, unit=unit,
                               candidate_source=left_source, reference_source=right_source,
                               candidate_source_label=CONDITION_SOURCE_LABELS.get(left_source, left_source),
                               reference_source_label=CONDITION_SOURCE_LABELS.get(right_source, right_source)))
    return result


def _date_diff(candidate, reference):
    if not candidate.get('test_date') or not reference.get('test_date'):
        return None
    left, right = _date_key(candidate['test_date']), _date_key(reference['test_date'])
    if left == datetime.min or right == datetime.min:
        return None
    return (right.date() - left.date()).days  # positive = the reference was measured on a later day


def _date_text(days):
    return None if days is None else '同一天' if days == 0 else f'晚 {days} 天' if days > 0 else f'早 {-days} 天'


def _ref_info(group, how=None):
    info = dict(_group_status(group),reference_key=group['group_key'], label=group['label'], kind=_kind(group),
                test_date=group.get('test_date'), conditions=group['conditions'],
                condition_sources=group.get('condition_sources', {}), default_note=group.get('default_note'),
                has_valid_data=_has_reference_data(group, group.get('after_stage') is not None))
    if how:
        info['how'] = how
    return info


def _signature(store, body):
    group_by = body.get('group_by')
    group_by = group_by.split(',') if isinstance(group_by, str) and group_by else (group_by or store._values['default_group_by'])
    custom = body.get('custom_group_ids')
    custom = custom.split(',') if isinstance(custom, str) and custom else (custom or [])
    return ','.join(group_by) + ('|' + ','.join(sorted(custom)) if custom else '')


def _read_choices(store, signature):
    """Read only (never creates the table, so a read-only data file works): {candidate: (reference, at, method)}."""
    # 新库：参比选择由调用方作为 {group_by_sig: {candidate: (reference, at, method)}} 传进 ListStore。
    return dict(getattr(store, 'reference_choices', {}).get(signature, {}))


class _Scope:
    """Groups (and single samples) visible for one request; shared by pairing and compare."""

    def __init__(self, store, body):
        self.store, self.body = store, body
        self.stage = _stage(body)
        self.filters = {key: body[key] for key in FILTER_KEYS if body.get(key)}
        self.groups = _contexts(store, **self.filters)
        # A project (one test day) narrows the candidates only; references come from the whole library,
        # because the standard references were measured in other projects (e.g. REF01 on 7.28 and 9.20).
        pool_filters = {k: v for k, v in self.filters.items() if k != 'project_id'}
        self.pool = _contexts(store, **pool_filters) if pool_filters != self.filters else self.groups
        self.by_key = {g['group_key']: g for g in self.pool}
        self.by_key.update({g['group_key']: g for g in self.groups})
        self.fields = match_fields(store)
        self._singles = None

    def find(self, key):
        if key in self.by_key:
            return self.by_key[key]
        if isinstance(key, str) and key.startswith('sample:'):
            if self._singles is None:
                self._singles = {'sample:' + sid: items for sid, items in _sample_groups(self.store).items()}
            if key in self._singles:
                return next((g for g in self._singles[key] if g.get('after_stage') in (None, self.stage)), self._singles[key][0])
        raise KeyError('分组不存在或已没有可分析测量：' + str(key))

    def usable(self, group):
        return group.get('after_stage') in (None, self.stage)


def _suggestion(candidate, reference, fields=None):
    days = _date_diff(candidate, reference)
    diffs = _condition_diffs(candidate, reference, fields)
    return dict(_ref_info(reference), date_diff_days=days, date_diff_text=_date_text(days),
                same_conditions=not diffs, condition_diffs=diffs)


def _pair(scope, candidate, choices):
    standards = sorted((g for g in scope.pool if g.get('link_status') == 'reference' and scope.usable(g)
                        and (candidate.get('series_id') is None or g.get('series_id') == candidate['series_id'])),
                       key=lambda g: g['group_key'])
    earlier = [g for g in standards if (days := _date_diff(candidate, g)) is not None and days <= 0]
    suggestions = sorted((_suggestion(candidate, g, scope.fields) for g in earlier
                          if _has_reference_data(g, candidate.get('after_stage') is not None)),
                         # User rule A (2026-10-22): fewest differing conditions first, then nearest date.
                         key=lambda s: (len(s['condition_diffs']), abs(s['date_diff_days']),
                                        s['reference_key']))
    pair = dict(_group_status(candidate),candidate_group_key=candidate['group_key'], candidate_label=candidate['label'],
                test_date=candidate.get('test_date'), conditions=candidate['conditions'],
                condition_sources=candidate.get('condition_sources', {}), default_note=candidate.get('default_note'),
                status='needs_choice', reference_key=None, reference=None, suggestions=suggestions,
                no_earlier_reference=not earlier)
    chosen = choices.get(candidate['group_key'])
    if chosen:
        try:
            reference = scope.find(chosen[0])
        except KeyError:
            pair['choice_missing'] = chosen[0]  # the stored reference is gone; fall back to automatic pairing
        else:
            pair.update(status='chosen', reference_key=chosen[0], reference=_ref_info(reference), chosen_at=chosen[1],
                        method=chosen[2], source_label=SOURCE_LABELS.get(chosen[2], '手选'))
            return pair
    if not suggestions:
        pair['note'] = ('样品没有测量日期，无法按日期推荐' if _date_key(candidate.get('test_date')) == datetime.min
                        else '没有更早的参比' if not earlier else '更早的参比没有可比数据，请手动选择参比')
        return pair
    if candidate.get('test_date'):
        matches = [s for s in suggestions if s['same_conditions'] and s['date_diff_days'] == 0]
        if matches:
            first = min(matches, key=lambda s: s['reference_key'])
            pair.update(status='auto', reference_key=first['reference_key'],
                        reference={k: first[k] for k in ('reference_key', 'label', 'kind', 'test_date', 'conditions', 'condition_sources', 'default_note', *GROUP_STATUS_FIELDS)})
            pair['source_label'] = SOURCE_LABELS['auto']
            if len(matches) > 1:
                pair['alternatives'] = [m for m in matches if m is not first]
            return pair
    # No same-day match: take the closest earlier same-condition reference.
    near = next((s for s in suggestions if s['same_conditions'] and s['date_diff_days'] is not None), None)
    if near:
        pair.update(status='nearest', reference_key=near['reference_key'], date_diff_days=near['date_diff_days'],
                    date_diff_text=near['date_diff_text'], source_label=SOURCE_LABELS['nearest'],
                    note=f"非同天，差 {abs(near['date_diff_days'])} 天",
                    reference={k: near[k] for k in ('reference_key', 'label', 'kind', 'test_date', 'conditions', 'condition_sources', 'default_note', *GROUP_STATUS_FIELDS)})
    return pair


def pairing(store, body):
    scope = _Scope(store, body)
    keys = body.get('candidate_group_keys')
    if keys is None or keys == []:
        keys = [g['group_key'] for g in scope.groups if g.get('link_status') != 'reference' and scope.usable(g)]
    if not isinstance(keys, list):
        raise ValueError('candidate_group_keys 必须是列表')
    choices = _read_choices(store, _signature(store, body))
    pairs = [_pair(scope, scope.find(key), choices) for key in dict.fromkeys(keys)]
    result = dict(pairs=pairs)
    if body.get('recommended_controls') is not None:
        result.update(_controls(store, scope, body['recommended_controls'], pairs))
    return clean(result)


def _controls(store, scope, recommended, pairs):
    """Echo the caller's recommended control per test date; nothing is chosen or stored."""
    if not isinstance(recommended, dict):
        raise ValueError('recommended_controls 必须是 {测试日期: 样品编号} 对象')
    # Keys may also be project ids (one project = one test day); translate them to that project's date.
    dates = {}  # 新库没有"项目"，测试日直接用日期
    recommended = {dates.get(key) or key: value for key, value in recommended.items()}
    samples = store.samples()
    controls, used = [], set()
    for date in sorted({p['test_date'] for p in pairs if p['test_date']}):
        found = None
        if date in recommended:
            used.add(date)
            value = recommended[date]
            matches = [s for s in samples if value in (s['sample_id'], s['sample_code'])
                       and _date_diff({'test_date': date}, s) is not None
                       and _date_diff({'test_date': date}, s) <= 0]
            singles = _sample_groups(store) if matches else {}
            require_after = any(scope.find(p['candidate_group_key']).get('after_stage') is not None for p in pairs if p['test_date'] == date)
            matches = [s for s in matches if any(scope.usable(g) and _has_reference_data(g, require_after)
                                                for g in singles.get(s['sample_id'], []))]
            sample = next((s for s in matches if s['test_date'] == date), matches[0] if matches else None)
            if sample:
                group = next((g for g in scope.pool if sample['sample_id'] in (g.get('sample_ids') or ()) and scope.usable(g)), None)
                found = dict(sample_id=sample['sample_id'], sample_code=sample['sample_code'],
                             group_key=group['group_key'] if group else None, source='platform')
        controls.append(dict(test_date=date, recommended=found))
    return dict(controls=controls, controls_unmatched=[k for k in recommended if k not in used])


def _write_choices(store, keys, reference_key, signature, method='manual'):
    """Store (or clear) the choice of each candidate and write one change-log entry."""
    from .db import log, now
    with store.lock, store.conn:
        store.conn.execute(CHOICES_DDL)
        if 'method' not in {r[1] for r in store.conn.execute('PRAGMA table_info(reference_choices)')}:
            store.conn.execute("ALTER TABLE reference_choices ADD COLUMN method TEXT NOT NULL DEFAULT 'manual'")
        old = {}
        for key in keys:
            row = store.conn.execute('SELECT reference_key FROM reference_choices WHERE candidate_group_key=? AND group_by_sig=?',
                                     (key, signature)).fetchone()
            old[key] = row[0] if row else None
            if reference_key is None:
                store.conn.execute('DELETE FROM reference_choices WHERE candidate_group_key=? AND group_by_sig=?', (key, signature))
            else:
                store.conn.execute('INSERT INTO reference_choices (candidate_group_key,group_by_sig,reference_key,chosen_at,method) VALUES (?,?,?,?,?) '
                                   'ON CONFLICT(candidate_group_key,group_by_sig) DO UPDATE SET reference_key=excluded.reference_key,'
                                   'chosen_at=excluded.chosen_at,method=excluded.method',
                                   (key, signature, reference_key, now(), method))
        if len(keys) == 1:
            key = keys[0]
            if reference_key is None and old[key] is None:
                return
            log(store.conn, 'reference.clear' if reference_key is None else 'reference.choose', 'reference_choice', key + '|' + signature,
                before=dict(reference_key=old[key]) if old[key] else None,
                after=None if reference_key is None else dict(reference_key=reference_key, method=method))
        else:
            log(store.conn, 'reference.clear_bulk' if reference_key is None else 'reference.choose_bulk', 'reference_choice',
                'bulk|' + signature, before=dict(candidates=old),
                after=dict(reference_key=reference_key, method=method, candidates=list(keys)))


def _method(body):
    method = body.get('method', 'manual')
    if method not in METHODS:
        raise ValueError('method 必须是 manual 或 recommended')
    return method


def _check_reference(scope, reference_key):
    if reference_key is not None:
        if not isinstance(reference_key, str) or not reference_key:
            raise ValueError('reference_key 必须是分组 key、sample:<id> 或 null')
        scope.find(reference_key)


def _writable_candidates(store,scope,keys):
    from .project_access import samples_writable
    ids={sid for key in keys for sid in scope.find(key).get('sample_ids',[])}
    samples_writable(store,ids)


def put_choice(store, body):
    key, reference_key = body.get('candidate_group_key'), body.get('reference_key')
    if not isinstance(key, str) or not key:
        raise ValueError('candidate_group_key 必填')
    scope = _Scope(store, body)
    scope.find(key)
    _check_reference(scope, reference_key)
    _writable_candidates(store,scope,[key])
    _write_choices(store, [key], reference_key, _signature(store, body), _method(body))
    return pairing(store, dict(body, candidate_group_keys=[key]))['pairs'][0]


def put_choices_bulk(store, body):
    keys = body.get('candidate_group_keys')
    if not isinstance(keys, list) or not keys or not all(isinstance(k, str) and k for k in keys):
        raise ValueError('candidate_group_keys 必须是非空的分组 key 列表')
    keys = list(dict.fromkeys(keys))
    scope = _Scope(store, body)
    for key in keys:
        scope.find(key)
    _check_reference(scope, body.get('reference_key'))
    _writable_candidates(store,scope,keys)
    _write_choices(store, keys, body.get('reference_key'), _signature(store, body), _method(body))
    return clean(dict(pairs=pairing(store, dict(body, candidate_group_keys=keys))['pairs']))


def put_choices_batch(store,body):
    """Validate every candidate first; commit distinct choices with one audit row."""
    items=body.get('items')
    if not isinstance(items,list) or not items or not all(isinstance(item,dict) and isinstance(item.get('candidate_group_key'),str) and item['candidate_group_key'] and 'reference_key' in item for item in items):
        raise ValueError('items 必须为非空候选和参比列表')
    keys=[item['candidate_group_key'] for item in items]
    if len(keys)!=len(set(keys)):
        raise ValueError('候选编号不能重复')
    method=_method(dict(body,method=body.get('method','recommended')))
    scope=_Scope(store,body)
    for item in items:
        scope.find(item['candidate_group_key'])
        _check_reference(scope,item.get('reference_key'))
    _writable_candidates(store,scope,keys)
    signature=_signature(store,body)
    from .db import log,now
    with store.lock,store.conn:
        if not store.conn.in_transaction:
            store.conn.execute('BEGIN')
        store.conn.execute(CHOICES_DDL)
        if 'method' not in {r[1] for r in store.conn.execute('PRAGMA table_info(reference_choices)')}:
            store.conn.execute("ALTER TABLE reference_choices ADD COLUMN method TEXT NOT NULL DEFAULT 'manual'")
        old={}
        at=now()
        for item in items:
            key,reference=item['candidate_group_key'],item.get('reference_key')
            row=store.conn.execute('SELECT reference_key FROM reference_choices WHERE candidate_group_key=? AND group_by_sig=?',(key,signature)).fetchone()
            old[key]=row[0] if row else None
            if reference is None:
                store.conn.execute('DELETE FROM reference_choices WHERE candidate_group_key=? AND group_by_sig=?',(key,signature))
            else:
                store.conn.execute('INSERT INTO reference_choices(candidate_group_key,group_by_sig,reference_key,chosen_at,method) VALUES (?,?,?,?,?) '
                    'ON CONFLICT(candidate_group_key,group_by_sig) DO UPDATE SET reference_key=excluded.reference_key,chosen_at=excluded.chosen_at,method=excluded.method',
                    (key,signature,reference,at,method))
        log(store.conn,'reference.choose_batch','reference_choice','batch|'+signature,dict(candidates=old),dict(method=method,items=items))
    choices=_read_choices(store,signature)
    return clean(dict(pairs=[_pair(scope,scope.find(key),choices) for key in keys]))


def compare_pairs(store, body):
    """One call of the unchanged engine per distinct reference, then merge the rows."""
    pairs = body['pairs']
    if not isinstance(pairs, list) or not all(isinstance(p, dict) and p.get('candidate_group_key') for p in pairs):
        raise ValueError('pairs 必须是 [{candidate_group_key, reference_key}] 列表')
    assigned = {}
    for p in pairs:
        assigned[p['candidate_group_key']] = p.get('reference_key')
    given = {p['candidate_group_key']: p.get('how') for p in pairs}
    auto = {x['candidate_group_key']: x for x in pairing(store, dict(body, candidate_group_keys=list(assigned)))['pairs']}
    order = {key: index for index, key in enumerate(assigned)}
    batches = {}
    for candidate, reference in assigned.items():
        if reference:
            batches.setdefault(reference, []).append(candidate)
    rows, references, plain, parts, lines, sections, warnings = [], [], [], [], [], [], []
    for reference_key, candidates in batches.items():
        request = {k: v for k, v in body.items() if k != 'pairs'}
        request.update(reference_key=reference_key, candidate_group_keys=candidates)
        comparison = _comparison(store, request)
        payload = _build(store, comparison)
        warnings.extend(payload['warnings'])
        reference, groups, config, engine = comparison
        info = _ref_info(reference)
        for row in payload['rows']:
            if row['group_key'] not in order:
                continue  # the reference itself is not a candidate of this call
            suggested = auto[row['group_key']]
            how = given[row['group_key']] if given[row['group_key']] in ('auto', 'nearest', 'chosen', 'single') else (
                suggested['status'] if suggested['reference_key'] == reference_key else 'single')
            extra = dict(date_diff_days=suggested['date_diff_days'], note=f"非同天，差 {abs(suggested['date_diff_days'])} 天") if how == 'nearest' else {}
            if how == 'chosen':
                label = SOURCE_LABELS.get(suggested.get('method'), '手选') if suggested['reference_key'] == reference_key else '手选'
            else:
                label = SOURCE_LABELS[how]
            rows.append(dict(row, source_label=label, reference=dict(info, how=how, source_label=label, **extra)))
        plain.append(payload['reference'])
        references.append(dict(payload['reference'], kind=info['kind'], test_date=info['test_date'], conditions=info['conditions'],
                               condition_sources=info['condition_sources'], default_note=info['default_note']))
        parts.append(dict(reference=info, engine=engine, candidates=candidates, groups=groups, config=config))
        sections.append(dict(reference_key=reference_key, label=info['label'], lines=payload['summary']))
    rows.sort(key=lambda row: order[row['group_key']])
    if len(sections) > 1:
        for section in sections:
            lines += ['【参比：' + section['label'] + '】', *section['lines']]
    elif sections:
        lines = list(sections[0]['lines'])
    result = clean(dict(reference=plain[0] if len(plain) == 1 else None, references=references, rows=rows,
                        summary=lines, summary_lines=lines, summary_by_reference=sections,
                        warnings=warnings,
                        unpaired=[key for key, ref in assigned.items() if not ref]))
    return result, parts


def compare(store, body):
    if body.get('pairs'):
        return compare_pairs(store, body)[0]
    return _build(store, _comparison(store, body))
