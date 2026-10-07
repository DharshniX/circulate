"""
Build one seller's recovery routes from real engine results, then plan.

Routes for a seller, for every buyer order of a matching kind:
  - direct: only this seller's own resources
  - pooled: this seller's share of a multi-seller order
  - donation: for food, an NGO order
plus an optional stockist buy-back (metals) at a quoted price per kg.
Salvage = salvage value of everything the seller listed of that kind.
value_to_seller = salvage value of all its resources + its gain above salvage.
"""
from __future__ import annotations

from .engines import engine_for
from .models import PlanRequest, Route, Seller, Settings, Transport
from .pooling import fulfil
from .recovery import plan

DEFAULTS = {
    "days": {"p_direct": 0.5, "p_pooled": 0.35, "p_stockist": 0.8, "w_direct": 5, "w_pooled": 7, "w_stockist": 3, "carry": 20.0, "deadline": 12},
    "hours": {"p_direct": 0.5, "p_pooled": 0.35, "p_stockist": 0.0, "w_direct": 3, "w_pooled": 4, "w_stockist": 1, "carry": 0.0, "deadline": 12},
}


def seller_routes(seller: Seller, kind: str, sellers, resources, demands, transport: Transport, settings: Settings,
                  alpha: float = 0.5, stockist_price_per_kg: float | None = None) -> dict:
    eng = engine_for(kind)
    cfg = DEFAULTS[eng.time_unit]
    mine = [r for r in resources if r.seller_id == seller.id and r.kind == kind]
    if not mine:
        raise ValueError(f"{seller.name} has no {kind} resources")
    salvage_all = 0.0
    for r in mine:
        salvage_all += seller.salvage_for(eng.salvage_key(r)).price_per_kg * eng.resource_kg(r, settings)
    executable = all(seller.salvage_for(eng.salvage_key(r)).executable for r in mine)
    routes, details = [], []
    for d in (x for x in demands if x.kind == kind):
        donation = getattr(d, "buyer_type", None) == "ngo"
        direct = fulfil(d, [seller], mine, transport, settings, alpha, allow_partial=True)
        b = direct.get("best")
        if b and b["units"] > 0 and b["value"]["worth_doing"] and not b["veto"]:
            gain = b["value"]["seller_gain_above_salvage"]
            routes.append(Route(id=f"direct-{d.id}", name=f"{d.buyer_name}: {b['units']:g} {eng.unit_name}",
                                kind="donation" if donation else "direct_buyer",
                                value_to_seller=round(salvage_all + gain, 2), p_close=cfg["p_direct"], window=cfg["w_direct"]))
            details.append({"route_id": f"direct-{d.id}", "units": b["units"], "gain_above_salvage": gain})
        pooled = fulfil(d, sellers, resources, transport, settings, alpha)
        pb = pooled.get("best")
        if pooled["feasible"] and pb and seller.id in pb["sellers_used"] and len(pb["sellers_used"]) > 1:
            share = next(x for x in pb["per_seller"] if x["seller_id"] == seller.id)
            routes.append(Route(id=f"pool-{d.id}", name=f"Pooled order for {d.buyer_name}",
                                kind="donation" if donation else "pooled_order",
                                value_to_seller=round(salvage_all + share["gain_above_salvage"], 2),
                                p_close=cfg["p_pooled"], window=cfg["w_pooled"]))
            details.append({"route_id": f"pool-{d.id}", "units": share["units"],
                            "gain_above_salvage": share["gain_above_salvage"], "partners": pb["sellers_used"]})
    if stockist_price_per_kg is not None and eng.time_unit == "days":
        kg = sum(eng.resource_kg(r, settings) for r in mine)
        routes.append(Route(id="stockist", name="Stockist buy-back", kind="stockist",
                            value_to_seller=round(kg * stockist_price_per_kg, 2),
                            p_close=cfg["p_stockist"], window=cfg["w_stockist"]))
    return {"seller_id": seller.id, "kind": kind, "time_unit": eng.time_unit, "salvage_value_all": round(salvage_all, 2),
            "salvage_executable": executable, "routes": routes, "details": details}


def seller_plan(built: dict, deadline: int | None = None, carry: float | None = None,
                rejected: list[str] | None = None, elapsed: int = 0) -> dict:
    cfg = DEFAULTS[built["time_unit"]]
    req = PlanRequest(routes=built["routes"], salvage_value=built["salvage_value_all"],
                      salvage_executable=built["salvage_executable"], deadline=deadline or cfg["deadline"],
                      time_unit=built["time_unit"], carry_cost_per_unit_time=cfg["carry"] if carry is None else carry,
                      rejected_route_ids=rejected or [], elapsed=elapsed)
    return plan(req)
