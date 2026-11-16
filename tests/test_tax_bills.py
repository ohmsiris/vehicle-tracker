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
    assert ws["A15"].value == "(สามพันแปดร้อยสิบเก้าบาทเก้าสิบสตางค์)" and ws["F10"].value == "=C10*E10"
    assert ws.page_setup.paperSize == tpl.page_setup.paperSize and ws.page_setup.scale == tpl.page_setup.scale
    assert ws.page_margins.left == tpl.page_margins.left and ws.column_dimensions["B"].width == tpl.column_dimensions["B"].width
    assert ws.row_dimensions[10].height == tpl.row_dimensions[10].height and ws["B6"].value == tpl["B6"].value
    changed = {c.coordinate for row in tpl.iter_rows() for c in row if ws[c.coordinate].value != c.value}
    assert changed <= {"E6", "E7", "B10", "C10", "E10", "A15"}


def test_the_monthly_bill_file_has_one_row_per_delivery_and_her_totals():
    dels = [{"day": f"2026-11-{d:02d}", "note_no": f"4733/2366{d:02d}", "item": "น้ำแข็งหลอดบด", "bags": 500, "kg": 10000, "price": 0.65} for d in range(1, 11)]
    wb = openpyxl.load_workbook(io.BytesIO(ti.monthly_workbook({"month": "2026-11", "doc_no": "SRI-2569-1011011"}, dels, ti.template("10", "monthly"))))
    ws = wb.worksheets[0]
    assert ws["H2"].value == "เลขที่ใบวางบิล : SRI-2569-1011011" and ws["B2"].value.endswith("1-30 พฤศจิกายน  2569")
    assert ws["B5"].value.year == 2569 and ws["C14"].value == "4733/236610" and ws["F14"].value == "=E14*20" and ws["I14"].value == "=F14*H14"
    assert ws["E15"].value == "=SUM(E5:E14)" and ws["I15"].value == "=SUM(I5:I14)" and ws["J17"].value == "=SUM(J15:J16)"
    assert ws.print_area.endswith("$A$1:$I$24") and ws["A16"].value is None
