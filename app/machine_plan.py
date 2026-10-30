"""
The machine maintenance programme (ภาพรวมเครื่องจักร): for every machine, which planned jobs are overdue, coming
up, or have no record, counted from the maintenance reports (machine_events rows that carry a job_key).

A job repeats every N months, every N running hours, or both (whichever comes first). Running hours come from
the hour-meter readings (machine_readings) when there are some, otherwise from an assumed running pattern
(hours a day x days a week on the machine). The jobs and their defaults live here; the owner can change a job's
interval or level ("ทำเอง / ควรทำ / ผู้รับเหมา / ไม่ต้องทำ") on the page, which is kept in machine_job_settings.

Everything that decides a state is a pure function (job_status, machine_rate, ...) so it is unit-tested; the
database calls at the bottom are thin. Needs schema_v33.sql (machinery.has_v33()).
"""
import calendar
import math
from datetime import date, timedelta
from typing import Optional

from app import machinery, supabase
from app.machinery import InvalidMachinery, NotFound

LEVELS = ["ทำเอง", "ควรทำ", "ผู้รับเหมา", "ไม่ต้องทำ"]
SOON_DAYS = 30                       # "coming up" = due within this many days
MIN_RATE_SPAN_DAYS = 7               # two meter readings closer than this say nothing about the running rate
DEFAULT_RUN = {"เครื่อง": (24.0, 7.0), "บ่อซอง": (24.0, 6.0)}
STATE_RANK = {"never": 0, "late": 1, "soon": 2, "none": 3, "ok": 4}

# key, name, which kinds of machine, months, hours, level, event kind, other jobs that restart it, is it a guess, note
JOBS: list[dict] = [
    dict(key="overhaul", name="โอเวอร์ฮอลคอมเพรสเซอร์", kinds=("เครื่อง", "บ่อซอง"), months=None, hours=24000, level="ทำเอง", event_kind="เปลี่ยนอะไหล่", reset_by=(), guess=False,
         note="เปลี่ยนลิ้น สปริง แหวน ปะเก็น แบริ่งก้านสูบ · ตรวจลูกสูบ/แบริ่ง · ครบ 24,000 ชม. นับใหม่ (Patkol)"),
    dict(key="check12k", name="เช็ควาล์ว/แหวน (กึ่งทาง)", kinds=("เครื่อง", "บ่อซอง"), months=None, hours=12000, level="ควรทำ", event_kind="ตรวจเช็ค", reset_by=("overhaul",), guess=True,
         note="เปิดฝาดูแผ่นวาล์ว สปริง แหวน เปลี่ยนเฉพาะที่สึก"),
    dict(key="oil", name="ถ่ายน้ำมันคอมเพรสเซอร์", kinds=("เครื่อง", "บ่อซอง"), months=6, hours=4000, level="ทำเอง", event_kind="หล่อลื่น / จาระบี", reset_by=("overhaul",), guess=False,
         note="ล้างอ่างน้ำมัน ล้างตะแกรงน้ำมัน และกรองทางดูดไปพร้อมกัน"),
    dict(key="grease", name="จาระบีมอเตอร์คอมเพรสเซอร์", kinds=("เครื่อง", "บ่อซอง"), months=None, hours=4000, level="ทำเอง", event_kind="หล่อลื่น / จาระบี", reset_by=(), guess=False,
         note="ประมาณ 40 g ต่อตลับลูกปืน (2Sg315M4B: 6318 C3 ทั้งสองข้าง) — เครื่อง 3 / 4 และมอเตอร์ซองยังไม่ทราบเบอร์ลูกปืน"),
    dict(key="belts", name="เปลี่ยนสายพานคอมเพรสเซอร์ (ทั้งชุด)", kinds=("เครื่อง", "บ่อซอง"), months=None, hours=24000, level="ทำเอง", event_kind="เปลี่ยนอะไหล่", reset_by=(), guess=False,
         note="เปลี่ยนทั้งชุดพร้อมกัน ตึงใหม่หลัง 4–8 ชม."),
    dict(key="safety", name="ทดสอบสวิตช์ความปลอดภัย (HP / LP / แรงดันน้ำมัน)", kinds=("เครื่อง", "บ่อซอง"), months=12, hours=None, level="ควรทำ", event_kind="ตรวจเช็ค", reset_by=(), guess=True,
         note="สวิตช์ตัดเครื่องเมื่อความดันสูง/ต่ำ/น้ำมันต่ำ — ทดสอบให้ตัดจริงปีละครั้ง"),
    dict(key="cooling", name="ล้างคูลลิ่งทาวเวอร์", kinds=("เครื่อง", "บ่อซอง"), months=3, hours=None, level="ทำเอง", event_kind="ทำความสะอาด", reset_by=(), guess=False, note=""),
    dict(key="condenser", name="ล้างคอนเดนเซอร์", kinds=("เครื่อง", "บ่อซอง"), months=6, hours=None, level="ทำเอง", event_kind="ทำความสะอาด", reset_by=(), guess=False, note=""),
    dict(key="headwash", name="ล้างฝาสูบ", kinds=("เครื่อง", "บ่อซอง"), months=12, hours=None, level="ควรทำ", event_kind="ทำความสะอาด", reset_by=("overhaul",), guess=True,
         note="รอบยังไม่ได้กำหนด — ตั้งไว้ 12 เดือน · ทำพร้อมโอเวอร์ฮอลนับด้วย"),
    dict(key="freezer_wash", name="ล้างฟรีซ (วนน้ำยา)", kinds=("เครื่อง",), months=3, hours=None, level="ทำเอง", event_kind="ทำความสะอาด", reset_by=(), guess=False,
         note="เช็คตะกรันก่อน — ถ้าไม่มีตะกรัน เลื่อนได้"),
    dict(key="freezer_vac", name="แวคคั่มฟรีซ", kinds=("เครื่อง",), months=6, hours=None, level="ทำเอง", event_kind="ทำความสะอาด", reset_by=(), guess=False,
         note="ทำเมื่อน้ำแข็งต่อรอบน้อยลง ไม่เช่นนั้นตรวจทุก 6 เดือน"),
    dict(key="screw_grease", name="จาระบีมอเตอร์ / ลูกปืนสกรูและเครื่องบด", kinds=("เครื่อง",), months=3, hours=None, level="ทำเอง", event_kind="หล่อลื่น / จาระบี", reset_by=(), guess=True,
         note="เปิด-ปิดบ่อย ไม่มีมิเตอร์ จึงนับเป็นเดือน · เครื่องบดใช้ร่วมกัน (1-2, 3-4, 5): ทำแล้วบันทึกทุกเครื่องที่ใช้ร่วม"),
    dict(key="screw_overhaul", name="โอเวอร์ฮอลสกรูและเครื่องบด (ลูกปืน ซีล เกียร์)", kinds=("เครื่อง",), months=24, hours=None, level="ควรทำ", event_kind="เปลี่ยนอะไหล่", reset_by=(), guess=True,
         note="เปลี่ยนลูกปืนและซีล ตรวจเกียร์/ใบมีด · เร็วกว่านี้ถ้าเสียงดังหรือร้อน"),
]
JOB_BY_KEY = {j["key"]: j for j in JOBS}


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


def jobs_for(machine: dict, settings: dict[str, dict]) -> list[dict]:
    """The jobs that apply to this machine, with the owner's changes laid over the defaults. A job set to
    "ไม่ต้องทำ" is left out."""
    out = []
    for job in JOBS:
        if (machine.get("kind") or "เครื่อง") not in job["kinds"]:
            continue
        s = settings.get(job["key"]) or {}
        merged = dict(job)
        if s.get("level") in LEVELS:
            merged["level"] = s["level"]
        if s.get("interval_months") is not None:
            merged["months"] = float(s["interval_months"]) or None          # 0 = no interval of this kind
        if s.get("interval_hours") is not None:
            merged["hours"] = float(s["interval_hours"]) or None
        if merged["level"] == "ไม่ต้องทำ":
            continue
        out.append(merged)
    return out


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


def plan_for_machine(machine: dict, events: list[dict], readings: list[dict], settings: dict[str, dict], today: date) -> dict:
    rate = machine_rate(machine, readings)
    now = hours_now(readings, rate["per_day"], today)
    jobs = [job_status(j, machine, events, rate["per_day"], now["hours"] if now else None, today) for j in jobs_for(machine, settings)]
    jobs.sort(key=lambda j: (STATE_RANK[j["state"]], j["days_left"] if j["days_left"] is not None else 10**6, j["name"]))
    late = sum(1 for j in jobs if j["state"] in ("late", "never"))
    soon = sum(1 for j in jobs if j["state"] == "soon")
    none = sum(1 for j in jobs if j["state"] == "none")
    return {"jobs": jobs, "counts": {"late": late, "soon": soon, "none": none, "ok": sum(1 for j in jobs if j["state"] == "ok")},
            "next": next((j for j in jobs if j["state"] in ("never", "late", "soon")), None), "rate": rate, "hours_now": now}


# ---------------------------------------------------------------- database
def _settings() -> dict[str, dict]:
    if not machinery.has_v33():
        return {}
    return {r["job_key"]: r for r in supabase.select_all("machine_job_settings", {"select": "*", "order": "job_key.asc"})}


def _events() -> list[dict]:
    return supabase.select_all("machine_events", {"select": "id,machine_id,job_key,event_date,hours_at,summary,created_at", "job_key": "not.is.null", "order": "id.asc"})


def _readings() -> list[dict]:
    return supabase.select_all("machine_readings", {"select": "*", "order": "read_on.desc,created_at.desc,id.asc"})


def job_catalogue(settings: dict[str, dict]) -> list[dict]:
    """Every job with its current interval / level, for the settings box (including jobs set to "ไม่ต้องทำ")."""
    out = []
    for job in JOBS:
        s = settings.get(job["key"]) or {}
        out.append({"key": job["key"], "name": job["name"], "kinds": list(job["kinds"]), "guess": job["guess"], "note": job["note"],
                    "months": float(s["interval_months"]) if s.get("interval_months") is not None else job["months"],
                    "hours": float(s["interval_hours"]) if s.get("interval_hours") is not None else job["hours"],
                    "level": s["level"] if s.get("level") in LEVELS else job["level"], "default_level": job["level"]})
    return out


def overview(today: Optional[date] = None) -> dict:
    today = today or date.today()
    if not machinery.has_v9():
        return {"ready": False, "machines": [], "jobs": [], "today": today.isoformat()}
    v33 = machinery.has_v33()
    machines = machinery.list_machines(False)
    settings = _settings()
    events = _events() if v33 else []
    readings = _readings() if v33 else []
    rows = []
    for m in machines:
        mine_e = [e for e in events if e.get("machine_id") == m["id"]]
        mine_r = [r for r in readings if r["machine_id"] == m["id"]]
        plan = plan_for_machine(m, mine_e, mine_r, settings, today) if v33 else {"jobs": [], "counts": {"late": 0, "soon": 0, "none": 0, "ok": 0}, "next": None, "rate": None, "hours_now": None}
        rows.append({**m, "plan": plan})
    return {"ready": v33, "machines": rows, "jobs": job_catalogue(settings), "today": today.isoformat(), "levels": LEVELS}


def machine_plan(machine_id: str, today: Optional[date] = None) -> dict:
    today = today or date.today()
    machine = machinery.get_machine(machine_id)
    if not machinery.has_v33():
        return {"ready": False, "machine": machine, "plan": None, "readings": []}
    readings = supabase.select_all("machine_readings", {"select": "*", "machine_id": f"eq.{machine['id']}", "order": "read_on.desc,created_at.desc"})
    events = supabase.select_all("machine_events", {"select": "id,machine_id,job_key,event_date,hours_at,summary,created_at", "machine_id": f"eq.{machine['id']}",
                                                    "job_key": "not.is.null", "order": "id.asc"})
    return {"ready": True, "machine": machine, "plan": plan_for_machine(machine, events, readings, _settings(), today), "readings": readings[:8]}


def add_reading(machine_id: str, data: dict, today: Optional[date] = None) -> dict:
    today = today or date.today()
    if not machinery.has_v33():
        raise InvalidMachinery(machinery.NEEDS_V33)
    machine = machinery.get_machine(machine_id)
    raw = str(data.get("read_on") or "").strip()
    try:
        d = date.fromisoformat(raw) if raw else today
    except ValueError:
        raise InvalidMachinery("วันที่ไม่ถูกต้อง")
    if d.year >= 2400:
        raise InvalidMachinery(f"วันที่: ปี {d.year} ดูเหมือนเป็น พ.ศ. แต่ช่องนี้ต้องเป็น ค.ศ. (ปี {d.year - 543})")
    if d > today:
        raise InvalidMachinery("วันที่เป็นวันในอนาคต")
    hours = machinery._number(data.get("hours"), "ชั่วโมงที่มิเตอร์", maximum=1e7)
    if hours is None:
        raise InvalidMachinery("ใส่ชั่วโมงที่อ่านได้จากมิเตอร์ด้วย")
    earlier = supabase.select_all("machine_readings", {"select": "read_on,hours", "machine_id": f"eq.{machine['id']}", "order": "read_on.asc,created_at.asc"})
    for r in earlier:
        if r["read_on"] < d.isoformat() and float(r["hours"]) > hours:
            raise InvalidMachinery(f"มิเตอร์ต้องไม่น้อยกว่าครั้งก่อน ({float(r['hours']):g} ชม. เมื่อ {r['read_on']})")
        if r["read_on"] > d.isoformat() and float(r["hours"]) < hours:
            raise InvalidMachinery(f"มิเตอร์มากกว่าครั้งหลัง ({float(r['hours']):g} ชม. เมื่อ {r['read_on']})")
    notes = machinery._clean(data.get("notes"), 200) or None
    return {"reading_id": supabase.insert("machine_readings", [{"machine_id": machine["id"], "read_on": d.isoformat(), "hours": hours, "notes": notes}])[0]["id"]}


def delete_reading(reading_id: str) -> dict:
    rid = machinery._uuid_or_none(reading_id)
    if not machinery.has_v33() or not rid:
        raise NotFound()
    supabase.delete("machine_readings", {"id": f"eq.{rid}"})
    return {"ok": True}


def clean_setting(key: str, data: dict) -> dict:
    if key not in JOB_BY_KEY:
        raise NotFound()
    values: dict = {}
    for field, label in (("interval_months", "รอบ (เดือน)"), ("interval_hours", "รอบ (ชั่วโมง)")):
        if field in data:
            n = machinery._number(data.get(field), label, maximum=100000)
            values[field] = n if n else 0       # 0 = no interval of this kind
    if "level" in data:
        level = machinery._clean(data.get("level"), 20)
        if level not in LEVELS:
            raise InvalidMachinery(f"ระดับต้องเป็น {', '.join(LEVELS)}")
        values["level"] = level
    if not values:
        raise InvalidMachinery("ไม่มีอะไรให้บันทึก")
    final = {**{"interval_months": JOB_BY_KEY[key]["months"], "interval_hours": JOB_BY_KEY[key]["hours"]}, **values}
    if not final.get("interval_months") and not final.get("interval_hours") and values.get("level", "") != "ไม่ต้องทำ":
        raise InvalidMachinery("ต้องมีรอบอย่างน้อยหนึ่งอย่าง (เดือนหรือชั่วโมง) — หรือเลือก ‘ไม่ต้องทำ’")
    return values


def edit_job_setting(key: str, data: dict) -> dict:
    if not machinery.has_v33():
        raise InvalidMachinery(machinery.NEEDS_V33)
    values = clean_setting(key, data)
    supabase.insert("machine_job_settings", [{"job_key": key, **values, "updated_at": date.today().isoformat()}], on_conflict="job_key")
    return {"ok": True}
