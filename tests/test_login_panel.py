"""The Demo accounts panel on the seller and buyer sign-in pages lists that role's seeded businesses as prefill buttons."""
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

import app.main as main
from conftest import DEMO_PASSWORD


class Panel(HTMLParser):
    """Collects the buttons inside <section class="demo-panel">, and notes any collapsible wrapper around them."""

    def __init__(self):
        super().__init__()
        self.depth, self.buttons, self.in_details, self.collapsible = 0, [], 0, False
        self.cur = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "details":
            self.in_details += 1
        if tag == "section" and "demo-panel" in (a.get("class") or ""):
            self.depth = 1
            self.collapsible = self.in_details > 0
        elif self.depth and tag == "section":
            self.depth += 1
        if self.depth and tag == "button" and "data-email" in a:
            self.cur = {"email": a["data-email"], "business": a.get("data-business"), "text": ""}

    def handle_endtag(self, tag):
        if tag == "details":
            self.in_details -= 1
        if self.depth and tag == "section":
            self.depth -= 1
        if tag == "button" and self.cur:
            self.buttons.append(self.cur)
            self.cur = None

    def handle_data(self, data):
        if self.cur is not None:
            self.cur["text"] += data


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")
    monkeypatch.delenv("AUTH_MODE", raising=False)


@pytest.mark.parametrize("role, want", [
    ("seller", {"Sri Ram Fabricators": "sriram@demo.circulate", "Thamarai Hotel Kitchen": "thamarai@demo.circulate",
                "Kaveri Constructions (site)": "kaveri@demo.circulate"}),
    ("buyer", {"Ezhil Engineering Works": "ezhil@demo.circulate", "Anna Nagar Boys' Hostel Mess": "anna@demo.circulate",
               "Thozhan Food Bank": "thozhan@demo.circulate"}),
])
def test_demo_accounts_panel_lists_the_roles_seeded_businesses(role, want):
    page = TestClient(main.app).get(f"/login/{role}").text
    p = Panel()
    p.feed(page)
    assert not p.collapsible and '<details class="demo-panel"' not in page  # always open: nothing can hide the list
    got = {next(n for n in want if n in b["text"]): b["email"] for b in p.buttons if any(n in b["text"] for n in want)}
    assert got == want and len(p.buttons) == len(want)  # every seeded business of this role, and only those
    seeded = {email: bid for email, bid, r, _ in main.DEMO_USERS}
    assert all(b["business"] == seeded[b["email"]] for b in p.buttons)
    assert f'DEMO_PW = "{DEMO_PASSWORD}"' in page and 'em.value = b.dataset.email; pw.value = DEMO_PW' in page  # tap = prefill
    assert "location.replace" not in page.split("data-email")[0] and "go.focus()" in page  # prefill only; the user presses Sign in


def test_no_panel_for_the_admin_and_the_stylesheet_is_versioned():
    c = TestClient(main.app)
    assert 'class="demo-panel"' not in c.get("/login/admin").text
    for path in ("/", "/login/seller", "/signup/buyer"):
        assert "/static/app.css?v=" in c.get(path).text  # a new deploy never shows a stale cached stylesheet


def test_the_emergency_one_tap_page_is_one_whole_page(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "demo")
    page = TestClient(main.app).get("/login/seller").text
    assert page.count("<html") == 1 and "Sri Ram Fabricators" in page
