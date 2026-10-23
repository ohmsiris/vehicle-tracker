"""
Reads ONE purchase bill (one or several photos / PDF pages of the same bill) via Claude and returns structured data:
the supplier, the date, the bill number, the VAT, the total and every line with quantity, unit and amounts. Nothing is
saved -- app/purchasing.py matches the supplier and the items and the review page edits it before saving.

Same model, client and file handling as the repair-bill reader (app/extraction.py); a different schema, because a
purchase bill is about how many of what at which price, not which truck.
"""
import json
import logging
from typing import Optional

from app.extraction import MODEL, _client_lazy, _content_block

logger = logging.getLogger("vehicle_tracker.purchase_extraction")

MAX_PARSE_ATTEMPTS = 2
HEADER_FIELDS = ["vendor_name", "bill_no", "date", "vat_mode", "subtotal", "vat_amount", "discount", "total"]
LINE_FIELDS = ["description", "quantity", "unit", "unit_price", "line_total"]

_num = {"anyOf": [{"type": "number"}, {"type": "null"}]}      # structured outputs take nullable as anyOf, not a type list
_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "vendor_name": {**_str, "description": "The seller's shop / company name as printed (the one issuing the bill, not the buyer)."},
        "vendor_tax_id": {**_str, "description": "The seller's 13-digit tax ID if printed."},
        "vendor_phone": _str,
        "bill_no": {**_str, "description": "Bill / receipt / tax invoice number (เลขที่)."},
        "date": {**_str, "description": "Bill date as YYYY-MM-DD in the Gregorian calendar. Thai bills often use the Buddhist "
                                        "Era (e.g. 2568 or 68): subtract 543 (2568 -> 2025). null if not readable."},
        "vat_mode": {"type": "string", "enum": ["included", "excluded", "none"],
                     "description": "excluded = the lines are before VAT and 7% VAT is added at the bottom; included = prices "
                                    "already include VAT (ราคารวมภาษีแล้ว) ; none = no VAT on this bill."},
        "subtotal": {**_num, "description": "Sum of the lines before VAT / discount, if printed."},
        "discount": {**_num, "description": "Discount on the whole bill (ส่วนลด), if any."},
        "vat_amount": {**_num, "description": "VAT amount (ภาษีมูลค่าเพิ่ม 7%), if printed."},
        "total": {**_num, "description": "The grand total actually to pay (รวมทั้งสิ้น / ยอดสุทธิ)."},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "description": {"type": "string", "description": "The item NAME only, in full, as printed in the description column (keep brand, model, size and spec that are part of the name, e.g. 'ลูกปืน 6205-2RS NSK'). Do NOT include the code / SKU / reference column (รหัส, รหัสสินค้า, Ref, Part No, barcode) or the row number: leave those out completely."},
                    "quantity": _num,
                    "unit": {**_str, "description": "ตัว / ชิ้น / เส้น / ลิตร / กล่อง / ม้วน / เมตร / ชุด ..."},
                    "unit_price": {**_num, "description": "Price of one unit as printed."},
                    "discount": {**_num, "description": "Discount on this line in baht, if any."},
                    "line_total": {**_num, "description": "The amount of this line as printed (after its own discount)."},
                    "low_confidence_fields": {"type": "array", "items": {"type": "string", "enum": LINE_FIELDS}},
                },
                "required": ["description", "quantity", "unit", "unit_price", "discount", "line_total", "low_confidence_fields"],
            },
        },
        "low_confidence_fields": {"type": "array", "items": {"type": "string", "enum": HEADER_FIELDS}},
    },
    "required": ["vendor_name", "vendor_tax_id", "vendor_phone", "bill_no", "date", "vat_mode", "subtotal", "discount",
                 "vat_amount", "total", "line_items", "low_confidence_fields"],
}

SYSTEM_PROMPT = """You read purchase bills, receipts and tax invoices of a Thai ice factory buying parts and supplies for
its machines (bearings, belts, motors, pumps, breakers, contactors, pipes, fittings, oil, grease, tools, chemicals ...).
The bills are in Thai, often handwritten (บิลเงินสด) or printed by small shops. Extract exactly what is written.

Rules:
- One bill may be several images / pages: read them all as ONE bill (lines continue across pages; one header and total).
- Item names: write each line's description as the full item name from the description column and nothing from the code
  columns. Many bills have a first column with a product code / SKU / reference number (รหัสสินค้า, รหัส, Ref., Part No.,
  barcode) before the item name, and a row number (ลำดับ): ignore those columns, we do not use them. Keep the whole name
  itself, including any brand, model number, size or spec that is part of the name (a bearing named "6205-2RS NSK" keeps
  its "6205-2RS NSK"; the code printed in the code column is not part of the name).
- Keep every line, in order, including services (ค่าแรง, ค่าขนส่ง). Do not merge or invent lines. Skip only lines that
  are clearly not purchases (signatures, notes, bank details).
- Numbers: plain numbers without commas or currency. A quantity like "2 เส้น" -> quantity 2, unit "เส้น".
- If a line's amount is missing but quantity and unit price are there, leave line_total null (it will be computed).
- Dates: convert Buddhist Era to Gregorian (subtract 543). Two-digit years like 68 mean 2568 BE = 2025.
- The vendor is the SELLER (the shop that issued the bill), not the buyer.
- List in low_confidence_fields the fields you could not read clearly or had to guess. Be honest: the reviewer checks
  exactly those."""


def _validate(bill: dict) -> None:
    missing = [k for k in SCHEMA["required"] if k not in bill]
    if missing:
        raise ValueError(f"extraction missing fields: {missing}")
    if not isinstance(bill["line_items"], list):
        raise ValueError("line_items is not a list")


def read_bill(files: list[tuple[bytes, str]]) -> tuple[dict, dict]:
    """files = [(bytes, media type)] of ONE bill -> (extraction, usage). Raises after MAX_PARSE_ATTEMPTS."""
    content = [_content_block(data, mime) for data, mime in files]
    content.append({"type": "text", "text": "Read this purchase bill and extract the data."})
    last: Optional[Exception] = None
    for attempt in range(1, MAX_PARSE_ATTEMPTS + 1):
        try:
            response = _client_lazy().messages.create(
                model=MODEL,
                max_tokens=8000,
                system=[{"type": "text", "text": SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": content}],
                output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            )
            text = next(b.text for b in response.content if b.type == "text")
            bill = json.loads(text)
            _validate(bill)
            u = response.usage
            return bill, {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens}
        except Exception as exc:
            last = exc
            logger.warning("purchase extraction attempt %d/%d failed: %r", attempt, MAX_PARSE_ATTEMPTS, exc)
    raise RuntimeError(f"purchase extraction failed after {MAX_PARSE_ATTEMPTS} attempts: {last!r}")
