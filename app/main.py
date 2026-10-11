"""
CIRCULATE web API (FastAPI) + demo page.

Run:   python -m uvicorn app.main:app --reload
Open:  http://127.0.0.1:8000

Engine endpoints are stateless: send the data, get the answer. Only deals are
stored (SQLite file circulate.db), because a deal has a life cycle:
agreed -> dispatched -> delivered (with evidence and buyer confirmation).
"""
from __future__ import annotations

import functools
import io
import json
import secrets
import re
import sqlite3
import asyncio
import os
import threading
from contextlib import asynccontextmanager, closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from engine import scenarios as SC
from engine.engines import REGISTRY
from engine.gapfill import GapFillRequest, plan_gap_fill
from engine.impact import DealImpactInput, EmissionFactor, compute
from engine import connectors as CN
from engine import importer as IMP
from engine import messages as MSG
from engine.models import Demand, PlanRequest, Resource, Seller, Settings, Transport
from engine.pooling import _dist, fulfil
from engine.recovery import plan
from engine.routes import seller_plan, seller_routes

ROOT = Path(__file__).resolve().parent
DB = ROOT.parent / "circulate.db"


@asynccontextmanager
async def lifespan(_app):
    """Seed the accounts, then start the auto-sync folder checker (set CIRCULATE_AUTOSYNC=0 to switch it off)."""
    _SEEDED.discard(str(DB))
    with closing(db()):  # seeding runs on every startup; it is idempotent and never overwrites a password
        pass
    task = asyncio.create_task(_autosync_loop()) if os.environ.get("CIRCULATE_AUTOSYNC", "1") != "0" else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="CIRCULATE", version="0.2", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


# ---------------- Who may call what: one check for "signed in", one for "role in (...)" ----------------
# Every /api route uses one of these, except the sign-in endpoints, /api/qr.svg and GET /api/photo (public passports).
# The checks use the business acting in the request: the signed-in account, or the business the admin is viewing as,
# so "View as" shows exactly what that business would see.
def signed_in(request: Request) -> dict:
    sess = _session(request)
    if sess is None:
        raise HTTPException(401, "Not signed in. Sign in on the start page.")
    return _me(sess)


def role_in(*roles: str):
    """Only these roles. The admin passes every check, except while viewing the app as a business."""
    def check(request: Request) -> dict:
        me = signed_in(request)
        if me["role"] != "admin" and me["role"] not in roles:
            raise HTTPException(403, "Only the CIRCULATE admin can do this." if roles == ("admin",) else
                                f"This is for {ROLE_FOR[roles[0]]}. Sign in with a {roles[0]} account to use it.")
        return me
    check.guard = roles
    return check


def real_admin(request: Request) -> dict:
    """The admin's own account management (users, View as): checks the real account, not the business being viewed."""
    me = signed_in(request)
    if not me["real_admin"]:
        raise HTTPException(403, "Only the CIRCULATE admin can do this.")
    return me


signed_in.guard = ("any",)
real_admin.guard = ("admin",)
ANY, SELLER, BUYER, ADMIN = Depends(signed_in), Depends(role_in("seller")), Depends(role_in("buyer")), Depends(role_in("admin"))
REAL_ADMIN = Depends(real_admin)


_SEEDED: set[str] = set()


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, kind TEXT, counterfactual TEXT,
        demand_id TEXT, buyer_name TEXT, sellers TEXT, units REAL, unit_name TEXT, kg_in_units REAL,
        kg_shipped REAL, route_km REAL, value_above_salvage REAL, buyer_utilisation REAL,
        status TEXT, is_simulated INTEGER, evidence TEXT, buyer_confirmed INTEGER, history TEXT)""")
    have = {r[1] for r in con.execute("PRAGMA table_info(deals)")}
    # seller_business: JSON list of the selling businesses (one deal can pool several); buyer_business: the buying business
    for col, typ in (("scenario", "TEXT"), ("rate_multiplier", "REAL"), ("booking", "TEXT"), ("seller_business", "TEXT"),
                     ("buyer_business", "TEXT")):
        if col not in have:  # older databases: add the column, keep every row
            con.execute(f"ALTER TABLE deals ADD COLUMN {col} {typ}")
    con.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, seller_type TEXT, business_id TEXT, business_name TEXT, contact_name TEXT,
        email TEXT UNIQUE, phone TEXT, area TEXT, lane TEXT, password_hash TEXT, salt TEXT, token_version INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1, created_at TEXT, last_login_at TEXT)""")
    if str(DB) not in _SEEDED:
        _SEEDED.add(str(DB))
        _seed_users(con)
    con.execute("""CREATE TABLE IF NOT EXISTS requirements (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, lane TEXT, kind TEXT, scenario TEXT, demand TEXT,
        needed_by TEXT, standing INTEGER, status TEXT, deal_id INTEGER, history TEXT)""")
    if "owner" not in {r[1] for r in con.execute("PRAGMA table_info(requirements)")}:
        con.execute("ALTER TABLE requirements ADD COLUMN owner TEXT")  # older rows have no owner: admin only
    con.execute("""CREATE TABLE IF NOT EXISTS reservations (
        req_id INTEGER PRIMARY KEY, seller TEXT, parts INTEGER, at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS collection_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, owner TEXT, business_name TEXT, area TEXT, lane TEXT, items TEXT,
        weight_kg REAL, pickup_date TEXT, slot TEXT, note TEXT, status TEXT, history TEXT, label TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS whatsapp_contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, business TEXT, role TEXT, name TEXT, phone TEXT, alerts TEXT,
        consent_at TEXT, active INTEGER, created_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, event TEXT, deal_id INTEGER, item_id TEXT, contact_id INTEGER,
        text TEXT, link TEXT, status TEXT, opened_at TEXT, sent_at TEXT)""")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_notifications ON notifications(event, IFNULL(deal_id, 0), IFNULL(item_id, ''), contact_id)")
    # owner = the business_id of the seller who added it; "" for rows saved before accounts existed (admin only)
    _with_owner(con, "sync_folders", """CREATE TABLE IF NOT EXISTS sync_folders (owner TEXT, lane TEXT, path TEXT, enabled INTEGER,
        measured INTEGER, days_unused_limit INTEGER, hours_left_limit REAL, PRIMARY KEY (owner, lane))""")
    _with_owner(con, "sync_seen", """CREATE TABLE IF NOT EXISTS sync_seen (owner TEXT, lane TEXT, file TEXT, fingerprint TEXT,
        imported_at TEXT, PRIMARY KEY (owner, lane, file))""")
    con.execute("""CREATE TABLE IF NOT EXISTS sync_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, lane TEXT, folder TEXT, file TEXT, status TEXT, rows_read INTEGER,
        surplus INTEGER, rejected INTEGER, listed INTEGER, label TEXT, message TEXT, details TEXT)""")
    if "owner" not in {r[1] for r in con.execute("PRAGMA table_info(sync_log)")}:
        con.execute("ALTER TABLE sync_log ADD COLUMN owner TEXT")
    _with_owner(con, "imported_stock", """CREATE TABLE IF NOT EXISTS imported_stock (owner TEXT, lane TEXT, item_id TEXT, item TEXT,
        rules TEXT, label TEXT, listed_at TEXT, source TEXT, PRIMARY KEY (owner, lane, item_id))""")
    return con


def _with_owner(con, table: str, create: str) -> None:
    """Create a table keyed by owner. An older copy without the column is rebuilt once; its rows keep owner '' (admin only)."""
    old = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    if old and "owner" not in old:
        con.execute(f"ALTER TABLE {table} RENAME TO {table}_before_owners")
        con.execute(create)
        cols = ", ".join(old)
        con.execute(f"INSERT INTO {table} ({cols}, owner) SELECT {cols}, '' FROM {table}_before_owners")
        con.execute(f"DROP TABLE {table}_before_owners")
        con.commit()
    else:
        con.execute(create)


class Dataset(BaseModel):
    sellers: list[Seller]
    resources: list[Resource]
    demands: list[Demand]
    transport: Transport = Transport()
    settings: Settings = Settings()
    alpha: float = Field(0.5, ge=0, le=1, description="Seller's share of the value created above salvage")


class FulfilRequest(BaseModel):
    demand_id: str
    data: Dataset
    seller_ids: list[str] | None = None
    allow_partial: bool = False


class SellerPlanRequest(BaseModel):
    seller_id: str
    kind: Literal["flat", "linear", "perishable"]
    data: Dataset
    deadline: int | None = Field(None, ge=1, le=720)
    stockist_price_per_kg: float | None = None
    rejected_route_ids: list[str] = []
    elapsed: int = Field(0, ge=0)


class NewDeal(BaseModel):
    kind: Literal["flat", "linear", "perishable"]
    counterfactual: Literal["recycled", "uncertain", "disposed", "landfill", "ruco_collection"]
    demand_id: str
    buyer_name: str
    sellers: list[str]
    units: float = Field(gt=0)
    unit_name: str
    kg_in_units: float = Field(ge=0)
    kg_shipped: float = Field(ge=0)
    route_km: float = Field(ge=0)
    value_above_salvage: float
    buyer_utilisation: float | None = None
    is_simulated: bool = True
    scenario: str | None = None
    rate_multiplier: float = Field(1.0, gt=0, le=20)
    requirement_id: int | None = None


class Advance(BaseModel):
    status: Literal["dispatched", "delivered"]
    evidence: list[str] = []
    buyer_confirmed: bool = False


class ImpactFactors(BaseModel):
    ef_avoided: EmissionFactor | None = None
    ef_truck: EmissionFactor | None = None


def _bad(e: Exception):
    raise HTTPException(status_code=422, detail=str(e))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """The start page: choose seller, buyer or admin, then a demo login."""
    return _start_page(_session(request))


@app.get("/app")
def app_page(request: Request):
    """The app itself. Without a demo session you are sent to the start page."""
    if _session(request) is None:
        return RedirectResponse("/", status_code=307)
    html = (ROOT / "static" / "app.html").read_text(encoding="utf-8")
    stamp = _asset_stamp()
    # a version on the script and style links, so a browser never mixes an old cached app.js with a new page
    html = html.replace("/static/app.js", f"/static/app.js?v={stamp}").replace("/static/app.css", f"/static/app.css?v={stamp}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


def _asset_stamp() -> int:
    return int(max((ROOT / "static" / f).stat().st_mtime for f in ("app.js", "app.css")))


@app.get("/legacy")
def legacy():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/engines")
def engines(me=ANY):
    return [{"kind": e.kind, "unit_name": e.unit_name, "time_unit": e.time_unit} for e in REGISTRY.values()]


@app.get("/api/scenarios")
def scenarios(me=ANY):
    return [{"key": k, "title": v["title"], "engine": v["engine"]} for k, v in SC.SCENARIOS.items()]


@app.get("/api/scenario/{key}")
def scenario(key: str, me=ANY):
    """A sample cluster. A seller gets only its own sellers and stock (every open order stays, so it sees what fits);
    a buyer gets every seller's stock and only its own orders; the admin gets everything."""
    if key not in SC.SCENARIOS:
        raise HTTPException(404, f"unknown scenario {key}")
    d = SC.dataset(key)
    body = Dataset(sellers=d["sellers"], resources=d["resources"], demands=d["demands"],
                   transport=d["transport"], settings=d["settings"]).model_dump(mode="json")
    if me["role"] == "seller":
        mine = _my_sample_sellers(me, key)
        body["sellers"] = [s for s in body["sellers"] if s["id"] in mine]
        body["resources"] = [r for r in body["resources"] if r["seller_id"] in mine]
    elif me["role"] == "buyer":
        body["demands"] = [x for x in body["demands"] if f"{key}:{x['buyer_id']}" == me["business_id"]]
    return {**body, **{k: d[k] for k in ("scenario", "title", "engine", "plan_seller", "metric_word")}}


def _gapfill_data() -> dict:
    """The sample job and standing orders, plus every buyer's open standing orders (a seller sees the ones that fit its sheet)."""
    g = SC.gapfill_demo()
    g["orders"] += _posted_standing()
    return g


@app.get("/api/gapfill/demo")
def gapfill_demo(me=SELLER):
    return _gapfill_data()


@app.post("/api/gapfill")
def api_gapfill(req: GapFillRequest, request: Request, record: bool = False, me=SELLER):
    """record=true (the Sold before it's cut screen): posted requirements in the plan are marked reserved
    by this seller; posted ones declined or left out go back to open."""
    try:
        plan = plan_gap_fill(req)
    except ValueError as e:
        _bad(e)
    if record:
        seller = OWN_NAME if me["admin"] else me["name"]
        _record_reservations(req, plan, seller)
        _safely(_notify_gapfill, plan, {o.id: o for o in req.orders}, _origin(request), seller)
    return plan


@app.post("/api/fulfil")
def api_fulfil(req: FulfilRequest, me=BUYER):
    d = next((x for x in req.data.demands if x.id == req.demand_id), None)
    if d is None:
        _bad(ValueError(f"unknown demand {req.demand_id}"))
    sellers = [s for s in req.data.sellers if req.seller_ids is None or s.id in req.seller_ids]
    keep = {s.id for s in sellers}
    res = [r for r in req.data.resources if r.seller_id in keep]
    try:
        return fulfil(d, sellers, res, req.data.transport, req.data.settings, req.data.alpha, req.allow_partial)
    except (ValueError, RuntimeError) as e:
        _bad(e)


@app.post("/api/seller-plan")
def api_seller_plan(req: SellerPlanRequest, me=ADMIN):
    seller = next((s for s in req.data.sellers if s.id == req.seller_id), None)
    if seller is None:
        _bad(ValueError(f"unknown seller {req.seller_id}"))
    try:
        built = seller_routes(seller, req.kind, req.data.sellers, req.data.resources, req.data.demands,
                              req.data.transport, req.data.settings, req.data.alpha, req.stockist_price_per_kg)
        result = seller_plan(built, req.deadline, None, req.rejected_route_ids, req.elapsed)
    except (ValueError, RuntimeError) as e:
        _bad(e)
    return {"seller_name": seller.name, "routes": [r.model_dump() for r in built["routes"]], "details": built["details"],
            "salvage_value_all": built["salvage_value_all"], "plan": result}


@app.post("/api/plan")
def api_plan(req: PlanRequest, me=ANY):
    try:
        return plan(req)
    except ValueError as e:
        _bad(e)


@app.post("/api/deals")
def create_deal(d: NewDeal, request: Request, me=BUYER):
    """A buyer accepts one of its own orders. The deal records both sides, so the buyer and every seller in it can see it."""
    key = d.scenario if d.scenario in SC.SCENARIOS else _scenario_of(d.demand_id)
    buyer_business = _demand_owner(key, d.demand_id)
    if d.requirement_id:
        req_owner = _requirement_owner(d.requirement_id)
        buyer_business = buyer_business or req_owner
        if not me["admin"] and req_owner != me["business_id"]:
            raise HTTPException(403, "You can accept only your own orders. Post a requirement to get your own.")
    if not me["admin"]:
        if buyer_business != me["business_id"]:
            raise HTTPException(403, "You can accept only your own orders. Post a requirement to get your own.")
    sellers_business = _seller_owners(key, d.sellers) if key else []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as con, con:
        cur = con.execute(
            "INSERT INTO deals (created_at, kind, counterfactual, demand_id, buyer_name, sellers, units, unit_name,"
            " kg_in_units, kg_shipped, route_km, value_above_salvage, buyer_utilisation, status, is_simulated,"
            " evidence, buyer_confirmed, history, scenario, rate_multiplier, seller_business, buyer_business)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, d.kind, d.counterfactual, d.demand_id, d.buyer_name, json.dumps(d.sellers), d.units, d.unit_name,
             d.kg_in_units, d.kg_shipped, d.route_km, d.value_above_salvage, d.buyer_utilisation, "agreed",
             int(d.is_simulated), "[]", 0, json.dumps([{"at": now, "status": "agreed"}]), d.scenario, d.rate_multiplier,
             json.dumps(sellers_business), buyer_business))
        if d.requirement_id:
            row = con.execute("SELECT history FROM requirements WHERE id=?", (d.requirement_id,)).fetchone()
            if row:
                con.execute("UPDATE requirements SET status='matched', deal_id=?, history=? WHERE id=?",
                            (cur.lastrowid, json.dumps(json.loads(row["history"]) + [{"at": now, "status": "matched", "deal": cur.lastrowid}]),
                             d.requirement_id))
                con.execute("DELETE FROM reservations WHERE req_id=?", (d.requirement_id,))
        new_id = cur.lastrowid
    _safely(_notify_deal, "order_accepted", new_id, _origin(request))
    return {"id": new_id, "status": "agreed"}


@app.post("/api/deals/{deal_id}/advance")
def advance(deal_id: int, a: Advance, request: Request, me=ANY):
    order = {"agreed": 0, "dispatched": 1, "delivered": 2}
    with closing(db()) as con, con:
        row = con.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
        if row is None or not _is_party(me, row):
            raise HTTPException(404, "deal not found")
        if order[a.status] != order[row["status"]] + 1:
            raise HTTPException(409, f"cannot go from {row['status']} to {a.status}")
        if a.status == "delivered" and not a.evidence:
            raise HTTPException(422, "delivery needs at least one piece of evidence (challan, invoice, photo)")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        hist = json.loads(row["history"]) + [{"at": now, "status": a.status}]
        con.execute("UPDATE deals SET status=?, evidence=?, buyer_confirmed=?, history=? WHERE id=?",
                    (a.status, json.dumps(json.loads(row["evidence"]) + a.evidence),
                     int(a.buyer_confirmed or row["buyer_confirmed"]), json.dumps(hist), deal_id))
    if a.status == "delivered":
        _safely(_notify_deal, "delivered", deal_id, _origin(request))
    return {"id": deal_id, "status": a.status}


@app.post("/api/ledger")
def ledger(f: ImpactFactors, me=ANY):
    """Deals this business is part of, as the buyer or one of the sellers. The admin sees every deal."""
    with closing(db()) as con:
        rows = [r for r in con.execute("SELECT * FROM deals ORDER BY id DESC").fetchall() if _is_party(me, r)]
    out = []
    for r in rows:
        imp = compute(DealImpactInput(
            kind=r["kind"], counterfactual=r["counterfactual"], units=r["units"], unit_name=r["unit_name"],
            kg_in_units=r["kg_in_units"], kg_shipped=r["kg_shipped"], route_km=r["route_km"],
            value_above_salvage=r["value_above_salvage"], buyer_utilisation=r["buyer_utilisation"],
            is_simulated=bool(r["is_simulated"]), status=r["status"], evidence=json.loads(r["evidence"]),
            buyer_confirmed=bool(r["buyer_confirmed"]), ef_avoided=f.ef_avoided, ef_truck=f.ef_truck))
        sellers = json.loads(r["sellers"])
        try:
            names = _deal_context(r)[1]
        except Exception:  # noqa: BLE001  an old deal the engine cannot place still lists its seller ids
            names = {}
        out.append({"id": r["id"], "kind": r["kind"], "buyer_name": r["buyer_name"], "sellers": sellers,
                    "seller_names": [names.get(x, x) for x in sellers], "status": r["status"], "is_simulated": bool(r["is_simulated"]),
                    "history": json.loads(r["history"]), "impact": imp})
    return {"deals": out}


@app.post("/api/reset")
def reset(me=ADMIN):
    with closing(db()) as con, con:
        con.execute("DELETE FROM deals")
    return {"ok": True}


# ---------------- stock board, passports, QR, data quality, simulation summary ----------------
import html as _html

try:
    import segno
except ImportError:  # the app still runs; only QR codes are missing
    segno = None


def _describe(p) -> str:
    if p.kind == "flat":
        extra = f", {len(p.defects)} defect(s)" if p.defects else ""
        return f"{p.length_mm:g} x {p.width_mm:g} x {p.thickness_mm:g} mm {M_name(p.material)} {p.grade}, {_words(p.condition)}{extra}"
    if p.kind == "linear":
        return f"{p.profile}, {p.length_mm:g} mm, {M_name(p.material)} {p.grade}, {_words(p.condition)}"
    return f"{p.product}, {p.quantity_kg:g} kg, {p.storage}, {p.hours_left:g} h left"


def _words(v: str) -> str:
    return v.replace("_", " ")


def _extras(p, seller_type: str | None) -> dict:
    """What a stock row, passport and Browse card show besides the basics: condition, seller type, and the rebar weight note."""
    from engine import materials as M
    out = {"seller_type": seller_type, "seller_type_name": SELLER_TYPES.get(seller_type or "")}
    if p.kind != "perishable":
        out.update(material=p.material, condition=p.condition, condition_name=_words(p.condition).capitalize())
    if p.kind == "linear" and p.material in M.BAR_MATERIALS:
        out.update(weight_note=M.REBAR_WEIGHT_NOTE, weight_label="assumption")
    return out


def _seller_types() -> dict[str, str]:
    """business name -> seller type, for seller accounts (sample sellers without an account have none)."""
    return {u["business_name"]: u["seller_type"] for u in _accounts_by_bid().values() if u["role"] == "seller" and u["seller_type"]}


def M_name(key: str) -> str:
    from engine import materials as M
    return M.get(key).name


def _import_key(lane: str, owner: str = "") -> str:
    """Stock key for a seller's listed items: import_durable@<business id>. Plain import_durable holds rows saved before accounts."""
    return f"import_{lane}" + (f"@{owner}" if owner else "")


def _parse_import(key: str) -> tuple[str, str] | None:
    """(lane, owner) for an import key, else None."""
    m = re.fullmatch(r"import_(durable|fresh)(?:@(.+))?", key)
    return (m.group(1), m.group(2) or "") if m else None


def _import_rows(lane: str, owner: str | None = None) -> list:
    """Listed rows for one owner, or for every owner (None)."""
    q, args = "SELECT * FROM imported_stock WHERE lane=?", [lane]
    if owner is not None:
        q, args = q + " AND owner=?", args + [owner]
    with closing(db()) as con:
        return con.execute(q + " ORDER BY listed_at, item_id", args).fetchall()


def _imported_stock(lane: str, owner: str | None = None) -> list[dict]:
    names, accounts = _owner_names(), _accounts_by_bid()
    out = []
    for r in _import_rows(lane, owner):
        p = IMP.parse_item(lane, r["item"])
        key = _import_key(lane, r["owner"])
        u = accounts.get(r["owner"])
        item = {"id": p.id, "seller_id": p.seller_id, "seller_name": names.get(r["owner"]) or p.seller_id, "kind": p.kind,
                "description": _describe(p), "kg": round(REGISTRY[p.kind].resource_kg(p, Settings()), 2),
                "scrap_value": None, "scrap_price_per_kg": None, "label": r["label"], "status": "available",
                "passport": f"/passport/{key}/{p.id}", "rules": json.loads(r["rules"]), "source": r["source"] or IMP.SOURCE,
                "listed_at": r["listed_at"], "key": key, "owner": r["owner"], **_extras(p, u["seller_type"] if u else None)}
        if p.kind == "perishable":
            item["hours_left"] = p.hours_left
            item["urgent"] = True
        else:
            item["days_unused"] = p.days_unused
        out.append(item)
    return out


def _stock(key: str) -> list[dict]:
    """Every item under one stock key (passports and labels are public, for QR codes)."""
    imp = _parse_import(key)
    if imp:
        return _imported_stock(*imp)
    if key not in SC.SCENARIOS:
        raise HTTPException(404, f"unknown scenario {key}")
    d = SC.dataset(key)
    names = {s.id: s.name for s in d["sellers"]}
    sellers = {s.id: s for s in d["sellers"]}
    types = _seller_types()
    out = []
    for p in d["resources"]:
        eng = REGISTRY[p.kind]
        kg = eng.resource_kg(p, d["settings"])
        price = sellers[p.seller_id].salvage_for(eng.salvage_key(p)).price_per_kg
        item = {"id": p.id, "seller_id": p.seller_id, "seller_name": names[p.seller_id], "kind": p.kind,
                "description": _describe(p), "kg": round(kg, 2), "scrap_value": round(kg * price, 2),
                "scrap_price_per_kg": price, "label": p.label, "status": "available",
                "passport": f"/passport/{key}/{p.id}", **_extras(p, types.get(names[p.seller_id]))}
        if p.kind == "perishable":
            item["hours_left"] = p.hours_left
        out.append(item)
    return out


@app.get("/api/stock/{key}")
def stock(key: str, request: Request, me=SELLER):
    """A seller's own stock: its sample pieces in a cluster, or the items it listed (import_durable or import_fresh).
    The admin gets every seller's, including rows saved before accounts existed."""
    if key == "food" or key.startswith("import_fresh"):
        _safely(_sweep_fresh, _origin(request))
    imp = _parse_import(key)
    if imp:
        lane, owner = imp
        if not me["admin"]:
            if owner and owner != me["business_id"]:
                raise HTTPException(404, f"unknown scenario {key}")
            owner = me["business_id"]
        mine = _import_key(lane, owner) if owner else key
        return {"scenario": mine, "items": _imported_stock(lane, owner or None), "labels": f"/labels/{mine}"}
    items = _stock(key)
    if not me["admin"]:
        own = _my_sample_sellers(me, key)
        items = [i for i in items if i["seller_id"] in own]
    return {"scenario": key, "items": items, "labels": f"/labels/{key}"}


@app.get("/api/qr.svg")
def qr(data: str):
    if segno is None:
        raise HTTPException(503, "QR library not installed: pip install segno")
    buf = io.BytesIO()
    segno.make(data[:500], error="m").save(buf, kind="svg", scale=4, border=2, xmldecl=False, svgns=True, nl=False)
    return Response(buf.getvalue(), media_type="image/svg+xml")


_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{font:15px/1.5 system-ui,sans-serif;margin:0;background:#eef1f4;color:#17222b}}
main{{max-width:520px;margin:0 auto;padding:20px 16px;display:flex;flex-direction:column;gap:14px}}
.card{{background:#fff;border:1px solid #d9dfe4;border-radius:8px;padding:16px}}
h1{{font-size:1.3rem;margin:0}} dl{{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;margin:0}} dt{{color:#5d6b77}} dd{{margin:0}}
.chip{{display:inline-block;background:#fff2dc;color:#8a4b00;border:1px solid #8a4b00;border-radius:99px;padding:1px 10px;font-size:12px;font-weight:600;text-transform:uppercase}}
svg{{width:140px;height:140px}} .note{{color:#5d6b77;font-size:13px}}
img.photo{{display:block;width:100%;height:auto;border-radius:6px;margin:8px 0}}
.opt{{border:1px solid #d9dfe4;border-radius:8px;padding:12px;margin:10px 0;display:grid;grid-template-columns:120px 1fr;gap:12px;align-items:center}}
.opt svg.plan{{width:120px;height:auto;background:#eef1f4;border-radius:4px}} .opt b.v{{font-size:1.15rem;color:#b45f06}} .opt b.neg{{color:#5d6b77}}
.tag{{display:inline-block;font-size:12px;font-weight:600;border-radius:99px;padding:1px 8px;background:#eef1f4;color:#17222b}} .tag.o{{background:#e2f3e9;color:#1d6b3f}}
details{{margin-top:8px}} summary{{cursor:pointer;font-weight:600}} details li{{margin:4px 0;color:#5d6b77}}
@media (max-width:420px){{.opt{{grid-template-columns:1fr}} .opt svg.plan{{width:100%}}}} form{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}}
button{{font:inherit;font-weight:600;padding:6px 14px;border-radius:6px;border:1px solid #c4ccd3;background:#fff;cursor:pointer}} button:hover{{border-color:#5d6b77}}
button:focus-visible,input:focus-visible{{outline:2px solid #d9780f;outline-offset:2px}}
@media print{{body{{background:#fff}} .noprint{{display:none}}}}
</style><main>{body}</main>"""


def _origin(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@app.get("/passport/{key}/{rid}", response_class=HTMLResponse)
def passport(key: str, rid: str, request: Request):
    item = next((i for i in _stock(key) if i["id"] == rid), None)
    if item is None:
        raise HTTPException(404, "no such item")
    e = _html.escape
    weight = f"{item['kg']} kg" + (f" (Assumption: {item['weight_note']})" if item.get("weight_note") else "")
    rows = [("Passport ID", f"{key}:{item['id']}"), ("Seller", item["seller_name"]), ("What it is", item["description"]),
            ("Weight", weight),
            ("Scrap value", "No scrap price entered yet. Add a dated quote before showing one." if item["scrap_value"] is None
             else f"Rs {item['scrap_value']:,.0f} at Rs {item['scrap_price_per_kg']}/kg"),
            ("Status", item["status"]), ("Data", item["label"].title())]
    if "source" in item:
        rows += [("Source", item["source"]), ("Found as surplus by", "; ".join(item["rules"]))]
    if "hours_left" in item:
        rows.insert(3, ("Shelf life left", f"{item['hours_left']:g} hours"))
    if item.get("condition_name"):
        rows.insert(3, ("Condition", item["condition_name"]))
    dl = "".join(f"<dt>{e(a)}</dt><dd>{e(str(b))}</dd>" for a, b in rows)
    if item.get("seller_type_name"):  # a small chip after the seller's name
        dl = dl.replace(f"<dd>{e(item['seller_name'])}</dd>",
                        f'<dd>{e(item["seller_name"])} <span class="tag">{e(item["seller_type_name"])}</span></dd>', 1)
    qr_url = f"/api/qr.svg?data={_origin(request)}/passport/{key}/{rid}"
    share = (f'<p class="noprint"><a href="/share/{e(key)}/{e(rid)}" target="_blank" rel="noopener" style="display:inline-block;padding:8px 14px;'
             f'border:1px solid #c4ccd3;border-radius:8px;font-weight:600;color:#17222b;text-decoration:none">Share on WhatsApp</a>'
             f' <span class="note">Opens WhatsApp with this item\'s details; you choose the chat and press send.</span></p>')
    body = (f'<div class="card"><h1>Offcut passport</h1><p><span class="chip">{e(item["label"])} data</span></p><dl>{dl}</dl>{share}</div>'
            f'<div class="card"><b>Deal history</b><p class="note">No deals recorded for this item yet. A deal appears here once it is dispatched and confirmed.</p></div>'
            f'<div class="card"><img alt="QR code for this passport" src="{e(qr_url)}" width="140" height="140"><p class="note">Scan to open this passport.</p></div>'
            + _become_card(key, rid)
            + _photo_card(key, rid, item))
    return _PAGE.format(title=f"Passport {item['id']}", body=body)


def _photo_card(key: str, rid: str, item: dict) -> str:
    """Optional photo of this exact item, uploaded by the seller. Never a stock image."""
    e, url = _html.escape, _photo_url(key, rid)
    shown = (f'<img class="photo" alt="Photo of {e(rid)} uploaded by the seller" src="{e(url)}">'
             f'<p class="note">Photo uploaded by the seller.</p>' if url else
             '<p class="note">No photo yet. Only a photo the seller takes of this item is shown; no stock images.</p>')
    api = json.dumps(f"/api/photo/{key}/{rid}").replace("</", "<\\/")
    what = "lot" if item["kind"] == "perishable" else "piece"
    remove = '<button type="button" id="pdel">Remove photo</button>' if url else ""
    return (f'<div class="card"><b>Photo</b>{shown}<div class="noprint"><form id="pf"><input type="file" id="pfile" accept="image/*" capture="environment" aria-label="Take or choose a photo">'
            f'<button type="submit">{"Replace photo" if url else "Upload photo"}</button>{remove}</form>'
            f'<p class="note" id="pmsg" aria-live="polite">Seller only: a photo of this exact {what}. On a phone this opens the camera. JPEG, PNG or WebP, up to 5 MB.</p></div></div>'
            f'<script>const PURL={api},msg=t=>document.getElementById("pmsg").textContent=t;'
            'document.getElementById("pf").onsubmit=async ev=>{ev.preventDefault();const f=document.getElementById("pfile").files[0];'
            'if(!f){msg("Choose a photo first.");return}msg("Uploading...");'
            'const r=await fetch(PURL,{method:"POST",headers:{"Content-Type":f.type||"application/octet-stream"},body:f});'
            'if(r.ok)location.reload();else{const t=await r.json().catch(()=>({}));msg(t.detail||"Upload failed. Try a JPEG or PNG under 5 MB.")}};'
            'const d=document.getElementById("pdel");if(d)d.onclick=async()=>{await fetch(PURL,{method:"DELETE"});location.reload()};</script>')


@app.get("/labels/{key}", response_class=HTMLResponse)
def labels(key: str, request: Request):
    e = _html.escape
    cells = ""
    for i in _stock(key):
        url = f"{_origin(request)}/passport/{key}/{i['id']}"
        cells += (f'<div style="border:1px dashed #999;padding:8px;text-align:center;width:150px"><img alt="QR {e(i["id"])}" '
                  f'src="/api/qr.svg?data={e(url)}" width="110" height="110"><div><b>{e(i["seller_id"] + i["id"] if not i["id"].startswith(i["seller_id"]) else i["id"])}</b></div>'
                  f'<div style="font-size:11px">{e(i["description"][:46])}</div><div style="font-size:11px;color:#8a4b00">{e(i["label"]).upper()} DATA</div></div>')
    body = f'<h1>Passport labels: {e(key)}</h1><p class="note">Print this page and stick a label on each piece. QR codes open the passport on this server.</p><div style="display:flex;flex-wrap:wrap;gap:8px">{cells}</div>'
    return _PAGE.format(title=f"Labels {key}", body=body).replace("max-width:520px", "max-width:900px")


@app.get("/api/data-quality")
def data_quality(me=ADMIN):
    from collections import Counter
    rows = []
    for key in SC.SCENARIOS:
        d = SC.dataset(key)
        for name, objs in (("Sellers", d["sellers"]), ("Stock items", d["resources"]), ("Buyer orders", d["demands"])):
            for label, n in Counter(o.label for o in objs).items():
                rows.append({"area": f"{SC.SCENARIOS[key]['title']}: {name.lower()}", "label": label, "count": n})
    g = SC.gapfill_demo()
    rows.append({"area": "Gap Fill demo: standing orders", "label": "sample", "count": len(g["orders"])})
    rows.append({"area": "Gap Fill simulation: sheets", "label": "simulated", "count": 5000})
    with closing(db()) as con:
        n_sim = con.execute("SELECT COUNT(*) FROM deals WHERE is_simulated=1").fetchone()[0]
        n_real = con.execute("SELECT COUNT(*) FROM deals WHERE is_simulated=0").fetchone()[0]
        for lane, label, src, n in con.execute("SELECT lane, label, COALESCE(source, ''), COUNT(*) FROM imported_stock GROUP BY lane, label, source"):
            area = "Entered by hand" if src == IMP.MANUAL_SOURCE else "Imported stock (CSV)"
            rows.append({"area": f"{area}, {lane}", "label": label, "count": n})
    rows.append({"area": "Deals made in this demo", "label": "simulated", "count": n_sim})
    rows.append({"area": "Deals with real delivery", "label": "verified", "count": n_real})
    return {"rows": rows}


@app.get("/api/sim-summary")
def sim_summary(me=ADMIN):
    p = ROOT.parent / "research" / "sim_summary.json"
    if not p.exists():
        raise HTTPException(404, "research/sim_summary.json is missing")
    return json.loads(p.read_text())


# ---------------- Connect your system: import from ERP or POS export (CSV) ----------------
EXAMPLES = ROOT / "static" / "examples"


class ImportRequest(BaseModel):
    lane: Literal["durable", "fresh"]
    csv_text: str = Field(max_length=2_000_000)
    days_unused_limit: int = Field(30, ge=0, le=36500)
    hours_left_limit: float = Field(24, ge=0, le=24 * 365)


class ListRequest(ImportRequest):
    measured: bool = False


class CuttingJob(BaseModel):
    sheet_length_mm: float = Field(gt=0, le=12000)
    sheet_width_mm: float = Field(gt=0, le=4000)
    thickness_mm: float = Field(gt=0, le=100)
    grade: str = Field(min_length=1)
    part_name: str = "own part"
    part_length_mm: float = Field(gt=0)
    part_width_mm: float = Field(gt=0)
    quantity: int = Field(ge=1, le=10000)


def _run_import(req: ImportRequest, label="sample"):
    try:
        upload = CN.CsvUpload(req.csv_text).stock_csv(req.lane)[0]
        return IMP.run_import(req.lane, upload.text, req.days_unused_limit, req.hours_left_limit, label)
    except ValueError as e:
        _bad(e)


@app.get("/api/import/example/{lane}")
def import_example(lane: Literal["durable", "fresh"], me=SELLER):
    name = f"{lane}_stock_example.csv"
    return FileResponse(EXAMPLES / name, media_type="text/csv", filename=name)


@app.post("/api/import/preview")
def import_preview(req: ImportRequest, me=SELLER):
    """Check every row and sort the good ones into surplus and working stock. Nothing is stored."""
    return _run_import(req)


def _list_surplus(lane: str, result: dict, label: str, source: str = IMP.SOURCE, owner: str = "") -> int:
    """Put an import's surplus on the marketplace: stock board, passports and QR codes. Same item id from the same seller = updated."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as con, con:
        for s in result["surplus"]:
            con.execute("INSERT OR REPLACE INTO imported_stock (owner, lane, item_id, item, rules, label, listed_at, source) VALUES (?,?,?,?,?,?,?,?)",
                        (owner, lane, s["item"]["id"], json.dumps(s["item"]), json.dumps(s["rules"]), label, now, source))
    return len(result["surplus"])


@app.post("/api/import/list")
def import_list(req: ListRequest, me=SELLER):
    """List the surplus items on the marketplace (stock board, passports, QR codes), owned by this seller."""
    label = "measured" if req.measured else "sample"
    r = _run_import(req, label)
    if not r["surplus"]:
        _bad(ValueError("Nothing was found as surplus, so nothing was listed. Lower the thresholds or check the file."))
    _list_surplus(req.lane, r, label, owner=me["business_id"])
    key = _import_key(req.lane, me["business_id"])
    return {"listed": len(r["surplus"]), "label": label, "stock_key": key, "items": _stock(key)}


@app.post("/api/import/clear")
def import_clear(lane: Literal["durable", "fresh"], me=SELLER):
    """Remove this seller's listed items in one lane. The admin removes every seller's."""
    q, args = "DELETE FROM imported_stock WHERE lane=?", [lane]
    if not me["admin"]:
        q, args = q + " AND owner=?", args + [me["business_id"]]
    with closing(db()) as con, con:
        n = con.execute(q, args).rowcount
    return {"removed": n}


@app.post("/api/import/cutting-job")
def import_cutting_job(j: CuttingJob, me=SELLER):
    """Turn the next cutting job into a Sold-before-it's-cut request, checked by the engine before it is sent."""
    g = _gapfill_data()
    g["sheet"].update(length_mm=j.sheet_length_mm, width_mm=j.sheet_width_mm, thickness_mm=j.thickness_mm,
                      grade=j.grade.strip().upper())
    g["job"] = dict(part_name=j.part_name.strip() or "own part", length_mm=j.part_length_mm, width_mm=j.part_width_mm,
                    quantity=j.quantity)
    try:
        plan_gap_fill(GapFillRequest(**g))
    except ValueError as e:
        _bad(e)
    return {"gapfill": g}


# ---------------- Browse surplus (buyer marketplace) and seller photos ----------------
PHOTOS = ROOT.parent / "uploads" / "photos"
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
PHOTO_MAX = 5 * 1024 * 1024


def _photo_file(key: str, rid: str) -> Path | None:
    folder = PHOTOS / re.sub(r"[^A-Za-z0-9_]", "_", key)
    for ext in PHOTO_TYPES.values():
        f = folder / (rid.encode().hex() + ext)  # hex name: any item id becomes a safe, unique file name
        if f.exists():
            return f
    return None


def _photo_url(key: str, rid: str) -> str | None:
    f = _photo_file(key, rid)
    return f"/api/photo/{key}/{rid}?v={int(f.stat().st_mtime)}" if f else None


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _item_or_404(key: str, rid: str) -> dict:
    item = next((i for i in _stock(key) if i["id"] == rid), None)
    if item is None:
        raise HTTPException(404, "no such item")
    return item


def _may_change(me: dict, key: str, rid: str) -> None:
    """Only the seller who listed an item (or the admin) may change its photo."""
    item = _item_or_404(key, rid)
    if me["admin"]:
        return
    imp = _parse_import(key)
    ok = imp[1] == me["business_id"] if imp else item["seller_id"] in _my_sample_sellers(me, key)
    if not ok:
        raise HTTPException(403, "Only the seller who listed this item can change its photo.")


@app.post("/api/photo/{key}/{rid}")
async def upload_photo(key: str, rid: str, request: Request, me=SELLER):
    """The seller's own photo of this item. The body is the image file itself (JPEG, PNG or WebP)."""
    _may_change(me, key, rid)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    data = await request.body()
    problem = _photo_problem(ctype, data)
    if problem:
        raise HTTPException(*problem)
    return {"photo": _write_photo(key, rid, ctype, data)}


def _photo_problem(ctype: str, data: bytes) -> tuple[int, str] | None:
    if ctype not in PHOTO_TYPES:
        return 415, "Send a JPEG, PNG or WebP photo."
    if not data:
        return 422, "The photo is empty. Choose the file again."
    if len(data) > PHOTO_MAX:
        return 413, "The photo is larger than 5 MB. Resize it and try again."
    if _sniff(data) != ctype:
        return 422, "The file is not a real JPEG, PNG or WebP image."
    return None


def _write_photo(key: str, rid: str, ctype: str, data: bytes) -> str:
    old = _photo_file(key, rid)
    if old:
        old.unlink()
    f = PHOTOS / re.sub(r"[^A-Za-z0-9_]", "_", key) / (rid.encode().hex() + PHOTO_TYPES[ctype])
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    return _photo_url(key, rid)


@app.get("/api/photo/{key}/{rid}")
def get_photo(key: str, rid: str):
    f = _photo_file(key, rid)
    if f is None:
        raise HTTPException(404, "no photo for this item")
    return FileResponse(f, media_type={v: k for k, v in PHOTO_TYPES.items()}[f.suffix])


@app.delete("/api/photo/{key}/{rid}")
def delete_photo(key: str, rid: str, me=SELLER):
    _may_change(me, key, rid)
    f = _photo_file(key, rid)
    if f:
        f.unlink()
    return {"removed": bool(f)}


ILLUSTRATIVE = ROOT / "static" / "illustrative" / "credits.json"
FOOD_WORDS = (("tomato", "tomatoes"), ("onion", "onions"), ("bread", "bread"), ("paneer", "paneer"), ("rice", "cooked_rice"),
              ("biryani", "cooked_rice"))
FOOD_CATEGORY = {"vegetables": "vegetables", "bakery": "bread", "dairy": "paneer"}
SHEET_MATERIAL = {"mild_steel": "mild_steel", "aluminium": "aluminium", "plywood": "plywood"}


def _illustrative(item: dict) -> dict | None:
    """A freely licensed photo of the same kind of thing, never of the item itself. None if nothing matches."""
    if item.get("photo") or not ILLUSTRATIVE.exists():
        return None  # a seller's own photo always wins
    credits = json.loads(ILLUSTRATIVE.read_text(encoding="utf-8"))
    if item["kind"] == "flat":
        key = SHEET_MATERIAL.get(item["material"])
    elif item["kind"] == "linear":
        key = "steel_bars_pipes" if item["material"] == "mild_steel" else None
    else:
        name = item["product"].lower()
        key = next((k for w, k in FOOD_WORDS if w in name), None) or FOOD_CATEGORY.get(item["category"])
    photos = [c for stem, c in sorted(credits.items()) if (c.get("category") or stem) == key] if key else []
    if not photos:
        return None
    import zlib
    c = photos[zlib.crc32(f"{item.get('key')}:{item['id']}".encode()) % len(photos)]  # stable: the same item, the same photo
    return {"url": c["file"], "shows": c["shows"], "title": c["title"], "author": c["author"], "licence": c["licence"],
            "licence_url": c["licence_url"], "source_url": c["source_url"], "site": c.get("site", "Wikimedia Commons")}


def _browse_scenario(key: str, me: dict) -> tuple[list[dict], dict]:
    """Every seller's stock in one sample cluster. Orders it fits and distances are this buyer's own (the admin: the cluster's)."""
    d = SC.dataset(key)
    cfg, sellers = d["settings"], {s.id: s for s in d["sellers"]}
    demands = _my_demands(me, key)
    here = _buyer_origin(me, key, demands)
    types = _seller_types()
    out = []
    for p in d["resources"]:
        eng, seller = REGISTRY[p.kind], sellers[p.seller_id]
        kg = eng.resource_kg(p, cfg)
        offer = seller.salvage_for(eng.salvage_key(p))
        alt = {"value": round(kg * offer.price_per_kg, 2), "price_per_kg": offer.price_per_kg, "label": offer.label,
               "quoted_on": offer.quoted_on.isoformat() if offer.quoted_on else None}
        item = {**p.model_dump(mode="json"), "key": key, "seller_name": seller.name, "kg": round(kg, 2),
                "distance_km": round(_dist((seller.x_km, seller.y_km), (here["x_km"], here["y_km"])), 1),
                "approx_location": here["approx"], "passport": f"/passport/{key}/{p.id}", "photo": _photo_url(key, p.id),
                **_extras(p, types.get(seller.name))}
        if p.kind == "perishable":
            item["disposal"] = alt
        else:
            item["material_name"] = M_name(p.material)
            item["floor"] = alt
            same = [x for x in d["demands"] if x.kind == p.kind and x.material == p.material]  # sample prices, for the range
            low = min(same, key=lambda x: x.new_price_per_kg) if same else None  # the cautious ceiling
            item["ceiling"] = None if low is None else {"value": round(kg * low.new_price_per_kg, 2),
                                                        "price_per_kg": low.new_price_per_kg, "label": low.label,
                                                        "buyer_name": low.buyer_name}
        fits, why = None, None
        for i, dm in enumerate(demands):
            if dm.kind != p.kind or not eng.relevant(p, dm):
                continue
            reason = eng.check(p, dm, seller, cfg)
            if reason is None and eng.capacity(p, dm, cfg) <= 0:
                reason = f"too small for one {dm.part_name}"
            if reason is None:
                fits = (i, dm)
                break
            why = why or f"{dm.buyer_name}: {reason}"
        item["order"] = {"scenario": key, "index": fits[0], "demand_id": fits[1].id, "buyer_name": fits[1].buyer_name} if fits else None
        item["order_reason"] = None if fits else (why or (NO_ORDER if demands else NO_ORDER_YET))
        out.append(item)
    return out, here


NO_ORDER = "No open order in this cluster takes this kind of stock."
NO_ORDER_YET = "You have no open order this could fill. Post a requirement and it is matched against this stock."
NO_OWNER = "Saved before accounts existed, so it has no seller and is not matched."


def _browse_imported(lane: str, me: dict) -> list[dict]:
    """Items sellers listed themselves, each matched as its own seller. Rows saved before accounts: admin only."""
    from engine.models import PerishableLot
    names, accounts = _owner_names(), _accounts_by_bid()
    out = []
    for r in _import_rows(lane):
        owner = r["owner"]
        if not owner and not me["admin"]:
            continue
        p = IMP.parse_item(lane, r["item"])
        key = _import_key(lane, owner)
        item = {**p.model_dump(mode="json"), "key": key, "label": r["label"], "seller_name": names.get(owner) or p.seller_id,
                "kg": round(REGISTRY[p.kind].resource_kg(p, Settings()), 2), "distance_km": None, "approx_location": False,
                "passport": f"/passport/{key}/{p.id}", "photo": _photo_url(key, p.id), "rules": json.loads(r["rules"]),
                "source": r["source"] or IMP.SOURCE, "match_seller": None, "order": None, "order_reason": NO_OWNER,
                **_extras(p, accounts[owner]["seller_type"] if owner in accounts else None)}
        if owner:
            item.update(_own_order(p, owner, me))
        if p.kind == "perishable":
            item["disposal"] = None
        else:
            item.update(material_name=M_name(p.material), floor=None, ceiling=None)
        out.append(item)
    return out


@app.get("/api/browse/{lane}")
def browse(lane: Literal["durable", "fresh"], request: Request, me=BUYER):
    """Every seller's stock in this lane as a marketplace listing: sample clusters plus items sellers listed themselves."""
    items, buyers = [], {}
    for key, sc in SC.SCENARIOS.items():
        if (sc["engine"] == "perishable") == (lane == "fresh"):
            rows, here = _browse_scenario(key, me)
            buyers[key] = here["name"]
            items += rows
    items += _browse_imported(lane, me)
    if lane == "fresh":
        _safely(_sweep_fresh, _origin(request))
    for it in items:
        it["illustrative"] = _illustrative(it)
    return {"lane": lane, "items": items, "distance_from": buyers}


# ---------------- Pickup and delivery (transporters and bookings are simulated) ----------------
SLOTS = ["08:00-10:00", "10:00-12:00", "12:00-14:00", "14:00-16:00", "16:00-18:00", "18:00-20:00"]
SIMULATED = "Transporters and bookings are simulated in this prototype."


class Booking(BaseModel):
    pickup_date: date
    slot: Literal["08:00-10:00", "10:00-12:00", "12:00-14:00", "14:00-16:00", "16:00-18:00", "18:00-20:00"]


def _deal_row(deal_id: int):
    with closing(db()) as con:
        row = con.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "deal not found")
    return row


def _slot_start(b: dict) -> datetime:
    return datetime.fromisoformat(f"{b['date']}T{b['slot'][:5]}")


def _deal_for(me: dict, deal_id: int):
    """A deal only its buyer, its sellers and the admin can see. Anyone else gets "deal not found"."""
    row = _deal_row(deal_id)
    if not _is_party(me, row):
        raise HTTPException(404, "deal not found")
    return row


def _logistics(row, now: datetime | None = None) -> dict:
    """Re-run the engine on exactly the deal's sellers: same pickup order, vehicle and transport cost as when it was agreed."""
    key = row["scenario"] if row["scenario"] in SC.SCENARIOS else next(
        (k for k in SC.SCENARIOS if any(d.id == row["demand_id"] for d in SC.dataset(k)["demands"])), None)
    if key is None:
        raise HTTPException(422, f"deal {row['id']} refers to order {row['demand_id']}, which is in no scenario")
    d = _dataset_with_own(key)
    approx = set(_owned(key)["approx"])
    demand = next(x for x in d["demands"] if x.id == row["demand_id"])
    used = json.loads(row["sellers"])
    sellers = [x for x in d["sellers"] if x.id in used]
    res = [r for r in d["resources"] if r.seller_id in used]
    transport = d["transport"].model_copy(update={"rate_multiplier": row["rate_multiplier"] or 1.0})
    f = fulfil(demand, sellers, res, transport, d["settings"], 0.5, True)
    b = f["best"]
    if b is None:
        raise HTTPException(422, f"the engine can no longer build this pickup: {f['message']}")
    by_id = {x.id: x for x in d["sellers"]}
    res_by_id = {r.id: r for r in res}
    depot = (demand.x_km, demand.y_km)
    stops = [("buyer", demand.buyer_name, depot)] + [(sid, by_id[sid].name, (by_id[sid].x_km, by_id[sid].y_km)) for sid in b["pickup_order"]]
    legs = []
    for i, (a, z) in enumerate(zip(stops, stops[1:] + stops[:1]), start=1):
        legs.append({"n": i, "from": a[1], "to": z[1], "km": round(_dist(a[2], z[2]), 2)})
    per = {x["seller_id"]: x for x in b["per_seller"]}
    collect = []
    for n, sid in enumerate(b["pickup_order"], start=1):
        items = []
        for a in b["allocations"]:
            if a["seller_id"] != sid:
                continue
            r = res_by_id[a["resource_id"]]
            it = {"resource_id": r.id, "what": _describe(r), "units": a["units"]}
            if r.kind == "perishable":
                it.update(product=r.product, kg=a["taken_kg"], storage=r.storage, hours_left=r.hours_left)
            items.append(it)
        collect.append({"stop": n, "seller_id": sid, "seller_name": by_id[sid].name, "x_km": by_id[sid].x_km,
                        "y_km": by_id[sid].y_km, "kg": per[sid]["kg_shipped"], "units": per[sid]["units"], "items": items})
    vehicle = next((v for v in transport.vehicles if v.name == b["vehicle"]), None)
    booking = json.loads(row["booking"]) if row["booking"] else None
    out = {"deal": {"id": row["id"], "status": row["status"], "buyer_name": row["buyer_name"], "kind": row["kind"],
                    "is_simulated": bool(row["is_simulated"]), "evidence": json.loads(row["evidence"]),
                    "buyer_confirmed": bool(row["buyer_confirmed"]), "history": json.loads(row["history"])},
           "scenario": key, "unit_name": f["unit_name"], "part_name": getattr(demand, "part_name", ""),
           "buyer": {"name": demand.buyer_name, "x_km": demand.x_km, "y_km": demand.y_km},
           "pickup_order": b["pickup_order"], "legs": legs, "route_km": b["route_km"], "tour_hours": b["tour_hours"],
           "speed_kmph": transport.speed_kmph, "collect": collect,
           "vehicle": {"name": b["vehicle"], "payload_kg": vehicle.payload_kg if vehicle else None, "trips": b["trips"],
                       "load_kg": b["kg_shipped"], "transport_cost": b["value"]["transport_T"],
                       "rate_multiplier": transport.rate_multiplier, "label": transport.label},
           "matches_deal": sorted(b["sellers_used"]) == sorted(used), "booking": booking, "slots": SLOTS,
           "note": SIMULATED, "warnings": [],
           "approx_location": any(c["seller_id"] in approx for c in collect) or demand.id in _approx_demands(key)}
    if row["kind"] == "perishable":
        now = now or datetime.now()
        wait = max(0.0, (_slot_start(booking) - now).total_seconds() / 3600) if booking else None
        if booking:
            out["arrival"] = (_slot_start(booking) + timedelta(hours=b["tour_hours"])).isoformat(timespec="minutes")
        lots, cold = [], []
        for c in collect:
            for it in c["items"]:
                now_left = round(it["hours_left"] - b["tour_hours"], 2)  # the engine's own arrival rule
                at_slot = None if wait is None else round(it["hours_left"] - wait - b["tour_hours"], 2)
                lots.append({**it, "seller_name": c["seller_name"], "on_arrival_if_collected_now": now_left,
                             "on_arrival_at_booked_slot": at_slot, "min_on_arrival": demand.min_hours_left_on_arrival,
                             "ok": (at_slot if at_slot is not None else now_left) >= demand.min_hours_left_on_arrival})
                if it["storage"] in ("chilled", "frozen"):
                    cold.append(f"{it['product']} ({it['storage']})")
        out["lots"] = lots
        if cold:
            out["warnings"].append(f"Needs a chilled vehicle or insulated boxes: {', '.join(cold)}. "
                                   f"The {b['vehicle']} in this prototype has no cold chain.")
        late = [x["product"] for x in lots if not x["ok"]]
        if late:
            out["warnings"].append(f"Would arrive with less than the buyer's {demand.min_hours_left_on_arrival:g} h of shelf life: "
                                   f"{', '.join(late)}. Book an earlier slot.")
    if not out["matches_deal"]:
        out["warnings"].append("The engine now picks different sellers for this order than the deal recorded; check before booking.")
    return out


@app.get("/api/deals/{deal_id}/logistics")
def deal_logistics(deal_id: int, now: datetime | None = None, me=ANY):
    """Map, vehicle, schedule and collection list for one deal. `now` is only for testing the clock."""
    return _logistics(_deal_for(me, deal_id), now)


@app.post("/api/deals/{deal_id}/booking")
def book_pickup(deal_id: int, bk: Booking, request: Request, me=ANY):
    row = _deal_for(me, deal_id)
    if row["status"] != "agreed":
        raise HTTPException(409, f"the goods are already {row['status']}; a pickup can only be booked before collection")
    if bk.pickup_date < datetime.now().date():
        raise HTTPException(422, "pick today or a later date")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as con, con:
        con.execute("UPDATE deals SET booking=?, history=? WHERE id=?",
                    (json.dumps({"date": bk.pickup_date.isoformat(), "slot": bk.slot, "booked_at": now}),
                     json.dumps(json.loads(row["history"]) + [{"at": now, "status": "pickup booked", "slot": f"{bk.pickup_date} {bk.slot}"}]),
                     deal_id))
    lg = _logistics(_deal_row(deal_id))
    _safely(_notify_deal, "pickup_booked", deal_id, _origin(request), lg)
    return lg


def _doc(title: str, body: str) -> str:
    return _PAGE.format(title=title, body=body).replace("max-width:520px", "max-width:820px").replace("</style>", "table{width:100%;border-collapse:collapse} th,td{text-align:left;padding:6px 8px;"
        "border-bottom:1px solid #d9dfe4;vertical-align:top} th{font-size:12px;text-transform:uppercase;color:#5d6b77} .sig{display:flex;gap:24px;"
        "flex-wrap:wrap;margin-top:12px} .sig div{flex:1;min-width:200px;border-top:1px solid #17222b;padding-top:4px;margin-top:36px;font-size:13px}"
        " .warn{background:#fff2dc;color:#8a4b00;border-radius:6px;padding:8px 12px}</style>")


def _qty(it, lg):
    return f"{it['kg']:g} kg" if "kg" in it else f"{it['units']:g} {lg['part_name']}"


def _deal_page(request: Request, deal_id: int):
    """Printable deal papers: signed out goes to the start page; only the deal's buyer, its sellers and the admin may open them."""
    sess = _session(request)
    if sess is None:
        return None
    return _logistics(_deal_for(_me(sess), deal_id))


@app.get("/deals/{deal_id}/pickup-sheet", response_class=HTMLResponse)
def pickup_sheet(deal_id: int, request: Request):
    lg, e = _deal_page(request, deal_id), _html.escape
    if lg is None:
        return RedirectResponse("/", status_code=307)
    v, bk = lg["vehicle"], lg["booking"]
    when = f"{bk['date']}, {bk['slot']}" if bk else "Not booked yet"
    rows = ""
    for c in lg["collect"]:
        items = "".join(f"<li><code>{e(i['resource_id'])}</code> {e(i['what'])}: {e(_qty(i, lg))}</li>" for i in c["items"])
        rows += (f"<tr><td><b>{c['stop']}</b></td><td><b>{e(c['seller_name'])}</b><br>Contact: ______________<br>Phone: ______________"
                 f"</td><td><ul style=\"margin:0;padding-left:18px\">{items}</ul>Total {c['kg']:g} kg</td><td>Seller sign:<br><br>______</td></tr>")
    warn = "".join(f'<p class="warn">{e(w)}</p>' for w in lg["warnings"])
    body = (f'<div class="card"><h1>Pickup sheet: deal #{lg["deal"]["id"]}</h1><p><span class="chip">simulated</span> {e(SIMULATED)}</p>'
            f'<dl><dt>Pickup slot</dt><dd>{e(when)}</dd><dt>Vehicle</dt><dd>{e(str(v["name"]))} x {v["trips"]} {"trip" if v["trips"] == 1 else "trips"}, payload {v["payload_kg"]:g} kg, load {v["load_kg"]:g} kg</dd>'
            f'<dt>Route</dt><dd>{lg["route_km"]:g} km round trip, about {lg["tour_hours"] * 60:.0f} min at {lg["speed_kmph"]:g} km/h (sample)</dd>'
            f'<dt>Vehicle no.</dt><dd>______________</dd><dt>Driver</dt><dd>______________ Phone ______________</dd></dl>{warn}</div>'
            f'<div class="card"><h2 style="margin:0 0 8px">Collect, in this order</h2><table><tr><th>Stop</th><th>Seller</th><th>What to collect</th><th></th></tr>{rows}'
            f'<tr><td><b>{len(lg["collect"]) + 1}</b></td><td><b>Deliver to {e(lg["buyer"]["name"])}</b><br>Contact: ______________</td>'
            f'<td>All of the above, {v["load_kg"]:g} kg</td><td>Buyer sign:<br><br>______</td></tr></table>'
            f'<p class="note">Positions are on a simple km grid from sample data; follow the real addresses. Contacts are left blank to fill in.'
            f'{" Some stops use an approximate location from the business&#39;s area." if lg["approx_location"] else ""}</p></div>')
    return _doc(f"Pickup sheet {deal_id}", body)


@app.get("/deals/{deal_id}/challan", response_class=HTMLResponse)
def delivery_challan(deal_id: int, request: Request):
    lg, e = _deal_page(request, deal_id), _html.escape
    if lg is None:
        return RedirectResponse("/", status_code=307)
    bk, v = lg["booking"], lg["vehicle"]
    lines = "".join(f"<tr><td>{n}</td><td>{e(c['seller_name'])}</td><td><code>{e(i['resource_id'])}</code> {e(i['what'])}</td><td>{e(_qty(i, lg))}</td></tr>"
                    for n, (c, i) in enumerate(((c, i) for c in lg["collect"] for i in c["items"]), start=1))
    body = (f'<div class="card"><h1>Delivery challan (simulated)</h1><p><span class="chip">simulated</span> {e(SIMULATED)} Not a tax invoice.</p>'
            f'<dl><dt>Challan no.</dt><dd>SIM-{lg["deal"]["id"]:05d}</dd><dt>Deal</dt><dd>#{lg["deal"]["id"]}</dd>'
            f'<dt>Date</dt><dd>{e(bk["date"]) if bk else "______________"}</dd>'
            f'<dt>From</dt><dd>{e(", ".join(c["seller_name"] for c in lg["collect"]))}</dd><dt>To</dt><dd>{e(lg["buyer"]["name"])}</dd>'
            f'<dt>Vehicle</dt><dd>{e(str(v["name"]))}, no. ______________</dd></dl></div>'
            f'<div class="card"><table><tr><th>#</th><th>From</th><th>Item</th><th>Quantity</th></tr>{lines}'
            f'<tr><td></td><td></td><td><b>Total weight</b></td><td><b>{v["load_kg"]:g} kg</b></td></tr></table>'
            f'<div class="sig"><div>Delivered by (driver)</div><div>Received in good condition by (buyer)</div></div>'
            f'<p class="note">Attach this signed challan as delivery evidence. The deal is marked delivered only with evidence and the buyer\'s confirmation.</p></div>')
    return _doc(f"Challan {deal_id}", body)


# ---------------- Connectors: CSV upload, auto-sync folder, and planned ones ----------------
ERP_DROP = ROOT.parent / "erp_drop"
SYNC_EVERY_SECONDS = 60
SYNC_MIN_AGE_SECONDS = 1.0  # a file younger than this may still be being written; it is read on the next check
WORKS_WITH = "Works with any ERP or billing software that can export a stock file."
_sync_lock = threading.Lock()


class FolderConfig(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    enabled: bool = False
    measured: bool = False
    days_unused_limit: int = Field(30, ge=0, le=36500)
    hours_left_limit: float = Field(24, ge=0, le=24 * 365)


def _default_folder(lane: str) -> Path:
    return ERP_DROP / lane


def _folder_config(lane: str, owner: str) -> dict:
    with closing(db()) as con:
        row = con.execute("SELECT * FROM sync_folders WHERE owner=? AND lane=?", (owner, lane)).fetchone()
    if row is None:
        return {"lane": lane, "path": str(_default_folder(lane)), "enabled": False, "measured": False,
                "days_unused_limit": 30, "hours_left_limit": 24.0, "saved": False}
    return {"lane": lane, "path": row["path"], "enabled": bool(row["enabled"]), "measured": bool(row["measured"]),
            "days_unused_limit": row["days_unused_limit"], "hours_left_limit": row["hours_left_limit"], "saved": True}


def _log(owner, lane, folder, file, status, rows_read=None, surplus=None, rejected=None, listed=None, label=None, message="", details=None):
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as con, con:
        cur = con.execute("INSERT INTO sync_log (owner, at, lane, folder, file, status, rows_read, surplus, rejected, listed, label, message, details)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (owner, at, lane, folder, file, status, rows_read, surplus, rejected, listed,
                                                                   label, message, json.dumps(details or [])))
    return {"id": cur.lastrowid, "at": at, "lane": lane, "folder": folder, "file": file, "status": status, "rows_read": rows_read,
            "surplus": surplus, "rejected": rejected, "listed": listed, "label": label, "message": message, "details": details or []}


def _sync_log(lane: str, owner: str, limit: int = 20) -> list[dict]:
    """One seller's import history in a lane."""
    with closing(db()) as con:
        rows = con.execute("SELECT * FROM sync_log WHERE lane=? AND IFNULL(owner, '')=? ORDER BY id DESC LIMIT ?", (lane, owner, limit)).fetchall()
    return [{**dict(r), "details": json.loads(r["details"] or "[]")} for r in rows]


def _sync_folder(lane: str, owner: str, background: bool = False) -> list[dict]:
    """Import every new or changed .csv in this seller's folder for the lane. Unchanged files are never imported twice.
    Files are only read; nothing in the folder is moved, changed or deleted. What is listed belongs to this seller."""
    cfg = _folder_config(lane, owner)
    folder = Path(cfg["path"])
    if folder == _default_folder(lane):
        folder.mkdir(parents=True, exist_ok=True)  # only ever creates the project's own default folder
    conn = CN.FolderSync(folder, SYNC_MIN_AGE_SECONDS)
    label = "measured" if cfg["measured"] else "sample"
    with _sync_lock:
        try:
            exports = conn.stock_csv(lane)
        except (ValueError, OSError) as e:
            last = _sync_log(lane, owner, 1)
            if background and last and last[0]["status"] == "error" and last[0]["message"] == str(e):
                return []  # the same folder problem is logged once, not every minute
            return [_log(owner, lane, str(folder), "", "error", message=str(e))]
        with closing(db()) as con:
            seen = {r["file"]: r["fingerprint"] for r in con.execute("SELECT file, fingerprint FROM sync_seen WHERE owner=? AND lane=?", (owner, lane))}
        entries = []
        for ex in exports:
            where = str((folder / ex.name).resolve())
            if seen.get(where) == ex.fingerprint:
                continue  # same bytes as last time
            again = where in seen
            if ex.error:
                entry = {"status": "error", "message": ex.error}
            else:
                try:
                    r = IMP.run_import(lane, ex.text, cfg["days_unused_limit"], cfg["hours_left_limit"], label)
                    entry = {"status": "imported", "rows_read": r["rows_read"], "surplus": len(r["surplus"]),
                             "rejected": len(r["rejected"]), "listed": _list_surplus(lane, r, label, owner=owner), "label": label,
                             "message": "Changed file, imported again." if again else "New file.", "details": r["rejected"]}
                except ValueError as e:
                    entry = {"status": "error", "message": str(e)}
            with closing(db()) as con, con:
                con.execute("INSERT OR REPLACE INTO sync_seen (owner, lane, file, fingerprint, imported_at) VALUES (?,?,?,?,?)",
                            (owner, lane, where, ex.fingerprint, datetime.now(timezone.utc).isoformat(timespec="seconds")))
            entries.append(_log(owner, lane, str(folder), ex.name, **entry))
    return entries


def sync_all_enabled() -> dict:
    """One background pass: every seller's folder whose sync is switched on. {lane: entries from every seller}."""
    with closing(db()) as con:
        on = con.execute("SELECT owner, lane FROM sync_folders WHERE enabled=1 ORDER BY lane, owner").fetchall()
    out: dict[str, list] = {}
    for r in on:
        out.setdefault(r["lane"], []).extend(_sync_folder(r["lane"], r["owner"], background=True))
    return out


async def _autosync_loop():
    for lane in ("durable", "fresh"):
        _default_folder(lane).mkdir(parents=True, exist_ok=True)
    while True:
        try:
            await asyncio.to_thread(sync_all_enabled)
        except Exception as e:  # noqa: BLE001  keep checking even if one pass fails
            print("auto-sync pass failed:", e)
        await asyncio.sleep(SYNC_EVERY_SECONDS)


@app.get("/api/connectors")
def connectors(me=SELLER):
    folders = {}
    for lane in ("durable", "fresh"):
        cfg = _folder_config(lane, me["business_id"])
        last = _sync_log(lane, me["business_id"], 1)
        folders[lane] = {**cfg, "default_path": str(_default_folder(lane)), "last": last[0] if last else None}
    return {"connectors": [c.info() for c in CN.catalogue()], "works_with": WORKS_WITH, "every_seconds": SYNC_EVERY_SECONDS,
            "folder": folders}


@app.put("/api/connectors/folder/{lane}")
def save_folder(lane: Literal["durable", "fresh"], cfg: FolderConfig, me=SELLER):
    folder = Path(cfg.path.strip().strip('"')).expanduser()
    if not folder.is_absolute():
        folder = ROOT.parent / folder
    if folder == _default_folder(lane):
        folder.mkdir(parents=True, exist_ok=True)
    problem = CN.FolderSync(folder).check()
    if problem:
        _bad(ValueError(problem))
    with closing(db()) as con, con:
        con.execute("INSERT OR REPLACE INTO sync_folders (owner, lane, path, enabled, measured, days_unused_limit, hours_left_limit)"
                    " VALUES (?,?,?,?,?,?,?)", (me["business_id"], lane, str(folder), int(cfg.enabled), int(cfg.measured),
                                                cfg.days_unused_limit, cfg.hours_left_limit))
    return _folder_config(lane, me["business_id"])


@app.post("/api/connectors/folder/{lane}/sync")
def sync_now(lane: Literal["durable", "fresh"], me=SELLER):
    """Check this seller's folder at once (the same check the background task runs every 60 seconds)."""
    return {"entries": _sync_folder(lane, me["business_id"]), "log": _sync_log(lane, me["business_id"])}


@app.get("/api/connectors/folder/{lane}/log")
def sync_log(lane: Literal["durable", "fresh"], limit: int = 20, me=SELLER):
    return {"log": _sync_log(lane, me["business_id"], max(1, min(limit, 200)))}


@app.post("/api/connectors/{key}/sync")
def sync_connector(key: str, lane: Literal["durable", "fresh"] = "durable", me=SELLER):
    c = next((x for x in CN.catalogue() if x.key == key), None)
    if c is None:
        raise HTTPException(404, f"unknown connector {key}")
    if key == "folder_sync":
        return sync_now(lane, me)
    if key == "csv_upload":
        raise HTTPException(400, "CSV upload has no sync: upload a file with the form instead.")
    raise HTTPException(501, f"{c.name} is planned and not built yet. Use CSV upload or Auto-sync folder.")


# ---------------- Manual entry: one item typed by hand, same checks as the CSV importer ----------------
class ManualItem(BaseModel):
    fields: dict[str, str | float | int | bool | None]
    use_by: datetime | None = Field(None, description="Fresh: a use-by date and time instead of hours left")
    days_unused_limit: int = Field(30, ge=0, le=36500)
    hours_left_limit: float = Field(24, ge=0, le=24 * 365)
    measured: bool = False
    list_anyway: bool = False
    photo: str | None = Field(None, max_length=8_000_000, description="data:image/...;base64,... (optional)")


def _manual_id() -> str:
    return "M-" + datetime.now().strftime("%y%m%d-%H%M%S") + "-" + os.urandom(2).hex().upper()


def _decode_photo(url: str) -> tuple[str, bytes] | str:
    import base64
    m = re.match(r"^data:([\w/+.-]+);base64,(.*)$", url, re.S)
    if not m:
        return "The photo could not be read. Take or choose it again."
    try:
        data = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        return "The photo could not be read. Take or choose it again."
    problem = _photo_problem(m.group(1).lower(), data)
    return problem[1] if problem else (m.group(1).lower(), data)


@app.get("/api/manual/options")
def manual_options(me=SELLER):
    from typing import get_args
    from engine import materials as MAT
    from engine.models import Condition, FoodCategory, Storage
    mats = [{"key": m.key, "name": m.name, "bar": m.key in MAT.BAR_MATERIALS} for m in MAT.MATERIALS.values()]
    if me["account"].get("seller_type") == "construction_site":  # site stock first
        mats.sort(key=lambda m: m["key"] not in ("tmt_rebar", "shuttering_plywood"))
    return {"materials": mats, "conditions": [c for c in get_args(Condition) if c not in MAT.USE_CONDITIONS[1:]],
            "categories": list(get_args(FoodCategory)), "storage": list(get_args(Storage)), "source": IMP.MANUAL_SOURCE,
            "rebar": {"grades": list(MAT.REBAR_GRADES), "diameters_mm": list(MAT.REBAR_DIAMETERS_MM), "weight_note": MAT.REBAR_WEIGHT_NOTE,
                      "kg_per_m": {str(d): round(MAT.rebar_kg_per_m(d), 3) for d in MAT.REBAR_DIAMETERS_MM}},
            "shuttering": {"thicknesses_mm": list(MAT.SHUTTERING_THICKNESSES_MM), "conditions": list(MAT.USE_CONDITIONS)}}


@app.post("/api/manual/{lane}")
def manual_entry(lane: Literal["durable", "fresh"], m: ManualItem, now: datetime | None = None, me=SELLER):
    """Check one hand-typed item with the importer, apply the surplus rules, and list it if it is surplus
    (or if the seller asks to list it anyway). An invalid item is never listed. `now` is only for tests."""
    fields = {k: v for k, v in m.fields.items() if k in IMP.COLUMNS[lane] + IMP.OPTIONAL[lane]}
    fields["item_id"] = str(fields.get("item_id") or "").strip() or _manual_id()
    fields["seller_id"] = str(fields.get("seller_id") or "").strip() or (me["name"] if not me["admin"] else "My workshop" if lane == "durable" else "My kitchen")
    errors = {}
    if lane == "fresh" and m.use_by is not None:
        hours = (m.use_by.replace(tzinfo=None) - (now or datetime.now()).replace(tzinfo=None)).total_seconds() / 3600
        if hours <= 0:
            errors["use_by"] = "The use-by time has already passed."
        fields["hours_left"] = round(max(hours, 0), 1)
    label = "measured" if m.measured else "sample"
    r = IMP.check_one(lane, fields, m.days_unused_limit, m.hours_left_limit, label)
    errors.update(r.get("errors", {}))
    photo = None
    if m.photo:
        photo = _decode_photo(m.photo)
        if isinstance(photo, str):
            errors["photo"] = photo
    if errors:
        raise HTTPException(422, {"message": "Some fields need fixing. Nothing was listed.", "errors": errors})
    listed = r["surplus"] or m.list_anyway
    rules = r["rules"] if r["surplus"] else ["Listed by the seller"]
    out = {"ok": True, "item_id": r["item"]["id"], "item": r["item"], "surplus": r["surplus"], "rules": r["rules"],
           "kept_reason": r["kept_reason"], "urgent": r["urgent"], "label": label, "listed": listed, "source": IMP.MANUAL_SOURCE}
    if not listed:
        return {**out, "message": f"Not surplus: {r['kept_reason']}. It stays as working stock unless you list it anyway."}
    key = _import_key(lane, me["business_id"])
    _list_surplus(lane, {"surplus": [{"item": r["item"], "rules": rules}]}, label, IMP.MANUAL_SOURCE, me["business_id"])
    out.update(listed_rules=rules, stock_key=key, passport=f"/passport/{key}/{r['item']['id']}",
               photo=_write_photo(key, r["item"]["id"], *photo) if photo else None)
    return out


# ---------------- Each seller's own stock in matching, and "What can this become?" ----------------
from engine import become as BEC
from engine.gapfill import GapFillPrices, StandingOrder
from engine.models import FlatPiece, LinearStock, PerishableLot

OWN_ID, OWN_NAME = "YOU", "Your workshop"  # the admin's own test items (and rows saved before accounts)
# Approximate positions of Chennai areas in km east and north of the city centre (Chennai Central), from rounded map
# coordinates. Used only for businesses that sign up themselves; any other area is placed at the city centre.
AREA_KM = {"Ambattur": (-12.6, 3.5), "Anna Nagar": (-6.6, 0.3), "Egmore": (-1.1, -1.1), "Saidapet": (-5.2, -6.8),
           "Guindy": (-5.4, -8.4), "Porur": (-12.4, -4.9), "T Nagar": (-4.0, -4.5), "Velachery": (-5.7, -11.2),
           "Perungudi": (-2.7, -13.0), "Tambaram": (-18.5, -17.4)}
CITY_CENTRE = (0.0, 0.0)
APPROX_NOTE = "approximate location"


def _area_xy(area: str | None) -> tuple[float, float]:
    """A business's area as approximate km coordinates; an area not in the list is the city centre."""
    flat = re.sub(r"[^a-z]", "", (area or "").lower())
    for name, xy in AREA_KM.items():
        if re.sub(r"[^a-z]", "", name.lower()) in flat:
            return xy
    return CITY_CENTRE


@functools.lru_cache(maxsize=1)
def _sample_salvage() -> dict:
    """One sample scrap or disposal price per material across every cluster. Rebar takes the mild steel scrap price and
    shuttering plywood the plywood price (same scrap stream); both stay labelled Sample."""
    out = {}
    for key in SC.SCENARIOS:
        for x in SC.dataset(key)["sellers"]:
            for o in x.salvage:
                out.setdefault(o.applies_to, o)
    for new, like in (("tmt_rebar", "mild_steel"), ("shuttering_plywood", "plywood")):
        if like in out:
            out.setdefault(new, out[like].model_copy(update={"applies_to": new}))
    return out


def _complete_salvage(seller: Seller, first: list) -> Seller:
    """The seller's own prices first (then the cluster's), then a sample price for any material it has none for."""
    have = {o.applies_to: o for o in first}
    for k, o in _sample_salvage().items():
        have.setdefault(k, o)
    return seller.model_copy(update={"salvage": list(have.values())})


def _own_seller(d: dict) -> Seller:
    """The admin's own items: a seller placed at the centre of the cluster's sellers, with the cluster's sample scrap prices."""
    xs, ys = [x.x_km for x in d["sellers"]], [x.y_km for x in d["sellers"]]
    salvage = {}
    for x in d["sellers"]:
        for o in x.salvage:
            salvage.setdefault(o.applies_to, o)
    s = Seller(id=OWN_ID, name=OWN_NAME, x_km=round(sum(xs) / len(xs), 2), y_km=round(sum(ys) / len(ys), 2),
               salvage=list(salvage.values()), fssai_licensed=False, label="sample")
    return _complete_salvage(s, s.salvage)


def _accounts_by_bid() -> dict:
    with closing(db()) as con:
        return {r["business_id"]: r for r in con.execute("SELECT * FROM users")}


def _owner_names() -> dict[str, str]:
    """business id -> business name, for sellers. The admin's own items keep the seller name typed with them."""
    return {b: u["business_name"] for b, u in _accounts_by_bid().items() if u["role"] == "seller"}


def _is_new_business(u) -> bool:
    """A business that signed up itself (not a demo business, not the admin): its position comes from its area."""
    return u is not None and u["role"] != "admin" and u["business_id"] not in _demo_accounts()


def _owner_seller(owner: str, key: str, d: dict, accounts: dict) -> tuple[Seller, bool]:
    """The seller an owner's items are matched as in one cluster, and whether its position is approximate.
    A demo business that is already a seller in this sample cluster keeps its own id and place there. Any other business is
    its own seller, named after the business, at its area's approximate position, with the cluster's sample scrap prices.
    The admin's own items (and rows saved before accounts) are "Your workshop" at the cluster centre."""
    u = accounts.get(owner)
    if u is None or u["role"] == "admin":
        return _own_seller(d), False
    same = next((x for x in d["sellers"] if f"{key}:{x.id}" == owner or x.name == u["business_name"]), None)
    if same is not None:
        return _complete_salvage(same, same.salvage), False
    x, y = _area_xy(u["area"])
    return _own_seller(d).model_copy(update={"id": owner, "name": u["business_name"], "x_km": x, "y_km": y}), True


def _own_items(lane: str, owner: str | None = None) -> list[tuple[str, object]]:
    """(owner, item) for items listed in a lane: one owner's ("" = rows saved before accounts), or every owner's (None)."""
    out = []
    for r in _import_rows(lane, owner):
        if owner is None and not r["owner"]:
            continue  # saved before accounts: no seller to match it as
        data = {**json.loads(r["item"]), "label": r["label"] if r["label"] in ("measured", "sample") else "sample"}
        data.pop("days_unused", None)
        model = PerishableLot if lane == "fresh" else LinearStock if data.get("kind") == "linear" else FlatPiece
        out.append((r["owner"], model(**data)))
    return out


def _owned(key: str) -> dict:
    """Every owner's listed items for one cluster, ready for the engine: new sellers, resources (ids "<seller id>-<item id>"),
    where each resource came from, and which sellers have only an approximate position."""
    eng = SC.SCENARIOS[key]["engine"]
    lane = "fresh" if eng == "perishable" else "durable"
    d, accounts = SC.dataset(key), _accounts_by_bid()
    sample = {x.id for x in d["sellers"]}
    out = {"sellers": [], "resources": [], "origin": {}, "approx": []}
    for owner, item in _own_items(lane):
        if item.kind != eng:
            continue
        seller, approx = _owner_seller(owner, key, d, accounts)
        if seller.id not in sample and all(s.id != seller.id for s in out["sellers"]):
            out["sellers"].append(seller)
            if approx:
                out["approx"].append(seller.id)
        rid = f"{seller.id}-{item.id}"
        out["resources"].append(item.model_copy(update={"id": rid, "seller_id": seller.id}))
        out["origin"][rid] = (owner, item.id)
    return out


def _dataset_with_own(key: str) -> dict:
    """The sample cluster plus every buyer's posted requirements and every seller's listed items."""
    d = SC.dataset(key)
    d["demands"] = d["demands"] + [r["demand_obj"] for r in _requirements(scenario=key)]
    own = _owned(key)
    d["sellers"] = d["sellers"] + own["sellers"]
    d["resources"] = d["resources"] + own["resources"]
    return d


@app.get("/api/own-stock/{key}")
def own_stock(key: str, me=BUYER):
    """Items sellers listed themselves, to add to a cluster before matching: each owner is its own seller, named after the
    business (a demo business already in the cluster keeps its place there). Empty if there are none."""
    if key not in SC.SCENARIOS:
        raise HTTPException(404, f"unknown scenario {key}")
    own = _owned(key)
    return {"sellers": [s.model_dump(mode="json") for s in own["sellers"]],
            "resources": [x.model_dump(mode="json") for x in own["resources"]], "approximate": own["approx"],
            "note": "Items sellers listed themselves. A business that signed up itself is placed at an approximate location "
                    "from its area. Scrap prices are the cluster's sample prices."}


def _my_sample_sellers(me: dict, key: str) -> set[str]:
    """Sample seller ids in a cluster that are this business (its demo id, or the same business name)."""
    if key not in SC.SCENARIOS:
        return set()
    return {x.id for x in SC.dataset(key)["sellers"] if f"{key}:{x.id}" == me["business_id"] or x.name == me["name"]}


def _my_demands(me: dict, key: str) -> list:
    """A buyer's own open orders in a cluster: its sample order (demo businesses) and the requirements it posted.
    The admin: every sample order."""
    d = SC.dataset(key)
    if me["admin"]:
        return list(d["demands"])
    sample = [x for x in d["demands"] if f"{key}:{x.buyer_id}" == me["business_id"]]
    posted = [r["demand_obj"] for r in _requirements(scenario=key, owner=me["business_id"]) if r["status"] in ("open", "reserved")]
    return sample + posted


def _buyer_origin(me: dict, key: str, demands: list) -> dict:
    """Where distances are measured from: the buyer's own order; else a new business's area (approximate);
    else the cluster's first order, named in distance_from."""
    a = me["account"]
    if a.get("approx_location") and not demands:
        return {"x_km": a["x_km"], "y_km": a["y_km"], "name": me["name"], "approx": True}
    x = demands[0] if demands else SC.dataset(key)["demands"][0]
    return {"x_km": x.x_km, "y_km": x.y_km, "name": x.buyer_name, "approx": bool(a.get("approx_location"))}


def _approx_demands(key: str) -> set[str]:
    """Posted requirements whose buyer signed up itself, so their position is approximate."""
    accounts = _accounts_by_bid()
    return {r["demand_id"] for r in _requirements(scenario=key, include_closed=True) if _is_new_business(accounts.get(r["owner"]))}


def _own_order(p, owner: str, me: dict) -> dict:
    """For a listed item on Browse surplus: the first of this buyer's open orders it fits, matched as its owner's seller,
    or the engine's reason it fits none. Also the distance to the buyer."""
    eng, why, first = REGISTRY[p.kind], None, None
    accounts = _accounts_by_bid()
    for key, sc in SC.SCENARIOS.items():
        if sc["engine"] != p.kind:
            continue
        d = SC.dataset(key)
        seller, approx = _owner_seller(owner, key, d, accounts)
        demands = _my_demands(me, key)
        here = _buyer_origin(me, key, demands)
        place = {"match_seller": seller.id, "approx_location": approx or here["approx"],
                 "distance_km": round(_dist((seller.x_km, seller.y_km), (here["x_km"], here["y_km"])), 1)}
        first = first or place
        for i, dm in enumerate(demands):
            if dm.kind != p.kind or not eng.relevant(p, dm):
                continue
            reason = eng.check(p, dm, seller, d["settings"])
            if reason is None:
                return {**place, "order": {"scenario": key, "index": i, "demand_id": dm.id, "buyer_name": dm.buyer_name}, "order_reason": None}
            why = why or f"{dm.buyer_name}: {reason}"
    return {**(first or {}), "order": None, "order_reason": why or NO_ORDER_YET}


def _gap_standing() -> tuple[list, GapFillPrices]:
    g = _gapfill_data()
    return [StandingOrder(**o) for o in g["orders"]], GapFillPrices(**g["prices"])


def _reference_price(piece, demands: list, standing: list) -> tuple[float | None, str]:
    """The cluster's lowest new price for this material (the cautious choice), from orders, else standing orders."""
    same = [x for x in demands if x.kind == piece.kind and x.material == piece.material]
    if same:
        low = min(same, key=lambda x: x.new_price_per_kg)
        return low.new_price_per_kg, low.label
    st = [x for x in standing if x.material == piece.material]
    return (min(x.new_price_per_kg for x in st), "sample") if st else (None, "sample")


def _become(key: str, rid: str) -> dict:
    imp = _parse_import(key)
    if imp:
        lane, owner = imp
        res = next((x for _, x in _own_items(lane, owner) if x.id == rid), None)
        if res is None:
            raise HTTPException(404, "no such item")
        scen = [k for k, v in SC.SCENARIOS.items() if v["engine"] == res.kind]
        own, accounts = True, _accounts_by_bid()
    elif key in SC.SCENARIOS:
        res = next((x for x in SC.dataset(key)["resources"] if x.id == rid), None)
        if res is None:
            raise HTTPException(404, "no such item")
        scen, own = [key], False
    else:
        raise HTTPException(404, f"unknown scenario {key}")
    standing, gp = _gap_standing()
    merged = None
    all_demands = []
    approx = False
    for k in scen:
        d = SC.dataset(k)
        all_demands += d["demands"]
        if own:
            seller, far = _owner_seller(owner, k, d, accounts)
            approx = approx or far
        else:
            seller = next(x for x in d["sellers"] if x.id == res.seller_id)
        cands = [BEC.Candidate(x, "order", BEC.order_label(x.buyer_name)) for x in d["demands"]]
        r = BEC.become(res, seller, cands, d["settings"], d["transport"])
        if merged is None:
            merged, first = r, (seller, d)
        else:
            merged["options"] += r["options"]
            merged["refused"] += r["refused"]
        if merged["scrap"] is None and r["scrap"] is not None:  # use the cluster that has a scrap price for this material
            merged["scrap"], first = r["scrap"], (seller, d)
    if res.kind == "flat":  # standing orders and common parts, with the first cluster's seller, settings and transport
        seller, d = first
        cands = [BEC.Candidate(BEC.standing_order_demand(o, gp.handling_per_part), "standing",
                               BEC.order_label(o.buyer_name, not o.id.startswith("R")), pickup=gp.pickup) for o in standing]
        price, label = _reference_price(res, all_demands, standing)
        cands += BEC.reference_candidates(res, price, label)
        extra = BEC.become(res, seller, cands, d["settings"], d["transport"])
        merged["options"] += extra["options"]
        merged["refused"] += extra["refused"]
        merged["reference_price_per_kg"] = price
    merged["options"].sort(key=lambda o: (o["source"] == "reference", o["value_above_scrap"] is None, -(o["value_above_scrap"] or 0)))
    note = "Prices are sample data. Open orders and standing orders are sample orders; common parts have no buyer yet."
    if approx:
        note += " Transport is worked out from an approximate location (the seller's area)."
    merged.update(key=key, seller_name=first[0].name, own=own, approx_location=approx, note=note)
    if res.kind == "flat":
        merged["piece"] = {"length_mm": res.length_mm, "width_mm": res.width_mm, "thickness_mm": res.thickness_mm,
                           "defects": [x.model_dump() for x in res.defects]}
    elif res.kind == "linear":
        merged["piece"] = {"length_mm": res.length_mm}
    return merged


@app.get("/api/become/{key}/{rid}")
def what_it_can_become(key: str, rid: str, me=ANY):
    """Every order, standing order or common part this one item could become, ranked by value above scrap."""
    return _become(key, rid)


def _plan_svg(b: dict, o: dict) -> str:
    det = o["detail"]
    if det.get("shape") == "sheet":
        L, W = det["length_mm"], det["width_mm"]
        g = f'<rect x="0" y="0" width="{L}" height="{W}" style="fill:#dfe5ea;stroke:#5d6b77;stroke-width:{max(L, W) / 120}"/>'
        g += "".join(f'<rect x="{x["x_mm"]}" y="{x["y_mm"]}" width="{x["length_mm"]}" height="{x["width_mm"]}" style="fill:#a1352a;opacity:.5"/>' for x in det.get("defects", []))
        g += "".join(f'<rect x="{x["x"]}" y="{x["y"]}" width="{x["length"]}" height="{x["width"]}" style="fill:#f3c14b;stroke:#b98400;stroke-width:{max(L, W) / 300}"/>' for x in det["parts"])
        return f'<svg class="plan" viewBox="0 0 {L} {W}" role="img" aria-label="{len(det["parts"])} parts placed on the piece">{g}</svg>'
    if det.get("shape") == "bar":
        L = det["length_mm"]
        g = f'<rect x="0" y="40" width="{L}" height="60" style="fill:#dfe5ea;stroke:#5d6b77;stroke-width:{L / 200}"/>'
        g += "".join(f'<rect x="{c["start_mm"]}" y="40" width="{c["length_mm"]}" height="60" style="fill:#f3c14b;stroke:#b98400;stroke-width:{L / 400}"/>' for c in det["cuts"])
        return f'<svg class="plan" viewBox="0 0 {L} 140" role="img" aria-label="{len(det["cuts"])} cuts along the bar">{g}</svg>'
    return ""


def _become_card(key: str, rid: str) -> str:
    e = _html.escape
    try:
        b = _become(key, rid)
    except HTTPException:
        return ""
    unit = {"flat": "", "linear": "", "perishable": "kg"}[b["kind"]]
    rows = ""
    def card(o):
        val = o["value_above_scrap"]
        money = ("No new price for this material, so only the count is shown." if val is None else
                 f'Donation: avoids Rs {-o["scrap_value"]:,.0f} disposal; pickup Rs {o["transport"]:,.0f}.' if o.get("is_donation") else
                 f'<b class="v {"neg" if val < 0 else ""}">Rs {val:,.0f}</b> {"above" if val >= 0 else "below"} {"disposal" if b["kind"] == "perishable" else "scrap"}')
        what = (f'{o["units"]:g} kg to {e(o["buyer_name"])}{" (donation)" if o.get("is_donation") else ""}, {o["hours_on_arrival"]:g} h left on arrival'
                if b["kind"] == "perishable" else f'{o["units"]:g} x {e(o["part_name"])}' + (f' {o["part_mm"][0]:g} x {o["part_mm"][1]:g} mm' if o.get("part_mm") else f' {o.get("cut_length_mm", 0):g} mm'))
        return (f'<div class="opt">{_plan_svg(b, o) or "<div></div>"}<div><span class="tag {"o" if o["source"] != "reference" else ""}">{e(o["label"])}</span>'
                f'<p style="margin:6px 0"><b>{what}</b></p><p style="margin:0">{money}</p></div></div>')
    rows = "".join(card(o) for o in b["options"][:5])
    if len(b["options"]) > 5:
        rows += f'<details><summary>More options ({len(b["options"]) - 5})</summary>{"".join(card(o) for o in b["options"][5:])}</details>'
    if not rows:
        rows = '<p class="note">Nothing in this lane can use it right now. See the reasons below.</p>'
    refused = "".join(f'<li><b>{e(r["label"])}: {e(r["part_name"])}</b> {e(r["reason"])}</li>' for r in b["refused"])
    scrap = b["scrap"]
    base = (f'Compared with {"disposal" if b["kind"] == "perishable" else "scrap"}: Rs {abs(scrap["value"]):,.0f} '
            f'at Rs {abs(scrap["price_per_kg"]):g}/kg ({e(scrap["label"])}, {"quoted " + e(scrap["quoted_on"]) if scrap["quoted_on"] else "no date"}).') if scrap else ""
    return (f'<div class="card"><h2 style="margin:0">What can this become?</h2><p class="note">{e(base)} {e(b["note"])}</p>{rows}'
            + (f'<details><summary>Not possible ({len(b["refused"])})</summary><ul>{refused}</ul></details>' if refused else "")
            + '</div>')


# ---------------- Buyers: post a requirement ----------------
from engine.models import FlatDemand, LinearDemand, PerishableDemand

POSTED_NOTE = "Posted by a buyer in this demo"
BUYER_TYPES = {"business": "food_business", "hostel": "food_business", "food_bank": "ngo"}


class RequirementIn(BaseModel):
    lane: Literal["durable", "fresh"]
    kind: Literal["flat", "linear", "perishable"]
    fields: dict
    needed_by: date | None = None
    standing: bool = False


def _scenario_for(kind: str, material: str | None) -> str:
    if kind == "perishable":
        return "food"
    if kind == "linear":
        return "bars"
    return "steel" if material == "mild_steel" else "sheets"


def _centre(key: str) -> tuple[float, float]:
    sel = SC.dataset(key)["sellers"]
    return round(sum(x.x_km for x in sel) / len(sel), 1), round(sum(x.y_km for x in sel) / len(sel), 1)


def _sample_price(kind: str, material: str | None, buyer_type: str | None = None) -> float | None:
    """The cluster's lowest new price for this material (a cautious default the buyer can change). Sample data."""
    if kind == "perishable":
        prices = [x.normal_price_per_kg for x in SC.dataset("food")["demands"] if x.buyer_type == buyer_type]
        return min(prices) if prices else None
    key = _scenario_for(kind, material)
    prices = [x.new_price_per_kg for x in SC.dataset(key)["demands"] if x.kind == kind and x.material == material]
    if not prices and kind == "flat":
        prices = [o["new_price_per_kg"] for o in SC.gapfill_demo()["orders"] if o["material"] == material]
    if not prices and kind == "linear" and material == "tmt_rebar":  # no rebar order in the sample: the cluster's steel bar price
        prices = [x.new_price_per_kg for x in SC.dataset(key)["demands"] if x.kind == "linear" and x.material == "mild_steel"]
    return min(prices) if prices else None


def _build_demand(r: RequirementIn, req_id: int = 0):
    """Check a requirement with the existing demand model. Returns (demand, scenario, errors)."""
    f = {k: v for k, v in r.fields.items() if v not in (None, "")}
    errors = {}
    if r.kind == "perishable" and r.lane != "fresh" or r.kind != "perishable" and r.lane != "durable":
        errors["kind"] = "This kind of requirement belongs to the other lane."
    material = str(f.get("material", "mild_steel" if r.kind == "linear" else "")).strip().lower().replace(" ", "_")
    if r.kind != "perishable":
        try:
            M_name(material)
        except ValueError as e:
            errors["material"] = str(e)[0].upper() + str(e)[1:] + "."
            material = "mild_steel"
        f["material"] = material
    key = _scenario_for(r.kind, material)
    cx, cy = _centre(key)
    f.setdefault("x_km", cx)
    f.setdefault("y_km", cy)
    name = str(f.get("buyer_name", "")).strip()
    f.update(id=f"R{req_id}", buyer_id=f"BUYER-{name.lower().replace(' ', '-')}" if name else "", label="sample")
    if r.kind == "perishable":
        bt = f.pop("buyer_type", "")
        if bt not in BUYER_TYPES:
            errors["buyer_type"] = "Choose business, hostel mess or food bank."
        f["buyer_type"] = BUYER_TYPES.get(bt, "food_business")
        f.setdefault("part_name", "kg of food")
        if f["buyer_type"] == "ngo":
            f["normal_price_per_kg"] = 0.0  # a donation: no price is charged
        model = PerishableDemand
    else:
        model = FlatDemand if r.kind == "flat" else LinearDemand
    if r.standing and r.kind != "flat":
        errors["standing"] = "Standing orders are for sheet parts only."
    if r.needed_by is not None and r.needed_by < datetime.now().date():
        errors["needed_by"] = "Pick today or a later date."
    try:
        demand = model(**f)
    except ValidationError as e:
        return None, key, {**IMP._field_errors(e), **errors}
    if not name:
        errors["buyer_name"] = "This is needed."
    return (None if errors else demand), key, errors


def _requirements(lane: str | None = None, scenario: str | None = None, include_closed: bool = False,
                  owner: str | None = None) -> list[dict]:
    """Posted requirements, newest first. owner: only that buyer's (None: every buyer's, as matching and Gap Fill use them)."""
    q, args = "SELECT * FROM requirements WHERE 1=1", []
    if owner is not None:
        q, args = q + " AND IFNULL(owner, '')=?", args + [owner]
    if lane:
        q, args = q + " AND lane=?", args + [lane]
    if scenario:
        q, args = q + " AND scenario=?", args + [scenario]
    if not include_closed:
        q += " AND status != 'closed'"
    with closing(db()) as con:
        rows = con.execute(q + " ORDER BY id DESC", args).fetchall()
        res = {r["req_id"]: dict(r) for r in con.execute("SELECT * FROM reservations")}
    model = {"flat": FlatDemand, "linear": LinearDemand, "perishable": PerishableDemand}
    out = []
    for r in rows:
        dem = model[r["kind"]].model_validate_json(r["demand"])
        rv = res.get(r["id"]) if r["status"] == "open" else None
        status = "reserved" if rv else r["status"]
        text = {"open": "Open", "matched": f"Matched (deal #{r['deal_id']})", "closed": "Closed",
                "reserved": f"Reserved in a cutting plan by {rv['seller']}, {rv['parts']} parts" if rv else ""}[status]
        out.append({"id": r["id"], "demand_id": dem.id, "lane": r["lane"], "kind": r["kind"], "scenario": r["scenario"],
                    "created_at": r["created_at"], "needed_by": r["needed_by"], "standing": bool(r["standing"]), "status": status,
                    "status_text": text, "deal_id": r["deal_id"], "reserved": rv, "history": json.loads(r["history"]),
                    "demand": dem.model_dump(mode="json"), "demand_obj": dem, "note": POSTED_NOTE, "owner": r["owner"] or ""})
    return out


def _public(r: dict) -> dict:
    return {k: v for k, v in r.items() if k != "demand_obj"}


def _posted_standing() -> list[dict]:
    """Open standing requirements as Gap Fill standing orders (sheet parts only)."""
    out = []
    for r in _requirements(lane="durable"):
        if not r["standing"] or r["status"] not in ("open", "reserved"):
            continue
        d = r["demand_obj"]
        out.append(dict(id=d.id, buyer_name=d.buyer_name, part_name=d.part_name, length_mm=d.length_mm, width_mm=d.width_mm,
                        thickness_mm=d.thickness_mm, material=d.material, accepted_grades=d.accepted_grades, quantity=d.quantity,
                        new_price_per_kg=d.new_price_per_kg, buyer_utilisation=d.buyer_utilisation, label="sample",
                        posted=True, note=POSTED_NOTE))
    return out


def _record_reservations(req: GapFillRequest, plan: dict, seller: str):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    reserved = {n["order_id"]: n["units"] for n in plan.get("neighbours", [])}
    with closing(db()) as con, con:
        for o in req.orders:
            if not (o.id.startswith("R") and o.id[1:].isdigit()):
                continue
            rid = int(o.id[1:])
            if o.id in reserved and o.id not in req.declined_order_ids:
                con.execute("INSERT OR REPLACE INTO reservations (req_id, seller, parts, at) VALUES (?,?,?,?)",
                            (rid, seller, reserved[o.id], now))
            else:
                con.execute("DELETE FROM reservations WHERE req_id=?", (rid,))


@app.get("/api/requirements/options")
def requirement_options(me=BUYER):
    from typing import get_args
    from engine import materials as MAT
    from engine.models import Condition, FoodCategory, Storage
    grades: dict[str, set] = {}
    for key in ("steel", "sheets"):
        d = SC.dataset(key)
        for x in d["resources"]:
            grades.setdefault(x.material, set()).add(x.grade.upper())
        for x in d["demands"]:
            grades.setdefault(x.material, set()).update(x.accepted_grades)
    for o in SC.gapfill_demo()["orders"]:
        grades.setdefault(o["material"], set()).update(o["accepted_grades"])
    bars = SC.dataset("bars")
    profiles = sorted({(x.profile, x.mass_per_m_kg, x.material) for x in [*bars["resources"], *bars["demands"]]})
    return {"materials": [{"key": m.key, "name": m.name, "grades": sorted(grades.get(m.key, [])),
                           "sample_price": _sample_price("flat", m.key)} for m in MAT.MATERIALS.values() if m.key not in MAT.BAR_MATERIALS],
            "conditions": list(get_args(Condition)), "default_conditions": ["new", "good", "surface_rust"],
            "profiles": [{"profile": pr, "mass_per_m_kg": kg, "material": mat, "sample_price": _sample_price("linear", mat)} for pr, kg, mat in profiles]
                        + [{"profile": MAT.rebar_profile(dia), "mass_per_m_kg": round(MAT.rebar_kg_per_m(dia), 3), "material": "tmt_rebar",
                            "sample_price": _sample_price("linear", "tmt_rebar"), "grades": list(MAT.REBAR_GRADES),
                            "weight_note": MAT.REBAR_WEIGHT_NOTE, "weight_label": "assumption"} for dia in MAT.REBAR_DIAMETERS_MM],
            "bar_grades": sorted({g for x in bars["demands"] for g in x.accepted_grades} | {x.grade.upper() for x in bars["resources"]}),
            "categories": list(get_args(FoodCategory)), "storage": list(get_args(Storage)),
            "buyer_types": [{"key": "business", "name": "Food business"}, {"key": "hostel", "name": "Hostel mess"},
                            {"key": "food_bank", "name": "Food bank (donation)"}],
            "food_price": _sample_price("perishable", None, "food_business"),
            "centre": {k: _centre(k) for k in SC.SCENARIOS}, "note": POSTED_NOTE}


@app.post("/api/requirements")
def post_requirement(r: RequirementIn, request: Request, me=BUYER):
    """Check a buyer's requirement with the demand models and save it (as a standing order if asked), owned by this buyer.
    A buyer's requirement always carries its own business name."""
    if not me["admin"]:
        r = r.model_copy(update={"fields": {**r.fields, "buyer_name": me["name"]}})
    demand, key, errors = _build_demand(r)
    if errors:
        raise HTTPException(422, {"message": "Some fields need fixing. Nothing was saved.", "errors": errors})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as con, con:
        cur = con.execute("INSERT INTO requirements (created_at, lane, kind, scenario, demand, needed_by, standing, status, history, owner)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?)", (now, r.lane, r.kind, key, "{}", r.needed_by.isoformat() if r.needed_by else None,
                                                             int(r.standing), "open", json.dumps([{"at": now, "status": "open"}]),
                                                             me["business_id"]))
        rid = cur.lastrowid
        demand = demand.model_copy(update={"id": f"R{rid}"})
        con.execute("UPDATE requirements SET demand=? WHERE id=?", (demand.model_dump_json(), rid))
    _safely(_notify_requirement_fit, demand, key, r.standing, _origin(request))
    return _public(next(x for x in _requirements(include_closed=True) if x["id"] == rid))


def _mine(me: dict) -> str | None:
    """The owner filter for this business: None for the admin (everything)."""
    return None if me["admin"] else me["business_id"]


@app.get("/api/requirements")
def list_requirements(lane: Literal["durable", "fresh"] | None = None, me=BUYER):
    """This buyer's requirements. The admin sees every buyer's."""
    return {"requirements": [_public(r) for r in _requirements(lane=lane, include_closed=True, owner=_mine(me))]}


@app.get("/api/requirements/demands/{key}")
def requirement_demands(key: str, me=BUYER):
    """The buyer's own posted requirements for Order and match (newest first, closed ones left out)."""
    if key not in SC.SCENARIOS:
        raise HTTPException(404, f"unknown scenario {key}")
    return {"demands": [{**r["demand"], "requirement_id": r["id"], "status": r["status"]}
                        for r in _requirements(scenario=key, owner=_mine(me))]}


@app.post("/api/requirements/{req_id}/close")
def close_requirement(req_id: int, me=BUYER):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as con, con:
        row = con.execute("SELECT history, owner FROM requirements WHERE id=?", (req_id,)).fetchone()
        if row is None or not me["admin"] and (row["owner"] or "") != me["business_id"]:
            raise HTTPException(404, "no such requirement")
        con.execute("UPDATE requirements SET status='closed', history=? WHERE id=?",
                    (json.dumps(json.loads(row["history"]) + [{"at": now, "status": "closed"}]), req_id))
        con.execute("DELETE FROM reservations WHERE req_id=?", (req_id,))
    return {"id": req_id, "status": "closed"}


# ---------------- WhatsApp alerts: prepared automatically, sent by a person with one click ----------------
# Nothing is sent by CIRCULATE. A notification is a ready message and a wa.me link; a person opens it and
# presses send in WhatsApp. Statuses only record what that person did: opened, marked as sent, skipped.
ALERT_TYPES = {"new_match": "New matching order", "order_accepted": "Order accepted", "pickup_booked": "Pickup booked",
               "delivered": "Delivered", "fresh": "Fresh food near its use-by", "collection": "Collection request update"}
CONTACT_ROLES = ("seller", "buyer", "food bank")
DEFAULT_ALERTS = {"seller": ["new_match", "pickup_booked", "collection"], "buyer": ["order_accepted", "pickup_booked", "delivered", "fresh"],
                  "food bank": ["order_accepted", "pickup_booked", "delivered", "fresh"]}
STATUS_TEXT = {"ready": "Ready to send", "opened": "Opened in WhatsApp", "sent": "Marked as sent by you", "skipped": "Skipped"}
CONSENT_TEXT = "I agree to receive CIRCULATE alerts on this WhatsApp number. I can turn them off anytime."
FRESH_ALERT_HOURS = 24


def _safely(fn, *args):
    """Alerts must never stop the action that caused them."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        print(f"whatsapp alert not prepared ({fn.__name__}): {e}")


def _norm_biz(name: str | None) -> str:
    return (name or "").split(",")[0].strip().lower()


def _sample_businesses() -> list[dict]:
    out, seen = [], set()
    for key in SC.SCENARIOS:
        d = SC.dataset(key)
        sellers = [(x.name, "seller") for x in d["sellers"]]
        buyers = [(x.buyer_name, "food bank" if getattr(x, "buyer_type", "") == "ngo" else "buyer") for x in d["demands"]]
        for nm, role in sellers + buyers:
            if nm not in seen:
                seen.add(nm)
                out.append({"name": nm, "role": role})
    out.append({"name": OWN_NAME, "role": "seller"})
    return out


def _contact_out(r) -> dict:
    return {"id": r["id"], "business": r["business"], "role": r["role"], "name": r["name"],
            "phone": MSG.mask_phone(r["phone"]), "alerts": json.loads(r["alerts"]), "active": bool(r["active"]),
            "consent_at": r["consent_at"], "created_at": r["created_at"]}


def notify(event: str, text: str, roles: list[str], businesses: list[str] | None = None,
           deal_id: int | None = None, item_id: str | None = None) -> int:
    """Prepare one ready message per matching contact: active, consented, alert type ticked, and (if given) the right business.
    The same event for the same deal or item is prepared only once per contact."""
    wanted = None if businesses is None else {_norm_biz(b) for b in businesses}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = 0
    with closing(db()) as con, con:
        for c in con.execute("SELECT * FROM whatsapp_contacts WHERE active=1 AND consent_at IS NOT NULL").fetchall():
            if c["role"] not in roles or event not in json.loads(c["alerts"]):
                continue
            if wanted is not None and _norm_biz(c["business"]) not in wanted:
                continue
            cur = con.execute("INSERT OR IGNORE INTO notifications (created_at, event, deal_id, item_id, contact_id, text, link, status)"
                              " VALUES (?,?,?,?,?,?,?,?)", (now, event, deal_id, item_id, c["id"], text, MSG.wa_link(text, c["phone"]), "ready"))
            n += cur.rowcount
    return n


def _passport_path(key: str, rid: str) -> str:
    origin = _owned(key)["origin"].get(rid)  # a seller's listed item, matched as "<seller id>-<item id>"
    if origin:
        lane = "fresh" if SC.SCENARIOS[key]["engine"] == "perishable" else "durable"
        return f"/passport/{_import_key(lane, origin[0])}/{origin[1]}"
    return f"/passport/{key}/{rid}"


def _notify_requirement_fit(demand, key: str, standing: bool, origin: str):
    """A posted requirement that fits: the sellers whose stock the engine would use, and the workshop whose next sheet it fits."""
    order = demand.model_dump(mode="json")
    d = _dataset_with_own(key)
    f = fulfil(demand, d["sellers"], d["resources"], d["transport"], d["settings"], 0.5, True)
    b = f.get("best")
    if b and b["units"] > 0:
        for ps in b["per_seller"]:
            rid = next((a["resource_id"] for a in b["allocations"] if a["seller_id"] == ps["seller_id"]), None)
            link = origin + (_passport_path(key, rid) if rid else "/")
            text = MSG.new_match(order, {"kind": "stock", "units": ps["units"], "unit_name": f["unit_name"],
                                         "value": ps["gain_above_salvage"], "link": link, "label": demand.label}, {"name": ps["seller_name"]})
            notify("new_match", text, ["seller"], [ps["seller_name"]], item_id=demand.id)
    if standing and demand.kind == "flat":
        g = _gapfill_data()
        req = GapFillRequest(**g)
        _notify_gapfill(plan_gap_fill(req), {o.id: o for o in req.orders}, origin, OWN_NAME)


def _notify_gapfill(plan: dict, orders: dict, origin: str, seller: str = OWN_NAME):
    """Standing orders posted by buyers that fit this sheet before cutting."""
    for nb in plan.get("neighbours", []):
        o = orders.get(nb["order_id"])
        if o is None or not (o.id.startswith("R") and o.id[1:].isdigit()):
            continue  # only buyers' posted requirements; the sample standing orders have no contact
        text = MSG.new_match(o.model_dump(mode="json"), {"kind": "gapfill", "units": nb["units"], "unit_name": "parts", "value": nb["value"],
                                                          "link": origin + "/#seller/gapfill/durable", "label": o.label}, {"name": seller})
        notify("new_match", text, ["seller"], [seller], item_id=o.id)


def _deal_context(row):
    key = row["scenario"] if row["scenario"] in SC.SCENARIOS else next(
        (k for k in SC.SCENARIOS if any(x.id == row["demand_id"] for x in _dataset_with_own(k)["demands"])), "steel")
    d = _dataset_with_own(key)
    demand = next((x for x in d["demands"] if x.id == row["demand_id"]), None)
    return demand, {x.id: x.name for x in d["sellers"]}


def _notify_deal(event: str, deal_id: int, origin: str, lg: dict | None = None):
    row = _deal_row(deal_id)
    demand, names = _deal_context(row)
    base = {"id": row["id"], "buyer_name": row["buyer_name"], "sellers": [names.get(x, x) for x in json.loads(row["sellers"])],
            "units": row["units"], "unit_name": row["unit_name"], "is_simulated": bool(row["is_simulated"]), "label": "sample",
            "part_name": None if row["kind"] == "perishable" or demand is None else demand.part_name}
    buyers = ["buyer", "food bank"]
    if event == "order_accepted":
        text = MSG.order_accepted({**base, "link": f"{origin}/deals/{deal_id}/pickup-sheet"})
        notify(event, text, buyers, [row["buyer_name"]], deal_id=deal_id)
    elif event == "pickup_booked":
        bk = json.loads(row["booking"])
        when = datetime.fromisoformat(bk["date"]).strftime("%d %b %Y").lstrip("0")
        sellers = [c["seller_name"] for c in lg["collect"]] if lg else base["sellers"]
        text = MSG.pickup_booked({**base, "sellers": sellers, "date": when, "slot": bk["slot"],
                                  "vehicle": (lg or {}).get("vehicle", {}).get("name"), "link": f"{origin}/deals/{deal_id}/pickup-sheet"})
        slot = f"{bk['date']} {bk['slot']}"  # a new slot is a new alert; the same slot is never prepared twice
        notify(event, text, ["seller"], sellers, deal_id=deal_id, item_id=slot)
        notify(event, text, buyers, [row["buyer_name"]], deal_id=deal_id, item_id=slot)
    elif event == "delivered":
        text = MSG.delivered({**base, "evidence": json.loads(row["evidence"]), "link": f"{origin}/deals/{deal_id}/challan"})
        notify(event, text, buyers, [row["buyer_name"]], deal_id=deal_id)


def _fresh_lots():
    d = SC.dataset("food")
    names = {x.id: x.name for x in d["sellers"]}
    out = [("food", r, names[r.seller_id]) for r in d["resources"]]
    names = _owner_names()
    return out + [(_import_key("fresh", o), r, names.get(o) or r.seller_id) for o, r in _own_items("fresh")]


def _sweep_fresh(origin: str, now: datetime | None = None) -> int:
    """Food bank and buyer contacts with fresh alerts hear about every lot at 24 hours or less, once per lot."""
    now, n = now or datetime.now(), 0
    for key, lot, seller_name in _fresh_lots():
        if lot.category == "used_cooking_oil" or lot.hours_left > FRESH_ALERT_HOURS:
            continue
        by = now + timedelta(hours=lot.hours_left)
        text = MSG.fresh_expiring({"seller_name": seller_name, "product": lot.product, "quantity_kg": lot.quantity_kg,
                                   "storage": lot.storage, "hours_left": lot.hours_left,
                                   "pickup_by": by.strftime("%H:%M on %d %b").replace(" 0", " "),
                                   "link": f"{origin}/passport/{key}/{lot.id}", "label": lot.label})
        n += notify("fresh", text, ["food bank", "buyer"], None, item_id=f"{key}:{lot.id}")
    return n


class ContactIn(BaseModel):
    business: str = ""
    role: str = ""
    name: str = ""
    phone: str = ""
    alerts: list[str] = []
    consent: bool = False


class ContactPatch(BaseModel):
    business: str | None = None
    role: str | None = None
    name: str | None = None
    phone: str | None = None
    alerts: list[str] | None = None
    active: bool | None = None


def _check_contact(c: dict, need_consent: bool) -> tuple[dict, dict]:
    errors, out = {}, {}
    if "business" in c:
        out["business"] = (c["business"] or "").strip()
        if not out["business"]:
            errors["business"] = "Choose your business, or type its name."
    if "role" in c:
        out["role"] = c["role"]
        if c["role"] not in CONTACT_ROLES:
            errors["role"] = "Choose seller, buyer or food bank."
    if "name" in c:
        out["name"] = (c["name"] or "").strip()
        if not out["name"]:
            errors["name"] = "Enter the name of the person who will read the alerts."
    if "phone" in c:
        try:
            out["phone"] = MSG.normalize_phone(c["phone"])
        except ValueError:
            errors["phone"] = "Enter a 10-digit mobile number, or the full number with its country code."
    if "alerts" in c:
        out["alerts"] = [a for a in ALERT_TYPES if a in (c["alerts"] or [])]
        if not out["alerts"] or len(out["alerts"]) != len(set(c["alerts"] or [])):
            errors["alerts"] = "Choose at least one alert." if not out["alerts"] else "Choose from the listed alerts."
    if need_consent and not c.get("consent"):
        errors["consent"] = "Tick the box to agree to receive alerts on this number."
    return out, errors


def _my_contact(me: dict, business: str | None) -> bool:
    """A seller or buyer sees and changes only the numbers registered for its own business. The admin sees all."""
    return me["admin"] or _norm_biz(business) == _norm_biz(me["name"])


@app.get("/api/whatsapp/contacts")
def whatsapp_contacts(role: str | None = None, me=ANY):
    """Registered contacts with the phone masked: this business's own, or every contact for the admin.
    role filters the list (seller, buyer, food bank)."""
    with closing(db()) as con:
        rows = con.execute("SELECT * FROM whatsapp_contacts ORDER BY id DESC").fetchall()
    roles = None if not role else set(role.split(","))
    if me["admin"]:
        known = {b["name"] for b in _sample_businesses()}
        businesses = _sample_businesses() + [{"name": u["business_name"], "role": "seller" if u["role"] == "seller" else "buyer"}
                                             for u in _accounts_by_bid().values() if u["role"] != "admin" and u["business_name"] not in known]
    else:
        businesses = [{"name": me["name"], "role": me["account"].get("contact_role", me["role"])}]
    return {"contacts": [_contact_out(r) for r in rows if (roles is None or r["role"] in roles) and _my_contact(me, r["business"])],
            "businesses": businesses, "alert_types": ALERT_TYPES, "roles": list(CONTACT_ROLES),
            "default_alerts": DEFAULT_ALERTS, "consent_text": CONSENT_TEXT}


def _add_contact(c: "ContactIn", request: Request) -> dict:
    vals, errors = _check_contact(c.model_dump(), need_consent=True)
    if not errors:
        with closing(db()) as con:
            dup = con.execute("SELECT id FROM whatsapp_contacts WHERE phone=? AND business=?", (vals["phone"], vals["business"])).fetchone()
        if dup:
            errors["phone"] = "This number is already registered for this business. Edit it in the list instead."
    if errors:
        raise HTTPException(422, {"message": "Some fields need fixing. Nothing was saved.", "errors": errors})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as con, con:
        cur = con.execute("INSERT INTO whatsapp_contacts (business, role, name, phone, alerts, consent_at, active, created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (vals["business"], vals["role"], vals["name"], vals["phone"], json.dumps(vals["alerts"]), now, 1, now))
        row = con.execute("SELECT * FROM whatsapp_contacts WHERE id=?", (cur.lastrowid,)).fetchone()
    _safely(_sweep_fresh, _origin(request))
    return _contact_out(row)


@app.post("/api/whatsapp/contacts")
def add_whatsapp_contact(c: ContactIn, request: Request, me=ANY):
    """A seller or buyer registers numbers for its own business; the admin for any business."""
    if not me["admin"]:
        c = c.model_copy(update={"business": me["name"]})
    return _add_contact(c, request)


@app.post("/register")
def register_number(c: ContactIn, request: Request):
    """The public registration form (/register, opened from a QR code): no sign-in, consent required."""
    return _add_contact(c, request)


def _contact_or_404(me: dict, con, cid: int):
    row = con.execute("SELECT * FROM whatsapp_contacts WHERE id=?", (cid,)).fetchone()
    if row is None or not _my_contact(me, row["business"]):
        raise HTTPException(404, "no such contact")
    return row


@app.patch("/api/whatsapp/contacts/{cid}")
def edit_whatsapp_contact(cid: int, c: ContactPatch, me=ANY):
    given = {k: v for k, v in c.model_dump().items() if v is not None}
    if not me["admin"]:
        given.pop("business", None)  # a business edits only its own numbers, and they stay its own
    vals, errors = _check_contact({k: v for k, v in given.items() if k != "active"}, need_consent=False)
    if errors:
        raise HTTPException(422, {"message": "Some fields need fixing. Nothing was changed.", "errors": errors})
    if "active" in given:
        vals["active"] = int(given["active"])
    if "alerts" in vals:
        vals["alerts"] = json.dumps(vals["alerts"])
    with closing(db()) as con, con:
        _contact_or_404(me, con, cid)
        if vals:
            con.execute(f"UPDATE whatsapp_contacts SET {', '.join(k + '=?' for k in vals)} WHERE id=?", (*vals.values(), cid))
        row = con.execute("SELECT * FROM whatsapp_contacts WHERE id=?", (cid,)).fetchone()
    return _contact_out(row)


@app.delete("/api/whatsapp/contacts/{cid}")
def remove_whatsapp_contact(cid: int, me=ANY):
    with closing(db()) as con, con:
        _contact_or_404(me, con, cid)
        con.execute("DELETE FROM whatsapp_contacts WHERE id=?", (cid,))
        con.execute("DELETE FROM notifications WHERE contact_id=? AND status='ready'", (cid,))
    return {"removed": True}


class NotifStatus(BaseModel):
    status: Literal["opened", "sent", "skipped"]  # never "delivered" or "read": we only record what a person did


def _my_notifications(me: dict) -> list:
    """Alerts prepared for this business's own numbers (every alert for the admin)."""
    with closing(db()) as con:
        rows = con.execute("SELECT n.*, c.name AS c_name, c.business AS c_business, c.phone AS c_phone, c.role AS c_role "
                           "FROM notifications n LEFT JOIN whatsapp_contacts c ON c.id = n.contact_id ORDER BY n.id DESC").fetchall()
    return [r for r in rows if me["admin"] or (r["c_business"] is not None and _my_contact(me, r["c_business"]))]


@app.get("/api/whatsapp/notifications")
def whatsapp_notifications(request: Request, status: str | None = None, me=ANY):
    _safely(_sweep_fresh, _origin(request))
    out = []
    for r in _my_notifications(me):
        if status and r["status"] != status:
            continue
        out.append({"id": r["id"], "created_at": r["created_at"], "event": r["event"], "event_text": ALERT_TYPES.get(r["event"], r["event"]),
                    "deal_id": r["deal_id"], "item_id": r["item_id"], "text": r["text"], "link": r["link"], "status": r["status"],
                    "status_text": STATUS_TEXT[r["status"]], "opened_at": r["opened_at"], "sent_at": r["sent_at"],
                    "to": {"name": r["c_name"] or "Removed contact", "business": r["c_business"] or "", "role": r["c_role"] or "",
                           "phone": MSG.mask_phone(r["c_phone"]) if r["c_phone"] else ""}})
    return {"notifications": out, "ready": sum(1 for x in out if x["status"] == "ready")}


@app.get("/api/whatsapp/notifications/count")
def whatsapp_ready_count(request: Request, me=ANY):
    _safely(_sweep_fresh, _origin(request))
    return {"ready": sum(1 for r in _my_notifications(me) if r["status"] == "ready")}


@app.post("/api/whatsapp/notifications/{nid}/status")
def whatsapp_notification_status(nid: int, s: NotifStatus, me=ANY):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    col = {"opened": "opened_at", "sent": "sent_at"}.get(s.status)
    if all(r["id"] != nid for r in _my_notifications(me)):
        raise HTTPException(404, "no such alert")
    with closing(db()) as con, con:
        con.execute(f"UPDATE notifications SET status=?{', ' + col + '=?' if col else ''} WHERE id=?",
                    (s.status, now, nid) if col else (s.status, nid))
    return {"id": nid, "status": s.status, "status_text": STATUS_TEXT[s.status]}


@app.get("/share/{key}/{rid}")
def share_on_whatsapp(key: str, rid: str, request: Request):
    """Open WhatsApp with one stock item's details typed in; the person chooses the chat and presses send."""
    item = next((i for i in _stock(key) if i["id"] == rid), None)
    if item is None:
        raise HTTPException(404, "no such item")
    text = MSG.offcut_share(item, f"{_origin(request)}/passport/{key}/{rid}")
    return RedirectResponse(MSG.wa_link(text), status_code=307)


@app.get("/register", response_class=HTMLResponse)
def register_page():
    """A phone-friendly form to register a WhatsApp number for alerts (for a visitor scanning the QR code)."""
    e = _html.escape
    biz = "".join(f'<option value="{e(b["name"])}" data-role="{e(b["role"])}">{e(b["name"])}</option>' for b in _sample_businesses())
    alerts = "".join(f'<label class="ck"><input type="checkbox" name="alerts" value="{k}"> {e(v)}</label>' for k, v in ALERT_TYPES.items())
    body = f"""<div class="card"><h1>WhatsApp alerts</h1>
<p class="note">Register a WhatsApp number to get CIRCULATE alerts. Each alert is prepared in the app and sent by a person with one click; nothing is sent automatically. <span class="chip">Sample data</span></p>
<form id="f" novalidate>
<label>Business<select name="business_pick" id="bp">{biz}<option value="__other">Other</option></select></label>
<label id="otherl" hidden>Business name<input name="business_other" autocomplete="organization"></label><span class="err" id="e-business"></span>
<label>You are a<select name="role" id="role"><option value="seller">Seller</option><option value="buyer">Buyer</option><option value="food bank">Food bank</option></select></label>
<label>Your name<input name="name" autocomplete="name"></label><span class="err" id="e-name"></span>
<label>WhatsApp number<input name="phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="98765 43210"></label><span class="err" id="e-phone"></span>
<fieldset><legend>Alerts you want</legend>{alerts}</fieldset><span class="err" id="e-alerts"></span>
<label class="ck"><input type="checkbox" name="consent"> {e(CONSENT_TEXT)}</label><span class="err" id="e-consent"></span>
<button type="submit">Register this number</button></form><p id="msg" class="note" aria-live="polite"></p></div>
<script>
const f=document.getElementById("f"),bp=document.getElementById("bp"),role=document.getElementById("role"),D={json.dumps(DEFAULT_ALERTS)};
const tick=()=>f.querySelectorAll("[name=alerts]").forEach(c=>c.checked=(D[role.value]||[]).includes(c.value));
bp.onchange=()=>{{document.getElementById("otherl").hidden=bp.value!=="__other";const o=bp.selectedOptions[0];if(o.dataset.role){{role.value=o.dataset.role;tick();}}}};
role.onchange=tick;bp.onchange();
f.onsubmit=async ev=>{{ev.preventDefault();document.querySelectorAll(".err").forEach(x=>x.textContent="");
 const fd=new FormData(f),body={{business:bp.value==="__other"?fd.get("business_other"):bp.value,role:role.value,name:fd.get("name"),phone:fd.get("phone"),alerts:fd.getAll("alerts"),consent:fd.get("consent")==="on"}};
 const r=await fetch("/register",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify(body)}});const t=await r.json().catch(()=>({{}}));
 if(r.ok){{f.hidden=true;document.getElementById("msg").textContent="Registered: "+t.phone+" for "+t.business+". Alerts will appear on the laptop under Ready to send. You can turn them off anytime.";}}
 else{{const er=(t.detail&&t.detail.errors)||{{}};for(const k in er){{const x=document.getElementById("e-"+k);if(x)x.textContent=er[k];}}document.getElementById("msg").textContent=(t.detail&&t.detail.message)||"Something went wrong. Try again.";}}}};
</script>"""
    css = ("[hidden]{display:none!important}label{display:flex;flex-direction:column;gap:4px;font-size:14px;margin:10px 0 2px;color:#5d6b77}"
           "input:not([type=checkbox]),select{width:100%;box-sizing:border-box;font:inherit;font-size:16px;padding:10px 12px;border:1px solid #c4ccd3;border-radius:8px;background:#fff;color:#17222b}"
           "label.ck{flex-direction:row;align-items:flex-start;gap:10px;color:#17222b;font-size:15px}label.ck input{width:20px;height:20px;margin-top:1px;flex:none}"
           "fieldset{border:1px solid #d9dfe4;border-radius:8px;margin:12px 0 2px;padding:6px 12px 10px}legend{font-size:14px;color:#5d6b77;padding:0 4px}"
           "button{margin-top:14px;width:100%;padding:12px;font-size:16px;background:#f2a33a;border-color:#f2a33a;color:#1f1400}"
           ".err{color:#b0281c;font-size:13px;display:block}.err:empty{display:none}")
    return _PAGE.format(title="WhatsApp alerts", body=body).replace("</style>", css + "</style>")


# ---------------- Demo logins: separate entry for seller, buyer and admin ----------------
# A demo login only picks whose screens and data you see. There are no passwords, nothing secret is stored,
# and the APIs stay open (no API authentication) by design: this is a prototype.

SESSION_COOKIE = "circulate_session"
ROLE_NAMES = {"seller": "Seller", "buyer": "Buyer", "admin": "Admin"}
ROLE_FOR = {"seller": "sellers", "buyer": "buyers", "admin": "the CIRCULATE team"}
DEMO_PHONE, DEMO_PIN = "98765 43210", "1234"
ADD_STOCK_GROUP = ("Add stock", [("add", "Add stock", "plus", None), ("add-csv", "Upload CSV", "upload", None),
                                 ("add-sync", "Auto-sync", "folder-sync", None), ("add-manual", "Manual entry", "keyboard", None)])
# Each role's left rail: (group, [(view, label, icon, lane or None)]). The first item that fits the lane is the role's home.
ROLE_MENUS = {
    "seller": [("My workshop", [("gapfill", "Sold before it's cut", "scissors", "durable"), ("stock", "My stock and passports", "qr-code", None)]),
               ADD_STOCK_GROUP,
               ("Orders", [("deals", "Deals", "receipt", None), ("pickup", "Pickups", "truck", None)]),
               ("Alerts", [("whatsapp", "WhatsApp alerts", "message-circle", None)])],
    "buyer": [("Marketplace", [("browse", "Browse surplus", "search", None), ("post", "Post a requirement", "file-plus-2", None),
                               ("reqs", "My requirements", "clipboard-list", None), ("match", "Order and match", "layers", None)]),
              ("Orders", [("pickup", "Deliveries", "truck", None)]),
              ("Alerts", [("whatsapp", "WhatsApp alerts", "message-circle", None)])],
    "admin": [("Seller screens", [("gapfill", "Sold before it's cut", "scissors", "durable"), ("stock", "Stock and passports", "qr-code", None),
                                  ("offers", "Offers and recovery plan", "route", None)]),
              ADD_STOCK_GROUP,
              ("Buyer screens", [("browse", "Browse surplus", "search", None), ("post", "Post a requirement", "file-plus-2", None),
                                 ("reqs", "My requirements", "clipboard-list", None), ("match", "Order and match", "layers", None)]),
              ("Deals", [("monitor", "Deal monitor", "gauge", None), ("deals", "Deals", "receipt", None), ("pickup", "Pickups and deliveries", "truck", None),
                         ("collections", "Collection requests", "package", None)]),
              ("Platform", [("quality", "Data quality", "shield-check", None), ("sim", "Simulation lab", "flask-conical", None),
                            ("prices", "Price references", "banknote", None), ("rules", "Rules", "list-checks", None), ("users", "All users", "users", None)]),
              ("Alerts", [("ready", "Ready to send", "send", None), ("whatsapp", "WhatsApp alerts", "message-circle", None)])],
}
EVERY_ROLE_VIEWS = ["ready", "account"]  # the bell opens "Ready to send"; the business name opens Account


@functools.lru_cache(maxsize=1)
def _demo_accounts() -> dict:
    """Demo businesses, using the ids already in engine/scenarios.py (as scenario:id, because ids repeat across scenarios)."""
    out = {}

    def seller(key, sid):
        x = next(v for v in SC.dataset(key)["sellers"] if v.id == sid)
        lane = "fresh" if SC.SCENARIOS[key]["engine"] == "perishable" else "durable"
        out[f"{key}:{sid}"] = {"business_id": f"{key}:{sid}", "role": "seller", "name": x.name, "scenario": key, "seller_id": sid,
                               "lane": lane, "contact_role": "seller"}

    def buyer(key, bid):
        x = next(v for v in SC.dataset(key)["demands"] if v.buyer_id == bid)
        lane = "fresh" if SC.SCENARIOS[key]["engine"] == "perishable" else "durable"
        ngo = getattr(x, "buyer_type", "") == "ngo"
        out[f"{key}:{bid}"] = {"business_id": f"{key}:{bid}", "role": "buyer", "name": x.buyer_name, "scenario": key, "buyer_id": bid,
                               "demand_id": x.id, "lane": lane, "contact_role": "food bank" if ngo else "buyer",
                               "buyer_type": "food_bank" if ngo else ("hostel" if "hostel" in x.buyer_name.lower() else "business")}
    seller("steel", "A")
    seller("food", "H")
    buyer("steel", "W")
    buyer("food", "M")
    buyer("food", "N")
    out["admin"] = {"business_id": "admin", "role": "admin", "name": "CIRCULATE Admin", "lane": "durable", "area": "CIRCULATE team"}
    for a in out.values():
        a.setdefault("area", a["name"].split(", ", 1)[1] if ", " in a["name"] else "")
        a["short_name"] = a["name"].split(", ")[0]
        a["initial"] = a["short_name"][0].upper()
    return out


def _menu(role: str) -> list[dict]:
    return [{"group": g, "items": [{"view": v, "label": lab, "icon": icon, "lane": lane} for v, lab, icon, lane in items]}
            for g, items in ROLE_MENUS[role]]


def _views(role: str) -> list[str]:
    return [v for _, items in ROLE_MENUS[role] for v, *_ in items] + EVERY_ROLE_VIEWS


def _owner(view: str) -> str:
    for role in ("seller", "buyer"):
        if view in _views(role):
            return role
    return "admin"


# ---------------- Accounts: real sign-in, with a demo switch for emergencies ----------------
import logging
import re as _re

from app import auth as AUTH

log = logging.getLogger("circulate")
SESSION_COOKIE = "circ_session"
OLD_COOKIE = "circulate_session"
THROTTLE = AUTH.Throttle()
SELLER_TYPES = {"workshop": "Workshop", "construction_site": "Construction site", "shop": "Shop", "kitchen": "Kitchen"}
LOGIN_ERROR = "That email and password do not match an account. Check them and try again."
DEMO_USERS = [  # email, business id (from engine/scenarios.py), role, seller type
    ("sriram@demo.circulate", "steel:A", "seller", "workshop"),
    ("thamarai@demo.circulate", "food:H", "seller", "kitchen"),
    ("ezhil@demo.circulate", "steel:W", "buyer", None),
    ("anna@demo.circulate", "food:M", "buyer", None),
    ("thozhan@demo.circulate", "food:N", "buyer", None),
]
_HASH_CACHE: dict[tuple[str, str], tuple[str, str]] = {}  # (email, password) -> (salt, hash): seeding a new database stays fast


def auth_mode() -> str:
    """AUTH_MODE=real (default) needs email and password. AUTH_MODE=demo restores the one-tap demo login for emergencies."""
    return "demo" if os.environ.get("AUTH_MODE", "real").strip().lower() == "demo" else "real"


def _on_server() -> bool:
    return bool(os.environ.get("RENDER") or os.environ.get("CIRCULATE_ON_SERVER"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _seed_users(con) -> None:
    """Idempotent: creates the admin and the demo accounts if their email is missing; never changes an existing password."""
    have = {r[0] for r in con.execute("SELECT email FROM users")}
    demo_pw = os.environ.get("DEMO_PASSWORD") or "Circulate@2026"
    rows = []
    admin_email = (os.environ.get("ADMIN_EMAIL") or "admin@circulate.local").strip().lower()
    if admin_email not in have:
        pw = os.environ.get("ADMIN_PASSWORD")
        if not pw and _on_server():
            log.warning("No ADMIN_PASSWORD set: no admin account was created. Set ADMIN_EMAIL and ADMIN_PASSWORD and restart.")
        else:
            if not pw:
                pw = secrets.token_urlsafe(12)
                print(f"\n  CIRCULATE admin account created (shown once):\n    email:    {admin_email}\n    password: {pw}\n"
                      "  Set ADMIN_PASSWORD to choose your own.\n", flush=True)
            rows.append(("admin", None, "admin", "CIRCULATE Admin", "CIRCULATE team", admin_email, "", "CIRCULATE team", "durable", pw))
    for email, bid, role, stype in DEMO_USERS:
        if email in have:
            continue
        a = _demo_accounts()[bid]
        rows.append((role, stype, bid, a["name"], "Demo contact", email, "", a["area"], a["lane"], demo_pw))
    for role, stype, bid, name, contact, email, phone, area, lane, pw in rows:
        if (email, pw) not in _HASH_CACHE:
            _HASH_CACHE[(email, pw)] = AUTH.hash_password(pw)
        salt, h = _HASH_CACHE[(email, pw)]
        con.execute("INSERT OR IGNORE INTO users (role, seller_type, business_id, business_name, contact_name, email, phone, area, lane,"
                    " password_hash, salt, token_version, active, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,1,?)",
                    (role, stype, bid, name, contact, email, phone, area, lane, h, salt, _now()))
    con.commit()


def _key() -> bytes:
    return AUTH.secret_key(ROOT.parent)


def _user(where: str, *args):
    with closing(db()) as con:
        return con.execute(f"SELECT * FROM users WHERE {where}", args).fetchone()


def _account(u) -> dict:
    """The fields the app uses for a business: demo businesses keep their scenario links (stock, orders); new ones start empty."""
    a = dict(_demo_accounts().get(u["business_id"], {}))
    a.update(business_id=u["business_id"], role=u["role"], name=u["business_name"], lane=u["lane"] or "durable",
             area=u["area"] or "", seller_type=u["seller_type"], user_id=u["id"], email=u["email"], contact_name=u["contact_name"],
             phone=u["phone"] or "")
    a.setdefault("contact_role", "seller" if u["role"] == "seller" else "buyer")
    a["short_name"] = a["name"].split(", ")[0]
    a["initial"] = (a["short_name"][:1] or "?").upper()
    if _is_new_business(u):  # placed from its area; every distance or pickup cost for it says "approximate location"
        a["x_km"], a["y_km"] = _area_xy(u["area"])
        a["approx_location"] = True
    return a


def _session(request: Request) -> dict | None:
    """{real: account, view: account or None, user: row}. A bad, expired or out-of-date cookie counts as signed out."""
    tok = AUTH.read_token(_key(), request.cookies.get(SESSION_COOKIE))
    if tok is None:
        return None
    u = _user("id=?", tok["user_id"])
    if u is None or not u["active"] or u["token_version"] != tok["token_version"]:
        return None
    view = None
    if u["role"] == "admin" and tok["view"]:
        v = _user("business_id=? AND active=1", tok["view"])
        view = _account(v) if v is not None and v["role"] != "admin" else None
    return {"real": _account(u), "view": view, "user": u}


def _me(sess: dict) -> dict:
    """The business acting in a request: the admin's "View as" business if one is chosen, else the signed-in account."""
    a = sess["view"] or sess["real"]
    return {"role": a["role"], "business_id": a["business_id"], "name": a["name"], "admin": a["role"] == "admin",
            "real_admin": sess["real"]["role"] == "admin", "account": a}


# ---------------- Ownership of deals: both sides are stored, so both can see them ----------------
def _scenario_of(demand_id: str) -> str | None:
    return next((k for k in SC.SCENARIOS if any(x.id == demand_id for x in _dataset_with_own(k)["demands"])), None)


def _requirement_owner(req_id: int) -> str:
    with closing(db()) as con:
        row = con.execute("SELECT owner FROM requirements WHERE id=?", (req_id,)).fetchone()
    return (row["owner"] or "") if row else ""


def _demand_owner(key: str | None, demand_id: str) -> str:
    """The buying business of an order: whoever posted the requirement, or the demo business of a sample order ("" if none)."""
    if re.fullmatch(r"R\d+", demand_id):
        return _requirement_owner(int(demand_id[1:]))
    if key not in SC.SCENARIOS:
        return ""
    d = next((x for x in SC.dataset(key)["demands"] if x.id == demand_id), None)
    bid = f"{key}:{d.buyer_id}" if d else ""
    return bid if bid in _accounts_by_bid() else ""


def _seller_owners(key: str, seller_ids: list[str]) -> list[str]:
    """The selling businesses behind a deal's seller ids: listed items' owners and demo businesses. Sample sellers without an
    account have none."""
    d, accounts, owned = _dataset_with_own(key), _accounts_by_bid(), _owned(key)
    by_seller = {}
    for rid, (owner, _) in owned["origin"].items():
        by_seller.setdefault(next(r.seller_id for r in owned["resources"] if r.id == rid), set()).add(owner)
    out = []
    for sid in seller_ids:
        found = set(by_seller.get(sid, set()))
        s = next((x for x in d["sellers"] if x.id == sid), None)
        if f"{key}:{sid}" in accounts:
            found.add(f"{key}:{sid}")
        elif s is not None:
            found |= {b for b, u in accounts.items() if u["role"] == "seller" and u["business_name"] == s.name}
        out += [b for b in sorted(found) if b not in out]
    return out


def _is_party(me: dict, row) -> bool:
    """The deal's buyer, one of its sellers, or the admin. Deals saved before accounts have neither side: admin only."""
    return me["admin"] or me["business_id"] in [row["buyer_business"] or "", *json.loads(row["seller_business"] or "[]")]


def _session_out(sess: dict) -> dict:
    a = sess["view"] or sess["real"]
    lane_items = [i for g in _menu(a["role"]) for i in g["items"] if not i["lane"] or i["lane"] == a["lane"]]
    out = {**{k: v for k, v in a.items() if k != "user_id"}, "business_name": a["name"], "role_name": ROLE_NAMES[a["role"]],
           "menu": _menu(a["role"]), "views": _views(a["role"]), "home": lane_items[0]["view"], "viewing_as": sess["view"] is not None,
           "owners": {v: {"role": _owner(v), "for": ROLE_FOR[_owner(v)]} for v in _views("admin")}, "auth_mode": auth_mode()}
    if sess["real"]["role"] == "admin":  # the "View as" menu
        out["real"] = {"business_id": sess["real"]["business_id"], "business_name": sess["real"]["name"], "role": "admin", "user_id": sess["user"]["id"]}
        with closing(db()) as con:
            users = con.execute("SELECT * FROM users WHERE active=1 ORDER BY role='admin' DESC, id").fetchall()
        out["accounts"] = [{"business_id": x["business_id"], "name": x["business_name"], "role": x["role"], "role_name": ROLE_NAMES[x["role"]],
                            "area": x["area"] or "", "lane": x["lane"]} for x in users]
    return out


def _issue(request: Request, response: Response, u, view: str = "") -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
    response.set_cookie(SESSION_COOKIE, AUTH.make_token(_key(), u["id"], u["token_version"], view), httponly=True, samesite="lax",
                        secure=secure, max_age=AUTH.SESSION_DAYS * 86400, path="/")
    response.delete_cookie(OLD_COOKIE, path="/")


class SessionIn(BaseModel):
    role: Literal["seller", "buyer", "admin"]
    business_id: str


class ViewAsIn(BaseModel):
    business_id: str


class LoginIn(BaseModel):
    email: str = ""
    password: str = ""
    role: Literal["seller", "buyer", "admin"] | None = None


class RegisterIn(BaseModel):
    role: str = ""
    seller_type: str | None = None
    business_name: str = ""
    contact_name: str = ""
    email: str = ""
    phone: str = ""
    area: str = ""
    lane: str = ""
    password: str = ""
    confirm: str = ""


class PasswordIn(BaseModel):
    current: str = ""
    new: str = ""
    confirm: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/session")
def get_session(request: Request):
    sess = _session(request)
    if sess is None:
        raise HTTPException(401, "Not signed in. Sign in on the start page.")
    return _session_out(sess)


@app.post("/api/session")
def start_session(s: SessionIn, request: Request, response: Response):
    """The old one-tap demo login. Works only when AUTH_MODE=demo (an emergency switch)."""
    if auth_mode() != "demo":
        raise HTTPException(403, "One-tap demo login is switched off. Sign in with your email and password.")
    u = _user("business_id=? AND role=? AND active=1", s.business_id, s.role)
    if u is None:
        raise HTTPException(422, "Choose one of the demo accounts for this role.")
    _issue(request, response, u)
    return _session_out({"real": _account(u), "view": None, "user": u})


@app.delete("/api/session")
def end_session(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(OLD_COOKIE, path="/")
    return {"signed_out": True}


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    email = body.email.strip().lower()
    wait = THROTTLE.wait(email)
    if wait:
        raise HTTPException(429, f"Too many attempts for this email. Try again in {max(1, round(wait / 60))} minute(s).")
    u = _user("email=?", email) if email else None
    ok = AUTH.verify_password(body.password, u["salt"], u["password_hash"]) if u is not None else (AUTH.burn_time(body.password) or False)
    if not ok:
        THROTTLE.fail(email)
        raise HTTPException(401, LOGIN_ERROR)  # the same words for an unknown email and a wrong password
    THROTTLE.success(email)
    if not u["active"]:
        raise HTTPException(403, "This account is switched off. Ask the CIRCULATE admin to turn it back on.")
    if body.role and body.role != u["role"]:
        raise HTTPException(403, f"This is a {ROLE_NAMES[u['role']].lower()} account. Use the {ROLE_NAMES[u['role']].lower()} sign-in.")
    with closing(db()) as con, con:
        con.execute("UPDATE users SET last_login_at=? WHERE id=?", (_now(), u["id"]))
    _issue(request, response, u)
    return _session_out({"real": _account(u), "view": None, "user": u})


@app.post("/api/auth/logout")
def logout(response: Response):
    return end_session(response)


_EMAIL = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.post("/api/auth/register")
def register(r: RegisterIn, request: Request, response: Response):
    """Sellers and buyers create their own account. Admin accounts cannot be registered."""
    e = {}
    if r.role not in ("seller", "buyer"):
        e["role"] = "Choose seller or buyer. Admin accounts cannot be created here."
    if r.role == "seller" and r.seller_type not in SELLER_TYPES:
        e["seller_type"] = "Choose what kind of seller you are."
    for k, label in (("business_name", "your business name"), ("contact_name", "your name"), ("area", "your area")):
        if not getattr(r, k).strip():
            e[k] = f"Enter {label}."
    email = r.email.strip().lower()
    if not _EMAIL.match(email):
        e["email"] = "Enter an email address like name@business.in."
    elif _user("email=?", email) is not None:
        e["email"] = "An account with this email already exists. Sign in instead."
    phone = _re.sub(r"[\s\-+()]", "", r.phone)
    if phone.startswith("91") and len(phone) == 12:
        phone = phone[2:]
    if not (phone.isdigit() and len(phone) == 10):
        e["phone"] = "Enter a 10-digit mobile number."
    if r.lane not in ("durable", "fresh"):
        e["lane"] = "Choose Durable or Fresh."
    if len(r.password) < AUTH.MIN_PASSWORD:
        e["password"] = f"Use at least {AUTH.MIN_PASSWORD} characters."
    elif r.password != r.confirm:
        e["confirm"] = "The two passwords do not match."
    if e:
        raise HTTPException(422, {"message": "Some fields need fixing.", "errors": e})
    salt, h = AUTH.hash_password(r.password)
    with closing(db()) as con, con:
        cur = con.execute("INSERT INTO users (role, seller_type, business_id, business_name, contact_name, email, phone, area, lane,"
                          " password_hash, salt, token_version, active, created_at, last_login_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,1,?,?)",
                          (r.role, r.seller_type if r.role == "seller" else None, "", r.business_name.strip(), r.contact_name.strip(),
                           email, phone, r.area.strip(), r.lane, h, salt, _now(), _now()))
        con.execute("UPDATE users SET business_id=? WHERE id=?", (f"u{cur.lastrowid}", cur.lastrowid))
    u = _user("email=?", email)
    _issue(request, response, u)
    return _session_out({"real": _account(u), "view": None, "user": u})


@app.post("/api/auth/change-password")
def change_password(b: PasswordIn, request: Request, response: Response, me=ANY):
    sess = _session(request)
    if sess is None:
        raise HTTPException(401, "Not signed in.")
    u = sess["user"]
    e = {}
    if not AUTH.verify_password(b.current, u["salt"], u["password_hash"]):
        e["current"] = "Your current password is not right."
    if len(b.new) < AUTH.MIN_PASSWORD:
        e["new"] = f"Use at least {AUTH.MIN_PASSWORD} characters."
    elif b.confirm is not None and b.confirm != b.new:
        e["confirm"] = "The two passwords do not match."
    if e:
        raise HTTPException(422, {"message": "Some fields need fixing.", "errors": e})
    salt, h = AUTH.hash_password(b.new)
    with closing(db()) as con, con:
        con.execute("UPDATE users SET password_hash=?, salt=?, token_version=token_version+1 WHERE id=?", (h, salt, u["id"]))
    _issue(request, response, _user("id=?", u["id"]))  # this browser stays signed in; every other cookie stops working
    return {"changed": True}


@app.post("/api/session/view-as")
def view_as(v: ViewAsIn, request: Request, response: Response, me=REAL_ADMIN):
    """Admin only: see the app exactly as one business does, for a live demo."""
    sess = _session(request)
    if sess is None or sess["real"]["role"] != "admin":
        raise HTTPException(403, "Only the CIRCULATE admin can view the app as another business.")
    t = _user("business_id=? AND active=1", v.business_id)
    if t is None:
        raise HTTPException(422, "Choose one of the businesses in the list.")
    view = "" if t["role"] == "admin" else t["business_id"]
    _issue(request, response, sess["user"], view)
    return _session_out({"real": sess["real"], "view": _account(t) if view else None, "user": sess["user"]})


@app.delete("/api/session/view-as")
def exit_view_as(request: Request, response: Response, me=REAL_ADMIN):
    sess = _session(request)
    if sess is None or sess["real"]["role"] != "admin":
        raise HTTPException(403, "Only the CIRCULATE admin can view the app as another business.")
    _issue(request, response, sess["user"])
    return _session_out({"real": sess["real"], "view": None, "user": sess["user"]})


_MARK = ('<svg class="mark" viewBox="0 0 32 32" aria-hidden="true"><rect x="2" y="7" width="13" height="18" rx="2" fill="none" stroke="currentColor" stroke-width="2"/>'
         '<rect x="8.5" y="13" width="5" height="6" rx=".5" fill="none" stroke="currentColor" stroke-width="1.2" stroke-dasharray="1.6 1.4" opacity=".7"/>'
         '<rect x="17" y="7" width="13" height="18" rx="2" fill="none" stroke="currentColor" stroke-width="2" opacity=".55"/>'
         '<rect x="19.5" y="11" width="6" height="7" rx="1" fill="#f2a33a"/><path d="M13 21.5h4.2" stroke="#f2a33a" stroke-width="1.6" stroke-linecap="round"/>'
         '<path d="M15.6 19.9l1.8 1.6-1.8 1.6" fill="none" stroke="#f2a33a" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def _entry_page(title: str, body: str) -> str:
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_html.escape(title)}</title><link rel="icon" href="/static/brand/favicon.svg" type="image/svg+xml">'
            '<link rel="preload" href="/static/fonts/ibm-plex-sans-latin.woff2" as="font" type="font/woff2" crossorigin>'
            f'<link rel="stylesheet" href="/static/app.css?v={_asset_stamp()}"></head>'
            f'<body class="entry"><main class="entry-main">{body}</main></body></html>')


def _ic(name: str) -> str:
    return f'<svg class="ic" aria-hidden="true"><use href="/static/icons/sprite.svg#i-{name}"/></svg>'


def _start_page(sess: dict | None) -> str:
    e = _html.escape
    cont = ""
    if sess:
        a = sess["view"] or sess["real"]
        cont = (f'<p class="entry-cont">Signed in as <b>{e(a["short_name"])}</b>. <a href="/app">Continue {_ic("arrow-right")}</a></p>')
    cards = [("seller", "I sell surplus", "Workshops and kitchens: find your surplus and sell it before it becomes scrap.", "factory"),
             ("buyer", "I buy materials", "Fabricators, messes and food banks: get what you need from nearby surplus.", "shopping-cart"),
             ("admin", "Admin", "CIRCULATE team: follow every deal and see where each number comes from.", "shield-check")]
    body = (f'<header class="entry-head">{_MARK}<div><div class="entry-brand">CIRCULATE</div><p class="lead">Sell surplus before it becomes scrap.</p></div></header>'
            + cont +
            '<nav class="entry-roles" aria-label="Choose how you use CIRCULATE">'
            + "".join(f'<a class="rolecard r-{r}" href="/login/{r}"><span class="rico">{_ic(icon)}</span><span class="rc-t">{e(t)}</span>'
                      f'<span class="rc-d">{e(d)}</span><span class="rc-go">{"Sign in" if r != "admin" else "CIRCULATE team"} {_ic("arrow-right")}</span></a>'
                      for r, t, d, icon in cards)
            + '</nav><p class="entry-foot">Prototype. Demo accounts only. Sample data is labelled.</p>')
    return _entry_page("CIRCULATE", body)


def _demo_login_page(role: str) -> str:
    """AUTH_MODE=demo only: the one-tap business login."""
    e = _html.escape
    accounts = [a for a in _demo_accounts().values() if a["role"] == role]
    first = accounts[0]
    cards = "".join(
        f'<button class="acct" type="button" data-id="{e(a["business_id"])}"><span class="av">{e(a["initial"])}</span>'
        f'<span class="ac-t"><b>{e(a["short_name"])}</b><span>{e(a["area"] or ("Food bank" if a.get("contact_role") == "food bank" else "Hostel mess" if a.get("buyer_type") == "hostel" else ""))}</span></span>'
        f'<span class="pill">{e(ROLE_NAMES[role])}</span>{_ic("chevron-right")}</button>' for a in accounts)
    body = f"""<div class="login r-{role}">
  <a class="entry-back" href="/">{_ic("chevron-right")}Back</a>
  <div class="login-card">
    <div class="login-head">{_MARK}<div><span class="lbl">CIRCULATE</span><h1>{e(ROLE_NAMES[role])}</h1></div></div>
    <p class="mut">{"Choose your business. One tap signs you in." if len(accounts) > 1 else "One tap signs you in."}</p>
    <div class="accts">{cards}</div>
    <details class="phone"><summary>Sign in with phone</summary>
      <form id="pf" class="form" novalidate><label class="field">Phone number<input id="ph" type="tel" inputmode="tel" value="{DEMO_PHONE}" autocomplete="off"></label>
        <label class="field">4-digit PIN<input id="pin" type="password" inputmode="numeric" maxlength="4" value="{DEMO_PIN}" autocomplete="off"></label>
        <button class="btn pri" type="submit">Sign in</button>
        <p class="note">Demo: this signs you in as {e(first["short_name"])}.</p></form></details>
    <p class="err" id="lerr" role="alert" hidden></p>
  </div>
  <p class="entry-foot"><b>Demo login mode.</b> One-tap sign-in is switched on (AUTH_MODE=demo).</p>
</div>
<script>
const ROLE = {json.dumps(role)}, FIRST = {json.dumps(first["business_id"])};
async function signIn(id) {{
  document.querySelectorAll(".acct,#pf button").forEach(b => b.disabled = true);
  try {{
    const r = await fetch("/api/session", {{method: "POST", headers: {{"Content-Type": "application/json"}}, body: JSON.stringify({{role: ROLE, business_id: id}})}});
    if (!r.ok) throw new Error();
    location.replace("/app");
  }} catch (e) {{
    const x = document.getElementById("lerr"); x.hidden = false; x.textContent = "Could not reach the app. Check that it is still running, then try again.";
    document.querySelectorAll(".acct,#pf button").forEach(b => b.disabled = false);
  }}
}}
document.querySelectorAll(".acct").forEach(b => b.onclick = () => signIn(b.dataset.id));
document.getElementById("pf").onsubmit = ev => {{ ev.preventDefault(); signIn(FIRST); }};
</script>"""
    return _entry_page(f"{ROLE_NAMES[role]} login · CIRCULATE", body)


PROTOTYPE_NOTE = "Prototype server. Accounts created here may be reset when the server restarts. Demo accounts are always available."


@app.get("/login/{role}", response_class=HTMLResponse)
def login_page(role: str, request: Request):
    if role not in ROLE_NAMES:
        raise HTTPException(404, "There is no such login page. Choose seller, buyer or admin on the start page.")
    if _session(request) is not None:
        return RedirectResponse("/app", status_code=307)
    if auth_mode() == "demo":
        return HTMLResponse(_demo_login_page(role))  # already a whole page
    e = _html.escape
    demo_pw = os.environ.get("DEMO_PASSWORD") or "Circulate@2026"
    demos = [(email, _demo_accounts()[bid]) for email, bid, r, _ in DEMO_USERS if r == role]
    panel = ""
    if demos:
        # a plain section, always open: nothing to collapse by accident, and it renders the same in every browser
        panel = (f'<section class="demo-panel" aria-labelledby="demo-h"><h2 class="demo-h" id="demo-h">Demo accounts <span class="pill p-warn">Demo only</span></h2>'
                 f'<p class="note">Tap a business to fill in its email and password, then press Sign in.</p><div class="accts">'
                 + "".join(f'<button class="acct" type="button" data-email="{e(email)}" data-business="{e(a["business_id"])}"><span class="av">{e(a["initial"])}</span>'
                           f'<span class="ac-t"><b>{e(a["short_name"])}</b><span>{e(email)}</span></span><span class="pill">{e(ROLE_NAMES[role])}</span>'
                           f'{_ic("chevron-right")}</button>' for email, a in demos) + '</div></section>')
    create = (f'<p class="login-alt">New to CIRCULATE? <a href="/signup/{role}">Create a {role} account</a></p>' if role != "admin" else "")
    body = f"""<div class="login r-{role}">
  <a class="entry-back" href="/">{_ic("chevron-right")}Back</a>
  <div class="login-card">
    <div class="login-head">{_MARK}<div><span class="lbl">CIRCULATE</span><h1>{e(ROLE_NAMES[role])} sign-in</h1></div></div>
    <form id="lf" class="form" novalidate>
      <label class="field">Email<input id="em" type="email" autocomplete="username" inputmode="email" required></label>
      <label class="field">Password<span class="pw"><input id="pw" type="password" autocomplete="current-password" required>
        <button class="btn sm ghost" type="button" id="pwt" aria-pressed="false" aria-controls="pw">Show</button></span></label>
      <p class="err" id="lerr" role="alert" hidden></p>
      <button class="btn pri lg" type="submit" id="go">Sign in</button>
      <p class="note">Forgot password? Ask the CIRCULATE admin.</p>
    </form>
    {create}
    {panel}
  </div>
  <p class="entry-foot">{e(PROTOTYPE_NOTE)}</p>
</div>
<script>
const ROLE = {json.dumps(role)}, DEMO_PW = {json.dumps(demo_pw if demos else "")};
const em = document.getElementById("em"), pw = document.getElementById("pw"), er = document.getElementById("lerr"), go = document.getElementById("go");
document.getElementById("pwt").onclick = ev => {{ const show = pw.type === "password"; pw.type = show ? "text" : "password"; ev.target.textContent = show ? "Hide" : "Show"; ev.target.setAttribute("aria-pressed", String(show)); }};
document.querySelectorAll("[data-email]").forEach(b => b.onclick = () => {{ em.value = b.dataset.email; pw.value = DEMO_PW; er.hidden = true; go.focus(); }});
document.getElementById("lf").onsubmit = async ev => {{
  ev.preventDefault(); er.hidden = true;
  if (!em.value.trim() || !pw.value) {{ er.hidden = false; er.textContent = "Enter your email and password."; return; }}
  go.disabled = true;
  try {{
    const r = await fetch("/api/auth/login", {{method: "POST", headers: {{"Content-Type": "application/json"}}, body: JSON.stringify({{email: em.value, password: pw.value, role: ROLE}})}});
    const t = await r.json().catch(() => ({{}}));
    if (r.ok) {{ location.replace("/app"); return; }}
    er.hidden = false; er.textContent = typeof t.detail === "string" ? t.detail : "Sign-in did not work. Try again.";
  }} catch (x) {{ er.hidden = false; er.textContent = "Could not reach the app. Check that it is still running, then try again."; }}
  go.disabled = false;
}};
</script>"""
    return _entry_page(f"{ROLE_NAMES[role]} sign-in · CIRCULATE", body)


@app.get("/signup/{role}", response_class=HTMLResponse)
def signup_page(role: str, request: Request):
    if role not in ("seller", "buyer"):
        raise HTTPException(404, "Accounts can be created for sellers and buyers. The CIRCULATE admin is set up on the server.")
    if _session(request) is not None:
        return RedirectResponse("/app", status_code=307)
    e = _html.escape
    f = lambda k, label, inp: f'<label class="field mfield" data-f="{k}">{label}{inp}<span class="ferr" id="e-{k}"></span></label>'
    types = "".join(f'<label><input type="radio" name="seller_type" value="{k}">{e(v)}</label>' for k, v in SELLER_TYPES.items())
    body = f"""<div class="login signup r-{role}">
  <a class="entry-back" href="/login/{role}">{_ic("chevron-right")}Back to sign-in</a>
  <div class="login-card">
    <div class="login-head">{_MARK}<div><span class="lbl">CIRCULATE</span><h1>Create a {role} account</h1></div></div>
    <form id="sf" class="form" novalidate><div class="mgrid">
      {f("business_name", "Business name", '<input name="business_name" autocomplete="organization">')}
      {'<div class="field mfield wide" role="group" aria-label="Seller type"><span>What kind of seller are you?</span><div class="opts">' + types + '</div><span class="ferr" id="e-seller_type"></span></div>' if role == "seller" else ""}
      {f("contact_name", "Your name", '<input name="contact_name" autocomplete="name">')}
      {f("email", "Email", '<input name="email" type="email" autocomplete="email" inputmode="email">')}
      {f("phone", "Mobile number (10 digits)", '<input name="phone" type="tel" inputmode="numeric" autocomplete="tel" placeholder="98765 43210">')}
      {f("area", "Area", '<input name="area" placeholder="e.g. Ambattur" autocomplete="address-level2">')}
      <div class="field mfield wide" role="group" aria-label="Lane"><span>What do you deal in?</span><div class="opts">
        <label><input type="radio" name="lane" value="durable">Durable: steel, aluminium, plywood, bars</label><label><input type="radio" name="lane" value="fresh">Fresh: food and perishables</label></div><span class="ferr" id="e-lane"></span></div>
      {f("password", "Password (at least 8 characters)", '<input name="password" type="password" autocomplete="new-password">')}
      {f("confirm", "Confirm password", '<input name="confirm" type="password" autocomplete="new-password">')}
    </div>
    <label class="check"><input type="checkbox" id="show"> Show passwords</label>
    <p class="err" id="serr" role="alert" hidden></p>
    <button class="btn pri lg" type="submit" id="go">Create account</button></form>
    <p class="login-alt">Already have an account? <a href="/login/{role}">Sign in</a></p>
  </div>
  <p class="entry-foot">{e(PROTOTYPE_NOTE)}</p>
</div>
<script>
const ROLE = {json.dumps(role)}, form = document.getElementById("sf"), go = document.getElementById("go"), er = document.getElementById("serr");
document.getElementById("show").onchange = ev => form.querySelectorAll("[name=password],[name=confirm]").forEach(i => i.type = ev.target.checked ? "text" : "password");
form.querySelectorAll("[name=seller_type]").forEach(r => r.onchange = () => {{ if (r.value === "kitchen" && r.checked) form.querySelector("[name=lane][value=fresh]").checked = true; }});
form.onsubmit = async ev => {{
  ev.preventDefault(); er.hidden = true; form.querySelectorAll(".ferr").forEach(x => x.textContent = ""); form.querySelectorAll("[aria-invalid]").forEach(x => x.removeAttribute("aria-invalid"));
  const fd = new FormData(form), body = {{role: ROLE}};
  for (const k of ["business_name", "seller_type", "contact_name", "email", "phone", "area", "lane", "password", "confirm"]) body[k] = fd.get(k) || "";
  go.disabled = true;
  try {{
    const r = await fetch("/api/auth/register", {{method: "POST", headers: {{"Content-Type": "application/json"}}, body: JSON.stringify(body)}});
    const t = await r.json().catch(() => ({{}}));
    if (r.ok) {{ location.replace("/app"); return; }}
    const es = (t.detail && t.detail.errors) || {{}};
    let first = null;
    for (const k in es) {{ const x = document.getElementById("e-" + k); if (x) x.textContent = es[k]; const i = form.querySelector(`[name=${{k}}]`); if (i) {{ i.setAttribute("aria-invalid", "true"); first = first || i; }} }}
    if (!Object.keys(es).length) {{ er.hidden = false; er.textContent = typeof t.detail === "string" ? t.detail : "Something went wrong. Try again."; }}
    if (first) first.focus();
  }} catch (x) {{ er.hidden = false; er.textContent = "Could not reach the app. Check that it is still running, then try again."; }}
  go.disabled = false;
}};
</script>"""
    return _entry_page(f"Create a {role} account · CIRCULATE", body)


# ---------------- Account page and admin user management ----------------
def _profile(u) -> dict:
    return {"id": u["id"], "role": u["role"], "role_name": ROLE_NAMES[u["role"]], "seller_type": u["seller_type"],
            "seller_type_name": SELLER_TYPES.get(u["seller_type"] or "", ""), "business_id": u["business_id"], "business_name": u["business_name"],
            "contact_name": u["contact_name"], "email": u["email"], "phone": u["phone"] or "", "area": u["area"] or "", "lane": u["lane"],
            "active": bool(u["active"]), "created_at": u["created_at"], "last_login_at": u["last_login_at"]}


@app.get("/api/account")
def my_account(request: Request, me=ANY):
    sess = _session(request)
    if sess is None:
        raise HTTPException(401, "Not signed in.")
    return _profile(sess["user"])


def _admin_only(request: Request) -> dict:
    sess = _session(request)
    if sess is None:
        raise HTTPException(401, "Not signed in.")
    if sess["real"]["role"] != "admin":
        raise HTTPException(403, "Only the CIRCULATE admin can do this.")
    return sess


@app.get("/api/admin/users")
def admin_users(request: Request, me=REAL_ADMIN):
    _admin_only(request)
    with closing(db()) as con:
        rows = con.execute("SELECT * FROM users ORDER BY role='admin' DESC, role, id").fetchall()
    return {"users": [_profile(u) for u in rows], "demo_emails": [d[0] for d in DEMO_USERS]}


class ActiveIn(BaseModel):
    active: bool


@app.post("/api/admin/users/{uid}/active")
def admin_set_active(uid: int, a: ActiveIn, request: Request, me=REAL_ADMIN):
    sess = _admin_only(request)
    if uid == sess["user"]["id"] and not a.active:
        raise HTTPException(422, "You cannot switch off your own admin account.")
    with closing(db()) as con, con:
        n = con.execute("UPDATE users SET active=?, token_version=token_version+? WHERE id=?", (int(a.active), 0 if a.active else 1, uid)).rowcount
    if not n:
        raise HTTPException(404, "No such account.")
    return _profile(_user("id=?", uid))


@app.post("/api/admin/users/{uid}/temp-password")
def admin_temp_password(uid: int, request: Request, me=REAL_ADMIN):
    """A new temporary password, shown once to the admin. Every existing session of that account stops working."""
    _admin_only(request)
    if _user("id=?", uid) is None:
        raise HTTPException(404, "No such account.")
    temp = secrets.token_urlsafe(9)
    salt, h = AUTH.hash_password(temp)
    with closing(db()) as con, con:
        con.execute("UPDATE users SET password_hash=?, salt=?, token_version=token_version+1 WHERE id=?", (h, salt, uid))
    return {"temporary_password": temp, "note": "Give this to the account holder and ask them to change it under Account."}


# ---------------- Collection requests: a seller asks for a pickup; CIRCULATE arranges a hired vehicle ----------------
# Asset-light: CIRCULATE never buys or stores goods. A request only records what the seller asked for and how far the
# CIRCULATE team has got (matched to a buyer, vehicle booked, closed).
COLLECTION_STATUS = {"requested": "Requested", "matched": "Matched to buyer", "vehicle_booked": "Vehicle booked", "closed": "Closed"}
HIRED = "Pickup by a hired vehicle. CIRCULATE never buys or stores goods."


class ItemRef(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    id: str = Field(min_length=1, max_length=200)


class CollectionIn(BaseModel):
    items: list[ItemRef] = Field(default_factory=list, max_length=500)
    pickup_date: date | None = None
    slot: str = ""
    note: str = Field("", max_length=500)


class CollectionStatusIn(BaseModel):
    status: Literal["matched", "vehicle_booked", "closed"]


def _collection_out(r) -> dict:
    items = json.loads(r["items"])
    return {"id": r["id"], "created_at": r["created_at"], "owner": r["owner"], "business_name": r["business_name"], "area": r["area"],
            "lane": r["lane"], "items": items, "item_count": len(items), "weight_kg": r["weight_kg"], "pickup_date": r["pickup_date"],
            "slot": r["slot"], "note": r["note"], "status": r["status"], "status_text": COLLECTION_STATUS[r["status"]],
            "history": json.loads(r["history"]), "label": r["label"], "pickup": HIRED}


def _my_item(me: dict, ref: ItemRef) -> dict | None:
    """One of this seller's own items (its listed stock, or its own sample pieces); None if it is not theirs or not there."""
    try:
        item = next((i for i in _stock(ref.key) if i["id"] == ref.id), None)
    except HTTPException:
        return None
    if item is None or me["admin"]:
        return item
    imp = _parse_import(ref.key)
    mine = imp[1] == me["business_id"] if imp else item["seller_id"] in _my_sample_sellers(me, ref.key)
    return item if mine else None


def _notify_collection(row, origin: str) -> None:
    c = _collection_out(row)
    when = datetime.fromisoformat(c["pickup_date"]).strftime("%d %b %Y").lstrip("0")
    text = MSG.collection_update({"id": c["id"], "business": c["business_name"], "status_text": c["status_text"].lower(),
                                  "items": c["item_count"], "kg": c["weight_kg"], "date": when, "slot": c["slot"],
                                  "link": f"{origin}/app#seller/stock/{c['lane']}", "label": c["label"]})
    notify("collection", text, ["seller"], [c["business_name"]], item_id=f"C{c['id']}:{c['status']}")


@app.get("/api/collections")
def collections(me=SELLER):
    """This seller's collection requests, newest first. The admin sees every seller's."""
    with closing(db()) as con:
        rows = con.execute("SELECT * FROM collection_requests ORDER BY id DESC").fetchall()
    return {"requests": [_collection_out(r) for r in rows if me["admin"] or r["owner"] == me["business_id"]],
            "slots": SLOTS, "statuses": COLLECTION_STATUS, "pickup": HIRED}


@app.post("/api/collections")
def request_collection(c: CollectionIn, request: Request, me=SELLER):
    """Ask for a pickup of some of your own items, on a preferred date and slot. Status starts as requested."""
    errors, items = {}, []
    for ref in c.items:
        it = _my_item(me, ref)
        if it is None:
            errors["items"] = f"{ref.id} is not one of your items. Pick from My stock."
            break
        if all(not (x["key"] == ref.key and x["id"] == ref.id) for x in items):
            items.append({"key": ref.key, "id": it["id"], "description": it["description"], "kg": it["kg"], "label": it["label"]})
    if not c.items:
        errors["items"] = "Pick at least one item to collect."
    if c.pickup_date is None:
        errors["pickup_date"] = "Pick a preferred date."
    elif c.pickup_date < datetime.now().date():
        errors["pickup_date"] = "Pick today or a later date."
    if c.slot not in SLOTS:
        errors["slot"] = "Choose a time slot."
    if errors:
        raise HTTPException(422, {"message": "Some fields need fixing. Nothing was requested.", "errors": errors})
    a, now = me["account"], _now()
    label = "measured" if all(x["label"] == "measured" for x in items) else "sample"
    with closing(db()) as con, con:
        cur = con.execute("INSERT INTO collection_requests (created_at, owner, business_name, area, lane, items, weight_kg, pickup_date, slot,"
                          " note, status, history, label) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (now, me["business_id"], me["name"], a.get("area", ""), a.get("lane") or "durable", json.dumps(items),
                           round(sum(x["kg"] for x in items), 2), c.pickup_date.isoformat(), c.slot, c.note.strip(), "requested",
                           json.dumps([{"at": now, "status": "requested"}]), label))
        row = con.execute("SELECT * FROM collection_requests WHERE id=?", (cur.lastrowid,)).fetchone()
    _safely(_notify_collection, row, _origin(request))
    return _collection_out(row)


@app.post("/api/collections/{cid}/status")
def collection_status(cid: int, s: CollectionStatusIn, request: Request, me=ADMIN):
    """The CIRCULATE team records progress: matched to a buyer, vehicle booked (a hired vehicle), or closed."""
    now = _now()
    with closing(db()) as con, con:
        row = con.execute("SELECT * FROM collection_requests WHERE id=?", (cid,)).fetchone()
        if row is None:
            raise HTTPException(404, "no such collection request")
        con.execute("UPDATE collection_requests SET status=?, history=? WHERE id=?",
                    (s.status, json.dumps(json.loads(row["history"]) + [{"at": now, "status": s.status}]), cid))
        row = con.execute("SELECT * FROM collection_requests WHERE id=?", (cid,)).fetchone()
    _safely(_notify_collection, row, _origin(request))
    return _collection_out(row)


# ---------------- Add stock: direct links to the four pages ----------------
ADD_PAGES = {"": "add", "/csv": "add-csv", "/sync": "add-sync", "/manual": "add-manual"}


def _add_redirect(request: Request, tail: str) -> RedirectResponse:
    """/seller/add, /seller/add/csv, /seller/add/sync and /seller/add/manual open the page inside the app.
    Anyone who is not a seller (or the admin) is sent to their own home; no session goes to the start page."""
    sess = _session(request)
    if sess is None:
        return RedirectResponse("/", status_code=307)
    a = sess["view"] or sess["real"]
    if a["role"] == "buyer":
        return RedirectResponse("/app", status_code=307)
    return RedirectResponse(f"/app#{a['role']}/{ADD_PAGES[tail]}/{a['lane']}", status_code=307)


@app.get("/seller/add")
def seller_add(request: Request):
    return _add_redirect(request, "")


@app.get("/seller/add/{page}")
def seller_add_page(page: str, request: Request):
    if "/" + page not in ADD_PAGES:
        raise HTTPException(404, "There is no such Add stock page. Use csv, sync or manual.")
    return _add_redirect(request, "/" + page)


@app.get("/seller/connect")
def seller_connect_old(request: Request):
    return _add_redirect(request, "")  # the old combined Connect screen
