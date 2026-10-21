"""
A one-line "what was done" for a repair event, written from its line items.

Shown in the history list so you can tell the visits apart at a glance
("ถ่ายน้ำมันเครื่อง เปลี่ยนไส้กรอง จาระบีล้อ ซีลล้อหลัง") instead of reading
every bill. Text only, no image -- the lines were already read and checked.

Never blocks anything: if the model call fails the event simply keeps no
summary and the history list falls back to naming its biggest line items.
fill_summary() only ever writes into an EMPTY summary, so a summary a person
typed is never overwritten.
"""
import json
import logging
from typing import Optional

from app import supabase
from app.extraction import get_client

logger = logging.getLogger("vehicle_tracker.summarize")

MODEL = "claude-sonnet-5"
MAX_LINES = 60
MAX_DESCRIPTION_CHARS = 120
MAX_SUMMARY_CHARS = 160     # the column allows 500; this keeps it to one readable line
MAX_ATTEMPTS = 2

SYSTEM_PROMPT = """You write one-line summaries of vehicle repair visits for a \
fleet of Isuzu and Hino delivery trucks in Thailand. You are given the line \
items of one repair bill (Thai text, usually handwritten and informal).

Write ONE short Thai line saying what WORK was done on the truck, so someone \
scanning a history list can tell visits apart.

Rules:
- Name the jobs, not every part: "ถ่ายน้ำมันเครื่อง เปลี่ยนไส้กรอง" rather than \
listing each filter and each litre. Group related parts into the job they belong \
to (wheel bearings + seals + grease -> "ซ่อมลูกปืนล้อ"; brake pads + brake fluid -> \
"เปลี่ยนผ้าเบรก").
- Most important work first. Mention labour only if it is the ONLY thing on the bill.
- Use only what is on the bill. Never guess a cause or add work that is not listed. \
If a line is unclear, leave it out rather than invent it.
- No prices, no quantities, no shop name, no date, no truck number.
- At most about 90 characters. Plain words, no punctuation at the end, no quotes.
- If the bill gives nothing you can name, answer with an empty string."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string", "description": "The one-line Thai summary, or an empty string."}},
    "required": ["summary"],
    "additionalProperties": False,
}


def _clean(text: str) -> str:
    one_line = " ".join((text or "").split()).strip(" \t\"'“”.,;:-")
    return one_line[:MAX_SUMMARY_CHARS].rstrip()


def summarize(lines: list[dict], context: Optional[dict] = None) -> Optional[str]:
    """
    lines: [{"description", "category", "quantity", "unit"}, ...]. Returns the
    summary, or None if there is nothing to summarize or the model failed /
    answered with nothing usable (the caller then just leaves the field empty).
    """
    usable = [l for l in lines if (l.get("description") or "").strip()][:MAX_LINES]
    if not usable:
        return None
    rows = []
    for i, l in enumerate(usable, start=1):
        qty = l.get("quantity")
        bits = [f"{i}. {(l['description'] or '').strip()[:MAX_DESCRIPTION_CHARS]}"]
        if l.get("category"):
            bits.append(f"[{l['category']}]")
        if qty not in (None, "", 0):
            bits.append(f"x{qty}{(' ' + l['unit']) if l.get('unit') else ''}")
        rows.append(" ".join(bits))
    prompt = "Bill lines:\n" + "\n".join(rows)
    who = (context or {}).get("performed_by")
    if who:
        prompt += f"\n\n(Done in-house by: {who})"

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = get_client().messages.create(
                model=MODEL,
                max_tokens=300,
                thinking={"type": "disabled"},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            )
            text_block = next(b.text for b in response.content if b.type == "text")
            return _clean(json.loads(text_block)["summary"]) or None
        except Exception as exc:
            last_error = exc
            logger.warning("summarize attempt %d/%d failed: %r", attempt, MAX_ATTEMPTS, exc)
    logger.error("summarize gave up: %r", last_error)
    return None


def fill_summary(event_id: str) -> Optional[str]:
    """
    Writes an AI summary into a saved event IF it has none yet. Safe to call
    any time (after a save, from the backfill script): it does nothing when
    schema_v4.sql hasn't been run, when the event already has a summary, or
    when the model has nothing to say. Returns the summary written, or None.
    """
    from app import history       # local import: history imports nothing from here, but keep the graph one-way
    if not history._has_summary():
        return None
    rows = supabase.select("maintenance_events", {"select": "id,summary,performed_by", "id": f"eq.{event_id}", "status": "eq.confirmed", "limit": "1"})
    if not rows or (rows[0].get("summary") or "").strip():
        return None
    lines = supabase.select_all("bill_line_items", {
        "select": "line_number,description,category,quantity,unit", "event_id": f"eq.{event_id}", "order": "line_number.asc"})
    text = summarize(lines, {"performed_by": rows[0].get("performed_by")})
    if not text:
        return None
    # Conditional on "still empty": if someone typed their own summary while the model was thinking, theirs wins.
    done = supabase.update("maintenance_events", {"id": f"eq.{event_id}", "summary": "is.null"}, {"summary": text})
    return text if done else None
