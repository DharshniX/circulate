"""Add stock as separate pages: direct links, role redirects, and the existing import endpoints still work."""
import pytest
from fastapi.testclient import TestClient

import app.main as main

PAGES = {"/seller/add": "add", "/seller/add/csv": "add-csv", "/seller/add/sync": "add-sync", "/seller/add/manual": "add-manual"}
CSV = "item_id,seller_id,material,length_mm,width_mm,thickness_mm,grade,condition,is_full_sheet,days_unused\nP1,A,mild_steel,800,400,3,E250,good,false,5\n"


@pytest.fixture(autouse=True)
def temp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")
    monkeypatch.setattr(main, "ERP_DROP", tmp_path / "erp_drop")
    monkeypatch.setattr(main, "SYNC_MIN_AGE_SECONDS", 0)


def signed(role, business):
    c = TestClient(main.app)
    assert c.post("/api/session", json={"role": role, "business_id": business}).status_code == 200
    return c


def test_each_url_opens_its_page_for_a_seller():
    c = signed("seller", "steel:A")
    for url, view in PAGES.items():
        r = c.get(url, follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == f"/app#seller/{view}/durable", url
    fresh = signed("seller", "food:H").get("/seller/add/sync", follow_redirects=False)
    assert fresh.headers["location"] == "/app#seller/add-sync/fresh"  # a food seller lands in its own lane
    assert c.get("/seller/add/nope").status_code == 404
    old = c.get("/seller/connect", follow_redirects=False)
    assert old.headers["location"] == "/app#seller/add/durable"  # the old combined Connect screen


def test_buyers_and_signed_out_visitors_are_redirected():
    buyer = signed("buyer", "steel:W")
    for url in PAGES:
        r = buyer.get(url, follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/app"
    out = TestClient(main.app).get("/seller/add/csv", follow_redirects=False)
    assert out.status_code == 307 and out.headers["location"] == "/"


def test_the_menu_has_the_add_stock_group_in_order():
    s = signed("seller", "steel:A").get("/api/session").json()
    group = next(g for g in s["menu"] if g["group"] == "Add stock")
    assert [i["view"] for i in group["items"]] == ["add", "add-csv", "add-sync", "add-manual"]
    assert "connect" not in {i["view"] for g in s["menu"] for i in g["items"]}
    b = signed("buyer", "steel:W").get("/api/session").json()
    assert not any(i["view"].startswith("add") for g in b["menu"] for i in g["items"])


def test_app_page_defines_all_four_views():
    js = (main.ROOT / "static" / "app.js").read_text(encoding="utf-8")
    for fn in ("vAdd", "vAddCsv", "vAddSync", "vAddManual"):
        assert f"async function {fn}(el)" in js
    assert "OLD_VIEWS" in js and "history.pushState" in js  # old links redirect; Back works


def test_csv_import_still_works_through_the_existing_endpoints():
    c = signed("seller", "steel:A")
    prev = c.post("/api/import/preview", json={"lane": "durable", "csv_text": CSV}).json()
    assert prev["rows_read"] == 1 and len(prev["surplus"]) == 1
    listed = c.post("/api/import/list", json={"lane": "durable", "csv_text": CSV}).json()
    assert listed["listed"] == 1 and listed["label"] == "sample"
    assert c.post("/api/import/preview", json={"lane": "durable", "csv_text": ""}).status_code == 422


def test_sync_check_still_works(tmp_path):
    c = signed("seller", "steel:A")
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "stock.csv").write_text(CSV)
    assert c.put("/api/connectors/folder/durable", json={"path": str(folder), "enabled": True}).status_code == 200
    entries = c.post("/api/connectors/folder/durable/sync", json={}).json()["entries"]
    assert [e["status"] for e in entries] == ["imported"]
    assert c.post("/api/connectors/folder/durable/sync", json={}).json()["entries"] == []  # duplicates are skipped


def test_manual_save_still_works():
    c = signed("seller", "steel:A")
    fields = {"material": "mild_steel", "length_mm": "800", "width_mm": "400", "thickness_mm": "3", "grade": "E250", "condition": "good",
              "is_full_sheet": False, "days_unused": "5", "seller_id": "Sri Ram"}
    r = c.post("/api/manual/durable", json={"fields": fields}).json()
    assert r["listed"] and r["surplus"]
    assert c.post("/api/manual/durable", json={"fields": {**fields, "width_mm": "-3"}}).status_code == 422
