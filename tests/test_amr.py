from app import amr

HTML = """<td colspan='5' class='header'>[ประจำเดือน : มกราคม 2569]</td></tr>
<tr><td class='detail'>บัญชีผู้ใช้ไฟ : </td><td>020019229615&nbsp;</td><td class='detail'>ชื่อผู้ใช้ไฟ : </td><td>x</td><td></td></tr>
<tr><td class='detail'>หมายเลขมิเตอร์ : </td><td>23056197&nbsp;</td></tr> รายงานข้อมูลกิโลวัตต์ชั่วโมงรายเดือน
<tr><td class='leftcenter'>&nbsp;01/01/2026 00.15</td><td class='rightdetail'></td><td class='rightdetail'></td><td class='rightdetail'>106.43</td><td class='rightdetail'>106.43</td></tr>
<tr><td class='leftcenter'>&nbsp;02/01/2026 24.00</td><td class='rightdetail'></td><td class='rightdetail'>50.5</td><td class='rightdetail'></td><td class='rightdetail'>50.5</td></tr>
<tr><td class='leftcenter'>&nbsp;05/01/2026 09.15</td><td class='rightdetail'>80</td><td class='rightdetail'></td><td class='rightdetail'></td><td class='rightdetail'>80</td></tr>
"""


def test_parse_slots_are_start_of_15_minutes_and_rate_column():
    r = amr.parse(HTML)
    assert (r["month"], r["ca_no"], r["meter_no"]) == ("2026-01", "020019229615", "23056197")
    assert r["rows"] == [{"slot_start": "2026-01-01 00:00:00+07", "tou": "C", "kwh": 106.43},
                         {"slot_start": "2026-01-02 23:45:00+07", "tou": "B", "kwh": 50.5},
                         {"slot_start": "2026-01-05 09:00:00+07", "tou": "A", "kwh": 80.0}]


def test_empty_report_has_no_month():
    assert amr.parse("รายงานข้อมูลกิโลวัตต์ชั่วโมงรายเดือน [ประจำเดือน : ]")["month"] is None
