"""
ตรวจเอกสารก่อนลงบัญชี › บันทึกในพนักงาน (owner, 28 Nov 2026): "the end-of-month paper has HR pages and staff loans -- record them STRAIGHT
AWAY from the check page, seeing before each write who it goes to, what will change and what is already there".

* The items live in the file's preview: preview["hr_items"], one per thing to record --
    {id, page, source "page" | "line", line_key, kind, employee_code, employee_name_read, code_read, match, fields, summary,
     status pending | applied | dismissed, result, applied_at, applied_by}
  kinds: warning (หนังสือเตือน), resign (ลาออก), loan (เงินกู้ -- a memo, or a 1510 line of the expense list), loan_terms (เปลี่ยนการผ่อน),
  role_change (เปลี่ยนตำแหน่ง, with the new daily rate). They ride along like preview["line_edits"]: a choice, อ่านใหม่ and บันทึกการแก้
  keep them (inbox_ai.apply_choices); a fresh paid AI read makes new ones.
* Made by ONE text-only Claude call (read_items: no page images -- what the page reads already say: the memo pages' title / date /
  summary / person / codes, and the 1510 lines' text, amount, date, key), at the end of a paid read of an expense paper (inbox_ai.run)
  or on the owner's click for a file read before this (read_file, POST /api/accounts/inbox/<id>/hr/read). Never in from_pages /
  apply_choices: those stay free and offline. The person is then found IN CODE (resolve): the staff code first, else the name
  (app/payroll._consonants, Thai-tolerant); nobody sure -> employee_code None and the page asks to pick.
* What is there now is worked out on every GET, never stored (view / state_of): resign -> still working / resigned on which day (the
  same day: "มีแล้ว ตรงกัน", nothing to write); warning -> the warnings so far and the level this one would be; loan -> the active
  loans (one of the same amount, within 1 baht, is "มีแล้ว" and cannot be recorded again); loan_terms -> the loan it changes;
  role_change -> position, branch, daily rate (employee_rates, what payroll reads), pay group and route_until.
* Recorded only by the item's own buttons (apply / dismiss / reopen), never by ลงบัญชี; the writes are hr_suggest.write_* (the same
  as พนักงาน › จากเอกสารสิ้นเดือน), and an applied / dismissed row is left in hr_suggestions so that list's history shows it.

Pure parts (sources, resolve, build_items, clean_fields, state_of, merge, carried) are unit-tested in tests/test_inbox_hr.py with a
fake model and an in-memory database; nothing here is ever tested against the live database.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Optional

from app import supabase

logger = logging.getLogger("vehicle_tracker.inbox_hr")

KINDS = ("warning", "resign", "loan", "loan_terms", "role_change")
KIND_TH = {"warning": "หนังสือเตือน", "resign": "ลาออก", "loan": "เงินกู้", "loan_terms": "เปลี่ยนการผ่อน", "role_change": "เปลี่ยนตำแหน่ง"}
LOAN_KINDS = ("loan", "advance", "accident", "other")
HR_PAGE_KINDS = ("memo", "other")                # the AI's page_kind for memos, warnings, resignations (pay lists are "payslip")
LOAN_ACCOUNT = "1510"                            # เงินกู้พนักงาน on the expense list
CODE_RE = re.compile(r"SB\s*-?\s*(FR|PD|KK)\s*-?\s*(\d{3})(?!\d)", re.I)   # never the first 3 digits of a longer number
EMP_COLS = "id,code,prefix,gender,full_name,nickname,branch,position,status,start_date,end_date,end_reason,driver_id"
MAX_OUTPUT_TOKENS = 16000                    # thinking and the JSON share it; 8000 was overrun on the page reads (inbox_ai)
MAX_PARSE_ATTEMPTS = 2
EFFORT = "high"
NAME_SURE = 0.75                                 # a name match at least this good ...
NAME_MARGIN = 0.08                               # ... and this much better than the next person
MAX_PENALTY = 100_000
MAX_LOAN = 1_000_000
MAX_RATE = 5000
SAME_LOAN_BAHT = 1.0

FIELDS = {
    "warning": ("doc_date", "offence_date", "offence", "punishment", "penalty_amount", "level"),
    "resign": ("end_date", "reason"),
    "loan": ("loan_kind", "amount", "loan_date", "repay_amount", "first_month", "reason"),
    "loan_terms": ("repay_amount", "first_month"),
    "role_change": ("change_date", "to_position", "to_branch", "daily_rate", "reason"),
}
NO_ACCESS = "บันทึกเรื่องพนักงานได้เฉพาะผู้ที่มีเมนูรายชื่อพนักงาน (รวมค่าจ้าง)"


class HrReadError(RuntimeError):
    """str() is Thai; usage = what the failed call(s) still cost."""
    usage: Optional[dict] = None


def _inbox():
    from app import inbox
    return inbox


def _err(msg: str):
    return _inbox().InboxError(msg)


# ------------------------------------------------------------------ what the call reads (text only)
def sources(preview: dict) -> dict:
    """{"pages": the memo / HR pages as the page reads have them, "lines": the 1510 staff-loan lines of the expense list}."""
    pages = []
    for r in preview.get("ai_pages") or []:
        if r.get("page_kind") not in HR_PAGE_KINDS or not (r.get("summary") or r.get("title") or r.get("person")):
            continue
        pages.append({"page": r.get("n"), "title": r.get("title") or "", "date": r.get("date_from") or "", "summary": r.get("summary") or "",
                      "person": r.get("person") or "", "employee_codes": [str(c) for c in r.get("employee_codes") or []]})
    lines = []
    for row in ((preview.get("batches") or [{}])[0] or {}).get("rows") or []:
        if row.get("account") == LOAN_ACCOUNT and row.get("dest_kind") == "book" and row.get("key"):
            lines.append({"key": row["key"], "page": row.get("page"), "date": row.get("date") or "", "text": row.get("text") or "",
                          "amount": row.get("amount")})
    return {"pages": pages, "lines": lines}


_ITEM = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "source": {"type": "string", "enum": ["page", "line"]},
        "page": {"type": "integer", "description": "the page number given (for a line: its page)"},
        "line_key": {"type": "string", "description": "a line: its key exactly as given (e.g. p3:l12); a page: ''"},
        "kind": {"type": "string", "enum": list(KINDS)},
        "employee_code": {"type": "string", "description": "the staff code as written (SB-FR024 ...); '' when none is written"},
        "employee_name": {"type": "string", "description": "the person's name as written, without นาย / นาง / นางสาว"},
        "summary": {"type": "string", "description": "one short Thai line: what is to be recorded"},
        "doc_date": {"type": "string", "description": "warning: the letter's date YYYY-MM-DD; else ''"},
        "offence_date": {"type": "string", "description": "warning: the day of the offence YYYY-MM-DD when written; else ''"},
        "offence": {"type": "string", "description": "warning: what was done, short Thai; else ''"},
        "punishment": {"type": "string", "description": "warning: the punishment as written (e.g. ไม่อนุมัติเบี้ยขยัน, หักเงิน 1,000 บาท); else ''"},
        "penalty_amount": {"type": "number", "description": "warning: baht deducted as a fine; 0 when none"},
        "level": {"type": "integer", "description": "warning: ครั้งที่ written on the letter (1-3); 0 when not written"},
        "end_date": {"type": "string", "description": "resign: the last working day YYYY-MM-DD; else ''"},
        "reason": {"type": "string", "description": "resign / loan / role change: the reason, short Thai; else ''"},
        "loan_kind": {"type": "string", "enum": list(LOAN_KINDS), "description": "loan: loan = กู้ยืม, advance, accident = ค่าเสียหาย, other"},
        "amount": {"type": "number", "description": "loan: the amount lent; 0 when not a loan"},
        "loan_date": {"type": "string", "description": "loan: the day it was lent / approved YYYY-MM-DD; else ''"},
        "repay_amount": {"type": "number", "description": "loan / loan_terms: baht deducted per month; 0 when not written"},
        "first_month": {"type": "string", "description": "loan / loan_terms: first month deducted YYYY-MM-01 when written; else ''"},
        "change_date": {"type": "string", "description": "role_change: the day it takes effect (มีผล) YYYY-MM-DD; else ''"},
        "to_position": {"type": "string", "enum": [], "description": "role_change: the new position; '' otherwise"},
        "to_branch": {"type": "string", "enum": [], "description": "role_change: the new branch when it changes; else ''"},
        "daily_rate": {"type": "number", "description": "role_change: the new daily wage in baht; 0 when not written"},
    },
    "required": ["source", "page", "line_key", "kind", "employee_code", "employee_name", "summary", "doc_date", "offence_date", "offence",
                 "punishment", "penalty_amount", "level", "end_date", "reason", "loan_kind", "amount", "loan_date", "repay_amount",
                 "first_month", "change_date", "to_position", "to_branch", "daily_rate"],
}

PROMPT = """You turn what was read from a Thai ice factory's end-of-month papers (company สระบุรีวารีเทพ, branches สระบุรี and แก่งคอย) into
the staff records the owner has to make. You get JSON: "pages" (HR / memo pages: title, date, summary, person, employee codes) and
"lines" (expense-list lines booked as a staff loan, account 1510: key, date, text, amount). Work ONLY from that text; never invent.

One item per thing to record:
- warning: a หนังสือเตือน (warning letter) about one employee. doc_date = the letter's date; offence; punishment as written (for example
  "ไม่อนุมัติเบี้ยขยัน", "หักเงิน 1,000 บาท"); penalty_amount = baht deducted as a fine (0 if none); level = ครั้งที่ if written (else 0).
- resign: a resignation (หนังสือลาออก) or dismissal. end_date = the last working day.
- loan: a staff loan / advance / damage to be paid back by salary deduction -- a memo approving it (ขออนุมัติ ... เงินกู้ยืม) or an expense
  line (source "line", line_key = its key, amount = its amount unless the text says otherwise). repay_amount per month: "(2500x4)" means
  2,500 a month for 4 months; "5,000 บาท เดือนละ 1,000 บาท 5 เดือน" means amount 5000, repay 1000. first_month only when written.
- loan_terms: a memo that only changes how an EXISTING loan is paid back (new monthly amount / start month), no new money lent.
- role_change: a memo moving someone to another position or branch (จาก...เป็น...). to_position must be one of the positions listed;
  daily_rate = the new daily wage if written (วันละ 400 บาท -> 400); change_date = the day it takes effect (มีผล).
Skip everything that is not about one employee: insurance settlements (สัญญาประนีประนอมยอมความ), covers, pay lists, fuel, attendance
summaries without a decision. A page about two people gives two items.

Dates are Buddhist Era (69 = 2569 BE = 2026): return YYYY-MM-DD Gregorian. A day and month with no year take the year of the page.
Numbers are plain baht. employee_code exactly as written (SB-FR024, SB-PD011, SB-KK003); "" when the paper has none. employee_name
without the title นาย / นาง / นางสาว. Text fields not used by the item's kind are "" and number fields 0."""


def _schema() -> dict:
    from app import employees
    item = json.loads(json.dumps(_ITEM))
    item["properties"]["to_position"]["enum"] = ["", *employees.POSITION_PRESETS]
    item["properties"]["to_branch"]["enum"] = ["", *employees.BRANCHES]
    return {"type": "object", "additionalProperties": False, "properties": {"items": {"type": "array", "items": item}}, "required": ["items"]}


def _system() -> str:
    from app import employees
    return PROMPT + "\nPositions: " + ", ".join(employees.POSITION_PRESETS) + "\nBranches: " + ", ".join(employees.BRANCHES)


def _ask(src: dict) -> tuple[list, dict]:
    """The one call: text in, the items out (structured output). Retried once when the answer does not parse; a refused request is not."""
    from app import inbox_ai
    from app.extraction import FALLBACK, MODEL
    usage = {"model": MODEL, "calls": 0, "input_tokens": 0, "output_tokens": 0}
    last = None
    for attempt in range(1, MAX_PARSE_ATTEMPTS + 1):
        try:
            r = inbox_ai._client().beta.messages.create(
                model=MODEL, max_tokens=MAX_OUTPUT_TOKENS, **FALLBACK,
                system=[{"type": "text", "text": _system()}],
                messages=[{"role": "user", "content": [{"type": "text", "text": json.dumps(src, ensure_ascii=False)}]}],
                output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": _schema()}})
            u = inbox_ai._usage_of(r)
            usage["calls"] += 1
            usage["input_tokens"] += u["input_tokens"]
            usage["output_tokens"] += u["output_tokens"]
            if r.stop_reason == "refusal":
                raise ValueError("the model declined")
            if r.stop_reason == "max_tokens":
                raise ValueError(f"the answer was cut off at {MAX_OUTPUT_TOKENS} tokens")
            out = json.loads(next(b.text for b in r.content if b.type == "text"))
            if not isinstance(out.get("items"), list):
                raise ValueError("no items")
            return out["items"], usage
        except Exception as exc:                       # noqa: BLE001 -- retried once, then reported in Thai
            last = exc
            logger.warning("inbox HR read attempt %d failed: %r", attempt, exc)
            if inbox_ai._is_bad_request(exc):
                break
    err = HrReadError(f"อ่านเรื่องพนักงานไม่สำเร็จ ({last})")
    err.usage = usage
    raise err


def add_usage(a: Optional[dict], b: Optional[dict]) -> Optional[dict]:
    """The file's ai_usage with the HR call added (the same counters the page reads)."""
    if not b or not b.get("calls"):
        return a
    a = dict(a or {"model": b.get("model"), "calls": 0, "input_tokens": 0, "output_tokens": 0})
    for k in ("calls", "input_tokens", "output_tokens"):
        a[k] = int(a.get(k) or 0) + int(b.get(k) or 0)
    a["model"] = a.get("model") or b.get("model")
    return a


def read_items(preview: dict, people: Optional[list] = None) -> tuple[list, dict]:
    """(items, usage). No HR page and no 1510 line: no call at all ([], {}). The people are read before the paid call (a failed
    list then costs nothing), and anything failing after it still reports the tokens it cost (HrReadError.usage)."""
    src = sources(preview)
    if not src["pages"] and not src["lines"]:
        return [], {}
    if people is None:
        people = _people() if supabase.configured() else []
    raw, usage = _ask(src)
    try:
        return build_items(raw, src, people), usage
    except Exception as exc:                           # noqa: BLE001 -- the tokens were spent: they are counted all the same
        logger.exception("inbox HR: building the items failed")
        err = HrReadError(f"อ่านเรื่องพนักงานไม่สำเร็จ ({exc})")
        err.usage = usage
        raise err from exc


# ------------------------------------------------------------------ who it is about
def norm_code(s) -> Optional[str]:
    m = CODE_RE.search(str(s or ""))
    return f"SB-{m.group(1).upper()}{m.group(2)}" if m else None


def _cons(s) -> str:
    from app.payroll import _consonants
    return _consonants(s)


def name_score(paper: str, p: dict) -> float:
    """How well a name on the paper fits an employee: the consonants (clerks spell names their own way: เอก ลีศรี / เอก ดีศรี) of the
    full name, of nickname + surname, and a one-word paper name against the nickname or the first name."""
    def plain(s):
        from app.payroll import NAME_TITLE_RE
        return NAME_TITLE_RE.sub("", re.sub(r"\(.*?\)|\s", "", str(s or "")))
    if plain(paper) and plain(paper) in (plain(p.get("full_name")), plain(p.get("nickname"))):
        return 1.0                                     # written exactly so (short names like ตู่ have one consonant)
    a = _cons(paper)
    if len(a) < 2:
        return 0.0
    full = re.sub(r"\(.*?\)", " ", str(p.get("full_name") or "")).split()
    nick = str(p.get("nickname") or "").strip()
    cands = [" ".join(full)]
    if nick and len(full) > 1:
        cands.append(f"{nick} {full[-1]}")
    scores = [SequenceMatcher(None, a, b).ratio() for b in (_cons(c) for c in cands) if b]
    if nick and _cons(nick) == a:
        scores.append(0.95)
    if full and _cons(full[0]) == a:
        scores.append(0.9)
    return max(scores, default=0.0)


def resolve(code, name, people: list[dict]) -> tuple[Optional[dict], Optional[str]]:
    """(the employee, "code" | "name") -- the code first, else a sure name match; (None, None) when nobody is sure."""
    by_code = {str(p.get("code") or "").upper(): p for p in people}
    for c in (re.sub(r"\s+", "", str(code or "")).upper(), norm_code(code), norm_code(name)):   # the exact code first
        if c and c in by_code:
            return by_code[c], "code"
    if not str(name or "").strip():
        return None, None
    scored = sorted(((name_score(name, p), i) for i, p in enumerate(people)), reverse=True)
    if not scored or scored[0][0] < NAME_SURE:
        return None, None
    if len(scored) > 1 and scored[1][0] > scored[0][0] - NAME_MARGIN:
        return None, None
    return people[scored[0][1]], "name"


def _people() -> list[dict]:
    return supabase.select_all("employees", {"select": EMP_COLS, "order": "code.asc"})


# ------------------------------------------------------------------ the items
def _iso(v) -> Optional[str]:
    try:
        d = date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None
    return d.isoformat() if 2000 <= d.year < 2400 else None


def _month1(v) -> Optional[str]:
    m = re.fullmatch(r"(20\d\d)-(0[1-9]|1[0-2])(?:-\d\d)?", str(v or "").strip())
    return f"{m.group(1)}-{m.group(2)}-01" if m else None


def _next_month(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    y, m = int(iso[:4]), int(iso[5:7])
    return f"{y + (m == 12)}-{(m % 12) + 1:02d}-01"


def _pos(v) -> Optional[float]:
    try:
        n = round(float(str(v).replace(",", "")), 2)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _txt(v, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


def fields_from(kind: str, x: dict, line: Optional[dict] = None) -> dict:
    """The kind's fields from the model's answer (a 1510 line fills a loan's amount, date and reason when the answer has none)."""
    if kind == "warning":
        lvl = x.get("level")
        return {"doc_date": _iso(x.get("doc_date")), "offence_date": _iso(x.get("offence_date")), "offence": _txt(x.get("offence")),
                "punishment": _txt(x.get("punishment")), "penalty_amount": _pos(x.get("penalty_amount")),
                "level": int(lvl) if isinstance(lvl, (int, float)) and 1 <= int(lvl) <= 3 else None}
    if kind == "resign":
        return {"end_date": _iso(x.get("end_date")), "reason": _txt(x.get("reason"))}
    if kind in ("loan", "loan_terms"):
        amount = _pos(x.get("amount")) or (_pos((line or {}).get("amount")) if line else None)
        loan_date = _iso(x.get("loan_date")) or _iso((line or {}).get("date"))
        out = {"repay_amount": _pos(x.get("repay_amount")), "first_month": _month1(x.get("first_month"))}
        if kind == "loan_terms":
            return out
        lk = x.get("loan_kind") if x.get("loan_kind") in LOAN_KINDS else "loan"
        return {"loan_kind": lk, "amount": amount, "loan_date": loan_date, "repay_amount": out["repay_amount"],
                "first_month": out["first_month"] or _next_month(loan_date), "reason": _txt(x.get("reason")) or _txt((line or {}).get("text"))}
    return {"change_date": _iso(x.get("change_date")), "to_position": _txt(x.get("to_position"), 60) or None,
            "to_branch": _txt(x.get("to_branch"), 40) or None, "daily_rate": _pos(x.get("daily_rate")), "reason": _txt(x.get("reason"))}


def _safe_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9:_-]", "_", s)[:80]


def build_items(raw: list, src: dict, people: list[dict]) -> list[dict]:
    """The model's answer -> items: only kinds we know, pages / lines that were sent, the person found in code."""
    line_at = {ln["key"]: ln for ln in src.get("lines") or []}
    pages = {p["page"] for p in src.get("pages") or []}
    out, seen = [], set()
    for x in raw or []:
        if not isinstance(x, dict) or x.get("kind") not in KINDS:
            continue
        kind = x["kind"]
        key = str(x.get("line_key") or "").strip() or None
        if x.get("source") == "line" or key:
            if key not in line_at:
                continue
            source, page = "line", line_at[key]["page"]
        else:
            source = "page"
            try:
                page = int(x.get("page"))
            except (TypeError, ValueError):
                continue
            if page not in pages:
                continue
        emp, how = resolve(x.get("employee_code"), x.get("employee_name"), people)
        code = emp["code"] if emp else None
        base = _safe_id(f"{key}:{kind}" if key else f"p{page}:{kind}:{code or 'x'}")
        iid, i = base, 2
        while iid in seen:
            iid, i = f"{base}:{i}", i + 1
        seen.add(iid)
        out.append({"id": iid, "page": page, "source": source, "line_key": key, "kind": kind, "employee_code": code,
                    "employee_name_read": _txt(x.get("employee_name"), 120) or None, "code_read": norm_code(x.get("employee_code")),
                    "match": how, "fields": fields_from(kind, x, line_at.get(key) if key else None), "summary": _txt(x.get("summary")),
                    "status": "pending", "result": None, "applied_at": None})
    return out


def _sig(it: dict) -> tuple:
    """What an item is about, whatever its id: an unresolved person's id is only its place in the answer (p20:resign:x, x:2)."""
    who = re.sub(r"\s+", "", str(it.get("employee_name_read") or "")) or str(it.get("code_read") or "")
    return it.get("page"), it.get("kind"), it.get("line_key") or None, who


def _stable_id(it: dict) -> bool:
    """The id names the thing itself: the person's code, or the expense line (not a place in the answer)."""
    return bool(it.get("employee_code")) or (bool(it.get("line_key")) and it.get("id") == _safe_id(f"{it['line_key']}:{it['kind']}"))


def merge(old: Optional[list], new: list) -> list:
    """อ่านเรื่องพนักงาน again: what the owner already recorded or set aside stays as it is; the rest is the new read. A decided item
    takes the place of the new one about the same thing -- the same stable id, else the same page / kind / line / name read -- never
    of a different person who only got the same positional id; a new item whose id is taken gets a fresh one."""
    decided = [it for it in old or [] if it.get("status") in ("applied", "dismissed")]
    used: set[int] = set()
    out = []
    for n in new:
        hit = next((i for i, d in enumerate(decided) if i not in used and d["id"] == n["id"] and _stable_id(n)), None)
        if hit is None:
            hit = next((i for i, d in enumerate(decided) if i not in used and _sig(d) == _sig(n)), None)
        if hit is not None:
            used.add(hit)
        out.append(decided[hit] if hit is not None else n)
    out += [d for i, d in enumerate(decided) if i not in used]
    kept = {it["id"] for it in out if it.get("status") in ("applied", "dismissed")}
    taken = {it["id"] for it in out}
    for k, it in enumerate(out):
        if it.get("status") in ("applied", "dismissed") or it["id"] not in kept:
            continue
        base, i = it["id"], 2
        while f"{base}:{i}" in taken:
            i += 1
        out[k] = {**it, "id": f"{base}:{i}"}
        taken.add(out[k]["id"])
    return out


def carried(items: Optional[list]) -> Optional[list]:
    """The items of an earlier read of the same bytes (inbox_r_scan.read): what was recorded stays recorded -- the write is in พนักงาน,
    not in the row, and showing it as waiting again would invite a second one -- while a "ไม่บันทึก" (a decision about that row only)
    comes back as waiting."""
    if items is None:
        return None
    return [it if it.get("status") == "applied" else {**it, "status": "pending", "result": None, "applied_at": None} for it in items]


# ------------------------------------------------------------------ checking what the owner sends
def _date_in(v, what: str, today: date, ahead: int = 0, need: bool = True) -> Optional[str]:
    if v in (None, ""):
        if need:
            raise _err(f"ใส่{what}ด้วย")
        return None
    try:
        d = date.fromisoformat(str(v)[:10])
    except ValueError:
        raise _err(f"{what}ไม่ถูกต้อง")
    if not 2000 <= d.year < 2400:
        raise _err(f"{what}ไม่ถูกต้อง (ปีต้องเป็น ค.ศ. เช่น 2026)")
    if d > today + timedelta(days=ahead):
        raise _err(f"{what}เป็นวันในอนาคต" if not ahead else f"{what}ไกลเกินไป (ไม่เกิน {ahead} วันข้างหน้า)")
    return d.isoformat()


def _money_in(v, what: str, top: float, need: bool = True) -> Optional[float]:
    if v in (None, ""):
        if need:
            raise _err(f"ใส่{what}ด้วย")
        return None
    try:
        n = round(float(str(v).replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        raise _err(f"{what}ไม่ถูกต้อง")
    if not 0 < n <= top:
        raise _err(f"{what}ต้องมากกว่า 0 และไม่เกิน {top:,.0f}")
    return n


def clean_fields(kind: str, data, today: Optional[date] = None) -> dict:
    """The fields of one item as the owner sends them, checked strictly (Thai messages). Raises InboxError."""
    from app import employees
    today = today or employees.today_th()
    if kind not in FIELDS:
        raise _err("ประเภทรายการไม่ถูกต้อง")
    if not isinstance(data, dict):
        raise _err("ข้อมูลไม่ถูกต้อง")
    extra = [k for k in data if k not in FIELDS[kind]]
    if extra:
        raise _err(f"แก้ช่อง {extra[0]} ไม่ได้")
    if kind == "warning":
        level = data.get("level")
        if level not in (None, ""):
            try:
                level = int(level)
            except (TypeError, ValueError):
                raise _err("ครั้งที่ของหนังสือเตือนไม่ถูกต้อง")
            if not 1 <= level <= 3:
                raise _err("ครั้งที่ของหนังสือเตือนต้องเป็น 1–3")
        else:
            level = None
        offence = _txt(data.get("offence"))
        if not offence:
            raise _err("ใส่ความผิดด้วย")
        return {"doc_date": _date_in(data.get("doc_date"), "วันที่ของหนังสือเตือน", today),
                "offence_date": _date_in(data.get("offence_date"), "วันที่ทำผิด", today, need=False), "offence": offence,
                "punishment": _txt(data.get("punishment")), "level": level,
                "penalty_amount": _money_in(data.get("penalty_amount"), "ยอดหักเงิน", MAX_PENALTY, need=False)}
    if kind == "resign":
        return {"end_date": _date_in(data.get("end_date"), "วันสุดท้ายที่ทำงาน", today, ahead=60), "reason": _txt(data.get("reason")) or None}
    if kind in ("loan", "loan_terms"):
        first = data.get("first_month")
        if kind == "loan":
            lk = str(data.get("loan_kind") or "loan")
            if lk not in LOAN_KINDS:
                raise _err("ประเภทเงินกู้ไม่ถูกต้อง")
            amount = _money_in(data.get("amount"), "ยอดเงินกู้", MAX_LOAN)
            loan_date = _date_in(data.get("loan_date"), "วันที่กู้", today)
            repay = _money_in(data.get("repay_amount"), "ยอดหักต่อเดือน", amount)
            fm = _month1(first) if first not in (None, "") else _next_month(loan_date)
            if not fm:
                raise _err("เดือนที่เริ่มหักไม่ถูกต้อง (เช่น 2026-12)")
            if fm[:7] < loan_date[:7]:
                raise _err("เดือนที่เริ่มหักต้องไม่ก่อนเดือนที่กู้")
            return {"loan_kind": lk, "amount": amount, "loan_date": loan_date, "repay_amount": repay, "first_month": fm,
                    "reason": _txt(data.get("reason")) or None}
        repay = _money_in(data.get("repay_amount"), "ยอดหักต่อเดือน", MAX_LOAN)
        fm = _month1(first)
        if not fm:
            raise _err("ใส่เดือนที่เริ่มหัก (เช่น 2026-12)")
        return {"repay_amount": repay, "first_month": fm}
    pos = _txt(data.get("to_position"), 60)
    if pos not in employees.POSITION_PRESETS:
        raise _err("เลือกตำแหน่งใหม่จากรายการ")
    branch = _txt(data.get("to_branch"), 40) or None
    if branch and branch not in employees.BRANCHES:
        raise _err("เลือกสาขาให้ถูกต้อง")
    return {"change_date": _date_in(data.get("change_date"), "วันที่มีผล", today, ahead=60), "to_position": pos, "to_branch": branch,
            "daily_rate": _money_in(data.get("daily_rate"), "ค่าแรงต่อวัน", MAX_RATE, need=False), "reason": _txt(data.get("reason")) or None}


# ------------------------------------------------------------------ what is there now (worked out on every GET)
def dmy(iso) -> str:
    """DD/MM/YYYY in the Buddhist year (the owner's papers)."""
    try:
        d = date.fromisoformat(str(iso or "")[:10])
    except ValueError:
        return str(iso or "")
    return f"{d.day:02d}/{d.month:02d}/{d.year + 543}"


def my(iso) -> str:
    m = re.match(r"(\d{4})-(\d{2})", str(iso or ""))
    return f"{m.group(2)}/{int(m.group(1)) + 543}" if m else ""


def baht(x) -> str:
    v = float(x or 0)
    return f"{v:,.0f}" if abs(v - round(v)) < 0.005 else f"{v:,.2f}"


def _in(ids) -> str:
    return "in.(" + ",".join(sorted(ids)) + ")"


def context(items: list[dict], people: Optional[list] = None) -> dict:
    """Everything state_of needs about the people of these items, read once: the employees, their active loans, warnings, rates and
    pay rules. A part that cannot be read is None (its line says so) -- the page still opens."""
    codes = {it["employee_code"] for it in items if it.get("employee_code")}
    if people is None:
        people = supabase.select_all("employees", {"select": EMP_COLS, "code": _in(codes), "order": "code.asc"}) if codes else []
    by_code = {str(p["code"]).upper(): p for p in people}
    ids = {p["id"] for c, p in by_code.items() if c in {x.upper() for x in codes}}

    def grouped(table, params):
        if not ids:
            return {}
        try:
            out: dict = {}
            for r in supabase.select_all(table, {**params, "employee_id": _in(ids)}):
                out.setdefault(r["employee_id"], []).append(r)
            return out
        except supabase.SupabaseError:
            logger.warning("inbox HR: could not read %s", table)
            return None
    return {"by_code": by_code,
            "loans": grouped("employee_loans", {"select": "id,employee_id,kind,amount,loan_date,repay_mode,repay_amount,repay_months,repay_percent,first_month,status",
                                                "status": "eq.active", "order": "loan_date.asc"}),
            "warnings": grouped("employee_documents", {"select": "id,employee_id,doc_type,doc_date,status,warning_level", "doc_type": "eq.warning",
                                                       "order": "doc_date.asc"}),
            "rates": grouped("employee_rates", {"select": "id,employee_id,daily_rate,effective_from,kind", "order": "effective_from.desc"}),
            "rules": grouped("employee_pay_rules", {"select": "employee_id,pay_group,route_until", "order": "employee_id.asc"})}


def _loan_text(ln: dict) -> str:
    from app import loans
    return f"{loans.KINDS.get(ln.get('kind'), '')} {baht(ln['amount'])} " + (
        f"ผ่อน {baht(ln['repay_amount'])}/เดือน เริ่ม {my(ln.get('first_month'))}" if ln.get("repay_mode") == "monthly_fixed" and ln.get("repay_amount")
        else loans.describe(ln)) + (f" (กู้ {dmy(ln['loan_date'])})" if ln.get("loan_date") else "")


def _target_loan(active: list[dict]) -> Optional[dict]:
    """The loan a loan_terms memo changes: the general loan, or the only one."""
    general = [ln for ln in active if ln.get("kind") == "loan"]
    return general[0] if general else (active[0] if len(active) == 1 else None)


def state_of(it: dict, ctx: dict, today: Optional[date] = None) -> dict:
    """{"now": what is in พนักงาน now, "dest": where recording sends it, "block": why it cannot be recorded (None = it can),
    "employee": {code, name, ...} | None, "level": a warning's level}. For one item, from context()."""
    from app import documents, employees
    from app.rates import rate_on
    today = today or employees.today_th()
    f = it.get("fields") or {}
    kind = it["kind"]
    emp = ctx["by_code"].get(str(it.get("employee_code") or "").upper()) if it.get("employee_code") else None
    if not emp:
        read = it.get("employee_name_read") or it.get("code_read")
        return {"now": "ไม่พบคนนี้ในรายชื่อพนักงาน — เลือกคนก่อน" + (f" (ในกระดาษ: {read})" if read else ""),
                "dest": "พนักงาน · ยังไม่ได้เลือกคน" + (f" (ในกระดาษ: {read})" if read else ""), "block": "เลือกพนักงานก่อน",
                "employee": None, "level": None}
    name = employees.display_name(emp.get("prefix"), emp["full_name"])
    who = {"code": emp["code"], "name": name, "nickname": emp.get("nickname") or "", "position": emp.get("position") or "",
           "branch": emp.get("branch"), "status": emp.get("status") or "active"}
    head = f"พนักงาน · {name} ({emp['code']})"
    gone = emp.get("status") == "resigned"
    out = {"employee": who, "level": None, "block": None}
    if it.get("employee_name_read") and it.get("match") == "code":
        from app.payroll import same_person
        if not same_person(it["employee_name_read"], emp.get("full_name"), emp.get("nickname")):
            out["mismatch"] = f"ชื่อในกระดาษ “{it['employee_name_read']}” ไม่เหมือน {name} — ตรวจรหัส"

    if kind == "resign":
        end = f.get("end_date")
        if end and emp.get("start_date") and end < str(emp["start_date"]):
            out["block"] = f"วันสุดท้ายต้องไม่ก่อนวันเริ่มงาน ({dmy(emp['start_date'])})"
        if gone:
            if end and emp.get("end_date") == end:
                out.update(now=f"ลาออกแล้ว วันสุดท้าย {dmy(emp['end_date'])} — มีแล้ว ตรงกัน", block="มีแล้ว ตรงกัน — ไม่มีอะไรต้องบันทึก",
                           dest=f"{head} · ลาออก วันสุดท้าย {dmy(end)}")
            else:
                out.update(now=f"ลาออกแล้ว วันสุดท้าย {dmy(emp.get('end_date')) or 'ไม่ได้ใส่'} — ในกระดาษ {dmy(end) or 'ไม่ได้อ่าน'} ไม่ตรงกัน"
                               " · บันทึก = แก้วันสุดท้ายในระบบเป็นตามช่องวันที่",
                           dest=f"{head} · แก้วันสุดท้ายเป็น {dmy(end)}")
        else:
            out.update(now=f"ยังทำงานอยู่ · {emp.get('position') or 'ยังไม่ระบุตำแหน่ง'} สาขา{emp.get('branch')}",
                       dest=f"{head} · ลาออก วันสุดท้าย {dmy(end)}" if end else f"{head} · ลาออก")
        return out

    if kind == "warning":
        hist = ctx.get("warnings")
        if hist is None:
            return {**out, "now": "อ่านหนังสือเตือนในระบบไม่สำเร็จ — ลองเปิดใหม่", "dest": f"{head} · หนังสือเตือน", "block": "อ่านข้อมูลในระบบไม่สำเร็จ"}
        mine = [w for w in hist.get(emp["id"], []) if w.get("status") != "cancelled"]
        nxt = documents.next_level(mine)
        level = f.get("level") or nxt
        done = [w for w in mine if w.get("warning_level") and w.get("status") in ("issued", "signed", "refused")]
        now = ("หนังสือเตือนที่มีแล้ว: " + ", ".join(f"ครั้งที่ {w['warning_level']} ({dmy(w['doc_date'])})" for w in done)) if done else "ยังไม่มีหนังสือเตือนในระบบ"
        now += f" · ฉบับนี้จะเป็นครั้งที่ {level}" + (f" (ระบบนับได้ครั้งที่ {nxt})" if f.get("level") and f["level"] != nxt else "")
        if f.get("doc_date") and any(w.get("doc_date") == f["doc_date"] for w in mine):
            now += f" — มีหนังสือเตือนวันที่ {dmy(f['doc_date'])} แล้ว"
            out["block"] = f"มีหนังสือเตือนวันที่ {dmy(f['doc_date'])} แล้ว"
        return {**out, "now": now, "dest": f"{head} · หนังสือเตือนครั้งที่ {level}", "level": level}

    if kind in ("loan", "loan_terms"):
        act = ctx.get("loans")
        if act is None:
            return {**out, "now": "อ่านเงินกู้ในระบบไม่สำเร็จ — ลองเปิดใหม่", "dest": f"{head} · {KIND_TH[kind]}", "block": "อ่านข้อมูลในระบบไม่สำเร็จ"}
        mine = act.get(emp["id"], [])
        now = ("เงินกู้ที่ผ่อนอยู่: " + "; ".join(_loan_text(ln) for ln in mine)) if mine else "ยังไม่มีเงินกู้ที่ผ่อนอยู่"
        if kind == "loan":
            amount = f.get("amount")
            same = [ln for ln in mine if amount is not None and abs(float(ln["amount"]) - float(amount)) <= SAME_LOAN_BAHT]
            if same:
                now += " — มีแล้ว (ยอดเดียวกัน ไม่บันทึกซ้ำ)"
                out["block"] = "มีเงินกู้นี้แล้ว"
            elif gone:
                out["block"] = "คนนี้ลาออกแล้ว — บันทึกเงินกู้ใหม่ไม่ได้"
            dest = f"{head} · เงินกู้ {baht(amount)}" + (f" ผ่อน {baht(f['repay_amount'])}/เดือน" if f.get("repay_amount") else "") if amount else f"{head} · เงินกู้"
            return {**out, "now": now, "dest": dest}
        tgt = _target_loan(mine)
        if not tgt:
            return {**out, "now": now if mine else "ไม่พบเงินกู้ที่ยังผ่อนอยู่ของคนนี้", "dest": f"{head} · เปลี่ยนการผ่อน",
                    "block": "ไม่พบเงินกู้ที่ยังผ่อนอยู่" if not mine else "มีเงินกู้หลายรายการ — แก้ที่หน้าเงินกู้"}
        if (tgt.get("repay_mode") == "monthly_fixed" and f.get("repay_amount") and abs(float(tgt.get("repay_amount") or 0) - float(f["repay_amount"])) < 0.005
                and str(tgt.get("first_month") or "")[:7] == str(f.get("first_month") or "")[:7]):
            out["block"] = "การผ่อนตรงกับที่มีแล้ว"
        return {**out, "now": f"เงินกู้ที่จะเปลี่ยน: {_loan_text(tgt)}",
                "dest": f"{head} · เปลี่ยนการผ่อนเป็น {baht(f.get('repay_amount'))}/เดือน" + (f" เริ่ม {my(f['first_month'])}" if f.get("first_month") else "")}

    # role_change
    rates_of = (ctx.get("rates") or {}).get(emp["id"], [])
    cur = rate_on(rates_of, today)
    now = f"ตอนนี้: {emp.get('position') or 'ยังไม่ระบุตำแหน่ง'} สาขา{emp.get('branch')}"
    if ctx.get("rates") is None:
        now += " · อ่านค่าแรงไม่สำเร็จ"
    else:
        now += f" · ค่าแรง {baht(cur['daily_rate'])}/วัน (ตั้งแต่ {dmy(cur['effective_from'])})" if cur else " · ยังไม่มีค่าแรงในระบบ"
    rule = ((ctx.get("rules") or {}).get(emp["id"]) or [None])[0]
    from app.payroll import ROUTE_GROUPS, ROUTE_POSITION
    if rule and rule.get("pay_group"):
        now += f" · กลุ่มคิดเงิน {rule['pay_group']}"
    if (rule and rule.get("pay_group") in ROUTE_GROUPS) or emp.get("position") == ROUTE_POSITION:
        now += f" · ขับรถสายถึง {dmy(rule['route_until'])}" if rule and rule.get("route_until") else " · ยังไม่ได้ตั้งวันสุดท้ายที่ขับรถสาย"
    to_pos, to_branch = f.get("to_position"), f.get("to_branch") or emp.get("branch")
    if gone:
        out["block"] = "คนนี้ลาออกแล้ว"
    elif to_pos and to_pos == emp.get("position") and to_branch == emp.get("branch"):
        on_day = rate_on(rates_of, date.fromisoformat(f["change_date"])) if f.get("change_date") else None
        if not f.get("daily_rate") or (on_day and abs(float(on_day["daily_rate"]) - float(f["daily_rate"])) < 0.005):
            out["block"] = "เปลี่ยนแล้ว — ตำแหน่งและค่าแรงตรงกับกระดาษ" if f.get("daily_rate") else "ตำแหน่งนี้อยู่แล้ว"
    dest = f"{head} · เปลี่ยนตำแหน่ง → {to_pos or '?'}" + (f" สาขา{to_branch}" if to_branch != emp.get("branch") else "") \
        + (f" {baht(f['daily_rate'])}/วัน" if f.get("daily_rate") else "") + (f" จาก {dmy(f['change_date'])}" if f.get("change_date") else "")
    return {**out, "now": now, "dest": dest}


def view(preview: dict, access: str = "none", ai_ready: bool = False) -> dict:
    """What the check page gets: the items with now / dest / block (only for someone who may open พนักงาน: access "ok"; "locked" =
    the HR PIN is not given yet; "none" = no employees menu), how many wait, and whether อ่านเรื่องพนักงาน can be offered."""
    from app import employees, loans
    raw = preview.get("hr_items")
    items = [dict(it) for it in raw or [] if isinstance(it, dict)]
    src = sources(preview) if raw is None else None
    out = {"access": access, "pending": sum(1 for it in items if it.get("status") == "pending"), "read": raw is not None,
           "can_read": raw is None and bool(src["pages"] or src["lines"]) and ai_ready, "items": items,
           "sources": ({"pages": len(src["pages"]), "lines": len(src["lines"])} if src else None)}
    if access != "ok":
        for it in items:
            it.update(now=None, block=NO_ACCESS if access == "none" else "ใส่ PIN ส่วนพนักงานก่อน",
                      dest="พนักงาน · " + (it.get("employee_name_read") or it.get("employee_code") or "?"), employee=None)
        return out
    out["options"] = {"positions": list(employees.POSITION_PRESETS), "branches": list(employees.BRANCHES), "loan_kinds": loans.KINDS}
    if not items:
        return out
    try:
        ctx = context(items)
    except supabase.SupabaseError:
        logger.exception("inbox HR: reading the people failed")
        for it in items:
            it.update(now="อ่านข้อมูลพนักงานไม่สำเร็จ — ลองเปิดใหม่", block="อ่านข้อมูลในระบบไม่สำเร็จ", dest="พนักงาน", employee=None)
        return out
    today = employees.today_th()
    for it in items:
        st = state_of(it, ctx, today)
        if it.get("status") == "applied":                # recorded: what it says now is the result, never "record it again"
            st.update(block="บันทึกแล้ว", now=None, dest=it.get("dest") or st.get("dest"))   # and where it went, as it was then
        it.update(st)
    return out


# ------------------------------------------------------------------ the owner's buttons (each under the file's lock)
def _find(items: list, item_id: str) -> dict:
    it = next((x for x in items if x.get("id") == item_id), None)
    if it is None:
        raise _inbox().NotFound("ไม่พบรายการนี้ในไฟล์ — เปิดหน้าตรวจใหม่")
    return it


def _open(file_id: str) -> tuple[dict, dict, list]:
    inbox = _inbox()
    row = inbox._get(file_id)
    inbox._refuse_running(row)
    p = dict(row.get("preview") or {})
    if not p.get("acc_docs_batch"):
        raise _err("ไฟล์นี้ไม่ใช่เอกสารเบิกที่อ่านด้วย AI")
    return row, p, [dict(x) for x in p.get("hr_items") or [] if isinstance(x, dict)]


def _person(code) -> dict:
    code = re.sub(r"\s+", "", str(code or "")).upper()
    if not code:
        raise _err("เลือกพนักงานก่อน")
    rows = supabase.select("employees", {"select": EMP_COLS, "code": f"eq.{code}", "limit": "1"})
    if not rows:
        raise _err(f"ไม่พบรหัส {code} ในรายชื่อพนักงาน")
    return rows[0]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _batch_key(row: dict, p: dict) -> str:
    return str(((p.get("batches") or [{}])[0] or {}).get("key") or (p.get("acc_docs_batch") or {}).get("batch") or f"inbox:{row['id']}")


HERE = "(จากหน้าตรวจเอกสาร)"                    # marks the พนักงาน-queue rows this page wrote (the only ones it may take back)


def _hkey(row: dict, p: dict, it: dict) -> dict:
    return {"batch": f"eq.{_batch_key(row, p)}", "page": f"eq.{int(it['page'])}", "kind": f"eq.{it['kind']}",
            "employee_code": f"eq.{it['employee_code']}"}


def _history(row: dict, p: dict, it: dict, status: str) -> None:
    """พนักงาน › จากเอกสารสิ้นเดือน shows what was decided here too. The documents import (hr_suggest.sync_batch) uses the same batch
    key, so a row may be there already: one recorded there ("applied") is never overwritten, and a ไม่บันทึก here leaves a row of the
    queue as it is (it is that page's own decision). Never fails the write it follows: before schema_v95.sql the table refuses kind
    role_change, and a history row is not worth undoing a recorded warning for."""
    if not it.get("employee_code"):
        return
    rec = {"batch": _batch_key(row, p), "page": int(it["page"]), "kind": it["kind"], "employee_code": it["employee_code"],
           "payload": it.get("fields") or {}, "summary": it.get("summary") or None, "status": status,
           "result": (f"{(it.get('result') or '')[:270]} {HERE}" if status == "applied" else f"ไม่บันทึก {HERE}"), "decided_at": _now_iso()}
    try:
        have = supabase.select("hr_suggestions", {"select": "id,status,result", **_hkey(row, p, it), "limit": "1"})
        if have and (have[0].get("status") == "applied" or status == "dismissed"):
            return
        supabase.insert("hr_suggestions", [rec], on_conflict="batch,page,kind,employee_code")
    except supabase.SupabaseError:
        logger.warning("inbox HR: hr_suggestions row not written (%s %s) -- schema_v95.sql not applied yet?", it["kind"], it["employee_code"])


def _unhistory(row: dict, p: dict, it: dict) -> None:
    """เอากลับมา: only the ไม่บันทึก row this page wrote goes; an applied one, or one the พนักงาน queue decided, stays."""
    if not it.get("employee_code"):
        return
    try:
        for h in supabase.select("hr_suggestions", {"select": "id,status,result", **_hkey(row, p, it)}):
            if h.get("status") == "dismissed" and str(h.get("result") or "").endswith(HERE):
                supabase.delete("hr_suggestions", {"id": f"eq.{h['id']}"})
    except supabase.SupabaseError:
        logger.warning("inbox HR: could not take back the dismissed hr_suggestions row")


def _store(file_id: str, p: dict, items: list) -> None:
    _inbox()._save(file_id, {"preview": {**p, "hr_items": items}})


def _write(it: dict, emp: dict, f: dict, ctx: dict, ref: str, level: Optional[int]) -> str:
    """The write itself (hr_suggest.write_*), and the result line the page shows after it."""
    from app import employees, hr_suggest
    name = f"{employees.display_name(emp.get('prefix'), emp['full_name'])} ({emp['code']})"
    kind = it["kind"]
    if kind == "resign":
        hr_suggest.write_resign(emp, f["end_date"], f.get("reason"))
        return f"{name} · " + (f"แก้วันสุดท้ายเป็น {dmy(f['end_date'])}" if emp.get("status") == "resigned" else f"ลาออก วันสุดท้าย {dmy(f['end_date'])}")
    if kind == "warning":
        lvl = hr_suggest.write_warning(emp, {**f, "level": f.get("level") or level},
                                              f"บันทึกจากหน้าตรวจเอกสาร ({ref}) — กระดาษออกนอกโปรแกรม เซ็นแล้ว",
                                              (ctx.get("warnings") or {}).get(emp["id"], []))[1]
        pen = f" · หักเงิน {baht(f['penalty_amount'])} (ลงวันที่ {dmy(f['doc_date'])})" if f.get("penalty_amount") else ""
        return f"{name} · หนังสือเตือนครั้งที่ {lvl} (เซ็นแล้ว){pen}"
    if kind == "loan":
        hr_suggest.write_loan(emp, f, ref)
        return f"{name} · เงินกู้ {baht(f['amount'])} ผ่อน {baht(f['repay_amount'])}/เดือน เริ่ม {my(f['first_month'])}"
    if kind == "loan_terms":
        tgt = _target_loan((ctx.get("loans") or {}).get(emp["id"], []))
        hr_suggest.write_loan_terms(tgt, f)
        return f"{name} · ผ่อน {baht(f['repay_amount'])}/เดือน เริ่ม {my(f['first_month'])}"
    hr_suggest.write_role_change(emp, f, ref)
    moved = f["to_position"] != (emp.get("position") or None) or (f.get("to_branch") or emp.get("branch")) != emp.get("branch")
    return f"{name} · " + " · ".join(x for x in (
        f"เปลี่ยนตำแหน่ง → {f['to_position']} จาก {dmy(f['change_date'])}" if moved else "",
        f"ค่าแรง {baht(f['daily_rate'])}/วัน จาก {dmy(f['change_date'])} (ลงในอัตราค่าแรงที่หน้าเงินเดือนใช้)" if f.get("daily_rate") else "") if x)


def apply(file_id: str, item_id: str, body: Optional[dict] = None, who: str = "") -> None:
    """บันทึก: the owner's fields (over what was read) and person, checked strictly; refused when it would duplicate what is there;
    written; the item marked applied with the result. Applying twice is refused."""
    from app import employees
    body = body if isinstance(body, dict) else {}
    with _inbox()._busy(file_id):
        row, p, items = _open(file_id)
        it = _find(items, item_id)
        if it.get("status") == "applied":
            raise _err("บันทึกรายการนี้ไปแล้ว — บันทึกซ้ำไม่ได้")
        if it.get("status") == "dismissed":
            raise _err("รายการนี้กด “ไม่บันทึก” ไว้ — กด “เอากลับมา” ก่อน")
        sent = body.get("fields")
        if sent is not None and not isinstance(sent, dict):
            raise _err("ข้อมูลไม่ถูกต้อง")
        extra = [k for k in sent or {} if k not in FIELDS[it["kind"]]]
        if extra:
            raise _err(f"แก้ช่อง {extra[0]} ไม่ได้")
        merged = {k: v for k, v in {**(it.get("fields") or {}), **(sent or {})}.items() if k in FIELDS[it["kind"]]}
        f = clean_fields(it["kind"], merged)
        emp = _person(body.get("employee_code") or it.get("employee_code"))
        trial = {**it, "fields": f, "employee_code": emp["code"], "match": it.get("match") if emp["code"] == it.get("employee_code") else "picked"}
        ctx = context([trial], people=[emp])
        st = state_of(trial, ctx)
        if st.get("block"):
            raise _err(st["block"])
        ref = f"{row.get('file_name') or 'เอกสาร'} หน้า {it['page']}"
        try:
            result = _write(trial, emp, f, ctx, ref, st.get("level"))
        except employees.InvalidEmployee as exc:
            raise _err(str(exc))
        if it["kind"] == "warning":                   # the level written, so a later count (which includes this one) never relabels it
            f = {**f, "level": f.get("level") or st.get("level")}
        it.update(fields=f, employee_code=emp["code"], match=trial["match"], status="applied", result=result, applied_at=_now_iso(),
                  applied_by=(who or "")[:80] or None, dest=st.get("dest"))
        _store(row["id"], p, items)
        _history(row, p, it, "applied")


def dismiss(file_id: str, item_id: str, who: str = "") -> None:
    """ไม่บันทึก: nothing is written in พนักงาน."""
    with _inbox()._busy(file_id):
        row, p, items = _open(file_id)
        it = _find(items, item_id)
        if it.get("status") == "applied":
            raise _err("บันทึกไปแล้ว — ถ้าผิด แก้ที่หน้าพนักงาน / เงินกู้ / ใบเตือน")
        it.update(status="dismissed", result="ไม่บันทึก", applied_at=_now_iso(), applied_by=(who or "")[:80] or None)
        _store(row["id"], p, items)
        _history(row, p, it, "dismissed")


def reopen(file_id: str, item_id: str) -> None:
    """เอากลับมา: a ไม่บันทึก item waits again (never one already recorded)."""
    with _inbox()._busy(file_id):
        row, p, items = _open(file_id)
        it = _find(items, item_id)
        if it.get("status") == "applied":
            raise _err("บันทึกไปแล้ว — เอากลับมาไม่ได้")
        if it.get("status") != "dismissed":
            return
        _unhistory(row, p, it)
        it.update(status="pending", result=None, applied_at=None, applied_by=None)
        _store(row["id"], p, items)


def set_person(file_id: str, item_id: str, code) -> None:
    """The owner picked who the item is about (the read found nobody, or the wrong one): the page then shows that person's now."""
    with _inbox()._busy(file_id):
        row, p, items = _open(file_id)
        it = _find(items, item_id)
        if it.get("status") == "applied":
            raise _err("บันทึกไปแล้ว — เปลี่ยนคนไม่ได้")
        emp = _person(code)
        it.update(employee_code=emp["code"], match="picked")
        _store(row["id"], p, items)


def read_file(file_id: str) -> None:
    """อ่านเรื่องพนักงาน for a file read before this page existed (owner's click): the same call as at the end of a paid read; what was
    already recorded or set aside on it stays (merge). The tokens are added to the file's ai_usage, a failed call's too."""
    from app.config import settings
    if not settings.anthropic_api_key:
        raise _err("ยังไม่ได้ตั้งค่า AI บนเซิร์ฟเวอร์ (ANTHROPIC_API_KEY)")
    if not settings.inbox_ai_enabled:
        raise _err("การอ่านด้วย AI ปิดอยู่ (ตั้งค่า INBOX_AI_ENABLED)")
    inbox = _inbox()
    with inbox._busy(file_id):
        row, p, _items = _open(file_id)
        base = inbox.base_preview(row)
        if not base.get("ai_pages"):
            raise _err("ไฟล์นี้ไม่มีหน้าที่ AI อ่านไว้ — อ่านด้วย AI ก่อน")
        if not any(sources(base).values()):
            raise _err("ไม่พบหน้าเรื่องพนักงานหรือรายการเงินกู้พนักงานในไฟล์นี้")
        try:
            items, used = read_items(base)
        except HrReadError as exc:
            if exc.usage and exc.usage.get("calls"):
                usage = add_usage(row.get("ai_usage") or base.get("ai_usage"), exc.usage)
                inbox._save(row["id"], {"preview": {**p, "ai_usage": usage}, "ai_usage": usage})
            raise _err(str(exc))
        usage = add_usage(row.get("ai_usage") or base.get("ai_usage"), used)
        inbox._save(row["id"], {"preview": {**p, "hr_items": merge(base.get("hr_items"), items), "ai_usage": usage}, "ai_usage": usage})
