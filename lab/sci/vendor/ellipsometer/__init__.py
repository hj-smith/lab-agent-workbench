"""椭偏膜厚计算核心（原样复制）。

来源：<本机目录>，提交 ddbc78a4（2026-10-23）。
  src\\           1.3.3 计算核心：读表、统计、离群、参比差值、容差。原样复制，一个字没改（包内相对 import 本来就是）。
  config\\        config.yaml 原样。
  ellipsometer2\\ 2.0 的 conditions、treatments、settings、read_views：原样（settings.py 只把 `from src.` 改成 `from ..src.`）。
                 reference、quality_flags：只改取数（见各文件开头）。
                 memstore.py：从 store.py 搬来的组统计、读表导入、样品/读数视图，读数据库改成传列表（见文件开头）。
不要用平台 vendor 里那份简化版 analyzer（算法不同）。
"""
