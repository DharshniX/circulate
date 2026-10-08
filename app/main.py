"""
CIRCULATE web API (FastAPI) + demo page.

Run:   python -m uvicorn app.main:app --reload
Open:  http://127.0.0.1:8000

Engine endpoints are stateless: send the data, get the answer. Only deals are
stored (SQLite file circulate.db), because a deal has a life cycle:
agreed -> dispatched -> delivered (with evidence and buyer confirmation).
"""
from __future__ import annotations

import io
import json
import re
import sqlite3
import asyncio
import os
import threading
from contextlib import asynccontextmanager, closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
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
    """Start the auto-sync folder checker with the server (set CIRCULATE_AUTOSYNC=0 to switch it off)."""
    task = asyncio.create_task(_autosync_loop()) if os.environ.get("CIRCULATE_AUTOSYNC", "1") != "0" else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="CIRCULATE", version="0.2", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, kind TEXT, counterfactual TEXT,
        demand_id TEXT, buyer_name TEXT, sellers TEXT, units REAL, unit_name TEXT, kg_in_units REAL,
        kg_shipped REAL, route_km REAL, value_above_salvage REAL, buyer_utilisation REAL,
        status TEXT, is_simulated INTEGER, evidence TEXT, buyer_confirmed INTEGER, history TEXT)""")
    have = {r[1] for r in con.execute("PRAGMA table_info(deals)")}
    for col, typ in (("scenario", "TEXT"), ("rate_multiplier", "REAL"), ("booking", "TEXT")):
        if col not in have:  # older databases: add the column, keep every row
            con.execute(f"ALTER TABLE deals ADD COLUMN {col} {typ}")
    con.execute("""CREATE TABLE IF NOT EXISTS requirements (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, lane TEXT, kind TEXT, scenario TEXT, demand TEXT,
        needed_by TEXT, standing INTEGER, status TEXT, deal_id INTEGER, history TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS reservations (
        req_id INTEGER PRIMARY KEY, seller TEXT, parts INTEGER, at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS whatsapp_contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, business TEXT, role TEXT, name TEXT, phone TEXT, alerts TEXT,
        consent_at TEXT, active INTEGER, created_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, event TEXT, deal_id INTEGER, item_id TEXT, contact_id INTEGER,
        text TEXT, link TEXT, status TEXT, opened_at TEXT, sent_at TEXT)""")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_notifications ON notifications(event, IFNULL(deal_id, 0), IFNULL(item_id, ''), contact_id)")
    con.execute("""CREATE TABLE IF NOT EXISTS sync_folders (
        lane TEXT PRIMARY KEY, path TEXT, enabled INTEGER, measured INTEGER, days_unused_limit INTEGER, hours_left_limit REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS sync_seen (
        lane TEXT, file TEXT, fingerprint TEXT, imported_at TEXT, PRIMARY KEY (lane, file))""")
    con.execute("""CREATE TABLE IF NOT EXISTS sync_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, lane TEXT, folder TEXT, file TEXT, status TEXT, rows_read INTEGER,
        surplus INTEGER, rejected INTEGER, listed INTEGER, label TEXT, message TEXT, details TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS imported_stock (
        lane TEXT, item_id TEXT, item TEXT, rules TEXT, label TEXT, listed_at TEXT, PRIMARY KEY (lane, item_id))""")
    if "source" not in {r[1] for r in con.execute("PRAGMA table_info(imported_stock)")}:
        con.execute("ALTER TABLE imported_stock ADD COLUMN source TEXT")  # older rows read as CSV imports
    return con


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


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "app.html")


@app.get("/legacy")
def legacy():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/engines")
def engines():
    return [{"kind": e.kind, "unit_name": e.unit_name, "time_unit": e.time_unit} for e in REGISTRY.values()]


@app.get("/api/scenarios")
def scenarios():
    return [{"key": k, "title": v["title"], "engine": v["engine"]} for k, v in SC.SCENARIOS.items()]


@app.get("/api/scenario/{key}")
def scenario(key: str):
    if key not in SC.SCENARIOS:
        raise HTTPException(404, f"unknown scenario {key}")
    d = SC.dataset(key)
    body = Dataset(sellers=d["sellers"], resources=d["resources"], demands=d["demands"],
                   transport=d["transport"], settings=d["settings"]).model_dump(mode="json")
    return {**body, **{k: d[k] for k in ("scenario", "title", "engine", "plan_seller", "metric_word")}}


@app.get("/api/gapfill/demo")
def gapfill_demo():
    """The sample job and standing orders, plus standing orders posted by buyers in this demo."""
    g = SC.gapfill_demo()
    g["orders"] += _posted_standing()
    return g


@app.post("/api/gapfill")
def api_gapfill(req: GapFillRequest, request: Request, record: bool = False, seller: str = "Your workshop"):
    """record=true (the Sold before it's cut screen): posted requirements in the plan are marked reserved
    by this seller; posted ones declined or left out go back to open."""
    try:
        plan = plan_gap_fill(req)
    except ValueError as e:
        _bad(e)
    if record:
        _record_reservations(req, plan, seller)
        _safely(_notify_gapfill, plan, {o.id: o for o in req.orders}, _origin(request), seller)
    return plan


@app.post("/api/fulfil")
def api_fulfil(req: FulfilRequest):
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
def api_seller_plan(req: SellerPlanRequest):
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
def api_plan(req: PlanRequest):
    try:
        return plan(req)
    except ValueError as e:
        _bad(e)


@app.post("/api/deals")
def create_deal(d: NewDeal, request: Request):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as con, con:
        cur = con.execute(
            "INSERT INTO deals (created_at, kind, counterfactual, demand_id, buyer_name, sellers, units, unit_name,"
            " kg_in_units, kg_shipped, route_km, value_above_salvage, buyer_utilisation, status, is_simulated,"
            " evidence, buyer_confirmed, history, scenario, rate_multiplier) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, d.kind, d.counterfactual, d.demand_id, d.buyer_name, json.dumps(d.sellers), d.units, d.unit_name,
             d.kg_in_units, d.kg_shipped, d.route_km, d.value_above_salvage, d.buyer_utilisation, "agreed",
             int(d.is_simulated), "[]", 0, json.dumps([{"at": now, "status": "agreed"}]), d.scenario, d.rate_multiplier))
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
def advance(deal_id: int, a: Advance, request: Request):
    order = {"agreed": 0, "dispatched": 1, "delivered": 2}
    with closing(db()) as con, con:
        row = con.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
        if row is None:
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
def ledger(f: ImpactFactors):
    with closing(db()) as con:
        rows = con.execute("SELECT * FROM deals ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        imp = compute(DealImpactInput(
            kind=r["kind"], counterfactual=r["counterfactual"], units=r["units"], unit_name=r["unit_name"],
            kg_in_units=r["kg_in_units"], kg_shipped=r["kg_shipped"], route_km=r["route_km"],
            value_above_salvage=r["value_above_salvage"], buyer_utilisation=r["buyer_utilisation"],
            is_simulated=bool(r["is_simulated"]), status=r["status"], evidence=json.loads(r["evidence"]),
            buyer_confirmed=bool(r["buyer_confirmed"]), ef_avoided=f.ef_avoided, ef_truck=f.ef_truck))
        out.append({"id": r["id"], "kind": r["kind"], "buyer_name": r["buyer_name"], "sellers": json.loads(r["sellers"]),
                    "status": r["status"], "is_simulated": bool(r["is_simulated"]),
                    "history": json.loads(r["history"]), "impact": imp})
    return {"deals": out}


@app.post("/api/reset")
def reset():
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
        return f"{p.length_mm:g} x {p.width_mm:g} x {p.thickness_mm:g} mm {M_name(p.material)} {p.grade}, {p.condition}{extra}"
    if p.kind == "linear":
        return f"{p.profile}, {p.length_mm:g} mm, {M_name(p.material)} {p.grade}, {p.condition}"
    return f"{p.product}, {p.quantity_kg:g} kg, {p.storage}, {p.hours_left:g} h left"


def M_name(key: str) -> str:
    from engine import materials as M
    return M.get(key).name


IMPORT_KEYS = {"import_durable": "durable", "import_fresh": "fresh"}


def _imported_stock(key: str) -> list[dict]:
    from engine.models import PerishableLot
    lane = IMPORT_KEYS[key]
    with closing(db()) as con:
        rows = con.execute("SELECT * FROM imported_stock WHERE lane=? ORDER BY listed_at, item_id", (lane,)).fetchall()
    out = []
    for r in rows:
        p = (IMP.ImportedSheet if lane == "durable" else PerishableLot).model_validate_json(r["item"])
        item = {"id": p.id, "seller_id": p.seller_id, "seller_name": p.seller_id, "kind": p.kind,
                "description": _describe(p), "kg": round(REGISTRY[p.kind].resource_kg(p, Settings()), 2),
                "scrap_value": None, "scrap_price_per_kg": None, "label": r["label"], "status": "available",
                "passport": f"/passport/{key}/{p.id}", "rules": json.loads(r["rules"]), "source": r["source"] or IMP.SOURCE,
                "listed_at": r["listed_at"]}
        if p.kind == "perishable":
            item["hours_left"] = p.hours_left
            item["urgent"] = True
        else:
            item["days_unused"] = p.days_unused
        out.append(item)
    return out


def _stock(key: str) -> list[dict]:
    if key in IMPORT_KEYS:
        return _imported_stock(key)
    if key not in SC.SCENARIOS:
        raise HTTPException(404, f"unknown scenario {key}")
    d = SC.dataset(key)
    names = {s.id: s.name for s in d["sellers"]}
    sellers = {s.id: s for s in d["sellers"]}
    out = []
    for p in d["resources"]:
        eng = REGISTRY[p.kind]
        kg = eng.resource_kg(p, d["settings"])
        price = sellers[p.seller_id].salvage_for(eng.salvage_key(p)).price_per_kg
        item = {"id": p.id, "seller_id": p.seller_id, "seller_name": names[p.seller_id], "kind": p.kind,
                "description": _describe(p), "kg": round(kg, 2), "scrap_value": round(kg * price, 2),
                "scrap_price_per_kg": price, "label": p.label, "status": "available",
                "passport": f"/passport/{key}/{p.id}"}
        if p.kind == "perishable":
            item["hours_left"] = p.hours_left
        out.append(item)
    return out


@app.get("/api/stock/{key}")
def stock(key: str, request: Request):
    if key in ("food", "import_fresh"):
        _safely(_sweep_fresh, _origin(request))
    return {"scenario": key, "items": _stock(key)}


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
    rows = [("Passport ID", f"{key}:{item['id']}"), ("Seller", item["seller_name"]), ("What it is", item["description"]),
            ("Weight", f"{item['kg']} kg"),
            ("Scrap value", "No scrap price entered yet. Add a dated quote before showing one." if item["scrap_value"] is None
             else f"Rs {item['scrap_value']:,.0f} at Rs {item['scrap_price_per_kg']}/kg"),
            ("Status", item["status"]), ("Data", item["label"].title())]
    if "source" in item:
        rows += [("Source", item["source"]), ("Found as surplus by", "; ".join(item["rules"]))]
    if "hours_left" in item:
        rows.insert(3, ("Shelf life left", f"{item['hours_left']:g} hours"))
    dl = "".join(f"<dt>{e(a)}</dt><dd>{e(str(b))}</dd>" for a, b in rows)
    qr_url = f"/api/qr.svg?data={_origin(request)}/passport/{key}/{rid}"
    body = (f'<div class="card"><h1>Offcut passport</h1><p><span class="chip">{e(item["label"])} data</span></p><dl>{dl}</dl></div>'
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
def data_quality():
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
def sim_summary():
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
def import_example(lane: Literal["durable", "fresh"]):
    name = f"{lane}_stock_example.csv"
    return FileResponse(EXAMPLES / name, media_type="text/csv", filename=name)


@app.post("/api/import/preview")
def import_preview(req: ImportRequest):
    """Check every row and sort the good ones into surplus and working stock. Nothing is stored."""
    return _run_import(req)


def _list_surplus(lane: str, result: dict, label: str, source: str = IMP.SOURCE) -> int:
    """Put an import's surplus on the marketplace: stock board, passports and QR codes. Same item id = updated."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as con, con:
        for s in result["surplus"]:
            con.execute("INSERT OR REPLACE INTO imported_stock (lane, item_id, item, rules, label, listed_at, source) VALUES (?,?,?,?,?,?,?)",
                        (lane, s["item"]["id"], json.dumps(s["item"]), json.dumps(s["rules"]), label, now, source))
    return len(result["surplus"])


@app.post("/api/import/list")
def import_list(req: ListRequest):
    """List the surplus items on the marketplace (stock board, passports, QR codes)."""
    label = "measured" if req.measured else "sample"
    r = _run_import(req, label)
    if not r["surplus"]:
        _bad(ValueError("Nothing was found as surplus, so nothing was listed. Lower the thresholds or check the file."))
    _list_surplus(req.lane, r, label)
    key = f"import_{req.lane}"
    return {"listed": len(r["surplus"]), "label": label, "stock_key": key, "items": _stock(key)}


@app.post("/api/import/clear")
def import_clear(lane: Literal["durable", "fresh"]):
    with closing(db()) as con, con:
        n = con.execute("DELETE FROM imported_stock WHERE lane=?", (lane,)).rowcount
    return {"removed": n}


@app.post("/api/import/cutting-job")
def import_cutting_job(j: CuttingJob):
    """Turn the next cutting job into a Sold-before-it's-cut request, checked by the engine before it is sent."""
    g = gapfill_demo()
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


@app.post("/api/photo/{key}/{rid}")
async def upload_photo(key: str, rid: str, request: Request):
    """The seller's own photo of this item. The body is the image file itself (JPEG, PNG or WebP)."""
    _item_or_404(key, rid)
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
def delete_photo(key: str, rid: str):
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


def _browse_scenario(key: str) -> tuple[list[dict], str]:
    d = SC.dataset(key)
    cfg, sellers, demands = d["settings"], {s.id: s for s in d["sellers"]}, d["demands"]
    buyer = demands[0]  # distances are measured from the first open order's buyer in this sample cluster
    out = []
    for p in d["resources"]:
        eng, seller = REGISTRY[p.kind], sellers[p.seller_id]
        kg = eng.resource_kg(p, cfg)
        offer = seller.salvage_for(eng.salvage_key(p))
        alt = {"value": round(kg * offer.price_per_kg, 2), "price_per_kg": offer.price_per_kg, "label": offer.label,
               "quoted_on": offer.quoted_on.isoformat() if offer.quoted_on else None}
        item = {**p.model_dump(mode="json"), "key": key, "seller_name": seller.name, "kg": round(kg, 2),
                "distance_km": round(_dist((seller.x_km, seller.y_km), (buyer.x_km, buyer.y_km)), 1),
                "passport": f"/passport/{key}/{p.id}", "photo": _photo_url(key, p.id)}
        if p.kind == "perishable":
            item["disposal"] = alt
        else:
            item["material_name"] = M_name(p.material)
            item["floor"] = alt
            same = [x for x in demands if x.kind == p.kind and x.material == p.material]
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
        item["order_reason"] = None if fits else (why or "No open order in this cluster takes this kind of stock.")
        out.append(item)
    return out, buyer.buyer_name


def _browse_imported(lane: str) -> list[dict]:
    from engine.models import PerishableLot
    key = f"import_{lane}"
    with closing(db()) as con:
        rows = con.execute("SELECT * FROM imported_stock WHERE lane=? ORDER BY listed_at, item_id", (lane,)).fetchall()
    out = []
    for r in rows:
        p = (IMP.ImportedSheet if lane == "durable" else PerishableLot).model_validate_json(r["item"])
        item = {**p.model_dump(mode="json"), "key": key, "label": r["label"], "seller_name": p.seller_id,
                "kg": round(REGISTRY[p.kind].resource_kg(p, Settings()), 2), "distance_km": None,
                "passport": f"/passport/{key}/{p.id}", "photo": _photo_url(key, p.id), "rules": json.loads(r["rules"]),
                "source": r["source"] or IMP.SOURCE, "match_seller": OWN_ID, **_own_order(p)}
        if p.kind == "perishable":
            item["disposal"] = None
        else:
            item.update(material_name=M_name(p.material), floor=None, ceiling=None)
        out.append(item)
    return out


@app.get("/api/browse/{lane}")
def browse(lane: Literal["durable", "fresh"], request: Request):
    """Every stock item in this lane as a marketplace listing: sample scenarios plus stock listed from a CSV import."""
    items, buyers = [], {}
    for key, sc in SC.SCENARIOS.items():
        if (sc["engine"] == "perishable") == (lane == "fresh"):
            rows, buyers[key] = _browse_scenario(key)
            items += rows
    items += _browse_imported(lane)
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


def _logistics(row, now: datetime | None = None) -> dict:
    """Re-run the engine on exactly the deal's sellers: same pickup order, vehicle and transport cost as when it was agreed."""
    key = row["scenario"] if row["scenario"] in SC.SCENARIOS else next(
        (k for k in SC.SCENARIOS if any(d.id == row["demand_id"] for d in SC.dataset(k)["demands"])), None)
    if key is None:
        raise HTTPException(422, f"deal {row['id']} refers to order {row['demand_id']}, which is in no scenario")
    d = _dataset_with_own(key)
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
           "note": SIMULATED, "warnings": []}
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
def deal_logistics(deal_id: int, now: datetime | None = None):
    """Map, vehicle, schedule and collection list for one deal. `now` is only for testing the clock."""
    return _logistics(_deal_row(deal_id), now)


@app.post("/api/deals/{deal_id}/booking")
def book_pickup(deal_id: int, bk: Booking, request: Request):
    row = _deal_row(deal_id)
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


@app.get("/deals/{deal_id}/pickup-sheet", response_class=HTMLResponse)
def pickup_sheet(deal_id: int):
    lg, e = _logistics(_deal_row(deal_id)), _html.escape
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
            f'<p class="note">Positions are on a simple km grid from sample data; follow the real addresses. Contacts are left blank to fill in.</p></div>')
    return _doc(f"Pickup sheet {deal_id}", body)


@app.get("/deals/{deal_id}/challan", response_class=HTMLResponse)
def delivery_challan(deal_id: int):
    lg, e = _logistics(_deal_row(deal_id)), _html.escape
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


def _folder_config(lane: str) -> dict:
    with closing(db()) as con:
        row = con.execute("SELECT * FROM sync_folders WHERE lane=?", (lane,)).fetchone()
    if row is None:
        return {"lane": lane, "path": str(_default_folder(lane)), "enabled": False, "measured": False,
                "days_unused_limit": 30, "hours_left_limit": 24.0, "saved": False}
    return {"lane": lane, "path": row["path"], "enabled": bool(row["enabled"]), "measured": bool(row["measured"]),
            "days_unused_limit": row["days_unused_limit"], "hours_left_limit": row["hours_left_limit"], "saved": True}


def _log(lane, folder, file, status, rows_read=None, surplus=None, rejected=None, listed=None, label=None, message="", details=None):
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as con, con:
        cur = con.execute("INSERT INTO sync_log (at, lane, folder, file, status, rows_read, surplus, rejected, listed, label, message, details)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (at, lane, folder, file, status, rows_read, surplus, rejected, listed,
                                                                 label, message, json.dumps(details or [])))
    return {"id": cur.lastrowid, "at": at, "lane": lane, "folder": folder, "file": file, "status": status, "rows_read": rows_read,
            "surplus": surplus, "rejected": rejected, "listed": listed, "label": label, "message": message, "details": details or []}


def _sync_log(lane: str, limit: int = 20) -> list[dict]:
    with closing(db()) as con:
        rows = con.execute("SELECT * FROM sync_log WHERE lane=? ORDER BY id DESC LIMIT ?", (lane, limit)).fetchall()
    return [{**dict(r), "details": json.loads(r["details"] or "[]")} for r in rows]


def _sync_folder(lane: str, background: bool = False) -> list[dict]:
    """Import every new or changed .csv in the lane's folder. Unchanged files are never imported twice.
    Files are only read; nothing in the folder is moved, changed or deleted."""
    cfg = _folder_config(lane)
    folder = Path(cfg["path"])
    if folder == _default_folder(lane):
        folder.mkdir(parents=True, exist_ok=True)  # only ever creates the project's own default folder
    conn = CN.FolderSync(folder, SYNC_MIN_AGE_SECONDS)
    label = "measured" if cfg["measured"] else "sample"
    with _sync_lock:
        try:
            exports = conn.stock_csv(lane)
        except (ValueError, OSError) as e:
            last = _sync_log(lane, 1)
            if background and last and last[0]["status"] == "error" and last[0]["message"] == str(e):
                return []  # the same folder problem is logged once, not every minute
            return [_log(lane, str(folder), "", "error", message=str(e))]
        with closing(db()) as con:
            seen = {r["file"]: r["fingerprint"] for r in con.execute("SELECT file, fingerprint FROM sync_seen WHERE lane=?", (lane,))}
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
                             "rejected": len(r["rejected"]), "listed": _list_surplus(lane, r, label), "label": label,
                             "message": "Changed file, imported again." if again else "New file.", "details": r["rejected"]}
                except ValueError as e:
                    entry = {"status": "error", "message": str(e)}
            with closing(db()) as con, con:
                con.execute("INSERT OR REPLACE INTO sync_seen (lane, file, fingerprint, imported_at) VALUES (?,?,?,?)",
                            (lane, where, ex.fingerprint, datetime.now(timezone.utc).isoformat(timespec="seconds")))
            entries.append(_log(lane, str(folder), ex.name, **entry))
    return entries


def sync_all_enabled() -> dict:
    """One background pass: every lane whose folder sync is switched on."""
    return {lane: _sync_folder(lane, background=True) for lane in ("durable", "fresh") if _folder_config(lane)["enabled"]}


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
def connectors():
    folders = {}
    for lane in ("durable", "fresh"):
        cfg = _folder_config(lane)
        last = _sync_log(lane, 1)
        folders[lane] = {**cfg, "default_path": str(_default_folder(lane)), "last": last[0] if last else None}
    return {"connectors": [c.info() for c in CN.catalogue()], "works_with": WORKS_WITH, "every_seconds": SYNC_EVERY_SECONDS,
            "folder": folders}


@app.put("/api/connectors/folder/{lane}")
def save_folder(lane: Literal["durable", "fresh"], cfg: FolderConfig):
    folder = Path(cfg.path.strip().strip('"')).expanduser()
    if not folder.is_absolute():
        folder = ROOT.parent / folder
    if folder == _default_folder(lane):
        folder.mkdir(parents=True, exist_ok=True)
    problem = CN.FolderSync(folder).check()
    if problem:
        _bad(ValueError(problem))
    with closing(db()) as con, con:
        con.execute("INSERT OR REPLACE INTO sync_folders (lane, path, enabled, measured, days_unused_limit, hours_left_limit) VALUES (?,?,?,?,?,?)",
                    (lane, str(folder), int(cfg.enabled), int(cfg.measured), cfg.days_unused_limit, cfg.hours_left_limit))
    return _folder_config(lane)


@app.post("/api/connectors/folder/{lane}/sync")
def sync_now(lane: Literal["durable", "fresh"]):
    """Check the folder at once (the same check the background task runs every 60 seconds)."""
    return {"entries": _sync_folder(lane), "log": _sync_log(lane)}


@app.get("/api/connectors/folder/{lane}/log")
def sync_log(lane: Literal["durable", "fresh"], limit: int = 20):
    return {"log": _sync_log(lane, max(1, min(limit, 200)))}


@app.post("/api/connectors/{key}/sync")
def sync_connector(key: str, lane: Literal["durable", "fresh"] = "durable"):
    c = next((x for x in CN.catalogue() if x.key == key), None)
    if c is None:
        raise HTTPException(404, f"unknown connector {key}")
    if key == "folder_sync":
        return sync_now(lane)
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
def manual_options():
    from typing import get_args
    from engine import materials as MAT
    from engine.models import Condition, FoodCategory, Storage
    return {"materials": [{"key": m.key, "name": m.name} for m in MAT.MATERIALS.values()], "conditions": list(get_args(Condition)),
            "categories": list(get_args(FoodCategory)), "storage": list(get_args(Storage)), "source": IMP.MANUAL_SOURCE}


@app.post("/api/manual/{lane}")
def manual_entry(lane: Literal["durable", "fresh"], m: ManualItem, now: datetime | None = None):
    """Check one hand-typed item with the importer, apply the surplus rules, and list it if it is surplus
    (or if the seller asks to list it anyway). An invalid item is never listed. `now` is only for tests."""
    fields = {k: v for k, v in m.fields.items() if k in IMP.COLUMNS[lane]}
    fields["item_id"] = str(fields.get("item_id") or "").strip() or _manual_id()
    fields["seller_id"] = str(fields.get("seller_id") or "").strip() or ("My workshop" if lane == "durable" else "My kitchen")
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
    key = f"import_{lane}"
    _list_surplus(lane, {"surplus": [{"item": r["item"], "rules": rules}]}, label, IMP.MANUAL_SOURCE)
    out.update(listed_rules=rules, stock_key=key, passport=f"/passport/{key}/{r['item']['id']}",
               photo=_write_photo(key, r["item"]["id"], *photo) if photo else None)
    return out


# ---------------- Your own stock in matching, and "What can this become?" ----------------
from engine import become as BEC
from engine.gapfill import GapFillPrices, StandingOrder
from engine.models import FlatPiece, PerishableLot

OWN_ID, OWN_NAME = "YOU", "Your workshop"


def _own_seller(d: dict) -> Seller:
    """The seller's own business, placed at the centre of the cluster's sellers, with the cluster's sample scrap prices."""
    xs, ys = [x.x_km for x in d["sellers"]], [x.y_km for x in d["sellers"]]
    salvage = {}
    for x in d["sellers"]:
        for o in x.salvage:
            salvage.setdefault(o.applies_to, o)
    return Seller(id=OWN_ID, name=OWN_NAME, x_km=round(sum(xs) / len(xs), 2), y_km=round(sum(ys) / len(ys), 2),
                  salvage=list(salvage.values()), fssai_licensed=False, label="sample")


def _own_items(lane: str) -> list:
    with closing(db()) as con:
        rows = con.execute("SELECT item, label FROM imported_stock WHERE lane=? ORDER BY listed_at, item_id", (lane,)).fetchall()
    out = []
    for r in rows:
        data = {**json.loads(r["item"]), "label": r["label"] if r["label"] in ("measured", "sample") else "sample"}
        data.pop("days_unused", None)
        out.append(FlatPiece(**data) if lane == "durable" else PerishableLot(**data))
    return out


def _own_resources(key: str) -> list:
    """Imported, auto-synced and hand-entered items as "Your workshop" stock for one scenario (ids prefixed YOU-)."""
    eng = SC.SCENARIOS[key]["engine"]
    lane = "fresh" if eng == "perishable" else "durable"
    return [x.model_copy(update={"id": f"{OWN_ID}-{x.id}", "seller_id": OWN_ID}) for x in _own_items(lane) if x.kind == eng]


def _dataset_with_own(key: str) -> dict:
    d = SC.dataset(key)
    d["demands"] = d["demands"] + [r["demand_obj"] for r in _requirements(scenario=key)]
    own = _own_resources(key)
    if own:
        d["sellers"] = d["sellers"] + [_own_seller(d)]
        d["resources"] = d["resources"] + own
    return d


@app.get("/api/own-stock/{key}")
def own_stock(key: str):
    """Your own listed items as stock from "Your workshop", to add to a scenario before matching. Empty if there are none."""
    if key not in SC.SCENARIOS:
        raise HTTPException(404, f"unknown scenario {key}")
    d = SC.dataset(key)
    own = _own_resources(key)
    return {"seller": _own_seller(d).model_dump(mode="json") if own else None,
            "resources": [x.model_dump(mode="json") for x in own],
            "note": "Your own items, placed at the centre of the cluster. Scrap prices are the cluster's sample prices."}


def _own_order(p) -> dict:
    """For an own item on Browse surplus: the first open order it fits, matched as Your workshop, or the reason it fits none."""
    eng, why = REGISTRY[p.kind], None
    for key, sc in SC.SCENARIOS.items():
        if sc["engine"] != p.kind:
            continue
        d = SC.dataset(key)
        seller = _own_seller(d)
        for i, dm in enumerate(d["demands"]):
            if dm.kind != p.kind or not eng.relevant(p, dm):
                continue
            reason = eng.check(p, dm, seller, d["settings"])
            if reason is None:
                return {"order": {"scenario": key, "index": i, "demand_id": dm.id, "buyer_name": dm.buyer_name}, "order_reason": None,
                        "distance_km": round(_dist((seller.x_km, seller.y_km), (d["demands"][0].x_km, d["demands"][0].y_km)), 1)}
            why = why or f"{dm.buyer_name}: {reason}"
    return {"order": None, "order_reason": why or "No open order in this lane takes this kind of stock."}


def _gap_standing() -> tuple[list, GapFillPrices]:
    g = gapfill_demo()
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
    if key in IMPORT_KEYS:
        lane = IMPORT_KEYS[key]
        res = next((x for x in _own_items(lane) if x.id == rid), None)
        if res is None:
            raise HTTPException(404, "no such item")
        scen = [k for k, v in SC.SCENARIOS.items() if v["engine"] == res.kind]
        own = True
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
    for k in scen:
        d = SC.dataset(k)
        all_demands += d["demands"]
        seller = _own_seller(d) if own else next(x for x in d["sellers"] if x.id == res.seller_id)
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
    merged.update(key=key, seller_name=OWN_NAME if own else seller.name, own=own,
                  note="Prices are sample data. Open orders and standing orders are sample orders; common parts have no buyer yet.")
    if res.kind == "flat":
        merged["piece"] = {"length_mm": res.length_mm, "width_mm": res.width_mm, "thickness_mm": res.thickness_mm,
                           "defects": [x.model_dump() for x in res.defects]}
    elif res.kind == "linear":
        merged["piece"] = {"length_mm": res.length_mm}
    return merged


@app.get("/api/become/{key}/{rid}")
def what_it_can_become(key: str, rid: str):
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


def _requirements(lane: str | None = None, scenario: str | None = None, include_closed: bool = False) -> list[dict]:
    q, args = "SELECT * FROM requirements WHERE 1=1", []
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
                    "demand": dem.model_dump(mode="json"), "demand_obj": dem, "note": POSTED_NOTE})
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
def requirement_options():
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
                           "sample_price": _sample_price("flat", m.key)} for m in MAT.MATERIALS.values()],
            "conditions": list(get_args(Condition)), "default_conditions": ["new", "good", "surface_rust"],
            "profiles": [{"profile": pr, "mass_per_m_kg": kg, "material": mat, "sample_price": _sample_price("linear", mat)} for pr, kg, mat in profiles],
            "bar_grades": sorted({g for x in bars["demands"] for g in x.accepted_grades} | {x.grade.upper() for x in bars["resources"]}),
            "categories": list(get_args(FoodCategory)), "storage": list(get_args(Storage)),
            "buyer_types": [{"key": "business", "name": "Food business"}, {"key": "hostel", "name": "Hostel mess"},
                            {"key": "food_bank", "name": "Food bank (donation)"}],
            "food_price": _sample_price("perishable", None, "food_business"),
            "centre": {k: _centre(k) for k in SC.SCENARIOS}, "note": POSTED_NOTE}


@app.post("/api/requirements")
def post_requirement(r: RequirementIn, request: Request):
    """Check a buyer's requirement with the demand models and save it (as a standing order if asked)."""
    demand, key, errors = _build_demand(r)
    if errors:
        raise HTTPException(422, {"message": "Some fields need fixing. Nothing was saved.", "errors": errors})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as con, con:
        cur = con.execute("INSERT INTO requirements (created_at, lane, kind, scenario, demand, needed_by, standing, status, history)"
                          " VALUES (?,?,?,?,?,?,?,?,?)", (now, r.lane, r.kind, key, "{}", r.needed_by.isoformat() if r.needed_by else None,
                                                           int(r.standing), "open", json.dumps([{"at": now, "status": "open"}])))
        rid = cur.lastrowid
        demand = demand.model_copy(update={"id": f"R{rid}"})
        con.execute("UPDATE requirements SET demand=? WHERE id=?", (demand.model_dump_json(), rid))
    _safely(_notify_requirement_fit, demand, key, r.standing, _origin(request))
    return _public(next(x for x in _requirements(include_closed=True) if x["id"] == rid))


@app.get("/api/requirements")
def list_requirements(lane: Literal["durable", "fresh"] | None = None):
    return {"requirements": [_public(r) for r in _requirements(lane=lane, include_closed=True)]}


@app.get("/api/requirements/demands/{key}")
def requirement_demands(key: str):
    """The buyer's own posted requirements for Order and match (newest first, closed ones left out)."""
    if key not in SC.SCENARIOS:
        raise HTTPException(404, f"unknown scenario {key}")
    return {"demands": [{**r["demand"], "requirement_id": r["id"], "status": r["status"]} for r in _requirements(scenario=key)]}


@app.post("/api/requirements/{req_id}/close")
def close_requirement(req_id: int):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as con, con:
        row = con.execute("SELECT history FROM requirements WHERE id=?", (req_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "no such requirement")
        con.execute("UPDATE requirements SET status='closed', history=? WHERE id=?",
                    (json.dumps(json.loads(row["history"]) + [{"at": now, "status": "closed"}]), req_id))
        con.execute("DELETE FROM reservations WHERE req_id=?", (req_id,))
    return {"id": req_id, "status": "closed"}


# ---------------- WhatsApp alerts: prepared automatically, sent by a person with one click ----------------
# Nothing is sent by CIRCULATE. A notification is a ready message and a wa.me link; a person opens it and
# presses send in WhatsApp. Statuses only record what that person did: opened, marked as sent, skipped.
ALERT_TYPES = {"new_match": "New matching order", "order_accepted": "Order accepted", "pickup_booked": "Pickup booked",
               "delivered": "Delivered", "fresh": "Fresh food near its use-by"}
CONTACT_ROLES = ("seller", "buyer", "food bank")
DEFAULT_ALERTS = {"seller": ["new_match", "pickup_booked"], "buyer": ["order_accepted", "pickup_booked", "delivered", "fresh"],
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
    if rid.startswith(f"{OWN_ID}-"):
        lane = "fresh" if SC.SCENARIOS[key]["engine"] == "perishable" else "durable"
        return f"/passport/import_{lane}/{rid[len(OWN_ID) + 1:]}"
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
        g = gapfill_demo()
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
    return out + [("import_fresh", r, r.seller_id) for r in _own_items("fresh")]


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


@app.get("/api/whatsapp/contacts")
def whatsapp_contacts(role: str | None = None):
    """Registered contacts with the phone masked. role filters the list (seller, buyer, food bank)."""
    with closing(db()) as con:
        rows = con.execute("SELECT * FROM whatsapp_contacts ORDER BY id DESC").fetchall()
    roles = None if not role else set(role.split(","))
    return {"contacts": [_contact_out(r) for r in rows if roles is None or r["role"] in roles],
            "businesses": _sample_businesses(), "alert_types": ALERT_TYPES, "roles": list(CONTACT_ROLES),
            "default_alerts": DEFAULT_ALERTS, "consent_text": CONSENT_TEXT}


@app.post("/api/whatsapp/contacts")
def add_whatsapp_contact(c: ContactIn, request: Request):
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


@app.patch("/api/whatsapp/contacts/{cid}")
def edit_whatsapp_contact(cid: int, c: ContactPatch):
    given = {k: v for k, v in c.model_dump().items() if v is not None}
    vals, errors = _check_contact({k: v for k, v in given.items() if k != "active"}, need_consent=False)
    if errors:
        raise HTTPException(422, {"message": "Some fields need fixing. Nothing was changed.", "errors": errors})
    if "active" in given:
        vals["active"] = int(given["active"])
    if "alerts" in vals:
        vals["alerts"] = json.dumps(vals["alerts"])
    with closing(db()) as con, con:
        if con.execute("SELECT id FROM whatsapp_contacts WHERE id=?", (cid,)).fetchone() is None:
            raise HTTPException(404, "no such contact")
        if vals:
            con.execute(f"UPDATE whatsapp_contacts SET {', '.join(k + '=?' for k in vals)} WHERE id=?", (*vals.values(), cid))
        row = con.execute("SELECT * FROM whatsapp_contacts WHERE id=?", (cid,)).fetchone()
    return _contact_out(row)


@app.delete("/api/whatsapp/contacts/{cid}")
def remove_whatsapp_contact(cid: int):
    with closing(db()) as con, con:
        n = con.execute("DELETE FROM whatsapp_contacts WHERE id=?", (cid,)).rowcount
        con.execute("DELETE FROM notifications WHERE contact_id=? AND status='ready'", (cid,))
    if not n:
        raise HTTPException(404, "no such contact")
    return {"removed": True}


class NotifStatus(BaseModel):
    status: Literal["opened", "sent", "skipped"]  # never "delivered" or "read": we only record what a person did


@app.get("/api/whatsapp/notifications")
def whatsapp_notifications(request: Request, status: str | None = None):
    _safely(_sweep_fresh, _origin(request))
    with closing(db()) as con:
        rows = con.execute("SELECT n.*, c.name AS c_name, c.business AS c_business, c.phone AS c_phone, c.role AS c_role "
                           "FROM notifications n LEFT JOIN whatsapp_contacts c ON c.id = n.contact_id ORDER BY n.id DESC").fetchall()
    out = []
    for r in rows:
        if status and r["status"] != status:
            continue
        out.append({"id": r["id"], "created_at": r["created_at"], "event": r["event"], "event_text": ALERT_TYPES.get(r["event"], r["event"]),
                    "deal_id": r["deal_id"], "item_id": r["item_id"], "text": r["text"], "link": r["link"], "status": r["status"],
                    "status_text": STATUS_TEXT[r["status"]], "opened_at": r["opened_at"], "sent_at": r["sent_at"],
                    "to": {"name": r["c_name"] or "Removed contact", "business": r["c_business"] or "", "role": r["c_role"] or "",
                           "phone": MSG.mask_phone(r["c_phone"]) if r["c_phone"] else ""}})
    return {"notifications": out, "ready": sum(1 for x in out if x["status"] == "ready")}


@app.get("/api/whatsapp/notifications/count")
def whatsapp_ready_count(request: Request):
    _safely(_sweep_fresh, _origin(request))
    with closing(db()) as con:
        return {"ready": con.execute("SELECT COUNT(*) FROM notifications WHERE status='ready'").fetchone()[0]}


@app.post("/api/whatsapp/notifications/{nid}/status")
def whatsapp_notification_status(nid: int, s: NotifStatus):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    col = {"opened": "opened_at", "sent": "sent_at"}.get(s.status)
    with closing(db()) as con, con:
        if con.execute("SELECT id FROM notifications WHERE id=?", (nid,)).fetchone() is None:
            raise HTTPException(404, "no such alert")
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
 const r=await fetch("/api/whatsapp/contacts",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify(body)}});const t=await r.json().catch(()=>({{}}));
 if(r.ok){{f.hidden=true;document.getElementById("msg").textContent="Registered: "+t.phone+" for "+t.business+". Alerts will appear on the laptop under Ready to send. You can turn them off anytime.";}}
 else{{const er=(t.detail&&t.detail.errors)||{{}};for(const k in er){{const x=document.getElementById("e-"+k);if(x)x.textContent=er[k];}}document.getElementById("msg").textContent=(t.detail&&t.detail.message)||"Something went wrong. Try again.";}}}};
</script>"""
    css = ("label{display:flex;flex-direction:column;gap:4px;font-size:14px;margin:10px 0 2px;color:#5d6b77}"
           "input:not([type=checkbox]),select{font:inherit;font-size:16px;padding:10px 12px;border:1px solid #c4ccd3;border-radius:8px;background:#fff;color:#17222b}"
           "label.ck{flex-direction:row;align-items:flex-start;gap:10px;color:#17222b;font-size:15px}label.ck input{width:20px;height:20px;margin-top:1px;flex:none}"
           "fieldset{border:1px solid #d9dfe4;border-radius:8px;margin:12px 0 2px;padding:6px 12px 10px}legend{font-size:14px;color:#5d6b77;padding:0 4px}"
           "button{margin-top:14px;width:100%;padding:12px;font-size:16px;background:#f2a33a;border-color:#f2a33a;color:#1f1400}"
           ".err{color:#b0281c;font-size:13px;display:block}.err:empty{display:none}")
    return _PAGE.format(title="WhatsApp alerts", body=body).replace("</style>", css + "</style>")
