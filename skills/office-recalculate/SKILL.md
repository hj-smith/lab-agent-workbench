---
name: office-recalculate
description: 刷新 Excel 公式的计算结果（缓存值）并核对：用本机 Excel 的 CalculateFull 另存新文件，没有 Excel 时用 LibreOffice。
---

> 改写自 DeepSeek Harness 桌面版 office-skills 的 office-recalculate（MIT 许可）。原件用 LibreOffice Kit；这里改用本机 Excel。

# 重算 Excel 公式

openpyxl / XlsxWriter 写的公式没有计算结果；人用预览、网页或别的程序打开时会看到空格子。要让公式带上算好的值：

```python
from office import officekit as ok
r = ok.recalc('投料计算.xlsx')                  # 默认存成 投料计算-重算.xlsx，原件不动
r['engine']               # 'Excel'（本机 Excel，不可见，做完关掉）；没有 Excel 时 'LibreOffice'
r['report']['no_value']   # 还没有值的公式格子，应为 []
r['report']['errors']     # 算出 #DIV/0!、#REF!、#NAME? 等错误的格子，应为 []
r['report']['rows'][:20]  # (位置, 公式, 算出的值)，挑关键的几格手算核对
```

- 指定输出：`ok.recalc('a.xlsx', 'a_v2.xlsx')`；强制用 LibreOffice：`engine='soffice'`（会用临时配置让它打开时重算所有公式）。
- 保留原工作簿，结果写成新文件，放在工作区。

## 核对

用 openpyxl 分别以 `data_only=False` 和 `data_only=True` 打开结果（`ok.formula_report(路径)` 已经这样做了）：
- 公式还在，没有变成死数；
- 每个公式都有值，没有错误值；
- 关键结果和需求的算法、预期的合计、单元格类型对得上——挑几格自己用 Python 手算一遍比较；
- 要求不动的表、样式、数据都还在。

Excel 另存会按 Excel 的习惯重写一些样式细节，内容不变。重算不检查业务逻辑对不对，也不保证 Excel 特有的功能在 LibreOffice 里都一样。

## 做不到时

没有能用的重算程序或命令失败：保留工作簿，说清"计算值没能刷新，打开 Excel 后会自动算出"。不要把公式换成常数，也不要把旧的或空的缓存说成新算的结果。核对过的结果用 `outputs`（action=attach）登记，文件留在原地。
