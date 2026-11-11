"""Thickness workspace calculations. Solids and solvent ratios are mass fractions.

The linear calculation intentionally uses the same centred least squares and
sample standard deviation as the user's dilution workbook. No numerical
packages, database state or UI formatting are needed by this module.
"""
import math
from statistics import mean

from .pilot_calc import (PilotCalculationError, linear_fit, thickness_stats,
                         mass_decimals, solids_decimals, solids_format)


def predict(fit, solids):
    """Evaluate a workspace model (and legacy linear curve records)."""
    model = fit.get('model', 'linear')
    if model == 'power':
        return fit['a'] * solids ** fit['b']
    if model == 'quadratic':
        c, d, e = fit['coefficients']
        return c + d * solids + e * solids * solids
    return fit['intercept'] + fit['slope'] * solids


def invert(fit, target):
    """Invert a curve, preferring the root inside its measured solids range."""
    model = fit.get('model', 'linear')
    if model == 'power':
        if target <= 0 or fit['a'] <= 0 or fit['b'] == 0:
            raise PilotCalculationError('幂函数无法反算该目标膜厚')
        return (target / fit['a']) ** (1 / fit['b'])
    if model == 'quadratic':
        c, d, e = fit['coefficients']
        if abs(e) < 1e-14:
            if not d:
                raise PilotCalculationError('拟合斜率为 0，无法反算')
            return (target - c) / d
        discriminant = d * d - 4 * e * (c - target)
        if discriminant < 0:
            raise PilotCalculationError('二次模型没有对应目标膜厚的实数解')
        roots = [(-d + math.sqrt(discriminant)) / (2 * e),
                 (-d - math.sqrt(discriminant)) / (2 * e)]
        positive = [x for x in roots if 0 < x <= 1]
        if not positive:
            raise PilotCalculationError('二次模型反算固含量不在 0–100% 范围')
        low, high = fit['x_min'], fit['x_max']
        inside = [x for x in positive if low <= x <= high]
        if len(inside) > 1 and abs(inside[0] - inside[1]) > 1e-12:
            raise PilotCalculationError('二次模型在验证范围有两个解，请选择其他模型')
        return min(inside or positive, key=lambda x: abs(x - (low + high) / 2))
    if not fit['slope']:
        raise PilotCalculationError('拟合斜率为 0，无法反算')
    return (target - fit['intercept']) / fit['slope']


def _solve(matrix, vector):
    """Small normal equation solver with partial pivoting."""
    rows = [list(a) + [b] for a, b in zip(matrix, vector)]
    size = len(rows)
    for col in range(size):
        pivot = max(range(col, size), key=lambda i: abs(rows[i][col]))
        if abs(rows[pivot][col]) < 1e-14:
            raise PilotCalculationError('有效固含量取值不足，无法拟合该模型')
        rows[col], rows[pivot] = rows[pivot], rows[col]
        divisor = rows[col][col]
        rows[col] = [value / divisor for value in rows[col]]
        for i in range(size):
            if i != col:
                factor = rows[i][col]
                rows[i] = [a - factor * b for a, b in zip(rows[i], rows[col])]
    return [row[-1] for row in rows]


def fit_model(points, model='linear'):
    """Fit linear, quadratic or log-log power model to included points.

    Residuals are observed minus predicted. Metrics include only fitted points;
    residual_rows also retain excluded observations for chart inspection.
    """
    valid = [p for p in points if p.get('include', True) and not p.get('excluded', False)]
    needed = 3 if model == 'quadratic' else 2
    if len(valid) < needed:
        raise PilotCalculationError('拟合至少需要 %s 个有效点' % needed)
    xs, ys = [float(p['x']) for p in valid], [float(p['y']) for p in valid]
    if model == 'linear':
        fit = dict(linear_fit(valid), model=model)
        fit['coefficients'] = [fit['intercept'], fit['slope']]
    elif model == 'quadratic':
        # Centre and scale before solving: solids are around .01, so raw
        # fourth-power normal equations otherwise lose unnecessary precision.
        centre, scale = mean(xs), max(xs) - min(xs)
        if not scale:
            raise PilotCalculationError('拟合点的横坐标不能全部相同')
        zs = [(x - centre) / scale for x in xs]
        matrix = [[sum(z ** (i + j) for z in zs) for j in range(3)] for i in range(3)]
        vector = [sum(y * z ** i for y, z in zip(ys, zs)) for i in range(3)]
        a, b, c = _solve(matrix, vector)
        e = c / scale ** 2
        d = b / scale - 2 * e * centre
        intercept = a - b * centre / scale + e * centre ** 2
        fit = {'model': model, 'coefficients': [intercept, d, e], 'intercept': intercept,
               'slope': d, 'quadratic': e}
    elif model == 'power':
        if min(xs) <= 0 or min(ys) <= 0:
            raise PilotCalculationError('幂函数要求固含量与平均膜厚均大于 0')
        logged = linear_fit([{'x': math.log(x), 'y': math.log(y)} for x, y in zip(xs, ys)])
        fit = {'model': model, 'a': math.exp(logged['intercept']), 'b': logged['slope']}
        fit['coefficients'] = [fit['a'], fit['b']]
    else:
        raise PilotCalculationError('没有找到该膜厚模型')
    residuals = [y - predict(fit, x) for x, y in zip(xs, ys)]
    sse = sum(r * r for r in residuals)
    syy = sum((y - mean(ys)) ** 2 for y in ys)
    fit.update(n=len(valid), x_min=min(xs), x_max=max(xs), y_min=min(ys), y_max=max(ys),
               r_squared=1 - sse / syy if syy else (1 if sse < 1e-20 else 0),
               rmse=math.sqrt(sse / len(valid)), max_abs_residual=max(map(abs, residuals)),
               residuals=residuals, status='点数不足' if len(valid) < 5 else '待独立验证')
    # Compare residuals on the original nm scale for all three model families.
    # AICc counts fitted curve coefficients and penalizes the extra quadratic term.
    n, parameters = len(valid), len(fit['coefficients'])
    variance = max(sse / n, max(mean(y * y for y in ys), 1) * 1e-24)
    aic = n * math.log(variance) + 2 * parameters
    fit['parameter_count'] = parameters
    fit['aicc'] = aic + 2 * parameters * (parameters + 1) / (n - parameters - 1) if n > parameters + 1 else None
    fit['adjusted_rmse'] = math.sqrt(sse / max(n - parameters, 1))
    warnings = []
    if len(valid) < 5:
        warnings.append('有效点少于 5 个，建议增加实验点')
    if fit['r_squared'] < .99:
        warnings.append('拟合 R² 小于 0.99，建议检查数据')
    fit['warnings'] = warnings
    fit['residual_rows'] = []
    for point in points:
        if model == 'power' and point['x'] <= 0:
            continue
        predicted = predict(fit, point['x'])
        residual = point['y'] - predicted
        fit['residual_rows'].append(dict(point, predicted=predicted, predicted_thickness=predicted,
                                        residual=residual, abs_residual=abs(residual)))
    return fit


def recommend_model(models):
    """Mark the best available fit without changing the user's model choice."""
    candidates = [fit for fit in models.values() if 'coefficients' in fit]
    if not candidates:
        return None, '尚无可比较的有效模型'
    eligible = [fit for fit in candidates if fit['aicc'] is not None]
    if eligible:
        best = min(eligible, key=lambda fit: (fit['aicc'], fit['parameter_count']))
        metric = 'AICc'
        reason = 'AICc 最低（%.2f），已计入 %s 个参数的复杂度惩罚。' % (best['aicc'], best['parameter_count'])
    else:
        best = min(candidates, key=lambda fit: (fit['adjusted_rmse'], fit['parameter_count']))
        metric = 'adjusted_rmse'
        reason = '点数不足以计算 AICc；按参数数修正的 RMSE 最低（%.4g nm）。' % best['adjusted_rmse']
    for fit in models.values():
        fit['recommended'] = fit is best
        fit['recommendation_reason'] = reason if fit is best else ''
        fit['recommendation_metric'] = metric
    return best['model'], reason


def preview_thickness_report(data, filename=''):
    """Read instrument measurements for a row-mapping dialog, without saving."""
    from .pilot_report_import import parse_report
    result = parse_report(data, filename)
    for sample in result['samples']:
        sample['values'] = [value for test in sample['tests'] if test['type'] == 'thickness'
                            for value in test['values']]
        if not sample['values']:
            result['warnings'].append('%s：没有膜厚测量值，可跳过此样品。' % sample['name'])
        elif len(sample['values']) > 9:
            result['warnings'].append('%s：共有 %s 个膜厚测量值，原始数据表可放入前 9 个点。' % (sample['name'], len(sample['values'])))
    return result


def _parts(diluent):
    if isinstance(diluent, dict):
        return {key: float(value or 0) for key, value in diluent.items() if key}
    return {row.get('source_id', row.get('code', '')): float(row.get('fraction', 0) or 0)
            for row in diluent or [] if row.get('source_id', row.get('code'))}


def _values(values):
    return [float(value) if value not in (None, '') else None for value in (values or [])[:9]]


def _stats(values):
    measured = [value for value in values if value is not None]
    return thickness_stats(measured) if measured else None


def thickness_workspace(stock_solids=None, available_g=None, points=None, diluent=None,
                        p0_values=None, p0_include=True, p0=None, model='linear',
                        target=None, mass_g=None, solids=None, concentrate_solids=None,
                        verification=None, allowed_error=.01, fit=None, mass_display=None,
                        use_saved_fit=False, solids_display=None):
    """Calculate the full workspace while allowing unmeasured design rows.

    Input rows and P0 metadata are preserved so the caller can save this complete
    object inside its existing JSON adjustment record without a schema change.
    """
    warnings = []
    parts = _parts(diluent)
    diluent_total = sum(parts.values())
    diluent_valid = bool(parts) and all(v >= 0 for v in parts.values()) and abs(diluent_total - 1) < 1e-8
    if not diluent_valid:
        warnings.append('稀释剂组成合计须为 100%，且比例不能为负')
    stock = float(stock_solids or 0)
    rows, observations = [], []
    p0row = dict(p0 or {})
    p0row.update(label=p0row.get('label', 'P0'), x=stock, solids=stock,
                 include=p0row.get('include', p0_include), values=_values(p0row.get('values', p0_values)))
    p0row['measurement_cells'] = list(p0row['values'])
    p0row['stats'] = p0row['statistics'] = _stats(p0row['values'])
    if p0row['stats']:
        p0row['y'] = p0row['stats']['mean']
        if stock > 0:
            observations.append(dict(p0row, values=[v for v in p0row['values'] if v is not None]))
    for i, point in enumerate(points or [], 1):
        row = dict(point)
        row.setdefault('label', 'P%s' % i)
        row['values'] = _values(row.get('values'))
        row['measurement_cells'] = list(row['values'])
        row['stats'] = row['statistics'] = _stats(row['values'])
        row['include'] = row.get('include', True)
        target_s, sample = float(row.get('target_solids') or 0), float(row.get('sample_mass_g') or 0)
        row.update(stock_g=None, diluent_g=None, actual_solids=None, diluent_components={})
        if stock > 0 and 0 <= target_s <= stock and sample > 0:
            row['stock_g'] = sample * target_s / stock
            row['diluent_g'] = sample - row['stock_g']
            row['diluent_components'] = {key: row['diluent_g'] * ratio for key, ratio in parts.items()}
            row['status'] = '已测量' if row['stats'] else '待称量 / 待测试'
        else:
            row['status'] = '检查原液固含、目标固含和样品量'
        a, b = row.get('actual_stock_g'), row.get('actual_diluent_g')
        if a is not None and a != '' and b is not None and b != '':
            a, b = float(a), float(b)
            if a >= 0 and b >= 0 and a + b > 0:
                row['actual_solids'] = a * stock / (a + b)
                row['status'] = '已测量' if row['stats'] else '已称量 / 待测试'
            else:
                row['status'] = '实际称量须非负且总量大于 0'
        x = row['actual_solids'] if row['actual_solids'] is not None else target_s
        row['solids'] = x
        if row['stats'] and x > 0:
            observations.append(dict(row, x=x, y=row['stats']['mean'],
                                     values=[v for v in row['values'] if v is not None]))
        rows.append(row)
    total = sum(row['stock_g'] or 0 for row in rows)
    actual_total = sum(float(row['actual_stock_g']) if row.get('actual_stock_g') not in (None, '') else row['stock_g'] or 0 for row in rows)
    within = available_g is not None and max(total, actual_total) <= float(available_g) + 1e-9
    if available_g is not None and not within:
        warnings.append('原液用量超过可用量')
    models = {}
    for name in ('linear', 'quadratic', 'power'):
        try:
            models[name] = fit_model(observations, name)
        except PilotCalculationError as error:
            models[name] = {'model': name, 'status': '点数不足' if len([p for p in observations if p.get('include', True)]) < 5 else '无法拟合',
                            'n': len([p for p in observations if p.get('include', True)]), 'error': str(error), 'warnings': [str(error)]}
    recommended_model, recommendation_reason = recommend_model(models)
    selected = models.get(model, models['linear'])
    if use_saved_fit and fit:
        selected = dict(fit)
    elif 'coefficients' not in selected:
        selected = dict(fit) if fit else None
    if selected:
        selected.setdefault('model', 'linear')
    calculation, scenarios, checks, verified = None, {}, {}, []
    if selected:
        if selected['model'] == 'quadratic':
            _, d, e = selected['coefficients']
            increasing = all(d + 2 * e * x > 0 for x in (selected['x_min'], selected['x_max']))
        elif selected['model'] == 'power':
            increasing = selected['a'] * selected['b'] > 0
        else:
            increasing = selected['slope'] > 0
        checks['model_increasing'] = increasing
        if not increasing:
            warnings.append('模型斜率方向异常：验证范围内膜厚未随固含量单调增加，请复核数据和模型')
    if selected and target is not None:
        try:
            target_s = invert(selected, float(target))
            if not 0 < target_s <= 1:
                raise PilotCalculationError('反算目标固含量须在 0–100% 范围')
            extrapolated = not selected.get('x_min', selected.get('valid_min', 0)) <= target_s <= selected.get('x_max', selected.get('valid_max', 1))
            local_warnings = ['外推，建议增加实验点'] if extrapolated else []
            calculation = {'target_solids': target_s, 'extrapolated': extrapolated,
                           'verdict': '外推' if extrapolated else '内插', 'predicted_thickness': predict(selected, target_s),
                           'warnings': local_warnings, 'addition_g': None, 'method': 'none',
                           'diluent_components': {}, 'diluent_g': 0, 'mass_after_g': None, 'solids_after': None}
            if mass_g is not None and solids is not None and float(mass_g) > 0:
                m, s = float(mass_g), float(solids)
                reachable = s >= target_s
                ms = m * (s / target_s - 1) if reachable else None
                scenarios['A'] = {'reachable': reachable, 'addition_g': ms, 'method': 'dilution',
                    'reason': '可通过加稀释剂降低固含量' if reachable else '当前固含量低于目标，不能通过稀释达到',
                    'diluent_components': {k: ms * v for k, v in parts.items()} if ms is not None else {}}
                sx = float(concentrate_solids or 0)
                can_concentrate = sx > target_s > s
                mx = m * (target_s - s) / (sx - target_s) if can_concentrate else None
                scenarios['B'] = {'reachable': can_concentrate, 'addition_g': mx, 'method': 'concentrate',
                                   'concentrate_solids': sx, 'reason': '可补加高固含母液' if can_concentrate else '须满足母液固含 > 目标固含 > 当前固含'}
                for key, addition, extra_solids in [('A', ms, 0), ('B', mx, sx)]:
                    if addition is not None:
                        after = m + addition
                        solid_after = (m * s + addition * extra_solids) / after
                        scenarios[key].update(mass_after_g=after, solids_after=solid_after,
                                              mass_balance_error=solid_after - target_s,
                                              solid_mass_error_g=m * s + addition * extra_solids - after * target_s,
                                              predicted_thickness=predict(selected, solid_after))
                chosen = scenarios['A'] if reachable else scenarios['B']
                calculation.update({k: v for k, v in chosen.items() if k != 'reason'})
                calculation['diluent_g'] = ms or 0
                if not reachable:
                    calculation['warnings'].append('需配方/质量批准，不得默认蒸发浓缩')
                checks.update({'mass_balance_error': chosen.get('mass_balance_error'),
                          'solid_mass_error_g': chosen.get('solid_mass_error_g'),
                          'back_substitution_error': calculation['predicted_thickness'] - float(target),
                          'component_mass_error_g': sum(scenarios['A']['diluent_components'].values()) - ms if ms is not None else None,
                          'diluent_valid': diluent_valid, 'within_available': within})
            default_verification = [{'label': label, 'solids': value} for label, value in
                [('验证下限', selected.get('x_min', target_s * .99)), ('目标中心', target_s), ('验证上限', selected.get('x_max', target_s * 1.01))]]
            for entry in verification if verification is not None else default_verification:
                row = dict(entry)
                x = row.get('solids')
                predicted = predict(selected, float(x)) if x not in (None, '') else None
                measured = row.get('measured', row.get('measured_thickness'))
                error = (float(measured) - predicted) / predicted if predicted and measured not in (None, '') else None
                row.update(predicted=predicted, predicted_thickness=predicted, error=error,
                           error_percent=error * 100 if error is not None else None,
                           passed=abs(error) <= float(allowed_error) if error is not None else None,
                           status='待确认' if error is None else ('通过' if abs(error) <= float(allowed_error) else '不通过'))
                verified.append(row)
        except (PilotCalculationError, OverflowError, ZeroDivisionError) as error:
            warnings.append(str(error))
    return {'points': rows, 'p0': p0row, 'fit_points': observations, 'models': models, 'fit': selected,
            'recommended_model': recommended_model, 'recommendation_reason': recommendation_reason,
            'model': model, 'calculation': calculation, 'scenarios': scenarios, 'checks': checks,
            'verification': verified, 'allowed_error': allowed_error, 'diluent': parts,
            'diluent_total': diluent_total, 'diluent_valid': diluent_valid,
            'total_stock_g': total, 'actual_total_stock_g': actual_total, 'within_available': within,
            'available_g': available_g, 'warnings': warnings}


def thickness_weighing_excel(**inputs):
    """Export a printable lab weighing sheet and raw measurements as XLSX."""
    import base64
    import inspect
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    data = thickness_workspace(**{k: v for k, v in inputs.items()
                                  if k in inspect.signature(thickness_workspace).parameters})
    solids_display = inputs.get('solids_display')
    percent_format = solids_format(solids_display, fraction=True)
    targets = [row.get('target_solids') for row in data['points'] if row.get('target_solids') is not None]
    places = solids_decimals(solids_display)
    target_extra = int(any(a != b and round(a * 100, places) == round(b * 100, places)
                           for a, b in zip(targets, targets[1:])))
    target_format = solids_format(solids_display, fraction=True, extra=target_extra)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = '稀释称量单'
    solvents = list(data['diluent'])
    columns = ['样品', '目标固含', '样品量 g', '原液理论 g', '稀释剂理论 g']
    columns += [key + ' g' for key in solvents]
    columns += ['原液实际 g', '稀释剂实际 g', '实际固含', '状态', '称量日期', '操作者']
    sheet.append(['膜厚 / 固含调整 · 稀释系列称量单'])
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(columns))
    sheet.append(['原液固含', inputs.get('stock_solids'), '可用原液 g', inputs.get('available_g'),
                  '原液理论合计 g', data['total_stock_g'], '用量校验', '通过' if data['within_available'] else '待确认'])
    sheet['B2'].number_format = percent_format
    for address in ('D2', 'F2'):
        value = sheet[address].value
        if isinstance(value, (int, float)):
            decimals = mass_decimals(value, inputs.get('mass_display'))
            sheet[address].number_format = '0' + ('.' + '0' * decimals if decimals else '')
    sheet.append(['稀释剂组成'] + ['%s %.4g%%' % (key, value * 100) for key, value in data['diluent'].items()]
                 + ['比例校验', '通过' if data['diluent_valid'] else '不通过'])
    sheet.append(['仅改变固含量；保持树脂批次、溶剂组成、基材、旋涂和软烘条件一致。',
                  'St（固含显示位数 +2 位）', (data['calculation'] or {}).get('target_solids')])
    sheet['C4'].number_format = solids_format(solids_display, fraction=True, extra=2)
    sheet.append(columns)
    for row in data['points']:
        sheet.append([row['label'], row.get('target_solids'), row.get('sample_mass_g'), row['stock_g'], row['diluent_g']]
                     + [row['diluent_components'].get(key) for key in solvents]
                     + [row.get('actual_stock_g'), row.get('actual_diluent_g'), row['actual_solids'], row['status'],
                        row.get('date', ''), row.get('operator', '')])
        sheet.cell(sheet.max_row, 2).number_format = target_format
        sheet.cell(sheet.max_row, 8 + len(solvents)).number_format = percent_format
        for col in range(3, 8 + len(solvents)):
            cell = sheet.cell(sheet.max_row, col)
            if isinstance(cell.value, (int, float)):
                decimals = mass_decimals(cell.value, inputs.get('mass_display'))
                cell.number_format = '0' + ('.' + '0' * decimals if decimals else '')
    raw = workbook.create_sheet('膜厚原始数据')
    raw.append(['样品', '固含量', 'rpm', '软烘 °C', '软烘 s', '环境 °C', 'RH %', '基材']
               + ['测点%s nm' % i for i in range(1, 10)]
               + ['平均 nm', '最大', '最小', '极差', '3σ', '面内均匀性', '异常', '原因', '纳入拟合', '日期', '操作者'])
    for row in [data['p0'], *data['points']]:
        conditions = dict(row.get('conditions', {}), **{k: row[k] for k in ('rpm', 'bake_c', 'bake_seconds', 'ambient_c', 'rh', 'substrate', 'temperature_c', 'humidity_percent') if k in row})
        conditions.setdefault('ambient_c', conditions.get('temperature_c'))
        conditions.setdefault('rh', conditions.get('humidity_percent'))
        stats = row.get('stats') or {}
        values = row['values'] + [None] * (9 - len(row['values']))
        raw.append([row['label'], row.get('solids'), *[conditions.get(k) for k in ('rpm', 'bake_c', 'bake_seconds', 'ambient_c', 'rh', 'substrate')],
                    *values, *[stats.get(k) for k in ('mean', 'max', 'min', 'range', 'three_sigma', 'uniformity')],
                    row.get('abnormal', ''), row.get('abnormal_reason', row.get('reason', '')), '是' if row.get('include', True) else '否',
                    row.get('date', ''), row.get('operator', '')])
        raw.cell(raw.max_row, 2).number_format = percent_format
        raw.cell(raw.max_row, 23).number_format = '0.0000%'
    for ws, header_row in [(sheet, 5), (raw, 1)]:
        ws.freeze_panes = 'C%s' % (header_row + 1)
        ws.auto_filter.ref = 'A%s:%s%s' % (header_row, get_column_letter(ws.max_column), ws.max_row)
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.paperSize = ws.PAPERSIZE_A3 if ws == raw else ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.print_title_rows = '1:%s' % header_row
        ws.print_options.horizontalCentered = True
        ws.print_area = ws.dimensions
        for row in ws:
            for cell in row:
                cell.font = Font(name='Microsoft YaHei', size=10, color='18364B')
                cell.alignment = Alignment(vertical='center', wrap_text=True)
                if isinstance(cell.value, float) and cell.number_format == 'General':
                    cell.number_format = '0.0000'
        for cell in ws[header_row]:
            cell.fill = PatternFill('solid', fgColor='18364B')
            cell.font = Font(name='Microsoft YaHei', size=10, bold=True, color='FFFFFF')
        ws.row_dimensions[header_row].height = 34
        for i in range(1, ws.max_column + 1):
            ws.column_dimensions[get_column_letter(i)].width = 15 if ws == sheet else 13
        for i in range(header_row + 1, ws.max_row + 1):
            ws.row_dimensions[i].height = 30
            if i % 2 == 0:
                for cell in ws[i]:
                    cell.fill = PatternFill('solid', fgColor='EDF5F8')
    sheet['A1'].font = Font(name='Microsoft YaHei', size=18, bold=True, color='18364B')
    sheet.row_dimensions[1].height = 36
    buffer = io.BytesIO()
    workbook.save(buffer)
    return {'filename': '膜厚稀释系列称量单.xlsx', 'base64': base64.b64encode(buffer.getvalue()).decode('ascii')}
