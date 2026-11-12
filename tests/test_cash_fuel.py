"""น้ำมันเงินสด (app/cash_fuel.py): the pure parts."""
from datetime import date
from io import BytesIO

from openpyxl import load_workbook

from app import cash_fuel as cf

ROSTER = [{"id": "a", "truck_number": "52", "plate": "2ฒค 3332", "branch": "สระบุรี", "driver": "มานัส ใจดี"},
          {"id": "b", "truck_number": "34", "plate": "84-4764", "branch": "สระบุรี", "driver": "ประเสริฐ ดี"},
          {"id": "c", "truck_number": "52", "plate": "70-1111", "branch": "แก่งคอย", "driver": "คนอื่น"},
          {"id": "d", "truck_number": "", "plate": "มพ 1093", "branch": "สระบุรี", "driver": None}]


def test_company_month_and_half():
    assert cf.month_of(date(2026, 10, 26)) == ("2026-11", 1)
    assert cf.month_of(date(2026, 11, 10)) == ("2026-11", 1)
    assert cf.month_of(date(2026, 11, 11)) == ("2026-11", 2)
    assert cf.month_of(date(2026, 12, 27)) == ("2027-01", 1)
    assert cf.period("2026-11") == (date(2026, 10, 26), date(2026, 11, 25))
    assert cf.period("2026-11", 2) == (date(2026, 11, 11), date(2026, 11, 25))


def test_which_truck():
    assert cf.match_vehicle("2ฒค3332", "52", ROSTER)["id"] == "a"            # number first, Saraburi's
    assert cf.match_vehicle("84-4764", None, ROSTER)["id"] == "b"
    assert cf.match_vehicle("มพ1093", None, ROSTER)["id"] == "d"
    assert cf.match_vehicle("6161", None, ROSTER) is None


def test_two_reads_that_disagree_are_flagged():
    a = [{"is_fuel": True, "date": "2026-10-26", "amount": 1000, "plate": "6161", "truck_number": None},
         {"is_fuel": False, "text": "OT"},
         {"is_fuel": True, "date": "2026-10-26", "amount": 1000, "plate": "2ฒค 3332", "truck_number": "52"}]
    b = [{"is_fuel": True, "date": "2026-10-26", "amount": 1000, "plate": "6161", "truck_number": None},
         {"is_fuel": True, "date": "2026-10-26", "amount": 1500, "plate": "2ฒค3332", "truck_number": "52"}]
    d = cf.disagreements(a, b)
    assert list(d) == [2] and "จำนวนเงิน 1500" in d[2]                    # spaces in a plate are not a disagreement


def test_who_took_the_money():
    ppl = {"route": [{"name": "มานัส ใจดี", "nickname": "นัส", "truck": "52"}], "foh": [{"name": "สมชาย ดี", "nickname": "", "truck": None}]}
    assert cf.who_took("มานัส", ppl) == "มานัส ใจดี" and cf.who_took("นัส", ppl) == "มานัส ใจดี"
    assert cf.who_took("ไม่รู้", ppl) is None and cf.who_took("", ppl) is None


def test_summary_per_truck_and_the_file():
    lines = [{"vehicle_id": "a", "day": "2026-10-30", "amount": 1000, "counted": True, "batch": 1, "driver_name": "มานัส ใจดี"},
             {"vehicle_id": "a", "day": "2026-10-28", "amount": 1000, "counted": True, "batch": 1, "driver_name": "มานัส ใจดี"},
             {"vehicle_id": "b", "day": "2026-11-12", "amount": 1500, "counted": True, "batch": 2, "driver_name": None},
             {"vehicle_id": "b", "day": "2026-11-12", "amount": 1500, "counted": False, "batch": 2}]
    s = cf.summary(lines, ROSTER)
    assert [t["truck_no"] for t in s["trucks"]] == ["34", "52"] and s["total"] == 3500
    assert s["trucks"][1]["driver"] == "มานัส" and [f["day"] for f in s["trucks"][1]["fills"]] == ["2026-10-28", "2026-10-30"]
    assert cf.summary(lines, ROSTER, batch=2)["total"] == 1500
    assert [(x["day"], x["truck_no"], x["driver"]) for x in s["by_date"]] == [("2026-10-28", "52", "มานัส"), ("2026-10-30", "52", "มานัส"), ("2026-11-12", "34", "")]
    wb = load_workbook(BytesIO(cf.to_xlsx("2026-11", s)))
    assert wb.sheetnames == ["ตามวันที่", "ตามรถ"]
    ws = wb["ตามวันที่"]
    assert ws["A1"].value == "รายการค่าน้ำมัน" and "26 ตุลาคม 2569" in ws["A2"].value
    assert [ws.cell(row=5, column=k).value for k in range(1, 7)] == [1, "28/10/2569", "52", "2ฒค 3332", "มานัส", 1000]
    assert ws.cell(row=8, column=1).value == "รวม" and ws.cell(row=8, column=6).value == 3500
    assert wb["ตามรถ"].cell(row=5, column=1).value == "34"


def test_a_plate_and_number_that_point_at_different_trucks_are_flagged():
    read = {"lines": [{"is_fuel": True, "date": "2026-11-03", "text": "เติมน้ำมัน 84-4764 52", "plate": "84-4764", "truck_number": "52",
                       "amount": 1000, "requester": None, "position": None, "unsure": False}]}
    ln = cf.fuel_lines(read, ROSTER, "สระบุรี", {})[0]
    assert ln["vehicle_id"] == "a" and "เป็นของรถ 34" in ln["note"]


def test_only_confirmed_pages_and_typed_lines_count(monkeypatch):
    from app import supabase
    monkeypatch.setattr(supabase, "select_all", lambda table, params=None: [{"id": 7}])
    lines = [{"id": 1, "page_id": 7}, {"id": 2, "page_id": 8}, {"id": 3, "page_id": None}]
    assert [ln["id"] for ln in cf._confirmed_lines(lines)] == [1, 3]


def test_a_page_with_doubtful_lines_cannot_be_confirmed(monkeypatch):
    import pytest
    from app import supabase
    rows = [{"id": 1, "note": "ลายมือไม่ชัด", "checked": False, "counted": True, "vehicle_id": "a", "amount": 1000, "day": "2026-11-03", "driver_name": "มานัส ใจดี"},
            {"id": 2, "note": "x", "checked": False, "counted": False, "dup_of": 5, "vehicle_id": "a", "amount": 500, "day": "2026-11-03", "no_name": True}]
    writes = []
    monkeypatch.setattr(supabase, "select_all", lambda table, params=None: rows)
    monkeypatch.setattr(supabase, "update", lambda table, match, body: writes.append((table, body)))
    with pytest.raises(cf.CashFuelError):
        cf.confirm_page(3, "เจ้าของ")
    assert writes == []
    rows[0]["checked"] = True                                              # ticked "ถูกแล้ว"; a duplicate does not block
    cf.confirm_page(3, "เจ้าของ")
    assert writes[-1][0] == "cash_fuel_pages" and writes[-1][1]["confirmed"] is True and writes[-1][1]["confirmed_by"] == "เจ้าของ"


def test_no_plate_written_takes_the_truck_the_requester_drives():
    ppl = {"route": [{"name": "มานัส ใจดี", "nickname": "นัส", "truck": "52"}], "foh": []}
    read = {"lines": [{"is_fuel": True, "date": "2026-11-03", "text": "เติมน้ำมัน", "plate": None, "truck_number": None, "amount": 1000,
                       "requester": "มานัส", "position": "ขับรถ", "unsure": False},
                      {"is_fuel": True, "date": "2026-11-03", "text": "เติมน้ำมัน", "plate": None, "truck_number": None, "amount": 500,
                       "requester": "ใครไม่รู้", "position": None, "unsure": False}]}
    a, b = cf.fuel_lines(read, ROSTER, "สระบุรี", ppl)
    assert a["vehicle_id"] == "a" and a["driver_name"] == "มานัส ใจดี" and "ไม่ได้เขียนทะเบียน" in a["note"]
    assert b["vehicle_id"] is None and b["note"] == "ไม่พบรถ"


def test_a_pdf_of_several_pages_is_split_into_one_picture_per_page(monkeypatch):
    import pymupdf
    from app import supabase
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page(width=595, height=842).insert_text((72, 72), f"page {i + 1}")
    raw = doc.tobytes()
    pages = cf.pdf_pages(raw)
    assert len(pages) == 3 and all(p[:2] == b"\xff\xd8" for p in pages)          # JPEGs
    seen, inserted = [], []
    monkeypatch.setattr(cf, "_roster", lambda: [])
    monkeypatch.setattr(cf, "people", lambda branch, roster: {})
    monkeypatch.setattr(cf, "read_twice", lambda data, mime: (seen.append(mime), {"lines": [], "second": []})[1])
    monkeypatch.setattr(supabase, "select", lambda table, params=None: [])
    monkeypatch.setattr(supabase, "storage_upload", lambda *a, **k: None)
    monkeypatch.setattr(supabase, "insert", lambda table, rows: (inserted.extend(rows), [{"id": len(inserted)}])[1])
    out = cf.add_pages([("scan.pdf", raw)], "สระบุรี", "test")
    assert [a["name"] for a in out["added"]] == ["scan.pdf หน้า 1", "scan.pdf หน้า 2", "scan.pdf หน้า 3"]
    assert seen == ["image/jpeg"] * 3 and len({r["image_hash"] for r in inserted}) == 3


def test_a_front_of_house_requester_is_kept_but_not_sent_to_hq():
    ppl = {"route": [{"name": "มานัส ใจดี", "nickname": "", "truck": "52"}], "foh": [{"name": "สมชาย ดี", "nickname": "ชาย", "truck": None}]}
    read = {"lines": [{"is_fuel": True, "date": "2026-11-03", "text": "เติมน้ำมัน 84-4764", "plate": "84-4764", "truck_number": None, "amount": 500,
                       "requester": "สมชาย", "position": None, "unsure": False},
                      {"is_fuel": True, "date": "2026-11-03", "text": "เติมน้ำมัน 52", "plate": None, "truck_number": "52", "amount": 1000,
                       "requester": "มานัส", "position": None, "unsure": False}]}
    foh, route = cf.fuel_lines(read, ROSTER, "สระบุรี", ppl)
    assert foh["driver_name"] == "สมชาย ดี" and foh["counted"] is False
    assert route["counted"] is True


def test_confirm_needs_every_field_on_every_row(monkeypatch):
    import pytest
    from app import supabase
    rows = [{"id": 1, "note": None, "checked": True, "counted": True, "vehicle_id": None, "amount": 0, "day": "2026-11-03", "driver_name": "ก"},
            {"id": 2, "note": None, "checked": True, "counted": False, "vehicle_id": "a", "amount": 500, "day": "2026-11-03", "driver_name": None}]
    monkeypatch.setattr(supabase, "select_all", lambda table, params=None: rows)
    monkeypatch.setattr(supabase, "update", lambda *a: None)
    with pytest.raises(cf.CashFuelError):
        cf.confirm_page(1, "x")
    rows[0].update({"vehicle_id": "a", "amount": 500})
    with pytest.raises(cf.CashFuelError):                               # a หน้าโรง row needs its name too
        cf.confirm_page(1, "x")
    rows[1]["no_name"] = True                                            # ไม่มีชื่อผู้เบิก
    assert cf.confirm_page(1, "x") == {"ok": True}


def test_no_truck_written_can_be_confirmed_as_such(monkeypatch):
    import pytest
    from app import supabase
    rows = [{"id": 1, "note": "ไม่พบรถ", "checked": False, "counted": True, "vehicle_id": None, "no_truck": False, "amount": 1000,
             "day": "2026-11-02", "driver_name": "มานัส ใจดี"}]
    monkeypatch.setattr(supabase, "select_all", lambda table, params=None: rows)
    monkeypatch.setattr(supabase, "update", lambda *a: None)
    with pytest.raises(cf.CashFuelError):
        cf.confirm_page(1, "x")
    rows[0]["no_truck"] = True                                           # ไม่มีทะเบียนรถ
    assert cf.confirm_page(1, "x") == {"ok": True}
    s = cf.summary([{"vehicle_id": None, "day": "2026-11-02", "amount": 1000, "counted": True, "batch": 1, "driver_name": None}], ROSTER)
    assert s["trucks"][0]["plate"] == "ไม่มีทะเบียนรถ" and s["by_date"][0]["plate"] == "ไม่มีทะเบียนรถ"


def test_it_goes_by_who_took_the_money_not_the_truck():
    """Only คนขับรถสาย on the by-date and by-truck sheets, even in a รถหน้าโรง; everyone else on a third (owner, 12 Nov 2026)."""
    route = {"มานัส ใจดี"}
    lines = [{"vehicle_id": "a", "day": "2026-11-01", "amount": 1000, "counted": True, "batch": 1, "driver_name": "มานัส ใจดี"},
             {"vehicle_id": "b", "day": "2026-11-02", "amount": 1500, "counted": True, "batch": 1, "driver_name": "มานัส ใจดี"},   # his truck broke
             {"vehicle_id": "b", "day": "2026-11-02", "amount": 800, "counted": False, "batch": 1, "driver_name": "สมชาย ดี"},      # หน้าโรง
             {"vehicle_id": None, "day": "2026-11-03", "amount": 700, "counted": True, "batch": 1, "driver_name": None, "no_name": True},
             {"vehicle_id": "a", "day": "2026-11-01", "amount": 1000, "counted": False, "batch": 1, "driver_name": "มานัส ใจดี", "dup_of": 9}]
    s = cf.summary(lines, ROSTER, route_names=route)
    assert [t["truck_no"] for t in s["trucks"]] == ["34", "52"] and s["total"] == 2500
    assert [t["truck_no"] for t in s["other"]] == ["34", ""] and s["other_total"] == 1500
    wb = load_workbook(BytesIO(cf.to_xlsx("2026-11", s)))
    assert wb.sheetnames == ["ตามวันที่", "ตามรถ", "ไม่ใช่คนขับรถสาย"]
    ws = wb["ไม่ใช่คนขับรถสาย"]
    assert ws.cell(row=5, column=1).value == "34" and ws.cell(row=7, column=1).value == "รวม" and ws.cell(row=7, column=6).value == 1500
    assert wb["ตามรถ"].cell(row=7, column=6).value == 2500
    assert load_workbook(BytesIO(cf.to_xlsx("2026-11", cf.summary(lines[:2], ROSTER, route_names=route)))).sheetnames == ["ตามวันที่", "ตามรถ"]
