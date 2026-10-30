"""
The machine maintenance programme (แผนซ่อมบำรุงเครื่องจักร): for every machine, which planned jobs are overdue,
coming up, or have no record, counted from the maintenance reports (machine_events rows that carry a job_key).

Like the truck groups, every machine follows a PROGRAM: which jobs it gets, each job's interval (months, running
hours, or both -- whichever comes first) and its level (ทำเอง / ควรทำ / ผู้รับเหมา / ไม่ต้องทำ). Two programs are built in
(T = Tube for the หลอด machines, B = Block for the brine-pond machines: no freezer or screw jobs); the owner changes them, adds
his own, and picks each machine's program on the settings page (machine_programs, machines.program_code).

Running hours come from the hour-meter readings (machine_readings) when there are two a week apart, otherwise from an
assumed running pattern (hours a day x days a week on the machine).

Everything that decides a state is a pure function (job_status, build_chart, clean_program ...) so it is unit-tested;
the database calls at the bottom are thin. Needs schema_v33.sql and schema_v34.sql.
"""
import calendar
import math
import time
from datetime import date, timedelta
from typing import Optional

from app import machinery, supabase
from app.machinery import InvalidMachinery, NotFound

LEVELS = ["ทำเอง", "ควรทำ", "ผู้รับเหมา", "ไม่ต้องทำ"]
SOON_DAYS = 30                       # "coming up" = due within this many days
MIN_RATE_SPAN_DAYS = 7               # two meter readings closer than this say nothing about the running rate
DEFAULT_RUN = {"เครื่อง": (24.0, 7.0), "บ่อซอง": (24.0, 6.0)}
STATE_RANK = {"never": 0, "late": 1, "soon": 2, "none": 3, "ok": 4}
DAYS_PER_MONTH = 30.4375
CHART_ROUNDS = 8
NEEDS_V34 = "ต้องรัน schema_v34.sql ใน Supabase ก่อน จึงจะตั้งโปรแกรมของเครื่องจักรได้"

TUBE, SONG = "T", "B"
# key, name, the built-in programs that include it, months, hours, level, event kind, other jobs that restart it, is it a guess, note
JOBS: list[dict] = [
    dict(key="overhaul", name="โอเวอร์ฮอลคอมเพรสเซอร์", programs=(TUBE, SONG), months=None, hours=24000, level="ทำเอง", event_kind="เปลี่ยนอะไหล่", reset_by=(), guess=False,
         note="เปลี่ยนลิ้น สปริง แหวน ปะเก็น แบริ่งก้านสูบ · ตรวจลูกสูบ/แบริ่ง · ครบ 24,000 ชม. นับใหม่ (Patkol)"),
    dict(key="check12k", name="เช็ควาล์ว/แหวน (กึ่งทาง)", programs=(TUBE, SONG), months=None, hours=12000, level="ควรทำ", event_kind="ตรวจเช็ค", reset_by=("overhaul",), guess=True,
         note="เปิดฝาดูแผ่นวาล์ว สปริง แหวน เปลี่ยนเฉพาะที่สึก"),
    dict(key="oil", name="ถ่ายน้ำมันคอมเพรสเซอร์", programs=(TUBE, SONG), months=6, hours=4000, level="ทำเอง", event_kind="หล่อลื่น / จาระบี", reset_by=("overhaul",), guess=False,
         note="ล้างอ่างน้ำมัน ล้างตะแกรงน้ำมัน และกรองทางดูดไปพร้อมกัน"),
    dict(key="grease", name="จาระบีมอเตอร์คอมเพรสเซอร์", programs=(TUBE, SONG), months=None, hours=4000, level="ทำเอง", event_kind="หล่อลื่น / จาระบี", reset_by=(), guess=False,
         note="ประมาณ 40 g ต่อตลับลูกปืน (2Sg315M4B: 6318 C3 ทั้งสองข้าง) — เครื่อง 3 / 4 และมอเตอร์ซองยังไม่ทราบเบอร์ลูกปืน"),
    dict(key="belts", name="เปลี่ยนสายพานคอมเพรสเซอร์ (ทั้งชุด)", programs=(TUBE, SONG), months=None, hours=24000, level="ทำเอง", event_kind="เปลี่ยนอะไหล่", reset_by=(), guess=False,
         note="เปลี่ยนทั้งชุดพร้อมกัน ตึงใหม่หลัง 4–8 ชม."),
    dict(key="safety", name="ทดสอบสวิตช์ความปลอดภัย (HP / LP / แรงดันน้ำมัน)", programs=(TUBE, SONG), months=12, hours=None, level="ควรทำ", event_kind="ตรวจเช็ค", reset_by=(), guess=True,
         note="สวิตช์ตัดเครื่องเมื่อความดันสูง/ต่ำ/น้ำมันต่ำ — ทดสอบให้ตัดจริงปีละครั้ง"),
    dict(key="cooling", name="ล้างคูลลิ่งทาวเวอร์", programs=(TUBE, SONG), months=3, hours=None, level="ทำเอง", event_kind="ทำความสะอาด", reset_by=(), guess=False, note=""),
    dict(key="condenser", name="ล้างคอนเดนเซอร์", programs=(TUBE, SONG), months=6, hours=None, level="ทำเอง", event_kind="ทำความสะอาด", reset_by=(), guess=False, note=""),
    dict(key="headwash", name="ล้างฝาสูบ", programs=(TUBE, SONG), months=12, hours=None, level="ควรทำ", event_kind="ทำความสะอาด", reset_by=("overhaul",), guess=True,
         note="รอบยังไม่ได้กำหนด — ตั้งไว้ 12 เดือน · ทำพร้อมโอเวอร์ฮอลนับด้วย"),
    dict(key="freezer_wash", name="ล้างฟรีซ (วนน้ำยา)", programs=(TUBE,), months=3, hours=None, level="ทำเอง", event_kind="ทำความสะอาด", reset_by=(), guess=False,
         note="เช็คตะกรันก่อน — ถ้าไม่มีตะกรัน เลื่อนได้"),
    dict(key="freezer_vac", name="แวคคั่มฟรีซ", programs=(TUBE,), months=6, hours=None, level="ทำเอง", event_kind="ทำความสะอาด", reset_by=(), guess=False,
         note="ทำเมื่อน้ำแข็งต่อรอบน้อยลง ไม่เช่นนั้นตรวจทุก 6 เดือน"),
    dict(key="screw_grease", name="จาระบีมอเตอร์ / ลูกปืนสกรูและเครื่องบด", programs=(TUBE,), months=3, hours=None, level="ทำเอง", event_kind="หล่อลื่น / จาระบี", reset_by=(), guess=True,
         note="เปิด-ปิดบ่อย ไม่มีมิเตอร์ จึงนับเป็นเดือน · เครื่องบดใช้ร่วมกัน (1-2, 3-4, 5): ทำแล้วบันทึกทุกเครื่องที่ใช้ร่วม"),
    dict(key="screw_overhaul", name="โอเวอร์ฮอลสกรูและเครื่องบด (ลูกปืน ซีล เกียร์)", programs=(TUBE,), months=24, hours=None, level="ควรทำ", event_kind="เปลี่ยนอะไหล่", reset_by=(), guess=True,
         note="เปลี่ยนลูกปืนและซีล ตรวจเกียร์/ใบมีด · เร็วกว่านี้ถ้าเสียงดังหรือร้อน"),
]
JOB_BY_KEY = {j["key"]: j for j in JOBS}

DEFAULT_PROGRAMS = [
    {"code": TUBE, "label": "Tube — เครื่องน้ำแข็งหลอด", "step_months": 3, "sort_order": 1},
    {"code": SONG, "label": "Block — เครื่องบ่อซอง (คอมเพรสเซอร์ + ระบายความร้อน)", "step_months": 3, "sort_order": 2},
]
BUILTIN = {p["code"] for p in DEFAULT_PROGRAMS}
DEFAULT_FOR_KIND = {"เครื่อง": TUBE, "บ่อซอง": SONG}


# ------------------------------------------------------------------ programs
def default_job_settings(code: str) -> dict[str, dict]:
    """A built-in program's jobs as they come; a program the owner added starts with nothing switched on."""
    return {j["key"]: {"on": code in j["programs"], "months": j["months"], "hours": j["hours"], "level": j["level"]} for j in JOBS}


def merge_programs(rows: Optional[list[dict]]) -> dict[str, dict]:
    """code -> program, the saved row laid over the built-in defaults. Each program carries every job's settings."""
    out: dict[str, dict] = {}
    for p in DEFAULT_PROGRAMS:
        out[p["code"]] = {**p, "builtin": True, "customised": False, "jobs": default_job_settings(p["code"])}
    for r in rows or []:
        base = out.get(r["code"]) or {"code": r["code"], "builtin": False, "customised": True, "jobs": default_job_settings(r["code"]), "step_months": 3, "sort_order": 100}
        jobs = {k: dict(v) for k, v in base["jobs"].items()}
        for key, s in (r.get("jobs") or {}).items():
            if key in jobs and isinstance(s, dict):
                jobs[key].update({k: s[k] for k in ("on", "months", "hours", "level") if k in s})
        out[r["code"]] = {**base, "label": r.get("label") or base.get("label") or r["code"], "step_months": float(r["step_months"]) if r.get("step_months") else base["step_months"],
                          "sort_order": r.get("sort_order", base.get("sort_order", 100)), "customised": True, "jobs": jobs}
    return dict(sorted(out.items(), key=lambda kv: (kv[1].get("sort_order", 100), kv[0])))


def program_code_for(machine: dict, programs: dict[str, dict]) -> str:
    code = machine.get("program_code")
    if code and code in programs:
        return code
    return DEFAULT_FOR_KIND.get(machine.get("kind") or "เครื่อง", TUBE)


def jobs_for(program: dict) -> list[dict]:
    """The jobs a machine on this program gets (switched on, not ไม่ต้องทำ, with some interval), in the catalogue order."""
    out = []
    for job in JOBS:
        s = program["jobs"].get(job["key"]) or {}
        if not s.get("on") or s.get("level") == "ไม่ต้องทำ":
            continue
        months = float(s["months"]) if s.get("months") else None
        hours = float(s["hours"]) if s.get("hours") else None
        if not months and not hours:
            continue
        out.append({**job, "months": months, "hours": hours, "level": s.get("level") if s.get("level") in LEVELS else job["level"]})
    return out


# ------------------------------------------------------------------ pure rules
def add_months(d: date, months: float) -> date:
    n = int(round(months))
    total = d.year * 12 + (d.month - 1) + n
    year, month = divmod(total, 12)
    return date(year, month + 1, min(d.day, calendar.monthrange(year, month + 1)[1]))


def _as_date(value) -> Optional[date]:
    if not value:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def machine_rate(machine: dict, readings: list[dict]) -> dict:
    """Running hours per calendar day: worked out from the hour-meter readings when two of them are at least a
    week apart, otherwise the assumed pattern (hours a day x days a week / 7)."""
    pts = sorted(((_as_date(r["read_on"]), float(r["hours"])) for r in readings if r.get("read_on") is not None and r.get("hours") is not None))
    if len(pts) >= 2:
        last_d, last_h = pts[-1]
        for d, h in reversed(pts[:-1]):
            if (last_d - d).days >= MIN_RATE_SPAN_DAYS:
                if last_h > h:
                    return {"per_day": (last_h - h) / (last_d - d).days, "source": "มิเตอร์"}
                break
    hpd, dpw = DEFAULT_RUN.get(machine.get("kind") or "เครื่อง", (24.0, 7.0))
    if machine.get("hours_per_day"):
        hpd = float(machine["hours_per_day"])
    if machine.get("days_per_week"):
        dpw = float(machine["days_per_week"])
    return {"per_day": hpd * dpw / 7.0, "source": "สมมติ", "assumed": f"{hpd:g} ชม. × {dpw:g} วัน/สัปดาห์"}


def hours_now(readings: list[dict], rate_per_day: float, today: date) -> Optional[dict]:
    """The hour meter today: the latest reading plus the running since."""
    pts = [(_as_date(r["read_on"]), float(r["hours"])) for r in readings if r.get("read_on") is not None and r.get("hours") is not None]
    if not pts:
        return None
    d, h = max(pts)
    days = max(0, (today - d).days)
    return {"hours": h + days * rate_per_day, "read_on": d.isoformat(), "reading": h, "estimated": days > 0}


def interval_days(job: dict, rate_per_day: float) -> Optional[float]:
    """How many calendar days one interval of the job lasts (months or hours, whichever is shorter)."""
    c = []
    if job.get("months"):
        c.append(job["months"] * DAYS_PER_MONTH)
    if job.get("hours") and rate_per_day > 0:
        c.append(job["hours"] / rate_per_day)
    return min(c) if c else None


def job_status(job: dict, machine: dict, events: list[dict], rate_per_day: float, now_hours: Optional[float], today: date) -> dict:
    """One job on one machine: when it was last done, when it is due and which state that makes.
    states: never (known never done) · late · soon (due within SOON_DAYS) · none (no record) · ok."""
    keys = {job["key"], *job.get("reset_by", ())}
    mine = [e for e in events if e.get("job_key") in keys and e.get("event_date")]
    last = max(mine, key=lambda e: (e["event_date"], e.get("created_at") or "")) if mine else None
    out = {"key": job["key"], "name": job["name"], "level": job["level"], "months": job["months"], "hours": job["hours"], "guess": job["guess"],
           "note": job["note"], "event_kind": job["event_kind"], "last_date": None, "last_job": None, "due_date": None, "days_left": None,
           "hours_since": None, "hours_estimated": False, "state": "none"}
    if last is None:
        if job["key"] == "overhaul" and machine.get("never_overhauled"):
            out["state"] = "never"
            if now_hours is not None and job["hours"]:
                out.update(hours_since=round(now_hours), hours_estimated=True)
        return out
    last_d = _as_date(last["event_date"])
    out.update(last_date=last_d.isoformat(), last_note=last.get("summary"), last_job=last.get("job_key"))
    due: list[date] = []
    if job["months"]:
        due.append(add_months(last_d, job["months"]))
    if job["hours"] and rate_per_day > 0:
        if last.get("hours_at") is not None and now_hours is not None:
            since, estimated = now_hours - float(last["hours_at"]), False
        else:
            since, estimated = (today - last_d).days * rate_per_day, True
        due.append(today + timedelta(days=math.floor((job["hours"] - since) / rate_per_day)))
        out.update(hours_since=round(since), hours_estimated=estimated)
    if not due:
        out["state"] = "ok"
        return out
    d = min(due)
    left = (d - today).days
    out.update(due_date=d.isoformat(), days_left=left, state="late" if left < 0 else "soon" if left <= SOON_DAYS else "ok")
    return out


def build_chart(statuses: list[dict], jobs: list[dict], rate_per_day: float, step_months: float, today: date, rounds: int = CHART_ROUNDS) -> dict:
    """The truck-style chart for one machine. Columns: "ตอนนี้", then the next rounds, `step_months` apart. Rows: the jobs.
    Column 0 says how the job stands today: done (within the last round), overdue / never done, or no record. The later
    columns put a dot in the round each job falls due -- counted from its due date, or from today when it is overdue or
    has no record (as if it were done now) -- and again every interval after that."""
    step_days = step_months * DAYS_PER_MONTH
    ends = [today + timedelta(days=round(n * step_days)) for n in range(1, rounds + 1)]
    columns = [{"n": 0, "label": "ตอนนี้", "date": today.isoformat()}] + \
              [{"n": n, "label": "รอบถัดไป" if n == 1 else f"อีก {n} รอบ", "date": e.isoformat()} for n, e in enumerate(ends, 1)]
    by_key = {j["key"]: j for j in jobs}
    rows = []
    for s in statuses:
        job = by_key[s["key"]]
        cells: list[Optional[str]] = [None] * (rounds + 1)
        last = _as_date(s["last_date"])
        if s["state"] in ("late", "never"):
            cells[0] = "overdue" if s["state"] == "late" else "never"
        elif s["state"] == "none":
            cells[0] = "unknown"
        elif last and (today - last).days <= step_days:
            cells[0] = "done"
        every = interval_days(job, rate_per_day)
        if every:
            # counted from the due date, or from today (as if done now); no rounding carried over, so a 6-month job sits on every 2nd 3-month round
            first = _as_date(s["due_date"]) if s["state"] in ("ok", "soon") else None
            base, k = (first, 0) if first else (today, 1)
            while True:
                nxt = base + timedelta(days=round(k * every))
                if nxt > ends[-1]:
                    break
                n = next(i for i, e in enumerate(ends, 1) if e >= nxt)
                cells[n] = cells[n] or "due"
                k += 1
        rows.append({"key": s["key"], "name": s["name"], "months": s["months"], "hours": s["hours"], "level": s["level"], "state": s["state"],
                     "last_date": s["last_date"], "last_job": s["last_job"], "due_date": s["due_date"], "days_left": s["days_left"], "cells": cells})
    return {"columns": columns, "rows": rows, "step_months": step_months}


def plan_for_machine(machine: dict, program: dict, events: list[dict], readings: list[dict], today: date, *, with_chart: bool = False) -> dict:
    rate = machine_rate(machine, readings)
    now = hours_now(readings, rate["per_day"], today)
    jobs = jobs_for(program)
    statuses = [job_status(j, machine, events, rate["per_day"], now["hours"] if now else None, today) for j in jobs]
    ordered = sorted(statuses, key=lambda j: (STATE_RANK[j["state"]], j["days_left"] if j["days_left"] is not None else 10**6, j["name"]))
    late = sum(1 for j in statuses if j["state"] in ("late", "never"))
    out = {"jobs": ordered, "counts": {"late": late, "soon": sum(1 for j in statuses if j["state"] == "soon"),
                                       "none": sum(1 for j in statuses if j["state"] == "none"), "ok": sum(1 for j in statuses if j["state"] == "ok")},
           "next": next((j for j in ordered if j["state"] in ("never", "late", "soon")), None), "rate": rate, "hours_now": now,
           "program": {"code": program["code"], "label": program["label"], "step_months": program["step_months"]}}
    if with_chart:
        out["chart"] = build_chart(statuses, jobs, rate["per_day"], program["step_months"], today)
    return out


def clean_program(data: dict, current: Optional[dict]) -> dict:
    """A program saved from the settings page: its name, round length and each job's on/off, months, hours and level."""
    values: dict = {}
    if "label" in data or current is None:
        label = machinery._clean(data.get("label"), 60)
        if not label:
            raise InvalidMachinery("ตั้งชื่อโปรแกรมด้วย")
        values["label"] = label
    if "step_months" in data:
        step = machinery._number(data.get("step_months"), "ความยาวรอบในตาราง (เดือน)", maximum=12)
        if not step or step < 1:
            raise InvalidMachinery("ความยาวรอบในตารางต้อง 1–12 เดือน")
        values["step_months"] = step
    if "jobs" in data:
        if not isinstance(data["jobs"], dict):
            raise InvalidMachinery("ข้อมูลงานไม่ถูกต้อง")
        jobs = {}
        for key, s in data["jobs"].items():
            if key not in JOB_BY_KEY or not isinstance(s, dict):
                raise InvalidMachinery("ไม่รู้จักงานนี้")
            name = JOB_BY_KEY[key]["name"]
            months = machinery._number(s.get("months"), f"{name}: เดือน", maximum=240)
            hours = machinery._number(s.get("hours"), f"{name}: ชั่วโมง", maximum=200000)
            level = machinery._clean(s.get("level"), 20) or JOB_BY_KEY[key]["level"]
            if level not in LEVELS:
                raise InvalidMachinery(f"{name}: ระดับต้องเป็น {', '.join(LEVELS)}")
            on = bool(s.get("on"))
            if on and level != "ไม่ต้องทำ" and not months and not hours:
                raise InvalidMachinery(f"{name}: ใส่รอบเป็นเดือนหรือชั่วโมงอย่างน้อยหนึ่งอย่าง (หรือเอาติ๊กออก)")
            jobs[key] = {"on": on, "months": months or None, "hours": hours or None, "level": level}
        values["jobs"] = jobs
    if not values:
        raise InvalidMachinery("ไม่มีอะไรให้บันทึก")
    return values


def clean_jobs_done(data: dict, program_jobs: list[dict], *, today: date) -> dict:
    """"บันทึกว่าทำแล้ว" for one or more jobs at once."""
    raw = machinery._clean(data.get("done_date"), 20)
    try:
        d = date.fromisoformat(raw) if raw else None
    except ValueError:
        d = None
    if not d:
        raise InvalidMachinery("ใส่วันที่ทำด้วย")
    if d.year >= 2400:
        raise InvalidMachinery(f"วันที่: ปี {d.year} ดูเหมือนเป็น พ.ศ. แต่ช่องนี้ต้องเป็น ค.ศ. (ปี {d.year - 543})")
    if d > today:
        raise InvalidMachinery("วันที่เป็นวันในอนาคต")
    known = {j["key"] for j in program_jobs} | set(JOB_BY_KEY)
    keys = []
    for k in data.get("job_keys") or []:
        if k not in known:
            raise InvalidMachinery("ไม่รู้จักงานนี้")
        if k not in keys:
            keys.append(k)
    if not keys:
        raise InvalidMachinery("เลือกงานที่ทำอย่างน้อยหนึ่งงาน")
    return {"done_date": d.isoformat(), "job_keys": keys, "performed_by": machinery._clean(data.get("performed_by"), 100) or None,
            "cost": machinery._number(data.get("cost"), "ค่าใช้จ่าย", maximum=1e8), "hours_at": machinery._number(data.get("hours_at"), "ชั่วโมงที่มิเตอร์", maximum=1e7),
            "notes": machinery._clean(data.get("notes"), 500) or None}


# ---------------------------------------------------------------- database
_probe34 = {"at": 0.0, "ok": False}


def has_v34() -> bool:
    now = time.time()
    if now - _probe34["at"] < 30:
        return _probe34["ok"]
    try:
        supabase.select("machine_programs", {"select": "code", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe34.update(at=now, ok=ok)
    return ok


def _programs() -> dict[str, dict]:
    rows = supabase.select_all("machine_programs", {"select": "*", "order": "sort_order.asc,code.asc"}) if has_v34() else []
    return merge_programs(rows)


def _events(machine_id: Optional[str] = None) -> list[dict]:
    params = {"select": "id,machine_id,job_key,event_date,hours_at,summary,created_at", "job_key": "not.is.null", "order": "id.asc"}
    if machine_id:
        params["machine_id"] = f"eq.{machine_id}"
    return supabase.select_all("machine_events", params)


def _readings(machine_id: Optional[str] = None) -> list[dict]:
    params = {"select": "*", "order": "read_on.desc,created_at.desc,id.asc"}
    if machine_id:
        params["machine_id"] = f"eq.{machine_id}"
    return supabase.select_all("machine_readings", params)


def overview(today: Optional[date] = None) -> dict:
    today = today or date.today()
    if not machinery.has_v9():
        return {"ready": False, "machines": [], "jobs": [], "today": today.isoformat()}
    if not machinery.has_v33():
        return {"ready": False, "machines": [{**m, "plan": None} for m in machinery.list_machines(False)], "jobs": [], "today": today.isoformat()}
    programs = _programs()
    events, readings = _events(), _readings()
    rows = []
    for m in machinery.list_machines(False):
        program = programs[program_code_for(m, programs)]
        plan = plan_for_machine(m, program, [e for e in events if e.get("machine_id") == m["id"]], [r for r in readings if r["machine_id"] == m["id"]], today)
        rows.append({**m, "plan": plan})
    return {"ready": True, "machines": rows, "jobs": [{"key": j["key"], "name": j["name"]} for j in JOBS], "today": today.isoformat()}


def machine_plan(machine_id: str, today: Optional[date] = None) -> dict:
    today = today or date.today()
    machine = machinery.get_machine(machine_id)
    if not machinery.has_v33():
        return {"ready": False, "machine": machine, "plan": None, "readings": []}
    programs = _programs()
    program = programs[program_code_for(machine, programs)]
    readings = _readings(machine["id"])
    plan = plan_for_machine(machine, program, _events(machine["id"]), readings, today, with_chart=True)
    return {"ready": True, "machine": machine, "plan": plan, "readings": readings[:8]}


def programs_view() -> dict:
    """The settings page: every program with every job's settings and its defaults, and which machine follows which."""
    programs = _programs()
    machines = machinery.list_machines(False)
    used: dict[str, int] = {}
    rows = []
    for m in machines:
        code = program_code_for(m, programs)
        used[code] = used.get(code, 0) + 1
        rows.append({"id": m["id"], "name": m["name"], "kind": m["kind"], "branch": m["branch"], "program_code": code})
    out = []
    for p in programs.values():
        defaults = default_job_settings(p["code"]) if p["builtin"] else None
        out.append({"code": p["code"], "label": p["label"], "step_months": p["step_months"], "builtin": p["builtin"], "customised": p["customised"],
                    "machines": used.get(p["code"], 0),
                    "jobs": [{"key": j["key"], "name": j["name"], "note": j["note"], "guess": j["guess"], **p["jobs"][j["key"]],
                              "default": defaults[j["key"]] if defaults else None} for j in JOBS]})
    return {"ready": has_v34(), "programs": out, "machines": rows, "levels": LEVELS}


def _require_v34() -> None:
    if not machinery.has_v33() or not has_v34():
        raise InvalidMachinery(NEEDS_V34)


def save_program(code: str, data: dict) -> dict:
    _require_v34()
    programs = _programs()
    if code not in programs:
        raise NotFound()
    current = programs[code]
    values = clean_program(data, current)
    row = {"code": code, "label": values.get("label", current["label"]), "step_months": values.get("step_months", current["step_months"]),
           "jobs": values.get("jobs", current["jobs"]), "sort_order": current.get("sort_order", 100)}
    supabase.insert("machine_programs", [row], on_conflict="code")
    return programs_view()


def add_program(data: dict) -> dict:
    _require_v34()
    programs = _programs()
    label = machinery._clean(data.get("label"), 60)
    if not label:
        raise InvalidMachinery("ตั้งชื่อโปรแกรมด้วย")
    source = programs.get(machinery._clean(data.get("copy_from"), 20)) or programs[TUBE]
    n = 1
    while f"P{n}" in programs:
        n += 1
    supabase.insert("machine_programs", [{"code": f"P{n}", "label": label, "step_months": source["step_months"], "jobs": source["jobs"],
                                          "sort_order": 10 + n}])
    return programs_view()


def delete_program(code: str) -> dict:
    """A program the owner added goes away (when no machine follows it); a built-in one goes back to its defaults."""
    _require_v34()
    programs = _programs()
    if code not in programs:
        raise NotFound()
    if not programs[code]["builtin"]:
        users = [m["name"] for m in machinery.list_machines(True) if m.get("program_code") == code]
        if users:
            raise InvalidMachinery(f"ยังมีเครื่องที่ใช้โปรแกรมนี้: {', '.join(users)} — เปลี่ยนโปรแกรมของเครื่องเหล่านั้นก่อน")
    supabase.delete("machine_programs", {"code": f"eq.{code}"})
    return programs_view()


def assign_programs(data: dict) -> dict:
    _require_v34()
    programs = _programs()
    machines = {m["id"]: m for m in machinery.list_machines(True)}
    changes = []
    for c in data.get("changes") or []:
        mid = machinery._uuid_or_none(c.get("machine_id"))
        code = machinery._clean(c.get("program_code"), 20)
        if mid not in machines:
            raise InvalidMachinery("ไม่พบเครื่องที่เลือก")
        if code not in programs:
            raise InvalidMachinery("ไม่พบโปรแกรมที่เลือก")
        changes.append((mid, code))
    if not changes:
        raise InvalidMachinery("ไม่มีอะไรให้บันทึก")
    for mid, code in changes:
        supabase.update("machines", {"id": f"eq.{mid}"}, {"program_code": code})
    return programs_view()


def jobs_done(machine_id: str, data: dict, today: Optional[date] = None) -> dict:
    """One maintenance report per job ticked; the cost goes on the first one (the notes say it covers them all)."""
    today = today or date.today()
    if not machinery.has_v33():
        raise InvalidMachinery(machinery.NEEDS_V33)
    machine = machinery.get_machine(machine_id)
    programs = _programs()
    clean = clean_jobs_done(data, jobs_for(programs[program_code_for(machine, programs)]), today=today)
    ids = []
    many = len(clean["job_keys"]) > 1
    for i, key in enumerate(clean["job_keys"]):
        job = JOB_BY_KEY[key]
        notes = clean["notes"]
        if many and clean["cost"] and i == 0:
            notes = " · ".join(filter(None, [notes, f"ค่าใช้จ่ายรวมทุกงานที่ทำวันนี้ ({len(clean['job_keys'])} งาน)"]))
        res = machinery.create_event({"machine_id": machine["id"], "event_date": clean["done_date"], "kind": job["event_kind"], "summary": job["name"],
                                      "job_key": key, "hours_at": clean["hours_at"], "performed_by": clean["performed_by"],
                                      "cost": clean["cost"] if i == 0 else None, "notes": notes})
        ids.append(res["event_id"])
    return {"event_ids": ids}
