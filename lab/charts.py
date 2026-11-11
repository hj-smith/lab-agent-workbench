"""画图：一张可以缩放、悬停看数的网页图。样式统一在这里，模型只给数据。"""
import re
from pathlib import Path

COLORS = ['#c96442', '#3f7cc0', '#b5841c', '#8a63b8', '#5f8c3e', '#c25f8a', '#6b665c', '#3f8a86']     # 和网页图同一套、同一顺序（配色 B）
REST = '#8a8985'            # 第 9 条起用灰色：颜色再多就分不清了，应该分成几张图
SURFACE, INK, MUTED, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#e9e8e4'
FONT = 'Microsoft YaHei, Segoe UI, sans-serif'
PLOTLY = '/web/vendor/plotly.min.js'


def _file(folder, title, name):
    stem = re.sub(r'[\\/:*?"<>|\s]+', '_', (name or title or '图').removesuffix('.html')).strip('_') or '图'
    path, number = folder / f'{stem}.html', 2
    while path.exists() and not name:
        path, number = folder / f'{stem}-{number}.html', number + 1
    return path


def make(folder, title, series, kind='line', x_title='', y_title='', log_x=False, log_y=False, note='', name=None):
    import plotly.graph_objects as go

    folder = Path(folder)
    figure = go.Figure()
    for index, item in enumerate(series):
        color = COLORS[index] if index < len(COLORS) else REST
        x, y, label = item.get('x') or [], item.get('y') or [], str(item.get('name') or f'系列{index + 1}')
        if kind == 'bar':
            figure.add_bar(x=x, y=y, name=label, marker_color=color)
        else:
            mode = 'markers' if kind == 'scatter' else ('lines+markers' if len(x) <= 40 else 'lines')
            figure.add_scatter(x=x, y=y, name=label, mode=mode, line={'color': color, 'width': 2},
                               marker={'color': color, 'size': 8, 'line': {'color': SURFACE, 'width': 2}})
    axis = {'gridcolor': GRID, 'linecolor': GRID, 'zeroline': False, 'ticks': 'outside', 'tickcolor': GRID,
            'tickfont': {'color': MUTED}, 'title_font': {'color': MUTED}}
    figure.update_layout(
        title={'text': title + (f'<br><sup style="color:{MUTED}">{note}</sup>' if note else ''), 'x': 0, 'xanchor': 'left',
               'font': {'color': INK, 'size': 18}},
        font={'family': FONT, 'color': INK, 'size': 13}, paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        showlegend=len(series) > 1, legend={'orientation': 'h', 'y': -0.18, 'font': {'color': MUTED}},
        hovermode='x unified' if kind == 'line' else 'closest', bargap=0.3, margin={'l': 70, 'r': 30, 't': 80, 'b': 70},
        xaxis={**axis, 'title': x_title, 'type': 'log' if log_x else None},
        yaxis={**axis, 'title': y_title, 'type': 'log' if log_y else None})
    path = _file(folder, title, name)
    figure.write_html(path, include_plotlyjs=PLOTLY, full_html=True,                     # 画图库用工作台自己那一份，在哪个页面打开都找得到
                      config={'displaylogo': False, 'responsive': True})
    return path.name
