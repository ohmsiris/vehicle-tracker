"""
The mechanics' stock room as counted on 27 Oct 2026 (the owner's list), with standard names (app/stock.py compose_name) and the words a bill is matched by.

    .venv/Scripts/python.exe scripts/import_stock_opening.py [--apply]

Each row: (category, kind, brand, model, spec, unit, qty, words, note). qty None = "wait for confirmation": the item is made but nothing is counted yet.
A model that comes in several ratings gets a PAIR as its word ("TR-N2/3+12-18A": both must be on the bill line); a unique model is its own word.
An item already there (same name) is left alone, so it can be run again. Needs schema_v16.sql and schema_v28.sql.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import stock, supabase                                  # noqa: E402

EL, WELD, CHAIN, CHEM, PIPE = "ไฟฟ้า", "เชื่อม-ตัด", "โซ่", "น้ำยา-กาว", "ท่อ-ประปา"
CHECK = "ตรวจชนิด/ยี่ห้อ"
HOLD = "รอยืนยันจำนวน (คาดว่า 1)"
# (category, kind, brand, model, spec, unit, qty, words, note)
ROWS = [
    # ---- electrical: big stuff
    (EL, "แมกเนติก", "Fuji", "SC-N8", "220V", "ตัว", 1, ["SC-N8"], None),
    (EL, "เบรกเกอร์", "Mitsubishi", "NF63-HV", "63A", "ตัว", 1, ["NF63-HV"], None),
    (EL, "แมกเนติก", "Fuji", "SC-N6", "220V", "ตัว", 2, ["SC-N6"], "นับเป็น 2 (ในลิสต์เขียน SC-N6 220V สองบรรทัด)"),
    (EL, "โอเวอร์โหลด", "Fuji", "TR-N8/3", "85-125A", "ตัว", 1, ["TR-N8/3"], None),
    # ---- Fuji overload
    (EL, "โอเวอร์โหลด", "Fuji", "TR-N2/3", "12-18A", "ตัว", 4, ["TR-N2/3+12-18A"], None),
    (EL, "โอเวอร์โหลด", "Fuji", "TR-N2/3", "6-9A", "ตัว", 3, ["TR-N2/3+6-9A"], None),
    (EL, "โอเวอร์โหลด", "Fuji", "TR-0N/3", "1.7-2.6A", "ตัว", 1, ["TR-0N/3"], None),
    (EL, "โอเวอร์โหลด", "Fuji", "TR-5-1N/3", "9-13A", "ตัว", 1, ["TR-5-1N/3"], None),
    # ---- Mitsubishi overload
    (EL, "โอเวอร์โหลด", "Mitsubishi", "TH-T25", "18-26A", "ตัว", 4, ["TH-T25+18-26A"], None),
    (EL, "โอเวอร์โหลด", "Mitsubishi", "TH-T25", "7-11A", "ตัว", 1, ["TH-T25+7-11A"], None),
    # ---- magnetic / breakers
    (EL, "เบรกเกอร์", "Fuji", "BW50SAG", "220V 40A", "ตัว", 2, ["BW50SAG"], None),
    (EL, "เบรกเกอร์", "Fuji", "BW32AAG", "220V 30A", "ตัว", 1, ["BW32AAG+30A"], None),
    (EL, "เบรกเกอร์", "Fuji", "BW32AAG", "220V 10A", "ตัว", 2, ["BW32AAG+10A"], None),
    (EL, "แมกเนติก", "Fuji", "SC-N2", "220V", "ตัว", 3, ["SC-N2"], "ยี่ห้อ Fuji ตามรุ่น (ลิสต์ไม่ได้เขียน)"),
    (EL, "แมกเนติก", "Fuji", "SC-N1", "220V", "ตัว", 3, ["SC-N1"], "ยี่ห้อ Fuji ตามรุ่น (ลิสต์ไม่ได้เขียน)"),
    (EL, "แมกเนติก", "Mitsubishi", "S-T25", "220V", "ตัว", 2, ["S-T25"], None),
    (EL, "แมกเนติก", "Mitsubishi", "S-T21", "220V", "ตัว", 3, ["S-T21+220V"], None),
    (EL, "แมกเนติก", "Mitsubishi", "S-T21", "24V", "ตัว", 3, ["S-T21+24V"], None),
    (EL, "แมกเนติก", "Mitsubishi", "S-T12", "24V", "ตัว", 1, ["S-T12"], None),
    (EL, "แมกเนติก", "Schneider", "LC1D09B7", "24V", "ตัว", 1, ["LC1D09B7"], None),
    (EL, "แมกเนติก", "Schneider", "LC1D09BD", "24VDC", "ตัว", 1, ["LC1D09BD"], None),
    (EL, "แมกเนติก", "Schneider", "LC1D32M7", "220V", "ตัว", 1, ["LC1D32M7"], None),
    (EL, "โอเวอร์โหลด", "Schneider", "LRD08", "2.5-4A", "ตัว", 5, ["LRD08"], None),
    # ---- utilities, electrical box
    (EL, "อุปกรณ์ตู้ไฟ", None, "W-OP4", None, "ตัว", 2, ["W-OP4"], CHECK),
    (EL, "ชั่วโมงทำงาน Hour Meter", None, "HM-1", None, "ตัว", 1, ["HM-1"], CHECK),
    (EL, "อุปกรณ์ตู้ไฟ", "Omron", "61F-GP-N", None, "ตัว", 1, ["61F-GP-N"], CHECK),
    (EL, "อุปกรณ์ตู้ไฟ", "Omron", "61F-11", None, "ตัว", 3, ["61F-11"], CHECK),
    (EL, "ไทเมอร์", "Omron", "H3CR-A8", None, "ตัว", 1, ["H3CR-A8"], None),
    (EL, "รีเลย์", "Omron", "MKS2P", "24V", "ตัว", 4, ["MKS2P"], None),
    (EL, "ซ็อกเก็ต", "Omron", "PF083A-E", "2544CX", "ตัว", 5, ["PF083A-E", "OMPF083A-E"], "ลิสต์เขียน OMPF083A-E 2544CX"),
    (EL, "ซ็อกเก็ต", "Omron", "2865CX", None, "ตัว", 1, ["2865CX"], None),
    (EL, "ไพลอตแลมป์", None, None, "เขียว", "ตัว", 4, ["Pilot lamp+green", "ไพลอตแลมป์+เขียว"], None),
    (EL, "สวิตช์ 2 ทาง", None, None, None, "ตัว", 3, ["2way switch", "สวิตช์ 2 ทาง"], None),
    (EL, "เคเบิลแกลนด์", None, None, None, "ตัว", 3, ["Cable Gland", "เคเบิลแกลนด์"], None),
    (EL, "เทปพันสายไฟ", None, None, None, "ม้วน", 16, ["เทปพันสายไฟ"], None),
    (EL, "สายไฟ", None, None, "1×2.5", "ม้วน", 1, ["1*2.5", "1x2.5"], None),
    (EL, "สายไฟ", None, None, "1×4", "ม้วน", 1, ["1*4", "1x4"], None),
    # ---- floats
    (EL, "ลูกลอย", "Qubix", "UKY-2", None, "ตัว", 1, ["UKY-2"], "ลิสต์เขียน “Qubix Float Switch UKY-2”"),
    (EL, "ลูกลอย", "Big One", "202-AB", None, "ตัว", 2, ["202-AB"], None),
    # ---- consumables
    (CHEM, "น้ำยาล้างสนิมและเคลือบผิวโลหะ", "Draga", None, None, "ขวด", 9, ["Draga"], None),
    (CHEM, "น้ำยา", "Sonax", None, None, "กระป๋อง", 1, ["Sonax"], CHECK),
    (CHEM, "ซิลิโคน", None, None, "General purpose", "หลอด", 9, ["Silicone", "ซิลิโคน"], None),
    (CHAIN, "โซ่", "KANA", "#50", None, "กล่อง", 1, ["KANA #50", "KANA 50"], None),
    (CHAIN, "โซ่", "KANA", "#80", None, "กล่อง", 1, ["KANA #80", "KANA 80"], None),
    (WELD, "ใบเจียร", None, None, None, "กล่อง", 1, ["ใบเจียร"], None),
    (WELD, "ใบตัด", None, None, None, "กล่อง", 4, ["ใบตัด"], None),
    (WELD, "ลวดเชื่อม", "Yawata", "FT-51", "3.2 มม. mild steel", "กล่อง", None, ["FT-51+3.2"], HOLD),
    (WELD, "ลวดเชื่อม", "Kovet", "Ni55", "Nickel Cast Iron", "กล่อง", None, ["Ni55"], HOLD),
    (WELD, "ลวดเชื่อม", "Yawata", "FT-51", "2.6 มม. mild steel", "กล่อง", None, ["FT-51+2.6"], HOLD),
    (WELD, "ลวดเชื่อม", "Yawata", "308L-16", "2.6 มม. (แพ็ก 1 กก.)", "แพ็ก", 9, ["308L-16"], None),
    (PIPE, "กาวประสานท่อพีวีซี", "ท่อน้ำไทย", None, None, "หลอด", 8, ["กาวประสานท่อพีวีซี"], None),
    (PIPE, "เทปพันเกลียว", None, None, None, "ม้วน", 7, ["เทปพันเกลียว"], None),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    existing = supabase.select_all("stock_items", {"select": "id,name", "order": "name.asc"})
    have = {r["name"].lower() for r in existing}
    taken = {a["alias"].lower() for a in supabase.select_all("stock_item_aliases", {"select": "alias", "order": "alias.asc"})}
    today = date.today().isoformat()
    plan, problems, names = [], [], set()
    for cat, kind, brand, model, spec, unit, qty, words, note in ROWS:
        name = stock.compose_name(kind, brand, model, spec)
        if name.lower() in names:
            problems.append(f"duplicate name in the list: {name}")
        names.add(name.lower())
        clash = [w for w in words if w.lower() in taken or sum(w.lower() == x.lower() for r in ROWS for x in r[7]) > 1]
        if clash:
            problems.append(f"{name}: word used twice: {clash}")
        plan.append({"name": name, "kind": kind, "brand": brand, "model": model, "spec": spec, "category": cat, "unit": unit, "qty": qty, "words": words, "notes": note, "skip": name.lower() in have})
    for p in plan:
        mark = "skip (already there)" if p["skip"] else ("hold" if p["qty"] is None else f"count {p['qty']:g}")
        print(f"{p['category']:10} {p['name']:52} {p['unit']:8} {mark:20} words={p['words']}")
    print(f"\n{len(plan)} items · {sum(1 for p in plan if p['qty'] is None)} on hold · {sum(1 for p in plan if p['skip'])} already there")
    for pr in problems:
        print("PROBLEM:", pr)
    if problems or not args.apply:
        print("dry run -- nothing written (add --apply)" if not problems else "fix the problems first")
        return
    made = 0
    for p in plan:
        if p["skip"]:
            continue
        row = supabase.insert("stock_items", [{"name": p["name"], "kind": p["kind"], "brand": p["brand"], "model": p["model"], "spec": p["spec"], "category": p["category"],
                                               "unit": p["unit"], "notes": p["notes"]}])[0]
        supabase.insert("stock_item_aliases", [{"item_id": row["id"], "alias": w} for w in p["words"]])
        if p["qty"] is not None:
            supabase.insert("stock_moves", [{"item_id": row["id"], "move_date": today, "kind": "count", "qty": p["qty"], "notes": "ยอดยกมา (นับ 27 ต.ค. 2569)"}])
        made += 1
    print(f"written: {made} items")


if __name__ == "__main__":
    main()
