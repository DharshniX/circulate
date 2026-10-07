"""Summarise gap_fill_sim.py results. Everything printed is SIMULATED with sample prices."""
import json
import statistics as st
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "research/results.json"
d = json.load(open(path))
R, P = d["results"], d["params"]
L, W, K, rho = P["sheet_l"], P["sheet_w"], P["kerf"], P["rho"]


def leftover_kg(r):
    return (L * W - r["n"] * (r["a"] + K) * (r["b"] + K)) * r["t"] * rho


def q(vals):
    v = sorted(vals)
    return st.median(v), v[len(v) // 4], v[3 * len(v) // 4]


print(f"{len(R)} simulated sheets; prices: new Rs {P['p_new']}/kg, scrap Rs {P['p_scrap']}/kg (assumptions)")
for key, label in [("post1_compact", "after the cut, ordinary layout, largest remnant kept"),
                   ("post2_remnant", "after the cut, careful layout, two remnants kept"),
                   ("precut_remnant", "Gap Fill before the cut, layout unchanged"),
                   ("shadow", "Gap Fill plus layout shaping")]:
    m, lo, hi = q(r[key] / r["scrap_only"] for r in R)
    print(f"  {label:52s} x{m:.2f} of scrap value (IQR x{lo:.2f}-x{hi:.2f})")
shape = [(r["shadow"] - r["precut_remnant"]) / r["precut_remnant"] * 100 for r in R]
print(f"  shaping adds {st.mean(shape):.1f}% on average; at least 5% on {sum(s >= 5 for s in shape) / len(shape) * 100:.0f}% of sheets")
m, lo, hi = q((r["precut_remnant"] - r["scrap_only"]) / leftover_kg(r) for r in R)
print(f"  gain over scrap: Rs {m:.1f} per kg of leftover (IQR {lo:.1f}-{hi:.1f})")
for t in sorted({r["t"] for r in R}):
    rs = [r for r in R if r["t"] == t]
    print(f"  {t:2d} mm: median gain over scrap Rs {st.median(r['precut_remnant'] - r['scrap_only'] for r in rs):,.0f} per part-used sheet")
