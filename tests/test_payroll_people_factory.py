import os
import re

import pytest

from app import payroll_people_factory as pf

ROOT = "E:/Saraburi/ClaudeCode/CP Accounting/เงินเดือน/รายละเอียดเงินเดือน 2569"
SEP_XLSX = f"{ROOT}/09/เงินเดือน ช่าง+ออฟฟิศ+ฝ่ายผลิตสระบุรี กันยายน 2569 .xlsx"
OCT_PDF = f"{ROOT}/10/สรุปเงินเดือน ช่าง+ออฟฟิศ+ฝ่ายผลิตสระบุรี ตุลาคม 2569 .pdf"
JAN_PDF = f"{ROOT}/01/ใบสรุป-ช่าง+ออฟฟิศ+ฝ่ายผลิตสระบุรี มกราคม 2569 .pdf"


def raw(**kw):
    base = {"code": "SB-FR001", "name": "นายทดสอบ", "dept": "ผลิต", "days": 30, "wage": 11700, "money2": 0, "money2_formula": None,
            "bag_cell": 0, "bag_formula": None, "gross": 11700, "deduct_total": 3480, "net": 8220,
            "deductions": {"advance": 3000, "sso": 480}, "notes": ""}
    base.update(kw)
    return base


def check_invariants(p):
    assert abs(sum(p["parts"].values()) + p["refund"] - p["gross"]) <= 1
    assert abs(p["gross"] - p["deduct_total"] - p["net"]) <= 1
    assert abs(sum(p["deductions"].values()) - p["deduct_total"]) <= 1


# ------------------------------------------------------------------------------------------------ helpers
def test_fix_thai_rejoins_split_vowels():
    assert pf.fix_thai(["นางส", "ำเนา", "ทิพเนตร"]) == "นางสำเนา ทิพเนตร"
    assert pf.fix_thai(["วันท", "ำงาน"]) == "วันทำงาน"
    assert pf.fix_thai(["นางส", "าเนา"]) == "นางสำเนา"          # the Jan-style font prints ำ as า
    assert pf.fix_thai(["เด็กรถหม", "่ำ"]) == "เด็กรถหม่ำ"
    assert pf.fix_thai(["นาย", "(Mr.", "YE)"]) == "นาย (Mr. YE)"


def test_numbers():
    assert pf.to_num("12,702") == 12702 and pf.to_num("-") == 0 and pf.to_num(None) == 0 and pf.to_num("(1,000)") == -1000


def test_layout_from_headers_with_and_without_card_column():
    oct_ = {1: "scan no.", 2: "รายชื่อ", 3: "แผนก", 4: "วัน", 5: "วันท ำงาน เงิน", 7: "OT. เงิน", 8: "ซอง%", 9: "รวมรับ", 10: "รวมจ่าย",
            11: "คงเหลือ", 12: "สะสม", 13: "เบิกล่วงหน้า", 14: "เงินยืม", 15: "ส่วนกลาง", 16: "ทำบัตร", 17: "เงินขาด/ความผิด",
            18: "อุปกรณ์/อื่นๆ", 19: "ปกส.", 20: "ค้างเงิน", 23: "สะสมยกไป"}
    lay = pf.layout(oct_)
    assert (lay["code"], lay["days"], lay["money2"], lay["bag"], lay["gross"], lay["net"]) == (1, 4, 7, 8, 9, 11)
    assert lay["deductions"] == {"savings": 12, "advance": 13, "loan": 14, "common": 15, "card": 16, "shortage": 17, "other": 18,
                                 "sso": 19}
    jan = {k: v for k, v in oct_.items() if k < 16}
    jan.update({16: "เงินขาด/ความผิด", 17: "อุปกรณ์/อื่นๆ", 18: "ปกส.", 19: "ค้างเงิน"})
    assert pf.layout(jan)["deductions"] == {"savings": 12, "advance": 13, "loan": 14, "common": 15, "shortage": 16, "other": 17,
                                            "sso": 18}
    with pytest.raises(ValueError):
        pf.layout({1: "รายชื่อ"})


def test_deduction_labels():
    assert pf.ded_key("รายจ่าย สะสม") == "savings" and pf.ded_key("สะสมยกไป") is None
    assert pf.ded_key("ความผิด/อื่นๆ") == "other" and pf.ded_key("เงินขาด/กระสอบไม่ครบ") == "shortage"
    assert pf.ded_key("คืนเงินกู้") == "loan" and pf.ded_key("passport") == "card"


# ------------------------------------------------------------------------------------------------ rules
def test_plain_wage_row():
    p = pf.build_person(raw(), "2026-09", "xlsx")
    assert p["parts"] == {"wage": 11700, "commission": 0, "bag": 0, "bonus": 0, "ot": 0, "other": 0}
    assert p["deductions"]["advance"] == 3000 and p["deductions"]["sso"] == 480 and p["flags"] == []
    assert p["group"] == "factory" and p["source"] == "xlsx"
    check_invariants(p)


def test_xlsx_formula_decides_ot_or_commission():
    ot = pf.build_person(raw(money2=400, money2_formula="=H5*40", gross=12100, net=8620), "2026-09", "xlsx")
    assert ot["parts"]["ot"] == 400 and ot["parts"]["commission"] == 0 and ot["flags"] == []
    com = pf.build_person(raw(dept="หน้าโรง", money2=450, money2_formula="=H10*1.5%", gross=12150, net=8670), "2026-09", "xlsx")
    assert com["parts"]["commission"] == 450
    com2 = pf.build_person(raw(money2=300, money2_formula="=H36*2/100", gross=12000, net=8520), "2026-09", "xlsx")
    assert com2["parts"]["commission"] == 300
    ot35 = pf.build_person(raw(money2=350, money2_formula="=H28*35", gross=12050, net=8570), "2026-09", "xlsx")
    assert ot35["parts"]["ot"] == 350 and ot35["flags"]
    for p in (ot, com, com2, ot35):
        check_invariants(p)


def test_pdf_money_column_by_department():
    prod = pf.build_person(raw(money2=120, gross=11820, net=8340), "2026-10", "pdf")
    assert prod["parts"]["ot"] == 120 and prod["flags"]
    front = pf.build_person(raw(dept="หน้าโรง", money2=500, gross=12200, net=8720), "2026-10", "pdf")
    pack = pf.build_person(raw(dept="แพ็ค", money2=500, gross=12200, net=8720), "2026-10", "pdf")
    assert front["parts"]["commission"] == 500 and pack["parts"]["commission"] == 500
    other = pf.build_person(raw(dept="ช่าง", money2=500, gross=12200, net=8720), "2026-10", "pdf")
    assert other["parts"]["ot"] == 500 and "ไม่รู้จัก" in other["flags"][0]


def test_bag_column_bonus_split():
    f = pf.build_person(raw(bag_cell=2491, bag_formula="=497*3+1000", gross=14191, net=10711), "2026-09", "xlsx")
    assert (f["parts"]["bag"], f["parts"]["bonus"]) == (1491, 1000) and f["flags"] == []
    only = pf.build_person(raw(bag_cell=1000, gross=12700, net=9220), "2026-09", "xlsx")
    assert (only["parts"]["bag"], only["parts"]["bonus"]) == (0, 1000)
    no_bonus = pf.build_person(raw(bag_cell=1491, bag_formula="=497*3", gross=13191, net=9711), "2026-09", "xlsx")
    assert (no_bonus["parts"]["bag"], no_bonus["parts"]["bonus"]) == (1491, 0)
    pdf = pf.build_person(raw(bag_cell=2491, gross=14191, net=10711), "2026-10", "pdf")
    assert (pdf["parts"]["bag"], pdf["parts"]["bonus"]) == (1491, 1000) and any("เบี้ยขยัน" in x for x in pdf["flags"])
    marked = pf.build_person(raw(bag_cell=2491, gross=14191, net=10711, notes="ไม่ให้เบี้ยขยัน"), "2026-10", "pdf")
    assert (marked["parts"]["bag"], marked["parts"]["bonus"]) == (2491, 0)
    for p in (f, only, no_bonus, pdf, marked):
        check_invariants(p)


def test_savings_refund_note_is_not_bag_pay():
    p = pf.build_person(raw(name="นางอี๊ด -คืนสะสม", wage=0, days=0, bag_cell=10000, gross=10000, deduct_total=0, net=10000,
                            deductions={}), "2026-10", "pdf")
    assert p["parts"]["other"] == 10000 and p["parts"]["bag"] == 0 and p["parts"]["bonus"] == 0 and p["refund"] == 0
    assert "OVERRIDES" in p["flags"][0]
    check_invariants(p)


def test_override_moves_money_to_refund(monkeypatch):
    p = pf.build_person(raw(code="sb-pd025", bag_cell=21500, gross=33200, net=29720), "2026-09", "xlsx")
    assert p["code"] == "SB-PD025" and p["refund"] == 20500 and p["parts"]["bag"] == 1000 - p["parts"]["bonus"]
    check_invariants(p)
    monkeypatch.setitem(pf.OVERRIDES, ("2026-10", "SB-FR016"), {"refund": 10000})
    q = pf.build_person(raw(code="SB-FR016", name="นางอี๊ด -คืนสะสม", wage=0, days=0, bag_cell=10000, gross=10000, deduct_total=0,
                            net=10000, deductions={}), "2026-10", "pdf")
    assert q["refund"] == 10000 and sum(q["parts"].values()) == 0
    check_invariants(q)


def test_gross_not_adding_up_lands_in_other_with_flag():
    p = pf.build_person(raw(gross=12000, net=8520), "2026-09", "xlsx")
    assert p["parts"]["other"] == 300 and p["flags"]
    check_invariants(p)


# ------------------------------------------------------------------------------------------------ real files (skipped elsewhere)
@pytest.mark.skipif(not os.path.exists(SEP_XLSX), reason="payroll files not on this machine")
def test_september_xlsx_matches_total_row():
    r = pf.parse_file_full(SEP_XLSX, "2026-09")
    people = r["people"]
    assert all(p["code"].startswith("SB-FR") and p["source"] == "xlsx" for p in people)
    for p in people:
        check_invariants(p)
    for k in ("gross", "deduct_total", "net"):
        assert abs(sum(p[k] for p in people) + sum(e[k] for e in r["extras"]) - r["total"][k]) <= 1
    assert abs(r["total"]["gross"] - 870473) <= 1 and abs(r["total"]["deduct_total"] - 205271) <= 1
    fr001 = next(p for p in people if p["code"] == "SB-FR001")
    assert fr001["days"] == 19 and fr001["parts"]["wage"] == 15200 and fr001["deductions"]["loan"] == 2500
    assert len(r["extras"]) == 2                                      # the two petty-cash pay-back rows
    assert pf.parse_file(SEP_XLSX, "2026-09") == people


@pytest.mark.skipif(not os.path.exists(OCT_PDF), reason="payroll files not on this machine")
def test_october_pdf_matches_total_row():
    r = pf.parse_file_full(OCT_PDF, "2026-10")
    people = r["people"]
    for p in people:
        check_invariants(p)
        assert p["source"] == "pdf"
    for k in ("gross", "deduct_total", "net"):
        assert abs(sum(p[k] for p in people) + sum(e[k] for e in r["extras"]) - r["total"][k]) <= 1
    assert (r["total"]["gross"], r["total"]["deduct_total"], r["total"]["net"]) == (875017, 193118, 681899)
    fr008 = next(p for p in people if p["code"] == "SB-FR008")
    assert fr008["name"] == "นางสำเนา ทิพเนตร" and fr008["dept"] == "หัวหน้างาน" and fr008["days"] == 26
    fr002 = next(p for p in people if p["code"] == "SB-FR002")
    assert fr002["deductions"]["advance"] == 3000 and fr002["deductions"]["loan"] == 2000 and fr002["deductions"]["sso"] == 625
    fr016 = next(p for p in people if p["code"] == "SB-FR016")
    assert fr016["parts"]["other"] == 10000 and fr016["flags"]


@pytest.mark.skipif(not os.path.exists(JAN_PDF), reason="payroll files not on this machine")
def test_january_pdf_without_card_column():
    r = pf.parse_file_full(JAN_PDF, "2026-01")
    for p in r["people"]:
        check_invariants(p)
        assert p["deductions"]["card"] == 0
    assert abs(sum(p["gross"] for p in r["people"]) + sum(e["gross"] for e in r["extras"]) - 812081) <= 1
    fr003 = next(p for p in r["people"] if p["code"] == "SB-FR003")      # numbers printed on a second line inside the row
    assert fr003["deductions"]["advance"] == 3000 and fr003["deductions"]["loan"] == 2500 and fr003["net"] == 5976


# ------------------------------------------------------------------------------------------------ details (every cell on the row)
def test_layout_finds_balance_columns_and_stops_there():
    head = {1: "scan no.", 2: "รายชื่อ", 3: "แผนก", 4: "วัน", 5: "เงิน", 7: "เงิน", 8: "ซอง%", 9: "รวมรับ", 10: "รวมจ่าย", 11: "คงเหลือ",
            12: "รายจ่าย สะสม", 13: "เบิกล่วงหน้า", 14: "ปกส.", 15: "ค้างเงิน", 16: "จ่ายค้าง", 17: "ค้างย/ป", 18: "สะสมยกไป", 19: "แผนก",
            20: "เลขที่บัญชี", 21: "ค้างเงิน"}
    lay = pf.layout(head)
    assert lay["balances"] == {"loan_before": 15, "loan_paid": 16, "loan_after": 17, "savings_balance": 18}
    assert lay["labels"][12] == "สะสม" and lay["labels"][8] == "ซอง%"
    # a PDF header with the vertical รายรับ / รายจ่าย pieces stuck in front
    assert pf.short_label("ายจ่า เงินขาด/ความผิด") == "เงินขาด/ความผิด" and pf.short_label("ยรับ ซอง%") == "ซอง%"
    assert pf.layout({k: v for k, v in head.items() if k <= 14})["balances"] == {}


def test_helpers_rate_notes_name_suffix():
    assert pf.wage_rate("=F5*390") == 390 and pf.wage_rate("=390*F12") == 390 and pf.wage_rate("=F5*357.5") == 357.5
    assert pf.wage_rate("=28*390+2*344") is None and pf.wage_rate(11700) is None and pf.wage_rate(None) is None
    assert pf.clean_note("Admin:\nปรับค่าแรง ต.ค.64ขึ้นอีก46.-") == "ปรับค่าแรง ต.ค.64ขึ้นอีก46.-"
    assert pf.clean_note("PC:\nเบิกเพิ่ม ปีใหม่ 10,000+ ซ่อมรถ10000\n") == "เบิกเพิ่ม ปีใหม่ 10,000+ ซ่อมรถ10000"
    assert pf.clean_note("เบิกติดโควิด") == "เบิกติดโควิด" and pf.clean_note("Admin:\n") == ""
    assert pf.name_note("นายเม ( Mr. MIN HTKE )-หัก ปกส.นางนวย") == "หัก ปกส.นางนวย"
    assert pf.name_note("นางอี๊ด -คืนสะสม") == "คืนสะสม"
    assert pf.name_note("นายเป้(Mr.PYAE PHYO AUNG)03/69 ย้ายเป็นเด็กรถ") == "03/69 ย้ายเป็นเด็กรถ"
    assert pf.name_note("นางแตงโม-( Mrs. HAY TWAL MOE )") is None and pf.name_note("นางจารุณี (แพ็ค) คตวงศ์ฆ้อง") is None
    assert pf.name_note("นายจรูญ  มินฉิมพลี") is None


def test_details_lines_balances_and_notes():
    p = pf.build_person(raw(name="นายเม ( Mr. MIN HTKE )-หัก ปกส.นางนวย", wage_formula="=F5*390", money2=400, money2_formula="=H5*40",
                            bag_cell=2491, bag_formula="=497*3+1000", gross=14591, deduct_total=3960, net=10631,
                            deductions={"advance": 3000, "other": 480, "sso": 480},
                            balances={"loan_before": 7500, "loan_paid": 1500, "loan_after": 6000, "savings_balance": 10700},
                            labels={"bag": "ซอง%", "other": "อุปกรณ์/อื่นๆ"},
                            comments=[("ปกส.", "Admin:\nย้อนหลังไม่ได้หลายเดือน"), ("ค้างเงิน", "Admin:\n"), ("เงิน", "PC: เพิ่ม50.- มี.ค.63")]),
                        "2026-09", "xlsx")
    check_invariants(p)
    d = p["details"]
    assert d["position"] == "ผลิต" and d["rate"] == 390 and d["sales"] is None and d["commission_pct"] is None
    assert (d["loan_before"], d["loan_paid"], d["loan_after"], d["savings_balance"]) == (7500, 1500, 6000, 10700)
    assert [(x["side"], x["label"], x["key"], x["amount"]) for x in d["lines"]] == [
        ("income", "เงิน", "wage", 11700), ("income", "เงิน", "ot", 400), ("income", "ซอง%", "bag", 2491),
        ("deduction", "เบิกล่วงหน้า", "advance", 3000), ("deduction", "อุปกรณ์/อื่นๆ", "other", 480), ("deduction", "ปกส.", "sso", 480)]
    assert d["notes"] == [{"label": "รายชื่อ", "text": "หัก ปกส.นางนวย"}, {"label": "ปกส.", "text": "ย้อนหลังไม่ได้หลายเดือน"},
                          {"label": "เงิน", "text": "เพิ่ม50.- มี.ค.63"}]
    assert p["name"] == "นายเม ( Mr. MIN HTKE )-หัก ปกส.นางนวย"          # the name itself is kept as before
    assert p["parts"] == {"wage": 11700, "commission": 0, "bag": 1491, "bonus": 1000, "ot": 400, "other": 0}


def test_details_commission_sales_refund_and_missing_columns():
    com = pf.build_person(raw(dept="หน้าโรง", money2=450, money2_formula="=H10*1.5%", qty=30000, gross=12150, net=8670), "2026-09", "xlsx")
    d = com["details"]
    assert d["sales"] == 30000 and d["commission_pct"] == 1.5 and d["lines"][1]["key"] == "commission"
    assert d["rate"] is None and d["loan_before"] is None and d["savings_balance"] is None and d["notes"] == []
    ref = pf.build_person(raw(name="นางอี๊ด -คืนสะสม", wage=0, days=0, bag_cell=10000, gross=10000, deduct_total=0, net=10000,
                              deductions={}), "2026-10", "pdf")
    assert ref["details"]["lines"] == [{"side": "income", "label": "ซอง%", "key": "other", "amount": 10000}]
    assert ref["details"]["notes"] == [{"label": "รายชื่อ", "text": "คืนสะสม"}]
    gap = pf.build_person(raw(gross=12000, net=8520), "2026-09", "xlsx")      # รวมรับ printed 300 more than its cells
    assert sum(x["amount"] for x in gap["details"]["lines"] if x["side"] == "income") == 12000


BANK_RE = re.compile(r"\d{3}-?\d-?\d{5}-?\d")
ID_RE = re.compile(r"\d{13}")


def _all_months():
    from app import payroll_people as pp
    return [(m, pp.BASE + g["factory"]) for m, g in sorted(pp.FILES.items()) if g.get("factory")]


@pytest.mark.parametrize("month,path", _all_months())
def test_details_add_up_on_every_real_month(month, path):
    if not os.path.exists(path):
        pytest.skip("payroll file not on this machine")
    people = pf.parse_file(path, month)
    loan_breaks = []
    for p in people:
        d = p["details"]
        assert set(d) == {"position", "rate", "sales", "commission_pct", "loan_before", "loan_paid", "loan_after", "savings_balance",
                          "lines", "notes"}
        assert abs(sum(x["amount"] for x in d["lines"] if x["side"] == "income") - p["gross"]) <= 1, p["code"]
        assert abs(sum(x["amount"] for x in d["lines"] if x["side"] == "deduction") - p["deduct_total"]) <= 1, p["code"]
        for x in d["lines"]:
            assert x["amount"] and x["label"] and (x["key"] in pf.PART_KEYS if x["side"] == "income" else x["key"] in pf.DED_KEYS)
        a, b, c = d["loan_before"], d["loan_paid"], d["loan_after"]
        if None not in (a, b, c) and abs(a - b - c) > 1:
            loan_breaks.append((p["code"], a, b, c))
        assert None not in (a, b, c, d["savings_balance"])         # every month, xlsx and PDF, prints the four balance columns
        blob = repr(d)
        assert not BANK_RE.search(blob) and not ID_RE.search(blob), p["code"]
        if p["source"] == "pdf":
            assert all(n["label"] == "รายชื่อ" for n in d["notes"])      # no comments in a PDF, only HQ's note after a name
    # HQ's own arithmetic held on every row of 2569 when this was written; a break is listed here, it is HQ's sheet, not the reader
    assert loan_breaks == [], f"ค้างเงิน - จ่ายค้าง != ค้างย/ป on: {loan_breaks}"


@pytest.mark.skipif(not os.path.exists(SEP_XLSX), reason="payroll files not on this machine")
def test_september_details_spot_checks():
    by = {p["code"]: p for p in pf.parse_file(SEP_XLSX, "2026-09")}
    d = by["SB-FR001"]["details"]
    assert d["rate"] == 800 and (d["loan_before"], d["loan_paid"], d["loan_after"]) == (2500, 2500, 0) and d["savings_balance"] == 21300
    assert d["notes"][0]["label"] == "ค้างเงิน" and not d["notes"][0]["text"].startswith("Admin")
    nuay = by["SB-FR066"]["details"]
    assert {"label": "รายชื่อ", "text": "หัก ปกส.นางนวย"} in nuay["notes"]
    assert [(x["key"], x["amount"]) for x in nuay["lines"] if x["side"] == "deduction"][-2:] == [("other", 480), ("sso", 480)]
    assert {"label": "ปกส.", "text": "ปกส.8/64 ไม่ได้หัก"} in by["SB-FR008"]["details"]["notes"]
