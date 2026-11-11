"""ใบงานคนขับ as the owner's Word sheet (app/route_docx.py)."""
import re
import zipfile
from io import BytesIO

from app import route_docx as rd
from app import route_sheet as rsh

ROUTE = {"truck_no": "35", "driver_name": "คนขับ ทดสอบ", "name": "สายทดสอบ", "driver_phone": "081"}


def _custs(n):
    out = [{"id": i, "seq": i, "name": f"ร้าน{i}", "bins": "V200-1, S200-2" if i == 1 else None, "prices": {"lod": 40}} for i in range(1, n + 1)]
    out[1]["prices"] = {"zong": 150, "lod": 30}                       # its own price per product: the diagonal cells
    return out


def _doc(b):
    return zipfile.ZipFile(BytesIO(b)).read("word/document.xml").decode("utf-8")


def test_blank_sheet_is_the_template_with_rows_added():
    s = rsh.build(ROUTE, _custs(30), None, None, [], {})
    x = _doc(rd.build([s]))
    assert x.count("<w:tbl>") == 2 * 2 + 1                            # 2 blocks (24 + 6): header + customers each, one summary box
    assert x.rindex("รวมขาย") > x.rindex("ร้าน30")                       # the summary box comes after the last shop
    assert "ร้าน30" in x and "ร้าน1" in x and "คนขับ ทดสอบ" in x
    assert x.count("<w:tr2bl") == 2                                  # shop 2: ซอง and ใหญ่ have their own price
    assert "ใหญ่" in x and "หลอด" not in x and "โม่" not in x            # the owner's product names
    assert "ไมล์ใหม่" not in x                                         # no mileage row
    assert rd.MARK not in x                                           # the PDF marker only goes in for PDF


def test_a_filled_day_and_several_routes():
    day_row = {"status": "closed", "returns_foh": None, "doc": {"loads": [{"q": {"lod": 10}}], "cash": {"100": 2}},
               "totals": {"amount": 80, "loaded": {"lod": 10}, "sold": {"lod": 2}, "expected_cash": 80, "counted": 200}}
    lines = [{"customer_id": 1, "qty": {"lod": 2}, "amount": 80, "paid": 80, "old_paid": 0, "billing": "cash"}]
    s = rsh.build(ROUTE, _custs(5), "2026-11-10", day_row, lines, {})
    b = rd.build([s, s], marks=True)
    x = _doc(b)
    assert "10/11/2026" in x and rd.MARK in x
    assert len(re.findall(r'<w:sectPr[ >]', x)) == 2                  # route 2 starts on a new sheet (+ the document's own)


def test_the_pdf_copy_uses_the_servers_font_scaled():
    s = rsh.build(ROUTE, _custs(3), None, None, [], {})
    x = _doc(rd.build([s], marks=True))
    assert "TH Sarabun New" not in x and 'w:ascii="Sarabun"' in x
    assert '<w:sz w:val="32"/>' not in x                              # 16 pt -> about 11.5 pt
