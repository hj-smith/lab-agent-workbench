---
name: office-docx
description: 做、读、改 Word 文档（.docx）：python-docx 写法、中文字体、三线表、插图、改字不丢格式，做完重新打开检查。
---

> 改写自 DeepSeek Harness 桌面版 office-skills 的 office-docx（MIT 许可）。

# Word 文档

用常驻 `python` 里的 python-docx（1.2）。不用另找 Python、不用装包。先读过 `office` 技能里的版式约定。

脚本、中间文件、成品都放在当前工作区；技能文件夹是只读的。用人要的语言；改已有文档时保留它原来的设计，除非人要重新设计。
自己写的 .py 文件按任务起名（如 `edit_report.py`），不要叫 `docx.py`、`inspect.py`、`zipfile.py`，会挡住同名的库。

## 读

`files`（action=read）能直接读 docx 的文字。要看样式、表格结构时用 python-docx 打开看对应的段落和 run。

## 做和改

- **改字**：按文字或位置找到目标段落，只看它的 run 和格式，改对应的 run。给 `paragraph.text` 赋值会把这段的格式全冲掉。标题可能只是普通段落，没有"标题"样式。
- 默认另存新文件（`…_v2.docx`），人要求原地改才覆盖。
- 普通改字时不要解压 docx、列 ZIP 成员、倒原始 XML、看与目标无关的页眉页脚和属性。只有遇到具体问题、目标不明确、python-docx 不支持的功能，或人明确要求时才去看。重建文档会丢掉不支持的内容。
- 标题和正文用段落样式（Title、Heading 1/2/3、Normal），不要每段手动设字号。
- 表格宽度按所在节的版心算，注意合并单元格和嵌套表。
- 中文要设 `w:eastAsia` 字体，只设 `run.font.name` 不够；字体名写对了也不代表机器上有这个字，看渲染图才知道。
- python-docx 不分页、不渲染。不要把普通的替换、彩色字、批注做成修订（tracked changes）。有真实修订或不支持的 OOXML 功能时，保留原来的部件并核对要做的操作，不要悄悄丢掉。
- 直接写包里的部件时，新建一个 ZIP，把没改的成员各复制一次；往原 ZIP 里追加同名成员会让文件损坏。

新文档的骨架（套实验室字体、三线表）：

```python
from docx import Document
from docx.shared import Cm
from docx.enum.text import WD_BREAK
from office import officekit as ok

doc = Document()
ok.docx_lab_styles(doc)                       # 正文微软雅黑 10.5 pt，标题等线加粗
doc.add_paragraph('R100 两条升温路线的分子量增长', style='Title')
doc.add_paragraph('报告时间：2026-11-09 14:30（北京时间）')
doc.add_heading('结论', 1)
doc.add_paragraph('B 路线 16 h 的 Mw（PS 当量）比 A 路线高约 18%。')
doc.add_heading('数据', 1)
doc.add_paragraph('表 1  各取样点的分子量（PS 当量）')
rows = [('样品', '反应时间 / h', 'Mw / (g/mol)'), ('A-16', '16', '7 920'), ('B-16', '16', '9 350')]
table = doc.add_table(rows=len(rows), cols=3)
for r, row in enumerate(rows):
    for c, value in enumerate(row):
        table.cell(r, c).text = value
ok.three_line_table(table)                    # 三线表、表头加粗、单元格居中
doc.add_picture('mw.png', width=Cm(15))       # 图先用 ok.style_matplotlib() 画
doc.add_paragraph('图 1  Mw（PS 当量）随反应时间变化')
doc.add_heading('方法', 1)
doc.add_heading('出处', 1)
doc.save('R100-42_升温路线对比_GPC报告_v1.docx')
```

要换页：`doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)`。页边距：`doc.sections[0].left_margin = Cm(2.5)`。

## 检查和交付

1. 用 python-docx 重新打开存好的文件，核对：要的内容在、相关格式对、不该动的没动。过了就停，不做 ZIP 完整性、部件清单、元数据这类例行检查。
2. 只是新建或改内容、顺手设了基本格式的，到这里就可以交。
3. 人要图片或要看版面，或者这次改的就是样式、字色、间距、大小、对齐：读 `office-render`，渲染改动的那页看一下（人说不用就不看）。
4. 要 PDF：读 `office-export-pdf`。
5. 用 `outputs`（action=attach）登记最终的 docx，文件留在原地；检查用的图和临时文件不登记，除非人要。
