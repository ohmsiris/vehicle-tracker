import pytest

from app import acc_files as af
from app import accounting as acc

FEEDS = {"electricity", "midmonth"}
BR = ("สระบุรี", "แก่งคอย")


def test_clean_upload_keeps_the_file_under_its_row_and_period():
    rows = af.clean_upload("electricity", "2026-10-2", "สระบุรี", [("PEA ต.ค.pdf", b"%PDF-1")], feeds=FEEDS, branches=BR)
    r = rows[0]
    assert r["mime_type"] == "application/pdf" and r["storage_path"].startswith("accfiles/2026-10-2/") and r["storage_path"].endswith(".pdf")
    assert r["branch"] == "สระบุรี" and r["byte_size"] == 6 and len(r["sha256"]) == 64


@pytest.mark.parametrize("feed,period,branch,files", [
    ("truck_bill", "2026-10-2", "สระบุรี", [("a.pdf", b"x")]),      # program rows take no files
    ("electricity", "2026-10", "สระบุรี", [("a.pdf", b"x")]),
    ("electricity", "2026-10-2", "ลพบุรี", [("a.pdf", b"x")]),
    ("electricity", "2026-10-2", "สระบุรี", []),
    ("electricity", "2026-10-2", "สระบุรี", [("a.exe", b"x")]),
    ("electricity", "2026-10-2", "สระบุรี", [("a.pdf", b"")]),
])
def test_clean_upload_refuses(feed, period, branch, files):
    with pytest.raises(af.BadFile):
        af.clean_upload(feed, period, branch, files, feeds=FEEDS, branches=BR)


def test_a_row_with_files_but_no_amounts_is_received():
    rows = {f["key"]: f for f in acc.feed_status(2, {}, {}, 0, {"electricity": 2})}
    assert rows["electricity"]["state"] == "received" and rows["electricity"]["files"] == 2
    assert rows["water_city"]["state"] == "missing"
    rows = {f["key"]: f for f in acc.feed_status(2, {"electricity": (1, 100.0)}, {}, 0, {"electricity": 2})}
    assert rows["electricity"]["state"] == "done"
