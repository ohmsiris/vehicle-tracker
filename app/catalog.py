"""
The standard items of the families whose parts are named by a SIZE, not by a brand: V-belts and bearings. Pure functions (unit-tested); scripts/import_belts_bearings.py writes them.

Why these are different from a contactor or a relay: a B-29 belt is a B-29 from any maker, and one bearing unit has several names depending on who writes it --
    SKF "Y/UCF 207"  =  "FYJ 35 TF" (the SKF unit code)  =  "UCF207"  =  "F207 สี่เหลี่ยม" (the shop's / the parts sheet's name)
So the item's name is kind + size only (stock.BRAND_FREE_KINDS keeps the brand out of it), every name the part goes by is one of its words, and the brand is kept on each bill line
for comparing prices brand by brand.

Bearing units: a letter group says the housing, the number the bearing, an optional "-NN" the bore in inches:
    UCF = flange with 4 bolts (square, "F" on the parts sheet)   UCFL = oval flange, 2 bolts (SKF FYTB)   UCP = pillow block on legs ("P")
    UC2xx: the number's last two digits x 5 = bore in mm (207 -> 35 mm); with "-NN" the bore is NN/16 inch (207-20 -> 1 1/4").
Shop shorthand: รูมิล = bore in millimetres; รูหุน = bore in หุน, 1 หุน = 1/8 inch (3.2 mm), so 1 1/4" = 10 หุน.
"""
import re
from typing import Optional

SHORTHAND = "รูมิล = รูวัดเป็นมิลลิเมตร · รูหุน = รูวัดเป็นหุน (1 หุน = 1/8 นิ้ว = 3.2 มม. · 1 นิ้ว = 8 หุน)"

HOUSINGS = {  # letters -> (kind, the parts sheet's letter, how it looks)
    "UCF": ("ลูกปืนตุ๊กตาหน้าแปลนเหลี่ยม", "F", "สี่เหลี่ยม"),
    "UCFL": ("ลูกปืนตุ๊กตาหน้าแปลนวงรี", "FL", "วงรี"),
    "UCP": ("ลูกปืนตุ๊กตาขาตั้ง", "P", "ขาตั้ง"),
}
FRACTIONS = {0: "", 0.25: "¼", 0.5: "½", 0.75: "¾"}


def inch_text(inches: float) -> str:
    whole, frac = int(inches), round(inches - int(inches), 2)
    return f"{whole}{FRACTIONS.get(frac, '')}" if frac in FRACTIONS else f"{inches:g}"


def inch_slash(inches: float) -> str:
    """SKF writes the bore of an inch unit like 1.1/4 or 2.1/2 ('FYJ 1.1/4 TF')."""
    whole, frac = int(inches), round(inches - int(inches), 2)
    part = {0.25: "1/4", 0.5: "1/2", 0.75: "3/4"}.get(frac)
    return f"{whole}.{part}" if part and whole else (part or str(whole))


def bore(series: int, suffix: Optional[int]) -> dict:
    """{mm, inch, hun, text, plain} of a bearing unit: UC2xx number + optional -NN. Metric: last two digits x 5 mm; inch: NN/16."""
    if suffix:
        inches = suffix / 16
        return {"mm": round(inches * 25.4, 1), "inch": inches, "hun": inches * 8,
                "text": f"รูหุน {inches * 8:g} หุน ({inch_text(inches)} นิ้ว)", "plain": f"รู {inches * 8:g} หุน ({inch_text(inches)} นิ้ว)"}
    mm = (series % 100) * 5
    return {"mm": mm, "inch": None, "hun": None, "text": f"รูมิล {mm} มม.", "plain": f"รู {mm} มม."}


def unit_item(letters: str, series: int, suffix: Optional[int] = None) -> dict:
    """The standard item of one bearing unit, e.g. ('UCF', 207) or ('UCF', 207, 20)."""
    kind, letter, look = HOUSINGS[letters]
    model = f"{letters}{series}" + (f"-{suffix}" if suffix else "")
    b = bore(series, suffix)
    words = [model, f"{letter}{series}" + (f"-{suffix}" if suffix else "")]
    names = [f"{letter}{series}{' ' + look if look else ''}"]
    if letters == "UCF":                                         # SKF's own code for the unit: FYJ <bore> TF
        fyj = f"FYJ {inch_slash(b['inch'])} TF" if b["inch"] else f"FYJ {b['mm']:g} TF"
        words.append(fyj)
        names.append(fyj)
    if letters == "UCFL":
        fytb = f"FYTB {inch_slash(b['inch'])} TF" if b["inch"] else f"FYTB {b['mm']:g} TF"
        words.append(fytb)
        names.append(fytb)
    return {"category": "ลูกปืน", "kind": kind, "brand": None, "model": model, "spec": b["plain"], "unit": "ตลับ",
            "remark": " · ".join([b["text"], *names]), "words": list(dict.fromkeys(words))}


def groove_item(number: str) -> dict:
    """A deep-groove ball bearing: '6204-2RS' (the seal code after the number)."""
    base = number.split("-")[0]
    words = list(dict.fromkeys([number] + ([base] if "-" not in number else []) + ([number + "1"] if number.endswith("2RS") else [])))        # SKF writes the rubber-sealed type 2RS1
    mm = (int(base[-2:]) * 5) if base[-2:].isdigit() and int(base[-2:]) >= 4 else None
    return {"category": "ลูกปืน", "kind": "ลูกปืนเม็ดกลม", "brand": None, "model": number, "spec": None, "unit": "ตลับ",
            "remark": f"รูมิล {mm} มม." if mm else None, "words": words}


def belt_item(profile: str, length: str, used_on: Optional[list] = None, mm: bool = False) -> dict:
    """A V-belt: profile letter(s) + length ('B', '29' = 29 inches; 'SPC', '3750' = 3750 mm, narrow wedge belt)."""
    model = f"{profile}-{length}" if len(profile) == 1 else f"{profile}{length}"          # SPC3750Lw: the letters after the length are the maker's, they stay in the code
    num = re.sub(r"\D", "", length) if len(profile) > 1 else length
    what = f"ร่อง {profile} · ยาว {num} {'มม.' if mm else 'นิ้ว'}" if len(profile) == 1 else f"ร่องแคบ {profile} · ยาว {num} มม."
    use = f" · ใช้กับ {', '.join(used_on[:3])}" if used_on else ""
    return {"category": "สายพาน", "kind": "สายพาน", "brand": None, "model": model, "spec": None, "unit": "เส้น", "remark": (what + use)[:200], "words": [model]}
