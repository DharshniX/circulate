"""
SAMPLE scenarios for the demo. Every seller, buyer, size and price here is
made up and labelled "sample". Replace with measured data before claiming
any result. Positions are km on a simple local grid; each buyer has its own.
"""
from __future__ import annotations

from .models import (Defect, FlatDemand, FlatPiece, LinearDemand, LinearStock, PerishableDemand, PerishableLot,
                     SalvageOffer, Seller, Settings, Transport)


def _sal(key, price):
    return SalvageOffer(applies_to=key, price_per_kg=price, executable=False, label="sample")


# ---------------- 1. mild steel sheet ----------------
def steel():
    sal = [_sal("mild_steel", 30.0)]
    sellers = [Seller(id="A", name="Fabricator A", x_km=2.0, y_km=1.5, salvage=sal),
               Seller(id="B", name="Machine shop B", x_km=-3.0, y_km=2.0, salvage=sal),
               Seller(id="C", name="Panel works C", x_km=4.5, y_km=-4.0, salvage=sal),
               Seller(id="D", name="Fabricator D", x_km=-6.0, y_km=-7.5, salvage=sal)]

    def p(i, s, L, W, t=2.0, grade="E250", cond="good", full=False, defects=()):
        return FlatPiece(id=f"{s}{i}", seller_id=s, material="mild_steel", length_mm=L, width_mm=W, thickness_mm=t,
                         grade=grade, condition=cond, is_full_sheet=full, defects=list(defects))
    rust = Defect(x_mm=300, y_mm=200, length_mm=220, width_mm=180, note="rust patch")
    res = [p(1, "A", 2500, 1250, full=True), p(2, "A", 2500, 1250, full=True), p(3, "A", 1250, 600),
           p(4, "A", 900, 700, defects=[rust]), p(5, "A", 1100, 450), p(6, "A", 800, 800), p(7, "A", 1250, 300),
           p(8, "A", 600, 500), p(9, "A", 700, 400), p(10, "A", 1000, 350), p(11, "A", 500, 500), p(12, "A", 450, 300),
           p(13, "A", 900, 600, t=3.0),
           p(1, "B", 1200, 550), p(2, "B", 850, 650), p(3, "B", 950, 400), p(4, "B", 700, 700), p(5, "B", 600, 300),
           p(6, "B", 1100, 500, cond="bent"), p(7, "B", 750, 450),
           p(1, "C", 1250, 900), p(2, "C", 1000, 600), p(3, "C", 800, 550), p(4, "C", 1250, 400), p(5, "C", 650, 650),
           p(6, "C", 900, 300), p(7, "C", 1200, 700, grade="E350"), p(8, "C", 550, 450),
           p(1, "D", 1250, 1000), p(2, "D", 1100, 800), p(3, "D", 900, 500), p(4, "D", 700, 600), p(5, "D", 1250, 350)]
    dem = [FlatDemand(id="S1", buyer_id="W", buyer_name="Workshop W", part_name="bracket", material="mild_steel",
                      length_mm=150, width_mm=100, thickness_mm=2.0, accepted_grades=["E250"], quantity=600,
                      new_price_per_kg=62.0, buyer_utilisation=0.75),
           FlatDemand(id="S2", buyer_id="Q", buyer_name="Panel maker Q", part_name="base plate", material="mild_steel",
                      length_mm=400, width_mm=300, thickness_mm=2.0, accepted_grades=["E250", "E350"], quantity=40,
                      new_price_per_kg=62.0, buyer_utilisation=0.8, x_km=1.0, y_km=3.0),
           FlatDemand(id="S3", buyer_id="R", buyer_name="Fabricator R", part_name="gusset", material="mild_steel",
                      length_mm=200, width_mm=200, thickness_mm=2.0, accepted_grades=["E250"], quantity=200,
                      new_price_per_kg=62.0, buyer_utilisation=0.78, x_km=-2.0, y_km=-1.0)]
    return sellers, res, dem


# ---------------- 2. aluminium and plywood sheet (same engine, other profiles) ----------------
def sheets():
    sellers = [Seller(id="A", name="Fabricator A", x_km=2.0, y_km=1.5,
                      salvage=[_sal("aluminium", 150.0), _sal("stainless_304", 110.0)]),
               Seller(id="S", name="Signage works S", x_km=-2.5, y_km=3.0, salvage=[_sal("aluminium", 150.0), _sal("acrylic", 15.0)]),
               Seller(id="F", name="Furniture unit F", x_km=3.0, y_km=-3.0, salvage=[_sal("plywood", 4.0)]),
               Seller(id="I", name="Interiors firm I", x_km=-4.0, y_km=-2.0, salvage=[_sal("plywood", 4.0)])]

    def al(i, s, L, W, grade="5052", t=1.5, full=False, mat="aluminium"):
        return FlatPiece(id=f"{s}{i}", seller_id=s, material=mat, length_mm=L, width_mm=W, thickness_mm=t,
                         grade=grade, is_full_sheet=full)

    def ply(i, s, L, W, grain=True, t=18.0):
        return FlatPiece(id=f"{s}{i}", seller_id=s, material="plywood", length_mm=L, width_mm=W, thickness_mm=t,
                         grade="BWR", grain_along_length=grain)
    res = [al("al1", "A", 1250, 600), al("al2", "A", 900, 450), al("al3", "A", 700, 500, grade="6061"),
           al("ss1", "A", 1000, 500, grade="304", mat="stainless_304"),
           al("al1", "S", 1200, 800), al("al2", "S", 800, 600), al("al3", "S", 650, 400),
           ply("p1", "F", 1800, 600), ply("p2", "F", 1200, 450), ply("p3", "F", 900, 700, grain=False),
           ply("p1", "I", 1500, 500), ply("p2", "I", 1000, 600), ply("p3", "I", 1000, 400, grain=None)]
    dem = [FlatDemand(id="H1", buyer_id="E", buyer_name="Enclosure maker E", part_name="panel", material="aluminium",
                      length_mm=300, width_mm=200, thickness_mm=1.5, accepted_grades=["5052"], quantity=30,
                      new_price_per_kg=300.0, buyer_utilisation=0.8),
           FlatDemand(id="H2", buyer_id="K", buyer_name="Kitchen-cabinet maker K", part_name="shelf", material="plywood",
                      length_mm=550, width_mm=280, thickness_mm=18.0, accepted_grades=["BWR"], quantity=14,
                      grain_matters=True, new_price_per_kg=95.0, buyer_utilisation=0.8, x_km=0.5, y_km=-1.0)]
    return sellers, res, dem


# ---------------- 3. bars and tubes ----------------
def bars():
    sal = [_sal("mild_steel", 30.0)]
    sellers = [Seller(id="G", name="Gate fabricator G", x_km=1.5, y_km=2.5, salvage=sal),
               Seller(id="T", name="Truss workshop T", x_km=-3.5, y_km=1.0, salvage=sal),
               Seller(id="P", name="Pipe fitter P", x_km=4.0, y_km=-2.5, salvage=sal)]
    RB, RBkg = "round bar 16", 1.58  # 0.00617 x d^2 kg/m for round steel bar
    PI, PIkg = "pipe 26.9 x 2.6", 1.56  # sample section weight; check the IS 1239 table

    def b(i, s, L, prof=RB, kg=RBkg, full=False, grade="E250", cond="good"):
        return LinearStock(id=f"{s}{i}", seller_id=s, material="mild_steel", profile=prof, length_mm=L,
                           mass_per_m_kg=kg, grade=grade, condition=cond, is_full_length=full)
    res = [b(1, "G", 6000, full=True), b(2, "G", 2350), b(3, "G", 1720), b(4, "G", 980), b(5, "G", 460),
           b(6, "G", 1900, prof=PI, kg=PIkg), b(7, "G", 1450, prof=PI, kg=PIkg),
           b(1, "T", 2600), b(2, "T", 1480), b(3, "T", 1260), b(4, "T", 3100, cond="bent"),
           b(5, "T", 2200, prof=PI, kg=PIkg), b(6, "T", 1650, prof="pipe 26.9 x 3.2", kg=1.87),
           b(1, "P", 2900, prof=PI, kg=PIkg), b(2, "P", 2400, prof=PI, kg=PIkg), b(3, "P", 1100, prof=PI, kg=PIkg),
           b(4, "P", 6000, prof=PI, kg=PIkg, full=True)]
    dem = [LinearDemand(id="B1", buyer_id="R", buyer_name="Railing maker R", part_name="baluster", material="mild_steel",
                        profile=RB, mass_per_m_kg=RBkg, cut_length_mm=450, accepted_grades=["E250"], quantity=30,
                        new_price_per_kg=64.0, buyer_utilisation=0.92),
           LinearDemand(id="B2", buyer_id="F", buyer_name="Furniture workshop F", part_name="chair leg", material="mild_steel",
                        profile=PI, mass_per_m_kg=PIkg, cut_length_mm=720, accepted_grades=["E250"], quantity=20,
                        new_price_per_kg=70.0, buyer_utilisation=0.9, x_km=-1.0, y_km=-2.0)]
    return sellers, res, dem


# ---------------- 4. food-business surplus ----------------
def food():
    sal = [_sal(c, -2.0) for c in ("vegetables", "fruit", "dairy", "bakery", "packaged", "cooked_meal", "used_cooking_oil")]
    sellers = [Seller(id="H", name="Hotel kitchen H", x_km=1.0, y_km=1.2, salvage=sal, fssai_licensed=True),
               Seller(id="K", name="Cloud kitchen K", x_km=-1.8, y_km=0.8, salvage=sal, fssai_licensed=True),
               Seller(id="R", name="Restaurant R", x_km=2.5, y_km=-1.5, salvage=sal, fssai_licensed=True),
               Seller(id="C", name="Caterer C", x_km=-2.2, y_km=-2.4, salvage=sal, fssai_licensed=False)]

    def lot(i, s, product, cat, kg, storage, hours):
        return PerishableLot(id=f"{s}{i}", seller_id=s, product=product, category=cat, quantity_kg=kg,
                             storage=storage, hours_left=hours)
    res = [lot(1, "H", "Tomatoes", "vegetables", 14, "ambient", 40), lot(2, "H", "Paneer", "dairy", 6, "chilled", 30),
           lot(3, "H", "Cooked rice and dal", "cooked_meal", 18, "chilled", 5),
           lot(1, "K", "Onions", "vegetables", 12, "ambient", 120), lot(2, "K", "Capsicum", "vegetables", 5, "chilled", 9),
           lot(3, "K", "Used cooking oil", "used_cooking_oil", 20, "ambient", 720),
           lot(1, "R", "Carrots and beans", "vegetables", 9, "chilled", 48), lot(2, "R", "Bread loaves", "bakery", 6, "ambient", 14),
           lot(1, "C", "Cabbage", "vegetables", 15, "ambient", 60), lot(2, "C", "Cooked biryani", "cooked_meal", 12, "chilled", 4)]
    dem = [PerishableDemand(id="F1", buyer_id="M", buyer_name="Hostel mess M", part_name="kg of vegetables",
                            buyer_type="food_business", categories=["vegetables"], storage_available=["ambient", "chilled"],
                            quantity=30, min_hours_left_on_arrival=12, normal_price_per_kg=38.0, buyer_fssai_licensed=True),
           PerishableDemand(id="F2", buyer_id="N", buyer_name="Food bank N", part_name="kg of food",
                            buyer_type="ngo", categories=["cooked_meal", "bakery"], storage_available=["ambient", "chilled"],
                            quantity=20, min_hours_left_on_arrival=2, normal_price_per_kg=0.0, x_km=-0.5, y_km=-1.0)]
    return sellers, res, dem


SCENARIOS = {
    "steel": {"title": "Mild steel sheet", "engine": "flat", "build": steel, "seller": "A", "metric_word": "surplus steel"},
    "sheets": {"title": "Aluminium and plywood", "engine": "flat", "build": sheets, "seller": "A", "metric_word": "surplus sheet"},
    "bars": {"title": "Bars and tubes", "engine": "linear", "build": bars, "seller": "G", "metric_word": "bar and pipe ends"},
    "food": {"title": "Food-business surplus", "engine": "perishable", "build": food, "seller": "K", "metric_word": "food"},
}


def dataset(key: str) -> dict:
    sc = SCENARIOS[key]
    sellers, res, dem = sc["build"]()
    s = Settings(kerf_mm=0.3 if key == "food" else (2.0 if key == "bars" else 3.0))
    if key == "sheets":
        s = Settings(kerf_mm=3.0, edge_margin_mm=5.0)
    return {"scenario": key, "title": sc["title"], "engine": sc["engine"], "plan_seller": sc["seller"],
            "metric_word": sc["metric_word"], "sellers": sellers, "resources": res, "demands": dem,
            "transport": Transport(), "settings": s}


# ---------------- Gap Fill demo ("Sold Before It's Cut"), all SAMPLE ----------------
def gapfill_demo() -> dict:
    """A workshop is about to cut 9 base plates from a 6 mm sheet. Neighbours have standing orders."""
    def o(i, buyer, part, L, W, t=6.0, q=1, grades=("E250",)):
        return dict(id=i, buyer_name=buyer, part_name=part, length_mm=L, width_mm=W, thickness_mm=t,
                    material="mild_steel", accepted_grades=list(grades), quantity=q, label="sample")
    return dict(
        sheet=dict(length_mm=2500, width_mm=1250, thickness_mm=6.0, material="mild_steel", grade="E250"),
        job=dict(part_name="base plate", length_mm=450, width_mm=350, quantity=9),
        orders=[o("G1", "Workshop W", "shim", 75, 75, q=10), o("G2", "Workshop W", "bracket", 150, 100, q=20),
                o("G3", "Fabricator R", "gusset", 200, 200, q=8), o("G4", "Panel maker Q", "base plate", 300, 300, q=6),
                o("G5", "Fabricator R", "end plate", 350, 200, q=4), o("G6", "Machine shop B", "stiffener", 400, 100, q=6),
                o("G7", "Panel maker Q", "cover plate", 400, 300, t=8.0, q=4),
                o("G8", "Panel works C", "flange blank", 220, 220, q=6, grades=("E350",))],
        prices=dict(scrap_price_per_kg=32.0, handling_per_part=2.0, cut_cost_per_m=6.0, pickup=300.0, label="sample"),
        kerf_mm=3.0, edge_margin_mm=5.0, declined_order_ids=[])
