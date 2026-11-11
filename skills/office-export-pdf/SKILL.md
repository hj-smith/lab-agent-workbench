---
name: office-export-pdf
description: 把 Word / PPT / Excel 转成 PDF：用本机 Word、PowerPoint、Excel（COM，不可见），没有时用 LibreOffice；都没有时用 reportlab 直接做 PDF。
---

> 改写自 DeepSeek Harness 桌面版 office-skills 的 office-export-pdf（MIT 许可）。原件用 LibreOffice Kit；这里改用本机 Office。

# Office 文件转 PDF

把存好的最终文件转成一个新的 PDF，放在工作区，原件不动：

```python
from office import officekit as ok
r = ok.to_pdf('R100-42_升温路线对比_GPC报告_v1.docx')          # 默认同名 .pdf
r   # {'pdf': 路径, 'engine': 'Word', 'pages': 2, 'ok': True}
ok.to_pdf('汇报.pptx', 'pdf/汇报.pdf')                            # 指定输出
ok.to_pdf('投料.xlsx', engine='soffice')                          # 改用 LibreOffice
```

shell 里：`& '<本机目录>' '<本机目录>' pdf '报告.docx' '报告.pdf'`

- docx → Word，pptx → PowerPoint，xlsx → Excel；都是 COM 自动化，不可见、做完关掉，人自己开着的 Office 不受影响。
- Excel 导 PDF 约 70 秒（比别的慢），急用可 `engine='soffice'`（约 16 秒，版式可能和 Excel 略有不同）。
- 卡住超过 5 分钟会自动结束这次开的 Office 进程并报错，可换 `engine='soffice'` 再试一次。

## 检查

看返回：`ok` 为 True（文件非空、开头是 `%PDF-`、能读出页数），页数合理（例：两页的报告是 2 页）。说清检查到哪一步就行。人没要求更深的检查时，不去找 PDF 阅读器、不出图、不装工具来验证。

- Excel 的 PDF 分页按工作簿的打印设置；人要求时才改打印设置（纸张方向、缩放到一页宽、打印区域）。
- LibreOffice 转的分页和字体可能和 Word / PowerPoint 不同。

转失败：保留可用的源文件，说明 PDF 没转成。源文件没改就不要反复转，也不要拿转换去排查预览问题。只要 PDF 的，检查完就停；人要图片或要看版面才读 `office-render`。用 `outputs`（action=attach）登记 PDF，文件留在原地。

## 都没有时

这台机器没有 Office 也没有 LibreOffice 时：简单的报告用 `export` 工具直接出 pdf；版式要求高的用 reportlab 直接画 PDF（中文字体注册 `C:\Windows\Fonts\msyh.ttc`），说清"不是从 Word 转的"。
