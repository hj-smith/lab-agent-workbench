"""表格进出：Excel / CSV 读成行，研发库的一类记录写成 Excel。

导入是宽容的：表头在哪一行自己找，列顺序随意，多出来的列变成新字段，缺的列空着，数字样子的字符串转成数。
"""
import csv
import io
import json
import re
import uuid
from pathlib import Path

from . import config, kernel, store

UPLOADS = lambda: config.HOME / '导入'


def _number(value):
    if isinstance(value, str):
        text = value.strip().replace(',', '')
        if re.fullmatch(r'[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?', text):
            number = float(text)
            return int(number) if number.is_integer() and '.' not in text and 'e' not in text.lower() else number
        return value.strip()
    return value


def _grid(path, sheet=None):
    """(工作表名单, 当前表名, 二维表)。"""
    suffix = path.suffix.lower()
    if suffix in ('.xlsx', '.xlsm'):
        from openpyxl import load_workbook
        book = load_workbook(path, data_only=True, read_only=True)
        name = sheet if sheet in book.sheetnames else book.sheetnames[0]
        rows = [list(row) for row in book[name].iter_rows(values_only=True)]
        return book.sheetnames, name, rows
    raw = path.read_bytes()
    for encoding in ('utf-8-sig', 'gb18030', 'latin-1'):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    dialect = csv.Sniffer().sniff(text[:4000], delimiters=',\t;|') if text.strip() else csv.excel
    return [path.stem], path.stem, [row for row in csv.reader(io.StringIO(text), dialect)]


def read(path, sheet=None):
    sheets, name, grid = _grid(Path(path), sheet)
    grid = [[None if cell in ('', None) else cell for cell in row] for row in grid]
    grid = [row for row in grid if any(cell is not None for cell in row)]
    head_at = next((index for index, row in enumerate(grid[:30]) if sum(cell is not None for cell in row) >= max(2, 0.6 * max(len(r) for r in grid[:30]))), 0) if grid else 0
    header = grid[head_at] if grid else []
    columns, seen = [], {}
    for index, cell in enumerate(header):
        label = str(cell).strip() if cell is not None else f'列{index + 1}'
        seen[label] = seen.get(label, 0) + 1
        columns.append(label if seen[label] == 1 else f'{label}_{seen[label]}')
    rows = [[_number(row[index]) if index < len(row) else None for index in range(len(columns))] for row in grid[head_at + 1:]]
    return {'sheets': sheets, 'sheet': name, 'columns': columns, 'rows': rows, 'header_row': head_at + 1}


def _norm(text):
    return re.sub(r'[\s_\-·/()（）\[\]]+', '', str(text).lower())


def preview(library, filename, content):
    token = uuid.uuid4().hex[:10]
    folder = UPLOADS() / token
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / Path(filename).name
    path.write_bytes(content)
    return {'token': token, 'file': path.name, **inspect(library, token)}


def inspect(library, token, sheet=None, kind=None):
    path = next((UPLOADS() / token).iterdir())
    table = read(path, sheet)
    lib = store.open(library)
    kinds = lib.kinds()
    target = next((item for item in kinds if item['name'] == kind), None)
    fields = {_norm(field['key']): field['key'] for field in (target or {}).get('fields', [])}
    for field in (target or {}).get('fields', []):
        fields.setdefault(_norm(field['key'] + (field.get('unit') or '')), field['key'])
    name_guess = next((column for column in table['columns'] if _norm(column) in ('名字', '名称', 'name', '编号', '样品', '样品名', 'id', 'sample')), table['columns'][0] if table['columns'] else '')
    mapping = {column: fields.get(_norm(re.sub(r'[/(（].*$', '', column)), fields.get(_norm(column), re.sub(r'\s*[/(（].*$', '', column).strip() or column))
               for column in table['columns'] if column != name_guess}
    return {**{key: table[key] for key in ('sheets', 'sheet', 'columns', 'header_row')}, 'total': len(table['rows']), 'rows': table['rows'][:15],
            'name_column': name_guess, 'mapping': mapping, 'kinds': [item['name'] for item in kinds]}


def commit(library, token, kind, name_column, mapping, sheet=None, about=''):
    """按人确认的对应关系写进库。mapping: {列名: 字段名}，字段名空着就不要这一列。同名记录只更新给出的字段。"""
    path = next((UPLOADS() / token).iterdir())
    table = read(path, sheet)
    lib = store.open(library)
    if about and not any(item['name'] == kind for item in lib.kinds()):
        lib.define(kind, about)
    file_id = lib.add_file(path, kind, note=f'导入 {kernel.now()}')
    index = {column: number for number, column in enumerate(table['columns'])}
    rows = []
    for row in table['rows']:
        name = row[index[name_column]] if name_column in index else None
        if name is None:
            continue
        item = {'name': str(name), 'files': [file_id]}
        for column, field in mapping.items():
            if field and column in index and row[index[column]] is not None:
                item[field] = row[index[column]]
        rows.append(item)
    saved = lib.save(kind, rows, source=f'导入 {path.name}', by='人')
    return {'saved': len(saved), 'created': sum(1 for item in saved if item.get('created')), 'skipped': len(table['rows']) - len(rows)}


def export(library, kind, ids=None, with_series=False):
    """一类记录写成 Excel：第一张是记录表（待确认的格子标黄），选了记录时每条的曲线各一张表。"""
    import xlsxwriter
    lib = store.open(library)
    rows = lib.rows(kind)
    if ids:
        rows = [row for row in rows if row['id'] in ids]
    meta = next((item for item in lib.kinds() if item['name'] == kind), {'fields': []})
    keys = [field['key'] for field in meta['fields'] if any(not isinstance(row.get(field['key']), (dict, list)) and row.get(field['key']) is not None for row in rows)]
    units = {field['key']: field.get('unit') for field in meta['fields']}
    buffer = io.BytesIO()
    book = xlsxwriter.Workbook(buffer, {'in_memory': True})
    head = book.add_format({'bold': True, 'bg_color': '#EEF0EC', 'border': 1, 'border_color': '#C9CDC4', 'font_name': 'Microsoft YaHei'})
    cell = book.add_format({'font_name': 'Microsoft YaHei'})
    unsure = book.add_format({'font_name': 'Microsoft YaHei', 'bg_color': '#FFF2C2'})
    sheet = book.add_worksheet(kind[:31])
    columns = ['名字'] + [key + (f' / {units[key]}' if units.get(key) else '') for key in keys] + ['备注', '来源', '改动时间']
    sheet.write_row(0, 0, columns, head)
    for number, row in enumerate(rows, 1):
        values = [row['name']] + [row.get(key) for key in keys] + [row.get('note'), row.get('source'), row.get('updated_at')]
        for col, value in enumerate(values):
            fmt = unsure if col and col <= len(keys) and keys[col - 1] in (row.get('unconfirmed') or []) else cell
            sheet.write(number, col, value if isinstance(value, (int, float)) or value is None else str(value), fmt)
    sheet.freeze_panes(1, 1)
    sheet.set_column(0, 0, 26)
    sheet.set_column(1, len(columns), 14)
    if rows:
        sheet.autofilter(0, 0, len(rows), len(columns) - 1)
    if with_series and ids:
        used = set()
        for row in rows[:40]:
            record = lib.get(row['id'], with_series=True)
            for title, table in list(record['series'].items())[:3]:
                name = re.sub(r'[\[\]:*?/\\]', '_', f'{row["name"]}_{title}')[:28]
                while name in used:
                    name = name[:26] + uuid.uuid4().hex[:2]
                used.add(name)
                page = book.add_worksheet(name)
                page.write_row(0, 0, table['columns'], head)
                for number, values in enumerate(table['rows'], 1):
                    page.write_row(number, 0, values)
    book.close()
    return buffer.getvalue()


def preview_file(path, limit=300):
    """产出页上直接看文件：Excel 每个工作表一张表（算好的值），CSV 一张表，Word 按顺序出段落和表。"""
    path = Path(path)
    suffix = path.suffix.lower()
    cell = lambda value: '' if value is None else value if isinstance(value, (int, float)) else str(value)
    if suffix in ('.xlsx', '.xlsm'):
        from openpyxl import load_workbook
        book = load_workbook(path, data_only=True, read_only=True)
        sheets = []
        for name in book.sheetnames:
            if name.startswith('_') or book[name].sheet_state != 'visible':      # 程序用的隐藏表不给人看
                continue
            rows = [[cell(value) for value in row] for row in book[name].iter_rows(values_only=True)]
            while rows and not any(value != '' for value in rows[-1]):
                rows.pop()
            width = max((max((i + 1 for i, value in enumerate(row) if value != ''), default=0) for row in rows), default=0)
            sheets.append({'name': name, 'rows': [row[:width] for row in rows[:limit]], 'total': len(rows)})
        return {'kind': 'sheets', 'sheets': sheets}
    if suffix in ('.csv', '.tsv', '.txt'):
        _, _, rows = _grid(path)
        return {'kind': 'sheets', 'sheets': [{'name': path.stem, 'rows': [[cell(v) for v in row] for row in rows[:limit]], 'total': len(rows)}]}
    if suffix == '.docx':
        from docx import Document
        document, parts = Document(path), []
        body = document.element.body
        for child in body.iterchildren():
            tag = child.tag.rsplit('}', 1)[-1]
            if tag == 'p':
                text = ''.join(node.text or '' for node in child.iter() if node.tag.endswith('}t'))
                style = child.find('.//{*}pStyle')
                level = style.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val', '') if style is not None else ''
                if text.strip():
                    parts.append({'type': 'heading' if 'Heading' in level or '标题' in level else 'text', 'text': text})
            elif tag == 'tbl':
                rows = [[''.join(node.text or '' for node in cell_.iter() if node.tag.endswith('}t')) for cell_ in row.iter('{*}tc')]
                        for row in child.iter('{*}tr')]
                parts.append({'type': 'table', 'rows': rows})
        return {'kind': 'document', 'parts': parts}
    return {'kind': 'none'}
