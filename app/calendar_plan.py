"""
The manager's small calendar (ปฏิทิน on the board page): one visit per truck and per machine, on the day its OIL CHANGE is due.
Everything else rides along with that visit: each item that is overdue, or would come due before the NEXT oil change, is listed
under "ทำพร้อมกันเลย" -- nothing else gets a date of its own (anything urgent goes through แจ้งซ่อม). The grey never-done items of a
truck whose history is complete (and the machine jobs with no record) come along as "ถ้ามีโอกาส".

A truck or machine with no oil change on record has no date: it is listed grey, not counted as overdue (the history is still being
entered). The machine jobs that keep their own interval (ล้างคอนเดนเซอร์, โอเวอร์ฮอลสกรู) are entries of their own. The board's to-dos
with a due date are added by the page itself (it has them already).

The pure functions below are unit-tested; `calendar()` only gathers the two overviews.
"""
from datetime import date
from typing import Optional

from app import machine_plan, service_plan

SOON_DAYS = 14                       # yellow: within this many days (the same window as the board's "ล่วงหน้า")
SLACK_DAYS = 7                       # an item due within a week of the NEXT oil change waits for that one
OIL = "engine_oil"


def tone(days_left: Optional[int]) -> str:
    """late (red) / soon (yellow, within SOON_DAYS) / later (grey) / none (no date)."""
    if days_left is None:
        return "none"
    return "late" if days_left < 0 else "soon" if days_left <= SOON_DAYS else "later"


def truck_visit(row: dict, today: date) -> Optional[dict]:
    """One truck's next visit from its overview row (service_plan.overview()['vehicles'][i]); None when it has no plan or no oil item."""
    if not row.get("has_plan"):
        return None
    items = row.get("items") or []
    oil = next((i for i in items if i["key"] == OIL), None)
    if oil is None:
        return None
    day, left = oil.get("due_date"), oil.get("days_left")
    if oil.get("snoozed_until"):                          # snoozed: the day it was put off to
        day = oil["snoozed_until"]
        left = (date.fromisoformat(day) - today).days
    if oil["state"] == "unknown":
        day = left = None
    together, maybe = [], []
    every = (oil.get("interval_months") or 2) * service_plan.DAYS_PER_MONTH
    for it in items:
        if it["key"] == OIL:
            continue
        dl = it.get("days_left")
        if it["state"] == "overdue":
            together.append(it["label"])
        elif it["state"] == "unknown":
            if row.get("history_current_on"):             # green tick: no record = never done
                maybe.append(it["label"])
        elif left is not None and dl is not None and dl < left + every - SLACK_DAYS:
            together.append(it["label"])
    num, plate = str(row.get("vehicle_number") or "").strip(), str(row.get("plate_number") or "").strip()
    label = " · ".join(x for x in (f"#{num}" if num and num != plate else "", plate) if x) or "รถ"
    return {"kind": "truck", "id": row["id"], "label": label, "branch": row.get("branch"), "date": day, "days_left": left, "tone": tone(left),
            "main": oil["label"], "together": together if day else [], "maybe": maybe if day else [], "at_shop": bool(row.get("at_shop")),
            "green_tick": bool(row.get("history_current_on"))}


def machine_entries(machine: dict, today: date) -> list[dict]:
    """A machine's oil-change visit (with what rides along) and its own-date jobs, from machine_plan.overview()['machines'][i]."""
    plan = machine.get("plan") or {}
    names = {j["key"]: j["name"] for j in plan.get("jobs") or []}
    out = []
    v = plan.get("visit")
    if v:
        out.append({"kind": "machine", "id": machine["id"], "label": machine["name"], "branch": machine.get("branch"), "date": v["date"], "days_left": v["days_left"],
                    "tone": tone(v["days_left"]), "main": names.get("oil", "ถ่ายน้ำมันคอมเพรสเซอร์"), "green_tick": True,
                    "together": [names[k] for k in v["together"] if k in names] if v["date"] else [], "maybe": [names[k] for k in v["maybe"] if k in names] if v["date"] else []})
    for j in plan.get("jobs") or []:
        if j["key"] != "oil" and not j.get("with_oil") and j.get("due_date"):
            out.append({"kind": "machine", "id": machine["id"], "label": machine["name"], "branch": machine.get("branch"), "date": j["due_date"], "days_left": j["days_left"],
                        "tone": tone(j["days_left"]), "main": j["name"], "together": [], "maybe": []})
    return out


def build(truck_rows: list[dict], machines: list[dict], today: date) -> dict:
    entries = [e for e in (truck_visit(r, today) for r in truck_rows) if e]
    for m in machines:
        entries += machine_entries(m, today)
    dated = sorted((e for e in entries if e["date"]), key=lambda e: (e["date"], e["kind"], e["label"]))
    return {"today": today.isoformat(), "soon_days": SOON_DAYS, "entries": dated,
            "no_date": [{"kind": e["kind"], "id": e["id"], "label": e["label"], "branch": e.get("branch"), "main": e["main"], "green_tick": e.get("green_tick", False)}
                        for e in entries if not e["date"]]}


def calendar(today: Optional[date] = None) -> dict:
    today = today or service_plan.today_th()
    trucks = service_plan.overview(today=today)["vehicles"] if service_plan.has_v10() else []
    mo = machine_plan.overview(today)
    return build(trucks, mo["machines"] if mo.get("ready") else [], today)
