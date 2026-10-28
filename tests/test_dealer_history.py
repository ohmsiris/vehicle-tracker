from datetime import date

from app import dealer_history as dh

SAMPLE = """
##### page 1
ประวัติการซ่อม
หมายเลขทะเบียนรถ
หมายเลขประÎารถ
1ฒบ6257 กท
MP1TFR86JFT028171
เลขครื่อง
NK0306
วันที่เข้าซ่อม
ค้างซ่อม
17/04/2569
19/04/2569
423,733
ค่าแรง
บริการล้างรถฟรี
 
ค่าแรง
ตรวจสอบช่วงล่างทั้งหมด
ปกติ
 
ค่าแรง
ตรวจ/เติมลมยาง
*ตรวจเช็คสภาพยาง ความลึกดอกยางล้อหน้า 7 มม. ปี 
24/25**ล้อหลัง 5 มม. ปี 29/24*
 
อะไหล่
นÎ้ามันเครื่อง SEMI 10W30 CI4 6L
1.00 EA
วัสดุสิ้นเปลือง
นÎ้ามันล้างชิ้นส่วน (ไม่มียี่ห้อ)
1.00
วัน/เวลาที่พิมพ์26/10/2569  16:22
หน้า1 / 2

##### page 2
ประวัติการซ่อม
หมายเลขทะเบียนรถ
1ฒบ6257 กท
ค้างซ่อม
17/04/2569
19/04/2569
423,733
อะไหล่
ปะเก็นเหลวสีดÎา
10.00 G1C
05/10/2568
05/10/2568
409,401
ค่าแรง
ตรวจ/เติมนÎ้ากลั่นแบตเตอรี่
*ค่าแบตเตอรี่ 590 CCA วัดได้ 575 CCA*
 
วัน/เวลาที่พิมพ์26/10/2569  16:22
หน้า2 / 2
"""


def test_the_floating_sara_am_is_put_right():
    assert dh.normalise("นÎ้ามันเครื่อง") == "น้ำมันเครื่อง" and dh.normalise("ทÎาความสะอาด") == "ทำความสะอาด" and dh.normalise("จÎานวน") == "จำนวน"


def test_a_report_is_read_into_visits_and_lines_and_a_visit_continued_on_the_next_page_is_one_visit():
    r = dh.parse_report_text(SAMPLE)
    assert (r["plate"], r["province"], r["vin"], r["engine_no"]) == ("1ฒบ6257", "กท", "MP1TFR86JFT028171", "NK0306") and r["unparsed"] == []
    assert [(v["date"], v["invoice_date"], v["mileage"], len(v["lines"])) for v in r["visits"]] == [
        (date(2026, 4, 17), date(2026, 4, 19), 423733, 6), (date(2025, 10, 5), date(2025, 10, 5), 409401, 1)]          # พ.ศ. -> ค.ศ.; page 2 continues the first visit
    first = r["visits"][0]["lines"]
    assert [(l["kind"], dh.line_text(l)) for l in first[:3]] == [("ค่าแรง", "บริการล้างรถฟรี"), ("ค่าแรง", "ตรวจสอบช่วงล่างทั้งหมด (ปกติ)"),
                                                                ("ค่าแรง", "ตรวจ/เติมลมยาง (ตรวจเช็คสภาพยาง ความลึกดอกยางล้อหน้า 7 มม. ปี 24/25 ล้อหลัง 5 มม. ปี 29/24)")]
    oil, wash, gasket = first[3], first[4], first[5]
    assert (oil["kind"], oil["description"], oil["quantity"], oil["unit"]) == ("อะไหล่", "น้ำมันเครื่อง SEMI 10W30 CI4 6L", 1.0, "EA")
    assert (wash["kind"], wash["quantity"], wash["unit"]) == ("วัสดุสิ้นเปลือง", 1.0, None)                              # a quantity with no unit
    assert (gasket["description"], gasket["quantity"], gasket["unit"]) == ("ปะเก็นเหลวสีดำ", 10.0, "G1C")
