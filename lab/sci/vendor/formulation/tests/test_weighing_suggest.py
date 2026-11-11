import pytest

from lab.sci.vendor.formulation.formula_core import calculate_formula
from lab.sci.vendor.formulation.weighing_suggest import suggest_weighing

REGISTRY = {
    "materials": {
        "RES": {"id": "RES", "solid_fraction": 0.5, "solvent_fraction": 0.5, "volatile_fraction": 0, "solvent_id": "SolvB"},
        "PAG": {"id": "PAG", "solid_fraction": 1, "solvent_fraction": 0, "volatile_fraction": 0},
        "SolvB": {"id": "SolvB", "solid_fraction": 0, "solvent_fraction": 1, "volatile_fraction": 0, "solvent_id": "SolvB"},
    },
    "premixes": {"PAG-1%": {"id": "PAG-1%", "components": [
        {"source_type": "material", "source_id": "PAG", "fraction": 0.01},
        {"source_type": "material", "source_id": "SolvB", "fraction": 0.99}]}},
}


def formula(percent=0.01):
    return {"total_mass_g": 100, "solid_fraction": 0.1,
            "resins": [{"id": "r", "source_type": "material", "source_id": "RES", "parts": 100}],
            "additives": [{"id": "a", "source_type": "material", "source_id": "PAG", "percent": percent, "basis": "solid"}],
            "solvents": [{"id": "s", "source_id": "SolvB", "parts": 1}]}


def test_suggestions_weigh_masses_and_recommendation():
    f = formula()
    pag = calculate_formula(f, REGISTRY)["lines"][1]["planned_mass_g"]
    assert pag == pytest.approx(10/1.0001*0.0001)
    out = suggest_weighing(f, REGISTRY)
    line = out["lines"][0]
    assert out["limit_g"] == 0.05 and line["row_id"] == "a"
    assert line["existing"][0]["id"] == "PAG-1%"
    assert line["existing"][0]["weigh_mass_g"] == pytest.approx(pag/0.01)
    by_pct = {p["percent"]: p for p in line["proposals"]}
    assert by_pct[10]["weigh_mass_g"] == pytest.approx(pag/0.1) and not by_pct[10]["enough"]
    assert by_pct[1]["weigh_mass_g"] == pytest.approx(pag/0.01) and by_pct[1]["recommended"]
    assert by_pct[0.1]["weigh_mass_g"] == pytest.approx(pag/0.001) and not by_pct[0.1]["recommended"]
    assert by_pct[1]["id"] == "PAG-1%-SolvB" and all(p["ok"] for p in line["proposals"])


def test_no_suggestion_when_weighable_or_limit_changed():
    assert suggest_weighing(formula(1), REGISTRY)["lines"] == []
    assert suggest_weighing(formula(1), REGISTRY, min_weigh_g=1)["lines"][0]["row_id"] == "a"
