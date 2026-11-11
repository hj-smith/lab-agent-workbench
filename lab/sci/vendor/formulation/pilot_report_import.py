"""Import instrument / QC report files (ellipsometer summary, QC report) as pilot test records.

parse_report() only reads the file and proposes records; nothing is stored here. The proposed
records use the same fields the 检测结果 editor writes (thickness: values/statistics, optical:
optical list, viscosity / density: value, particles: counts, metals: elements with value / nd).
"""
import csv
import difflib
import io
import re
import unicodedata
from statistics import mean

from .pilot_calc import thickness_stats

ELEMENTS = set('Li Be B Na Mg Al Si P K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Rb Sr Y Zr Nb Mo Ru Rh Pd Ag Cd In Sn Sb Te Cs Ba La Ce Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Th U'.split())
MEAN_WORDS = ('平均值', '平均', 'average', 'mean', 'avg')


def _norm(value):
    if value is None:
        return ''
    text = unicodedata.normalize('NFKC', str(value)).replace('µ', 'u').replace('μ', 'u')
    return re.sub(r'\s+', ' ', text).strip()


def _num(value):
    """Number from a cell, or None. Text like '0.100 ng/g' gives 0.1; ND and blanks give None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.match(r'^[<>≤≥~]?\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)', _norm(value).replace(',', ''))
    return float(match.group(1)) if match else None


def _is_nd(value):
    return _norm(value).upper() in ('ND', 'N.D.', 'N.D', '未检出')


def _read_sheets(data, filename):
    if str(filename).lower().endswith(('.csv', '.txt')):
        for encoding in ('utf-8-sig', 'gb18030'):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        delimiter = '\t' if text.count('\t') > text.count(',') else ','
        return [(str(filename), [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)])]
    import openpyxl
    book = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    return [(ws.title, [list(row) for row in ws.iter_rows(values_only=True)]) for ws in book.worksheets]


def _find_header(rows, words, limit=15):
    for r, row in enumerate(rows[:limit]):
        if any(_norm(c) in words for c in row):
            return r
    return None


def _round_key(size):
    text = repr(float(size))
    return text[:-2] if text.endswith('.0') else text


def _parse_ellipsometer(title, rows, warnings):
    h = None
    for r, row in enumerate(rows[:15]):
        cells = [_norm(c) for c in row]
        if any(c.startswith('样品') or c.lower() == 'sample' for c in cells) and any(('N/K' in c.upper()) or '膜厚' in c or 'MSE' in c.upper() or c.lower() == 'thickness' for c in cells):
            h = r
            break
    if h is None:
        return []
    head = [_norm(c) for c in rows[h]]
    sub = [_norm(c) for c in rows[h + 1]] if h + 1 < len(rows) else []
    col = {'name': None, 'idx': None, 'thickness': None, 'mse': None}
    waves = {}
    for c, text in enumerate(head):
        low = text.lower()
        wave = re.search(r'(\d+(?:\.\d+)?)\s*nm', low)
        if text.startswith('样品') or low == 'sample':
            col['name'] = c if col['name'] is None else col['name']
        elif '次数' in text or low in ('no', 'no.', 'index', '#'):
            col['idx'] = c
        elif 'mse' in low:
            col['mse'] = c
        elif '膜厚' in text or 'thickness' in low:
            col['thickness'] = c
        elif wave:
            key = float(wave.group(1))
            slot = waves.setdefault(key, {})
            tail = low.replace(wave.group(0), '').strip()
            if 'n/k' in tail:
                slot['n'] = c
                if c + 1 < len(head) and head[c + 1] in ('', 'k'):
                    slot['k'] = c + 1
            elif tail.endswith('k'):
                slot['k'] = c
            else:
                slot['n'] = c
    if h + 1 < len(sub):  # N / K written on a second header row
        for key, slot in waves.items():
            if 'n' in slot and 'k' not in slot and sub[slot['n'] + 1:slot['n'] + 2] == ['k']:
                slot['k'] = slot['n'] + 1
    if col['name'] is None:
        col['name'] = 0
    model = ''
    for row in rows[:h + 1]:
        for cell in row:
            text = _norm(cell)
            if text.startswith('模型'):
                model = text
    samples, current = [], None

    def cell(row, c):
        return _num(row[c]) if c is not None and c < len(row) else None

    start = h + 1
    if sub and any(s in ('n', 'k', 'N', 'K') for s in sub) and not any(_num(c) is not None for c in rows[h + 1]):
        start = h + 2
    for row in rows[start:]:
        if all(c is None or _norm(c) == '' for c in row):
            continue
        name = _norm(row[col['name']]) if col['name'] < len(row) else ''
        mean_row = name.lower() in MEAN_WORDS
        if name and not mean_row:
            current = {'name': name, 'rows': [], 'file_mean': None}
            samples.append(current)
        if current is None:
            continue
        label = _norm(row[col['idx']]).lower() if col['idx'] is not None and col['idx'] < len(row) else ''
        values = {'thickness': cell(row, col['thickness']), 'mse': cell(row, col['mse']),
                  'n': {w: cell(row, s.get('n')) for w, s in waves.items()},
                  'k': {w: cell(row, s.get('k')) for w, s in waves.items()}}
        if mean_row or label in MEAN_WORDS:
            current['file_mean'] = values
            continue
        if values['thickness'] is None and values['mse'] is None and all(v is None for v in values['n'].values()) and all(v is None for v in values['k'].values()):
            continue
        current['rows'].append(values)
    result = []
    for s in samples:
        if not s['rows']:
            warnings.append(f"{s['name']}：没有找到测量数据，已跳过。")
            continue
        result.append(_ellipsometer_sample(s, waves, model, title, warnings))
    if result and not any(r['mse'] is not None for s in samples for r in s['rows']):
        warnings.append(f'工作表「{title}」没有 MSE 数据。')
    return result


def _ellipsometer_sample(s, waves, model, title, warnings):
    rows = s['rows']
    tests, name = [], s['name']
    note = '椭偏仪汇总' + (f'（{model}）' if model else '') + f'；共 {len(rows)} 次测量'
    thickness = [r['thickness'] for r in rows if r['thickness'] is not None]
    if thickness:
        stats = thickness_stats(thickness)
        tests.append({'type': 'thickness', 'values': thickness, 'statistics': stats, 'note': note})
        if len(thickness) < len(rows):
            warnings.append(f'{name}：{len(rows) - len(thickness)} 次测量没有膜厚，统计只用 {len(thickness)} 个点。')
    optical = []
    for wave in sorted(waves):
        ns = [r['n'][wave] for r in rows if r['n'][wave] is not None]
        ks = [r['k'][wave] for r in rows if r['k'][wave] is not None]
        mse = [r['mse'] for r in rows if r['mse'] is not None]
        if not ns and not ks:
            continue
        if ns and not ks:
            warnings.append(f'{name}：{wave:g} nm 没有 K 值。')
        if (ns and len(ns) < len(rows)) or (ks and len(ks) < len(rows)):
            warnings.append(f'{name}：{wave:g} nm 部分测量缺 N 或 K，平均值只用已有的数据。')
        optical.append({'wavelength': wave, 'n': mean(ns) if ns else None, 'k': mean(ks) if ks else None,
                        'thickness': mean(thickness) if thickness else None, 'mse': mean(mse) if mse else None,
                        'n_values': ns, 'k_values': ks})
    if optical:
        tests.append({'type': 'optical', 'optical': optical, 'note': note})
    return {'name': name, 'source': 'ellipsometer', 'sheet': title, 'measurements': len(rows),
            'file_mean': s['file_mean'], 'tests': tests}


def _parse_qc(title, rows, warnings):
    samples, current, lod_text, instrument = [], None, '', ''

    def fresh():
        return {'name': '', 'report_no': '', 'lot': '', 'viscosity': None, 'density': None,
                'counts': {}, 'elements': [], 'lod': None, 'instrument': ''}

    def finish():
        if current is not None and (current['viscosity'] is not None or current['density'] is not None or current['counts'] or current['elements']):
            samples.append(current)

    for row in rows:
        cells = [(c, _norm(v)) for c, v in enumerate(row) if _norm(v) != '']
        if not cells:
            continue
        label = cells[0][1]
        rest = [v for _, v in cells[1:]]
        raws = [row[c] for c, _ in cells[1:]]
        key = label.replace(' ', '')
        if key in ('样品名称', '样品名', '样品'):
            if current is not None and (current['viscosity'] is not None or current['density'] is not None or current['counts'] or current['elements']):
                finish()
                current = None
            current = current or fresh()
            current['name'] = rest[0] if rest else ''
        elif key in ('样品编号', '编号'):
            if current is not None and (current['viscosity'] is not None or current['density'] is not None or current['counts'] or current['elements']):
                finish()
                current = None
            current = current or fresh()
            current['report_no'] = rest[0] if rest else ''
        elif key in ('样品批号', '批号'):
            current = current or fresh()
            current['lot'] = rest[0] if rest else ''
        elif '检出限' in key:
            current = current or fresh()
            current['lod'] = _num(rest[0]) if rest else None
            lod_text = rest[0] if rest else ''
        elif '仪器' in key and ('金杂' in key or '设备' in key):
            current = current or fresh()
            current['instrument'] = rest[0] if rest else ''
        elif key.startswith('项目'):
            continue
        elif ('粘度' in key or 'viscosity' in key.lower()) and rest:
            current = current or fresh()
            current['viscosity'] = next((_num(v) for v in raws if _num(v) is not None), None)
            current['viscosity_unit'] = next((v for v in rest if re.search(r'[A-Za-z]', v) and _num(v) is None), 'cP')
        elif ('密度' in key or 'density' in key.lower()) and rest:
            current = current or fresh()
            current['density'] = next((_num(v) for v in raws if _num(v) is not None), None)
            current['density_unit'] = next((v for v in rest if re.search(r'[A-Za-z]', v) and _num(v) is None), 'g/cm3')
        elif 'particle' in key.lower() or '颗粒' in key:
            size = re.search(r'>\s*(\d*\.?\d+)', key)
            value = next((_num(v) for v in raws if _num(v) is not None), None)
            if size and value is not None:
                current = current or fresh()
                current['counts'][_round_key(size.group(1))] = value
        elif label in ELEMENTS and rest:
            current = current or fresh()
            values = [v for v in raws if v is not None and _norm(v) != '' and not (isinstance(v, str) and re.search(r'/', v) and _num(v) is None)]
            unit_ok = any('/g' in v or 'ppb' in v.lower() for v in rest)
            values = [v for v in values if not (isinstance(v, str) and re.search(r'[A-Za-z]/[A-Za-z]|ppb', v))]
            if not values or not unit_ok and not any(_num(v) is not None or _is_nd(v) for v in values):
                continue
            raw = _num(values[0]) if values else None
            reported = values[1] if len(values) > 1 else values[0]
            nd = _is_nd(reported)
            element = {'element': label, 'value': None if nd else _num(reported), 'nd': nd}
            if len(values) > 1 and raw is not None:
                element['raw_value'] = raw
            if element['value'] is None and not nd:
                continue
            current['elements'].append(element)
    finish()
    result = []
    for s in samples:
        name = s['name'] or s['report_no'] or title
        if not s['name']:
            warnings.append(f'{title}：报告没有样品名称，暂用「{name}」。')
        tests = []
        ref = '报告样品编号 ' + s['report_no'] if s['report_no'] else ''
        ref += ('；' if ref and s['lot'] else '') + ('样品批号 ' + s['lot'] if s['lot'] else '')
        ref = ref or f'来自 {title}'
        if s['viscosity'] is not None:
            tests.append({'type': 'viscosity', 'value': s['viscosity'], 'unit': s.get('viscosity_unit', 'cP'), 'note': ref + '；25℃' if '25' in ''.join(str(r) for r in rows if r and '粘度' in str(r[0])) else ref})
        if s['density'] is not None:
            tests.append({'type': 'density', 'value': s['density'], 'unit': s.get('density_unit', 'g/cm3'), 'note': ref})
        if s['counts']:
            tests.append({'type': 'particles', 'counts': s['counts'], 'unit': 'ea/mL', 'note': ref + '；' + '；'.join(f'>{k}µm {v:g}' for k, v in s['counts'].items()) + ' ea/mL'})
        if s['elements']:
            lod = s['lod']
            elements = []
            for e in s['elements']:
                e = dict(e)
                if lod is not None:
                    e['detection_limit'] = lod
                elements.append(e)
            above = sum(1 for e in elements if not e['nd'])
            note = ref + (f"；仪器 {s['instrument']}" if s['instrument'] else '') + (f'；方法检出限 {lod_text}；ND 为低于检出限' if lod is not None else '；ND 为未检出')
            if lod is None:
                warnings.append(f'{name}：金属杂质没有找到检出限。')
            tests.append({'type': 'metals', 'elements': elements, 'note': note, 'above_lod': above})
        result.append({'name': name, 'source': 'qc', 'sheet': title, 'report_no': s['report_no'], 'lot': s['lot'], 'tests': tests})
    return result


def _detect(rows):
    blob = ' '.join(_norm(c) for row in rows[:60] for c in row if c is not None)
    upper = blob.upper()
    if '椭偏' in blob or ('N/K' in upper and ('MSE' in upper or '膜厚' in blob)) or ('MSE' in upper and '膜厚' in blob):
        return 'ellipsometer'
    low = blob.lower()
    if ('项目' in blob and '检测结果' in blob) or 'ng/g' in low or 'particle' in low or '动力粘度' in blob:
        return 'qc'
    return None


def _summary(sample):
    out = {}
    for t in sample['tests']:
        if t['type'] == 'thickness':
            out['thickness_mean'], out['thickness_3sigma'], out['thickness_n'] = t['statistics']['mean'], t['statistics']['three_sigma'], t['statistics']['n']
        elif t['type'] == 'optical':
            out['optical'] = [{'wavelength': o['wavelength'], 'n': o['n'], 'k': o['k']} for o in t['optical']]
        elif t['type'] in ('viscosity', 'density'):
            out[t['type']] = t['value']
        elif t['type'] == 'particles':
            out['particles'] = t['counts']
        elif t['type'] == 'metals':
            out['metals_total'], out['metals_above_lod'] = len(t['elements']), t['above_lod']
    return out


def parse_report(data, filename=''):
    """Read an xlsx / csv report. Returns {samples, warnings, kinds}; each sample has proposed `tests`."""
    warnings, samples, kinds = [], [], []
    try:
        sheets = _read_sheets(data, filename)
    except Exception:
        return {'samples': [], 'kinds': [], 'warnings': ['这个文件无法读取，请确认是 .xlsx 或 .csv 报告。']}
    for title, rows in sheets:
        if not any(_norm(c) for row in rows for c in row):
            continue
        kind = _detect(rows)
        if kind is None:
            warnings.append(f'工作表「{title}」不像检测报告，已忽略。')
            continue
        kinds.append(kind)
        found = _parse_ellipsometer(title, rows, warnings) if kind == 'ellipsometer' else _parse_qc(title, rows, warnings)
        if not found:
            warnings.append(f'工作表「{title}」没有找到可导入的数据。')
        samples.extend(found)
    merged = {}
    for s in samples:
        key = _norm(s['name']).upper()
        if key in merged:
            merged[key]['tests'].extend(s['tests'])
            merged[key]['source'] += '+' + s['source']
        else:
            merged[key] = s
    out = list(merged.values())
    for s in out:
        s['summary'] = _summary(s)
    return {'samples': out, 'kinds': sorted(set(kinds)), 'warnings': warnings, 'filename': filename}


def _key(text):
    text = _norm(text).upper()
    text = re.sub(r'[-_](R|复测)$', '', text)
    return re.sub(r'[^0-9A-Z一-鿿]', '', text)


def match_sample(name, candidates):
    """Best candidate for a report sample name: {id, code, score} or None.

    candidates: [{id, code, kind}] with kind sample / retained / final (成品)."""
    want, best = _key(name), None
    tail = re.split(r'[-_]', _norm(name).upper())
    for c in candidates:
        code = _key(c['code'])
        score = 0.0
        if want and want == code:
            score = 1.0
        elif want and code and (want in code or code in want):
            score = 0.9
        else:
            ctail = re.split(r'[-_]', _norm(c['code']).upper())
            clean = [t for t in tail if t not in ('R', '复测')]
            if len(clean) > 1 and len(ctail) > 1 and clean[-1] == ctail[-1] and clean[0] == ctail[0]:
                score = 0.8
            elif c['kind'] == 'retained' and ('留样' in name or '尾液' in name):
                score = 0.6
            elif c['kind'] == 'final' and ('成品' in name or 'FINAL' in want):
                score = 0.7
            else:
                score = difflib.SequenceMatcher(None, want, code).ratio() * 0.7
        if score >= 0.5 and (best is None or score > best['score']):
            best = {'id': c['id'], 'code': c['code'], 'score': round(score, 3)}
    return best


def preview_report(store, batch_id, data, filename=''):
    """parse_report plus a proposed match of every report sample to a sample of the batch."""
    from . import pilot_production as pilot
    detail = pilot.get_batch(store, batch_id)
    batch = detail['batch']
    samples = [{**r['data'], **r} for r in detail['records'] if r['kind'] == 'sample' and not r.get('deleted_at')]
    by_id = {s['id']: s for s in samples}
    candidates = [{'id': s['id'], 'code': s.get('sample_code') or '', 'kind': 'sample'} for s in samples]
    for r in detail['retained']:
        source = by_id.get(r.get('sample_id'))
        if source:
            candidates.append({'id': source['id'], 'code': r.get('sample_code') or '', 'kind': 'retained'})
    candidates.append({'id': '', 'code': batch['batch_code'] + '-成品', 'kind': 'final'})
    result = parse_report(data, filename)
    for s in result['samples']:
        s['match'] = match_sample(s['name'], candidates)
    result['candidates'] = [{'id': c['id'], 'code': c['code'], 'kind': c['kind']} for c in candidates if c['kind'] != 'retained'] + \
        [{'id': c['id'], 'code': c['code'] + '（留样）', 'kind': 'retained'} for c in candidates if c['kind'] == 'retained']
    return result
