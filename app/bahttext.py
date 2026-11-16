"""Excel's BAHTTEXT in Python: 7617.97 -> "เจ็ดพันหกร้อยสิบเจ็ดบาทเก้าสิบเจ็ดสตางค์" (checked against every CP bill of Aug-Nov 2569, 16 Nov 2026).
The amount is rounded to the satang first, as Excel does."""
from decimal import ROUND_HALF_UP, Decimal

_DIGIT = ["", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า"]
_PLACE = ["", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน"]


def _under_million(n: int, higher: bool = False) -> str:
    out = []
    s = str(n)
    size = len(s)
    for i, ch in enumerate(s):
        d, place = int(ch), size - i - 1
        if d == 0:
            continue
        if place == 1 and d == 1:
            out.append("สิบ")
        elif place == 1 and d == 2:
            out.append("ยี่สิบ")
        elif place == 0 and d == 1 and (size > 1 or higher):
            out.append("เอ็ด")
        else:
            out.append(_DIGIT[d] + _PLACE[place])
    return "".join(out)


def _number(n: int) -> str:
    if n == 0:
        return "ศูนย์"
    parts = []
    millions, rest = divmod(n, 1_000_000)
    if millions:
        parts.append(_number(millions) + "ล้าน")
    if rest:
        parts.append(_under_million(rest, higher=bool(millions)))
    return "".join(parts)


def bahttext(amount) -> str:
    v = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    neg = v < 0
    v = abs(v)
    baht = int(v)
    satang = int((v - baht) * 100)
    if baht == 0 and satang == 0:
        return "ศูนย์บาทถ้วน"
    text = ""
    if baht:
        text += _number(baht) + "บาท"
    text += _number(satang) + "สตางค์" if satang else "ถ้วน"
    return ("ลบ" if neg else "") + text
