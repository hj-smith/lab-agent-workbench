---
name: office-pptx
description: 做、读、改 PPT（.pptx）：python-pptx 写法、16:9 版面、中文字体、原生图表或 matplotlib 图、每页一个意思，做完检查文字溢出和版面。
---

> 改写自 DeepSeek Harness 桌面版 office-skills 的 office-pptx（MIT 许可）。

# PPT 演示文稿

用常驻 `python` 里的 python-pptx（1.0.2）。不用另找 Python、不用装包。先读过 `office` 技能里的版式约定。

脚本和成品放在当前工作区；技能文件夹是只读的。用人要的语言；改已有的 PPT 时照它原来的设计。
自己写的 .py 按任务起名（如 `make_deck.py`），不要叫 `pptx.py`、`inspect.py`、`zipfile.py`。

## 做和改

- **改已有的**：先看版式（layout）、文本 run、图片、表格、图表，再只改要改的；保留一段里混合的格式；默认另存新文件。重建幻灯片会丢掉动画、SmartArt 等不支持的内容。
- **新做**：用 python-pptx 做可编辑的文字、表格、图表。图片用本地文件，不用网络地址。页面尺寸、字号、图表数据都显式写出来。
- 位置和大小用 `Inches` 或 `Cm`，字号用 `Pt`。
- 中文字体要设 `a:ea`，用 `ok.pptx_text(...)` 写字就一起设好了。
- 段落居中不会移动文本框：标题要在页面上居中，段落居中，同时把文本框按 `prs.slide_width` 放到正中，重新打开核对两样。
- 原生图表（`add_chart`）里嵌的工作簿也是交付的一部分，里面要有对的数据。科研曲线（点多、要对数轴、要误差棒）用 matplotlib 画成 PNG 再放进来更好看、更可控；简单柱状对比可以用原生图表，方便人改。
- python-pptx 能写 PPT，但不是渲染器，不保证每个 PowerPoint 功能改完都还在。

**实验室的 PPT**：16:9（13.333 × 7.5 英寸）；每页一个意思，标题写这页的结论；图大字少，正文 ≥ 18 pt，标题 28–32 pt，一页不超过 6 行字；页面背景可设成 `#fcfcfb` 和图底色一致；颜色按约定的顺序；不用花哨的装饰图。

```python
from pptx import Presentation
from pptx.util import Inches
from pptx.dml.color import RGBColor
from office import officekit as ok

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
blank = prs.slide_layouts[6]

def page(title):
    slide = prs.slides.add_slide(blank)
    slide.background.fill.solid(); slide.background.fill.fore_color.rgb = RGBColor.from_string('FCFCFB')
    ok.pptx_text(slide.shapes.add_textbox(Inches(0.6), Inches(0.35), Inches(12.1), Inches(0.9)), title, size=30, bold=True)
    return slide

s = page('16 h 时 B 路线的 Mw（PS 当量）比 A 高约 18%')
s.shapes.add_picture('mw.png', Inches(1.0), Inches(1.4), width=Inches(11.3))   # 图先用 ok.style_matplotlib() 画
s = page('下一步')
box = s.shapes.add_textbox(Inches(0.8), Inches(1.6), Inches(11.5), Inches(4))
for line in ['B 路线 20 h、24 h 补测 GPC', '两条路线各做一次涂膜，比较膜厚']:
    ok.pptx_text(box, '· ' + line, size=24)
prs.save('R100-42_升温路线对比_汇报_v1.pptx')
```

改已有标题、保留其它页：

```python
prs = Presentation('汇报.pptx')
for shape in prs.slides[0].shapes:
    if shape.has_text_frame and shape.text_frame.text == '旧标题':
        shape.text_frame.paragraphs[0].runs[0].text = '新标题'
prs.save('汇报_v2.pptx')
```

## 检查和交付

1. 用 python-pptx 重新打开，核对页数、内容、格式、图表数据、备注，以及不该动的内容。这一步看不出文字是否放得下、对比度、渲染效果。
2. `ok.pptx_check('文件.pptx')`：列出超出页面的形状、可能溢出的文本框、太小的字、字太多的页。有问题就改。
3. 只是新建或改内容、顺手设了基本格式的，到这里就可以交。人要图片或要看版面，或这次改的就是样式、间距、大小、对齐：读 `office-render` 出图检查（PowerPoint 直接导出每页 PNG）。新做的整套汇报 PPT，建议渲染一次全部页面检查。
4. 要 PDF：读 `office-export-pdf`。
5. 用 `outputs`（action=attach）登记最终的 pptx，文件留在原地；检查用的图不登记，除非人要。
