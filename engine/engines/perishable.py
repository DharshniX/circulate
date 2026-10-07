"""
Perishable engine: food-business surplus (ingredients, packaged food,
bakery, cooked meals) and kitchen by-products (used cooking oil).

Food is not steel. The unit is kg, the clock is in hours, and safety rules
come before value:
  - storage must match: a chilled lot can only go to a buyer who can chill it
  - the lot must arrive with at least the buyer's minimum hours left,
    counting the actual pickup tour time
  - selling to a food business: both sides need FSSAI licence/registration
  - cooked meals: donation to an NGO only (platform policy, not a legal ruling)
  - used cooking oil: only to an FSSAI RUCO-enrolled collector
    (FSSAI requires large users to dispose of UCO through authorised agencies)
These are platform rules for the prototype; confirm them with a food-safety
expert before real use.

Urgency is handled by hours: lots that would arrive with too little time are
excluded, the pickup tour time is checked, and the recovery plan runs in
hours. A time-based markdown curve needs real sell-through data to calibrate;
it is on the roadmap rather than guessed here.
"""
from __future__ import annotations

from ..models import PerishableDemand, PerishableLot, Settings
from .base import Engine, Take



class PerishableEngine(Engine):
    kind = "perishable"
    unit_name = "kg"
    time_unit = "hours"
    integral_units = False

    def salvage_key(self, lot: PerishableLot) -> str:
        return lot.category

    def relevant(self, lot, d):
        # show the used-cooking-oil rule whenever UCO is present; otherwise only categories the buyer takes
        return lot.category in d.categories or lot.category == "used_cooking_oil" or d.buyer_type == "ruco_collector"

    def check(self, lot: PerishableLot, d: PerishableDemand, seller, s: Settings):
        if lot.category == "used_cooking_oil":
            if d.buyer_type != "ruco_collector":
                return "used cooking oil may only go to an FSSAI RUCO-enrolled collector"
            return None
        if d.buyer_type == "ruco_collector":
            return "RUCO collectors take used cooking oil only"
        if lot.category not in d.categories:
            return f"buyer does not take {lot.category.replace('_', ' ')}"
        if lot.storage not in d.storage_available:
            return f"needs {lot.storage} storage; buyer has {', '.join(d.storage_available)}"
        if lot.category == "cooked_meal" and d.buyer_type != "ngo":
            return "cooked meals go to donation only (platform policy)"
        if d.buyer_type == "food_business" and not (seller.fssai_licensed and d.buyer_fssai_licensed):
            return "food-business sale needs FSSAI licence or registration on both sides"
        if d.buyer_type == "ngo" and not seller.fssai_licensed:
            return "donor must hold FSSAI licence or registration"
        if lot.hours_left < d.min_hours_left_on_arrival:
            return f"only {lot.hours_left:.0f} h left; buyer needs {d.min_hours_left_on_arrival:.0f} h on arrival"
        return None

    def capacity(self, lot, d, s):
        return lot.quantity_kg

    def take(self, lot, d, units, s):
        kg = round(min(units, lot.quantity_kg), 3)
        return Take(units=kg, shipped_kg=kg,
                    detail={"shape": "lot", "quantity_kg": lot.quantity_kg, "taken_kg": kg,
                            "hours_left": lot.hours_left, "storage": lot.storage})

    def verify(self, lot, d, t, s):
        return [] if 0 <= t.units <= lot.quantity_kg + 1e-9 else ["took more than the lot holds"]

    def resource_kg(self, lot, s):
        return lot.quantity_kg

    def unit_kg(self, d, s):
        return 1.0

    def buyer_value(self, d, units, s):
        return units * d.normal_price_per_kg

    def order_key(self, lot):
        return (lot.hours_left, lot.id)  # soonest to expire goes first

    def route_ok(self, used, d, tour_hours):
        for lot in used:
            if lot.category == "used_cooking_oil":
                continue
            if lot.hours_left - tour_hours < d.min_hours_left_on_arrival:
                return f"{lot.product} would arrive with {lot.hours_left - tour_hours:.1f} h left"
        return None

    def counterfactual(self, lot):
        return "ruco_collection" if lot.category == "used_cooking_oil" else "disposed"
