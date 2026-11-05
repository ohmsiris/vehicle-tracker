from datetime import date

from app import acc_cheques as ac

SHEET = [
    ["รายการจ่ายเช็ค บจ.สระบุรีวารีเทพ ประจำเดือน กันยายน 2569"] + [""] * 11,
    ["ที่", "รายการจ่าย", "ชื่อในเช็ค", "จำนวนเงิน", "", "วันที่บนเช็ค", "เลขที่เช็ค", "", "", "", "", "หมายเหตุ"],
    [1.0, "คชจ.+เบิกกลางเดือน 9/2569", "คุณรัชนิดาถอนจากเล่ม", "", 404701.0, "", "", "", "", "", "", ""],
    [2.0, "ค่าไฟฟ้า 72/เดือน 08/69", "ซื้อแคชเชียร์เช็คไปจ่ายที่ไฟฟ้า", "", 107.0, 244612.0, 10207161.0, "", "", "", "", ""],
    [3.0, "ค่าทำบัญชี+ภาษี+08-69", "ธิดารัตน์", "", 96494.67, "", "10207220", "", "", "", "", "บริการ=13000/ภงด.53=455.67vat=83,039"],
    [4.0, "ถุงใส19*31", "นายสุกิจ", "", 10800.0, "", "", "", "", "", "", ""],
    [5.0, "ของแปลก", "บริษัท เอ", "", 500.0, "", "", "", "", "", "", ""],
    ["รวม", "", "", 0.0, 1.0, "", "", "", "", "", "", ""],
]


def test_be_dates():
    assert ac.be_date(244592.0) == date(2026, 8, 31)
    assert ac.be_date("15/09/2569") == date(2026, 9, 15)
    assert ac.be_date("") is None


def test_parse_and_build():
    rows = ac.parse_sheet(SHEET)
    assert [r["no"] for r in rows] == [1, 2, 3, 4, 5]
    out = ac.build(rows, branch="สระบุรี", file_month=date(2026, 9, 1), batch="t")
    e = {int(x["source_id"].split(":r")[1]): x for x in out["entries"]}
    debit = lambda n: {ln["account"]: ln["debit"] for ln in e[n]["lines"] if ln["debit"]}
    assert debit(1) == {"1100": 404701.0}                                   # petty cash funding is not a cost
    assert debit(2) == {"5100": 100.0, "1400": 7.0} and e[2]["entry_date"] == "2026-08-25"   # PEA: VAT split, usage month
    assert debit(3) == {"7600": 13000.0, "2200": 455.67, "2300": 83039.0}
    assert debit(4) == {"5330": 10800.0} and e[4]["entry_date"] == "2026-08-25"  # an individual: no VAT; last month's bill
    assert debit(5) == {"7900": 467.29, "1400": 32.71} or debit(5) == {"7900": 500.0}
    assert any("ของแปลก" in f for f in out["flags"])
    for x in out["entries"]:
        assert round(sum(ln["debit"] for ln in x["lines"]), 2) == round(sum(ln["credit"] for ln in x["lines"]), 2)
