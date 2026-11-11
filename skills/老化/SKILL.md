---
name: 老化
description: 储存与老化（UV-Vis 谱）。查样品在室温/冷藏下 A500 怎么变、峰高和颜色、拟合峰高、速率和货架期，导入新的 *.WVS.csv，导出带图的 Excel。问到"老化""储存稳定性""A500""500 nm""变色""货架期"时用。
---
# 储存与老化

## 这里有什么（库 产品A，类型 `老化谱`，分组 测试）
一条记录 = 一个样品在一个储存条件、一个时间点的一条 UV-Vis 吸光度谱。id = `age-<旧 id>`。
- 字段：`样品`、`种类`（配方液 / 树脂溶液 / 参比 / 其它）、`条件`（室温、冷藏 4 °C、冷冻、40 °C…；起点空着）、
  `天数`（天；24 h 记 1）、`小时`（h）、`时间标签`（D5、24 h）、`是否起点`（true = 人标的）、`测试日`（多数空）、
  `原文件名`、`原路径`、`文件夹`、`指纹`（sha256）、`仪器表头`、`导入说明`、`旧id`、`导入时间`、`旧库改过`。
- 曲线 `谱`：列 `波长 / nm`、`吸光度`（300–800 nm，步长 2 nm）。原件挂在 files（原始文件\老化谱\）。
- 结果（`lib.results('老化.指标')`、`'老化.对起点变化'`）：A500、峰、峰高、拟合、颜色和对起点的变化；参数改了要重算。
- 设置：`老化.分析设置`（500 nm、峰窗 470–540、基线 440/600、上限 3.999、拟合 held 507 nm / 75 nm / 440–700、
  动力学温度和阈值）、`老化.文件名规则`。

## 常用查法
```python
from lab import store
lib = store.open('产品A')
lib.sql('SELECT 样品, 条件, 天数, name FROM "老化谱" WHERE 样品 LIKE ? ORDER BY 条件, 天数', ('PA-T23%',))
lib.sql('SELECT DISTINCT 样品, 种类 FROM "老化谱" ORDER BY 样品')
cols, rows, meta = lib.table('PA-T23A-4C-D5', '谱')     # 谱名 = 原文件名去掉 .WVS.csv
lib.sql("SELECT r.name, json_extract(x.value, '$.a500'), json_extract(x.value, '$.at500Ceiling') FROM results x "
        "JOIN records r ON r.id = x.record_id WHERE x.method = '老化.指标' AND r.name LIKE ?", ('R100-16%',))
```

## 能算什么（`from lab.sci import aging`）
- `aging.spectrum_metrics(谱名或 id)` → A500 / Abs、是否截顶、峰位 / nm、峰高（扣 440–600 基线）、半高宽、带面积、截顶到 / nm、
  拟合峰高 / Abs（只给可靠的）、拟合 R²、500 nm 处背景、L* a* b*、黄度 YI。
- `aging.sample_series('PA-T23A')` → 起点 + 每个条件按天排的点，带 ΔA500（Abs 和 %）、ΔE*ab、ΔYI、Δ拟合峰高、变化口径。
- `aging.kinetics('R100-16', metric='a500', model='linear'|'first'|'exp')` → 每个条件初速率（/天）、R²、阈值、
  已超过阈值的最早一天、到阈值天数（货架期），两个以上温度时 Ea（kJ/mol）、Q10、预测温度下速率。
  metric 可选：a500、height、area、ceilingUpTo、fitH、fitBg、L、a、b、yi、dE、dFit、dYI、A450、A600。
- `aging.import_spectra([路径…])` → 新文件读进来、按文件名认样品/条件/天数、挂原件；人改过的值用 overrides={文件名: {字段: 值}}。
  导入后跑 `aging.compute_all(lib)`。
- `aging.export_xlsx(['PA-T23A', 'R100-16'], 路径)` → 数据表 + 光谱 + Excel 原生图（谱图、A500-天数）。
- 底层纯函数：`measure`、`fit_band`、`colour`、`value_at`、`aging_series`、`fit_kinetics`、`time_to`、`arrhenius`、`parse_name`。

## 业务口径和坑
- 老化信号是 500 nm 的显色物种带（用户 2026-10-30 定），读 A500，**不要换成 A700**。
- 仪器上限 4.0：A500 ≥ 3.999 写"≥ 4.0"，是下限，不是 4。截顶的点不进动力学拟合，但"已超过阈值"照算；
  起点就截顶时比起点的变化算不出（kind = start-ceiling），改看"截顶到 nm"（截顶区长波边，越长说明带越强）。
- 起点：人标的 > 0 h / D0 > 不写条件的 D1。D1 以后文件名没写条件按室温（实验室只写 4C）。
- 峰拟合默认 held：中心 507 nm、半高宽 75 nm 固定（来自不截顶的 PA 谱），截顶点不参与；"不可靠"= 峰两侧未截顶点不够，不要用它的高度；
  "外推"= 峰中心截顶，用两侧点估的，写"≈"。
- 颜色只对覆盖 380–780 nm 的谱有值（T = 10^−A，D65、2°）。
- 不同条件比较前看 `种类`：配方液（PA-T…）、树脂溶液（R100-…、35-5%-…）、参比（REF01）浓度不同，不能直接比绝对值。
  其中 33-2%PPTS-TEA、21MEOH-2%PPTS-TEA 没有天数。
- 算法照抄自储存与老化工作台 1.0.0，A500、峰高、拟合和旧程序逐位一致；不要换成 scipy 拟合。
