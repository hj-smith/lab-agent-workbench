"""仪器导入队列：认文件、提议字段和关联，按人改过的预览写库并调用原算法。

preview 不写库。椭偏一块一项，block 保存原解析块号；其它项一文件一项。
commit 以 existing 指定的记录为更新目标；新项按 kind 和 name 同名更新。
"""
import hashlib
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from zipfile import BadZipFile

from lab import store
from lab.sci import aging, ellipsometry as ell, gpc, nk
from lab.sci.vendor.ellipsometer.ellipsometer2.conditions import apply_library_defaults
from lab.sci.vendor.ellipsometer.ellipsometer2.memstore import block_fingerprint, library_values, read_workbooks

KINDS = {'GPC色谱': 'GPC样品', 'GPC标准曲线': 'GPC标准曲线', 'NK谱': 'NK谱',
         '椭偏汇总': '膜', '老化谱': '老化谱', '不认识': ''}
ANALYSIS = ('基线方法', '基线锚点', '基线排除区', '基线参数', '基线算法版本', '积分区间')
READ_ERRORS = (OSError, UnicodeError, ValueError, BadZipFile)


def _read(path, lib=None, selected=None):
    """只调用原读法；返回类型、原解析内容、摘要。"""
    path = Path(path)
    raw = path.read_bytes()
    suffix = path.suffix.casefold()
    if selected == '椭偏汇总' or (selected is None and suffix in {'.xlsx', '.xlsm'}):
        values, _ = library_values(ell.library_values(lib) if lib is not None else None)
        own_series = next(s['series_id'] for s in values['series'] if s['name'] == lib.name) if lib is not None else 'unassigned'
        parsed = read_workbooks([path], values, own_series)
        if parsed['samples'] or parsed['other_series']:
            return '椭偏汇总', parsed, f'{len(parsed["samples"])} 块、{len(parsed["measurements"])} 行读数'
    if selected is None and suffix not in {'.csv', '.txt', '.dat', '.asc', '.pdf', '.xlsx', '.xlsm', '.cdf', '.nc'}:
        return '不认识', None, '没有对应的仪器读法'
    encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    head = raw[:4096].decode(encoding, errors='replace')
    if selected == '老化谱' or (selected is None and (path.name.casefold().endswith('.wvs.csv') or 'SpectralTest' in head)):
        parsed = aging.parse_spectrum(raw.decode('utf-8-sig'))
        if parsed['problem']:
            raise ValueError(parsed['problem'])
        w = parsed['wavelengths']
        return '老化谱', parsed, f'{len(w)} 点，{w[0]:g}–{w[-1]:g} nm'
    if selected == 'NK谱' or (selected is None and re.search(r'(?:Wavelength|波长).*\bn\b.*\bk\b', head, re.I)):
        sample, _ = nk.core.parse_sample_file(path, nk.config(lib) if lib is not None else nk.core.Config())
        w = sample.clean['Wavelength_nm'].tolist()
        return 'NK谱', sample, f'{len(w)} 点，{w[0]:g}–{w[-1]:g} nm'
    parsed = gpc.read_instrument_file(path)
    if parsed['类型'] == '标准曲线':
        return 'GPC标准曲线', parsed, f'{len(parsed["标样点"])} 个标样点，{parsed["次数"]} 次曲线'
    t = parsed['时间 / min']
    return 'GPC色谱', parsed, f'{len(t)} 点，{t[0]:g}–{t[-1]:g} min'


def detect(path):
    """识别五类仪器文件，读不了的原因放在摘要里。"""
    try:
        kind, _, summary = _read(Path(path))
    except READ_ERRORS as error:
        return {'type': '不认识', 'summary': str(error)}
    return {'type': kind, 'summary': summary}


def _spelling(lib, name):
    rules = lib.setting('平台.命名规则')
    value = name.strip().upper().replace('−', '-').replace('–', '-')
    for prefix, aliases in rules['R1 产品前缀归一'].items():
        if prefix == '去掉编号前导零':
            continue
        for alias in aliases:
            value = re.sub(re.escape(alias), prefix, value, flags=re.I)
    if rules['R1 产品前缀归一']['去掉编号前导零']:
        value = re.sub(r'(?<=-)0+(?=\d)', '', value)
    for alias in lib.setting('平台.写法别名')['别名']:
        value = re.sub(re.escape(alias['从']), alias['到'].upper(), value, flags=re.I)
    return value


def _formula(lib, name):
    value = _spelling(lib, name)
    rules = lib.setting('平台.命名规则')
    labels = '|'.join(re.escape(a['到']) for a in lib.setting('平台.写法别名')['别名'] if a['类'] in ('溶剂', '处理'))
    patterns = rules['R2 条件后缀剥离（正则，按顺序）'] + [rf'(?:[-_ ]?(?:{labels})(?:/(?:{labels}))*)$']
    previous = None
    while previous != value:
        previous = value
        for suffix in rules['R6 FP 后缀剥离']:
            value = re.sub(re.escape(suffix) + '$', '', value, flags=re.I)
        for pattern in patterns:
            value = re.sub(pattern, '', value, flags=re.I).strip()
    return value


def _existing(lib, kind, name, sha, block=None):
    rows = lib.rows(kind)
    matches = [r for r in rows if r['name'].casefold() == name.casefold()]
    if block is not None:
        matches = [r for r in rows if r.get('旧id') == block]
    if not matches:
        ids = {r[0] for r in lib.sql('SELECT record_id FROM file_records WHERE file_id=?', (sha[:16],))[1]}
        # 一份工作簿有多块；膜只按块号对应。
        if kind != '膜':
            matches = [r for r in rows if r['id'] in ids]
    notes = []
    if len(matches) > 1:
        notes.append('存在多个同名或同原件记录，暂选最近记录；可改 existing：' + '；'.join(
            f'{r["id"]}（{r["name"]}，{r.get("所属项目", "")}）' for r in matches))
    chosen = max(matches, key=lambda r: (r['updated_at'], r.get('导入时间') or '', r['id'])) if matches else None
    return chosen, notes


def _link(item, relation, row, reason):
    link = {'relation': relation, 'to': row['id'], 'to_name': row['name'], '依据': reason}
    same = [x for x in item['links'] if x['relation'] == relation and x['to'] == row['id']]
    if not same:
        item['links'].append(link)


def _formula_link(lib, item, code):
    if code is None:
        return
    found = [r for r in lib.rows('配方') if _spelling(lib, r['name']) == _spelling(lib, code)]
    if len(found) == 1:
        _link(item, '对应配方', found[0], '配方编号完全对应（含库里已启用的写法别名）')
    elif not found:
        item['notes'].append(f'配方 {code} 在本库没有对应记录')
    else:
        item['notes'].append(f'配方 {code} 对应多条记录，要人选关联')


def _gpc_preview(lib, item, parsed, old):
    fields = item['fields']
    spelling = _spelling(lib, item['name'])
    match = re.match(r'R100-\d+[A-Z]?', spelling)
    project = match[0] if match else None
    if old is None:
        fields.update({'样品名': item['name'], '所属项目': project, '批次': project})
        methods = [r for r in lib.rows('GPC分析模板') if r.get('所属项目') == project]
        if methods:
            method = max(methods, key=lambda r: (r['updated_at'], r.get('修订') or 0, r['id']))
            fields.update({key: method[key] for key in ANALYSIS if key in method})
            if fields.get('基线锚点') and lib.setting('GPC.套模板时锚点贴曲线', True) is not False:     # 锚点贴到这条谱上（和手调时一样），落在峰上的不贴
                fields['基线锚点'], moved = gpc.snap_anchors(parsed['时间 / min'], parsed['信号'], fields['基线锚点'], fields.get('积分区间') or [])
                for move in moved:
                    item['notes'].append(f'模板 {move["从"]} min 的锚点落在这条谱的峰上，挪到附近谷底 {move["到"]} min 再贴')
            fields['分析模板'] = method['name']
            _link(item, '分析模板', method, '同项目最近的分析模板；锚点和区间要在预览中确认')
            item['notes'].append(f'沿用 {method["name"]} 的基线锚点和积分区间，请按本条色谱确认')
        else:
            item['notes'].append(f'{project or "这个样品的项目"} 没有分析模板，待定基线')
        curves = lib.rows('GPC标准曲线')
        own = [r for r in curves if r.get('所属项目') == project]
        if curves:
            curve = max(own if own else curves, key=lambda r: (r.get('导入时间') or '', r['updated_at'], r['id']))
            fields.update({'标准曲线id': curve['id'], '标准曲线': curve['name']})
            _link(item, '标准曲线', curve, '同项目最近标准曲线' if own else '本库最近标准曲线，待确认是否适用')
            if not own:
                item['notes'].append('同项目没有标准曲线，暂用本库最近曲线')
        else:
            item['notes'].append('本库没有 GPC标准曲线')
    fields.update({'原文件sha256': parsed['sha256'], '原文件大小': Path(item['path']).stat().st_size,
                   '导入格式': parsed['格式'], '导入提示': parsed['提示']})
    if old is None:
        fields.update({'原文件名': item['file'], '原路径': item['path']})
    stage = next((r for r in lib.setting('树脂.GPC命名规则')
                  if any(word.upper() in spelling for word in re.split('[、,，]', r['words']))), None)
    if stage:
        fields['取样阶段'] = stage['label']
    target = project
    reason = '样品编号前缀'
    for mapping in lib.setting('平台.人工对应'):
        if mapping['从的模块'] == 'gpc' and target == _spelling(lib, mapping['从']):
            target, reason = _spelling(lib, mapping['到']), mapping['依据']
    samples = [r for r in lib.rows('树脂取样') if any(
        _spelling(lib, str(r[key])) == spelling for key in ('name', '样品名', 'GPC名') if r.get(key))]
    for row in samples:
        _link(item, '对应取样', row, '样品名或 GPC名相同（按库设置统一写法）')
    if len(samples) != 1:
        item['notes'].append(f'对应树脂取样有 {len(samples)} 条，需确认')
    for row in lib.rows('树脂实验'):
        if _spelling(lib, row.get('编号') or row['name']) == target:
            _link(item, '对应实验', row, reason)
    time = re.search(lib.setting('平台.命名规则')['R4 时间取样（只给候选）'], spelling, re.I)
    if time:
        item['notes'].append(f'时间取样候选：{time["base"]}，{time["hours"]} h')
    if not fields.get('基线锚点') or not fields.get('积分区间'):
        fields['导入状态'] = '待定基线'


def _nk_preview(lib, item, sample, old):
    fields = item['fields']
    name = item['name']
    group = nk.core.infer_repeatability_group(name + '.txt')
    round_match = re.search(r'(?:^|-)T(\d+)', group, re.I)
    round_name = 'T' + round_match[1] if round_match else ('REF01' if group.startswith('REF01') else None)
    if old is None:
        fields.update({'谱名': name, '分组': group, '轮次': round_name, '纳入': True, '纳入重复性': True,
                       '文件名': name + Path(item['path']).suffix})
    clean = sample.clean
    fields.update({'内容指纹': hashlib.sha256(Path(item['path']).read_bytes()).hexdigest(), '点数': len(clean),
                   '起始波长': float(clean.iloc[0]['Wavelength_nm']), '终止波长': float(clean.iloc[-1]['Wavelength_nm']),
                   '编码': sample.encoding, '分隔符': sample.delimiter, '波长单位': sample.wavelength_unit})
    for row in lib.rows(nk.GROUP):
        if row['name'] == fields['分组']:
            _link(item, '属于', row, '原 NK 程序的重复编号分组规则；已有组沿用人定分组')
    _formula_link(lib, item, _formula(lib, fields['分组']))
    if sample.qc['overall'] != '通过':
        item['notes'].append(sample.qc['message'])


def _curve_fields(parsed):
    keys = ('次数', '系数', '缩放系数', '有效范围起', '有效范围止', '单调方向', 'R²', 'RMSE', '算法版本')
    points = tuple(gpc.CalibrationPoint(float(t), float(m), bool(on)) for t, m, on in parsed['标样点'])
    fitted = gpc.fit_polynomial(points, parsed['次数'], parsed['sha256'])
    return {**{key: parsed[key] for key in keys}, '模型': 'polynomial', '来源sha256': parsed['sha256'],
            '来源文件名': parsed['原文件名'], '证据类型': 'imported_pdf', '拟合统计': asdict(fitted.statistics)}


def _film_fingerprints(lib):
    found = {}
    for film in lib.rows(ell.KIND):
        columns, rows, _ = lib.table(film['id'], '读数')
        index = {label: i for i, label in enumerate(columns)}
        payloads = [{**row[index['其它']], **{key: row[index[label]] for key, label in ell.READING_COLUMNS}}
                    for row in rows]
        found[block_fingerprint(payloads)] = film
    return found


def _ell_items(lib, path, parsed, sha):
    values, _ = library_values(ell.library_values(lib))
    aliases = lib.setting('椭偏.配方别名规则')
    references = {code for s in values['series'] for code in s['reference_samples']}
    titles = Counter(f'{s["sample_code"]} · {s["test_date"]}' for s in parsed['samples'].values())
    seen = Counter()
    items, fingerprints = [], None
    for order, (sid, sample) in enumerate(parsed['samples'].items(), 1):
        measurements = [m for m in parsed['measurements'] if m['sample_id'] == sid]
        sheet = parsed['sheets'][measurements[0]['sheet_id']]
        book = next(b for b in parsed['books'] if b['workbook_id'] == measurements[0]['workbook_id'])
        code = sample['sample_code']
        alias = next((r for r in aliases if (r['方式'] == 'exact' and r['样品编号'] == code)
                      or (r['方式'] == 'regex' and re.search(r['样品编号'], code))), None)
        formula = alias['配方编号'] if alias else _formula(lib, code)
        matched = [r for r in lib.rows('配方') if _spelling(lib, r['name']) == formula]
        if code in references:
            info = {'status': 'reference', 'formulation_code': None, 'reason': '系列标准参比 ' + code}
        elif alias:
            info = {'status': 'rule', 'formulation_code': formula, 'reason': '库设置的配方别名规则'}
        elif len(matched) == 1:
            info = {'status': 'auto', 'formulation_code': formula, 'reason': '同编号配方'}
        else:
            info = {'status': 'pending', 'formulation_code': None, 'reason': '未找到唯一的同编号配方'}
        title = f'{code} · {sample["test_date"]}'
        seen[title] += 1
        name = f'{title} #{seen[title]}' if titles[title] > 1 else title
        old, notes = _existing(lib, '膜', name, sha, block=sid)
        duplicate = False
        if old is None:
            if fingerprints is None:
                fingerprints = _film_fingerprints(lib)
            fingerprint = block_fingerprint([m['payload'] for m in measurements])
            if fingerprint in fingerprints:
                old = fingerprints[fingerprint]
                duplicate = True
                notes.append('与已有块的原程序指纹相同，沿用已有块及读数；本工作簿作为另一个原件挂上')
            elif any(r['name'] == name for r in lib.rows(ell.KIND)):
                name += ' · ' + sid[-8:]
        fields = ell.film_fields(apply_library_defaults(sample, values), info, sheet, book)
        fields['导入顺序'] = order
        item = _item(path, '椭偏汇总', name, old, notes, lib)
        # 已有块的人定条件、配方对应、剔除和导入顺序继续沿用。
        item['fields'] = {**fields, **item['fields']}
        item['block'] = sid
        item['duplicate'] = duplicate
        if old is None and info['status'] == 'pending':
            item['notes'].append(info['reason'])
        if fields['处理待确认']:
            item['notes'].append('处理名称需要人确认')
        for day in lib.rows(ell.DAY):
            if day['测试日'] == item['fields']['测试日']:
                _link(item, '测于', day, '工作表解析的测试日')
        _formula_link(lib, item, item['fields']['配方编号'])
        items.append(item)
    return items


def _item(path, kind, name, old, notes, lib):
    record = lib.get(old['id']) if old is not None else None
    return {'file': path.name, 'path': str(path.resolve()), 'type': kind, 'kind': KINDS[kind],
            'name': old['name'] if old is not None else name,
            'fields': dict(record['data']) if record is not None else {},
            'existing': old['id'] if old is not None else None,
            'links': [{'relation': x['relation'], 'to': x['to'], 'to_name': x['to_name'], '依据': x['note']}
                      for x in record['links']] if record is not None else [],
            'notes': notes, 'problem': None}


def preview(lib, paths):
    """逐文件预览；椭偏工作簿逐块展开。返回值可以直接序列化为 JSON 并交给人编辑。"""
    items = []
    for source in paths:
        path = Path(source)
        try:
            kind, parsed, summary = _read(path, lib)
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
        except READ_ERRORS as error:
            item = _item(path, '不认识', path.stem, None, [], lib)
            item['problem'] = str(error)
            items.append(item)
            continue
        if kind == '椭偏汇总':
            blocks = _ell_items(lib, path, parsed, sha)
            if not blocks:
                item = _item(path, kind, path.stem, None, [], lib)
                item['problem'] = '工作簿没有属于本库系列的样品块'
                blocks.append(item)
            items.extend(blocks)
            continue
        if kind == '不认识':
            item = _item(path, kind, path.stem, None, ['请填写 type、kind、fields 并清除 problem 后导入'], lib)
            item['problem'] = summary
            items.append(item)
            continue
        name = re.sub(r'\.WVS\.csv$', '', path.name, flags=re.I) if kind == '老化谱' else path.stem
        if kind == 'NK谱':
            name = re.sub('-' + sha[:12] + '$', '', name, flags=re.I)
        if kind == '老化谱':
            name = re.sub('^' + sha + '-', '', name, flags=re.I)
        old, notes = _existing(lib, KINDS[kind], name, sha)
        item = _item(path, kind, name, old, notes, lib)
        if kind == 'GPC色谱':
            _gpc_preview(lib, item, parsed, old)
        elif kind == 'GPC标准曲线':
            if old is None or item['fields'].get('来源sha256') != parsed['sha256']:
                item['fields'].update(_curve_fields(parsed))
            else:
                item['notes'].append('原件指纹相同，沿用库里已保存的标准曲线系数和拟合统计')
        elif kind == 'NK谱':
            _nk_preview(lib, item, parsed, old)
        elif kind == '老化谱':
            n = aging.parse_name(item['name'] + '.WVS.csv', lib.setting('老化.文件名规则'))
            fields = {'样品': n['sample'], '种类': n['kind'], '条件': n['condition'], '天数': n['day'],
                      '小时': n['hours'], '时间标签': n['timeLabel'], '测试日': aging.folder_date(str(path.parent)),
                      '原文件名': path.name, '原路径': str(path.resolve()), '文件夹': path.parent.name,
                      '指纹': sha, '仪器表头': parsed['header'], '导入说明': '；'.join(n['notes'])}
            item['fields'] = {**fields, **item['fields']}
            item['notes'].extend(n['notes'])
            _formula_link(lib, item, _formula(lib, item['fields']['样品']))
        items.append(item)
    return items


def _series(lib, item, parsed, file_id):
    kind = item['type']
    if kind == 'GPC色谱':
        return {'色谱': {'columns': ['时间 / min', '信号'], 'rows': list(zip(parsed['时间 / min'], parsed['信号'])),
                         'meta': {'点数': parsed['点数'], '单位': {'时间 / min': 'min'}}}}
    if kind == 'GPC标准曲线':
        return {'标样点': {'columns': ['保留时间 / min', '分子量 / g/mol', '启用'], 'rows': parsed['标样点']}}
    if kind == 'NK谱':
        return {'nk': {'columns': nk.COLUMNS, 'rows': parsed.clean[['Wavelength_nm', 'n', 'k']].to_numpy(dtype=float).tolist(),
                       'meta': {'单位': {'波长 / nm': 'nm'}}}}
    if kind == '老化谱':
        return {'谱': {'columns': ['波长 / nm', '吸光度'], 'rows': list(zip(parsed['wavelengths'], parsed['absorbance']))}}
    if kind == '椭偏汇总':
        if item['duplicate']:
            return {'读数': lib.get(item['existing'], with_series=True)['series']['读数']}
        measurements = [m for m in parsed['measurements'] if m['sample_id'] == item['block']]
        first = measurements[0]
        book = next(b for b in parsed['books'] if b['workbook_id'] == first['workbook_id'])
        sheet = parsed['sheets'][first['sheet_id']]
        columns, rows = ell.reading_table(measurements)
        return {'读数': {'columns': columns, 'rows': rows, 'meta': {
            '单位': {'膜厚 / nm': 'nm'}, 'workbook_id': book['workbook_id'], 'sha256': book['sha256'],
            'file_name': book['file_name'], 'file_id': file_id, 'sheet_id': sheet['sheet_id'],
            'sheet': sheet['name'], 'field_mapping': sheet['field_mapping']}}}


def _memberships(lib, item):
    """创建缺少的分组、轮次和测试日，关联采用人编辑后的字段。"""
    fields = item['fields']
    links = list(item['links'])
    if item['type'] == 'NK谱':
        group_row = {'name': fields['分组'], '分组': fields['分组'], '轮次': fields['轮次']}
        if fields['轮次'] is not None:
            round_id = lib.save(nk.ROUND, [{'name': fields['轮次'], '轮次': fields['轮次']}])[0]['id']
            group_row['links'] = [{'relation': '属于', 'to': round_id}]
        group_id = lib.save(nk.GROUP, [group_row])[0]['id']
        links = [x for x in links if x['relation'] != '属于']
        links.append({'relation': '属于', 'to': group_id, '依据': '人确认的分组和轮次'})
    elif item['type'] == '椭偏汇总':
        date = fields['测试日']
        days = [r for r in lib.rows(ell.DAY) if r['测试日'] == date]
        day = {'name': f'椭偏 {date}', '测试日': date, '系列': fields['系列']}
        if days:
            day['id'] = days[0]['id']
        day_id = lib.save(ell.DAY, [day])[0]['id']
        links = [x for x in links if x['relation'] != '测于']
        links.append({'relation': '测于', 'to': day_id, '依据': '人确认的测试日'})
    elif item['type'] == 'GPC色谱':
        links = [x for x in links if x['relation'] != '标准曲线']
        if fields.get('标准曲线id'):
            links.append({'relation': '标准曲线', 'to': fields['标准曲线id'], '依据': '人确认的标准曲线'})
    return links


def _save_item(lib, item, parsed):
    file_id = lib.add_file(item['path'], kind=item['type'])
    links = _memberships(lib, item)
    row = {'name': item['name'], **item['fields'], 'files': [file_id], 'series': _series(lib, item, parsed, file_id)}
    if item['existing'] is not None:
        row['id'] = item['existing']
    saved = lib.save(item['kind'], [row], source=f'导入 {item["file"]}', by='人')[0]
    if item['type'] == 'NK谱':
        # NK 原算法会重读第一个附件；换原件时把旧谱附件的挂接替换掉。
        with lib.connect() as db:
            db.execute('DELETE FROM file_records WHERE record_id=? AND file_id<>? '
                       'AND file_id IN (SELECT id FROM files WHERE kind=?)', (saved['id'], file_id, 'NK谱'))
    # 预览里带着原有关联；人删掉或改掉的关联在这里同步。
    for old in lib.get(saved['id'])['links']:
        if not any(x['relation'] == old['relation'] and x['to'] == old['to'] for x in links):
            lib.link(saved['id'], old['relation'], old['to'], remove=True)
    for link in links:
        lib.link(saved['id'], link['relation'], link['to'], note=link['依据'], by='人')
    return saved


def commit(lib, items):
    """按编辑后的 items 导入，每项返回文件、记录 id、新建与否、计算与否和具体问题。"""
    results, saved_items, parsed_files = [], [], {}
    for item in items:
        result = {'file': item['file'], 'id': None, 'created': False, 'computed': False, 'problem': item['problem']}
        results.append(result)
        if item['problem'] or item['type'] == '不认识':
            result['problem'] = item['problem'] or '不认识文件类型'
            continue
        try:
            key = (item['path'], item['type'])
            if key not in parsed_files:
                _, parsed_files[key], _ = _read(Path(item['path']), lib, selected=item['type'])
            saved = _save_item(lib, item, parsed_files[key])
            result.update(id=saved['id'], created=saved['created'])
            saved_items.append((item, result))
        except READ_ERRORS + (store.StoreError,) as error:
            result['problem'] = str(error)
    for item, result in saved_items:
        if item['type'] == 'GPC色谱':
            fields = lib.get(result['id'])['data']
            with lib.connect() as db:
                db.execute('DELETE FROM results WHERE record_id=? AND method IN (?,?)',
                           (result['id'], 'GPC分子量', 'GPC分布'))
            if not fields.get('基线锚点') or not fields.get('积分区间'):
                lib.save('GPC样品', [{'id': result['id'], '导入状态': '待定基线'}])
                result['problem'] = '待定基线'
                continue
            try:
                gpc.analyze(result['id'], lib, save=True)
                gpc.distribution(result['id'], lib, save=True)
                lib.save('GPC样品', [{'id': result['id'], '导入状态': '已计算'}])
                result['computed'] = True
            except (ValueError, store.StoreError) as error:
                result['problem'] = str(error)
    for kind, compute in [('NK谱', nk.compute_all), ('椭偏汇总', ell.compute_all), ('老化谱', aging.compute_all)]:
        selected = [(item, result) for item, result in saved_items if item['type'] == kind]
        if not selected:
            continue
        if kind == 'NK谱':
            counts = Counter(r['分组'] for r in lib.rows(nk.SPECTRUM))
            lib.save(nk.GROUP, [{'id': r['id'], '谱数': counts[r['name']]} for r in lib.rows(nk.GROUP)])
            with lib.connect() as db:
                db.execute('DELETE FROM results WHERE method IN (?,?)', ('NK.组均值', 'NK.配对差值'))
        elif kind == '椭偏汇总':
            films = lib.rows(ell.KIND)
            lib.save(ell.DAY, [{'id': day['id'], '膜数': sum(r['测试日'] == day['测试日'] for r in films),
                               '工作簿': sorted({r['工作簿'] for r in films if r['测试日'] == day['测试日']})}
                              for day in lib.rows(ell.DAY)])
        try:
            compute(lib)
            for _, result in selected:
                result['computed'] = True
        except (ValueError, store.StoreError) as error:
            for _, result in selected:
                result['problem'] = str(error)
    return results
