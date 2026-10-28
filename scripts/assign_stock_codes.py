"""
Give every stock item without one its code (E001 ...) and mark the cheap consumables as "counted, not ticked" (track_out = false).

    .venv/Scripts/python.exe scripts/assign_stock_codes.py [--apply]

Codes follow the order of the stock table (หมวด, ชนิด, ยี่ห้อ, รุ่น, สเปค) so a sheet reads in the same order as the shelf list. Items that already have a code
keep it; new ones continue from the highest. Needs schema_v29.sql.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import stock, supabase                                      # noqa: E402

COUNTED_ONLY_KINDS = {"เทปพันสายไฟ", "เทปพันเกลียว", "ซิลิโคน", "กาวประสานท่อพีวีซี", "ใบเจียร", "ใบตัด", "สายไฟ", "เคเบิลแกลนด์", "ไพลอตแลมป์", "สวิตช์ 2 ทาง",
                      "น้ำยาล้างสนิมและเคลือบผิวโลหะ", "น้ำยา", "โซ่", "ลวดเชื่อม"}


def main(apply: bool) -> None:
    items = supabase.select_all("stock_items", {"select": "*", "order": "name.asc"})
    items.sort(key=lambda i: tuple(str(i.get(k) or "") for k in ("category", "kind", "brand", "model", "spec", "name")))
    have = [dict(i) for i in items]
    todo = []
    for i in items:
        values = {}
        if not i.get("code"):
            code = stock.next_code(i.get("category"), have)
            i["code"] = code
            have.append(i)
            values["code"] = code
        counted = (i.get("kind") or "") in COUNTED_ONLY_KINDS
        if i.get("track_out") is not (not counted):
            values["track_out"] = not counted
        if values:
            todo.append((i, values))
    for i, v in todo:
        print(f"{i.get('code') or v.get('code'):6} {i['name'][:50]:50} {v}")
    print(f"\n{len(todo)} items to update")
    if apply:
        for i, v in todo:
            supabase.update("stock_items", {"id": f"eq.{i['id']}"}, v)
        print("written")
    else:
        print("dry run -- nothing written (add --apply)")


if __name__ == "__main__":
    main("--apply" in sys.argv)
