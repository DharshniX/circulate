import random

import pytest
from fastapi.testclient import TestClient

from app.main import app
from conftest import sign_in
from engine.gapfill import GapFillRequest, plan_gap_fill
from engine.scenarios import gapfill_demo

c = TestClient(app)


@pytest.fixture(autouse=True)
def _signed_in():
    sign_in(c, "admin")


def demo(**kw):
    d = gapfill_demo()
    d.update(kw)
    return GapFillRequest(**d)


def test_demo_plan_is_valid_and_beats_scrap():
    r = plan_gap_fill(demo())
    assert r["checks"]["ok"], r["checks"]["problems"]
    assert r["worth_doing"] and r["money"]["net_gain"] > 0
    assert r["own"]["units"] == 9
    assert r["money"]["total_with_gap_fill"] == pytest.approx(
        r["money"]["neighbours_value"] + r["money"]["scrap_of_rest"] - r["money"]["pickup"], abs=0.02)


def test_wrong_thickness_and_grade_are_refused_with_reasons():
    r = plan_gap_fill(demo())
    reasons = {x["order_id"]: x["reason"] for x in r["refused"]}
    assert "thickness" in reasons["G7"] and "grade" in reasons["G8"]
    used = {n["order_id"] for n in r["neighbours"]}
    assert not used & {"G7", "G8"}


def test_never_more_than_the_ordered_quantity():
    r = plan_gap_fill(demo())
    qty = {o["id"]: o["quantity"] for o in gapfill_demo()["orders"]}
    for n in r["neighbours"]:
        assert n["units"] <= qty[n["order_id"]]


def test_buyer_declines_replans_validly_and_frees_the_order():
    first = plan_gap_fill(demo())
    top = max(first["neighbours"], key=lambda n: n["value"])["order_id"]
    second = plan_gap_fill(demo(declined_order_ids=[top]))
    assert second["checks"]["ok"]
    assert top not in {n["order_id"] for n in second["neighbours"]}
    assert second["money"]["net_gain"] <= first["money"]["net_gain"] + 0.01


def test_no_orders_means_scrap_and_no_claim():
    r = plan_gap_fill(demo(orders=[]))
    assert not r["worth_doing"] and r["neighbours"] == []
    assert r["money"]["net_gain"] == 0 and r["money"]["pickup"] == 0


def test_own_job_that_does_not_fit_is_an_error():
    d = gapfill_demo()
    d["job"]["quantity"] = 500
    with pytest.raises(ValueError):
        plan_gap_fill(GapFillRequest(**d))


def test_never_worse_than_scrap_and_always_valid_on_random_jobs():
    rng = random.Random(7)
    for _ in range(40):
        d = gapfill_demo()
        d["job"].update(length_mm=rng.randrange(150, 700, 10), width_mm=rng.randrange(100, 500, 10),
                        quantity=rng.randint(1, 6))
        d["sheet"]["thickness_mm"] = 6.0
        for o in d["orders"]:
            o["quantity"] = rng.randint(1, 12)
        try:
            r = plan_gap_fill(GapFillRequest(**d))
        except ValueError:
            continue
        assert r["checks"]["ok"], r["checks"]["problems"]
        assert r["money"]["net_gain"] >= -0.01


def test_api_demo_and_plan():
    d = c.get("/api/gapfill/demo").json()
    r = c.post("/api/gapfill", json=d)
    assert r.status_code == 200 and r.json()["checks"]["ok"]
    d["job"]["quantity"] = 500
    assert c.post("/api/gapfill", json=d).status_code == 422
