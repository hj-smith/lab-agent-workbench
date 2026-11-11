r"""配方模块的算法，原样复制自 <本机目录> 1.7.3）。

原样复制、没有改动的：formula_core、dose_report、formula_components、equivalents、weighing_suggest、doe、doe_analysis、
pilot_calc、pilot_fit、pilot_report_import、excel_io、pdf_export、doe_report、export_format、beijing_time、
export_privacy、dispensing、display_text；data/templates/orders.xlsx、preparation.xlsx；assets/NotoSansSC-Regular.ttf。
截断的：experiment_service（只有前 457 行，见文件头）。
新写的小文件（原文件是库服务层，不搬；只把被引用的几个函数原文照抄过来，这样算法文件一个字都不用改）：
app_service（number、digest）、batch_management（plan_fingerprint）、capabilities（_formula_input）、
pilot_sheets（_style）、preparation_actuals（PREMIX_STOCK_REMINDER、去掉 db 参数的 group_summary）。
所以盘点报告说的两处小改（dose_report 引 app_service.number；pilot_fit / design_thickness 引 pilot_production）
用"同名小文件"代替了：dose_report 不改；pilot_fit 本身不引 pilot_production（只有 pilot_report_import 的
一个没用到的函数里有延迟 import）；design_thickness 要从库里读膜厚曲线，没搬。
tests/ 里是原测试，只把 import 的包名换成 lab.sci.vendor.formulation。
"""
