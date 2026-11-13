import os

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
