"""Read-only quality suggestions shared by the quality page and capabilities.

搬自 ellipsometer-2-v7ui/ellipsometer2/quality_flags.py（提交 ddbc78a4）。改动只有取数：
  - import 改成包内相对路径（.memstore、..src.）；
  - _Engine.__init__ 里样品、工作簿、表页列位置、忽略清单从 ListStore 的列表取，不再读 SQLite。
规则、阈值、判定和消息一行没动。quality_flags()（批量接口）里查 duplicate_blocks 的那一支没用到。
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
import json
import math
from statistics import mean, median
from threading import RLock

from .memstore import measurement_load
from .memstore import api_stage

FIELDS = ('n_193', 'k_193', 'n_248', 'k_248', 'n_633', 'k_633', 'thickness', 'mse')
COLUMNS = dict(zip(FIELDS, 'CDEFGHIJ'))
RULES = ('copied_columns', 'mse_high', 'mse_block', 'optical_outlier',
         'readings_count', 'missing_stage', 'average_row', 'dissolved_block',
         'text_number', 'duplicate_conflict')
# Only cell metadata is retained, never open workbook objects. Content hashes are
# immutable identities, shared by imported copies in different libraries.
_TEXT_CACHE = {}
_TEXT_LOCK = RLock()


def _numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _cells(fields, row, columns=COLUMNS):
    cols = sorted({columns[f] for f in fields if f in columns}, key=lambda c: (len(c), c))
    if row is None or not cols:
        return ''
    if len(cols) > 1 and all(len(c) == 1 for c in cols) and ord(cols[-1]) - ord(cols[0]) == len(cols) - 1:
        return f'{cols[0]}{row}:{cols[-1]}{row}'
    return ','.join(f'{c}{row}' for c in cols)


def _text_cells(store, book):
    sha = book['sha256']
    with _TEXT_LOCK:
        if sha in _TEXT_CACHE:
            return _TEXT_CACHE[sha], None
        path = store._find_original(book)
        if path is None:
            return {}, '找不到 SHA256 匹配的原工作簿副本，无法检查 Excel 单元格类型'
        from openpyxl import load_workbook
        try:
            wb = load_workbook(path, read_only=True, data_only=False)
            result = {}
            try:
                for ws in wb:
                    for row in ws.iter_rows():
                        for cell in row:
                            if cell.data_type not in ('s', 'inlineStr') or not isinstance(cell.value, str):
                                continue
                            try:
                                value = float(''.join(cell.value.split()).replace(',', '').replace('，', ''))
                            except ValueError:
                                continue
                            if math.isfinite(value):
                                result[(ws.title, cell.row, cell.column_letter)] = (cell.value, value)
            finally:
                wb.close()
        except (OSError, ValueError, KeyError) as exc:
            return {}, f'无法读取工作簿单元格类型：{exc}'
        _TEXT_CACHE[sha] = result
        return result, None


class _Engine:
    def __init__(self, store):
        self.store = store
        self.settings = store.settings()['values']['quality_rules']
        self.samples = {r['sample_id']: dict(r) for r in store._samples}
        self.books = dict(store.books)
        from openpyxl.utils import get_column_letter
        self.columns = {}
        for sheet_id, sheet in store.sheets.items():
            mapping = sheet.get('field_mapping') or {}
            self.columns[sheet_id] = {f: get_column_letter(mapping[f]) for f in FIELDS if isinstance(mapping.get(f), int) and mapping[f] > 0} or COLUMNS
        for row in store._raw_rows():
            self.columns.setdefault(row['sheet_id'], COLUMNS)
        self.exclusions = store._exclusions()
        self.dismissals = set(store.dismissals)
        self.rows = defaultdict(list)
        for row in store._raw_rows():
            item = dict(row)
            item['record'] = measurement_load(item['payload'])
            # Manual and older sparse rows can omit provenance in the payload;
            # the owning workbook still provides its authoritative identity.
            if not item['record'].get('source_file_hash'):
                item['record']['source_file_hash'] = self.books[item['workbook_id']]['sha256']
            self.rows[item['sample_id']].append(item)
        import pandas as pd
        from ..src.analysis_mode import single_stage_sheets, is_single_stage_group
        frame = pd.DataFrame([r['record'] for rows in self.rows.values() for r in rows])
        single_sheets = single_stage_sheets(frame, store.config)
        self.single_samples = {sid for sid, rows in self.rows.items()
                               if is_single_stage_group(pd.DataFrame([r['record'] for r in rows]), single_sheets)}
        self.flags = []

    def flag(self, rule, sample_id, stage, rows, fields=(), *, level='review',
             row=None, basis=None, values=None, message='', actions=None, identity=None):
        sample = self.samples[sample_id]
        source = row or rows[0]
        record = source['record']
        book = self.books[source['workbook_id']]
        stage_key, label = api_stage(stage, sample_id in self.single_samples)
        # Source average rows are not excludable measurements; expose their
        # source location and values without a measurement action target.
        mid = row['measurement_id'] if row and not row['is_average'] else None
        stable = identity or mid or f'{sample_id}:{stage_key}'
        # A grouped reading/average issue keeps its dismissal when a threshold
        # changes only part of its hit set. Fixed-field rules retain field suffixes.
        id_fields = () if rule in {'optical_outlier', 'average_row'} else fields
        fid = f'{rule}:{stable}:{",".join(id_fields)}'
        mids = [r['measurement_id'] for r in rows if not r['is_average']]
        affected = [mid] if mid else ([] if row else mids)
        def excluded(measurement_id):
            exclusion = self.exclusions.get(measurement_id)
            if not exclusion:
                return False
            scope = exclusion['scope']
            selected = set(exclusion.get('fields') or [])
            if scope == 'thickness':
                selected.add('thickness')
            return scope == 'all' or bool(fields) and set(fields) <= selected
        result = dict(flag_id=fid, rule=rule, level=level, sample_id=sample_id,
                      sample_code=sample['sample_code'], project_id=sample.get('project_id'),
                      test_date=sample.get('test_date'), stage=stage_key, stage_label=label,
                      replicate=record.get('replicate_number') if row else None,
                      measurement_id=mid, measurement_ids=mids if not mid else [mid], fields=list(fields),
                      location=dict(workbook=book['file_name'], workbook_sha256=book['sha256'],
                                    sheet=record['source_sheet'], row=source['source_row'],
                                    cells=_cells(fields, source['source_row'], self.columns[source['sheet_id']])),
                      values=values if values is not None else {f: record.get(f) for f in fields},
                      basis=basis or {}, message=message,
                      actions=actions if actions is not None else ['exclude_fields', 'exclude_all', 'dismiss'],
                      excluded=bool(affected) and all(excluded(m) for m in affected), dismissed=fid in self.dismissals)
        self.flags.append(result)
        return result

    def block(self, sid, stage, rows):
        raw = [r for r in rows if not r['is_average']]
        valid = [r for r in raw if any(_numeric(r['record'].get(f)) for f in FIELDS)]
        average = [r for r in rows if r['is_average']]
        if any(r['record'].get('is_dissolved') for r in rows):
            self.flag('dissolved_block', sid, stage, rows, level='info',
                      basis={'note': next((r['record'].get('dissolution_note') for r in rows if r['record'].get('dissolution_note')), None)},
                      message='本阶段标注溶解，按原计算约定处理，不作为错误。', actions=['dismiss'])
            return
        self.copied(sid, stage, valid)
        self.mse(sid, stage, valid)
        self.outliers(sid, stage, valid)
        readings = self.settings['readings']
        if readings['enabled'] and readings['expected_count']:
            if not valid:
                self.flag('missing_stage', sid, stage, rows, basis=dict(actual=0, expected=readings['expected_count'],
                          warnings=[r['record'].get('warning_message') for r in raw if r['record'].get('warning_message')]),
                          message='本阶段无有效读数。', actions=['dismiss'])
            elif len(valid) != readings['expected_count']:
                self.flag('readings_count', sid, stage, rows, basis=dict(actual=len(valid), expected=readings['expected_count']),
                          message=f"本阶段有 {len(valid)}/{readings['expected_count']} 次有效读数。", actions=['dismiss'])
            else:
                seq = [r['record'].get('replicate_number') for r in valid]
                if sorted(v for v in seq if _numeric(v)) != list(range(1, readings['expected_count'] + 1)):
                    self.flag('readings_count', sid, stage, rows, basis=dict(actual=len(valid), expected=readings['expected_count'], replicates=seq),
                              message='读数序号有缺失或重复。', actions=['dismiss'])
            for r in valid:
                missing = [f for f in FIELDS if not _numeric(r['record'].get(f))]
                if missing:
                    self.flag('readings_count', sid, stage, rows, missing, row=r,
                              basis={'warning': r['record'].get('warning_message')},
                              message='本次读数部分数值缺失或无法解析。', actions=['dismiss'])
        self.averages(sid, stage, rows, valid, average)
        if readings['flag_text_numbers']:
            self.text_numbers(sid, stage, rows, raw)

    def copied(self, sid, stage, rows):
        cfg = self.settings['copied_columns']
        if not cfg['enabled'] or not rows:
            return
        medians = {w: median(v) for w in ('193', '248', '633')
                   if (v := [r['record']['n_' + w] for r in rows if _numeric(r['record'].get('n_' + w))])}
        for left, right in combinations(('193', '248', '633'), 2):
            matches = []
            for row in rows:
                p = row['record']
                pair = [p.get(f'{f}_{w}') for w in (left, right) for f in ('n', 'k')]
                if not all(_numeric(v) for v in pair):
                    continue
                if cfg['ignore_zero_pairs'] and all(v == 0 for v in pair):
                    continue
                if abs(pair[0] - pair[2]) <= cfg['equal_tolerance'] and abs(pair[1] - pair[3]) <= cfg['equal_tolerance']:
                    matches.append(row)
            if not matches:
                continue
            if len(matches) / len(rows) > cfg['block_systematic_fraction']:
                fields = [f'{f}_{w}' for w in (left, right) for f in ('n', 'k')]
                self.flag('copied_columns', sid, stage, rows, fields, level='info',
                          basis=dict(systematic=True, fraction=len(matches) / len(rows), threshold=cfg['block_systematic_fraction']),
                          message=f'{left}/{right} nm 的 n/k 在块内系统性相同，请核对模型设定。', actions=['dismiss'])
                continue
            for row in matches:
                p = row['record']
                def deviation(w):
                    med = medians.get(w)
                    return abs(p['n_' + w] - med) / abs(med) if med else 0
                wrong, other = (left, right) if deviation(left) >= deviation(right) else (right, left)
                ambiguous = max(deviation(left), deviation(right)) < .01
                fields = [f'{f}_{w}' for w in ((left, right) if ambiguous else (wrong,)) for f in ('n', 'k')]
                other_cells = _cells(['n_' + other, 'k_' + other], row['source_row'], self.columns[row['sheet_id']])
                self.flag('copied_columns', sid, stage, rows, fields, row=row, level='action',
                          basis=dict(same_as=f'{other} nm（{other_cells}）', block_median={'n_' + w: medians.get(w) for w in (left, right)},
                                     relative_deviation={'n_' + w: deviation(w) for w in (left, right)},
                                     mse=p.get('mse'), threshold=cfg['equal_tolerance'], ambiguous=ambiguous),
                          message=f"第 {int(p['replicate_number']) if _numeric(p.get('replicate_number')) and float(p['replicate_number']).is_integer() else p.get('replicate_number') or '未知'} 次读数：{wrong} nm 的 n/k（{p['n_' + wrong]} / {p['k_' + wrong]}）与 {other} nm 相同，" +
                                  ('无法判断是哪一组抄错。' if ambiguous else f"疑似抄错（{_cells(fields, row['source_row'], self.columns[row['sheet_id']])}）。"))

    def mse(self, sid, stage, rows):
        cfg = self.settings['mse']
        usable = [r for r in rows if _numeric(r['record'].get('mse'))]
        if not cfg['enabled'] or not usable:
            return
        med = median(r['record']['mse'] for r in usable)
        hits = [r for r in usable if r['record']['mse'] > cfg['absolute_max'] or
                len(usable) >= cfg['relative_min_readings'] and med > 0 and r['record']['mse'] > cfg['relative_to_block_median'] * med]
        if not hits:
            return
        basis = dict(block_median=med, absolute_max=cfg['absolute_max'], relative_to_block_median=cfg['relative_to_block_median'],
                     hit_count=len(hits), total_readings=len(usable),
                     hits=[dict(measurement_id=r['measurement_id'], row=r['source_row'], mse=r['record']['mse']) for r in hits])
        if len(hits) / len(usable) > cfg['block_merge_fraction']:
            self.flag('mse_block', sid, stage, rows, ['mse'], basis=basis,
                      values={_reading_label(r): r['record']['mse'] for r in hits},
                      message=f'本阶段 {len(hits)}/{len(usable)} 次 MSE 偏高，中位数 {_shown(med)}。', actions=['exclude_all', 'dismiss'])
        else:
            for r in hits:
                self.flag('mse_high', sid, stage, rows, ['mse'], row=r, basis={**basis, 'hit_count': 1, 'hits': [h for h in basis['hits'] if h['measurement_id'] == r['measurement_id']]},
                          message=f"本次 MSE {r['record']['mse']} 偏高（块中位数 {_shown(med)}，复核线 {_shown(cfg['absolute_max'])}）。", actions=['exclude_all', 'dismiss'])

    def outliers(self, sid, stage, rows):
        cfg = self.settings['optical_outliers']
        if not cfg['enabled']:
            return
        # Later settings rows override earlier rows for the same field, allowing
        # a dedicated wavelength threshold without a second conflicting rule.
        options = {f: option for option in cfg['rows'] for f in option.get('fields', []) if f in FIELDS and f != 'mse'}
        hits = defaultdict(dict)
        for field, option in options.items():
            if not option.get('enabled', True):
                continue
            values = [r['record'][field] for r in rows if _numeric(r['record'].get(field))]
            if len(values) < max(1, cfg['min_readings']):
                continue
            med = median(values)
            mad = median(abs(v - med) for v in values)
            for r in rows:
                v = r['record'].get(field)
                if not _numeric(v):
                    continue
                dev = abs(v - med)
                relative = dev / abs(med) * 100 if med else 0
                floor = max(option['min_absolute'], option['min_absolute_relative_percent'] / 100 * abs(med))
                if dev > cfg['mad_multiplier'] * mad and dev >= floor and relative >= option['min_relative_percent']:
                    hits[r['measurement_id']][field] = dict(median=med, mad=mad, deviation=dev, relative_percent=relative,
                                                          mad_multiplier=cfg['mad_multiplier'], min_absolute=floor,
                                                          min_relative_percent=option['min_relative_percent'])
        for r in rows:
            details = hits.get(r['measurement_id'])
            if details:
                fields = [f for f in FIELDS if f in details]
                actions = ['exclude_fields', 'exclude_all', 'dismiss']
                if fields == ['thickness']:
                    actions.insert(1, 'exclude_thickness')
                self.flag('optical_outlier', sid, stage, rows, fields, row=r, basis={'fields': details, 'hit_count': len(fields)},
                          message='本次读数的 ' + '、'.join(FIELD_LABELS.get(f, f) for f in fields) + ' 偏离同块中位数。', actions=actions)

    def averages(self, sid, stage, rows, valid, averages):
        cfg = self.settings['average_row']
        if not cfg['enabled'] or not valid:
            return
        if not averages:
            if cfg['flag_missing_row']:
                self.flag('average_row', sid, stage, rows, level='info', basis={'missing_row': True, 'hit_count': 1},
                          message='原表平均值行缺失；程序自行复算，统计不受影响。', actions=['dismiss'])
            return
        for avg in averages:
            problems = {}
            for f in FIELDS:
                values = [r['record'][f] for r in valid if _numeric(r['record'].get(f))]
                if not values:
                    continue
                actual, expected = avg['record'].get(f), mean(values)
                kind = f.split('_')[0]
                tolerance = cfg['tolerance'][kind]
                if actual is None and cfg['flag_empty_cell']:
                    problems[f] = dict(status='empty', expected=expected)
                elif actual is not None and (not _numeric(actual) or abs(actual - expected) > tolerance):
                    problems[f] = dict(status='mismatch', expected=expected, actual=actual, tolerance=tolerance)
            if problems:
                fields = [f for f in FIELDS if f in problems]
                self.flag('average_row', sid, stage, rows, fields, row=avg, level='info', basis={'fields': problems, 'hit_count': len(fields)},
                          message=f'原表平均值行有 {len(fields)} 项空白或不同；程序自行复算，统计不受影响。', actions=['dismiss'])

    def text_numbers(self, sid, stage, rows, raw):
        book = self.books[rows[0]['workbook_id']]
        if book.get('original_source') == 'manual':
            return
        cells, error = _text_cells(self.store, book)
        if error:
            # Exactly one unavailable-workbook notice, even across many blocks.
            if any(f['rule'] == 'text_number' and f.get('status') == 'not_computable' and f['location']['workbook_sha256'] == book['sha256'] for f in self.flags):
                return
            flag = self.flag('text_number', sid, stage, rows, level='info', identity=book['sha256'], basis={'reason': error}, message=error, actions=['dismiss'])
            flag['status'] = 'not_computable'
            return
        for r in raw:
            columns = self.columns[r['sheet_id']]
            found = {f: cells[(r['record']['source_sheet'], r['source_row'], columns[f])] for f in FIELDS if f in columns
                     and (r['record']['source_sheet'], r['source_row'], columns[f]) in cells}
            if found:
                self.flag('text_number', sid, stage, rows, list(found), row=r, level='info',
                          values={f: value[0] for f, value in found.items()}, basis={'parsed_values': {f: value[1] for f, value in found.items()}},
                          message='数字在 Excel 中以文本存储；程序已按数值读取，统计不受影响。', actions=['dismiss'])

    def run(self):
        for sid, rows in self.rows.items():
            stages = defaultdict(list)
            for r in rows:
                stages[r['record']['treatment_stage']].append(r)
            # Completely unfit two-stage blocks produce one missing-stage flag.
            all_empty = not any(_numeric(r['record'].get(f)) or r['record'].get('is_dissolved') for r in rows if not r['is_average'] for f in FIELDS)
            if all_empty and self.settings['readings']['enabled'] and self.settings['readings']['expected_count']:
                self.flag('missing_stage', sid, next(iter(stages)), rows, basis={'actual': 0, 'expected': self.settings['readings']['expected_count'],
                          'stages': [api_stage(s)[0] for s in stages], 'warnings': [r['record'].get('warning_message') for r in rows if r['record'].get('warning_message')]},
                          message='整块无有效读数，原表标注或模型无法拟合。', actions=['dismiss'])
                continue
            for stage, block in stages.items():
                self.block(sid, stage, block)
            # Reuse the core's workbook/sheet mode; a before-only workbook is
            # ordinary single-stage unless its configured mode expects treatment.
            if 'before_solva' in stages and not any(s.startswith('after_') for s in stages) and sid not in self.single_samples and self.settings['readings']['enabled'] and self.settings['readings']['expected_count']:
                self.flag('missing_stage', sid, 'after_solva_1', rows, basis={'actual': 0, 'expected': self.settings['readings']['expected_count']},
                          message='缺少处理后阶段读数。', actions=['dismiss'])
            if any(s.startswith('after_') for s in stages) and 'before_solva' not in stages and self.settings['readings']['enabled'] and self.settings['readings']['expected_count']:
                self.flag('missing_stage', sid, 'before_solva', rows, basis={'actual': 0, 'expected': self.settings['readings']['expected_count']},
                          message='缺少处理前阶段读数。', actions=['dismiss'])
        self.duplicates()
        return self.flags

    def duplicates(self):
        cfg = self.settings['duplicate_conflict']
        if not cfg['enabled']:
            return
        candidates = defaultdict(list)
        for sid, rows in self.rows.items():
            sample = self.samples[sid]
            p = rows[0]['record']
            candidates[(p['source_sheet'], ''.join((sample.get('raw_name') or sample['sample_code']).split()))].append((sid, rows))
        # Exact duplicate imports in duplicate_blocks refer to canonical rows;
        # they are not another measured block and never become a conflict.
        for blocks in candidates.values():
            for (left, a), (right, b) in combinations(blocks, 2):
                if a[0]['workbook_id'] == b[0]['workbook_id']:
                    continue
                def readings(rows):
                    occurrences = defaultdict(int)
                    result = {}
                    for r in rows:
                        if r['is_average']:
                            continue
                        base = (r['record']['treatment_stage'], r['record'].get('replicate_number'))
                        key = (*base, occurrences[base])
                        occurrences[base] += 1
                        result[key] = r
                    return result
                aa, bb = readings(a), readings(b)
                changes = []
                for key in sorted(aa.keys() | bb.keys(), key=str):
                    ra, rb = aa.get(key), bb.get(key)
                    for f in (*FIELDS, 'is_dissolved'):
                        av = ra['record'].get(f) if ra else None
                        bv = rb['record'].get(f) if rb else None
                        if av == bv or _numeric(av) and _numeric(bv) and abs(av - bv) <= cfg['value_tolerance']:
                            continue
                        changes.append(dict(stage=key[0], replicate=key[1], field=f, left=av, right=bv,
                                            left_row=ra['source_row'] if ra else None, right_row=rb['source_row'] if rb else None))
                if changes:
                    self.flag('duplicate_conflict', right, b[0]['record']['treatment_stage'], b, level='action',
                              identity=':'.join(sorted((left, right))), basis={'other_sample_id': left, 'sample_ids': [left, right],
                              'other_workbook': self.books[a[0]['workbook_id']]['file_name'],
                              'different_cells': len(changes), 'changes': changes, 'value_tolerance': cfg['value_tolerance']},
                              message=f'跨工作簿同名同页的块有 {len(changes)} 个数值不同，两份均参与统计，请复核。', actions=['exclude_all', 'dismiss'])

    def matches(self, flag, project_id=None, sample_id=None, selected=None):
        participants = flag['basis'].get('sample_ids', [flag['sample_id']]) if flag['rule'] == 'duplicate_conflict' else [flag['sample_id']]
        return any((not sample_id or sid == sample_id) and
                   (not project_id or self.samples[sid].get('project_id') == project_id) and
                   (selected is None or sid in selected) for sid in participants)


def _result(flags, settings=None):
    result = dict(flags=flags, counts={rule: sum(f['rule'] == rule for f in flags) for rule in RULES})
    if settings is not None:
        result.update(levels={level: sum(f['level'] == level for f in flags) for level in ('action', 'review', 'info')}, settings=settings)
    return result


def flags_for(store, project_id=None, sample_id=None):
    """Return suggestions from original values, including excluded/dismissed ones."""
    with store.lock:
        engine = _Engine(store)
        flags = engine.run()
        return _result([f for f in flags if engine.matches(f, project_id, sample_id)], engine.settings)


# Plain names for people (messages and reports); the API keeps the field codes.
FIELD_LABELS = {'thickness': '膜厚', 'mse': 'MSE', **{f'{q}_{w}': f'{w} nm {q}' for q in ('n', 'k') for w in (193, 248, 633)}}


def _shown(value):
    """Message text only: drop float noise such as 20.744999999999997; basis keeps the raw value."""
    return f'{value:.6g}' if isinstance(value, float) else value


def _reading_label(row):
    rep = row['record'].get('replicate_number')
    rep = int(float(rep)) if _numeric(rep) and float(rep).is_integer() else rep
    return f"第 {rep if rep is not None else '?'} 次（第 {row['source_row']} 行）"


def quality_flags(store, items):
    """Batch capability adapter; one result per project, sample block, or sheet."""
    if not isinstance(items, list):
        raise ValueError('items 必须为列表')
    with store.lock:
        engine = _Engine(store)
        flags = engine.run()
        results = []
        for item in items:
            if not isinstance(item, dict) or not (item.get('project_id') or item.get('sample_block_id') or item.get('workbook_sha256') and item.get('sheet')):
                # One bad item must not fail the whole batch (W12 §1.2).
                results.append(dict(error=dict(code='CAPABILITY_INPUT_INVALID',
                                               message='每项必须指定 project_id、sample_block_id 或 workbook_sha256 与 sheet')))
                continue
            sha, sheet = item.get('workbook_sha256'), item.get('sheet')
            selected = {sid for sid, rows in engine.rows.items() if any(engine.books[r['workbook_id']]['sha256'] == sha and r['record']['source_sheet'] == sheet for r in rows)} if sha else None
            if sha:
                for d in store.conn.execute('SELECT d.canonical_sample_id,d.payload FROM duplicate_blocks d JOIN workbooks w USING(workbook_id) WHERE w.sha256=? AND w.status=\'active\'', (sha,)):
                    if json.loads(d['payload']).get('sheet') == sheet:
                        selected.add(d['canonical_sample_id'])
            results.append(_result([f for f in flags if engine.matches(f, item.get('project_id'), item.get('sample_block_id'), selected)]))
        return results
