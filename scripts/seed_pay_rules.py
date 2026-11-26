"""
Fill employee_pay_rules (schema_v93) from the workbook "แบบคำนวณเงินเดือน (เสนอ).xlsx", sheet อัตรา (owner, 26 Nov 2026).

    python scripts/seed_pay_rules.py "<path to the workbook>"            # shows what it would write
    python scripts/seed_pay_rules.py "<path to the workbook>" --write    # writes (one row per employee, replaced)

Only these columns are read: รหัส, กลุ่มคิดเงิน, % ค่าคอม, บาท/ซอง, OT บาท/ชม., OT ชม./วัน, ได้เบี้ยขยัน, หมายเหตุ. The daily rate stays in
employee_rates. People the workbook does not list (new people, people who left after it was made) get the group their branch and
position imply. On top come three things the owner told us on 26 Nov 2026: who rides with which route driver, the two people paid
344 on a day spent only on a truck, and the route that ended on 17/11/2026.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ALIASES = {"SB-KK073S": "SB-KK073"}                 # the workbook's code -> the code in employees
RIDES_WITH = {                                      # เด็กรถ -> the route driver whose truck they ride (owner's list, 26 Nov 2026)
    "SB-FR071": "SB-PD012", "SB-FR022": "SB-PD003", "SB-FR028": "SB-PD030", "SB-FR067": "SB-PD010", "SB-FR039": "SB-PD008",
    "SB-FR031": "SB-PD006", "SB-FR046": "SB-PD001", "SB-FR041": "SB-PD001", "SB-FR021": "SB-PD001", "SB-FR055": "SB-PD002",
    "SB-FR072": "SB-PD009",
}
RATE2 = {"SB-FR034": 344, "SB-FR076": 344}          # a day spent only on a truck (the plant day is their normal rate, 390)
ROUTE_UNTIL = {"SB-PD011": "2026-11-17"}            # truck 9's route ended; other work after that


def group_for(branch: str, position: str) -> str:
    """The workbook's group for someone it does not list (the same mapping reproduces its 119 non-route rows)."""
    position = position or ""
    if branch == "แก่งคอย":
        return "KK-DRV" if position.startswith("พนักงานขับรถ") else "KK-PROD" if position == "ผลิต" else "DAY"
    if position == "พนักงานขับรถหน้าโรง":
        return "DRV-F"
    if position == "พนักงานขับรถสาย":
        return "DRV-E"
    return "FAC-COM" if position == "แพ็ค" else "DAY"


def num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def read_workbook(path: str) -> dict:
    import openpyxl
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)["อัตรา"]
    head = [str(c or "").strip() for c in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
    col = {name: head.index(name) for name in ("รหัส", "กลุ่มคิดเงิน", "% ค่าคอม", "บาท/ซอง", "OT บาท/ชม.", "OT ชม./วัน (อัตโนมัติ)", "ได้เบี้ยขยัน (Y/N)", "หมายเหตุ")}
    out = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        code = str(row[col["รหัส"]] or "").strip()
        if not code or not row[col["กลุ่มคิดเงิน"]]:
            continue
        pct = num(row[col["% ค่าคอม"]])
        out[ALIASES.get(code, code)] = {
            "pay_group": str(row[col["กลุ่มคิดเงิน"]]).strip(),
            "commission_pct": round(pct * 100, 4) if pct is not None and pct < 1 else pct,
            "bag_rate": num(row[col["บาท/ซอง"]]), "ot_rate": num(row[col["OT บาท/ชม."]]),
            "ot_auto_hours": num(row[col["OT ชม./วัน (อัตโนมัติ)"]]),
            "bonus": str(row[col["ได้เบี้ยขยัน (Y/N)"]] or "").strip().upper() == "Y",
            "note": (str(row[col["หมายเหตุ"]]).strip() or None) if row[col["หมายเหตุ"]] else None,
        }
    return out


def main() -> None:
    from app import supabase
    path, write = sys.argv[1], "--write" in sys.argv
    book = read_workbook(path)
    people = supabase.select_all("employees", {"select": "id,code,branch,position,status,end_date,pay_group", "order": "code.asc"})
    by_code = {p["code"]: p for p in people}
    rows, derived, skipped = [], [], []
    for p in people:
        if p.get("pay_group") == "hq":                                   # HQ pays these two; nothing to work out here
            skipped.append(p["code"])
            continue
        rule = book.get(p["code"])
        if rule is None:
            if p["status"] == "resigned" and str(p.get("end_date") or "") < "2026-10-26":
                continue                                                 # left before the first month we calculate
            rule = {"pay_group": group_for(p["branch"], p["position"]), "commission_pct": None, "bag_rate": None, "ot_rate": None,
                    "ot_auto_hours": None, "bonus": False, "note": "ไม่อยู่ในแบบคำนวณ (เสนอ): จัดกลุ่มตามตำแหน่ง"}
            derived.append(f"{p['code']} -> {rule['pay_group']}")
        mate = by_code.get(RIDES_WITH.get(p["code"], ""))
        rows.append({"employee_id": p["id"], **rule, "rate2": RATE2.get(p["code"]), "rides_with": mate["id"] if mate else None,
                     "route_until": ROUTE_UNTIL.get(p["code"])})
    unknown = sorted(c for c in book if c not in by_code)
    groups = {}
    for r in rows:
        groups[r["pay_group"]] = groups.get(r["pay_group"], 0) + 1
    print(f"workbook rows {len(book)} · rules to write {len(rows)} · by group {dict(sorted(groups.items()))}")
    print(f"not in the workbook, group from position: {derived or '-'}")
    print(f"paid by HQ, left out: {skipped or '-'} · workbook codes not in employees: {unknown or '-'}")
    print(f"rides_with {sum(1 for r in rows if r['rides_with'])} · rate2 {sum(1 for r in rows if r['rate2'])} · route_until {sum(1 for r in rows if r['route_until'])}")
    if write:
        for i in range(0, len(rows), 100):
            supabase.insert("employee_pay_rules", rows[i:i + 100], on_conflict="employee_id")
        print("written")
    else:
        print("nothing written (add --write)")


if __name__ == "__main__":
    main()
