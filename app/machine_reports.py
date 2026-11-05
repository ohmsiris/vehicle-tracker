"""
Printable machine-maintenance files for officials and outside parties (the buttons on แผนซ่อมบำรุงเครื่องจักร):

* yearly_report(year) -- รายงานซ่อมบำรุงเครื่องจักรประจำปี: a landscape "สรุปสถานะ" page (each job's last date and state per machine),
  one portrait sheet per kind of job with a month-by-month table (two jobs per A4 page), and the list of every report of the year.
* pm_list(year) -- แผน PM: ONE landscape A4 page -- each job, what is done, how often and on which machine; no done / not-done marks.

The owner's print rules: black and white, plain, font Angsana New, fits A4 (more pages are fine), the sign-off line is
"ผู้ตรวจสอบ ...... วันที่ ......" only, and ฟรีซเซอร์ always written in full. Both return the .xlsx file as bytes.
"""
import io
from datetime import date
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.pagebreak import Break

from app import machine_plan, supabase

COMPANY = "บริษัท สระบุรีวารีเทพ จำกัด (สาขาเมืองสระบุรี)"
MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
STATE_TEXT = {"late": "เลยกำหนด", "never": "ไม่เคยทำ", "soon": "ใกล้ถึงกำหนด", "ok": "ปกติ", "none": "ไม่มีบันทึก"}
CHECKER = "ผู้ตรวจสอบ ......................................................  วันที่ ............................"
_LINE = Side(style="thin", color="000000")
_BOX = Border(left=_LINE, right=_LINE, top=_LINE, bottom=_LINE)

# What each job is, written for someone outside the company (the app's notes are working notes).
WHAT = {
    "oil": "เปลี่ยนน้ำมันคอมเพรสเซอร์ ล้างอ่างน้ำมัน ตะแกรงน้ำมัน และกรองทางดูด",
    "grease": "อัดจาระบีตลับลูกปืนมอเตอร์คอมเพรสเซอร์",
    "belts": "ตรวจสภาพและตั้งความตึงสายพาน (เปลี่ยนทั้งชุดเมื่อสึกหรอ)",
    "cooling": "ล้างทำความสะอาดคูลลิ่งทาวเวอร์และอ่างน้ำ",
    "freezer_wash": "วนน้ำยาล้างตะกรันในฟรีซเซอร์",
    "headwash": "ล้างทำความสะอาดฝาสูบ",
    "check12k": "เปิดฝาตรวจแผ่นวาล์ว สปริง แหวน เปลี่ยนชิ้นที่สึกหรอ",
    "overhaul": "เปลี่ยนลิ้น สปริง แหวนลูกสูบ ปะเก็น แบริ่งก้านสูบ ตรวจสภาพลูกสูบและแบริ่ง",
    "condenser": "ล้างทำความสะอาดคอนเดนเซอร์ ขจัดตะกรัน",
    "screw_overhaul": "อัดจาระบี เปลี่ยนลูกปืนและซีล ตรวจเกียร์และใบมีด ของสกรูลำเลียงและเครื่องบด",
}
# The jobs done at the compressor oil change: every oil change, or every 2nd / 3rd / 6th one.
ALONG = {"grease": "ทุกครั้งที่ถ่ายน้ำมัน", "belts": "ทุกครั้งที่ถ่ายน้ำมัน", "cooling": "ทุกครั้งที่ถ่ายน้ำมัน", "freezer_wash": "ทุกครั้งที่ถ่ายน้ำมัน",
         "headwash": "ทุก 2 รอบถ่ายน้ำมัน", "check12k": "ทุก 3 รอบถ่ายน้ำมัน", "overhaul": "ทุก 6 รอบถ่ายน้ำมัน"}
PM_GROUPS = [("คอมเพรสเซอร์ (ทำพร้อมรอบถ่ายน้ำมัน)", ["oil", "grease", "belts", "check12k", "overhaul"]),
             ("ระบบระบายความร้อนและฟรีซเซอร์", ["cooling", "freezer_wash", "headwash", "condenser"]),
             ("สกรูลำเลียงและเครื่องบด", ["screw_overhaul"])]
# The yearly report's month-by-month sheets: (sheet title, tab name, jobs). Jobs taken out of the plan still get a table when
# they were done that year (แวคคั่มฟรีซเซอร์ is now done on demand).
MONTH_SHEETS = [("ตารางบันทึกการถ่ายน้ำมันเครื่องจักร", "ถ่ายน้ำมัน", ["oil"]),
                ("ตารางบันทึกการล้างคอนเดนเซอร์และฝาสูบ", "คอนเดนเซอร์ ฝาสูบ", ["condenser", "headwash"]),
                ("ตารางบันทึกการล้างฟรีซเซอร์ แวคคั่มฟรีซเซอร์ และล้างคูลลิ่ง", "ฟรีซเซอร์ คูลลิ่ง", ["freezer_wash", "freezer_vac", "cooling"]),
                ("ตารางบันทึกการโอเวอร์ฮอลและงานตรวจเช็ค", "โอเวอร์ฮอล ตรวจเช็ค", ["overhaul", "check12k", "belts", "grease", "safety", "screw_overhaul", "screw_grease"])]


# ------------------------------------------------------------------ helpers
def _font(bold: bool = False, size: int = 16) -> Font:
    return Font(name="Angsana New", size=size, bold=bold, color="000000")


def _cell(ws, r: int, c: int, v=None, *, bold: bool = False, left: bool = False):
    x = ws.cell(row=r, column=c, value=v)
    x.font = _font(bold=bold)
    x.alignment = Alignment(horizontal="left" if left else "center", vertical="center", wrap_text=True)
    x.border = _BOX
    return x


def _setup(ws, title: str, be_year: int, orientation: str = "landscape", one_page: bool = False) -> None:
    ws.page_setup.orientation = orientation
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1 if one_page else 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.5
    ws.page_margins.top = ws.page_margins.bottom = 0.6
    ws.cell(row=1, column=1, value=f"{title} พ.ศ. {be_year}").font = _font(bold=True, size=22)
    ws.cell(row=2, column=1, value=COMPANY).font = _font()


def _checker(ws, r: int) -> None:
    ws.cell(row=r + 2, column=1, value=CHECKER).font = _font()


def be(d) -> str:
    d = date.fromisoformat(str(d)[:10])
    return f"{d.day}/{d.month}/{str(d.year + 543)[2:]}"


def short_name(m: dict) -> str:
    """เครื่อง 1 -> หลอด 1, เครื่องบ่อซอง MYCOM 30 ตัน -> ซอง MYCOM (so the machine columns stay narrow)."""
    if m.get("kind") == "บ่อซอง":
        return m["name"].replace("เครื่องบ่อซอง ", "ซอง ").replace(" 30 ตัน", "")
    return m["name"].replace("เครื่อง ", "หลอด ")


def every_text(job: dict) -> str:
    parts = [f"{int(job['months'])} เดือน" if job.get("months") else "", f"{int(job['hours']):,} ชม." if job.get("hours") else ""]
    parts = [p for p in parts if p]
    text = ("ทุก " + " หรือ ".join(parts) + (" (อย่างใดถึงก่อน)" if len(parts) > 1 else "")) if parts else "-"
    return f"{text} ({ALONG[job['key']]})" if job["key"] in ALONG else text


def _machines(overview: dict) -> list[dict]:
    return sorted(overview["machines"], key=lambda m: (m.get("kind") == "บ่อซอง", m["name"]))


def _jobs_of(m: dict) -> list[dict]:
    return (m.get("plan") or {}).get("jobs") or []


def _to_bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ แผน PM (one page)
def pm_list(year: int, *, overview: Optional[dict] = None) -> bytes:
    overview = overview or machine_plan.overview()
    order = _machines(overview)
    job: dict = {}
    for m in order:
        for j in _jobs_of(m):
            job.setdefault(j["key"], j)
    wb = Workbook()
    ws = wb.active
    ws.title = "แผน PM"
    _setup(ws, "แผนการบำรุงรักษาเชิงป้องกัน (PM) เครื่องจักรผลิตน้ำแข็ง", year + 543, one_page=True)
    ncol = 4 + len(order)
    r = 4
    for c, h in enumerate(["ลำดับ", "งาน", "รายละเอียดงาน", "ความถี่", *[short_name(m) for m in order]], start=1):
        _cell(ws, r, c, h, bold=True)
    n = 0
    listed = set()
    groups = [*PM_GROUPS, ("งานอื่นๆ", [k for k in job if k not in {x for _, ks in PM_GROUPS for x in ks}])]     # a job added later still shows
    for title, keys in groups:
        keys = [k for k in keys if k in job and k not in listed]
        if not keys:
            continue
        r += 1
        for c in range(1, ncol + 1):
            _cell(ws, r, c)
        _cell(ws, r, 1, title, bold=True, left=True)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)
        for k in keys:
            listed.add(k)
            r += 1
            n += 1
            _cell(ws, r, 1, n)
            _cell(ws, r, 2, job[k]["name"], left=True)
            _cell(ws, r, 3, WHAT.get(k, ""), left=True)
            _cell(ws, r, 4, every_text(job[k]), left=True)
            for c, m in enumerate(order, start=5):
                _cell(ws, r, c, "●" if any(x["key"] == k for x in _jobs_of(m)) else "-")
            ws.row_dimensions[r].height = 46
    for col, w in zip("ABCD", (6, 30, 46, 30)):
        ws.column_dimensions[col].width = w
    for c in range(5, ncol + 1):
        ws.column_dimensions[get_column_letter(c)].width = 9
    r += 1
    ws.cell(row=r, column=1, value="● = เครื่องที่ต้องทำงานนี้   - = ไม่มีงานนี้   ·   หลอด = เครื่องผลิตน้ำแข็งหลอด   ซอง = เครื่องผลิตน้ำแข็งซอง   ·   ชม. = ชั่วโมงเดินเครื่อง (นับจากมิเตอร์)").font = _font(size=14)
    _checker(ws, r)
    return _to_bytes(wb)


# ------------------------------------------------------------------ รายงานประจำปี
def yearly_report(year: int, *, today: Optional[date] = None, overview: Optional[dict] = None, events: Optional[list[dict]] = None) -> bytes:
    today = today or date.today()
    be_year = year + 543
    overview = overview or machine_plan.overview(today)
    order = _machines(overview)
    names = {j["key"]: j["name"] for j in overview["jobs"]}
    if events is None:
        events = [e for e in supabase.select_all("machine_events", {"select": "id,machine_id,job_key,event_date,performed_by,vendor,summary,reason,notes",
                                                                    "event_date": f"gte.{year}-01-01", "order": "event_date.asc,id.asc"})
                  if str(e["event_date"]) < f"{year + 1}-01-01"]
    has_job = lambda m, k: any(x["key"] == k for x in _jobs_of(m))        # noqa: E731
    wb = Workbook()

    # 1. today's state of every job on every machine
    ws = wb.active
    ws.title = "สรุปสถานะ"
    _setup(ws, "สรุปสถานะการบำรุงรักษาเครื่องจักร", be_year)
    ws.cell(row=3, column=1, value=f"ข้อมูล ณ วันที่ {be(today)}").font = _font()
    keys = [j["key"] for j in overview["jobs"] if any(has_job(m, j["key"]) for m in order)]
    r = 5
    for c, h in enumerate(["งาน", "ทำทุก", *[short_name(m) for m in order]], start=1):
        _cell(ws, r, c, h, bold=True)
    for k in keys:
        r += 1
        j0 = next(x for m in order for x in _jobs_of(m) if x["key"] == k)
        every = " / ".join(p for p in (f"{int(j0['months'])} เดือน" if j0.get("months") else "", f"{int(j0['hours']):,} ชม." if j0.get("hours") else "") if p)
        _cell(ws, r, 1, names.get(k, k), left=True)
        _cell(ws, r, 2, every or "-")
        for c, m in enumerate(order, start=3):
            j = next((x for x in _jobs_of(m) if x["key"] == k), None)
            _cell(ws, r, c, "-" if not j else f"{be(j['last_date']) if j.get('last_date') else '-'}\n{STATE_TEXT.get(j['state'], '')}")
        ws.row_dimensions[r].height = 44
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 13
    for c in range(3, 3 + len(order)):
        ws.column_dimensions[get_column_letter(c)].width = 13
    r += 1
    ws.cell(row=r, column=1, value="ในช่อง = วันที่ทำล่าสุด และสถานะ  ·  - = เครื่องนี้ไม่มีงานนี้").font = _font(size=14)
    _checker(ws, r)

    # 2. month by month, one sheet per kind of job, two jobs per A4 page
    for title, tab, gkeys in MONTH_SHEETS:
        ws = wb.create_sheet(tab)
        _setup(ws, title, be_year, "portrait")
        r, blocks = 3, 0
        for k in gkeys:
            ms = [m for m in order if has_job(m, k) or any(e["machine_id"] == m["id"] and e.get("job_key") == k for e in events)]
            if not ms:
                continue
            if blocks and blocks % 2 == 0:
                ws.row_breaks.append(Break(id=r))
            blocks += 1
            r += 1
            ws.cell(row=r, column=1, value=names.get(k, k)).font = _font(bold=True, size=18)
            r += 1
            for c, h in enumerate(["เดือน", *[short_name(m) for m in ms], "ผู้ตรวจสอบ"], start=1):
                _cell(ws, r, c, h, bold=True)
            for mi, month in enumerate(MONTHS):
                r += 1
                _cell(ws, r, 1, month)
                for i, m in enumerate(ms, start=2):
                    done = [e for e in events if e["machine_id"] == m["id"] and e.get("job_key") == k and int(str(e["event_date"])[5:7]) == mi + 1]
                    _cell(ws, r, i, "\n".join(be(e["event_date"]) + (f" {e['performed_by']}" if e.get("performed_by") else "") for e in done) or None)
                _cell(ws, r, len(ms) + 2)
                ws.row_dimensions[r].height = 20
            r += 1
        if not blocks:
            ws.cell(row=4, column=1, value="ไม่มีงานในหมวดนี้").font = _font()
            r = 4
        ws.column_dimensions["A"].width = 13
        for i in range(2, 9):
            ws.column_dimensions[get_column_letter(i)].width = 10
        ws.column_dimensions["I"].width = 16
        ws.cell(row=r, column=1, value="ในช่อง = วันที่ทำ (วัน/เดือน/ปี พ.ศ.) และผู้ทำ").font = _font(size=14)
        _checker(ws, r)

    # 3. every report of the year
    ws = wb.create_sheet("รายการทั้งหมด")
    _setup(ws, "รายการซ่อมบำรุงเครื่องจักรทั้งปี", be_year, "portrait")
    short = {m["id"]: short_name(m) for m in order}
    r = 4
    for c, h in enumerate(["วันที่", "เครื่อง", "งาน", "รายละเอียด", "ผู้ทำ"], start=1):
        _cell(ws, r, c, h, bold=True)
    for e in events:
        r += 1
        detail = " · ".join(x for x in (e.get("reason"), e.get("notes")) if x and x != e.get("summary"))
        for c, v in enumerate([be(e["event_date"]), short.get(e["machine_id"], "?"), names.get(e.get("job_key")) or e.get("summary") or "ซ่อม",
                               detail or None, e.get("performed_by") or e.get("vendor") or None], start=1):
            _cell(ws, r, c, v, left=c in (3, 4, 5))
        ws.row_dimensions[r].height = 22
    if not events:
        r += 1
        _cell(ws, r, 1, "ไม่มีรายการในปีนี้", left=True)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
    for col, w in zip("ABCDE", (11, 12, 28, 38, 14)):
        ws.column_dimensions[col].width = w
    ws.print_title_rows = "4:4"
    _checker(ws, r)
    return _to_bytes(wb)
