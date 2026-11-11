"""储存与老化：UV-Vis 谱的老化指标（A500、≥ 4.0 截顶、基线峰高、507 nm 峰拟合、颜色、动力学、货架期）。

照抄自储存与老化工作台 1.0.0（<本机目录>）：
    lib/parse.mjs     → parse_spectrum              lib/naming.mjs   → parse_name / parse_time / folder_date
    lib/analysis.mjs  → value_at / colour / measure / change / aging_series
    lib/fit.mjs       → lm / fit_band（自写 LM 逐行照搬：两个初值、求导步长 1e-6·(|p|+1e-3)、λ 调整、边界、120 次）
    lib/kinetics.mjs  → fit_kinetics / time_to / arrhenius；app/app.js:91 kineticsOf → kinetics_of；app.js:56 metricList → METRICS
只用 Python 自带的 float 和 math（不用 numpy / scipy），求和顺序和 JS 一样。
照 JS 处理的几处：Math.exp 用 fdlibm 的写法（_exp，和 V8 逐位一样）、x**2 写成 x*x、Math.round（四舍五入到 +∞）、
Math.min/max 遇 NaN 得 NaN、Number('') = 0。Math.pow / Math.cbrt 仍用 C 库（只影响颜色，差 ≤ 1e-13）。

给智能体用的函数（都返回 JSON 能表示的字典，键名里带单位）：
    spectrum_metrics(ref, lib=None, settings=None)   一条老化谱的全部指标 → {A500 / Abs, ≥4.0 截顶, 峰位 / nm, 峰高（扣基线）/ Abs,
                                                      半高宽 / nm, 带面积 / Abs·nm, 截顶到 / nm, 拟合峰高 / Abs, 拟合可靠, L* a* b* YI, …}
    sample_series(sample, lib=None, settings=None)   一个样品的老化系列：起点、每个条件按天排好的各点和对起点的变化（ΔA500、%、ΔE、ΔYI、Δ拟合峰高）
    kinetics(sample, metric='a500', model=None, lib=None)  每个条件的速率（/天）、R²、到阈值天数（货架期）、Arrhenius Ea（kJ/mol）、Q10、预测温度下的速率
    import_spectra(paths, lib=None)                  新的 *.WVS.csv：读谱、按文件名认样品/条件/天数，存成 老化谱（挂原文件），返回认出的内容
    export_xlsx(samples, path, lib=None)             老化数据表 + 光谱表 + Excel 原生图（A500-天数、谱图），给人看
    compute_all(lib, report=print)                   全部老化谱重算一遍指标，存 results：'老化.指标'、'老化.对起点变化'
设置（lib.setting）：'老化.分析设置'（波长、峰窗、基线、上限 3.999、拟合 held 507/75 nm、动力学温度和阈值）、'老化.文件名规则'。
"""
import json
import math
import re
import struct
from pathlib import Path

VERSION = 'aging-workbench 1.0.0 → py 1'
INF = math.inf
NAN = math.nan


def fin(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _jsround(v):
    return math.floor(v + 0.5)


def _jsmin(a, b):
    return NAN if a != a or b != b else min(a, b)


def _jsmax(a, b):
    return NAN if a != a or b != b else max(a, b)


def _sq(v):
    return v * v                                                  # V8 的 x ** 2 就是 x*x；Python 的 x ** 2 走 C pow，偶尔差最后一位


_LN2HI, _LN2LO = (6.93147180369123816490e-01, -6.93147180369123816490e-01), (1.90821492927058770002e-10, -1.90821492927058770002e-10)
_P = (1.66666666666666019037e-01, -2.77777777770155933842e-03, 6.61375632143793436117e-05, -1.65339022054652515390e-06, 4.13813679705723846039e-08)


def _exp(x):
    """和 V8 的 Math.exp 逐位一样（fdlibm e_exp.c）。C 库的 exp 有约 1/10 的值差最后一位，LM 迭代会把它放大到 1e-7。"""
    hx = struct.unpack('<Q', struct.pack('<d', x))[0] >> 32
    xsb, hx = (hx >> 31) & 1, hx & 0x7fffffff
    if hx >= 0x40862E42:
        if hx >= 0x7ff00000:
            return x if x != x or xsb == 0 else 0.0
        if x > 7.09782712893383973096e+02:
            return INF
        if x < -7.45133219101941108420e+02:
            return 0.0
    k = 0
    if hx > 0x3fd62e42:
        if hx < 0x3FF0A2B2:
            hi, lo, k = x - _LN2HI[xsb], _LN2LO[xsb], 1 - xsb - xsb
        else:
            k = int(1.44269504088896338700e+00 * x + (0.5, -0.5)[xsb])
            hi, lo = x - k * _LN2HI[0], k * _LN2LO[0]
        x = hi - lo
    elif hx < 0x3e300000:
        return 1.0 + x
    t = x * x
    c = x - t * (_P[0] + t * (_P[1] + t * (_P[2] + t * (_P[3] + t * _P[4]))))
    if k == 0:
        return 1.0 - ((x * c) / (c - 2.0) - x)
    y = 1.0 - ((lo - (x * c) / (2.0 - c)) - hi)
    return math.ldexp(y, k) if k >= -1021 else math.ldexp(y, k + 1000) * 9.33263618503218878990e-302


def _jsnum(text):
    t = str(text).strip()
    if t == '':
        return 0.0
    if re.fullmatch(r'[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?', t):
        return float(t)
    if re.fullmatch(r'0[xX][0-9a-fA-F]+', t):
        return float(int(t, 16))
    if re.fullmatch(r'[+-]?Infinity', t):
        return -INF if t.startswith('-') else INF
    return NAN


# ---------------------------------------------------------------- 默认设置（lib/store.mjs DEFAULT_SETTINGS、lib/naming.mjs DEFAULT_RULES）

DEFAULT_RULES = {
    'aliases': [
        {'from': '^5%35-', 'to': '35-5%-'},
        {'from': '-(\\d+(?:\\.\\d+)?)eq(DBU|DMAP|TEA|DIPEA)\\b', 'to': '-$2-$1eq'},
        {'from': '-(\\d+(?:\\.\\d+)?)%?tsoh\\b', 'to': '-$1%TsOH'},
        {'from': 'tsoh\\b', 'to': 'TsOH'},
    ],
    'conditions': [
        {'token': 'RT|常温|室温|25C|25℃', 'label': '室温'},
        {'token': '4C|4℃|冷藏|0-10C', 'label': '冷藏 4 °C'},
        {'token': '冷冻|-?1[58]C|-?20C', 'label': '冷冻'},
        {'token': '(\\d+)(?:C|℃)', 'label': '$1 °C'},
    ],
    'roomAfterDay1': True,
    'kinds': [
        {'pattern': '^PA\\d+-T', 'kind': '配方液'},
        {'pattern': '^LT\\d+', 'kind': '参比'},
        {'pattern': '^(CP\\d+|\\d+[A-Z]?-|\\d+%)', 'kind': '树脂溶液'},
    ],
}

DEFAULT_SETTINGS = {
    'wavelength': 500, 'peakWindow': [470, 540], 'baseline': [440, 600], 'extraWavelengths': [450, 600], 'ceiling': 3.999,
    'fit': {'mode': 'held', 'nm': 507, 'fwhm': 75, 'range': [440, 700]},
    'columns': ['a500', 'peak', 'ceilingUpTo', 'fitH', 'delta', 'pct', 'dE'], 'trendMetric': 'a500',
    'kinetics': {'model': 'linear', 'temps': {'室温': 25, '冷藏 4 °C': 4, '冷冻': -18},
                 'thresholds': {'a500': {'type': 'delta', 'value': 0.5}, 'fitH': {'type': 'delta', 'value': 0.5}, 'dE': {'type': 'abs', 'value': 3}},
                 'predictT': 40},
}


# ---------------------------------------------------------------- 读谱（lib/parse.mjs）

_SPLIT = re.compile(r'[,;\t]|\s{2,}|(?<=\d)\s+(?=[-\d])')


def parse_spectrum(text):
    """*.WVS.csv（"SpectralTest … wave,value, 300.0,4.0000,"）或两列文本 → {header, wavelengths, absorbance, problem}。"""
    header, points, in_data = {}, [], False
    for raw in re.split(r'\r?\n', re.sub('^\ufeff', '', text)):
        cells = [c.strip() for c in _SPLIT.split(raw)]
        if not cells[0]:
            continue
        if len(cells) > 1 and cells[1] != '':
            x, y = _jsnum(cells[0]), _jsnum(cells[1])
            if math.isfinite(x) and math.isfinite(y):
                in_data = True
                points.append([x, y])
                continue
        if in_data:
            continue
        if re.fullmatch(r'(wave|wavelength|波长|nm)', cells[0], re.I):
            continue
        header[re.sub(r':$', '', re.sub('^#', '', cells[0]).strip())] = next((c for c in cells[1:] if c), '')
    points.sort(key=lambda p: p[0])
    w, a = [p[0] for p in points], [p[1] for p in points]
    return {'header': header, 'wavelengths': w, 'absorbance': a, 'problem': _problem(header, w, a)}


def _problem(header, w, a):
    h = ' '.join(f'{k} {v}' for k, v in header.items())
    if re.search(r'NETZSCH|\bTGA?\b|\bDSC\b|MTYPE', h, re.I):
        return f'不是 UV 光谱（{(header.get("INSTRUMENT") or header.get("MTYPE") or "其它仪器").strip()} 的数据）'
    if len(w) < 10:
        return '没有光谱数据'
    if w[0] < 150 or w[-1] > 3300:
        return f'第一列不像波长（{w[0]}–{w[-1]}）'
    steps = [x - w[i] for i, x in enumerate(w[1:]) if x - w[i] > 0]
    mean = sum(steps) / len(steps) if steps else NAN
    if not steps or any(d > mean * 5 for d in steps):
        return '波长间隔不均匀，不像光谱'
    if any(v < -2 or v > 12 for v in a):
        return '数值不像吸光度'
    if header.get('ScanMode') and not re.search('Abs', header['ScanMode'], re.I):
        return f'测量模式是 {header["ScanMode"]}，不是吸光度'
    return None


# ---------------------------------------------------------------- 文件名（lib/naming.mjs）

def _rx(source, whole=False):
    try:
        return re.compile(f'^(?:{source})$' if whole else source, re.I)
    except re.error:
        return None


def _js_repl(template):
    return re.sub(r'\$(\d)', r'\\g<\1>', template.replace('\\', '\\\\'))


def parse_time(label):
    """"D3" → 3 d，"24h" / "24 h" → 1 d，"3d" → 3 d，"0h" → 0。"""
    t = str(label if label is not None else '').strip()
    m = re.fullmatch(r'D(\d+(?:\.\d+)?)', t, re.I)
    if m:
        return {'day': float(m.group(1)), 'hours': None, 'timeLabel': 'D' + m.group(1)}
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(h|d|天|小时)', t, re.I)
    if m:
        n, h = float(m.group(1)), bool(re.fullmatch(r'(h|小时)', m.group(2), re.I))
        return {'day': n / 24 if h else n, 'hours': n if h else None, 'timeLabel': m.group(1) + (' h' if h else ' d')}
    return None


def parse_name(file_name, rules=None):
    """PA-T13-RT-D2 → PA-T13、室温、第 2 天；R100-16-4C-24h → R100-16、冷藏 4 °C、24 h。"""
    rules = rules or DEFAULT_RULES
    base = re.sub(r'\.(csv|txt|dat|asc)$', '', re.sub(r'\.WVS\.csv$', '', re.sub(r'^.*[\\/]', '', file_name), flags=re.I), flags=re.I).strip()
    parts = base.split('-')
    condition, day, hours, time_label, notes = None, None, None, '', []
    time = parse_time(parts[-1]) if len(parts) > 1 or re.match(r'D\d', parts[0], re.I) else None
    if time and len(parts) > 1:
        day, hours, time_label = time['day'], time['hours'], time['timeLabel']
        parts.pop()
    if len(parts) > 1:
        token = parts[-1]
        for c in rules.get('conditions') or []:
            r = _rx(c['token'], True)
            m = r.match(token) if r else None
            if m:
                condition = re.sub(r'\$(\d)', lambda g: (m.group(int(g.group(1))) if int(g.group(1)) <= (r.groups or 0) else None) or '', c['label'])
                parts.pop()
                break
    if rules.get('roomAfterDay1') and condition is None and day is not None and day > 1:
        condition = '室温'
        notes.append('文件名没写条件，按室温')
    raw = '-'.join(parts)
    sample = raw.strip()
    for a in rules.get('aliases') or []:
        r = _rx(a['from'])
        if r:
            sample = r.sub(_js_repl(a['to']), sample, count=1)
    if sample != raw:
        notes.append(f'样品名按规则统一：{raw} → {sample}')
    kind = next((k['kind'] for k in rules.get('kinds') or [] if _rx(k['pattern']) and _rx(k['pattern']).search(sample)), '其它')
    return {'sample': sample, 'condition': condition, 'day': day, 'hours': hours, 'timeLabel': time_label, 'kind': kind, 'notes': notes}


def folder_date(path):
    """紫外测试数据(R100-16&17-0&24&48h)-20260611 → 2026-06-11"""
    m = re.search(r'(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)', path or '')
    return f'{m.group(1)}-{m.group(2)}-{m.group(3)}' if m else None


# ---------------------------------------------------------------- 指标（lib/analysis.mjs）

def value_at(spectrum, nm):
    """线性插值吸光度；超出谱的范围 → None。"""
    w, a = spectrum['wavelengths'], spectrum['absorbance']
    if not w or nm < w[0] or nm > w[-1]:
        return None
    i = next(k for k, x in enumerate(w) if x >= nm)
    if w[i] == nm:
        return a[i]
    t = (nm - w[i - 1]) / (w[i] - w[i - 1])
    return a[i - 1] + t * (a[i] - a[i - 1])


CMF = [[0.001368, 0.000039, 0.00645], [0.004243, 0.00012, 0.02005], [0.01431, 0.000396, 0.06785], [0.04351, 0.00121, 0.2074], [0.13438, 0.004, 0.6456],
       [0.2839, 0.0116, 1.3856], [0.34828, 0.023, 1.74706], [0.3362, 0.038, 1.77211], [0.2908, 0.06, 1.6692], [0.19536, 0.09098, 1.28764],
       [0.09564, 0.13902, 0.81295], [0.03201, 0.20802, 0.46518], [0.0049, 0.323, 0.272], [0.0093, 0.503, 0.1582], [0.06327, 0.71, 0.07825],
       [0.1655, 0.862, 0.04216], [0.2904, 0.954, 0.0203], [0.43345, 0.99495, 0.00875], [0.5945, 0.995, 0.0039], [0.7621, 0.952, 0.0021],
       [0.9163, 0.87, 0.00165], [1.0263, 0.757, 0.0011], [1.0622, 0.631, 0.0008], [1.0026, 0.503, 0.00034], [0.85445, 0.381, 0.00019],
       [0.6424, 0.265, 0.00005], [0.4479, 0.175, 0.00002], [0.2835, 0.107, 0], [0.1649, 0.061, 0], [0.0874, 0.032, 0], [0.04677, 0.017, 0],
       [0.0227, 0.00821, 0], [0.011359, 0.004102, 0], [0.00579, 0.002091, 0], [0.002899, 0.001047, 0], [0.00144, 0.00052, 0],
       [0.00069, 0.000249, 0], [0.000332, 0.00012, 0], [0.000166, 0.00006, 0], [0.000083, 0.00003, 0], [0.000042, 0.000015, 0]]
D65 = [49.9755, 54.6482, 82.7549, 91.486, 93.4318, 86.6823, 104.865, 117.008, 117.812, 114.861, 115.923, 108.811, 109.354, 107.802, 104.79,
       107.689, 104.405, 104.046, 100, 96.3342, 95.788, 88.6856, 90.0062, 89.5991, 87.6987, 83.2886, 83.6992, 80.0268, 80.2146, 82.2778,
       78.2842, 69.7213, 71.6091, 74.349, 61.604, 69.8856, 75.087, 63.5927, 46.4182, 66.8054, 63.3828]


def _sum(fn):
    s = 0
    for i, c in enumerate(CMF):
        s = s + fn(c, i)
    return s


K_NORM = 100 / _sum(lambda c, i: D65[i] * c[1])
WHITE = [K_NORM * _sum(lambda c, i, j=j: D65[i] * c[j]) for j in range(3)]


def _lab(x, y, z):
    f = lambda t: math.cbrt(t) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116
    wx, wy, wz = WHITE
    return {'L': 116 * f(y / wy) - 16, 'a': 500 * (f(x / wx) - f(y / wy)), 'b': 200 * (f(y / wy) - f(z / wz))}


def colour(spectrum):
    """T = 10^−A，CIE 1931 2°、D65、380–780 nm 每 10 nm → L*a*b*、XYZ、黄度 YI（ASTM E313）、RGB。谱不到 380–780 nm → None。"""
    t = []
    for i in range(len(CMF)):
        v = value_at(spectrum, 380 + 10 * i)
        t.append(None if v is None else 10 ** -_jsmax(0, v))
    if any(v is None for v in t):
        return None
    X, Y, Z = [K_NORM * _sum(lambda c, i, j=j: D65[i] * c[j] * t[i]) for j in range(3)]

    def gamma(v):
        v /= 100
        v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        return _jsround(255 * _jsmin(1, _jsmax(0, v)))
    r = [gamma(v) for v in (3.2406 * X - 1.5372 * Y - 0.4986 * Z, -0.9689 * X + 1.8758 * Y + 0.0415 * Z, 0.0557 * X - 0.204 * Y + 1.057 * Z)]
    return {**_lab(X, Y, Z), 'X': X, 'Y': Y, 'Z': Z, 'yi': 100 * (1.2985 * X - 1.1335 * Z) / Y if Y > 0 else None, 'rgb': f'rgb({",".join(map(str, r))})'}


def delta_e(p, q):
    return math.hypot(p['L'] - q['L'], p['a'] - q['a'], p['b'] - q['b']) if p and q else None


def _key(nm):
    return str(int(nm)) if float(nm).is_integer() else repr(float(nm))


def measure(spectrum, settings=None):
    """一条谱的全部指标（和旧程序 /api/state 的 m 一样的结构）。spectrum: {wavelengths, absorbance}。"""
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    ceiling, w, a = settings['ceiling'], spectrum['wavelengths'], spectrum['absorbance']
    pw, bl = settings['peakWindow'], settings['baseline']
    a500 = value_at(spectrum, settings['wavelength'])
    at500 = a500 is not None and a500 >= ceiling
    peak_index = -1
    for i in range(len(w)):
        if pw[0] <= w[i] <= pw[1] and (peak_index < 0 or a[i] > a[peak_index]):
            peak_index = i
    window_ceiling = any(pw[0] <= x <= pw[1] and a[i] >= ceiling for i, x in enumerate(w))
    in_win = [i for i in range(len(w)) if pw[0] <= w[i] <= pw[1]]
    edge = peak_index >= 0 and not window_ceiling and (peak_index == in_win[0] or peak_index == in_win[-1])
    peak = None if peak_index < 0 else {'nm': None if window_ceiling or edge else w[peak_index], 'value': a[peak_index], 'ceiling': window_ceiling, 'edge': edge}
    b0, b1 = value_at(spectrum, bl[0]), value_at(spectrum, bl[1])
    line = lambda nm: b0 + (b1 - b0) * (nm - bl[0]) / (bl[1] - bl[0])
    height = fwhm = area = None
    area_low = False
    if a500 is not None and b0 is not None and b1 is not None:
        height = a500 - line(settings['wavelength'])
        area = 0
        for i in range(1, len(w)):
            if w[i - 1] >= bl[0] and w[i] <= bl[1]:
                area += ((a[i] - line(w[i])) + (a[i - 1] - line(w[i - 1]))) / 2 * (w[i] - w[i - 1])
                if a[i] >= ceiling:
                    area_low = True
        if peak and peak['nm']:
            hh = (a[peak_index] - line(w[peak_index])) / 2
            y = lambda i: a[i] - line(w[i])
            left = right = peak_index
            while left > 0 and w[left] > bl[0] and y(left) > hh:
                left -= 1
            while right < len(w) - 1 and w[right] < bl[1] and y(right) > hh:
                right += 1
            if y(left) <= hh and y(right) <= hh and hh > 0:
                xl = w[left] + (hh - y(left)) / (y(left + 1) - y(left)) * (w[left + 1] - w[left])
                xr = w[right - 1] + (y(right - 1) - hh) / (y(right - 1) - y(right)) * (w[right] - w[right - 1])
                fwhm = xr - xl
    fs, fit = settings.get('fit'), None
    if fs and fs.get('mode') != 'off':
        fit = fit_band(spectrum, fs['range'], ceiling, {'nm': fs['nm'], 'fwhm': fs['fwhm']} if fs.get('mode') == 'held' else None)
    last = next((i for i in range(len(a) - 1, -1, -1) if a[i] >= ceiling), -1)
    extra = {}
    for nm in settings.get('extraWavelengths') or []:
        v = value_at(spectrum, nm)
        extra[_key(nm)] = {'value': v, 'ceiling': v is not None and v >= ceiling}
    return {'a500': a500, 'at500Ceiling': at500, 'peak': peak, 'height': height,
            'heightLowerBound': at500 or (b0 if b0 is not None else 0) >= ceiling, 'fwhm': fwhm, 'area': area, 'areaLowerBound': area_low,
            'ceilingUpTo': None if last < 0 else w[last], 'extra': extra, 'colour': colour(spectrum), 'fit': fit}


def change(start, now):
    """对起点的变化：exact、at-least（当天截顶，是下限）、start-ceiling / both-ceiling（算不出）。"""
    if not start or start.get('a500') is None or not now or now.get('a500') is None:
        return None
    delta, de = now['a500'] - start['a500'], delta_e(start.get('colour'), now.get('colour'))
    sc, nc, sf, nf = start.get('colour') or {}, now.get('colour') or {}, start.get('fit') or {}, now.get('fit') or {}
    more = {'dE': de, 'dYI': nc['yi'] - sc['yi'] if fin(sc.get('yi')) and fin(nc.get('yi')) else None,
            'dFit': nf['height'] - sf['height'] if sf.get('reliable') and nf.get('reliable') else None,
            'dFitExtrapolated': bool(sf.get('extrapolated') or nf.get('extrapolated'))}
    if start['at500Ceiling'] and now['at500Ceiling']:
        return {'delta': None, **more, 'kind': 'both-ceiling'}
    if start['at500Ceiling']:
        return {'delta': None, **more, 'kind': 'start-ceiling'}
    return {'delta': delta, **more, 'percent': delta / start['a500'] * 100 if start['a500'] > 0 else None,
            'kind': 'at-least' if now['at500Ceiling'] else 'exact'}


def aging_series(spectra, settings=None):
    """一个样品的谱 → 起点 + 每个条件一组（按天排），每点带 m 和 change。spectra: [{id, wavelengths, absorbance, condition, day, is_start, …}]。"""
    rows = [{**s, 'm': measure(s, settings)} for s in spectra]
    early = sorted([r for r in rows if not r.get('condition') and r.get('day') is not None and r['day'] <= 1], key=lambda r: r['day'])
    start = (next((r for r in rows if r.get('is_start') is True), None) or next((r for r in rows if r.get('day') == 0), None)
             or (early[0] if early else None) or (next((r for r in rows if not r.get('condition') and r.get('day') is None), None) if len(rows) > 1 else None))
    groups = {}
    for r in rows:
        key = r['condition'] if r.get('condition') is not None else ('起点' if r is start else '未写条件')
        groups.setdefault(key, []).append(r)
    for items in groups.values():
        items.sort(key=lambda r: r['day'] if r.get('day') is not None else INF)
    return {'start': start, 'rows': rows, 'groups': [{'condition': c, 'rows': [{**r, 'change': change(start['m'], r['m']) if start and r is not start else None}
                                                                              for r in items]} for c, items in groups.items()]}


# ---------------------------------------------------------------- 507 nm 峰拟合（lib/fit.mjs）

def _solve(A, b):
    n = len(b)
    M = [list(r) + [b[i]] for i, r in enumerate(A)]
    for c in range(n):
        p = c
        for r in range(c + 1, n):
            if abs(M[r][c]) > abs(M[p][c]):
                p = r
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-300:
            return None
        for r in range(n):
            if r != c:
                k = M[r][c] / M[c][c]
                for j in range(c, n + 1):
                    M[r][j] -= k * M[c][j]
    return [r[n] / r[i] for i, r in enumerate(M)]


def lm(f, p0, xs, ys, fixed=(), lower=(), upper=(), iters=120):
    """Levenberg–Marquardt，带上下界和固定参数（逐行照搬 fit.mjs:20）。返回 (p, cost)。"""
    p, lam = list(p0), 1e-3
    free = [i for i in range(len(p)) if i not in fixed]
    clamp = lambda q: [_jsmin(upper[i] if i < len(upper) and upper[i] is not None else INF,
                              _jsmax(lower[i] if i < len(lower) and lower[i] is not None else -INF, v)) for i, v in enumerate(q)]

    def cost(q):
        s = 0
        for i in range(len(xs)):
            r = ys[i] - f(xs[i], q)
            s += r * r
        return s
    c0 = cost(p)
    for _ in range(iters):
        J = []
        for x in xs:
            f0, row = f(x, p), []
            for j in free:
                h = 1e-6 * (abs(p[j]) + 1e-3)
                q = list(p)
                q[j] += h
                row.append((f(x, q) - f0) / h)
            J.append(row)
        r = [ys[i] - f(x, p) for i, x in enumerate(xs)]
        n = len(free)
        JTJ = [[0] * n for _ in range(n)]
        for a_ in range(n):
            for b_ in range(n):
                s = 0
                for row in J:
                    s = s + row[a_] * row[b_]
                JTJ[a_][b_] = s
        JTr = []
        for a_ in range(n):
            s = 0
            for i, row in enumerate(J):
                s = s + row[a_] * r[i]
            JTr.append(s)
        improved, tries = False, 0
        while tries < 10 and not improved:
            d = _solve([[v * (1 + lam) + 1e-12 if a_ == b_ else v for b_, v in enumerate(row)] for a_, row in enumerate(JTJ)], JTr)
            tries += 1
            if not d:
                lam *= 10
                continue
            q = list(p)
            for a_, j in enumerate(free):
                q[j] += d[a_]
            qc = clamp(q)
            c1 = cost(qc)
            if c1 < c0:
                rel = (c0 - c1) / c0
                p, c0 = qc, c1
                lam = max(1e-9, lam / 4)
                improved = True
                if rel < 1e-10:
                    return p, c0
            else:
                lam *= 6
        if not improved:
            break
    return p, c0


def fit_model(x, p):
    H, x0, s, c, k, d = p
    return H * _exp(-_sq(x - x0) / (2 * s * s)) + c * _exp(k * (x - 25)) + d


def band_only(x, p):
    return p[0] * _exp(-_sq(x - p[1]) / (2 * p[2] * p[2]))


def background(x, p):
    return p[3] * _exp(p[4] * (x - 25)) + p[5]


def fit_band(spectrum, range_=(400, 700), ceiling=3.999, shape=None):
    """波数 x = 10⁴/λ：高斯带 + 指数紫外尾 + 常数；截顶点不参与；shape={nm, fwhm} 时固定中心和宽度（"外推"）。"""
    w, a, xs, ys, cut = spectrum['wavelengths'], spectrum['absorbance'], [], [], 0
    for i in range(len(w)):
        if range_[0] <= w[i] <= range_[1]:
            if a[i] >= ceiling:
                cut += 1
                continue
            xs.append(1e4 / w[i])
            ys.append(a[i])
    if len(xs) < 12:
        return None

    def near(nm):
        best = None
        for i in range(len(xs)):
            if best is None or abs(1e4 / xs[i] - nm) < abs(1e4 / xs[best] - nm):
                best = i
        return ys[best]
    d0 = min((y for i, y in enumerate(ys) if 1e4 / xs[i] > range_[1] - 60), default=INF)
    tail = _jsmax(0.01, near(range_[0]) - d0)
    x0 = 1e4 / shape['nm'] if shape else 20
    s0 = (1e4 / (shape['nm'] - shape['fwhm'] / 2) - 1e4 / (shape['nm'] + shape['fwhm'] / 2)) / 2.3548 if shape else 1.2
    p0 = [_jsmax(0.01, near(500) - d0 - tail * _exp(1 * (20 - 25))), x0, s0, tail, 1, d0]
    opts = {'lower': [0, 18.2, 0.35, 0, 0.02, -1], 'upper': [60, 21.8, 3.5, 1e4, 6, 3], 'fixed': [1, 2] if shape else []}
    best = lm(fit_model, p0, xs, ys, **opts)
    alt = lm(fit_model, [p0[0] * 3 + 0.2, x0, s0, tail * 0.5, 1.5, d0], xs, ys, **opts)
    if alt[1] < best[1]:
        best = alt
    p, cost = best
    mean = 0
    for v in ys:
        mean = mean + v
    mean /= len(ys)
    ss_tot = 0
    for v in ys:
        ss_tot = ss_tot + _sq(v - mean)
    nm = 1e4 / p[1]
    fwhm = 1e4 / (p[1] - 1.1774 * p[2]) - 1e4 / (p[1] + 1.1774 * p[2])
    area, l = 0, range_[0]
    while l < range_[1]:
        area += (band_only(1e4 / l, p) + band_only(1e4 / (l + 1), p)) / 2
        l += 1
    side = lambda lo, hi: sum(1 for x in xs if lo <= 1e4 / x <= hi)
    reliable = side(nm - 0.75 * fwhm, nm - 0.2 * fwhm) >= 3 and side(nm + 0.2 * fwhm, nm + 0.75 * fwhm) >= 3
    return {'height': p[0], 'nm': nm, 'fwhm': fwhm, 'area': area, 'at500': band_only(20, p), 'background500': background(20, p),
            'r2': 1 - cost / ss_tot if ss_tot else None, 'rmse': math.sqrt(cost / len(ys)), 'points': len(ys), 'cut': cut, 'extrapolated': cut > 0,
            'reliable': reliable, 'heldShape': bool(shape), 'range': list(range_), 'p': p}


# ---------------------------------------------------------------- 动力学和货架期（lib/kinetics.mjs、app/app.js:91 kineticsOf）

MODELS = {'linear': '线性', 'first': '一级（趋于平台）', 'exp': '指数（自加速）'}


def _linear(pts):
    n = len(pts)
    mx = sum_seq(p['x'] for p in pts) / n
    my = sum_seq(p['y'] for p in pts) / n
    sxx = sum_seq(_sq(p['x'] - mx) for p in pts)
    sxy = sum_seq((p['x'] - mx) * (p['y'] - my) for p in pts)
    k = sxy / sxx if sxx else 0
    y0 = my - k * mx
    return {'k': k, 'y0': y0, 'f': lambda t: y0 + k * t}


def sum_seq(values):
    s = 0
    for v in values:
        s = s + v
    return s


def _r2(pts, f):
    my = sum_seq(p['y'] for p in pts) / len(pts)
    tot = sum_seq(_sq(p['y'] - my) for p in pts)
    res = sum_seq(_sq(p['y'] - f(p['x'])) for p in pts)
    return 1 - res / tot if tot else None


def fit_kinetics(pts, model='linear'):
    """pts: [{x: 天, y}]（只放不截顶的点）→ {model, k, y0, yInf, rate0, f, r2, n} 或 None。"""
    if len(pts) < 2:
        return None
    if model == 'first' and len(pts) >= 3:
        lin = _linear(pts)
        last = pts[0]
        for b in pts[1:]:
            last = b if b['x'] > last['x'] else last
        p, _ = lm(lambda t, q: q[1] - (q[1] - q[0]) * _exp(-q[2] * t), [lin['y0'], last['y'] + (last['y'] - lin['y0']) * 0.5, 0.3],
                  [q['x'] for q in pts], [q['y'] for q in pts], lower=[-INF, -INF, 1e-6], upper=[INF, INF, 50])
        y0, y_inf, k = p
        out = {'model': model, 'y0': y0, 'yInf': y_inf, 'k': k, 'rate0': k * (y_inf - y0), 'f': lambda t: y_inf - (y_inf - y0) * _exp(-k * t)}
    elif model == 'exp' and all(q['y'] > 0 for q in pts):
        lin = _linear([{'x': q['x'], 'y': math.log(q['y'])} for q in pts])
        out = {'model': model, 'y0': _exp(lin['y0']), 'k': lin['k'], 'rate0': lin['k'] * _exp(lin['y0']),
               'f': lambda t: _exp(lin['y0'] + lin['k'] * t)}
    else:
        lin = _linear(pts)
        out = {'model': 'linear', 'y0': lin['y0'], 'k': lin['k'], 'rate0': lin['k'], 'f': lin['f']}
    return {**out, 'r2': _r2(pts, out['f']) if len(pts) > 2 else None, 'n': len(pts)}


def time_to(fit, target, limit=3650):
    """模型走到 target 要几天（往后找到 limit 天）；到不了 → None。"""
    if not fit:
        return None
    up = target >= fit['f'](0)
    if fit['model'] == 'linear':
        if not fit['k'] or (fit['k'] > 0) != up:
            return None
        t = (target - fit['y0']) / fit['k']
        return t if 0 <= t <= limit else None
    lo, hi, t = 0, None, 0.25
    while t <= limit:
        if (fit['f'](t) >= target) == up:
            hi = t
            break
        lo = t
        t *= 1.25
    if hi is None:
        return None
    for _ in range(50):
        m = (lo + hi) / 2
        if (fit['f'](m) >= target) == up:
            hi = m
        else:
            lo = m
    return hi


def arrhenius(items):
    """[{T: °C, k: /天}] 两个以上温度 → Ea（kJ/mol）、lnA、rate_at(T)、Q10(T)。"""
    ok = [r for r in items if r['k'] > 0 and fin(r['T'])]
    if len(ok) < 2 or len({r['T'] for r in ok}) < 2:
        return None
    lin = _linear([{'x': 1 / (r['T'] + 273.15), 'y': math.log(r['k'])} for r in ok])
    ea = -lin['k'] * 8.314462618 / 1000
    rate_at = lambda T: _exp(lin['y0'] + lin['k'] / (T + 273.15))
    return {'Ea': ea, 'lnA': lin['y0'], 'rate_at': rate_at, 'Q10': lambda T=25: rate_at(T + 10) / rate_at(T), 'n': len(ok)}


def _fit_ok(r):
    return (r['m'].get('fit') or {}).get('reliable')


# 趋势指标（app.js:56 metricList 里 trend 的那些）：取值、是不是下限（截顶）、是不是"对起点的变化"、单位
METRICS = {
    'a500': {'head': 'A500', 'unit': 'Abs', 'val': lambda r: r['m']['a500'], 'low': lambda r: r['m']['at500Ceiling']},
    'height': {'head': '峰高（扣斜线基线）', 'unit': 'Abs', 'val': lambda r: r['m']['height'], 'low': lambda r: r['m']['heightLowerBound']},
    'area': {'head': '带面积', 'unit': 'Abs·nm', 'val': lambda r: r['m']['area'], 'low': lambda r: r['m']['areaLowerBound']},
    'ceilingUpTo': {'head': '截顶到', 'unit': 'nm', 'val': lambda r: r['m']['ceilingUpTo']},
    'fitH': {'head': '拟合峰高', 'unit': 'Abs', 'val': lambda r: r['m']['fit']['height'] if _fit_ok(r) else None},
    'fitBg': {'head': '500 nm 处背景', 'unit': 'Abs', 'val': lambda r: r['m']['fit']['background500'] if _fit_ok(r) else None},
    'L': {'head': 'L*', 'unit': '', 'val': lambda r: (r['m']['colour'] or {}).get('L')},
    'a': {'head': 'a*', 'unit': '', 'val': lambda r: (r['m']['colour'] or {}).get('a')},
    'b': {'head': 'b*', 'unit': '', 'val': lambda r: (r['m']['colour'] or {}).get('b')},
    'yi': {'head': '黄度 YI', 'unit': '', 'val': lambda r: (r['m']['colour'] or {}).get('yi')},
    'dE': {'head': 'ΔE*ab', 'unit': '', 'change': True, 'val': lambda r: (r.get('change') or {}).get('dE')},
    'dFit': {'head': 'Δ拟合峰高', 'unit': 'Abs', 'change': True, 'val': lambda r: (r.get('change') or {}).get('dFit')},
    'dYI': {'head': 'ΔYI', 'unit': '', 'change': True, 'val': lambda r: (r.get('change') or {}).get('dYI')},
}


def _metric(key):
    if key in METRICS:
        return {'key': key, **METRICS[key]}
    m = re.fullmatch(r'A(\d+(?:\.\d+)?)', key)                      # 设置里加的其它波长：A450、A600
    if m:
        nm = m.group(1)
        return {'key': key, 'head': key, 'unit': 'Abs', 'val': lambda r: ((r['m']['extra'] or {}).get(nm) or {}).get('value'),
                'low': lambda r: ((r['m']['extra'] or {}).get(nm) or {}).get('ceiling')}
    raise ValueError(f'没有指标 {key}。可用：{"、".join(METRICS)}、A<波长>（设置 extraWavelengths 里的）')


def temp_of(condition, kin):
    t = (kin.get('temps') or {}).get(condition)
    if fin(t):
        return t
    m = re.search(r'(-?\d+(?:\.\d+)?)\s*°?\s*C', condition or '', re.I)
    return float(m.group(1)) if m else None


def kinetics_of(rows, tm, base, base_low, kin):
    """一个条件的点（含起点）→ 拟合、阈值、到阈值天数 t、已超过的那一点 seen（app.js:91 照搬）。"""
    val, low = tm['val'], tm.get('low') or (lambda r: False)
    every = [r for r in rows if fin(r.get('day')) and fin(val(r))]
    exact, lows = [r for r in every if not low(r)], [r for r in every if low(r)]
    fit = fit_kinetics([{'x': r['day'], 'y': val(r)} for r in exact], kin.get('model') or 'linear')
    thr = (kin.get('thresholds') or {}).get(tm['key'])
    b = base if fin(base) else (fit['f'](0) if fit else None)
    target = None if not thr or not fin(thr.get('value')) else (thr['value'] if tm.get('change') or thr.get('type') == 'abs'
                                                                 else (b + thr['value'] if fin(b) and not base_low else None))
    up = target >= b if fin(target) and fin(b) else True
    past = lambda v: v >= target if up else v <= target
    seen = sorted([r for r in every if past(val(r))], key=lambda r: r['day'])[0] if fin(target) and any(past(val(r)) for r in every) else None
    t = time_to(fit, target) if fin(target) and not seen else None
    return {'fit': fit, 'target': target, 'base': b, 'baseLow': base_low, 't': t, 'seen': seen, 'lows': len(lows), 'n': len(exact)}


# ---------------------------------------------------------------- 接新库：取数、算、存

def _lib(lib):
    if lib is None or isinstance(lib, str):
        from lab import store
        return store.open(lib or '产品A')
    return lib


def settings_of(lib):
    return {**DEFAULT_SETTINGS, **(lib.setting('老化.分析设置') or {})}


def _spectrum(lib, row):
    cols, rows, _ = lib.table(row['id'], '谱')
    return {'id': row['id'], 'name': row['name'], 'sample': row.get('样品'), 'condition': row.get('条件'), 'day': row.get('天数'),
            'hours': row.get('小时'), 'time_label': row.get('时间标签') or '', 'is_start': row.get('是否起点'), 'test_date': row.get('测试日'),
            'wavelengths': [r[0] for r in rows], 'absorbance': [r[1] for r in rows]}


def _round(v, d=6):
    return round(v, d) if fin(v) else v


def _flat(r):
    """给智能体看的一行：键名带单位。截顶的值是下限，写在"截顶"里。"""
    m, ch = r['m'], r.get('change') or {}
    fit, col, peak = m.get('fit') or {}, m.get('colour') or {}, m.get('peak') or {}
    return {'id': r['id'], '谱': r.get('name'), '条件': r.get('condition') or ('起点' if r.get('is_start') else None), '天数 / 天': r.get('day'),
            '时间': r.get('time_label') or (f'D{r["day"]:g}' if fin(r.get('day')) else '未写时间'),
            'A500 / Abs': _round(m['a500']), 'A500 截顶（≥ 4.0，下限）': m['at500Ceiling'],
            '峰位 / nm': peak.get('nm'), '峰窗截顶': peak.get('ceiling'), '峰窗无峰（只有紫外尾）': peak.get('edge'),
            '峰高（扣 440–600 基线）/ Abs': _round(m['height']), '峰高是下限': m['heightLowerBound'], '半高宽 / nm': _round(m['fwhm'], 2),
            '带面积 / Abs·nm': _round(m['area'], 3), '截顶到 / nm': m['ceilingUpTo'],
            '拟合峰高 / Abs': _round(fit.get('height')) if fit.get('reliable') else None, '拟合可靠': fit.get('reliable'), '拟合外推（中心截顶）': fit.get('extrapolated'),
            '拟合 R²': _round(fit.get('r2')), '500 nm 处背景 / Abs': _round(fit.get('background500')) if fit.get('reliable') else None,
            'L*': _round(col.get('L'), 3), 'a*': _round(col.get('a'), 3), 'b*': _round(col.get('b'), 3), '黄度 YI': _round(col.get('yi'), 3),
            'ΔA500 / Abs': _round(ch.get('delta')), 'ΔA500 / %': _round(ch.get('percent'), 3), '变化口径': ch.get('kind'),
            'ΔE*ab': _round(ch.get('dE'), 3), 'ΔYI': _round(ch.get('dYI'), 3), 'Δ拟合峰高 / Abs': _round(ch.get('dFit'))}


def spectrum_metrics(ref, lib=None, settings=None):
    """一条老化谱（id 或名字）→ 全部指标。"""
    lib = _lib(lib)
    rec = lib.get(ref, '老化谱')
    s = _spectrum(lib, {'id': rec['id'], 'name': rec['name'], **rec['data']})
    return _flat({**s, 'm': measure(s, settings or settings_of(lib))})


def _series(lib, sample, settings):
    rows = [r for r in lib.rows('老化谱') if r.get('样品') == sample]
    if not rows:
        raise ValueError(f'没有样品 {sample} 的老化谱。用 SELECT DISTINCT 样品 FROM "老化谱" 看有哪些。')
    return aging_series([_spectrum(lib, r) for r in rows], settings)


def sample_series(sample, lib=None, settings=None):
    """一个样品的老化系列：起点、每个条件按天排好的点（含对起点的变化）。"""
    lib = _lib(lib)
    a = _series(lib, sample, settings or settings_of(lib))
    return {'样品': sample, '起点': _flat(a['start']) if a['start'] else None,
            '条件': [{'条件': g['condition'], '点': [_flat(r) for r in g['rows']]} for g in a['groups']]}


def kinetics(sample, metric=None, model=None, lib=None, settings=None):
    """每个条件的速率、R²、到阈值天数（货架期），以及温度依赖（Ea、Q10、预测温度下的速率）。"""
    lib = _lib(lib)
    settings = settings or settings_of(lib)
    kin = {**DEFAULT_SETTINGS['kinetics'], **(settings.get('kinetics') or {})}
    if model:
        kin['model'] = model
    tm = _metric(metric or settings.get('trendMetric') or 'a500')
    a = _series(lib, sample, settings)
    start = a['start']
    base = (0 if tm.get('change') else tm['val'](start)) if start else None
    base_low = bool(start and (tm.get('low') or (lambda r: False))(start))
    out = []
    for g in a['groups']:
        if g['condition'] == '起点':
            continue
        rows = ([{**start, 'change': {'delta': 0, 'dE': 0, 'dFit': 0, 'dYI': 0} if tm.get('change') else None}]
                if start and not any(r['id'] == start['id'] for r in g['rows']) else []) + g['rows']
        k = kinetics_of(rows, tm, base, base_low, kin)
        fit = k['fit']
        out.append({'条件': g['condition'], '温度 / °C': temp_of(g['condition'], kin), '点数（不截顶）': k['n'], '截顶点数': k['lows'],
                    '模型': MODELS.get(fit['model']) if fit else None, f'初速率 / ({tm["unit"] or "单位"}/天)': _round(fit['rate0'], 8) if fit else None,
                    'R²': _round(fit['r2']) if fit else None, '阈值': k['target'], '起点值': _round(k['base']), '起点已截顶': k['baseLow'],
                    '已超过阈值的最早一天 / 天': k['seen']['day'] if k['seen'] else None, '到阈值 / 天': _round(k['t'], 3),
                    '_rate0': fit['rate0'] if fit else None})
    arr = arrhenius([{'T': x['温度 / °C'], 'k': x['_rate0']} for x in out if x['_rate0'] is not None and fin(x['温度 / °C'])])
    pt = kin.get('predictT', 40)
    for x in out:
        x.pop('_rate0')
    return {'样品': sample, '指标': f'{tm["head"]}' + (f' / {tm["unit"]}' if tm['unit'] else ''), '阈值设置': (kin.get('thresholds') or {}).get(tm['key']),
            '各条件': out, '温度依赖': {'Ea / (kJ/mol)': _round(arr['Ea'], 4), 'Q10（25 °C）': _round(arr['Q10'](25), 4), '点数': arr['n'],
                                   f'{pt} °C 下的速率 / ({tm["unit"] or "单位"}/天)': _round(arr['rate_at'](pt), 8)} if arr else None}


def compute_all(lib=None, report=print):
    """全部老化谱重算：'老化.指标'（measure 的 m 原样）、'老化.对起点变化'（同样品起点；是起点的记 {'起点': True}）。"""
    lib = _lib(lib)
    settings = settings_of(lib)
    params = {k: settings[k] for k in ('wavelength', 'peakWindow', 'baseline', 'extraWavelengths', 'ceiling', 'fit')}
    by_sample = {}
    for r in lib.rows('老化谱'):
        by_sample.setdefault(r.get('样品'), []).append(r)
    count = 0
    for sample, rows in by_sample.items():
        a = aging_series([_spectrum(lib, r) for r in rows], settings)
        for g in a['groups']:
            for r in g['rows']:
                lib.set_result(r['id'], '老化.指标', r['m'], params, VERSION)
                lib.set_result(r['id'], '老化.对起点变化', {'起点': True} if r is a['start'] or (a['start'] and r['id'] == a['start']['id'])
                               else {**(r['change'] or {}), '起点谱': a['start']['id'] if a['start'] else None}, params, VERSION)
                count += 1
    report(f'老化：{len(by_sample)} 个样品、{count} 条谱已重算')
    return {'样品': len(by_sample), '谱': count}


# ---------------------------------------------------------------- 导入新文件

def import_spectra(paths, lib=None, overrides=None):
    """读 *.WVS.csv，按文件名认样品/条件/天数（规则在设置 '老化.文件名规则'），存成 老化谱并挂原文件。
    overrides: {文件名: {样品, 条件, 天数, …}} 人改对的值。同一文件（指纹相同）再导一次是更新，不重复。"""
    import hashlib
    lib = _lib(lib)
    rules = lib.setting('老化.文件名规则') or DEFAULT_RULES
    out = []
    for path in [Path(p) for p in ([paths] if isinstance(paths, (str, Path)) else paths)]:
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        spec = parse_spectrum(raw.decode('utf-8', errors='replace'))
        if spec['problem']:
            out.append({'文件': path.name, '没导入': spec['problem']})
            continue
        n = parse_name(path.name, rules)
        fields = {'样品': n['sample'], '种类': n['kind'], '条件': n['condition'], '天数': n['day'], '小时': n['hours'], '时间标签': n['timeLabel'],
                  '测试日': folder_date(str(path.parent)) or folder_date(path.name), '原文件名': path.name, '原路径': str(path),
                  '文件夹': path.parent.name, '指纹': sha, '仪器表头': spec['header'], '导入说明': '；'.join(n['notes']) or None,
                  **((overrides or {}).get(path.name) or {})}
        file_id = lib.add_file(path, kind='老化谱', subfolder='老化谱')
        found = lib.sql('SELECT id FROM "老化谱" WHERE 指纹=?', (sha,))[1] if lib.kind('老化谱') else []
        saved = lib.save('老化谱', [{**({'id': found[0][0]} if found else {'id': 'age-' + sha[:16]}), 'name': re.sub(r'\.WVS\.csv$', '', path.name, flags=re.I),
                                    **fields, 'files': [file_id],
                                    'series': {'谱': {'columns': ['波长 / nm', '吸光度'], 'rows': list(zip(spec['wavelengths'], spec['absorbance']))}}}],
                         source=str(path))
        out.append({'文件': path.name, 'id': saved[0]['id'], '新建': saved[0]['created'], **{k: fields[k] for k in ('样品', '种类', '条件', '天数', '时间标签', '导入说明')}})
    return out


# ---------------------------------------------------------------- 导出 xlsx（给人看：数据表 + 光谱 + Excel 原生图）

def export_xlsx(samples, path, lib=None):
    from openpyxl import Workbook
    from openpyxl.chart import Reference, ScatterChart, Series
    lib = _lib(lib)
    settings = settings_of(lib)
    samples = [samples] if isinstance(samples, str) else list(samples)
    wb = Workbook()
    data = wb.active
    data.title = '数据'
    head = ['样品', '条件', '时间', '天数', 'A500', '≥4.0', '峰位 nm', '峰高（扣基线）', '半高宽 nm', '带面积 A·nm', '截顶到 nm', '拟合峰高', '拟合 R²',
            'L*', 'a*', 'b*', 'YI', 'ΔA500', '变化率 %', 'ΔE*ab', 'Δ拟合峰高', '谱']
    data.append(head)
    spec_ws, trend_ws, charts_ws = wb.create_sheet('光谱'), wb.create_sheet('随时间'), wb.create_sheet('光谱图')
    col = 1
    trend_row = 1
    for si, sample in enumerate(samples):
        a = _series(lib, sample, settings)
        chart = ScatterChart()
        chart.title, chart.x_axis.title, chart.y_axis.title = f'{sample} 光谱', '波长（nm）', '吸光度'
        chart.x_axis.scaling.min, chart.x_axis.scaling.max, chart.y_axis.scaling.min, chart.y_axis.scaling.max = 300, 800, 0, 4.2
        tchart = ScatterChart()
        tchart.title, tchart.x_axis.title, tchart.y_axis.title = f'{sample} A500 随储存时间（≥ 4.0 记 4）', '储存时间（天）', 'A500'
        for g in a['groups']:
            for r in g['rows']:
                f = _flat(r)
                data.append([sample, f['条件'], f['时间'], f['天数 / 天'], f['A500 / Abs'], '≥' if f['A500 截顶（≥ 4.0，下限）'] else '', f['峰位 / nm'],
                             f['峰高（扣 440–600 基线）/ Abs'], f['半高宽 / nm'], f['带面积 / Abs·nm'], f['截顶到 / nm'], f['拟合峰高 / Abs'], f['拟合 R²'],
                             f['L*'], f['a*'], f['b*'], f['黄度 YI'], f['ΔA500 / Abs'], f['ΔA500 / %'], f['ΔE*ab'], f['Δ拟合峰高 / Abs'], r['name']])
                spec_ws.cell(1, col, '波长 nm')
                spec_ws.cell(1, col + 1, f'{sample} {f["条件"] or ""} {f["时间"]}')
                for i, (x, y) in enumerate(zip(r['wavelengths'], r['absorbance']), 2):
                    spec_ws.cell(i, col, x)
                    spec_ws.cell(i, col + 1, y)
                s = Series(Reference(spec_ws, min_col=col + 1, min_row=2, max_row=len(r['wavelengths']) + 1),
                           Reference(spec_ws, min_col=col, min_row=2, max_row=len(r['wavelengths']) + 1), title=spec_ws.cell(1, col + 1).value)
                s.marker.symbol = 'none'
                chart.series.append(s)
                col += 2
            pts = sorted([r for r in g['rows'] if fin(r.get('day')) and fin(r['m']['a500'])], key=lambda r: r['day'])
            if a['start'] and g['condition'] != '起点' and not any(r['id'] == a['start']['id'] for r in pts) and fin(a['start'].get('day')):
                pts = [a['start']] + pts
            if pts:
                trend_ws.cell(trend_row, 1, f'{sample} {g["condition"]}')
                trend_ws.cell(trend_row, 2, 'A500')
                for i, r in enumerate(pts, 1):
                    trend_ws.cell(trend_row + i, 1, r['day'])
                    trend_ws.cell(trend_row + i, 2, min(r['m']['a500'], 4))
                s = Series(Reference(trend_ws, min_col=2, min_row=trend_row + 1, max_row=trend_row + len(pts)),
                           Reference(trend_ws, min_col=1, min_row=trend_row + 1, max_row=trend_row + len(pts)), title=f'{g["condition"]}')
                s.marker.symbol = 'circle'
                tchart.series.append(s)
                trend_row += len(pts) + 2
        chart.width = tchart.width = 16
        chart.height = tchart.height = 9
        charts_ws.add_chart(chart, f'A{1 + si * 20}')
        charts_ws.add_chart(tchart, f'K{1 + si * 20}')
    data.freeze_panes = 'B2'
    for i, w in enumerate([20, 12, 8, 7, 9, 5, 8, 12, 9, 11, 9, 9, 8, 7, 7, 7, 7, 9, 9, 8, 10, 26], 1):
        data.column_dimensions[data.cell(1, i).column_letter].width = w
    wb.save(path)
    return {'文件': str(path), '样品': samples}
