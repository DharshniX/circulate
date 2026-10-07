"""
Bar and tube engine: one-dimensional cutting of rods, pipes, tubes, angles.

For one cut length per order the best plan is exact and simple:
  usable length = bar length - 2 x end trim
  cuts per bar  = floor((usable + kerf) / (cut length + kerf))
The leftover is reported, and kept as usable stock if it is at least
`min_usable_remnant_mm` long (so it can be offered to the next buyer).
Mixed cut lengths in one order (a true cutting-stock problem) are on the
roadmap; they need first-fit-decreasing or an integer program.
"""
from __future__ import annotations

import math

from .. import materials as M
from ..models import LinearDemand, LinearStock, Settings
from .base import Engine, Take


def _norm(profile: str) -> str:
    return " ".join(profile.lower().replace("×", "x").split())


class LinearEngine(Engine):
    kind = "linear"
    unit_name = "cut pieces"
    time_unit = "days"

    def salvage_key(self, b: LinearStock) -> str:
        return b.material

    def relevant(self, b, d):
        # same material and same section family (e.g. "pipe 26.9"), so a wrong wall thickness is still shown
        return b.material == d.material and _norm(b.profile).split()[:2] == _norm(d.profile).split()[:2]

    def check(self, b: LinearStock, d: LinearDemand, seller, s: Settings):
        if b.material != d.material:
            return f"material {M.get(b.material).name}, order needs {M.get(d.material).name}"
        if _norm(b.profile) != _norm(d.profile):
            return f"profile '{b.profile}', order needs '{d.profile}'"
        if abs(b.mass_per_m_kg - d.mass_per_m_kg) > 0.05 * d.mass_per_m_kg:
            return f"{b.mass_per_m_kg} kg/m differs from the order's {d.mass_per_m_kg} kg/m (wall thickness?)"
        if b.grade.strip().upper() not in d.accepted_grades:
            return f"grade {b.grade} not accepted"
        if b.condition not in d.accepted_conditions:
            return f"condition '{b.condition}' not accepted"
        if self.capacity(b, d, s) == 0:
            return f"too short: {b.length_mm:.0f} mm after {s.end_trim_mm:.0f} mm end trims"
        return None

    def capacity(self, b, d, s):
        usable = b.length_mm - 2 * s.end_trim_mm
        if usable < d.cut_length_mm:
            return 0
        return int(math.floor((usable + s.kerf_mm + 1e-9) / (d.cut_length_mm + s.kerf_mm)))

    def take(self, b, d, units, s):
        n = min(int(units), self.capacity(b, d, s))
        cuts = [{"start_mm": s.end_trim_mm + i * (d.cut_length_mm + s.kerf_mm), "length_mm": d.cut_length_mm}
                for i in range(n)]
        used_to = (cuts[-1]["start_mm"] + d.cut_length_mm + s.kerf_mm) if cuts else 0.0
        remnant = max(0.0, b.length_mm - used_to - s.end_trim_mm) if cuts else b.length_mm
        return Take(units=n, shipped_kg=self.resource_kg(b, s),
                    detail={"shape": "bar", "length_mm": b.length_mm, "end_trim_mm": s.end_trim_mm,
                            "cuts": cuts, "remnant_mm": round(remnant, 1),
                            "remnant_usable": remnant >= s.min_usable_remnant_mm})

    def verify(self, b, d, t, s):
        problems, prev_end = [], s.end_trim_mm - s.kerf_mm
        for i, c in enumerate(t.detail["cuts"]):
            if c["start_mm"] < prev_end + s.kerf_mm - 1e-6:
                problems.append(f"cut {i} overlaps the previous cut")
            prev_end = c["start_mm"] + c["length_mm"]
        if t.detail["cuts"] and prev_end > b.length_mm - s.end_trim_mm + 1e-6:
            problems.append("cuts run into the end trim")
        return problems

    def resource_kg(self, b, s):
        return b.length_mm / 1000 * b.mass_per_m_kg

    def unit_kg(self, d, s):
        return d.cut_length_mm / 1000 * d.mass_per_m_kg

    def buyer_value(self, d, units, s):
        return units * self.unit_kg(d, s) / d.buyer_utilisation * d.new_price_per_kg - units * d.handling_cost_per_unit

    def order_key(self, b):
        return (b.is_full_length, b.length_mm, b.id)  # shortest ends first

    def counterfactual(self, b):
        return M.get(b.material).counterfactual
