"""Hand-computed flexible design balances, independent of product defaults."""
from copy import deepcopy

import pytest

from lab.sci.vendor.formulation.formula_core import calculate_formula, formula_fingerprint


def worked_formula():
    registry = {"materials": {
        "R50": {"solid_fraction": .5, "solvent_fraction": .5, "solvents": {"S": .5}},
        "R25": {"solid_fraction": .25, "solvent_fraction": .75, "solvents": {"S": .75}},
        "A": {"solid_fraction": 1, "solvent_fraction": 0, "molar_mass_g_mol": 200},
        "S": {"solid_fraction": 0, "solvent_fraction": 1, "solvents": {"S": 1}},
        "T": {"solid_fraction": 0, "solvent_fraction": 1, "solvents": {"T": 1}}}}
    formula = {"total_mass_g": 100, "solid_fraction": .1,
               "resins": [{"id": "r", "source_type": "material", "source_id": "R50", "parts": 100}],
               "additives": [], "solvents": [{"id": "s", "source_id": "S", "parts": 1}]}
    return formula, registry


def assert_balance(result, total, resin):
    assert result["ok"], result["errors"]
    assert result["totals"]["total_mass_g"] == pytest.approx(total)
    assert result["totals"]["resin_solid_mass_g"] == pytest.approx(resin)
    assert result["checks"]["mass_delta_g"] == pytest.approx(0, abs=1e-9)
    return {line["id"]: line["planned_mass_g"] for line in result["lines"]}


def test_solution_parts_convert_to_solid_parts_with_fixed_dose():
    formula, registry = worked_formula()
    formula["resins"] = [dict(formula["resins"][0], parts=2, parts_basis="solution"),
                         {"id": "r2", "source_type": "material", "source_id": "R25", "parts": 2, "parts_basis": "solution"}]
    formula["additives"] = [{"id": "a", "source_type": "material", "source_id": "A", "percent": 1, "dose_basis": "fixed_g"}]
    # 10 g solids minus 1 g additive; equal solution masses: .5*x + .25*x = 9.
    masses = assert_balance(calculate_formula(formula, registry), 100, 9)
    assert masses == pytest.approx({"r": 12, "r2": 12, "a": 1, "s": 75})
    formula["resins"][0]["parts_basis"] = "solid"
    formula["resins"][1]["parts_basis"] = "solid"
    assert "RESIN_PARTS_SUM" in {e["code"] for e in calculate_formula(formula, registry)["errors"]}


def test_fixed_final_solvent_includes_carried_mass_and_ratio_remainder():
    formula, registry = worked_formula()
    formula["solvents"] = [{"id": "s", "source_id": "S", "solvent_mode": "fixed_g", "fixed_g": 30},
                           {"id": "t", "source_id": "T", "parts": 2}]
    # R50=20 g carries 10 g S; pool=90 g, final S=30, final T=60.
    result = calculate_formula(formula, registry)
    assert assert_balance(result, 100, 10) == pytest.approx({"r": 20, "s": 20, "t": 60})
    assert result["solvent_balance"][0]["final_mass_g"] == pytest.approx(30)


@pytest.mark.parametrize("fixed,ratio,code", [(91, True, "FIXED_SOLVENT_EXCEEDS_POOL"),
                                                (9, True, "CARRIED_EXCEEDS_FIXED"),
                                                (30, False, "SOLVENT_RATIO_REQUIRED")])
def test_fixed_solvent_errors(fixed, ratio, code):
    formula, registry = worked_formula()
    formula["solvents"] = [{"id": "s", "source_id": "S", "solvent_mode": "fixed_g", "fixed_g": fixed}]
    if ratio:
        formula["solvents"].append({"id": "t", "source_id": "T", "parts": 1})
    result = calculate_formula(formula, registry)
    assert not result["ok"]
    assert code in {e["code"] for e in result["errors"]}


def test_all_fixed_solvents_close_pool_without_ratio_rows():
    formula, registry = worked_formula()
    formula["solvents"] = [{"id": "s", "source_id": "S", "solvent_mode": "fixed_g", "fixed_g": 90}]
    assert assert_balance(calculate_formula(formula, registry), 100, 10) == pytest.approx({"r": 20, "s": 80})


@pytest.mark.parametrize("mode,fields", [
    ("resin_solid", {"resin_solid_g": 12}),
    ("source_amount", {"start_row_id": "r", "start_amount_g": 24}),
    ("source_amount", {"start_row_id": "a", "start_amount_g": 1.2}),
    ("source_amount", {"start_row_id": "s", "start_amount_g": 1288/11}),
])
def test_affine_starts_include_fixed_intercept_and_total_dose(mode, fields):
    formula, registry = worked_formula()
    formula.update(design_start=mode, total_mass_g="unused", **fields)
    formula["additives"] = [
        {"id": "a", "source_type": "material", "source_id": "A", "percent": 10},
        {"id": "fixed", "source_type": "material", "source_id": "A", "dose_basis": "fixed_g", "percent": 1},
        {"id": "ppm", "source_type": "material", "source_id": "A", "dose_basis": "total_ppm", "percent": 1000}]
    # .1 M = 1.1*12 + 1 + .001 M -> M = 14200/99.
    # Source sum is 24 + 1.2 + 1 + M/1000; solvent addition = 1288/11.
    total, resin = 14200/99, 12
    original = deepcopy(formula)
    result = calculate_formula(formula, registry)
    masses = assert_balance(result, total, resin)
    assert result["totals"]["derived_total_mass_g"] == pytest.approx(total)
    assert result["totals"]["design_start"] == mode
    if mode == "source_amount":
        assert masses[fields["start_row_id"]] == pytest.approx(fields["start_amount_g"])
    assert formula == original


def test_source_start_solvent_solves_signed_addition_before_validation():
    formula, registry = worked_formula()
    formula.update(design_start="source_amount", start_row_id="s", start_amount_g=20)
    formula["solvents"] = [{"id": "s", "source_id": "S", "solvent_mode": "fixed_g", "fixed_g": 30},
                           {"id": "t", "source_id": "T", "parts": 1}]
    assert assert_balance(calculate_formula(formula, registry), 100, 10) == pytest.approx({"r": 20, "s": 20, "t": 60})


@pytest.mark.parametrize("row,value", [("r", 0), ("a", 1)])
def test_source_start_zero_or_constant_row_cannot_determine_total(row, value):
    formula, registry = worked_formula()
    formula["resins"].append(dict(formula["resins"][0], id="zero", parts=0))
    formula["additives"] = [{"id": "a", "source_type": "material", "source_id": "A", "dose_basis": "fixed_g", "percent": value}]
    formula.update(design_start="source_amount", start_row_id="zero" if row == "r" else row, start_amount_g=12)
    result = calculate_formula(formula, registry)
    assert not result["ok"]
    assert result["errors"][0]["code"] == "START_CONDITION_UNSOLVABLE"


def test_start_unknown_row_and_impossible_negative_total():
    formula, registry = worked_formula()
    formula.update(design_start="source_amount", start_row_id="missing", start_amount_g=12)
    assert calculate_formula(formula, registry)["errors"][0]["code"] == "START_ROW_NOT_FOUND"
    formula.update(start_row_id="s", start_amount_g=40)
    formula["solvents"] = [{"id": "s", "source_id": "S", "solvent_mode": "fixed_g", "fixed_g": 30},
                           {"id": "t", "source_id": "T", "parts": 1}]
    assert calculate_formula(formula, registry)["errors"][0]["code"] == "START_CONDITION_UNSOLVABLE"


@pytest.mark.parametrize("mw_source", ["material", "equivalents", "frozen"])
def test_mmol_dose_uses_active_molar_mass(mw_source):
    formula, registry = worked_formula()
    formula["additives"] = [{"id": "a", "source_type": "material", "source_id": "A", "dose_basis": "mmol_per_g_resin", "percent": .05}]
    if mw_source == "equivalents":
        registry["materials"]["A"].pop("molar_mass_g_mol")
        formula["equivalents"] = {"rows": [{"row_id": "a", "molar_mass_g_mol": 200}]}
    elif mw_source == "frozen":
        formula["additives"][0]["frozen_source"] = dict(registry["materials"]["A"])
        registry["materials"]["A"]["molar_mass_g_mol"] = 999
    # .05 mmol/g * 200 g/mol / 1000 = .01 g/g; R=10/1.01.
    resin = 1000/101
    masses = assert_balance(calculate_formula(formula, registry), 100, resin)
    assert masses["a"] == pytest.approx(10/101)
    assert masses["r"] == pytest.approx(2000/101)


def test_mmol_premix_with_solution_parts_fixed_solvent_and_resin_start():
    formula, registry = worked_formula()
    registry["premixes"] = {"A10": {"components": [{"source_id": "A", "fraction": .1}, {"source_id": "S", "fraction": .9}]}}
    formula.update(design_start="resin_solid", resin_solid_g=9)
    formula["resins"] = [dict(formula["resins"][0], parts=1, parts_basis="solution"),
                         {"id": "r2", "source_type": "material", "source_id": "R25", "parts": 1, "parts_basis": "solution"}]
    formula["additives"] = [{"id": "a", "source_type": "premix", "source_id": "A10", "dose_basis": "mmol_per_g_resin", "percent": .05}]
    formula["equivalents"] = {"rows": [{"row_id": "a", "molar_mass_g_mol": 200}]}
    formula["solvents"] = [{"id": "s", "source_id": "S", "solvent_mode": "fixed_g", "fixed_g": 30},
                           {"id": "t", "source_id": "T", "parts": 1}]
    # R=9, additive active=.09 -> source=.9; M=90.9. S carry=6+9+.81=15.81.
    masses = assert_balance(calculate_formula(formula, registry), 90.9, 9)
    assert masses == pytest.approx({"r": 12, "r2": 12, "a": .9, "s": 14.19, "t": 51.81})


def test_missing_molar_mass_names_source_and_does_not_use_other_source_config():
    formula, registry = worked_formula()
    registry["materials"]["A"].pop("molar_mass_g_mol")
    formula["additives"] = [{"id": "a", "source_type": "material", "source_id": "A", "dose_basis": "mmol_per_g_resin", "percent": .05}]
    formula["equivalents"] = {"rows": [{"row_id": "a", "source_id": "other", "molar_mass_g_mol": 200}]}
    result = calculate_formula(formula, registry)
    assert not result["ok"]
    assert result["errors"][0]["code"] == "MOLAR_MASS_REQUIRED"
    assert "A" in result["errors"][0]["message"]


def test_default_new_fields_keep_legacy_results_and_fingerprint():
    formula, registry = worked_formula()
    original = deepcopy(formula)
    baseline = calculate_formula(formula, registry)
    formula.update(design_start="total")
    formula["resins"][0]["parts_basis"] = "solid"
    formula["solvents"][0]["solvent_mode"] = "ratio"
    assert calculate_formula(formula, registry) == baseline
    assert formula_fingerprint(formula) == formula_fingerprint(original)
    formula["solvents"][0].update(solvent_mode="fixed_g", fixed_g=90)
    assert formula_fingerprint(formula) != formula_fingerprint(original)


@pytest.mark.parametrize("start,fields,resin,total", [
    ("total", {}, 10/1.03, 100),
    ("resin_solid", {"resin_solid_g": 10}, 10, 103),
    ("source_amount", {"start_row_id": "r", "start_amount_g": 20}, 10, 103),
    ("source_amount", {"start_row_id": "s", "start_amount_g": 20}, 10, 103),
])
def test_equivalent_inverse_mmol_keeps_start_and_fixed_solvent(start, fields, resin, total):
    from lab.sci.vendor.formulation.equivalents import suggest_equivalent_percent
    from test_dose_basis import confirm_species

    formula, registry = worked_formula()
    formula.update(design_start=start, **fields)
    formula["additives"] = [{"id": "a", "source_type": "material", "source_id": "A", "dose_basis": "mmol_per_g_resin", "percent": .05},
                             {"id": "b", "source_type": "material", "source_id": "A", "dose_basis": "mmol_per_g_resin", "percent": 0}]
    formula["solvents"] = [{"id": "s", "source_id": "S", "solvent_mode": "fixed_g", "fixed_g": 30},
                           {"id": "t", "source_id": "T", "parts": 1}]
    formula["equivalents"] = {"mode": "molar", "reference_row_id": "a", "rows": [
        {"row_id": rid, "fraction_mode": "explicit", "active_fraction": 1, "molar_mass_g_mol": 200}
        for rid in ("a", "b")]}
    confirm_species(formula, registry)
    proposed = suggest_equivalent_percent(formula, registry, "b", 2)
    assert proposed["ok"], proposed
    assert proposed["suggested_percent"] == pytest.approx(.1)
    formula["additives"][1]["percent"] = proposed["suggested_percent"]
    masses = assert_balance(calculate_formula(formula, registry), total, resin)
    assert masses["b"] == pytest.approx(.02*resin)


def test_chained_mmol_equivalent_doses_keep_resin_start():
    from lab.sci.vendor.formulation.experiment_service import apply_series_row
    from test_dose_basis import confirm_species

    formula, registry = worked_formula()
    formula.update(design_start="resin_solid", resin_solid_g=10)
    formula["additives"] = [{"id": rid, "source_type": "material", "source_id": "A", "dose_basis": "mmol_per_g_resin", "percent": .05 if rid == "a" else 0}
                             for rid in ("a", "b", "c")]
    formula["equivalents"] = {"mode": "molar", "reference_row_id": "a", "rows": [
        {"row_id": rid, "fraction_mode": "explicit", "active_fraction": 1, "molar_mass_g_mol": 200}
        for rid in ("a", "b", "c")]}
    confirm_species(formula, registry)
    variables = [{"key": "equivalent:b", "equivalent": {"target_row_id": "b", "reference_row_id": "a", "mode": "molar"}},
                 {"key": "equivalent:c", "equivalent": {"target_row_id": "c", "reference_row_id": "b", "mode": "molar"}}]
    candidate = apply_series_row(formula, {"values": {"equivalent:b": 2, "equivalent:c": 3}}, variables, registry=registry)
    masses = assert_balance(calculate_formula(candidate, registry), 109, 10)
    assert [row["percent"] for row in candidate["additives"]] == pytest.approx([.05, .1, .3])
    assert [masses[rid] for rid in ("a", "b", "c")] == pytest.approx([.1, .2, .6])
