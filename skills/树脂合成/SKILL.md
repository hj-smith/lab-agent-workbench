---
name: 树脂合成
description: R100 树脂合成实验（单体A MonA + 单体B MonB 缩合）的设计、投料换算、产率、过程固含和干基闭合、批次因子、HPLC 单体剩余、SOP 与批记录导出导回。问到树脂批次、R100-xx、投料、产率、HPLC、分料 A/B 路线时用。
---
# 树脂合成（库 产品A，分组「树脂合成」）

## 类型和字段
- **树脂实验** `res-<编号>`（例 res-R100-38）：编号、标题、日期、目的、问题、假设、结论、结论判定、状态、父编号、烧瓶容积 mL、路线（A/B）。
  - 字段 `设计` = 设计 doc 原样（vars 变量、charges 投料、steps / procedure 流程、samplingPlan 取样计划、historicalBranches…），**是参数**。
  - 表 `实测` = 实测值（键、值）：`record-charges/<投料id>/实际值`（g 或 mL）、`record-t0/actual/实际钟点`、
    `record-timeline/<节点>/内温|外温|取样量|累计水量|现象|实际钟点`、`record-operations/<步骤>[@A]/质量|含固率|M2`、
    `record-hplc/<节点>/取样量|稀释后净重|MonA峰面积|MonB峰面积|检测日期`。**钟点是 Excel 日分数**（0.7326 = 17:35）。
- **树脂取样** `res-<旧样品id>`：样品名、实验编号、分支、种类、节点、计划检测；旧指标摊成字段 Mn、Mw、Đ、干品质量、膜厚、Δn/Δk 193/248/633 nm、
  SolvA膜厚损失率（这些是旧总表/旧 GPC 记下的值，单位见字段定义）；表 `指标` 有来源。关联 `属于` → 树脂实验。
- **树脂批** `res-<批号>`（例 res-R100-38A-FP）：干品产品批。关联 `来自` → 树脂实验、`取样` → 树脂取样。
- **树脂物料** `res-<物料id>`：分子量 g/mol、密度 g/mL、纯度（空 = 没确认）、溶液浓度 %、CAS。
- 设置：`树脂.步骤模板`、`树脂.因子规则`、`树脂.HPLC方法`（斜率、截距、倍数、密度按自己的标定填）、
  `树脂.GPC命名规则`、`树脂.研发路线`、`树脂.实验别名`（R100-38A → R100-38）、`树脂.库偏好`。

## 常用查法
```python
from lab import store; lib = store.open('产品A')
lib.sql('SELECT name, 标题, 状态 FROM "树脂实验" ORDER BY name')
lib.sql("SELECT name, Mn, Mw FROM \"树脂取样\" WHERE 实验编号='R100-30' AND Mn IS NOT NULL")
cols, rows, meta = lib.table('R100-41', '实测', kind='树脂实验')     # 实测值
lib.results('树脂.产率', kind='树脂实验')                            # 存好的产率（compute_all 算的）
```
产率等结果存在 results：`树脂.产率`、`树脂.闭合`、`树脂.因子`、`树脂.HPLC`（版本 resin-kernel 2.11.0）。

## 能算什么（`from lab.sci import resin`，ref = 编号或 id）
| 函数 | 给什么 |
|---|---|
| `resin.charges(ref)` | 每个料 模式、质量_g、物质的量_mmol、体积_mL、实测；变量（单体比、理论净树脂、理论生成水…） |
| `resin.schedule(ref)` | 每步计划/实际起止 h、温度 °C |
| `resin.yield_(ref)` | 各路 干品 g、分料比、理论 g、产率 %；合计 |
| `resin.profile(ref)` / `resin.closure(ref)` | 过程固含 w/w、体积 mL、分水；后处理干基闭合；偏差 |
| `resin.factors(ref)` | 25 个批次因子（值、单位、状态 set/missing/to_confirm） |
| `resin.hplc(ref)` | 每个取样点 MonA/MonB 含量 mg/g、单体剩余 % |
| `resin.export_sop(ref, fmt='xlsx'|'docx', records=False)` | SOP 与批记录 xlsx（活公式、_meta 页）/ SOP Word，存到产出文件夹 |
| `resin.import_record(ref, path)` | 读回操作员填好的批记录 xlsx，实测值并进 `实测` 表，原件挂到实验上 |
| `resin.call(cmd, design=…)` | 直接调计算核心（改了设计想先试算，不存库时用） |
| `resin.compute_all()` | 全部重算并存结果 |
计算都在 node 小进程 `workbench2\resin-kernel`（旧树脂模块 2.11.0 的 kernel 原样复制），结果和旧程序一样。

## 业务口径和坑
- **产率** = 干品 ÷（理论净树脂 × 分料比），未扣添加剂。理论净树脂 = MonB + MonA − 理论生成水；分料比 = A/B 分料称重比，没称用计划比；
- **HPLC 含量** mg/g = (峰面积 − 截距)/斜率 × 50 × (稀释后净重/取样量) ÷ 0.889。**剩余 %** 以理论投料为 100 %，按时间扣取样、分水，滴加按进度算；不是"除以第一个点"。
- 投料模式：mass、volume、mmol、eq（当量，相对基准料）、molpct（mol%）、solidFill（按目标固含补溶剂：Σ固体 g × (100/目标% − 1)）、perProduct、expr。
- 偏差容差：投料 ±1 %、时长 ±10 %、温度 ±2 °C（或步骤里写的 ±x °C）。
- 物料纯度大多没确认（空）；用到纯度的投料会报错，要先在物料上填。
- GPC 读数是 PS 当量，不是真分子量；GPC 样品按 `样品名` 和 GPC 模块对应（规则在 `树脂.GPC命名规则`）。
- 旧总表里的"最终产率""理论产品整批"是历史抄录值，以 `resin.yield_` 现算为准；两者差 ≥ 0.5 % 时 yield_ 会标"和历史不一致"。
- 派生设计抄了父设计的 lotUid（38/42、39/40/41 相同），批用批号区分，不要按 lotUid 连。
