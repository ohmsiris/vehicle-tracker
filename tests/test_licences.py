from datetime import date, datetime, timezone

import pytest

from app import licences as lc

TODAY = date(2026, 11, 12)


def test_status_by_warning_time_and_waiting_for_the_new_paper():
    assert lc.status({"kind": "driving", "expires_on": "2026-11-15"}, TODAY) == {"state": "soon", "days": 3, "warn": 60}
    assert lc.status({"kind": "driving", "expires_on": "2027-05-14"}, TODAY)["state"] == "ok"
    assert lc.status({"kind": "certificate", "expires_on": "2026-10-21"}, TODAY)["state"] == "expired"
    assert lc.status({"kind": "hazard", "expires_on": "2026-12-18", "submitted_on": "2026-11-12"}, TODAY)["state"] == "submitted"
    assert lc.status({"kind": "hazard", "expires_on": "2026-12-18", "warn_days": 10}, TODAY)["state"] == "ok"
    assert lc.status({"kind": "pp20"}, TODAY)["state"] == "never"                 # ภพ.20 does not expire
    assert lc.status({"kind": "sign_tax"}, TODAY)["state"] == "nodate"            # should have a due date, has none


def test_suggested_dates_after_a_new_copy():
    # a หนังสือรับรอง is good for 3 months from its date (owner, 12 Nov 2026)
    assert lc.suggest("certificate", {"issued_on": "2026-07-21"}, {})["expires_on"] == "2026-10-21"
    # what the paper says wins
    assert lc.suggest("o2", {"issued_on": "2026-12-20", "expires_on": "2029-12-31", "doc_no": " 19-2-00758 "}, {}) == \
        {"issued_on": "2026-12-20", "expires_on": "2029-12-31", "doc_no": "19-2-00758"}
    # a yearly tax receipt moves the due date on a year
    assert lc.suggest("land_tax", {"issued_on": "2027-05-20"}, {"expires_on": "2027-06-01"})["expires_on"] == "2028-06-01"
    assert lc.suggest("driving", {}, {"expires_on": "2026-11-15"})["expires_on"] is None
    assert lc.add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


def test_clean_refuses_bad_input():
    with pytest.raises(lc.LicenceError):
        lc.clean({"kind": "nope"}, new=True)
    with pytest.raises(lc.LicenceError):
        lc.clean({"kind": "pp20", "expires_on": "31/12/2026"}, new=True)
    v = lc.clean({"kind": "pp20", "place": " สาขา 1 ", "warn_days": "45"}, new=True)
    assert v["title"] == "ภพ.20" and v["place"] == "สาขา 1" and v["warn_days"] == 45 and v["company"] == lc.COMPANY


def test_the_page_lists_what_to_do_first_then_each_section():
    docs = [{"id": 1, "kind": "driving", "title": "ใบขับขี่", "employee_id": "e1", "expires_on": "2026-11-15"},
            {"id": 2, "kind": "driving", "title": "ใบขับขี่", "employee_id": "e2", "expires_on": "2027-05-14"},
            {"id": 3, "kind": "pp20", "title": "ภพ.20", "place": "สาขา 2"},
            {"id": 4, "kind": "o2", "title": "อ.2 ใบอนุญาตผลิตอาหาร", "place": "สาขา 2", "expires_on": "2026-12-31"}]
    files = [{"id": 10, "doc_id": 3, "current": False, "uploaded_at": "2025-01-01"}, {"id": 11, "doc_id": 3, "current": True, "uploaded_at": "2026-11-12"}]
    people = {"e1": {"name": "ศราวุธ โสภา", "nickname": "อ๊อฟ", "truck": "10"}, "e2": {"name": "รวม ขอนบกกลาง", "nickname": "รวม", "truck": "1"}}
    v = lc.view(docs, files, people, TODAY)
    assert [r["id"] for r in v["act"]] == [1, 4]                                   # soonest first
    assert v["tiles"] == {"expired": 0, "soon": 2, "nofile": 3, "ok": 2}
    company, driving = v["sections"]
    assert [r["id"] for r in company["rows"]] == [3] and company["count"] == 2
    assert [r["id"] for r in driving["rows"]] == [2] and driving["rows"][0]["person"] == "รวม ขอนบกกลาง"
    row3 = company["rows"][0]
    assert row3["file"]["id"] == 11 and [f["id"] for f in row3["old_files"]] == [10] and row3["place_note"] == "แก่งคอย"


def test_the_calendar_feed():
    docs = [{"id": 4, "kind": "o2", "title": "อ.2 ใบอนุญาตผลิตอาหาร", "place": "สาขา 2", "expires_on": "2026-12-31", "doc_no": "19-2-00658"},
            {"id": 5, "kind": "land_tax", "title": "ภาษีที่ดินและสิ่งปลูกสร้าง", "expires_on": "2027-06-01"},
            {"id": 3, "kind": "pp20", "title": "ภพ.20"}]
    out = lc.ics(docs, {}, datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc))
    assert out.startswith("BEGIN:VCALENDAR\r\n") and out.endswith("END:VCALENDAR\r\n")
    assert "DTSTART;VALUE=DATE:20261231" in out and "DTSTART;VALUE=DATE:20261101" in out          # the reminder 60 days before
    assert "ครบกำหนดจ่าย" in out.replace("\r\n ", "") and out.count("BEGIN:VEVENT") == 4
    assert all(len(line.encode()) <= 75 for line in out.split("\r\n"))
    assert lc.feed_key("a") != lc.feed_key("b") and len(lc.feed_key("a")) == 32


def test_file_types():
    assert lc.mime_of("x.PDF", b"") == "application/pdf" and lc.mime_of("scan", b"\xff\xd8\xff\xe0") == "image/jpeg"
    with pytest.raises(lc.LicenceError):
        lc.mime_of("x.docx", b"PK")
