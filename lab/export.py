"""导出给人看的东西：Excel 表、Word / PDF / 网页报告、CSV。样式统一在这里，智能体只给内容。

一份报告是一串"块"：
    {'type': 'heading', 'text': …, 'level': 1 或 2}
    {'type': 'text', 'text': …}                          一段或几段话（空行分段）
    {'type': 'list', 'items': [...]}
    {'type': 'table', 'columns': [...], 'rows': [[...]], 'caption': …}
    {'type': 'chart', 'title': …, 'series': [{name, x, y}], 'kind': line|scatter|bar, 'x_title': …, 'y_title': …, 'log_x', 'log_y', 'note': …}
    {'type': 'image', 'file': 'a.png', 'caption': …}
同一份内容可以同时出几种格式，每种按自己的版面排。报告里的图是静态图（打印、发给别人都能看）；要能缩放的图用 make_chart。
"""
import base64
import csv
import html
import io
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .charts import COLORS, REST

INK, MUTED, RULE, HEAD, ACCENT = '#29261f', '#6b665c', '#e4ded3', '#f3efe8', '#c96442'      # 配色 B：炭黑字、陶土色强调
FONT_FILES = [('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/msyhbd.ttc'), ('C:/Windows/Fonts/simhei.ttf', 'C:/Windows/Fonts/simhei.ttf')]
FORMATS = ('xlsx', 'docx', 'pdf', 'html', 'csv')


def _today():
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')


def _stem(text):
    return re.sub(r'[\\/:*?"<>|\s]+', '_', str(text)).strip('_') or '导出'


def _table(value):
    """表格的几种给法都收：{columns, rows}、[字典, …]、(列名, 行)。给出 (列名, 行)。"""
    if isinstance(value, dict):
        columns, rows = value.get('columns') or [], value.get('rows') or []
    elif isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(item, (list, tuple)) for item in value) \
            and value[1] and isinstance(value[1][0], (list, tuple)):
        columns, rows = value
    else:
        columns, rows = [], list(value or [])
    if rows and isinstance(rows[0], dict):
        columns = list(columns) or list(dict.fromkeys(key for row in rows for key in row))
        rows = [[row.get(key) for key in columns] for row in rows]
    return [str(item) for item in columns], [list(row) for row in rows]


def _cell(value):
    if value is None:
        return ''
    if isinstance(value, float):
        return f'{value:.6g}'
    return str(value)


def excel(folder, name, sheets):
    """一个 Excel 文件。sheets: {表名: 表格}。表头加粗、冻结、可筛选，列宽按内容。数字按原值存，不截断。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    book = Workbook()
    book.remove(book.active)
    for title, value in sheets.items():
        columns, rows = _table(value)
        sheet = book.create_sheet(re.sub(r'[\\/*?:\[\]]', '_', str(title))[:31] or '表')
        sheet.append(columns)
        for row in rows:
            sheet.append([item if isinstance(item, (int, float, str, bool)) or item is None else str(item) for item in row])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='1A1A1A', name='微软雅黑')
            cell.fill = PatternFill('solid', fgColor='EEF3F0')
            cell.alignment = Alignment(vertical='center', wrap_text=True)
            cell.border = Border(bottom=Side(style='thin', color='9FB0A6'))
        for index, column in enumerate(columns, 1):
            width = max([sum(2 if ord(char) > 255 else 1 for char in _cell(item)) for item in [column] + [row[index - 1] for row in rows[:300] if index <= len(row)]] or [8])
            sheet.column_dimensions[get_column_letter(index)].width = min(max(width + 3, 9), 60)
        sheet.freeze_panes = 'A2'
        if columns and rows:
            sheet.auto_filter.ref = sheet.dimensions
    path = Path(folder) / (_stem(name).removesuffix('.xlsx') + '.xlsx')
    book.save(path)
    return path.name


def figure(block, dpi=200):
    """报告里的一张静态图（PNG 字节）。颜色和线型跟网页图一致。"""
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot

    pyplot.rcParams.update({'font.family': ['Microsoft YaHei', 'DejaVu Sans'], 'axes.unicode_minus': False, 'font.size': 10})
    plot, axis = pyplot.subplots(figsize=(7.2, 4.1), dpi=dpi)
    series, kind = block.get('series') or [], block.get('kind') or 'line'
    for index, item in enumerate(series):
        color = COLORS[index] if index < len(COLORS) else REST
        x, y, label = item.get('x') or [], item.get('y') or [], str(item.get('name') or f'系列{index + 1}')
        if kind == 'bar':
            width = 0.8 / max(len(series), 1)
            axis.bar([position + index * width - 0.4 + width / 2 for position in range(len(x))], y, width=width * 0.92, color=color, label=label)
            axis.set_xticks(range(len(x)), [str(value) for value in x])
        elif kind == 'scatter':
            axis.scatter(x, y, s=26, color=color, label=label, edgecolors='white', linewidths=0.8, zorder=3)
        else:
            axis.plot(x, y, color=color, linewidth=1.8, label=label, marker='o' if len(x) <= 40 else None, markersize=4.5,
                      markeredgecolor='white', markeredgewidth=0.8)
    axis.set_xlabel(block.get('x_title') or '', color=MUTED)
    axis.set_ylabel(block.get('y_title') or '', color=MUTED)
    if block.get('log_x'):
        axis.set_xscale('log')
    if block.get('log_y'):
        axis.set_yscale('log')
    axis.grid(True, color='#e9e8e4', linewidth=0.8)
    axis.set_axisbelow(True)
    for side in ('top', 'right'):
        axis.spines[side].set_visible(False)
    for side in ('left', 'bottom'):
        axis.spines[side].set_color('#c9c8c4')
    axis.tick_params(colors=MUTED)
    if block.get('title'):
        axis.set_title(block['title'], loc='left', fontsize=12, fontweight='bold', color=INK, pad=10)
    if len(series) > 1:
        axis.legend(frameon=False, fontsize=9, ncol=min(len(series), 4), loc='upper center', bbox_to_anchor=(0.5, -0.16))
    plot.tight_layout()
    data = io.BytesIO()
    plot.savefig(data, format='png', facecolor='white')
    pyplot.close(plot)
    return data.getvalue()


def _picture(folder, block):
    if block['type'] == 'chart':
        return figure(block), block.get('note') or ''
    return (Path(folder) / block['file']).read_bytes(), block.get('caption') or ''


def _paragraphs(text):
    return [part.strip() for part in re.split(r'\n\s*\n', str(text or '')) if part.strip()]


def _html(folder, title, subtitle, blocks):
    out, tables, figures = [], 0, 0
    for block in blocks:
        kind = block['type']
        if kind == 'heading':
            out.append(f'<h{2 if block.get("level", 1) == 1 else 3}>{html.escape(block["text"])}</h{2 if block.get("level", 1) == 1 else 3}>')
        elif kind == 'text':
            out += [f'<p>{html.escape(part).replace(chr(10), "<br>")}</p>' for part in _paragraphs(block['text'])]
        elif kind == 'list':
            out.append('<ul>' + ''.join(f'<li>{html.escape(str(item))}</li>' for item in block['items']) + '</ul>')
        elif kind == 'table':
            columns, rows = _table(block)
            tables += 1
            caption = f'<caption>表 {tables}　{html.escape(block["caption"])}</caption>' if block.get('caption') else ''
            out.append(f'<div class="wrap"><table>{caption}<thead><tr>' + ''.join(f'<th>{html.escape(item)}</th>' for item in columns) +
                       '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{html.escape(_cell(item))}</td>' for item in row) + '</tr>'
                                                        for row in rows) + '</tbody></table></div>')
        elif kind in ('chart', 'image'):
            data, caption = _picture(folder, block)
            figures += 1
            out.append(f'<figure><img src="data:image/png;base64,{base64.b64encode(data).decode()}" alt="">'
                       f'<figcaption>图 {figures}{"　" + html.escape(caption) if caption else ""}</figcaption></figure>')
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{margin:0;background:#f4f2ec;color:{INK};font:15px/1.75 "Microsoft YaHei","Segoe UI",sans-serif}}
main{{max-width:980px;margin:0 auto;background:#fff;padding:48px 56px 64px;min-height:100vh;box-sizing:border-box}}
h1{{font-size:26px;margin:0 0 6px;line-height:1.3}} .sub{{color:{MUTED};margin:0 0 28px;padding-bottom:16px;border-bottom:2px solid {ACCENT}}}
h2{{font-size:19px;margin:34px 0 10px}} h3{{font-size:16px;margin:24px 0 8px}} p{{margin:10px 0}} ul{{margin:8px 0;padding-left:22px}}
.wrap{{overflow-x:auto;margin:14px 0}} table{{border-collapse:collapse;font-size:13.5px;font-variant-numeric:tabular-nums;min-width:50%}}
caption{{caption-side:top;text-align:left;color:{MUTED};font-size:13px;padding-bottom:6px}}
th{{background:{HEAD};text-align:left;font-weight:600;border-bottom:1.5px solid #cbbfae;padding:7px 14px 7px 10px;white-space:nowrap}}
td{{border-bottom:1px solid {RULE};padding:6px 14px 6px 10px;vertical-align:top}}
figure{{margin:18px 0}} figure img{{max-width:100%;border:1px solid {RULE};border-radius:6px}} figcaption{{color:{MUTED};font-size:13px;margin-top:4px}}
@media print{{body{{background:#fff}} main{{padding:0;max-width:none}}}} @media(max-width:700px){{main{{padding:24px 18px}}}}
</style></head><body><main><h1>{html.escape(title)}</h1><p class="sub">{html.escape(subtitle)}</p>
{chr(10).join(out)}</main></body></html>'''


def _docx(folder, title, subtitle, blocks, path):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    document = Document()
    for section in document.sections:
        section.left_margin = section.right_margin = Cm(2.2)
        section.top_margin = section.bottom_margin = Cm(2.2)
    for name in ('Normal', 'Title', 'Heading 1', 'Heading 2'):
        style = document.styles[name]
        style.font.name = '微软雅黑'
        style.element.rPr.rFonts.set(qn('w:eastAsia'), '微软雅黑')
        style.font.color.rgb = RGBColor(0x29, 0x26, 0x1f)
    document.styles['Normal'].font.size = Pt(10.5)
    document.styles['Title'].font.size = Pt(20)
    document.styles['Heading 1'].font.size = Pt(14)
    document.styles['Heading 2'].font.size = Pt(12)
    document.add_paragraph(title, style='Title')
    note = document.add_paragraph(subtitle)
    note.runs[0].font.color.rgb = RGBColor(0x6b, 0x66, 0x5c)
    width = document.sections[0].page_width - document.sections[0].left_margin - document.sections[0].right_margin
    tables = figures = 0

    def caption(text):
        line = document.add_paragraph(text)
        line.runs[0].font.size = Pt(9)
        line.runs[0].font.color.rgb = RGBColor(0x6b, 0x66, 0x5c)
        return line

    for block in blocks:
        kind = block['type']
        if kind == 'heading':
            document.add_paragraph(block['text'], style='Heading 1' if block.get('level', 1) == 1 else 'Heading 2')
        elif kind == 'text':
            for part in _paragraphs(block['text']):
                document.add_paragraph(part)
        elif kind == 'list':
            for item in block['items']:
                document.add_paragraph(str(item), style='List Bullet')
        elif kind == 'table':
            columns, rows = _table(block)
            tables += 1
            if block.get('caption'):
                caption(f'表 {tables}　{block["caption"]}')
            grid = document.add_table(rows=1, cols=max(len(columns), 1))
            grid.style = 'Table Grid'
            for index, text in enumerate(columns):
                cell = grid.rows[0].cells[index]
                cell.text = text
                cell.paragraphs[0].runs[0].font.bold = True
                shade = OxmlElement('w:shd')
                shade.set(qn('w:val'), 'clear'); shade.set(qn('w:color'), 'auto'); shade.set(qn('w:fill'), 'F3EFE8')
                cell._tc.get_or_add_tcPr().append(shade)
            for row in rows:
                cells = grid.add_row().cells
                for index, item in enumerate(row[:len(cells)]):
                    cells[index].text = _cell(item)
            for row in grid.rows:
                for cell in row.cells:
                    for line in cell.paragraphs:
                        for run in line.runs:
                            run.font.size = Pt(9)
            document.add_paragraph()
        elif kind in ('chart', 'image'):
            data, text = _picture(folder, block)
            figures += 1
            document.add_picture(io.BytesIO(data), width=min(width, Cm(15.5)))
            document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            caption(f'图 {figures}{"　" + text if text else ""}').alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.save(path)


def _pdf(folder, title, subtitle, blocks, path):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, KeepTogether, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    if 'LabSans' not in pdfmetrics.getRegisteredFontNames():
        regular, bold = next((pair for pair in FONT_FILES if Path(pair[0]).is_file()), FONT_FILES[0])
        pdfmetrics.registerFont(TTFont('LabSans', regular, subfontIndex=0))
        pdfmetrics.registerFont(TTFont('LabSans-Bold', bold, subfontIndex=0))
    ink, muted = colors.HexColor(INK), colors.HexColor(MUTED)
    body = ParagraphStyle('body', fontName='LabSans', fontSize=10.5, leading=17, textColor=ink, wordWrap='CJK')
    small = ParagraphStyle('small', parent=body, fontSize=9, leading=13, textColor=muted)
    cell = ParagraphStyle('cell', parent=body, fontSize=8.5, leading=12)
    head = ParagraphStyle('head', parent=cell, fontName='LabSans-Bold')
    styles = {0: ParagraphStyle('title', parent=body, fontName='LabSans-Bold', fontSize=20, leading=28),
              1: ParagraphStyle('h1', parent=body, fontName='LabSans-Bold', fontSize=14, leading=22, spaceBefore=14, spaceAfter=4, ),
              2: ParagraphStyle('h2', parent=body, fontName='LabSans-Bold', fontSize=12, leading=19, spaceBefore=10, spaceAfter=2)}
    document = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2.1 * cm, rightMargin=2.1 * cm, topMargin=2 * cm, bottomMargin=2 * cm, title=title)
    safe = lambda text: html.escape(str(text)).replace('\n', '<br/>')
    rule = Table([['']], colWidths=[document.width], rowHeights=[4])
    rule.setStyle(TableStyle([('LINEBELOW', (0, 0), (-1, -1), 1.5, colors.HexColor(ACCENT))]))
    flow = [Paragraph(safe(title), styles[0]), Paragraph(safe(subtitle), small), rule, Spacer(1, 10)]
    tables = figures = 0
    pending = []                                       # 标题先攒着，和后面那一块放在一起，不落单在页底

    def put(parts, keep=True):
        nonlocal pending
        flow.extend([KeepTogether(pending + parts)] if keep else pending + parts)     # 长表本来就会跨页，不整块挪
        pending = []

    for block in blocks:
        kind = block['type']
        if kind == 'heading':
            pending.append(Paragraph(safe(block['text']), styles[1 if block.get('level', 1) == 1 else 2]))
        elif kind == 'text':
            texts = [item for part in _paragraphs(block['text']) for item in (Paragraph(safe(part), body), Spacer(1, 5))]
            put(texts[:2], keep=bool(pending)); flow.extend(texts[2:])
        elif kind == 'list':
            put([ListFlowable([ListItem(Paragraph(safe(item), body), leftIndent=14) for item in block['items']],
                              bulletType='bullet', bulletFontSize=7, leftIndent=14)], keep=bool(pending))
        elif kind == 'table':
            columns, rows = _table(block)
            tables += 1
            head_line = [Paragraph(safe(f'表 {tables}　{block["caption"]}'), small)] if block.get('caption') else []
            weights = [min(max([sum(2 if ord(char) > 255 else 1 for char in _cell(item)) for item in [column] + [row[index] for row in rows[:200] if index < len(row)]]), 40) + 2
                       for index, column in enumerate(columns)] or [1]
            widths = [document.width * weight / sum(weights) for weight in weights]
            grid = Table([[Paragraph(safe(item), head) for item in columns]] +
                         [[Paragraph(safe(_cell(item)), cell) for item in (row + [''] * len(columns))[:len(columns)]] for row in rows],
                         colWidths=widths, repeatRows=1)
            grid.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(HEAD)), ('LINEBELOW', (0, 0), (-1, 0), 0.9, colors.HexColor('#cbbfae')),
                                      ('LINEBELOW', (0, 1), (-1, -1), 0.4, colors.HexColor(RULE)), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                      ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
            put(head_line + [Spacer(1, 3), grid, Spacer(1, 10)], keep=len(rows) <= 25)          # 短表和表名、标题放在同一页
        elif kind in ('chart', 'image'):
            data, text = _picture(folder, block)
            figures += 1
            picture = Image(io.BytesIO(data))
            scale = min(document.width / picture.imageWidth, 9.5 * cm / picture.imageHeight)
            picture.drawWidth, picture.drawHeight = picture.imageWidth * scale, picture.imageHeight * scale
            put([Spacer(1, 4), picture, Paragraph(safe(f'图 {figures}{"　" + text if text else ""}'), small), Spacer(1, 8)])   # 图、图注、标题不分开
    flow.extend(pending)
    document.build(flow)


def report(folder, title, blocks, formats=('html',), name=None, subtitle=''):
    """把一份内容导出成几种格式，返回文件名清单。xlsx / csv 只收里面的表格（一张表一个工作表 / 一个文件）。"""
    folder, stem = Path(folder), _stem(name or title)
    blocks = [{'type': 'text', 'text': block} if isinstance(block, str) else dict(block) for block in blocks or []]
    subtitle = subtitle or f'导出日期 {_today()}（北京时间）'
    formats = [formats] if isinstance(formats, str) else list(formats)
    tables = [block for block in blocks if block['type'] == 'table']
    made = []
    for kind in formats:
        kind = kind.lower().lstrip('.')
        if kind == 'html':
            (folder / f'{stem}.html').write_text(_html(folder, title, subtitle, blocks), encoding='utf-8')
            made.append(f'{stem}.html')
        elif kind == 'docx':
            _docx(folder, title, subtitle, blocks, folder / f'{stem}.docx')
            made.append(f'{stem}.docx')
        elif kind == 'pdf':
            _pdf(folder, title, subtitle, blocks, folder / f'{stem}.pdf')
            made.append(f'{stem}.pdf')
        elif kind == 'xlsx':
            made.append(excel(folder, stem, {block.get('caption') or f'表{index}': block for index, block in enumerate(tables, 1)}))
        elif kind == 'csv':
            for index, block in enumerate(tables, 1):
                columns, rows = _table(block)
                target = folder / (f'{stem}.csv' if len(tables) == 1 else f'{stem}-{_stem(block.get("caption") or index)}.csv')
                with target.open('w', encoding='utf-8-sig', newline='') as file:
                    csv.writer(file).writerows([columns] + rows)
                made.append(target.name)
        else:
            raise ValueError(f'不会导出 {kind}。可用：{"、".join(FORMATS)}')
    return made
