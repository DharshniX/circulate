"""Pickup and delivery: route, vehicle, schedule, printable documents. Transport is simulated."""
import json
import re
import sqlite3
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main

c = TestClient(main.app)
TOMORROW = (date.today() + timedelta(days=1)).isoformat()


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")


def accept(key, n=0, rate=1.0, with_scenario=True):
    """Accept an order the way the app does, and return (deal id, the engine's best plan)."""
    data = c.get(f"/api/scenario/{key}").json()
    meta = {k: data.pop(k) for k in ("scenario", "title", "engine", "plan_seller", "metric_word")}
    data["transport"]["rate_multiplier"] = rate
    d = data["demands"][n]
    r = c.post("/api/fulfil", json={"demand_id": d["id"], "data": data, "allow_partial": True}).json()
    b = r["best"]
    body = {"kind": meta["engine"], "counterfactual": b["counterfactual"][0], "demand_id": d["id"], "buyer_name": d["buyer_name"],
            "sellers": b["sellers_used"], "units": b["units"], "unit_name": r["unit_name"], "kg_in_units": b["kg_in_units"],
            "kg_shipped": b["kg_shipped"], "route_km": b["route_km"], "value_above_salvage": b["value"]["seller_gain_above_salvage"],
            "rate_multiplier": rate}
    if with_scenario:
        body["scenario"] = key
    return c.post("/api/deals", json=body).json()["id"], b


def test_route_vehicle_and_cost_come_from_the_engine():
    i, b = accept("steel", rate=1.5)
    lg = c.get(f"/api/deals/{i}/logistics").json()
    assert lg["pickup_order"] == b["pickup_order"] and lg["matches_deal"]
    assert [x["seller_id"] for x in lg["collect"]] == b["pickup_order"]
    assert lg["legs"][0]["from"] == "Ezhil Engineering Works, Ambattur" and lg["legs"][-1]["to"] == "Ezhil Engineering Works, Ambattur"
    assert len(lg["legs"]) == len(b["pickup_order"]) + 1
    assert sum(x["km"] for x in lg["legs"]) == pytest.approx(b["route_km"], abs=0.02)
    v = lg["vehicle"]
    assert v["name"] == b["vehicle"] and v["trips"] == b["trips"] and v["load_kg"] == b["kg_shipped"]
    assert v["transport_cost"] == b["value"]["transport_T"] and v["rate_multiplier"] == 1.5  # same rate as when agreed
    assert v["payload_kg"] >= v["load_kg"] / v["trips"]
    assert lg["note"] == "Transporters and bookings are simulated in this prototype."
    assert sum(x["units"] for x in lg["collect"]) == pytest.approx(b["units"])


def test_older_deal_without_scenario_still_opens():
    i, b = accept("bars", with_scenario=False)
    lg = c.get(f"/api/deals/{i}/logistics").json()
    assert lg["scenario"] == "bars" and lg["pickup_order"] == b["pickup_order"]


def test_older_database_gets_new_columns_and_keeps_rows(tmp_path, monkeypatch):
    old = tmp_path / "old.db"
    con = sqlite3.connect(old)
    con.execute("""CREATE TABLE deals (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, kind TEXT, counterfactual TEXT,
        demand_id TEXT, buyer_name TEXT, sellers TEXT, units REAL, unit_name TEXT, kg_in_units REAL, kg_shipped REAL, route_km REAL,
        value_above_salvage REAL, buyer_utilisation REAL, status TEXT, is_simulated INTEGER, evidence TEXT, buyer_confirmed INTEGER, history TEXT)""")
    con.execute("INSERT INTO deals (kind, demand_id, buyer_name, sellers, status, is_simulated, evidence, buyer_confirmed, history, units, "
                "unit_name, kg_in_units, kg_shipped, route_km, value_above_salvage, counterfactual) VALUES "
                "('flat','S1','Ezhil Engineering Works, Ambattur','[\"A\",\"B\"]','agreed',1,'[]',0,'[]',600,'parts',1,1,1,1,'recycled')")
    con.commit()
    con.close()
    monkeypatch.setattr(main, "DB", old)
    lg = c.get("/api/deals/1/logistics").json()
    assert lg["scenario"] == "steel" and lg["pickup_order"] == ["A", "B"]


def test_fresh_arrival_uses_the_engine_rule_and_warns_about_cold_chain():
    i, b = accept("food", 1)  # food bank: cooked rice (chilled) and bread
    lg = c.get(f"/api/deals/{i}/logistics").json()
    rice = next(x for x in lg["lots"] if x["product"] == "Cooked rice and dal")
    assert rice["on_arrival_if_collected_now"] == pytest.approx(rice["hours_left"] - b["tour_hours"], abs=0.01)
    assert rice["on_arrival_at_booked_slot"] is None and rice["ok"]
    assert any("chilled vehicle" in w and "Cooked rice" in w for w in lg["warnings"])


def test_booking_moves_arrival_and_flags_late_lots():
    i, b = accept("food", 1)
    r = c.post(f"/api/deals/{i}/booking", json={"pickup_date": TOMORROW, "slot": "10:00-12:00"})
    assert r.status_code == 200 and r.json()["booking"]["slot"] == "10:00-12:00"
    now = datetime.fromisoformat(f"{TOMORROW}T08:00")  # two hours before the slot
    lg = c.get(f"/api/deals/{i}/logistics", params={"now": now.isoformat()}).json()
    rice = next(x for x in lg["lots"] if x["product"] == "Cooked rice and dal")
    assert rice["on_arrival_at_booked_slot"] == pytest.approx(rice["hours_left"] - 2 - b["tour_hours"], abs=0.01)
    assert lg["arrival"].startswith(f"{TOMORROW}T10:")
    late = c.get(f"/api/deals/{i}/logistics", params={"now": f"{date.today().isoformat()}T08:00"}).json()
    assert any("Book an earlier slot" in w for w in late["warnings"]) and not all(x["ok"] for x in late["lots"])
    hist = json.loads(sqlite3.connect(main.DB).execute("SELECT history FROM deals WHERE id=?", (i,)).fetchone()[0])
    assert hist[-1]["status"] == "pickup booked"


def test_booking_is_checked():
    i, _ = accept("steel")
    assert c.post(f"/api/deals/{i}/booking", json={"pickup_date": TOMORROW, "slot": "03:00-05:00"}).status_code == 422
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert c.post(f"/api/deals/{i}/booking", json={"pickup_date": yesterday, "slot": "08:00-10:00"}).status_code == 422
    assert c.post("/api/deals/999/booking", json={"pickup_date": TOMORROW, "slot": "08:00-10:00"}).status_code == 404
    assert c.get("/api/deals/999/logistics").status_code == 404
    c.post(f"/api/deals/{i}/advance", json={"status": "dispatched"})
    assert c.post(f"/api/deals/{i}/booking", json={"pickup_date": TOMORROW, "slot": "08:00-10:00"}).status_code == 409


def test_delivered_still_needs_evidence():
    i, _ = accept("steel")
    c.post(f"/api/deals/{i}/booking", json={"pickup_date": TOMORROW, "slot": "08:00-10:00"})
    assert c.post(f"/api/deals/{i}/advance", json={"status": "dispatched"}).status_code == 200
    assert c.post(f"/api/deals/{i}/advance", json={"status": "delivered"}).status_code == 422
    assert c.post(f"/api/deals/{i}/advance", json={"status": "delivered", "evidence": ["SIM-00001"], "buyer_confirmed": True}).status_code == 200
    assert c.get(f"/api/deals/{i}/logistics").json()["deal"]["status"] == "delivered"


def test_printable_pickup_sheet_and_challan():
    i, b = accept("food", 0)
    c.post(f"/api/deals/{i}/booking", json={"pickup_date": TOMORROW, "slot": "14:00-16:00"})
    sheet = c.get(f"/deals/{i}/pickup-sheet").text
    name_of = {x["seller_id"]: x["seller_name"] for x in b["per_seller"]}
    order = [name_of[sid] for sid in b["pickup_order"]]
    positions = [sheet.index(n) for n in order]
    assert positions == sorted(positions)  # sellers listed in the engine's pickup order
    assert "simulated" in sheet and "Contact: ______________" in sheet and "14:00-16:00" in sheet
    challan = c.get(f"/deals/{i}/challan").text
    assert "Delivery challan (simulated)" in challan and "Anna Nagar Boys" in challan and "Not a tax invoice" in challan
    assert f"SIM-{i:05d}" in challan
    assert c.get("/deals/999/challan").status_code == 404


def test_no_named_logistics_company_anywhere():
    i, _ = accept("steel")
    text = (c.get(f"/deals/{i}/pickup-sheet").text + c.get(f"/deals/{i}/challan").text
            + json.dumps(c.get(f"/api/deals/{i}/logistics").json())).lower()
    for name in ("porter", "dunzo", "delhivery", "blue dart", "lalamove", "shadowfax", "ecom express"):
        assert not re.search(rf"{name}", text), name  # whole words: "transporters" is fine
