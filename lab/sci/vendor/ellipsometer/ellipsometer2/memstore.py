"""椭偏 2.0 Store 的"传列表"版：只换了取数接口，公式和排序一行不动。

来源：<本机目录> ddbc78a4，2026-10-23）
  ellipsometer2/store.py   digest、normalized、seconds、sample_fields、block_fingerprint、api_stage（原样）
                           import_workbooks + _refresh_metadata 的解析部分 → read_workbooks()（去掉 SQLite、附件、项目、日志）
                           analysis、samples、measurements、groups_summary、_group_statistics、_manual_pending、_exclusions
                           → ListStore 的同名方法：self.conn.execute(...) 换成读构造时给的列表；去掉 pickle 缓存和视图缓存
  ellipsometer2/db.py      clean、dumps（原样）
  ellipsometer2/compact.py measurement_load（原样逻辑；这里 payload 已经是 dict，不再 json.loads）
  ellipsometer2/library.py SAMPLE_COLUMNS、known_row（原样，row 换成 dict）
  ellipsometer2/linking.py configured_series（规则从设置里的 series 读，不读 series_rules 表）
没有搬：锁、回收站、项目只读、自定义组的增删、剔除/忽略的写入（剔除和忽略作为参数从新库传进来）。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import unicodedata
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..src.analyzer import analyze_measurements, _series_stats
from ..src.config_manager import load_config
from ..src.parser import BASE_COLUMNS, NUMERIC_FIELDS, parse_workbook
from ..src.quality_checker import run_quality_checks
from ..src.sheet_detector import detect_workbook_sheets
from ..src.workbook_reader import load_workbook_bundle
from ..src.pipeline import _manual_mappings_for_bundle

CONFIG_FILE = Path(__file__).resolve().parents[1] / 'config' / 'config.yaml'
EXCLUSION_REASONS = ['拟合失败/MSE过高', '片子边缘', '污染或划伤', '算法标记的离群点', '录入错误', '其他']
CONDITIONS = ['spin_rpm', 'bake_temp_c', 'bake_time_s', 'treatment', 'treatment_time', 'post_bake_temp_c', 'post_bake_time_s', 'variant']
DEFAULT_GROUP = ['formulation', 'spin_rpm', 'bake_temp_c', 'bake_time_s', 'treatment', 'test_date', 'variant']
GROUP_KEYS = ['formulation', *CONDITIONS, 'test_date']
SAMPLE_COLUMNS = ('sample_id', 'sample_code', 'raw_name', 'series_id', 'test_date', 'spin_rpm', 'bake_temp_c', 'bake_time_s',
                  'treatment', 'treatment_time', 'variant', 'note', 'post_bake_temp_c', 'post_bake_time_s', 'field_sources', 'footnote',
                  'treatment_pending', 'project_id', 'deleted_at', 'sort_order', 'tags')


# ---- db.py ----
def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, 'item'):
        return clean(value.item())
    if isinstance(value, (datetime, Path)):
        return str(value)
    return value


def dumps(value):
    return json.dumps(clean(value), ensure_ascii=False, allow_nan=False, sort_keys=True)


# ---- compact.py ----
def measurement_load(payload):
    record = dict(json.loads(payload) if isinstance(payload, str) else payload)
    empty = record.pop('_empty', {})
    nulls = record.pop('_nulls', [])
    result = dict.fromkeys((*BASE_COLUMNS, *nulls))
    result.update(record)
    result.update(empty)
    return dict(sorted(result.items()))


# ---- store.py（原样） ----
def digest(value):
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def normalized(value):
    return unicodedata.normalize('NFKC', str(value or '')).replace('−', '-').replace('–', '-').strip()


def seconds(value):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(s|min|h)\s*', str(value or ''), re.I)
    return float(match[1]) * {'s': 1, 'min': 60, 'h': 3600}[match[2].lower()] if match else None


def sample_fields(row, rows):
    raw = row['sample_raw']
    text = normalized(raw)
    first = text.split('\n')[0].split('(')[0].strip()
    match = re.match(r'^[A-Za-z]+\d+(?:-[A-Za-z0-9]+)*', first)
    code = match[0].upper() if match else first
    # Chinese suffixes identify retained pilot samples, not process conditions.
    if '尾液留样' in first:
        code = first.replace(' ', '')
    process = normalized(row.get('process_condition'))
    # Exclude model declarations, whose rpm can differ from the actual process.
    condition_text = '\n'.join(line for line in text.splitlines()
                               if not re.search(r'模型|model', line, re.I) and 'SolvA' not in line.upper())
    rpm = re.search(r'(\d+(?:\.\d+)?)\s*rpm\b', condition_text, re.I)
    temp = re.search(r'(\d+(?:\.\d+)?)\s*(?:°C|℃)', process, re.I)
    # User 2026-10-21: a trailing -R means a re-test of the same sample.
    variant = None
    if code.endswith('-R'):
        code, variant = code[:-2], '重测'
    dilution = re.search(r'稀释\s*\d+\s*倍', text)
    if dilution:
        variant = dilution[0].replace(' ', '')
    treatment = 'SolvA' if ('SolvA' in text.upper() or any(
        str(r['treatment_stage']).startswith('after_solva_') for r in rows)) else None
    return dict(sample_id=row['sample_instance_id'], sample_code=code, raw_name=raw,
                series_id='unassigned', test_date=row.get('test_date'),
                spin_rpm=float(rpm[1]) if rpm else None,
                bake_temp_c=row.get('bake_temperature') if row.get('bake_temperature') is not None else (float(temp[1]) if temp else None),
                bake_time_s=seconds(row.get('bake_time')), treatment=treatment,
                treatment_time=row.get('solva_time'), variant=variant,
                note=row.get('sample_description'), payload=dumps(row))


def block_fingerprint(rows):
    first = rows[0]
    observations = [{k: r.get(k) for k in (*NUMERIC_FIELDS, 'treatment_stage',
                     'replicate_number', 'is_dissolved', 'dissolution_note')}
                    for r in rows if not r['is_source_average_row']]
    return digest({'sheet': first['source_sheet'], 'raw': normalized(first['sample_raw']),
                   'values': observations})


def api_stage(stage, single=False):
    if single:
        return 'single', '常规'
    if stage == 'before_solva':
        return 'before', '处理前'
    if stage.startswith('after_solva_'):
        number = stage.rsplit('_', 1)[1]
        return 'after_' + number, '处理后（SolvA）' if number == '1' else f'第 {number} 次处理后（SolvA）'
    return 'single', '常规'


def known_row(row, kind='sample'):
    result = {k: row[k] for k in SAMPLE_COLUMNS if k in row}
    if 'tags' in result and not isinstance(result['tags'], list):
        result['tags'] = json.loads(result['tags'] or '[]')
    return result


def configured_series(values, code):
    """linking.configured_series：按设置里各系列的 code_rules（正则）定系列。"""
    for series in values.get('series', []):
        for rule in series.get('code_rules', []):
            if re.search(rule['pattern'], code, re.I):
                return series['series_id']
    return 'unassigned'


def library_values(saved=None):
    """store._initialize_r2：库设置 = 程序默认 + 库里存的 r2_values；返回 (values, config)。"""
    from .settings import defaults, apply_to_config, backfill_quality_rules, V5_DEFAULT_CONDITIONS
    base = load_config(CONFIG_FILE)
    settings_defaults = defaults(base)
    values = {**settings_defaults, **(saved or {})}
    values['data_prefs'] = {**settings_defaults['data_prefs'], **values['data_prefs']}
    values['hierarchy'] = {**settings_defaults['hierarchy'], **values['hierarchy']}
    values['quality_rules'] = backfill_quality_rules(values['quality_rules'])
    if values['default_conditions'] == V5_DEFAULT_CONDITIONS:
        values['default_conditions'] = settings_defaults['default_conditions']
    return values, apply_to_config(base, values)


def read_workbooks(paths, values, own_series='unassigned', overrides=None):
    """import_workbooks + _refresh_metadata（不重读原件那一支），全部在内存里做。

    paths 按原来的导入顺序给（重复块保留先导入的那份）。返回
    dict(books, sheets, samples, measurements, duplicates, other_series)；样品和读数的 id 和旧库相同。
    """
    from .conditions import compose_sample, extract_sheet_footnote, parse_footnote
    from .treatments import annotate_rows
    _, config = library_values(values)
    overrides = overrides or {}
    enforce = own_series != 'unassigned'
    books, sheets, samples, measurements, fingerprints, duplicates, other = [], {}, {}, [], {}, [], []
    for source in paths:
        path = Path(source).resolve()
        content = path.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        bundle = load_workbook_bundle(path)
        try:
            detections = detect_workbook_sheets(bundle.formula_workbook, config, _manual_mappings_for_bundle(bundle, config.get('manual_mappings', {})))
            parsed = parse_workbook(bundle, detections, config)
            records = clean(parsed.measurements.astype(object).where(pd.notna(parsed.measurements), None).to_dict('records'))
            accepted = set()
            for iid in dict.fromkeys(r['sample_instance_id'] for r in records):
                rows = [r for r in records if r['sample_instance_id'] == iid]
                fields = sample_fields(rows[0], rows)
                series = configured_series(values, fields['sample_code'])
                if enforce and series != own_series:
                    other.append(dict(file_name=path.name, sample_id=iid, sample_code=fields['sample_code'], series_id=series))
                else:
                    accepted.add(iid)
            records = [r for r in records if r['sample_instance_id'] in accepted]
            if not records:
                continue
            wid = 'WB-' + sha[:20]
            books.append(dict(workbook_id=wid, file_name=path.name, sha256=sha, original_source='path', path=str(path), status='active'))
            sheet_ids = {}
            for detection in detections:
                sid = 'SH-' + digest([sha, detection.sheet_name])[:20]
                sheet_ids[detection.sheet_name] = sid
                note, defaults = extract_sheet_footnote(bundle.formula_workbook[detection.sheet_name], values['treatments'])
                sheets[sid] = dict(sheet_id=sid, workbook_id=wid, name=detection.sheet_name, payload=asdict(detection),
                                   field_mapping=asdict(detection).get('field_mapping', {}), footnote=note, footnote_defaults=defaults)
            blocks = {}
            for record in records:
                blocks.setdefault(record['sample_instance_id'], []).append(record)
            for iid, rows in blocks.items():
                fp = block_fingerprint(rows)
                if fp in fingerprints and fingerprints[fp]['workbook_id'] != wid:
                    canonical = fingerprints[fp]['sample_id']
                    duplicates.append(dict(workbook_id=wid, original_sample_id=iid, canonical_sample_id=canonical,
                                           file_name=path.name, sheet=rows[0]['source_sheet'], sample_code=rows[0]['sample_id']))
                    continue
                sample = sample_fields(rows[0], rows)
                sample['series_id'] = configured_series(values, sample['sample_code'])
                samples[iid] = sample
                fingerprints.setdefault(fp, dict(sample_id=iid, workbook_id=wid))
                for row in rows:
                    mid = 'M-' + digest([sha, row['source_sheet'], row['source_row']])[:24]
                    measurements.append(dict(measurement_id=mid, sample_id=iid, workbook_id=wid, sheet_id=sheet_ids[row['source_sheet']],
                                             source_row=row['source_row'], is_average=int(bool(row['is_source_average_row'])), payload=row, active=1))
        finally:
            bundle.formula_workbook.close()
            bundle.value_workbook.close()
    # _refresh_metadata
    for sheet in sheets.values():
        defaults = {k: v for k, v in (sheet['footnote_defaults'] or {}).items() if k not in CONDITIONS}
        defaults.update(parse_footnote(sheet['footnote'], values['treatments']))
        sheet['footnote_defaults'] = defaults
    order = {b['workbook_id']: index for index, b in enumerate(books)}
    measurements.sort(key=lambda m: (m['workbook_id'], m['sheet_id'], m['source_row']))
    annotated = annotate_rows([measurement_load(m['payload']) for m in measurements], config)
    by_sample = {}
    for stored, record in zip(measurements, annotated):
        override = overrides.get(stored['sample_id']) or {}
        if override.get('sample_code'):
            record['sample_id'] = override['sample_code']
        stored['payload'] = clean(record)
        by_sample.setdefault(stored['sample_id'], []).append(record)
    for sample_id, sample in samples.items():
        records = by_sample.get(sample_id, [])
        sheet = sheets[next(m['sheet_id'] for m in measurements if m['sample_id'] == sample_id)]
        override = overrides.get(sample_id) or {}
        base = sample_fields(json.loads(sample['payload']), records)
        enriched = compose_sample(base, records, sheet['footnote_defaults'] or {}, override, values['treatments'])
        sample.update({k: enriched.get(k) for k in CONDITIONS})
        sample['test_date'] = enriched.get('test_date')
        sample.update(field_sources=enriched['field_sources'], footnote=sheet['footnote'],
                      treatment_pending=int(enriched['treatment_pending']), note=enriched['note'])
        sample['stages'] = sorted({api_stage(r['treatment_stage'])[0] for r in records})
    return dict(books=sorted(books, key=lambda b: order[b['workbook_id']]), sheets=sheets, samples=samples,
                measurements=measurements, duplicates=duplicates, other_series=other)


class ListStore:
    """原 Store 的读接口。输入全是普通列表/字典：

    samples       [{sample_id, sample_code, raw_name, series_id, test_date, 8 项条件, field_sources, footnote,
                    treatment_pending, note, payload(第一行原始解析行, JSON 文本), project_id}]
    measurements  [{measurement_id, sample_id, workbook_id, sheet_id, source_row, is_average, payload(解析行 dict)}]
    books         [{workbook_id, file_name, sha256, original_source, path}]
    sheets        {sheet_id: {name, field_mapping}}
    links         {sample_id: {status, formulation_key, formulation_code, product_name, source, reason}}
    exclusions    {measurement_id: {scope: all|thickness|fields, fields: [...], reason, note, at, exclusion_id}}
    dismissals    {flag_id, ...}；reference_choices {group_by_sig: {candidate_key: (reference_key, at, method)}}
    """

    def __init__(self, *, samples, measurements, books, sheets=None, links=None, values=None, exclusions=None,
                 dismissals=(), reference_choices=None, custom_groups=None):
        self._values, self.config = library_values(values)
        self.lock = threading.RLock()
        self._views = {}
        self._samples = [dict(s) for s in samples]
        self._rows = sorted((dict(m) for m in measurements), key=lambda m: (m['workbook_id'], m['sheet_id'], m['source_row']))
        self.books = {b['workbook_id']: dict(b) for b in books}
        self.sheets = sheets or {}
        self.links = links or {}
        self._excluded = {}
        for mid, item in (exclusions or {}).items():
            item = dict(item, measurement_id=mid)
            item.setdefault('fields', [])
            item.setdefault('exclusions', [dict(item)])
            self._excluded[mid] = item
        self.dismissals = set(dismissals)
        self.reference_choices = reference_choices or {}
        self._custom = custom_groups or []
        self._cache = None

    # ----- 取数（原来读 SQLite 的地方） -----
    def settings(self):
        return dict(values=clean(self._values))

    def _find_original(self, book):
        return Path(book['path']) if book.get('path') else None

    def _raw_rows(self):
        return [dict(r) for r in self._rows if r.get('active', 1)]

    def _exclusions(self):
        return deepcopy(self._excluded)

    def _manual_pending(self):
        from .treatments import identify_treatment
        result = {}
        for row in self._samples:
            if not row.get('treatment_pending'):
                continue
            sources = row['field_sources'] if isinstance(row.get('field_sources'), dict) else json.loads(row.get('field_sources') or '{}')
            if (sources.get('treatment') == 'manual' and row['treatment']
                    and identify_treatment(row['treatment'], self._values['treatments']) is None):
                result[row['sample_id']] = row['treatment']
        return result

    def read_view(self, name, parameters, build):
        """同一份数据上算过的视图（全库分组统计、参比上下文、组光学……）记下来，再要时给副本。
        原程序在数据库版里就是这样按数据版本缓存的；这里数据一变，平台会整份换一个新 store。"""
        key = (name, json.dumps(parameters, sort_keys=True, default=str))
        with self.lock:
            if key not in self._views:
                self._views[key] = build()
            return deepcopy(self._views[key])

    def _group_statistics(self, values):
        return _series_stats(pd.Series(values, dtype=float))

    def custom_groups(self, include_deleted=False):
        return [g for g in self._custom if include_deleted or not g.get('deleted_at')]

    # ----- 原样搬来的计算 -----
    def analysis(self):
        if self._cache is not None:
            return self._cache
        stored = self._raw_rows()
        excluded = self._exclusions()
        blocked = self._manual_pending()
        data = []
        for row in stored:
            record = measurement_load(row['payload'])
            if record.get('treatment_pending') or row['sample_id'] in blocked:
                continue
            exclusion = excluded.get(row['measurement_id'])
            if exclusion and exclusion['scope'] == 'all':
                continue
            if exclusion and exclusion['scope'] == 'thickness':
                record['thickness'] = None
            elif exclusion and exclusion['scope'] == 'fields':
                for field in exclusion['fields']:
                    record[field] = None
            record['_measurement_id'] = row['measurement_id']
            data.append(record)
        frame = pd.DataFrame(data) if data else pd.DataFrame(columns=[*BASE_COLUMNS, '_measurement_id'])
        for field in NUMERIC_FIELDS:
            frame[field] = pd.to_numeric(frame[field], errors='coerce')
        quality = run_quality_checks(frame, self.config)
        analysis = analyze_measurements(quality.measurements, quality.issues, self.config)
        self._cache = (quality, analysis)
        return self._cache

    def _sample_value(self, row):
        from .conditions import apply_library_defaults
        item = known_row(row, 'sample')
        item['field_sources'] = item['field_sources'] if isinstance(item.get('field_sources'), dict) else json.loads(item.get('field_sources') or '{}')
        return apply_library_defaults(item, self._values)

    def samples(self, series_id='', **filters):
        active_ids = {r['sample_id'] for r in self._raw_rows()}
        _, analysis = self.analysis()
        single = set()
        if not analysis.thickness_comparison.empty:
            single = set(analysis.thickness_comparison.loc[analysis.thickness_comparison['data_completeness_status'] == '常规单阶段', 'sample_instance_id'])
        result = []
        for row in sorted(self._samples, key=lambda r: (r.get('sort_order') is None, r.get('sort_order') or 0)):
            if row['sample_id'] not in active_ids or (series_id and row['series_id'] != series_id):
                continue
            if filters.get('project_id') and row.get('project_id') != filters['project_id']:
                continue
            if filters.get('test_date') and row['test_date'] != filters['test_date']:
                continue
            item = self._sample_value(row)
            item['treatment_pending'] = bool(item['treatment_pending'])
            item['model'] = measurement_load(row['payload']).get('model')
            link = self.links.get(row['sample_id'])
            item['link'] = {k: link.get(k) for k in ('status', 'formulation_key', 'formulation_code', 'product_name', 'source', 'reason')} if link else dict(status='pending', formulation_key=None, formulation_code=None, product_name=None, source=None, reason=None)
            item['measurement_count'] = sum(1 for m in self._rows if m['sample_id'] == row['sample_id'] and not m['is_average'] and m.get('active', 1))
            stages = sorted({api_stage(measurement_load(m['payload'])['treatment_stage'])[0] for m in self._rows if m['sample_id'] == row['sample_id']}, reverse=True)
            item['stages'] = ['single'] if row['sample_id'] in single and not item['treatment_pending'] else stages
            result.append(item)
        return result

    def measurements(self, series_id='', formulation_key='', sample_id='', stage='', include_excluded='1', **filters):
        quality, _ = self.analysis()
        suggested = {r['_measurement_id']: bool(r['is_thickness_outlier']) for r in quality.measurements.to_dict('records')}
        samples = {s['sample_id']: s for s in self.samples(series_id=series_id, **filters)}
        excluded = self._exclusions()
        blocked = self._manual_pending()
        result = []
        for row in self._raw_rows():
            if row['is_average']:
                continue
            sample = samples.get(row['sample_id'])
            if not sample or (sample_id and row['sample_id'] != sample_id) or (formulation_key and sample['link']['formulation_key'] != formulation_key):
                continue
            record = measurement_load(row['payload'])
            s, label = api_stage(record['treatment_stage'], sample['stages'] == ['single'])
            treatment_name = sample['treatment']
            if str(record['treatment_stage']).startswith('after_'):
                from .treatments import stage_label
                s = api_stage(record['treatment_stage'])[0]
                if sample['field_sources'].get('treatment') != 'manual' or record.get('treatment_pending'):
                    treatment_name = record.get('treatment_name') or treatment_name
                label = stage_label(record['treatment_stage'], blocked.get(row['sample_id']) or treatment_name, record.get('treatment_pending', False) or row['sample_id'] in blocked)
            if stage and stage != s:
                continue
            exclusion = excluded.get(row['measurement_id'])
            if exclusion and str(include_excluded) == '0':
                continue
            values = {k: record.get(k) for k in NUMERIC_FIELDS}
            effective = dict(values)
            if exclusion and exclusion['scope'] == 'all':
                effective = dict.fromkeys(NUMERIC_FIELDS)
            elif exclusion and exclusion['scope'] == 'thickness':
                effective['thickness'] = None
            elif exclusion and exclusion['scope'] == 'fields':
                effective.update(dict.fromkeys(exclusion['fields']))
            result.append(dict(measurement_id=row['measurement_id'], workbook=record['source_file'], sheet=record['source_sheet'], row=record['source_row'],
                sample_id=row['sample_id'], sample_code=sample['sample_code'], formulation_key=sample['link']['formulation_key'], formulation_code=sample['link']['formulation_code'],
                series_id=sample['series_id'], stage=s, stage_label=label, replicate=record['replicate_number'],
                treatment_pending=bool(record.get('treatment_pending') or row['sample_id'] in blocked), field_sources=sample['field_sources'], footnote=sample['footnote'],
                condition_sources=sample['condition_sources'], default_note=sample['default_note'],
                **values, effective_values=effective, **{k: treatment_name if k == 'treatment' else sample[k] for k in CONDITIONS},
                dissolved=bool(record['is_dissolved']), dissolution_note=record['dissolution_note'], suggested_outlier=suggested.get(row['measurement_id'], False),
                excluded={k: exclusion.get(k) for k in ('exclusion_id', 'scope', 'fields', 'reason', 'note', 'at')} if exclusion else None,
                exclusions=exclusion['exclusions'] if exclusion else []))
        if filters.get('limit'):
            result = result[int(filters.get('offset', 0)):int(filters.get('offset', 0)) + int(filters['limit'])]
        return clean(result)

    def groups_summary(self, series_id='', group_by='', formulation_keys='', **filters):
        group_by = group_by.split(',') if isinstance(group_by, str) and group_by else (group_by or self._values['default_group_by'].copy())
        if not group_by or any(k not in GROUP_KEYS for k in group_by) or len(set(group_by)) != len(group_by):
            raise ValueError('group_by 包含未知字段或重复字段')
        selected = set(formulation_keys.split(',')) if formulation_keys else None
        samples = {s['sample_id']: s for s in self.samples(series_id=series_id, **filters)
                   if not selected or s['link']['formulation_key'] in selected}
        recorded = {r['sample_id']: dict(r) for r in self._samples}
        measurements = [m for m in self.measurements(series_id=series_id) if m['sample_id'] in samples]
        blocked = self._manual_pending()
        _, analysis = self.analysis()
        comps = clean(analysis.thickness_comparison.to_dict('records'))
        optics = clean(analysis.optical_changes.to_dict('records'))
        # The old core defines the first post-treatment stage for the default view.
        # Additional post-treatment stages remain separate groups instead of being averaged together.
        buckets, sample_rows, memberships = {}, [], {}
        custom_ids = filters.get('custom_group_ids', '')
        custom_ids = custom_ids.split(',') if isinstance(custom_ids, str) and custom_ids else (custom_ids or [])
        custom = {g['custom_group_id']: g for g in self.custom_groups() if g['custom_group_id'] in custom_ids}
        if set(custom_ids) - set(custom):
            raise ValueError('自定义分组不存在或已移到回收站')

        def excludes_thickness(m):
            exclusion = m['excluded']
            return bool(exclusion) and (exclusion['scope'] in ('all', 'thickness') or
                exclusion['scope'] == 'fields' and 'thickness' in exclusion['fields'])

        for row in comps:
            sid = row['sample_instance_id']
            if sid not in samples:
                continue
            sample = dict(samples[sid])
            identity_sample = dict(recorded[sid])
            if sid in blocked:
                continue
            if sample['treatment_pending'] and row.get('after_stage') is None:
                continue
            stage_records = [measurement_load(m['payload']) for m in self._rows if m['sample_id'] == sid and m.get('active', 1)]
            stage_records = [r for r in stage_records if r['treatment_stage'] == row.get('after_stage') and not r.get('treatment_pending')]
            if stage_records and sample['field_sources'].get('treatment') != 'manual':
                sample['treatment'] = stage_records[0].get('treatment_name') or sample['treatment']
                identity_sample['treatment'] = stage_records[0].get('treatment_name') or identity_sample['treatment']
            link = sample['link']
            formula = link['formulation_key'] or ('sample:' + sample['sample_code'])
            stage = row.get('after_stage')
            identity = [(k, formula if k == 'formulation' else identity_sample[k]) for k in group_by]
            if filters.get('per_sample'):  # reference.py: a single sample as its own group
                identity.append(('sample', sample['sample_id']))
            key = 'G-' + digest([identity, stage])[:24]
            buckets.setdefault(key, []).append((sample, row))
            for cid, cg in custom.items():
                wanted_stage = filters.get('after_stage', 'after_1')
                if re.fullmatch(r'after_\d+', wanted_stage):
                    wanted_stage = wanted_stage.replace('after_', 'after_solva_')
                if sid in cg['sample_ids'] and (stage == wanted_stage or stage is None):
                    ck = 'C-' + cid
                    buckets.setdefault(ck, []).append((sample, row))
                    memberships.setdefault(sid, []).append((ck, stage))
                    sample_rows.append(dict(sample_id=sid, group_key=ck, sample_code=sample['sample_code'],
                        test_date=sample['test_date'], before_mean=row['before_thickness_mean'],
                        after_mean=row['after_thickness_mean'], loss_percent=row['loss_percent'],
                        retention_percent=row['retention_percent'], dissolved=row['after_dissolved'], after_stage=stage))
            memberships.setdefault(sid, []).append((key, stage))
            sample_rows.append(dict(sample_id=sid, group_key=key, sample_code=sample['sample_code'], test_date=sample['test_date'],
                before_mean=row['before_thickness_mean'], after_mean=row['after_thickness_mean'], loss_percent=row['loss_percent'],
                retention_percent=row['retention_percent'], dissolved=row['after_dissolved'],
                after_stage=stage, before_sd=row['before_thickness_std'], after_sd=row['after_thickness_std'],
                before_cv=row['before_thickness_cv'], after_cv=row['after_thickness_cv'], n_before=row['before_thickness_count'], n_after=row['after_thickness_count'],
                loss=row['thickness_loss'], completeness_status=row['data_completeness_status']))
        for sample_row in sample_rows:
            notes = list(dict.fromkeys(m['dissolution_note'] for m in measurements
                if m['sample_id'] == sample_row['sample_id'] and m['stage'] == api_stage(sample_row['after_stage'] or '')[0]
                and m['dissolved'] and m['dissolution_note'] and not (m['excluded'] and m['excluded']['scope'] == 'all')))
            sample_row['dissolution_note'] = '；'.join(notes) if sample_row['dissolved'] and notes else None

        def mean(values):
            return self._group_statistics(values)['mean']
        groups = []
        for key, entries in buckets.items():
            first = entries[0][0]
            rows = [e[1] for e in entries]
            ids = {e[0]['sample_id'] for e in entries}
            is_custom = key.startswith('C-')
            stage = next((r.get('after_stage') for r in rows if r.get('after_stage')), None) if is_custom else rows[0].get('after_stage')
            after_api = api_stage(stage)[0] if stage and str(stage).startswith('after_solva_') else None
            member_measurements = [m for m in measurements if m['sample_id'] in ids]
            points = [m for m in member_measurements if not excludes_thickness(m)]
            condition_sets = {k: {e[0][k] for e in entries} for k in CONDITIONS}
            conditions = {k: first[k] if len(condition_sets[k]) == 1 else None for k in CONDITIONS}
            mixed = [k for k in CONDITIONS if len(condition_sets[k]) > 1]
            condition_sources = {}
            for field in first['condition_sources']:
                sources = {e[0]['condition_sources'][field] for e in entries}
                condition_sources[field] = next(iter(sources)) if len(sources) == 1 else 'mixed'
            default_notes = list(dict.fromkeys(e[0]['default_note'] for e in entries if e[0]['default_note']))
            before = self._group_statistics([r['before_thickness_mean'] for r in rows])
            after = self._group_statistics([r['after_thickness_mean'] for r in rows])
            formulas = {e[0]['link']['formulation_key'] or 'sample:' + e[0]['sample_code'] for e in entries}
            statuses = {e[0]['link']['status'] for e in entries}
            models = {e[0].get('model') for e in entries}
            label = (first['link']['formulation_code'] or first['sample_code']) if len(formulas) == 1 else '多配方'
            for k, unit in [('spin_rpm', ' rpm'), ('bake_temp_c', '℃'), ('bake_time_s', ' s'),
                            ('post_bake_temp_c', '℃'), ('post_bake_time_s', ' s')]:
                if k in group_by and conditions[k] is not None:
                    label += f' · {conditions[k]:g}{unit}'
                    if condition_sources.get(k) == 'library_default':
                        label += '（默认）'
            if 'treatment' in group_by and conditions['treatment']:
                label += ' · ' + conditions['treatment']
                if condition_sources.get('treatment') == 'library_default':
                    label += '（默认）'
            if 'treatment_time' in group_by and conditions['treatment_time'] is not None:
                label += ' · ' + str(conditions['treatment_time'])
                if condition_sources.get('treatment_time') == 'library_default':
                    label += '（默认）'
            if 'variant' in group_by and conditions['variant']:
                label += ' · ' + conditions['variant']
            if 'test_date' in group_by and len({e[0]['test_date'] for e in entries}) == 1 and first['test_date']:
                label += ' · ' + str(first['test_date'])
            if stage and stage != 'after_solva_1':
                label += ' · ' + api_stage(stage)[1]
            from .reference import group_optics
            group_optical = self.read_view('group_optics', dict(sample_ids=sorted(ids), stage=stage),
                lambda: group_optics(optics, ids, stage, member_measurements, statistics=self._group_statistics))
            if is_custom:
                label = custom[key[2:]]['name']
            dissolved_ids = {e[0]['sample_id'] for e in entries if e[1]['after_dissolved']}
            after_ids = {e[0]['sample_id'] for e in entries if e[1]['after_dissolved'] or pd.notna(e[1]['after_thickness_mean'])}
            dissolution_notes = list(dict.fromkeys(m['dissolution_note'] for m in member_measurements
                if m['sample_id'] in dissolved_ids and m['stage'] == after_api and m['dissolved'] and m['dissolution_note']
                and not (m['excluded'] and m['excluded']['scope'] == 'all')))[:3]
            groups.append(dict(group_key=key, label=label, formulation_key=first['link']['formulation_key'] if len(formulas) == 1 else None,
                formulation_code=(first['link']['formulation_code'] or first['sample_code']) if len(formulas) == 1 else None,
                conditions=conditions, mixed_conditions=mixed, condition_sources=condition_sources, default_note='；'.join(default_notes) or None,
                n_samples=len(ids), n_before=sum(r['before_thickness_count'] for r in rows), n_after=sum(r['after_thickness_count'] for r in rows),
                before_mean=before['mean'], before_sd=before['std'], before_cv=before['cv'], after_mean=after['mean'], after_sd=after['std'], after_cv=after['cv'],
                loss=mean([r['thickness_loss'] for r in rows]), loss_percent=mean([r['loss_percent'] for r in rows]), retention_percent=mean([r['retention_percent'] for r in rows]),
                loss_percent_excl_dissolved=mean([r['loss_percent'] for r in rows if not r['after_dissolved']]), dissolved_count=len(dissolved_ids),
                after_count=len(after_ids), dissolution_notes=dissolution_notes,
                pooled_before_mean=mean([m['thickness'] for m in points if m['stage'] in ('before', 'single')]),
                pooled_before_sd=self._group_statistics([m['thickness'] for m in points if m['stage'] in ('before', 'single')])['std'],
                pooled_after_sd=self._group_statistics([m['thickness'] for m in points if m['stage'] == after_api])['std'],
                pooled_after_mean=mean([m['thickness'] for m in points if m['stage'] == after_api]), optics=group_optical, after_stage=stage,
                series_id=first['series_id'] if len({e[0]['series_id'] for e in entries}) == 1 else None,
                test_date=first['test_date'] if len({e[0]['test_date'] for e in entries}) == 1 else None,
                link_status=next(iter(statuses)) if len(statuses) == 1 else 'mixed',
                sample_ids=sorted(ids), custom=is_custom, model=next(iter(models)) if len(models) == 1 else None))
        point_rows = []
        for m in measurements:
            for key, stage in memberships.get(m['sample_id'], []):
                if m['stage'] not in ('before', 'single') and m['stage'] != api_stage(stage or '')[0]:
                    continue
                point_rows.append(dict(measurement_id=m['measurement_id'], group_key=key, sample_id=m['sample_id'], stage=m['stage'], stage_label=m['stage_label'],
                    thickness=m['thickness'], excluded=excludes_thickness(m), suggested_outlier=m['suggested_outlier']))
        return clean(dict(group_by=group_by, groups=groups, samples=sample_rows, points=point_rows))
