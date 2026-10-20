"""
Re-sorts bill line items into categories from their TEXT alone -- no image.

Used by the review page when someone corrects a line's description (the
model read "brake" but it's actually oil): the category picked at
extraction time belongs to the OLD wording, so it gets re-decided from the
corrected text instead of making the reviewer re-pick it by hand.

Same 16 categories and the same close-call rules the extraction prompt uses
(imported from there, so the two can't drift apart). A cheaper, faster model
than extraction -- there's no handwriting to read here, just short text.
"""
import json
import logging
from typing import Optional

from app.extraction import CATEGORIES, EXTRACTION_SCHEMA, get_client

logger = logging.getLogger("vehicle_tracker.categorize")

MODEL = "claude-sonnet-5"
MAX_ITEMS = 80
MAX_DESCRIPTION_CHARS = 200
MAX_ATTEMPTS = 2

# The model decides in ENGLISH and the code maps back to the Thai names the
# rest of the app uses. Answering with the Thai labels directly gave wrong
# picks for items it had correctly identified (it called a battery "battery"
# and then filed it under filters & fluids) -- the failure was in choosing a
# Thai enum value, not in understanding the item. Same order as CATEGORIES;
# tests/test_categories.py checks it against schema.sql's name_en column.
CATEGORY_EN = [
    "Engine", "Transmission", "Clutch", "Drivetrain", "Suspension", "Steering", "Brakes",
    "Wheels & Tires", "Electrical", "Cooling", "Fuel System", "Air Conditioning",
    "Body & Chassis", "Filters & Fluids", "Labor", "Other",
]
assert len(CATEGORY_EN) == len(CATEGORIES)
EN_TO_TH = dict(zip(CATEGORY_EN, CATEGORIES))

_CATEGORY_RULES = EXTRACTION_SCHEMA["properties"]["line_items"]["items"]["properties"]["category"]["description"]

SYSTEM_PROMPT = f"""You sort lines from Thai vehicle repair bills (a fleet of \
Isuzu and Hino delivery trucks) into exactly one category each.

You get a numbered list of line items: a description in Thai, sometimes with \
a unit. For every line, first work out what the item physically IS (the \
`is` field), then pick its category. One entry per line, never skipping or \
merging lines; echo each line's number in `line`.

Judge by what the item physically IS and what it is for, not by surface \
wording -- the description may be informal, abbreviated, or slightly \
misspelled. Answer with the ENGLISH category names. The rules after the \
list are written with the Thai names, which correspond like this:
{chr(10).join(f"  {th} = {en}" for en, th in EN_TO_TH.items())}

Category rules:
{_CATEGORY_RULES}

Reminders of how those rules apply to common items:
- Brake FLUID / oil (น้ำมันเบรก) is a consumable -> ไส้กรองและของเหลว; brake \
PADS or shoes (ผ้าเบรก, ก้ามเบรก) are parts -> เบรก.
- Engine oil, gear oil, differential oil, coolant, grease, any filter -> \
ไส้กรองและของเหลว.
- A pure labor/service charge with no part named -> ค่าแรง."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "description": "Exactly one entry per input line.",
            "items": {
                "type": "object",
                "properties": {
                    "line": {"type": "integer", "description": "The line number from the input list."},
                    "is": {
                        "type": "string",
                        "description": (
                            "Plain English name of what this item physically IS, e.g. 'battery', "
                            "'brake pads', 'power-steering pump', 'propeller-shaft universal joint', "
                            "'truck tire', 'engine oil'. Decide this FIRST -- it is what the "
                            "category is based on."
                        ),
                    },
                    "category": {"type": "string", "enum": CATEGORY_EN},
                },
                "required": ["line", "is", "category"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lines"],
    "additionalProperties": False,
}


def categorize(items: list[dict]) -> list[Optional[str]]:
    """
    items: [{"description": str, "unit": str | None}, ...]

    Deliberately text-only, no bill context: trying the bill's other lines
    as hints made ambiguous items (dust seals) LESS stable, not more, in
    testing -- 5/5 -> 3/5 on the same wording.
    Returns one category per item, same order. Raises RuntimeError if the
    model couldn't produce a well-formed answer after MAX_ATTEMPTS -- the
    caller decides what to tell the user; nothing here is guessed.
    """
    if not items:
        return []
    lines = []
    for i, it in enumerate(items, start=1):
        text = (it.get("description") or "").strip()[:MAX_DESCRIPTION_CHARS]
        unit = (it.get("unit") or "").strip()
        lines.append(f"{i}. {text}" + (f"  (หน่วย: {unit})" if unit else ""))
    prompt = "Categorize these bill lines:\n" + "\n".join(lines)

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = get_client().messages.create(
                model=MODEL,
                max_tokens=1500,
                thinking={"type": "disabled"},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            )
            text_block = next(b.text for b in response.content if b.type == "text")
            entries = json.loads(text_block)["lines"]
            by_line = {e["line"]: EN_TO_TH[e["category"]] for e in entries}
            if sorted(by_line) != list(range(1, len(items) + 1)):
                raise ValueError(f"expected lines 1..{len(items)}, got {sorted(by_line)}")
            return [by_line[i] for i in range(1, len(items) + 1)]
        except Exception as exc:
            last_error = exc
            logger.warning("categorize attempt %d/%d failed: %r", attempt, MAX_ATTEMPTS, exc)
    raise RuntimeError(f"categorize failed after {MAX_ATTEMPTS} attempts: {last_error!r}")
