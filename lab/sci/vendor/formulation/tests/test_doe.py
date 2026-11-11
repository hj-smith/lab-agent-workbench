import math
import unittest
from collections import Counter
from itertools import combinations

from lab.sci.vendor.formulation.doe import generate_design, methods, DesignError


def variables(k):
    return [{'key':f'x{i+1}','name':f'变量{i+1}','low':0,'high':10} for i in range(k)]


class DesignTests(unittest.TestCase):
    def test_catalog_has_eight_explained_methods_and_sources(self):
        catalog=methods()
        self.assertEqual(len(catalog),8)
        self.assertTrue(all(m['meaning'] and m['scenarios'] and m['limitations'] and m['source'].startswith('https://') for m in catalog))
        catalog[0]['name']='changed'
        self.assertNotEqual(methods()[0]['name'],'changed')

    def test_full_factorial_cartesian_and_scale(self):
        vs=variables(3)
        vs[0]['levels']=[0,2,10]
        r=generate_design(vs,'full_factorial',{'randomize':False})
        self.assertTrue(r['ok'],r['errors'])
        self.assertEqual(r['runs'],12)
        self.assertEqual({row['values']['x1'] for row in r['rows']},{0,2,10})
        self.assertEqual(r['model']['rank'],4)

    def test_half_fraction_generator_resolution_and_orthogonality(self):
        for k in range(3,7):
            r=generate_design(variables(k),'fractional_half')
            self.assertEqual(r['runs'],2**(k-1))
            self.assertEqual(r['diagnostics']['resolution'],k)
            rows=[list(row['coded'].values()) for row in r['rows']]
            self.assertTrue(all(abs(x[-1]-math.prod(x[:-1]))<1e-12 for x in rows))
            for i,j in combinations(range(k),2):
                self.assertAlmostEqual(sum(x[i]*x[j] for x in rows),0)
            self.assertTrue(r['model']['full_rank'])

    def test_l9_all_pairs_are_balanced_and_additive_quadratic_rank9(self):
        r=generate_design(variables(4),'orthogonal_l9')
        self.assertEqual(r['runs'],9)
        self.assertEqual(r['model']['rank'],9)
        self.assertEqual(r['model']['residual_df'],0)
        for i,j in combinations(range(4),2):
            pairs=Counter((x['values'][f'x{i+1}'],x['values'][f'x{j+1}']) for x in r['rows'])
            self.assertEqual(set(pairs.values()),{1})
            self.assertEqual(len(pairs),9)

    def test_face_centered_matches_three_factor_count_and_quadratic_rank(self):
        r=generate_design(variables(3),'central_composite',{'center_points':6})
        self.assertTrue(r['ok'],r['errors'])
        self.assertEqual(r['runs'],20)
        self.assertEqual(sum(x['is_center'] for x in r['rows']),6)
        self.assertEqual(r['model']['rank'],10)
        self.assertTrue(all(-1<=v<=1 for row in r['rows'] for v in row['coded'].values()))

    def test_box_behnken_excludes_corners_and_full_quadratic_rank(self):
        for k,centers,expected in [(3,3,15),(4,3,27),(5,6,46)]:
            r=generate_design(variables(k),'box_behnken',{'center_points':centers})
            self.assertTrue(r['ok'],r['errors'])
            self.assertEqual(r['runs'],expected)
            for row in r['rows']:
                if not row['is_center']:
                    self.assertEqual(sum(abs(v)>1e-10 for v in row['coded'].values()),2)
            self.assertEqual(r['model']['rank'],1+2*k+k*(k-1)//2)

    def test_lhs_stratifies_every_dimension_and_seed_reproduces(self):
        r=generate_design(variables(4),'latin_hypercube',{'runs':20,'seed':123})
        self.assertEqual(r,generate_design(variables(4),'latin_hypercube',{'runs':20,'seed':123}))
        for key in ('x1','x2','x3','x4'):
            bins=sorted(int(row['values'][key]/10*20) for row in r['rows'])
            self.assertEqual(bins,list(range(20)))
        self.assertGreater(r['diagnostics']['min_distance_coded'],0)

    def test_dopt_filters_first_and_only_returns_full_rank(self):
        r=generate_design(variables(2),'d_optimal',{'runs':7,'model':'quadratic'},feasible_fn=lambda v:v['x1']+v['x2']<=15)
        self.assertTrue(r['ok'],r['errors'])
        self.assertEqual(r['diagnostics']['candidate_count'],8)
        self.assertEqual(r['diagnostics']['infeasible_candidates'],1)
        self.assertEqual(r['model']['parameter_count'],6)
        self.assertEqual(r['model']['rank'],6)
        self.assertTrue(all(row['values']['x1']+row['values']['x2']<=15 for row in r['rows']))
        hist=r['diagnostics']['logdet_history']
        self.assertTrue(all(a<=b for a,b in zip(hist,hist[1:])))
        self.assertEqual(r,generate_design(variables(2),'d_optimal',{'runs':7,'model':'quadratic'},feasible_fn=lambda v:v['x1']+v['x2']<=15))

    def test_dopt_two_level_full_factorial_linear_determinant(self):
        vs=variables(3)
        for v in vs:v['levels']=[0,10]
        r=generate_design(vs,'d_optimal',{'runs':8,'model':'linear'})
        self.assertTrue(r['ok'],r['errors'])
        self.assertAlmostEqual(r['model']['logdet_information'],4*math.log(8),places=10)

    def test_dopt_rejects_insufficient_runs_and_singular_candidate_set(self):
        with self.assertRaisesRegex(DesignError,'至少需要'):
            generate_design(variables(3),'d_optimal',{'runs':8,'model':'quadratic'})
        with self.assertRaisesRegex(DesignError,'候选集不能'):
            generate_design(variables(2),'d_optimal',{'runs':3,'model':'linear'},feasible_fn=lambda v:v['x1']==v['x2'])

    def test_standard_infeasibility_preserves_whole_design(self):
        r=generate_design(variables(2),'full_factorial',feasible_fn=lambda v:v['x1']+v['x2']<20)
        self.assertFalse(r['ok'])
        self.assertEqual(r['runs'],4)
        self.assertEqual(sum(not x['feasible'] for x in r['rows']),1)

    def test_two_levels_do_not_claim_quadratic_estimability(self):
        r=generate_design(variables(3),'full_factorial',{'model':'quadratic'})
        self.assertFalse(r['ok'])
        self.assertFalse(r['model']['full_rank'])
        self.assertIsNone(r['model']['logdet_information'])

    def test_mixture_uses_fixed_sum_and_scheffe_model(self):
        vs=[{**v,'high':100} for v in variables(3)]
        r=generate_design(vs,'simplex_lattice',{'degree':2,'center_points':3},constraints=[{'type':'sum','keys':['x1','x2','x3'],'total':100}])
        self.assertTrue(r['ok'],r['errors'])
        self.assertEqual(r['runs'],9)
        self.assertEqual(r['model']['rank'],6)
        self.assertFalse(r['model']['includes_intercept'])
        for row in r['rows']:
            self.assertAlmostEqual(sum(row['values'].values()),100)

    def test_mixture_supports_lower_bounds_without_silent_upper_bound_clipping(self):
        vs=[{**v,'low':10,'high':80} for v in variables(3)]
        c=[{'type':'sum','keys':['x1','x2','x3'],'total':100}]
        r=generate_design(vs,'simplex_lattice',constraints=c)
        self.assertTrue(r['ok'])
        self.assertTrue(all(min(x['values'].values())>=10 for x in r['rows']))
        vs[0]['high']=50
        r=generate_design(vs,'simplex_lattice',constraints=c)
        self.assertFalse(r['ok'])
        self.assertEqual(r['runs'],6)

    def test_replicates_and_random_order_keep_standard_identity(self):
        r=generate_design(variables(2),'full_factorial',{'replicates':3,'seed':6})
        self.assertEqual(r['runs'],12)
        self.assertEqual(Counter(x['standard_order'] for x in r['rows']),{1:3,2:3,3:3,4:3})
        self.assertEqual([x['run'] for x in r['rows']],list(range(1,13)))

    def test_invalid_numeric_inputs_duplicate_keys_and_resource_limits(self):
        for low in [float('nan'),float('inf'),'bad',True]:
            with self.assertRaises(DesignError):generate_design([{'key':'x','low':low,'high':10}],'full_factorial')
        with self.assertRaises(DesignError):generate_design(variables(8),'full_factorial',{'levels_count':3})
        with self.assertRaises(DesignError):generate_design(variables(2)*2,'full_factorial')
        with self.assertRaises(DesignError):generate_design(variables(2),'latin_hypercube',{'center_points':1})

    def test_mixed_full_factorial_has_dummy_model_not_ordinal(self):
        vs=[{'key':'resin','name':'树脂','type':'categorical','levels':['树脂A','树脂B','树脂C']},variables(1)[0]]
        r=generate_design(vs,'full_factorial',{'levels_count':3,'model':'quadratic'})
        self.assertTrue(r['ok'],r['errors'])
        self.assertEqual(r['runs'],9)
        # 1 intercept + 2 resin dummy + 1 numeric + 2 mixed interactions + 1 numeric square.
        self.assertEqual(r['model']['parameter_count'],7)
        self.assertEqual(r['model']['rank'],7)
        self.assertFalse(any('树脂²' in term for term in r['model']['terms']))
        self.assertEqual({x['values']['resin'] for x in r['rows']},{'树脂A','树脂B','树脂C'})

    def test_categorical_by_categorical_interaction_is_full_factorial_estimable(self):
        vs=[{'key':'a','name':'来源','type':'categorical','levels':['甲','乙','丙']},
            {'key':'b','name':'工艺','type':'categorical','levels':['搅拌','超声']}]
        r=generate_design(vs,'full_factorial',{'model':'interaction'})
        self.assertTrue(r['ok'],r['errors'])
        self.assertEqual(r['model']['parameter_count'],6)
        self.assertEqual(r['model']['rank'],6)

    def test_dopt_categorical_and_continuous_with_feasible_source_filter(self):
        vs=[{'key':'source','name':'来源','type':'categorical','levels':['A','B','C']},variables(1)[0]]
        r=generate_design(vs,'d_optimal',{'model':'linear','runs':5},feasible_fn=lambda v:not(v['source']=='C' and v['x1']==10))
        self.assertTrue(r['ok'],r['errors'])
        self.assertEqual(r['model']['parameter_count'],4)
        self.assertEqual(r['model']['rank'],4)
        self.assertEqual(r['diagnostics']['candidate_count'],8)
        self.assertTrue(all(not(row['values']['source']=='C' and row['values']['x1']==10) for row in r['rows']))

    def test_l9_mixed_categories_and_numeric_additive_model(self):
        vs=[{'key':'source','type':'categorical','levels':['A','B','C']},*variables(3)]
        r=generate_design(vs,'orthogonal_l9')
        self.assertTrue(r['ok'],r['errors'])
        self.assertEqual(r['model']['rank'],9)
        self.assertEqual(Counter(x['values']['source'] for x in r['rows']),{'A':3,'B':3,'C':3})

    def test_half_fraction_supports_exactly_two_categories(self):
        vs=[{'key':'source','type':'categorical','levels':['A','B']},*variables(2)]
        r=generate_design(vs,'fractional_half')
        self.assertTrue(r['ok'],r['errors'])
        self.assertEqual(r['model']['rank'],4)
        vs[0]['levels'].append('C')
        with self.assertRaisesRegex(DesignError,'两个类别'):generate_design(vs,'fractional_half')

    def test_unsupported_categorical_methods_and_sum_constraint_fail_clearly(self):
        vs=[{'key':'source','type':'categorical','levels':['A','B','C']},*variables(2)]
        for method in ['latin_hypercube','central_composite','box_behnken','simplex_lattice']:
            with self.assertRaisesRegex(DesignError,'仅支持数值'):generate_design(vs,method)
        with self.assertRaisesRegex(DesignError,'不能参与'):
            generate_design(vs,'full_factorial',constraints=[{'type':'sum','keys':['source','x1'],'total':100}])


if __name__=='__main__':
    unittest.main(verbosity=2)
