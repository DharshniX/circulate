"""Own items in matching, and "What can this become?" (engine/become.py)."""
import pytest
from fastapi.testclient import TestClient

import app.main as main
from engine import become as BEC
from engine import scenarios as SC
from engine.models import Defect, FlatPiece
from engine.nesting import verify_placements

c = TestClient(main.app)
OFFCUT_2MM = {"material": "mild_steel", "length_mm": "1250", "width_mm": "600", "thickness_mm": "2", "grade": "E250",
              "condition": "good", "is_full_sheet": False, "days_unused": "10", "seller_id": "My workshop"}


@pytest.fixture(autouse=True)
def temp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")
    monkeypatch.setattr(main, "PHOTOS", tmp_path / "photos")


def scenario_data(key):
    data = c.get(f"/api/scenario/{key}").json()
    for k in ("scenario", "title", "engine", "plan_seller", "metric_word"):
        data.pop(k)
    return data


# ---------------- Task 1: own items in matching ----------------
def test_an_order_is_filled_from_a_manually_entered_item():
    item = c.post("/api/manual/durable", json={"fields": OFFCUT_2MM}).json()
    assert item["listed"]
    own = c.get("/api/own-stock/steel").json()
    assert own["seller"]["name"] == "Your workshop" and own["seller"]["id"] == "YOU"
    assert [r["id"] for r in own["resources"]] == [f"YOU-{item['item_id']}"]
    data = scenario_data("steel")
    xs = [s["x_km"] for s in data["sellers"]]
    assert own["seller"]["x_km"] == pytest.approx(sum(xs) / len(xs), abs=0.01)  # placed at the cluster centre
    data["sellers"].append(own["seller"])
    data["resources"] += own["resources"]
    r = c.post("/api/fulfil", json={"demand_id": "S1", "data": data, "seller_ids": ["YOU"], "allow_partial": True}).json()
    b = r["best"]
    assert b["sellers_used"] == ["YOU"] and b["units"] > 0 and b["pickup_order"] == ["YOU"]
    a = b["allocations"][0]
    assert a["resource_id"] == f"YOU-{item['item_id']}" and len(a["parts"]) == b["units"]  # a real cutting plan
    deal = c.post("/api/deals", json={"kind": "flat", "counterfactual": "recycled", "demand_id": "S1", "buyer_name": "Ezhil Engineering Works, Ambattur",
                                      "sellers": ["YOU"], "units": b["units"], "unit_name": "parts", "kg_in_units": b["kg_in_units"],
                                      "kg_shipped": b["kg_shipped"], "route_km": b["route_km"], "value_above_salvage": 0,
                                      "scenario": "steel"}).json()
    lg = c.get(f"/api/deals/{deal['id']}/logistics").json()
    assert lg["pickup_order"] == ["YOU"] and lg["collect"][0]["seller_name"] == "Your workshop"


def test_no_own_items_leaves_the_sample_data_unchanged():
    assert c.get("/api/own-stock/steel").json() == {"seller": None, "resources": [], "note": c.get("/api/own-stock/steel").json()["note"]}
    assert main._dataset_with_own("steel")["resources"] == SC.dataset("steel")["resources"]
    assert c.get("/api/own-stock/bars").json()["resources"] == []  # own items are sheets, not bars
    assert c.get("/api/own-stock/nope").status_code == 404


# ---------------- Task 2: what can this become ----------------
def options(key, rid):
    return c.get(f"/api/become/{key}/{rid}").json()


def test_a_piece_that_fits_several_parts():
    b = options("steel", "A3")  # 1250 x 600 x 2 mm offcut
    orders = [o for o in b["options"] if o["source"] == "order"]
    refs = {o["part_name"]: o for o in b["options"] if o["source"] == "reference"}
    assert {o["id"] for o in orders} >= {"S1", "S2", "S3"} and all(o["label"].startswith("Ordered by ") for o in orders)
    assert len(refs) == len(BEC.REFERENCE_PARTS) and all(o["label"] == "Common part, no buyer yet" for o in refs.values())
    kinds = [o["source"] == "reference" for o in b["options"]]
    assert kinds == sorted(kinds)  # options with a real buyer come before common parts with no buyer
    for group in (False, True):  # then by value above scrap
        vals = [o["value_above_scrap"] for o in b["options"] if (o["source"] == "reference") is group]
        assert vals == sorted(vals, reverse=True)
    for o in b["options"]:  # every plan is a real, checked plan from the nesting engine
        det = o["detail"]
        from engine.nesting import Placement
        assert not verify_placements(det["length_mm"], det["width_mm"], [Placement(**p) for p in det["parts"]],
                                     b["kerf_mm"], b["edge_margin_mm"], [])
        assert o["units"] == len(det["parts"])
    s1 = next(o for o in orders if o["id"] == "S1")
    W = s1["units"] * 150 * 100 * 2 * 7850e-9 / 0.75 * 62.0  # the flat engine's buyer value
    assert s1["value_above_scrap"] == pytest.approx(W - b["scrap"]["value"] - s1["transport"], abs=0.05)


def test_a_piece_too_thin_for_some():
    b = options("steel", "A3")  # 2 mm; the standing orders are for 6 and 8 mm plate
    thin = [r for r in b["refused"] if r["source"] == "standing"]
    assert thin and all("thickness 2.0 mm, part needs" in r["reason"] for r in thin)
    assert all(o["source"] != "standing" for o in b["options"])


def test_a_defect_reduces_the_count():
    d = SC.dataset("steel")
    seller = next(s for s in d["sellers"] if s.id == "A")
    clean = FlatPiece(id="X", seller_id="A", material="mild_steel", length_mm=900, width_mm=700, thickness_mm=2, grade="E250")
    rusty = clean.model_copy(update={"defects": [Defect(x_mm=300, y_mm=200, length_mm=220, width_mm=180, note="rust")]})
    cands = BEC.reference_candidates(clean, 62.0)
    n_clean = {o["part_name"]: o["units"] for o in BEC.become(clean, seller, cands, d["settings"], d["transport"])["options"]}
    n_rusty = {o["part_name"]: o["units"] for o in BEC.become(rusty, seller, cands, d["settings"], d["transport"])["options"]}
    assert all(n_rusty[k] <= n_clean[k] for k in n_rusty) and n_rusty["shim"] < n_clean["shim"]
    a4 = options("steel", "A4")  # the sample piece with a rust patch: no part is placed over it
    for o in a4["options"]:
        for p in o["detail"]["parts"]:
            assert p["x"] + p["length"] <= 300 or p["x"] >= 520 or p["y"] + p["width"] <= 200 or p["y"] >= 380


def test_a_bent_piece_is_refused_everywhere():
    b = options("steel", "B6")
    assert b["options"] == []
    assert any("condition 'bent' not accepted" in r["reason"] for r in b["refused"] if r["source"] == "reference")


def test_a_bar_is_cut_to_open_order_lengths():
    b = options("bars", "G2")  # 2350 mm round bar 16
    [o] = b["options"]
    assert o["id"] == "B1" and o["cut_length_mm"] == 450 and o["units"] == len(o["detail"]["cuts"]) == 5


def test_a_fresh_lot_with_too_few_hours():
    b = options("food", "K2")  # capsicum, 9 h left; the hostel needs 12 h on arrival
    assert b["options"] == []
    assert any("only 9 h left; buyer needs 12 h on arrival" in r["reason"] for r in b["refused"])


def test_a_donation():
    b = options("food", "H3")  # cooked rice and dal: donation only
    [o] = b["options"]
    assert o["buyer_name"] == "Thozhan Food Bank, Saidapet" and o["is_donation"] and o["buyer_value"] == 0 and o["units"] == 18
    assert o["value_above_scrap"] == pytest.approx(0 - o["scrap_value"] - o["transport"], abs=0.01)
    assert o["hours_on_arrival"] < 5 and o["hours_on_arrival"] >= o["min_hours_on_arrival"]
    hostel = next(r for r in b["refused"] if "Hostel" in r["label"])
    assert "buyer does not take cooked meal" in hostel["reason"]  # who cannot, and why


def test_own_item_becomes_with_the_cluster_scrap_price():
    item = c.post("/api/manual/durable", json={"fields": OFFCUT_2MM}).json()
    b = options("import_durable", item["item_id"])
    assert b["own"] and b["seller_name"] == "Your workshop" and b["scrap"]["price_per_kg"] == 30.0 and b["scrap"]["label"] == "sample"
    assert any(o["id"] == "S1" for o in b["options"])
    assert c.get("/api/become/import_durable/NOPE").status_code == 404
    assert c.get("/api/become/nowhere/A1").status_code == 404


def test_passport_shows_what_it_can_become():
    page = c.get("/passport/steel/A3").text
    assert "What can this become?" in page and "Ordered by Ezhil Engineering Works, Ambattur" in page and "Common part, no buyer yet" in page
    assert '<svg class="plan"' in page and "Not possible" in page
    food = c.get("/passport/food/H3").text
    assert "Thozhan Food Bank, Saidapet" in food and "donation" in food
