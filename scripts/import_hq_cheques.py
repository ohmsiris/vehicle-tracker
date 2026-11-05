"""HQ's monthly cheque summary -> the books.

    python scripts/import_hq_cheques.py "<file.xls>" 2026-09 [สระบุรี|แก่งคอย] [--write]

The month is the month of the cheque run (the file name's MM-69). Without --write it only prints what it would book.
Re-running with --write replaces that month's rows for that branch.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import acc_cheques  # noqa: E402


def main() -> None:
    path, month = sys.argv[1], sys.argv[2]
    branch = next((a for a in sys.argv[3:] if not a.startswith("--")), "สระบุรี")
    y, m = (int(x) for x in month.split("-"))
    out = acc_cheques.import_file(path, date(y, m, 1), branch, write="--write" in sys.argv)
    print("sheet:", out["sheet"])
    for e in out["entries"]:
        acc = " + ".join(f"{ln['account']} {ln['debit']:,.2f}" for ln in e["lines"] if ln["debit"])
        print(f"{e['entry_date']}  {e['kind']:<10} {acc:<42} {e['party'][:26]:<26} {e['description'][:60]}")
    for f in out["flags"]:
        print("!", f)
    print("written" if "--write" in sys.argv else "dry run")


if __name__ == "__main__":
    main()
