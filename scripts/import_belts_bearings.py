"""
The standard belts and bearings (app/catalog.py): one item per SIZE, named by kind + size without a brand, every name the part goes by as a word of the item, a short remark with
the shop shorthand (รูมิล / รูหุน) -- then the lines already on saved bills are linked to them and given their brand, so the prices can be compared brand by brand.

    .venv/Scripts/python.exe scripts/import_belts_bearings.py [--apply]

The sizes come from what is really used: the machine parts index (what each machine takes) and the saved purchase bills. An item already there (same name) is left alone, so it can be run
again. Nothing is counted into stock (the shelf has not been counted): the items start at nothing. Needs schema_v31.sql.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import catalog, purchasing, stock, supabase              # noqa: E402

# bearing units: (housing letters, number, inch suffix) -- the ones the bills and the machines show
UNITS = [("UCF", 205, None), ("UCF", 205, 16), ("UCF", 207, None), ("UCF", 207, 20), ("UCF", 208, None), ("UCF", 211, None), ("UCF", 211, 32), ("UCF", 213, None), ("UCF", 213, 40),
         ("UCFL", 207, None), ("UCFL", 207, 20), ("UCFL", 208, None), ("UCFL", 208, 24), ("UCP", 208, None), ("UCP", 209, None), ("UCP", 210, None)]
GROOVES = ["6204-2RS", "6207-2RS", "6308-2Z", "6307"]
BELT_RE = re.compile(r"(?<![A-Za-z0-9])([ABC])\s*-?\s*(\d{2,3})(?![A-Za-z0-9])")


def used_on(parts: list[dict]) -> tuple[dict, dict]:
    """(belt size -> equipment names, unit code -> equipment names) from the machine parts index."""
    belts, units = {}, {}
    for p in parts:
        spec, eq = str(p.get("spec") or ""), (p.get("equipment") or "").strip()
        if not eq:
            continue
        if re.search(r"สายพาน", str(p.get("part") or "")):
            m = BELT_RE.search(spec)
            key = f"{m.group(1)}-{m.group(2)}" if m else (re.search(r"SPC\d+\w*", spec) or [None])[0]
            if key:
                belts.setdefault(key, []).append(eq)
        elif re.search(r"ลูกปืน", str(p.get("part") or "")):
            m = re.match(r"\s*(F|P|FYTB)\s*-?\s*(\d{3})", spec, re.I)
            if m:
                letters = {"F": "UCF", "P": "UCP", "FYTB": "UCFL"}[m.group(1).upper()]
                units.setdefault((letters, int(m.group(2)) % 100 + 200), []).append(eq)
    uniq = lambda d: {k: list(dict.fromkeys(v)) for k, v in d.items()}
    return uniq(belts), uniq(units)


def build_rows(parts: list[dict], lines: list[dict]) -> list[dict]:
    belt_use, unit_use = used_on(parts)
    sizes = set(belt_use)
    for ln in lines:
        if re.search(r"สายพาน|belt", ln["description"], re.I):
            m = BELT_RE.search(ln["description"])
            if m:
                sizes.add(f"{m.group(1)}-{m.group(2)}")
    rows = []
    for key in sorted(sizes, key=lambda k: (k[0] if "-" in k else "Z", int(re.sub(r"\D", "", k)))):
        if "-" in key:
            prof, length = key.split("-")
            rows.append(catalog.belt_item(prof, length, belt_use.get(key)))
        else:
            rows.append(catalog.belt_item("SPC", key[3:], belt_use.get(key)))
    for letters, series, suffix in UNITS:
        item = catalog.unit_item(letters, series, suffix)
        eq = unit_use.get((letters, series)) if not suffix else None
        if eq:
            item["remark"] = (item["remark"] + f" · ใช้กับ {', '.join(eq[:3])}")[:200]
        rows.append(item)
    rows.extend(catalog.groove_item(n) for n in GROOVES)
    return rows


def main(apply: bool) -> None:
    parts = supabase.select_all("machine_parts", {"select": "equipment,part,spec", "order": "id.asc"})
    lines = supabase.select_all("purchase_lines", {"select": "id,description,item_id,brand", "order": "id.asc"})
    rows = build_rows(parts, lines)
    have = {i["name"].lower() for i in supabase.select_all("stock_items", {"select": "name", "order": "name.asc"})}
    new = []
    for r in rows:
        name = stock.compose_name(r["kind"], r["brand"], r["model"], r["spec"])
        print(f"{'skip ' if name.lower() in have else 'new  '}{name:52} {r['unit']:6} | {r['remark'] or ''}")
        print(f"       words: {r['words']}")
        if name.lower() not in have:
            new.append({"category": r["category"], "kind": r["kind"], "brand": None, "model": r["model"], "spec": r["spec"], "unit": r["unit"], "remark": r["remark"], "aliases": r["words"]})
    print(f"\n{len(rows)} standard items · {len(new)} new")
    if not apply:
        print("dry run -- nothing written (add --apply)")
        return
    if new:
        stock.create_items_batch({"rows": new})
    items = purchasing.items_with_aliases()
    linked = branded = 0
    for ln in lines:
        hit = purchasing.match_item(ln["description"], items) if not ln["item_id"] else {"id": ln["item_id"]}
        values = {}
        if hit and not ln["item_id"]:
            values["item_id"] = hit["id"]
            linked += 1
        brand = purchasing.detect_brand(ln["description"])
        if brand and brand != ln.get("brand") and (hit or ln["item_id"]):
            values["brand"] = brand
            branded += 1
        if values:
            supabase.update("purchase_lines", {"id": f"eq.{ln['id']}"}, values)
    print(f"written: {len(new)} items · {linked} bill lines linked to an item · {branded} lines given their brand")


if __name__ == "__main__":
    main("--apply" in sys.argv)
