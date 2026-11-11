"""DOE analysis engine: regression, ANOVA, prediction, contour grids, desirability optimisation.

Pure Python + numpy. Every input and output is a JSON-serialisable dict / list.
Statistics are checked against published examples in tests/test_doe_analysis.py
(see docs/doe_analysis.md).  No scipy: the t and F distributions are built on the
regularised incomplete beta function.
"""
from __future__ import annotations

from copy import deepcopy
import itertools
import math

import numpy as np


class AnalysisError(ValueError):
    pass


# --------------------------------------------------------------------------- distributions

def _betacf(a, b, x):
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1., a - 1.
    c, d = 1., 1. - qab * x / qap
    d = 1. / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1. + aa * d
        d = 1. / (d if abs(d) > tiny else tiny)
        c = 1. + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1. + aa * d
        d = 1. / (d if abs(d) > tiny else tiny)
        c = 1. + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.) < 1e-15:
            break
    return h


def betainc_reg(a, b, x):
    """Regularised incomplete beta function I_x(a, b)."""
    if x <= 0.:
        return 0.
    if x >= 1.:
        return 1.
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.) / (a + b + 2.):
        return front * _betacf(a, b, x) / a
    return 1. - front * _betacf(b, a, 1. - x) / b


def t_sf(t, df):
    """P(T > t) for Student's t with df degrees of freedom."""
    if t == 0:
        return 0.5
    tail = 0.5 * betainc_reg(df / 2., 0.5, df / (df + t * t))
    return tail if t > 0 else 1. - tail


def t_cdf(t, df):
    return 1. - t_sf(t, df)


def t_two_sided_p(t, df):
    return betainc_reg(df / 2., 0.5, df / (df + t * t))


def f_sf(f, d1, d2):
    """P(F > f) for the F distribution with (d1, d2) degrees of freedom."""
    if f <= 0:
        return 1.
    return betainc_reg(d2 / 2., d1 / 2., d2 / (d2 + d1 * f))


def f_cdf(f, d1, d2):
    return 1. - f_sf(f, d1, d2)


def _bisect_quantile(sf, p_upper, lo, hi):
    """Find x with sf(x) = p_upper by bisection (sf decreasing)."""
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if sf(mid) > p_upper:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-15 * max(1., abs(hi)):
            break
    return 0.5 * (lo + hi)


def t_ppf(p, df):
    """Quantile of Student's t: P(T <= x) = p."""
    if not 0. < p < 1.:
        raise AnalysisError('分位数的概率必须在 0 和 1 之间。')
    if p == 0.5:
        return 0.
    if p < 0.5:
        return -t_ppf(1. - p, df)
    hi = 1.
    while t_sf(hi, df) > 1. - p:
        hi *= 2.
        if hi > 1e300:
            break
    return _bisect_quantile(lambda x: t_sf(x, df), 1. - p, 0., hi)


def f_ppf(p, d1, d2):
    """Quantile of the F distribution: P(F <= x) = p."""
    if not 0. < p < 1.:
        raise AnalysisError('分位数的概率必须在 0 和 1 之间。')
    hi = 1.
    while f_sf(hi, d1, d2) > 1. - p:
        hi *= 2.
        if hi > 1e300:
            break
    return _bisect_quantile(lambda x: f_sf(x, d1, d2), 1. - p, 0., hi)


# --------------------------------------------------------------------------- small helpers

def _num(x):
    """JSON-friendly float (None for nan / inf)."""
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def _is_number(x):
    return isinstance(x, (int, float, np.integer, np.floating)) and not isinstance(x, bool) and math.isfinite(float(x))


def _try_float(x):
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _fmt(x):
    return format(float(x), '.10g')


# --------------------------------------------------------------------------- model specification

def _parse_term(text, keys):
    """'A*B' -> {'A':1,'B':1}; 'A^2' -> {'A':2}; 'A*A' -> {'A':2}."""
    powers = {}
    for part in str(text).replace(' ', '').split('*'):
        if not part:
            raise AnalysisError(f'模型项 "{text}" 写法不对。')
        power = 1
        name = part
        if '^' in part:
            name, _, p = part.rpartition('^')
            try:
                power = int(p)
            except ValueError:
                raise AnalysisError(f'模型项 "{text}" 的幂次不是整数。') from None
        if name not in keys:
            raise AnalysisError(f'模型项 "{text}" 里的变量 "{name}" 不在变量列表中。')
        powers[name] = powers.get(name, 0) + power
    return powers


def _canon(powers, order):
    parts = []
    for k in order:
        if k in powers:
            parts.append(k if powers[k] == 1 else f'{k}^{powers[k]}')
    return '*'.join(parts)


def _build_variables(runs, variables, options):
    """Variable specs with coding information, from the design spec or from the run values."""
    declared = {v['key']: v for v in (variables or []) if isinstance(v, dict) and v.get('key')}
    order = [v['key'] for v in (variables or []) if isinstance(v, dict) and v.get('key')]
    seen = {}
    for run in runs:
        for k, val in (run.get('values') or {}).items():
            seen.setdefault(k, []).append(val)
            if k not in declared and k not in order:
                order.append(k)
    mix = (options or {}).get('mixture') or {}
    mix_keys = list(mix.get('keys') or [v['key'] for v in declared.values() if v.get('type') == 'mixture'])
    specs = []
    for k in order:
        decl = declared.get(k, {})
        vals = seen.get(k, [])
        kind = decl.get('type')
        if k in mix_keys:
            kind = 'mixture'
        nums = [_try_float(x) for x in vals]
        if kind is None:
            kind = 'numeric' if vals and all(x is not None for x in nums) else 'categorical'
        spec = {'key': k, 'name': decl.get('name') or k, 'type': kind}
        if decl.get('unit'):
            spec['unit'] = decl['unit']
        if kind == 'categorical':
            levels = decl.get('levels')
            if not levels:
                levels = []
                for x in vals:
                    if x not in levels:
                        levels.append(x)
            spec['levels'] = list(levels)
        else:
            data = [x for x in nums if x is not None]
            low, high = _try_float(decl.get('low')), _try_float(decl.get('high'))
            if kind == 'mixture':
                spec['low'] = low if low is not None else 0.
                spec['high'] = high if high is not None else (max(data) if data else 1.)
            else:
                spec['low'] = low if low is not None else (min(data) if data else 0.)
                spec['high'] = high if high is not None else (max(data) if data else 1.)
        specs.append(spec)
    mixspecs = [s for s in specs if s['type'] == 'mixture']
    if mixspecs:
        total = _try_float(mix.get('total'))
        if total is None:
            first = [sum(float(run['values'][s['key']]) for s in mixspecs) for run in runs
                     if all(_try_float((run.get('values') or {}).get(s['key'])) is not None for s in mixspecs)]
            total = first[0] if first else 1.
        scale = total - sum(s['low'] for s in mixspecs)
        if scale <= 0:
            raise AnalysisError('混料各组分下限之和必须小于合计。')
        for s in mixspecs:
            s['total'] = total
            s['scale'] = scale
    return specs


def _expand_model(model, specs, intercept):
    """Return the list of canonical term strings (each a monomial, joined by '*')."""
    keys = [s['key'] for s in specs]
    kinds = {s['key']: s['type'] for s in specs}
    if isinstance(model, (list, tuple)):
        terms = []
        for t in model:
            c = _canon(_parse_term(t, set(keys)), keys)
            if c not in terms:
                terms.append(c)
        for t in terms:
            for k, p in _parse_term(t, set(keys)).items():
                if kinds[k] == 'categorical' and p > 1:
                    raise AnalysisError(f'类别变量 {k} 不能取平方。')
        return terms
    if model not in ('linear', 'interaction', 'quadratic'):
        raise AnalysisError('model 应为 linear / interaction / quadratic 或模型项列表。')
    mix = [k for k in keys if kinds[k] == 'mixture']
    other = [k for k in keys if kinds[k] != 'mixture']
    terms = list(mix) + list(other)
    if model != 'linear':
        terms += [f'{a}*{b}' for a, b in itertools.combinations(mix, 2)]
        terms += [f'{a}*{b}' for a, b in itertools.combinations(other, 2)]
    if model == 'quadratic':
        terms += [f'{k}^2' for k in other if kinds[k] == 'numeric']
    return terms


def _column_defs(term, specs):
    """Columns of one term. Each column: dict(label, parts=[(key, kind, level_index or None, power)])."""
    by_key = {s['key']: s for s in specs}
    keys = [s['key'] for s in specs]
    powers = _parse_term(term, set(keys))
    blocks = []
    for k in keys:
        if k not in powers:
            continue
        s = by_key[k]
        if s['type'] == 'categorical':
            blocks.append([(f'{k}[{lvl}]', (k, 'categorical', j, 1)) for j, lvl in enumerate(s['levels'][:-1])])
        else:
            lab = k if powers[k] == 1 else f'{k}^{powers[k]}'
            blocks.append([(lab, (k, s['type'], None, powers[k]))])
    cols = []
    for combo in itertools.product(*blocks):
        cols.append({'label': '*'.join(c[0] for c in combo), 'parts': [c[1] for c in combo]})
    return cols


def _encode(values, specs):
    """Per variable: coded value (numeric), pseudo-component (mixture), level index (categorical)."""
    enc = {}
    for s in specs:
        k = s['key']
        raw = values.get(k)
        if s['type'] == 'categorical':
            if raw not in s['levels']:
                raise AnalysisError(f'变量 {k} 的取值 "{raw}" 不在水平 {s["levels"]} 中。')
            enc[k] = s['levels'].index(raw)
            continue
        v = _try_float(raw)
        if v is None:
            raise AnalysisError(f'变量 {k} 的取值 "{raw}" 不是有限数值。')
        if s['type'] == 'mixture':
            enc[k] = (v - s['low']) / s['scale']
        else:
            half = (s['high'] - s['low']) / 2.
            enc[k] = 0. if half == 0 else (v - (s['low'] + s['high']) / 2.) / half
    return enc


def _row(enc, specs, columns):
    by_key = {s['key']: s for s in specs}
    row = []
    for col in columns:
        if col is None:
            row.append(1.)
            continue
        val = 1.
        for k, kind, j, p in col['parts']:
            if kind == 'categorical':
                n = len(by_key[k]['levels'])
                val *= 1. if enc[k] == j else (-1. if enc[k] == n - 1 else 0.)
            else:
                val *= enc[k] ** p
        row.append(val)
    return row


def _independent_columns(X, tol=1e-9):
    """Greedy column selection; returns indices of columns that add rank."""
    keep = []
    basis = np.zeros((X.shape[0], 0))
    for j in range(X.shape[1]):
        col = X[:, j]
        norm = np.linalg.norm(col)
        if norm == 0:
            continue
        if basis.shape[1]:
            coef, *_ = np.linalg.lstsq(basis, col, rcond=None)
            resid = np.linalg.norm(col - basis @ coef)
        else:
            resid = norm
        if resid > tol * norm:
            keep.append(j)
            basis = np.column_stack([basis, col])
    return keep


def _sse(X, y):
    if X.shape[1] == 0:
        return float(np.sum(y ** 2)), 0
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    r = y - X @ beta
    return float(r @ r), int(np.linalg.matrix_rank(X))


# polynomial algebra for actual-unit coefficients ------------------------------------------

def _poly_mul(a, b):
    out = {}
    for ma, ca in a.items():
        for mb, cb in b.items():
            merged = dict(ma)
            for k, p in mb:
                merged[k] = merged.get(k, 0) + p
            m = tuple(sorted(merged.items()))
            out[m] = out.get(m, 0.) + ca * cb
    return out


def _actual_polynomial(columns, beta, specs):
    """Expand the fitted model into a polynomial in the actual variable values.

    Coded numeric c = (x - mid)/half ; pseudo-component z = (x - low)/scale ;
    categorical contrast columns stay as symbols.  Returns {label: coefficient}.
    """
    by_key = {s['key']: s for s in specs}
    total = {}
    for col, b in zip(columns, beta):
        poly = {(): 1.}
        if col is not None:
            for k, kind, j, p in col['parts']:
                if kind == 'categorical':
                    factor = {((f'{k}[{by_key[k]["levels"][j]}]', 1),): 1.}
                else:
                    s = by_key[k]
                    if kind == 'mixture':
                        a0, b0 = -s['low'] / s['scale'], 1. / s['scale']
                    else:
                        half = (s['high'] - s['low']) / 2.
                        a0 = 0. if half == 0 else -((s['low'] + s['high']) / 2.) / half
                        b0 = 0. if half == 0 else 1. / half
                    factor = {((k, 1),): b0}
                    if a0 != 0:
                        factor[()] = a0
                for _ in range(p if kind != 'categorical' else 1):
                    poly = _poly_mul(poly, factor)
        for m, c in poly.items():
            total[m] = total.get(m, 0.) + b * c
    out = []
    for m, c in sorted(total.items(), key=lambda kv: (len(kv[0]), [x[0] for x in kv[0]])):
        label = '*'.join(k if p == 1 else f'{k}^{p}' for k, p in m) or '截距'
        out.append({'term': label, 'estimate': _num(c)})
    return out


# --------------------------------------------------------------------------- analyze

def analyze(runs, responses, model, variables=None, options=None):
    """Fit a regression model and return coefficients, ANOVA, R2, effects, residuals, warnings.

    runs: [{"run": id, "values": {key: value}}]; responses: {run_id: value or None}
    model: "linear" | "interaction" | "quadratic" | list of terms such as ["A", "B", "A*B", "A^2"]
    variables (optional): [{key, name, type, low, high, levels}] giving the coding range of each
        variable; without it, numeric variables are coded from the min/max of the run values.
    options (optional): {"mixture": {"keys": [...], "total": 100}, "response_id": "..."}
    ANOVA: ``ss`` is the adjusted (Type III) sum of squares, ``seq_ss`` the sequential one.
    """
    options = options or {}
    runs = list(runs or [])
    if not runs:
        raise AnalysisError('没有试验数据。')
    warnings = []
    specs = _build_variables(runs, variables, options)
    mix_present = any(s['type'] == 'mixture' for s in specs)
    intercept = options.get('intercept', not mix_present)
    for s in specs:
        if s['type'] == 'numeric' and s['high'] == s['low']:
            warnings.append(f'变量 {s["key"]} 在所有试验中取值相同，已按 0 编码，无法估计它的效应。')
    terms = _expand_model(model, specs, intercept)
    const_keys = {s['key'] for s in specs if s['type'] == 'numeric' and s['high'] == s['low']}
    terms = [t for t in terms if not (set(_parse_term(t, {s['key'] for s in specs})) & const_keys)]
    columns = ([None] if intercept else [])
    groups = []   # (label, [column indices], mixture_linear)
    if intercept:
        groups.append(('截距', [0], False))
    mix_cols = []
    for t in terms:
        defs = _column_defs(t, specs)
        idx = list(range(len(columns), len(columns) + len(defs)))
        columns.extend(defs)
        is_mix_lin = mix_present and all(p[1] == 'mixture' for d in defs for p in d['parts']) and len(_parse_term(t, {s['key'] for s in specs})) == 1 and \
            all(v == 1 for v in _parse_term(t, {s['key'] for s in specs}).values()) and \
            next(iter(_parse_term(t, {s['key'] for s in specs}))) in {s['key'] for s in specs if s['type'] == 'mixture'}
        if is_mix_lin:
            mix_cols.extend(idx)
        else:
            groups.append((t, idx, False))
    if mix_cols:
        pos = 1 if intercept else 0
        groups.insert(pos, ('混料线性', mix_cols, True))

    # rows with responses
    used, rows_y, skipped = [], [], []
    for run in runs:
        rid = run.get('run')
        y = responses.get(rid, responses.get(str(rid))) if isinstance(responses, dict) else None
        yv = _try_float(y) if y is not None else None
        if yv is None:
            skipped.append(rid)
            continue
        used.append(run)
        rows_y.append(yv)
    for rid in skipped:
        warnings.append(f'试验 {rid} 没有响应值，已跳过。')
    n = len(used)
    if n == 0:
        raise AnalysisError('所有试验都没有响应值。')
    X_full = np.array([_row(_encode(r['values'], specs), specs, columns) for r in used], dtype=float)
    y = np.array(rows_y, dtype=float)
    p_all = X_full.shape[1]
    keep = _independent_columns(X_full)
    dropped = [j for j in range(p_all) if j not in keep]
    if dropped:
        names = [('截距' if columns[j] is None else columns[j]['label']) for j in dropped]
        warnings.append('模型项之间完全混杂（或设计矩阵奇异），已去掉：' + '、'.join(names) + '。其余项的估计值是在去掉它们之后得到的。')
    X = X_full[:, keep]
    p = X.shape[1]
    if n < p:
        warnings.append(f'试验只有 {n} 次，少于可估计的 {p} 个参数，结果不可靠。')
    df_res = n - p
    if df_res == 0:
        warnings.append('模型是饱和的，没有剩余自由度，估计不出误差，标准误、t、p 和区间都无法给出；请增加试验或重复。')
    elif n < 2 * p:
        warnings.append(f'试验次数（{n}）不足参数个数（{p}）的两倍，估计的精度有限。')
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    fitted = X @ beta
    resid = y - fitted
    sse = float(resid @ resid)
    sst = float(np.sum((y - y.mean()) ** 2))
    mse = sse / df_res if df_res > 0 else None
    hat = np.einsum('ij,jk,ik->i', X, xtx_inv, X)

    # coefficients ----------------------------------------------------------------------
    col_index = {j: i for i, j in enumerate(keep)}
    vif = _vifs(X, [columns[j] is None for j in keep])
    coefs = []
    for j in range(p_all):
        label = '截距' if columns[j] is None else columns[j]['label']
        if j in dropped:
            coefs.append({'term': label, 'estimate': None, 'se': None, 't': None, 'p': None, 'vif': None, 'aliased': True})
            continue
        i = col_index[j]
        se = math.sqrt(mse * xtx_inv[i, i]) if mse is not None else None
        t = beta[i] / se if se else None
        pv = t_two_sided_p(t, df_res) if t is not None else None
        coefs.append({'term': label, 'estimate': _num(beta[i]), 'se': _num(se), 't': _num(t), 'p': _num(pv), 'vif': _num(vif[i])})
    cols_kept = [columns[j] for j in keep]
    actual = _actual_polynomial(cols_kept, beta, specs)

    # ANOVA ------------------------------------------------------------------------------
    anova = []
    ms_res = mse
    model_df = int(np.linalg.matrix_rank(X)) - 1
    model_ss = sst - sse
    def row(source, df, ss, seq_ss=None, tested=True):
        ms = ss / df if df > 0 else None
        f = ms / ms_res if (tested and ms is not None and ms_res) else None
        pv = f_sf(f, df, df_res) if f is not None else None
        out = {'source': source, 'df': int(df), 'ss': _num(ss), 'ms': _num(ms), 'f': _num(f), 'p': _num(pv)}
        if seq_ss is not None:
            out['seq_ss'] = _num(seq_ss)
        return out
    anova.append(row('模型', model_df, model_ss, model_ss))
    prev_idx = [col_index[0]] if intercept and 0 in col_index else []
    prev_sse = sst if intercept or mix_present else float(y @ y)
    prev_rank = 1
    cum = [] if not intercept else [col_index[0]]
    for label, idx, mix_lin in groups:
        if label == '截距':
            continue
        gi = [col_index[j] for j in idx if j in col_index]
        if not gi:
            warnings.append(f'模型项 {label} 与其他项完全混杂，没有出现在方差分析中。')
            continue
        cum_new = cum + gi
        sse_now, rank_now = _sse(X[:, cum_new], y)
        base_sse = prev_sse if (intercept or cum) else sst
        seq_ss = base_sse - sse_now
        df = rank_now - prev_rank
        if mix_lin or not (len(cum_new) > len(gi)):
            adj = seq_ss
        else:
            rest = [c for c in range(p) if c not in gi]
            sse_red, _ = _sse(X[:, rest], y)
            adj = sse_red - sse
        if mix_lin:
            df_adj = df
        else:
            df_adj = len(gi)
        r = row(label, df_adj, adj, seq_ss)
        anova.append(r)
        cum, prev_sse, prev_rank = cum_new, sse_now, rank_now
    anova.append(row('残差', df_res, sse, tested=False))
    # lack of fit and pure error
    keys = [s['key'] for s in specs]
    cells = {}
    for i, r in enumerate(used):
        cells.setdefault(tuple(_fmt(r['values'][k]) if _try_float(r['values'][k]) is not None else str(r['values'][k]) for k in keys), []).append(i)
    n_cells = len(cells)
    pe_df = n - n_cells
    if pe_df > 0:
        pe_ss = sum(float(np.sum((y[ix] - y[ix].mean()) ** 2)) for ix in cells.values())
        lof_df = n_cells - p
        if lof_df > 0:
            lof_ss = sse - pe_ss
            lof_ms, pe_ms = lof_ss / lof_df, pe_ss / pe_df
            f = lof_ms / pe_ms if pe_ms > 0 else None
            anova.append({'source': '失拟', 'df': lof_df, 'ss': _num(lof_ss), 'ms': _num(lof_ms), 'f': _num(f),
                          'p': _num(f_sf(f, lof_df, pe_df)) if f is not None else None})
        anova.append({'source': '纯误差', 'df': pe_df, 'ss': _num(pe_ss), 'ms': _num(pe_ss / pe_df), 'f': None, 'p': None})
    anova.append(row('合计', n - 1, sst, tested=False))

    # R2 ---------------------------------------------------------------------------------
    r2 = 1. - sse / sst if sst > 0 else None
    r2_adj = 1. - (sse / df_res) / (sst / (n - 1)) if (sst > 0 and df_res > 0 and n > 1) else None
    one_minus_h = 1. - hat
    r2_pred = None
    if sst > 0 and np.all(one_minus_h > 1e-9):
        press = float(np.sum((resid / one_minus_h) ** 2))
        r2_pred = 1. - press / sst
    elif sst > 0:
        warnings.append('有试验的杠杆值为 1（模型对该点是精确插值），无法计算预测 R²。')

    # residual table ---------------------------------------------------------------------
    p_rank = int(np.linalg.matrix_rank(X))
    residuals = []
    for i, r in enumerate(used):
        h = float(hat[i])
        if mse and h < 1 - 1e-9:
            stud = resid[i] / math.sqrt(mse * (1. - h))
            cook = (resid[i] ** 2 / (p_rank * mse)) * h / (1. - h) ** 2
        else:
            stud = cook = None
        residuals.append({'run': r.get('run'), 'observed': _num(y[i]), 'fitted': _num(fitted[i]), 'residual': _num(resid[i]),
                          'studentized': _num(stud), 'leverage': _num(h), 'cook': _num(cook)})
    for i, rec in enumerate(residuals):
        if rec['leverage'] is not None and rec['leverage'] > max(0.99, 2. * p_rank / n) and df_res > 0 and rec['leverage'] > 2. * p_rank / n:
            warnings.append(f'试验 {rec["run"]} 的杠杆值 {rec["leverage"]:.2f} 很高，它对拟合影响很大。')
        if rec['cook'] is not None and rec['cook'] > 1.:
            warnings.append(f'试验 {rec["run"]} 的 Cook 距离 {rec["cook"]:.2f} 大于 1，是强影响点。')

    # extrapolation / mixture sanity -----------------------------------------------------
    for s in specs:
        if s['type'] == 'numeric':
            vs = [_try_float(r['values'].get(s['key'])) for r in runs]
            if any(v is not None and (v < s['low'] - 1e-9 * max(1., abs(s['low'])) or v > s['high'] + 1e-9 * max(1., abs(s['high']))) for v in vs):
                warnings.append(f'变量 {s["key"]} 有试验取值超出设计范围 [{_fmt(s["low"])}, {_fmt(s["high"])}]，编码值会超出 −1…+1（中心复合设计的星点超出范围属正常）。')
    mixspecs = [s for s in specs if s['type'] == 'mixture']
    if mixspecs:
        for r in used:
            tot = sum(float(r['values'][s['key']]) for s in mixspecs)
            if abs(tot - mixspecs[0]['total']) > 1e-6 * max(1., abs(mixspecs[0]['total'])):
                warnings.append(f'试验 {r.get("run")} 的混料合计为 {_fmt(tot)}，不等于 {_fmt(mixspecs[0]["total"])}，混料模型要求合计固定。')
                break

    # effects (Pareto) -------------------------------------------------------------------
    effects = []
    if not mix_present:
        by_key = {s['key']: s for s in specs}
        for j in keep:
            col = columns[j]
            if col is None:
                continue
            ks = [pp[0] for pp in col['parts']]
            powers = [pp[3] for pp in col['parts']]
            if any(pp[1] == 'categorical' for pp in col['parts']) or sum(powers) > 2 or any(pw > 1 for pw in powers):
                continue
            i = col_index[j]
            c = coefs[j]
            effects.append({'term': col['label'], 'effect': _num(2. * beta[i]), 'abs_effect': _num(abs(2. * beta[i])),
                            't': c['t'], 'p': c['p'], 'order': len(ks)})
        for s in specs:
            if s['type'] == 'categorical':
                cols = [j for j in keep if columns[j] is not None and len(columns[j]['parts']) == 1 and columns[j]['parts'][0][0] == s['key']]
                if not cols:
                    continue
                effs = [beta[col_index[j]] for j in cols]
                effs.append(-sum(effs))
                effects.append({'term': s['key'], 'effect': _num(max(effs) - min(effs)), 'abs_effect': _num(max(effs) - min(effs)),
                                't': None, 'p': None, 'order': 1, 'note': '类别变量：各水平效应的最大差'})
        effects.sort(key=lambda e: -(e['abs_effect'] or 0.))
        for rank, e in enumerate(effects, 1):
            e['rank'] = rank

    result = {
        'response_id': options.get('response_id'),
        'n': n, 'n_params': p, 'df_residual': df_res,
        'coefficients': coefs, 'coefficients_actual': actual, 'coefficient_units_note': '估计值按编码变量给出（数值变量 −1…+1，混料为伪组分 0…1，类别为和为零编码）；coefficients_actual 为换回实际单位的多项式。',
        'anova': anova, 'anova_note': 'ss 为调整平方和（Type III，去掉该项后残差平方和的增加量）；seq_ss 为依模型项顺序的序贯平方和。',
        'r2': _num(r2), 'r2_adj': _num(r2_adj), 'r2_pred': _num(r2_pred),
        'mse': _num(mse), 'rmse': _num(math.sqrt(mse)) if mse is not None else None,
        'effects': effects, 'residuals': residuals, 'warnings': warnings,
        'model': {
            'terms': terms, 'intercept': bool(intercept),
            'columns': [None if c is None else {'label': c['label'], 'parts': [list(pp) for pp in c['parts']]} for c in cols_kept],
            'aliased': [('截距' if columns[j] is None else columns[j]['label']) for j in dropped],
            'variables': specs,
            'beta': [float(b) for b in beta],
            'xtx_inv': xtx_inv.tolist(),
            'y_range': [float(y.min()), float(y.max())],
        },
    }
    return result


def _vifs(X, is_intercept):
    p = X.shape[1]
    out = [None] * p
    if p == 0:
        return out
    has_int = any(is_intercept)
    cols = [j for j in range(p) if not is_intercept[j]]
    if len(cols) < 2:
        return out
    if has_int:
        Z = X[:, cols] - X[:, cols].mean(axis=0)
    else:
        Z = X[:, cols]
    G = Z.T @ Z
    try:
        inv = np.linalg.inv(G)
    except np.linalg.LinAlgError:
        return out
    for k, j in enumerate(cols):
        out[j] = float(G[k, k] * inv[k, k])
    return out


# --------------------------------------------------------------------------- predict

def _spec_columns(mr):
    m = mr['model']
    cols = [None if c is None else {'label': c['label'], 'parts': [tuple(pp) for pp in c['parts']]} for c in m['columns']]
    return m['variables'], cols


def _predict_one(mr, values, tcrit):
    specs, cols = _spec_columns(mr)
    fixed = dict(values)
    for s in specs:
        if s['key'] not in fixed:
            fixed[s['key']] = s['levels'][0] if s['type'] == 'categorical' else (
                (s['low'] + s['high']) / 2. if s['type'] == 'numeric' else s['low'] + s['scale'] / len(
                    [q for q in specs if q['type'] == 'mixture']))
    enc = _encode(fixed, specs)
    x = np.array(_row(enc, specs, cols), dtype=float)
    beta = np.array(mr['model']['beta'])
    value = float(x @ beta)
    out = {'value': value, 'extrapolated': False}
    for s in specs:
        if s['type'] == 'numeric' and abs(enc[s['key']]) > 1 + 1e-9:
            out['extrapolated'] = True
        if s['type'] == 'mixture' and enc[s['key']] < -1e-9:
            out['extrapolated'] = True
    mse, df = mr.get('mse'), mr.get('df_residual')
    if mse is not None and df and df > 0:
        h = float(x @ np.array(mr['model']['xtx_inv']) @ x)
        t = tcrit if tcrit else t_ppf(0.975, df)
        se_fit = math.sqrt(max(mse * h, 0.))
        se_pred = math.sqrt(mse * (1. + h))
        out.update(se_fit=se_fit, ci_low=value - t * se_fit, ci_high=value + t * se_fit,
                   pi_low=value - t * se_pred, pi_high=value + t * se_pred)
    else:
        out.update(se_fit=None, ci_low=None, ci_high=None, pi_low=None, pi_high=None)
    out['low'], out['high'] = out['pi_low'], out['pi_high']
    out['interval_note'] = 'ci_* 为均值的 95% 置信区间；pi_* 为单个新试验的 95% 预测区间；low/high 等于 pi_*。'
    return out


def predict(model_result, points, level=0.95):
    """Predict at points (dicts of actual values). low/high = 95% prediction interval; ci_*/pi_* give both."""
    df = model_result.get('df_residual')
    tcrit = t_ppf(0.5 + level / 2., df) if (df and df > 0) else None
    return [_predict_one(model_result, pt, tcrit) for pt in points]


def stationary_point(model_result):
    """Stationary point of a quadratic model in the numeric variables (others held at their centres).

    Returns {coded, values, value, eigenvalues, nature}; nature is maximum / minimum / saddle.
    """
    specs, cols = _spec_columns(model_result)
    num = [s for s in specs if s['type'] == 'numeric']
    k = len(num)
    beta = model_result['model']['beta']
    b = np.zeros(k)
    B = np.zeros((k, k))
    pos = {s['key']: i for i, s in enumerate(num)}
    for col, bv in zip(cols, beta):
        if col is None or any(pp[1] != 'numeric' for pp in col['parts']):
            continue
        parts = col['parts']
        if len(parts) == 1 and parts[0][3] == 1:
            b[pos[parts[0][0]]] = bv
        elif len(parts) == 1 and parts[0][3] == 2:
            B[pos[parts[0][0]], pos[parts[0][0]]] = bv
        elif len(parts) == 2 and all(pp[3] == 1 for pp in parts):
            i, j = pos[parts[0][0]], pos[parts[1][0]]
            B[i, j] = B[j, i] = bv / 2.
    x_s = np.linalg.solve(2. * B, -b)
    # value at the stationary point
    values = {s['key']: float(s['low'] + (x + 1.) / 2. * (s['high'] - s['low'])) for s, x in zip(num, x_s)}
    value = _predict_one(model_result, values, None)['value']
    eig = np.linalg.eigvalsh(B)
    nature = 'maximum' if np.all(eig < 0) else ('minimum' if np.all(eig > 0) else 'saddle')
    return {'coded': {s['key']: float(x) for s, x in zip(num, x_s)}, 'values': values, 'value': float(value),
            'eigenvalues': [float(e) for e in eig], 'nature': nature}


# --------------------------------------------------------------------------- contour

def contour(model_result, x_key, y_key, fixed=None, grid=25):
    """Grid of fitted values over two numeric / mixture variables in actual units (null outside the region)."""
    specs, _ = _spec_columns(model_result)
    by_key = {s['key']: s for s in specs}
    for k in (x_key, y_key):
        if k not in by_key or by_key[k]['type'] == 'categorical':
            raise AnalysisError(f'等高线变量 {k} 必须是数值或混料变量。')
    grid = int(grid)
    if grid < 2:
        raise AnalysisError('网格点数至少为 2。')
    fixed = dict(fixed or {})
    mix = [s for s in specs if s['type'] == 'mixture']

    def axis(s):
        if s['type'] == 'mixture':
            lo, hi = s['low'], s['low'] + s['scale']
        else:
            lo, hi = s['low'], s['high']
        return [lo + (hi - lo) * i / (grid - 1) for i in range(grid)]
    xs, ys = axis(by_key[x_key]), axis(by_key[y_key])
    base = {}
    for s in specs:
        if s['key'] in (x_key, y_key):
            continue
        if s['key'] in fixed:
            base[s['key']] = fixed[s['key']]
        elif s['type'] == 'categorical':
            base[s['key']] = s['levels'][0]
        elif s['type'] == 'numeric':
            base[s['key']] = (s['low'] + s['high']) / 2.
    free_mix = [s for s in mix if s['key'] not in (x_key, y_key) and s['key'] not in fixed]
    fixed_mix = sum(float(fixed[s['key']]) for s in mix if s['key'] in fixed and s['key'] not in (x_key, y_key))
    z = []
    for yv in ys:
        line = []
        for xv in xs:
            point = dict(base)
            point[x_key], point[y_key] = xv, yv
            if mix:
                total = mix[0]['total']
                used = sum(point[s['key']] for s in mix if s['key'] in (x_key, y_key)) + fixed_mix
                if free_mix:
                    rest = total - used
                    share = rest / len(free_mix)
                    if any(share < s['low'] - 1e-9 for s in free_mix):
                        line.append(None)
                        continue
                    for s in free_mix:
                        point[s['key']] = share
            val = _predict_one(model_result, point, None)['value']
            line.append(val)
        z.append(line)
    return {'x_key': x_key, 'y_key': y_key, 'x': xs, 'y': ys, 'z': z, 'fixed': {k: v for k, v in base.items()}}


# --------------------------------------------------------------------------- desirability optimisation

def desirability(y, goal):
    """Derringer–Suich individual desirability for one response value."""
    g, low, high, w = goal['goal'], goal['low'], goal['high'], goal.get('weight', 1.) or 1.
    if g == 'max':
        top = goal.get('target', high)
        if y <= low:
            return 0.
        if y >= top:
            return 1.
        return ((y - low) / (top - low)) ** w
    if g == 'min':
        best = goal.get('target', low)
        if y >= high:
            return 0.
        if y <= best:
            return 1.
        return ((high - y) / (high - best)) ** w
    if g == 'target':
        t = goal['target']
        if y < low or y > high:
            return 0.
        if y <= t:
            return 1. if t == low else ((y - low) / (t - low)) ** w
        return 1. if t == high else ((high - y) / (high - t)) ** w
    raise AnalysisError(f'未知目标类型 {g}。')


def _overall(ds, imps):
    if any(d <= 0 for d in ds):
        return 0.
    s = sum(imps)
    return math.exp(sum(i * math.log(d) for d, i in zip(ds, imps)) / s)


def _coords_to_values(specs, cat, num, mixz):
    values = {}
    for s in specs:
        if s['type'] == 'categorical':
            values[s['key']] = s['levels'][cat[s['key']]]
        elif s['type'] == 'numeric':
            values[s['key']] = s['low'] + (num[s['key']] + 1.) / 2. * (s['high'] - s['low'])
        else:
            values[s['key']] = s['low'] + s['scale'] * mixz[s['key']]
    return values


def _nelder_mead(f, x0, steps, lo, hi, iters=200):
    n = len(x0)
    def clip(v):
        return [min(max(a, l), h) for a, l, h in zip(v, lo, hi)]
    simplex = [clip(x0)]
    for i in range(n):
        v = list(x0)
        v[i] += steps[i]
        if v[i] > hi[i]:
            v[i] = x0[i] - steps[i]
        simplex.append(clip(v))
    vals = [f(v) for v in simplex]
    for _ in range(iters):
        order = sorted(range(n + 1), key=lambda i: vals[i])
        simplex = [simplex[i] for i in order]
        vals = [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) < 1e-12 and max(abs(a - b) for a, b in zip(simplex[0], simplex[-1])) < 1e-9:
            break
        cen = [sum(v[i] for v in simplex[:-1]) / n for i in range(n)]
        def point(coef):
            return clip([c + coef * (w - c) for c, w in zip(cen, simplex[-1])])
        xr = point(-1.)
        fr = f(xr)
        if fr < vals[0]:
            xe = point(-2.)
            fe = f(xe)
            simplex[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            simplex[-1], vals[-1] = xr, fr
        else:
            xc = point(0.5 if fr >= vals[-1] else -0.5)
            fc = f(xc)
            if fc < min(fr, vals[-1]):
                simplex[-1], vals[-1] = xc, fc
            else:
                for i in range(1, n + 1):
                    simplex[i] = clip([simplex[0][j] + 0.5 * (simplex[i][j] - simplex[0][j]) for j in range(n)])
                    vals[i] = f(simplex[i])
    i = min(range(n + 1), key=lambda i: vals[i])
    return simplex[i], vals[i]


def optimize(model_results, goals, options=None):
    """Maximise the overall desirability over the design region.

    model_results: {response_id: analyze() result} or a list of results carrying "response_id".
    goals: [{response_id, goal: max|min|target, target?, low?, high?, weight?, importance?}]
    Search: categorical levels enumerated; numeric coded cube and mixture simplex by coarse grid then
    Nelder–Mead refinement. Returns {"best": {...}, "candidates": [...]}.
    """
    options = options or {}
    if isinstance(model_results, dict) and 'coefficients' not in model_results:
        models = dict(model_results)
    else:
        lst = [model_results] if isinstance(model_results, dict) else list(model_results)
        models = {m.get('response_id'): m for m in lst}
    gl = []
    for g in goals:
        rid = g['response_id']
        if rid not in models:
            raise AnalysisError(f'目标里的响应 {rid} 没有对应的模型。')
        lo_y, hi_y = models[rid]['model']['y_range']
        goal = dict(g)
        if goal['goal'] == 'max':
            goal.setdefault('low', lo_y)
            goal.setdefault('high', hi_y)
        elif goal['goal'] == 'min':
            goal.setdefault('low', lo_y)
            goal.setdefault('high', hi_y)
        else:
            goal.setdefault('low', lo_y)
            goal.setdefault('high', hi_y)
        if goal['goal'] == 'target' and goal.get('target') is None:
            raise AnalysisError(f'响应 {rid} 的目标值型目标需要 target。')
        if goal['high'] <= goal['low']:
            raise AnalysisError(f'响应 {rid} 的 low 必须小于 high。')
        goal['weight'] = float(goal.get('weight') or 1.)
        goal['importance'] = float(goal.get('importance') or 1.)
        gl.append(goal)
    if not gl:
        raise AnalysisError('至少需要一个目标。')
    specs = deepcopy(models[gl[0]['response_id']]['model']['variables'])
    cats = [s for s in specs if s['type'] == 'categorical']
    nums = [s for s in specs if s['type'] == 'numeric']
    mixes = [s for s in specs if s['type'] == 'mixture']
    top_n = int(options.get('candidates', 5))

    def predicted_d(values):
        ds, preds = [], {}
        for g in gl:
            pr = _predict_one(models[g['response_id']], values, None)
            ds.append(desirability(pr['value'], g))
        return ds

    def objective_factory(cat):
        nn, qq = len(nums), len(mixes)
        def to_values(vec):
            num = {s['key']: vec[i] for i, s in enumerate(nums)}
            w = [max(v, 0.) for v in vec[nn:nn + qq]]
            tot = sum(w)
            mixz = {s['key']: (w[i] / tot if tot > 0 else 1. / qq) for i, s in enumerate(mixes)}
            return _coords_to_values(specs, cat, num, mixz)
        def f(vec):
            ds = predicted_d(to_values(vec))
            return -_overall(ds, [g['importance'] for g in gl])
        return to_values, f

    # coarse grid
    nn, qq = len(nums), len(mixes)
    dims = nn + qq
    budget = int(options.get('grid_budget', 1500))
    per = max(3, int(round(budget ** (1. / max(nn, 1))))) if nn else 1
    if nn and per ** nn > budget:
        per = max(3, int(budget ** (1. / nn)))
    axis = [(-1. + 2. * i / (per - 1)) for i in range(per)] if per > 1 else [0.]
    degree = 8 if qq <= 3 else 5
    simplex_pts = []
    if qq:
        def lattice(q, d):
            if q == 1:
                yield [d]
            else:
                for i in range(d + 1):
                    for rest in lattice(q - 1, d - i):
                        yield [i] + rest
        simplex_pts = [[v / degree for v in row] for row in lattice(qq, degree)]
    cat_combos = list(itertools.product(*[range(len(s['levels'])) for s in cats])) or [()]
    if len(cat_combos) > 60:
        raise AnalysisError('类别水平组合太多，无法逐一搜索，请减少类别变量。')
    found = []
    for combo in cat_combos:
        cat = {s['key']: c for s, c in zip(cats, combo)}
        to_values, f = objective_factory(cat)
        starts = []
        num_pts = list(itertools.product(axis, repeat=nn)) if nn else [()]
        for npt in num_pts:
            for sp in (simplex_pts or [[]]):
                vec = list(npt) + list(sp)
                starts.append((f(vec), vec))
        starts.sort(key=lambda t: t[0])
        lo = [-1.] * nn + [0.] * qq
        hi = [1.] * nn + [1.] * qq
        steps = [0.4] * nn + [0.2] * qq
        for fv, vec in starts[:max(6, top_n * 3)]:
            if dims:
                vec, fv = _nelder_mead(f, vec, steps, lo, hi)
            found.append((fv, vec, cat, to_values))
    found.sort(key=lambda t: t[0])
    chosen = []
    for fv, vec, cat, to_values in found:
        sig = (tuple(sorted(cat.items())), vec)
        distinct = True
        for c in chosen:
            if c['_cat'] == cat and math.sqrt(sum((a - b) ** 2 for a, b in zip(c['_vec'], vec))) < 0.15:
                distinct = False
                break
        if not distinct:
            continue
        values = to_values(vec)
        # normalise mixture to exact sums
        preds, ds = {}, []
        for g in gl:
            pr = _predict_one(models[g['response_id']], values, None)
            preds[g['response_id']] = {k: pr[k] for k in ('value', 'low', 'high', 'ci_low', 'ci_high', 'pi_low', 'pi_high')}
            ds.append(desirability(pr['value'], g))
        chosen.append({'values': values, 'desirability': _overall(ds, [g['importance'] for g in gl]),
                       'individual': {g['response_id']: d for g, d in zip(gl, ds)}, 'predicted': preds,
                       '_cat': cat, '_vec': vec})
        if len(chosen) >= top_n:
            break
    for c in chosen:
        c.pop('_cat')
        c.pop('_vec')
    note = ('desirability 为 Derringer–Suich 综合期望值（0–1）；predicted 的 low/high 是 95% 预测区间。'
            ' 如果综合期望值为 0，说明所选目标在设计范围内无法同时达到。')
    return {'best': chosen[0] if chosen else None, 'candidates': chosen, 'goals': gl, 'note': note}
