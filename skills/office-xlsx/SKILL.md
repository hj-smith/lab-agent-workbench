---
name: office-xlsx
description: 做、读、改 Excel（.xlsx）：openpyxl / XlsxWriter / pandas 各用在哪、保留公式和格式、类型不乱、列宽不截字，做完重新打开检查；公式结果要刷新时接 office-recalculate。
---

> 改写自 DeepSeek Harness 桌面版 office-skills 的 office-xlsx（MIT 许可）。

# Excel 工作簿

用常驻 `python` 里的 openpyxl（3.1.5）、XlsxWriter（3.3.1）、pandas。不用另找 Python、不用装包。先读过 `office` 技能里的版式约定。

脚本、中间文件、成品都放在当前工作区；技能文件夹是只读的。默认另存新工作簿，人要求原地改才覆盖。
自己写的 .py 按任务起名（如 `edit_feed.py`），不要叫 `openpyxl.py`、`pandas.py`、`inspect.py`。

## 读和改

- `files`（action=read，sheet=表名）能直接读表格内容。
- 已有的 xlsx 和指定单元格、样式的改动用 openpyxl。要保留公式就用 `data_only=False` 打开。改之前先看：表名、要改的单元格类型、公式、样式、合并区域、被引用的区域。存好后重新打开核对，包括不该动的内容。
- 数据分析和变换用 pandas。DataFrame 不是工作簿：把它导出覆盖原文件会丢掉别的表、公式、图表和格式。分析结果用 openpyxl 写回指定区域。
- XlsxWriter 只能新建，不能读或改已有文件；新建、要漂亮格式和原生图表时它很好用。
- 数字、日期、布尔、编号保持原本的类型；设格式不等于换类型（`'16'` 和 `16` 不一样）。
- 写公式不会算出结果。openpyxl 和 XlsxWriter 都不计算公式；`data_only=True` 读到的是文件里缓存的值，可能没有或者是旧的。要刷新结果：读 `office-recalculate`。不要把人要的公式换成死数，不要把旧缓存说成新算的。
- `.xls`、`.xlsb`、加密文件、带宏的文件不要改后缀当 xlsx 用。`keep_vba=True` 能在 xlsm 里保留宏的包内容，但不运行、不改宏，也不保证所有高级功能都留得住。保留原件，逐项核对。

## 新建（实验室写法）

```python
from openpyxl import Workbook
from openpyxl.styles import Font, Border, Side, Alignment

wb = Workbook()
ws = wb.active
ws.title = '投料'
ws.append(['物料', '质量 / g', '摩尔质量 / (g/mol)', '物质的量 / mol'])
for i, (name, mass, molar) in enumerate([('单体 A', 120.5, 136.2), ('单体 B', 88.0, 150.1)], start=2):
    ws.append([name, mass, molar, f'=B{i}/C{i}'])
ws.append(['合计', '=SUM(B2:B3)', None, '=SUM(D2:D3)'])
head = Font(name='微软雅黑', bold=True)
for cell in ws[1]:
    cell.font, cell.alignment = head, Alignment(horizontal='center', vertical='center', wrap_text=True)
    cell.border = Border(top=Side('medium'), bottom=Side('thin'))       # 三线表：顶线、表头下线
for cell in ws[ws.max_row]:
    cell.border = Border(bottom=Side('medium'))                          # 底线
for row in ws.iter_rows(min_row=2):
    for cell in row:
        cell.font = Font(name='微软雅黑')
for column, width in zip('ABCD', (14, 12, 20, 16)):                      # 列宽要够，中文一个字约 2 宽
    ws.column_dimensions[column].width = width
for cell in ws['D'][1:]:
    cell.number_format = '0.0000'                                         # 同一列小数位一致
ws.freeze_panes = 'A2'
wb.save('R100-42_投料计算_v1.xlsx')
```

- 单位写在表头里；原始数据、参数、计算、结果分表或分区放；参数放单独的格子，公式引用它，不把数字写死在公式里。
- 图表：XlsxWriter 或 openpyxl 的原生图表方便人改；颜色按约定顺序设。

## 检查和交付

1. 用 openpyxl 重新打开，核对表名、值、类型、公式、样式、合计，和源数据对上，不该动的没动。这一步不计算公式，也看不出外观。
2. `ok.xlsx_check('文件.xlsx')`（`from office import officekit as ok`）列出字比列宽长、会被截断的单元格，有就加宽列或换行。
3. 公式结果要刷新：读 `office-recalculate`（新做的、带公式的给人用的表，建议都刷新一次，人用别的软件预览时才看得到数）。
4. 人要图片或要看版面，或者这次改的主要是外观（底色、对齐、间距、大小）：读 `office-render`。新建表、改数据和公式时顺手加粗表头、设数字格式，不用渲染。
5. 要 PDF：读 `office-export-pdf`。
6. 用 `outputs`（action=attach）登记最终的工作簿，文件留在原地；中间脚本和报告不登记，除非人要。
