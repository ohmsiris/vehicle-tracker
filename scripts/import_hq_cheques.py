"""HQ's monthly cheque summaries -> the books.

    python scripts/import_hq_cheques.py "<file.xls or folder>" [สระบุรี|แก่งคอย|both] [--write]

The month of each cheque run is read from the sheet's title row ("... ประจำเดือน กันยายน 2569"), not the file name.
Without --write it only prints what it would book. Re-running with --write replaces that month's rows for that branch.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import acc_cheques  # noqa: E402


def main() -> None:
    target = sys.argv[1]
    which = next((a for a in sys.argv[2:] if not a.startswith("--")), "both")
    branches = ["สระบุรี", "แก่งคอย"] if which == "both" else [which]
    files = sorted(glob.glob(os.path.join(target, "*.xls*"))) if os.path.isdir(target) else [target]
    write = "--write" in sys.argv
    for path in files:
        for branch in branches:
            out = acc_cheques.import_file(path, None, branch, write=write)
            print(f"## {out['month']:%Y-%m} {branch} ({os.path.basename(path)})")
            for e in out["entries"]:
                acc = " + ".join(f"{ln['account']} {ln['debit']:,.2f}" for ln in e["lines"] if ln["debit"])
                print(f"   {e['entry_date']}  {e['kind']:<10} {acc:<40} {e['party'][:24]:<24} {e['description'][:60]}")
            for f in out["flags"]:
                print("   !", f)
    print("written" if write else "dry run")


if __name__ == "__main__":
    main()
