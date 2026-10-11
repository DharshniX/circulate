"""The demo-ready network: Kaveri Constructions and its sample site stock, Ezhil's sample rebar order, and the admin Network view."""
import json
import sqlite3
from contextlib import closing
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main
from conftest import DEMO_PASSWORD, sign_in


@pytest.fixture(autouse=True)
def sample_network(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCULATE_SAMPLE_NETWORK", "1")  # off for every other test file
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")


def q(sql, *args):
    with closing(sqlite3.connect(main.DB)) as con:
        return con.execute(sql, args).fetchall()


def kaveri():
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"email": "kaveri@demo.circulate", "password": DEMO_PASSWORD}).status_code == 200
    return c


def ezhil_requirement():
    [r] = [x for x in sign_in(TestClient(main.app), "steel:W").get("/api/requirements").json()["requirements"] if x["kind"] == "linear"]
    return r


def test_the_seeded_construction_seller_and_its_stock_exist_after_a_restart():
    for restart in range(3):
        main._SEEDED.discard(str(main.DB))  # as when the server starts again
        with closing(main.db()):
            pass
    [(bid, name, role, stype, lane, area)] = q("SELECT business_id, business_name, role, seller_type, lane, area FROM users WHERE email='kaveri@demo.circulate'")
    assert (bid, name, role, stype, lane, area) == ("site:kaveri", "Kaveri Constructions (site), Porur", "seller", "construction_site", "durable", "Porur")
    rows = q("SELECT item, label, source FROM imported_stock WHERE owner='site:kaveri'")
    items = [json.loads(r[0]) for r in rows]
    bars = [x for x in items if x["material"] == "tmt_rebar"]
    ply = [x for x in items if x["material"] == "shuttering_plywood"]
    assert len(bars) == 60 and len(ply) == 10 and len(rows) == 70  # seeded once, however often it restarts
    assert {r[1] for r in rows} == {"sample"} and {r[2] for r in rows} == {main.SAMPLE_SOURCE}
    assert {(x["grade"], x["profile"], x["mass_per_m_kg"]) for x in bars} == {("Fe500", "TMT bar 12", 0.889)}
    assert min(x["length_mm"] for x in bars) == 1200 and max(x["length_mm"] for x in bars) == 2500
    assert {(x["thickness_mm"], x["condition"]) for x in ply} == {(18, "used_once")}
    assert len(q("SELECT id FROM requirements WHERE owner='steel:W'")) == 1
    me = kaveri().get("/api/session").json()
    assert me["business_name"] == "Kaveri Constructions (site), Porur" and me["seller_type"] == "construction_site"
    assert me["home"] == "stock"  # a construction site starts on its stock, not on sheet cutting
    assert len(kaveri().get("/api/stock/import_durable").json()["items"]) == 70


def test_a_closed_sample_requirement_is_not_seeded_again():
    r = ezhil_requirement()
    sign_in(TestClient(main.app), "steel:W").post(f"/api/requirements/{r['id']}/close")
    main._SEEDED.discard(str(main.DB))
    with closing(main.db()):
        pass
    assert [x["status"] for x in sign_in(TestClient(main.app), "steel:W").get("/api/requirements").json()["requirements"]] == ["closed"]


def test_kaveris_rebar_fills_ezhils_one_metre_requirement_through_the_bar_engine():
    r = ezhil_requirement()
    d = r["demand"]
    assert (d["profile"], d["cut_length_mm"], d["quantity"], d["accepted_grades"], d["label"]) == ("TMT bar 12", 1000, 40, ["FE500"], "sample")
    ezhil = sign_in(TestClient(main.app), "steel:W")
    card = next(i for i in ezhil.get("/api/browse/durable").json()["items"] if i["key"] == "import_durable@site:kaveri" and i["kind"] == "linear")
    assert card["order"]["demand_id"] == r["demand_id"] and card["seller_type_name"] == "Construction site" and card["approx_location"]
    data = ezhil.get("/api/scenario/bars").json()
    for k in ("scenario", "title", "engine", "plan_seller", "metric_word"):
        data.pop(k)
    own = ezhil.get("/api/own-stock/bars").json()
    data["sellers"] += own["sellers"]
    data["resources"] += own["resources"]
    data["demands"] = ezhil.get("/api/requirements/demands/bars").json()["demands"] + data["demands"]
    f = ezhil.post("/api/fulfil", json={"demand_id": r["demand_id"], "data": data, "allow_partial": True}).json()
    b = f["best"]
    assert f["feasible"] and f["shortfall"] == 0 and b["units"] == 40 and b["sellers_used"] == ["site:kaveri"]
    assert all(a["shape"] == "bar" and all(c["length_mm"] == 1000 for c in a["cuts"]) for a in b["allocations"])
    deal = ezhil.post("/api/deals", json={"kind": "linear", "counterfactual": b["counterfactual"][0], "demand_id": r["demand_id"],
                                          "buyer_name": d["buyer_name"], "sellers": b["sellers_used"], "units": b["units"], "unit_name": f["unit_name"],
                                          "kg_in_units": b["kg_in_units"], "kg_shipped": b["kg_shipped"], "route_km": b["route_km"],
                                          "value_above_salvage": b["value"]["seller_gain_above_salvage"], "scenario": "bars",
                                          "requirement_id": r["id"]}).json()["id"]
    assert deal in [x["id"] for x in kaveri().post("/api/ledger", json={}).json()["deals"]]  # Kaveri sees its deal
    assert ezhil_requirement()["status"] == "matched"


def test_the_network_counts_match_the_database():
    k = kaveri()
    ask = k.post("/api/collections", json={"items": [{"key": "import_durable@site:kaveri", "id": "KC-R01"}, {"key": "import_durable@site:kaveri", "id": "KC-P01"}],
                                           "pickup_date": (date.today() + timedelta(days=1)).isoformat(), "slot": "10:00-12:00"}).json()
    k.post("/api/collections", json={"items": [{"key": "import_durable@site:kaveri", "id": "KC-R02"}],
                                     "pickup_date": (date.today() + timedelta(days=1)).isoformat(), "slot": "08:00-10:00"})
    admin = sign_in(TestClient(main.app))
    admin.post(f"/api/collections/{ask['id']}/status", json={"status": "closed"})
    with closing(sqlite3.connect(main.DB)) as con:
        con.execute("INSERT INTO deals (created_at, kind, demand_id, buyer_name, sellers, status, is_simulated, evidence, buyer_confirmed, history, units,"
                    " unit_name, kg_in_units, kg_shipped, route_km, value_above_salvage, counterfactual) VALUES (?, 'flat','S1','x','[]','agreed',1,'[]',0,'[]',1,'p',1,1,1,1,'recycled')",
                    ((date.today() - timedelta(days=30)).isoformat(),))  # an old deal: not this week
        con.commit()
    ezhil = sign_in(TestClient(main.app), "steel:W")  # a deal this week, through the app: Ezhil accepts its order S1
    data = ezhil.get("/api/scenario/steel").json()
    for key in ("scenario", "title", "engine", "plan_seller", "metric_word"):
        data.pop(key)
    best = ezhil.post("/api/fulfil", json={"demand_id": "S1", "data": data}).json()["best"]
    assert ezhil.post("/api/deals", json={"kind": "flat", "counterfactual": "recycled", "demand_id": "S1", "buyer_name": "Ezhil Engineering Works, Ambattur",
                                          "sellers": best["sellers_used"], "units": best["units"], "unit_name": "parts", "kg_in_units": best["kg_in_units"],
                                          "kg_shipped": best["kg_shipped"], "route_km": best["route_km"], "value_above_salvage": 1,
                                          "scenario": "steel"}).status_code == 200
    n = admin.get("/api/network").json()
    assert n["counts"] == _counts_from_db()
    assert n["counts"] == {"sellers_by_type": {"Construction site": 1, "Kitchen": 1, "Workshop": 1}, "sellers": 3, "buyers": 3,
                           "open_collections": 1, "deals_this_week": 1}
    site = next(p for p in n["places"] if p["business_id"] == "site:kaveri")
    assert (site["x_km"], site["y_km"], site["seller_type_name"]) == (*main.AREA_KM["Porur"], "Construction site")
    [open_one] = n["collections"]
    assert open_one["business_name"].startswith("Kaveri") and open_one["item_count"] == 1 and (open_one["x_km"], open_one["y_km"]) == main.AREA_KM["Porur"]
    assert n["sample"] and "demo businesses" in n["sample_why"]
    anna = next(p for p in n["places"] if p["business_id"] == "food:M")
    assert anna["area"] == "Anna Nagar" and (anna["x_km"], anna["y_km"]) == main.AREA_KM["Anna Nagar"]  # from its name
    assert len(n["places"]) == len(q("SELECT id FROM users WHERE role != 'admin' AND active=1"))
    for who in ("steel:A", "steel:W"):
        assert sign_in(TestClient(main.app), who).get("/api/network").status_code == 403


def _counts_from_db():
    users = q("SELECT role, seller_type FROM users WHERE role != 'admin' AND active=1")
    by_type = {}
    for role, st in users:
        if role == "seller":
            name = main.SELLER_TYPES.get(st or "", "Type not given")
            by_type[name] = by_type.get(name, 0) + 1
    week = (main.datetime.now(main.timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    return {"sellers_by_type": by_type, "sellers": sum(by_type.values()), "buyers": sum(1 for r, _ in users if r == "buyer"),
            "open_collections": q("SELECT COUNT(*) FROM collection_requests WHERE status != 'closed'")[0][0],
            "deals_this_week": q("SELECT COUNT(*) FROM deals WHERE created_at >= ?", week)[0][0]}
