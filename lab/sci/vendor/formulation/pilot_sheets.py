"""迁移时新写的小文件：原 pilot_sheets.py（中试离线工作簿，从没用过）不搬，这里只留 doe_report 导出 xlsx 用到的 _style 和 HEAD_FILL，原文照抄。"""
from openpyxl.styles import Alignment, Font, PatternFill

HEAD_FILL = PatternFill('solid', fgColor='DCE6F1')


def _style(ws, header_row, ncols, widths=None):
    for cell in ws[header_row][:ncols]:
        cell.font = Font(bold=True)
        cell.fill = HEAD_FILL
        cell.alignment = Alignment(wrap_text=True, vertical='center')
    for i in range(ncols):
        letter = ws.cell(row=header_row, column=i + 1).column_letter
        values = [len(str(c.value)) for c in ws[letter] if c.value is not None and c.row >= header_row and len(str(c.value)) < 60]
        ws.column_dimensions[letter].width = min(30, max(8, (max(values) if values else 8) * 1.6))
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f'{header_row}:{header_row}'

