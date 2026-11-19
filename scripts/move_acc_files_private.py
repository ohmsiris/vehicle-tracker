"""
One-off (19 Nov 2026): the accounts' page scans (accdocs/...) and attached files (accfiles/...) sat in the PUBLIC bucket under names that
can be guessed. This copies each into the private bucket at the same path (the app reads them from there, app/supabase.py
storage_download_private) and checks the copy. Nothing is deleted unless --delete-public is given, and then only a file whose private
copy has been checked.

    .venv/Scripts/python.exe scripts/move_acc_files_private.py                  copy + check
    .venv/Scripts/python.exe scripts/move_acc_files_private.py --delete-public  then remove the public copies
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app import supabase

PREFIXES = ("accdocs/", "accfiles/")


def main() -> int:
    delete = "--delete-public" in sys.argv
    public = [o for o in supabase.storage_list_all(supabase.STORAGE_BUCKET) if o["path"].startswith(PREFIXES)]
    private = {o["path"]: o["size"] for o in supabase.storage_list_all(supabase.PRIVATE_BUCKET)}
    copied = ok = removed = 0
    bad = []
    for o in public:
        if private.get(o["path"]) != o["size"]:
            resp = supabase._client().post("/storage/v1/object/copy", json={"bucketId": supabase.STORAGE_BUCKET, "sourceKey": o["path"],
                                                                            "destinationBucket": supabase.PRIVATE_BUCKET, "destinationKey": o["path"]})
            if resp.status_code >= 400 and "exists" not in resp.text.lower():
                bad.append(f"{o['path']}: copy failed HTTP {resp.status_code} {resp.text[:120]}")
                continue
            copied += 1
    private = {o["path"]: o["size"] for o in supabase.storage_list_all(supabase.PRIVATE_BUCKET)}
    safe = []
    for o in public:
        if private.get(o["path"]) == o["size"]:
            ok += 1
            safe.append(o["path"])
        else:
            bad.append(f"{o['path']}: private copy missing or a different size")
    if delete and not bad:
        for i in range(0, len(safe), 100):
            supabase.storage_delete(safe[i:i + 100], bucket=supabase.STORAGE_BUCKET)
        removed = len(safe)
    print(f"public files under {PREFIXES}: {len(public)} · copied now: {copied} · private copies checked: {ok} · public copies removed: {removed}")
    for b in bad[:20]:
        print("  PROBLEM", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
