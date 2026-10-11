"""
Material profiles for the flat-sheet and bar/tube engines.

Densities are typical physical values (kg per m3 converted to kg per mm3).
Real stock varies (plywood especially); override per piece if you measure it.

counterfactual = what most likely happens to this material if CIRCULATE
does nothing. It decides what impact we are allowed to claim.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Material:
    key: str
    name: str
    density_kg_per_mm3: float
    family: str  # metal | wood | plastic
    has_grain: bool  # True: part orientation may be restricted by grain direction
    counterfactual: str  # recycled | uncertain


MATERIALS: dict[str, Material] = {
    "mild_steel": Material("mild_steel", "Mild steel", 7850e-9, "metal", False, "recycled"),
    "stainless_304": Material("stainless_304", "Stainless steel 304", 8000e-9, "metal", False, "recycled"),
    "aluminium": Material("aluminium", "Aluminium", 2700e-9, "metal", False, "recycled"),
    "plywood": Material("plywood", "Plywood (typical)", 600e-9, "wood", True, "uncertain"),
    "acrylic": Material("acrylic", "Acrylic sheet", 1190e-9, "plastic", False, "uncertain"),
    # Construction-site stock. Shuttering plywood uses the same typical plywood density (film-faced boards are
    # somewhat heavier; weigh a board to override). Rebar is steel and is recycled anyway: the claim is reuse.
    "tmt_rebar": Material("tmt_rebar", "TMT rebar", 7850e-9, "metal", False, "recycled"),
    "shuttering_plywood": Material("shuttering_plywood", "Shuttering plywood (film-faced)", 600e-9, "wood", True, "uncertain"),
}

# ---------------- construction-site stock ----------------
BAR_MATERIALS = ("tmt_rebar",)  # sold as bars (the bar and tube engine), not sheets
REBAR_GRADES = ("Fe415", "Fe500", "Fe500D", "Fe550", "Fe550D")
REBAR_DIAMETERS_MM = (8, 10, 12, 16, 20, 25, 32)
REBAR_WEIGHT_NOTE = "Weight per metre = diameter squared / 162 kg, the standard site rule. An assumption, not a weighed value."
SHUTTERING_THICKNESSES_MM = (12, 18)
USE_CONDITIONS = ("new", "used_once", "used_many_times")  # shuttering plywood is reused on site


def rebar_kg_per_m(diameter_mm: float) -> float:
    """The site rule for TMT bars: kg per metre = d x d / 162 (d in mm)."""
    return diameter_mm * diameter_mm / 162


def rebar_profile(diameter_mm: float) -> str:
    return f"TMT bar {diameter_mm:g}"


def rebar_grade(text: str) -> str | None:
    """'fe500d' -> 'Fe500D'; None if it is not a TMT grade."""
    return next((g for g in REBAR_GRADES if g.upper() == text.strip().upper()), None)


def get(key: str) -> Material:
    try:
        return MATERIALS[key]
    except KeyError:
        raise ValueError(f"unknown material '{key}'. Known: {', '.join(sorted(MATERIALS))}")
