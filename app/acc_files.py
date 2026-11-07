"""
บัญชี › แนบไฟล์: the files behind each checklist row (mid / end-of-month PDFs, electricity and water bills, the fuel-card statement,
HQ's cheque Excel ...), kept per feed, period and branch -- so the paper is never only in someone's Downloads folder.
Needs schema_v45 (acc_files). Files are served through the app (payroll papers are not put on a public link).

A row with files but no amounts yet shows "ได้รับไฟล์แล้ว · ยังไม่ได้ลงตัวเลข" on the checklist (accounting.feed_status).
clean_upload is pure and unit-tested.
"""
import hashlib
import re
import uuid
from typing import Optional

from app import supabase

MAX_FILES = 20
TYPES = {  # extension -> media type
    "pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "heic": "image/heic",
    "xls": "application/vnd.ms-excel", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "csv": "text/csv",
}


class BadFile(ValueError):
    pass


def clean_upload(feed: str, period: str, branch: str, files: list[tuple[str, bytes]], *, feeds: set, branches: tuple) -> list[dict]:
    """Checks one upload and returns the rows to store (with `_data`)."""
    if feed not in feeds:
        raise BadFile("เลือกรายการในเช็คลิสต์")
    if not re.fullmatch(r"\d{4}-\d{2}-[012]", period or ""):
        raise BadFile("งวดไม่ถูกต้อง")
    if branch not in (*branches, "all"):
        raise BadFile("สาขาไม่ถูกต้อง")
    if not files:
        raise BadFile("เลือกไฟล์อย่างน้อยหนึ่งไฟล์")
    if len(files) > MAX_FILES:
        raise BadFile(f"ครั้งละไม่เกิน {MAX_FILES} ไฟล์")
    out = []
    for name, data in files:
        ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
        if ext not in TYPES:
            raise BadFile(f"{name}: รับเฉพาะ PDF รูป และ Excel / CSV")
        if not data:
            raise BadFile(f"{name}: ไฟล์ว่าง")
        fid = str(uuid.uuid4())
        out.append({"id": fid, "feed": feed, "period": period, "branch": branch, "file_name": name[:200],
                    "storage_path": f"accfiles/{period}/{fid}.{ext}", "mime_type": TYPES[ext], "byte_size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(), "_data": data})
    return out


def upload(feed: str, period: str, branch: str, files: list[tuple[str, bytes]]) -> list[dict]:
    from app.accounting import BRANCHES, FEED_BY_KEY
    rows = clean_upload(feed, period, branch, files, feeds={k for k, f in FEED_BY_KEY.items() if f["from"] != "program"}, branches=BRANCHES)
    have = {r["sha256"] for r in supabase.select_all("acc_files", {"select": "sha256", "feed": f"eq.{feed}", "period": f"eq.{period}", "order": "id.asc"})}
    dup = [r["file_name"] for r in rows if r["sha256"] in have]
    if dup:
        raise BadFile(f"ไฟล์นี้แนบไว้แล้ว: {', '.join(dup)}")
    done: list[str] = []
    try:
        for r in rows:
            supabase.storage_upload(r["storage_path"], r["_data"], r["mime_type"])
            done.append(r["storage_path"])
        supabase.insert("acc_files", [{k: v for k, v in r.items() if k != "_data"} for r in rows])
    except Exception:
        supabase.storage_delete(done)
        raise
    return [{k: r[k] for k in ("id", "feed", "period", "branch", "file_name", "mime_type", "byte_size")} for r in rows]


def for_month(month: str, branch: str = "") -> list[dict]:
    """The files of a company month (both halves), for one branch (plus the ones filed for both) or all."""
    params = {"select": "id,feed,period,branch,file_name,mime_type,byte_size,uploaded_at", "period": f"like.{month}-*", "order": "uploaded_at.asc"}
    if branch:
        params["branch"] = f"in.({branch},all)"
    return supabase.select_all("acc_files", params)


def _row(file_id: str) -> dict:
    rows = supabase.select("acc_files", {"select": "*", "id": f"eq.{file_id}", "limit": "1"})
    if not rows:
        raise BadFile("ไม่พบไฟล์นี้")
    return rows[0]


def download(file_id: str) -> tuple[bytes, str, str]:
    r = _row(file_id)
    return supabase.storage_download(r["storage_path"]), r["mime_type"] or "application/octet-stream", r["file_name"]


def remove(file_id: str) -> None:
    r = _row(file_id)
    supabase.delete("acc_files", {"id": f"eq.{file_id}"})
    supabase.storage_delete([r["storage_path"]])


def counts(month: str, half: Optional[int], branch: str) -> dict:
    """{feed: number of files} for the checklist of this period."""
    out: dict = {}
    for f in for_month(month, branch):
        h = int(f["period"][-1])
        if half and h not in (half, 0):
            continue
        out[f["feed"]] = out.get(f["feed"], 0) + 1
    return out
