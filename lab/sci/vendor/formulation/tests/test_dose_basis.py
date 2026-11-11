"""Hand-worked additive-dose balances; no product-specific assumptions."""
from copy import deepcopy

import pytest

from lab.sci.vendor.formulation.formula_core import calculate_formula, formula_fingerprint, plan_premix_preparation


def worked_formula():
    registry = {"materials": {
        "R": {"solid_fraction": .5, "solvent_fraction": .5, "solvents": {"S": .5}},
        "A": {"solid_fraction": 1, "solvent_fraction": 0},
        "V": {"solid_fraction": .2, "volatile_fraction": .3, "solvent_fraction": .5, "solvents": {"S": .5}},
        "S": {"solid_fraction": 0, "solvent_fraction": 1, "solvents": {"S": 1}}},
        "premixes": {"A50": {"components": [{"source_id": "A", "fraction": .5},
                                                {"source_id": "S", "fraction": .5}]}}}
    formula = {"total_mass_g": 100, "solid_fraction": .1,
               "resins": [{"id": "r", "source_type": "material", "source_id": "R", "parts": 100}],
               "additives": [{"id": "a", "source_type": "material", "source_id": "A", "basis": "solid", "percent": 10}],
               "solvents": [{"id": "s", "source_id": "S", "parts": 100}]}
    return formula, registry


@pytest.mark.parametrize("basis,value,resin,additive", [
    ("resin_solid_pct", 10, 100/11, 10/11),
    ("total_solid_pct", 1, 9.9, .1),
    ("total_pct", .1, 9.9, .1),
    ("total_ppm", 1000, 9.9, .1),
    ("fixed_g", .1, 9.9, .1),
])
def test_each_dose_basis_hand_balance(basis, value, resin, additive):
    formula, registry = worked_formula()
    formula["additives"][0].update(dose_basis=basis, percent=value)
    result = calculate_formula(formula, registry)
    assert result["ok"], result["errors"]
    lines = {line["id"]: line for line in result["lines"]}
    assert lines["r"]["planned_mass_g"] == pytest.approx(resin/.5)
    assert lines["a"]["planned_mass_g"] == pytest.approx(additive)
    assert lines["s"]["planned_mass_g"] == pytest.approx(100-resin/.5-additive)
    assert lines["a"]["dose_basis"] == basis
    assert lines["a"]["dose_value"] == value
    assert lines["r"]["effective_mass_g"] == pytest.approx(resin)
    assert lines["a"]["effective_mass_g"] == pytest.approx(additive)
    assert lines["s"]["effective_mass_g"] == 0
    assert result["totals"]["effective_mass_g"] == pytest.approx(10)
    assert result["totals"]["actual_mass_g"] == pytest.approx(100)
    assert result["totals"]["solid_mass_g"] == pytest.approx(10)


def test_mixed_five_bases_share_one_resin_solve():
    formula, registry = worked_formula()
    original = formula["additives"][0]
    formula["additives"] = [dict(original, id=str(i), dose_basis=basis, percent=value)
                            for i, (basis, value) in enumerate([
                                ("resin_solid_pct", 10), ("total_solid_pct", 1),
                                ("total_pct", .1), ("total_ppm", 1000), ("fixed_g", .1)])]
    result = calculate_formula(formula, registry)
    assert result["ok"], result["errors"]
    assert result["totals"]["resin_solid_mass_g"] == pytest.approx(96/11)
    doses = [line["planned_mass_g"] for line in result["lines"] if line["category"] == "additive"]
    assert doses == pytest.approx([9.6/11, .1, .1, .1, .1])
    assert result["totals"]["solid_mass_g"] == pytest.approx(10)
    assert result["totals"]["actual_mass_g"] == pytest.approx(100)


def test_volatile_basis_counts_both_effective_fractions_and_solvent_carry():
    formula, registry = worked_formula()
    formula["additives"][0].update(source_id="V", basis="volatile", dose_basis="fixed_g", percent=.3)
    result = calculate_formula(formula, registry)
    assert result["ok"], result["errors"]
    lines = {line["id"]: line for line in result["lines"]}
    assert lines["a"]["planned_mass_g"] == pytest.approx(1)
    assert lines["a"]["effective_mass_g"] == pytest.approx(.5)
    assert lines["a"]["solid_mass_g"] == pytest.approx(.2)
    assert lines["a"]["volatile_mass_g"] == pytest.approx(.3)
    assert lines["r"]["planned_mass_g"] == pytest.approx(19.6)
    assert lines["s"]["planned_mass_g"] == pytest.approx(79.4)
    assert result["totals"]["effective_mass_g"] == pytest.approx(10.3)
    assert result["totals"]["solvent_mass_g"] == pytest.approx(89.7)


@pytest.mark.parametrize("value", [10, 11])
def test_fixed_solids_leave_no_positive_resin(value):
    formula, registry = worked_formula()
    formula["additives"][0].update(dose_basis="fixed_g", percent=value)
    result = calculate_formula(formula, registry)
    assert not result["ok"]
    assert result["errors"][0]["message"] == "按总体系 / 固定量设计的添加剂固体已超过目标固体量"
    assert not result["lines"]


def test_legacy_missing_unknown_and_explicit_default_are_identical():
    formula, registry = worked_formula()
    original = deepcopy(formula)
    legacy = calculate_formula(formula, registry)
    assert legacy["ok"]
    assert legacy["totals"]["resin_solid_mass_g"] == 10/1.1
    assert legacy["lines"][1]["planned_mass_g"] == (10/1.1)*10/100
    for basis in ("resin_solid_pct", "unknown", None):
        formula["additives"][0]["dose_basis"] = basis
        assert calculate_formula(formula, registry) == legacy
        assert formula_fingerprint(formula) == formula_fingerprint(original)
    formula["additives"][0]["dose_basis"] = "total_ppm"
    assert formula_fingerprint(formula) != formula_fingerprint(original)


def test_fixed_premix_demand_uses_calculated_source_mass():
    formula, registry = worked_formula()
    formula["additives"][0].update(source_type="premix", source_id="A50", dose_basis="total_ppm", percent=1000)
    result = calculate_formula(formula, registry)
    assert result["ok"], result["errors"]
    preparation = plan_premix_preparation([result], registry)
    assert preparation["ok"], preparation["errors"]
    assert preparation["plans"][0]["demand_mass_g"] == pytest.approx(.2)
    masses = {line["source_id"]: line["planned_mass_g"] for line in preparation["raw_materials"]}
    assert masses == pytest.approx({"A": .1, "S": .1})


BASES = ("resin_solid_pct", "total_solid_pct", "total_pct", "total_ppm", "fixed_g")


def confirm_species(formula, registry):
    from lab.sci.vendor.formulation.equivalents import equivalent_options

    options = {item["row_id"]: item for item in equivalent_options(formula, registry)["rows"]}
    for config in formula["equivalents"]["rows"]:
        option = options[config["row_id"]]
        config.update(source_type=option["source_type"], source_id=option["source_id"],
                      source_signature=option["source_signature"], species_name=option["source_id"], species_confirmed=True)


@pytest.mark.parametrize("reference_basis", BASES)
@pytest.mark.parametrize("target_basis", BASES)
@pytest.mark.parametrize("ratio", [0, 2])
def test_equivalent_inverse_all_basis_pairs(reference_basis, target_basis, ratio):
    from lab.sci.vendor.formulation.equivalents import suggest_equivalent_percent

    formula, registry = worked_formula()
    reference_values = dict(zip(BASES, (10, 10, 1, 10000, 1)))
    formula["additives"][0].update(dose_basis=reference_basis, percent=reference_values[reference_basis])
    formula["additives"].append(dict(formula["additives"][0], id="b", dose_basis=target_basis, percent=0))
    formula["equivalents"] = {"mode": "molar", "reference_row_id": "a", "rows": [
        {"row_id": rid, "fraction_mode": "explicit", "active_fraction": 1, "molar_mass_g_mol": 100}
        for rid in ("a", "b")]}
    confirm_species(formula, registry)
    original = deepcopy(formula)
    result = suggest_equivalent_percent(formula, registry, "b", ratio)
    assert result["ok"], result
    # Relative reference: R + .1R + ratio*.1R = 10. Fixed reference: R+1+ratio=10.
    resin = 10/(1.1+.1*ratio) if reference_basis == "resin_solid_pct" else 9-ratio
    active = ratio*.1*resin if reference_basis == "resin_solid_pct" else ratio
    expected_values = dict(zip(BASES, (active/resin*100, active/10*100,
                                            active, active*10000, active)))
    assert result["suggested_percent"] == pytest.approx(expected_values[target_basis])
    assert result["planned_mass_g"] == pytest.approx(active)
    assert result["validated_ratio"] == pytest.approx(ratio)
    assert formula == original


@pytest.mark.parametrize("mode,active", [("molar", 2), ("functional", .5)])
def test_equivalent_inverse_volatile_fraction_purity_and_functionality(mode, active):
    from lab.sci.vendor.formulation.equivalents import suggest_equivalent_percent

    formula, registry = worked_formula()
    formula["additives"][0].update(dose_basis="fixed_g", percent=1)
    formula["additives"].append({"id": "b", "source_type": "material", "source_id": "V",
                                  "basis": "volatile", "dose_basis": "total_ppm", "percent": 0})
    formula["equivalents"] = {"mode": mode, "reference_row_id": "a", "rows": [
        {"row_id": "a", "fraction_mode": "explicit", "active_fraction": 1,
         "molar_mass_g_mol": 100, "functionality": 1},
        {"row_id": "b", "fraction_mode": "explicit", "active_fraction": .3,
         "molar_mass_g_mol": 200, "functionality": 4, "purity_applied": True, "purity_fraction": .5}]}
    confirm_species(formula, registry)
    result = suggest_equivalent_percent(formula, registry, "b", .5)
    assert result["ok"], result
    assert result["suggested_percent"] == pytest.approx(active*10000)
    assert result["planned_mass_g"] == pytest.approx(active/.3)
    assert result["validated_ratio"] == pytest.approx(.5)


@pytest.mark.parametrize("chained", [False, True])
def test_joint_equivalent_doe_preserves_every_mixed_basis_constraint(chained):
    from lab.sci.vendor.formulation.experiment_service import apply_series_row

    formula, registry = worked_formula()
    formula["additives"][0].update(dose_basis="resin_solid_pct" if chained else "fixed_g", percent=10 if chained else 1)
    for rid in ("b", "c"):
        formula["additives"].append(dict(formula["additives"][0], id=rid,
                                          dose_basis="fixed_g" if chained else "resin_solid_pct", percent=0))
    formula["equivalents"] = {"mode": "molar", "reference_row_id": "a", "rows": [
        {"row_id": rid, "fraction_mode": "explicit", "active_fraction": 1, "molar_mass_g_mol": 100}
        for rid in ("a", "b", "c")]}
    variables = [
        {"key": "equivalent:b", "equivalent": {"target_row_id": "b", "reference_row_id": "a", "mode": "molar"}},
        {"key": "equivalent:c", "equivalent": {"target_row_id": "c", "reference_row_id": "b" if chained else "a", "mode": "molar"}}]
    confirm_species(formula, registry)
    result = apply_series_row(formula, {"values": {"equivalent:b": 2, "equivalent:c": 3}}, variables, registry=registry)
    calculation = calculate_formula(result, registry)
    assert calculation["ok"], calculation["errors"]
    assert calculation["totals"]["resin_solid_mass_g"] == pytest.approx(100/19 if chained else 4)
    masses = {line["id"]: line["planned_mass_g"] for line in calculation["lines"]}
    assert masses["b"] == pytest.approx(20/19 if chained else 2)
    assert masses["c"] == pytest.approx(60/19 if chained else 3)
    assert [item["ratio"] for item in result["equivalent_design_results"]] == pytest.approx([2, 3])


@pytest.mark.parametrize("basis,value,active", [
    ("resin_solid_pct", 10, 20/11), ("total_solid_pct", 1, .2),
    ("total_pct", .1, .2), ("total_ppm", 1000, .2), ("fixed_g", .1, .1)])
def test_series_dose_value_keeps_basis_when_batch_mass_changes(basis, value, active):
    from lab.sci.vendor.formulation.experiment_service import apply_series_row

    formula, registry = worked_formula()
    formula["additives"][0]["dose_basis"] = basis
    result = apply_series_row(formula,
        {"values": {"total_mass_g": 200, "additives.0.percent": value}},
        [{"key": "total_mass_g"}, {"key": "additives.0.percent"}], registry=registry)
    calculation = calculate_formula(result, registry)
    assert calculation["ok"], calculation["errors"]
    line = next(line for line in calculation["lines"] if line["id"] == "a")
    assert line["solid_mass_g"] == pytest.approx(active)
    assert line["dose_basis"] == basis
