"""Construction-site supply: TMT rebar and shuttering plywood, seller-type chips, and collection requests (asset-light)."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main
from conftest import sign_in
from engine import materials as M
from engine.engines import REGISTRY
from engine.models import LinearDemand, LinearStock, Settings

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
SITE = {"role": "seller", "seller_type": "construction_site", "business_name": "Velan Builders site, Porur", "contact_name": "Velan",
        "email": "velan@example.com", "phone": "98765 43210", "area": "Porur", "lane": "durable", "password": "buildit123",
        "confirm": "buildit123"}
BAR = {"material": "tmt_rebar", "length_mm": "1800", "diameter_mm": "12", "grade": "Fe500", "condition": "good",
       "is_full_sheet": False, "days_unused": "20"}
PLY = {"material": "shuttering_plywood", "length_mm": "2440", "width_mm": "1220", "thickness_mm": "18", "grade": "BWP",
       "condition": "used_once", "is_full_sheet": True, "days_unused": "40"}


@pytest.fixture(autouse=True)
def temp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")
    monkeypatch.setattr(main, "PHOTOS", tmp_path / "photos")


def site():
    c = TestClient(main.app)
    assert c.post("/api/auth/register", json=SITE).status_code == 200
    return c


def add(c, fields, **kw):
    r = c.post("/api/manual/durable", json={"fields": fields, **kw})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------- rebar ----------------
def test_rebar_weight_is_diameter_squared_over_162_and_labelled_an_assumption():
    assert [round(M.rebar_kg_per_m(d), 3) for d in (8, 10, 12, 16, 20, 25, 32)] == [0.395, 0.617, 0.889, 1.58, 2.469, 3.858, 6.321]
    c = site()
    r = add(c, BAR)
    assert r["item"]["kind"] == "linear" and r["item"]["profile"] == "TMT bar 12" and r["item"]["mass_per_m_kg"] == 0.889
    assert r["rules"] == ["Offcut: not a full-length bar"]
    [it] = c.get("/api/stock/import_durable").json()["items"]
    assert it["kg"] == pytest.approx(1.8 * 0.889, abs=0.01) and it["weight_label"] == "assumption" and "d" in it["weight_note"]
    page = c.get(it["passport"]).text
    assert "Assumption: Weight per metre = diameter squared / 162 kg" in page
    o = c.get("/api/manual/options").json()
    assert o["rebar"]["grades"] == ["Fe415", "Fe500", "Fe500D", "Fe550", "Fe550D"] and o["rebar"]["diameters_mm"] == [8, 10, 12, 16, 20, 25, 32]


def test_rebar_entry_is_checked_per_field():
    c = site()
    for bad, field in (({**BAR, "diameter_mm": "11"}, "diameter_mm"), ({**BAR, "grade": "Fe600"}, "grade"), ({**BAR, "diameter_mm": ""}, "diameter_mm")):
        r = c.post("/api/manual/durable", json={"fields": bad})
        assert r.status_code == 422 and field in r.json()["detail"]["errors"], bad
    assert add(c, {**BAR, "grade": "fe500d"})["item"]["grade"] == "Fe500D"  # written the usual way


def test_a_fe500_order_is_not_filled_from_fe415_and_says_why():
    eng, s = REGISTRY["linear"], Settings()
    order = LinearDemand(id="R1", buyer_id="W", buyer_name="Ezhil", part_name="bar", material="tmt_rebar", profile="TMT bar 12",
                         mass_per_m_kg=0.889, cut_length_mm=1000, accepted_grades=["Fe500"], quantity=4, new_price_per_kg=64)
    bar = lambda g: LinearStock(id="X", seller_id="K", material="tmt_rebar", profile="TMT bar 12", length_mm=2400, mass_per_m_kg=0.889, grade=g)
    assert eng.check(bar("Fe415"), order, None, s) == "grade Fe415 not accepted"
    assert eng.check(bar("Fe500"), order, None, s) is None and eng.capacity(bar("Fe500"), order, s) == 2
    # the same through the app: a buyer's Fe500 requirement, a site's Fe415 and Fe500 bars
    c = site()
    weak, good = add(c, {**BAR, "grade": "Fe415"}), add(c, BAR)
    ezhil = sign_in(TestClient(main.app), "steel:W")
    o = ezhil.get("/api/requirements/options").json()
    tmt = next(p for p in o["profiles"] if p["profile"] == "TMT bar 12")
    assert tmt["material"] == "tmt_rebar" and tmt["mass_per_m_kg"] == 0.889 and tmt["weight_label"] == "assumption" and "Fe500" in tmt["grades"]
    assert all(m["key"] != "tmt_rebar" for m in o["materials"])  # rebar is a bar, not a sheet part
    req = ezhil.post("/api/requirements", json={"lane": "durable", "kind": "linear", "fields": {
        "part_name": "bar", "profile": "TMT bar 12", "mass_per_m_kg": 0.889, "material": "tmt_rebar", "cut_length_mm": 1000, "quantity": 1,
        "accepted_grades": ["Fe500"], "new_price_per_kg": 64}}).json()
    data = ezhil.get("/api/scenario/bars").json()
    for k in ("scenario", "title", "engine", "plan_seller", "metric_word"):
        data.pop(k)
    own = ezhil.get("/api/own-stock/bars").json()
    data["sellers"] += own["sellers"]
    data["resources"] += own["resources"]
    data["demands"] = ezhil.get("/api/requirements/demands/bars").json()["demands"]
    f = ezhil.post("/api/fulfil", json={"demand_id": req["demand_id"], "data": data, "allow_partial": True}).json()
    why = {x["resource_id"].split("-", 1)[1]: x["reason"] for x in f["rejections"]}
    assert why[weak["item_id"]] == "grade Fe415 not accepted"
    assert f["best"]["units"] == 1 and f["best"]["allocations"][0]["resource_id"].endswith(good["item_id"])


# ---------------- shuttering plywood ----------------
def test_shuttering_plywood_condition_is_shown_and_checked():
    c = site()
    r = add(c, PLY)
    assert r["item"]["condition"] == "used_once" and r["item"]["thickness_mm"] == 18
    [it] = c.get("/api/stock/import_durable").json()["items"]
    assert it["condition_name"] == "Used once" and "used once" in it["description"]
    page = c.get(it["passport"]).text
    assert "<dt>Condition</dt><dd>Used once</dd>" in page
    assert c.post("/api/manual/durable", json={"fields": {**PLY, "thickness_mm": "15"}}).json()["detail"]["errors"]["thickness_mm"].startswith("Shuttering plywood is 12 or 18 mm")
    assert "condition" in c.post("/api/manual/durable", json={"fields": {**PLY, "condition": "surface_rust"}}).json()["detail"]["errors"]
    assert add(c, {**PLY, "thickness_mm": "12", "condition": "used_many_times"})["item"]["condition"] == "used_many_times"


def test_the_csv_template_offers_rebar_and_shuttering_plywood():
    c = site()
    text = c.get("/api/import/example/durable").text
    assert text.splitlines()[0].endswith(",diameter_mm") and "tmt_rebar" in text and "shuttering_plywood" in text
    r = c.post("/api/import/preview", json={"lane": "durable", "csv_text": text}).json()
    got = {s["item"]["id"]: s["item"] for s in r["surplus"]}
    assert got["TMT-501"]["profile"] == "TMT bar 12" and got["TMT-502"]["grade"] == "Fe415" and got["SP-601"]["condition"] == "used_once"


def test_construction_sites_see_their_materials_first():
    first = [m["key"] for m in site().get("/api/manual/options").json()["materials"][:2]]
    assert first == ["tmt_rebar", "shuttering_plywood"]
    assert sign_in(TestClient(main.app), "steel:A").get("/api/manual/options").json()["materials"][0]["key"] == "mild_steel"


# ---------------- seller type chips ----------------
def test_seller_type_shows_on_profile_stock_passport_and_browse():
    c = site()
    item = add(c, BAR)
    assert c.get("/api/account").json()["seller_type_name"] == "Construction site"
    [row] = c.get("/api/stock/import_durable").json()["items"]
    assert row["seller_type_name"] == "Construction site"
    assert '<span class="tag">Construction site</span>' in c.get(row["passport"]).text
    browse = sign_in(TestClient(main.app), "steel:W").get("/api/browse/durable").json()["items"]
    assert next(i for i in browse if i["id"] == item["item_id"])["seller_type_name"] == "Construction site"
    assert next(i for i in browse if i["key"] == "steel" and i["id"] == "A1")["seller_type_name"] == "Workshop"  # Sri Ram
    assert next(i for i in browse if i["key"] == "steel" and i["id"] == "B1")["seller_type_name"] is None  # a sample seller, no account
    sriram = sign_in(TestClient(main.app), "steel:A").get("/api/stock/steel").json()["items"]
    assert {i["seller_type_name"] for i in sriram} == {"Workshop"}


# ---------------- collection requests ----------------
def ask(c, items, **kw):
    return c.post("/api/collections", json={"items": items, "pickup_date": TOMORROW, "slot": "10:00-12:00", "note": "Gate 2", **kw})


def test_a_collection_request_is_created_and_its_status_changes():
    c = site()
    a, b = add(c, BAR), add(c, PLY)
    key = a["stock_key"]
    r = ask(c, [{"key": key, "id": a["item_id"]}, {"key": key, "id": b["item_id"]}])
    assert r.status_code == 200, r.text
    req = r.json()
    assert req["status"] == "requested" and req["status_text"] == "Requested" and req["item_count"] == 2
    assert req["weight_kg"] == pytest.approx(sum(i["kg"] for i in c.get("/api/stock/import_durable").json()["items"]), abs=0.02)
    assert req["business_name"] == SITE["business_name"] and req["area"] == "Porur" and req["note"] == "Gate 2" and req["label"] == "sample"
    assert req["pickup"].startswith("Pickup by a hired vehicle")
    assert c.post(f"/api/collections/{req['id']}/status", json={"status": "closed"}).status_code == 403  # only the CIRCULATE team
    admin = sign_in(TestClient(main.app))
    for s, text in (("matched", "Matched to buyer"), ("vehicle_booked", "Vehicle booked"), ("closed", "Closed")):
        assert admin.post(f"/api/collections/{req['id']}/status", json={"status": s}).json()["status_text"] == text
    [mine] = c.get("/api/collections").json()["requests"]
    assert mine["status"] == "closed" and [h["status"] for h in mine["history"]] == ["requested", "matched", "vehicle_booked", "closed"]
    assert admin.post(f"/api/collections/{req['id']}/status", json={"status": "requested"}).status_code == 422
    assert admin.post("/api/collections/999/status", json={"status": "closed"}).status_code == 404


def test_a_collection_request_is_checked():
    c = site()
    a = add(c, BAR)
    e = ask(c, [], pickup_date=(date.today() - timedelta(days=1)).isoformat(), slot="03:00-05:00").json()["detail"]["errors"]
    assert set(e) == {"items", "pickup_date", "slot"}
    sriram = sign_in(TestClient(main.app), "steel:A")
    assert "not one of your items" in ask(sriram, [{"key": a["stock_key"], "id": a["item_id"]}]).json()["detail"]["errors"]["items"]
    assert ask(sriram, [{"key": "steel", "id": "B1"}]).status_code == 422  # Kumaran's piece, not Sri Ram's
    assert ask(sriram, [{"key": "steel", "id": "A1"}]).status_code == 200  # his own sample piece


def test_a_seller_sees_only_its_own_collection_requests_and_the_admin_sees_all():
    c, sriram = site(), sign_in(TestClient(main.app), "steel:A")
    a = add(c, BAR)
    mine = ask(c, [{"key": a["stock_key"], "id": a["item_id"]}]).json()["id"]
    his = ask(sriram, [{"key": "steel", "id": "A1"}]).json()["id"]
    assert [x["id"] for x in c.get("/api/collections").json()["requests"]] == [mine]
    assert [x["id"] for x in sriram.get("/api/collections").json()["requests"]] == [his]
    assert {x["id"] for x in sign_in(TestClient(main.app)).get("/api/collections").json()["requests"]} == {mine, his}
    assert sign_in(TestClient(main.app), "steel:W").get("/api/collections").status_code == 403  # buyers do not see them


def test_an_opted_in_whatsapp_contact_gets_the_collection_alert():
    c = site()
    a = add(c, BAR)
    c.post("/api/whatsapp/contacts", json={"business": "", "role": "seller", "name": "Velan", "phone": "9000000001",
                                           "alerts": ["collection"], "consent": True})
    req = ask(c, [{"key": a["stock_key"], "id": a["item_id"]}]).json()
    sign_in(TestClient(main.app)).post(f"/api/collections/{req['id']}/status", json={"status": "vehicle_booked"})
    texts = [n["text"] for n in c.get("/api/whatsapp/notifications").json()["notifications"] if n["event"] == "collection"]
    assert len(texts) == 2 and all("Pickup by a hired vehicle" in t and len(t) < 400 for t in texts)
    assert any("is vehicle booked" in t for t in texts) and any("is requested" in t for t in texts)
    other = sign_in(TestClient(main.app), "steel:A")  # no contact for Sri Ram: nothing prepared, nothing seen
    assert [n for n in other.get("/api/whatsapp/notifications").json()["notifications"] if n["event"] == "collection"] == []


def test_never_our_fleet():
    for f in (main.ROOT / "main.py", main.ROOT / "static" / "app.js", main.ROOT.parent / "engine" / "messages.py"):
        assert "our fleet" not in f.read_text(encoding="utf-8").lower()
