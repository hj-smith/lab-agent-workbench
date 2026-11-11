"""Verification of lab.sci.vendor.formulation.doe_analysis against published numbers and hand calculations.

Sources are repeated in docs/doe_analysis.md.
"""
import math

import pytest

from lab.sci.vendor.formulation import doe, doe_analysis as da


def term(result, name):
    return next(c for c in result['coefficients'] if c['term'] == name)


def anova(result, source):
    return next(a for a in result['anova'] if a['source'] == source)


# 4. distributions -------------------------------------------------------------------------

def test_t_and_f_table_values():
    """Standard table values (Montgomery appendix / NIST e-Handbook), printed precision."""
    assert da.t_ppf(0.975, 10) == pytest.approx(2.228, abs=5e-4)
    assert da.t_ppf(0.95, 5) == pytest.approx(2.015, abs=5e-4)
    assert da.t_ppf(0.995, 20) == pytest.approx(2.845, abs=5e-4)
    assert da.t_ppf(0.975, 1) == pytest.approx(12.706, abs=5e-4)
    assert da.f_ppf(0.95, 2, 10) == pytest.approx(4.10, abs=5e-3)
    assert da.f_ppf(0.95, 5, 10) == pytest.approx(3.33, abs=5e-3)
    assert da.f_ppf(0.99, 3, 12) == pytest.approx(5.95, abs=5e-3)
    assert da.f_ppf(0.95, 1, 8) == pytest.approx(5.32, abs=5e-3)


def test_distributions_closed_forms():
    """t with 1 df is Cauchy, t with 2 df and F(2,2) have closed forms: exact to 1e-9."""
    for x in (-3.2, -0.7, 0.4, 2.5):
        assert da.t_cdf(x, 1) == pytest.approx(0.5 + math.atan(x) / math.pi, rel=1e-9)
        assert da.t_cdf(x, 2) == pytest.approx(0.5 + x / (2 * math.sqrt(2 + x * x)), rel=1e-9)
    for x in (0.1, 1.0, 7.5):
        assert da.f_sf(x, 2, 2) == pytest.approx(1 / (1 + x), rel=1e-9)
        assert da.f_sf(x, 2, 10) == pytest.approx((1 + 2 * x / 10) ** -5, rel=1e-9)
    assert da.t_ppf(0.3, 7) == pytest.approx(-da.t_ppf(0.7, 7), rel=1e-12)
    assert da.t_cdf(da.t_ppf(0.9, 13), 13) == pytest.approx(0.9, rel=1e-10)
    assert da.f_cdf(da.f_ppf(0.99, 4, 9), 4, 9) == pytest.approx(0.99, rel=1e-10)


# 1. 2^2 factorial with interaction ---------------------------------------------------------

def yield_example():
    """Montgomery, Design and Analysis of Experiments, Example 6.1 (2^2, n = 3):
    A reactant concentration 15/25 %, B catalyst 1/2 bags, response yield.
    (1)=28,25,27  a=36,32,32  b=18,19,23  ab=31,30,29.
    Published: effects A=8.33, B=-5.00, AB=1.67; SS_A=208.33, SS_B=75.00, SS_AB=8.33,
    SS_E=31.33, SS_T=323.00; fitted y = 27.5 + 4.1667 x1 - 2.5 x2 + 0.8333 x1x2."""
    data = [((-1, -1), [28, 25, 27]), ((1, -1), [36, 32, 32]), ((-1, 1), [18, 19, 23]), ((1, 1), [31, 30, 29])]
    runs, resp, i = [], {}, 0
    for (a, b), ys in data:
        for y in ys:
            i += 1
            runs.append({'run': i, 'values': {'A': 20 + 5 * a, 'B': 1.5 + .5 * b}})
            resp[i] = y
    return runs, resp


def test_factorial_2x2_montgomery_example_6_1():
    runs, resp = yield_example()
    r = da.analyze(runs, resp, 'interaction')
    assert term(r, '截距')['estimate'] == pytest.approx(27.5, rel=1e-9)
    assert term(r, 'A')['estimate'] == pytest.approx(25 / 6, rel=1e-9)
    assert term(r, 'B')['estimate'] == pytest.approx(-2.5, rel=1e-9)
    assert term(r, 'A*B')['estimate'] == pytest.approx(5 / 6, rel=1e-9)
    ss = {s: anova(r, s)['ss'] for s in ('A', 'B', 'A*B', '残差', '合计')}
    assert ss['A'] == pytest.approx(208.33, abs=5e-3)
    assert ss['B'] == pytest.approx(75.00, abs=5e-3)
    assert ss['A*B'] == pytest.approx(8.33, abs=5e-3)
    assert ss['残差'] == pytest.approx(31.33, abs=5e-3)
    assert ss['合计'] == pytest.approx(323.00, abs=5e-3)
    # exact arithmetic: SS_A = 2500/12, MSE = 94/24, F = SS/MSE
    assert ss['A'] == pytest.approx(2500 / 12, rel=1e-9)
    assert anova(r, 'A')['f'] == pytest.approx((2500 / 12) / (94 / 24), rel=1e-9)
    assert anova(r, 'B')['f'] == pytest.approx(75 / (94 / 24), rel=1e-9)
    # p values: two-sided t(8) / F(1,8) tail, cross-checked with scipy.stats.f.sf (offline literals)
    assert anova(r, 'A')['p'] == pytest.approx(8.4437169e-05, rel=1e-6)
    assert anova(r, 'B')['p'] == pytest.approx(0.0023615708, rel=1e-6)
    assert anova(r, 'A*B')['p'] == pytest.approx(0.18277648, rel=1e-6)
    assert [a['df'] for a in r['anova'] if a['source'] in ('模型', '残差', '合计')] == [3, 8, 11]
    assert r['r2'] == pytest.approx(1 - 94 / 3 / 323, rel=1e-9)
    # replicates: all residual is pure error, so no lack-of-fit row
    assert anova(r, '纯误差')['df'] == 8 and not any(a['source'] == '失拟' for a in r['anova'])
    eff = {e['term']: e['effect'] for e in r['effects']}
    assert eff['A'] == pytest.approx(8.33, abs=5e-3) and eff['B'] == pytest.approx(-5.00, abs=5e-3)
    assert eff['A*B'] == pytest.approx(1.67, abs=5e-3)
    assert r['effects'][0]['term'] == 'A' and r['effects'][0]['rank'] == 1
    # coefficients in actual units: y = 28.333 + 0.3333 A - 11.667 B + 0.3333 A*B (hand expansion)
    act = {c['term']: c['estimate'] for c in r['coefficients_actual']}
    assert act['截距'] == pytest.approx(85 / 3, rel=1e-9)
    assert act['A'] == pytest.approx(1 / 3, rel=1e-9)
    assert act['B'] == pytest.approx(-35 / 3, rel=1e-9)
    assert act['A*B'] == pytest.approx(1 / 3, rel=1e-9)
    assert term(r, 'A')['vif'] == pytest.approx(1.0, rel=1e-9)


def test_predict_intervals_hand_calculation():
    runs, resp = yield_example()
    r = da.analyze(runs, resp, 'interaction')
    p = da.predict(r, [{'A': 20, 'B': 1.5}, {'A': 25, 'B': 1}])
    t = 2.306004135
    assert p[0]['value'] == pytest.approx(27.5, rel=1e-9)
    half_ci = t * math.sqrt(94 / 24 / 12)
    half_pi = t * math.sqrt(94 / 24 * (1 + 1 / 12))
    assert p[0]['ci_high'] - 27.5 == pytest.approx(half_ci, rel=1e-6)
    assert 27.5 - p[0]['pi_low'] == pytest.approx(half_pi, rel=1e-6)
    assert (p[0]['low'], p[0]['high']) == (p[0]['pi_low'], p[0]['pi_high'])
    assert p[1]['value'] == pytest.approx(100 / 3, rel=1e-9)   # saturated 2^2: fitted = mean of the 'a' cell (36,32,32)


def test_missing_response_skipped_and_named():
    runs, resp = yield_example()
    resp[3] = None
    del resp[5]
    r = da.analyze(runs, resp, 'interaction')
    assert r['n'] == 10
    assert any('试验 3' in w for w in r['warnings']) and any('试验 5' in w for w in r['warnings'])


def test_design_wrapper_roundtrip_with_categorical_and_aliasing():
    d = doe.design([{'key': 'A', 'type': 'numeric', 'low': 0, 'high': 10},
                    {'key': 'B', 'type': 'numeric', 'low': 5, 'high': 15},
                    {'key': 'S', 'type': 'categorical', 'levels': ['x', 'y', 'z']}],
                   'full_factorial', {'seed': 3, 'replicates': 2, 'model': 'linear'})
    assert d['runs'] and set(d['runs'][0]) == {'run', 'values'}
    truth = {'x': 1.0, 'y': -2.0, 'z': 1.0}   # sum-to-zero level effects
    resp = {r['run']: 10 + 0.5 * (r['values']['A'] - 5) + 0.25 * (r['values']['B'] - 10) + truth[r['values']['S']] for r in d['runs']}
    r = da.analyze(d['runs'], resp, 'linear', [{'key': 'A', 'low': 0, 'high': 10}, {'key': 'B', 'low': 5, 'high': 15},
                                               {'key': 'S', 'type': 'categorical', 'levels': ['x', 'y', 'z']}])
    assert term(r, 'A')['estimate'] == pytest.approx(2.5, rel=1e-9)
    assert term(r, 'B')['estimate'] == pytest.approx(1.25, rel=1e-9)
    assert term(r, 'S[x]')['estimate'] == pytest.approx(1.0, rel=1e-9)
    assert term(r, 'S[y]')['estimate'] == pytest.approx(-2.0, rel=1e-9)   # z = -(1-2) = 1
    # half fraction: C = AB, so interaction model aliases A*B with C
    h = doe.design([{'key': k, 'type': 'numeric', 'low': -1, 'high': 1} for k in 'ABC'], 'fractional_half', {'randomize': False})
    y = {r['run']: 3 + r['values']['A'] + 2 * r['values']['B'] for r in h['runs']}
    ra = da.analyze(h['runs'], y, ['A', 'B', 'C', 'A*B'])
    assert any('A*B' in w and '混杂' in w for w in ra['warnings'])
    assert term(ra, 'A*B')['aliased'] is True
    assert term(ra, 'A')['estimate'] == pytest.approx(1.0, rel=1e-9)


# 2. response surface: central composite -----------------------------------------------------

CCD = [(-1, -1, 76.5), (-1, 1, 77.0), (1, -1, 78.0), (1, 1, 79.5), (-1.414, 0, 75.6), (1.414, 0, 78.4),
       (0, -1.414, 77.0), (0, 1.414, 78.5), (0, 0, 79.9), (0, 0, 80.3), (0, 0, 80.0), (0, 0, 79.7), (0, 0, 79.8)]


def test_central_composite_montgomery_example_11_1():
    """Montgomery, Design and Analysis of Experiments, Example 11-1 (time 80-90 min, temperature 170-180 F,
    yield, alpha = 1.414, 5 centre points). Published: y = 79.94 + 0.995 x1 + 0.515 x2 - 1.376 x1^2 - 1.001 x2^2
    + 0.25 x1x2, ANOVA model SS 28.25 of total 28.74, stationary point x1 = 0.389, x2 = 0.306, y = 80.21
    (time about 87 min, temperature about 176.5 F)."""
    runs = [{'run': i + 1, 'values': {'time': 85 + 5 * a, 'temp': 175 + 5 * b}} for i, (a, b, _) in enumerate(CCD)]
    resp = {i + 1: y for i, (_, _, y) in enumerate(CCD)}
    r = da.analyze(runs, resp, 'quadratic', [{'key': 'time', 'low': 80, 'high': 90}, {'key': 'temp', 'low': 170, 'high': 180}])
    for name, want in (('截距', 79.94), ('time', 0.995), ('temp', 0.515), ('time^2', -1.376), ('temp^2', -1.001), ('time*temp', 0.25)):
        assert term(r, name)['estimate'] == pytest.approx(want, abs=5e-4), name
    assert anova(r, '模型')['ss'] == pytest.approx(28.25, abs=5e-3)
    assert anova(r, '合计')['ss'] == pytest.approx(28.74, abs=5e-3)
    assert anova(r, '模型')['df'] == 5 and anova(r, '残差')['df'] == 7
    assert anova(r, '模型')['p'] < 1e-4
    assert r['r2'] == pytest.approx(28.25 / 28.74, abs=5e-4)
    assert anova(r, '失拟')['df'] == 3 and anova(r, '纯误差')['df'] == 4
    assert 0 < r['r2_pred'] < r['r2_adj'] < r['r2'] < 1
    s = da.stationary_point(r)
    assert s['coded']['time'] == pytest.approx(0.389, abs=5e-4) and s['coded']['temp'] == pytest.approx(0.306, abs=5e-4)
    assert s['value'] == pytest.approx(80.21, abs=5e-3) and s['nature'] == 'maximum'
    assert s['values']['time'] == pytest.approx(86.95, abs=0.01) and s['values']['temp'] == pytest.approx(176.53, abs=0.01)
    # the optimiser (max yield, no extrapolation beyond the factorial cube) lands at the stationary point
    o = da.optimize({'y': r}, [{'response_id': 'y', 'goal': 'max', 'low': 75, 'high': 85}])
    assert o['best']['values']['time'] == pytest.approx(s['values']['time'], abs=0.02)
    assert o['best']['values']['temp'] == pytest.approx(s['values']['temp'], abs=0.02)
    assert o['best']['desirability'] == pytest.approx((s['value'] - 75) / 10, abs=1e-6)
    # contour grid in actual units
    c = da.contour(r, 'time', 'temp', {}, grid=5)
    assert len(c['x']) == 5 and len(c['z']) == 5 and len(c['z'][0]) == 5
    assert c['x'][0] == 80 and c['x'][-1] == 90 and c['z'][2][2] == pytest.approx(79.94, abs=5e-4)
    # prediction beyond the design range is flagged
    assert da.predict(r, [{'time': 100, 'temp': 175}])[0]['extrapolated'] is True


# 3. mixture: Scheffe quadratic -----------------------------------------------------------------

YARN = [((1, 0, 0), [11.0, 12.4]), ((0, 1, 0), [8.8, 10.0]), ((0, 0, 1), [16.8, 16.0]),
        ((.5, .5, 0), [15.0, 14.8, 16.1]), ((.5, 0, .5), [17.7, 16.4, 16.6]), ((0, .5, .5), [10.0, 9.7, 11.8])]


def yarn_result():
    runs, resp, i = [], {}, 0
    for p, ys in YARN:
        for y in ys:
            i += 1
            runs.append({'run': i, 'values': dict(zip(('polyethylene', 'polystyrene', 'polypropylene'), p))})
            resp[i] = y
    return da.analyze(runs, resp, 'quadratic', None, {'mixture': {'keys': ['polyethylene', 'polystyrene', 'polypropylene'], 'total': 1}})


def test_mixture_scheffe_quadratic_cornell_yarn():
    """Cornell, Experiments with Mixtures (yarn elongation, polyethylene/polystyrene/polypropylene,
    simplex-lattice with replicates). Published Scheffe quadratic fit:
    y = 11.7 x1 + 9.4 x2 + 16.4 x3 + 19.0 x1x2 + 11.4 x1x3 - 9.6 x2x3."""
    r = yarn_result()
    want = {'polyethylene': 11.7, 'polystyrene': 9.4, 'polypropylene': 16.4,
            'polyethylene*polystyrene': 19.0, 'polyethylene*polypropylene': 11.4, 'polystyrene*polypropylene': -9.6}
    for name, v in want.items():
        assert term(r, name)['estimate'] == pytest.approx(v, rel=1e-9), name
    assert not any(c['term'] == '截距' for c in r['coefficients'])
    assert r['df_residual'] == 9 and anova(r, '纯误差')['df'] == 9
    assert anova(r, '纯误差')['ss'] == pytest.approx(6.56, abs=5e-3)    # within-cell sum of squares of the data above
    assert r['effects'] == []
    # blend means reproduce the fitted surface; (.5,.5,0) -> 15.3
    p = da.predict(r, [{'polyethylene': .5, 'polystyrene': .5, 'polypropylene': 0}])[0]['value']
    assert p == pytest.approx(15.3, rel=1e-9)


def test_mixture_design_roundtrip_exact():
    keys = ['a', 'b', 'c']
    d = doe.design([{'key': k, 'type': 'mixture', 'low': 0, 'high': 100} for k in keys], 'simplex_lattice',
                   {'mixture': {'keys': keys, 'total': 100}, 'center_points': 2, 'randomize': False})
    f = lambda v: 2 * v['a'] / 100 + 5 * v['b'] / 100 + 3 * v['c'] / 100 + 8 * v['a'] * v['b'] / 1e4
    resp = {r['run']: f(r['values']) for r in d['runs']}
    r = da.analyze(d['runs'], resp, 'quadratic', None, {'mixture': {'keys': keys, 'total': 100}})
    assert term(r, 'a')['estimate'] == pytest.approx(2, rel=1e-9)
    assert term(r, 'a*b')['estimate'] == pytest.approx(8, rel=1e-9)
    assert term(r, 'b*c')['estimate'] == pytest.approx(0, abs=1e-9)


# 5. desirability ---------------------------------------------------------------------------

def test_desirability_by_hand():
    """Derringer-Suich worked by hand: max with low 0 / high 10 at y = 7.5 -> 0.75 (weight 2 -> 0.5625);
    target 5 within 0..10 at y = 7.5 -> 0.5; min with best 2 / worst 10 at y = 4 -> 0.75.
    Overall D with importances 1 and 2 = (0.75 * 0.5^2)^(1/3)."""
    mx = {'goal': 'max', 'low': 0., 'high': 10.}
    assert da.desirability(7.5, mx) == pytest.approx(0.75, rel=1e-12)
    assert da.desirability(7.5, {**mx, 'weight': 2}) == pytest.approx(0.5625, rel=1e-12)
    assert da.desirability(-1, mx) == 0 and da.desirability(11, mx) == 1
    tg = {'goal': 'target', 'target': 5., 'low': 0., 'high': 10.}
    assert da.desirability(7.5, tg) == pytest.approx(0.5, rel=1e-12)
    assert da.desirability(2.5, tg) == pytest.approx(0.5, rel=1e-12) and da.desirability(11, tg) == 0
    mn = {'goal': 'min', 'low': 2., 'high': 10.}
    assert da.desirability(4, mn) == pytest.approx(0.75, rel=1e-12)
    assert da._overall([0.75, 0.5], [1, 2]) == pytest.approx((0.75 * 0.25) ** (1 / 3), rel=1e-12)


def test_optimize_two_responses_known_optimum():
    """Exact (noise-free) models on a face-centred CCD in coded units:
    y1 = 10 + 2 x  (max, 8..12)  and  y2 = 20 - 4 (x - 0.5)^2 (target 20, 16..20).
    Hand solution: D(x) = ((x+... )) -> search must give x close to the analytic maximiser of
    d1 * d2 = ((x + 1)/2) * (1 - (x - 0.5)^2), i.e. root of derivative on (-1, 1)."""
    pts = [(a, b) for a in (-1, 0, 1) for b in (-1, 0, 1)] + [(0, 0), (0, 0)]
    runs = [{'run': i + 1, 'values': {'x': a, 'z': b}} for i, (a, b) in enumerate(pts)]
    vs = [{'key': 'x', 'low': -1, 'high': 1}, {'key': 'z', 'low': -1, 'high': 1}]
    m1 = da.analyze(runs, {i + 1: 10 + 2 * a for i, (a, b) in enumerate(pts)}, 'quadratic', vs)
    m2 = da.analyze(runs, {i + 1: 20 - 4 * (a - .5) ** 2 for i, (a, b) in enumerate(pts)}, 'quadratic', vs)
    o = da.optimize({'y1': m1, 'y2': m2}, [{'response_id': 'y1', 'goal': 'max', 'low': 8, 'high': 12},
                                           {'response_id': 'y2', 'goal': 'target', 'target': 20, 'low': 16, 'high': 20}])
    g = lambda x: ((x + 1) / 2) * (1 - (x - .5) ** 2)
    xs = [-1 + 2 * i / 200000 for i in range(200001)]
    xbest = max(xs, key=g)
    assert o['best']['values']['x'] == pytest.approx(xbest, abs=2e-3)
    assert o['best']['desirability'] == pytest.approx(math.sqrt(g(xbest)), abs=1e-4)
    assert o['best']['predicted']['y1']['value'] == pytest.approx(10 + 2 * o['best']['values']['x'], abs=1e-6)
    assert len(o['candidates']) >= 1
