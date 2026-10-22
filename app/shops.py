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
    "ชมรมย์การช่าง",
    "อู่ช่างเก่ง เจริญยนต์",
    "โชคพัฒนา",
    "มงคลไดนาโม",
    "24 ไดนาโม",
    "อิซูซุจึงกงเฮงแก่งคอย",
    "ฮีโน่ เอเซีย หนองแค",
]

# (fragment, canonical name). First match wins, so put the more specific fragment first.
# Fragments are compared after lower-casing and removing spaces / dots / dashes.
# The old short names ("อู่ชมรม", "ศูนย์ Isuzu" ...) still fold into the official ones.
ALIASES: list[tuple[str, str]] = [
    ("ช่างเอ๋", IN_HOUSE),
    ("ชมรม", "ชมรมย์การช่าง"),
    ("ช่างเก่ง", "อู่ช่างเก่ง เจริญยนต์"),
    ("เก่งเจริญ", "อู่ช่างเก่ง เจริญยนต์"),
    ("โชคพัฒนา", "โชคพัฒนา"),
    ("โชค", "โชคพัฒนา"),
    ("ช่างลิ้ม", "24 ไดนาโม"),               # before "ไดนาโม" (มงคลไดนาโม is a different shop); "24 ไดนาโม" is the official name
    ("ลิ้ม", "24 ไดนาโม"),
    ("24ไดนาโม", "24 ไดนาโม"),
    ("มงคล", "มงคลไดนาโม"),
    ("ไดนาโม", "มงคลไดนาโม"),
    ("isuzu", "อิซูซุจึงกงเฮงแก่งคอย"),
    ("อีซูซุ", "อิซูซุจึงกงเฮงแก่งคอย"),
    ("อิซูซุ", "อิซูซุจึงกงเฮงแก่งคอย"),
    ("hino", "ฮีโน่ เอเซีย หนองแค"),
    ("ฮีโน่", "ฮีโน่ เอเซีย หนองแค"),
    ("ฮีโน", "ฮีโน่ เอเซีย หนองแค"),
]

# Who to call. canonical shop -> its phone numbers (a shop can have several: a service centre and a body shop).
# A shop with no number yet simply shows no call button.
DIRECTORY: dict[str, dict] = {
    "ชมรมย์การช่าง": {"contacts": [{"label": "ช่างลม", "phone": "081-928-5474"}]},
    "24 ไดนาโม": {"contacts": [{"label": "ช่างลิ้ม", "phone": "081-374-0394"}]},
    IN_HOUSE: {"contacts": [{"label": "ช่างเอ๋", "phone": "085-967-4875"}]},
    "อิซูซุจึงกงเฮงแก่งคอย": {"contacts": [
        {"label": "ศูนย์บริการ", "phone": "086-391-0548"}, {"label": "ศูนย์ซ่อมสีและตัวถัง", "phone": "081-581-3465"}]},
    "ฮีโน่ เอเซีย หนองแค": {"contacts": []},
}

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
