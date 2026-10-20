"""
Reads ONE bill photo or PDF via Claude and returns structured data. Nothing
is saved anywhere -- see app/batch.py for combining several files into one
draft event, and app/main.py for the endpoint.

The category list, schema and system prompt below are ported verbatim from
the NongTick repo's app/bill_extraction.py (tuned against several hundred
real bills there), with two additions:

  * `low_confidence_fields` -- the model lists which header fields / which
    fields of each line item it wasn't sure about, so the review page can
    highlight exactly those inputs instead of a single yes/no.
  * the roster is passed in (see app/roster.py) rather than read from a
    module-level path.

Keep CATEGORIES identical to the categories table in schema.sql --
tests/test_categories.py checks that.
"""
import base64
import json
import logging
from typing import Optional

from anthropic import Anthropic

from app.config import settings

logger = logging.getLogger("vehicle_tracker.extraction")

_client: Optional[Anthropic] = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        # SDK-level retries cover 429/5xx/connection errors; the loop in
        # extract_file() covers a response that comes back but doesn't parse.
        _client = Anthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=240.0)
    return _client


_client_lazy = get_client


# Same stronger/pricier model the NongTick bot uses for bills -- OCR on
# messy handwriting benefits meaningfully from it.
MODEL = "claude-opus-5"
MAX_PARSE_ATTEMPTS = 2

# Header fields the model may flag as uncertain / per-line fields likewise.
HEADER_CONFIDENCE_FIELDS = [
    "shop_name", "date", "branch", "vehicle_license", "vehicle_number",
    "mileage", "next_service_mileage", "total_cost",
]
LINE_CONFIDENCE_FIELDS = ["quantity", "unit", "description", "cost", "category"]

CATEGORIES = [
    "เครื่องยนต์",       # Engine
    "เกียร์",            # Transmission
    "คลัตช์",            # Clutch
    "ระบบส่งกำลัง",       # Drivetrain
    "ช่วงล่าง",           # Suspension (includes bearings/seals -- see category rules below)
    "พวงมาลัย",          # Steering
    "เบรก",              # Brakes
    "ล้อและยาง",         # Wheels & Tires
    "ระบบไฟฟ้า",         # Electrical
    "ระบบหล่อเย็น",       # Cooling
    "ระบบเชื้อเพลิง",     # Fuel System
    "ระบบแอร์",           # Air Conditioning
    "ตัวถังและโครงรถ",     # Body & Chassis
    "ไส้กรองและของเหลว",  # Filters & Fluids / consumables
    "ค่าแรง",            # Labor
    "อื่นๆ",             # Other (catch-all)
]

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "shop_name": {
            "type": "string",
            "description": "Name of the repair shop, in the original language exactly as written on the bill (do not translate or add an English transliteration in parentheses). Empty string if not legible or not present.",
        },
        "date": {
            "type": "string",
            "description": (
                "Date of the bill, converted to YYYY-MM-DD format if possible. "
                "Thai bills often use the Buddhist Era year (e.g. 2567) instead "
                "of the Christian/Gregorian year -- if you see a year in the "
                "range 2500-2600, subtract 543 to get the Gregorian year "
                "(e.g. 2567 - 543 = 2024) before writing the date. If the date "
                "cannot be determined at all, use an empty string."
            ),
        },
        "branch": {
            "type": "string",
            "description": (
                "Which branch of the business the truck belongs to, if "
                "stated -- e.g. a note reading 'รถของสระบุรี' means Saraburi "
                "branch, 'รถของแก่งคอย' means Kaeng Khoi branch. A dealership "
                "document may instead show the branch as part of the "
                "service center's own name (e.g. '...ศูนย์ซ่อมสีและตัวถัง"
                "แก่งคอย' means the Kaeng Khoi service center) -- if so, "
                "just write the branch name (e.g. 'แก่งคอย'), not the whole "
                "shop name again. Empty string if the branch isn't stated "
                "anywhere on the bill."
            ),
        },
        "vehicle_license": {
            "type": "string",
            "description": (
                "The vehicle's plate NUMBER ONLY, written in the original "
                "language exactly as shown on the bill (do not translate or "
                "add an English transliteration) -- do NOT include the "
                "province name here, that goes in vehicle_license_province "
                "below (e.g. a plate reading '1ฒย 4267 กทม' becomes "
                "vehicle_license='1ฒย 4267' and vehicle_license_province="
                "'กทม'). This is NOT always near the top of the bill -- "
                "look over the WHOLE page, since it's sometimes written in "
                "the middle of the page instead. A reference list of the "
                "known fleet may be provided below -- this is a small, "
                "mostly closed fleet (around 50-70 vehicles total across "
                "both branches), so a plate that doesn't appear on it is "
                "the unusual case, not the norm, and is more often a "
                "misread digit than a genuinely new vehicle. If your first "
                "reading doesn't match anything on the list, look again "
                "character by character before finalizing -- check "
                "specifically for commonly confused digits (4/9, 3/8, 1/7, "
                "0/6) -- and only keep a no-match reading if you're still "
                "confident after that second look. That said, never force "
                "a clearly-legible plate to match the list just because "
                "it's close if you're genuinely confident in what's "
                "written -- the fleet does occasionally add new vehicles "
                "or use a rented/dealership loaner truck, so a real "
                "no-match is still possible. Empty string if no plate is "
                "present or legible at all."
            ),
        },
        "vehicle_license_province": {
            "type": "string",
            "description": (
                "The province name shown with the plate, if any (Thai "
                "plates commonly show one below/beside the number) -- "
                "common ones for this fleet include กทม/กรุงเทพมหานคร "
                "(Bangkok), นนทบุรี (Nonthaburi), สระบุรี (Saraburi), and "
                "ยโสธร (Yasothon), but transcribe whatever province is "
                "actually written, even if it's not one of these. Keep "
                "whatever abbreviation/form is actually written (don't "
                "expand 'กทม' to the full 'กรุงเทพมหานคร' or vice versa). "
                "Empty string if no province is shown."
            ),
        },
        "vehicle_number": {
            "type": "string",
            "description": (
                "The fleet truck number, ONLY if it's actually written on "
                "the bill itself (e.g. 'เบอร์ 18', 'รถเบอร์ 1', or similar) "
                "-- write ONLY the number itself as plain digits (e.g. "
                "'18'), NEVER include the word 'เบอร์'/'รถเบอร์' or any "
                "other label text in this field, since it gets displayed "
                "elsewhere already labeled as a truck number. Do not infer "
                "or guess this from the plate, even if a reference list "
                "below happens to show a matching plate; a separate, more "
                "reliable lookup step handles that afterward. Empty string "
                "if no truck number is written on the bill."
            ),
        },
        "mileage": {
            "type": "string",
            "description": (
                "ALWAYS actively check the BOTTOM of the page for a "
                "mileage/odometer note, regardless of what else is on the "
                "bill -- this is easy to overlook because some shops write "
                "it as a small handwritten annotation in RED ink "
                "(different from the black ink used for the rest of the "
                "bill), separate from the main line-item table, and it's "
                "easy to mistake for a stray mark instead of real data. "
                "One common format: 'เลขไมล์[ปัจจุบัน] 650316 ครั้งต่อไป "
                "660316' -- this means CURRENT mileage 650316 and NEXT "
                "SERVICE DUE mileage 660316; put only the first (current) "
                "number here as plain digits with no commas or units (the "
                "second number goes in next_service_mileage below). If the "
                "shop instead wrote that the odometer was broken, write "
                "exactly 'ไมล์เสีย' instead of a number. Only use an empty "
                "string if you've actually checked the bottom of the page "
                "carefully and there's truly no mileage note there. As "
                "general background: dealership documents almost always "
                "show mileage, while independent mechanic shops more often "
                "write it when there's an engine oil change (น้ำมันเครื่อง) "
                "on the bill -- but that's just a pattern, not a reason to "
                "stop looking; some shops note it even without one."
            ),
        },
        "next_service_mileage": {
            "type": "string",
            "description": (
                "The NEXT service due mileage, if the bill shows one "
                "alongside the current mileage (e.g. the 'ครั้งต่อไป "
                "660316' part of 'เลขไมล์ 650316 ครั้งต่อไป 660316' means "
                "next service due at 660316) -- plain digits, no commas or "
                "units. Empty string if only a current mileage is shown "
                "with no next-service number, or if there's no mileage "
                "note at all."
            ),
        },
        "total_cost": {
            "type": "number",
            "description": (
                "The grand total shown on THIS PHOTO ONLY, in Thai Baht -- "
                "the actual final amount owed/paid, not a pre-tax subtotal. "
                "Some dealership documents show several totals (before "
                "discount, after discount, VAT 7%, then a final 'net "
                "amount' / จำนวนเงินรวมสุทธิ) -- always use that final net "
                "amount including VAT when multiple totals are shown, since "
                "that's what's actually paid. Use 0 if no grand total "
                "appears on this specific photo -- for example, this is "
                "normal for the first page of a bill that continues onto a "
                "second photo, where only a running subtotal (ยอดยกไป) may "
                "appear instead."
            ),
        },
        "has_total_this_page": {
            "type": "boolean",
            "description": "True if a grand total (รวมเงิน) is shown on this photo. False if this photo only shows a running subtotal, or no total at all -- which usually means the bill continues on another photo.",
        },
        "continues_next_page": {
            "type": "boolean",
            "description": "True if this photo shows a 'ยอดยกไป' note (often with an arrow) near the bottom, indicating the bill's line items continue onto another photo. False for a normal single-page bill or the final page of a multi-page bill.",
        },
        "low_confidence_fields": {
            "type": "array",
            "items": {"type": "string", "enum": HEADER_CONFIDENCE_FIELDS},
            "description": "Header fields on this photo that you are not sure you read correctly (blurry, ambiguous digits, inferred rather than read, or left empty because illegible). Empty array if all are clearly legible.",
        },
        "line_items": {
            "type": "array",
            "description": "Every individual repair or part listed on the bill, in the order they appear.",
            "items": {
                "type": "object",
                "properties": {
                    "quantity": {
                        "type": "number",
                        "description": (
                            "The quantity/count from the left-most column of "
                            "the line item table, if the bill has one (e.g. "
                            "4 for a row showing '4' next to 'brake shoes'). "
                            "Use 0 if there is no quantity column or it "
                            "isn't legible. Labor/service charges (ค่าแรง "
                            "etc.) normally have NO quantity written at all "
                            "-- that's expected, not a legibility problem, "
                            "so use 0 rather than guessing a plausible-"
                            "looking number like 1."
                        ),
                    },
                    "unit": {
                        "type": "string",
                        "description": (
                            "The unit written next to the quantity, if any "
                            "(e.g. ชิ้น, ตัว, ลิตร, คู่). Empty string if no "
                            "unit is shown -- this is expected and normal for "
                            "labor/service charges (ค่าแรง etc.), which "
                            "usually have no unit written at all; don't "
                            "guess one. Helpful context: liquid oils "
                            "(engine oil น้ำมันเครื่อง, gear oil น้ำมันเกียร์, "
                            "differential oil น้ำมันเฟืองท้าย, brake fluid "
                            "น้ำมันเบรค, etc.) are almost always measured in "
                            "ลิตร (liters) -- if the handwriting is ambiguous "
                            "for one of these, ลิตร is far more likely than "
                            "กิโล. Grease (จารบี) is the notable exception and "
                            "is commonly measured by weight in กิโล instead. "
                            "Also watch for these specific unit misreadings "
                            "observed before: ก.ป misread as ก.ล or ถ.ม; ชุด "
                            "misread as อัน; หัว misread as ชิ้น; คัน misread "
                            "as ตัว or เส้น."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "The repair or part description exactly as written on the bill, in the original language, word for word -- do not translate, paraphrase, reword, or 'clean up' the text, and do not fold the quantity into this field since it has its own field above. If a word is genuinely illegible, write [illegible] in place of that word rather than guessing.",
                    },
                    "cost": {
                        "type": "number",
                        "description": "Cost of this individual line item in Thai Baht. Use 0 if not itemized or not legible.",
                    },
                    "category": {
                        "type": "string",
                        "enum": CATEGORIES,
                        "description": (
                            "The single best-fit category for this line item. Rules "
                            "for close calls:\n"
                            "1. Consumables win over location: any lubricant, fluid, "
                            "oil, grease, or filter goes in 'ไส้กรองและของเหลว' "
                            "(Filters & Fluids) even if it's used on a specific system "
                            "-- e.g. wheel grease (จารบีล้อ) is 'ไส้กรองและของเหลว', "
                            "NOT 'ช่วงล่าง' (Suspension), because it's a consumable, "
                            "not a physical part.\n"
                            "2. Bearings, seals, and other physical wear parts are "
                            "categorized by where they're installed, e.g. a wheel "
                            "bearing/seal (ลูกปืนล้อ, ซีลล้อ) goes under 'ช่วงล่าง' "
                            "(Suspension).\n"
                            "3. Labor/service/diagnostic-only charges (no specific "
                            "part named, e.g. ค่าแรง, ค่าบริการ, ค่าตรวจเช็ค) go under "
                            "'ค่าแรง' (Labor), even if the labor was for a specific "
                            "system -- parts and labor should be tracked separately.\n"
                            "4. Only use 'อื่นๆ' (Other) if nothing else genuinely fits."
                        ),
                    },
                    "low_confidence_fields": {
                        "type": "array",
                        "items": {"type": "string", "enum": LINE_CONFIDENCE_FIELDS},
                        "description": "Fields of THIS line item that you are not sure you read correctly. Empty array if the whole line is clearly legible.",
                    },
                },
                "required": ["quantity", "unit", "description", "cost", "category", "low_confidence_fields"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "shop_name", "date", "branch", "vehicle_license", "vehicle_license_province",
        "vehicle_number", "mileage", "next_service_mileage", "total_cost",
        "has_total_this_page", "continues_next_page", "line_items", "low_confidence_fields",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are an expert at reading Thai vehicle repair bills for \
a fleet of Isuzu and Hino delivery trucks used in an ice delivery business \
in Saraburi, Thailand. These come in two forms: informal handwritten bills \
from independent mechanic shops, and official printed quotations/invoices \
from dealerships (e.g. Isuzu service centers) -- apply the same care to \
both, even though the printed ones are much easier to read accurately.

Read the bill photo carefully and extract every line item, even if the \
handwriting is messy or the photo quality is imperfect. If a field is \
illegible or missing, use an empty string (or 0 for numbers) rather than \
guessing a value.

Look at the ENTIRE page, not just the main table -- some shops add small \
handwritten annotations near the edges or bottom of the bill, sometimes \
in a different ink color (e.g. red) than the rest of the bill. Don't \
mistake these for stray marks; check whether they contain real data \
(mileage notes are a common example).

Keep all text fields in Thai, exactly as written on the bill. Do not \
translate anything into English or add an English transliteration, even \
in parentheses -- write only what is actually on the bill.

Read the line item table as a whole, column by column, the way it is laid \
out on the bill (quantity column, description column, price column, etc.). \
Transcribe descriptions verbatim -- do not reword, summarize, or "clean up" \
the wording, even if it seems repetitive or informally written.

Some bills span more than one photo (the line items continue onto a second \
page). Watch specifically for a handwritten "ยอดยกไป" note, often with an \
arrow, near the bottom of the page -- this means the bill continues on \
another photo, and this page will NOT show a grand total (รวมเงิน), only a \
running subtotal at most. Report this accurately in has_total_this_page \
and continues_next_page -- a separate step will stitch multi-page bills \
back together, so just describe THIS PHOTO accurately rather than trying \
to guess what's on another photo.

Specific misreadings observed before on these shops' handwriting -- look \
twice before writing any of the "misread as" versions below, and check \
which one is actually written:
- "แหนบ" (leaf spring) has been misread as "แท่นบน" (upper mount).
- "หัวฉีด" (fuel injector) has been misread as "น๊อตยึด" (mounting bolt).
- The unit abbreviation "ก.ป" has been misread as "ก.ล" -- these look \
similar handwritten; check the last character carefully."""

CONFIDENCE_PROMPT_ADDENDUM = """

Confidence flags: in `low_confidence_fields` (top level) list every header \
field you are NOT sure you read correctly -- blurry or smudged writing, \
digits that could be read two ways, a value you had to infer rather than \
read, or a field you left empty because you couldn't make it out. Do the \
same inside each line item for that line's own fields. Be honest rather \
than optimistic: a field in the list costs the reviewer two seconds to \
double-check, while a wrong value left unflagged goes into the records. \
Leave the list empty only when everything on that level is clearly \
legible."""

SYSTEM_PROMPT = SYSTEM_PROMPT + CONFIDENCE_PROMPT_ADDENDUM

_REQUIRED_TOP = EXTRACTION_SCHEMA["required"]


def _content_block(file_bytes: bytes, media_type: str) -> dict:
    """PDFs use a 'document' block -- Claude reads it visually page by page,
    which sidesteps garbled text layers some Thai business software makes."""
    b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
    kind = "document" if media_type == "application/pdf" else "image"
    return {"type": kind, "source": {"type": "base64", "media_type": media_type, "data": b64}}


def _validate(bill: dict) -> None:
    missing = [k for k in _REQUIRED_TOP if k not in bill]
    if missing:
        raise ValueError(f"extraction missing fields: {missing}")
    if not isinstance(bill["line_items"], list):
        raise ValueError("line_items is not a list")


def extract_file(file_bytes: bytes, media_type: str, roster_text: str = "") -> tuple[dict, dict]:
    """
    Sends one bill file to Claude. Returns (extraction_dict, usage_dict).
    Raises on failure after MAX_PARSE_ATTEMPTS -- the caller (batch endpoint)
    turns that into a per-file error so one bad file doesn't sink the batch.
    """
    # The fleet list is ~10k tokens and identical on every call, so it goes in
    # a cached system block (before the per-file image) rather than the user
    # turn -- repeat uploads within the cache window read it at a fraction of
    # the input price. Parallel calls in the SAME batch start together and
    # won't see each other's cache; the saving shows up across batches.
    system_blocks = [{"type": "text", "text": SYSTEM_PROMPT}]
    if roster_text:
        system_blocks.append({"type": "text", "text": roster_text, "cache_control": {"type": "ephemeral"}})
    prompt_text = "Read this repair bill and extract the data."

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_PARSE_ATTEMPTS + 1):
        try:
            response = _client_lazy().messages.create(
                model=MODEL,
                max_tokens=8000,
                system=system_blocks,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            _content_block(file_bytes, media_type),
                            {"type": "text", "text": prompt_text},
                        ],
                    }
                ],
                output_config={
                    "effort": "high",
                    "format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA},
                },
            )
            text_block = next(b.text for b in response.content if b.type == "text")
            bill = json.loads(text_block)
            _validate(bill)
            u = response.usage
            usage = {
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            }
            return bill, usage
        except Exception as exc:
            last_error = exc
            logger.warning("extraction attempt %d/%d failed: %r", attempt, MAX_PARSE_ATTEMPTS, exc)
    raise RuntimeError(f"extraction failed after {MAX_PARSE_ATTEMPTS} attempts: {last_error!r}")
