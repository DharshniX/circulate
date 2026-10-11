"""Shared test setup: a fixed admin password and signing key, and helpers to sign a test client in properly."""
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("ADMIN_EMAIL", "admin@circulate.local")
os.environ.setdefault("ADMIN_PASSWORD", "Admin@Test2026")
os.environ.setdefault("DEMO_PASSWORD", "Circulate@2026")
os.environ.setdefault("CIRCULATE_AUTOSYNC", "0")
os.environ.setdefault("CIRCULATE_SAMPLE_NETWORK", "0")  # Kaveri's seeded stock and Ezhil's rebar order: on only in test_network.py

ADMIN = (os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])
DEMO_PASSWORD = os.environ["DEMO_PASSWORD"]
DEMO = {"steel:A": "sriram@demo.circulate", "food:H": "thamarai@demo.circulate", "steel:W": "ezhil@demo.circulate",
        "food:M": "anna@demo.circulate", "food:N": "thozhan@demo.circulate"}


def sign_in(client, who="admin"):
    """Sign a TestClient in with email and password. who: 'admin' or a demo business id such as 'steel:A'."""
    email, pw = ADMIN if who == "admin" else (DEMO[who], DEMO_PASSWORD)
    r = client.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return client


import pytest  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _private_database(tmp_path_factory):
    """No test ever touches the real circulate.db (or seeds it with the test admin password)."""
    import app.main as main
    real = main.DB
    main.DB = tmp_path_factory.mktemp("db") / "session.db"
    yield
    main.DB = real
