"""
The repair shops (and the in-house mechanic), as one list of canonical names.

Why it exists: the same shop is written differently on every bill
("ชมรมย์การช่าง", "ชมรม", "อู่ชมรม"; "ช่างเก่งเจริญยนต์", "อู่ช่างเก่ง"), so
filtering the log by shop needs them folded into one name. canonical() does
that with a short alias list; a name that matches nothing is kept as written
(a new shop just appears under its own name -- nothing is ever dropped).

To teach it a new shop or spelling, add to SHOPS / ALIASES below.
"""
import re

IN_HOUSE = "ช่างเอ๋ (ในโรงงาน)"

# The places a truck is sent to, in the order they are offered on the board.
SHOPS: list[str] = [
    "อู่ชมรม",
    "อู่ช่างเก่ง",
    "โชคพัฒนา",
    "มงคลไดนาโม",
    "อู่ช่างลิ้ม",
    "ศูนย์ Isuzu",
    "ศูนย์ Hino",
]

# (fragment, canonical name). First match wins, so put the more specific fragment first.
# Fragments are compared after lower-casing and removing spaces / dots / dashes.
ALIASES: list[tuple[str, str]] = [
    ("ช่างเอ๋", IN_HOUSE),
    ("ชมรม", "อู่ชมรม"),
    ("ช่างเก่ง", "อู่ช่างเก่ง"),
    ("เก่งเจริญ", "อู่ช่างเก่ง"),
    ("โชคพัฒนา", "โชคพัฒนา"),
    ("โชค", "โชคพัฒนา"),
    ("มงคล", "มงคลไดนาโม"),
    ("ไดนาโม", "มงคลไดนาโม"),
    ("ช่างลิ้ม", "อู่ช่างลิ้ม"),
    ("ลิ้ม", "อู่ช่างลิ้ม"),
    ("isuzu", "ศูนย์ Isuzu"),
    ("อีซูซุ", "ศูนย์ Isuzu"),
    ("อิซูซุ", "ศูนย์ Isuzu"),
    ("hino", "ศูนย์ Hino"),
    ("ฮีโน่", "ศูนย์ Hino"),
    ("ฮีโน", "ศูนย์ Hino"),
]

_STRIP = re.compile(r"[\s\-_.]")


def _norm(text: str) -> str:
    return _STRIP.sub("", (text or "").lower())


_NORM_ALIASES = [(_norm(frag), canon) for frag, canon in ALIASES]


def canonical(name: str | None) -> str:
    """The canonical shop for a name as written on a bill / typed by a person ('' if blank)."""
    raw = (name or "").strip()
    if not raw:
        return ""
    n = _norm(raw)
    for frag, canon in _NORM_ALIASES:
        if frag in n:
            return canon
    return raw


def choices() -> list[str]:
    """What the board offers when sending a truck somewhere: the shops, then the in-house mechanic."""
    return [*SHOPS, IN_HOUSE]
