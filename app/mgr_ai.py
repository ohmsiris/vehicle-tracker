"""
Picks the type of a manager's ticket from what was typed ("โทรตามอะไหล่ชมรม" -> ตามงาน / ตามของ).

Claude reads the title and notes and answers with one of the types (structured output, so it can only answer with a real
key). If that fails -- no API key, no network, a bad answer -- a keyword rule gives a best guess instead, and the ticket
is marked as picked by the rule, so nothing ever blocks on the AI. The type can always be changed by hand; a ticket whose
type was chosen by hand is never touched again.
"""
import json
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger("vehicle_tracker.mgr_ai")

TIMEOUT_SECONDS = 20

# What each default type means -- told to the model, and the words the fallback rule looks for.
GUIDE = {
    "chase": ("ตามงาน / ตามของ: chasing suppliers, shops, parts, deliveries, quotes, someone who owes an answer",
              ["ตาม", "เร่ง", "ยังไม่ส่ง", "ยังไม่มา", "รออะไหล่", "ของยังไม่", "ส่งของ", "โทรถาม"]),
    "finance": ("การเงิน: money owed to us, bills and wages to pay, bank, transfers, cheques, loans, accounts",
                ["จ่าย", "เงิน", "โอน", "ธนาคาร", "เช็ค", "กู้", "หนี้", "บัญชี", "ค่าไฟ", "ค่าน้ำ", "ค่าเช่า", "บิล", "ใบแจ้งหนี้", "เก็บเงิน"]),
    "fix": ("แก้ปัญหา / ซ่อม: something broken or not working -- machine, truck, building, electrics, water, equipment",
            ["ซ่อม", "เสีย", "พัง", "รั่ว", "แก้", "ชำรุด", "ไม่ทำงาน", "ตัน", "ไฟดับ", "ท่อ", "ปั๊ม", "มอเตอร์", "คอมเพรสเซอร์"]),
    "external": ("ติดต่อภายนอก: government and tax offices, licences, insurance, vehicle tax, พรบ., the industrial office, officials",
                 ["สรรพากร", "ภาษี", "ขนส่ง", "พรบ", "ประกัน", "ใบอนุญาต", "อุตสาหกรรม", "เทศบาล", "อบต", "ราชการ", "ทะเบียนรถ", "ต่อประกัน", "ต่อภาษี", "ประกันสังคม"]),
    "customer": ("ลูกค้า / ขาย: customers, sales, prices, complaints, new accounts, collecting orders",
                 ["ลูกค้า", "ขาย", "ราคา", "ร้องเรียน", "เปิดบัญชี", "ออเดอร์", "ใบสั่งซื้อลูกค้า", "ตัวแทน"]),
    "purchase": ("จัดซื้อ: purchasing for the business -- orders, quotes, comparing suppliers, spare parts and materials",
                 ["สั่งซื้อ", "ใบเสนอราคา", "จัดซื้อ", "เปรียบเทียบราคา", "สั่งของ", "po"]),
    "people": ("คน: staff, hiring, salary, leave, driver problems, meetings with people, training",
               ["พนักงาน", "สมัครงาน", "เงินเดือน", "ลาออก", "ลางาน", "ประชุม", "คนขับ", "อบรม", "รับสมัคร", "ตักเตือน", "โอที"]),
    "visit": ("ตรวจ / เยี่ยมโรงงาน: real visits and inspections -- rounds, GMP, safety, ammonia, cleanliness, quality checks",
              ["ตรวจ", "เยี่ยม", "เดินโรงงาน", "gmp", "ความปลอดภัย", "แอมโมเนีย", "สะอาด", "ตรวจสอบ", "ออดิท", "audit", "เดินตรวจ"]),
    "decide": ("ตัดสินใจ / โครงการ: decisions to make, plans, projects, ideas, things to think through",
               ["ตัดสินใจ", "โครงการ", "แผน", "ไอเดีย", "คิด", "วางแผน", "ลงทุน", "ขยาย"]),
    "shopping": ("ซื้อของ: things to buy -- errands, supplies, everyday items (not formal purchasing)",
                 ["ซื้อ", "ซื้อของ", "ไปซื้อ", "หาซื้อ", "ฝากซื้อ"]),
    "report": ("รายงาน: things to report or tell Mum / Grandma / the family or partners; summaries to give someone",
               ["รายงาน", "แจ้งแม่", "แจ้งยาย", "บอกแม่", "บอกยาย", "สรุป", "รายงานแม่", "รายงานยาย"]),
    "personal": ("ส่วนตัว: personal and family matters -- home, health, doctor, children",
                 ["ส่วนตัว", "บ้าน", "ครอบครัว", "หมอ", "ลูก", "สุขภาพ", "โรงพยาบาล"]),
    "other": ("อื่นๆ: anything that fits none of the others", []),
}

SYSTEM_PROMPT = """You sort tickets written by the owner of a Thai ice factory (Saraburi and Kaeng Khoi, with a truck fleet) into exactly one type. \
The text is short and informal Thai, sometimes mixed with English. Judge by what the person actually has to DO or deal with, not by single words. \
If nothing fits, use "other". Answer with the key only."""


def keyword_type(text: str, keys: list[str]) -> str:
    """Best guess from keywords: the type with the most (and most specific) words in the text; 'other' if none."""
    t = (text or "").lower().replace(" ", "")
    best, best_n = None, 0
    for key in keys:
        words = GUIDE.get(key, ("", []))[1]
        n = sum(len(w.replace(" ", "")) for w in words if w.replace(" ", "") in t)       # longer, more specific words count for more
        if n > best_n:
            best, best_n = key, n
    return best or ("other" if "other" in keys else keys[-1])


def _ask(title: str, notes: Optional[str], types: list[dict]) -> str:
    from app.extraction import get_client                       # imported here: the pure helpers above must not need the SDK
    from app.categorize import MODEL
    keys = [t["key"] for t in types]
    lines = [f"- {t['key']}: {GUIDE[t['key']][0] if t['key'] in GUIDE else t['label']}" for t in types]
    prompt = "Types:\n" + "\n".join(lines) + f"\n\nTicket title: {title}" + (f"\nNotes: {notes}" if notes else "")
    schema = {"type": "object", "properties": {"type": {"type": "string", "enum": keys}}, "required": ["type"], "additionalProperties": False}
    response = get_client().with_options(timeout=TIMEOUT_SECONDS, max_retries=1).messages.create(
        model=MODEL, max_tokens=60, thinking={"type": "disabled"}, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}], output_config={"format": {"type": "json_schema", "schema": schema}})
    answer = json.loads(next(b.text for b in response.content if b.type == "text"))["type"]
    if answer not in keys:
        raise ValueError(f"unknown type {answer!r}")
    return answer


def classify(title: str, notes: Optional[str], types: list[dict]) -> tuple[str, str]:
    """(type key, source): source is 'ai' when Claude chose, 'rule' when the keyword fallback did."""
    keys = [t["key"] for t in types]
    if not keys:
        raise ValueError("no ticket types")
    if settings.anthropic_api_key:
        try:
            return _ask(title, notes, types), "ai"
        except Exception as exc:                                   # never block a ticket on the AI
            logger.warning("ticket type by AI failed (%r); using the keyword rule", exc)
    return keyword_type(f"{title} {notes or ''}", keys), "rule"
