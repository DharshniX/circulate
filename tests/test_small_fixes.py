"""Small fixes: Offers and recovery plan in the seller rail (for the seller's own stock), and the Next cutting job form
at the top of Sold before it's cut."""
import re

import pytest
from fastapi.testclient import TestClient

import app.main as main
from conftest import sign_in


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")


def plan(c, key, seller_id="A"):
    data = c.get(f"/api/scenario/{key}").json()
    meta = {k: data.pop(k) for k in ("scenario", "title", "engine", "plan_seller", "metric_word")}
    return c.post("/api/seller-plan", json={"seller_id": seller_id, "kind": meta["engine"], "data": data, "scenario": key, "deadline": 12})


def test_offers_is_in_the_seller_rail_and_still_in_the_admins():
    for who in ("steel:A", "admin"):
        s = sign_in(TestClient(main.app), who).get("/api/session").json()
        assert "offers" in {i["view"] for g in s["menu"] for i in g["items"]}, who
    workshop = next(g for g in sign_in(TestClient(main.app), "steel:A").get("/api/session").json()["menu"] if g["group"] == "My workshop")
    assert [i["view"] for i in workshop["items"]] == ["gapfill", "stock", "offers"]


def test_a_seller_gets_the_plan_for_its_own_stock_in_the_whole_cluster():
    sriram = sign_in(TestClient(main.app), "steel:A")
    mine = plan(sriram, "steel", seller_id="B").json()  # asking for Kumaran's plan still gives Sri Ram's own
    assert mine["seller_name"] == "Sri Ram Fabricators, Ambattur" and mine["routes"] and mine["plan"]["best"]
    admin = plan(sign_in(TestClient(main.app)), "steel", seller_id="A").json()
    assert mine["routes"] == admin["routes"]  # the same plan the admin sees with every seller's stock
    thamarai = plan(sign_in(TestClient(main.app), "food:H"), "food", seller_id="K").json()
    assert thamarai["seller_name"] == "Thamarai Hotel Kitchen, Egmore"


def test_no_stock_gives_a_plain_empty_answer_and_buyers_cannot_ask():
    new = TestClient(main.app)
    new.post("/api/auth/register", json={"role": "seller", "seller_type": "shop", "business_name": "Mani Hardware", "contact_name": "Mani",
                                         "email": "mani@example.com", "phone": "9876543210", "area": "Guindy", "lane": "durable",
                                         "password": "manihard1", "confirm": "manihard1"})
    r = plan(new, "steel").json()
    assert r["empty"] and "no stock in this material" in r["message"]
    assert plan(sign_in(TestClient(main.app), "steel:W"), "steel").status_code == 403


def test_the_next_cutting_job_form_is_on_sold_before_its_cut_not_on_add_stock():
    js = (main.ROOT / "static" / "app.js").read_text(encoding="utf-8")
    add = re.search(r"async function vAdd\(el\) \{.*?\n\}", js, re.S).group(0)
    gap = re.search(r"async function vGap\(el\) \{.*?\n\}", js, re.S).group(0)
    assert "Next cutting job" not in add and "data-job" not in add
    assert "let h = jobCard() +" in gap and "bindJob(el)" in gap  # at the top of the page, above the result
    assert "/api/import/cutting-job" in re.search(r"function bindJob\(el\) \{.*?\n\}", js, re.S).group(0)
