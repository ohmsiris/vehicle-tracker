import io
from datetime import date

import openpyxl

from app import tax_invoices as ti
from app.bahttext import bahttext


def test_bahttext_like_excel():
    assert bahttext(7617.972) == "เจ็ดพันหกร้อยสิบเจ็ดบาทเก้าสิบเจ็ดสตางค์"       # the bill's net, unrounded VAT included
    assert bahttext(2128.23) == "สองพันหนึ่งร้อยยี่สิบแปดบาทยี่สิบสามสตางค์"
    assert bahttext(21) == "ยี่สิบเอ็ดบาทถ้วน" and bahttext(1000001) == "หนึ่งล้านเอ็ดบาทถ้วน" and bahttext(0.5) == "ห้าสิบสตางค์"


def test_next_number_runs_like_hers():
    cp = {"number_format": "SRI-{be}-{mm}{seq:03d}", "seq_monthly": True}
    assert ti.next_for(cp, date(2026, 11, 16), ["SRI-2569-11152", "SRI-2569-10301"]) == "SRI-2569-11153"
    assert ti.next_for(cp, date(2026, 12, 1), ["SRI-2569-11152"]) == "SRI-2569-12001"                 # a new month starts at 001
    nongdon = {"number_format": "SRI-{be}-06{mm}{seq:03d}", "seq_monthly": False}                       # runs on through the year
    assert ti.next_for(nongdon, date(2026, 11, 30), ["SRI-2569-0610034", "SRI-2569-0609033"]) == "SRI-2569-0611035"
    assert ti.next_for({"number_format": None}, date(2026, 11, 1), []) is None


def test_items_with_bags_and_tab_names():
    sj = {"item_format": "น้ำแข็งซอง {bags} ก้อน (ศูนย์จำหน่าย)"}
    assert ti.item_text(sj, 90) == "น้ำแข็งซอง 90 ก้อน (ศูนย์จำหน่าย)"
    assert ti.item_text({"items": ["น้ำแข็งซอง 200 ก้อน (ไก่ชิ้นส่วน)", "x"]}) == "น้ำแข็งซอง 200 ก้อน (ไก่ชิ้นส่วน)"
    assert ti.item_text(sj, 90, "chosen") == "chosen"
    assert ti.sheet_title("SRI-2569-11152") == "CPF01-11152-2026" and ti.sheet_title("SRI-2569-0711022") == "CPF0711022-2026"


def test_bags_give_the_kg_for_sun_jamnai_and_global():
    cust = {"07": {"code": "07", "name": "CPF ศูนย์จำหน่าย", "mode": "kg", "entry": "bags", "kg_per_bag": 65, "item_format": "น้ำแข็งซอง {bags} ก้อน (ศูนย์จำหน่าย)"}}
    row = ti.clean({"doc_no": "SRI-2569-0711023", "doc_date": "2026-11-16", "bags": 90}, cust, [{"code": "07", "price": 0.51, "from_date": "2026-07-01"}])
    assert row["kg"] == 5850 and row["item"] == "น้ำแข็งซอง 90 ก้อน (ศูนย์จำหน่าย)" and row["total"] == 3192.35


def test_formulas_move_with_the_totals():
    assert ti.shift_formula("=SUM(E5:E31)", 31, 24, 32, -7) == "=SUM(E5:E24)"
    assert ti.shift_formula("=SUM(J32:J33)", 31, 24, 32, -7) == "=SUM(J25:J26)"
    assert ti.shift_formula("=J32*7%", 31, 24, 32, -7) == "=J25*7%"
    assert ti.shift_formula("รวมเงินทั้งสิ้น", 31, 24, 32, -7) == "รวมเงินทั้งสิ้น"


def test_the_bill_file_is_her_bill_with_five_cells_changed():
    rows = [{"doc_no": "SRI-2569-11153", "doc_date": "2026-11-16", "item": "น้ำแข็งซอง 200 ก้อน (ไก่ชิ้นส่วน)", "kg": 7000, "price": 0.51, "total": 3819.9}]
    wb = openpyxl.load_workbook(io.BytesIO(ti.bills_workbook(rows, ti.template("P"))))
    tpl = openpyxl.load_workbook(ti.template("P")).worksheets[0]
    ws = wb.worksheets[0]
    assert wb.sheetnames == ["CPF01-11153-2026"]
    assert ws["E6"].value == "เลขที่   SRI-2569-11153" and ws["E7"].value == "วันที่  16/11/2569" and ws["C10"].value == 7000
    assert ws["A15"].value == "(สามพันแปดร้อยสิบเก้าบาทเก้าสิบสตางค์)" and ws["F10"].value == 3570 and abs(ws["F15"].value - 3819.9) < 1e-6
    assert ws.page_setup.paperSize == tpl.page_setup.paperSize and ws.page_setup.scale == tpl.page_setup.scale
    assert ws.page_margins.left == tpl.page_margins.left and ws.column_dimensions["B"].width == tpl.column_dimensions["B"].width
    assert ws.row_dimensions[10].height == tpl.row_dimensions[10].height and ws["B6"].value == tpl["B6"].value
    changed = {c.coordinate for row in tpl.iter_rows() for c in row if ws[c.coordinate].value != c.value}
    assert changed <= {"E6", "E7", "B10", "C10", "E10", "A15", "F10", "F11", "F12", "F13", "F14", "F15"}


def test_the_monthly_bill_file_has_one_row_per_delivery_and_her_totals():
    dels = [{"day": f"2026-11-{d:02d}", "note_no": f"4733/2366{d:02d}", "item": "น้ำแข็งหลอดบด", "bags": 500, "kg": 10000, "price": 0.65} for d in range(1, 11)]
    wb = openpyxl.load_workbook(io.BytesIO(ti.monthly_workbook({"month": "2026-11", "doc_no": "SRI-2569-1011011"}, dels, ti.template("10", "monthly"))))
    ws = wb.worksheets[0]
    assert ws["H2"].value == "เลขที่ใบวางบิล : SRI-2569-1011011" and ws["B2"].value.endswith("1-30 พฤศจิกายน  2569")
    assert ws["B5"].value.year == 2569 and ws["C14"].value == "4733/236610"
    assert ws["E15"].value == 5000 and ws["I15"].value == 65000 and abs(ws["I16"].value - 4550) < 1e-6 and abs(ws["I17"].value - 69550) < 1e-6
    assert ws["F14"].value == 10000 and ws["I14"].value == 6500                 # her sums written as numbers (Protected View shows them)
    assert all(ws.cell(r, 10).value is None for r in range(1, 40))              # nothing outside her table
    assert ws.print_area.endswith("$A$1:$I$24") and ws["A16"].value is None


def test_many_bills_on_one_tab_one_page_each_and_nothing_outside_the_box():
    rows = [{"doc_no": f"SRI-2569-1115{i}", "doc_date": "2026-11-16", "item": "น้ำแข็งซอง 200 ก้อน (ไก่ชิ้นส่วน)", "kg": 7000 + i, "price": 0.51,
             "total": round((7000 + i) * 0.51 * 1.07, 2)} for i in range(3, 6)]
    wb = openpyxl.load_workbook(io.BytesIO(ti.bills_workbook(rows, ti.template("P"))))
    assert len(wb.sheetnames) == 1
    ws = wb.worksheets[0]
    assert ws["E6"].value == "เลขที่   SRI-2569-11153" and ws["E26"].value == "เลขที่   SRI-2569-11154" and ws["E46"].value == "เลขที่   SRI-2569-11155"
    assert ws["C30"].value == 7004 and abs(ws["F30"].value - 7004 * 0.51) < 1e-6 and ws["F35"].value == 3822.08
    assert ws["A21"].value == ws["A1"].value and ws.row_dimensions[30].height == ws.row_dimensions[10].height
    assert [b.id for b in ws.row_breaks.brk] == [20, 40] and ws.print_area.endswith("$A$1:$M$60")
    assert all(ws.cell(r, c).value is None for r in range(1, 61) for c in range(7, 14))   # no notes, no helper column G


def test_excel_will_not_call_the_file_damaged():
    """Every cell format points at fonts / fills / borders / styles that are there (Excel "repairs" the file otherwise and drops the
    column widths -- 16 Nov 2026), and no formula is left that Protected View would show blank."""
    import re
    import zipfile
    rows = [{"doc_no": "SRI-2569-11153", "doc_date": "2026-11-16", "item": "x", "kg": 7000, "price": 0.51, "total": 3819.9}]
    dels = [{"day": "2026-11-01", "note_no": "1", "item": "x", "bags": 10, "kg": 200, "price": 0.65}]
    for code in ("P", "07", "03"):
        files = [ti.bills_workbook(rows, ti.template(code))]
        for data in files:
            check(data, re, zipfile)
    for code in ("10", "06"):
        check(ti.monthly_workbook({"month": "2026-11", "doc_no": "SRI-2569-1011011"}, dels, ti.template(code, "monthly")), re, zipfile)


def check(data, re, zipfile):
    z = zipfile.ZipFile(io.BytesIO(data))
    st = z.read("xl/styles.xml").decode("utf-8")
    count = lambda tag, child: len(re.findall(rf"<{child}\b", re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", st, re.S).group(1)))  # noqa: E731
    limits = {"xfId": count("cellStyleXfs", "xf"), "fontId": count("fonts", "font"), "fillId": count("fills", "fill"), "borderId": count("borders", "border")}
    xfs = re.findall(r"<xf\b[^>]*>", re.search(r"<cellXfs\b[^>]*>(.*?)</cellXfs>", st, re.S).group(1))
    for x in xfs:
        for attr, limit in limits.items():
            m = re.search(rf'{attr}="(\d+)"', x)
            assert not m or int(m.group(1)) < limit, (attr, x)
    sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "<f>" not in sheet
    book = z.read("xl/workbook.xml").decode("utf-8")
    n = book.count("<sheet ")
    for attr in ("firstSheet", "activeTab"):                    # a tab index past the last tab: Excel "repairs" the file
        m = re.search(rf'{attr}="(\d+)"', book)
        assert not m or int(m.group(1)) < n, (attr, n)
    for v in re.findall(r"<v>([^<]+)</v>", sheet):
        assert len(v.split(".")[1]) <= 2 if "." in v and "e" not in v.lower() else True, v
