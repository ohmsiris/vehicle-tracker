import os
import re

import pytest

from app import payroll_people_kk as kk

BASE = "E:/Saraburi/ClaudeCode/CP Accounting/เงินเดือน/"
SEP_XLSX = BASE + "รายละเอียดเงินเดือน 2569/09/เงินเดือนแก่งคอย กันยายน 2569.xlsx"
OCT_PDF = BASE + "รายละเอียดเงินเดือน 2569/10/สรุป-เงินเดือนแก่งคอย ตุลาคม 2569.pdf"
AUG_PDF = BASE + "รายละเอียดเงินเดือน 2569/08-แก้ไข-KK/สรุป-เงินเดือนแก่งคอย สิงหาคม 2569.pdf"


def _check_invariants(r):
    assert abs(sum(r["parts"].values()) + r["refund"] - r["gross"]) <= 1
    assert abs(r["gross"] - r["deduct_total"] - r["net"]) <= 1
    assert abs(sum(r["deductions"].values()) - r["deduct_total"]) <= 1


# ---------------------------------------------------------------- the H / I splits

def test_h_typed_amount_is_commission_flagged_off_the_trucks():
    assert kk.split_h(11200, None, None, "พขร.") == ({"commission": 11200.0}, [])
    parts, flags = kk.split_h(9400, None, None, "ผลิต")
    assert parts == {"commission": 9400.0} and flags and "ผลิต" in flags[0]


def test_h_formulas():
    assert kk.split_h(3457.8, "=G8*2/100", 172890, "พขร.") == ({"commission": 3457.8}, [])
    assert kk.split_h(200, "=G13*40", 5, "ผลิต") == ({"ot": 200.0}, [])
    assert kk.split_h(350, "=G26*35", 10, "ช่าง") == ({"ot": 350.0}, [])
    parts, flags = kk.split_h(3546.3, "=G8*2/100+280", 163315, "พขร.")
    assert parts == {"commission": pytest.approx(3266.3), "other": 280.0} and flags


def test_h_pdf_by_arithmetic():
    assert kk.split_h_pdf("1,040", "26", "ผลิต") == ({"ot": 1040.0}, [])
    assert kk.split_h_pdf("3,143", "157,130", "พขร.") == ({"commission": 3143.0}, [])
    assert kk.split_h_pdf("9,500", None, "พขร.") == ({"commission": 9500.0}, [])
    assert kk.split_h_pdf("-", "-", "ผลิต") == ({}, [])


def test_i_bonus_and_the_rest():
    assert kk.split_i(1000, None, "พขร.") == ({"bonus": 1000.0}, [])
    parts, flags = kk.split_i(1070, "=1000+70", "พขร.")
    assert parts == {"bonus": 1000.0, "other": 70.0} and flags
    assert kk.split_i(280, "=7*40", "เด็กรถ") == ({"ot": 280.0}, [])
    parts, flags = kk.split_i(1140, None, "พขร.")
    assert parts == {"bonus": 1000.0, "other": 140.0} and flags
    parts, flags = kk.split_i(10000, None, "ผลิต")             # savings paid back? stays pay until the owner says so
    assert parts == {"other": 10000.0} and flags


# ---------------------------------------------------------------- one row

def _row(**kw):
    a = dict(month="2026-09", source="xlsx", code="sb-kk001", name="x", dept="พขร.", days=31, wage=13640,
             h_parts={"commission": 11200.0}, i_parts={"bonus": 1000.0}, gross=25840,
             deds={"advance": 3000, "sso": 475}, deduct_total=3475, net=22365)
    a.update(kw)
    return kk.build_row(**a)


def test_build_row_contract():
    r = _row()
    assert r["code"] == "SB-KK001" and r["group"] == "kk" and r["refund"] == 0 and r["flags"] == []
    assert r["parts"] == {"wage": 13640, "commission": 11200, "bag": 0, "bonus": 1000, "ot": 0, "other": 0}
    assert set(r["deductions"]) == set(kk.DEDUCTIONS)
    _check_invariants(r)


def test_build_row_flags_what_does_not_add_up():
    r = _row(gross=26840, net=23365)                     # 1,000 more in รวมรับ than the pay columns
    assert r["parts"]["other"] == 1000 and r["flags"]
    _check_invariants(r)
    assert _row(deduct_total=3000, net=22840)["flags"]   # deductions do not add to รวมจ่าย


def test_override_moves_money_to_refund(monkeypatch):
    monkeypatch.setitem(kk.OVERRIDES, ("2026-09", "SB-KK001"), {"refund": 10000})
    r = _row(i_parts={"bonus": 1000.0, "other": 10000.0}, gross=35840, net=32365)
    assert r["refund"] == 10000 and r["parts"]["other"] == 0 and r["parts"]["bonus"] == 1000
    _check_invariants(r)


def test_thai_rejoin_and_remarks():
    assert kk._thai("นายจ ำเริญ") == "นายจำเริญ"
    assert kk._thai("นายจ าเริญ  เพ็ชรมาลี") == "นายจำเริญ เพ็ชรมาลี"
    assert kk._thai("ค่าน ้ำมัน") == "ค่าน้ำมัน"
    assert kk._clean_name("นายมิน ตุน อ๋อง-ออกคืนสะสม01/12/69") == ("นายมิน ตุน อ๋อง", ["หมายเหตุในชื่อ: ออกคืนสะสม01/12/69"])


# ---------------------------------------------------------------- a small workbook in the sheet's layout

def test_xlsx_layout_and_row_filter(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "สรุป"
    ws["A2"], ws["B2"], ws["C2"], ws["D2"], ws["E2"] = "ที่", "Scan", "รายชื่อ", "ยอดรับเงินเดือนเดือน เม.ย.58", "รายรับ"
    ws["L2"], ws["X2"] = "คงเหลือ", "สะสมยกไป"
    for col, t in zip("JKMNOPQRST", ["รวมรับ", "รวมจ่าย", "สะสม", "เบิกล่วงหน้า", "คืนเงินกู้", "ส่วนกลาง", "passport", "ความผิด",
                                    "อุปกรณ์/อื่นๆ", "ปกส."]):
        ws[f"{col}3"] = t
    ws["E4"], ws["F4"], ws["H4"] = "วัน", "เงิน", "เงิน"
    rows = [
        [1, "SB-KK001", "นายหนึ่ง", "พขร.", 31, 13640, None, 11200, 1000, 25840, 3475, 22365, None, 3000, None, None, None, None, None, 475],
        [None, "SB-KK018", "นายว่าง-ออก", "ผลิต", None, 0, None, 0, None, 0, 0, 0],      # empty row: skipped
        [2, "SB-KK013", "นายสอง", "ผลิต", 30, 9750, 69, 2760, None, 12510, 3000, 9510, None, 3000],
        [47, None, "ค่าใช้จ่าย 11-25/9/69", None, None, None, None, None, None, 55683, 0, 55683],
        ["รวม", None, None, None, None, None, None, None, None, 93033, 6475, 87558],
        [99, "SB-KK099", "after total", "ผลิต", 1, 1, None, None, None, 1, 0, 1],
    ]
    for i, r in enumerate(rows, start=5):
        for j, v in enumerate(r, start=1):
            ws.cell(i, j, v)
    p = tmp_path / "kk.xlsx"
    wb.save(p)
    out = kk.parse_file(str(p), "2026-09")
    assert [r["code"] for r in out] == ["SB-KK001", "SB-KK013"]
    a, b = out
    assert a["parts"]["commission"] == 11200 and a["parts"]["bonus"] == 1000 and a["deductions"]["sso"] == 475
    assert b["parts"]["commission"] == 2760 and b["flags"]          # typed on a ผลิต row: flagged guess
    for r in out:
        _check_invariants(r)
    assert kk.extra_rows(str(p)) == [{"label": "ค่าใช้จ่าย 11-25/9/69", "gross": 55683.0, "deduct_total": 0.0, "net": 55683.0}]


# ---------------------------------------------------------------- the real files (skipped where they are not on disk)

@pytest.mark.skipif(not os.path.exists(SEP_XLSX), reason="payroll files not on this machine")
def test_real_september_workbook_matches_hq_total():
    rows = kk.parse_file(SEP_XLSX, "2026-09")
    extra = kk.extra_rows(SEP_XLSX)
    assert all(r["code"].startswith("SB-KK") and r["source"] == "xlsx" for r in rows)
    for r in rows:
        _check_invariants(r)
    assert sum(r["gross"] for r in rows) + sum(e["gross"] for e in extra) == pytest.approx(876431, abs=1)
    assert sum(r["deduct_total"] for r in rows) == pytest.approx(153705, abs=1)
    assert sum(r["net"] for r in rows) + sum(e["net"] for e in extra) == pytest.approx(722726, abs=1)
    kk004 = next(r for r in rows if r["code"] == "SB-KK004")
    assert kk004["parts"]["commission"] == pytest.approx(3457.8) and kk004["parts"]["bonus"] == 0


@pytest.mark.skipif(not os.path.exists(OCT_PDF), reason="payroll files not on this machine")
def test_real_october_pdf_matches_hq_total():
    rows = kk.parse_file(OCT_PDF, "2026-10")
    extra = kk.extra_rows(OCT_PDF)
    for r in rows:
        _check_invariants(r)
        assert r["source"] == "pdf"
    assert sum(r["gross"] for r in rows) + sum(e["gross"] for e in extra) == pytest.approx(809483, abs=1)
    assert sum(r["deduct_total"] for r in rows) == pytest.approx(150505, abs=1)
    assert sum(r["net"] for r in rows) + sum(e["net"] for e in extra) == pytest.approx(658978, abs=1)
    by = {r["code"]: r for r in rows}
    assert by["SB-KK006"]["name"].startswith("นายจำเริญ")
    assert by["SB-KK012"]["parts"]["ot"] == 360 and by["SB-KK004"]["parts"]["commission"] == 3143


@pytest.mark.skipif(not os.path.exists(AUG_PDF), reason="payroll files not on this machine")
def test_real_pdf_cells_glued_together_are_cut_at_the_column_lines():
    by = {r["code"]: r for r in kk.parse_file(AUG_PDF, "2026-08")}
    assert by["SB-KK003"]["dept"] == "พขร." and not by["SB-KK003"]["name"].endswith("พขร.")
    assert by["SB-KK034"]["name"].startswith("นายชำนาญ")


# ---------------------------------------------------------------- details (every cell of the row)

def test_note_text_and_rate():
    assert kk.note_text("Admin:\nส.ค.69 คุณโอมอนุมัติ  ปรับจาก325\n เป็น 350") == "ส.ค.69 คุณโอมอนุมัติ ปรับจาก325 เป็น 350"
    assert kk.note_text("PC: ok") == "ok"
    assert kk.note_text("13:30 โอนแล้ว") == "13:30 โอนแล้ว"
    assert kk.rate_from("=E5*440") == 440 and kk.rate_from("=368*E5") == 368 and kk.rate_from("=E5*357.5") == 357.5
    assert kk.rate_from(None) is None and kk.rate_from(12000) is None and kk.rate_from("=E5*440+100") is None


def test_pct_rate_and_details_shape():
    assert kk.pct_rate("3,143", "157,130") == 2 and kk.pct_rate("1,040", "26") is None and kk.pct_rate("9,500", None) is None
    d = kk.make_details(" พขร. ", 400.0, 172890, 3457.8, {"loan_before": 8750, "loan_paid": 500, "loan_after": 8250,
                                                          "savings_balance": 20000})
    assert d == {"position": "พขร.", "rate": 400.0, "sales": 172890.0, "commission_pct": 2.0, "loan_before": 8750.0,
                 "loan_paid": 500.0, "loan_after": 8250.0, "savings_balance": 20000.0, "lines": [], "notes": []}
    empty = kk.make_details()
    assert empty["position"] is None and empty["sales"] is None and empty["commission_pct"] is None and empty["loan_after"] is None


def test_lines_one_per_cell_in_sheet_order():
    inc = kk.income_lines(13640, 3546.3, {"commission": 3266.3, "other": 280.0}, "1,000", {"bonus": 1000.0})
    assert inc == [{"side": "income", "label": "วันทำงาน/เงิน", "key": "wage", "amount": 13640.0},
                   {"side": "income", "label": "OT./เงิน", "key": "commission", "amount": 3546.3},
                   {"side": "income", "label": "เบี้ยขยัน", "key": "bonus", "amount": 1000.0}]
    assert kk.income_lines(9100, "-", {}, 280, {"ot": 280.0})[1] == {"side": "income", "label": "เบี้ยขยัน", "key": "ot", "amount": 280.0}
    ded = kk.deduction_lines({"sso": "475", "advance": 3000, "loan": 0, "card": "-"}, {"advance": "เบิกล่วงหน้า"})
    assert ded == [{"side": "deduction", "label": "เบิกล่วงหน้า", "key": "advance", "amount": 3000.0},
                   {"side": "deduction", "label": "ปกส.", "key": "sso", "amount": 475.0}]


def test_build_row_carries_details():
    assert _row()["details"]["position"] == "พขร." and _row()["details"]["lines"] == []
    d = kk.make_details("ผลิต")
    assert _row(details=d)["details"] is d


def test_xlsx_details_comments_balances_and_remark(tmp_path, monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    from openpyxl.comments import Comment

    def sheet(values):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "สรุป"
        for ref, t in (("B2", "Scan"), ("C2", "รายชื่อ"), ("D2", "ยอดรับเงินเดือนเดือน เม.ย.58"), ("L2", "คงเหลือ"),
                       ("X2", "สะสมยกไป"), ("AA2", "เลขที่บัญชี"), ("AD2", "หมายเหตุ"), ("E4", "วัน"), ("F4", "เงิน"), ("H4", "เงิน")):
            ws[ref] = t
        for col, t in zip("JKMNOPQRSTUVW", ["รวมรับ", "รวมจ่าย", "สะสม", "เบิกล่วงหน้า", "คืนเงินกู้", "ส่วนกลาง", "passport",
                                           "ความผิด", "อุปกรณ์/อื่นๆ", "ปกส.", "ค้างเงิน", "จ่ายค้าง", "ค้างย/ป"]):
            ws[f"{col}3"] = t
        # formulas in the first workbook, the values Excel would have cached in the second
        a = [1, "SB-KK006", "นายห้า", "พขร.", 27, "=E5*400" if not values else 10800, None, 9400, None,
             20200, 4250, 15950, None, 3000, 500, None, None, None, None, 750, 8750, "=O5" if not values else 500,
             "=U5-V5" if not values else 8250, 20000]
        b = [2, "SB-KK004", "นายสี่", "พขร.", 30, "=E6*400" if not values else 12000, 172890,
             "=G6*2/100" if not values else 3457.8, None, 15457.8, 3000, 12457.8, None, 3000] + [None] * 9 + [20000]
        for i, r in enumerate((a, b), start=5):
            for j, v in enumerate(r, start=1):
                ws.cell(i, j, v)
        ws["AA5"], ws["AD5"], ws["AD6"] = "123-4-56789-0", "ออกสิ้นเดือน", "นายสี่"
        ws["F5"].comment = Comment("ส.ค.69 คุณโอมอนุมัติ ปรับจาก325 เป็น 350", "Admin")
        ws["AA5"].comment = Comment("bank note", "Admin")          # right of สะสมยกไป: never read
        return wb
    pf, pv = tmp_path / "kk.xlsx", tmp_path / "kk_values.xlsx"
    sheet(False).save(pf)
    sheet(True).save(pv)
    real_load = openpyxl.load_workbook
    monkeypatch.setattr(openpyxl, "load_workbook",
                        lambda path, *a, **kw: real_load(pv if kw.get("data_only") else path, *a, **kw))
    by = {r["code"]: r for r in kk.parse_file(str(pf), "2026-09")}
    d6 = by["SB-KK006"]["details"]
    assert d6["rate"] == 400 and d6["position"] == "พขร." and d6["sales"] is None
    assert (d6["loan_before"], d6["loan_paid"], d6["loan_after"], d6["savings_balance"]) == (8750, 500, 8250, 20000)
    assert [(l["side"], l["label"], l["key"], l["amount"]) for l in d6["lines"]] == [
        ("income", "วันทำงาน/เงิน", "wage", 10800), ("income", "OT./เงิน", "commission", 9400),
        ("deduction", "เบิกล่วงหน้า", "advance", 3000), ("deduction", "คืนเงินกู้", "loan", 500), ("deduction", "ปกส.", "sso", 750)]
    assert d6["notes"] == [{"label": "เงิน", "text": "ส.ค.69 คุณโอมอนุมัติ ปรับจาก325 เป็น 350"},
                           {"label": "หมายเหตุ", "text": "ออกสิ้นเดือน"}]
    d4 = by["SB-KK004"]["details"]
    assert d4["sales"] == 172890 and d4["commission_pct"] == 2.0 and d4["notes"] == []   # หมายเหตุ = the name again: not a note
    assert d4["loan_before"] == 0 and d4["savings_balance"] == 20000
    assert "123-4-56789-0" not in repr(by) and "bank note" not in repr(by)


# ---------------------------------------------------------------- details over every real month

BANK = re.compile(r"\d{3}-?\d-?\d{5}-?\d")
ID13 = re.compile(r"(?<!\d)\d{13}(?!\d)|\d-\d{4}-\d{5}-\d{2}-\d")


def _real_kk_files():
    from app import payroll_people as pp
    return [(m, pp.BASE + g["kk"]) for m, g in sorted(pp.FILES.items()) if "kk" in g]


@pytest.mark.parametrize("month,path", _real_kk_files())
def test_real_details_add_up_and_hold_no_bank_data(month, path):
    if not os.path.exists(path):
        pytest.skip("payroll file not on this machine")
    rows = kk.parse_file(path, month)
    assert rows
    loan_breaks = []
    for r in rows:
        d = r["details"]
        assert set(d) == {"position", "rate", "sales", "commission_pct", "loan_before", "loan_paid", "loan_after",
                          "savings_balance", "lines", "notes"}
        inc = sum(ln["amount"] for ln in d["lines"] if ln["side"] == "income")
        ded = sum(ln["amount"] for ln in d["lines"] if ln["side"] == "deduction")
        assert abs(inc - r["gross"]) <= 1, (r["code"], inc, r["gross"])
        assert abs(ded - r["deduct_total"]) <= 1, (r["code"], ded, r["deduct_total"])
        assert all(ln["amount"] and ln["key"] in (kk.PARTS if ln["side"] == "income" else kk.DEDUCTIONS) for ln in d["lines"])
        lb, lp, la = d["loan_before"], d["loan_paid"], d["loan_after"]
        if None not in (lb, lp, la) and abs(lb - lp - la) > 1:
            loan_breaks.append((r["code"], lb, lp, la))
        text = repr(d)
        assert not BANK.search(text) and not ID13.search(text), r["code"]
        if r["source"] == "pdf":
            assert d["notes"] == [] and d["rate"] is None
        else:
            assert d["rate"] and d["savings_balance"] is not None
    if loan_breaks:   # HQ's own sheet, not the reader: listed, not failed
        print(f"\nHQ ค้างเงิน - จ่ายค้าง != ค้างย/ป in {month}: {loan_breaks}")


@pytest.mark.skipif(not os.path.exists(OCT_PDF), reason="payroll files not on this machine")
def test_real_october_pdf_prints_the_balance_columns():
    by = {r["code"]: r["details"] for r in kk.parse_file(OCT_PDF, "2026-10")}
    assert (by["SB-KK006"]["loan_before"], by["SB-KK006"]["loan_paid"], by["SB-KK006"]["loan_after"]) == (8250, 500, 7750)
    assert by["SB-KK004"]["sales"] == 157130 and by["SB-KK004"]["commission_pct"] == 2.0
    assert by["SB-KK001"]["savings_balance"] == 20200 and by["SB-KK001"]["loan_before"] == 0
