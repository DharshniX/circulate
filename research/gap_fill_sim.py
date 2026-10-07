"""
Gap Fill simulation (SAMPLE / SIMULATED — not field data).

Run from the prototype folder:  python research/gap_fill_sim.py 1000 research/results.json
Then:                            python research/summarise.py research/results.json

Question: when a workshop cuts its own job from a fresh sheet, how much of the
leftover can become parts that nearby buyers already need, under four policies?

  scrap      : everything left over goes to the scrap dealer by weight
  post-hoc   : the shop nests its job the usual way, keeps the largest
               rectangular remnant(s); a marketplace finds buyers later
  pre-cut    : buyers' parts are known before cutting and are cut into the
               free space in the same run (layout unchanged)
  shaped     : the layout of the shop's own job is ALSO chosen so the leftover
               fits the buyers' parts best (same parts, same sheet)

Uses the prototype's MaxRects packer (engine/nesting.py) for every fill.
All prices are assumptions; see PARAMS.
"""
from __future__ import annotations

import json
import math
import random
import sys
from multiprocessing import Pool

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the prototype folder
from engine.nesting import EPS, MaxRectsBin, Rect  # noqa: E402

PARAMS = dict(
    sheet_l=2500.0, sheet_w=1250.0,  # common Indian MS sheet size, mm
    kerf=3.0, margin=10.0,           # plasma-like kerf, untouched edge strip
    rho=7.85e-6,                     # kg per mm^3, mild steel
    p_new=65.0,                      # Rs/kg new plate (ASSUMPTION: low end of Rs 65-85 listed range)
    p_scrap=32.0,                    # Rs/kg melting scrap (ASSUMPTION, IndiaMART listings ~28-35)
    buyer_util=0.80,                 # buyer would use 80% of a new sheet for these parts
    handling=2.0,                    # Rs per part
    cut_cost_per_m=6.0,              # Rs per metre of cut (ASSUMPTION)
    pickup=300.0,                    # Rs per pickup run (goods auto, ASSUMPTION)
)
P = PARAMS
L, W, K, M = P["sheet_l"], P["sheet_w"], P["kerf"], P["margin"]
UW, UH = L - 2 * M + K, W - 2 * M + K  # usable area in kerf-inflated coordinates

# Typical small parts a structural / general fabrication cluster buys repeatedly (mm)
CATALOGUE = [
    ("shim", 75, 75), ("cleat", 100, 75), ("bracket", 150, 100), ("gusset-s", 150, 150),
    ("gusset-m", 200, 200), ("flange-blank", 220, 220), ("base-plate-s", 250, 250),
    ("base-plate-m", 300, 300), ("end-plate", 350, 200), ("stiffener", 400, 100),
    ("base-plate-l", 400, 400), ("cover-plate", 500, 300), ("long-stiffener", 600, 120),
]


def part_value(a, b, t):
    kg = a * b * t * P["rho"]
    return kg * P["p_new"] / P["buyer_util"] - P["handling"] - P["cut_cost_per_m"] * 2 * (a + b) / 1000


def layouts(a, b, n):
    """Grid layouts of n identical parts anchored in one corner: row-major and column-major,
    both orientations. Returns dicts with the occupied blocks (kerf-inflated coordinates)."""
    seen, out = set(), []
    orients = [(a, b, False)] if abs(a - b) < EPS else [(a, b, False), (b, a, True)]
    for pa, pb, rot in orients:
        pw, ph = pa + K, pb + K
        maxc, maxr = int((UW + EPS) // pw), int((UH + EPS) // ph)
        cand = []
        for c in range(1, maxc + 1):
            r = math.ceil(n / c)
            if r <= maxr:
                full, rem = n // c, n % c
                blocks = []
                if full:
                    blocks.append((0.0, 0.0, c * pw, full * ph))
                if rem:
                    blocks.append((0.0, full * ph, rem * pw, ph))
                cand.append(("row", c, r, blocks))
        for r in range(1, maxr + 1):
            c = math.ceil(n / r)
            if c <= maxc:
                full, rem = n // r, n % r
                blocks = []
                if full:
                    blocks.append((0.0, 0.0, full * pw, r * ph))
                if rem:
                    blocks.append((full * pw, 0.0, pw, rem * ph))
                cand.append(("col", c, r, blocks))
        for kind, c, r, blocks in cand:
            key = tuple(sorted((round(x, 3), round(y, 3), round(w, 3), round(h, 3)) for x, y, w, h in blocks))
            if key in seen:
                continue
            seen.add(key)
            bbox = max(x + w for x, y, w, h in blocks) * max(y + h for x, y, w, h in blocks)
            out.append(dict(kind=kind, rot=rot, c=c, r=r, blocks=blocks, bbox=bbox, pw=pw, ph=ph))
    return out


def free_after(blocks):
    b = MaxRectsBin(UW, UH, True)
    for x, y, w, h in blocks:
        b.block(Rect(x, y, w, h))
    return list(b.free)


def largest(free):
    return max(free, key=lambda r: r.w * r.h) if free else None


def two_remnants(free):
    r1 = largest(free)
    if r1 is None:
        return []
    b = MaxRectsBin(UW, UH, True)
    b.free = list(free)
    b.block(r1)
    r2 = largest(b.free)
    return [r1] + ([r2] if r2 is not None else [])


def fill(free, demand, t):
    """Fill free rectangles with buyers' parts (finite quantities). Two greedy strategies, best kept."""
    best = (0.0, 0.0, [])
    for strategy in ("bigfirst", "global"):
        b = MaxRectsBin(UW, UH, True)
        b.free = list(free)
        left = [q for (_, _, _, q) in demand]
        placed = []
        if strategy == "bigfirst":
            for i in sorted(range(len(demand)), key=lambda i: -(demand[i][1] * demand[i][2])):
                _, a, bb, _ = demand[i]
                while left[i] > 0:
                    res = b.insert(a + K, bb + K)
                    if res is None:
                        break
                    placed.append((i, res[0], res[1]))
                    left[i] -= 1
        else:
            while True:
                pick = None
                for i, (_, a, bb, _) in enumerate(demand):
                    if left[i] <= 0:
                        continue
                    f = b._find_position(a + K, bb + K)
                    if f is None:
                        continue
                    key = (f[0], f[1], -(a * bb))
                    if pick is None or key < pick[0]:
                        pick = (key, i)
                if pick is None:
                    break
                i = pick[1]
                _, a, bb, _ = demand[i]
                res = b.insert(a + K, bb + K)
                placed.append((i, res[0], res[1]))
                left[i] -= 1
        value = sum(part_value(demand[i][1], demand[i][2], t) for i, _, _ in placed)
        area = sum((demand[i][1] + K) * (demand[i][2] + K) for i, _, _ in placed)
        # judge by net gain over scrapping the same area, so the comparison matches total()
        if value - area * t * P["rho"] * P["p_scrap"] > best[0] - best[1] * t * P["rho"] * P["p_scrap"] + 1e-9:
            best = (value, area, placed)
    return best


def fill_precut(free, demand, t):
    """Before the cut, any post-hoc crop is also possible, so keep the best of: all free space,
    the two largest disjoint remnants, the single largest remnant."""
    options = [fill(free, demand, t), fill(two_remnants(free), demand, t), fill([largest(free)] if free else [], demand, t)]
    return max(options, key=lambda o: o[0] - o[1] * t * P["rho"] * P["p_scrap"])


def total(fill_result, leftover_area, t):
    value, area, placed = fill_result
    scrap = max(0.0, leftover_area - area) * t * P["rho"] * P["p_scrap"]
    pickup = P["pickup"] if placed else 0.0
    return value + scrap - pickup, len(placed)


LARGE = [c for c in CATALOGUE if c[1] * c[2] >= 300 * 200]
REGIMES = {
    "mixed-scarce": (CATALOGUE, 1, 8),
    "mixed-balanced": (CATALOGUE, 4, 20),
    "mixed-plentiful": (CATALOGUE, 15, 40),
    "large-scarce": (LARGE, 1, 6),
    "large-balanced": (LARGE, 3, 12),
}


def instance(arg):
    seed, regime = arg
    cat, qlo, qhi = REGIMES[regime]
    rng = random.Random(seed)
    while True:
        t = rng.choice([3, 5, 6, 8, 10])
        a, b = rng.randrange(100, 810, 10), rng.randrange(100, 810, 10)
        a, b = max(a, b), min(a, b)
        frac = rng.uniform(0.30, 0.80)
        n = max(1, round(frac * UW * UH / ((a + K) * (b + K))))
        lays = layouts(a, b, n)
        if lays:
            break
    k = rng.randint(2, min(5, len(cat)))
    types = rng.sample(cat, k)
    demand = [(name, pa, pb, rng.randint(qlo, qhi)) for name, pa, pb in types]
    leftover_area = L * W - n * (a + K) * (b + K)
    scrap_only = leftover_area * t * P["rho"] * P["p_scrap"]

    rows = []
    for lay in lays:
        free = free_after(lay["blocks"])
        rows.append(dict(lay=lay, free=free, big=(largest(free).w * largest(free).h) if free else 0.0))
    compact = min(rows, key=lambda r: (r["lay"]["bbox"], -r["big"]))
    remnant = max(rows, key=lambda r: (r["big"], -r["lay"]["bbox"]))

    def post(row, k2):
        rems = [largest(row["free"])] if k2 == 1 else two_remnants(row["free"])
        return total(fill([r for r in rems if r is not None], demand, t), leftover_area, t)

    res = dict(seed=seed, regime=regime, t=t, a=a, b=b, n=n, demand=demand, layouts=len(lays),
               seller_frac=n * (a + K) * (b + K) / (UW * UH),
               demand_area_ratio=sum((pa + K) * (pb + K) * q for _, pa, pb, q in demand) / leftover_area,
               scrap_only=scrap_only)
    res["post1_compact"], _ = post(compact, 1)
    res["post2_compact"], _ = post(compact, 2)
    res["post1_remnant"], _ = post(remnant, 1)
    res["post2_remnant"], _ = post(remnant, 2)
    res["precut_compact"], res["parts_precut_compact"] = total(fill_precut(compact["free"], demand, t), leftover_area, t)
    res["precut_remnant"], res["parts_precut_remnant"] = total(fill_precut(remnant["free"], demand, t), leftover_area, t)
    best = None
    for row in rows:
        v, cnt = total(fill_precut(row["free"], demand, t), leftover_area, t)
        if best is None or v > best[0] + 1e-9:
            best = (v, cnt, row["lay"])
    res["shadow"], res["parts_shadow"] = best[0], best[1]
    res["shadow_layout"] = {k3: best[2][k3] for k3 in ("kind", "rot", "c", "r")}
    res["remnant_layout"] = {k3: remnant["lay"][k3] for k3 in ("kind", "rot", "c", "r")}
    return res


if __name__ == "__main__":
    n_inst = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    out = sys.argv[2] if len(sys.argv) > 2 else "results.json"
    args = [(1000 + i, reg) for reg in REGIMES for i in range(n_inst)]
    with Pool(2) as pool:
        results = pool.map(instance, args, chunksize=8)
    with open(out, "w") as f:
        json.dump(dict(params=PARAMS, results=results), f)
    print("done", len(results))
