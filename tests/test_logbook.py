import csv
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import logbook, shops

VEH = {
    "v1": {"id": "v1", "plate_number": "84-4786", "vehicle_number": "36", "branch": "สระบุรี", "make": "Hino", "model": "Mega 500"},
    "v2": {"id": "v2", "plate_number": "81-3195", "vehicle_number": "7", "branch": "แก่งคอย", "make": "Isuzu", "model": "NPR"},
    "v3": {"id": "v3", "plate_number": "1ฒถ 8966", "vehicle_number": "1", "branch": "สระบุรี", "make": None, "model": None},
}


def ev(id_, vid, date_, vendor=None, performed_by=None, total=1000, source="bill", summary=None, notes=None):
    return {"id": id_, "vehicle_id": vid, "event_date": date_, "vendor": vendor, "performed_by": performed_by, "source": source,
            "total_cost": total, "mileage": None, "summary": summary, "notes": notes, "low_confidence_fields": [], "created_at": f"{date_}T05:00:00+00:00"}


def ln(event_id, desc, cat, total, n=1):
    return {"event_id": event_id, "line_number": n, "description": desc, "category": cat, "line_total": total}


def rq(id_, vid, status="open", seq=1, problem="เบรกมีเสียง", **kw):
    base = {"id": id_, "seq": seq, "vehicle_id": vid, "status": status, "problem": problem, "request_date": "2026-10-05", "reported_by": "สุกฤ",
            "urgent": False, "fixed_by": None, "fixed_date": None, "fixed_event_id": None, "no_bill": False, "no_bill_reason": None, "notes": None,
            "plate_text": None, "truck_number_text": None}
    base.update(kw)
    return base


def build(events=(), lines=(), atts=(), requests=()):
    return logbook.build_rows(events=list(events), lines=list(lines), attachments=list(atts), requests=list(requests), vehicles=VEH)


@pytest.fixture
def rows():
    events = [
        ev("e1", "v1", "2026-10-10", vendor="ชมรมย์การช่าง", total=3000, summary="ถ่ายน้ำมันเครื่อง เปลี่ยนผ้าเบรก"),
        ev("e2", "v2", "2026-09-20", vendor="ช่างเก่งเจริญยนต์", total=12000),
        ev("e3", "v1", "2026-08-01", performed_by="ช่างเอ๋", total=None, source="manual"),
        ev("e4", "v3", "2026-10-18", vendor="ศูนย์อีซูซุสระบุรี", total=45000),
    ]
    lines = [ln("e1", "น้ำมันเครื่อง", "ไส้กรองและของเหลว", 1800), ln("e1", "ผ้าเบรกหน้า", "เบรก", 900, 2), ln("e1", "ค่าแรง", "ค่าแรง", 300, 3),
             ln("e2", "ลูกปืนล้อหลัง", "ช่วงล่าง", 12000), ln("e3", "ถ่ายน้ำมันเครื่อง", "ไส้กรองและของเหลว", None), ln("e4", "ชุดคลัตช์", "คลัตช์", 45000)]
    requests = [
        rq("r1", "v1", "open", seq=1, problem="กระจกข้างตก"),
        rq("r2", "v2", "at_shop", seq=2, problem="เพลาร้าว", shop="โชคพัฒนา", sent_date="2026-10-12"),
        rq("r3", "v3", "awaiting_bill", seq=3, problem="ไฟท้ายเสีย", fixed_by="ช่างลิ้ม 24 ไดนาโม", fixed_date="2026-10-15"),
        rq("r4", "v1", "completed", seq=4, problem="เปลี่ยนไฟเลี้ยว", fixed_by="ช่างเอ๋", fixed_date="2026-10-14", no_bill=True, no_bill_reason="งานเล็กน้อย"),
        rq("r5", "v2", "cancelled", seq=5, problem="ซ่อมที่นั่ง"),
        rq("r6", "v1", "completed", seq=6, problem="เบรกหลังมีเสียง", fixed_by="ชมรมย์การช่าง", fixed_date="2026-10-10", fixed_event_id="e1"),
    ]
    return build(events, lines, [{"event_id": "e1"}, {"event_id": "e1"}], requests)


def q(rows, **f):
    sort = f.pop("sort", "date_desc")
    return logbook.query(rows, f, sort=sort, limit=200)


def keys(res):
    return [r["key"] for r in res["rows"]]


# -------------------------------------------------- shop folding
@pytest.mark.parametrize("written, canon", [
    ("ชมรมย์การช่าง", "ชมรมย์การช่าง"), ("อู่ชมรม", "ชมรมย์การช่าง"), ("  ชมรม ", "ชมรมย์การช่าง"),
    ("ช่างเก่งเจริญยนต์", "อู่ช่างเก่ง เจริญยนต์"), ("อู่ช่างเก่ง", "อู่ช่างเก่ง เจริญยนต์"), ("อู่ช่างเก่ง เจริญยนต์", "อู่ช่างเก่ง เจริญยนต์"),
    ("โชคพัฒนา", "โชคพัฒนา"), ("มงคลไดนาโม", "มงคลไดนาโม"), ("ช่างลิ้ม 24 ไดนาโม", "ช่างลิ้ม 24 ไดนาโม"),
    ("ศูนย์อีซูซุสระบุรี", "อิซูซุจึงกงเฮงแก่งคอย"), ("ศูนย์ isuzu", "อิซูซุจึงกงเฮงแก่งคอย"), ("ISUZU  center", "อิซูซุจึงกงเฮงแก่งคอย"),
    ("ศูนย์ฮีโน่", "ฮีโน่ เอเซีย หนองแค"), ("Hino Saraburi", "ฮีโน่ เอเซีย หนองแค"),
    ("ช่างเอ๋", shops.IN_HOUSE), ("[ทดสอบ] ช่างเอ๋", shops.IN_HOUSE),
    ("อู่ใหม่ไม่รู้จัก", "อู่ใหม่ไม่รู้จัก"), ("", ""), (None, ""),
])
def test_shop_names_fold_into_one(written, canon):
    assert shops.canonical(written) == canon


def test_the_board_offers_every_shop_you_named_plus_the_mechanic():
    assert shops.choices() == ["ชมรมย์การช่าง", "อู่ช่างเก่ง เจริญยนต์", "โชคพัฒนา", "มงคลไดนาโม", "ช่างลิ้ม 24 ไดนาโม", "อิซูซุจึงกงเฮงแก่งคอย", "ฮีโน่ เอเซีย หนองแค", shops.IN_HOUSE]


# ----------------------------------------------- what becomes a row
def test_a_repair_with_a_record_is_listed_once_as_the_event_not_also_as_its_request(rows):
    k = [r["key"] for r in rows]
    assert "event:e1" in k and "request:r6" not in k
    e1 = next(r for r in rows if r["key"] == "event:e1")
    assert e1["from_requests"] == [{"no": 6, "problem": "เบรกหลังมีเสียง"}]


def test_every_other_request_is_a_row_with_the_right_stage(rows):
    st = {r["id"]: r["status"] for r in rows if r["kind"] == "request"}
    assert st == {"r1": "open", "r2": "at_shop", "r3": "awaiting_bill", "r4": "done_no_bill", "r5": "cancelled"}
    assert len(rows) == 4 + 5


def test_request_rows_are_dated_by_the_date_that_matters_for_their_stage(rows):
    d = {r["id"]: r["date"] for r in rows if r["kind"] == "request"}
    assert d == {"r1": "2026-10-05", "r2": "2026-10-12", "r3": "2026-10-15", "r4": "2026-10-14", "r5": "2026-10-05"}


def test_shop_and_truck_details_are_filled_in(rows):
    e4 = next(r for r in rows if r["key"] == "event:e4")
    assert (e4["shop"], e4["plate"], e4["truck_number"], e4["branch"]) == ("อิซูซุจึงกงเฮงแก่งคอย", "1ฒถ 8966", "1", "สระบุรี")
    r3 = next(r for r in rows if r["key"] == "request:r3")
    assert r3["shop"] == "ช่างลิ้ม 24 ไดนาโม" and r3["problem"] == "ไฟท้ายเสีย"            # who it came back from
    r2 = next(r for r in rows if r["key"] == "request:r2")
    assert r2["shop"] == "โชคพัฒนา" and r2["sent_date"] == "2026-10-12"


def test_a_request_for_an_unknown_vehicle_keeps_the_text_the_paper_had():
    r = build(requests=[rq("rx", None, plate_text="9กก 1", truck_number_text="99")])[0]
    assert (r["plate"], r["truck_number"]) == ("9กก 1", "99")


# ------------------------------------------------------- filters
def test_no_filters_returns_everything_newest_first(rows):
    res = q(rows)
    assert res["total"] == 9 and keys(res)[0] == "event:e4"
    dates = [r["date"] for r in res["rows"]]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.parametrize("status, expected", [
    ("active", {"request:r1", "request:r2", "request:r3"}),
    ("done", {"event:e1", "event:e2", "event:e3", "event:e4", "request:r4"}),
    ("open", {"request:r1"}), ("at_shop", {"request:r2"}), ("awaiting_bill", {"request:r3"}), ("cancelled", {"request:r5"}),
])
def test_status_filter(rows, status, expected):
    assert set(keys(q(rows, status=status))) == expected


def test_date_range_is_inclusive_and_filters_by_the_rows_own_date(rows):
    assert set(keys(q(rows, date_from="2026-10-10", date_to="2026-10-15"))) == {"event:e1", "request:r2", "request:r3", "request:r4"}
    assert set(keys(q(rows, date_to="2026-08-01"))) == {"event:e3"}
    assert q(rows, date_from="garbage")["total"] == 9                                 # a bad date is ignored, not an error


def test_branch_truck_shop_category_source(rows):
    assert {r["plate"] for r in q(rows, branch="แก่งคอย")["rows"]} == {"81-3195"}
    assert set(keys(q(rows, vehicle_id="v3"))) == {"event:e4", "request:r3"}
    assert set(keys(q(rows, shop="ชมรมย์การช่าง"))) == {"event:e1"}
    assert set(keys(q(rows, shop=shops.IN_HOUSE))) == {"event:e3", "request:r4"}
    assert set(keys(q(rows, category="เบรก"))) == {"event:e1"}
    assert set(keys(q(rows, source="manual"))) == {"event:e3"}
    assert set(keys(q(rows, source="request"))) == {"request:r1", "request:r2", "request:r3", "request:r4", "request:r5"}


def test_cost_range_only_matches_rows_that_have_a_cost(rows):
    assert set(keys(q(rows, min_cost="10000"))) == {"event:e2", "event:e4"}
    assert set(keys(q(rows, min_cost="1000", max_cost="5000"))) == {"event:e1"}
    assert q(rows, min_cost="0")["total"] == 3                                        # rows with no cost (manual / requests) aren't "0 baht"


def test_free_text_searches_everything_a_person_would_remember(rows):
    assert set(keys(q(rows, q="4786"))) == {"event:e1", "event:e3", "request:r1", "request:r4"}   # truck #36's rows (plate, any spacing)
    assert set(keys(q(rows, q="84 4786"))) == set(keys(q(rows, q="844786")))                      # 'plate with or without the dash'
    assert set(keys(q(rows, q="ลูกปืน"))) == {"event:e2"}                                          # a part named on a bill line
    assert set(keys(q(rows, q="น้ำมัน เครื่อง"))) == {"event:e1", "event:e3"}                      # Thai spacing doesn't matter
    assert set(keys(q(rows, q="เพลาร้าว"))) == {"request:r2"}                                      # a reported problem
    assert set(keys(q(rows, q="ชมรม"))) == {"event:e1"}                                            # the shop, however it was written
    assert set(keys(q(rows, q="เบรกหลังมีเสียง"))) == {"event:e1"}                                 # the request that this bill closed
    assert q(rows, q="ไม่มีทางเจอ")["total"] == 0


def test_all_words_must_match(rows):
    assert set(keys(q(rows, q="ชมรม น้ำมัน"))) == {"event:e1"}
    assert q(rows, q="ชมรม ลูกปืน")["total"] == 0


def test_filters_combine(rows):
    assert set(keys(q(rows, status="done", branch="สระบุรี", date_from="2026-10-01"))) == {"event:e1", "event:e4", "request:r4"}


def test_needs_review_filter():
    rows = build(events=[ev("e1", "v1", "2024-01-01", vendor="x", total=500)], lines=[ln("e1", "a", None, 100)])    # total != lines, date far from saved
    assert rows[0]["needs_review"] is True and "ยอดรวมไม่ตรงกับรายการ" in rows[0]["review_reasons"][0]      # 500 on the bill, 100 in the lines
    ok = build(events=[ev("e2", "v1", "2024-01-01", vendor="x", total=100)], lines=[ln("e2", "a", None, 100)])
    assert ok[0]["needs_review"] is False
    both = rows + ok
    assert [r["key"] for r in logbook.query(both, {"review": "1"})["rows"]] == ["event:e1"]
    assert logbook.query(both, {})["total"] == 2


# --------------------------------------------- sort / paging / stats
def test_sorting_and_paging(rows):
    assert keys(logbook.query(rows, {}, sort="date_asc", limit=2)) == ["event:e3", "event:e2"]        # oldest first
    costs = [r["total_cost"] for r in q(rows, sort="cost_desc")["rows"][:3]]
    assert costs == [45000, 12000, 3000]
    page2 = logbook.query(rows, {}, limit=4, offset=4)
    assert len(page2["rows"]) == 4 and page2["total"] == 9 and page2["offset"] == 4
    assert logbook.query(rows, {}, limit=10_000)["limit"] == logbook.MAX_LIMIT
    assert logbook.query(rows, {}, limit=0)["limit"] == 50 and logbook.query(rows, {}, offset=-5)["offset"] == 0


def test_stats_cover_all_matches_not_just_the_page(rows):
    res = logbook.query(rows, {"status": "done"}, limit=1)
    assert len(res["rows"]) == 1 and res["total"] == 5
    assert res["stats"]["total_cost"] == 3000 + 12000 + 45000 and res["stats"]["trucks"] == 3
    assert res["stats"]["by_status"] == {"done": 4, "done_no_bill": 1}


def test_internal_search_text_never_leaks_to_the_page(rows):
    assert all(not any(k.startswith("_") for k in r) for r in q(rows)["rows"])


# ------------------------------------------------------------ facets
def test_facets_offer_every_shop_even_ones_with_no_work_yet(rows):
    f = logbook.facets(rows)
    counts = {s["value"]: s["count"] for s in f["shops"]}
    assert counts["ชมรมย์การช่าง"] == 1 and counts["ฮีโน่ เอเซีย หนองแค"] == 0 and counts["มงคลไดนาโม"] == 0
    assert {c["value"] for c in f["categories"]} == {"ไส้กรองและของเหลว", "เบรก", "ค่าแรง", "ช่วงล่าง", "คลัตช์"}
    assert {b["value"]: b["count"] for b in f["branches"]} == {"สระบุรี": 6, "แก่งคอย": 3}
    assert {s["value"]: s["count"] for s in f["statuses"]}["at_shop"] == 1


def test_facets_list_each_truck_with_history_in_fleet_order(rows):
    trucks = logbook.facets(rows)["vehicles"]
    assert [(t["truck_number"], t["plate"], t["count"]) for t in trucks] == [("1", "1ฒถ 8966", 2), ("36", "84-4786", 4), ("7", "81-3195", 3)]
    assert trucks[0]["value"] == "v3" and trucks[0]["branch"] == "สระบุรี"


def test_one_truck_filter_gives_its_whole_history(rows):
    res = q(rows, vehicle_id="v1")
    assert sorted(keys(res)) == ["event:e1", "event:e3", "request:r1", "request:r4"] and res["stats"]["trucks"] == 1


# --------------------------------------------------------------- CSV
def test_csv_has_both_year_systems_and_thai_text_survives():
    rows = build(events=[ev("e1", "v1", "2026-10-10", vendor="ชมรมย์การช่าง", total=3000, summary="ถ่ายน้ำมัน")], lines=[ln("e1", "น้ำมัน", "ไส้กรองและของเหลว", 3000)])
    out = list(csv.reader(io.StringIO(logbook.to_csv(rows))))
    assert out[0] == logbook.CSV_HEADER
    row = dict(zip(out[0], out[1]))
    assert row["วันที่ (ค.ศ.)"] == "2026-10-10" and row["ปี พ.ศ."] == "2569" and row["เบอร์รถ"] == "36" and row["ร้าน / ช่าง"] == "ชมรมย์การช่าง"
    assert row["สถานะ"] == "เสร็จแล้ว" and row["ประเภท"] == "บิล" and row["ยอดรวม (บาท)"] == "3000" and row["หมวด"] == "ไส้กรองและของเหลว"


def test_csv_quotes_commas_and_newlines_properly():
    rows = build(requests=[rq("r1", "v1", problem='เบรก, "ดึงซ้าย"\nมีเสียง')])
    out = list(csv.reader(io.StringIO(logbook.to_csv(rows))))
    assert len(out) == 2 and out[1][CSV_COL := logbook.CSV_HEADER.index("สรุปงาน")] == 'เบรก, "ดึงซ้าย"\nมีเสียง'
