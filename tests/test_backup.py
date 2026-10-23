import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import backup

NOW = datetime(2026, 10, 21, 15, 30, tzinfo=ZoneInfo("Asia/Bangkok"))


class Fake:
    def __init__(self):
        self.tables = {t: [] for t in backup.TABLES}
        self.tables["event_attachments"] = [
            {"id": "a1", "storage_path": "e1/a1.jpg", "byte_size": 5},
            {"id": "a2", "storage_path": "e1/a2.pdf", "byte_size": 3},
        ]
        self.tables["bill_line_items"] = [{"id": "l1", "description": "น้ำมันเครื่อง", "was_low_confidence": True}]
        self.tables["vehicles"] = [{"id": "v1", "plate_number": "84-4786"}]
        self.blobs = {"e1/a1.jpg": b"12345", "e1/a2.pdf": b"abc"}
        self.downloads = []

    def select_all(self, table, params):
        assert params["order"] and params["select"] == "*"
        return self.tables[table]

    def download(self, path):
        self.downloads.append(path)
        if isinstance(self.blobs[path], Exception):
            raise self.blobs[path]
        return self.blobs[path]


def go(fake, tmp_path, now=NOW):
    return backup.run_backup(fake.select_all, fake.download, tmp_path, now=now)


def test_a_clean_backup_writes_every_table_and_file_and_checks_out(tmp_path):
    f = Fake()
    m = go(f, tmp_path)
    assert m["ok"] and m["problems"] == []
    assert set(m["rows"]) == set(backup.TABLES) and m["rows"]["vehicles"] == 1 and m["files"] == {"expected": 2, "downloaded_now": 2, "already_had": 0, "bytes": 8}
    run = tmp_path / "2026-10-21_1530"
    assert json.loads((run / "data" / "vehicles.json").read_text(encoding="utf-8")) == [{"id": "v1", "plate_number": "84-4786"}]
    assert (tmp_path / "files" / "e1" / "a1.jpg").read_bytes() == b"12345"
    assert json.loads((run / "MANIFEST.json").read_text(encoding="utf-8"))["ok"] is True


def test_thai_text_is_stored_readably(tmp_path):
    go(Fake(), tmp_path)
    assert "น้ำมันเครื่อง" in (tmp_path / "2026-10-21_1530" / "data" / "bill_line_items.json").read_text(encoding="utf-8")


def test_the_generated_column_is_not_saved(tmp_path):
    go(Fake(), tmp_path)
    rows = json.loads((tmp_path / "2026-10-21_1530" / "data" / "bill_line_items.json").read_text(encoding="utf-8"))
    assert "was_low_confidence" not in rows[0] and rows[0]["description"] == "น้ำมันเครื่อง"


def test_second_run_downloads_nothing_again(tmp_path):
    f = Fake()
    go(f, tmp_path)
    f.downloads.clear()
    m = go(f, tmp_path, now=datetime(2026, 10, 22, 9, 0, tzinfo=ZoneInfo("Asia/Bangkok")))
    assert f.downloads == [] and m["files"]["already_had"] == 2 and m["files"]["downloaded_now"] == 0 and m["ok"]
    assert (tmp_path / "2026-10-22_0900" / "data" / "vehicles.json").exists()      # but the tables are a fresh full copy


def test_a_truncated_local_file_is_fetched_again(tmp_path):
    f = Fake()
    go(f, tmp_path)
    (tmp_path / "files" / "e1" / "a1.jpg").write_bytes(b"12")                       # damaged copy from an earlier run
    f.downloads.clear()
    m = go(f, tmp_path, now=datetime(2026, 10, 22, 9, 0, tzinfo=ZoneInfo("Asia/Bangkok")))
    assert f.downloads == ["e1/a1.jpg"] and m["ok"] and (tmp_path / "files" / "e1" / "a1.jpg").read_bytes() == b"12345"


def test_a_size_mismatch_is_reported_and_the_bad_file_is_not_kept(tmp_path):
    f = Fake()
    f.blobs["e1/a1.jpg"] = b"123"                                                    # server returned a short file
    m = go(f, tmp_path)
    assert m["ok"] is False and any("size mismatch" in p and "e1/a1.jpg" in p for p in m["problems"])
    assert not (tmp_path / "files" / "e1" / "a1.jpg").exists()
    assert json.loads((tmp_path / "2026-10-21_1530" / "MANIFEST.json").read_text(encoding="utf-8"))["ok"] is False


def test_a_failed_download_is_reported_not_fatal(tmp_path):
    f = Fake()
    f.blobs["e1/a2.pdf"] = RuntimeError("HTTP 500")
    m = go(f, tmp_path)
    assert m["ok"] is False and any("could not download e1/a2.pdf" in p for p in m["problems"])
    assert (tmp_path / "files" / "e1" / "a1.jpg").exists()                           # the others still saved


@pytest.mark.parametrize("evil", ["../../escape.jpg", "/etc/passwd", r"..\..\escape.jpg", "e1/../../x.jpg"])
def test_a_storage_path_can_never_write_outside_the_backup_folder(tmp_path, evil):
    f = Fake()
    f.tables["event_attachments"] = [{"id": "x", "storage_path": evil, "byte_size": 1}]
    f.blobs[evil] = b"x"
    m = go(f, tmp_path / "bk")
    assert f.downloads == [] and any("unsafe storage path" in p for p in m["problems"])
    assert not (tmp_path / "escape.jpg").exists()


def test_empty_database_backs_up_cleanly(tmp_path):
    f = Fake()
    f.tables = {t: [] for t in backup.TABLES}
    m = go(f, tmp_path)
    assert m["ok"] and m["files"]["expected"] == 0 and all(n == 0 for n in m["rows"].values())


def test_purchase_bill_photos_are_saved_too(tmp_path):
    f = Fake()
    f.tables["purchase_attachments"] = [{"id": "p1", "storage_path": "purchases/b1/1.jpg", "byte_size": 4}]
    f.blobs["purchases/b1/1.jpg"] = b"abcd"
    m = go(f, tmp_path)
    assert m["ok"] and m["files"] == {"expected": 3, "downloaded_now": 3, "already_had": 0, "bytes": 12}
    assert (tmp_path / "files" / "purchases" / "b1" / "1.jpg").read_bytes() == b"abcd"


def test_a_table_that_is_not_in_the_database_yet_is_noted_not_fatal(tmp_path):
    f = Fake()
    real = f.select_all

    def select_all(table, params):
        if table == "stock_moves":
            raise RuntimeError("select stock_moves failed: HTTP 404 {\"code\":\"PGRST205\"}")
        if table == "vehicles":
            raise RuntimeError("select vehicles failed: HTTP 500 boom")        # any other failure is NOT swallowed
        return real(table, params)

    with pytest.raises(RuntimeError):
        backup.run_backup(select_all, f.download, tmp_path, now=NOW)
    f.tables["vehicles"] = []
    m = backup.run_backup(lambda t, p: (_ for _ in ()).throw(RuntimeError("HTTP 404")) if t == "stock_moves" else real(t, p), f.download, tmp_path / "ok", now=NOW)
    assert m["ok"] and m["tables_not_in_this_database"] == ["stock_moves"] and m["rows"]["stock_moves"] == 0


def test_every_table_the_schema_files_create_is_in_the_backup():
    import re
    created = set()
    for sql in Path(__file__).resolve().parent.parent.glob("schema*.sql"):
        text = sql.read_text(encoding="utf-8")
        created |= set(re.findall(r"create table (?:if not exists )?(?:public\.)?(\w+)", text, re.I))
    created -= {"service_custom_items"}                                     # made by schema_v18 and dropped again by schema_v20
    assert created - set(backup.TABLES) == set(), "a table is not backed up: add it to app/backup.py TABLES"
