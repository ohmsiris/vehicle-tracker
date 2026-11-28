"""
A wage line goes to the account of the PERSON it names (owner, 28 Nov 2026: "ตกหล่น ชัชวาลย์ อักษรกิ่ง 4 วัน" -- the surname was
right there, he is a driver, yet it was booked to ค่าแรงฝ่ายผลิต; and "ไปเอารถเบอร์ 9 กับเบอร์ 15 = สุทธิสาร-พิมพ์" is a driver's
trip money, ค่าแรงพนักงานขับรถ).

  drivers (พนักงานขับรถสาย / หน้าโรง) and เด็กรถ  -> 6200 ค่าแรงพนักงานขับรถ
  office (เสมียน...)                               -> 7100 เงินเดือนสำนักงาน
  everyone else (ผลิต, ช่าง, แพ็ค, ล้างกระสอบ ...)   -> 5400 ค่าแรงฝ่ายผลิต

Only for a line that IS a wage line: read as 5400 / 6200 / 7100, or as อื่นๆ (7900) with a wage word in it. A person is named by the
full name, or by a first name of 3+ letters that only one person has. Several people on one line of different accounts: left as read.
"""
import re
import threading
import time
from typing import Optional

WAGE_ACCOUNTS = {"5400", "6200", "7100"}
WAGE_WORDS = ("ค่าแรง", "ตกหล่น", "เบี้ยเลี้ยง", "ค่าเที่ยว", "ไปเอารถ", "ไปส่งรถ", "เบี้ยขยัน", "ค่าจ้าง", "ทำงานแทน")
DRIVER_POSITIONS = ("พนักงานขับรถสาย", "พนักงานขับรถหน้าโรง", "เด็กรถ")
TTL = 600.0
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "rows": None}


def account_of_position(position: Optional[str]) -> str:
    p = position or ""
    if p in DRIVER_POSITIONS or p.startswith("พนักงานขับรถ"):
        return "6200"
    if p.startswith("เสมียน"):
        return "7100"
    return "5400"


def people() -> list[dict]:
    """Everyone on the staff list (name, nickname, position, status), cached for TTL seconds; [] without a database."""
    from app import supabase
    if not supabase.configured():
        return []
    with _lock:
        if _cache["rows"] is not None and time.monotonic() - _cache["at"] < TTL:
            return _cache["rows"]
    try:
        rows = supabase.select_all("employees", {"select": "full_name,nickname,position,status", "order": "full_name.asc"})
    except Exception:                                  # noqa: BLE001 -- no staff list: lines stay as read
        return []
    with _lock:
        _cache.update(at=time.monotonic(), rows=rows)
    return rows


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def named(text: str, staff: list[dict]) -> list[dict]:
    """The people a line names: full names first; else unique first names (3+ letters) of people still working."""
    t = _squash(text)
    full = [p for p in staff if len(_squash(p.get("full_name"))) >= 4 and _squash(p.get("full_name")) in t]
    if full:
        return full
    firsts: dict = {}
    for p in staff:
        if p.get("status") == "resigned":
            continue
        f = (p.get("full_name") or "").split(" ")[0].strip()
        if len(f) >= 3:
            firsts.setdefault(f, []).append(p)
    return [ps[0] for f, ps in firsts.items() if len(ps) == 1 and f in t]


def account(text: str, acc: str, staff: list[dict]) -> str:
    """The account for this line: the named person's wage account when it is a wage line, else acc as it was."""
    if not staff or not (acc in WAGE_ACCOUNTS or (acc == "7900" and any(w in (text or "") for w in WAGE_WORDS))):
        return acc
    accs = {account_of_position(p.get("position")) for p in named(text, staff)}
    return accs.pop() if len(accs) == 1 else acc
