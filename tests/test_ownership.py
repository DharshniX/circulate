"""Phase 3: every /api route needs a session, roles are enforced, and each business sees only its own data
(the marketplace stays open: buyers see every seller's stock, and matching uses every buyer's open orders)."""
import json
import sqlite3
from datetime import date, timedelta

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main
from conftest import sign_in

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
CSV = ("item_id,seller_id,material,length_mm,width_mm,thickness_mm,grade,condition,is_full_sheet,days_unused\n"
       "SR1,A,mild_steel,1250,600,2,E250,good,false,12\n")
OFFCUT = {"material": "mild_steel", "length_mm": "1250", "width_mm": "600", "thickness_mm": "2", "grade": "E250",
          "condition": "good", "is_full_sheet": False, "days_unused": "10"}
SITE = {"role": "seller", "seller_type": "construction_site", "business_name": "Kaveri Builders site, Porur", "contact_name": "Ravi",
        "email": "site@kaveri.example", "phone": "98765 11111", "area": "Porur", "lane": "durable", "password": "Builders@2026",
        "confirm": "Builders@2026"}
BRACKETS = {"part_name": "bracket", "material": "mild_steel", "length_mm": 150, "width_mm": 100, "thickness_mm": 2,
            "accepted_grades": ["E250"], "quantity": 20, "new_price_per_kg": 62, "buyer_name": "typed by the buyer"}
# Sign-in, start pages and public QR material: everything else under /api needs a session.
PUBLIC_API = {("/api/auth/login", "POST"), ("/api/auth/logout", "POST"), ("/api/auth/register", "POST"),
              ("/api/session", "GET"), ("/api/session", "POST"), ("/api/session", "DELETE"),
              ("/api/qr.svg", "GET"), ("/api/photo/{key}/{rid}", "GET")}


@pytest.fixture(autouse=True)
def temp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")
    monkeypatch.setattr(main, "PHOTOS", tmp_path / "photos")
    monkeypatch.setattr(main, "ERP_DROP", tmp_path / "erp_drop")
    monkeypatch.setattr(main, "SYNC_MIN_AGE_SECONDS", 0)


def who(business="admin"):
    return sign_in(TestClient(main.app), business)


def new_site():
    """A construction site that signs up itself, in Porur."""
    c = TestClient(main.app)
    r = c.post("/api/auth/register", json=SITE)
    assert r.status_code == 200, r.text
    return c, r.json()["business_id"]


def scenario_data(c, key):
    data = c.get(f"/api/scenario/{key}").json()
    for k in ("scenario", "title", "engine", "plan_seller", "metric_word"):
        data.pop(k)
    return data


def accept_s1(ezhil):
    """Ezhil Engineering Works accepts its own sample order S1, the way Order and match does."""
    data = scenario_data(ezhil, "steel")
    own = ezhil.get("/api/own-stock/steel").json()
    data["sellers"] += own["sellers"]
    data["resources"] += own["resources"]
    f = ezhil.post("/api/fulfil", json={"demand_id": "S1", "data": data, "allow_partial": True}).json()
    b = f["best"]
    r = ezhil.post("/api/deals", json={"kind": "flat", "counterfactual": "recycled", "demand_id": "S1",
                                       "buyer_name": "Ezhil Engineering Works, Ambattur", "sellers": b["sellers_used"], "units": b["units"],
                                       "unit_name": "parts", "kg_in_units": b["kg_in_units"], "kg_shipped": b["kg_shipped"],
                                       "route_km": b["route_km"], "value_above_salvage": 1, "scenario": "steel"})
    assert r.status_code == 200, r.text
    return r.json()["id"], b


# ---------------- the two checks ----------------
def test_every_api_route_has_a_session_check():
    unguarded = []
    for r in main.app.routes:
        if not isinstance(r, APIRoute) or not r.path.startswith("/api"):
            continue
        for m in r.methods:
            guarded = any(getattr(d.call, "guard", None) for d in r.dependant.dependencies)
            if (r.path, m) not in PUBLIC_API and not guarded:
                unguarded.append((m, r.path))
    assert unguarded == []


def test_signed_out_gets_401_and_protected_pages_go_to_the_start_page():
    out = TestClient(main.app)
    for path in ("/api/engines", "/api/scenario/steel", "/api/stock/steel", "/api/browse/durable", "/api/requirements",
                 "/api/whatsapp/contacts", "/api/connectors", "/api/data-quality", "/api/account", "/api/become/steel/A3"):
        assert out.get(path).status_code == 401, path
    assert out.post("/api/ledger", json={}).status_code == 401
    assert out.post("/api/import/preview", json={"lane": "durable", "csv_text": CSV}).status_code == 401
    for page in ("/app", "/deals/1/pickup-sheet", "/deals/1/challan"):
        r = out.get(page, follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/", page
    assert out.get("/passport/steel/A1").status_code == 200 and out.get("/api/qr.svg", params={"data": "x"}).status_code == 200
    assert out.get("/health").json() == {"status": "ok"} and out.get("/register").status_code == 200


def test_each_role_gets_403_on_the_other_roles_routes():
    buyer, seller = who("steel:W"), who("steel:A")
    for path in ("/api/stock/steel", "/api/gapfill/demo", "/api/connectors", "/api/manual/options"):
        assert buyer.get(path).status_code == 403, path
    assert buyer.post("/api/import/preview", json={"lane": "durable", "csv_text": CSV}).status_code == 403
    for path in ("/api/browse/durable", "/api/requirements", "/api/requirements/options", "/api/own-stock/steel"):
        assert seller.get(path).status_code == 403, path
    for c in (buyer, seller):
        for path in ("/api/data-quality", "/api/sim-summary", "/api/admin/users"):
            assert c.get(path).status_code == 403, path
        assert c.post("/api/reset", json={}).status_code == 403
    admin = who()
    assert all(admin.get(p).status_code == 200 for p in ("/api/stock/steel", "/api/browse/durable", "/api/data-quality"))


# ---------------- 12. sellers see only their own ----------------
def test_two_sellers_cannot_see_each_others_stock_imports_deals_or_pickups(tmp_path):
    sriram, (site, site_id) = who("steel:A"), new_site()
    assert sriram.post("/api/import/list", json={"lane": "durable", "csv_text": CSV}).json()["listed"] == 1
    added = site.post("/api/manual/durable", json={"fields": OFFCUT}).json()
    assert added["listed"] and added["stock_key"] == f"import_durable@{site_id}"
    mine = sriram.get("/api/stock/import_durable").json()["items"]
    theirs = site.get("/api/stock/import_durable").json()["items"]
    assert [i["id"] for i in mine] == ["SR1"] and [i["id"] for i in theirs] == [added["item_id"]]
    assert mine[0]["seller_name"] == "Sri Ram Fabricators, Ambattur" and theirs[0]["seller_name"] == SITE["business_name"]
    assert sriram.get(f"/api/stock/import_durable@{site_id}").status_code == 404  # asking for another seller's key
    assert {i["seller_id"] for i in sriram.get("/api/stock/steel").json()["items"]} == {"A"}  # sample stock: only its own pieces
    assert site.get("/api/stock/steel").json()["items"] == []
    assert {s["id"] for s in scenario_data(sriram, "steel")["sellers"]} == {"A"} and scenario_data(site, "steel")["sellers"] == []
    assert scenario_data(sriram, "steel")["demands"]  # every open order stays visible, so a seller sees what fits
    # imports: the auto-sync folder, its settings and its log are per seller
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "stock.csv").write_text(CSV.replace("SR1", "SR2"))
    assert sriram.put("/api/connectors/folder/durable", json={"path": str(folder), "enabled": True}).status_code == 200
    assert [e["status"] for e in sriram.post("/api/connectors/folder/durable/sync", json={}).json()["entries"]] == ["imported"]
    assert len(sriram.get("/api/connectors/folder/durable/log").json()["log"]) == 1
    assert site.get("/api/connectors/folder/durable/log").json()["log"] == []
    assert site.get("/api/connectors").json()["folder"]["durable"]["saved"] is False
    assert [i["id"] for i in site.get("/api/stock/import_durable").json()["items"]] == [added["item_id"]]
    # clearing removes only your own
    assert site.post("/api/import/clear", params={"lane": "durable"}).json()["removed"] == 1
    assert {i["id"] for i in sriram.get("/api/stock/import_durable").json()["items"]} == {"SR1", "SR2"}
    # deals and pickups: Sri Ram is in Ezhil's S1 deal, the new site is not
    deal, _ = accept_s1(who("steel:W"))
    assert [d["id"] for d in sriram.post("/api/ledger", json={}).json()["deals"]] == [deal]
    assert site.post("/api/ledger", json={}).json()["deals"] == []
    assert site.get(f"/api/deals/{deal}/logistics").status_code == 404
    assert site.post(f"/api/deals/{deal}/booking", json={"pickup_date": TOMORROW, "slot": "10:00-12:00"}).status_code == 404
    assert site.get(f"/deals/{deal}/pickup-sheet").status_code == 404
    # a photo can be changed only by the seller who listed the item
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    assert site.post("/api/photo/steel/A1", content=png, headers={"Content-Type": "image/png"}).status_code == 403
    assert site.post("/api/photo/import_durable@steel:A/SR1", content=png, headers={"Content-Type": "image/png"}).status_code == 403


# ---------------- 13. the marketplace stays open ----------------
def test_buyers_see_every_sellers_stock_in_browse_surplus():
    sriram, (site, site_id) = who("steel:A"), new_site()
    sriram.post("/api/import/list", json={"lane": "durable", "csv_text": CSV})
    site.post("/api/manual/durable", json={"fields": OFFCUT})
    for buyer in ("steel:W", "food:M"):
        items = who(buyer).get("/api/browse/durable").json()["items"]
        sellers = {i["seller_name"] for i in items}
        assert {"Sri Ram Fabricators, Ambattur", "Kumaran Machine Shop, Guindy", "Vaigai Panel Works, Perungudi",
                "Vetri Gate Works, Avadi", SITE["business_name"]} <= sellers
        assert {i["key"] for i in items} >= {"steel", "sheets", "bars", "import_durable@steel:A", f"import_durable@{site_id}"}


# ---------------- 14. a deal is seen by both sides only ----------------
def test_both_parties_see_a_deal_and_a_third_business_cannot():
    ezhil, sriram, admin = who("steel:W"), who("steel:A"), who()
    deal, b = accept_s1(ezhil)
    assert set(b["sellers_used"]) == {"A", "B"}
    row = sqlite3.connect(main.DB).execute("SELECT buyer_business, seller_business FROM deals WHERE id=?", (deal,)).fetchone()
    assert row[0] == "steel:W" and json.loads(row[1]) == ["steel:A"]  # Kumaran (B) is a sample seller with no account
    for party in (ezhil, sriram, admin):
        assert deal in [d["id"] for d in party.post("/api/ledger", json={}).json()["deals"]]
        assert party.get(f"/api/deals/{deal}/logistics").status_code == 200
        assert party.get(f"/deals/{deal}/challan").status_code == 200
    assert sriram.post(f"/api/deals/{deal}/booking", json={"pickup_date": TOMORROW, "slot": "10:00-12:00"}).status_code == 200
    for third in ("food:H", "food:M", "food:N"):
        c = who(third)
        assert c.post("/api/ledger", json={}).json()["deals"] == []
        assert c.get(f"/api/deals/{deal}/logistics").status_code == 404
        assert c.post(f"/api/deals/{deal}/advance", json={"status": "dispatched"}).status_code == 404
        assert c.get(f"/deals/{deal}/pickup-sheet").status_code == 404
    assert sriram.post(f"/api/deals/{deal}/advance", json={"status": "dispatched"}).status_code == 200


def test_a_buyer_can_accept_only_its_own_order():
    anna = who("food:M")
    r = anna.post("/api/deals", json={"kind": "flat", "counterfactual": "recycled", "demand_id": "S1", "buyer_name": "x", "sellers": ["A"],
                                      "units": 1, "unit_name": "parts", "kg_in_units": 1, "kg_shipped": 1, "route_km": 1,
                                      "value_above_salvage": 1, "scenario": "steel"})
    assert r.status_code == 403 and "your own orders" in r.json()["detail"]


# ---------------- 15. buyers see only their own requirements ----------------
def test_a_buyer_sees_only_its_own_requirements():
    ezhil, thozhan = who("steel:W"), who("food:N")
    mine = ezhil.post("/api/requirements", json={"lane": "durable", "kind": "flat", "fields": BRACKETS, "standing": True}).json()
    assert mine["demand"]["buyer_name"] == "Ezhil Engineering Works, Ambattur"  # a buyer's requirement carries its own name
    food = {"categories": ["bakery"], "quantity": 5, "storage_available": ["ambient"], "min_hours_left_on_arrival": 2, "buyer_type": "food_bank"}
    theirs = thozhan.post("/api/requirements", json={"lane": "fresh", "kind": "perishable", "fields": food}).json()
    assert [r["id"] for r in ezhil.get("/api/requirements").json()["requirements"]] == [mine["id"]]
    assert [r["id"] for r in thozhan.get("/api/requirements").json()["requirements"]] == [theirs["id"]]
    assert thozhan.get("/api/requirements/demands/steel").json()["demands"] == []
    assert thozhan.post(f"/api/requirements/{mine['id']}/close").status_code == 404
    assert {r["id"] for r in who().get("/api/requirements").json()["requirements"]} == {mine["id"], theirs["id"]}
    # a buyer's Order and match shows only its own orders; a seller still sees every open standing order in Gap Fill
    assert [d["id"] for d in scenario_data(ezhil, "steel")["demands"]] == ["S1"]
    assert scenario_data(thozhan, "food")["demands"][0]["id"] == "F2" and len(scenario_data(thozhan, "food")["demands"]) == 1
    assert mine["demand_id"] in [o["id"] for o in who("steel:A").get("/api/gapfill/demo").json()["orders"]]


# ---------------- 16. a new business's stock is matched as its own seller ----------------
def test_a_new_construction_site_sellers_manual_stock_is_on_browse_and_matched():
    site, site_id = new_site()
    added = site.post("/api/manual/durable", json={"fields": OFFCUT}).json()
    ezhil = who("steel:W")
    item = next(i for i in ezhil.get("/api/browse/durable").json()["items"] if i["id"] == added["item_id"])
    assert item["seller_name"] == SITE["business_name"] and item["match_seller"] == site_id and item["approx_location"]
    assert item["order"]["demand_id"] == "S1" and item["distance_km"] > 0
    own = ezhil.get("/api/own-stock/steel").json()
    [seller] = own["sellers"]
    assert (seller["id"], seller["name"], seller["x_km"], seller["y_km"]) == (site_id, SITE["business_name"], *main.AREA_KM["Porur"])
    assert own["approximate"] == [site_id] and seller["salvage"][0]["label"] == "sample"  # the sample scrap price, labelled
    data = scenario_data(ezhil, "steel")
    data["sellers"] += own["sellers"]
    data["resources"] += own["resources"]
    f = ezhil.post("/api/fulfil", json={"demand_id": "S1", "data": data, "seller_ids": [site_id], "allow_partial": True}).json()
    assert f["best"]["sellers_used"] == [site_id] and f["best"]["units"] > 0
    b = f["best"]
    deal = ezhil.post("/api/deals", json={"kind": "flat", "counterfactual": "recycled", "demand_id": "S1", "buyer_name": "Ezhil Engineering Works, Ambattur",
                                          "sellers": [site_id], "units": b["units"], "unit_name": "parts", "kg_in_units": b["kg_in_units"],
                                          "kg_shipped": b["kg_shipped"], "route_km": b["route_km"], "value_above_salvage": 1, "scenario": "steel"}).json()["id"]
    assert [d["id"] for d in site.post("/api/ledger", json={}).json()["deals"]] == [deal]  # the new seller sees its deal
    lg = site.get(f"/api/deals/{deal}/logistics").json()
    assert lg["collect"][0]["seller_name"] == SITE["business_name"] and lg["approx_location"]
    assert "approximate location" in site.get(f"/deals/{deal}/pickup-sheet").text
    assert who().get(f"/api/become/{added['stock_key']}/{added['item_id']}").json()["approx_location"]


def test_areas_map_to_approximate_coordinates():
    assert main._area_xy("Ambattur") == main.AREA_KM["Ambattur"] and main._area_xy("T. Nagar, Chennai") == main.AREA_KM["T Nagar"]
    assert main._area_xy("anna nagar west") == main.AREA_KM["Anna Nagar"] and main._area_xy("Madurai") == main.CITY_CENTRE
    s = TestClient(main.app)
    s.post("/api/auth/register", json={**SITE, "email": "b@x.example", "role": "buyer", "seller_type": None, "area": "Velachery"})
    me = s.get("/api/session").json()
    assert me["approx_location"] and (me["x_km"], me["y_km"]) == main.AREA_KM["Velachery"]
    assert "approx_location" not in who("steel:W").get("/api/session").json()  # demo businesses keep their sample positions


# ---------------- 4. rows saved before accounts: admin only ----------------
def test_rows_saved_before_accounts_are_visible_to_the_admin_only():
    who()  # creates the database
    con = sqlite3.connect(main.DB)
    item = {"id": "OLD1", "seller_id": "old", "material": "mild_steel", "length_mm": 800, "width_mm": 400, "thickness_mm": 2,
            "grade": "E250", "condition": "good", "is_full_sheet": False, "days_unused": 40, "label": "sample", "kind": "flat"}
    con.execute("INSERT INTO imported_stock (owner, lane, item_id, item, rules, label, listed_at, source) VALUES ('', 'durable', 'OLD1', ?, '[]', 'sample', 'x', NULL)",
                (json.dumps(item),))
    con.execute("INSERT INTO deals (kind, demand_id, buyer_name, sellers, status, is_simulated, evidence, buyer_confirmed, history, units, unit_name,"
                " kg_in_units, kg_shipped, route_km, value_above_salvage, counterfactual, scenario) VALUES "
                "('flat','S1','Ezhil Engineering Works, Ambattur','[\"A\"]','agreed',1,'[]',0,'[]',1,'parts',1,1,1,1,'recycled','steel')")
    con.commit()
    con.close()
    for bid in ("steel:A", "steel:W"):
        c = who(bid)
        assert c.post("/api/ledger", json={}).json()["deals"] == []
    assert "OLD1" not in {i["id"] for i in who("steel:A").get("/api/stock/import_durable").json()["items"]}
    assert "OLD1" not in {i["id"] for i in who("steel:W").get("/api/browse/durable").json()["items"]}
    admin = who()
    assert len(admin.post("/api/ledger", json={}).json()["deals"]) == 1
    old = next(i for i in admin.get("/api/browse/durable").json()["items"] if i["id"] == "OLD1")
    assert old["order"] is None and "before accounts" in old["order_reason"]


def test_an_older_database_gets_owner_columns_and_keeps_its_rows(tmp_path, monkeypatch):
    old = tmp_path / "old.db"
    con = sqlite3.connect(old)
    con.execute("CREATE TABLE imported_stock (lane TEXT, item_id TEXT, item TEXT, rules TEXT, label TEXT, listed_at TEXT, PRIMARY KEY (lane, item_id))")
    con.execute("INSERT INTO imported_stock VALUES ('durable', 'X1', '{}', '[]', 'sample', 'then')")
    con.execute("CREATE TABLE sync_folders (lane TEXT PRIMARY KEY, path TEXT, enabled INTEGER, measured INTEGER, days_unused_limit INTEGER, hours_left_limit REAL)")
    con.execute("INSERT INTO sync_folders VALUES ('durable', 'C:/x', 1, 0, 30, 24)")
    con.commit()
    con.close()
    monkeypatch.setattr(main, "DB", old)
    with main.closing(main.db()) as c2:
        assert [tuple(r) for r in c2.execute("SELECT owner, lane, item_id, source FROM imported_stock")] == [("", "durable", "X1", None)]
        assert [tuple(r) for r in c2.execute("SELECT owner, lane, enabled FROM sync_folders")] == [("", "durable", 1)]


# ---------------- 8. View as shows exactly what that business sees ----------------
def test_view_as_shows_exactly_what_that_business_sees():
    sriram, (site, _) = who("steel:A"), new_site()
    sriram.post("/api/import/list", json={"lane": "durable", "csv_text": CSV})
    site.post("/api/manual/durable", json={"fields": OFFCUT})
    admin = who()
    assert len(admin.get("/api/stock/import_durable").json()["items"]) == 2  # the admin sees every seller's
    admin.post("/api/session/view-as", json={"business_id": "steel:A"})
    assert admin.get("/api/stock/import_durable").json() == sriram.get("/api/stock/import_durable").json()
    assert admin.get("/api/stock/steel").json() == sriram.get("/api/stock/steel").json()
    assert admin.get("/api/browse/durable").status_code == 403  # Sri Ram is a seller
    assert admin.get("/api/admin/users").status_code == 200  # managing accounts still works while viewing as


# ---------------- WhatsApp numbers and alerts are per business ----------------
def test_whatsapp_numbers_and_alerts_are_per_business():
    ezhil, sriram = who("steel:W"), who("steel:A")
    reg = lambda c, phone, business="anything": c.post("/api/whatsapp/contacts", json={"business": business, "role": "buyer", "name": "N",
                                                                                      "phone": phone, "alerts": ["order_accepted"], "consent": True})
    mine = reg(ezhil, "9000000001", "Sri Ram Fabricators, Ambattur").json()
    assert mine["business"] == "Ezhil Engineering Works, Ambattur"  # a business registers numbers for itself only
    reg(sriram, "9000000002")
    assert [c["id"] for c in ezhil.get("/api/whatsapp/contacts").json()["contacts"]] == [mine["id"]]
    assert ezhil.get("/api/whatsapp/contacts").json()["businesses"] == [{"name": "Ezhil Engineering Works, Ambattur", "role": "buyer"}]
    assert sriram.delete(f"/api/whatsapp/contacts/{mine['id']}").status_code == 404
    assert len(who().get("/api/whatsapp/contacts").json()["contacts"]) == 2
    accept_s1(ezhil)
    assert [n["to"]["business"] for n in ezhil.get("/api/whatsapp/notifications").json()["notifications"]] == ["Ezhil Engineering Works, Ambattur"]
    assert sriram.get("/api/whatsapp/notifications").json()["notifications"] == []
    n = ezhil.get("/api/whatsapp/notifications").json()["notifications"][0]
    assert sriram.post(f"/api/whatsapp/notifications/{n['id']}/status", json={"status": "sent"}).status_code == 404
    public = TestClient(main.app).post("/register", json={"business": "Thozhan Food Bank, Saidapet", "role": "food bank", "name": "V",
                                                           "phone": "9000000003", "alerts": ["fresh"], "consent": True})
    assert public.status_code == 200  # the phone registration page needs no sign-in


# ---------------- demo check: the seeded accounts still run the whole story ----------------
def test_demo_story_with_the_seeded_accounts():
    sriram = who("steel:A")
    g = sriram.get("/api/gapfill/demo").json()
    plan = sriram.post("/api/gapfill", params={"record": "true"}, json=g).json()
    assert plan["checks"]["ok"] and plan["neighbours"]  # 17. Sold before it's cut
    ezhil = who("steel:W")
    ezhil.post("/api/whatsapp/contacts", json={"business": "", "role": "buyer", "name": "Ezhil", "phone": "9000000001",
                                               "alerts": ["order_accepted"], "consent": True})
    deal, _ = accept_s1(ezhil)  # 18. accept the S1 bracket order; the WhatsApp alert is prepared
    [alert] = ezhil.get("/api/whatsapp/notifications").json()["notifications"]
    assert alert["event"] == "order_accepted" and alert["status"] == "ready" and f"demo deal #{deal}" in alert["text"]
    for c in (sriram, ezhil):  # 19. both see that deal
        assert deal in [d["id"] for d in c.post("/api/ledger", json={}).json()["deals"]]
