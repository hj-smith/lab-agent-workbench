"""Portable XLSX work orders and actual-weight import.

The small style templates are authored separately. Runtime processing uses only
the Python standard library: ZIP/XML, never Excel automation, macros or links.
Plans are values. The import returns visible identities and plans for comparison
with the application's frozen database; workbook metadata is not proof of truth.
"""
from __future__ import annotations

from copy import deepcopy
import io
import math
from pathlib import Path
import posixpath
import re
import sys
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from .export_format import NUMBER_FORMAT, PERCENT_FORMAT
from .beijing_time import format_date
from .export_privacy import collect_name_codes, redact_export_text, product_export_context
from .preparation_actuals import PREMIX_STOCK_REMINDER
from .dispensing import TABLE_HEADER, instruction_table

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
NS = {'s': S, 'r': R}
ET.register_namespace('', S)
ET.register_namespace('r', R)
ROOT = Path(__file__).parent
TEMPLATE_DIR = ROOT / 'data' / 'templates'
META_NAME = '_FORMULA_META'
PREP_META_NAME = '_PREPARATION_META'
SIGNATURE = 'FORMULATION_WORKBENCH_EXCEL'
LEGACY_SIGNATURE = 'PA_EXCEL'
VERSION = 1
META_HEADERS = ['record_type', 'batch_id', 'batch_code', 'formula_code',
                'line_id', 'source_id', 'planned_mass_g', 'visible_sheet',
                'batch_code_cell', 'formula_code_cell', 'operator_cell', 'note_cell',
                'source_id_cell', 'planned_mass_cell', 'actual_mass_cell', 'review_cell']
PREP_META_HEADERS = ['record_type', 'preparation_id', 'group_key', 'component_id',
                     'source_id', 'visible_sheet', 'source_id_cell', 'actual_mass_cell',
                     'review_cell', 'actual_yield_cell', 'operator_cell', 'note_cell']
CELL_RE = re.compile(r'^[A-Z]{1,3}[1-9][0-9]{0,6}$')


def _q(name):
    return '{' + S + '}' + name


def _xml(element):
    return ET.tostring(element, encoding='utf-8', xml_declaration=True)


def _content_types_xml(element):
    """Use unprefixed OPC names for System.IO.Packaging/OpenXML consumers.

    Keep this local: changing ElementTree's global default namespace would also
    change worksheet/style serialization in simultaneous workbook exports.
    """
    plain = deepcopy(element)
    prefix = '{' + CT + '}'
    for node in plain.iter():
        if isinstance(node.tag, str) and node.tag.startswith(prefix):
            node.tag = node.tag[len(prefix):]
    plain.set('xmlns', CT)
    return _xml(plain)


def _col(index):
    text = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        text = chr(65 + remainder) + text
    return text


def _address(row, col):
    return _col(col) + str(row)


def _row_ref(value):
    """Normalize a repeat-row spec such as '1:2' or (1, 2) to an absolute ref."""
    if isinstance(value, str):
        first, _, last = value.strip().replace('$', '').partition(':')
        return '$' + first + ':$' + (last or first)
    first, last = value
    return '$' + str(first) + ':$' + str(last)


def _finite(value, label, *, blank=False, nonnegative=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if blank:
            return None
        raise ValueError(label + '不能为空。')
    if isinstance(value, bool):
        raise ValueError(label + '必须是数值。')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(label + '必须是数值。') from None
    if not math.isfinite(result):
        raise ValueError(label + '必须是有限数值。')
    if nonnegative and result < 0:
        raise ValueError(label + '不能为负数。')
    return result


def _text(value):
    return '' if value is None else str(value)


def _clean_xml_text(value):
    text = _text(value)
    if any(ord(c) < 32 and c not in '\t\r\n' for c in text):
        raise ValueError('工单文字包含不支持的控制字符。')
    return text


class _Sheet:
    def __init__(self, name, widths, *, landscape=False, protected=True, hidden=False,
                 fit_height=None, title_rows=None):
        self.name = name
        self.widths = widths
        self.landscape = landscape
        self.protected = protected
        self.hidden = hidden
        # fit_height=None keeps the old one-page default; 0 lets paper height
        # paginate the table while fitToWidth still fits the sheet across.
        self.fit_height = fit_height
        # Excel print titles, e.g. '1:2' repeats those rows at the top of every
        # printed page.  It is a workbook-level defined name, emitted in bytes().
        self.title_rows = title_rows
        self.cells = {}
        self.heights = {}
        self.merges = []
        self.last_row = 1
        self.print_context = ''
        self.page_breaks = []

    def put(self, row, col, value=None, style=0):
        self.cells[(row, col)] = (value, style)
        self.last_row = max(self.last_row, row)
        return _address(row, col)

    def merge(self, row, first, last, value, style=0):
        for col in range(first, last + 1):
            self.put(row, col, value if col == first else None, style)
        self.merges.append(_address(row, first) + ':' + _address(row, last))
        return _address(row, first)

    def height(self, row, value):
        self.heights[row] = max(self.heights.get(row, 0), value)

    def xml(self):
        root = ET.Element(_q('worksheet'))
        props = ET.SubElement(root, _q('sheetPr'))
        ET.SubElement(props, _q('pageSetUpPr'), {'fitToPage': '1'})
        ET.SubElement(root, _q('dimension'), {'ref': 'A1:' + _address(self.last_row, len(self.widths))})
        views = ET.SubElement(root, _q('sheetViews'))
        ET.SubElement(views, _q('sheetView'), {'workbookViewId': '0', 'showGridLines': '0', 'zoomScale': '85'})
        ET.SubElement(root, _q('sheetFormatPr'), {'defaultRowHeight': '20'})
        cols = ET.SubElement(root, _q('cols'))
        for i, width in enumerate(self.widths, 1):
            ET.SubElement(cols, _q('col'), {'min': str(i), 'max': str(i), 'width': str(width), 'customWidth': '1'})
        data = ET.SubElement(root, _q('sheetData'))
        rows = {}
        for (row, col), (value, style) in sorted(self.cells.items()):
            if row not in rows:
                attrs = {'r': str(row)}
                if row in self.heights:
                    attrs.update(ht=str(self.heights[row]), customHeight='1')
                rows[row] = ET.SubElement(data, _q('row'), attrs)
            c = ET.SubElement(rows[row], _q('c'), {'r': _address(row, col), 's': str(style)})
            if value is None:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                number = _finite(value, '导出数值')
                ET.SubElement(c, _q('v')).text = repr(number)
            else:
                c.set('t', 'inlineStr')
                text = ET.SubElement(ET.SubElement(c, _q('is')), _q('t'), {'{http://www.w3.org/XML/1998/namespace}space': 'preserve'})
                text.text = _clean_xml_text(value)
        if self.protected:
            ET.SubElement(root, _q('sheetProtection'), {'sheet': '1', 'objects': '1', 'scenarios': '1',
                                                       'selectLockedCells': '0', 'selectUnlockedCells': '0'})
        if self.merges:
            merged = ET.SubElement(root, _q('mergeCells'), {'count': str(len(self.merges))})
            for ref in self.merges:
                ET.SubElement(merged, _q('mergeCell'), {'ref': ref})
        ET.SubElement(root, _q('printOptions'), {'horizontalCentered': '1'})
        ET.SubElement(root, _q('pageMargins'), {'left': '0.23', 'right': '0.23', 'top': '0.28',
                                              'bottom': '0.3', 'header': '0.08', 'footer': '0.13'})
        ET.SubElement(root, _q('pageSetup'), {'paperSize': '9', 'orientation': 'landscape' if self.landscape else 'portrait',
                                            'fitToWidth': '1', 'fitToHeight': str(
                                                self.fit_height if self.fit_height is not None
                                                else (0 if self.page_breaks else 1))})
        footer = ET.SubElement(ET.SubElement(root, _q('headerFooter')), _q('oddFooter'))
        context = self.print_context.replace('&', '&&')
        footer.text = ('&L&7' + context[:150] if context else '') + '&R' + self.name + '    第 &P 页 / 共 &N 页'
        if self.page_breaks:
            breaks = ET.SubElement(root, _q('rowBreaks'), {'count': str(len(self.page_breaks)), 'manualBreakCount': str(len(self.page_breaks))})
            for row in self.page_breaks:
                ET.SubElement(breaks, _q('brk'), {'id': str(row), 'min': '0', 'max': '16383', 'man': '1'})
        return _xml(root)


def _remap_style(node, mappings):
    for field, group in (('fontId', 'fonts'), ('fillId', 'fills'), ('borderId', 'borders'),
                         ('numFmtId', 'numFmts'), ('xfId', 'cellStyleXfs')):
        if field in node.attrib:
            old = int(node.get(field))
            node.set(field, str(mappings.get(group, {}).get(old, old)))


class _Book:
    def __init__(self, template):
        path = TEMPLATE_DIR / template
        if not path.is_file():
            raise ValueError('Excel 导出模板缺失，请重新安装完整的工作台。')
        with zipfile.ZipFile(path) as archive:
            self.parts = {name: archive.read(name) for name in archive.namelist()}
        self.styles = ET.fromstring(self.parts['xl/styles.xml'])
        for fmt in self.styles.findall('s:numFmts/s:numFmt', NS):
            code = fmt.get('formatCode', '')
            if re.fullmatch(r'(?:#,##)?0\.[0#]+%?', code):
                fmt.set('formatCode', PERCENT_FORMAT if code.endswith('%') else NUMBER_FORMAT)
        self.xfs = self.styles.find(_q('cellXfs'))
        source = ET.fromstring(self.parts['xl/worksheets/sheet1.xml'])
        self.cell_styles = {cell.get('r'): int(cell.get('s', '0')) for cell in source.findall('.//s:sheetData/s:row/s:c', NS)}
        self.style_cache = {}
        self.sheets = []

    def style(self, ref, *, unlocked=False, shrink=False, emphasis=None, wrap=False):
        base = self.cell_styles.get(ref, 0) if isinstance(ref, str) else ref
        if not unlocked and not shrink and emphasis is None and not wrap:
            return base
        key = (base, unlocked, shrink, emphasis, wrap)
        if key in self.style_cache:
            return self.style_cache[key]
        xf = deepcopy(self.xfs[base])
        if emphasis:
            fonts = self.styles.find(_q('fonts'))
            font = deepcopy(fonts[int(xf.get('fontId', '0'))])
            for tag in ('b', 'color'):
                node = font.find(_q(tag))
                if node is not None:
                    font.remove(node)
            if emphasis == 'primary':
                ET.SubElement(font, _q('b'))
            ET.SubElement(font, _q('color'), {'rgb': 'FF163444' if emphasis == 'primary' else 'FF536570'})
            xf.set('fontId', str(len(fonts)))
            xf.set('applyFont', '1')
            fonts.append(font)
            fonts.set('count', str(len(fonts)))
        if unlocked:
            protection = xf.find(_q('protection'))
            if protection is None:
                protection = ET.SubElement(xf, _q('protection'))
            protection.set('locked', '0')
            xf.set('applyProtection', '1')
        if shrink or wrap:
            alignment = xf.find(_q('alignment'))
            if alignment is None:
                alignment = ET.SubElement(xf, _q('alignment'))
            if wrap:
                alignment.attrib.pop('shrinkToFit', None)
                alignment.set('wrapText', '1')
            else:
                alignment.set('shrinkToFit', '1')
            alignment.set('vertical', 'center')
            xf.set('applyAlignment', '1')
        result = len(self.xfs)
        self.xfs.append(xf)
        self.xfs.set('count', str(len(self.xfs)))
        self.style_cache[key] = result
        return result

    def template_styles(self, template):
        """Import a second template's style records, remapping every ID.

        Cells are authored once into this workbook; no worksheet is copied with
        stale font, border, fill or number-format references.
        """
        source = _Book(template)
        maps = {}
        for name in ('numFmts', 'fonts', 'fills', 'borders', 'cellStyleXfs'):
            old = source.styles.find(_q(name))
            target = self.styles.find(_q(name))
            if target is None:
                target = ET.Element(_q(name), {'count': '0'})
                self.styles.insert(0, target)
            mapping = {}
            for index, node in enumerate(old if old is not None else []):
                copied = deepcopy(node)
                if name == 'numFmts':
                    key = int(node.get('numFmtId'))
                    existing = next((n for n in target if n.get('formatCode') == node.get('formatCode')), None)
                    if existing is not None:
                        mapping[key] = int(existing.get('numFmtId'))
                        continue
                    new = max([163] + [int(n.get('numFmtId')) for n in target]) + 1
                    copied.set('numFmtId', str(new))
                else:
                    key, new = index, len(target)
                    if name == 'cellStyleXfs':
                        _remap_style(copied, maps)
                target.append(copied)
                mapping[key] = new
            target.set('count', str(len(target)))
            maps[name] = mapping
        imported = {}
        for index, node in enumerate(source.xfs):
            copied = deepcopy(node)
            _remap_style(copied, maps)
            imported[index] = len(self.xfs)
            self.xfs.append(copied)
        self.xfs.set('count', str(len(self.xfs)))
        refs = {ref: imported[index] for ref, index in source.cell_styles.items()}
        return lambda ref, **options: self.style(refs.get(ref, imported[0]), **options)

    def add(self, sheet):
        self.sheets.append(sheet)
        return sheet

    def bytes(self):
        parts = {key: value for key, value in self.parts.items()
                 if not key.startswith('xl/worksheets/') and key != 'xl/calcChain.xml'}
        workbook = ET.Element(_q('workbook'))
        views = ET.SubElement(workbook, _q('bookViews'))
        ET.SubElement(views, _q('workbookView'), {'activeTab': '0'})
        sheets = ET.SubElement(workbook, _q('sheets'))
        definitions = ET.SubElement(workbook, _q('definedNames'))
        rels = ET.fromstring(parts['xl/_rels/workbook.xml.rels'])
        for rel in list(rels):
            if rel.get('Type', '').endswith(('/worksheet', '/calcChain')):
                rels.remove(rel)
        types = ET.fromstring(parts['[Content_Types].xml'])
        for node in list(types):
            if node.get('PartName', '').startswith('/xl/worksheets/') or node.get('PartName') == '/xl/calcChain.xml':
                types.remove(node)
        for i, sheet in enumerate(self.sheets, 1):
            rid = 'rIdPage' + str(i)
            attrs = {'name': sheet.name, 'sheetId': str(i), '{' + R + '}id': rid}
            if sheet.hidden:
                attrs['state'] = 'veryHidden'
            ET.SubElement(sheets, _q('sheet'), attrs)
            part = 'xl/worksheets/sheet' + str(i) + '.xml'
            parts[part] = sheet.xml()
            ET.SubElement(rels, '{' + P + '}Relationship', {'Id': rid, 'Type': R + '/worksheet', 'Target': 'worksheets/sheet' + str(i) + '.xml'})
            ET.SubElement(types, '{' + CT + '}Override', {'PartName': '/' + part,
                'ContentType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'})
            if not sheet.hidden:
                quoted = "'" + sheet.name.replace("'", "''") + "'"
                defined = ET.SubElement(definitions, _q('definedName'), {'name': '_xlnm.Print_Area', 'localSheetId': str(i - 1)})
                defined.text = quoted + '!$A$1:$' + _col(len(sheet.widths)) + '$' + str(sheet.last_row)
                if sheet.title_rows:
                    titles = ET.SubElement(definitions, _q('definedName'), {'name': '_xlnm.Print_Titles', 'localSheetId': str(i - 1)})
                    titles.text = quoted + '!' + _row_ref(sheet.title_rows)
        ET.SubElement(workbook, _q('calcPr'), {'calcId': '191029', 'fullCalcOnLoad': '0'})
        parts['xl/workbook.xml'] = _xml(workbook)
        parts['xl/_rels/workbook.xml.rels'] = _xml(rels)
        parts['[Content_Types].xml'] = _content_types_xml(types)
        parts['xl/styles.xml'] = _xml(self.styles)
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in parts.items():
                archive.writestr(name, value)
        return output.getvalue()


def _export_product(product):
    if product is None:
        return None
    if not isinstance(product, dict) or any(not isinstance(product.get(key), str) or not product[key].strip() for key in ('id', 'name')):
        raise ValueError('导出产品必须有完整的产品标识和名称。')
    return {'id': product['id'].strip(), 'name': product['name'].strip()}


def _combine_pages(book, pages, name, records):
    """Keep printable pages together on one tab and shift import cell addresses."""
    if not pages:
        return
    combined = _Sheet(name, pages[0].widths, landscape=pages[0].landscape)
    for page in pages:
        offset = combined.last_row if combined.cells else 0
        if offset:
            combined.page_breaks.append(offset)
        def shifted(address):
            return re.sub(r'\d+', lambda match: str(int(match.group()) + offset), address)
        for (row, col), (value, style) in page.cells.items():
            combined.put(row + offset, col, value, style)
        for row, height in page.heights.items():
            combined.height(row + offset, height)
        combined.merges.extend(shifted(address) for address in page.merges)
        for record in records:
            if record.get('visible_sheet') != page.name:
                continue
            record['visible_sheet'] = name
            for field, address in list(record.items()):
                if field.endswith('_cell') and address:
                    record[field] = shifted(address)
    first = book.sheets.index(pages[0])
    book.sheets = [sheet for sheet in book.sheets if sheet not in pages]
    book.sheets.insert(first, combined)


def _private_workbook(book, batches=(), preparations=(), product=None, project=None):
    """Remove descriptive names while retaining the exact source IDs for weighing."""
    names = collect_name_codes([*batches, *preparations])
    source_ids = set(names.values())
    for batch in batches:
        source_ids.update(batch[field] for field in ('batch_code', 'formula_code')
                          if isinstance(batch.get(field), str))
    for prep in preparations:
        if isinstance(prep.get('code'), str):
            source_ids.add(prep['code'])
        for group in prep.get('groups', []):
            source_ids.update(code for code in group.get('batch_codes', []) if isinstance(code, str))
    by_name = {sheet.name: sheet for sheet in book.sheets}
    for sheet in book.sheets:
        source_col = 6 if sheet.name == META_NAME else 5 if sheet.name == PREP_META_NAME else None
        if source_col:
            source_ids.update(value for (row, col), (value, _) in sheet.cells.items()
                              if row >= 3 and col == source_col and isinstance(value, str))
            sheet_col = 8 if sheet.name == META_NAME else 6
            person_cols = (11, 16) if sheet.name == META_NAME else (11, 9)
            for row in {r for r, _ in sheet.cells if r >= 3}:
                target = by_name.get(sheet.cells.get((row, sheet_col), (None,))[0])
                if not target:
                    continue
                for col in person_cols:
                    address = sheet.cells.get((row, col), (None,))[0]
                    if not address:
                        continue
                    letters, number = re.fullmatch(r'([A-Z]+)([0-9]+)', address).groups()
                    column = 0
                    for letter in letters:
                        column = column * 26 + ord(letter) - 64
                    position = (int(number), column)
                    value, style = target.cells[position]
                    target.cells[position] = ('', style)
    for sheet in book.sheets:
        sheet.print_context = redact_export_text(sheet.print_context, product, project, names)
        for address, (value, style) in list(sheet.cells.items()):
            if not isinstance(value, str):
                continue
            row, col = address
            if sheet.hidden:
                if sheet.name == META_NAME and row == 1 and col == 7 and product:
                    value = product_export_context(product)['name']
                # Remaining metadata is opaque IDs, exact source IDs, g/c indices
                # and cell addresses. No original descriptive-name map is saved.
            elif value not in source_ids:
                value = redact_export_text(value, product, project, names)
            sheet.cells[address] = (value, style)


def _metadata(book, kind, records, product=None):
    meta = _Sheet(META_NAME, [18] * (len(META_HEADERS) + 2), protected=True, hidden=True)
    meta.put(1, 1, SIGNATURE)
    meta.put(1, 2, VERSION)
    meta.put(1, 3, kind)
    meta.put(1, 8, 'export_privacy')
    meta.put(1, 9, 'neutral-v1')
    if product:
        meta.put(1, 4, 'product_id')
        meta.put(1, 5, product['id'])
        meta.put(1, 6, 'product_name')
        meta.put(1, 7, product['name'])
    for col, value in enumerate(META_HEADERS, 1):
        meta.put(2, col, value)
    if kind in ('orders', 'bundle'):
        meta.put(2, 17, 'edit_version')
        meta.put(2, 18, 'plan_fingerprint')
    for row, record in enumerate(records, 3):
        for col, key in enumerate(META_HEADERS, 1):
            meta.put(row, col, record.get(key))
        if kind in ('orders', 'bundle'):
            meta.put(row, 17, record.get('edit_version'))
            meta.put(row, 18, record.get('plan_fingerprint'))
    book.add(meta)


def _preparation_metadata(book, records):
    meta = _Sheet(PREP_META_NAME, [18] * len(PREP_META_HEADERS), protected=True, hidden=True)
    meta.put(1, 1, SIGNATURE)
    meta.put(1, 2, VERSION)
    for col, field in enumerate(PREP_META_HEADERS, 1):
        meta.put(2, col, field)
    for row, record in enumerate(records, 3):
        for col, field in enumerate(PREP_META_HEADERS, 1):
            meta.put(row, col, record.get(field))
    book.add(meta)


def _wrapped_lines(text, limit=48):
    result = []
    text = _clean_xml_text(text).replace('\r\n', '\n').replace('\r', '\n')
    for paragraph in text.split('\n'):
        line, width = '', 0
        for character in paragraph:
            units = 2 if unicodedata.east_asian_width(character) in ('W', 'F') else 1
            if width + units > limit and line:
                result.append(line)
                line, width = '', 0
            line += character
            width += units
        result.append(line)
    return result


def _condition_lines(formula):
    """Wrap fixed design conditions into printable, locked single-line rows."""
    return [line for name, value in (formula.get('process_factors') or {}).items()
            for line in _wrapped_lines(str(name) + '：' + str(value))]


_ORDER_PAGE_POINTS = 740
_ORDER_CARD_POINTS = 700
_ORDER_FRAME_POINTS = 28 + 25 + 35
# Fixed card rows only: title + weighing header + footer.  The four metadata
# rows are budgeted separately and there is no spacer row inside a card.
_PAIR_GAP_POINTS = 20
_ORDER_WIDTHS = [8, 12, 7, 11, 12, 11, 2, 8, 12, 7, 11, 12, 11]
# The 分装指示 block spans the full printable width A:M.  Its six logical cells
# map onto column spans; 说明 gets H:M (61 width units) instead of the old 9.
_DISPENSING_SPANS = ((1, 1), (2, 3), (4, 4), (5, 5), (6, 7), (8, 13))
_DISPENSING_LIMITS = (7, 18, 10, 11, 12, 59)
_DISPENSING_NOTE_LINES = 12
_DISPENSING_CONTINUED_LINES = 24


def _order_card_heights(card, product_rows=0):
    """Row heights in the exact order _write_orders emits them.

    This must stay row-for-row in sync with _write_orders: title, product rows,
    four metadata rows, optional condition header/rows, then weighing header,
    lines and footer (or the appendix rows), plus the 分装指示 block when present.
    """
    heights = [28] + [20] * product_rows + _order_meta_heights(card['batch'])
    if card['conditions']:
        heights += [25] + [20] * len(card['conditions'])
    if not card['appendix']:
        heights += [25] + [_order_line_height(line) for line in card['lines']] + [35]
        if card.get('instructions'):
            heights += [25] + [height for _, height in card['instructions']]
    elif card.get('instructions'):
        heights += [25] + [height for _, height in card['instructions']]
    else:
        heights += [35]
    return heights


def _dispensing_note_chunks(note):
    """Wrap a note at the 说明 width; only very long notes need continuation rows."""
    text = _text(note).strip()
    if not text:
        return []
    lines = _wrapped_lines(text, _DISPENSING_LIMITS[-1])
    return ([lines[:_DISPENSING_NOTE_LINES]]
            + [lines[start:start + _DISPENSING_CONTINUED_LINES]
               for start in range(_DISPENSING_NOTE_LINES, len(lines), _DISPENSING_CONTINUED_LINES)])


def _dispensing_cell_height(cells):
    lines = max(len(_wrapped_lines(_text(text), limit))
                for text, limit in zip(cells, _DISPENSING_LIMITS))
    return max(22, 15 * lines + 6)


def _dispensing_rows(batch):
    """Excel rows for the full-width dispensing table: (cells, height).

    A five-field bottle row carries 瓶号/用途/每瓶量/过滤/容器 and the first part of
    说明; only a genuinely long note continues on merged full-width rows.
    """
    table = instruction_table(batch)
    if not table:
        return []
    summary, plan_rows = table
    rows = [(TABLE_HEADER, 22)]
    for *head, note in plan_rows:
        chunks = _dispensing_note_chunks(note)
        cells = tuple(head) + ('\n'.join(chunks[0]) if chunks else '',)
        rows.append((cells, _dispensing_cell_height(cells)))
        for chunk in chunks[1:]:
            rows.append(('\n'.join(chunk), max(22, 15 * len(chunk) + 6)))
    summary_lines = len(_wrapped_lines(summary, 120))
    rows.append((summary, max(22, 15 * summary_lines + 6)))
    return rows


def _dispensing_sections(rows, budget):
    """Split the dispensing table into appendix pages; every page repeats the header.

    A bottle row stays with its note rows when that group fits on a page; a
    group taller than a whole page splits at row boundaries instead.
    """
    header, body = rows[0], rows[1:]
    room = max(1, budget - header[1])
    groups = []
    for item in body:
        if isinstance(item[0], str) and groups:
            groups[-1].append(item)
        else:
            groups.append([item])
    sections, used = [[]], 0
    for group in groups:
        height = sum(item[1] for item in group)
        if height <= room:
            if used + height > room and sections[-1]:
                sections.append([])
                used = 0
            sections[-1].extend(group)
            used += height
            continue
        for item in group:
            if used + item[1] > room and sections[-1]:
                sections.append([])
                used = 0
            sections[-1].append(item)
            used += item[1]
    return [[header] + section for section in sections]


def _put_dispensing(page, row, book, rows):
    last_col = len(_ORDER_WIDTHS)
    for i, (cells, height) in enumerate(rows):
        target = row + i
        style = book.style('A6') if i == 0 else book.style('B2', wrap=True)
        if isinstance(cells, str):
            page.merge(target, 1, last_col, cells, style)
        else:
            for (first, last), text in zip(_DISPENSING_SPANS, cells):
                if first == last:
                    page.put(target, first, text, style)
                else:
                    page.merge(target, first, last, text, style)
        page.height(target, height)


def _order_line_height(line):
    return max(21, 15 * len(_wrapped_lines(_text(line.get('source_id')), 19)) + 5)


def _order_meta_heights(batch):
    return [max(22, 15 * len(_wrapped_lines(_text(batch.get(key)), 19)) + 5)
            for key in ('formula_code', 'batch_code')] + [22, 22]


def _chunk_order_lines(lines, capacity, reserve=0):
    """Chunk weighing lines; only the final chunk reserves room for 分装指示."""
    if not lines:
        return []
    heights = [_order_line_height(line) for line in lines]
    if any(height > capacity for height in heights):
        raise ValueError('原料编号过长，单行无法在 A4 工单中完整打印。')
    if reserve and reserve + min(heights) > capacity:
        reserve = 0
    suffix = [0] * (len(heights) + 1)
    for index in range(len(heights) - 1, -1, -1):
        suffix[index] = suffix[index + 1] + heights[index]
    chunks, chunk, used = [], [], 0
    index = 0
    while index < len(lines):
        height = heights[index]
        if used + height + suffix[index + 1] + reserve <= capacity:
            chunks.append(chunk + lines[index:])
            return chunks
        if chunk and used + height > capacity:
            chunks.append(chunk)
            chunk, used = [], 0
            continue
        chunk.append(lines[index])
        used += height
        index += 1
    if chunk:
        chunks.append(chunk)
    return chunks


def orders_xlsx(batches: list, product=None) -> bytes:
    """Export protected plans and unlocked actuals using paired A:F / H:M cards."""
    book = _Book('orders.xlsx')
    product = _export_product(product)
    records = _write_orders(book, batches, product)
    _combine_pages(book, list(book.sheets), '配样工单', records)
    _metadata(book, 'orders', records, product)
    _private_workbook(book, batches=batches, product=product)
    return book.bytes()


def _write_orders(book, batches, product):
    if not batches:
        raise ValueError('请先选择需要导出的配样工单。')
    from .batch_management import plan_fingerprint
    fingerprints = {batch['id']: plan_fingerprint(batch) for batch in batches}
    product_lines = _wrapped_lines('产品：' + product['name']) if product else []
    product_rows = len(product_lines)
    widths = _ORDER_WIDTHS
    cards = []
    seen_batches = set()
    for batch in batches:
        bid = _text(batch.get('id'))
        if not bid or bid in seen_batches:
            raise ValueError('导出工单存在空批次标识或重复批次。')
        seen_batches.add(bid)
        lines = batch.get('result', {}).get('lines', [])
        if not lines:
            raise ValueError('工单没有可导出的投料行。')
        seen_lines = set()
        for line in lines:
            lid = _text(line.get('id'))
            if not lid or lid in seen_lines:
                raise ValueError('工单存在空行标识或重复行标识。')
            seen_lines.add(lid)
        # A long batch is continued as full, repeated-header cards. Actual line
        # identifiers occur exactly once; operator/note are edited on the first.
        condition_lines = _condition_lines(batch.get('formula_data') or {})
        long_conditions = len(condition_lines) > 10
        visible_conditions = (['完整设计条件见本批次的“设计条件附页”。'] if long_conditions else condition_lines)
        condition_points = 25 + 20 * len(visible_conditions) if visible_conditions else 0
        meta_heights = _order_meta_heights(batch)
        capacity = (_ORDER_CARD_POINTS - _ORDER_FRAME_POINTS - sum(meta_heights)
                    - condition_points - product_rows * 20)
        instructions = _dispensing_rows(batch)
        block_points = 25 + sum(height for _, height in instructions) if instructions else 0
        chunks = _chunk_order_lines(lines, capacity, reserve=block_points)
        line_offset = 0
        for i, chunk in enumerate(chunks):
            cards.append({'batch': batch, 'lines': chunk, 'segment': i, 'segments': len(chunks),
                          'line_offset': line_offset, 'conditions': visible_conditions, 'appendix': False})
            line_offset += len(chunk)
        if instructions:
            last = cards[-1]
            last['instructions'] = instructions
            if sum(_order_card_heights(last, product_rows)) > _ORDER_CARD_POINTS:
                last.pop('instructions')
                budget = max(240, _ORDER_CARD_POINTS - (28 + sum(meta_heights)
                                                        + 25 + product_rows * 20))
                sections = _dispensing_sections(instructions, budget)
                for i, section in enumerate(sections):
                    cards.append({'batch': batch, 'lines': [], 'segment': i, 'segments': len(sections),
                                  'line_offset': 0, 'conditions': [], 'appendix': 'dispensing',
                                  'instructions': section})
        if long_conditions:
            fixed = 28 + sum(meta_heights) + 25 + 35 + product_rows * 20
            section_size = min(25, max(1, (_ORDER_CARD_POINTS - fixed) // 20))
            sections = [condition_lines[i:i + section_size] for i in range(0, len(condition_lines), section_size)]
            for i, section in enumerate(sections):
                cards.append({'batch': batch, 'lines': [], 'segment': i, 'segments': len(sections),
                              'line_offset': 0, 'conditions': section, 'appendix': True})
    records = []
    page = None
    next_row = 1
    used_points = 0
    def layout_points(pair):
        layouts = [_order_card_heights(card, product_rows) for card in pair]
        rows = max(len(heights) for heights in layouts)
        return rows, sum(max(h[i] if i < len(h) else 0 for h in layouts) for i in range(rows))

    pairs = []
    offset = 0
    while offset < len(cards):
        card = cards[offset]
        if card.get('instructions'):
            # A full-width 分装指示 block never shares a page side-by-side: its tall
            # rows would otherwise stretch the neighbouring weighing rows.
            pairs.append([card])
            offset += 1
        elif offset + 1 < len(cards) and not cards[offset + 1].get('instructions'):
            pair = cards[offset:offset + 2]
            if layout_points(pair)[1] > _ORDER_PAGE_POINTS:
                pair = pair[:1]
            pairs.append(pair)
            offset += len(pair)
        else:
            pairs.append([card])
            offset += 1
    for pair in pairs:
        row_count, pair_points = layout_points(pair)
        full_width = any(card.get('instructions') for card in pair)
        if page is None or full_width or used_points + pair_points > _ORDER_PAGE_POINTS:
            page = book.add(_Sheet('配样工单' + str(len(book.sheets) + 1).zfill(2), widths))
            next_row = 1
            used_points = 0
        for side, card in enumerate(pair):
            batch, lines = card['batch'], card['lines']
            segment, segments, line_offset = card['segment'], card['segments'], card['line_offset']
            appendix, conditions = card['appendix'], card['conditions']
            first_col = 1 + side * 7
            title = ('设计条件附页（' + str(segment + 1) + '/' + str(segments) + '）' if appendix else
                     '配 样 工 单' if not segment else '配样工单（续 ' + str(segment + 1) + '/' + str(segments) + '）')
            if appendix == 'dispensing':
                title = '分装指示附页（' + str(segment + 1) + '/' + str(segments) + '）'
            page.merge(next_row, first_col, first_col + 5, title, book.style('A1'))
            page.height(next_row, 28)
            for i, text in enumerate(product_lines, 1):
                page.merge(next_row + i, first_col, first_col + 5, text, book.style('B2'))
                page.height(next_row + i, 20)
            metadata_row = next_row + product_rows
            code = batch.get('batch_code')
            visible_code = code or '历史导入'
            formula_code = _text(batch.get('formula_code'))
            values = [
                ('配方编号', formula_code, '版本', ('R' + str(batch['revision']).zfill(2) if batch.get('revision') else '历史原件') + ' · 工单 v' + str(batch.get('plan_revision', 1))),
                ('批次号', visible_code, '日期', format_date(batch.get('planned_date')) if batch.get('planned_date') else ''),
                ('目标量/g', batch.get('formula_data', {}).get('total_mass_g'), '目标固含', batch.get('formula_data', {}).get('solid_fraction')),
                ('操作人', batch.get('actual', {}).get('operator', '') if not segment else '见首单', '记录', '黄色格可填写' if not segment else '备注填写于首单'),
            ]
            if appendix:
                values[-1] = ('条件类型', '已保存的设计', '填写', '以配样工单为准')
            for i, (label, value, right_label, right_value) in enumerate(values, 1):
                row = metadata_row + i
                page.put(row, first_col, label, book.style('A2', shrink=True))
                page.put(row, first_col + 3, right_label, book.style('D2', shrink=True))
                importance = 'primary' if i == 1 else 'secondary' if i == 2 else None
                left_style = book.style('B4') if i == 3 else book.style('B5', unlocked=True) if i == 4 and not segment and not appendix else book.style('B2', wrap=i <= 2, shrink=i > 2, emphasis=importance)
                right_style = book.style('E4') if i == 3 else book.style('E2', shrink=True, emphasis=importance)
                page.merge(row, first_col + 1, first_col + 2, value, left_style)
                page.merge(row, first_col + 4, first_col + 5, right_value, right_style)
                page.height(row, _order_meta_heights(batch)[i - 1])
            header_row = metadata_row + 5
            if conditions:
                page.merge(header_row, first_col, first_col + 5, '设计条件（计划，已锁定）', book.style('A6'))
                page.height(header_row, 25)
                for i, condition in enumerate(conditions, 1):
                    page.merge(header_row + i, first_col, first_col + 5, condition, book.style('B2'))
                    page.height(header_row + i, 20)
                header_row += 1 + len(conditions)
            if appendix == 'dispensing':
                page.merge(header_row, 1, len(widths), '分装指示（仅为操作指示）', book.style('A6'))
                page.height(header_row, 25)
                _put_dispensing(page, header_row + 1, book, card['instructions'])
                continue
            if appendix:
                page.merge(header_row, first_col, first_col + 5,
                           '本页与相同批次号的配样工单一同使用；称量记录填写在配样工单。', book.style('B2', shrink=True))
                page.height(header_row, 35)
                continue
            for c, text in ((0, '序'), (3, '计划量/g'), (4, '实际/g'), (5, '复核')):
                page.put(header_row, first_col + c, text, book.style('A6'))
            page.merge(header_row, first_col + 1, first_col + 2, '原料编号', book.style('A6'))
            page.height(header_row, 25)
            footer_row = header_row + 1 + len(lines)
            if not segment:
                records.append({'record_type': 'batch', 'batch_id': batch['id'], 'batch_code': code,
                    'edit_version': batch.get('edit_version', 1),
                    'plan_fingerprint': fingerprints[batch['id']],
                    'formula_code': formula_code, 'visible_sheet': page.name,
                    'batch_code_cell': _address(metadata_row + 2, first_col + 1),
                    'formula_code_cell': _address(metadata_row + 1, first_col + 1),
                    'operator_cell': _address(metadata_row + 4, first_col + 1),
                    'note_cell': _address(footer_row, first_col + 1)})
            actuals = batch.get('actual', {}).get('lines', {})
            for i, line in enumerate(lines):
                row = header_row + 1 + i
                lid = _text(line['id'])
                actual = actuals.get(lid, {})
                plan = _finite(line.get('planned_mass_g'), '计划量', nonnegative=True)
                actual_mass = _finite(actual.get('mass_g'), '实际称量', blank=True, nonnegative=True)
                page.put(row, first_col, line_offset + i + 1, book.style('A7'))
                source_ref = page.merge(row, first_col + 1, first_col + 2, line.get('source_id', ''), book.style('B7', wrap=True))
                plan_ref = page.put(row, first_col + 3, plan, book.style('D7'))
                actual_ref = page.put(row, first_col + 4, actual_mass, book.style('E7', unlocked=True))
                review_ref = page.put(row, first_col + 5, actual.get('review', ''), book.style('F7', unlocked=True, shrink=True))
                page.height(row, _order_line_height(line))
                records.append({'record_type': 'line', 'batch_id': batch['id'], 'batch_code': code,
                    'edit_version': batch.get('edit_version', 1),
                    'plan_fingerprint': fingerprints[batch['id']],
                    'formula_code': formula_code, 'line_id': lid, 'source_id': line.get('source_id', ''),
                    'planned_mass_g': plan, 'visible_sheet': page.name,
                    'batch_code_cell': _address(metadata_row + 2, first_col + 1),
                    'formula_code_cell': _address(metadata_row + 1, first_col + 1),
                    'source_id_cell': source_ref, 'planned_mass_cell': plan_ref,
                    'actual_mass_cell': actual_ref, 'review_cell': review_ref})
            page.put(footer_row, first_col, '备注', book.style('A8', shrink=True))
            page.merge(footer_row, first_col + 1, first_col + 5,
                       batch.get('actual', {}).get('note', '') if not segment else '操作人及实验备注请填写在此批次首单。',
                       book.style('B8', unlocked=not segment))
            page.height(footer_row, 35)
            if card.get('instructions'):
                page.merge(footer_row + 1, 1, len(widths), '分装指示（仅为操作指示）', book.style('A6'))
                page.height(footer_row + 1, 25)
                _put_dispensing(page, footer_row + 2, book, card['instructions'])
        next_row += row_count + 1
        used_points = (_ORDER_PAGE_POINTS if full_width
                       else used_points + pair_points + _PAIR_GAP_POINTS)
    return records


def preparation_xlsx(prep: dict, product=None) -> bytes:
    """Export frozen premix component weighing, with writable actuals and yield."""
    product = _export_product(product)
    book = _Book('preparation.xlsx')
    records = _write_preparation(book, prep, product, book.style)
    _combine_pages(book, list(book.sheets), '预混工单', records)
    _metadata(book, 'preparations', [], product)
    _preparation_metadata(book, records)
    _private_workbook(book, preparations=[prep], product=product)
    return book.bytes()


def bundle_xlsx(batches: list, preparations: list, product=None, project=None) -> bytes:
    """One printable workbook for selected project work orders and premixes."""
    if not batches and not preparations:
        raise ValueError('请至少选择一份配样工单或预混备料单。')
    product = _export_product(product)
    book = _Book('orders.xlsx')
    records = _write_orders(book, batches, product) if batches else []
    _combine_pages(book, list(book.sheets), '配样工单', records)
    prep_records = []
    seen = set()
    if preparations:
        style = book.template_styles('preparation.xlsx')
        first_prep_sheet = len(book.sheets)
        for prep in preparations:
            pid = prep.get('id')
            if not pid or pid in seen:
                raise ValueError('联合导出备料单存在空标识或重复记录。')
            seen.add(pid)
            prep_records.extend(_write_preparation(book, prep, product, style))
        _combine_pages(book, book.sheets[first_prep_sheet:], '预混工单', prep_records)
        # Keep each part's own print area and manual page breaks.
        premix_sheet = book.sheets.pop()
        book.sheets.insert(0, premix_sheet)
    if project and book.sheets:
        name = str(project.get('name') or (project.get('data') or {}).get('name') or '实验项目')
        revision = project.get('revision')
        # Print context lives in the existing footer, not on an empty cover page.
        context = '项目：' + name + (f' · 方案 R{revision}' if revision else '')
        for sheet in book.sheets:
            sheet.print_context = context
    _metadata(book, 'bundle', records, product)
    _preparation_metadata(book, prep_records)
    _private_workbook(book, batches, preparations, product, project)
    return book.bytes()


def _write_preparation(book, prep, product, style):
    groups = prep.get('groups', [])
    if not groups:
        raise ValueError('备料单没有预混需求。')
    pid = prep.get('id')
    actual = prep.get('actual') or {}
    actual_groups = actual.get('groups') or {}
    records = []
    keys = set()
    for group in groups:
        key = group.get('key')
        if not isinstance(key, str) or not key or key in keys:
            raise ValueError('预混备料缺少稳定的组成标识或存在重复组成。')
        keys.add(key)
    widths = [25, 24, 18, 18, 17, 18, 21]
    page_no = len([sheet for sheet in book.sheets if sheet.name.startswith('预混称量')])
    header_style = book.style(style('A2'), wrap=True)

    def page(title):
        nonlocal page_no
        page_no += 1
        page_widths = widths
        last_col = len(page_widths)
        sheet = book.add(_Sheet(title + str(page_no).zfill(2), page_widths, landscape=True))
        sheet.merge(1, 1, last_col, '预混配制工单' + (' · 已过期' if prep.get('stale') else ''), style('A1'))
        sheet.height(1, 30)
        information = _text(prep.get('code')) + ' · ' + (format_date(prep.get('created_at')) if prep.get('created_at') else '') + ' · ' + title
        lines = (_wrapped_lines('产品：' + product['name'], 120) if product else []) + _wrapped_lines(information, 120) + [PREMIX_STOCK_REMINDER]
        if prep.get('note'):
            lines += _wrapped_lines('备注：' + str(prep['note']), 120)
        sheet.merge(2, 1, last_col, '\n'.join(lines), header_style)
        sheet.height(2, 18 * sum(len(_wrapped_lines(line, 120)) for line in lines) + 6)
        return sheet

    # Pack complete premix blocks onto weighing pages. A repeated header and
    # continuation title keep long definitions readable without wasted pages.
    weighing = None
    next_row = 4
    used = 0
    prep_record_written = False

    def group_footer(sheet, row, group, continued):
        entered = actual_groups.get(group['key']) or {}
        if not continued:
            sheet.merge(row, 1, 2, '实际产出/g（选填）', style('A8'))
            yield_cell = sheet.merge(row, 3, 4, entered.get('actual_yield_g'), style('F7', unlocked=True))
            sheet.merge(row, 5, 7, '填写最终产出量，用于回算损耗', style('A2', shrink=True))
            sheet.height(row, 32)
            records.append({'record_type': 'yield', 'preparation_id': pid, 'group_key': group['key'],
                            'source_id': group.get('source_id'), 'visible_sheet': sheet.name,
                            'actual_yield_cell': yield_cell})
            row += 1
        sheet.put(row, 1, '操作人', style('A8'))
        operator_cell = sheet.merge(row, 2, 3, '见本组首段' if continued else entered.get('operator', ''), style('B8', unlocked=not continued))
        sheet.put(row, 4, '备注', style('D8'))
        note_cell = sheet.merge(row, 5, 7, '见本组首段' if continued else entered.get('note', ''), style('E8', unlocked=not continued))
        sheet.height(row, 32)
        if not continued:
            records.append({'record_type': 'group', 'preparation_id': pid, 'group_key': group['key'],
                            'source_id': group.get('source_id'), 'visible_sheet': sheet.name,
                            'operator_cell': operator_cell, 'note_cell': note_cell})
        return row + 1

    for group in groups:
        components = group.get('components', [])
        if not components:
            raise ValueError('预混备料缺少冻结的原料组成。')
        start = 0
        line_heights = [max(28, 15 * max(len(_wrapped_lines(_text(group.get('source_id')), 30)),
                                        len(_wrapped_lines(_text(component.get('source_id')), 50))) + 6)
                        for component in components]
        if weighing is not None and used + 28 + sum(line_heights) + 64 > 545:
            weighing = None
        while start < len(components):
            footer_height = 32 if start else 64
            if weighing is None or used + 28 + line_heights[start] + footer_height > 545:
                weighing = page('预混称量')
                header_row = 3
                if not prep_record_written:
                    weighing.put(3, 1, '整单操作人', style('A8'))
                    operator_cell = weighing.merge(3, 2, 3, actual.get('operator', ''), style('B8', unlocked=True))
                    weighing.put(3, 4, '整单备注', style('D8'))
                    note_cell = weighing.merge(3, 5, 7, actual.get('note', ''), style('E8', unlocked=True))
                    weighing.height(3, 32)
                    records.append({'record_type': 'preparation', 'preparation_id': pid,
                                    'visible_sheet': weighing.name, 'operator_cell': operator_cell, 'note_cell': note_cell})
                    prep_record_written = True
                    header_row += 1
                for c, text in ((1, '预混编号'), (2, '原料编号'), (4, '质量比例'), (5, '计划/g'), (6, '实际/g'), (7, '复核')):
                    if c == 2:
                        weighing.merge(header_row, 2, 3, text, style('A6'))
                    else:
                        weighing.put(header_row, c, text, style('A6'))
                weighing.height(header_row, 27)
                next_row = header_row + 1
                used = 30 + weighing.heights[2] + 27 + (32 if header_row == 4 else 0)
            wanted, required = 0, 28 + footer_height
            while start + wanted < len(components) and wanted < 12:
                height = line_heights[start + wanted]
                if used + required + height > 545:
                    break
                required += height
                wanted += 1
            if not wanted:
                raise ValueError('预混原料文字过长，单行无法在 A4 中完整打印。')
            sheet = weighing
            title = _text(group.get('source_id')) + ' · 本次配制 ' + format(float(group.get('make_g', 0)), '.4f') + ' g'
            if start:
                title += '（续）'
            sheet.merge(next_row, 1, 7, title, style('A5'))
            sheet.height(next_row, 28)
            next_row += 1
            entered_components = (actual_groups.get(group['key']) or {}).get('components') or {}
            for i, component in enumerate(components[start:start + wanted], next_row):
                cid = 'c' + str(start + i - next_row)
                entered = entered_components.get(cid) or {}
                values = [group.get('source_id'), component.get('source_id'), None,
                           component.get('fraction'), component.get('planned_mass_g'), entered.get('mass_g'), entered.get('review', '')]
                for c, value in enumerate(values, 1):
                    if c == 3:
                        continue
                    ref = {4: 'D7', 5: 'E7', 6: 'F7', 7: 'G7'}.get(c, 'A7')
                    if c == 2:
                        sheet.merge(i, 2, 3, value, style(ref, wrap=True))
                    else:
                        sheet.put(i, c, value, style(ref, unlocked=c >= 6, wrap=c <= 3 or c == 7))
                sheet.height(i, line_heights[start + i - next_row])
                records.append({'record_type': 'component', 'preparation_id': pid, 'group_key': group['key'],
                                'component_id': cid, 'source_id': component.get('source_id'), 'visible_sheet': sheet.name,
                                'source_id_cell': _address(i, 2), 'actual_mass_cell': _address(i, 6), 'review_cell': _address(i, 7)})
            next_row += wanted
            next_row = group_footer(sheet, next_row, group, start > 0)
            used += required
            start += wanted
    aliases = {group['key']: 'g' + str(index) for index, group in enumerate(groups)}
    for record in records:
        if record.get('group_key') in aliases:
            record['group_key'] = aliases[record['group_key']]
    return records if pid else []


class _Reader:
    def __init__(self, data):
        try:
            self.archive = zipfile.ZipFile(io.BytesIO(data))
            if sum(item.file_size for item in self.archive.infolist()) > 100_000_000:
                raise ValueError('Excel 文件展开后过大，请只导入本次工单。')
            names = set(self.archive.namelist())
            if 'xl/vbaProject.bin' in names:
                raise ValueError('请导入工作台导出的无宏 XLSX 工单。')
            workbook = ET.fromstring(self.archive.read('xl/workbook.xml'))
            rels = ET.fromstring(self.archive.read('xl/_rels/workbook.xml.rels'))
            relationship = {rel.get('Id'): rel for rel in rels}
            self.shared = []
            if 'xl/sharedStrings.xml' in names:
                strings = ET.fromstring(self.archive.read('xl/sharedStrings.xml'))
                self.shared = [''.join(node.itertext()) for node in strings]
            self.sheets = {}
            for sheet in workbook.findall('s:sheets/s:sheet', NS):
                rel = relationship.get(sheet.get('{' + R + '}id'))
                if rel is None or rel.get('TargetMode') == 'External':
                    raise ValueError('工单工作表引用无效。')
                target = rel.get('Target', '')
                part = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
                if not part.startswith('xl/'):
                    raise ValueError('工单工作表路径无效。')
                name = sheet.get('name')
                if name in self.sheets:
                    raise ValueError('工单包含重复工作表名称。')
                xml = ET.fromstring(self.archive.read(part))
                cells = {}
                for cell in xml.findall('s:sheetData/s:row/s:c', NS):
                    address = cell.get('r')
                    if not address or address in cells:
                        raise ValueError('工单存在重复或缺失的单元格地址。')
                    cells[address] = cell
                self.sheets[name] = cells
        except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
            raise ValueError('不是可读取的工作台 XLSX 工单，请选择原导出文件填写后的版本。') from None

    def value(self, sheet, ref, *, label='单元格'):
        if sheet not in self.sheets or not isinstance(ref, str) or not CELL_RE.fullmatch(ref):
            raise ValueError(label + '的工作表或单元格引用无效。')
        cell = self.sheets[sheet].get(ref)
        if cell is None:
            return None
        if cell.find(_q('f')) is not None:
            raise ValueError(label + '不能含公式，请填入实际数值或文字。')
        kind = cell.get('t')
        if kind == 'e':
            raise ValueError(label + '含 Excel 错误值。')
        if kind == 'inlineStr':
            node = cell.find(_q('is'))
            return ''.join(node.itertext()) if node is not None else ''
        value = cell.find(_q('v'))
        if value is None or value.text is None:
            return None
        if kind == 's':
            try:
                return self.shared[int(value.text)]
            except (ValueError, IndexError):
                raise ValueError(label + '的文本索引无效。') from None
        if kind in ('str', 'd'):
            return value.text
        if kind == 'b':
            return value.text == '1'
        return _finite(value.text, label)


def _parse_preparation_actual(reader):
    """Read filled premix values without treating plan cells as editable data."""
    if PREP_META_NAME not in reader.sheets:
        raise ValueError('这份预混表没有导回标识，请在新版软件中重新导出后填写。')
    if reader.value(PREP_META_NAME, 'A1') not in (SIGNATURE, LEGACY_SIGNATURE) or reader.value(PREP_META_NAME, 'B1') != VERSION:
        raise ValueError('预混记录隐藏标识无效，请使用原导出文件。')
    for col, field in enumerate(PREP_META_HEADERS, 1):
        if reader.value(PREP_META_NAME, _address(2, col)) != field:
            raise ValueError('预混记录隐藏字段结构已改变，请使用原导出文件。')
    rows = sorted({int(re.search(r'[0-9]+$', ref).group()) for ref in reader.sheets[PREP_META_NAME]
                   if int(re.search(r'[0-9]+$', ref).group()) >= 3})
    preparations, groups, issues = {}, {}, []
    records, identities, locations = [], {}, {}
    for row in rows:
        try:
            record = {key: reader.value(PREP_META_NAME, _address(row, col), label='预混记录位置')
                      for col, key in enumerate(PREP_META_HEADERS, 1)}
        except ValueError as error:
            issues.append({'code': 'INVALID_PREPARATION_LOCATION', 'message': f'预混标识第 {row} 行：{error}'})
            continue
        if not any(value not in (None, '') for value in record.values()):
            continue
        pid, key, kind = record['preparation_id'], record['group_key'], record['record_type']
        identity = (pid, key, kind, record['component_id'])
        record['_identity'] = identity
        record['_locations'] = [(record['visible_sheet'], record[field]) for field in
                                 ('actual_mass_cell', 'review_cell', 'actual_yield_cell', 'operator_cell', 'note_cell')
                                 if record[field] not in (None, '')]
        identities[identity] = identities.get(identity, 0) + 1
        for location in record['_locations']:
            locations[location] = locations.get(location, 0) + 1
        records.append(record)
    for record in records:
        pid, key, kind = record['preparation_id'], record['group_key'], record['record_type']
        context = {field: record[field] for field in ('preparation_id', 'group_key', 'component_id', 'source_id') if record[field]}
        try:
            if not isinstance(pid, str) or not pid.strip():
                raise ValueError('备料单标识缺失，请导出已保存的备料单。')
            if kind not in ('preparation', 'group', 'yield', 'component'):
                raise ValueError('存在无法识别的预混记录类型。')
            if kind != 'preparation' and (not isinstance(key, str) or not key):
                raise ValueError('缺少预混组成标识。')
            if identities[record['_identity']] > 1 or any(locations[loc] > 1 for loc in record['_locations']):
                raise ValueError('多条预混记录引用了相同标识或填写单元格，无法确定归属。')
            prep = preparations.setdefault(pid, {'preparation_id': pid, 'groups': [], 'operator': '', 'note': ''})
            group = None
            if kind != 'preparation':
                if (pid, key) not in groups:
                    group = {'group_key': key, 'components': [], 'actual_yield_g': None, 'operator': '', 'note': ''}
                    groups[(pid, key)] = group
                    prep['groups'].append(group)
                group = groups[(pid, key)]
            sheet = record['visible_sheet']
            if kind in ('preparation', 'group'):
                target = prep if kind == 'preparation' else group
                # Read both before assigning, so an invalid cell cannot leave a
                # half-read field pair in the import payload.
                operator = _text(reader.value(sheet, record['operator_cell'], label='预混操作人'))
                note = _text(reader.value(sheet, record['note_cell'], label='预混备注'))
                target.update(operator=operator, note=note)
            elif kind == 'yield':
                group['actual_yield_g'] = _finite(reader.value(sheet, record['actual_yield_cell'], label='实际配制量'),
                                                 '实际配制量', blank=True, nonnegative=True)
            else:
                cid = record['component_id']
                if not isinstance(cid, str) or not re.fullmatch(r'c[0-9]+', cid):
                    raise ValueError('预混原料行标识无效。')
                mass = _finite(reader.value(sheet, record['actual_mass_cell'], label='预混实际称量'),
                               '预混实际称量', blank=True, nonnegative=True)
                review = _text(reader.value(sheet, record['review_cell'], label='预混复核'))
                if mass is None and not review.strip():
                    continue
                source = _text(reader.value(sheet, record['source_id_cell'], label='预混原料编号')).strip()
                if not source:
                    raise ValueError('已填写的预混实际记录缺少原料编号。')
                group['components'].append({'component_id': cid, 'source_id': source, 'actual_mass_g': mass, 'review': review})
        except ValueError as error:
            issues.append({'code': 'INVALID_PREPARATION_ACTUAL', 'message': str(error), **context,
                           'visible_sheet': record.get('visible_sheet'),
                           'actual_mass_cell': record.get('actual_mass_cell') or record.get('actual_yield_cell')})
    if not preparations and not issues:
        raise ValueError('这份预混表没有已保存备料单的导回标识，请保存备料单后重新导出。')
    return list(preparations.values()), issues


def _parse_actual_only(reader, product_id, product_name, kind='orders'):
    """Read only user-filled actual fields; stale planned cells are irrelevant."""
    rows = sorted({int(re.search(r'[0-9]+$', ref).group()) for ref in reader.sheets[META_NAME]
                   if int(re.search(r'[0-9]+$', ref).group()) >= 3})
    columns = {name: index for index, name in enumerate(META_HEADERS, 1)}
    needed = ('record_type', 'batch_id', 'line_id', 'visible_sheet', 'operator_cell', 'note_cell',
              'source_id_cell', 'actual_mass_cell', 'review_cell')
    batches, issues, blocked_fields = {}, [], set()
    def issue(code, message, bid=None, lid=None, source=None):
        issues.append({'code': code, 'message': message, 'batch_id': bid, 'line_id': lid, 'source_id': source})
    for row in rows:
        try:
            record = {name: reader.value(META_NAME, _address(row, columns[name]), label='实称记录位置') for name in needed}
        except ValueError as error:
            issue('INVALID_RECORD_LOCATION', f'隐藏记录第 {row} 行：{error}')
            continue
        if not any(value not in (None, '') for value in record.values()):
            continue
        bid = record['batch_id']
        if not isinstance(bid, str) or not bid.strip():
            issue('BATCH_ID_REQUIRED', f'隐藏记录第 {row} 行缺少工单标识。')
            continue
        batch = batches.setdefault(bid, {'batch_id': bid, 'operator': '', 'note': '', 'lines': []})
        sheet = record['visible_sheet']
        if record['record_type'] == 'batch':
            for key, ref_key, label in (('operator', 'operator_cell', '操作人'), ('note', 'note_cell', '备注')):
                try:
                    value = _text(reader.value(sheet, record[ref_key], label=label))
                except ValueError as error:
                    issue('INVALID_ACTUAL_FIELD', str(error), bid)
                    continue
                if not value.strip() or (bid, key) in blocked_fields:
                    continue
                if batch[key] and batch[key] != value:
                    issue('AMBIGUOUS_ACTUAL_FIELD', f'同一工单的{label}有多个不同值，此字段未导入。', bid)
                    batch[key] = ''
                    blocked_fields.add((bid, key))
                else:
                    batch[key] = value
            continue
        if record['record_type'] != 'line':
            issue('UNKNOWN_RECORD_TYPE', f'隐藏记录第 {row} 行无法识别为称量记录。', bid)
            continue
        lid = record.get('line_id')
        try:
            actual = _finite(reader.value(sheet, record['actual_mass_cell'], label='实际称量'), '实际称量', blank=True, nonnegative=True)
            review = _text(reader.value(sheet, record['review_cell'], label='复核'))
            if actual is None and not review.strip():
                continue
            source = _text(reader.value(sheet, record['source_id_cell'], label='原料编号')).strip()
            if not source:
                raise ValueError('已填写的实际记录缺少原料编号。')
        except ValueError as error:
            issue('INVALID_ACTUAL_ROW', f'{sheet} {record.get("actual_mass_cell") or ""}：{error}', bid, lid)
            continue
        batch['lines'].append({'line_id': lid, 'source_id': source, 'actual_mass_g': actual, 'review': review,
                               'visible_sheet': sheet, 'actual_mass_cell': record['actual_mass_cell']})
    preparations = []
    if kind in ('preparations', 'bundle'):
        preparations, prep_issues = _parse_preparation_actual(reader)
        issues.extend(prep_issues)
    return {'format_version': VERSION, 'kind': kind, 'product_id': product_id or None,
            'export_privacy': reader.value(META_NAME, 'I1') if reader.value(META_NAME, 'H1') == 'export_privacy' else None,
            'product_name': product_name or None, 'batches': list(batches.values()),
            'preparations': preparations, 'issues': issues}


def parse_actual_xlsx(data: bytes, *, actual_only=False) -> dict:
    """Read editable actuals plus visible frozen identifiers/plans for DB checks."""
    reader = _Reader(data)
    try:
        if META_NAME not in reader.sheets or reader.value(META_NAME, 'A1') not in (SIGNATURE, LEGACY_SIGNATURE):
            raise ValueError('无法识别工单格式。请使用工作台导出的配样工单 XLSX，不要删除隐藏标识页。')
        if reader.value(META_NAME, 'B1') != VERSION:
            raise ValueError('该 Excel 工单版本暂不支持导入。')
        kind = reader.value(META_NAME, 'C1')
        if kind not in ('orders', 'preparations', 'bundle'):
            raise ValueError('这份表没有可导回记录，请在新版软件中重新导出后填写。')
        product_id, product_name = reader.value(META_NAME, 'E1'), reader.value(META_NAME, 'G1')
        product_marker, name_marker = reader.value(META_NAME, 'D1'), reader.value(META_NAME, 'F1')
        if any(value not in (None, '') for value in (product_id, product_name, product_marker, name_marker)):
            if (product_marker != 'product_id' or name_marker != 'product_name'
                    or not isinstance(product_id, str) or not product_id.strip()
                    or not isinstance(product_name, str) or not product_name.strip()):
                raise ValueError('工单产品标识不完整，请使用原导出文件。')
        for col, expected in enumerate(META_HEADERS, 1):
            if reader.value(META_NAME, _address(2, col)) != expected:
                raise ValueError('工单隐藏标识的字段结构已改变，请使用原导出文件。')
        if actual_only or kind == 'preparations':
            return _parse_actual_only(reader, product_id, product_name, kind)
        version_header = reader.value(META_NAME, 'Q2')
        if version_header not in (None, '', 'edit_version'):
            raise ValueError('工单修订版本字段无效。')
        fingerprint_header = reader.value(META_NAME, 'R2')
        if fingerprint_header not in (None, '', 'plan_fingerprint'):
            raise ValueError('工单计划身份字段无效。')
        if fingerprint_header == 'plan_fingerprint' and version_header != 'edit_version':
            raise ValueError('工单计划身份缺少修订版本字段。')
        rows = sorted({int(re.search(r'[0-9]+$', ref).group()) for ref in reader.sheets[META_NAME] if int(re.search(r'[0-9]+$', ref).group()) >= 3})
        metadata = []
        for row in rows:
            record = {name: reader.value(META_NAME, _address(row, col), label='隐藏工单标识') for col, name in enumerate(META_HEADERS, 1)}
            raw_version = reader.value(META_NAME, _address(row, 17), label='工单修订版本')
            fingerprint = reader.value(META_NAME, _address(row, 18), label='工单计划身份')
            if not any(value is not None and value != '' for value in record.values()) and raw_version in (None, '') and fingerprint in (None, ''):
                continue
            if version_header == 'edit_version':
                version = _finite(raw_version, '工单修订版本')
                if version < 1 or not version.is_integer():
                    raise ValueError('工单修订版本应为正整数。')
                record['edit_version'] = int(version)
            elif raw_version not in (None, ''):
                raise ValueError('工单修订版本缺少字段标识。')
            if fingerprint_header == 'plan_fingerprint':
                if not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
                    raise ValueError('工单计划身份不完整，请使用原导出文件。')
                record['plan_fingerprint'] = fingerprint
            elif fingerprint not in (None, ''):
                raise ValueError('工单计划身份缺少字段标识。')
            if record['record_type'] not in ('batch', 'line'):
                raise ValueError('工单隐藏标识存在未知记录类型。')
            if not isinstance(record['batch_id'], str) or not record['batch_id'].strip():
                raise ValueError('工单缺少批次标识。')
            metadata.append(record)
        batches = {}
        for record in metadata:
            if record['record_type'] != 'batch':
                continue
            bid = record['batch_id']
            if bid in batches:
                raise ValueError('工单文件包含重复批次标识。')
            sheet = record['visible_sheet']
            visible = _text(reader.value(sheet, record['batch_code_cell'], label='可见批次号'))
            original_code = record['batch_code']
            code = visible if original_code not in (None, '') else None
            if code is None and visible != '历史导入':
                raise ValueError('历史工单的可见批次字段已改变，应为“历史导入”。')
            batches[bid] = {'batch_id': bid, 'batch_code': code, 'visible_batch_code': visible,
                'formula_code': _text(reader.value(sheet, record['formula_code_cell'], label='可见配方编号')),
                'operator': _text(reader.value(sheet, record['operator_cell'], label='操作人')),
                'note': _text(reader.value(sheet, record['note_cell'], label='实验备注')), 'lines': []}
            if 'edit_version' in record:
                batches[bid]['edit_version'] = record['edit_version']
            if 'plan_fingerprint' in record:
                batches[bid]['plan_fingerprint'] = record['plan_fingerprint']
        if not batches and kind != 'bundle':
            raise ValueError('工单文件没有可导入的批次。')
        seen_lines = set()
        seen_cells = set()
        for record in metadata:
            if record['record_type'] != 'line':
                continue
            bid, lid = record['batch_id'], record['line_id']
            if bid not in batches:
                raise ValueError('投料行引用了不存在的批次。')
            if not isinstance(lid, str) or not lid.strip() or (bid, lid) in seen_lines:
                raise ValueError('工单存在空行标识或重复行标识。')
            seen_lines.add((bid, lid))
            sheet = record['visible_sheet']
            actual_ref = (sheet, record['actual_mass_cell'])
            if actual_ref in seen_cells:
                raise ValueError('多条投料记录引用了同一个实际称量单元格。')
            seen_cells.add(actual_ref)
            batch = batches[bid]
            if record.get('edit_version') != batch.get('edit_version'):
                raise ValueError('同一批次的工单修订版本不一致。')
            if record.get('plan_fingerprint') != batch.get('plan_fingerprint'):
                raise ValueError('同一批次的工单计划身份不一致。')
            if _text(reader.value(sheet, record['batch_code_cell'], label='分段批次号')) != batch['visible_batch_code']:
                raise ValueError('同一批次的分段工单批次号不一致。')
            if _text(reader.value(sheet, record['formula_code_cell'], label='分段配方编号')) != batch['formula_code']:
                raise ValueError('同一批次的分段工单配方编号不一致。')
            source = _text(reader.value(sheet, record['source_id_cell'], label='可见原料编号'))
            planned = _finite(reader.value(sheet, record['planned_mass_cell'], label='可见计划量'), '可见计划量')
            actual = _finite(reader.value(sheet, record['actual_mass_cell'], label='实际称量'), '实际称量', blank=True, nonnegative=True)
            review = _text(reader.value(sheet, record['review_cell'], label='复核'))
            batch['lines'].append({'line_id': lid, 'source_id': source, 'planned_mass_g': planned,
                                   'actual_mass_g': actual, 'review': review})
        if any(not batch['lines'] for batch in batches.values()):
            raise ValueError('部分批次没有投料记录，请使用完整的原导出工单。')
        preparations, issues = _parse_preparation_actual(reader) if kind == 'bundle' else ([], [])
        return {'format_version': VERSION, 'kind': kind, 'product_id': product_id or None,
                'export_privacy': reader.value(META_NAME, 'I1') if reader.value(META_NAME, 'H1') == 'export_privacy' else None,
                'product_name': product_name or None, 'batches': list(batches.values()),
                'preparations': preparations, 'issues': issues}
    finally:
        reader.archive.close()
