---
name: gpc
description: GPC/SEC 分子量（PS 当量）：查样品的 Mn、Mw、PDI、分布，按反应时间看趋势，换基线/区间/标准曲线试算，读仪器导出的新色谱和标准曲线 PDF。
---
# GPC 分子量

数据在产品库 `产品A`（R100 树脂的 GPC 都在这），分组 `测试`。记录 id 是 `gpc-<旧id>`。

## 类型和字段
- **GPC样品**：一次进样。曲线 `色谱`（列 `时间 / min`、`信号`，约 4165 点，原样 float64）。
  字段：`样品名`（如 R100-38-0H，用来对应树脂取样）、`所属项目`（如 R100-34）、`批次`、`顺序`、`标准曲线`/`标准曲线id`、
  `基线方法`（pchip_anchors 最常用 / linear_anchors）、`基线锚点` [[t min, 信号]…]、`积分区间` [{起, 止 (min), 峰方向 正/负,
  计入分子量, 范围策略 外推/只算曲线范围内/超范围报错, 名称}]、`分析版本`、`原文件名`、`原路径`、`导入时间`（北京时间）、`分峰方案`、`旧id`。
  原始 CSV 在 `files` 里。
- **GPC标准曲线**：曲线 `标样点`（`保留时间 / min`、`M / (g/mol)`、`启用`）；字段 `模型`、`次数`、`系数`（a0 起，log10M=Σa_i·RT^i）、
  `缩放系数`、`有效范围起`/`有效范围止`（min）、`R²`、`RMSE`、`来源文件名`（PDF 在 files）。
- **GPC实验**：一组按反应时间的样品。表 `成员`（样品、样品id、反应时间、时间单位、重复、纳入、剔除原因、因子水平）；关联 `成员` → 样品。
- **GPC分析模板**（沿用到新样品的锚点+区间）、**GPC分子量分区**（`下限`/`上限` g/mol）、**GPC标准品批次**（表 `组分`，证书 Mp/Mn/Mw）。
- 关联：样品 → `标准曲线`、`分析模板`、`复制自`。

## 结果（results，不在字段里）
- `GPC分子量`：{Mn, Mw, Mz, Mz+1, PDI, Mp, Mp_峰高, D10, D50, D90 (g/mol), 面积, 并集面积 (信号·min), 外推面积占比 (0–1), 提示}
- `GPC分布`：{logM, dw/dlogM, M, 累积质量分数}，≤200 点。
- 旧快照（没重算）：`GPC分峰（旧快照）`、`GPC分析方案快照：…`、`GPC联合拟合（旧快照）`。

## 常用查法
```python
from lab import store
lib = store.open('产品A')
lib.sql('''SELECT r.name, s.所属项目, json_extract(x.value,'$.Mw') Mw, json_extract(x.value,'$.PDI') PDI
           FROM results x JOIN records r ON r.id=x.record_id JOIN "GPC样品" s ON s.id=r.id
           WHERE x.method='GPC分子量' AND s.所属项目='R100-34' ORDER BY r.name''')
lib.get('gpc-sample_…')                     # 字段 + 结果 + 关联；样品名可能在几个项目里重名，用 id 或带 kind
lib.table('gpc-sample_…', '色谱')           # (列名, 行, meta)
```

## 能算什么（`from lab.sci import gpc`）
- `gpc.analyze(ref, 锚点=None, 积分区间=None, 标准曲线=None)`：算分子量并存结果；给了任一参数就是试算，不存。
- `gpc.distribution(ref)`：分布曲线（200 点，dw/dlogM 已除以总面积）。
- `gpc.experiment_trend(实验名或id, metric='Mw')`：成员按时间排好带值，画趋势用（`纳入`=False 的是用户剔掉的点）。
- `gpc.refit_calibration(曲线)`：用标样点重拟合，和存的系数比。
- `gpc.read_instrument_file(path)`：读 ChemStation CSV（UTF-16 制表符）、一般 CSV/TXT、xlsx、NetCDF、zip、Cirrus 标准曲线 PDF。
  新样品：读出来 → `lib.save('GPC样品', {...,'series': {'色谱': {'columns': ['时间 / min','信号'], 'rows': …}}})`，
  锚点和区间可以从同项目的 GPC分析模板 或相邻样品抄，再给人看谱图确认。
- `gpc.compute_all()`：全库重算。

## 口径和坑
- 分子量是 **PS 当量读数**，不是真实分子量（树脂是支化/分形的，换算要另做）。
- 积分只用 `exact-boundary-trapezoid-v2`；算分子量用**存的系数**，不重拟合（设置 `GPC.积分算法`、`GPC.标准曲线用存的系数`）。
- 范围策略：`只算曲线范围内` 把超出标准曲线有效范围的部分裁掉；`外推` 整段用多项式外推（看 `外推面积占比`）。
  区间常越过低分子量端（如 41-16H 区间到 21.63 min，曲线只到 21.25）。
- Mp 默认取分布峰顶（dw/dlogM 最大），`Mp_峰高` 是色谱峰高处。
- 基线锚点是人在谱图上手调的，改了要给人看图确认，别自己悄悄改了存回去。
- 9 个样品（标准品谱图等）没有分析，不出结果。
