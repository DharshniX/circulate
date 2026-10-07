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
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine import scenarios as SC
from engine.engines import REGISTRY
from engine.gapfill import GapFillRequest, plan_gap_fill
from engine.impact import DealImpactInput, EmissionFactor, compute
from engine import importer as IMP
from engine.models import Demand, PlanRequest, Resource, Seller, Settings, Transport
from engine.pooling import _dist, fulfil
from engine.recovery import plan
from engine.routes import seller_plan, seller_routes

ROOT = Path(__file__).resolve().parent
DB = ROOT.parent / "circulate.db"
app = FastAPI(title="CIRCULATE", version="0.2")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, kind TEXT, counterfactual TEXT,
        demand_id TEXT, buyer_name TEXT, sellers TEXT, units REAL, unit_name TEXT, kg_in_units REAL,
        kg_shipped REAL, route_km REAL, value_above_salvage REAL, buyer_utilisation REAL,
        status TEXT, is_simulated INTEGER, evidence TEXT, buyer_confirmed INTEGER, history TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS imported_stock (
        lane TEXT, item_id TEXT, item TEXT, rules TEXT, label TEXT, listed_at TEXT, PRIMARY KEY (lane, item_id))""")
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
    return SC.gapfill_demo()


@app.post("/api/gapfill")
def api_gapfill(req: GapFillRequest):
    try:
        return plan_gap_fill(req)
    except ValueError as e:
        _bad(e)


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
def create_deal(d: NewDeal):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as con, con:
        cur = con.execute(
            "INSERT INTO deals (created_at, kind, counterfactual, demand_id, buyer_name, sellers, units, unit_name,"
            " kg_in_units, kg_shipped, route_km, value_above_salvage, buyer_utilisation, status, is_simulated,"
            " evidence, buyer_confirmed, history) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, d.kind, d.counterfactual, d.demand_id, d.buyer_name, json.dumps(d.sellers), d.units, d.unit_name,
             d.kg_in_units, d.kg_shipped, d.route_km, d.value_above_salvage, d.buyer_utilisation, "agreed",
             int(d.is_simulated), "[]", 0, json.dumps([{"at": now, "status": "agreed"}])))
        return {"id": cur.lastrowid, "status": "agreed"}


@app.post("/api/deals/{deal_id}/advance")
def advance(deal_id: int, a: Advance):
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
                "passport": f"/passport/{key}/{p.id}", "rules": json.loads(r["rules"]), "source": IMP.SOURCE,
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
def stock(key: str):
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
img.photo{{display:block;width:100%;height:auto;border-radius:6px;margin:8px 0}} form{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}}
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
    return (f'<div class="card"><b>Photo</b>{shown}<div class="noprint"><form id="pf"><input type="file" id="pfile" accept="image/jpeg,image/png,image/webp" aria-label="Photo file">'
            f'<button type="submit">{"Replace photo" if url else "Upload photo"}</button>{remove}</form>'
            f'<p class="note" id="pmsg" aria-live="polite">Seller only: a photo of this exact {what}. JPEG, PNG or WebP, up to 5 MB.</p></div></div>'
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
        for lane, label, n in con.execute("SELECT lane, label, COUNT(*) FROM imported_stock GROUP BY lane, label"):
            rows.append({"area": f"Imported stock (CSV), {lane}", "label": label, "count": n})
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
        return IMP.run_import(req.lane, req.csv_text, req.days_unused_limit, req.hours_left_limit, label)
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


@app.post("/api/import/list")
def import_list(req: ListRequest):
    """List the surplus items on the marketplace (stock board, passports, QR codes)."""
    label = "measured" if req.measured else "sample"
    r = _run_import(req, label)
    if not r["surplus"]:
        _bad(ValueError("Nothing was found as surplus, so nothing was listed. Lower the thresholds or check the file."))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(db()) as con, con:
        for s in r["surplus"]:
            con.execute("INSERT OR REPLACE INTO imported_stock (lane, item_id, item, rules, label, listed_at) VALUES (?,?,?,?,?,?)",
                        (req.lane, s["item"]["id"], json.dumps(s["item"]), json.dumps(s["rules"]), label, now))
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
    g = SC.gapfill_demo()
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
    if ctype not in PHOTO_TYPES:
        raise HTTPException(415, "Send a JPEG, PNG or WebP photo.")
    data = await request.body()
    if not data:
        raise HTTPException(422, "The photo is empty. Choose the file again.")
    if len(data) > PHOTO_MAX:
        raise HTTPException(413, "The photo is larger than 5 MB. Resize it and try again.")
    if _sniff(data) != ctype:
        raise HTTPException(422, "The file is not a real JPEG, PNG or WebP image.")
    old = _photo_file(key, rid)
    if old:
        old.unlink()
    f = PHOTOS / re.sub(r"[^A-Za-z0-9_]", "_", key) / (rid.encode().hex() + PHOTO_TYPES[ctype])
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    return {"photo": _photo_url(key, rid)}


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
                "source": IMP.SOURCE, "order": None,
                "order_reason": "Imported stock is not in the matching data yet. Open its passport to contact the seller."}
        if p.kind == "perishable":
            item["disposal"] = None
        else:
            item.update(material_name=M_name(p.material), floor=None, ceiling=None)
        out.append(item)
    return out


@app.get("/api/browse/{lane}")
def browse(lane: Literal["durable", "fresh"]):
    """Every stock item in this lane as a marketplace listing: sample scenarios plus stock listed from a CSV import."""
    items, buyers = [], {}
    for key, sc in SC.SCENARIOS.items():
        if (sc["engine"] == "perishable") == (lane == "fresh"):
            rows, buyers[key] = _browse_scenario(key)
            items += rows
    return {"lane": lane, "items": items + _browse_imported(lane), "distance_from": buyers}
