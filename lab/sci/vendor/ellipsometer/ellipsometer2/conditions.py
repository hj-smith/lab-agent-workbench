"""Process metadata with explicit name, sheet-footnote and manual provenance.

This module deliberately does not alter measurement values or legacy analysis.
Only units and unambiguous conditions written in the source are interpreted.
"""
from __future__ import annotations

import math
import json
import re
import unicodedata
from typing import Any, Mapping

from .treatments import DEFAULT_TREATMENTS, identify_treatment, normalize_alias

CONDITION_FIELDS = (
    'spin_rpm', 'bake_temp_c', 'bake_time_s', 'treatment', 'treatment_time',
    'post_bake_temp_c', 'post_bake_time_s', 'variant',
)
NUMERIC_CONDITIONS = {'spin_rpm', 'bake_temp_c', 'bake_time_s',
                      'post_bake_temp_c', 'post_bake_time_s'}
CONDITION_SOURCE_LABELS = {None: '未记录', 'manual': '手改', 'name': '样品名',
                           'footnote': '表脚注', 'library_default': '默认（用户确认）', 'mixed': '混合来源'}


def default_condition_rows(settings, series_id):
    """Specific rows reserve fields; overlapping wildcard groups stay unused."""
    rows = settings['default_conditions']['rows']
    def fields(row):
        return frozenset(k for k, v in row.get('values', {}).items() if v not in (None, ''))
    specific = {field for row in rows if row['series_id'] == series_id for field in fields(row)}
    selected = [row for row in rows if row['series_id'] == series_id or
                (row['series_id'] == '*' and not fields(row) & specific)]
    # Stable traversal also keeps overlapping rows at the same scope and notes
    # independent of their position in the editor.
    return sorted(selected, key=lambda row: (
        tuple(CONDITION_FIELDS.index(field) for field in CONDITION_FIELDS if field in fields(row)),
        json.dumps(row, sort_keys=True, ensure_ascii=False)))


def apply_library_defaults(sample, settings):
    """Overlay an entirely unrecorded condition set on a detached read value."""
    item = dict(sample)
    sources = dict(item.get('field_sources') or {})
    notes = []
    item['default_condition_rows'] = []
    for row in default_condition_rows(settings, item.get('series_id')):
        values = {k: v for k, v in row.get('values', {}).items() if v not in (None, '')}
        if values and all(sample.get(field) in (None, '') and
                          (sample.get('field_sources') or {}).get(field) not in
                          {'name', 'footnote', 'manual'} for field in values):
            for field, value in values.items():
                item[field], sources[field] = value, 'library_default'
            item['default_condition_rows'].append(dict(series_id=row['series_id'], values=dict(values),
                label=row.get('label'), note=row.get('note')))
            note = row.get('note') or row.get('label') or (row['series_id'] + '：' +
                '、'.join(f'{field}={value}' for field, value in sorted(values.items())))
            if note not in notes:
                notes.append(note)
    item['default_note'] = '；'.join(notes) or None
    item['field_sources'] = sources
    item['condition_sources'] = {field: sources.get(field) for field in CONDITION_FIELDS if field != 'variant'}
    return item


_NUMBER = r'(\d+(?:\.\d+)?)'
_TEMP = re.compile(_NUMBER + r'\s*(?:°\s*C|℃)', re.I)
_TIME = re.compile(_NUMBER + r'\s*(s|min|h|秒|分钟|小时)\b', re.I)
_RPM = re.compile(_NUMBER + r'\s*rpm\b', re.I)


_FILE_DATE = re.compile(r'(?<!\d)(20\d{2})\s*[.\-_/年]\s*(\d{1,2})\s*[.\-_/月]\s*(\d{1,2})(?!\d)')
_FILE_DATE_COMPACT = re.compile(r'(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)')


def date_from_filename(name: Any) -> str | None:
    """Full dates only (2026.9.20, 2026-09-20, 2026年9月20日, 20260920); a month alone is not a date."""
    import datetime as _dt
    stem = normalize_text(name).rsplit('.', 1)[0] if '.xls' in normalize_text(name).lower() else normalize_text(name)
    for pattern in (_FILE_DATE, _FILE_DATE_COMPACT):
        found = [m for m in pattern.finditer(stem)]
        for match in found:
            try:
                return _dt.date(int(match[1]), int(match[2]), int(match[3])).isoformat()
            except ValueError:
                continue
    return None


def normalize_text(value: Any) -> str:
    return unicodedata.normalize('NFKC', str(value or '')).replace('−', '-').replace('–', '-')


def seconds(value: Any) -> float | None:
    match = _TIME.fullmatch(normalize_text(value).strip())
    if not match:
        return None
    return float(match[1]) * {'s': 1, '秒': 1, 'min': 60, '分钟': 60,
                             'h': 3600, '小时': 3600}[match[2].lower()]


def _unique(matches, convert=float):
    values = list(dict.fromkeys(convert(match[1]) for match in matches))
    return values[0] if len(values) == 1 else None


def _treatment_match(text: str, treatments=None):
    """Find a named treatment, bounded by its following process wording."""
    for item in treatments if treatments is not None else DEFAULT_TREATMENTS:
        for alias in sorted([item['name'], *item.get('aliases', [])], key=len, reverse=True):
            pattern = r'\s*'.join(re.escape(char) for char in re.sub(r'\s+', '', alias))
            match = re.search(pattern + r'(?=\s*(?:浸泡|浸没|表面|冲洗|旋涂|处理|\d|后|[,;:()\s]|$))', text, re.I)
            if match:
                return item['name'], match
    return None, None


def _parse_primary(text: str) -> dict:
    result = {}
    rpm = _unique(_RPM.finditer(text))
    temperature = _unique(_TEMP.finditer(text))
    times = list(_TIME.finditer(text))
    duration = None
    durations = list(dict.fromkeys(seconds(match[0]) for match in times))
    if len(durations) == 1:
        duration = durations[0]
    if rpm is not None:
        result['spin_rpm'] = rpm
    if temperature is not None:
        result['bake_temp_c'] = temperature
    if duration is not None:
        result['bake_time_s'] = duration
    return result


def _parse_secondary(text: str, treatments=None) -> dict:
    result = {}
    name, match = _treatment_match(text, treatments)
    after_treatment = text
    if match:
        result['treatment'] = name
        after_treatment = text[match.end():]
        # Treatment time precedes the next comma or bake temperature. A later
        # 60s beside 250℃ is a bake duration, not a second treatment duration.
        treatment_part = re.split(r'[,;。]|\d+(?:\.\d+)?\s*(?:°\s*C|℃)', after_treatment, maxsplit=1)[0]
        times = list(_TIME.finditer(treatment_part))
        if len(times) == 1:
            result['treatment_time'] = f'{times[0][1]}{times[0][2].lower()}'
            after_treatment = after_treatment[times[0].end():]
    temperature = _unique(_TEMP.finditer(after_treatment))
    if temperature is not None:
        result['post_bake_temp_c'] = temperature
        # A duration is secondary bake only if a temperature or explicit bake
        # wording occurs. “旋干烘干” alone supplies no numeric condition.
        temps = list(_TEMP.finditer(after_treatment))
        suffix = after_treatment[temps[-1].end():]
        times = list(_TIME.finditer(suffix))
        if len(times) == 1:
            result['post_bake_time_s'] = seconds(times[0][0])
    elif re.search(r'烘|焙|bake', after_treatment, re.I):
        times = list(_TIME.finditer(after_treatment))
        if len(times) == 1:
            result['post_bake_time_s'] = seconds(times[0][0])
    return result


def parse_footnote(footnote: str | None, treatments=None) -> dict:
    """Return only defaults explicitly stated in a sheet's 注： annotation."""
    if not footnote:
        return {}
    text = normalize_text(footnote)
    result = {}
    rpm = re.search(r'(?:未特别标明|未注明|未标明)[^;。\n]*?' + _NUMBER + r'\s*rpm\b', text, re.I)
    if rpm:
        result['spin_rpm'] = float(rpm[1])
    primary = re.search(r'一次(?:旋涂)?(?:烘干|烘烤|烘焙)?条件(?:为)?\s*[:：]?\s*(.*?)(?=二次|$)', text, re.S)
    if primary:
        result.update(_parse_primary(primary[1]))
    secondary = re.search(r'二次(?:旋涂)?(?:烘干|烘烤|烘焙)?条件(?:为)?\s*[:：]?\s*(.*)', text, re.S)
    if secondary:
        result.update(_parse_secondary(secondary[1], treatments))
    return result


def sheet_footnote(worksheet) -> str | None:
    """Read annotation cells without modifying or saving the workbook."""
    notes = []
    for row in worksheet.iter_rows():
        for cell in row:
            value = cell.value
            if isinstance(value, str) and re.match(r'^\s*注\s*[:：]', value):
                if value not in notes:
                    notes.append(value)
    return '\n'.join(notes) or None


def extract_sheet_footnote(worksheet, treatments=None) -> tuple[str | None, dict]:
    note = sheet_footnote(worksheet)
    return note, parse_footnote(note, treatments)


def name_conditions(raw_name: Any, treatments=None) -> dict:
    """Extract process fields from sample text while omitting model declarations."""
    text = normalize_text(raw_name)
    primary, secondary = [], []
    for line in text.splitlines():
        if re.search(r'模型|model', line, re.I):
            continue
        name, match = _treatment_match(line, treatments)
        explicit_secondary = re.search(r'二次(?:烘|焙|旋)', line)
        if match:
            # Conditions before a treatment on the same line still describe
            # primary baking; the treatment and following bake are secondary.
            primary.append(line[:match.start()])
            secondary.append(line[match.start():])
        elif explicit_secondary:
            primary.append(line[:explicit_secondary.start()])
            secondary.append(line[explicit_secondary.start():])
        else:
            primary.append(line)
    result = _parse_primary('\n'.join(primary))
    if secondary:
        result.update(_parse_secondary('\n'.join(secondary), treatments))
    first = text.split('\n', 1)[0].split('(')[0].strip()
    if re.search(r'-R\s*$', first, re.I):
        result['variant'] = '重测'
    dilution = re.search(r'稀释\s*\d+\s*倍', text)
    if dilution:
        result['variant'] = re.sub(r'\s+', '', dilution[0])
    return result


def sample_conditions(raw_name: Any, footnote: str | None = None,
                      manual: Mapping | None = None, treatments=None) -> dict:
    defaults = parse_footnote(footnote, treatments)
    named = name_conditions(raw_name, treatments)
    overrides = manual or {}
    result, sources = {}, {}
    for field in CONDITION_FIELDS:
        if field in overrides:
            result[field], sources[field] = overrides[field], 'manual'
        elif field in named:
            result[field], sources[field] = named[field], 'name'
        elif field in defaults:
            result[field], sources[field] = defaults[field], 'footnote'
        else:
            result[field], sources[field] = None, None
    if overrides.get('treatment'):
        result['treatment'] = identify_treatment(overrides['treatment'], treatments) or overrides['treatment']
    return {**result, 'field_sources': sources, 'footnote': footnote}


def compose_sample(base_sample: Mapping, rows: list[dict], defaults: Mapping | None = None,
                   overrides: Mapping | None = None, treatments=None) -> dict:
    """Store adapter: enrich the existing sample without changing its identity."""
    named = name_conditions(base_sample.get('raw_name'), treatments)
    observed = [row for row in rows if str(row.get('treatment_stage', '')).startswith('after_')]
    observed_names = list(dict.fromkeys(row.get('treatment_name') or 'SolvA' for row in observed))
    if 'treatment' not in named and len(observed_names) == 1:
        # The explicit test-count marker is another part of the sample's name
        # metadata. A footnote is preferred over legacy inferred SolvA when
        # no row was annotated by the v2 recognizer.
        if not (defaults or {}).get('treatment') or normalize_alias(observed_names[0]) != normalize_alias(defaults['treatment']):
            named['treatment'] = observed_names[0]
    result, sources = dict(base_sample), {}
    for field in CONDITION_FIELDS:
        if field in (overrides or {}):
            result[field], sources[field] = overrides[field], 'manual'
        elif field in named:
            result[field], sources[field] = named[field], 'name'
        elif field in (defaults or {}):
            result[field], sources[field] = defaults[field], 'footnote'
        else:
            result[field], sources[field] = None, None
    if 'note' in (overrides or {}):
        result['note'] = overrides['note']
    # User 2026-10-21: when the sheet gives no test date, take it from the workbook
    # file name and say so; a manual date wins; otherwise leave it blank (待填).
    if 'test_date' in (overrides or {}):
        result['test_date'], sources['test_date'] = overrides['test_date'], 'manual'
    elif base_sample.get('test_date'):
        sources['test_date'] = 'sheet'
    else:
        source_file = next((row.get('source_file') for row in rows if row.get('source_file')), None)
        guessed = date_from_filename(source_file)
        result['test_date'], sources['test_date'] = (guessed, 'filename') if guessed else (None, None)
    manual_pending = False
    if (overrides or {}).get('treatment'):
        canonical = identify_treatment(overrides['treatment'], treatments)
        if canonical:
            result['treatment'] = canonical
        else:
            manual_pending = True
    result['field_sources'] = sources
    # A known manual sample field does not globally confirm an unknown marker;
    # the resolve endpoint remains responsible for that vocabulary decision.
    result['treatment_pending'] = manual_pending or any(bool(row.get('treatment_pending')) for row in rows)
    return result


def apply_manual_patch(manual: Mapping | None, patch: Mapping) -> dict:
    """Update persisted overrides; null + reset removes a manual key."""
    unknown = set(patch) - {*CONDITION_FIELDS, 'note', 'test_date', 'reset'}
    if unknown:
        raise ValueError('不支持修改字段：' + '、'.join(sorted(unknown)))
    if 'reset' in patch and not isinstance(patch['reset'], bool):
        raise ValueError('reset 必须为布尔值')
    updated = dict(manual or {})
    for field, value in patch.items():
        if field == 'reset':
            continue
        if patch.get('reset') and value is None:
            updated.pop(field, None)
            continue
        if value is not None:
            if field in NUMERIC_CONDITIONS:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f'{field} 必须为有限数值或 null')
                if field in {'spin_rpm', 'bake_time_s', 'post_bake_time_s'} and value < 0:
                    raise ValueError(f'{field} 不能为负数')
            elif not isinstance(value, str):
                raise ValueError(f'{field} 必须为字符串或 null')
            elif field == 'test_date' and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise ValueError('测试日期请写成 YYYY-MM-DD，例如 2026-09-20')
        updated[field] = value
    return updated
