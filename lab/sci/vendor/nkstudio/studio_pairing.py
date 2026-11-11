"""SolvA pairing data contract; no GUI, transport or new database schema.

All custom fields and confirmed pairs live in Library.settings under SETTINGS_KEY.
Native sample metadata is never updated by this module. IDs are database row IDs,
not editable names. Candidates are suggestions and always need explicit confirmation.

Public functions:
* default_config / normalize_config / validate_config / load_config: detached v1
  dicts. Writes belong to the host's conflict-checked DesktopStore transaction.
* metadata_values(record, config): native metadata + aliases + custom field values.
* propose_pairs(records, config): candidates, issues and active unpaired row IDs.
* make_pair(before_id, after_id, records, ...): evidence-bound pair; excluded
  samples are permitted, archived/missing samples are not.
* validate_pair(pair, records): validate an explicitly confirmed pair, returning
  a detached copy; raises ValueError for invalid or stale evidence.
* pair_status(pair, records): valid / stale / missing / excluded. Missing wins
  over stale, stale wins over excluded. Archived records are excluded; exclusion
  from repeatability alone does not exclude a pair.
* pair_target_rows(session, pair, records, targets=None): flat rows, post minus
  pre, using the original AnalysisSession.target_value exclusively. Only a
  confirmed, valid pair can be calculated. Native NaN and source labels survive.

String matching is NFKC + strip + casefold equality, never fuzzy/substring field
matching. A selected state_field disables name fallback, even for unknown states.
Name fallback removes the explicit SolvA marker using the existing native naming
convention, retaining batch/repeat numbers. Optional rule keys also constrain name
candidates. No pairing candidate establishes physical-point identity or causality.
"""
from __future__ import annotations

import copy
import hashlib
import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping

SETTINGS_KEY = 'nk_studio_pairing_v1'
SCHEMA_VERSION = 1
METHODS = frozenset(('manual', 'fields', 'name'))
# Custom values cannot spoof sample identity, evidence or analysis eligibility.
_PROTECTED = frozenset((
    'id', 'dbid', 'db_id', '_db_id', 'sample_id', 'content_hash', 'hash',
    '_content_hash', 'revision', '_revision', 'included', 'archived',
    'repeatability_included',
))
_SolvA = re.compile(r'([-_\s]*)SolvA([-_\s]*)', re.IGNORECASE)


def _text(value):
    return unicodedata.normalize('NFKC', str(value)).strip()


def _normalized(value):
    return '' if value is None else _text(value).casefold()


def _row_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError('样品数据库 ID 须为正整数')
    return value


def _field_name(value):
    if not isinstance(value, str) or not _text(value):
        raise ValueError('字段名须为非空字符串')
    return _text(value)


def _string_list(value, title):
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValueError(title + '须为字符串列表')
    result, seen = [], set()
    for item in value:
        name = _field_name(item)
        key = _normalized(name)
        if key in seen:
            raise ValueError(title + '不能包含规范化后重复的值')
        seen.add(key)
        result.append(name)
    return result


def default_config():
    """New mutable config; keys=[] means name fallback without field constraints."""
    return {'version': SCHEMA_VERSION, 'fields': [], 'values': {},
            'rules': {'keys': [], 'state_field': '', 'before_values': [],
                      'after_values': [], 'name_fallback': True}, 'pairs': []}


def _pair_dict(pair):
    if not isinstance(pair, Mapping):
        raise ValueError('配对须为对象')
    before, after = _row_id(pair.get('before_id')), _row_id(pair.get('after_id'))
    if before == after:
        raise ValueError('处理前后不能是同一条样品记录')
    method = pair.get('method', 'manual')
    if method not in METHODS:
        raise ValueError('配对 method 须为 manual、fields 或 name')
    identifier = pair.get('id', '')
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError('配对 ID 须为非空字符串')
    result = {'id': identifier.strip(), 'before_id': before, 'after_id': after,
              'method': method}
    for key in ('label', 'note', 'before_hash', 'after_hash'):
        value = pair.get(key, '')
        if not isinstance(value, str):
            raise ValueError(key + '须为字符串')
        result[key] = value
    confirmed = pair.get('confirmed', False)
    if type(confirmed) is not bool:
        raise ValueError('confirmed 须为布尔值')
    result['confirmed'] = confirmed
    return result


def normalize_config(config=None):
    """Validate v1 without mutating input; incomplete rule drafts may be saved.

    Stored pairs must already be confirmed. To inspect or confirm a candidate,
    use make_pair/validate_pair before appending it to config['pairs'].
    """
    if config is None:
        return default_config()
    if not isinstance(config, Mapping):
        raise ValueError('配对配置须为对象')
    version = config.get('version', SCHEMA_VERSION)
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ValueError('不支持的配对配置版本')
    result = default_config()
    result['fields'] = _string_list(config.get('fields', []), '自定义字段')
    field_map = {_normalized(v): v for v in result['fields']}
    if set(field_map) & _PROTECTED:
        raise ValueError('自定义字段不能覆盖样品 ID、哈希、版本或纳入状态')
    values = config.get('values', {})
    if not isinstance(values, Mapping):
        raise ValueError('values 须按数据库 ID 保存字段对象')
    for identifier, custom in values.items():
        if not isinstance(identifier, str) or not identifier.isascii() or not identifier.isdigit():
            raise ValueError('values 的键须为数据库 ID 字符串')
        key = str(_row_id(int(identifier)))
        if key in result['values']:
            raise ValueError('values 包含重复的数据库 ID')
        if not isinstance(custom, Mapping):
            raise ValueError('每条自定义值须为字段对象')
        cleaned = {}
        for name, value in custom.items():
            name = _normalized(_field_name(name))
            if name not in field_map:
                raise ValueError('未声明的自定义字段：' + name)
            canonical = field_map[name]
            if canonical in cleaned:
                raise ValueError('自定义值包含重复字段')
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise ValueError('自定义字段值须为标量或空值')
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError('自定义字段值不能为非有限数')
            cleaned[canonical] = value
        result['values'][key] = cleaned
    rules = config.get('rules', {})
    if not isinstance(rules, Mapping):
        raise ValueError('rules 须为对象')
    for key in ('keys', 'before_values', 'after_values'):
        result['rules'][key] = _string_list(rules.get(key, []), key)
    state = rules.get('state_field', '')
    if not isinstance(state, str):
        raise ValueError('state_field 须为字符串')
    result['rules']['state_field'] = _text(state)
    fallback = rules.get('name_fallback', True)
    if type(fallback) is not bool:
        raise ValueError('name_fallback 须为布尔值')
    result['rules']['name_fallback'] = fallback
    before = {_normalized(v) for v in result['rules']['before_values']}
    after = {_normalized(v) for v in result['rules']['after_values']}
    if before & after:
        raise ValueError('处理前和处理后状态值不能重叠')
    if state and _normalized(state) in {_normalized(k) for k in result['rules']['keys']}:
        raise ValueError('状态字段不能同时作为前后必须相等的配对键')
    pairs = config.get('pairs', [])
    if not isinstance(pairs, list):
        raise ValueError('pairs 须为列表')
    seen = set()
    for pair in pairs:
        item = _pair_dict(pair)
        if not item['confirmed']:
            raise ValueError('保存的 pairs 仅接受明确确认的配对')
        if item['id'] in seen:
            raise ValueError('配对 ID 重复')
        seen.add(item['id'])
        result['pairs'].append(item)
    return result


def load_config(library):
    """Read only this namespace through the original Library settings API."""
    return normalize_config(library.get_setting(SETTINGS_KEY, None))


def validate_config(config, records=None):
    """Normalize a detached config, retaining historical missing/stale pairs.

    records optionally verifies row-ID uniqueness, never re-confirms pair evidence.
    New/edited pairs must go through validate_pair separately. Persistence belongs
    to DesktopStore.save_pairing, inside its conflict-checked write transaction.
    """
    if records is not None:
        _records(records)
    return normalize_config(config)


def _metadata_values(record, config):
    identifier = _row_id(record.get('id'))
    metadata = copy.deepcopy(record.get('metadata') or {})
    if not isinstance(metadata, dict):
        raise ValueError('样品 metadata 须为对象')
    name = record.get('name') or metadata.get('样品名称') or record.get('sample_id', '')
    group = record.get('group', metadata.get('分组', ''))
    filename = record.get('original_file_name') or record.get('file_name', '')
    aliases = {'id': identifier, 'dbid': identifier, 'db_id': identifier,
               '_db_id': identifier, 'sample_id': record.get('sample_id', ''),
               'name': name, '样品名称': name, '分组': group, 'group': group,
               'filename': filename, 'file_name': record.get('file_name', filename),
               'original_file_name': filename,
               'content_hash': record.get('content_hash', ''),
               '_content_hash': record.get('content_hash', ''),
               'hash': record.get('content_hash', ''),
               'revision': record.get('revision'), '_revision': record.get('revision'),
               'included': record.get('included', True),
               'archived': record.get('archived', False),
               'repeatability_included': record.get('repeatability_included', True)}
    metadata.update(aliases)
    metadata.update(copy.deepcopy(config['values'].get(str(identifier), {})))
    return metadata


def metadata_values(record, config=None):
    """Return a detached merged view; custom values never touch native metadata."""
    return _metadata_values(record, normalize_config(config))


def _records(records):
    result = {}
    for record in records:
        identifier = _row_id(record.get('id'))
        if identifier in result:
            raise ValueError('样品数据库 ID 重复')
        result[identifier] = record
    return result


def _status(pair, indexed):
    before, after = indexed.get(pair['before_id']), indexed.get(pair['after_id'])
    if before is None or after is None:
        return 'missing'
    for side, record in (('before', before), ('after', after)):
        saved, current = pair[side + '_hash'], record.get('content_hash', '')
        if not saved or not current or saved != current:
            return 'stale'
    if any(r.get('archived', False) or not r.get('included', True) for r in (before, after)):
        return 'excluded'
    return 'valid'


def pair_status(pair, records):
    """Names/revisions do not rebind identity; changed or absent hashes are stale."""
    return _status(_pair_dict(pair), _records(records))


def _pair_records(before_id, after_id, indexed):
    if before_id == after_id:
        raise ValueError('处理前后不能是同一条样品记录')
    try:
        before, after = indexed[before_id], indexed[after_id]
    except KeyError as exc:
        raise ValueError('配对样品不存在') from exc
    if before.get('archived', False) or after.get('archived', False):
        raise ValueError('归档样品不能新建或重新确认配对')
    return before, after


def make_pair(before_id, after_id, records, *, label='', note='', method='manual',
              pair_id=None, confirmed=True):
    """Bind current evidence after an explicit manual choice/confirmation.

    Reconfirming stale evidence is explicit: call make_pair again and replace
    that saved pair. validate_pair never silently updates evidence hashes.
    """
    before_id, after_id = _row_id(before_id), _row_id(after_id)
    indexed = _records(records)
    before, after = _pair_records(before_id, after_id, indexed)
    identifier = pair_id if pair_id is not None else 'pair-' + hashlib.sha256(
        f'{before_id}:{after_id}'.encode('ascii')).hexdigest()[:20]
    pair = _pair_dict({'id': identifier, 'label': label or
                      f'{before.get("sample_id", before_id)} → {after.get("sample_id", after_id)}',
                      'before_id': before_id, 'after_id': after_id,
                      'note': note, 'method': method, 'confirmed': confirmed,
                      'before_hash': before.get('content_hash', ''),
                      'after_hash': after.get('content_hash', '')})
    if _status(pair, indexed) == 'stale':
        raise ValueError('缺少配对样品内容哈希，不能绑定证据')
    return pair


def validate_pair(pair, records, config=None):
    """Validate a user-confirmed new/edit pair; excluded remains saveable.

    Both hash keys absent means the caller explicitly asks to bind current
    evidence (the manual Add/Update action). Existing hashes are verified and
    never refreshed silently. Candidate confirmation keeps its proposed hashes.
    config is accepted for GUI callers and validated without changing its pairs.
    """
    if config is not None:
        normalize_config(config)
    bind_current = isinstance(pair, Mapping) and not any(
        key in pair for key in ('before_hash', 'after_hash'))
    pair = _pair_dict(pair)
    indexed = _records(records)
    before, after = _pair_records(pair['before_id'], pair['after_id'], indexed)
    if not pair['confirmed']:
        raise ValueError('候选配对尚未明确确认')
    if bind_current:
        pair['before_hash'] = before.get('content_hash', '')
        pair['after_hash'] = after.get('content_hash', '')
    if _status(pair, indexed) == 'stale':
        raise ValueError('配对内容哈希已变化或缺失，须明确重新确认')
    return pair


def _field_value(values, field):
    # Exact normalized field names only. Conflicting aliases are not guessed.
    matches = [value for key, value in values.items() if _normalized(key) == _normalized(field)]
    if not matches:
        return ''
    normalized = {_normalized(value) for value in matches}
    if len(normalized) > 1:
        raise ValueError('字段名规范化后存在冲突：' + field)
    return normalized.pop()


def _issue(key, kind, before=(), after=(), message=''):
    return {'key': key, 'type': kind, 'before_ids': sorted(before),
            'after_ids': sorted(after), 'message': message}


def propose_pairs(records, config=None):
    """Suggest only unique 1:1 matches, preserving every ambiguous/unmatched ID.

    Archived records are not proposed or listed as unpaired. Excluded records
    can be paired for record keeping, but pair_status remains excluded. Saved
    confirmed pairs reserve their IDs even when stale, preventing silent
    replacement. Stale/missing saved members remain visible in unpaired/issues.
    """
    config = normalize_config(config)
    indexed = _records(records)
    active = {i: r for i, r in indexed.items() if not r.get('archived', False)}
    issues, candidates, reserved, covered = [], [], set(), set()
    for pair in config['pairs']:
        ids = {pair['before_id'], pair['after_id']}
        reserved.update(ids)
        status = _status(pair, indexed)
        if status in ('valid', 'excluded'):
            covered.update(ids)
        if status != 'valid':
            issues.append(_issue(pair['id'], 'saved_pair_' + status,
                                 [pair['before_id']], [pair['after_id']],
                                 '已保存配对状态：' + status + '；不会自动替换或更新证据'))
    rules = config['rules']
    state = rules['state_field']
    keys = rules['keys']
    before_states = {_normalized(v) for v in rules['before_values']}
    after_states = {_normalized(v) for v in rules['after_values']}
    pending = {i: r for i, r in active.items() if i not in reserved}
    if state and (not keys or not before_states or not after_states):
        issues.append(_issue(state, 'incomplete_rules', message=
                             '字段识别须选择至少一个配对键，并填写前后状态值；未退回名称猜测'))
        return {'candidates': [], 'issues': issues, 'unpaired': sorted(set(active) - covered)}
    if not state and not rules['name_fallback']:
        return {'candidates': [], 'issues': issues, 'unpaired': sorted(set(active) - covered)}
    buckets = defaultdict(lambda: {'before': [], 'after': []})
    labels = {}
    for identifier, record in sorted(pending.items()):
        values = _metadata_values(record, config)
        side = None
        try:
            if state:
                value = _field_value(values, state)
                side = 'before' if value in before_states else 'after' if value in after_states else None
                if side is None:
                    issues.append(_issue(str(identifier), 'unknown_state', message=
                                         f'记录 {identifier} 的状态字段 {state} 为空或未精确匹配前后状态'))
                    continue
                name_key = None
            else:
                name = _text(record.get('sample_id', ''))
                side = 'after' if _SolvA.search(name) else 'before'
                name_key = _normalized(_SolvA.sub(lambda m: (m[1] or m[2])[:1], name).strip('-_ '))
                if not name_key:
                    issues.append(_issue(str(identifier), 'missing_name', message='缺少可用的名称配对键'))
                    continue
            field_keys = tuple(_field_value(values, key) for key in keys)
            if any(not value for value in field_keys):
                issues.append(_issue(str(identifier), 'missing_key',
                                     [identifier] if side == 'before' else [],
                                     [identifier] if side == 'after' else [],
                                     f'记录 {identifier} 缺少配对键值；空白不视为相同'))
                continue
        except ValueError as exc:
            issues.append(_issue(str(identifier), 'invalid_field', message=str(exc)))
            continue
        bucket = (*field_keys, name_key) if name_key is not None else field_keys
        label = ' | '.join([*(f'{k}={v}' for k, v in zip(keys, field_keys)),
                            *([name_key] if name_key is not None else [])])
        labels[bucket] = label
        buckets[bucket][side].append(identifier)
    for bucket, members in sorted(buckets.items()):
        before, after = members['before'], members['after']
        label = labels[bucket]
        if len(before) > 1 or len(after) > 1:
            issues.append(_issue(label, 'ambiguous', before, after,
                                 '同一键存在多个前/后记录；请人工选择，未自动配对'))
        elif not before or not after:
            issues.append(_issue(label, 'missing_before' if not before else 'missing_after',
                                 before, after, '缺少处理前记录' if not before else '缺少处理后记录'))
        else:
            try:
                pair = make_pair(before[0], after[0], list(indexed.values()), label=label,
                                 method='fields' if state else 'name', confirmed=False)
            except ValueError as exc:
                issues.append(_issue(label, 'invalid_evidence', before, after, str(exc)))
                continue
            candidates.append(pair)
            covered.update((before[0], after[0]))
    return {'candidates': candidates, 'issues': issues, 'unpaired': sorted(set(active) - covered)}


def _finite(value):
    try:
        return math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def _in_range(value):
    flag = value.get('处于实测范围', value.get('in_range', False))
    return flag == '是' if isinstance(flag, str) else bool(flag)


def pair_target_rows(session, pair, records, targets=None):
    """One flat row per requested wavelength; original values, no new gap policy.

    Row fields: pair_id, label, status, before_id, after_id, before_sample_id,
    after_sample_id, wavelength_nm, before_n/k, after_n/k, before/after_source,
    before/after_is_in_range (bool), before/after_range_label (original flag),
    delta_n/k (after-before, NaN if either input unavailable/outside its range).
    Does not change reference, Config, inclusion, metadata or scientific arrays.
    """
    indexed = _records(records)
    pair = validate_pair(pair, list(indexed.values()))
    status = _status(pair, indexed)
    if status != 'valid':
        raise ValueError('配对当前不可计算：' + status)
    sources = {}
    for side in ('before', 'after'):
        record = indexed[pair[side + '_id']]
        source = record.get('sample_id', '')
        if not source:
            raise ValueError('配对样品缺少当前分析来源名称')
        if hasattr(session, 'curves') and source not in session.curves:
            raise ValueError('配对样品不在当前分析会话；请先刷新')
        if hasattr(session, '_sample_by_id'):
            sample = session._sample_by_id.get(source)
            if sample is None:
                raise ValueError('当前会话缺少原样品；不能以组均值代替配对样品')
            metadata = sample.metadata
            if metadata.get('_db_id', record['id']) != record['id']:
                raise ValueError('当前会话的样品身份已变化；请先刷新')
            if metadata.get('_content_hash', record['content_hash']) != record['content_hash']:
                raise ValueError('当前会话的数据证据已变化；请先刷新')
        sources[side] = source
    requested = session.cfg.target_wavelengths_nm if targets is None else targets
    if isinstance(requested, (str, bytes)):
        raise ValueError('目标波长须为数值列表')
    wavelengths = []
    for target in requested:
        if isinstance(target, bool) or not _finite(target) or target <= 0:
            raise ValueError('目标波长须为正的有限数值')
        target = float(target)
        if target not in wavelengths:
            wavelengths.append(target)
    rows = []
    for wavelength in wavelengths:
        values = {side: session.target_value(source, wavelength) for side, source in sources.items()}
        row = {'pair_id': pair['id'], 'label': pair['label'], 'status': status,
               'before_id': pair['before_id'], 'after_id': pair['after_id'],
               'before_sample_id': sources['before'], 'after_sample_id': sources['after'],
               'wavelength_nm': wavelength}
        for side, value in values.items():
            row.update({side + '_n': value['n'], side + '_k': value['k'],
                        side + '_source': value.get('数据来源', value.get('source', '')),
                        side + '_is_in_range': _in_range(value),
                        side + '_range_label': value.get('处于实测范围', value.get('in_range', False))})
        for signal in ('n', 'k'):
            before, after = values['before'][signal], values['after'][signal]
            row['delta_' + signal] = (after - before if
                _in_range(values['before']) and _in_range(values['after']) and
                _finite(before) and _finite(after) else math.nan)
        rows.append(row)
    return rows
