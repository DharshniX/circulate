"""
Import from ERP or POS export (CSV), and surplus detection.

There is no live connection to any outside system. A seller exports a stock
file from whatever they use, uploads it, and every row is checked with the
same Pydantic models the engines use. Rows that fail are returned with the
reason, so the seller knows what to fix in their export.

Durable rows are sheets, except bar materials (TMT rebar): those need diameter_mm, leave width_mm and
thickness_mm empty, and use is_full_sheet for "a full-length bar". Bar weight per metre comes from the
site rule d x d / 162, an assumption. Shuttering plywood is 12 or 18 mm, and new, used once or used many times.

Surplus detection rules (platform rules, editable thresholds):
  Durable  offcut     the piece is not a full sheet (or not a full-length bar)
           idle       days_unused is at least N (default 30)
  Fresh    urgent     hours_left is at most H (default 24)
An item is surplus if any rule catches it; everything else is kept as
working stock. Imported values are SAMPLE unless the seller says they are
measured.
"""
from __future__ import annotations

import csv
import io
from typing import Literal

from pydantic import Field, ValidationError

from . import materials as M
from .models import DataLabel, FlatPiece, LinearStock, PerishableLot

Lane = Literal["durable", "fresh"]
SOURCE = "Import from ERP or POS export (CSV)"

COLUMNS: dict[str, list[str]] = {
    "durable": ["item_id", "seller_id", "material", "length_mm", "width_mm", "thickness_mm", "grade", "condition",
                "is_full_sheet", "days_unused"],
    "fresh": ["item_id", "seller_id", "product", "category", "quantity_kg", "storage", "hours_left"],
}
OPTIONAL: dict[str, list[str]] = {"durable": ["diameter_mm"], "fresh": []}  # diameter_mm: bars only
_LOWER = {"material", "condition", "category", "storage"}  # enum-like columns: "Mild Steel" -> "mild_steel"


class ImportedSheet(FlatPiece):
    """A FlatPiece plus the one column the stock export adds."""

    days_unused: int = Field(ge=0, le=36500, description="Days since the piece was last used")


class ImportedBar(LinearStock):
    """A bar (TMT rebar) from a Durable stock file: LinearStock plus days unused."""

    days_unused: int = Field(ge=0, le=36500, description="Days since the bar was last used")


class FieldError(ValueError):
    """A problem with one column, so manual entry can show it next to that field."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field, self.message = field, message


def parse_item(lane: Lane, text: str):
    """A stored listed item (JSON) back as its model: a sheet, a bar or a fresh lot."""
    import json
    data = json.loads(text)
    if lane == "fresh":
        return PerishableLot(**data)
    return ImportedBar(**data) if data.get("kind") == "linear" else ImportedSheet(**data)


def _norm(col: str, v: str) -> str:
    v = v.strip()
    if col in _LOWER:
        return v.lower().replace(" ", "_").replace("-", "_")
    if col == "grade":
        return v.upper()
    return v


def _reason(e: ValidationError) -> str:
    out = []
    for x in e.errors():
        where = ".".join(str(p) for p in x["loc"])
        msg = {"missing": "empty cell", "bool_parsing": "should be true or false"}.get(
            x["type"], x["msg"].removeprefix("Value error, "))
        out.append(f"{'item_id' if where == 'id' else where}: {msg}" if where else msg)
    return "; ".join(out)


def read_rows(lane: Lane, text: str) -> list[dict]:
    """Parse the CSV. Raises ValueError with a fix-it message for an empty file or missing columns."""
    text = (text or "").lstrip("﻿")  # spreadsheet programs often add a byte-order mark
    if not text.strip():
        raise ValueError("The file is empty. Export your stock as CSV with a header row and at least one item.")
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    header = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in COLUMNS[lane] if c not in header]
    if missing:
        raise ValueError(f"Missing column(s): {', '.join(missing)}. A {lane} stock file needs: {', '.join(COLUMNS[lane])}. "
                         "Download the example file to see the layout.")
    reader.fieldnames = header
    rows = [r for r in reader if any((v or "").strip() for k, v in r.items() if k is not None)]
    if not rows:
        raise ValueError("The file has a header but no stock rows. Add at least one item under the header.")
    return rows


def _build(lane: Lane, raw: dict, label: DataLabel):
    vals = {c: _norm(c, raw.get(c) or "") for c in COLUMNS[lane] + OPTIONAL[lane]}
    vals = {c: v for c, v in vals.items() if v != ""}  # empty cell -> "Field required"
    if "item_id" in vals:
        vals["id"] = vals.pop("item_id")
    if lane == "durable":
        if "material" in vals:
            M.get(vals["material"])  # unknown material -> ValueError with the known list
        if vals.get("material") in M.BAR_MATERIALS:
            return _bar(vals, label)
        vals.pop("diameter_mm", None)
        if vals.get("material") == "shuttering_plywood":
            _shuttering(vals)
        return ImportedSheet(**vals, label=label)
    return PerishableLot(**vals, label=label)


def _bar(vals: dict, label: DataLabel) -> ImportedBar:
    """A TMT rebar row: the diameter gives the profile and, by the site rule, the weight per metre."""
    try:
        d = float(vals.get("diameter_mm", ""))
    except ValueError:
        raise FieldError("diameter_mm", f"choose a bar diameter: {', '.join(f'{x}' for x in M.REBAR_DIAMETERS_MM)} mm")
    if d not in M.REBAR_DIAMETERS_MM:
        raise FieldError("diameter_mm", f"choose a bar diameter: {', '.join(f'{x}' for x in M.REBAR_DIAMETERS_MM)} mm")
    grade = M.rebar_grade(vals.get("grade", ""))
    if grade is None:
        raise FieldError("grade", f"choose a TMT grade: {', '.join(M.REBAR_GRADES)}")
    return ImportedBar(id=vals.get("id"), seller_id=vals.get("seller_id"), material=vals["material"], profile=M.rebar_profile(d),
                       length_mm=vals.get("length_mm"), mass_per_m_kg=round(M.rebar_kg_per_m(d), 3), grade=grade,
                       condition=vals.get("condition", "good"), is_full_length=vals.get("is_full_sheet", "false"),
                       days_unused=vals.get("days_unused"), label=label)


def _shuttering(vals: dict) -> None:
    try:
        t = float(vals.get("thickness_mm", ""))
    except ValueError:
        t = None
    if t is not None and t not in M.SHUTTERING_THICKNESSES_MM:
        raise FieldError("thickness_mm", "shuttering plywood is 12 or 18 mm")
    if vals.get("condition") not in M.USE_CONDITIONS:
        raise FieldError("condition", "for shuttering plywood choose new, used once or used many times")


def detect(lane: Lane, item, days_unused_limit: int, hours_left_limit: float) -> tuple[list[str], str | None]:
    """Return (rules that caught it, reason it was kept). Exactly one of the two is empty."""
    rules = []
    if lane == "durable":
        if item.kind == "linear" and not item.is_full_length:
            rules.append("Offcut: not a full-length bar")
        elif item.kind == "flat" and not item.is_full_sheet:
            rules.append("Offcut: not a full sheet")
        if item.days_unused >= days_unused_limit:
            rules.append(f"Unused for {item.days_unused} days (limit {days_unused_limit})")
        whole = "Full-length bar" if item.kind == "linear" else "Full sheet"
        return rules, None if rules else f"{whole}, used within the last {days_unused_limit} days"
    if item.hours_left <= hours_left_limit:
        rules.append(f"Urgent: {item.hours_left:g} h left (limit {hours_left_limit:g} h)")
    return rules, None if rules else f"{item.hours_left:g} h left, more than {hours_left_limit:g} h"


def run_import(lane: Lane, text: str, days_unused_limit: int = 30, hours_left_limit: float = 24,
               label: DataLabel = "sample") -> dict:
    rows = read_rows(lane, text)
    rejected, surplus, kept, seen = [], [], [], set()
    for n, raw in enumerate(rows, start=2):  # row 1 is the header
        rid = (raw.get("item_id") or "").strip()
        try:
            if rid and rid in seen:
                raise ValueError(f"item_id {rid} appears twice in this file")
            item = _build(lane, raw, label)
        except ValidationError as e:
            rejected.append({"row": n, "item_id": rid, "reason": _reason(e)})
            continue
        except ValueError as e:
            rejected.append({"row": n, "item_id": rid, "reason": str(e)})
            continue
        seen.add(item.id)
        rules, kept_reason = detect(lane, item, days_unused_limit, hours_left_limit)
        entry = {"row": n, "item": item.model_dump(mode="json")}
        if rules:
            surplus.append({**entry, "rules": rules, "urgent": lane == "fresh"})
        else:
            kept.append({**entry, "reason": kept_reason})
    return {"lane": lane, "source": SOURCE, "rows_read": len(rows), "accepted": len(surplus) + len(kept),
            "rejected": rejected, "surplus": surplus, "kept": kept,
            "thresholds": {"days_unused_limit": days_unused_limit, "hours_left_limit": hours_left_limit}}


MANUAL_SOURCE = "Entered by hand by the seller"


def _field_errors(e: ValidationError) -> dict[str, str]:
    """Plain-language errors keyed by form field (item_id, length_mm ...)."""
    out: dict[str, str] = {}
    for x in e.errors():
        field = str(x["loc"][0]) if x["loc"] else "item"
        field = "item_id" if field == "id" else field
        ctx = {k: (f"{v:g}" if isinstance(v, (int, float)) else v) for k, v in (x.get("ctx") or {}).items()}
        plain = {"missing": "This is needed.", "string_too_short": "This is needed.", "bool_parsing": "Choose full sheet or offcut.",
                 "float_parsing": "Enter a number.", "int_parsing": "Enter a whole number.", "int_from_float": "Enter a whole number.",
                 "literal_error": "Choose one of the options.", "too_short": "Choose at least one.", "greater_than": f"Must be more than {ctx.get('gt')}.",
                 "greater_than_equal": f"Must be {ctx.get('ge')} or more.", "less_than_equal": f"Must be {ctx.get('le')} or less."}
        msg = plain.get(x["type"], x["msg"].removeprefix("Value error, "))
        msg = msg[0].upper() + msg[1:] if msg else msg
        out.setdefault(field, msg if msg.endswith(".") else msg + ".")
    return out


def check_one(lane: Lane, values: dict, days_unused_limit: int = 30, hours_left_limit: float = 24,
              label: DataLabel = "sample") -> dict:
    """One item typed by hand: exactly the checks and surplus rules a CSV row gets, with errors per field."""
    raw = {k: ("true" if v is True else "false" if v is False else "" if v is None else str(v)) for k, v in values.items()}
    errors: dict[str, str] = {}
    if lane == "durable" and _norm("material", raw.get("material") or ""):
        try:
            M.get(_norm("material", raw["material"]))
        except ValueError as e:  # unknown material: still check every other field, so the form shows all errors at once
            errors["material"] = str(e)[0].upper() + str(e)[1:] + "."
            raw["material"] = "mild_steel"
    try:
        item = _build(lane, raw, label)
    except ValidationError as e:
        return {"ok": False, "errors": {**_field_errors(e), **errors}}
    except FieldError as e:
        return {"ok": False, "errors": {e.field: e.message[0].upper() + e.message[1:] + ".", **errors}}
    if errors:
        return {"ok": False, "errors": errors}
    rules, kept_reason = detect(lane, item, days_unused_limit, hours_left_limit)
    return {"ok": True, "item": item.model_dump(mode="json"), "surplus": bool(rules), "rules": rules,
            "kept_reason": kept_reason, "urgent": lane == "fresh" and bool(rules)}
