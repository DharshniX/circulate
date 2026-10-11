"""Browse surplus (buyer marketplace) and seller photos on the passport."""
import base64
import math

import pytest
from fastapi.testclient import TestClient

import app.main as main
from conftest import sign_in
from engine import scenarios as SC

c = TestClient(main.app)
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture(autouse=True)
def temp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")
    monkeypatch.setattr(main, "PHOTOS", tmp_path / "photos")
    sign_in(c, "admin")  # a fresh database each test: sign in again


def items(lane):
    return {f"{i['key']}:{i['id']}": i for i in c.get(f"/api/browse/{lane}").json()["items"]}


def test_browse_lists_every_sample_item_in_its_lane():
    dur, fresh = items("durable"), items("fresh")
    expected = sum(len(SC.dataset(k)["resources"]) for k in ("steel", "sheets", "bars"))
    assert len(dur) == expected and len(fresh) == len(SC.dataset("food")["resources"])
    assert all(i["label"] == "sample" and i["photo"] is None for i in [*dur.values(), *fresh.values()])
    assert c.get("/api/browse/textile").status_code == 422


def test_durable_price_range_and_distance():
    a1 = items("durable")["steel:A1"]
    kg = 2500 * 1250 * 2.0 * 7850e-9
    assert a1["floor"]["value"] == pytest.approx(kg * 30.0, abs=0.01)  # seller's scrap price
    assert a1["ceiling"]["value"] == pytest.approx(kg * 62.0, abs=0.01)  # buyer's new-plate price
    assert a1["floor"]["label"] == "sample" and a1["floor"]["quoted_on"] is None
    assert a1["distance_km"] == pytest.approx(round(math.hypot(2.0, 1.5), 1))  # Fabricator A to Workshop W
    assert items("durable")["steel:A4"]["defects"][0]["note"] == "rust patch"
    assert items("durable")["sheets:Ass1"]["ceiling"] is None  # no buyer price for stainless in that cluster


def test_order_is_preselected_or_refused_with_reason():
    dur = items("durable")
    assert dur["steel:A1"]["order"] == {"scenario": "steel", "index": 0, "demand_id": "S1", "buyer_name": "Ezhil Engineering Works, Ambattur"}
    assert dur["steel:A13"]["order"] is None and "thickness" in dur["steel:A13"]["order_reason"]
    assert "bent" in dur["steel:B6"]["order_reason"]
    fresh = items("fresh")
    assert fresh["food:H1"]["order"]["demand_id"] == "F1"
    assert "used cooking oil" in fresh["food:K3"]["order_reason"]


def test_fresh_cards_have_hours_and_disposal_cost():
    h3 = items("fresh")["food:H3"]
    assert h3["hours_left"] == 5 and h3["disposal"]["value"] == pytest.approx(-36.0)
    assert "ceiling" not in h3


def test_imported_stock_appears_without_invented_prices():
    text = (main.EXAMPLES / "durable_stock_example.csv").read_text()
    c.post("/api/import/list", json={"lane": "durable", "csv_text": text, "measured": True})
    imp = [i for i in items("durable").values() if i["key"] == "import_durable@admin"]  # the admin's own test items
    assert len(imp) == 5 and all(i["label"] == "measured" for i in imp)
    assert all(i["floor"] is None and i["ceiling"] is None for i in imp)  # still no invented prices on the card
    # the admin's own items are matched as "Your workshop": an order to open, or the engine's reason why none fits
    assert all(i["match_seller"] == "YOU" and (i["order"] or i["order_reason"]) for i in imp)
    ms103 = next(i for i in imp if i["id"] == "MS-103")  # 6 mm offcut: the sample orders are all 2 mm
    assert ms103["order"] is None and "thickness" in ms103["order_reason"]


def test_photo_upload_show_replace_and_remove():
    assert "No photo yet" in c.get("/passport/steel/A1").text
    r = c.post("/api/photo/steel/A1", content=PNG, headers={"Content-Type": "image/png"})
    assert r.status_code == 200 and r.json()["photo"].startswith("/api/photo/steel/A1")
    got = c.get("/api/photo/steel/A1")
    assert got.status_code == 200 and got.content == PNG and got.headers["content-type"] == "image/png"
    assert items("durable")["steel:A1"]["photo"].startswith("/api/photo/steel/A1")
    assert items("durable")["steel:A2"]["photo"] is None  # never a photo the seller did not upload
    assert "uploaded by the seller" in c.get("/passport/steel/A1").text
    assert c.post("/api/photo/steel/A1", content=JPEG, headers={"Content-Type": "image/jpeg"}).status_code == 200
    assert c.get("/api/photo/steel/A1").headers["content-type"] == "image/jpeg"  # replaced, not duplicated
    assert c.delete("/api/photo/steel/A1").json() == {"removed": True}
    assert c.get("/api/photo/steel/A1").status_code == 404 and items("durable")["steel:A1"]["photo"] is None


def test_photo_upload_is_checked():
    post = lambda body, ctype, rid="A1": c.post(f"/api/photo/steel/{rid}", content=body, headers={"Content-Type": ctype})
    assert post(b"hello", "text/plain").status_code == 415
    assert post(b"", "image/png").status_code == 422
    assert post(b"not really a png", "image/png").status_code == 422
    assert post(PNG, "image/jpeg").status_code == 422  # declared type must match the bytes
    assert post(b"\x89PNG\r\n\x1a\n" + b"0" * main.PHOTO_MAX, "image/png").status_code == 413
    assert post(PNG, "image/png", rid="ZZ").status_code == 404
    assert c.post("/api/photo/nowhere/A1", content=PNG, headers={"Content-Type": "image/png"}).status_code == 404


# ---------------- illustrative photos ----------------
def test_illustrative_photo_matches_the_kind_of_item_and_is_credited():
    dur, fresh = items("durable"), items("fresh")
    photo = lambda key: (dur.get(key) or fresh.get(key))["illustrative"]["url"]
    assert photo("steel:A1").startswith("/static/illustrative/mild_steel_")
    assert photo("sheets:Fp1").startswith("/static/illustrative/plywood_")
    assert photo("bars:G1").startswith("/static/illustrative/steel_bars_pipes_")
    assert dur["sheets:Ass1"]["illustrative"] is None  # no stainless photo: show none rather than a wrong one
    assert photo("food:H1").startswith("/static/illustrative/tomatoes_")
    assert photo("food:C2").startswith("/static/illustrative/cooked_rice_")  # biryani
    assert fresh["food:K3"]["illustrative"] is None  # used cooking oil
    assert photo("steel:A1") == items("durable")["steel:A1"]["illustrative"]["url"]  # the same item always gets the same photo
    assert len({it["illustrative"]["url"] for it in dur.values() if it.get("material") == "mild_steel" and it["illustrative"]}) >= 2  # variety
    for it in [*dur.values(), *fresh.values()]:
        il = it["illustrative"]
        if il:
            assert il["author"] and il["licence"] and il["source_url"].startswith("https://commons.wikimedia.org/")
            assert c.get(il["url"]).status_code == 200


def test_seller_photo_replaces_the_illustrative_one():
    assert items("durable")["steel:A1"]["illustrative"] is not None
    c.post("/api/photo/steel/A1", content=PNG, headers={"Content-Type": "image/png"})
    a1 = items("durable")["steel:A1"]
    assert a1["photo"] and a1["illustrative"] is None


def test_every_illustrative_photo_has_a_reusable_licence():
    import json
    credits = json.loads((main.ROOT / "static" / "illustrative" / "credits.json").read_text(encoding="utf-8"))
    from collections import Counter
    per = Counter(cr["category"] for cr in credits.values())
    cats = ("mild_steel", "aluminium", "plywood", "steel_bars_pipes", "tomatoes", "onions", "vegetables", "bread", "paneer", "cooked_rice")
    assert all(2 <= per[c] <= 3 for c in cats) and per["hero_workshop"] == 1 and per["hero_kitchen"] == 1
    for key, cr in credits.items():
        f = main.ROOT / "static" / "illustrative" / f"{key}.jpg"
        assert f.exists() and cr["file"] == f"/static/illustrative/{key}.jpg" and cr["width"] <= 1600
        assert cr["author"] and cr["source_url"].startswith("https://commons.wikimedia.org/") and cr["shows"]
        assert cr["licence"] in ("CC0", "Public domain") or cr["licence"].startswith(("CC BY 2", "CC BY 3", "CC BY 4", "CC BY-SA"))
        assert "NC" not in cr["licence"] and "ND" not in cr["licence"]


def test_passport_upload_opens_the_camera_on_phones():
    page = c.get("/passport/steel/A1").text
    assert 'accept="image/*"' in page and 'capture="environment"' in page
