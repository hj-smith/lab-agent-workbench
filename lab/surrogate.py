"""代理模型和下一轮实验提议：用已做过的树脂批（研发台的树脂批总表）给每个结果建一个高斯过程模型，
先用留一交叉验证说清"能不能信"，再在做过的范围里找最可能达到目标的几组条件。

高斯过程：常数均值 + 各因子各自长度尺度的 RBF 核 + 噪声，超参数按边际似然最大（和 BayBE 默认的同一套数学；
运行时没有 BayBE/torch，自己写，几十行，能查）。数值因子和结果都先标准化；类别因子 one-hot。
提议：在已做过的数值范围里（可放宽）随机撒点，类别取已经出现过的组合；每个目标用"偏保守的预测"（均值 ∓ κσ）
换成 0–1 的满意度，几个目标取几何平均；挑 5 个彼此不太像的，每个写出各结果的预测 ± σ 和最像哪一批。
"""
import math
import re

import numpy as np
from scipy.optimize import minimize

from .present import clean
from .sci import desk

NUMERIC = ['单体比', '催化剂用量', '反应固含', '反应温度', '反应时间', 'AddH/初始MonA']
CATEGORY = ['主溶剂', '加料方式', '终止/中和方式', '沉降溶剂', '是否分水']
RESPONSES = ['产率', 'Mn', 'Mw', 'Đ', 'n 193 nm', 'k 193 nm', '膜厚', 'SolvA膜厚损失率', '老化 A500']
MIN_POINTS = 8            # 少于这么多点不建模
TRUST_R2 = 0.3            # 留一 R² 低于这个，不拿来提议


def _number(cell):
    if not isinstance(cell, dict):
        return None
    text = str(cell.get('v') or '').replace(',', '').strip()
    if text.startswith('≥') or text.startswith('>'):
        return None                                 # 饱和读数只是下限，不当真值建模
    try:
        return float(text)
    except ValueError:
        return None


def _text(cell):
    return str(cell.get('v')) if isinstance(cell, dict) and cell.get('v') not in (None, '') else '（没写）'


def _lots():
    table = desk.lot_table()
    at = {name: index for index, name in enumerate(table['columns'])}
    lots = []
    for row in table['rows']:
        lots.append({'name': row[at['实验/样品']]['v'], 'x': {key: _number(row[at[key]]) for key in NUMERIC},
                     'c': {key: _text(row[at[key]]) for key in CATEGORY}, 'y': {key: _number(row[at[key]]) for key in RESPONSES}})
    return lots


class Space:
    """因子编码：数值标准化（缺的填均值），类别 one-hot。"""

    def __init__(self, lots):
        self.mean = {key: np.nanmean([lot['x'][key] if lot['x'][key] is not None else np.nan for lot in lots]) for key in NUMERIC}
        self.std = {key: (np.nanstd([lot['x'][key] if lot['x'][key] is not None else np.nan for lot in lots]) or 1.0) for key in NUMERIC}
        self.levels = {key: sorted({lot['c'][key] for lot in lots} - {'（没写）'}) for key in CATEGORY}     # 没写的不当一个水平（是记录缺，不是条件）
        self.levels = {key: levels for key, levels in self.levels.items() if len(levels) > 1}

    def encode(self, x, c):
        out = [((x[key] if x[key] is not None else self.mean[key]) - self.mean[key]) / self.std[key] for key in NUMERIC]
        for key, levels in self.levels.items():
            out += [1.0 if c.get(key) == level else 0.0 for level in levels]
        return np.asarray(out, dtype=float)

    def names(self):
        return NUMERIC + [f'{key}={level}' for key, levels in self.levels.items() for level in levels]


def _kernel(a, b, scale, lengths):
    d = (a[:, None, :] - b[None, :, :]) / lengths
    return scale * np.exp(-0.5 * np.sum(d * d, axis=2))


class GP:
    def __init__(self, x, y):
        self.x = x
        self.y_mean, self.y_std = float(np.mean(y)), float(np.std(y)) or 1.0
        self.y = (y - self.y_mean) / self.y_std
        dims = x.shape[1]

        def nll(params):
            scale, lengths, noise = math.exp(params[0]), np.exp(params[1:-1]), math.exp(params[-1])
            k = _kernel(x, x, scale, lengths) + (noise + 1e-8) * np.eye(len(x))
            try:
                chol = np.linalg.cholesky(k)
            except np.linalg.LinAlgError:
                return 1e10
            alpha = np.linalg.solve(chol.T, np.linalg.solve(chol, self.y))
            return 0.5 * self.y @ alpha + np.sum(np.log(np.diag(chol))) + 0.5 * len(x) * math.log(2 * math.pi)
        bounds = [(-3, 3)] + [(math.log(0.1), math.log(30))] * dims + [(math.log(1e-3), math.log(2))]
        best = None
        for start in (0.0, 1.0, -1.0):
            guess = np.asarray([0.0] + [start] * dims + [math.log(0.1)])
            found = minimize(nll, guess, bounds=bounds, method='L-BFGS-B')
            if best is None or found.fun < best.fun:
                best = found
        p = best.x
        self.scale, self.lengths, self.noise = math.exp(p[0]), np.exp(p[1:-1]), math.exp(p[-1])
        k = _kernel(x, x, self.scale, self.lengths) + (self.noise + 1e-8) * np.eye(len(x))
        self.k_inv = np.linalg.inv(k)
        self.alpha = self.k_inv @ self.y

    def predict(self, points):
        ks = _kernel(points, self.x, self.scale, self.lengths)
        mean = ks @ self.alpha
        var = np.maximum(self.scale - np.sum((ks @ self.k_inv) * ks, axis=1), 1e-12)
        return mean * self.y_std + self.y_mean, np.sqrt(var) * self.y_std

    def loo(self):
        """留一交叉验证（高斯过程的闭式解）：每个点用其它点预测它。"""
        diag = np.diag(self.k_inv)
        mean = self.y - self.alpha / diag
        return mean * self.y_std + self.y_mean, np.sqrt(1.0 / diag) * self.y_std


def models():
    lots = _lots()
    space = Space(lots)
    out = {}
    for response in RESPONSES:
        used = [lot for lot in lots if lot['y'][response] is not None]
        if len(used) < MIN_POINTS:
            out[response] = {'response': response, 'n': len(used), 'state': '点太少', 'note': f'只有 {len(used)} 批测了，至少 {MIN_POINTS} 批才建模'}
            continue
        x = np.vstack([space.encode(lot['x'], lot['c']) for lot in used])
        y = np.asarray([lot['y'][response] for lot in used])
        gp = GP(x, y)
        mean, sd = gp.loo()
        sse, sst = float(np.sum((y - mean) ** 2)), float(np.sum((y - y.mean()) ** 2))
        r2 = 1 - sse / sst if sst > 0 else 0.0
        weight = 1.0 / gp.lengths
        importance = sorted(zip(space.names(), (weight / weight.sum()).tolist()), key=lambda item: -item[1])[:5]
        out[response] = {'response': response, 'n': len(used), 'r2': r2, 'rmse': math.sqrt(sse / len(y)), 'gp': gp,
                         'state': '能用' if r2 >= TRUST_R2 else '不太可信', 'importance': importance,
                         'loo': [{'name': lot['name'], 'measured': float(v), 'predicted': float(m), 'sd': float(s)} for lot, v, m, s in zip(used, y, mean, sd)],
                         'range': [float(y.min()), float(y.max())]}
    return lots, space, out


def propose(objectives, extrapolate=0.0, kappa=1.0, count=5, seed=7):
    """objectives: [{response, goal: max|min|target, value?}]。返回模型可信度、提议和留一图的数据。"""
    lots, space, fitted = models()
    rng = np.random.default_rng(seed)
    usable = [item for item in objectives if fitted.get(item['response'], {}).get('state') == '能用']
    skipped = [item['response'] for item in objectives if item not in usable]
    bounds = {}
    for key in NUMERIC:
        values = [lot['x'][key] for lot in lots if lot['x'][key] is not None]
        lo, hi = min(values), max(values)
        pad = (hi - lo) * extrapolate
        bounds[key] = (max(lo - pad, 0.0), hi + pad)
    combos = list({tuple(lot['c'][key] for key in CATEGORY): lot['c'] for lot in lots}.values())
    n = 4000
    cand_x = [{key: float(rng.uniform(*bounds[key])) for key in NUMERIC} for _ in range(n)]
    cand_c = [combos[int(rng.integers(len(combos)))] for _ in range(n)]
    encoded = np.vstack([space.encode(x, c) for x, c in zip(cand_x, cand_c)])
    preds = {response: item['gp'].predict(encoded) for response, item in fitted.items() if 'gp' in item}
    score = np.ones(n)
    for item in usable:
        mean, sd = preds[item['response']]
        lo, hi = fitted[item['response']]['range']
        span = (hi - lo) or 1.0
        if item['goal'] == 'max':
            d = (mean - kappa * sd - lo) / span
        elif item['goal'] == 'min':
            d = (hi - (mean + kappa * sd)) / span
        else:
            d = 1 - (np.abs(mean - float(item['value'])) + kappa * sd) / span
        score *= np.clip(d, 1e-3, 1.0)
    score = score ** (1.0 / max(len(usable), 1))
    order = np.argsort(-score)
    picked = []
    for index in order:
        if all(np.linalg.norm(encoded[index][:len(NUMERIC)] - encoded[j][:len(NUMERIC)]) > 0.6 for j in picked):
            picked.append(int(index))
        if len(picked) == count:
            break
    existing = np.vstack([space.encode(lot['x'], lot['c']) for lot in lots])
    proposals = []
    for index in picked:
        nearest = int(np.argmin(np.linalg.norm(existing - encoded[index], axis=1)))
        proposals.append({'score': float(score[index]), 'x': {key: round(value, 4) for key, value in cand_x[index].items()}, 'c': cand_c[index],
                          'predicted': {response: {'mean': float(preds[response][0][index]), 'sd': float(preds[response][1][index])} for response in preds},
                          'nearest': lots[nearest]['name']})
    table = [{key: value for key, value in item.items() if key != 'gp'} for item in fitted.values()]
    return clean({'models': table, 'proposals': proposals, 'used': [item['response'] for item in usable], 'skipped': skipped,
                  'bounds': {key: list(value) for key, value in bounds.items()}, 'lots': len(lots)})
