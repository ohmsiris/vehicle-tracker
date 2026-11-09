"""Load PEA AMR 15-minute readings and Kaeng Khoi's trip tables (schema v57) -- owner, 9 Nov 2026.

    python scripts/import_amr.py srb "<AMR folder>" [--write]          Saraburi: every kWh*.xls under the folder (Meter_615, Meter_916)
    python scripts/import_amr.py kk "<icebox_dryrun folder>" [--write]  Kaeng Khoi: CP Accounting's export (elec_amr_readings.csv,
                                                                         elec_amr_daily.csv, fin_trip_tables.csv, fin_trip_days.csv)

Re-running is harmless: readings upsert on (ca_no, slot_start); trip tables are replaced per (source_file, source_page).
A month downloaded twice is read once.
"""
import csv
import glob
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import amr, supabase  # noqa: E402

BRANCH = {"srb": "สระบุรี", "kk": "แก่งคอย"}


def _chunks(rows, n=1000):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def srb(folder: str) -> list[dict]:
    seen, out = set(), []
    for f in sorted(glob.glob(os.path.join(folder, "**", "*.xls"), recursive=True)):
        rep = amr.parse(open(f, encoding="utf-8", errors="replace").read())
        if not rep["month"] or (rep["ca_no"], rep["month"]) in seen:
            print("skip", os.path.basename(f), rep["month"] or "(no data)")
            continue
        seen.add((rep["ca_no"], rep["month"]))
        out += [r | {"branch": BRANCH["srb"], "ca_no": rep["ca_no"], "meter_no": rep["meter_no"], "multiplier": 1} for r in rep["rows"]]
        print(rep["ca_no"], rep["month"], len(rep["rows"]), round(sum(r["kwh"] for r in rep["rows"])))
    return out


def _csv(folder, name):
    with open(os.path.join(folder, name + ".csv"), encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def kk(folder: str, write: bool) -> list[dict]:
    readings = [{"branch": r["branch"], "ca_no": r["ca_no"], "meter_no": r["meter_no"], "slot_start": r["slot_start"], "tou": r["tou"],
                 "kwh": float(r["kwh"]), "multiplier": float(r["multiplier"])} for r in _csv(folder, "elec_amr_readings")]
    daily = [{"ca_no": r["ca_no"], "day": r["day"], "kwh": float(r["kwh"]), "multiplier": float(r["multiplier"])} for r in _csv(folder, "elec_amr_daily")]
    tables = _csv(folder, "fin_trip_tables")
    days = _csv(folder, "fin_trip_days")
    print("kk readings", len(readings), Counter(r["slot_start"][:7] for r in readings))
    print("kk daily", len(daily), "trip tables", len(tables), "trip days", len(days))
    if write:
        for c in _chunks(daily):
            supabase.insert("elec_amr_daily", c, on_conflict="ca_no,day")
        by_old = {}
        for t in tables:
            supabase.delete("fin_trip_tables", {"source_file": f"eq.{t['source_file']}", "source_page": f"eq.{t['source_page']}"})
            row = {k: (t[k] or None) for k in ("branch", "month", "employee_code", "driver_written", "written_total", "computed_total",
                                               "source_file", "source_page", "reviewed_at")}
            row["rates"] = [float(x) for x in t["rates"].strip("{}").split(",") if x]
            by_old[t["id"]] = supabase.insert("fin_trip_tables", [row])[0]["id"]
        rows = [{"table_id": by_old[d["table_id"]], "trip_date": d["trip_date"], "rate": float(d["rate"]), "trips": float(d["trips"])} for d in days]
        for c in _chunks(rows):
            supabase.insert("fin_trip_days", c)
    return readings


def main() -> None:
    which, folder = sys.argv[1], sys.argv[2]
    write = "--write" in sys.argv
    readings = srb(folder) if which == "srb" else kk(folder, write)
    print("readings", len(readings))
    if write:
        for c in _chunks(readings):
            supabase.insert("elec_amr_readings", c, on_conflict="ca_no,slot_start")
        print("written")


if __name__ == "__main__":
    main()
