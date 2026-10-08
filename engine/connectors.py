"""
Connectors: where a seller's stock file comes from.

A connector only fetches stock CSV text for a lane. It never checks rows or
decides what is surplus: that stays in importer.py, the single place that
validates rows and applies the surplus rules. A connector never deletes,
moves or changes the seller's files.

  csv_upload   active   the seller uploads an export by hand
  folder_sync  active   the app reads .csv files from a folder on this computer
  tally, sap,  planned  shown so sellers know what is coming; they do not work
  pos                   yet and say so (NotImplementedError)
  manual       active   one item typed into a form, for sellers without an ERP

Nothing here talks to an outside system. "Planned" connectors are named after
software sellers already use, but no live connection exists.
"""
from __future__ import annotations

import csv
import hashlib
import io
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Lane = Literal["durable", "fresh"]
Status = Literal["active", "planned"]


@dataclass(frozen=True)
class StockExport:
    """One stock file as the connector found it."""

    name: str  # file name, or "upload"
    text: str  # CSV text, decoded
    fingerprint: str  # sha256 of the raw bytes: same bytes, same fingerprint
    error: str | None = None  # the file could not be read as text


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode(data: bytes) -> tuple[str, str | None]:
    for enc in ("utf-8-sig", "cp1252"):  # spreadsheet exports on Windows are often cp1252
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        if "\x00" in text:
            break
        return text, None
    return "", "The file is not a text CSV. Export the stock list as CSV (comma separated) and save it again."


class Connector:
    key: str = ""
    name: str = ""
    status: Status = "planned"
    description: str = ""

    def stock_csv(self, lane: Lane) -> list[StockExport]:
        """Stock CSV text for this lane, one entry per file."""
        raise NotImplementedError(f"{self.name} is planned and not built yet. Use CSV upload or Auto-sync folder.")

    def info(self) -> dict:
        return {"key": self.key, "name": self.name, "status": self.status, "description": self.description}


class CsvUpload(Connector):
    key, name, status = "csv_upload", "CSV upload", "active"
    description = "Upload a stock file exported from your system. Checked row by row before anything is listed."

    def __init__(self, text: str = ""):
        self.text = text

    def stock_csv(self, lane: Lane) -> list[StockExport]:
        return [StockExport("upload", self.text, fingerprint(self.text.encode("utf-8")))]


class FolderSync(Connector):
    """Reads every .csv file in one folder (not sub-folders), read-only."""

    key, name, status = "folder_sync", "Auto-sync folder", "active"
    description = "Save exports into a folder on this computer; new or changed .csv files are imported every 60 seconds."

    def __init__(self, folder: str | Path | None = None, min_age_seconds: float = 1.0):
        self.folder = Path(folder) if folder else None
        self.min_age_seconds = min_age_seconds  # skip a file that is still being written

    def check(self) -> str | None:
        if self.folder is None:
            return "Choose a folder first."
        if not self.folder.exists():
            return f"The folder {self.folder} does not exist. Create it or choose another one."
        if not self.folder.is_dir():
            return f"{self.folder} is a file, not a folder."
        return None

    def stock_csv(self, lane: Lane) -> list[StockExport]:
        problem = self.check()
        if problem:
            raise ValueError(problem)
        out, now = [], time.time()
        for f in sorted(self.folder.iterdir(), key=lambda p: p.name.lower()):
            if not f.is_file() or f.suffix.lower() != ".csv" or f.name.startswith(("~$", ".")):
                continue  # only CSV files; skip spreadsheet lock files and hidden files
            if now - f.stat().st_mtime < self.min_age_seconds:
                continue  # picked up on the next check, once the export has finished writing
            data = f.read_bytes()
            text, err = _decode(data)
            out.append(StockExport(f.name, text, fingerprint(data), err))
        return out


class ManualEntry(Connector):
    """One item typed by hand, turned into a one-row stock CSV with the importer's columns."""

    key, name, status = "manual", "Manual entry", "active"
    description = "No ERP? Add an item by hand on a phone or laptop, with a photo. Same checks as an upload."

    def __init__(self, fields: dict | None = None):
        self.fields = fields or {}

    def stock_csv(self, lane: Lane) -> list[StockExport]:
        from .importer import COLUMNS
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=COLUMNS[lane], extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerow({k: ("true" if v is True else "false" if v is False else v) for k, v in self.fields.items()})
        text = buf.getvalue()
        return [StockExport("manual entry", text, fingerprint(text.encode("utf-8")))]


class Planned(Connector):
    status = "planned"

    def __init__(self, key: str, name: str, description: str):
        self.key, self.name, self.description = key, name, description


def catalogue() -> list[Connector]:
    """Every connector, in the order the seller sees them."""
    return [
        CsvUpload(),
        FolderSync(),
        Planned("tally", "Tally", "Planned: read the stock summary straight from Tally, so no manual export is needed."),
        Planned("sap", "SAP", "Planned: read material stock from SAP through an export or interface set up with your SAP team."),
        Planned("pos", "POS / billing software", "Planned: read end-of-day stock from shop or restaurant billing software."),
        ManualEntry(),
    ]
