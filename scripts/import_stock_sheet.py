"""
Read the stock sheet made by scripts/export_stock_sheet.py back in.

    .venv/Scripts/python.exe scripts/import_stock_sheet.py "<filled .xlsx>" [--apply]

* a row WITH a รหัส: the minimum (ขั้นต่ำ) and the shelf (ที่เก็บ) are updated when filled in and different (blank = leave as it is);
* a row WITHOUT a รหัส and with a ชนิด (or at least one of ชนิด / ยี่ห้อ / รุ่น / สเปค): a new item, made with the standard name; "คงเหลือตอนนี้" is its opening count.
* the example row (หมายเหตุ starting with "ตัวอย่าง") is skipped. Nothing else in an existing row is changed.
Dry run unless --apply; a bad new row stops the whole import (named by its sheet row).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import load_workbook                                    # noqa: E402

from app import stock, supabase                                        # noqa: E402

HEAD_ROW = 7
ID, CAT, KIND, BRAND, MODEL, SPEC, UNIT, QTY, MIN, LOC, NOTE = range(11)


def text(v) -> str:
    return "" if v is None else str(v).strip()


def main(path: str, apply: bool) -> None:
    ws = load_workbook(path, data_only=True).active
    items = {i["id"]: i for i in stock.overview()["items"]}
    updates, new_rows, skipped = [], [], 0
    for r in range(HEAD_ROW + 1, ws.max_row + 1):
        row = [ws.cell(row=r, column=c + 1).value for c in range(11)]
        if text(row[NOTE]).startswith("ตัวอย่าง"):
            skipped += 1
            continue
        rid = text(row[ID])
        if rid:
            it = items.get(rid)
            if not it:
                raise SystemExit(f"row {r}: รหัส not found -- do not change column A")
            values = {}
            if text(row[MIN]) != "":
                try:
                    m = float(str(row[MIN]).replace(",", ""))
                except ValueError:
                    raise SystemExit(f"row {r}: ขั้นต่ำ is not a number: {row[MIN]!r}")
                if m < 0:
                    raise SystemExit(f"row {r}: ขั้นต่ำ is negative")
                if it.get("min_qty") is None or float(it["min_qty"]) != m:
                    values["min_qty"] = m
            if text(row[LOC]) and text(row[LOC]) != (it.get("location") or ""):
                values["location"] = text(row[LOC])
            if values:
                updates.append((rid, it["name"], values))
        elif any(text(row[c]) for c in (KIND, BRAND, MODEL, SPEC)):
            new_rows.append((r, {"category": text(row[CAT]), "kind": text(row[KIND]), "brand": text(row[BRAND]), "model": text(row[MODEL]), "spec": text(row[SPEC]),
                                 "unit": text(row[UNIT]), "qty": text(row[QTY])}))
    print(f"{len(updates)} items to update · {len(new_rows)} new items · example/skipped rows {skipped}")
    for _, name, v in updates[:200]:
        print("  update", name, v)
    for r, d in new_rows:
        print("  new   ", r, stock.compose_name(d["kind"], d["brand"], d["model"], d["spec"]), d["unit"], d["qty"])
    if not apply:
        print("dry run -- nothing written (add --apply)")
        return
    for rid, _, values in updates:
        supabase.update("stock_items", {"id": f"eq.{rid}"}, values)
    if new_rows:
        try:
            stock.create_items_batch({"rows": [d for _, d in new_rows]})
        except stock.InvalidStock as exc:                              # "แถวที่ N" counts the new rows only: map it back to the sheet row
            raise SystemExit(f"new items: {exc}  (new rows in sheet order: {[r for r, _ in new_rows]})")
    print("written")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], "--apply" in sys.argv)
