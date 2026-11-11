---
name: 配方
description: 配方设计与配样：原料、预混液、配方（总量、固含、树脂份、添加剂用量、溶剂比例）、配样工单（冻结计划量和实称）、备料单、DOE 研究。要算每行称多少、剂量换口径、摩尔当量、实称回算实际固含、称量建议、DOE 设计和回归、导出工单 PDF/Excel 时用。
---
# 配方

库：产品A（R100 树脂 + PA 配方）、产品B。分组都是"配方"（旧工作簿汇总在"历史"）。记录 id 是 `frm-<旧 id>`。

## 类型和字段
- **原料**：编号、名称、类别（树脂/添加剂/溶剂）、固体分数 / 溶剂分数 / 易挥发分数（质量分数 0–1，合计 1）、溶剂编号。树脂处理方法写在备注（note）里。
- **预混液**：编号、名称、用途、组分 `[{来源类型 material/premix, 来源编号, 质量分数 0–1, 名称, 类别}]`（合计 1，可以套预混），固体/溶剂/易挥发分数是旧库存的汇总值。
- **配方**（一个修订一条）：配方编号、修订、总量 g、固含 0–1、树脂、添加剂、溶剂、当量、工艺因素、树脂来源、树脂批、来源（imported 旧工作簿 / series 实验系列 / saved）。
  同编号有几个修订：最新的记录名就是编号（例 `PA-T23A`），旧的叫 `PA-T23A 修订1`。
  各行保持算法的英文键：
  - 树脂 `{id, source_type, source_id, parts 份, parts_basis solid/solution}`
  - 添加剂 `{id, source_type, source_id, percent, basis solid|volatile, dose_basis}`；dose_basis 缺省是 resin_solid_pct（占树脂固体 %），
    还有 total_solid_pct、total_pct、total_ppm、fixed_g、mmol_per_g_resin。**有效份按 basis 取固体或易挥发部分，不是两者之和**（例 DMAP-1% 用 volatile）。
  - 溶剂 `{id, source_id, parts 份}` 或 `{solvent_mode: fixed_g, fixed_g}`
  - 旧工作簿导入的行带 `frozen_source`（当时的组成），算法照用，不查今天的原料库。
- **工单**：工单号（例 PA-20261102-004）、配方编号、修订、状态（待配样/已完成/已作废/历史导入）、计划日期、冻结配方、冻结计划（开单时算好的每行 planned_mass_g g 和来源组成）、实称信息、预混实称关联。
  曲线 **称量**：行、来源编号、名称、计划量 g、实称 g、复核。32 张"历史导入"工单（名字是旧工作簿表名）只有计划量，原样保存，不能重算。
- **备料单**：备料单号、工单、分组（每种预混液的需求、库存、余量、配制量和组分计划 g）；曲线 **实称**：分组、组分、来源编号、计划量 g、实称 g。
- **DOE研究**（旧"实验系列"）：名称、目的、判据、基准配方、因子、实验行、设计方法；曲线 **实验点**：行号、点id、配方、配方编号、工单号。响应（膜厚、n/k）不在这里，在测试记录里。
- **旧工单汇总**（历史）：旧 PA 工作簿的汇总表和预混单，只读资料。

关联：工单 -按配方→ 配方；工单 -用预混实称→ 备料单；备料单 -用于工单→ 工单；DOE研究 -实验点配方/实验点工单→；预混液 -组分→ 原料/预混液。
跨模块（膜、树脂批）以后按 `配方编号`、`树脂批` 字段由规则连。

## 常用查法
```python
from lab import store; lib = store.open('产品A')
lib.get('PA-T25D', kind='配方')                       # 一个配方；results['配方核算'] 是算好的称量
lib.table('PA-20261102-004', '称量', kind='工单')       # 计划量 / 实称
lib.sql('SELECT name, 配方编号, 状态, 计划日期 FROM "工单" ORDER BY 计划日期 DESC')
lib.sql('SELECT name, 树脂批, json_extract(工艺因素, \'$."树脂批次"\') FROM "配方"')
lib.results('实称回算', kind='工单')                       # 实际固含 solid_fraction（0–1）等
```

## 能算什么（`from lab.sci import formulation as F`，ref 是记录 id 或名字）
- `F.calculate_formula(ref, lib)`：每行 planned_mass_g、solid/solvent/volatile_mass_g，solvent_balance（addition_mass_g 是要补的溶剂），totals，errors。ref 是工单时用冻结组成。也可 `formula=字典` 直接算一个新配方。
- `F.dose_report(ref, lib)`：每行设计值、四种口径值、有效份 g、称量 g；工单有实称时 `actual_summary`（实际总量、实际固含）。
- `F.formula_components(ref, lib)`：预混液拆回有效成分（DMAP、PPTS…），每种 6 种口径（设计 / 实称）。
- `F.equivalents(ref, lib)`、`F.suggest_equivalent(ref, 行id, 比值, lib)`：摩尔当量及反算 %。
- `F.weighing_suggestions(ref, lib, min_weigh_g=0.05)`：称量太小时的预混建议（每个建议都真重算过）。
- `F.premix_plan([工单...], lib, settings)`：备料（库存、余量比例、固定余量、最低配制量、取整）。
- DOE：`F.doe_methods()`、`F.doe_design(variables, method)`、`F.doe_analyze(runs, responses, model)`、`F.doe_predict`、`F.doe_contour`、`F.doe_optimize`、`F.doe_apply_row(基准, row, variables, lib)`（一行 → 一个配方字典）。
- 膜厚–固含：`F.thickness_fit(points, model)`、`F.thickness_invert(fit, nm)`。
- 导出（原模板原版式，写到 产出\<库>\）：`F.export_orders([工单...], lib, 'pdf'|'xlsx')`、`F.export_preparation(备料单, lib, fmt)`、
  `F.export_doe_report(F.doe_snapshot(DOE研究, lib, responses, measured, analysis), lib, fmt)`。
- `F.compute_all(lib)`：所有配方的"配方核算""当量"，工单的"工单重算对照"（和冻结计划逐行比）"实称回算"。

## 口径和坑
- 质量都是 g，分数都是 0–1；百分数字段（percent、dose_value）是 %。显示时再换算，存原值。
- 新开工单要冻结：把 `calculate_formula` 的结果原样存进"冻结计划"，以后原料库改了也不动它。开工单前让人看一眼配方表和三项平衡。
- 第一次给某个来源算当量时，物种和摩尔质量要人确认（当量行 `species_confirmed`）。
- 树脂批：`树脂批` 是从树脂行展开到树脂原料的编号（例 R100-40）；旧工作簿导入的配方用的预混液在原料库里没有定义，树脂批为空，只有 `树脂来源`。处理方法另见 `工艺因素.树脂批次` 和原料备注。
- 设置：`配方.称量下限`、`配方.建议预混浓度`、`配方.工单编号规则`、`配方.工单冻结`、`配方.剂量口径`。
