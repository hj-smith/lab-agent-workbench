"""Printable formulation work orders using a bundled, openly licensed font."""
from __future__ import annotations

from .beijing_time import format_date, today
from io import BytesIO
from pathlib import Path
from threading import Lock
from xml.sax.saxutils import escape
import os
import sys
from .export_format import format_number
from .export_privacy import anonymize_export_data, product_export_context, project_export_context
from .preparation_actuals import PREMIX_STOCK_REMINDER
from .dispensing import TABLE_HEADER, instruction_table, note_chunks

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle

FONT = "WorkbenchChinese"
_FONT_LOCK = Lock()
INK = colors.HexColor("#163444")
MUTED = colors.HexColor("#536570")
BORDER = colors.HexColor("#BCCBD1")
LIGHT = colors.HexColor("#EEF3F4")
WRITE = colors.HexColor("#FFFBEB")
PAGE_W, PAGE_H = A4
MARGIN = 24
GAP = 14
TOP = PAGE_H - 52
BOTTOM = 35
WIDTH = (PAGE_W - 2 * MARGIN - GAP) / 2


def _font():
    with _FONT_LOCK:
        if FONT in pdfmetrics.getRegisteredFontNames():
            return
        package = Path(__file__).parent
        choices = [package / "assets" / "NotoSansSC-Regular.ttf",
                   Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "simhei.ttf"]
        for path in choices:
            if path.is_file():
                pdfmetrics.registerFont(TTFont(FONT, str(path)))
                return
        raise RuntimeError("无法导出中文 PDF：随软件提供的 Noto Sans SC 字体缺失，请重新解压完整软件包。")


def _p(value, size=9, color=INK, align=TA_LEFT):
    text = "" if value is None else str(value)
    text = escape(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
    return Paragraph(text, ParagraphStyle("cell", fontName=FONT, fontSize=size,
                                          leading=size * 1.38, textColor=color,
                                          alignment=align, wordWrap="CJK",
                                          splitLongWords=True, spaceAfter=0, spaceBefore=0))


def _num(value):
    return format_number(value)


def _date(value):
    return format_date(value) if value else ''


def _table(rows, widths, spans=(), writing_columns=(), footer_rows=()):
    table = Table(rows, colWidths=widths, repeatRows=3, hAlign="LEFT",
                  splitByRow=1, splitInRow=1, spaceBefore=0, spaceAfter=0,
                  minRowHeights=[0, 0, 24] + [24] * (len(rows) - 3))
    commands = [
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("BACKGROUND", (0, 2), (-1, 2), LIGHT),
        ("GRID", (0, 0), (-1, -1), 0.45, BORDER),
        ("BOX", (0, 0), (-1, -1), 0.7, BORDER),
    ]
    for row in (0, 1, *spans):
        commands.append(("SPAN", (0, row), (-1, row)))
    last_data = min(footer_rows) - 1 if footer_rows else len(rows) - 1
    for col in writing_columns:
        commands.append(("BACKGROUND", (col, 3), (col, last_data), WRITE))
    table.setStyle(TableStyle(commands))
    return table


class _AdaptivePages:
    def __init__(self, title):
        self.buffer = BytesIO()
        self.canvas = canvas.Canvas(self.buffer, pagesize=A4, pageCompression=1)
        self.canvas.setTitle(title)
        self.canvas.setAuthor('')
        self.title = title
        self.cards = []
        self.y = TOP
        self._page_header()

    def _page_header(self):
        c = self.canvas
        c.setFillColor(INK)
        c.setFont(FONT, 14)
        c.drawString(MARGIN, PAGE_H - 29, self.title)
        c.setFont(FONT, 8)
        c.setFillColor(MUTED)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - 28, "导出 " + today().isoformat())
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.6)
        c.line(MARGIN, PAGE_H - 38, PAGE_W - MARGIN, PAGE_H - 38)

    def _page_footer(self):
        c = self.canvas
        c.setFont(FONT, 7.5)
        c.setFillColor(MUTED)
        c.drawRightString(PAGE_W - MARGIN, 19, f"第 {c.getPageNumber()} 页")

    def _advance(self, section):
        self._page_footer()
        self.canvas.showPage()
        self._page_header()
        self.y = TOP
        self._section(section, continued=True)

    def _section(self, title, continued=False):
        c = self.canvas
        c.setFillColor(LIGHT)
        c.rect(MARGIN, self.y - 28, PAGE_W - 2 * MARGIN, 28, fill=1, stroke=0)
        c.setFillColor(INK)
        c.setFont(FONT, 12)
        c.drawString(MARGIN + 8, self.y - 19, title + ('（续）' if continued else ''))
        c.setStrokeColor(INK)
        c.setLineWidth(1)
        c.line(MARGIN, self.y - 28, PAGE_W - MARGIN, self.y - 28)
        self.y -= 40

    def add_card(self, section, rows, widths, **options):
        self.cards.append((section, rows, widths, options))

    def _build(self, card, width):
        _, rows, widths, options = card
        total = sum(widths)
        inner = width - 8
        rows = [[cell(inner) if callable(cell) else cell for cell in row] for row in rows]
        return _table(rows, [value * width / total for value in widths], **options)

    def _draw_full(self, table, section):
        width = PAGE_W - 2 * MARGIN
        page_space = TOP - BOTTOM - 40
        full_height = table.wrap(width, page_space)[1]
        available = self.y - BOTTOM
        if full_height <= page_space and full_height > available:
            self._advance(section)
        pending = [table]
        while pending:
            current = pending.pop(0)
            available = self.y - BOTTOM
            height = current.wrap(width, available)[1]
            if height <= available:
                current.drawOn(self.canvas, MARGIN, self.y - height)
                self.y -= height + 14
                continue
            pieces = current.split(width, available)
            if not pieces:
                if self.y == TOP - 40:
                    raise ValueError("PDF 中一行内容过长，无法在 A4 页内排版。请缩短该行备注后重试。")
                self._advance(section)
                pending.insert(0, current)
                continue
            first = pieces[0]
            height = first.wrap(width, available)[1]
            first.drawOn(self.canvas, MARGIN, self.y - height)
            self.y -= height + 14
            pending[0:0] = pieces[1:]
            if pending:
                self._advance(section)

    def _layout(self):
        sections = list(dict.fromkeys(card[0] for card in self.cards))
        for number, name in enumerate(sections, 1):
            cards = [card for card in self.cards if card[0] == name]
            section = f'{number:02d}  {name}'
            if number > 1:
                self._page_footer()
                self.canvas.showPage()
                self._page_header()
                self.y = TOP
            initial = [self._build(card, WIDTH) for card in cards[:2]]
            initial_heights = [table.wrap(WIDTH, TOP - BOTTOM - 40)[1] for table in initial]
            if len(initial) == 2 and max(initial_heights) <= 540:
                first_height = max(initial_heights)
            else:
                first_height = self._build(cards[0], PAGE_W - 2 * MARGIN).wrap(PAGE_W - 2 * MARGIN, TOP - BOTTOM - 40)[1]
            required = 40 + min(first_height, TOP - BOTTOM - 40)
            if self.y - BOTTOM < required:
                self._advance(section)
            else:
                self._section(section)
            index = 0
            while index < len(cards):
                pair = cards[index:index + 2]
                tables = [self._build(card, WIDTH) for card in pair]
                heights = [table.wrap(WIDTH, TOP - BOTTOM - 40)[1] for table in tables]
                # Pair ordinary cards. A single/odd card, or one too tall for a
                # useful half-page column, receives the full printable width.
                if len(pair) == 2 and max(heights) <= 540:
                    height = max(heights)
                    if height > self.y - BOTTOM:
                        self._advance(section)
                    for side, (table, card_height) in enumerate(zip(tables, heights)):
                        table.drawOn(self.canvas, MARGIN + side * (WIDTH + GAP), self.y - card_height)
                    self.y -= height + 14
                    index += 2
                else:
                    self._draw_full(self._build(cards[index], PAGE_W - 2 * MARGIN), section)
                    index += 1

    def finish(self):
        self._layout()
        self._page_footer()
        self.canvas.save()
        return self.buffer.getvalue()


def orders_pdf(batches: list, product=None) -> bytes:
    """Export selected independent batches (including immutable imported orders)."""
    if not batches:
        raise ValueError("请选择至少一份配样工单。")
    _font()
    document = _AdaptivePages("配样工单")
    _add_orders(document, anonymize_export_data(batches, product), product_export_context(product))
    return document.finish()


def _add_orders(document, batches, product=None):
    widths = [19, WIDTH - 19 - 47 - 47 - 40, 47, 47, 40]
    for batch in batches:
        formula = batch.get("formula_data") or {}
        result = batch.get("result") or {}
        lines = result.get("lines", [])
        if not lines:
            raise ValueError(f"{batch.get('formula_code', '')} 没有可导出的计划投料。")
        if result.get("ok") is False:
            raise ValueError(f"{batch.get('formula_code', '')} 计算未通过，不能生成正式配样工单。")
        actual = batch.get("actual") or {}
        actual_lines = actual.get("lines") or {}
        revision = batch.get("revision")
        revision_text = str(revision) if revision not in (None, "") else "历史记录"
        if isinstance(revision, (int, float)):
            revision_text = f"R{int(revision):02d}"
        if result.get("historical"):
            revision_text += "（导入档案）"
        batch_code = batch.get("batch_code") or result.get("original_order_number") or "历史导入（原工单号未填写）"
        mass = formula.get("total_mass_g", result.get("totals", {}).get("target_mass_g"))
        solid = formula.get("solid_fraction", result.get("totals", {}).get("solid_fraction"))
        solid_text = _num(float(solid) * 100) + "%" if solid is not None else ""
        metadata = f"配方版本：{revision_text}    工单版本：v{batch.get('plan_revision', 1)}\n日期：{_date(batch.get('planned_date'))}\n目标量：{_num(mass)} g    目标固含：{solid_text}"
        if product:
            metadata = '产品代号：' + str(product['name']) + '\n' + metadata
        conditions = [str(name) + '：' + str(value) for name, value in (formula.get('process_factors') or {}).items()]
        long_conditions = sum(len(text) for text in conditions) > 120
        if conditions:
            metadata += ('\n设计条件：见本批次末尾的固定条件区。' if long_conditions else
                         '\n设计条件（计划）：\n' + '\n'.join(conditions))
        metadata_paragraphs = [_p(metadata, 8.4), _p('批次号：' + str(batch_code), 7.4, MUTED)]
        rows = [
            [_p(batch.get("formula_code") or formula.get("code") or "配样工单", 10.2, colors.white)] + [""] * 4,
            [metadata_paragraphs] + [""] * 4,
            [_p("序", align=TA_CENTER), _p("原料编号"), _p("计划/g", align=TA_RIGHT),
             _p("实际/g", align=TA_RIGHT), _p("复核", align=TA_CENTER)],
        ]
        notes = []
        for index, line in enumerate(lines, 1):
            entered = actual_lines.get(str(line.get("id")), actual_lines.get(line.get("id"), {})) or {}
            rows.append([_p(index, align=TA_CENTER), _p(line.get("source_id", "")),
                         _p(_num(line.get("planned_mass_g")), align=TA_RIGHT),
                         _p(_num(entered.get("mass_g")), align=TA_RIGHT),
                         _p("", align=TA_CENTER)])
            if line.get("note"):
                notes.append(f"{index}. {line['note']}")
        footer_rows = []
        dispensing = instruction_table(batch)
        if dispensing:
            for part in _dispensing_parts(*dispensing):
                footer_rows.append(len(rows))
                rows.append([part] + [''] * 4)
        if long_conditions:
            footer_rows.append(len(rows))
            rows.append([_p('设计条件（计划）', 8.1)] + [''] * 4)
            for condition in conditions:
                # Bounded paragraphs can flow onto the next column/page. No
                # condition is discarded, shortened, or mixed with actual notes.
                for start in range(0, len(condition), 120):
                    footer_rows.append(len(rows))
                    rows.append([_p(condition[start:start + 120], 8.1)] + [''] * 4)
        footer_rows.append(len(rows))
        rows.append([_p("配样：____________    复核：____________", 8.1)] + [""] * 4)
        note = "；".join(notes)
        if actual.get("note"):
            note += ("\n" if note else "") + str(actual["note"])
        if note:
            footer_rows.append(len(rows))
            rows.append([_p("备注：" + note, 7.5, MUTED)] + [""] * 4)
        document.add_card('配样工单', rows, widths, spans=footer_rows,
                          writing_columns=(3, 4), footer_rows=footer_rows)


_DISPENSING_WIDTHS = [.09, .2, .12, .17, .16, .26]


def _dispensing_parts(summary, plan_rows):
    """Spanned card rows: title, header, one small table per plan-row piece, total line."""
    def small(cells):
        def build(width):
            table = Table([[_p(text, 7.6, align=TA_CENTER if i < 4 else TA_LEFT) for i, text in enumerate(cells)]],
                          colWidths=[width * share for share in _DISPENSING_WIDTHS], hAlign='LEFT', splitInRow=1)
            table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .4, BORDER), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                       ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                                       ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]
                                      + ([('BACKGROUND', (0, 0), (-1, -1), LIGHT)] if cells[0] == TABLE_HEADER[0] else [])))
            return table
        return build
    parts = [_p('分装指示', 8.1), small(TABLE_HEADER)]
    for *head, note in plan_rows:
        for index, chunk in enumerate(note_chunks(note)):
            parts.append(small(head + [chunk] if not index else [''] * 5 + [chunk]))
    return parts + [_p(summary, 8.1)]


def preparation_pdf(prep: dict, product=None) -> bytes:
    """Export premix make-up amounts and their component weighing tables."""
    _font()
    document = _AdaptivePages("预混备料工单")
    _add_preparation(document, anonymize_export_data(prep, product), product_export_context(product))
    return document.finish()


def _add_preparation(document, prep, product=None):
    groups = prep.get("groups") or []
    if not groups:
        raise ValueError("没有可导出的预混备料。")
    if prep.get("errors") or any(group.get("errors") for group in groups):
        raise ValueError("预混备料存在未解决的组成或用量错误，不能导出正式备料单。")
    widths = [19, WIDTH - 19 - 43 - 51 - 51, 43, 51, 51]
    for group in groups:
        actual = prep.get('actual') or {}
        entered_group = (actual.get('groups') or {}).get(group.get('key')) or {}
        entered_components = entered_group.get('components') or {}
        components = group.get("components") or []
        if not components:
            raise ValueError(f"{group.get('source_id', '')} 缺少预混组成明细。")
        metadata = (f"备料单：{prep.get('code') or ''}\n日期：{_date(prep.get('created_at'))}"
                    f"\n需求：{_num(group.get('required_g'))} g"
                    f"\n损耗预留：{_num(group.get('extra_percent'))}% + {_num(group.get('extra_g'))} g"
                    f"\n本次配制量：{_num(group.get('make_g'))} g")
        if prep.get('stale'):
            metadata = '已过期\n' + metadata
        if product:
            metadata = '产品代号：' + str(product['name']) + '\n' + metadata
        if entered_group.get('actual_yield_g') is not None:
            metadata += '\n实际产出量：' + _num(entered_group['actual_yield_g']) + ' g'
        rows = [
            [_p(group.get("source_id", "预混备料"), 10.2, colors.white)] + [""] * 4,
            [_p(metadata, 8.4)] + [""] * 4,
            [_p("序", align=TA_CENTER), _p("原料编号"), _p("比例/%", align=TA_RIGHT),
             _p("计划/g", align=TA_RIGHT), _p("实际/g", align=TA_RIGHT)],
        ]
        for index, line in enumerate(components, 1):
            entered = entered_components.get('c' + str(index - 1)) or {}
            rows.append([_p(index, align=TA_CENTER), _p(line.get("source_id", "")),
                          _p(_num(float(line["fraction"]) * 100), align=TA_RIGHT),
                          _p(_num(line.get("planned_mass_g")), align=TA_RIGHT), _p(_num(entered.get('mass_g')), align=TA_RIGHT)])
        footer_rows = [len(rows)]
        rows.append([_p('配制：____________    复核：____________', 8.1)] + [""] * 4)
        sources = group.get("batch_codes") or []
        if sources:
            footer_rows.append(len(rows))
            rows.append([_p("来源工单：" + "、".join(str(s) for s in sources), 7.5, MUTED)] + [""] * 4)
        note = '\n'.join(str(value) for value in (prep.get('note'), actual.get('note'), entered_group.get('note')) if value)
        footer_rows.append(len(rows))
        rows.append([_p(PREMIX_STOCK_REMINDER, 7.5, MUTED)] + [""] * 4)
        if note:
            footer_rows.append(len(rows))
            rows.append([_p("备注：" + note, 7.5, MUTED)] + [""] * 4)
        document.add_card('预混配制工单' + (' · 已过期' if prep.get('stale') else ''), rows, widths, spans=footer_rows, writing_columns=(4,), footer_rows=footer_rows)


def bundle_pdf(batches: list, preparations: list, product=None, project=None) -> bytes:
    """Print premix groups first, then sample orders on separate A4 pages."""
    if not batches and not preparations:
        raise ValueError('请至少选择一份配样工单或预混备料单。')
    _font()
    project_context = project_export_context(project)
    title = '实验工单'
    if project_context:
        title += ' · ' + project_context['name']
    document = _AdaptivePages(title)
    neutral_product = product_export_context(product)
    for prep in preparations:
        _add_preparation(document, anonymize_export_data(prep, product, project), neutral_product)
    if batches:
        _add_orders(document, anonymize_export_data(batches, product, project), neutral_product)
    return document.finish()
