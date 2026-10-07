"""
Impact for one deal, against a counterfactual that depends on the material.

  metals (steel, stainless, aluminium)  -> would have been RECYCLED as scrap.
      Claim: material kept in use, new material not bought, remelting avoided.
      Never claim landfill diversion.
  plywood, acrylic                      -> UNCERTAIN (burned, dumped, sold on).
      Claim only material kept in use until the seller's actual disposal route is recorded.
  food                                  -> DISPOSED (bin, landfill or compost; record which).
      Claim food kept in human use. Landfill diversion only if the seller's
      recorded disposal route is landfill.
  used cooking oil                      -> RUCO collection. Claim compliant collection only.

CO2 lines need cited emission factors. Without a factor the line stays
empty: we never type in a number without a source.

Labels:  verified  - real deal, delivered, evidence + buyer confirmation
         simulated - demo deal
         modelled  - computed from a plan or assumption
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EmissionFactor(BaseModel):
    value: float = Field(ge=0)
    unit: str
    source: str = Field(min_length=5)


class DealImpactInput(BaseModel):
    kind: Literal["flat", "linear", "perishable"]
    counterfactual: Literal["recycled", "uncertain", "disposed", "landfill", "ruco_collection"]
    units: float = Field(ge=0)
    unit_name: str
    kg_in_units: float = Field(ge=0)
    kg_shipped: float = Field(ge=0)
    route_km: float = Field(ge=0)
    value_above_salvage: float
    buyer_utilisation: float | None = Field(None, gt=0, le=1)
    is_simulated: bool = True
    status: Literal["agreed", "dispatched", "delivered"] = "agreed"
    evidence: list[str] = []
    buyer_confirmed: bool = False
    ef_avoided: EmissionFactor | None = None  # kgCO2e per kg kept in use (remelt avoided, or food production)
    ef_truck: EmissionFactor | None = None  # kgCO2e per tonne-km


def label_of(d: DealImpactInput) -> str:
    if d.is_simulated:
        return "simulated"
    if d.status == "delivered" and d.evidence and d.buyer_confirmed:
        return "verified"
    return "modelled"


def compute(d: DealImpactInput) -> dict:
    lab = label_of(d)
    food = d.kind == "perishable"
    m = [
        {"metric": f"{d.unit_name.capitalize()} delivered", "value": d.units, "unit": d.unit_name, "label": lab},
        {"metric": "Food kept in human use" if food else "Material kept in use (in finished pieces)",
         "value": round(d.kg_in_units, 2), "unit": "kg", "label": lab},
        {"metric": "Value recovered above salvage", "value": round(d.value_above_salvage, 2), "unit": "INR", "label": lab},
    ]
    if d.buyer_utilisation:
        m.append({"metric": "New material the buyer did not need to buy", "value": round(d.kg_in_units / d.buyer_utilisation, 2),
                  "unit": "kg", "label": "modelled", "note": "uses the buyer's own utilisation figure"})
    landfill = d.kg_in_units if d.counterfactual == "landfill" else 0
    notes = {"recycled": "counterfactual is recycling: no landfill diversion claimed",
             "uncertain": "seller's disposal route not recorded: no landfill diversion claimed",
             "disposed": "record whether the seller's waste goes to landfill or compost before claiming diversion",
             "landfill": "seller's recorded disposal route is landfill",
             "ruco_collection": "used cooking oil: compliant collection, not diversion"}
    m.append({"metric": "Landfill diverted", "value": landfill, "unit": "kg", "label": lab if landfill else "not claimed",
              "note": notes[d.counterfactual]})
    avoided = d.kg_in_units * d.ef_avoided.value if d.ef_avoided else None
    truck = d.kg_shipped / 1000 * d.route_km * d.ef_truck.value if d.ef_truck else None
    m.append({"metric": "Emissions avoided", "value": None if avoided is None else round(avoided, 2), "unit": "kgCO2e",
              "label": lab if avoided is not None else "missing factor",
              "source": d.ef_avoided.source if d.ef_avoided else "add a cited factor"})
    m.append({"metric": "Transport emissions added", "value": None if truck is None else round(truck, 2), "unit": "kgCO2e",
              "label": lab if truck is not None else "missing factor",
              "source": d.ef_truck.source if d.ef_truck else "add a cited factor"})
    net = avoided - truck if (avoided is not None and truck is not None) else None
    m.append({"metric": "Net emissions avoided", "value": None if net is None else round(net, 2), "unit": "kgCO2e",
              "label": lab if net is not None else "missing factor"})
    return {"label": lab, "counterfactual": d.counterfactual, "metrics": m}
