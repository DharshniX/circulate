from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)


def test_new_app_and_legacy_page_are_served():
    assert "Sell surplus before it becomes scrap." in c.get("/").text  # the start page
    admin = TestClient(app)
    admin.post("/api/session", json={"role": "admin", "business_id": "admin"})
    assert "app.js" in admin.get("/app").text  # the app itself needs a demo session
    assert c.get("/legacy").status_code == 200


def test_stock_board_has_scrap_values_and_passports():
    for key in ("steel", "sheets", "bars", "food"):
        items = c.get(f"/api/stock/{key}").json()["items"]
        assert items and all(i["passport"].startswith(f"/passport/{key}/") for i in items)
        assert all(i["label"] == "sample" for i in items)
    food = c.get("/api/stock/food").json()["items"]
    assert all("hours_left" in i for i in food)
    assert c.get("/api/stock/nope").status_code == 404


def test_passport_page_qr_and_labels():
    page = c.get("/passport/steel/A1")
    assert page.status_code == 200 and "Offcut passport" in page.text and "sample" in page.text.lower()
    assert c.get("/passport/steel/ZZ").status_code == 404
    qr = c.get("/api/qr.svg", params={"data": "http://example.test/passport/steel/A1"})
    assert qr.status_code == 200 and qr.headers["content-type"].startswith("image/svg")
    assert c.get("/labels/food").status_code == 200


def test_data_quality_never_reports_sample_as_measured(tmp_path, monkeypatch):
    # an empty database, so stock a seller has imported as measured does not count here
    import app.main as main
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")
    rows = c.get("/api/data-quality").json()["rows"]
    assert not any(r["label"] == "measured" for r in rows)
    assert any(r["label"] == "simulated" for r in rows)


def test_simulation_summary_is_available():
    s = c.get("/api/sim-summary").json()
    assert s["n"] == 5000 and "precut_remnant" in s["pol"]
