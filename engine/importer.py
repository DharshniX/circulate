"""
Import from ERP or POS export (CSV), and surplus detection.

There is no live connection to any outside system. A seller exports a stock
file from whatever they use, uploads it, and every row is checked with the
same Pydantic models the engines use. Rows that fail are returned with the
reason, so the seller knows what to fix in their export.

Surplus detection rules (platform rules, editable thresholds):
  Durable  offcut     the piece is not a full sheet
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
from .models import DataLabel, FlatPiece, PerishableLot

Lane = Literal["durable", "fresh"]
SOURCE = "Import from ERP or POS export (CSV)"

COLUMNS: dict[str, list[str]] = {
    "durable": ["item_id", "seller_id", "material", "length_mm", "width_mm", "thickness_mm", "grade", "condition",
                "is_full_sheet", "days_unused"],
    "fresh": ["item_id", "seller_id", "product", "category", "quantity_kg", "storage", "hours_left"],
}
_LOWER = {"material", "condition", "category", "storage"}  # enum-like columns: "Mild Steel" -> "mild_steel"


class ImportedSheet(FlatPiece):
    """A FlatPiece plus the one column the stock export adds."""

    days_unused: int = Field(ge=0, le=36500, description="Days since the piece was last used")


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
    vals = {c: _norm(c, raw.get(c) or "") for c in COLUMNS[lane]}
    vals = {c: v for c, v in vals.items() if v != ""}  # empty cell -> "Field required"
    if "item_id" in vals:
        vals["id"] = vals.pop("item_id")
    if lane == "durable":
        if "material" in vals:
            M.get(vals["material"])  # unknown material -> ValueError with the known list
        return ImportedSheet(**vals, label=label)
    return PerishableLot(**vals, label=label)


def detect(lane: Lane, item, days_unused_limit: int, hours_left_limit: float) -> tuple[list[str], str | None]:
    """Return (rules that caught it, reason it was kept). Exactly one of the two is empty."""
    rules = []
    if lane == "durable":
        if not item.is_full_sheet:
            rules.append("Offcut: not a full sheet")
        if item.days_unused >= days_unused_limit:
            rules.append(f"Unused for {item.days_unused} days (limit {days_unused_limit})")
        return rules, None if rules else f"Full sheet, used within the last {days_unused_limit} days"
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
