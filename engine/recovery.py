"""
Recovery Route Engine: in what order should a seller try its options?

Every ordering of routes that fits inside the deadline is scored; salvage
(scrap, compost, disposal) is always the last step:

    EV = sum over steps of  P(reach step) x p_close x value
         + P(nothing closes) x salvage value
         - carrying cost per time unit x expected time

The time unit is days for metals and hours for food. With at most 5 routes
this is a few hundred orderings, solved exactly.

Honesty rules
- Salvage is a guaranteed floor only with a current, firm (executable) offer.
- p_close values are assumptions until there is outcome data.
- Donation routes are kept even though they earn no money; if two plans
  are worth the same, the one that keeps more material in use wins.
- If a buyer says no, call again with that route rejected and time elapsed.
"""
from __future__ import annotations

import itertools

from .models import PlanRequest

MAX_ROUTES = 5


def _score(seq, salvage, carry, time_left):
    ev, reach, t, exp_t = 0.0, 1.0, 0, 0.0
    steps = []
    for r in seq:
        t += r.window
        if t > time_left:
            return None
        exp_t += reach * r.window
        ev += reach * r.p_close * r.value_to_seller
        steps.append({"route_id": r.id, "name": r.name, "kind": r.kind, "start": t - r.window, "end": t,
                      "p_close": r.p_close, "p_reach": round(reach, 4), "value_to_seller": r.value_to_seller})
        reach *= 1 - r.p_close
    ev += reach * salvage - carry * exp_t
    reuse_steps = sum(1 for s in steps)
    return {"ev": ev, "steps": steps, "p_salvage": reach, "expected_time": exp_t, "time_used": t, "reuse_steps": reuse_steps}


def plan(req: PlanRequest) -> dict:
    left = req.deadline - req.elapsed
    if left < 0:
        raise ValueError("elapsed time is past the deadline")
    u = req.time_unit
    active = [r for r in req.routes if r.id not in req.rejected_route_ids]
    useful = [r for r in active if r.value_to_seller > req.salvage_value or r.kind == "donation"]
    skipped = [{"route_id": r.id, "reason": "worth no more than salvage"} for r in active if r not in useful]
    useful.sort(key=lambda r: -r.value_to_seller * r.p_close)
    if len(useful) > MAX_ROUTES:
        skipped += [{"route_id": r.id, "reason": "outside the top 5"} for r in useful[MAX_ROUTES:]]
        useful = useful[:MAX_ROUTES]

    scored = []
    for k in range(len(useful) + 1):
        for seq in itertools.permutations(useful, k):
            s = _score(seq, req.salvage_value, req.carry_cost_per_unit_time, left)
            if s:
                scored.append(s)
    scored.sort(key=lambda s: (-round(s["ev"], 2), -s["reuse_steps"], s["time_used"]))
    best, runner = scored[0], (scored[1] if len(scored) > 1 else None)

    floor = "firm salvage offer" if req.salvage_executable else "salvage ESTIMATE (no firm offer yet)"
    above = best["ev"] - req.salvage_value
    if best["steps"]:
        chain = " → ".join(f"{s['name']} for {s['end'] - s['start']} {u}" for s in best["steps"])
        first = best["steps"][0]
        why = (f"Plan: {chain} → salvage. Expected ₹{best['ev']:,.0f} (₹{above:,.0f} above salvage). "
               f"{first['name']} goes first because, even with a {(1 - first['p_close']) * 100:.0f}% chance of no deal, "
               f"trying it first gives the highest expected value. Chance of ending at salvage: "
               f"{best['p_salvage'] * 100:.0f}%. Worst case: the {floor}, after {best['time_used']} {u}.")
    else:
        why = f"Salvage now: no route beats salvage within the {left} {u} left."
    if runner:
        why += f" Runner-up plan: ₹{runner['ev']:,.0f}."
    warnings = []
    if not req.salvage_executable:
        warnings.append("Salvage value is an estimate. Get a current, firm offer before calling it a floor.")
    if any(r.p_label == "assumption" for r in useful):
        warnings.append("Close probabilities are assumptions, not observed rates.")
    return {"time_unit": u, "time_left": left, "salvage_value": req.salvage_value,
            "salvage_executable": req.salvage_executable,
            "best": {**best, "ev": round(best["ev"], 2), "above_salvage": round(above, 2),
                     "p_salvage": round(best["p_salvage"], 4), "expected_time": round(best["expected_time"], 2)},
            "runner_up": ({**runner, "ev": round(runner["ev"], 2)} if runner else None),
            "sequences_evaluated": len(scored), "skipped_routes": skipped, "explanation": why, "warnings": warnings}
