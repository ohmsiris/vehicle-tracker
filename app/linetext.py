"""
Small clean-ups of a bill line's text, shared by the reader, the saving code and the maintenance plan.

A dealership's job sheet has a column "ประเภทรายการ" (ค่าแรง / อะไหล่ / วัสดุสิ้นเปลือง) in front of the description, and a photo of it is sometimes read with that word
glued to the start of the line ("อะไหล่ ไส้กรองอากาศ", "ค่าแรง เปลี่ยนซีลลูกปืน"). The word is the line's TYPE, not part of its name: strip_type_word() takes it off.
Only a whole word followed by a space is removed -- "ค่าแรงเปลี่ยนน้ำมัน" (one written word) and a line that is just "ค่าแรง" are left alone.
"""
import re
from typing import Optional

TYPE_WORD_RE = re.compile(r"^\s*(อะไหล่|ค่าแรง|วัสดุสิ้นเปลือง)\s*[:\-–]?\s+(?=\S)")


def strip_type_word(description: Optional[str]) -> tuple[str, Optional[str]]:
    """('อะไหล่ ไส้กรองอากาศ') -> ('ไส้กรองอากาศ', 'อะไหล่'); text without the word comes back unchanged with None."""
    text = str(description or "")
    m = TYPE_WORD_RE.match(text)
    if not m:
        return text.strip(), None
    return text[m.end():].strip(), m.group(1)
