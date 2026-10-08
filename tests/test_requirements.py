"""Buyers post requirements: validation, Find it now, standing orders in Sold before it's cut, reservations."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main
from engine import scenarios as SC

c = TestClient(main.app)
TOMORROW = (date.today() + timedelta(days=1)).isoformat()
SHEET = {"part_name": "bracket", "material": "mild_steel", "length_mm": 150, "width_mm": 100, "thickness_mm": 2,
         "accepted_grades": ["E250"], "accepted_conditions": ["new", "good", "surface_rust"], "quantity": 50,
         "new_price_per_kg": 62, "buyer_name": "Test fabricator"}
SPACER = {**SHEET, "part_name": "spacer", "length_mm": 120, "width_mm": 90, "thickness_mm": 6, "quantity": 6, "new_price_per_kg": 200}
BAR = {"part_name": "rail post", "profile": "round bar 16", "mass_per_m_kg": 1.58, "material": "mild_steel", "cut_length_mm": 450,
       "quantity": 4, "accepted_grades": ["E250"], "new_price_per_kg": 64, "buyer_name": "Gate maker"}
FOOD = {"categories": ["vegetables"], "quantity": 10, "storage_available": ["ambient", "chilled"], "min_hours_left_on_arrival": 6,
        "buyer_type": "hostel", "normal_price_per_kg": 38, "buyer_fssai_licensed": True, "buyer_name": "Hostel mess Z"}


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")


def post(lane, kind, fields, **kw):
    return c.post("/api/requirements", json={"lane": lane, "kind": kind, "fields": fields, **kw})


def status(rid):
    return next(r for r in c.get("/api/requirements").json()["requirements"] if r["id"] == rid)


# ---------------- validation, both lanes ----------------
def test_valid_durable_requirement_is_saved_near_the_cluster_centre():
    r = post("durable", "flat", SHEET, needed_by=TOMORROW).json()
    assert r["demand_id"] == f"R{r['id']}" and r["status"] == "open" and r["scenario"] == "steel" and r["needed_by"] == TOMORROW
    sellers = SC.dataset("steel")["sellers"]
    assert r["demand"]["x_km"] == pytest.approx(sum(s.x_km for s in sellers) / len(sellers), abs=0.06)
    bar = post("durable", "linear", BAR).json()
    assert bar["scenario"] == "bars" and bar["demand"]["cut_length_mm"] == 450


def test_invalid_durable_requirement_shows_errors_per_field():
    bad = {**SHEET, "material": "brass", "length_mm": -5, "accepted_grades": [], "quantity": 0, "part_name": "", "buyer_name": ""}
    r = post("durable", "flat", bad, needed_by="2020-01-01")
    assert r.status_code == 422
    e = r.json()["detail"]["errors"]
    assert "Unknown material 'brass'" in e["material"] and e["length_mm"] == "Must be more than 0."
    assert e["accepted_grades"] == "Choose at least one." and e["quantity"] == "Must be more than 0."
    assert e["part_name"] == "This is needed." and e["buyer_name"] == "This is needed." and "later date" in e["needed_by"]
    bar = post("durable", "linear", {**BAR, "cut_length_mm": 0}).json()["detail"]["errors"]
    assert bar["cut_length_mm"] == "Must be more than 0."
    assert post("durable", "linear", BAR, standing=True).status_code == 422  # standing orders are for sheet parts only
    assert c.get("/api/requirements").json()["requirements"] == []


def test_valid_and_invalid_fresh_requirement():
    ok = post("fresh", "perishable", FOOD).json()
    assert ok["scenario"] == "food" and ok["demand"]["buyer_type"] == "food_business"
    bank = post("fresh", "perishable", {**FOOD, "buyer_type": "food_bank", "normal_price_per_kg": 50, "buyer_name": "Shelter"}).json()
    assert bank["demand"]["buyer_type"] == "ngo" and bank["demand"]["normal_price_per_kg"] == 0  # a donation is never charged
    e = post("fresh", "perishable", {**FOOD, "categories": [], "quantity": -2, "storage_available": ["fridge"], "buyer_type": "x"}).json()["detail"]["errors"]
    assert set(e) >= {"categories", "quantity", "storage_available", "buyer_type"}
    assert post("durable", "perishable", FOOD).status_code == 422  # wrong lane


# ---------------- Find it now ----------------
def test_find_it_now_runs_the_same_matching_and_accept_marks_it_matched():
    r = post("durable", "flat", SHEET).json()
    demands = c.get("/api/requirements/demands/steel").json()["demands"]
    assert demands[0]["id"] == r["demand_id"] and demands[0]["requirement_id"] == r["id"]
    data = c.get("/api/scenario/steel").json()
    for k in ("scenario", "title", "engine", "plan_seller", "metric_word"):
        data.pop(k)
    data["demands"] = demands + data["demands"]  # posted requirements at the top, sample orders below
    f = c.post("/api/fulfil", json={"demand_id": r["demand_id"], "data": data, "allow_partial": True}).json()
    b = f["best"]
    assert f["feasible"] and b["units"] == 50 and b["allocations"][0]["parts"]
    deal = c.post("/api/deals", json={"kind": "flat", "counterfactual": "recycled", "demand_id": r["demand_id"], "buyer_name": "Test fabricator",
                                      "sellers": b["sellers_used"], "units": b["units"], "unit_name": "parts", "kg_in_units": b["kg_in_units"],
                                      "kg_shipped": b["kg_shipped"], "route_km": b["route_km"], "value_above_salvage": 1, "scenario": "steel",
                                      "requirement_id": r["id"]}).json()
    q = status(r["id"])
    assert q["status"] == "matched" and q["deal_id"] == deal["id"]
    lg = c.get(f"/api/deals/{deal['id']}/logistics").json()  # pickup routing knows the posted order
    assert lg["buyer"]["name"] == "Test fabricator" and lg["pickup_order"] == b["pickup_order"]


def test_no_requirements_leave_the_sample_data_unchanged():
    assert c.get("/api/gapfill/demo").json() == SC.gapfill_demo()
    assert c.get("/api/requirements/demands/steel").json()["demands"] == []


# ---------------- standing orders, reservations ----------------
def test_standing_order_appears_in_sold_before_its_cut_and_what_it_can_become():
    r = post("durable", "flat", SPACER, standing=True).json()
    g = c.get("/api/gapfill/demo").json()
    mine = [o for o in g["orders"] if o["id"] == r["demand_id"]]
    assert mine and mine[0]["posted"] and mine[0]["note"] == "Posted by a buyer in this demo"
    assert [o["id"] for o in g["orders"][:8]] == [o["id"] for o in SC.gapfill_demo()["orders"]]  # sample orders still there
    small = post("durable", "flat", {**SPACER, "thickness_mm": 2, "buyer_name": "Shim buyer"}, standing=True).json()
    b = c.get("/api/become/steel/A3").json()  # a 2 mm offcut
    opt = next(o for o in b["options"] if o["id"] == small["demand_id"])
    assert opt["label"] == "Ordered by Shim buyer"


def test_reserved_status_and_decline_returns_to_open():
    r = post("durable", "flat", SPACER, standing=True).json()
    g = c.get("/api/gapfill/demo").json()
    plan = c.post("/api/gapfill", params={"record": "true"}, json=g).json()
    parts = next(n["units"] for n in plan["neighbours"] if n["order_id"] == r["demand_id"])
    q = status(r["id"])
    assert q["status"] == "reserved" and q["status_text"] == f"Reserved in a cutting plan by Your workshop, {parts} parts"
    c.post("/api/gapfill", params={"record": "true"}, json={**g, "declined_order_ids": [r["demand_id"]]})
    assert status(r["id"])["status"] == "open"
    c.post("/api/gapfill", json=g)  # a plain calculation (record off) never changes a status
    assert status(r["id"])["status"] == "open"


def test_close_removes_it_from_matching_and_sold_before_its_cut():
    r = post("durable", "flat", SPACER, standing=True).json()
    assert c.post(f"/api/requirements/{r['id']}/close").json()["status"] == "closed"
    assert status(r["id"])["status"] == "closed"
    assert all(o["id"] != r["demand_id"] for o in c.get("/api/gapfill/demo").json()["orders"])
    assert c.get("/api/requirements/demands/steel").json()["demands"] == []
    assert c.post("/api/requirements/999/close").status_code == 404


def test_options_come_from_the_models_and_sample_data():
    o = c.get("/api/requirements/options").json()
    steel = next(m for m in o["materials"] if m["key"] == "mild_steel")
    assert "E250" in steel["grades"] and steel["sample_price"] == 62.0
    assert {"profile": "round bar 16", "mass_per_m_kg": 1.58, "material": "mild_steel", "sample_price": 64.0} in o["profiles"]
    assert "bent" in o["conditions"] and o["default_conditions"] == ["new", "good", "surface_rust"]
