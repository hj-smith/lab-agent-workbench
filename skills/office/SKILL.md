---
name: office
description: Office 文档总入口：做、读、改 Word / PPT / Excel / PDF 和导出图片前先读这个，选下一步读哪个技能；也写着实验室的字体、配色、表格、单位等版式约定。
---

> 改写自 DeepSeek Harness 桌面版 office-skills（MIT 许可），按本工作台的工具和这台机器改过。

# Office 文档：先分流

动手做或改 Office 文件之前，先用 `skill` 读对应格式的技能；只转 PDF、只出图、只重算的，读那一个操作技能就够。

- 做、读、改：`office-docx`（Word）、`office-pptx`（PPT）、`office-xlsx`（Excel）。
- 出图片、看排版，或者这件事主要是版面（间距、大小、对齐、字体、颜色）：`office-render`，就算没人说"看一下"也要读。
- 刷新 Excel 公式的结果：`office-recalculate`。
- 转成 PDF：`office-export-pdf`。
- 只是简单的文字报告（标题、段落、表、图），用 `export` 工具一次出 docx / pdf / html 更快；要 PPT、复杂 Word / Excel 才用这里的做法。

人说了不用渲染，就不渲染。新建文件、改内容、写公式、顺手加粗表头或设数字格式，都不用渲染，除非要看版面。同一个技能不要反复读。

## 这台机器能用的程序（2026-11-09 查过）

| 要做的 | 用什么 | 大约多久 |
| --- | --- | --- |
| docx → PDF | Microsoft Word（COM，不可见） | 15–20 秒 |
| pptx → PDF | Microsoft PowerPoint（COM，不出窗口） | 15 秒 |
| xlsx → PDF | Microsoft Excel（COM，不可见） | 约 70 秒（Excel 导 PDF 慢）；急用 `engine="soffice"` 约 16 秒 |
| pptx → PNG | PowerPoint 直接导出每页 | 15 秒 |
| docx / xlsx / pdf → PNG | 先转 PDF，再 pdftoppm（poppler） | 多 1–2 秒 |
| 重算公式 | Microsoft Excel `CalculateFull`，另存新文件 | 15 秒 |
| 备用 | LibreOffice 26.8（`soffice --headless`，用临时配置，不碰人自己开着的 LibreOffice） | 15–20 秒 |

这些都包在本技能文件夹的 `officekit.py`（Python）和 `office_com.ps1`（PowerShell，COM 部分）里，Office 程序全程不可见、做完关掉；人自己开着的 Word / PPT / Excel 不会被关。

```python
from office import officekit as ok     # 常驻 python 里直接能 import
ok.tools()                              # 看本机有什么
```

shell 里也能用：`& '<本机目录>' '<本机目录>' pdf 报告.docx`（`pdf` / `render` / `recalc` / `tools`）。

都没有的时候：PDF 用 reportlab 直接做（或 `export` 工具出 pdf）；重算做不到，交出带公式的文件并说清"打开 Excel 后才有计算值"；出图做不到，就只做内容和结构检查。

## 实验室版式约定（所有 Office 产出都照这个）

**字体**
- 中文正文"微软雅黑"；标题、图、表可用"等线"；西文和数字 Arial。本机有：微软雅黑（msyh）、等线（Deng）、黑体、宋体、Arial。
- Word 里中文字体要同时设 `w:eastAsia`（`ok.cn_font(run_or_style)`，新文档用 `ok.docx_lab_styles(doc)`）；PPT 里要设 `a:ea`（`ok.pptx_text(...)`）。只设 `font.name` 中文不会变。
- matplotlib 先 `ok.style_matplotlib()`：中文字体、颜色、底色、线宽一次设好。

**图**
- 颜色顺序：`#2a78d6, #eb6834, #1baf7a, #eda100, #e87ba4, #008300, #6250d6, #e34948`；第 9 条线起用灰色（`ok.color(i)`）。
- 图底色 `#fcfcfb`，线宽 2。
- 不用双轴：量纲不同的量分两张图（上下排或左右排）。
- 一张图的线太多就拆开；同一个意思在整份文件里用同一个颜色（例：A 路线一直是蓝色）。
- 坐标轴写"量 / 单位"，例：`反应时间 / h`、`Mw（PS 当量）/ (g/mol)`。
- 出图存 PNG，`savefig.dpi` 200；放进 Word 宽 15 cm 左右，放进 PPT 占页面大部分。

**表**
- 三线表：顶线、表头下线、底线，其它不画线（Word 用 `ok.three_line_table(table)`）。表头加粗，表题在表上方（"表 1  …"），图题在图下方（"图 1  …"）。
- 数字带单位：单位写在表头里（`质量 / g`），或紧跟数字（`16 h`、`140 °C`）。数字和单位之间空一格，% 和 ° 例外。
- 同一列小数位一致；千位以上可用空格分组（`9 350`）。

**写法**
- GPC 分子量一律写"（PS 当量）"：`Mw（PS 当量）9 350 g/mol`，不能写成真实分子量。
- 时间写北京时间，带"（北京时间）"或明确时区：`2026-11-09 14:30（北京时间）`。不管电脑是什么时区。
- 文件名：`编号_实验名称_内容_版本`，例 `R100-42_升温路线对比_GPC报告_v1.docx`。

**报告类**
- 技术报告写全：结论（放最前面）、数据（表和图）、方法（仪器、条件、计算方法）、出处（产品库、记录 id、原始文件、文献 DOI）。不要只剩结论。
- 事实和推测分开写；没验证的写明"未验证"。
- PPT 每页一个意思，标题就是这页的结论（"16 h 时 B 比 A 高约 18%"，不是"GPC 结果"）；图大字少，正文 ≥ 18 pt，一页不超过 6 行字。
- Excel 给人用：首行表头冻结，列宽够宽不截字，公式保留（不要换成死数），原始数据、参数、计算、结果分开放（分表或分区）。

## 交付

做好的文件留在工作区原地，用 `outputs`（action=attach，path=文件，record=课题/实验/样品）登记成产出。检查用的图片、临时 PDF 不登记，除非人要。
