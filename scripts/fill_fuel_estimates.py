"""Card fuel per truck for the months that only have totals (Jan-Apr 2026, Saraburi), the owner's call (8 Nov): split by weight.

    python scripts/fill_fuel_estimates.py "<วารีเทพ 69.xlsx>" [--write]

Bangchak has two stations; the handwritten monthly page lists each station's trucks. The station whose fill log we have ("วารีเทพ 69.xlsx",
half-month sheets 11 Jan - 25 Aug, plate by plate -- it equals the page's right-hand list: May 257,122.10, Jun 265,430.10) is exact per
truck. The other station = the month's total (owner's Accounting sheet) minus that, split over its trucks by their share on the Jan / May /
Jun pages. PT: no per-truck data before May -> the month total split by each truck's May + Jun share; April has no PT total at all ->
the average of Jan, Feb, Mar, May, Jun. Every estimated entry says "ประมาณ" and its source_id key starts with "est".
"""
import os
import re
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import acc_cardfuel  # noqa: E402
from app.acc_docs import norm_plate  # noqa: E402

TH = {"มค": 1, "กพ": 2, "มีค": 3, "เมย": 4, "พค": 5, "มิย": 6, "กค": 7, "สค": 8}
BANGCHAK_TOTAL = {"2026-02": 282414.00, "2026-03": 290779.60, "2026-04": 481400.30}       # owner's Accounting sheet
PT_TOTAL = {"2026-01": 285856.30, "2026-02": 295590.20, "2026-03": 163772.20}            # owner's Accounting sheet
TYPOS = {"1ฒบ4270": "1ฒย 4270", "จำเนียร": "#9", "1ฒห-6240": "1ฒห 6200"}      # the log's slips: อำนาจ's plate with บ for ย; a name where the plate goes
# the other station's trucks on the handwritten pages (Jan, May, Jun): its weights
LEFT = {"#32": [20630, 29580, 35084.50], "83-2919": [11240, 12200, 10800], "#1": [18607, 28281, 26906.60], "#7": [24550, 33168, 28689],
        "#10": [16581.10, 17150, 18840], "#45": [12537, 19430, 19130.30], "#50": [18090, 24420, 25770]}


def sheet_month(title: str) -> int:
    t = title.replace(" ", "")
    hits = [TH[k] for k in TH if k in t]
    return max(hits) if (t.startswith("26") or t.startswith("25")) else next(TH[k] for k in sorted(TH, key=len, reverse=True) if k in t)


def station_log(path: str) -> dict:
    from openpyxl import load_workbook
    out: dict = defaultdict(lambda: defaultdict(float))
    for ws in load_workbook(path, data_only=True).worksheets:
        m = f"2026-{sheet_month(ws.title):02d}"
        for r in ws.iter_rows(min_row=5, values_only=True):
            if r[2] and isinstance(r[5], (int, float)) and r[1] not in (None, "รวม"):
                plate = str(r[2]).strip()
                plate = TYPOS.get(plate, plate)
                out[m][plate if plate.startswith("#") else norm_plate(plate)] += r[5]      # "1ฒห-6200" and "1ฒห 6200" are one truck
    return out


def split(total: float, weights: dict) -> dict:
    s = sum(weights.values())
    out = {k: round(total * w / s, 2) for k, w in weights.items() if w > 0}
    k0 = max(out, key=out.get)
    out[k0] = round(out[k0] + total - sum(out.values()), 2)        # the satang left over goes to the biggest share
    return out


def main() -> None:
    from app import supabase
    vehicles = supabase.select_all("vehicles", {"select": "id,plate_number,vehicle_number,branch", "order": "id.asc"})
    log = station_log(sys.argv[1])
    plan = []                                             # (month, station, {key: amount}, note, estimated)
    left_w = {k: sum(v) for k, v in LEFT.items()}
    for m, total in BANGCHAK_TOTAL.items():
        right = dict(log[m])
        plan.append((m, "bangchak", right, "สถานีวารีเทพ ตามบิลรายคัน", False))
        plan.append((m, "bangchak", split(round(total - sum(right.values()), 2), left_w), "อีกสถานี ประมาณ: ยอดรวมเดือนแบ่งตามสัดส่วน ม.ค./พ.ค./มิ.ย.", True))
    # PT weights: each truck's May + June PT in the books
    pt_w: dict = defaultdict(float)
    vid = {v["id"]: v for v in vehicles}
    for e in [x for m in ("2026-05", "2026-06") for x in supabase.select_all("acc_entries", {"select": "source_id,acc_lines(account,credit,vehicle_id)",
                                                 "source": "eq.card_fuel", "source_id": f"like.fuel:pt:{m}:*", "order": "id.asc"})]:
        for ln in e["acc_lines"]:
            if ln["account"] == "2100" and ln.get("vehicle_id"):
                pt_w[vid[ln["vehicle_id"]]["plate_number"]] += float(ln["credit"])
    pt = dict(PT_TOTAL)
    pt["2026-04"] = round((sum(PT_TOTAL.values()) + 370868.30 + 383121.30) / 5, 2)     # no April PT total: the average of Jan-Mar, May, Jun
    for m, total in pt.items():
        plan.append((m, "pt", split(total, pt_w), "ประมาณ: " + ("ยอดรวมเดือน" if m != "2026-04" else "ไม่มียอด เม.ย. ใช้ค่าเฉลี่ย ม.ค.-มี.ค./พ.ค.-มิ.ย.")
                     + " แบ่งตามสัดส่วน PT ของแต่ละคัน พ.ค.+มิ.ย.", True))
    entries, flags = [], []
    for m, st, by, note, est in plan:
        out = acc_cardfuel.build(m, st, by, vehicles, note)
        for e in out["entries"]:
            if est:
                e["source_id"] = e["source_id"].replace(f"fuel:{st}:{m}:", f"fuel:{st}:{m}:est-")
                e["description"] = e["description"].replace("น้ำมัน", "น้ำมัน (ประมาณ) ", 1)
        entries += out["entries"]
        flags += out["flags"]
        print(f"{m} {st:<8} {'est' if est else 'exact'} {sum(by.values()):>12,.2f}  {len(by)} trucks")
    print("\n".join(flags))
    if "--write" in sys.argv:
        from app.accounting import _insert_entries, _require
        _require()
        for m in ("2026-01", "2026-02", "2026-03", "2026-04"):
            supabase.delete("acc_entries", {"source": "eq.card_fuel", "source_id": f"like.fuel:pt:{m}:*"})
        for m in BANGCHAK_TOTAL:
            supabase.delete("acc_entries", {"source": "eq.card_fuel", "source_id": f"like.fuel:bangchak:{m}:*"})
        _insert_entries(entries)
        print("written", len(entries))


_ = (re, date, norm_plate)

if __name__ == "__main__":
    main()
