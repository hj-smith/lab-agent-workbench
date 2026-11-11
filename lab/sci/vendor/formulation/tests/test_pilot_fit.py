# 迁移：去掉了 test_calculate_dispatch_preserves_legacy_and_accepts_workspace（测的是没搬的 pilot_production 服务分发）。
"""Workbook regression, alternative fits and portable weighing exports."""
import base64
import io

import openpyxl
import pytest

from lab.sci.vendor.formulation.pilot_fit import (fit_model, invert, predict,
                                              thickness_workspace, thickness_weighing_excel,
                                              preview_thickness_report)


def excel_inputs():
    values = [[39.15, 39.51, 39.6, 39.78, 39.43, 39.2],
              [37.74, 37.67, 37.73, 37.69, 37.66, 37.84],
              [36.11, 35.79, 35.82, 35.94, 35.55, 36.08],
              [34.1, 34.01, 34.11, 33.92, 34.15],
              [33.33, 33.23, 33.06, 33.13, 33.1, 33.35],
              [30.71, 30.82, 31.11, 30.61, 30.68]]
    targets = [.0153, .0146, .0139, .0134, .0126]
    weights = [(76.4993, 3.4992), (73.0008, 6.9997), (69.5009, 10.5008),
               (66.9999, 13.001), (62.9994, 17)]
    return dict(stock_solids=.016, available_g=400, diluent={'SolvB': .7, 'SolvA': .3},
                p0_values=values[0], target=37.68, mass_g=29422, solids=.016,
                points=[dict(target_solids=s, sample_mass_g=80, actual_stock_g=a,
                             actual_diluent_g=b, values=v, include=i != 4)
                        for i, (s, (a, b), v) in enumerate(zip(targets, weights, values[1:]), 1)])


def test_workspace_excel_golden_fit_and_adjustment():
    result = thickness_workspace(**excel_inputs())
    fit, calc = result['models']['linear'], result['calculation']
    assert fit['slope'] == pytest.approx(2557.081289196303, abs=1e-10)
    assert fit['intercept'] == pytest.approx(-1.4481086316057628, abs=1e-10)
    assert fit['rmse'] == pytest.approx(.02876682222570821, abs=1e-10)
    assert fit['status'] == '待独立验证'
    assert len(fit['residual_rows']) == 6
    assert fit['residual_rows'][4]['include'] is False
    assert calc['target_solids'] == pytest.approx(.015301863416279515, abs=1e-15)
    assert calc['addition_g'] == pytest.approx(1342.3577251624947, abs=1e-8)
    assert calc['diluent_components'] == pytest.approx({'SolvB': 939.6504076137462, 'SolvA': 402.7073175487484})
    assert calc['predicted_thickness'] == pytest.approx(37.68, abs=1e-12)
    assert abs(result['checks']['solid_mass_error_g']) < 1e-10
    assert abs(result['checks']['component_mass_error_g']) < 1e-10
    assert result['checks']['model_increasing']
    assert result['total_stock_g'] == pytest.approx(349)
    assert result['within_available'] and result['diluent_valid']


@pytest.mark.parametrize('model', ['linear', 'quadratic', 'power'])
def test_known_models_predict_and_inverse(model):
    f = {'linear': lambda x: 2 + 2000 * x,
         'quadratic': lambda x: 2 + 1000 * x + 40000 * x * x,
         'power': lambda x: 3000 * x ** 1.1}[model]
    fit = fit_model([{'x': x, 'y': f(x)} for x in [.01, .012, .014, .016, .018]], model)
    assert fit['r_squared'] == pytest.approx(1)
    assert predict(fit, .015) == pytest.approx(f(.015), abs=1e-10)
    assert invert(fit, f(.015)) == pytest.approx(.015, abs=1e-12)


def test_workspace_handles_unmeasured_design_and_warns_checks():
    result = thickness_workspace(stock_solids=.016, available_g=10,
                                 diluent=[{'source_id': 'SolvB', 'fraction': .7}],
                                 points=[{'target_solids': .015, 'sample_mass_g': 80}])
    assert result['points'][0]['stock_g'] == pytest.approx(75)
    assert result['fit'] is None and result['calculation'] is None
    assert result['models']['linear']['status'] == '点数不足'
    assert not result['within_available'] and not result['diluent_valid']
    assert len(result['warnings']) == 2


def test_concentrate_scenario_and_verification():
    inputs = excel_inputs()
    inputs.update(solids=.01, concentrate_solids=.05, verification=[
        {'solids': .015, 'measured': 36.9}, {'solids': .015, 'measured': 45}])
    result = thickness_workspace(**inputs)
    assert not result['scenarios']['A']['reachable']
    assert result['scenarios']['B']['reachable']
    assert result['calculation']['method'] == 'concentrate'
    assert result['calculation']['solids_after'] == pytest.approx(result['calculation']['target_solids'])
    assert result['verification'][0]['passed'] is True
    assert result['verification'][1]['passed'] is False


def test_export_is_real_printable_xlsx_with_source_values():
    result = thickness_weighing_excel(**excel_inputs())
    workbook = openpyxl.load_workbook(io.BytesIO(base64.b64decode(result['base64'])))
    sheet = workbook['稀释称量单']
    assert sheet['D6'].value == pytest.approx(76.5)
    assert sheet['H6'].value == pytest.approx(76.4993)
    assert sheet['J6'].value == pytest.approx(.015300146877753959)
    assert sheet.page_setup.orientation == 'landscape'
    assert sheet.print_area
    assert workbook['膜厚原始数据']['I2'].value == 39.15


def test_measurement_cells_keep_excel_positions_and_export_metadata():
    inputs = excel_inputs()
    inputs['p0_values'] = [39, None, 40, '', 41]
    inputs['p0'] = {'conditions': {'temperature_c': 23, 'humidity_percent': 45},
                    'abnormal_reason': '复测', 'operator': '检验员'}
    inputs['mass_display'] = {'limits_g': [100, 1000], 'decimals': [3, 2, 1]}
    result = thickness_workspace(**inputs)
    assert result['p0']['values'] == [39, None, 40, None, 41]
    assert result['p0']['measurement_cells'] == result['p0']['values']
    assert result['p0']['statistics']['mean'] == 40
    assert result['fit_points'][0]['statistics']['n'] == 3
    assert result['fit_points'][0]['values'] == [39, 40, 41]
    assert result['fit_points'][0]['measurement_cells'] == [39, None, 40, None, 41]
    assert result['points'][0]['statistics'] == result['points'][0]['stats']
    exported = thickness_weighing_excel(**inputs)
    workbook = openpyxl.load_workbook(io.BytesIO(base64.b64decode(exported['base64'])))
    sheet, raw = workbook.worksheets
    assert raw['I2'].value == 39 and raw['J2'].value is None and raw['K2'].value == 40
    assert raw['F2'].value == 23 and raw['G2'].value == 45
    assert raw['Y2'].value == '复测'
    assert sheet['D6'].number_format == '0.000'
    assert sheet['D2'].number_format == '0.00'


def test_explicit_saved_fit_is_selected_even_with_own_observations():
    inputs = excel_inputs()
    saved = {'slope': 2000, 'intercept': 5, 'x_min': .01, 'x_max': .02,
             'r_squared': .999, 'n': 6}
    own = thickness_workspace(**inputs, fit=saved)
    reused = thickness_workspace(**inputs, fit=saved, use_saved_fit=True)
    assert own['fit']['slope'] == pytest.approx(2557.081289196303)
    assert reused['fit']['slope'] == 2000
    assert reused['fit']['model'] == 'linear'
    assert reused['calculation']['target_solids'] == pytest.approx((37.68 - 5) / 2000)
    assert reused['models']['linear']['slope'] == pytest.approx(own['fit']['slope'])


def test_excel_slope_direction_and_component_mass_checks():
    inputs = excel_inputs()
    inputs.update(target=70, diluent={'SolvB': .7, 'SolvA': .2})
    saved = {'slope': -2000, 'intercept': 100, 'x_min': .01, 'x_max': .02}
    result = thickness_workspace(**inputs, fit=saved, use_saved_fit=True)
    assert result['checks']['model_increasing'] is False
    assert any('斜率方向异常' in warning for warning in result['warnings'])
    addition = result['calculation']['addition_g']
    assert result['checks']['component_mass_error_g'] == pytest.approx(-.1 * addition)
    assert not result['checks']['diluent_valid']


def test_aicc_penalizes_extra_parameters_and_keeps_excel_linear_default():
    result = thickness_workspace(**excel_inputs())
    linear, quadratic = result['models']['linear'], result['models']['quadratic']
    assert quadratic['rmse'] < linear['rmse']
    assert linear['aicc'] < quadratic['aicc']
    best = min(result['models'].values(), key=lambda fit: fit['aicc'])
    assert result['recommended_model'] == best['model']
    assert best['recommended'] and not quadratic['recommended']
    assert '参数' in result['recommendation_reason']
    assert result['fit']['model'] == 'linear'
    n, p = linear['n'], linear['parameter_count']
    import math
    expected = n * math.log(linear['rmse'] ** 2) + 2 * p + 2 * p * (p + 1) / (n - p - 1)
    assert linear['aicc'] == pytest.approx(expected)


def test_recommendation_can_prefer_quadratic_without_changing_selection():
    values = [.010 + i * .001 for i in range(10)]
    result = thickness_workspace(stock_solids=values[0], p0_values=[2 + 1000 * values[0] + 40000 * values[0] ** 2],
        points=[{'target_solids': x, 'sample_mass_g': 1, 'values': [2 + 1000 * x + 40000 * x * x]}
                for x in values[1:]])
    assert result['recommended_model'] == 'quadratic'
    assert result['fit']['model'] == 'linear'


def test_thickness_import_returns_measurements_and_tolerates_unreadable_files():
    result = preview_thickness_report('样品名称,膜厚,MSE\nP1,40.125,0.1\n,41.75,0.2\nP2,32.25,0.1\n'.encode(), 'ellips.csv')
    assert [s['name'] for s in result['samples']] == ['P1', 'P2']
    assert result['samples'][0]['values'] == [40.125, 41.75]
    assert result['samples'][1]['values'] == [32.25]
    invalid = preview_thickness_report(b'not a workbook', 'bad.xlsx')
    assert invalid['samples'] == [] and invalid['warnings']


def test_thickness_excel_formats_solids_without_rounding_cell_values():
    inputs = excel_inputs()
    inputs['solids_display'] = {'decimals': 2}
    result = thickness_weighing_excel(**inputs)
    workbook = openpyxl.load_workbook(io.BytesIO(base64.b64decode(result['base64'])))
    sheet, raw = workbook.worksheets
    assert sheet['B2'].number_format == '0.00%'
    assert sheet['J6'].number_format == '0.00%'
    assert raw['B2'].number_format == '0.00%'
    assert sheet['J6'].value == pytest.approx(.015300146877753959)
    assert sheet['C4'].number_format == '0.0000%'
    assert sheet['C4'].value == pytest.approx(.015301863416279515)
