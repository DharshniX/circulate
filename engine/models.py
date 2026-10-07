"""
Input models for every resource category.

The platform has ONE core (sellers, buyers, pooling, recovery plan, deals,
impact) and several ENGINES, one per kind of resource, because the maths is
genuinely different:
  flat        sheets and offcuts      2D nesting       (steel, aluminium, plywood ...)
  linear      bars, tubes, pipes      1D cutting       (lengths)
  perishable  food and kitchen stock  shelf-life rules (hours, storage, food safety)

Every resource and every demand has a `kind` that picks its engine.
Every outside number carries a `label`:
  measured   - taken at a real business (tape measure, real quote)
  sample     - made up for the demo
  assumption - a stated assumption (e.g. chance a buyer accepts)
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator

DataLabel = Literal["measured", "sample", "assumption"]
Condition = Literal["new", "good", "surface_rust", "bent", "damaged"]


class Settings(BaseModel):
    kerf_mm: float = Field(3.0, ge=0, le=20, description="Material removed by a cut")
    edge_margin_mm: float = Field(5.0, ge=0, le=100, description="Strip never cut along each sheet edge")
    end_trim_mm: float = Field(10.0, ge=0, le=200, description="Length trimmed off each bar end")
    thickness_tolerance_mm: float = Field(0.0, ge=0, le=1)
    min_usable_remnant_mm: float = Field(300.0, ge=0, description="Bar leftovers at least this long are kept as stock")


class SalvageOffer(BaseModel):
    """What the seller gets if nothing better happens: scrap price, or a disposal cost (negative)."""

    applies_to: str = Field(description="material key, or food category")
    price_per_kg: float = Field(ge=-100)
    quoted_on: date | None = None
    executable: bool = Field(False, description="True only for a current, firm offer for this lot")
    label: DataLabel = "sample"


class Seller(BaseModel):
    id: str
    name: str
    x_km: float = 0.0
    y_km: float = 0.0
    salvage: list[SalvageOffer]
    fssai_licensed: bool = False
    label: DataLabel = "sample"

    def salvage_for(self, key: str) -> SalvageOffer:
        for s in self.salvage:
            if s.applies_to == key:
                return s
        raise ValueError(f"seller {self.id} has no salvage price for '{key}'")


class Defect(BaseModel):
    x_mm: float = Field(ge=0)
    y_mm: float = Field(ge=0)
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    note: str = ""


# ---------------- resources ----------------
class FlatPiece(BaseModel):
    kind: Literal["flat"] = "flat"
    id: str
    seller_id: str
    material: str
    length_mm: float = Field(gt=0, le=12000)
    width_mm: float = Field(gt=0, le=4000)
    thickness_mm: float = Field(gt=0, le=100)
    grade: str = Field(min_length=1)
    condition: Condition = "good"
    is_full_sheet: bool = False
    grain_along_length: bool | None = None  # for grained materials (plywood)
    defects: list[Defect] = []
    label: DataLabel = "sample"

    @model_validator(mode="after")
    def defects_inside(self):
        for d in self.defects:
            if d.x_mm + d.length_mm > self.length_mm or d.y_mm + d.width_mm > self.width_mm:
                raise ValueError(f"defect on {self.id} lies outside the piece")
        return self


class LinearStock(BaseModel):
    kind: Literal["linear"] = "linear"
    id: str
    seller_id: str
    material: str
    profile: str = Field(min_length=1, description="e.g. 'pipe 26.9 x 2.0' or 'round bar 16'")
    length_mm: float = Field(gt=0, le=13000)
    mass_per_m_kg: float = Field(gt=0, le=500)
    grade: str = Field(min_length=1)
    condition: Condition = "good"
    is_full_length: bool = False
    label: DataLabel = "sample"


FoodCategory = Literal["vegetables", "fruit", "dairy", "bakery", "packaged", "cooked_meal", "used_cooking_oil"]
Storage = Literal["ambient", "chilled", "frozen"]


class PerishableLot(BaseModel):
    kind: Literal["perishable"] = "perishable"
    id: str
    seller_id: str
    product: str
    category: FoodCategory
    quantity_kg: float = Field(gt=0, le=10000)
    storage: Storage
    hours_left: float = Field(ge=0, le=24 * 365, description="Hours until use-by")
    label: DataLabel = "sample"


Resource = Annotated[Union[FlatPiece, LinearStock, PerishableLot], Field(discriminator="kind")]


# ---------------- demands ----------------
class _DemandBase(BaseModel):
    id: str
    buyer_id: str
    buyer_name: str
    x_km: float = 0.0
    y_km: float = 0.0
    label: DataLabel = "sample"


class FlatDemand(_DemandBase):
    kind: Literal["flat"] = "flat"
    part_name: str
    material: str
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    thickness_mm: float = Field(gt=0)
    accepted_grades: list[str] = Field(min_length=1)
    accepted_conditions: list[str] = ["new", "good", "surface_rust"]
    quantity: int = Field(gt=0, le=100000)
    allow_rotation: bool = True
    grain_matters: bool = False  # part's long side must run along the grain
    machine_max_length_mm: float | None = None  # largest piece the buyer's machine bed takes
    machine_max_width_mm: float | None = None
    new_price_per_kg: float = Field(gt=0)
    buyer_utilisation: float = Field(0.75, gt=0.2, le=1.0)
    handling_cost_per_unit: float = Field(0.0, ge=0)

    @field_validator("accepted_grades")
    @classmethod
    def norm(cls, v):
        return [g.strip().upper() for g in v if g.strip()]


class LinearDemand(_DemandBase):
    kind: Literal["linear"] = "linear"
    part_name: str
    material: str
    profile: str
    mass_per_m_kg: float = Field(gt=0, le=500, description="From the section table for this profile")
    cut_length_mm: float = Field(gt=0)
    accepted_grades: list[str] = Field(min_length=1)
    accepted_conditions: list[str] = ["new", "good", "surface_rust"]
    quantity: int = Field(gt=0, le=100000)
    new_price_per_kg: float = Field(gt=0)
    buyer_utilisation: float = Field(0.9, gt=0.2, le=1.0)
    handling_cost_per_unit: float = Field(0.0, ge=0)

    @field_validator("accepted_grades")
    @classmethod
    def norm(cls, v):
        return [g.strip().upper() for g in v if g.strip()]


class PerishableDemand(_DemandBase):
    kind: Literal["perishable"] = "perishable"
    part_name: str = "kg"
    buyer_type: Literal["food_business", "ngo", "ruco_collector"]
    categories: list[FoodCategory] = Field(min_length=1)
    storage_available: list[Storage] = Field(min_length=1)
    quantity: float = Field(gt=0, le=10000, description="kg needed")
    min_hours_left_on_arrival: float = Field(6, ge=0)
    normal_price_per_kg: float = Field(ge=0, description="What the buyer pays its usual supplier (0 for donations)")
    buyer_fssai_licensed: bool = False


Demand = Annotated[Union[FlatDemand, LinearDemand, PerishableDemand], Field(discriminator="kind")]


class Vehicle(BaseModel):
    name: str
    payload_kg: float = Field(gt=0)
    fixed_cost_per_trip: float = Field(ge=0)
    cost_per_km: float = Field(ge=0)


def _default_vehicles():
    return [Vehicle(name="two-wheeler", payload_kg=40, fixed_cost_per_trip=80, cost_per_km=6),
            Vehicle(name="goods auto", payload_kg=400, fixed_cost_per_trip=200, cost_per_km=14),
            Vehicle(name="mini truck", payload_kg=1500, fixed_cost_per_trip=500, cost_per_km=25)]


class Transport(BaseModel):
    """The cheapest vehicle that can carry the load is used. All rates are sample values."""

    vehicles: list[Vehicle] = Field(default_factory=_default_vehicles, min_length=1)
    rate_multiplier: float = Field(1.0, ge=0, le=20, description="Scales every rate (fuel price, hire market)")
    cost_per_stop: float = Field(60.0, ge=0, description="Loading time at each extra pickup")
    speed_kmph: float = Field(20.0, gt=0, description="Average city speed, for time-critical goods")
    label: DataLabel = "sample"


class Route(BaseModel):
    id: str
    name: str
    kind: Literal["direct_buyer", "pooled_order", "stockist", "donation", "other"]
    value_to_seller: float
    p_close: float = Field(ge=0, le=1)
    window: int = Field(ge=1, le=720, description="Length of the attempt, in the plan's time unit")
    p_label: DataLabel = "assumption"


class PlanRequest(BaseModel):
    routes: list[Route]
    salvage_value: float
    salvage_executable: bool
    deadline: int = Field(ge=1, le=720)
    time_unit: Literal["days", "hours"] = "days"
    carry_cost_per_unit_time: float = Field(0.0, ge=0)
    rejected_route_ids: list[str] = []
    elapsed: int = Field(0, ge=0)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [r.id for r in self.routes]
        if len(ids) != len(set(ids)):
            raise ValueError("route ids must be unique")
        return self
