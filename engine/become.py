"""
What it can become: for ONE piece, bar or lot, every useful thing it could be cut into or sold as.

No new maths. Every candidate goes through the engine that already serves orders:
  check       the same rules (material, thickness, grade, condition, defects, grain,
              storage, licence, shelf life ...), with the same plain-language reasons
  capacity    the same nesting (kerf, edge margin, defects never cut through) or bar cutting
  take        the same placements or cuts, re-checked by the engine's verify
  value       the pooling formula  G = W - S - T
              W  what the buyer would pay for the same units from its usual source
              S  the piece's scrap value (or the lot's disposal cost, negative)
              T  pickup transport: the engine's cheapest vehicle for the round trip,
                 a fixed pickup price for standing orders, nothing for a part with no buyer

Candidates
  order      an open buyer order in the lane               "Ordered by <buyer>"
  standing   a standing order (Sold before it's cut)       "Ordered by <buyer> (standing order)"
  reference  a common part from REFERENCE_PARTS, at the    "Common part, no buyer yet"
             piece's own thickness and grade, priced at the cluster's new-plate price
Every price comes from the sample data and carries its label.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .engines import engine_for
from .models import FlatDemand, Seller, Settings, Transport
from .pooling import best_tour, trip_cost

REFERENCE_LABEL = "Common part, no buyer yet"
# The one place the common reference parts are defined: (name, length mm, width mm).
REFERENCE_PARTS = [("shim", 75, 75), ("cleat", 100, 75), ("bracket", 150, 100), ("gusset", 200, 200),
                   ("base plate", 300, 300), ("end plate", 350, 200), ("stiffener", 400, 100)]


@dataclass
class Candidate:
    demand: object  # FlatDemand | LinearDemand | PerishableDemand
    source: Literal["order", "standing", "reference"]
    label: str
    pickup: float | None = None  # fixed pickup cost; None = cost the round trip with the engine's vehicles
    priced: bool = True  # False: no new price known, so only the count is shown


def order_label(buyer_name: str, standing: bool = False) -> str:
    return f"Ordered by {buyer_name}" + (" (standing order)" if standing else "")


def standing_order_demand(o, handling_per_part: float = 0.0) -> FlatDemand:
    """A Gap Fill standing order as a buyer order the flat engine understands."""
    return FlatDemand(id=o.id, buyer_id=o.buyer_name, buyer_name=o.buyer_name, part_name=o.part_name, material=o.material,
                      length_mm=o.length_mm, width_mm=o.width_mm, thickness_mm=o.thickness_mm, accepted_grades=o.accepted_grades,
                      quantity=o.quantity, new_price_per_kg=o.new_price_per_kg, buyer_utilisation=o.buyer_utilisation,
                      handling_cost_per_unit=handling_per_part, label=o.label if o.label != "simulated" else "sample")


def reference_candidates(piece, new_price_per_kg: float | None, price_label: str = "sample") -> list[Candidate]:
    """Common parts at the piece's own thickness and grade. Conditions accepted are the order defaults."""
    out = []
    for name, length, width in REFERENCE_PARTS:
        d = FlatDemand(id=f"REF-{name.replace(' ', '-')}", buyer_id="", buyer_name="", part_name=name, material=piece.material,
                       length_mm=length, width_mm=width, thickness_mm=piece.thickness_mm, accepted_grades=[piece.grade],
                       quantity=100000, new_price_per_kg=new_price_per_kg or 1.0, label=price_label)
        out.append(Candidate(d, "reference", REFERENCE_LABEL, pickup=0.0, priced=new_price_per_kg is not None))
    return out


def become(resource, seller: Seller, candidates: list[Candidate], settings: Settings, transport: Transport) -> dict:
    """Try every candidate on one resource. Options are ranked by value above scrap (or disposal)."""
    eng = engine_for(resource.kind)
    kg = eng.resource_kg(resource, settings)
    try:
        offer = seller.salvage_for(eng.salvage_key(resource))
    except ValueError:
        offer = None
    options, refused, seen = [], [], set()
    for c in candidates:
        d = c.demand
        if d.kind != resource.kind or (c.source, d.id) in seen:
            continue
        if resource.kind != "perishable" and not eng.relevant(resource, d):
            continue  # another material family or bar profile altogether; food buyers are all checked, so each refusal has a reason
        seen.add((c.source, d.id))
        name = getattr(d, "part_name", "") or "kg"
        base = {"id": d.id, "source": c.source, "label": c.label, "part_name": name, "buyer_name": d.buyer_name or None}
        reason = eng.check(resource, d, seller, settings)
        if reason is None and offer is None:
            reason = f"no scrap or disposal price for {eng.salvage_key(resource).replace('_', ' ')} in this cluster"
        units = 0.0
        if reason is None:
            fits = eng.capacity(resource, d, settings)
            units = min(fits, d.quantity)
            if units <= 0:
                reason = "nothing fits"
        if reason is None:
            t = eng.take(resource, d, units, settings)
            problems = eng.verify(resource, d, t, settings)
            if problems:
                raise RuntimeError(f"invalid plan for {resource.id} / {d.id}: {problems[:3]}")
            if c.pickup is None:
                order, km = best_tour((d.x_km, d.y_km), [(seller.id, (seller.x_km, seller.y_km))])
                T, trips, vehicle = trip_cost(km, 1, t.shipped_kg, transport)
                tour_hours = km / transport.speed_kmph
            else:
                T, km, vehicle, tour_hours = c.pickup, None, None, 0.0
            veto = eng.route_ok([resource], d, tour_hours)
            if veto:
                reason = veto
        if reason is not None:
            refused.append({**base, "reason": reason})
            continue
        W = eng.buyer_value(d, t.units, settings) if c.priced else None
        S = offer.price_per_kg * t.shipped_kg
        opt = {**base, "units": round(t.units, 3), "unit_name": eng.unit_name, "kg_shipped": round(t.shipped_kg, 2),
               "buyer_value": None if W is None else round(W, 2), "scrap_value": round(S, 2), "transport": round(T, 2),
               "value_above_scrap": None if W is None else round(W - S - T, 2), "route_km": None if km is None else round(km, 2),
               "vehicle": vehicle, "price_label": d.label, "detail": t.detail}
        if resource.kind == "flat":
            opt.update(fits=fits, wanted=d.quantity if c.source != "reference" else None, part_mm=[d.length_mm, d.width_mm],
                       new_price_per_kg=d.new_price_per_kg if c.priced else None)
        elif resource.kind == "linear":
            opt.update(fits=fits, wanted=d.quantity, cut_length_mm=d.cut_length_mm, new_price_per_kg=d.new_price_per_kg)
        else:
            opt.update(buyer_type=d.buyer_type, is_donation=d.buyer_type == "ngo", wanted=d.quantity,
                       normal_price_per_kg=d.normal_price_per_kg, hours_on_arrival=round(resource.hours_left - tour_hours, 1),
                       min_hours_on_arrival=d.min_hours_left_on_arrival)
        options.append(opt)
    options.sort(key=lambda o: (o["value_above_scrap"] is None, -(o["value_above_scrap"] or 0), o["source"] != "order"))
    return {"resource_id": resource.id, "kind": resource.kind, "kg": round(kg, 2),
            "scrap": None if offer is None else {"value": round(offer.price_per_kg * kg, 2), "price_per_kg": offer.price_per_kg,
                                                 "label": offer.label, "quoted_on": offer.quoted_on.isoformat() if offer.quoted_on else None},
            "kerf_mm": settings.kerf_mm, "edge_margin_mm": settings.edge_margin_mm, "end_trim_mm": settings.end_trim_mm,
            "options": options, "refused": refused}
