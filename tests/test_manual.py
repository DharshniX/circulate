"""Manual entry: one item typed by hand gets the CSV importer's checks and surplus rules."""
import base64
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main
from conftest import sign_in
from engine import connectors as CN
from engine import importer as IMP

c = TestClient(main.app)
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
PHOTO = "data:image/png;base64," + base64.b64encode(PNG).decode()
OFFCUT = {"material": "mild_steel", "length_mm": "1100", "width_mm": "450", "thickness_mm": "3", "grade": "e250",
          "condition": "good", "is_full_sheet": False, "days_unused": "12", "seller_id": "Fabricator A"}
FULL_RECENT = {**OFFCUT, "length_mm": "2500", "width_mm": "1250", "is_full_sheet": True, "days_unused": "4"}
LOT = {"product": "Paneer", "category": "dairy", "quantity_kg": "6", "storage": "chilled", "hours_left": "10", "seller_id": "Hotel kitchen H"}


@pytest.fixture(autouse=True)
def temp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")
    monkeypatch.setattr(main, "PHOTOS", tmp_path / "photos")
    sign_in(c, "admin")  # a fresh database each test: sign in again


def add(lane, fields, **kw):
    return c.post(f"/api/manual/{lane}", json={"fields": fields, **kw})


def browse(lane):
    return {i["id"]: i for i in c.get(f"/api/browse/{lane}").json()["items"] if i["key"].startswith(f"import_{lane}")}


def test_valid_offcut_is_found_as_surplus_and_listed_with_a_passport():
    r = add("durable", OFFCUT).json()
    assert r["listed"] and r["surplus"] and r["rules"] == ["Offcut: not a full sheet"] and r["label"] == "sample"
    assert r["item"]["grade"] == "E250" and r["item_id"].startswith("M-")
    page = c.get(r["passport"])
    assert page.status_code == 200 and "Entered by hand by the seller" in page.text and "Offcut: not a full sheet" in page.text
    item = browse("durable")[r["item_id"]]
    assert item["source"] == "Entered by hand by the seller" and item["seller_name"] == "Fabricator A"


def test_invalid_item_shows_plain_errors_per_field_and_lists_nothing():
    bad = {**OFFCUT, "material": "copper", "width_mm": "-300", "thickness_mm": "", "condition": "rusty", "days_unused": "4.5"}
    r = add("durable", bad)
    assert r.status_code == 422
    errors = r.json()["detail"]["errors"]
    assert "Unknown material 'copper'" in errors["material"]
    assert errors["width_mm"] == "Must be more than 0."
    assert errors["thickness_mm"] == "This is needed."
    assert errors["condition"] == "Choose one of the options."
    assert errors["days_unused"] == "Enter a whole number."
    assert browse("durable") == {}


def test_same_checks_as_a_csv_row():
    fields = {**OFFCUT, "item_id": "X1", "days_unused": "40"}
    by_hand = IMP.check_one("durable", fields)
    csv_text = CN.ManualEntry(fields).stock_csv("durable")[0].text
    by_csv = IMP.run_import("durable", csv_text)["surplus"][0]
    assert by_hand["rules"] == by_csv["rules"] == ["Offcut: not a full sheet", "Unused for 40 days (limit 30)"]
    assert by_hand["item"] == by_csv["item"]


def test_with_photo_saved_as_the_sellers_own_photo():
    r = add("durable", OFFCUT, photo=PHOTO).json()
    assert r["listed"] and r["photo"].startswith(f"/api/photo/{r['stock_key']}/{r['item_id']}")
    assert c.get(r["photo"]).content == PNG
    item = browse("durable")[r["item_id"]]
    assert item["photo"] and item["illustrative"] is None  # the seller's photo replaces the illustrative one


def test_without_photo_has_no_seller_photo():
    r = add("durable", OFFCUT).json()
    assert r["photo"] is None and browse("durable")[r["item_id"]]["photo"] is None


def test_a_bad_photo_is_a_field_error_and_nothing_is_listed():
    r = add("durable", OFFCUT, photo="data:image/png;base64," + base64.b64encode(b"not an image").decode())
    assert r.status_code == 422 and "photo" in r.json()["detail"]["errors"]
    assert add("durable", OFFCUT, photo="nonsense").status_code == 422
    assert browse("durable") == {}


def test_not_surplus_is_said_plainly_and_not_listed():
    r = add("durable", FULL_RECENT).json()
    assert not r["surplus"] and not r["listed"] and r["kept_reason"] == "Full sheet, used within the last 30 days"
    assert "Not surplus" in r["message"] and browse("durable") == {}


def test_list_it_anyway_uses_the_rule_listed_by_the_seller():
    first = add("durable", FULL_RECENT).json()
    r = add("durable", {**FULL_RECENT, "item_id": first["item_id"]}, list_anyway=True, measured=True).json()
    assert r["listed"] and not r["surplus"] and r["listed_rules"] == ["Listed by the seller"] and r["item_id"] == first["item_id"]
    item = browse("durable")[first["item_id"]]
    assert item["rules"] == ["Listed by the seller"] and item["label"] == "measured"


def test_fresh_lot_with_hours_left_is_urgent():
    r = add("fresh", LOT).json()
    assert r["listed"] and r["urgent"] and r["rules"] == ["Urgent: 10 h left (limit 24 h)"]
    assert browse("fresh")[r["item_id"]]["product"] == "Paneer"


def test_fresh_use_by_time_is_converted_to_hours_left():
    now = datetime(2026, 10, 8, 9, 0)
    lot = {k: v for k, v in LOT.items() if k != "hours_left"}
    r = c.post("/api/manual/fresh", params={"now": now.isoformat()},
               json={"fields": lot, "use_by": (now + timedelta(hours=30)).isoformat()}).json()
    assert r["item"]["hours_left"] == 30 and not r["surplus"] and not r["listed"]  # 30 h is more than the 24 h rule
    past = c.post("/api/manual/fresh", params={"now": now.isoformat()},
                  json={"fields": lot, "use_by": (now - timedelta(hours=1)).isoformat()})
    assert past.status_code == 422 and "already passed" in past.json()["detail"]["errors"]["use_by"]


def test_fresh_errors_are_per_field():
    r = add("fresh", {**LOT, "category": "leafy_greens", "storage": "fridge", "quantity_kg": ""})
    e = r.json()["detail"]["errors"]
    assert set(e) == {"category", "storage", "quantity_kg"}


def test_manual_entry_connector_and_options():
    keys = {x["key"]: x["status"] for x in c.get("/api/connectors").json()["connectors"]}
    assert keys["manual"] == "active" and len(keys) == 6
    o = c.get("/api/manual/options").json()
    assert {"key": "mild_steel", "name": "Mild steel", "bar": False} in o["materials"] and "chilled" in o["storage"] and "good" in o["conditions"]
    assert {"key": "tmt_rebar", "name": "TMT rebar", "bar": True} in o["materials"] and o["rebar"]["grades"][1] == "Fe500"


def test_data_quality_counts_hand_entered_items_separately():
    add("durable", OFFCUT)
    rows = c.get("/api/data-quality").json()["rows"]
    assert {"area": "Entered by hand, durable", "label": "sample", "count": 1} in rows
