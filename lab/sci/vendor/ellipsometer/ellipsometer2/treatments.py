"""Named-treatment recognition at the v2 data boundary, leaving v1 intact."""
from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from typing import Any, Mapping

DEFAULT_TREATMENTS = [
    {'name': 'SolvA', 'aliases': ['SolvA', 'PMA', 'PM acetate', '溶剂A']},
]


def normalize_alias(value: Any) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))).casefold()


def get_treatments(config=None) -> list[dict]:
    if config is None:
        return deepcopy(DEFAULT_TREATMENTS)
    if isinstance(config, Mapping):
        config = config.get('treatments', DEFAULT_TREATMENTS)
    return deepcopy(config)


def identify_treatment(value: Any, config=None) -> str | None:
    normalized = normalize_alias(value)
    if not normalized:
        return None
    for row in get_treatments(config):
        if any(normalized == normalize_alias(alias) for alias in [row['name'], *row.get('aliases', [])]):
            return row['name']
    return None


def validate_treatments(rows: Any) -> list[str]:
    if not isinstance(rows, list):
        return ['处理方式 treatments 必须为列表']
    errors, owners = [], {}
    for index, row in enumerate(rows, 1):
        if not isinstance(row, Mapping):
            errors.append(f'第 {index} 项处理方式必须为对象')
            continue
        name, aliases = row.get('name'), row.get('aliases', [])
        valid = isinstance(name, str) and bool(normalize_alias(name))
        if not valid:
            errors.append(f'第 {index} 项处理名称必须为非空字符串')
        if not isinstance(aliases, list) or any(not isinstance(a, str) or not normalize_alias(a) for a in aliases):
            errors.append(f'第 {index} 项处理别名必须为非空字符串列表')
            aliases = []
        seen = set()
        for alias in aliases:
            normalized = normalize_alias(alias)
            if normalized in seen:
                errors.append(f'第 {index} 项处理别名“{alias}”重复')
            seen.add(normalized)
        for alias in ([name] if valid else []) + aliases:
            normalized = normalize_alias(alias)
            if normalized in owners and owners[normalized] != index:
                errors.append(f'第 {index} 项处理名称或别名“{alias}”与第 {owners[normalized]} 项冲突')
            owners.setdefault(normalized, index)
    return errors


class TreatmentClassifier:
    """Track explicit test-count markers, including their following replicates.

    Each block must have its own classifier or call reset(). The legacy parser
    still supplies numeric values; this classifier changes v2 stage metadata.
    """
    def __init__(self, config=None):
        self.config = config or {}
        self.treatments = get_treatments(config)
        self.markers = {}
        for treatment in self.treatments:
            for alias in [treatment['name'], *treatment.get('aliases', [])]:
                norm = normalize_alias(alias)
                for marker in (f'{norm}后', f'{norm}处理后', f'after{norm}', f'{norm}after'):
                    self.markers[marker] = treatment['name']
        self.reset()

    def reset(self):
        self.name, self.pending, self.sequence = None, False, 0

    def classify(self, test_label: Any) -> dict:
        text = unicodedata.normalize('NFKC', str(test_label or '')).strip()
        digit_marker = re.fullmatch(r'\d+\s*\(\s*([^()]+?)\s*\)', text)
        name, pending = None, False
        if digit_marker:
            raw_name = digit_marker[1].strip()
            name = identify_treatment(raw_name, self.treatments)
            if name is None:
                name, pending = raw_name, True
        else:
            name = self.markers.get(normalize_alias(text))
            # Preserve any configured legacy after marker semantics.
            if name is None and isinstance(self.config, Mapping):
                if any(re.search(pattern, text, re.I) for pattern in self.config.get('stage_rules', {}).get('after_markers', [])):
                    name = identify_treatment('SolvA', self.treatments)
                    if name is None:
                        name, pending = 'SolvA', True
        if name is not None:
            self.name, self.pending = name, pending
            # A physical block's successive treatments remain distinct even
            # when their names differ. The v1 core groups by stage sequence.
            self.sequence += 1
        stage = f'after_solva_{self.sequence}' if self.sequence else 'before_solva'
        api_stage = f'after_{self.sequence}' if self.sequence else 'before'
        return {'treatment_name': self.name, 'treatment_pending': self.pending,
                'treatment_stage': stage, 'treatment_sequence': self.sequence,
                'stage': api_stage, 'stage_label': stage_label(stage, self.name, self.pending),
                'is_after_marker': name is not None}


def stage_label(stage, treatment_name=None, pending=False) -> str:
    if stage in {'before', 'before_solva'}:
        return '处理前'
    match = re.fullmatch(r'after_(?:solva_)?(\d+)', str(stage))
    if not match:
        return '常规'
    prefix = f'第 {match[1]} 次' if int(match[1]) > 1 else ''
    return f'{prefix}处理后（{treatment_name or "SolvA"}{"，待确认" if pending else ""}）'


def annotate_records(records: list[dict], config=None) -> list[dict]:
    """Return records with v2 stage metadata; numeric fields stay untouched."""
    classifiers = {}
    result = []
    for record in records:
        key = (record.get('source_file_hash'), record.get('source_sheet'), record['sample_instance_id'])
        if key not in classifiers:
            classifiers[key] = TreatmentClassifier(config)
        classifier = classifiers[key]
        decision = classifier.classify(record.get('test_label_raw'))
        result.append({**record, **{k: decision[k] for k in (
            'treatment_name', 'treatment_pending', 'treatment_stage', 'treatment_sequence')}})
    return result


def annotate_rows(rows: list[dict], treatments=None, dissolution_markers=None) -> list[dict]:
    """Compatibility adapter used by Store during imports and settings changes.

    Dissolution is parsed by the legacy parser using the settings-derived config;
    no extra replacement of measured values is performed here.
    """
    config = treatments if isinstance(treatments, Mapping) else {'treatments': get_treatments(treatments)}
    result = annotate_records(rows, config)
    for row in result:
        row['treatment_raw_name'] = row.get('treatment_name') if row.get('treatment_pending') else None
    return result


def resolve_treatment(rows: list[dict], raw_name: str, action: str, treatment: str | None = None) -> list[dict]:
    if not isinstance(raw_name, str) or not normalize_alias(raw_name):
        raise ValueError('待确认处理名称不能为空')
    result = deepcopy(rows)
    if treatment is not None and (not isinstance(treatment, str) or not normalize_alias(treatment)):
        raise ValueError('处理方式 treatment 必须为非空字符串')
    if action == 'alias':
        if not isinstance(treatment, str) or not normalize_alias(treatment):
            raise ValueError('归为别名时必须指定已有处理方式 treatment')
        canonical = identify_treatment(treatment, result)
        if canonical is None:
            raise ValueError('指定的处理方式不存在')
        target = next(row for row in result if row['name'] == canonical)
        if not any(normalize_alias(a) == normalize_alias(raw_name) for a in [target['name'], *target.get('aliases', [])]):
            target.setdefault('aliases', []).append(raw_name.strip())
    elif action == 'new':
        name = (treatment or raw_name).strip()
        if identify_treatment(name, result):
            raise ValueError('该处理方式已存在，请使用 alias 操作')
        result.append({'name': name, 'aliases': [raw_name.strip()]})
    else:
        raise ValueError('action 必须为 alias 或 new')
    errors = validate_treatments(result)
    if errors:
        raise ValueError('；'.join(errors))
    return result
