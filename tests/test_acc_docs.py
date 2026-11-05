from datetime import date

from app import acc_docs as ad

VEH = [{"id": "v5", "vehicle_number": "5", "plate_number": "1ฒถ 2085", "branch": "สระบุรี"},
       {"id": "k5", "vehicle_number": "5", "plate_number": "86-8971", "branch": "แก่งคอย"},
       {"id": "k14", "vehicle_number": "14", "plate_number": "1ฒบ 6248", "branch": "แก่งคอย"}]


def batch(**over):
    b = {"batch": "2026-10-test", "month": "2026-10", "branch": "สระบุรี", "files": {"mid": "mid.pdf"},
         "pages": [{"page": 2, "file": "mid", "file_page": 2, "feed": "midmonth", "kind": "fuel", "title": "fuel", "written_total": 2500, "lines": [
             ("26/9", "เติมน้ำมัน (05)", 1500, "6130", "น้ำมันเงินสด", {"v": "1ฒถ-2085"}),
             ("3/10", "เติมน้ำมัน (14)", 1000, "6130", "น้ำมันเงินสด", {"v": "1ฒบ 6248"})]}],
         "covers": [{"page": 1, "file": "mid", "file_page": 1, "feed": "midmonth", "title": "cover", "from": "2026-09-26", "to": "2026-10-10",
                     "written_total": 2500, "parts": {"fuel": 2500}}],
         "memos": [{"page": 50, "file": "mid", "file_page": 50, "kind": "memo", "title": "ลาออก", "summary": "x"}]}
    b.update(over)
    return b


def test_line_date_uses_the_company_month():
    assert ad.line_date("26/9", "2026-10") == date(2026, 9, 26)
    assert ad.line_date("26/12", "2027-01") == date(2026, 12, 26)
    assert ad.line_date("5/1", "2027-01") == date(2027, 1, 5)


def test_build_links_trucks_by_plate_and_takes_their_branch():
    out = ad.build(batch(), VEH)
    fuel = [e for e in out["entries"]]
    assert [e["lines"][0]["vehicle_id"] for e in fuel] == ["v5", "k14"]
    assert fuel[1]["branch"] == "แก่งคอย"                          # Kaeng Khoi's truck filled with Saraburi cash: Kaeng Khoi's cost
    assert fuel[0]["lines"][1] == {"account": "1100", "debit": 0.0, "credit": 1500.0, "branch": "สระบุรี", "vehicle_id": None, "memo": None}
    assert not out["problems"]
    kinds = {d["page"]: (d["kind"], d["status"]) for d in out["docs"]}
    assert kinds == {2: ("fuel", "ok"), 1: ("cover", "ok"), 50: ("memo", "ok")}


def test_a_page_that_does_not_add_up_is_flagged():
    b = batch()
    b["pages"][0]["written_total"] = 2830
    out = ad.build(b, VEH)
    page = next(d for d in out["docs"] if d["page"] == 2)
    assert page["status"] == "mismatch" and page["read_total"] == 2500
    assert any("2,830" in p for p in out["problems"])


def test_unknown_truck_is_reported_not_guessed():
    b = batch()
    b["pages"][0]["lines"][0] = ("26/9", "เติม", 1500, "6130", "น้ำมันเงินสด", {"v": "มค 3585"})
    out = ad.build(b, VEH)
    assert out["entries"][0]["lines"][0]["vehicle_id"] is None
    assert any("มค 3585" in p for p in out["problems"])


def test_km_rate_prefers_gps_then_odometer():
    gps = [{"kind": "gps", "start_date": "2026-10-01", "end_date": "2026-10-20", "km": 4000}]
    assert ad.km_rate(gps, date(2026, 9, 26), date(2026, 10, 25)) == (200.0, "GPS")
    odo = [{"kind": "odometer", "end_date": "2026-08-01", "km": 100_000}, {"kind": "odometer", "end_date": "2026-11-05", "km": 109_600}]
    rate, src = ad.km_rate(odo, date(2026, 9, 26), date(2026, 10, 25))
    assert src == "เลขไมล์" and round(rate) == 100
    assert ad.km_rate([], date(2026, 9, 26), date(2026, 10, 25)) == (None, None)
