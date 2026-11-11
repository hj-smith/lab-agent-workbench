"""DOE reports built from the same result snapshot shown in the workbench.

The export never fits a model itself. Missing models and missing measurements
remain visible, so a report can also be used as a measurement checklist.
"""
from __future__ import annotations

from .beijing_time import today
from io import BytesIO
from itertools import combinations
import json
import math
import re
from statistics import NormalDist, mean

from .display_text import STATUSES
from .export_format import format_number
from .beijing_time import format_time


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text(value):
    if value is None:
        return '未提供'
    if _number(value):
        if value != 0 and abs(value) < .0001:
            return f'{value:.4g}'
        return format_number(value).rstrip('0').rstrip('.')
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return format_time(value)


def _get(row, *keys):
    return next((row[k] for k in keys if row.get(k) is not None), None)


def _label(item):
    name = item.get('name') or item.get('key') or item.get('response_id') or ''
    return f"{name} / {item['unit']}" if item.get('unit') else str(name)


def filename(snapshot, format='pdf'):
    """Return a safe human-readable filename for a Content-Disposition header."""
    extension = 'xlsx' if format in ('xlsx', 'excel') else 'pdf'
    name = (snapshot.get('series') or {}).get('name') or '实验项目'
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name)).strip('. ')[:100] or '实验项目'
    return f'{name}-DOE报告.{extension}'


def _coefficient_rows(model):
    rows = []
    for c in model.get('coefficients') or []:
        rows.append([_get(c, 'term', 'name', 'key'), _get(c, 'coefficient', 'coef', 'estimate', 'value'),
                     _get(c, 'se', 'std_error', 'standard_error'), _get(c, 't', 't_value'),
                     _get(c, 'p', 'p_value')])
    return rows


def _anova_rows(model):
    return [[_get(r, 'source', 'term', 'name'), r.get('df'), _get(r, 'ss', 'sum_sq'),
             _get(r, 'ms', 'mean_sq'), _get(r, 'f', 'f_value', 'F'), _get(r, 'p', 'p_value')]
            for r in model.get('anova') or []]


def _model_label(model):
    selected = model.get('model_type')
    names = {'linear': '线性', 'interaction': '交互', 'quadratic': '二次',
             'mixture_linear': 'Scheffé 线性', 'mixture_quadratic': 'Scheffé 二次'}
    if isinstance(selected, str):
        return names.get(selected, selected)
    definition = model.get('model')
    if isinstance(definition, str):
        return names.get(definition, definition)
    if isinstance(definition, dict):
        terms = list(definition.get('terms') or [])
        return ' + '.join((['截距'] if definition.get('intercept') else []) + [str(t) for t in terms]) or '无可估计模型项'
    return '用户所选模型'


def _conclusions(snapshot):
    runs, responses = snapshot.get('runs') or [], snapshot.get('responses') or []
    analysis = snapshot.get('analysis') or {}
    lines = []
    for response in responses:
        key = response['response_id']
        measured = [r.get('responses', {}).get(key) or {} for r in runs]
        available = sum(v.get('mean') is not None for v in measured)
        lines.append(f'{_label(response)}：{available}/{len(runs)} 次运行有结果，{len(runs)-available} 次待测。')
        model = analysis.get(key) or {}
        if not model.get('coefficients'):
            continue
        significant = []
        for term, coefficient, _se, _t, p in _coefficient_rows(model):
            if str(term).lower() in ('intercept', 'const', 'constant', '截距', '1'):
                continue
            if _number(p) and p < .05 and _number(coefficient):
                direction = '正' if coefficient > 0 else '负' if coefficient < 0 else '零'
                significant.append(f'{term}（系数 {_text(coefficient)}，{direction}向，p={_text(p)}）')
        if significant:
            lines.append('在 p<0.05 下显著的模型项：' + '；'.join(significant) + '。方向指模型项的系数符号；交互、二次及混料项需结合设定条件解释。')
        else:
            lines.append('当前模型没有 p<0.05 的非截距项；不能据此认定因子没有影响。')
        if _number(model.get('r2')):
            lines.append(f"模型 R²={_text(model['r2'])}，调整 R²={_text(model.get('r2_adj'))}，预测 R²={_text(model.get('r2_pred'))}。")
    if not analysis:
        lines.append('尚无分析模型；分析引擎即将合并，或尚未选择响应并运行分析。')
    return lines


def _prediction_rows(point, response_names):
    predictions = point.get('predictions') or point.get('responses') or point.get('predicted') or {}
    rows = []
    if isinstance(predictions, dict):
        for key, prediction in predictions.items():
            p = prediction if isinstance(prediction, dict) else {'value': prediction}
            rows.append([response_names.get(key, key), _get(p, 'value', 'mean', 'prediction', 'predicted'),
                         _get(p, 'low', 'lower'), _get(p, 'high', 'upper')])
    return rows


def _effect_tables(runs, factors, response_id):
    """Observed plot data; each run contributes once, after repeat aggregation."""
    measured = [(r, ((r.get('responses') or {}).get(response_id) or {}).get('mean')) for r in runs]
    measured = [(r, y) for r, y in measured if _number(y)]
    main_rows, interaction_rows = [], []
    for factor in factors:
        groups = {}
        for run, value in measured:
            level = (run.get('values') or {}).get(factor['key'])
            if level is not None:
                groups.setdefault(_text(level), []).append(value)
        for level, values in groups.items():
            main_rows.append([_label(factor), level, mean(values), len(values)])
    for first, second in combinations(factors, 2):
        groups = {}
        for run, value in measured:
            values = run.get('values') or {}
            if values.get(first['key']) is not None and values.get(second['key']) is not None:
                levels = (_text(values[first['key']]), _text(values[second['key']]))
                groups.setdefault(levels, []).append(value)
        for (level_a, level_b), values in groups.items():
            interaction_rows.append([_label(first), level_a, _label(second), level_b, mean(values), len(values)])
    blocks = []
    if main_rows:
        blocks += [('text', '主效应与交互图数据：以下按设计运行的响应均值分组，每次运行贡献一次；这是观测汇总，不代替模型项显著性检验。'),
                   ('table', ['因子', '水平', '响应均值', '运行数'], main_rows)]
    if interaction_rows:
        blocks.append(('table', ['因子 A', '水平 A', '因子 B', '水平 B', '响应均值', '运行数'], interaction_rows))
    return blocks


def _run_label(run, index=None):
    label = run.get('code') or run.get('formula_code') or f"设计点 {run.get('run') if isinstance(run.get('run'), int) else index or ''}".strip()
    platform_id = run.get('doe_run_id')
    return label + (f'\n平台运行号：{platform_id}' if platform_id else '')


def _sections(snapshot):
    """Shared semantic content: section = (title, [(text|table, ...)])."""
    series = snapshot.get('series') or {}
    data = series.get('data') or {}
    factors, runs, responses = (snapshot.get(k) or [] for k in ('factors', 'runs', 'responses'))
    analysis = snapshot.get('analysis') or {}
    run_labels = {run.get('run_id'): _run_label(run, index) for index, run in enumerate(runs, 1)}
    def label_for(row):
        return run_labels.get(_get(row, 'run_id', 'run', 'index'), '设计点')
    warnings = list(snapshot.get('warnings') or [])
    purpose = data.get('purpose') or (data.get('project') or {}).get('purpose') or series.get('purpose') or '未填写'
    sections = [('实验目的与范围', [('text', f"实验项目：{series.get('name') or series.get('id') or '未命名'}"),
                                  ('text', f'实验目的：{purpose}'),
                                  ('text', '范围：本报告只包含本实验项目的设计运行及其关联检测结果。历史非设计数据未纳入本次 DOE 分析。'),
                                  ('text', '结果按运行和响应汇总；重复测量显示均值与 n。待测不按零处理，原始结果需回到检测记录修改。')])]
    factor_rows = [[_label(f), f.get('key'), f.get('type') or 'numeric',
                    f.get('levels') if f.get('levels') is not None else [f.get('low'), f.get('high')]] for f in factors]
    sections.append(('因子与水平', [('table', ['因子 / 单位', '因子键', '类型', '水平 / 范围'], factor_rows)]))
    design = []
    for offset in range(0, max(1, len(factors)), 4):
        part = factors[offset:offset+4]
        rows = []
        for run in runs:
            orders = run.get('work_orders') or []
            order_text = '\n'.join(f"{o.get('batch_code') or o.get('work_order_id') or o.get('id') or '未编号'}：{STATUSES.get(o.get('status'), o.get('status') or '未记录')}" for o in orders) or '未生成'
            rows.append([run_labels[run.get('run_id')], *[(run.get('values') or {}).get(f['key']) for f in part], order_text])
        design.append(('table', ['设计点 / 配方编号', *[_label(f) for f in part], '关联工单 / 状态'], rows))
    sections.append(('设计运行与工单', design))
    matrix = []
    for offset in range(0, max(1, len(responses)), 5):
        part = responses[offset:offset+5]
        rows = []
        for run in runs:
            values = []
            for response in part:
                result = (run.get('responses') or {}).get(response['response_id']) or {}
                values.append(f"{_text(result['mean'])} (n={result.get('n', 1)})" if result.get('mean') is not None else '待测')
            rows.append([run_labels[run.get('run_id')], *values])
        matrix.append(('table', ['设计点 / 配方编号', *[_label(r) for r in part]], rows))
    if not responses:
        matrix.insert(0, ('text', '尚未发现关联检测响应。完成检测后可刷新结果矩阵。'))
    sections.append(('结果矩阵', matrix))
    raw = []
    for run in runs:
        for response in responses:
            result = (run.get('responses') or {}).get(response['response_id']) or {}
            if result.get('mean') is not None:
                raw.append([run_labels[run.get('run_id')], _label(response), result.get('mean'), result.get('n', 1),
                            result.get('values') or [], result.get('sources') or []])
    sections.append(('原始值与记录来源', [('table', ['设计点 / 配方编号', '响应', '均值', 'n', '原始值', '来源记录'], raw)]))
    for response in responses:
        model = analysis.get(response['response_id']) or {}
        if not model:
            continue
        blocks = [('text', f"模型：{_model_label(model)}"),
                  ('table', ['R²', '调整 R²', '预测 R²'], [[model.get('r2'), model.get('r2_adj'), model.get('r2_pred')]])]
        if model.get('coefficient_units_note'):
            blocks.append(('text', model['coefficient_units_note']))
        blocks.append(('table', ['模型项', '系数', '标准误', 't', 'p'], _coefficient_rows(model)))
        if model.get('anova_note'):
            blocks.append(('text', model['anova_note']))
        blocks.append(('table', ['来源', '自由度', '平方和', '均方', 'F', 'p'], _anova_rows(model)))
        effects = model.get('effects') or []
        if effects:
            blocks.append(('table', ['效应项', '效应', 'p'], [[_get(e, 'term', 'name', 'key'), _get(e, 'effect', 'value', 'coefficient'), _get(e, 'p', 'p_value')] for e in effects]))
        residuals = model.get('residuals') or []
        if residuals:
            blocks.append(('table', ['设计点 / 配方编号', '实际值', '拟合值', '残差', '标准化残差'], [[label_for(r), _get(r, 'actual', 'observed', 'y'), _get(r, 'fitted', 'predicted', 'y_hat'), _get(r, 'residual', 'value'), _get(r, 'standardized', 'standardized_residual', 'studentized')] for r in residuals]))
            ordered = sorted([r for r in residuals if _number(_get(r, 'residual', 'value'))], key=lambda r: _get(r, 'residual', 'value'))
            normal_rows = []
            for i, row in enumerate(ordered, 1):
                probability = (i-.375)/(len(ordered)+.25)
                normal_rows.append([label_for(row), _get(row, 'residual', 'value'),
                                    probability, NormalDist().inv_cdf(probability)])
            blocks.append(('table', ['正态概率图运行', '排序残差', '累积概率', '标准正态分位数'], normal_rows))
        blocks.extend(_effect_tables(runs, factors, response['response_id']))
        warnings.extend(f"{_label(response)}：{w}" for w in model.get('warnings') or [])
        sections.append((f'模型与诊断：{_label(response)}', blocks))
    if not analysis:
        sections.append(('模型与诊断', [('text', '尚无模型。分析引擎即将合并，或尚未运行分析。当前报告包含设计和已采集结果。')]))
    optimization = snapshot.get('optimization') or {}
    best = optimization.get('best') or {}
    response_names = {r['response_id']: _label(r) for r in responses}
    recommendations = []
    if best:
        recommendations += [('text', f"推荐点因子：{_text(best.get('values') or best.get('point') or best.get('factors') or {})}"),
                            ('text', f"综合期望度：{_text(_get(best, 'desirability', 'score'))}"),
                            ('table', ['响应', '预测值', '95% 预测区间下限', '95% 预测区间上限'], _prediction_rows(best, response_names))]
        candidate_rows = []
        for i, candidate in enumerate(optimization.get('candidates') or [], 1):
            predicted_rows = _prediction_rows(candidate, response_names) or [['未提供', None, None, None]]
            for prediction in predicted_rows:
                candidate_rows.append([i, candidate.get('values') or candidate.get('point') or candidate.get('factors') or {},
                                       _get(candidate, 'desirability', 'score'), *prediction])
        recommendations.append(('table', ['候选序号', '因子设定', '综合期望度', '响应', '预测值', '95% 预测下限', '95% 预测上限'], candidate_rows))
        if optimization.get('note'):
            recommendations.append(('text', optimization['note']))
        recommendations.append(('text', '推荐点是模型预测，需安排确认实验；未提供的预测区间以“未提供”显示。'))
    else:
        recommendations.append(('text', '尚未设置优化目标或生成推荐点。'))
    sections.append(('优化与推荐', recommendations))
    lines = _conclusions(snapshot)
    if best:
        lines.append('已获得模型推荐点；请用独立确认运行检验预测值及区间。')
    missing = sum(((r.get('responses') or {}).get(p['response_id']) or {}).get('mean') is None for r in runs for p in responses)
    next_round = f'下一轮建议：先补齐 {missing} 个待测运行响应，再更新模型。' if missing else '下一轮建议：在推荐条件及基准条件安排独立重复实验，核对预测误差和批间波动。'
    next_round += '根据显著模型项与残差诊断决定是否缩小因子范围或补充中心点；当前数据不足时增加重复，避免仅凭拟合优度选点。'
    sections.append(('结论与下一轮建议', [('text', x) for x in [*lines, next_round]]))
    if warnings:
        sections.append(('分析提示', [('text', str(w)) for w in dict.fromkeys(str(w) for w in warnings)]))
    return sections


def _xlsx(snapshot, sections):
    from openpyxl import Workbook
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont
    from openpyxl.styles import Alignment, Font, PatternFill
    from .pilot_sheets import _style

    book = Workbook()
    book.remove(book.active)
    for index, (title, blocks) in enumerate(sections):
        safe_title = re.sub(r'[\\/*?:\[\]]', '_', title)[:27]
        ws = book.create_sheet(f'{index+1:02d} {safe_title}'[:31])
        ws.append([title])
        ws['A1'].font = Font(name='等线', size=14, bold=True, color='163444')
        for block in blocks:
            if block[0] == 'text':
                ws.append([block[1]])
                ws.row_dimensions[ws.max_row].height = max(30, math.ceil(len(str(block[1])) / 38) * 16 + 6)
                continue
            _, headers, rows = block
            ws.append(headers)
            header_row = ws.max_row
            for row in rows or [['无记录'] + [''] * (len(headers)-1)]:
                ws.append([v if v is None or _number(v) else _text(v) for v in row])
            _style(ws, header_row, len(headers))
            for cell in ws[header_row]:
                cell.fill = PatternFill('solid', fgColor='DCE6F1')
            ws.append([])
        max_cols = max(2, ws.max_column)
        ws.column_dimensions['A'].width = 32
        if max_cols == 2:
            ws.column_dimensions['B'].width = 55
        for row in ws:
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=True)
                if isinstance(cell.value, str):
                    cell.data_type = 's'
                    if cell.column == 1 and '\n平台运行号：' in cell.value:
                        primary, secondary = cell.value.split('\n', 1)
                        cell.value = CellRichText(TextBlock(InlineFont(sz=11, b=True), primary),
                                                  TextBlock(InlineFont(sz=9, color='64748B'), '\n'+secondary))
                elif _number(cell.value):
                    cell.number_format = '0.0000E+00' if cell.value != 0 and abs(cell.value) < .0001 else '0.######'
        for row in ws:
            if row[0].value is not None and all(c.value is None for c in row[1:]):
                ws.merge_cells(start_row=row[0].row, start_column=1, end_row=row[0].row, end_column=max_cols)
        ws.freeze_panes = 'A2'
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _pdf(snapshot, sections):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle
    from .pdf_export import BORDER, FONT, INK, LIGHT, MUTED, _font, _p

    _font()
    buffer = BytesIO()
    width, height = landscape(A4)
    margin = 28
    title = 'DOE 实验报告'
    document = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=margin, rightMargin=margin,
                                 topMargin=51, bottomMargin=31, title=title, author='')
    story = []
    for section_title, blocks in sections:
        heading = _p(section_title, 12, INK)
        heading.keepWithNext = True
        heading.style.spaceAfter = 6
        story += [Spacer(1, 9), heading]
        for block in blocks:
            if block[0] == 'text':
                story += [_p(block[1], 9), Spacer(1, 5)]
                continue
            _, headers, rows = block
            if not rows:
                rows = [['无记录'] + [''] * (len(headers)-1)]
            def cell(value, column):
                text = _text(value)
                if column == 0 and '\n平台运行号：' in text:
                    primary, secondary = text.split('\n', 1)
                    return [_p(primary, 8), _p(secondary, 6.5, MUTED)]
                return _p(text, 8)
            cells = [[_p(h, 8.5) for h in headers]] + [[cell(v, column) for column, v in enumerate(row)] for row in rows]
            weights = [1.25] + [1] * (len(headers)-1)
            # IDs and provenance need room; paginate rows rather than shrinking text.
            if '来源记录' in headers:
                weights[-1] = 2
            table = Table(cells, colWidths=[(width-2*margin)*w/sum(weights) for w in weights],
                          repeatRows=1, hAlign='LEFT', splitByRow=1, splitInRow=1)
            table.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), FONT),
                                      ('BACKGROUND', (0,0), (-1,0), LIGHT),
                                      ('GRID', (0,0), (-1,-1), .4, BORDER),
                                      ('VALIGN', (0,0), (-1,-1), 'TOP'),
                                      ('LEFTPADDING', (0,0), (-1,-1), 5),
                                      ('RIGHTPADDING', (0,0), (-1,-1), 5),
                                      ('TOPPADDING', (0,0), (-1,-1), 4),
                                      ('BOTTOMPADDING', (0,0), (-1,-1), 4)]))
            story += [table, Spacer(1, 8)]

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 14)
        canvas.setFillColor(INK)
        canvas.drawString(margin, height-27, title)
        canvas.setFont(FONT, 8)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(width-margin, height-26, today().isoformat())
        canvas.setStrokeColor(BORDER)
        canvas.line(margin, height-36, width-margin, height-36)
        canvas.drawRightString(width-margin, 17, f'第 {doc.page} 页')
        canvas.restoreState()

    document.build(story, onFirstPage=page, onLaterPages=page)
    return buffer.getvalue()


def build_report(snapshot, format='pdf'):
    """Build an immutable snapshot report as PDF or Excel (xlsx) bytes."""
    if format not in ('pdf', 'xlsx', 'excel'):
        raise ValueError('报告格式应为 pdf 或 xlsx')
    sections = _sections(snapshot)
    return _pdf(snapshot, sections) if format == 'pdf' else _xlsx(snapshot, sections)
