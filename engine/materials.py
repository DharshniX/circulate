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
}


def get(key: str) -> Material:
    try:
        return MATERIALS[key]
    except KeyError:
        raise ValueError(f"unknown material '{key}'. Known: {', '.join(sorted(MATERIALS))}")
