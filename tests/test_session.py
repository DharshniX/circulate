"""Demo logins: separate seller, buyer and admin sessions. Demo only: the APIs themselves stay open by design."""
from fastapi.testclient import TestClient

from app.main import app

SELLER = {"gapfill", "stock", "add", "add-csv", "add-sync", "add-manual", "deals", "pickup", "whatsapp"}
BUYER = {"browse", "post", "reqs", "match", "pickup", "whatsapp"}


def client(role=None, business_id=None):
    c = TestClient(app)
    if role:
        assert c.post("/api/session", json={"role": role, "business_id": business_id}).status_code == 200
    return c


def menu_views(s):
    return {i["view"] for g in s["menu"] for i in g["items"]}


def test_session_set_get_delete():
    c = client()
    assert c.get("/api/session").status_code == 401
    r = c.post("/api/session", json={"role": "seller", "business_id": "steel:A"})
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
    s = c.get("/api/session").json()
    assert (s["role"], s["business_id"], s["business_name"]) == ("seller", "steel:A", "Sri Ram Fabricators, Ambattur")
    assert s["seller_id"] == "A" and s["lane"] == "durable" and s["home"] == "gapfill"
    assert c.delete("/api/session").status_code == 200
    assert c.get("/api/session").status_code == 401


def test_only_demo_accounts_and_matching_roles():
    c = client()
    assert c.post("/api/session", json={"role": "buyer", "business_id": "steel:A"}).status_code == 422  # a seller, not a buyer
    assert c.post("/api/session", json={"role": "seller", "business_id": "steel:Z"}).status_code == 422
    assert c.post("/api/session", json={"role": "owner", "business_id": "admin"}).status_code == 422
    c.cookies.set("circulate_session", "admin|steel:A|")  # a cookie that matches no account counts as signed out
    assert c.get("/api/session").status_code == 401


def test_each_business_lands_on_its_own_data():
    want = {"food:H": ("seller", "Thamarai Hotel Kitchen, Egmore", "fresh"), "steel:W": ("buyer", "Ezhil Engineering Works, Ambattur", "durable"),
            "food:M": ("buyer", "Anna Nagar Boys' Hostel Mess", "fresh"), "food:N": ("buyer", "Thozhan Food Bank, Saidapet", "fresh")}
    for bid, (role, name, lane) in want.items():
        s = client(role, bid).get("/api/session").json()
        assert (s["business_name"], s["lane"]) == (name, lane)
    assert client("buyer", "food:N").get("/api/session").json()["demand_id"] == "F2"
    assert client("seller", "food:H").get("/api/session").json()["home"] == "stock"  # Sold before it's cut is Durable only


def test_app_without_session_goes_to_the_start_page():
    c = client()
    r = c.get("/app", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/"
    start = c.get("/").text
    assert "Sell surplus before it becomes scrap." in start and "Prototype. Demo accounts only. Sample data is labelled." in start
    for role in ("seller", "buyer", "admin"):
        assert f'href="/login/{role}"' in start
    assert "app.js" in client("buyer", "steel:W").get("/app").text


def test_login_pages():
    c = client()
    seller = c.get("/login/seller").text
    assert "Sri Ram Fabricators" in seller and "Thamarai Hotel Kitchen" in seller and "Demo login. No real accounts or passwords." in seller
    assert "Sign in with phone" in seller and 'value="1234"' in seller and 'href="/"' in seller
    buyer = c.get("/login/buyer").text
    assert all(n in buyer for n in ("Ezhil Engineering Works", "Anna Nagar Boys", "Thozhan Food Bank"))
    assert "CIRCULATE Admin" in c.get("/login/admin").text
    assert c.get("/login/owner").status_code == 404
    signed = client("seller", "steel:A")
    r = signed.get("/login/seller", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/app"  # already signed in: straight to your home


def test_each_role_menu_has_only_its_screens():
    s = client("seller", "steel:A").get("/api/session").json()
    assert menu_views(s) == SELLER and s["menu"][0]["items"][0]["view"] == "gapfill"
    b = client("buyer", "steel:W").get("/api/session").json()
    assert menu_views(b) == BUYER and b["menu"][0]["items"][0]["view"] == "browse"
    a = client("admin", "admin").get("/api/session").json()
    assert menu_views(a) >= SELLER | BUYER | {"quality", "sim", "users", "monitor", "offers"}
    assert "accounts" not in s and "accounts" not in b and len(a["accounts"]) == 6
    assert s["owners"]["browse"]["for"] == "buyers" and b["owners"]["gapfill"]["for"] == "sellers"
    assert s["owners"]["quality"]["for"] == "the CIRCULATE team"


def test_view_as_works_only_for_admin():
    seller = client("seller", "steel:A")
    assert seller.post("/api/session/view-as", json={"business_id": "steel:W"}).status_code == 403
    assert seller.delete("/api/session/view-as").status_code == 403
    assert client().post("/api/session/view-as", json={"business_id": "steel:W"}).status_code == 403
    admin = client("admin", "admin")
    v = admin.post("/api/session/view-as", json={"business_id": "steel:A"}).json()
    assert v["viewing_as"] and v["role"] == "seller" and v["business_name"].startswith("Sri Ram") and menu_views(v) == SELLER
    s = admin.get("/api/session").json()
    assert s["viewing_as"] and s["real"]["role"] == "admin" and s["accounts"]  # the admin can still switch or exit
    assert admin.post("/api/session/view-as", json={"business_id": "nope"}).status_code == 422
    back = admin.delete("/api/session/view-as").json()
    assert back["role"] == "admin" and not back["viewing_as"]


def test_public_pages_still_work_without_a_session():
    c = client()
    assert c.get("/passport/steel/A1").status_code == 200  # QR codes on labels open without a login
    assert c.get("/register").status_code == 200
    assert c.get("/api/browse/durable").status_code == 200  # no API authentication in this prototype
