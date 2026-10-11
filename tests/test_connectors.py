"""Connector layer: interface, auto-sync folder and sync log. Temporary folder and database only."""
import os
import time

import pytest
from fastapi.testclient import TestClient

import app.main as main
from conftest import sign_in
from engine import connectors as CN

c = TestClient(main.app)
EXAMPLE = (main.EXAMPLES / "durable_stock_example.csv").read_bytes()
FRESH_EXAMPLE = (main.EXAMPLES / "fresh_stock_example.csv").read_bytes()
HEAD = b"item_id,seller_id,material,length_mm,width_mm,thickness_mm,grade,condition,is_full_sheet,days_unused\n"


@pytest.fixture(autouse=True)
def temp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB", tmp_path / "test.db")
    monkeypatch.setattr(main, "ERP_DROP", tmp_path / "erp_drop")
    monkeypatch.setattr(main, "SYNC_MIN_AGE_SECONDS", 0)
    sign_in(c, "admin")  # a fresh database each test: sign in again


@pytest.fixture
def drop(tmp_path):
    folder = tmp_path / "exports"
    folder.mkdir()
    r = c.put("/api/connectors/folder/durable", json={"path": str(folder), "enabled": True})
    assert r.status_code == 200 and r.json()["enabled"]
    return folder


def write(path, data: bytes, age=0):
    path.write_bytes(data)
    if age:
        t = time.time() - age
        os.utime(path, (t, t))


def sync(lane="durable"):
    r = c.post(f"/api/connectors/folder/{lane}/sync", json={})
    assert r.status_code == 200
    return r.json()["entries"]


def listed(lane="durable"):
    return c.get(f"/api/stock/import_{lane}").json()["items"]


# ---------------- the interface ----------------
def test_catalogue_has_three_active_and_three_planned_connectors():
    cat = CN.catalogue()
    assert [x.key for x in cat] == ["csv_upload", "folder_sync", "tally", "sap", "pos", "manual"]
    assert [x.status for x in cat] == ["active", "active", "planned", "planned", "planned", "active"]
    assert all(x.name and x.description for x in cat)
    for planned in cat[2:5]:
        assert planned.description.startswith("Planned")
        with pytest.raises(NotImplementedError):
            planned.stock_csv("durable")  # planned connectors never pretend to work


def test_csv_upload_connector_returns_the_text_unchanged():
    [ex] = CN.CsvUpload("a,b\n1,2\n").stock_csv("fresh")
    assert ex.name == "upload" and ex.text == "a,b\n1,2\n" and ex.error is None


def test_folder_connector_only_reads_csv_files(tmp_path):
    write(tmp_path / "stock.csv", EXAMPLE, age=10)
    write(tmp_path / "notes.txt", b"not stock", age=10)
    write(tmp_path / "~$stock.csv", b"lock file", age=10)
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub" / "deeper.csv", EXAMPLE, age=10)
    before = {p: (p.stat().st_mtime, p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()}
    exports = CN.FolderSync(tmp_path).stock_csv("durable")
    assert [x.name for x in exports] == ["stock.csv"] and exports[0].text.startswith("item_id")
    after = {p: (p.stat().st_mtime, p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after  # nothing moved, changed or deleted


def test_folder_connector_waits_for_a_file_still_being_written(tmp_path):
    write(tmp_path / "fresh_export.csv", EXAMPLE)
    assert CN.FolderSync(tmp_path, min_age_seconds=60).stock_csv("durable") == []


def test_folder_connector_reports_a_missing_folder(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        CN.FolderSync(tmp_path / "nope").stock_csv("durable")


def test_connectors_endpoint_and_planned_sync_refused():
    r = c.get("/api/connectors").json()
    assert r["works_with"] == "Works with any ERP or billing software that can export a stock file."
    assert {x["key"]: x["status"] for x in r["connectors"]}["tally"] == "planned"
    assert r["every_seconds"] == 60 and r["folder"]["durable"]["enabled"] is False
    for key in ("tally", "sap", "pos"):
        assert c.post(f"/api/connectors/{key}/sync").status_code == 501
    assert c.post("/api/connectors/csv_upload/sync").status_code == 400
    assert c.post("/api/connectors/nope/sync").status_code == 404


# ---------------- folder sync ----------------
def test_new_file_is_imported_and_listed_as_sample(drop):
    write(drop / "stock_2026-10-08.csv", EXAMPLE)
    [e] = sync()
    assert (e["status"], e["file"], e["rows_read"], e["surplus"], e["rejected"], e["listed"]) == ("imported", "stock_2026-10-08.csv", 14, 8, 3, 8)
    assert e["label"] == "sample" and len(e["details"]) == 3 and e["message"] == "New file."
    items = listed()
    assert len(items) == 8 and all(i["label"] == "sample" for i in items)
    assert c.get(items[0]["passport"]).status_code == 200  # same passports and QR codes as an upload
    assert (drop / "stock_2026-10-08.csv").read_bytes() == EXAMPLE  # the seller's file is untouched


def test_same_unchanged_file_is_never_imported_twice(drop):
    write(drop / "stock.csv", EXAMPLE)
    assert len(sync()) == 1
    assert sync() == [] and sync() == []
    os.utime(drop / "stock.csv")  # touched but the same bytes: still not imported again
    assert sync() == []
    assert len(c.get("/api/connectors/folder/durable/log").json()["log"]) == 1


def test_changed_file_is_imported_again(drop):
    write(drop / "stock.csv", HEAD + b"P1,A,mild_steel,800,400,3,E250,good,false,5\n")
    [first] = sync()
    assert first["surplus"] == 1
    write(drop / "stock.csv", HEAD + b"P1,A,mild_steel,800,400,3,E250,good,false,5\nP2,A,mild_steel,700,300,3,E250,good,false,9\n")
    [again] = sync()
    assert again["status"] == "imported" and again["surplus"] == 2 and again["message"] == "Changed file, imported again."
    assert {i["id"] for i in listed()} == {"P1", "P2"}  # P1 updated, not duplicated


def test_bad_files_are_logged_and_not_retried_until_they_change(drop):
    write(drop / "empty.csv", b"")
    write(drop / "wrong_lane.csv", FRESH_EXAMPLE)
    write(drop / "binary.csv", b"\x00\x01\x02PK\x03\x04")
    write(drop / "good.csv", EXAMPLE)
    entries = {e["file"]: e for e in sync()}
    assert entries["good.csv"]["status"] == "imported"
    assert entries["empty.csv"]["status"] == "error" and "empty" in entries["empty.csv"]["message"]
    assert "Missing column" in entries["wrong_lane.csv"]["message"]
    assert "not a text CSV" in entries["binary.csv"]["message"]
    assert len(listed()) == 8  # the bad files listed nothing
    assert sync() == []  # a bad file is not retried every minute
    write(drop / "empty.csv", HEAD + b"Q1,A,mild_steel,800,400,3,E250,good,false,5\n")
    [fixed] = sync()
    assert fixed["file"] == "empty.csv" and fixed["status"] == "imported"


def test_measured_only_when_ticked(drop):
    c.put("/api/connectors/folder/durable", json={"path": str(drop), "enabled": True, "measured": True})
    write(drop / "stock.csv", EXAMPLE)
    [e] = sync()
    assert e["label"] == "measured" and all(i["label"] == "measured" for i in listed())


def test_folder_thresholds_come_from_the_saved_settings(drop):
    c.put("/api/connectors/folder/durable", json={"path": str(drop), "days_unused_limit": 100})
    write(drop / "full_sheets.csv", HEAD + b"F1,A,mild_steel,2500,1250,6,E250,new,true,45\n")
    [e] = sync()
    assert e["surplus"] == 0 and e["rows_read"] == 1  # 45 days unused is below the saved 100-day rule


def test_sync_log_is_newest_first_and_per_lane(drop, tmp_path):
    write(drop / "a.csv", EXAMPLE)
    sync()
    write(drop / "b.csv", HEAD + b"P9,A,mild_steel,800,400,3,E250,good,false,5\n")
    sync()
    log = c.get("/api/connectors/folder/durable/log").json()["log"]
    assert [x["file"] for x in log] == ["b.csv", "a.csv"]
    assert set(log[0]) >= {"at", "file", "rows_read", "surplus", "rejected", "status"}
    assert c.get("/api/connectors/folder/fresh/log").json()["log"] == []


def test_background_pass_only_syncs_enabled_lanes(drop, tmp_path):
    write(drop / "stock.csv", EXAMPLE)
    fresh = tmp_path / "fresh_exports"
    fresh.mkdir()
    write(fresh / "lots.csv", FRESH_EXAMPLE)
    c.put("/api/connectors/folder/fresh", json={"path": str(fresh), "enabled": False})
    done = main.sync_all_enabled()
    assert list(done) == ["durable"] and done["durable"][0]["surplus"] == 8
    assert listed("fresh") == []


def test_missing_folder_is_refused_and_logged_once_in_the_background(tmp_path):
    assert c.put("/api/connectors/folder/durable", json={"path": str(tmp_path / "nope")}).status_code == 422
    gone = tmp_path / "gone"
    gone.mkdir()
    c.put("/api/connectors/folder/durable", json={"path": str(gone), "enabled": True})
    gone.rmdir()
    main.sync_all_enabled()
    main.sync_all_enabled()
    log = c.get("/api/connectors/folder/durable/log").json()["log"]
    assert len(log) == 1 and log[0]["status"] == "error" and "does not exist" in log[0]["message"]


def test_default_folder_is_inside_the_project_and_created_on_first_sync():
    cfg = c.get("/api/connectors").json()["folder"]["fresh"]
    assert cfg["path"].replace("\\", "/").endswith("erp_drop/fresh") and not cfg["saved"]
    assert sync("fresh") == []
    assert (main.ERP_DROP / "fresh").is_dir()
