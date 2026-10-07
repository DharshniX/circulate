"""
Flat-material engine: sheets and offcuts of steel, stainless, aluminium,
plywood, acrylic. One algorithm (MaxRects nesting), many material profiles.

Rules it enforces:
  - same material, thickness (within tolerance), an accepted grade and condition
  - the buyer's machine bed must take the piece, if a limit is given
  - grained materials: if the part's grain matters, the part's long side runs
    along the piece's grain, and rotation is switched off accordingly
  - defects on a piece are never cut through
"""
from __future__ import annotations

from dataclasses import asdict

from .. import materials as M
from ..models import FlatDemand, FlatPiece, Settings
from ..nesting import nest_identical_parts, verify_placements
from .base import Engine, Take


def _grain_setup(p: FlatPiece, d: FlatDemand):
    """Return (part_length_mm, part_width_mm, allow_rotation) respecting grain."""
    mat = M.get(p.material)
    if not (mat.has_grain and d.grain_matters):
        return d.length_mm, d.width_mm, d.allow_rotation
    long_side, short_side = max(d.length_mm, d.width_mm), min(d.length_mm, d.width_mm)
    if p.grain_along_length is False:
        return short_side, long_side, False  # grain runs across: long side goes across
    return long_side, short_side, False


class FlatEngine(Engine):
    kind = "flat"
    unit_name = "parts"
    time_unit = "days"

    def salvage_key(self, p: FlatPiece) -> str:
        return p.material

    def relevant(self, p, d):
        return M.get(p.material).family == M.get(d.material).family

    def check(self, p: FlatPiece, d: FlatDemand, seller, s: Settings):
        if p.material != d.material:
            return f"material {M.get(p.material).name}, part needs {M.get(d.material).name}"
        if abs(p.thickness_mm - d.thickness_mm) > s.thickness_tolerance_mm + 1e-9:
            return f"thickness {p.thickness_mm} mm, part needs {d.thickness_mm} mm"
        if p.grade.strip().upper() not in d.accepted_grades:
            return f"grade {p.grade} not accepted ({', '.join(d.accepted_grades)})"
        if p.condition not in d.accepted_conditions:
            return f"condition '{p.condition}' not accepted"
        if M.get(p.material).has_grain and d.grain_matters and p.grain_along_length is None:
            return "grain direction not recorded, and this part needs it"
        if d.machine_max_length_mm and d.machine_max_width_mm:
            L, W = max(p.length_mm, p.width_mm), min(p.length_mm, p.width_mm)
            if L > max(d.machine_max_length_mm, d.machine_max_width_mm) or W > min(d.machine_max_length_mm, d.machine_max_width_mm):
                return "larger than the buyer's machine bed"
        if self.capacity(p, d, s) == 0:
            return "no part fits after edge margin, kerf and defects"
        return None

    def _nest(self, p: FlatPiece, d: FlatDemand, s: Settings, cap=None):
        pl, pw, rot = _grain_setup(p, d)
        defects = [(x.x_mm, x.y_mm, x.length_mm, x.width_mm) for x in p.defects]
        return nest_identical_parts(p.length_mm, p.width_mm, pl, pw, max_parts=cap, kerf_mm=s.kerf_mm,
                                    edge_margin_mm=s.edge_margin_mm, allow_rotation=rot, defects=defects)

    def capacity(self, p, d, s):
        return len(self._nest(p, d, s))

    def take(self, p, d, units, s):
        pl = self._nest(p, d, s, cap=int(units))
        return Take(units=len(pl), shipped_kg=self.resource_kg(p, s),
                    detail={"shape": "sheet", "length_mm": p.length_mm, "width_mm": p.width_mm,
                            "defects": [x.model_dump() for x in p.defects],
                            "parts": [asdict(x) for x in pl]})

    def verify(self, p, d, t, s):
        from ..nesting import Placement
        pls = [Placement(**x) for x in t.detail["parts"]]
        defects = [(x.x_mm, x.y_mm, x.length_mm, x.width_mm) for x in p.defects]
        return verify_placements(p.length_mm, p.width_mm, pls, s.kerf_mm, s.edge_margin_mm, defects)

    def resource_kg(self, p, s):
        return p.length_mm * p.width_mm * p.thickness_mm * M.get(p.material).density_kg_per_mm3

    def unit_kg(self, d, s):
        return d.length_mm * d.width_mm * d.thickness_mm * M.get(d.material).density_kg_per_mm3

    def buyer_value(self, d, units, s):
        return units * self.unit_kg(d, s) / d.buyer_utilisation * d.new_price_per_kg - units * d.handling_cost_per_unit

    def order_key(self, p):
        return (p.is_full_sheet, p.length_mm * p.width_mm, p.id)  # offcuts first, smallest first

    def counterfactual(self, p):
        return M.get(p.material).counterfactual
