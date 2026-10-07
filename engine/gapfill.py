"""
Gap Fill: "Sold Before It's Cut".

Before a workshop cuts its own job from a sheet, the empty space in its
cutting plan is filled with parts that nearby buyers have already ordered.
The leftover is sold before it exists.

How a plan is made
------------------
1. Nest the seller's own parts first (same MaxRects packer as the other engines).
2. Take the free space left on the sheet.
3. Keep only the standing orders that fit the sheet's material, thickness and grade.
   Every other order is refused with a reason.
4. Fill the free space with the eligible parts, in three ways: all free space,
   the two largest remnants, the single largest remnant. Each way is tried with
   two greedy strategies. The best is the one with the highest net gain over
   scrapping the same area.
5. If filling is not worth more than scrapping the whole leftover (after the
   pickup cost), nothing is filled and the plan says so.
6. Every plan is checked by verify_placements, an independent checker.

Everything priced here is SAMPLE data until the admin enters real quotes.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from . import materials as M
from .nesting import EPS, MaxRectsBin, Placement, Rect, nest_identical_parts, verify_placements

DataLabel = Literal["measured", "sample", "assumption", "simulated"]


class Sheet(BaseModel):
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    thickness_mm: float = Field(gt=0)
    material: str = "mild_steel"
    grade: str = "E250"


class OwnJob(BaseModel):
    part_name: str = "own part"
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    quantity: int = Field(ge=1)


class StandingOrder(BaseModel):
    id: str
    buyer_name: str
    part_name: str
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    thickness_mm: float = Field(gt=0)
    material: str = "mild_steel"
    accepted_grades: list[str] = ["E250"]
    quantity: int = Field(ge=1)
    new_price_per_kg: float = Field(65.0, ge=0, description="What the buyer pays for new plate")
    buyer_utilisation: float = Field(0.8, gt=0, le=1, description="Share of a new sheet the buyer would use")
    label: DataLabel = "sample"


class GapFillPrices(BaseModel):
    scrap_price_per_kg: float = Field(32.0, ge=0)
    handling_per_part: float = Field(2.0, ge=0)
    cut_cost_per_m: float = Field(6.0, ge=0)
    pickup: float = Field(300.0, ge=0, description="One pickup run for the neighbours' parts")
    label: DataLabel = "sample"


class GapFillRequest(BaseModel):
    sheet: Sheet
    job: OwnJob
    orders: list[StandingOrder]
    prices: GapFillPrices = GapFillPrices()
    kerf_mm: float = Field(3.0, ge=0, le=20)
    edge_margin_mm: float = Field(5.0, ge=0, le=100)
    declined_order_ids: list[str] = []


def _largest(free: list[Rect]) -> Rect | None:
    return max(free, key=lambda r: r.w * r.h) if free else None


def _two_remnants(free: list[Rect], uw: float, uh: float) -> list[Rect]:
    r1 = _largest(free)
    if r1 is None:
        return []
    b = MaxRectsBin(uw, uh, True)
    b.free = list(free)
    b.block(r1)
    r2 = _largest(b.free)
    return [r1] + ([r2] if r2 is not None else [])


def _part_value(o: StandingOrder, rho: float, prices: GapFillPrices, thickness: float) -> float:
    kg = o.length_mm * o.width_mm * thickness * rho
    perimeter_m = 2 * (o.length_mm + o.width_mm) / 1000
    return kg * o.new_price_per_kg / o.buyer_utilisation - prices.handling_per_part - prices.cut_cost_per_m * perimeter_m


def _fill(free, items, k, uw, uh, scrap_per_mm2):
    """Fill free rectangles with finite quantities. Two greedy strategies; the best net gain is kept."""
    best = (0.0, 0.0, [])  # value, area, placed[(item index, node, rotated)]
    for strategy in ("bigfirst", "global"):
        b = MaxRectsBin(uw, uh, True)
        b.free = list(free)
        left = [it["qty"] for it in items]
        placed = []
        if strategy == "bigfirst":
            for i in sorted(range(len(items)), key=lambda i: -(items[i]["a"] * items[i]["b"])):
                while left[i] > 0:
                    res = b.insert(items[i]["a"] + k, items[i]["b"] + k)
                    if res is None:
                        break
                    placed.append((i, res[0], res[1]))
                    left[i] -= 1
        else:
            while True:
                pick = None
                for i, it in enumerate(items):
                    if left[i] <= 0:
                        continue
                    f = b._find_position(it["a"] + k, it["b"] + k)
                    if f is None:
                        continue
                    key = (f[0], f[1], -(it["a"] * it["b"]))
                    if pick is None or key < pick[0]:
                        pick = (key, i)
                if pick is None:
                    break
                i = pick[1]
                res = b.insert(items[i]["a"] + k, items[i]["b"] + k)
                placed.append((i, res[0], res[1]))
                left[i] -= 1
        value = sum(items[i]["value"] for i, _, _ in placed)
        area = sum((items[i]["a"] + k) * (items[i]["b"] + k) for i, _, _ in placed)
        if value - area * scrap_per_mm2 > best[0] - best[1] * scrap_per_mm2 + 1e-9:
            best = (value, area, placed)
    return best


def _rect(r: Rect, m: float) -> dict:
    return {"x": round(r.x + m, 2), "y": round(r.y + m, 2), "w": round(r.w, 2), "h": round(r.h, 2)}


def plan_gap_fill(req: GapFillRequest) -> dict:
    s, job, k, m, pr = req.sheet, req.job, req.kerf_mm, req.edge_margin_mm, req.prices
    mat = M.get(s.material)
    rho = mat.density_kg_per_mm3
    t = s.thickness_mm
    uw, uh = s.length_mm - 2 * m + k, s.width_mm - 2 * m + k
    scrap_per_mm2 = t * rho * pr.scrap_price_per_kg

    own = nest_identical_parts(s.length_mm, s.width_mm, job.length_mm, job.width_mm, max_parts=job.quantity,
                               kerf_mm=k, edge_margin_mm=m)
    if len(own) < job.quantity:
        raise ValueError(f"only {len(own)} of {job.quantity} of the seller's own parts fit on this sheet")

    base = MaxRectsBin(uw, uh, True)
    for p in own:
        base.block(Rect(p.x - m, p.y - m, p.length + k, p.width + k))
    free = list(base.free)
    leftover_area = s.length_mm * s.width_mm - sum((p.length + k) * (p.width + k) for p in own)
    scrap_alone = leftover_area * scrap_per_mm2

    # which orders can be used at all
    items, refused = [], []
    for o in req.orders:
        if o.id in req.declined_order_ids:
            refused.append({"order_id": o.id, "reason": "buyer declined; space released"})
            continue
        why = None
        if o.material != s.material:
            why = f"material {M.get(o.material).name}, sheet is {mat.name}"
        elif abs(o.thickness_mm - t) > 1e-9:
            why = f"thickness {o.thickness_mm} mm, sheet is {t} mm"
        elif s.grade.strip().upper() not in [g.strip().upper() for g in o.accepted_grades]:
            why = f"grade {s.grade} not accepted ({', '.join(o.accepted_grades)})"
        else:
            a, b = o.length_mm, o.width_mm
            if not ((a + k <= uw + EPS and b + k <= uh + EPS) or (b + k <= uw + EPS and a + k <= uh + EPS)):
                why = "part larger than the usable sheet"
        if why:
            refused.append({"order_id": o.id, "reason": why})
        else:
            items.append({"order": o, "a": o.length_mm, "b": o.width_mm, "qty": o.quantity,
                          "value": _part_value(o, rho, pr, t)})

    options = [("all free space", free), ("two largest remnants", _two_remnants(free, uw, uh)),
               ("largest remnant", [_largest(free)] if free else [])]
    best_opt = None
    for name, space in options:
        if not space or not items:
            continue
        v, area, placed = _fill(space, items, k, uw, uh, scrap_per_mm2)
        gain = v - area * scrap_per_mm2
        if best_opt is None or gain > best_opt[0] + 1e-9:
            best_opt = (gain, name, v, area, placed)

    placed, neighbours_value, area_used, option = [], 0.0, 0.0, "none"
    if best_opt and best_opt[4]:
        _, option, neighbours_value, area_used, placed = best_opt
    pickup = pr.pickup if placed else 0.0
    scrap_rest = max(0.0, leftover_area - area_used) * scrap_per_mm2
    total = neighbours_value + scrap_rest - pickup
    worth = bool(placed) and total > scrap_alone + 1e-9
    if not worth:  # filling does not beat scrapping everything: say so and fill nothing
        placed, neighbours_value, area_used, pickup, option = [], 0.0, 0.0, 0.0, "none"
        scrap_rest, total = scrap_alone, scrap_alone

    by_order: dict[str, dict] = {}
    nb_placements: list[Placement] = []
    for i, node, rot in placed:
        o = items[i]["order"]
        part = {"x": round(node.x + m, 2), "y": round(node.y + m, 2), "length": round(node.w - k, 2), "width": round(node.h - k, 2)}
        nb_placements.append(Placement(node.x + m, node.y + m, node.w - k, node.h - k, rot))
        e = by_order.setdefault(o.id, {"order_id": o.id, "buyer_name": o.buyer_name, "part_name": o.part_name,
                                        "label": o.label, "parts": [], "value_each": round(items[i]["value"], 2)})
        e["parts"].append(part)
    neighbours = []
    for e in by_order.values():
        e["units"] = len(e["parts"])
        e["value"] = round(e["units"] * e["value_each"], 2)
        neighbours.append(e)

    problems = verify_placements(s.length_mm, s.width_mm, list(own) + nb_placements, k, m)
    gain = total - scrap_alone
    return {
        "label": pr.label, "sheet": s.model_dump(), "kerf_mm": k, "edge_margin_mm": m,
        "own": {"part_name": job.part_name, "units": len(own),
                "parts": [{"x": round(p.x, 2), "y": round(p.y, 2), "length": p.length, "width": p.width} for p in own]},
        "free_rects": [_rect(r, m) for r in free],
        "neighbours": neighbours, "option": option, "worth_doing": worth,
        "money": {"scrap_alone": round(scrap_alone, 2), "neighbours_value": round(neighbours_value, 2),
                  "scrap_of_rest": round(scrap_rest, 2), "pickup": round(pickup, 2),
                  "total_with_gap_fill": round(total, 2), "net_gain": round(gain, 2),
                  "ratio": round(total / scrap_alone, 3) if scrap_alone > 0 else None,
                  "leftover_kg": round(leftover_area * t * rho, 2)},
        "refused": refused, "declined": list(req.declined_order_ids),
        "checks": {"ok": not problems, "problems": problems},
        "message": (f"{sum(e['units'] for e in neighbours)} neighbour parts fill the gap; net gain over scrap "
                    f"₹{gain:,.0f} after ₹{pickup:,.0f} pickup." if worth else
                    "Filling the gap would not beat selling the whole leftover as scrap, so nothing is reserved."),
    }
