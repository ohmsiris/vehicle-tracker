"""บัญชี › ตรวจความถูกต้อง (app/acc_checks.py): the pure checks on fake rows, and run() end to end on a faked database."""

import pytest

from app import acc_checks as ac
from app import supabase
from app.accounting import period_bounds

PNL = {"5510": "cogs", "5100": "cogs", "6130": "expense", "6120": "expense", "4100": "income", "4150": "income", "5060": "expense",
       "7100": "expense", "5500": "cogs"}
SB, KK = "สระบุรี", "แก่งคอย"


def ln(account, debit=0.0, credit=0.0, branch=SB, vehicle_id=None):
    return {"account": account, "debit": debit, "credit": credit, "branch": branch, "vehicle_id": vehicle_id}


def ent(id_, d, source, lines, party=None, description=None, doc_no=None, source_id=None, branch=SB):
    return {"id": id_, "entry_date": d, "branch": branch, "source": source, "source_id": source_id or id_, "party": party,
            "description": description, "doc_no": doc_no, "lines": lines}


# ------------------------------------------------------------------ names and document numbers
def test_norm_name_strips_company_words_spaces_and_tone_marks():
    a = ac.norm_name("บริษัท พีซี เคมีคอล ลูบ จำกัด (สำนักงานใหญ่)")
    assert a == ac.norm_name("บจก.พีซี เคมีคอล ลู้บ") == "พีซีเคมีคอลลูบ"
    assert ac.norm_name("หจก. ปัญจบวรอะไหล่") == "ปัญจบวรอะไหล"
    assert ac.norm_name("ABC Co., Ltd.") == ac.norm_name("abc company limited") == "abc"


def test_similar_names():
    assert ac.similar("บจก.พีซี เคมีคอล ลู้บ", "บริษัท พี เคมีคอล ลู้บ จำกัด")       # one letter off
    assert ac.similar("วิศวภัณฑ์", "บริษัท วิศวภัณฑ์ (1992) จำกัด")                   # one inside the other
    assert not ac.similar("วิศวภัณฑ์", "PEA มิเตอร์ 72")
    assert not ac.similar("", "บริษัท")                                                   # nothing left to compare


def test_document_numbers_but_not_plates_pages_or_truck_numbers():
    assert ac.norm_doc("PC6906-081") == "PC6906081"
    assert ac.norm_doc("10201535") == "10201535"                    # a cheque
    assert ac.norm_doc("หน้า 24") is None                           # a petty-cash paper's page
    assert ac.norm_doc("83-2919") is None                           # a plate
    assert ac.norm_doc("58") is None
    e = ent("x", "2026-06-25", "hq_cheques", [], description="น้ำมันคอม 2 ถัง Castrol · เช็ค 10201535 · #PC6906-081 · น้ำมัน #58 83-2919",
            doc_no="10201535")
    assert ac.doc_tags(e) == {"10201535", "PC6906081"}
    pea = ent("y", "2026-10-20", "hq_cheques", [], description="ค่าไฟฟ้า · จ่ายใบแจ้ง PEA 296011397238 · เช็ค 10207175")
    assert "296011397238" in ac.doc_tags(pea) and ac.pea_invoice_paid(pea) == "296011397238"


# ------------------------------------------------------------------ pairs that are supposed to look alike
def test_legit_pairs():
    bill = ent("b", "2026-09-25", "electricity", [ln("5100", 900), ln("1400", 63), ln("2100", credit=963)])
    pays = ent("c", "2026-10-20", "hq_cheques", [ln("2100", 963), ln("1130", credit=963)])
    assert ac.legit_pair(bill, pays) and ac.legit_pair(pays, bill)
    fund = ent("f", "2026-10-15", "hq_cheques", [ln("1100", 5000), ln("1120", credit=5000)])
    paper = ent("p", "2026-10-02", "midmonth", [ln("6130", 5000), ln("1100", credit=5000)])
    assert ac.legit_pair(fund, paper)
    sold = ent("s", "2026-10-25", "sales_program", [ln("1600", 7000), ln("4150", credit=7000)])
    settle = ent("t", "2026-10-20", "hq_cheques", [ln("1600", 7000), ln("1130", credit=7000)])
    assert ac.legit_pair(sold, settle)                                  # internal ice vs the 1600 that settles it
    purchase = ent("u", "2026-06-26", "purchase", [ln("5510", 42600), ln("1400", 2982), ln("2100", credit=45582)])
    cost_cheque = ent("v", "2026-06-25", "hq_cheques", [ln("5510", 42600), ln("1400", 2982), ln("1130", credit=45582)])
    assert not ac.legit_pair(purchase, cost_cheque)                     # both book the cost: the double count


# ------------------------------------------------------------------ 5: look-alikes
def _pc6906():
    cheque = ent("chq", "2026-06-25", "hq_cheques", [ln("5510", 42600), ln("1400", 2982), ln("1130", credit=45582)], party="บจก.พีซี เคมีคอล ลู้บ",
                 description="น้ำมันคอม 2 ถัง Castrol · เช็ค 10201535 · #PC6906-081 · วันที่ประมาณ (บิลเดือนก่อน)", doc_no="10201535",
                 source_id="cheque-2026-07-สระบุรี:r17")
    bill = ent("pur", "2026-06-26", "purchase", [ln("5510", 42600), ln("1400", 2982), ln("2100", credit=45582)],
               party="บริษัท พีซี เคมีคอล ลูบ จำกัด (สำนักงานใหญ่)", description="ซื้ออะไหล่ / วัสดุโรงงาน", doc_no="PC6906-081")
    return cheque, bill


def test_pc6906_081_found_across_the_month_boundary():
    """The known double of 2026-07: the purchase bill (26 Jun, company month 07) and the HQ cheque booked as the cost again (estimated
    25 Jun, company month 06) -- found from either month."""
    cheque, bill = _pc6906()
    for month in ("2026-07", "2026-06"):
        s, e = period_bounds(month)
        pairs = ac.find_lookalikes([cheque, bill], s, e, PNL)
        assert len(pairs) == 1 and pairs[0]["reason"] == "doc" and "PC6906081" in pairs[0]["why"]
        assert {pairs[0]["a"]["id"], pairs[0]["b"]["id"]} == {"chq", "pur"}
    s, e = period_bounds("2026-07")
    c = ac.lookalikes_check(ac.find_lookalikes([cheque, bill], s, e, PNL), SB)
    assert c["state"] == "warn" and c["data"]["pairs"] == 1 and len(c["rows"]) == 2 and c["rows"][0]["pair"] == 1
    assert "45,582" in c["summary"] and "25/06/2026" in c["rows"][0]["text"]
    assert ac.lookalikes_check([], SB)["state"] == "ok"
    # out of the window entirely: nothing
    s, e = period_bounds("2026-09")
    assert ac.find_lookalikes([cheque, bill], s, e, PNL) == []


def test_same_amount_and_similar_name_from_two_sources():
    s, e = period_bounds("2026-10")
    a = ent("a", "2026-10-03", "purchase", [ln("5510", 12000), ln("2100", credit=12000)], party="บริษัท วิศวภัณฑ์ (1992) จำกัด")
    b = ent("b", "2026-10-04", "midmonth", [ln("5510", 12000), ln("1100", credit=12000)], party="วิศวภัณฑ์", description="ลูกปืน")
    pairs = ac.find_lookalikes([a, b], s, e, PNL)
    assert len(pairs) == 1 and pairs[0]["reason"] == "amount"
    far = dict(b, entry_date="2026-10-20")                                   # two dated documents 16 days apart: two purchases
    assert ac.find_lookalikes([a, far], s, e, PNL) == []
    other = dict(b, party="ร้านอื่น", description="อย่างอื่น")
    assert ac.find_lookalikes([a, other], s, e, PNL) == []


def test_papers_two_fills_are_not_a_double_but_the_same_receipt_is():
    s, e = period_bounds("2026-07")
    end = ent("e1", "2026-06-24", "endmonth", [ln("6130", 4000, vehicle_id="t1"), ln("1100", credit=4000)], party="น้ำมันเงินสด", description="เติมน้ำมัน เบอร์ 1")
    mid = ent("m1", "2026-06-27", "midmonth", [ln("6130", 4000, vehicle_id="t1"), ln("1100", credit=4000)], party="น้ำมันเงินสด", description="เติมน้ำมัน เบอร์ 1")
    assert ac.find_lookalikes([end, mid], s, e, PNL) == []                  # 3 days apart: two fills
    same_day = dict(mid, entry_date="2026-06-27")
    end_same = dict(end, entry_date="2026-06-27")
    assert len(ac.find_lookalikes([end_same, same_day], s, e, PNL)) == 1    # the same receipt in both lists
    other_truck = dict(same_day, lines=[ln("6130", 4000, vehicle_id="t2"), ln("1100", credit=4000)])
    assert ac.find_lookalikes([end_same, other_truck], s, e, PNL) == []     # two trucks


def test_lookalikes_skip_same_source_payments_and_small_amounts():
    s, e = period_bounds("2026-10")
    bill = ent("b", "2026-09-25", "electricity", [ln("5100", 900000), ln("1400", 63000), ln("2100", credit=963000)], party="PEA มิเตอร์ 72",
               doc_no="296011397256")
    pays = ent("c", "2026-10-20", "hq_cheques", [ln("2100", 963000), ln("1130", credit=963000)], party="การไฟฟ้า",
               description="ค่าไฟฟ้า 72 · จ่ายใบแจ้ง PEA 296011397256 · เช็ค 10207175")
    assert ac.find_lookalikes([bill, pays], s, e, PNL) == []                # the cheque that pays the bill
    a = ent("x1", "2026-10-03", "midmonth", [ln("6130", 5000), ln("1100", credit=5000)], party="ปั๊ม")
    b = ent("x2", "2026-10-03", "midmonth", [ln("6130", 5000), ln("1100", credit=5000)], party="ปั๊ม")
    assert ac.find_lookalikes([a, b], s, e, PNL) == []                      # same source
    c = ent("y1", "2026-10-03", "midmonth", [ln("6130", 500), ln("1100", credit=500)], party="ปั๊ม")
    d = ent("y2", "2026-10-03", "endmonth", [ln("6130", 500), ln("1100", credit=500)], party="ปั๊ม")
    assert ac.find_lookalikes([c, d], s, e, PNL) == []                      # under 1,000: small repeat buys


# ------------------------------------------------------------------ 1 + 2: cash and advances
def test_balance_state_thresholds():
    assert ac.balance_state(0, 0) == "info"
    assert ac.balance_state(100000, 0) == "warn"                            # one side not in yet
    assert ac.balance_state(100000, 99100) == "ok"                          # within 1,000
    assert ac.balance_state(500000, 495500) == "ok"                         # within 1%
    assert ac.balance_state(500000, 480000) == "warn"                       # 4%
    assert ac.balance_state(500000, 400000) == "bad"                        # 20%


def test_advances_net_out():
    s, e = period_bounds("2026-10")
    adv = ent("a", "2026-10-08", "midmonth", [ln("1500", 255000), ln("1100", credit=255000)])
    pay = ent("p", "2026-10-25", "payroll", [ln("5400", 900000), ln("1500", credit=252000), ln("1100", credit=648000)])
    wo = ent("w", "2026-10-20", "adjustment", [ln("7920", 3000), ln("1500", credit=3000)])
    c = ac.advances([adv, pay, wo], SB, s, e)
    assert c["state"] == "ok" and c["data"]["in"] == 255000 and c["data"]["out"] == 255000
    assert c["data"]["out_by_source"] == {"payroll": 252000, "adjustment": 3000}
    c = ac.advances([adv, pay], SB, s, e)
    assert c["state"] == "warn" and "ต่าง +3,000" in c["summary"]
    assert ac.advances([adv, pay], KK, s, e)["state"] == "info"


def _cash_month():
    return [
        # this month (2026-10): mid-month top-up and papers, payroll, last month's end-of-month top-up
        ent("f1", "2026-10-15", "hq_cheques", [ln("1100", 321141), ln("1120", credit=321141)], description="คชจ.+เบิกกลางเดือน 10/2569 · เช็ค 1"),
        ent("f2", "2026-10-15", "hq_cheques", [ln("1100", 565771), ln("1120", credit=565771)], description="เงินเดือนสด+ค่าใช้จ่ายสิ้นเดือน 09 -69 · เช็ค 2"),
        ent("m1", "2026-10-05", "midmonth", [ln("6130", 322141), ln("1100", credit=322141)]),
        ent("e1", "2026-10-20", "endmonth", [ln("6130", 211690), ln("1100", credit=211690)]),
        ent("p1", "2026-10-25", "payroll", [ln("5400", 600000), ln("1100", credit=447658), ln("1500", credit=152342)]),
        # next month: the end-of-month top-up for October
        ent("f3", "2026-11-15", "hq_cheques", [ln("1100", 600000), ln("1120", credit=600000)], description="เงินเดือนสด+ค่าใช้จ่ายสิ้นเดือน 10-69"),
        # last month: a different mid-month top-up
        ent("f0", "2026-09-15", "hq_cheques", [ln("1100", 348528), ln("1120", credit=348528)], description="คชจ.+เบิกกลางเดือน 9/2569"),
    ]


def test_petty_cash_matches_each_top_up_with_what_it_pays_for():
    c = ac.petty_cash(_cash_month(), SB, "2026-10")
    n = c["data"]
    assert n["in"] == 886912 and n["out"] == 981489                         # the plain month totals, shown
    assert n["mid_in"] == 321141 and n["mid_out"] == 322141                  # within 1,000: ok
    assert n["end_in_next_run"] == 600000 and n["end_out"] == 211690 and n["cash_wages"] == 388310
    assert c["state"] == "ok"
    assert "เหลือเป็นเงินเดือนสด 388,310" in c["summary"]
    assert c["rows"][0]["text"].startswith("กลางเดือน")


def test_petty_cash_end_of_month_not_in_yet_and_short_papers():
    rows = [r for r in _cash_month() if r["id"] != "f3"]
    c = ac.petty_cash(rows, SB, "2026-10")
    assert c["state"] == "info" and "เช็คเติมยังไม่มา" in c["summary"]
    short = [dict(r, lines=[ln("1100", 100000), ln("1120", credit=100000)]) if r["id"] == "f3" else r for r in _cash_month()]
    assert ac.petty_cash(short, SB, "2026-10")["state"] == "bad"             # 100,000 sent for 211,690 of papers


def test_petty_cash_copied_sheet_and_stale_label():
    rows = _cash_month()
    rows[-1] = ent("f0", "2026-09-15", "hq_cheques", [ln("1100", 321141), ln("1120", credit=321141)], description="คชจ.+เบิกกลางเดือน 9/2569")
    rows[0] = dict(rows[0], description="คชจ.+เบิกกลางเดือน 9/2569 · เช็ค 1")      # HQ forgot to change the label too
    c = ac.petty_cash(rows, SB, "2026-10")
    assert c["state"] == "warn"
    texts = " | ".join(r["text"] for r in c["rows"])
    assert "ยอดเท่ากับเดือนก่อนทุกบาท" in texts and "ป้ายในใบเช็คบอก ก.ย. 2569" in texts
    assert ac.label_month({"description": "เงินเดือนสด+ค่าใช้จ่ายสิ้นเดือน 08-2569"}) == "2026-08"
    assert ac.label_month({"description": "คชจ.+เบิกกลางเดือน 7/2569"}) == "2026-07"
    assert ac.funding_kind({"description": "ถอนสดโอนเข้า K.รัชนิดา(คชจ.เบิกกลางเดือนไม่พอ)"}) == "mid"
    assert ac.funding_kind({"description": "คืนคุณโอม สำรอง เงินเดือนสด ไม่พอ"}) == "end"


def test_petty_cash_nothing():
    assert ac.petty_cash([], KK, "2026-10")["state"] == "info"


# ------------------------------------------------------------------ 3: bills owed
def _pea(inv, d, total, branch=SB):
    return ent(f"e{inv}", d, "electricity", [ln("5100", total - 7, branch=branch), ln("1400", 7, branch=branch), ln("2100", credit=total, branch=branch)],
               party="PEA มิเตอร์ 72", source_id=f"pea:{inv}", doc_no=inv, branch=branch)


def _pays(id_, d, inv, amount, branch=SB):
    return ent(id_, d, "hq_cheques", [ln("2100", amount, branch=branch), ln("1130", credit=amount, branch=branch)],
               description=f"ค่าไฟฟ้า · จ่ายใบแจ้ง PEA {inv} · เช็ค 1", branch=branch)


def test_bills_paid_unpaid_and_paid_twice():
    s, e = period_bounds("2026-09")
    owed = [_pea("111111", "2026-09-25", 800000), _pays("c1", "2026-10-20", "111111", 800000)]
    c = ac.bills_check(owed, SB, s, e, elec_bills=[{"invoice_no": "111111", "branch": SB, "total": 800000, "deposit_interest": 0}], later_run=True)
    assert c["state"] == "ok" and c["data"]["pea_paid"] == 800000
    unpaid = owed[:1]
    assert ac.bills_check(unpaid, SB, s, e, elec_bills=[], later_run=False)["state"] == "info"     # next month's run not in yet
    c = ac.bills_check(unpaid, SB, s, e, elec_bills=[], later_run=True)
    assert c["state"] == "warn" and "ยังไม่มีเช็คจ่าย" in c["rows"][0]["text"]
    twice = owed + [_pays("c2", "2026-11-20", "111111", 800000)]
    c = ac.bills_check(twice, SB, s, e, elec_bills=[], later_run=True)
    assert c["state"] == "bad" and "จ่ายซ้ำ 2 ครั้ง" in c["rows"][0]["text"]
    assert ac.bills_check(owed, KK, s, e, elec_bills=[], later_run=True)["state"] == "ok"           # the other branch has nothing


def test_bills_payment_for_a_bill_not_in_the_books_and_stored_bill_not_booked():
    s, e = period_bounds("2026-10")
    c = ac.bills_check([_pays("c1", "2026-10-20", "999999", 5000)], SB, s, e, elec_bills=[], later_run=False)
    assert c["state"] == "bad" and "ไม่มีบิลนี้ในบัญชี" in c["rows"][0]["text"]
    c = ac.bills_check([], SB, s, e, elec_bills=[{"invoice_no": "222222", "branch": SB, "total": 1000, "deposit_interest": 0}], later_run=False)
    assert c["state"] == "bad" and "ไม่ได้ลงบัญชี" in c["rows"][0]["text"]
    booked = [_pea("222222", "2026-10-25", 1500)]
    c = ac.bills_check(booked, SB, s, e, elec_bills=[{"invoice_no": "222222", "branch": SB, "total": 1000, "deposit_interest": 0}], later_run=False)
    assert c["state"] == "bad" and any("บิลเขียน 1,000" in r["text"] for r in c["rows"])
    vague = ent("v", "2026-10-20", "hq_cheques", [ln("2100", 3000), ln("1130", credit=3000)], description="จ่ายร้าน")
    assert ac.bills_check([vague], SB, s, e, elec_bills=[], later_run=False)["state"] == "warn"


def test_bills_card_fuel_is_info_and_shop_bills_from_payables():
    s, e = period_bounds("2026-10")
    fuel = ent("f", "2026-10-25", "card_fuel", [ln("6120", 9345.79), ln("1400", 654.21), ln("2100", credit=10000)], source_id="fuel:pt:2026-10:x")
    pur = ent("p", "2026-10-03", "purchase", [ln("5510", 5000), ln("2100", credit=5000)], source_id="pb1")
    trk = ent("t", "2026-10-04", "truck_bill", [ln("6100", 3000), ln("2100", credit=3000)], source_id="ev1")
    shop = [{"source": "purchase", "id": "pb1", "state": "due", "changed": 0.0, "shop": "วิศวภัณฑ์"},
            {"source": "truck", "id": "ev1", "state": "paid", "changed": 0.0, "shop": "อู่"}]
    c = ac.bills_check([fuel, pur, trk], SB, s, e, elec_bills=[], later_run=False, shop_bills=shop)
    assert c["state"] == "info" and c["data"]["no_payer"] == {"card_fuel": 10000}
    assert c["data"]["shop_owed"] == 8000 and c["data"]["shop_unpaid"] == 5000
    assert "น้ำมันบัตร ค้าง 10,000" in c["summary"]
    shop[1]["changed"] = 250.0
    assert ac.bills_check([fuel, pur, trk], SB, s, e, elec_bills=[], later_run=False, shop_bills=shop)["state"] == "warn"


# ------------------------------------------------------------------ 4: file totals
def _file(fid, key, file_total, rows_total, entries_total, booked_at="2026-11-01", branch=SB):
    entries = [{"lines": [{"account": "5300", "debit": entries_total, "credit": 0}, {"account": "1130", "debit": 0, "credit": entries_total}]}]
    return {"id": fid, "file_name": f"{fid}.xls", "booked_at": booked_at,
            "preview": {"batches": [{"key": key, "branch": branch, "source": "hq_cheques", "title": "เช็ค", "file_total": file_total,
                                     "rows_total": rows_total, "entries": entries}]}}


def test_file_totals():
    key = "cheque-2026-10-สระบุรี"
    c = ac.file_totals_check([_file("a", key, 845000, 845000, 845000)], {key: (20, 845000.4)}, SB)
    assert c["state"] == "ok" and c["data"] == {"batches": 1, "bad": 0}
    c = ac.file_totals_check([_file("a", key, 845000, 845000, 845000)], {key: (40, 1690000)}, SB)
    assert c["state"] == "bad" and "ตอนนี้ในบัญชีมี 1,690,000" in c["rows"][0]["text"]                # doubled after booking
    c = ac.file_totals_check([_file("a", key, 845000, 840000, 840000)], {key: (19, 840000)}, SB)
    assert c["state"] == "bad" and "ยอดในไฟล์ 845,000 แต่แถวรวมได้ 840,000" in c["rows"][0]["text"]    # read short
    files = [_file("old", key, 1, 1, 1, "2026-11-01"), _file("new", key, 845000, 845000, 845000, "2026-11-05")]
    c = ac.file_totals_check(files, {key: (20, 845000)}, SB)
    assert c["state"] == "ok" and any("ถูกแทน" in r["text"] for r in c["rows"])                         # re-dropped: the latest owns it
    assert ac.file_totals_check([], {}, SB)["state"] == "info"
    assert ac.file_totals_check([_file("a", key, 1, 1, 1)], {}, KK)["state"] == "info"                   # another branch's file


# ------------------------------------------------------------------ 6: vs the usual
def test_usual_check():
    names = {"5100": "ค่าไฟฟ้า", "5300": "เกลือ", "7100": "เงินเดือนสำนักงาน", "6300": "ภาษีรถ", "7900": "อื่นๆ"}
    lines = []
    for m in ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"):
        lines += [{"account": "5100", "debit": 800000, "credit": 0, "branch": SB, "month": m},
                  {"account": "5300", "debit": 40000, "credit": 0, "branch": SB, "month": m},
                  {"account": "7100", "debit": 54000, "credit": 0, "branch": SB, "month": m},
                  {"account": "7900", "debit": 1000, "credit": 0, "branch": SB, "month": m}]
    lines += [{"account": "5100", "debit": 820000, "credit": 0, "branch": SB, "month": "2026-10"},      # normal
              {"account": "5300", "debit": 90000, "credit": 0, "branch": SB, "month": "2026-10"},       # +125%, +50,000: warn
              {"account": "7900", "debit": 15000, "credit": 0, "branch": SB, "month": "2026-10"},       # +1400% but only +14,000: fine
              {"account": "6300", "debit": 30000, "credit": 0, "branch": SB, "month": "2026-10"}]       # new: info
    by = ac.pnl_amounts(lines, {"5100": "cogs", "5300": "cogs", "7100": "expense", "6300": "expense", "7900": "expense"}, SB)
    c = ac.usual_check("2026-10", by, SB, names)
    assert c["state"] == "warn" and c["data"]["warn"] == 2 and len(c["data"]["months"]) == 6
    texts = [r["text"] for r in c["rows"]]
    assert any(t.startswith("5300 เกลือ: 90,000 ปกติ 40,000 (+125%)") for t in texts)
    assert any(t.startswith("7100") and "ยังไม่มีเดือนนี้" in t for t in texts)                          # 54,000 usual, 0 now
    assert any(t.startswith("6300") and "ใหม่เดือนนี้" in t for t in texts)
    assert not any(t.startswith(("5100", "7900")) for t in texts)
    assert ac.usual_check("2026-10", {"2026-10": {"5100": 1.0}}, SB, names)["state"] == "info"            # no history


def test_pnl_amounts_income_is_credit_minus_debit():
    lines = [{"account": "4100", "debit": 0, "credit": 1000, "branch": SB, "month": "2026-10"},
             {"account": "4100", "debit": 100, "credit": 0, "branch": SB, "month": "2026-10"},
             {"account": "1100", "debit": 5, "credit": 0, "branch": SB, "month": "2026-10"},
             {"account": "4100", "debit": 0, "credit": 7, "branch": KK, "month": "2026-10"}]
    assert ac.pnl_amounts(lines, {"4100": "income"}, SB) == {"2026-10": {"4100": 900.0}}


# ------------------------------------------------------------------ run() on a faked database (read only)
ACCOUNTS = [{"code": c, "name_th": c, "kind": k, "section": "x", "sort_order": i, "active": True}
            for i, (c, k) in enumerate([("1100", "asset"), ("1130", "asset"), ("1400", "asset"), ("2100", "liability"), ("5510", "cogs"),
                                        ("5100", "cogs"), ("6130", "expense")])]


def _as_lines(entries):
    out = []
    for e in entries:
        head = {k: e[k] for k in ("id", "entry_date", "branch", "source", "source_id", "party", "description", "doc_no")}
        for i, x in enumerate(e["lines"]):
            out.append({**x, "id": f"{e['id']}-{i}", "entry": head})
    return out


def test_run_end_to_end_on_a_fake_database(monkeypatch):
    cheque, bill = _pc6906()
    history = ent("h", "2026-05-10", "midmonth", [ln("6130", 1000), ln("1100", credit=1000)])
    book = [cheque, bill, history, _pea("333333", "2026-07-25", 700000)]
    lines = _as_lines(book)
    calls = []

    def fake_select_all(table, params, page=1000):
        calls.append(table)
        if table == "acc_accounts":
            return ACCOUNTS
        if table == "acc_lines":
            return [x for x in lines if x["account"] == "2100"]
        if table == "elec_bills":
            return [{"invoice_no": "333333", "bill_period": "2026-07", "branch": SB, "total": 700000, "deposit_interest": 0}]
        if table == "inbox_files":
            return []
        raise AssertionError(table)

    def fake_parallel(table, params, page=1000, first=5):
        assert table == "acc_lines"
        lo, hi = params["entry.and"].strip("()").replace("entry_date.gte.", "").replace("entry_date.lte.", "").split(",")
        accts = params.get("account", "")[4:-1].split(",") if params.get("account") else None
        return [x for x in lines if lo <= x["entry"]["entry_date"] <= hi and (accts is None or x["account"] in accts)]

    def no_write(*a, **k):
        raise AssertionError("the checks never write")

    monkeypatch.setattr(supabase, "select_all", fake_select_all)
    monkeypatch.setattr(supabase, "select_all_parallel", fake_parallel)
    monkeypatch.setattr(supabase, "select", lambda table, params=None: [])
    for name in ("insert", "update", "delete"):
        monkeypatch.setattr(supabase, name, no_write)
    monkeypatch.setattr("app.accounting._require", lambda: None)
    monkeypatch.setattr(ac, "_shop_bills", lambda: [{"source": "purchase", "id": "pur", "state": "due", "changed": 0.0, "shop": "พีซี"}])

    out = ac.run("2026-07")
    assert out["month"] == "2026-07" and out["branch"] == ""
    keys = [(c["key"], c["branch"]) for c in out["checks"]]
    assert keys[:2] == [("petty_cash", SB), ("petty_cash", KK)] and len(keys) == 12
    by = {(c["key"], c["branch"]): c for c in out["checks"]}
    assert by[("lookalikes", SB)]["state"] == "warn" and "PC6906081" in by[("lookalikes", SB)]["rows"][0]["text"]
    assert by[("lookalikes", KK)]["state"] == "ok"
    assert by[("bills", SB)]["data"]["pea_owed"] == 700000 and by[("bills", SB)]["data"]["shop_unpaid"] == 45582
    assert by[("vs_usual", SB)]["data"]["months"] == ["2026-06", "2026-05"]      # the cheque (25 Jun) and the paper (10 May)
    for c in out["checks"]:
        assert c["state"] in ("ok", "warn", "bad", "info") and c["title"] and isinstance(c["summary"], str) and len(c["rows"]) <= 50
    one = ac.run("2026-07", KK)
    assert {c["branch"] for c in one["checks"]} == {KK} and len(one["checks"]) == 6


def test_run_refuses_bad_input(monkeypatch):
    from app.accounting import InvalidEntry
    monkeypatch.setattr("app.accounting._require", lambda: None)
    for bad in ("2026-13", "26-10", "", None):
        with pytest.raises(InvalidEntry):
            ac.run(bad)
    with pytest.raises(InvalidEntry):
        ac.run("2026-10", "หัวหิน")


def test_dmy_and_rows_are_capped():
    assert ac.dmy("2026-10-25") == "25/10/2026"
    rows = [{"amount": float(i)} for i in range(80)]
    assert len(ac.top_rows(rows)) == 50 and ac.top_rows(rows)[0]["amount"] == 79.0


# ------------------------------------------------------------------ review fixes (26 Nov 2026)
def _thai(s):
    return any("฀" <= ch <= "๿" for ch in s)


def test_numbers_are_a_flat_list_with_thai_labels_and_rows_have_the_page_shape():
    c = ac.petty_cash(_cash_month(), SB, "2026-10")
    assert isinstance(c["numbers"], list) and c["numbers"]
    for n in c["numbers"]:
        assert set(n) == {"label", "value", "kind"} and n["kind"] in ("money", "count", "pct")
        assert isinstance(n["value"], (int, float)) and not isinstance(n["value"], bool)
        assert _thai(n["label"])                                                       # Thai, never a key like mid_in
    labels = {n["label"]: n for n in c["numbers"]}
    assert labels["กลางเดือน: เช็คเติม"]["value"] == 321141 and labels["เงินเข้า: เช็ค HQ"]["kind"] == "money"
    for r in c["rows"]:
        assert {"date", "text", "amount", "source"} <= set(r)
        assert r["date"] is None or len(r["date"]) == 10
    assert {r["source"] for r in c["rows"]} >= {"เช็ค HQ", "เอกสารเบิกกลางเดือน"}
    s, e = period_bounds("2026-10")
    adv = ac.advances([ent("a", "2026-10-08", "midmonth", [ln("1500", 255000), ln("1100", credit=255000)])], SB, s, e)
    assert {"label": "เบิกไป", "value": 255000, "kind": "money"} in adv["numbers"]
    assert {"label": "ต่าง", "value": 100.0, "kind": "pct"} in adv["numbers"]
    ft = ac.file_totals_check([_file("a", "k", 1, 1, 1)], {"k": (1, 1)}, SB)
    assert ft["numbers"] == [{"label": "ชุดข้อมูล", "value": 1, "kind": "count"}, {"label": "ชุดที่ไม่ตรง", "value": 0, "kind": "count"}]
    usual = ac.usual_check("2026-10", {"2026-09": {"5100": 5.0}, "2026-10": {"5100": 5.0}}, SB, {})
    assert {"label": "เดือนที่ใช้เทียบ", "value": 1, "kind": "count"} in usual["numbers"]


def test_petty_cash_counts_papers_by_their_batch_not_the_line_date():
    # Saraburi 2026-07: the mid-month list of July holds receipts of 24-25 Jun (company month 2026-06 by date)
    rows = [ent("f1", "2026-07-15", "hq_cheques", [ln("1100", 300000), ln("1120", credit=300000)], description="คชจ.+เบิกกลางเดือน 7/2569"),
            ent("m1", "2026-06-24", "midmonth", [ln("6130", 120000), ln("1100", credit=120000)], source_id="2026-07-saraburi:p2:l1"),
            ent("m2", "2026-07-03", "midmonth", [ln("5510", 180000), ln("1100", credit=180000)], source_id="2026-07-saraburi:p3:l1"),
            ent("m3", "2026-06-25", "midmonth", [ln("5510", 50000), ln("1100", credit=50000)], source_id="docs:2026-06:สระบุรี:mid:p4:l1")]
    c = ac.petty_cash(rows, SB, "2026-07")
    assert c["data"]["mid_out"] == 300000 and c["data"]["mid_in"] == 300000 and c["state"] == "ok"
    assert ac.petty_cash(rows, SB, "2026-06")["data"]["mid_out"] == 50000                # June's own batch, not July's early lines
    assert ac.paper_month({"source_id": "docs:2026-07:สระบุรี:end:p1:l2"}) == "2026-07"
    assert ac.paper_month({"source_id": "x", "entry_date": "2026-06-26"}) == "2026-07"   # no batch: the date's company month


def test_file_totals_compare_scanned_papers_with_the_pages_after_review_fixes():
    key = "docs:2026-11:สระบุรี:mid"
    f = _file("ai", key, 4500, 4380, 4380)                     # the AI misread a line: 4,380 read against 4,500 written
    fixed = {key: {"read_total": 4500, "pages": 4, "edited": 1}}  # the owner fixed it on the review screen
    c = ac.file_totals_check([f], {key: (5, 4500)}, SB, fixed)
    assert c["state"] == "ok" and c["data"]["edited"] == 1
    assert any("แก้บนหน้าตรวจ 1 หน้า" in r["text"] for r in c["rows"]) and "เทียบกับยอดหลังแก้" in c["summary"]
    assert ac.file_totals_check([f], {key: (5, 4500)}, SB)["state"] == "bad"            # without the pages' totals: the old false red
    assert ac.file_totals_check([f], {key: (10, 9000)}, SB, fixed)["state"] == "bad"    # doubled after booking is still red
    off = {key: {"read_total": 4620, "pages": 4, "edited": 1}}                          # fixed, but still not what the paper says
    assert ac.file_totals_check([f], {key: (5, 4620)}, SB, off)["state"] == "bad"


def test_an_ai_read_pea_bill_and_the_pea_bill_are_a_lookalike():
    s, e = period_bounds("2026-10")
    pea = ent("p", "2026-10-25", "electricity", [ln("5100", 780729.25), ln("1400", 54651.03), ln("2100", credit=835380.28)],
              party="PEA มิเตอร์ 72", doc_no="318011438503", source_id="pea:318011438503")
    ai = ent("a", "2026-10-25", "electricity", [ln("5100", 835380.28), ln("2100", credit=835380.28)], party="การไฟฟ้าส่วนภูมิภาค",
             description="ค่าไฟฟ้า ต.ค. 69", doc_no="318011438503", source_id="bill:2026-10:สระบุรี:ab12cd34:1")
    pairs = ac.find_lookalikes([pea, ai], s, e, PNL)
    assert len(pairs) == 1 and pairs[0]["reason"] == "doc"
    pea2 = dict(pea, id="p2", source_id="pea:318011438999", doc_no="318011438999")
    assert ac.find_lookalikes([pea, pea2], s, e, PNL) == []                             # two PEA bills: one batch ('pea')
    # the same cheque payment every month is not a double
    pnl = {**PNL, "7200": "expense"}
    gps9 = ent("g9", "2026-09-20", "hq_cheques", [ln("7200", 3422), ln("1130", credit=3422)], party="GPS ติดตามรถ",
               source_id="cheque-2026-09-สระบุรี:r3")
    gps10 = ent("g10", "2026-10-20", "hq_cheques", [ln("7200", 3422), ln("1130", credit=3422)], party="GPS ติดตามรถ",
                source_id="cheque-2026-10-สระบุรี:r3")
    assert ac.find_lookalikes([gps9, gps10], s, e, pnl) == []
    assert ac.batch_of({"source_id": "2026-07-saraburi:p2:l1"}) == "2026-07-saraburi"
    assert ac.batch_of({"source_id": "fuel:pt:2026-10:842919"}) == "fuel:pt:2026-10"
    assert ac.batch_of({"source_id": "pb1", "source": "purchase"}) == "purchase"


def test_the_drop_box_papers_against_the_script_papers_of_the_same_half():
    s, e = period_bounds("2026-11")
    script = ent("s", "2026-11-02", "midmonth", [ln("5510", 1500), ln("1100", credit=1500)], party="ของใช้โรงงาน", description="ถุงมือ",
                 source_id="2026-11-saraburi:p2:l1")
    box = ent("b", "2026-11-02", "midmonth", [ln("5510", 1500), ln("1100", credit=1500)], party="ของใช้โรงงาน", description="ถุงมือ",
              source_id="docs:2026-11:สระบุรี:mid:p2:l1")
    assert len(ac.find_lookalikes([script, box], s, e, PNL)) == 1
    same = dict(box, id="b2", source_id="docs:2026-11:สระบุรี:mid:p3:l4")
    assert ac.find_lookalikes([box, same], s, e, PNL) == []                             # one file's own lines


def test_bills_ai_bills_paid_by_the_cheque_and_electricity_without_a_pea_bill():
    s, e = period_bounds("2026-10")
    sid = "bill:2026-10:สระบุรี:ab12cd34:1"
    bill = ent("w", "2026-10-25", "water_irrigation", [ln("5200", 5320.5), ln("2100", credit=5320.5)], party="ชลประทาน", source_id=sid,
               description="ค่าน้ำชลประทาน ต.ค. 69")
    pays = ent("c", "2026-11-20", "hq_cheques", [ln("2100", 5320.5), ln("1130", credit=5320.5)],
               description=f"ค่าน้ำชลประทาน 10/69 · จ่ายบิล W-1 (อ้างอิง {sid}) · เช็ค 1")
    c = ac.bills_check([bill, pays], SB, s, e, elec_bills=[], later_run=True)
    assert c["state"] == "ok" and c["data"]["ai_owed"] == 5320.5 and c["data"]["ai_unpaid"] == 0
    assert c["data"]["no_payer"] == {}
    c = ac.bills_check([bill], SB, s, e, elec_bills=[], later_run=True)
    assert c["state"] == "warn" and "ยังไม่มีเช็คจ่าย" in c["rows"][0]["text"]
    paid_now = dict(pays, entry_date="2026-10-20")                                      # a payment this month naming it: not "unknown"
    assert ac.bills_check([bill, paid_now], SB, s, e, elec_bills=[], later_run=False)["data"]["unknown_payments"] == 0
    ai_pea = ent("x", "2026-10-25", "electricity", [ln("5100", 9000), ln("2100", credit=9000)], party="การไฟฟ้า",
                 source_id="bill:2026-10:สระบุรี:ffff0000:1")
    c = ac.bills_check([ai_pea], SB, s, e, elec_bills=[], later_run=False)
    assert c["state"] == "warn" and c["data"]["elec_other"] == 9000 and "ไม่ได้มาจากใบแจ้ง PEA" in c["rows"][0]["text"]


def test_docs_now_sums_only_the_money_pages(monkeypatch):
    rows = [{"page": 1, "kind": "cover", "read_total": 4500, "edited_at": None},
            {"page": 2, "kind": "expense", "read_total": 1500, "edited_at": "2026-11-20"},
            {"page": 3, "kind": "fuel", "read_total": 3000, "edited_at": None},
            {"page": 4, "kind": "memo", "read_total": None, "edited_at": None}]
    monkeypatch.setattr(supabase, "select_all", lambda table, params, page=1000: rows if params["batch"] == "eq.docs:k" else [])
    assert ac._docs_now(["docs:k", "docs:none"]) == {"docs:k": {"read_total": 4500.0, "pages": 4, "edited": 1}}
