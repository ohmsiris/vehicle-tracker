"""
The account a line of the mid / end-of-month papers belongs to, from its TEXT alone (owner, 28 Nov 2026: "update category automatically
when I change the name" -- on the check page, retyping a line's รายการ re-picks its บัญชี unless the owner picked the บัญชี himself).

1. The books first: every line already booked from these papers (acc_entries of midmonth / endmonth, their debit account) is a past
   answer. The same wording (numbers, plates and "(อ่านไม่ชัด)" taken out) -> the account it was booked to most often; else the closest
   wording by character trigrams when it is close enough.
2. Nothing close: one short text-only call to the model with the same chart the page reader uses (inbox_ai.CHART), answer forced to
   one of its codes.
suggest() never raises for a model failure: it returns None and the page keeps the account as it is.
"""
import json
import logging
import re
import threading
import time
from collections import Counter
from typing import Optional

logger = logging.getLogger("vehicle_tracker.acc_suggest")

HISTORY_TTL = 600.0
MIN_SIMILARITY = 0.55
MAX_TEXT = 200
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "rows": None}


def norm(text: str) -> str:
    """'เติมน้ำมัน 84-4764 (34) (อ่านไม่ชัด)' -> 'เติมน้ำมัน': no unsure mark, brackets, digits or punctuation."""
    t = re.sub(r"\(อ่านไม่ชัด\)", " ", text or "")
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"[0-9๐-๙]+", " ", t)
    t = re.sub(r"[^\w฀-๿]+", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def grams(text: str) -> set:
    s = text.replace(" ", "")
    return {s[i:i + 3] for i in range(max(len(s) - 2, 1))} if s else set()


def history() -> dict:
    """{normalized wording: Counter(account)} of every line booked from the papers, cached for HISTORY_TTL seconds."""
    with _lock:
        if _cache["rows"] is not None and time.monotonic() - _cache["at"] < HISTORY_TTL:
            return _cache["rows"]
    from app import supabase
    out: dict = {}
    rows = supabase.select_all("acc_entries", {"select": "description,acc_lines!inner(account,debit)", "source": "in.(midmonth,endmonth)",
                                               "acc_lines.debit": "gt.0", "order": "id.asc"})
    for r in rows:
        k = norm(r.get("description") or "")
        if not k:
            continue
        for ln in r.get("acc_lines") or []:
            if ln.get("account") and ln["account"] != "1100":
                out.setdefault(k, Counter())[ln["account"]] += 1
    with _lock:
        _cache.update(at=time.monotonic(), rows=out)
    return out


def from_history(text: str, past: dict, accounts: set) -> Optional[tuple[str, str]]:
    """(account, the past wording it came from), or None when nothing booked before is close enough."""
    k = norm(text)
    if not k:
        return None
    if k in past:
        acc = next((a for a, _ in past[k].most_common() if a in accounts), None)
        return (acc, k) if acc else None
    g = grams(k)
    best, score = None, 0.0
    for wording, counts in past.items():
        h = grams(wording)
        if not h:
            continue
        s = len(g & h) / len(g | h)
        if s > score:
            best, score = (wording, counts), s
    if best and score >= MIN_SIMILARITY:
        acc = next((a for a, _ in best[1].most_common() if a in accounts), None)
        return (acc, best[0]) if acc else None
    return None


def from_model(text: str) -> Optional[str]:
    from app import inbox_ai
    from app.extraction import FALLBACK, MODEL
    from app.config import settings
    if not settings.anthropic_api_key:
        return None
    chart = "\n".join(f"{c} {name} -- e.g. {ex}" for c, name, ex in inbox_ai.CHART)
    schema = {"type": "object", "properties": {"account": {"type": "string", "enum": inbox_ai.ACCOUNTS}}, "required": ["account"],
              "additionalProperties": False}
    try:
        r = inbox_ai._client().beta.messages.create(
            model=MODEL, max_tokens=2000, **FALLBACK,
            system="A Thai ice factory's petty-cash paper lists what money was spent on. Pick the ONE account this line is booked to.\n" + chart,
            messages=[{"role": "user", "content": f"Line: {text}"}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}})
        out = json.loads(next(b.text for b in r.content if b.type == "text"))
        return out["account"] if out.get("account") in inbox_ai.ACCOUNTS else None
    except Exception as exc:                            # noqa: BLE001 -- no suggestion: the page keeps the account as it is
        logger.warning("account suggestion failed: %r", exc)
        return None


def suggest(text: str) -> dict:
    """{"account": code or None, "source": "history" | "ai" | None, "like": the past wording it matched}."""
    from app import inbox_ai
    text = (text or "").strip()[:MAX_TEXT]
    if not text:
        return {"account": None, "source": None, "like": None}
    try:
        hit = from_history(text, history(), set(inbox_ai.ACCOUNTS))
    except Exception as exc:                            # noqa: BLE001 -- the books could not be read: ask the model
        logger.warning("account history failed: %r", exc)
        hit = None
    from app import wage_account
    staff = wage_account.people()
    if hit:
        return {"account": wage_account.account(text, hit[0], staff), "source": "history", "like": hit[1]}
    acc = from_model(text)
    return {"account": wage_account.account(text, acc, staff) if acc else None, "source": "ai" if acc else None, "like": None}
