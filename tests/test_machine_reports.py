import io
import sys
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import machine_plan as mp
from app import machine_reports as mr

TODAY = date(2026, 11, 5)
M1 = {"id": "m1", "name": "เครื่อง 1", "kind": "เครื่อง"}
SONG = {"id": "m6", "name": "เครื่องบ่อซอง MYCOM 30 ตัน", "kind": "บ่อซอง"}


def overview():
    progs = mp.merge_programs([])
    rows = []
    for m in (M1, SONG):
        p = progs[mp.program_code_for(m, progs)]
        rows.append({**m, "plan": mp.plan_for_machine(m, p, [{"machine_id": m["id"], "job_key": "oil", "event_date": "2026-06-17", "created_at": "x"}], [], TODAY)})
    return {"ready": True, "machines": rows, "jobs": [{"key": j["key"], "name": j["name"]} for j in mp.JOBS] + [{"key": k, "name": n} for k, n in mp.RETIRED.items()], "today": TODAY.isoformat()}


def test_pm_list_is_one_landscape_page_with_a_dot_per_machine():
    wb = load_workbook(io.BytesIO(mr.pm_list(2026, overview=overview())))
    ws = wb["แผน PM"]
    assert ws.page_setup.orientation == "landscape" and ws.page_setup.fitToHeight == 1
    assert "2569" in ws["A1"].value and ws["E4"].value == "หลอด 1" and ws["F4"].value == "ซอง MYCOM"
    rows = {ws.cell(row=r, column=2).value: r for r in range(5, ws.max_row + 1)}
    assert ws.cell(row=rows["ล้างฟรีซเซอร์ (วนน้ำยา)"], column=5).value == "●" and ws.cell(row=rows["ล้างฟรีซเซอร์ (วนน้ำยา)"], column=6).value == "-"
    assert "ทุกครั้งที่ถ่ายน้ำมัน" in ws.cell(row=rows["ล้างคูลลิ่งทาวเวอร์"], column=4).value
    assert "แวคคั่มฟรีซเซอร์" not in rows                                   # out of the plan
    texts = [str(c.value) for row in ws.iter_rows() for c in row if c.value]
    assert any(t.startswith("ผู้ตรวจสอบ") for t in texts) and not any("ผู้บันทึก" in t for t in texts)
    assert all(c.font.name == "Angsana New" for row in ws.iter_rows() for c in row if c.value)


def test_yearly_report_has_status_month_sheets_and_the_full_list():
    events = [{"id": 1, "machine_id": "m1", "job_key": "oil", "event_date": "2026-06-17", "performed_by": "ช่างเอ๋", "summary": "ถ่ายน้ำมันคอมเพรสเซอร์"},
              {"id": 2, "machine_id": "m1", "job_key": "freezer_vac", "event_date": "2026-03-02", "summary": "แวคคั่มฟรีซเซอร์"}]
    wb = load_workbook(io.BytesIO(mr.yearly_report(2026, today=TODAY, overview=overview(), events=events)))
    assert wb.sheetnames == ["สรุปสถานะ", "ถ่ายน้ำมัน", "คอนเดนเซอร์ ฝาสูบ", "ฟรีซเซอร์ คูลลิ่ง", "โอเวอร์ฮอล ตรวจเช็ค", "รายการทั้งหมด"]
    oil = wb["ถ่ายน้ำมัน"]
    june = next(r for r in range(1, oil.max_row + 1) if oil.cell(row=r, column=1).value == "มิถุนายน")
    assert oil.cell(row=june, column=2).value == "17/6/69 ช่างเอ๋"
    freezer = [str(c.value) for row in wb["ฟรีซเซอร์ คูลลิ่ง"].iter_rows() for c in row if c.value]
    assert "แวคคั่มฟรีซเซอร์" in freezer                                      # done this year: still gets its table
    full = wb["รายการทั้งหมด"]
    assert full.cell(row=5, column=1).value == "17/6/69" and full.cell(row=5, column=3).value == "ถ่ายน้ำมันคอมเพรสเซอร์"
