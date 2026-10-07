"""Connect your system: import from ERP or POS export (CSV) and surplus detection."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from engine.importer import run_import

c = TestClient(main.app)
EX = Path(__file__).resolve().parent.parent / "app" / "static" / "examples"
DUR_HEAD = "item_id,seller_id,material,length_mm,width_mm,thickness_mm,grade,condition,is_full_sheet,days_unused\n"
FRESH_HEAD = "item_id,seller_id,product,category,quantity_kg,storage,hours_left\n"


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")


def preview(lane, text, **kw):
    return c.post("/api/import/preview", json={"lane": lane, "csv_text": text, **kw})


# ---------------- good rows, bad rows, empty file ----------------
def test_good_rows_are_accepted():
    r = preview("durable", DUR_HEAD + "P1,A,mild_steel,2500,1250,6,E250,new,true,3\nP2,A,Mild Steel,800,400,3,e250,Good,no,5\n").json()
    assert r["accepted"] == 2 and not r["rejected"]
    p2 = next(x for x in r["surplus"] if x["item"]["id"] == "P2")["item"]
    assert p2["material"] == "mild_steel" and p2["grade"] == "E250" and p2["condition"] == "good"
    assert p2["label"] == "sample"
    r = preview("fresh", FRESH_HEAD + "L1,K,Tomatoes,vegetables,14,ambient,40\n").json()
    assert r["accepted"] == 1 and not r["rejected"]


def test_bad_rows_are_rejected_with_reason():
    text = DUR_HEAD + ("B1,A,mild_steel,900,-300,6,E250,good,false,8\n"
                       "B2,A,copper,600,400,2,C101,good,false,30\n"
                       "B3,A,mild_steel,700,500,5,E250,good,maybe,40\n"
                       "B4,A,mild_steel,700,500,5,E250,good,false,\n"
                       "B5,A,mild_steel,700,500,5,E250,good,false,4\n"
                       "B5,A,mild_steel,700,500,5,E250,good,false,4\n")
    r = preview("durable", text).json()
    why = {x["item_id"]: x["reason"] for x in r["rejected"]}
    assert "width_mm" in why["B1"] and "greater than 0" in why["B1"]
    assert "unknown material 'copper'" in why["B2"]
    assert "is_full_sheet" in why["B3"]
    assert "days_unused: empty cell" in why["B4"]
    assert "appears twice" in why["B5"]
    assert r["accepted"] == 1 and [x["row"] for x in r["rejected"]] == [2, 3, 4, 5, 7]
    fresh = preview("fresh", FRESH_HEAD + "F1,K,Spinach,leafy_greens,3,chilled,12\nF2,K,Milk,dairy,10,fridge,8\n").json()
    assert {x["item_id"] for x in fresh["rejected"]} == {"F1", "F2"}
    assert "category" in fresh["rejected"][0]["reason"] and "storage" in fresh["rejected"][1]["reason"]


@pytest.mark.parametrize("text", ["", "   \n", "﻿", DUR_HEAD])
def test_empty_file_is_refused_with_a_fix(text):
    r = preview("durable", text)
    assert r.status_code == 422
    assert "empty" in r.json()["detail"] or "no stock rows" in r.json()["detail"]


def test_wrong_columns_are_named():
    r = preview("durable", FRESH_HEAD + "L1,K,Tomatoes,vegetables,14,ambient,40\n")
    assert r.status_code == 422 and "length_mm" in r.json()["detail"]


def test_semicolon_export_and_byte_order_mark():
    text = "﻿" + DUR_HEAD.replace(",", ";") + "P1;A;mild_steel;800;400;3;E250;good;false;5\n"
    assert preview("durable", text).json()["accepted"] == 1


# ---------------- each detection rule ----------------
def test_durable_offcut_rule():
    r = run_import("durable", DUR_HEAD + "O1,A,mild_steel,800,400,3,E250,good,false,0\n")
    assert r["surplus"][0]["rules"] == ["Offcut: not a full sheet"]


def test_durable_days_unused_rule_and_editable_limit():
    text = DUR_HEAD + "S1,A,mild_steel,2500,1250,6,E250,new,true,30\nS2,A,mild_steel,2500,1250,6,E250,new,true,29\n"
    r = run_import("durable", text)  # default N = 30, and "at least" includes 30
    assert [x["item"]["id"] for x in r["surplus"]] == ["S1"] and r["surplus"][0]["rules"] == ["Unused for 30 days (limit 30)"]
    assert [x["item"]["id"] for x in r["kept"]] == ["S2"] and "Full sheet" in r["kept"][0]["reason"]
    r = preview("durable", text, days_unused_limit=60).json()
    assert not r["surplus"] and len(r["kept"]) == 2


def test_durable_both_rules_are_shown():
    r = run_import("durable", DUR_HEAD + "X1,A,mild_steel,800,400,3,E250,good,false,90\n")
    assert len(r["surplus"][0]["rules"]) == 2


def test_fresh_urgent_rule_and_editable_limit():
    text = FRESH_HEAD + "U1,K,Paneer,dairy,6,chilled,24\nU2,K,Onions,vegetables,25,ambient,24.5\n"
    r = run_import("fresh", text)  # default H = 24, and "at most" includes 24
    assert [x["item"]["id"] for x in r["surplus"]] == ["U1"]
    assert r["surplus"][0]["urgent"] and r["surplus"][0]["rules"][0].startswith("Urgent")
    assert [x["item"]["id"] for x in r["kept"]] == ["U2"]
    r = preview("fresh", text, hours_left_limit=12).json()
    assert not r["surplus"]


def test_example_files_download_and_parse():
    for lane, surplus, kept, bad in (("durable", 5, 3, 3), ("fresh", 4, 3, 3)):
        d = c.get(f"/api/import/example/{lane}")
        assert d.status_code == 200 and "attachment" in d.headers["content-disposition"]
        r = preview(lane, d.text).json()
        assert (len(r["surplus"]), len(r["kept"]), len(r["rejected"])) == (surplus, kept, bad)
    assert c.get("/api/import/example/textile").status_code == 422


# ---------------- listing on the marketplace ----------------
def test_list_surplus_creates_passports_labelled_sample():
    text = (EX / "durable_stock_example.csv").read_text()
    r = c.post("/api/import/list", json={"lane": "durable", "csv_text": text}).json()
    assert r["listed"] == 5 and r["label"] == "sample"
    items = c.get("/api/stock/import_durable").json()["items"]
    assert len(items) == 5 and all(i["label"] == "sample" and i["scrap_value"] is None for i in items)
    assert {i["id"] for i in items} == {"MS-102", "MS-103", "MS-104", "AL-201", "SS-301"}  # working stock is not listed
    page = c.get(items[0]["passport"])
    assert page.status_code == 200 and "Import from ERP or POS export (CSV)" in page.text and "No scrap price" in page.text
    assert c.get("/labels/import_durable").status_code == 200
    assert c.get("/api/stock/import_fresh").json()["items"] == []
    assert c.post("/api/import/clear", params={"lane": "durable"}).json()["removed"] == 5


def test_measured_only_when_ticked_and_shown_in_data_quality():
    text = (EX / "fresh_stock_example.csv").read_text()
    r = c.post("/api/import/list", json={"lane": "fresh", "csv_text": text, "measured": True}).json()
    assert r["label"] == "measured" and all(i["label"] == "measured" and i["urgent"] for i in r["items"])
    rows = c.get("/api/data-quality").json()["rows"]
    assert {"area": "Imported stock (CSV), fresh", "label": "measured", "count": 4} in rows


def test_listing_nothing_is_refused():
    r = c.post("/api/import/list", json={"lane": "fresh", "csv_text": FRESH_HEAD + "L1,K,Rice,packaged,30,ambient,500\n"})
    assert r.status_code == 422 and "Nothing was found as surplus" in r.json()["detail"]


# ---------------- next cutting job -> Sold before it's cut ----------------
def test_cutting_job_goes_to_gapfill():
    job = {"sheet_length_mm": 2500, "sheet_width_mm": 1250, "thickness_mm": 6, "grade": "e250", "part_name": "flange",
           "part_length_mm": 400, "part_width_mm": 300, "quantity": 6}
    g = c.post("/api/import/cutting-job", json=job).json()["gapfill"]
    assert g["sheet"]["grade"] == "E250" and g["job"]["part_name"] == "flange" and g["job"]["quantity"] == 6
    plan = c.post("/api/gapfill", json=g).json()
    assert plan["checks"]["ok"] and plan["own"]["units"] == 6


def test_cutting_job_that_does_not_fit_is_refused_with_reason():
    job = {"sheet_length_mm": 1000, "sheet_width_mm": 500, "thickness_mm": 6, "grade": "E250",
           "part_length_mm": 600, "part_width_mm": 400, "quantity": 4}
    r = c.post("/api/import/cutting-job", json=job)
    assert r.status_code == 422 and "fit on this sheet" in r.json()["detail"]
