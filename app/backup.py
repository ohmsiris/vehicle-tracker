"""
A full, self-contained backup of the vehicle tracker's data -- the thing
Supabase's own backups do NOT cover for photos, and do not exist at all on the
Free plan.

What it writes (under backups/ -- gitignored, it is business data):

    backups/files/<event_id>/<attachment_id>.jpg   every bill photo / PDF (repair bills AND purchase bills), shared by all backups
    backups/<YYYY-MM-DD_HHMM>/data/<table>.json    every table, one JSON file each
    backups/<YYYY-MM-DD_HHMM>/MANIFEST.json        row counts, file counts, problems found

Files are named by random ids and never change, so each run only downloads
files it doesn't already have: the second backup takes seconds and adds almost
nothing to the disk. Tables are small, so every run saves a complete copy.

Each run is CHECKED: row counts are read back, and every downloaded file's size
is compared with the size recorded in the database. A backup that doesn't check
out is reported as such, not trusted.

Read-only against Supabase. Views are not backed up (they are derived from the
tables); neither is the `generated` column bill_line_items.was_low_confidence
(the database recomputes it).
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("vehicle_tracker.backup")

# table -> stable ordering column (needed for safe paging): its primary key. EVERY table of the app is here, in the order
# schema_v2 .. v17 made them; a table added later must be added here too (tests/test_backup.py checks this against the schema files).
TABLES: dict[str, str] = {
    "categories": "name.asc",
    "vehicles": "id.asc",
    "maintenance_events": "id.asc",
    "event_attachments": "id.asc",
    "bill_line_items": "id.asc",
    "repair_requests": "id.asc",
    "repair_request_updates": "id.asc",
    "drivers": "id.asc",
    "machines": "id.asc",
    "machine_parts": "id.asc",
    "machine_events": "id.asc",
    "service_groups": "code.asc",
    "vehicle_plans": "vehicle_id.asc",
    "service_records": "id.asc",
    "km_readings": "id.asc",
    "service_intervals": "vehicle_class.asc,item_key.asc",
    "service_snoozes": "vehicle_id.asc,item_key.asc",
    "mgr_types": "key.asc",
    "mgr_places": "id.asc",
    "mgr_recurring": "id.asc",
    "mgr_tickets": "id.asc",
    "mgr_settings": "key.asc",
    "mgr_notifications": "key.asc",
    "shops": "id.asc",
    "shop_aliases": "id.asc",
    "shop_contacts": "id.asc",
    "purchase_bills": "id.asc",
    "purchase_lines": "id.asc",
    "purchase_attachments": "id.asc",
    "stock_items": "id.asc",
    "stock_item_aliases": "id.asc",
    "stock_moves": "id.asc",
    "employees": "id.asc",
}
# the tables whose rows point at a file in Storage (storage_path, byte_size): bill photos and purchase-bill photos
FILE_TABLES = ("event_attachments", "purchase_attachments")
BANGKOK = ZoneInfo("Asia/Bangkok")


def _json_bytes(rows: list[dict]) -> bytes:
    return json.dumps(rows, ensure_ascii=False, indent=1, default=str).encode("utf-8")


def run_backup(
    select_all: Callable[[str, dict], list[dict]],
    download: Callable[[str], bytes],
    root: Path,
    *,
    now: Optional[datetime] = None,
) -> dict:
    """
    select_all(table, params) -> every row; download(storage_path) -> file bytes.
    Returns the manifest (also written to disk). manifest["ok"] is False if
    anything didn't check out.
    """
    now = now or datetime.now(BANGKOK)
    run_dir = root / now.strftime("%Y-%m-%d_%H%M")
    data_dir = run_dir / "data"
    files_dir = root / "files"
    data_dir.mkdir(parents=True, exist_ok=True)
    files_dir.mkdir(parents=True, exist_ok=True)

    problems: list[str] = []
    counts: dict[str, int] = {}
    tables: dict[str, list[dict]] = {}
    missing_tables: list[str] = []
    for table, order in TABLES.items():
        try:
            rows = select_all(table, {"select": "*", "order": order})
        except Exception as exc:
            if "HTTP 404" in str(exc):                 # the table is not in this database yet (a schema file not run): nothing to save, not a failure
                missing_tables.append(table)
                rows = []
            else:
                raise
        # was_low_confidence is a GENERATED column: it can't be written back, so it isn't saved
        if table == "bill_line_items":
            rows = [{k: v for k, v in r.items() if k != "was_low_confidence"} for r in rows]
        (data_dir / f"{table}.json").write_bytes(_json_bytes(rows))
        counts[table] = len(rows)
        tables[table] = rows

    downloaded = already = 0
    total_bytes = 0
    attachments = [att for table in FILE_TABLES for att in tables[table]]
    for att in attachments:
        rel = att["storage_path"]
        # the path comes from our own database, but never let it write outside the backup folder
        target = (files_dir / rel).resolve()
        if files_dir.resolve() not in target.parents:
            problems.append(f"unsafe storage path skipped: {rel!r}")
            continue
        if target.exists() and target.stat().st_size == att.get("byte_size"):
            already += 1
            total_bytes += target.stat().st_size
            continue
        try:
            blob = download(rel)
        except Exception as exc:
            problems.append(f"could not download {rel}: {exc}")
            continue
        if att.get("byte_size") is not None and len(blob) != att["byte_size"]:
            problems.append(f"size mismatch for {rel}: database says {att['byte_size']} bytes, downloaded {len(blob)}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        downloaded += 1
        total_bytes += len(blob)

    # read the tables back from disk: what is on disk is what counts
    for table, n in counts.items():
        on_disk = len(json.loads((data_dir / f"{table}.json").read_text(encoding="utf-8")))
        if on_disk != n:
            problems.append(f"{table}: wrote {n} rows but read back {on_disk}")

    manifest = {
        "taken_at": now.isoformat(),
        "ok": not problems,
        "rows": counts,
        "files": {"expected": len(attachments), "downloaded_now": downloaded, "already_had": already, "bytes": total_bytes},
        "tables_not_in_this_database": missing_tables,
        "problems": problems,
    }
    (run_dir / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest
