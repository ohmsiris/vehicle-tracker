"""ใบงานคนขับ export (app/route_sheet.py)."""
from openpyxl import load_workbook
from io import BytesIO

from app import route_sheet as rsh

ROUTE = {"truck_no": "35", "driver_name": "คนขับ", "name": "สายทดสอบ", "driver_phone": "081"}
CUST = [{"id": 1, "seq": 1, "name": "ร้านเอ", "bins": "V200-1, V200-2", "prices": {"lod": 40, "lek": 40, "mo": 40}},
        {"id": 2, "seq": 2, "name": "ร้านบี", "bins": None, "prices": {"lod": 40, "pack": 90}},
        {"id": 3, "seq": 3, "name": "Lotus", "bins": None, "prices": {"pack": 100}, "billing": "invoice"}]


def test_price_text():
    assert rsh.price_text({"lod": 40, "lek": 40}) == "40"
    assert rsh.price_text({"lod": 40, "pack": 90}) == "ญ40 พ90"
    assert rsh.price_text({}, True) == "ฟรี" and rsh.price_text({}) == ""


def test_blank_sheet_has_every_customer_and_no_numbers():
    s = rsh.build(ROUTE, CUST, None, None, [], {})
    assert [r["name"] for r in s["rows"]] == ["1. ร้านเอ", "2. ร้านบี", "3. Lotus"]
    assert s["summary"] is None and s["totals"]["amount"] is None
    wb = load_workbook(BytesIO(rsh.to_xlsx(s)))
    assert wb.active["A4"].value == "1. ร้านเอ"


def test_a_filled_day_carries_debts_and_walkins():
    day_row = {"status": "closed", "returns_foh": None, "doc": {"loads": [{"q": {"lod": 10}}], "cash": {"100": 2},
               "new_customers": [{"key": "new:1", "name": "ร้านใหม่", "prices": {"lod": 45}}]},
               "totals": {"amount": 205, "loaded": {"lod": 10}, "sold": {"lod": 5, "pack": 1}, "flags": ["เงินขาด 5 บาท"]}}
    lines = [{"customer_id": 1, "qty": {"lod": 2}, "amount": 80, "paid": 0, "old_paid": 30, "billing": "cash"},
             {"customer_id": 3, "qty": {"pack": 1}, "amount": 100, "paid": 0, "old_paid": 0, "billing": "invoice"},
             {"customer_id": None, "walkin": "ร้านใหม่", "qty": {"lod": 1}, "amount": 45, "paid": 45, "old_paid": 0, "billing": "cash"}]
    s = rsh.build(ROUTE, CUST, "2026-11-10", day_row, lines, {"1": 50})
    a, b, lotus, walk = s["rows"]
    assert a["owed_before"] == 50 and a["old_paid"] == 30 and a["owed_after"] == 100 and a["paid_all"] == 30
    assert not b["sold"] and b["amount"] is None
    assert lotus["invoice"] and lotus["owed_after"] is None                  # วางบิล is not ค้าง
    assert walk["name"] == "ร้านใหม่ (เพิ่มเอง)" and walk["paid"] == 45
    assert s["totals"]["q"]["lod"] == 3 and s["summary"]["notes"]["100"] == 2
    data = rsh.to_xlsx(s)
    ws = load_workbook(BytesIO(data)).active
    assert ws.print_title_rows == "$1:$3" and ws.page_setup.orientation == "portrait"


def test_a_shop_with_its_own_price_per_product_gets_the_diagonal_cells():
    assert rsh.split_prices({"zong": 150, "lod": 30, "lek": 30}) == {"zong": 150.0, "lod": 30.0, "lek": 30.0}
    assert rsh.split_prices({"lod": 40, "lek": 40}) == {}                       # one price: it goes in ราคา
    s = rsh.build(ROUTE, CUST, None, None, [], {})
    b = s["rows"][1]                                                            # ร้านบี: ใหญ่ 40, แพ็ค 90
    assert b["price"] == "" and b["split"] == {"lod": 40.0, "pack": 90.0}
    ws = load_workbook(BytesIO(rsh.to_xlsx(s))).active
    cell = ws.cell(row=5, column=4 + 2)                                         # row 2 of the customers, column ใหญ่
    assert str(cell.value) == "40" and cell.border.diagonalUp
