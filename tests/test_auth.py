"""Accounts and sessions: hashing, sign-in, throttle, cookies, password change, seeding, sign-up, roles and the AUTH_MODE switch."""
import sqlite3
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import auth as AUTH
from conftest import ADMIN, DEMO_PASSWORD, sign_in

NEW = {"role": "seller", "seller_type": "construction_site", "business_name": "Velan Builders, Porur", "contact_name": "Velan",
       "email": "Velan@Example.com", "phone": "+91 98765 43210", "area": "Porur", "lane": "durable", "password": "buildit123",
       "confirm": "buildit123"}


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")
    monkeypatch.delenv("AUTH_MODE", raising=False)
    main.THROTTLE.reset()
    yield
    main.THROTTLE.reset()


def login(c, email, password, **kw):
    return c.post("/api/auth/login", json={"email": email, "password": password, **kw})


def row(email):
    with closing(sqlite3.connect(main.DB)) as con:
        con.row_factory = sqlite3.Row
        return con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()


# ---------------- passwords ----------------
def test_passwords_are_hashed_with_a_random_salt_and_never_stored_or_returned():
    c = TestClient(main.app)
    r = c.post("/api/auth/register", json=NEW)
    assert r.status_code == 200 and "buildit123" not in r.text and "password_hash" not in r.text and "salt" not in r.text
    u = row("velan@example.com")  # stored in lower case
    assert u["password_hash"] != "buildit123" and len(u["salt"]) == 32 and len(u["password_hash"]) == 64
    assert all("buildit123" not in str(v) for v in dict(u).values())
    assert AUTH.verify_password("buildit123", u["salt"], u["password_hash"]) and not AUTH.verify_password("buildit124", u["salt"], u["password_hash"])
    assert AUTH.hash_password("same-password") != AUTH.hash_password("same-password")  # a new salt every time
    assert "buildit123" not in c.get("/api/account").text and "buildit123" not in sign_in(TestClient(main.app)).get("/api/admin/users").text


def test_the_same_message_for_a_wrong_password_and_an_unknown_email():
    c = TestClient(main.app)
    wrong, unknown = login(c, "sriram@demo.circulate", "not-the-password"), login(c, "nobody@nowhere.example", "whatever1")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"] == main.LOGIN_ERROR
    assert login(c, "SRIRAM@demo.circulate ", DEMO_PASSWORD).status_code == 200  # email is case- and space-insensitive


def test_five_failures_lock_the_email_for_five_minutes():
    c = TestClient(main.app)
    for _ in range(5):
        assert login(c, "ezhil@demo.circulate", "wrong-pass").status_code == 401
    locked = login(c, "ezhil@demo.circulate", DEMO_PASSWORD)  # even the right password waits
    assert locked.status_code == 429 and "Try again in 5 minute" in locked.json()["detail"]
    assert login(c, "anna@demo.circulate", DEMO_PASSWORD).status_code == 200  # other emails are not affected
    t = AUTH.Throttle()
    for i in range(5):
        t.fail("x", now=1000 + i)
    assert t.wait("x", now=1100) == pytest.approx(204) and t.wait("x", now=1305) == 0  # 5 minutes after the 5th failure
    t2 = AUTH.Throttle()
    for i in range(4):
        t2.fail("y", now=i * 200)  # spread over more than 10 minutes: never 5 inside one window
    t2.fail("y", now=801)
    assert t2.wait("y", now=802) == 0


# ---------------- the session cookie ----------------
def test_cookie_flags():
    r = login(TestClient(main.app), "thamarai@demo.circulate", DEMO_PASSWORD)
    ck = r.headers["set-cookie"].lower()
    assert ck.startswith("circ_session=") and "httponly" in ck and "samesite=lax" in ck and "path=/" in ck
    assert f"max-age={7 * 86400}" in ck and "secure" not in ck  # plain http locally
    secure = TestClient(main.app).post("/api/auth/login", json={"email": "thamarai@demo.circulate", "password": DEMO_PASSWORD},
                                       headers={"x-forwarded-proto": "https"})
    assert "secure" in secure.headers["set-cookie"].lower()  # behind Render's https proxy


def test_tampered_and_expired_cookies_are_rejected():
    c = sign_in(TestClient(main.app), "steel:A")
    good = c.cookies.get("circ_session")
    assert c.get("/api/session").status_code == 200
    uid, tv, exp, view, sig = good.split("|")
    for bad in (f"{int(uid) + 1}|{tv}|{exp}|{view}|{sig}", f"{uid}|{tv}|{int(exp) + 999}|{view}|{sig}", f"{uid}|{tv}|{exp}|steel:W|{sig}",
                good[:-1] + ("0" if good[-1] != "0" else "1"), "garbage", ""):
        t = TestClient(main.app)
        t.cookies.set("circ_session", bad)
        assert t.get("/api/session").status_code == 401, bad
    old = TestClient(main.app)
    old.cookies.set("circ_session", AUTH.make_token(main._key(), int(uid), int(tv), now=time.time() - 8 * 86400))  # 8 days old
    assert old.get("/api/session").status_code == 401
    other_key = TestClient(main.app)
    other_key.cookies.set("circ_session", AUTH.make_token(b"another-key", int(uid), int(tv)))
    assert other_key.get("/api/session").status_code == 401


def test_a_password_change_signs_out_every_other_cookie():
    a, b = sign_in(TestClient(main.app), "food:N"), sign_in(TestClient(main.app), "food:N")
    bad = a.post("/api/auth/change-password", json={"current": "nope", "new": "short", "confirm": "short"})
    assert bad.status_code == 422 and set(bad.json()["detail"]["errors"]) == {"current", "new"}
    assert a.post("/api/auth/change-password", json={"current": DEMO_PASSWORD, "new": "Thozhan@New1", "confirm": "Thozhan@New1"}).status_code == 200
    assert a.get("/api/session").status_code == 200  # this browser stays signed in
    assert b.get("/api/session").status_code == 401  # every other cookie stopped working
    assert login(TestClient(main.app), "thozhan@demo.circulate", DEMO_PASSWORD).status_code == 401
    assert login(TestClient(main.app), "thozhan@demo.circulate", "Thozhan@New1").status_code == 200
    assert row("thozhan@demo.circulate")["token_version"] == 1


def test_a_switched_off_account_cannot_sign_in_and_its_cookie_stops_working():
    seller, admin = sign_in(TestClient(main.app), "food:H"), sign_in(TestClient(main.app))
    uid = row("thamarai@demo.circulate")["id"]
    assert admin.post(f"/api/admin/users/{uid}/active", json={"active": False}).json()["active"] is False
    assert seller.get("/api/session").status_code == 401
    r = login(TestClient(main.app), "thamarai@demo.circulate", DEMO_PASSWORD)
    assert r.status_code == 403 and "switched off" in r.json()["detail"]


# ---------------- seeding ----------------
def test_seeding_is_idempotent_and_never_overwrites_a_changed_password():
    c = sign_in(TestClient(main.app), "steel:W")
    c.post("/api/auth/change-password", json={"current": DEMO_PASSWORD, "new": "Ezhil@Changed1"})
    with closing(main.db()) as con:
        before = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        main._seed_users(con)
        main._seed_users(con)
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == before == 7  # the admin and six demo accounts
    main._SEEDED.discard(str(main.DB))  # as on a restart
    with closing(main.db()):
        pass
    assert login(TestClient(main.app), "ezhil@demo.circulate", "Ezhil@Changed1").status_code == 200
    assert login(TestClient(main.app), "ezhil@demo.circulate", DEMO_PASSWORD).status_code == 401
    assert row(ADMIN[0])["role"] == "admin" and row("sriram@demo.circulate")["business_id"] == "steel:A"


def test_no_admin_is_created_on_a_server_without_admin_password(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("ADMIN_PASSWORD")
    monkeypatch.setattr(main, "DB", tmp_path / "server.db")
    with closing(main.db()) as con:
        assert con.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 6
    assert "No ADMIN_PASSWORD set" in caplog.text


def test_a_local_admin_without_admin_password_gets_a_random_one_shown_once(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ADMIN_PASSWORD")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setattr(main, "DB", tmp_path / "local.db")
    with closing(main.db()):
        pass
    out = capsys.readouterr().out
    pw = out.split("password: ")[1].split()[0]
    assert len(pw) >= 12 and login(TestClient(main.app), ADMIN[0], pw).status_code == 200


# ---------------- sign-up ----------------
def test_sign_up_creates_the_right_role_and_seller_type():
    s = TestClient(main.app)
    me = s.post("/api/auth/register", json=NEW).json()
    assert (me["role"], me["seller_type"], me["lane"], me["business_name"]) == ("seller", "construction_site", "durable", "Velan Builders, Porur")
    assert me["business_id"].startswith("u") and me["home"] == "stock" and s.get("/api/session").status_code == 200
    assert row("velan@example.com")["phone"] == "9876543210"
    b = TestClient(main.app).post("/api/auth/register", json={**NEW, "role": "buyer", "seller_type": "kitchen", "email": "mess@example.com",
                                                               "lane": "fresh", "business_name": "Mess"}).json()
    assert (b["role"], b["seller_type"], b["home"]) == ("buyer", None, "browse")


def test_sign_up_is_checked_and_an_email_is_used_once():
    c = TestClient(main.app)
    bad = c.post("/api/auth/register", json={**NEW, "seller_type": "", "email": "nope", "phone": "123", "lane": "", "password": "short",
                                             "confirm": "short", "business_name": " "}).json()["detail"]["errors"]
    assert set(bad) == {"seller_type", "email", "phone", "lane", "password", "business_name"}
    assert c.post("/api/auth/register", json={**NEW, "confirm": "different1"}).json()["detail"]["errors"] == {"confirm": "The two passwords do not match."}
    assert c.post("/api/auth/register", json=NEW).status_code == 200
    dup = TestClient(main.app).post("/api/auth/register", json={**NEW, "email": "VELAN@example.com"})
    assert dup.status_code == 422 and "already exists" in dup.json()["detail"]["errors"]["email"]
    taken = TestClient(main.app).post("/api/auth/register", json={**NEW, "email": "sriram@demo.circulate"})
    assert taken.status_code == 422


def test_an_admin_account_cannot_be_registered():
    r = TestClient(main.app).post("/api/auth/register", json={**NEW, "role": "admin", "email": "boss@example.com"})
    assert r.status_code == 422 and "Admin accounts cannot be created" in r.json()["detail"]["errors"]["role"]
    assert row("boss@example.com") is None


# ---------------- roles and data ----------------
def test_a_buyer_gets_403_on_a_seller_route_and_a_seller_on_a_buyer_route():
    buyer, seller = sign_in(TestClient(main.app), "steel:W"), sign_in(TestClient(main.app), "steel:A")
    assert buyer.get("/api/stock/steel").status_code == 403
    assert buyer.post("/api/manual/durable", json={"fields": {}}).status_code == 403
    assert seller.get("/api/browse/durable").status_code == 403
    assert seller.post("/api/requirements", json={"lane": "durable", "kind": "flat", "fields": {}}).status_code == 403
    assert login(TestClient(main.app), "ezhil@demo.circulate", DEMO_PASSWORD, role="seller").status_code == 403  # wrong sign-in page


def test_a_seller_cannot_read_another_sellers_stock():
    a = sign_in(TestClient(main.app), "steel:A")
    csv = ("item_id,seller_id,material,length_mm,width_mm,thickness_mm,grade,condition,is_full_sheet,days_unused\n"
           "P1,A,mild_steel,800,400,3,E250,good,false,5\n")
    a.post("/api/import/list", json={"lane": "durable", "csv_text": csv})
    other = TestClient(main.app)
    other.post("/api/auth/register", json=NEW)
    assert other.get("/api/stock/import_durable").json()["items"] == []
    assert other.get("/api/stock/import_durable@steel:A").status_code == 404
    assert other.get("/api/stock/steel").json()["items"] == []  # Sri Ram's sample pieces are his alone
    assert [i["id"] for i in a.get("/api/stock/import_durable").json()["items"]] == ["P1"]


def test_signed_out_calls_get_401():
    c = TestClient(main.app)
    for path in ("/api/session", "/api/account", "/api/stock/steel", "/api/browse/fresh", "/api/requirements", "/api/admin/users"):
        assert c.get(path).status_code == 401, path
    assert c.post("/api/auth/change-password", json={"current": "x", "new": "yyyyyyyy"}).status_code == 401


# ---------------- the AUTH_MODE safety switch ----------------
def test_auth_mode_real_blocks_one_tap_login_and_demo_restores_it(monkeypatch):
    c = TestClient(main.app)
    one_tap = {"role": "seller", "business_id": "steel:A"}
    r = c.post("/api/session", json=one_tap)
    assert r.status_code == 403 and "switched off" in r.json()["detail"]  # AUTH_MODE=real is the default
    monkeypatch.setenv("AUTH_MODE", "real")
    assert c.post("/api/session", json=one_tap).status_code == 403
    monkeypatch.setenv("AUTH_MODE", "demo")
    r = c.post("/api/session", json=one_tap)
    assert r.status_code == 200 and r.json()["auth_mode"] == "demo" and c.get("/api/stock/steel").status_code == 200
    assert "Demo login mode" in TestClient(main.app).get("/login/buyer").text
    monkeypatch.setenv("AUTH_MODE", "real")
    assert "Demo login mode" not in TestClient(main.app).get("/login/buyer").text
