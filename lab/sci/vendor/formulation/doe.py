"""Reproducible numerical experimental designs; stdlib only.

All public results are JSON-compatible. The engine designs experiments, not an
optimum formulation: responses have not yet been measured. See docs/experimental_design.md.
"""
from __future__ import annotations

from copy import deepcopy
import itertools
import math
import random


class DesignError(ValueError):
    pass


_NIST = 'https://www.itl.nist.gov/div898/handbook/pri/'
_METHODS = [
    dict(id='full_factorial', name='全因子设计', min_factors=1, max_factors=8, default_model='linear',
         meaning='枚举每个变量所选水平的全部组合，保留完整组合信息。',
         scenarios='变量较少，希望系统比较全部组合；可用于检验交互作用。',
         limitations='试验数为各变量水平数的乘积。只有两个水平时不能估计单独的平方项；需要重复试验估计误差。',
         source=_NIST+'section3/pri333.htm'),
    dict(id='fractional_half', name='二水平半分数因子', min_factors=3, max_factors=8, default_model='linear',
         meaning='用一半的二水平组合筛选变量；最后一列等于前面各列的乘积。',
         scenarios='3–8 个独立变量的初步筛选，试验预算受限。',
         limitations='存在交互作用混杂。3 因素为分辨率 III、4 因素为 IV；不能据此单独判断所有交互作用。报告生成元和分辨率。',
         source=_NIST+'section3/pri334.htm'),
    dict(id='orthogonal_l9', name='L9 三水平正交设计', min_factors=2, max_factors=4, default_model='additive_quadratic',
         meaning='用 9 次试验均衡安排至多 4 个三水平变量，每两列的水平组合出现次数相同。',
         scenarios='2–4 个变量，各有低、中、高三个水平，主要比较主效应和单变量曲率。',
         limitations='交互作用与其他效应混杂；4 因素时加性二次模型有 9 个参数，单次设计没有剩余误差自由度。',
         source=_NIST+'section3/pri33a.htm'),
    dict(id='central_composite', name='面心中心复合设计', min_factors=2, max_factors=6, default_model='quadratic',
         meaning='二水平角点加各轴面心点和中心重复，用于建立完整二次响应面。',
         scenarios='重要变量已筛出，想研究曲率和两两交互，变量不能超出设定上下限。',
         limitations='包含所有变量同时位于边界的角点；面心型通常不具旋转性。需获得响应数据后才能寻找和验证最佳条件。',
         source=_NIST+'section3/pri3361.htm'),
    dict(id='box_behnken', name='Box–Behnken 响应面', min_factors=3, max_factors=5, default_model='quadratic',
         meaning='每次让两个变量取高低水平，其余在中心，并加入中心重复，估计完整二次模型。',
         scenarios='3–5 个变量，希望研究曲率，同时避免所有变量一起到达极端条件。',
         limitations='不能充分检验多变量同时极端的区域；本实现限制 3–5 因素，不向更多因素外推表格。',
         source=_NIST+'section3/pri3362.htm'),
    dict(id='latin_hypercube', name='最大最小距离拉丁超立方', min_factors=1, max_factors=8, default_model='none',
         meaning='每个变量按试验数等分区间，每区取一点；从多个随机布局中选最小点距较大的布局。',
         scenarios='连续变量的探索、非线性黑箱模型、希望均匀覆盖范围且自行指定试验数。',
         limitations='是空间覆盖设计，不保证正交或特定模型可估计；不能替代确认试验。最大最小距离只在抽取的有限布局中比较。',
         source='https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.qmc.LatinHypercube.html'),
    dict(id='d_optimal', name='D 最优候选交换设计', min_factors=1, max_factors=6, default_model='quadratic',
         meaning='针对指定模型，从可行离散候选组合中交换选点，增大信息矩阵行列式，改善参数估计。',
         scenarios='有不可行组合、各变量水平数不同，或试验预算需要自定；已有明确线性或二次模型假设。',
         limitations='依赖候选水平、约束与模型。多起点交换得到局部较优结果，不保证全局最优；必须达到模型满秩。最多 1500 个候选、128 个独立设计点。',
         source=_NIST+'section5/pri521.htm'),
    dict(id='simplex_lattice', name='混料单纯形格点', min_factors=2, max_factors=6, default_model='mixture_quadratic',
         meaning='对总份数固定的一组组分，在混料单纯形内布置格点，适用相互制约的比例变量。',
         scenarios='树脂或溶剂混合比例优化；一组所选组分的合计必须固定。',
         limitations='只处理一个混料组，允许各组分下限，不能再叠加独立工艺变量。若额外上限裁掉格点，设计整体判为不可执行，不会删点冒充标准设计。',
         source=_NIST+'section5/pri542.htm'),
]
_INFO = {m['id']: m for m in _METHODS}
for _m in _METHODS:
    _m['supports_categorical'] = _m['id'] in ('full_factorial','fractional_half','orthogonal_l9','d_optimal')
    _m['categorical_note'] = ('类别按独立水平处理，模型使用 L−1 个虚拟变量，不按数字大小解释。' if _m['supports_categorical'] else '仅支持连续数值变量；类别变量请选择全因子、分数因子、L9 或 D 最优。')
    _m['default_center_points'] = 3 if _m['id'] in ('central_composite','box_behnken') else 0
_MODELS = {
    'none': '空间覆盖（不指定回归模型）',
    'linear': '线性主效应：截距 + 各变量',
    'interaction': '主效应与两两交互：截距 + 各变量 + 两两乘积',
    'quadratic': '完整二次：截距 + 各变量 + 两两乘积 + 各变量平方',
    'additive_quadratic': '加性二次：截距 + 各变量 + 各变量平方（不含交互）',
    'mixture_linear': '混料一次：各组分比例（无独立截距）',
    'mixture_quadratic': 'Scheffé 混料二次：各组分比例 + 两两乘积（无独立截距）',
}


def methods():
    return deepcopy(_METHODS)


def _number(value, label):
    if isinstance(value, bool):
        raise DesignError(label+'必须是有限数值。')
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise DesignError(label+'必须是有限数值。') from None
    if not math.isfinite(value):
        raise DesignError(label+'必须是有限数值。')
    return value


def _integer(value, label, minimum, maximum):
    n = _number(value, label)
    if not n.is_integer() or not minimum <= n <= maximum:
        raise DesignError(f'{label}须为 {minimum}–{maximum} 之间的整数。')
    return int(n)


def _variables(items):
    if not isinstance(items, list) or not items:
        raise DesignError('请至少选择一个数值变量。')
    result, seen = [], set()
    for item in items:
        key = item.get('key')
        if not isinstance(key, str) or not key or key in seen:
            raise DesignError('每个变量需要唯一的 key。')
        seen.add(key)
        name = str(item.get('name') or key)
        kind = item.get('type') or 'numeric'
        if kind == 'categorical':
            levels = item.get('levels')
            if not isinstance(levels,list) or not 2 <= len(levels) <= 11 or any(not isinstance(x,str) or not x.strip() for x in levels) or len(set(levels)) != len(levels):
                raise DesignError(name+'需要 2–11 个不同的非空类别名称。')
            result.append({**deepcopy(item),'key':key,'name':name,'type':kind,'low':0.,'high':float(len(levels)-1),'levels':list(levels)})
            continue
        if kind != 'numeric':
            raise DesignError(name+'变量类型应为 numeric 或 categorical。')
        low, high = _number(item.get('low'), name+'下限'), _number(item.get('high'), name+'上限')
        if high <= low:
            raise DesignError(name+'的上限必须大于下限。')
        # Stable midpoint and difference are used by encoding; reject overflow.
        if not math.isfinite(high-low):
            raise DesignError(name+'的范围过大。')
        levels = item.get('levels')
        if levels is not None:
            if not isinstance(levels, list) or not 2 <= len(levels) <= 11:
                raise DesignError(name+'离散水平数须为 2–11。')
            levels = sorted(_number(v, name+'水平') for v in levels)
            if len(set(levels)) != len(levels) or any(v < low or v > high for v in levels):
                raise DesignError(name+'水平须互不相同且在上下限内。')
        result.append({**deepcopy(item), 'key': key, 'name': name, 'type':kind, 'low': low, 'high': high, 'levels': levels})
    return result


def _constraints(items, variables):
    keys = {v['key'] for v in variables}
    category_keys = {v['key'] for v in variables if v['type']=='categorical'}
    result = []
    for c in items or []:
        selected = c.get('keys', [])
        if c.get('type') != 'sum' or not selected or len(set(selected)) != len(selected) or not set(selected) <= keys:
            raise DesignError('当前仅支持对已选变量指定合计约束，keys 必须唯一且完整有效。')
        if set(selected) & category_keys:
            raise DesignError('类别变量不能参与份数合计约束。')
        total = _number(c.get('total'), '合计约束')
        tolerance = _number(c.get('tolerance', 1e-7), '合计容差')
        if tolerance <= 0:
            raise DesignError('合计容差必须大于 0。')
        result.append({'type': 'sum', 'keys': list(selected), 'total': total, 'tolerance': tolerance})
    return result


def _actual(point, variables):
    return {v['key']: (v['levels'][round((x+1)/2*(len(v['levels'])-1))] if v['type']=='categorical'
                      else v['low'] + (x+1)/2*(v['high']-v['low'])) for x, v in zip(point, variables)}


def _coded(values, variables):
    return [2*v['levels'].index(values[v['key']])/(len(v['levels'])-1)-1 if v['type']=='categorical'
            else 2*(values[v['key']]-v['low'])/(v['high']-v['low'])-1 for v in variables]


def _is_feasible(values, variables, constraints, feasible_fn):
    for v in variables:
        if v['type']=='categorical':
            if values[v['key']] not in v['levels']:
                return False
            continue
        if values[v['key']] < v['low']-1e-10 or values[v['key']] > v['high']+1e-10:
            return False
    for c in constraints:
        if abs(sum(values[k] for k in c['keys'])-c['total']) > c['tolerance']:
            return False
    return bool(feasible_fn(values)) if feasible_fn else True


def _model_row(point, model, variables):
    if model == 'none':
        return []
    x = list(point)
    if model.startswith('mixture_'):
        return x + ([x[i]*x[j] for i in range(len(x)) for j in range(i+1, len(x))] if model == 'mixture_quadratic' else [])
    expanded = []
    for value,v in zip(point,variables):
        if v['type']=='categorical':
            idx=round((value+1)/2*(len(v['levels'])-1))
            expanded.append([1. if idx==j else 0. for j in range(1,len(v['levels']))])
        else:
            expanded.append([value])
    row = [1.0] + [a for block in expanded for a in block]
    if model in ('interaction', 'quadratic'):
        row.extend(a*b for i in range(len(x)) for j in range(i+1, len(x)) for a in expanded[i] for b in expanded[j])
    if model in ('quadratic', 'additive_quadratic'):
        row.extend(a*a for a,v in zip(x,variables) if v['type']=='numeric')
    return row


def _terms(variables, model):
    if model == 'none':
        return []
    names = [v['name'] for v in variables]
    expanded = [[v['name']+' = '+str(level)+'（参照 '+str(v['levels'][0])+'）' for level in v['levels'][1:]]
                if v['type']=='categorical' else [v['name']] for v in variables]
    result = ([] if model.startswith('mixture_') else ['截距']) + [a for block in expanded for a in block]
    if model in ('interaction', 'quadratic', 'mixture_quadratic'):
        result.extend(a+' × '+b for i in range(len(names)) for j in range(i+1, len(names)) for a in expanded[i] for b in expanded[j])
    if model in ('quadratic', 'additive_quadratic'):
        result.extend(v['name']+'²' for v in variables if v['type']=='numeric')
    return result


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _row_basis(rows, return_indices=False, rng=None):
    """Pivoted modified Gram-Schmidt, with reorthogonalization."""
    if not rows or not rows[0]:
        return []
    residuals = [list(row) for row in rows]
    norms = [_dot(r, r) for r in residuals]
    tolerance = max(norms, default=0)*1e-18
    order = list(range(len(rows)))
    if rng:
        rng.shuffle(order)
    basis, selected = [], []
    for _ in range(min(len(rows), len(rows[0]))):
        idx = max(order, key=lambda i: norms[i])
        if norms[idx] <= tolerance:
            break
        r = list(residuals[idx])
        for b in basis:
            d = _dot(r, b)
            r = [a-d*c for a,c in zip(r,b)]
        length = math.sqrt(_dot(r, r))
        if length*length <= tolerance:
            break
        b = [a/length for a in r]
        basis.append(b)
        selected.append(idx)
        order.remove(idx)
        for i in order:
            d = _dot(residuals[i], b)
            residuals[i] = [a-d*c for a,c in zip(residuals[i],b)]
            norms[i] = _dot(residuals[i], residuals[i])
    return selected if return_indices else basis


def _information(rows):
    p = len(rows[0]) if rows else 0
    gram = [[sum(row[i]*row[j] for row in rows) for j in range(p)] for i in range(p)]
    return gram


def _inverse_logdet(matrix):
    """Positive-definite Cholesky inverse; never regularize a singular design."""
    n = len(matrix)
    if not n:
        return [], None
    lower = [[0.0]*n for _ in range(n)]
    scale = max(abs(matrix[i][i]) for i in range(n))
    for i in range(n):
        for j in range(i+1):
            v = matrix[i][j]-sum(lower[i][k]*lower[j][k] for k in range(j))
            if i == j:
                if v <= 1e-12*max(1, scale):
                    raise DesignError('设计信息矩阵不满秩或数值接近奇异，请增加水平、缩减模型或调整约束。')
                lower[i][j] = math.sqrt(v)
            else:
                lower[i][j] = v/lower[j][j]
    inverse = [[0.0]*n for _ in range(n)]
    for col in range(n):
        y = [0.0]*n
        for i in range(n):
            y[i] = ((1.0 if i == col else 0.0)-sum(lower[i][j]*y[j] for j in range(i)))/lower[i][i]
        x = [0.0]*n
        for i in range(n-1, -1, -1):
            x[i] = (y[i]-sum(lower[j][i]*x[j] for j in range(i+1,n)))/lower[i][i]
        for i in range(n):
            inverse[i][col] = x[i]
    return inverse, 2*sum(math.log(lower[i][i]) for i in range(n))


def _statistics(points, variables, model):
    terms = _terms(variables, model)
    rows = [_model_row(x, model,variables) for x in points]
    rank = len(_row_basis(rows))
    p = len(terms)
    logdet = None
    if p and rank == p:
        try:
            _, logdet = _inverse_logdet(_information(rows))
        except DesignError:
            rank = min(rank, p-1)
    return {'id': model, 'name': _MODELS[model], 'terms': terms, 'parameter_count': p,
            'rank': rank, 'full_rank': rank == p, 'logdet_information': logdet,
            'residual_df': len(points)-rank, 'includes_intercept': p > 0 and not model.startswith('mixture_')}


def _d_optimal(candidates, model, runs, rng, starts, iterations, variables):
    features = [_model_row(x, model,variables) for x in candidates]
    p = len(features[0])
    if runs < p:
        raise DesignError(f'所选模型有 {p} 个参数（含截距，如适用），至少需要 {p} 个不同试验点；建议增加重复试验估计误差。')
    if runs > len(candidates):
        raise DesignError(f'只有 {len(candidates)} 个不同的可行候选点，不能选择 {runs} 个。请增加候选水平或减少独立设计点；重复次数另设。')
    if len(_row_basis(features)) < p:
        raise DesignError(f'可行候选集不能估计全部 {p} 个模型参数。请增加候选水平、改用线性模型，或用混料设计处理固定合计。')
    best = None
    starts_details = []
    for _ in range(starts):
        selected = _row_basis(features, return_indices=True, rng=rng)
        pool = [i for i in range(len(features)) if i not in set(selected)]
        rng.shuffle(pool)
        selected.extend(pool[:runs-len(selected)])
        inverse, score = _inverse_logdet(_information([features[i] for i in selected]))
        history, converged = [score], False
        for _iteration in range(iterations):
            selected_set = set(selected)
            transformed = [[_dot(row, col) for col in inverse] for row in features]
            leverages = [_dot(row, tr) for row, tr in zip(features, transformed)]
            ratio_best, exchange = 1+1e-9, None
            for candidate in range(len(features)):
                if candidate in selected_set:
                    continue
                a = leverages[candidate]
                candidate_transform = transformed[candidate]
                for slot, old in enumerate(selected):
                    c = _dot(features[old], candidate_transform)
                    ratio = (1+a)*(1-leverages[old])+c*c
                    if ratio > ratio_best:
                        ratio_best, exchange = ratio, (slot, candidate)
            if exchange is None:
                converged = True
                break
            proposed = list(selected)
            proposed[exchange[0]] = exchange[1]
            next_inverse, next_score = _inverse_logdet(_information([features[i] for i in proposed]))
            if next_score <= score+1e-9:
                converged = True
                break
            selected, inverse, score = proposed, next_inverse, next_score
            history.append(score)
        starts_details.append({'initial_logdet': history[0], 'final_logdet': score, 'exchanges': len(history)-1, 'converged': converged})
        if best is None or score > best[0]:
            best = (score, list(selected), list(history), converged)
    return [candidates[i] for i in best[1]], {
        'candidate_count': len(candidates), 'starts': starts_details,
        'logdet_history': best[2], 'exchange_converged': best[3],
        'optimality_claim': '有限候选集、多起点单点交换的局部较优解；不保证全局最优。',
        'candidate_selection': '不同候选点不重复选择；重复试验由 replicates 添加。',
    }


def _levels(variables, count):
    return [v['levels'] or [v['low']+(v['high']-v['low'])*i/(count-1) for i in range(count)] for v in variables]


def _lattice(q, degree):
    if q == 1:
        yield [degree]
    else:
        for i in range(degree+1):
            for rest in _lattice(q-1, degree-i):
                yield [i]+rest


def generate_design(variables, method, options=None, feasible_fn=None, constraints=None):
    """Generate a complete design. Standard designs keep every infeasible row.

    feasible_fn(values: dict[key, float]) -> bool allows the application to run
    its mass-balance calculation. D-optimal filters candidates *before* fitting;
    other methods set ok=False rather than silently dropping a design point.
    """
    if method not in _INFO:
        raise DesignError('未知的实验设计算法。')
    variables = _variables(variables)
    options = dict(options or {})
    constraints = _constraints(constraints, variables)
    info = deepcopy(_INFO[method])
    categorical=[v for v in variables if v['type']=='categorical']
    if categorical and not info['supports_categorical']:
        raise DesignError(info['name']+'仅支持数值变量。类别变量可用全因子、二水平半分数、L9 或 D 最优。')
    k = len(variables)
    if not info['min_factors'] <= k <= info['max_factors']:
        raise DesignError(f"{info['name']}支持 {info['min_factors']}–{info['max_factors']} 个变量。")
    seed = _integer(options.get('seed', 2026), '随机种子', 0, 2147483647)
    rng = random.Random(seed)
    replicates = _integer(options.get('replicates', 1), '重复次数', 1, 10)
    default_centers = 3 if method in ('central_composite', 'box_behnken') else 0
    centers = _integer(options.get('center_points', default_centers), '中心点次数', 0, 20)
    if categorical and centers:
        raise DesignError('类别变量没有数值中心，混合设计的中心点次数请设为 0。')
    model = options.get('model') or info['default_model']
    if model not in _MODELS:
        raise DesignError('未知的回归模型。')
    warnings, errors, diagnostics = [], [], {}
    points, actuals, model_points = [], None, None
    if method != 'simplex_lattice' and model.startswith('mixture_'):
        raise DesignError('混料模型请使用单纯形格点设计。')
    if method == 'd_optimal' and model not in ('linear', 'interaction', 'quadratic'):
        raise DesignError('D 最优支持线性、两两交互或完整二次模型。')
    parameter_count=len(_terms(variables,model))
    if parameter_count > (36 if method=='d_optimal' else 80):
        raise DesignError(f'所选模型含 {parameter_count} 个参数，超过当前交互计算上限。请减少类别水平、变量或改为线性模型。')
    if method in ('fractional_half', 'central_composite', 'box_behnken', 'latin_hypercube', 'simplex_lattice') and any(v['levels'] for v in variables if v['type']=='numeric'):
        raise DesignError('当前算法使用上下限和指定的连续设计位置，请清空离散水平；离散水平可用全因子、L9 或 D 最优。')
    if method == 'full_factorial':
        levels = _levels(variables, _integer(options.get('levels_count', 2), '水平数', 2, 11))
        runs = math.prod(len(x) for x in levels)
        if runs*replicates+centers > 512:
            raise DesignError('全因子组合过多（上限 512 次）。请减少变量/水平或选择分数因子、L9、D 最优。')
        points = [_coded(dict(zip([v['key'] for v in variables], x)), variables) for x in itertools.product(*levels)]
    elif method == 'fractional_half':
        if any(len(v['levels']) != 2 for v in categorical):
            raise DesignError('二水平半分数设计要求每个类别变量恰好两个类别。')
        points = [list(x)+[math.prod(x)] for x in itertools.product((-1.,1.), repeat=k-1)]
        letters = 'ABCDEFGH'[:k]
        diagnostics.update(generator=letters[-1]+' = '+letters[:-1], defining_relation='I = '+letters, resolution=k)
        aliases = [f'{a} = '+''.join(b for b in letters if b != a) for a in letters]
        diagnostics['main_effect_aliases'] = aliases
        warnings.append(f'分辨率 {k}；定义关系 I = {letters}。主效应混杂关系：'+ '；'.join(aliases)+'。')
    elif method == 'orthogonal_l9':
        levels = _levels(variables, 3)
        if any(len(x) != 3 for x in levels):
            raise DesignError('L9 要求每个变量恰好三个不同水平。')
        for a,b in itertools.product(range(3), repeat=2):
            indices = [a,b,(a+b)%3,(2*a+b)%3]
            values = {v['key']: levels[j][indices[j]] for j,v in enumerate(variables)}
            points.append(_coded(values,variables))
        diagnostics['orthogonal_array'] = 'OA(9, 4, 3, 2) 的前 k 列'
        warnings.append('L9 中主效应与部分交互作用混杂；不能把主效应差异直接解释为独立因果效应。')
    elif method == 'central_composite':
        points = [list(x) for x in itertools.product((-1.,1.),repeat=k)]
        for i in range(k):
            for sign in (-1.,1.):
                point=[0.]*k
                point[i]=sign
                points.append(point)
        diagnostics.update(alpha=1, subtype='face_centered', rotatable=False)
    elif method == 'box_behnken':
        for i,j in itertools.combinations(range(k),2):
            for a,b in itertools.product((-1.,1.),repeat=2):
                point=[0.]*k
                point[i],point[j]=a,b
                points.append(point)
    elif method == 'latin_hypercube':
        if centers:
            raise DesignError('拉丁超立方不可额外插入中心点，否则会破坏分层性质；请将中心点次数设为 0。')
        n = _integer(options.get('runs', max(12, 3*k)), '独立设计点数', 2, 128)
        trials = _integer(options.get('trials', 40), '布局比较次数', 1, 200)
        best_score = -1
        for _ in range(trials):
            columns=[]
            for _ in range(k):
                column=[2*(i+rng.random())/n-1 for i in range(n)]
                rng.shuffle(column)
                columns.append(column)
            sample=[list(x) for x in zip(*columns)]
            distance=min(sum((a-b)**2 for a,b in zip(sample[i],sample[j])) for i in range(n) for j in range(i))
            if distance > best_score:
                points,best_score=sample,distance
        diagnostics.update(trials=trials, min_distance_coded=math.sqrt(best_score), stratification_runs=n)
    elif method == 'd_optimal':
        if centers:
            raise DesignError('D 最优的中心候选已包含在候选集中；请将额外中心点次数设为 0，重复次数另设。')
        levels=_levels(variables,3)
        count=math.prod(len(x) for x in levels)
        if count > 1500:
            raise DesignError(f'候选组合数为 {count}，超过 1500。请减少变量或离散水平。')
        candidates=[]
        for x in itertools.product(*levels):
            values=dict(zip([v['key'] for v in variables],x))
            if _is_feasible(values,variables,constraints,feasible_fn):
                candidates.append(_coded(values,variables))
        if not candidates:
            raise DesignError('没有可行候选点，请调整上下限、离散水平或配方约束。')
        p=len(_model_row(candidates[0],model,variables))
        n=_integer(options.get('runs', min(len(candidates), p+max(3,k))), '独立设计点数', 2, 128)
        starts=_integer(options.get('starts',3), '交换起点数', 1, 5)
        iterations=_integer(options.get('max_iterations',20), '每起点最大交换次数', 1, 50)
        points, diagnostics=_d_optimal(candidates,model,n,rng,starts,iterations,variables)
        diagnostics.update(candidate_count_before_filter=count, infeasible_candidates=count-len(candidates))
        warnings.append(diagnostics['optimality_claim'])
        if not diagnostics['exchange_converged']:
            warnings.append('已达到交换次数上限，仍可能存在进一步改进；可增加交换次数或比较其他种子。')
    elif method == 'simplex_lattice':
        if len(constraints) != 1 or set(constraints[0]['keys']) != {v['key'] for v in variables}:
            raise DesignError('混料格点需要且只允许一个覆盖全部所选变量的固定合计约束。')
        degree=_integer(options.get('degree',2),'混料格点阶数',1,3)
        if model not in ('mixture_linear','mixture_quadratic'):
            raise DesignError('混料格点请选混料一次或混料二次模型。')
        if degree == 1 and model == 'mixture_quadratic':
            raise DesignError('一次格点不能估计混料二次模型；请改为二次格点或混料一次模型。')
        total=constraints[0]['total']
        if total <= 0 or any(v['low'] < 0 for v in variables):
            raise DesignError('混料总份数须为正数，各组分下限不得为负。')
        residual=total-sum(v['low'] for v in variables)
        if residual <= 1e-12:
            raise DesignError('各组分下限之和必须小于固定合计，才能形成可变化的混料区域。')
        pseudo=[[n/degree for n in row] for row in _lattice(k,degree)]
        pseudo.extend([[1/k]*k for _ in range(centers)])
        actuals=[{v['key']:v['low']+residual*z for z,v in zip(row,variables)} for row in pseudo]
        points=[_coded(values,variables) for values in actuals]
        model_points=pseudo
        centers=0
        diagnostics.update(degree=degree,total=total,component_minima={v['key']:v['low'] for v in variables},
                           pseudo_component_scale=residual,model_coordinates='伪组分 zi = (实际份数 − 该组分下限) / (固定合计 − 下限之和)，Σzi = 1')
        warnings.append('混料二次模型使用伪组分及两两乘积，无独立截距；可增设内部点和重复试验检验模型。')
    if centers:
        points.extend([[0.]*k for _ in range(centers)])
    if len(points)*replicates > 512:
        raise DesignError('含重复试验的总次数超过 512，请减少重复次数或变量。')
    if actuals is None:
        actuals=[_actual(p,variables) for p in points]
    if model_points is None:
        model_points=points
    records=[]
    all_model_points=[]
    for replicate in range(1,replicates+1):
        for i,(point,values,model_point) in enumerate(zip(points,actuals,model_points),1):
            feasible=_is_feasible(values,variables,constraints,feasible_fn)
            center=(not categorical and all(abs(x)<1e-10 for x in point)) if method != 'simplex_lattice' else all(abs(x-1/k)<1e-10 for x in model_point)
            records.append({'standard_order':i,'replicate':replicate,'values':dict(values),
                            'coded':dict(zip([v['key'] for v in variables],point)),
                            'is_center':center,'feasible':feasible})
            all_model_points.append(model_point)
    stats=_statistics(all_model_points,variables,model)
    if categorical:
        diagnostics['categorical_encoding']='各类别变量采用 L−1 个虚拟变量（首水平为参照）；表中编码仅用于安排设计，不代表类别有数值顺序。'
        stats['categorical_references']={v['key']:v['levels'][0] for v in categorical}
    infeasible=[i+1 for i,row in enumerate(records) if not row['feasible']]
    if infeasible:
        errors.append(f'{len(infeasible)} 次试验不满足配方计算或比例约束；已保留全部设计点。请调整范围或改用 D 最优候选过滤，不能删除行后继续称为原设计。')
    if not stats['full_rank']:
        errors.append(f"当前模型 {stats['parameter_count']} 个参数，但矩阵秩只有 {stats['rank']}；不能完整估计该模型。请增加水平或简化模型。")
    if stats['parameter_count'] and stats['residual_df'] == 0:
        warnings.append('当前设计为饱和设计，没有剩余误差自由度；建议增加独立重复试验估计实验误差。')
    elif stats['parameter_count'] and len({tuple(p) for p in all_model_points}) == len(all_model_points):
        warnings.append('没有相同条件的独立重复，不能单独估计纯误差；模型拟合还需要实际测量的响应值。')
    if method != 'simplex_lattice' and any(v.get('group') in ('resins','solvents') for v in variables) and not constraints:
        warnings.append('树脂/溶剂“份数”是相对权重，变化后由配方程序归一化；如研究固定合计比例，请选择混料设计或明确设置平衡组分。')
    if options.get('randomize',True):
        rng.shuffle(records)
    for i,row in enumerate(records,1):
        row['run']=i
    diagnostics.update(base_runs=len(points),replicates=replicates,unique_points=len({tuple(p) for p in points}),
                       randomized=bool(options.get('randomize',True)),infeasible_runs=[r['run'] for r in records if not r['feasible']])
    return {'ok':not errors,'method':method,'method_info':info,'variables':variables,'constraints':constraints,
            'rows':records,'runs':len(records),'model':stats,'warnings':warnings,'errors':errors,
            'seed':seed,'options':deepcopy(options),'diagnostics':diagnostics,
            'purpose':'推荐用于收集响应数据的实验矩阵。尚未测量实验结果，因此不预测或宣称最佳配方。'}


def design(variables, method, options=None):
    """Thin wrapper over generate_design for the shared DOE interface (docs/doe_analysis.md).

    variables: [{key, name, type: numeric|categorical|mixture, low, high, levels?, unit?}]
    options.mixture = {"keys": [...], "total": 100}; center_points, replicates, randomize, seed as before.
    Returns {"runs": [{"run": 1, "values": {key: value}}], "notes": [...]} plus the full result under "design".
    """
    options = dict(options or {})
    mixture = options.pop('mixture', None) or {}
    variables = deepcopy(variables)
    keys = list(mixture.get('keys') or [v['key'] for v in variables if v.get('type') == 'mixture'])
    for v in variables:
        if v.get('type') == 'mixture':
            v['type'] = 'numeric'
    constraints = None
    if keys:
        total = mixture.get('total')
        if total is None:
            raise DesignError('混料设计需要在 options.mixture 里给出合计 total。')
        constraints = [{'type': 'sum', 'keys': keys, 'total': total}]
        options.setdefault('model', 'mixture_quadratic')
    result = generate_design(variables, method, options, constraints=constraints)
    runs = [{'run': r['run'], 'values': dict(r['values'])} for r in sorted(result['rows'], key=lambda r: r['run'])]
    return {'runs': runs, 'notes': list(result['warnings']) + list(result['errors']), 'ok': result['ok'], 'design': result}
