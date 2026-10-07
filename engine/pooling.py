"""
Pooled fulfilment, for ANY resource engine.

Fill one buyer order from one or several sellers' resources:
  1. Ask the engine whether each resource can serve the order (with a reason if not).
  2. Ask the engine how much each compatible resource can supply.
  3. Try every combination of up to 7 nearest sellers that has enough capacity.
     For each: allocate resources in the engine's preferred order, verify every
     allocation independently, cost the shortest pickup tour exactly, and let the
     engine veto the tour if it is too slow (perishables).
  4. Keep the combination that creates the most value over salvage.
Each resource is used at most once, and the order is never over-filled.

Value created (all engines):
    G = W - S - T
    W = what the buyer would pay for the same units from its usual source
    S = salvage value of what is shipped (scrap price, or a disposal cost if negative)
    T = pickup transport
The seller keeps alpha x G above salvage; the buyer saves the rest.
Donations to an NGO are kept even when G <= 0, because their value is not money.
"""
from __future__ import annotations

import itertools
import math

from .engines import engine_for
from .models import Seller, Settings, Transport

MAX_CANDIDATES = 7


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def best_tour(depot, stops):
    """Shortest round trip depot -> every stop -> depot, by trying every order."""
    if not stops:
        return [], 0.0
    best = None
    for order in itertools.permutations(stops):
        d, cur = 0.0, depot
        for _, pos in order:
            d += _dist(cur, pos)
            cur = pos
        d += _dist(cur, depot)
        if best is None or d < best[1] - 1e-9:
            best = ([sid for sid, _ in order], d)
    return best


def trip_cost(distance_km: float, stops: int, kg: float, t: Transport):
    """Cheapest vehicle choice for this load and tour. Returns (cost, trips, vehicle name)."""
    if kg <= 0:
        return 0.0, 0, None
    best = None
    for v in t.vehicles:
        trips = max(1, math.ceil(kg / v.payload_kg))
        cost = t.rate_multiplier * trips * (v.fixed_cost_per_trip + v.cost_per_km * distance_km) + t.cost_per_stop * max(0, stops - 1)
        if best is None or cost < best[0]:
            best = (cost, trips, v.name)
    return best


def fulfil(demand, sellers: list[Seller], resources: list, transport: Transport, settings: Settings,
           alpha: float = 0.5, allow_partial: bool = False) -> dict:
    eng = engine_for(demand.kind)
    seller_by_id = {s.id: s for s in sellers}
    ids = [r.id for r in resources]
    if len(ids) != len(set(ids)):
        raise ValueError("resource ids must be unique")
    unknown = {r.seller_id for r in resources} - seller_by_id.keys()
    if unknown:
        raise ValueError(f"resources reference unknown sellers: {sorted(unknown)}")
    is_donation = getattr(demand, "buyer_type", None) == "ngo"

    usable, rejections, salvage, not_relevant = [], [], {}, []
    for r in resources:
        if r.kind != demand.kind or not eng.relevant(r, demand):
            not_relevant.append(r.id)  # different material, profile or food category: not a rejection
            continue
        seller = seller_by_id[r.seller_id]
        reason = eng.check(r, demand, seller, settings)
        if reason is None:
            try:
                salvage[r.id] = seller.salvage_for(eng.salvage_key(r))
            except ValueError as e:
                reason = str(e)
        if reason:
            rejections.append({"resource_id": r.id, "seller_id": r.seller_id, "reason": reason})
            continue
        usable.append((r, eng.capacity(r, demand, settings)))

    cap_by_seller: dict[str, float] = {}
    for r, c in usable:
        cap_by_seller[r.seller_id] = cap_by_seller.get(r.seller_id, 0) + c
    depot = (demand.x_km, demand.y_km)
    dist_of = {sid: _dist(depot, (s.x_km, s.y_km)) for sid, s in seller_by_id.items()}
    candidates = sorted((sid for sid, c in cap_by_seller.items() if c > 0), key=lambda sid: (dist_of[sid], sid))
    excluded = candidates[MAX_CANDIDATES:]
    candidates = candidates[:MAX_CANDIDATES]
    total_capacity = sum(cap_by_seller[s] for s in candidates)

    def evaluate(subset, qty):
        items = sorted([(r, c) for r, c in usable if r.seller_id in subset], key=lambda rc: eng.order_key(rc[0]))
        remaining, takes = qty, []
        for r, c in items:
            if remaining <= 1e-9:
                break
            t = eng.take(r, demand, min(c, remaining), settings)
            problems = eng.verify(r, demand, t, settings)
            if problems:
                raise RuntimeError(f"invalid allocation on {r.id}: {problems[:3]}")
            if t.units > 0:
                takes.append((r, t))
                remaining -= t.units
        got = qty - max(0.0, remaining)
        used_sellers = sorted({r.seller_id for r, _ in takes})
        shipped = sum(t.shipped_kg for _, t in takes)
        S = sum(salvage[r.id].price_per_kg * t.shipped_kg for r, t in takes)
        order, km = best_tour(depot, [(sid, (seller_by_id[sid].x_km, seller_by_id[sid].y_km)) for sid in used_sellers])
        T, trips, vehicle = trip_cost(km, len(used_sellers), shipped, transport)
        tour_hours = km / transport.speed_kmph
        veto = eng.route_ok([r for r, _ in takes], demand, tour_hours)
        W = eng.buyer_value(demand, got, settings)
        G = W - S - T
        worth = G > 0 or (is_donation and got > 0)
        seller_gain = alpha * G if G > 0 else 0.0
        per_seller = []
        for sid in used_sellers:
            mine = [(r, t) for r, t in takes if r.seller_id == sid]
            units = sum(t.units for _, t in mine)
            per_seller.append({
                "seller_id": sid, "seller_name": seller_by_id[sid].name,
                "units": round(units, 3), "resources_used": [r.id for r, _ in mine],
                "kg_shipped": round(sum(t.shipped_kg for _, t in mine), 2),
                "salvage_value": round(sum(salvage[r.id].price_per_kg * t.shipped_kg for r, t in mine), 2),
                "gain_above_salvage": round(seller_gain * units / got, 2) if got else 0.0,
                "salvage_executable": all(salvage[r.id].executable for r, _ in mine),
            })
        return {
            "sellers_used": used_sellers, "units": round(got, 3), "pickup_order": order,
            "route_km": round(km, 2), "tour_hours": round(tour_hours, 2), "trips": trips, "vehicle": vehicle,
            "kg_shipped": round(shipped, 2), "kg_in_units": round(got * eng.unit_kg(demand, settings), 2),
            "veto": veto, "counterfactual": sorted({eng.counterfactual(r) for r, _ in takes}),
            "value": {"buyer_value_W": round(W, 2), "salvage_value_S": round(S, 2), "transport_T": round(T, 2),
                      "gain_G": round(G, 2), "worth_doing": worth and veto is None,
                      "seller_gain_above_salvage": round(seller_gain, 2),
                      "buyer_saving": round((1 - alpha) * G, 2) if G > 0 else 0.0,
                      "is_donation": is_donation},
            "per_seller": per_seller,
            "allocations": [{"resource_id": r.id, "seller_id": r.seller_id, "units": t.units, **t.detail}
                            for r, t in takes],
        }

    base = {
        "kind": demand.kind, "unit_name": eng.unit_name, "time_unit": eng.time_unit,
        "demand_id": demand.id, "requested": demand.quantity, "unit_kg": round(eng.unit_kg(demand, settings), 5),
        "seller_capacity": [{"seller_id": sid, "seller_name": s.name, "capacity": round(cap_by_seller.get(sid, 0), 3),
                             "distance_km": round(dist_of[sid], 2)} for sid, s in sorted(seller_by_id.items())],
        "candidate_sellers": candidates, "excluded_far_sellers": excluded,
        "total_capacity": round(total_capacity, 3), "rejections": rejections, "not_relevant": not_relevant,
        "method": f"{eng.kind} engine; exact search over seller combinations and pickup orders",
    }

    if total_capacity + 1e-9 < demand.quantity and not allow_partial:
        partial = evaluate(tuple(candidates), total_capacity) if candidates else None
        return {**base, "feasible": False, "shortfall": round(demand.quantity - total_capacity, 3),
                "message": (f"Not enough compatible surplus: {total_capacity:g} of {demand.quantity:g} {eng.unit_name} "
                            f"available from {len(candidates)} seller(s)."),
                "best": partial, "alternatives": []}

    qty = min(demand.quantity, total_capacity)
    options, vetoed = [], []
    for k in range(1, len(candidates) + 1):
        for subset in itertools.combinations(candidates, k):
            if sum(cap_by_seller[s] for s in subset) + 1e-9 < qty:
                continue
            ev = evaluate(subset, qty)
            if set(ev["sellers_used"]) != set(subset):
                continue
            (vetoed if ev["veto"] else options).append(ev)
    if not options:
        why = vetoed[0]["veto"] if vetoed else "no combination of sellers works"
        return {**base, "feasible": False, "shortfall": round(demand.quantity, 3), "message": f"Cannot fulfil: {why}.",
                "best": vetoed[0] if vetoed else None, "alternatives": []}
    options.sort(key=lambda o: (-o["value"]["gain_G"], len(o["sellers_used"]), o["route_km"]))
    best = options[0]
    v = best["value"]
    if v["is_donation"]:
        msg = (f"{best['units']:g} {eng.unit_name} donated from {len(best['sellers_used'])} seller(s); "
               f"pickup {best['route_km']} km, about {best['tour_hours']*60:.0f} minutes.")
    elif v["worth_doing"]:
        msg = (f"{best['units']:g} {eng.unit_name} from {len(best['sellers_used'])} seller(s); value created over salvage "
               f"₹{v['gain_G']:,.0f} after ₹{v['transport_T']:,.0f} transport.")
    else:
        msg = "This order can be filled, but after transport it is worth no more than salvage. Salvage, or find a closer buyer."
    if qty < demand.quantity:
        msg = f"Partial: {qty:g} of {demand.quantity:g}. " + msg
    return {**base, "feasible": v["worth_doing"], "shortfall": round(demand.quantity - qty, 3), "message": msg,
            "best": best,
            "alternatives": [{k2: o[k2] for k2 in ("sellers_used", "units", "route_km", "value")} for o in options[1:4]]}
