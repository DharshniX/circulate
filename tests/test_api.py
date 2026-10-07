import pytest
from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)
KEYS = [s["key"] for s in c.get("/api/scenarios").json()]


@pytest.mark.parametrize("key", KEYS)
def test_every_scenario_end_to_end(key):
    data = c.get(f"/api/scenario/{key}").json()
    meta = {k: data.pop(k) for k in ("scenario", "title", "engine", "plan_seller", "metric_word")}
    d0 = data["demands"][0]
    r = c.post("/api/fulfil", json={"demand_id": d0["id"], "data": data}).json()
    assert r["feasible"], r["message"]
    p = c.post("/api/seller-plan", json={"seller_id": meta["plan_seller"], "kind": meta["engine"], "data": data}).json()
    assert "plan" in p and p["plan"]["best"]["ev"] >= p["salvage_value_all"] - 1e-6
    c.post("/api/reset", json={})
    b = r["best"]
    deal = c.post("/api/deals", json={
        "kind": meta["engine"], "counterfactual": b["counterfactual"][0], "demand_id": d0["id"],
        "buyer_name": d0["buyer_name"], "sellers": b["sellers_used"], "units": b["units"], "unit_name": r["unit_name"],
        "kg_in_units": b["kg_in_units"], "kg_shipped": b["kg_shipped"], "route_km": b["route_km"],
        "value_above_salvage": b["value"]["seller_gain_above_salvage"],
        "buyer_utilisation": d0.get("buyer_utilisation")}).json()
    i = deal["id"]
    assert c.post(f"/api/deals/{i}/advance", json={"status": "delivered"}).status_code == 409
    assert c.post(f"/api/deals/{i}/advance", json={"status": "dispatched"}).status_code == 200
    assert c.post(f"/api/deals/{i}/advance", json={"status": "delivered"}).status_code == 422
    assert c.post(f"/api/deals/{i}/advance", json={"status": "delivered", "evidence": ["challan:1"],
                                                   "buyer_confirmed": True}).status_code == 200
    assert c.post("/api/ledger", json={}).json()["deals"][0]["impact"]["label"] == "simulated"


def test_bad_input_gives_clear_error():
    data = c.get("/api/scenario/steel").json()
    for k in ("scenario", "title", "engine", "plan_seller", "metric_word"):
        data.pop(k)
    data["demands"][0]["length_mm"] = -10
    assert c.post("/api/fulfil", json={"demand_id": "S1", "data": data}).status_code == 422
    assert c.get("/api/scenario/textile").status_code == 404
