"""WhatsApp alerts: message text, wa.me links, registration, ready-to-send notifications. Nothing is ever sent by the app."""
import re
import urllib.parse
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main
from conftest import sign_in
from engine import messages as M

c = TestClient(main.app)
TOMORROW = (date.today() + timedelta(days=1)).isoformat()
EMOJI = re.compile("[\U00010000-\U0010FFFF☀-➿]")
ALL = ["new_match", "order_accepted", "pickup_booked", "delivered"]  # fresh-food alerts are tested on their own


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")
    sign_in(c, "admin")  # a fresh database each test: sign in again


def ok_message(text, *must):
    assert len(text) < 400, len(text)
    assert not EMOJI.search(text)
    assert re.search(r"https?://\S+", text)
    for m in must:
        assert m in text, (m, text)


# ---------------- message text ----------------
def test_new_match_uses_only_the_data_passed_in():
    order = {"id": "R7", "buyer_name": "Kaveri Enclosures, Guindy", "part_name": "lug", "quantity": 37,
             "length_mm": 123, "width_mm": 77, "thickness_mm": 4, "label": "sample"}
    t = M.new_match(order, {"kind": "gapfill", "units": 29, "unit_name": "parts", "value": 123456.4, "link": "http://h/x", "label": "sample"},
                    {"name": "Vetri Gate Works, Avadi"})
    ok_message(t, "Vetri Gate Works, Avadi", "Kaveri Enclosures, Guindy", "37", "123 x 77 x 4 mm", "R7", "29 parts", "Rs 1,23,456",
               "before cutting", "http://h/x")
    assert t.endswith("| Sample data")
    t2 = M.new_match(order, {"kind": "stock", "units": 5, "value": 99, "link": "http://h/y", "label": "measured"}, {"name": "Aadhi Pipe Fitters"})
    ok_message(t2, "Aadhi Pipe Fitters", "matches your stock", "Rs 99")
    assert t2.endswith("| Measured data")


def test_deal_messages():
    deal = {"id": 41, "buyer_name": "Marina Railings, Mylapore", "sellers": ["Vetri Gate Works, Avadi", "Senthil Truss Works, Red Hills"],
            "units": 63, "unit_name": "cut pieces", "part_name": "baluster", "is_simulated": True, "link": "http://h/deals/41", "label": "sample"}
    a = M.order_accepted(deal)
    ok_message(a, "Marina Railings, Mylapore", "demo deal #41", "63 cut pieces", "baluster", "Vetri Gate Works, Avadi", "http://h/deals/41")
    b = M.pickup_booked({**deal, "date": "9 Oct 2026", "slot": "14:00-16:00", "vehicle": "goods auto"})
    ok_message(b, "9 Oct 2026, 14:00-16:00", "Vetri Gate Works, Avadi, then Senthil Truss Works, Red Hills", "goods auto", "Marina Railings")
    assert "Vehicle" not in M.pickup_booked({**deal, "date": "9 Oct 2026", "slot": "14:00-16:00"})  # vehicle only if known
    d = M.delivered({**deal, "evidence": ["SIM-00041"]})
    ok_message(d, "Marina Railings, Mylapore", "63 cut pieces", "SIM-00041", "delivered")
    for t in (a, b, d):
        assert t.endswith("| Sample data")
        assert "read" not in t.lower().split() and "delivered to your phone" not in t.lower()


def test_fresh_and_share_messages():
    f = M.fresh_expiring({"seller_name": "Kurinji Restaurant, Mylapore", "product": "Idli batter", "quantity_kg": 13.5, "storage": "chilled",
                          "hours_left": 7, "pickup_by": "21:10 on 8 Oct", "link": "http://h/passport/food/R9", "label": "measured"})
    ok_message(f, "Kurinji Restaurant, Mylapore", "Idli batter", "13.5 kg", "chilled", "7 h", "21:10 on 8 Oct", "Food rules need expert review")
    assert f.endswith("| Measured data")
    long = M.fresh_expiring({"seller_name": "X" * 200, "product": "Y" * 200, "quantity_kg": 1, "storage": "ambient", "hours_left": 2,
                             "pickup_by": "now", "link": "http://h/p", "label": "sample"})
    assert len(long) < 400 and "Food rules need expert review" in long  # the food-safety line is never cut
    s = M.offcut_share({"seller_name": "Sri Ram Fabricators, Ambattur", "description": "1250 x 600 x 2 mm Mild steel E250, good",
                        "kg": 11.77, "scrap_value": 353.2, "label": "sample"}, "http://h/passport/steel/A3")
    ok_message(s, "Sri Ram Fabricators, Ambattur", "1250 x 600 x 2 mm", "11.8 kg", "Rs 353", "http://h/passport/steel/A3")
    assert "Scrap value" not in M.offcut_share({"seller_name": "S", "description": "d", "kg": 1, "scrap_value": None, "label": "sample"}, "http://h")
    assert not EMOJI.search(M.offcut_share({"seller_name": "S \U0001F600", "description": "d", "kg": 1, "label": "sample"}, "http://h"))


def test_indian_rupee_format():
    assert [M.inr(x) for x in (0, 999, 1250, 123456, 1234567, 99.6)] == ["Rs 0", "Rs 999", "Rs 1,250", "Rs 1,23,456", "Rs 12,34,567", "Rs 100"]


# ---------------- wa.me links ----------------
def test_wa_link_encoding_and_numbers():
    link = M.wa_link("Spacers & lugs\nRs 1,250 each", "98765 43210")
    assert link.startswith("https://wa.me/919876543210?text=")
    q = link.split("?text=", 1)[1]
    assert "%26" in q and "%0A" in q and "Rs%201%2C250" in q and " " not in q
    assert urllib.parse.unquote(q) == "Spacers & lugs\nRs 1,250 each"
    assert M.wa_link("hi").startswith("https://wa.me/?text=hi")  # no fixed number: the person chooses the chat
    assert M.normalize_phone("+91 (98765)-43210") == "919876543210"
    assert M.normalize_phone("098765 43210") == "919876543210"
    assert M.normalize_phone("+44 7911 123456") == "447911123456"
    for bad in ("12345", "98765 4321a", "1234567890123456", "", "+91 98765"):
        with pytest.raises(ValueError, match="10-digit mobile number"):
            M.normalize_phone(bad)
    assert M.mask_phone("919876543210") == "+91 ******3210"


# ---------------- registration ----------------
def register(business, role, phone, alerts=ALL, consent=True, name="Test person"):
    return c.post("/api/whatsapp/contacts", json={"business": business, "role": role, "name": name, "phone": phone, "alerts": alerts, "consent": consent})


def test_registration_needs_consent_and_shows_a_masked_phone():
    r = register("Your workshop", "seller", "98765 43210", consent=False)
    assert r.status_code == 422 and "agree" in r.json()["detail"]["errors"]["consent"]
    bad = register("", "boss", "123", alerts=[], consent=False).json()["detail"]["errors"]
    assert set(bad) >= {"business", "role", "phone", "alerts", "consent"} and "10-digit" in bad["phone"]
    ok = register("Your workshop", "seller", "98765 43210").json()
    assert ok["phone"] == "+91 ******3210" and ok["consent_at"]
    listed = c.get("/api/whatsapp/contacts").text
    assert "9876543210" not in listed and "******3210" in listed
    assert register("Your workshop", "seller", "9876543210").status_code == 422  # the same number twice for one business


def test_edit_pause_remove():
    cid = register("Your workshop", "seller", "9000000001").json()["id"]
    e = c.patch(f"/api/whatsapp/contacts/{cid}", json={"name": "Meena", "alerts": ["new_match"], "phone": "9000000009"}).json()
    assert e["name"] == "Meena" and e["alerts"] == ["new_match"] and e["phone"].endswith("0009")
    assert c.patch(f"/api/whatsapp/contacts/{cid}", json={"phone": "12"}).status_code == 422
    assert c.patch(f"/api/whatsapp/contacts/{cid}", json={"active": False}).json()["active"] is False
    assert c.delete(f"/api/whatsapp/contacts/{cid}").json() == {"removed": True}
    assert c.delete(f"/api/whatsapp/contacts/{cid}").status_code == 404


def test_register_page_for_phones():
    page = c.get("/register").text
    assert "I agree to receive CIRCULATE alerts on this WhatsApp number" in page and 'type="tel"' in page
    assert "Thozhan Food Bank, Saidapet" in page and "nothing is sent automatically" in page


# ---------------- notifications ----------------
def notes(event=None):
    xs = c.get("/api/whatsapp/notifications").json()["notifications"]
    return [x for x in xs if event is None or x["event"] == event]


SPACER = {"part_name": "spacer", "material": "mild_steel", "length_mm": 120, "width_mm": 90, "thickness_mm": 6, "accepted_grades": ["E250"],
          "quantity": 6, "new_price_per_kg": 200, "buyer_name": "Ezhil Engineering Works, Ambattur"}
BRACKET = {**SPACER, "part_name": "bracket", "length_mm": 150, "width_mm": 100, "thickness_mm": 2, "quantity": 50, "new_price_per_kg": 62}


def test_a_requirement_that_fits_prepares_alerts_only_for_opted_in_sellers():
    me = register("Your workshop", "seller", "9000000001", ["new_match"]).json()
    register("Your workshop", "seller", "9000000002", ["pickup_booked"])  # did not tick new orders
    paused = register("Your workshop", "seller", "9000000003", ["new_match"]).json()
    c.patch(f"/api/whatsapp/contacts/{paused['id']}", json={"active": False})
    register("Ezhil Engineering Works, Ambattur", "buyer", "9000000004")  # a buyer, not a seller
    kumaran = register("Kumaran Machine Shop, Guindy", "seller", "9000000005", ["new_match"]).json()
    sriram = register("Sri Ram Fabricators, Ambattur", "seller", "9000000006", ["new_match"]).json()
    c.post("/api/requirements", json={"lane": "durable", "kind": "flat", "standing": True, "fields": SPACER})
    got = notes("new_match")
    assert [n["to"]["phone"] for n in got] == [me["phone"]]  # Gap Fill fit: only the opted-in, active workshop contact
    assert got[0]["status"] == "ready" and "before cutting" in got[0]["text"] and got[0]["link"].startswith("https://wa.me/919000000001?text=")
    c.post("/api/requirements", json={"lane": "durable", "kind": "flat", "fields": BRACKET})
    phones = {n["to"]["phone"] for n in notes("new_match")}
    assert kumaran["phone"] in phones and sriram["phone"] not in phones  # only sellers whose stock the engine would use


def accept_s1():
    data = c.get("/api/scenario/steel").json()
    for k in ("scenario", "title", "engine", "plan_seller", "metric_word"):
        data.pop(k)
    b = c.post("/api/fulfil", json={"demand_id": "S1", "data": data}).json()["best"]
    return c.post("/api/deals", json={"kind": "flat", "counterfactual": "recycled", "demand_id": "S1", "buyer_name": "Ezhil Engineering Works, Ambattur",
                                      "sellers": b["sellers_used"], "units": b["units"], "unit_name": "parts", "kg_in_units": b["kg_in_units"],
                                      "kg_shipped": b["kg_shipped"], "route_km": b["route_km"], "value_above_salvage": 1, "scenario": "steel"}).json()["id"]


def test_accept_pickup_and_deliver_prepare_the_right_alerts():
    buyer = register("Ezhil Engineering Works, Ambattur", "buyer", "9000000001").json()
    other_buyer = register("Thendral Control Panels, Ekkattuthangal", "buyer", "9000000002").json()
    seller = register("Sri Ram Fabricators, Ambattur", "seller", "9000000003").json()
    deal = accept_s1()
    acc = notes("order_accepted")
    assert [n["to"]["phone"] for n in acc] == [buyer["phone"]] and f"demo deal #{deal}" in acc[0]["text"]
    assert f"http://testserver/deals/{deal}/pickup-sheet" in acc[0]["text"]  # the link uses the request origin
    c.post(f"/api/deals/{deal}/booking", json={"pickup_date": TOMORROW, "slot": "10:00-12:00"})
    pick = notes("pickup_booked")
    assert {n["to"]["phone"] for n in pick} == {buyer["phone"], seller["phone"]} and "10:00-12:00" in pick[0]["text"]
    c.post(f"/api/deals/{deal}/booking", json={"pickup_date": TOMORROW, "slot": "10:00-12:00"})
    assert len(notes("pickup_booked")) == 2  # the same booking is not prepared twice
    c.post(f"/api/deals/{deal}/advance", json={"status": "dispatched"})
    assert notes("delivered") == []
    c.post(f"/api/deals/{deal}/advance", json={"status": "delivered", "evidence": ["SIM-1"], "buyer_confirmed": True})
    dl = notes("delivered")
    assert [n["to"]["phone"] for n in dl] == [buyer["phone"]] and "SIM-1" in dl[0]["text"] and f"/deals/{deal}/challan" in dl[0]["text"]
    assert other_buyer["phone"] not in {n["to"]["phone"] for n in notes()}


def test_pause_stops_new_alerts():
    buyer = register("Ezhil Engineering Works, Ambattur", "buyer", "9000000001").json()
    c.patch(f"/api/whatsapp/contacts/{buyer['id']}", json={"active": False})
    accept_s1()
    assert notes() == []


def test_fresh_alert_once_per_lot_and_only_at_24_hours_or_less():
    bank = register("Thozhan Food Bank, Saidapet", "food bank", "9000000001", ["fresh"]).json()
    register("Anna Nagar Boys' Hostel Mess", "buyer", "9000000002", ["order_accepted"])  # did not ask for fresh alerts
    for _ in range(3):
        c.get("/api/browse/fresh")
        c.get("/api/stock/food")
        c.get("/api/whatsapp/notifications/count")
    fresh = notes("fresh")
    lots = {n["item_id"] for n in fresh}
    assert lots == {"food:H3", "food:K2", "food:R2", "food:C2"}  # 5, 9, 14 and 4 hours left; 30 h and more are not alerted
    assert len(fresh) == 4 and all(n["to"]["phone"] == bank["phone"] for n in fresh)
    assert all("Food rules need expert review" in n["text"] for n in fresh)


def test_status_ready_opened_sent_and_never_delivered():
    register("Ezhil Engineering Works, Ambattur", "buyer", "9000000001")
    accept_s1()
    n = notes()[0]
    assert n["status"] == "ready" and n["status_text"] == "Ready to send"
    assert c.get("/api/whatsapp/notifications/count").json()["ready"] == 1
    o = c.post(f"/api/whatsapp/notifications/{n['id']}/status", json={"status": "opened"}).json()
    assert o["status_text"] == "Opened in WhatsApp"
    s = c.post(f"/api/whatsapp/notifications/{n['id']}/status", json={"status": "sent"}).json()
    assert s["status_text"] == "Marked as sent by you"
    after = notes()[0]
    assert after["opened_at"] and after["sent_at"] and c.get("/api/whatsapp/notifications/count").json()["ready"] == 0
    for word in ("delivered", "read", "ready"):
        assert c.post(f"/api/whatsapp/notifications/{n['id']}/status", json={"status": word}).status_code == 422
    assert c.post("/api/whatsapp/notifications/999/status", json={"status": "sent"}).status_code == 404


def test_removing_a_contact_drops_its_unsent_alerts():
    cid = register("Ezhil Engineering Works, Ambattur", "buyer", "9000000001").json()["id"]
    accept_s1()
    assert len(notes()) == 1
    c.delete(f"/api/whatsapp/contacts/{cid}")
    assert notes() == []


def test_share_on_whatsapp_opens_wa_me_without_a_fixed_number():
    r = c.get("/share/steel/A3", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].startswith("https://wa.me/?text=")
    text = urllib.parse.unquote(r.headers["location"].split("?text=", 1)[1])
    assert "Sri Ram Fabricators, Ambattur" in text and "http://testserver/passport/steel/A3" in text and text.endswith("| Sample data")
    assert c.get("/share/steel/NOPE", follow_redirects=False).status_code == 404
    assert 'href="/share/steel/A3"' in c.get("/passport/steel/A3").text


def test_no_alert_without_any_registered_number():
    c.post("/api/requirements", json={"lane": "durable", "kind": "flat", "standing": True, "fields": SPACER})
    accept_s1()
    c.get("/api/browse/fresh")
    assert notes() == []
