"""
Import the customer register (Drive "Customer Info" sheet, tab สระบุรี) and its photos (Drive folder "Customer Info/สาขา สระบุรี",
one folder "N_name" per CUST_ID) into IceBox. See app/customers.py for the matching rules.

    python scripts/import_customer_register.py <Customer Info.xlsx> [--photos "<...>/Customer Info/สาขา สระบุรี"] [--write]

Without --write it only prints what it would do. Photos already imported (same Drive path) are skipped, so it is safe to run again.
"""
import hashlib
import mimetypes
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl  # noqa: E402

from app import customers, supabase  # noqa: E402


def main(argv):
    path = argv[1]
    write = "--write" in argv
    branch = "สระบุรี"
    ws = openpyxl.load_workbook(path, data_only=True)[branch]
    rows = customers.register_rows(list(ws.iter_rows(values_only=True)))
    res = customers.import_register(rows, branch, write=write)
    print(f"register: {res['rows']} rows -> {res['counts']}  ({'written' if write else 'dry run'})")
    for r in res["review"]:
        print(f"  review #{r['cust_no']}: truck {r['truck_no']} · {r['shop']} · bins {[b['size'] + '-' + b['no'] for b in r['bins']]}"
              f" · {len(r['suggest'])} suggestion(s)")
    if "--photos" not in argv:
        return
    base = argv[argv.index("--photos") + 1]
    done = {p["source"] for p in supabase.select_all("customer_photos", {"select": "source", "order": "id.asc"})} if write else set()
    known = {r["cust_no"] for r in rows}
    n_new = n_skip = n_bad = 0
    for folder in sorted(os.listdir(base)):
        m = re.match(r"^(\d+)_", folder)
        full = os.path.join(base, folder)
        if not m or not os.path.isdir(full):
            continue
        cust_no = int(m.group(1))
        if cust_no not in known:
            print(f"  folder {folder}: no register row #{cust_no} (kept anyway)")
        for name in sorted(os.listdir(full)):
            if name.lower() == "desktop.ini":
                continue
            source = f"{branch}/{folder}/{name}"
            if source in done:
                n_skip += 1
                continue
            mime = mimetypes.guess_type(name)[0] or ""
            if mime not in ("image/jpeg", "image/png", "image/webp", "application/pdf"):
                print(f"  skip {source}: {mime or 'unknown type'}")
                n_bad += 1
                continue
            if not write:
                n_new += 1
                continue
            with open(os.path.join(full, name), "rb") as f:
                raw = f.read()
            data, out_mime, ext = customers.shrink(raw, mime)
            spath = customers.photo_path(branch, cust_no, hashlib.sha1(raw).hexdigest()[:16], ext)
            supabase.storage_upload(spath, data, out_mime, upsert=True, bucket=supabase.PRIVATE_BUCKET)
            supabase.insert("customer_photos", [{"branch": branch, "cust_no": cust_no, "storage_path": spath, "source": source,
                                                 "name": name, "mime_type": out_mime, "byte_size": len(data)}], on_conflict="source",
                            ignore_duplicates=True)
            n_new += 1
            if n_new % 25 == 0:
                print(f"  ... {n_new} photos", flush=True)
    print(f"photos: {n_new} {'uploaded' if write else 'to upload'}, {n_skip} already there, {n_bad} skipped")


if __name__ == "__main__":
    main(sys.argv)
