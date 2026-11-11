"""Production-sheet golden values and the other material-balance paths."""
import pytest

from lab.sci.vendor.formulation import pilot_calc as calc


DILUENT = {"SolvB": 0.7, "SolvA": 0.3}
TARGETS = [0.0153, 0.0146, 0.0139, 0.0134, 0.0126]
ACTUAL = [(76.4993, 3.4992), (73.0008, 6.9997), (69.5009, 10.5008),
          (66.9999, 13.001), (62.9994, 17)]
SOLIDS = [0.015300146877753959, 0.014600068749570316, 0.013899884627451668,
          0.013399829251920917, 0.012599974499808746]
THICKNESS = [
    [39.15, 39.51, 39.6, 39.78, 39.43, 39.2],
    [37.74, 37.67, 37.73, 37.69, 37.66, 37.84],
    [36.11, 35.79, 35.82, 35.94, 35.55, 36.08],
    [34.1, 34.01, 34.11, 33.92, 34.15],
    [33.33, 33.23, 33.06, 33.13, 33.1, 33.35],
    [30.71, 30.82, 31.11, 30.61, 30.68],
]


def golden_fit():
    return calc.linear_fit([
        {"x": solid, "y": calc.thickness_stats(values)["mean"], "include": index != 4}
        for index, (solid, values) in enumerate(zip([0.016, *SOLIDS], THICKNESS))
    ])


def test_golden_dilution_and_actual_weighing():
    result = calc.dilution_series(0.016, 400, [
        {"target_solids": target, "sample_mass_g": 80, "actual_stock_g": actual[0], "actual_diluent_g": actual[1]}
        for target, actual in zip(TARGETS, ACTUAL)
    ], DILUENT)
    rows = result["points"]
    assert [row["stock_g"] for row in rows] == pytest.approx([76.5, 73, 69.5, 67, 63])
    assert [row["diluent_g"] for row in rows] == pytest.approx([3.5, 7, 10.5, 13, 17])
    assert [row["diluent_components"]["SolvB"] for row in rows] == pytest.approx([2.45, 4.9, 7.35, 9.1, 11.9])
    assert [row["diluent_components"]["SolvA"] for row in rows] == pytest.approx([1.05, 2.1, 3.15, 3.9, 5.1])
    assert [row["actual_solids"] for row in rows] == pytest.approx(SOLIDS, abs=1e-15)
    assert result["total_stock_g"] == pytest.approx(349)
    assert result["within_available"]


def test_golden_thickness_fit_and_batch_back_calculation():
    stats = calc.thickness_stats(THICKNESS[0])
    assert stats["mean"] == pytest.approx(39.445, abs=1e-12)
    assert stats["three_sigma"] == pytest.approx(0.7196874321537099, abs=1e-12)
    assert stats["uniformity"] == pytest.approx(0.00798580301685895, abs=1e-15)
    assert [calc.thickness_stats(points)["mean"] for points in THICKNESS] == pytest.approx(
        [39.445, 37.721666666666664, 35.88166666666666, 34.058, 33.2, 30.786], abs=1e-12)
    fit = golden_fit()
    for key, expected in {"slope": 2557.081289196303, "intercept": -1.4481086316057628,
                          "r_squared": 0.9999078779453596, "rmse": 0.02876682222570821,
                          "max_abs_residual": 0.046055995212547884}.items():
        assert fit[key] == pytest.approx(expected, abs=1e-10)
    assert fit["n"] == 5
    assert [fit["x_min"], fit["x_max"]] == pytest.approx([SOLIDS[-1], 0.016])
    assert [fit["y_min"], fit["y_max"]] == pytest.approx([30.786, 39.445])
    result = calc.solids_back_calculation(37.68, fit, 29422, 0.016, DILUENT)
    assert result["target_solids"] == pytest.approx(0.015301863416279515, abs=1e-15)
    assert result["addition_g"] == pytest.approx(1342.3577251624947, abs=1e-8)
    assert result["diluent_components"] == pytest.approx({"SolvB": 939.6504076137462, "SolvA": 402.7073175487484}, abs=1e-8)
    assert result["mass_after_g"] == pytest.approx(30764.357725162496, abs=1e-8)
    assert result["solids_after"] == pytest.approx(result["target_solids"], abs=1e-15)
    assert result["predicted_thickness"] == pytest.approx(37.68, abs=1e-12)
    assert not result["extrapolated"]


def test_concentrate_and_spike_adjustment_conserve_mass():
    addition = calc.concentrate_mass(1000, 0.01, 0.015, 0.05)
    assert (1000 * 0.01 + addition * 0.05) / (1000 + addition) == pytest.approx(0.015)
    with pytest.raises(calc.PilotCalculationError, match="母液固含"):
        calc.concentrate_mass(1000, 0.01, 0.015, 0.014)
    series = calc.spike_series(0.0005, 0.01, [
        {"delta": delta, "sample_mass_g": 80} for delta in [0, 0.02, 0.04, 0.06]
    ])
    assert [row["spike_g"] for row in series["points"]] == pytest.approx([0, 0.08, 0.16, 0.24])
    fit = calc.linear_fit(zip([0, 0.02, 0.04, 0.06], [30.2, 29.1, 28, 27.1]))
    result = calc.dose_back_calculation(28.5, fit, 29422, 0.0005, 0.01, 0.016, 0.01, golden_fit())
    expected_delta = (28.5 - fit["intercept"]) / fit["slope"]
    assert result["delta"] == pytest.approx(expected_delta)
    assert result["addition_g"] == pytest.approx(expected_delta * 29422 * 0.0005 / 0.01)
    assert result["solids_after"] == pytest.approx((29422 * 0.016 + result["addition_g"] * 0.01) / result["mass_after_g"])
    assert result["predicted_thickness"] < 39.445
    decrease = calc.dose_back_calculation(31, fit, 29422, 0.0005, 0.01)
    assert decrease["addition_g"] == 0
    assert "不能靠加它实现" in decrease["warnings"][0]


def test_yield_forward_reverse_and_balance():
    plan = calc.required_charge(8, 0.9172, samples_g=1500, holdup_g=200,
                                discard_ml=1000, residue_g=100)
    assert plan["mass_g"] == pytest.approx(8 * 3785 * 0.9172 + 1500 + 200 + 1000 * 0.9172 + 100)
    prediction = calc.yield_prediction(plan["mass_g"], 0.9172, samples_g=1500,
                                       holdup_g=200, discard_ml=1000, residue_g=100)
    assert prediction["bottles"] == 8
    assert prediction["remainder_g"] == pytest.approx(0, abs=1e-9)
    overage = calc.required_charge(8, 0.9172, overage=0.03)
    assert overage["mass_g"] == pytest.approx(overage["base_mass_g"] * 1.03)
    balance = calc.mass_balance(30000, 1342.36, samples_g=1000, discard_g=917.2,
                                product_g=8 * 3785 * 0.9172, holdup_g=200, residue_g=None)
    assert balance["unaccounted_g"] == pytest.approx(0)
    assert balance["yield_fraction"] == pytest.approx(8 * 3785 * 0.9172 / 31342.36)


def test_pour_sums_and_solvent_first_powder_target():
    pours = [{"id": "p1", "method": "difference", "before_g": 1500.1, "after_g": 1000.3},
             {"id": "p2", "method": "direct", "mass_g": 445.5},
             {"id": "p3", "method": "direct", "mass_g": 99, "deleted_at": "2026-10-20"}]
    assert calc.sum_pours(pours) == pytest.approx(945.3)
    powder = calc.premix_powder_target(calc.sum_pours(pours), .01)
    assert powder == pytest.approx(9.548484848484849)
    assert powder / (powder + calc.sum_pours(pours)) == pytest.approx(.01)
    assert [pour["id"] for pour in pours] == ["p1", "p2", "p3"]
    assert calc.pour_mass({"method": "difference", "gross_before_g": 500, "gross_after_g": 100}) == 400
    with pytest.raises(calc.PilotCalculationError, match="倒后毛重"):
        calc.pour_mass({"method": "difference", "before_g": 10, "after_g": 20})
    with pytest.raises(calc.PilotCalculationError, match="小于 100%"):
        calc.premix_powder_target(100, 1)


def test_golden_proportional_replan_and_repeat_from_original_basis():
    sources = ["S100", "PAG-A", "PAG-B", "SolvB", "SolvA"]
    masses = [4882, 923, 923, 14415, 8856]
    lines = [{"id": source, "source_id": source, "planned_mass_g": mass,
              "source_composition": {"solid_fraction": solid}}
             for source, mass, solid in zip(sources, masses, [.1, .01, .01, 0, 0])]
    result = calc.proportional_replan(lines, {"PAG-A": 950, "S100": {"mass_g": 4882, "completed": True}},
                                      vessel_limit_g=30500)
    factor = 950 / 923
    assert result["factor"] == pytest.approx(1.0292524377031418)
    assert result["basis_line_id"] == result["basis_line"] == "PAG-A"
    assert result["targets"] == pytest.approx(dict(zip(sources, [5024.810400866739, 950, 950,
                                                               14836.67388949079, 9115.060671722643])))
    assert result["total_mass_g"] == pytest.approx(30876.544962080174)
    assert result["theoretical_solids"] == pytest.approx((4882 * .1 + 1846 * .01) / 29999)
    assert any("S100" in warning and "还需要再倒" in warning for warning in result["warnings"])
    assert any("釜最大装载量" in warning for warning in result["warnings"])
    assert result["targets"]["SolvB"] / result["targets"]["SolvA"] == pytest.approx(14415 / 8856)
    # Later pours always compare with the initial design; adoption does not compound f.
    repeated = calc.proportional_replan(lines, {"PAG-A": 950, "PAG-B": 960})
    assert repeated["factor"] == pytest.approx(960 / 923)
    assert repeated["basis_line"] == "PAG-B"
    assert repeated["total_mass_g"] == pytest.approx(29999 * 960 / 923)
    under = calc.proportional_replan(lines, {"PAG-A": 900})
    assert under["factor"] == 1
    assert next(r for r in under["lines"] if r["id"] == "PAG-A")["remaining_g"] == 23
    manual = calc.proportional_replan(lines, {"PAG-A": 950}, manual_factor=1.05, note="按容器留余量")
    assert manual["factor"] == 1.05 and manual["manual_override"]
    with pytest.raises(calc.PilotCalculationError, match="不能低于"):
        calc.proportional_replan(lines, {"PAG-A": 950}, manual_factor=1.01, note="调整")
    with pytest.raises(calc.PilotCalculationError, match="填写原因"):
        calc.proportional_replan(lines, {}, manual_factor=1.05)


def test_replan_keeps_full_precision_and_warns_only_on_shown_gap():
    lines = [{"id": "PAG", "source_id": "PAG", "planned_mass_g": 150.12348,
              "source_composition": {"solid_fraction": .01}},
             {"id": "SolvB", "source_id": "SolvB", "planned_mass_g": 16753.7,
              "source_composition": {"solid_fraction": 0}}]
    # Both pours match the shown targets (150.1235 g and 16,753.7 g) but stay slightly short.
    result = calc.proportional_replan(lines, {"PAG": {"mass_g": 150.12346, "completed": True},
                                              "SolvB": {"mass_g": 16753.66, "completed": True}})
    rows = {r["id"]: r for r in result["lines"]}
    assert rows["PAG"]["remaining_g"] == pytest.approx(0.00002, abs=1e-12)
    assert rows["SolvB"]["remaining_g"] == pytest.approx(0.04)
    assert not result["warnings"]
    short = calc.proportional_replan(lines, {"PAG": {"mass_g": 150.1, "completed": True}})
    assert any("还需要再倒 0.0235 g" in w for w in short["warnings"])
    assert [calc.mass_decimals(v) for v in (219.99, 220, 4999.99, 5000)] == [4, 2, 2, 1]


def test_replan_actual_premix_concentration_preserves_solids_and_solvent_ratios():
    from copy import deepcopy
    from lab.sci.vendor.formulation.formula_core import calculate_formula

    registry = {"materials": {
        "R": {"solid_fraction": .1, "solvent_fraction": .9, "solvents": {"S": .9}},
        "A": {"solid_fraction": 1, "solvent_fraction": 0},
        "S": {"solid_fraction": 0, "solvent_fraction": 1, "solvents": {"S": 1}},
        "T": {"solid_fraction": 0, "solvent_fraction": 1, "solvents": {"T": 1}}},
        "premixes": {"A1": {"components": [{"source_type": "material", "source_id": "A", "fraction": .01},
                                                {"source_type": "material", "source_id": "S", "fraction": .99}]}}}
    formula = {"total_mass_g": 1000, "solid_fraction": .02,
               "resins": [{"id": "r", "source_type": "material", "source_id": "R", "parts": 100}],
               "additives": [{"id": "a", "source_type": "premix", "source_id": "A1", "percent": 10}],
               "solvents": [{"id": "s", "source_id": "S", "parts": 70},
                            {"id": "t", "source_id": "T", "parts": 30}]}
    design = calculate_formula(formula, registry)
    actual_registry = deepcopy(registry)
    actual_registry["premixes"]["A1"]["components"][0]["fraction"] = .02
    actual_registry["premixes"]["A1"]["components"][1]["fraction"] = .98
    corrected = calculate_formula(formula, actual_registry)
    assert design["ok"] and corrected["ok"]
    base_targets = {row["id"]: row["planned_mass_g"] for row in corrected["lines"]}
    original_targets = {row["id"]: row["planned_mass_g"] for row in design["lines"]}
    assert base_targets["a"] == pytest.approx(original_targets["a"] / 2)
    assert base_targets["s"] - original_targets["s"] == pytest.approx(original_targets["a"] / 2)
    lines = [dict(row, planned_mass_g=original_targets[row["id"]]) for row in corrected["lines"]]
    result = calc.proportional_replan(lines, {"a": base_targets["a"] * 1.1}, base_targets)
    assert result["factor"] == pytest.approx(1.1)
    assert result["total_mass_g"] == pytest.approx(1100)
    assert result["theoretical_solids"] == pytest.approx(.02)
    amounts = result["targets"]
    resin_solid = amounts["r"] * .1
    additive_solid = amounts["a"] * .02
    solvent_s = amounts["r"] * .9 + amounts["a"] * .98 + amounts["s"]
    assert additive_solid / resin_solid == pytest.approx(.1)
    assert solvent_s / amounts["t"] == pytest.approx(.7 / .3)
    assert next(row for row in result["lines"] if row["id"] == "a")["original_target_g"] == original_targets["a"]


def lot_design(solvent_s=700, solvent_t=300):
    return [
        {"id": "r", "source_id": "R", "source_type": "material", "category": "resin", "order": 1,
         "design_mass_g": 1000, "design_solid_fraction": .1, "designed_solid_mass_g": 100,
         "lot": "R-095", "lot_solid_fraction": .095,
         "source_composition": {"solid_fraction": .1, "solvent_fraction": .9, "volatile_fraction": 0, "solvents": {"S": .9}}},
        {"id": "s", "source_id": "S", "source_type": "material", "category": "solvent", "order": 2,
         "design_mass_g": solvent_s, "design_solid_fraction": 0,
         "source_composition": {"solid_fraction": 0, "solvent_fraction": 1, "volatile_fraction": 0, "solvents": {"S": 1}}},
        {"id": "t", "source_id": "T", "source_type": "material", "category": "solvent", "order": 3,
         "design_mass_g": solvent_t, "design_solid_fraction": 0,
         "source_composition": {"solid_fraction": 0, "solvent_fraction": 1, "volatile_fraction": 0, "solvents": {"T": 1}}}]


def test_lot_correction_keeps_dry_mass_and_splits_solvent_change_70_30():
    from copy import deepcopy
    lines = lot_design()
    original = deepcopy(lines)
    result = calc.lot_corrected_plan(lines, 2000, {"S": .7, "T": .3})
    resin, solvent_s, solvent_t = result["lines"]
    delta = 100 / .095 - 1000
    assert resin["planned_mass_g"] == pytest.approx(1052.6315789473683)
    assert resin["solid_mass_g"] == pytest.approx(100)
    assert resin["source_composition"]["solvent_fraction"] == pytest.approx(.905)
    assert solvent_s["planned_mass_g"] == pytest.approx(700 - delta * .7)
    assert solvent_t["planned_mass_g"] == pytest.approx(300 - delta * .3)
    assert sum(row["planned_mass_g"] for row in result["lines"]) == pytest.approx(2000)
    assert result["solid_fraction"] == pytest.approx(.05)
    assert result["can_save"] and not result["warnings"]
    assert lines == original


def test_lot_correction_without_setting_balances_only_main_solvent():
    result = calc.lot_corrected_plan(lot_design(), 2000)
    assert result["lines"][1]["planned_mass_g"] == pytest.approx(700 - (100 / .095 - 1000))
    assert result["lines"][2]["planned_mass_g"] == 300


def test_lot_correction_negative_solvent_is_visible_and_never_clipped():
    result = calc.lot_corrected_plan(lot_design(35, 15), 1050, {"S": .7, "T": .3})
    assert not result["can_save"]
    assert all(row["planned_mass_g"] < 0 for row in result["lines"][1:])
    assert sum(row["planned_mass_g"] for row in result["lines"]) == pytest.approx(1050)
    assert result["solid_mass_g"] == pytest.approx(100)
    assert any("溶剂目标为负" in message for message in result["warnings"])


def test_lot_correction_repeated_calculation_retains_precision():
    first = calc.lot_corrected_plan(lot_design(), 2000, {"S": .7, "T": .3})
    second = calc.lot_corrected_plan(first["lines"], 2000, {"S": .7, "T": .3})
    for original, repeated in zip(first["lines"], second["lines"]):
        assert repeated["planned_mass_g"] == pytest.approx(original["planned_mass_g"], abs=1e-12)
    assert first["lines"][0]["planned_mass_g"] != round(first["lines"][0]["planned_mass_g"], 2)
