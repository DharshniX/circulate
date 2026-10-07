"""Run with:  python -m pytest -q"""
import pytest
from pydantic import ValidationError

from engine.engines import REGISTRY, engine_for
from engine.impact import DealImpactInput, EmissionFactor, compute
from engine.models import (Defect, FlatPiece, LinearStock, PerishableLot, PlanRequest, Route, Settings)
from engine.nesting import Placement, nest_identical_parts, verify_placements
from engine.pooling import fulfil
from engine.recovery import plan
from engine.scenarios import SCENARIOS, dataset


def run(key, demand_index=0, **changes):
    D = dataset(key)
    d = D["demands"][demand_index].model_copy(update=changes)
    return d, fulfil(d, D["sellers"], D["resources"], D["transport"], D["settings"])


# ---------- nesting core ----------
def test_full_sheet_yield_is_valid_and_below_area_bound():
    pl = nest_identical_parts(2500, 1250, 150, 100, kerf_mm=5, edge_margin_mm=0)
    assert verify_placements(2500, 1250, pl, 5, 0) == []
    assert 0 < len(pl) <= (2505 * 1255) // (155 * 105)


def test_cap_is_prefix_stable():
    assert nest_identical_parts(900, 700, 150, 100, max_parts=5) == nest_identical_parts(900, 700, 150, 100)[:5]


def test_rotation_off_changes_fit():
    assert nest_identical_parts(120, 400, 300, 100, kerf_mm=0, edge_margin_mm=0, allow_rotation=False) == []
    assert len(nest_identical_parts(120, 400, 300, 100, kerf_mm=0, edge_margin_mm=0)) == 1


def test_defects_are_never_cut_and_cost_yield():
    defect = (300, 200, 200, 200)
    clean = nest_identical_parts(900, 700, 150, 100)
    holed = nest_identical_parts(900, 700, 150, 100, defects=[defect])
    assert len(holed) < len(clean)
    assert verify_placements(900, 700, holed, 3, 5, [defect]) == []


def test_verifier_catches_overlap():
    assert verify_placements(500, 500, [Placement(0, 0, 100, 100, False), Placement(50, 50, 100, 100, False)], 0, 0)


# ---------- validation ----------
def test_invalid_inputs_rejected():
    with pytest.raises(ValidationError):
        FlatPiece(id="x", seller_id="A", material="mild_steel", length_mm=-5, width_mm=100, thickness_mm=2, grade="E250")
    with pytest.raises(ValidationError):
        FlatPiece(id="x", seller_id="A", material="mild_steel", length_mm=500, width_mm=500, thickness_mm=2, grade="E250",
                  defects=[Defect(x_mm=450, y_mm=0, length_mm=100, width_mm=50)])


def test_registry_has_three_engines():
    assert set(REGISTRY) == {"flat", "linear", "perishable"}
    with pytest.raises(ValueError):
        engine_for("textile")


# ---------- every scenario's first order works and is consistent ----------
@pytest.mark.parametrize("key", list(SCENARIOS))
def test_scenario_first_order_feasible_and_exact(key):
    d, r = run(key)
    b = r["best"]
    assert r["feasible"] and b["units"] == pytest.approx(d.quantity)
    used = [rid for s in b["per_seller"] for rid in s["resources_used"]]
    assert len(used) == len(set(used)), "a resource was used twice"
    assert sum(a["units"] for a in b["allocations"]) == pytest.approx(d.quantity)


@pytest.mark.parametrize("key", list(SCENARIOS))
def test_same_inputs_same_answer(key):
    assert run(key)[1] == run(key)[1]


# ---------- flat engine rules ----------
def test_steel_rejections_have_reasons():
    _, r = run("steel")
    reasons = {x["resource_id"]: x["reason"] for x in r["rejections"]}
    assert "thickness" in reasons["A13"] and "bent" in reasons["B6"] and "grade" in reasons["C7"]


def test_aluminium_order_rejects_stainless_and_wrong_alloy():
    _, r = run("sheets", 0)
    reasons = {x["resource_id"]: x["reason"] for x in r["rejections"]}
    assert "Stainless" in reasons["Ass1"] and "6061" in reasons["Aal3"]


def test_plywood_grain_unknown_is_rejected():
    _, r = run("sheets", 1)
    reasons = {x["resource_id"]: x["reason"] for x in r["rejections"]}
    assert "grain" in reasons["Ip3"]


def test_machine_bed_limit():
    _, r = run("steel", 0, machine_max_length_mm=1300, machine_max_width_mm=1300)
    reasons = {x["resource_id"]: x["reason"] for x in r["rejections"]}
    assert "machine bed" in reasons["A1"]


# ---------- linear engine rules ----------
def test_bar_cuts_count_and_remnant():
    eng = engine_for("linear")
    D = dataset("bars")
    d = D["demands"][0]
    bar = LinearStock(id="x", seller_id="G", material="mild_steel", profile="round bar 16", length_mm=2000,
                      mass_per_m_kg=1.58, grade="E250")
    s = Settings(kerf_mm=2, end_trim_mm=10)
    assert eng.capacity(bar, d, s) == 4  # (2000 - 20 + 2) // (450 + 2)
    t = eng.take(bar, d, 4, s)
    assert eng.verify(bar, d, t, s) == [] and t.detail["remnant_mm"] == pytest.approx(2000 - 10 - 4 * 452 - 10, abs=0.2)  # both end trims


def test_wrong_wall_thickness_rejected():
    _, r = run("bars", 1)
    reasons = {x["resource_id"]: x["reason"] for x in r["rejections"]}
    assert "3.2" in reasons["T6"]  # wrong wall thickness is caught


# ---------- perishable engine rules ----------
def test_food_safety_rules():
    _, r = run("food", 0)
    reasons = {x["resource_id"]: x["reason"] for x in r["rejections"]}
    assert "RUCO" in reasons["K3"]  # used cooking oil
    assert "FSSAI" in reasons["C1"]  # unlicensed seller
    assert "h left" in reasons["K2"]  # too little shelf life


def test_cooked_meals_only_to_ngo():
    _, r = run("food", 1)
    assert r["feasible"] and r["best"]["value"]["is_donation"]
    used = {rid for s in r["best"]["per_seller"] for rid in s["resources_used"]}
    assert "C2" not in used  # unlicensed donor excluded


def test_slow_pickup_vetoes_food():
    D = dataset("food")
    d = D["demands"][1]
    t = D["transport"].model_copy(update={"speed_kmph": 1.0})  # walking pace: food would spoil on the way
    r = fulfil(d, D["sellers"], D["resources"], t, D["settings"])
    assert r["feasible"] is False


# ---------- pooling honesty ----------
def test_infeasible_order_reports_shortfall():
    _, r = run("steel", 0, quantity=50000)
    assert r["feasible"] is False and r["shortfall"] == pytest.approx(50000 - r["total_capacity"])


def test_expensive_transport_makes_route_not_worth_it():
    D = dataset("steel")
    t = D["transport"].model_copy(update={"rate_multiplier": 20})
    r = fulfil(D["demands"][0], D["sellers"], D["resources"], t, D["settings"])
    assert r["feasible"] is False and "no more than salvage" in r["message"]


# ---------- recovery plan ----------
def _example(**kw):
    routes = [Route(id="R1", name="Manufacturer", kind="direct_buyer", value_to_seller=15000, p_close=0.58, window=5),
              Route(id="R2", name="Workshop", kind="direct_buyer", value_to_seller=12600, p_close=0.70, window=4),
              Route(id="R3", name="Stockist", kind="stockist", value_to_seller=11400, p_close=0.80, window=3)]
    return PlanRequest(routes=routes, salvage_value=9000, salvage_executable=True, deadline=10,
                       carry_cost_per_unit_time=20, **kw)


def test_plan_matches_hand_calculation():
    res = plan(_example())
    assert [s["route_id"] for s in res["best"]["steps"]] == ["R1", "R2"]
    assert res["best"]["ev"] == pytest.approx(13404.8, abs=0.1)


def test_rejection_replans():
    res = plan(_example(rejected_route_ids=["R1"], elapsed=5))
    assert "R1" not in [s["route_id"] for s in res["best"]["steps"]] and res["time_left"] == 5


def test_estimate_floor_is_flagged():
    assert any("estimate" in w for w in plan(_example().model_copy(update={"salvage_executable": False}))["warnings"])


# ---------- impact ----------
def _deal(**kw):
    base = dict(kind="flat", counterfactual="recycled", units=600, unit_name="parts", kg_in_units=141, kg_shipped=205,
                route_km=11, value_above_salvage=2300, buyer_utilisation=0.75)
    return DealImpactInput(**{**base, **kw})


def test_no_factor_no_co2_and_no_landfill_claim_for_steel():
    out = compute(_deal())
    m = {x["metric"]: x for x in out["metrics"]}
    assert m["Net emissions avoided"]["value"] is None and m["Landfill diverted"]["value"] == 0


def test_simulated_never_verified():
    ef = EmissionFactor(value=1.0, unit="kgCO2e/kg", source="TEST factor, not for real use")
    out = compute(_deal(status="delivered", evidence=["challan:1"], buyer_confirmed=True, ef_avoided=ef))
    assert out["label"] == "simulated"
    real = compute(_deal(is_simulated=False, status="delivered", evidence=["challan:1"], buyer_confirmed=True))
    assert real["label"] == "verified"


def test_food_landfill_only_when_recorded():
    assert {x["metric"]: x for x in compute(_deal(kind="perishable", counterfactual="disposed"))["metrics"]}["Landfill diverted"]["value"] == 0
    assert {x["metric"]: x for x in compute(_deal(kind="perishable", counterfactual="landfill"))["metrics"]}["Landfill diverted"]["value"] == 141
