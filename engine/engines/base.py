"""
The contract every resource engine follows.

The core (pooling, recovery plan, deals, impact) only talks to engines
through these methods, so a new material type is added by writing one new
engine file and registering it. Nothing in the core changes.

To add an engine:
  1. Create engines/<name>.py with a class that subclasses Engine.
  2. Implement the methods below.
  3. Add an instance to REGISTRY in engines/__init__.py.
  4. Add resource and demand models with the same `kind` in models.py.
  5. Write tests in tests/ for check, capacity, take and verify.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Take:
    units: float  # parts, cut pieces, or kg allocated from this resource
    shipped_kg: float  # mass that physically moves to the buyer
    detail: dict[str, Any] = field(default_factory=dict)  # drawing data (placements, cuts ...)


class Engine:
    kind: str = ""
    unit_name: str = "units"
    time_unit: str = "days"  # unit for recovery-plan windows
    integral_units: bool = True  # parts are whole numbers; kg are not

    def salvage_key(self, resource) -> str:
        """Which of the seller's salvage offers applies (material or food category)."""
        raise NotImplementedError

    def relevant(self, resource, demand) -> bool:
        """Is this resource even the same kind of thing as the order? Irrelevant ones are skipped
        silently; relevant-but-unsuitable ones are rejected with a reason."""
        return True

    def check(self, resource, demand, seller, settings) -> str | None:
        """None if this resource can serve this demand, else a plain-language reason."""
        raise NotImplementedError

    def capacity(self, resource, demand, settings) -> float:
        """How many units this resource can supply for this demand."""
        raise NotImplementedError

    def take(self, resource, demand, units: float, settings) -> Take:
        """Allocate up to `units` from this resource and describe how."""
        raise NotImplementedError

    def verify(self, resource, demand, take: Take, settings) -> list[str]:
        """Independent check of a Take. Empty list = valid."""
        return []

    def resource_kg(self, resource, settings) -> float:
        raise NotImplementedError

    def unit_kg(self, demand, settings) -> float:
        """Mass embodied in one delivered unit (part, cut piece, or 1 kg)."""
        raise NotImplementedError

    def buyer_value(self, demand, units: float, settings) -> float:
        """What the same units would cost the buyer from their usual source."""
        raise NotImplementedError

    def order_key(self, resource):
        """Allocation order: which resources to use first."""
        return resource.id

    def route_ok(self, used_resources: list, demand, tour_hours: float) -> str | None:
        """Optional check after the pickup tour is known (e.g. food must arrive in time)."""
        return None

    def counterfactual(self, resource) -> str:
        """What happens to this resource without CIRCULATE (decides impact claims)."""
        return "recycled"
