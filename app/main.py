"""
Vehicle Maintenance & Bill Tracker -- backend.

POST /api/extract
    multipart: `files` (1..MAX_FILES_PER_BATCH bill photos/PDFs), optional
    `plate` (the vehicle the user picked, for a mismatch check).
    Reads every file with Claude IN PARALLEL and returns one draft event +
    line items + warnings. Writes NOTHING anywhere -- the review page edits
    this and a separate "confirm and save" call (not built yet) persists it.
    A file that fails to read doesn't sink the batch; it comes back with
    ok=false and an error, and a warning on the draft.

Auth: shared secret APP_TOKEN, as `X-App-Token` header or `?token=`.
Refuses everything if APP_TOKEN isn't configured -- each call spends real
money, so there's no open-by-default mode.
"""
import hmac
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import events, supabase
from app.batch import build_draft
from app.categorize import MAX_ITEMS, categorize
from app.config import settings
from app.extraction import CATEGORIES, MODEL as EXTRACTION_MODEL, extract_file
from app.files import UnsupportedFile, prepare_file
from app.roster import load_roster, roster_prompt_text

logger = logging.getLogger("vehicle_tracker")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Vehicle Tracker")

MAX_PARALLEL_EXTRACTIONS = 4


def _check_token(header_token: str, query_token: str) -> None:
    if not settings.app_token:
        raise HTTPException(503, "APP_TOKEN is not configured on the server")
    supplied = header_token or query_token
    if not hmac.compare_digest(supplied.encode(), settings.app_token.encode()):
        raise HTTPException(403, "missing or incorrect token")


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    # The page itself holds no data and no secrets; every API call it makes
    # is token-gated.
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/meta")
def meta(x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """What the upload page needs to build its form: the category list and
    the fleet (for the vehicle picker). Reads data/vehicle_roster.csv for
    now; becomes the vehicles table once Supabase is wired in."""
    _check_token(x_app_token, token)
    return {"categories": CATEGORIES, "vehicles": load_roster()}


# Dev-only: serves a saved extraction response so the review UI can be
# exercised without paying for a live extraction. Off unless DEV_FIXTURE_PATH
# is set; never set it in production.
_dev_fixture = os.environ.get("DEV_FIXTURE_PATH", "").strip()
if _dev_fixture:

    @app.get("/api/dev/sample-draft", include_in_schema=False)
    def dev_sample_draft() -> FileResponse:
        return FileResponse(_dev_fixture, media_type="application/json")


def _extract_one(index: int, filename: str, raw: bytes, roster_text: str) -> dict:
    result = {
        "index": index, "filename": filename, "media_type": None,
        "ok": False, "error": None, "usage": None, "extraction": None,
    }
    try:
        data, media_type = prepare_file(raw)
        result["media_type"] = media_type
        result["extraction"], result["usage"] = extract_file(data, media_type, roster_text)
        result["ok"] = True
    except UnsupportedFile as exc:
        result["error"] = str(exc)
    except Exception:
        logger.exception("extraction failed for %s", filename)
        result["error"] = "ระบบอ่านบิลไม่สำเร็จ ลองอัปโหลดใหม่อีกครั้ง"
    return result


@app.post("/api/extract")
def extract(
    files: list[UploadFile] = File(...),
    plate: str = Form(""),
    x_app_token: str = Header(default=""),
    token: str = Query(default=""),
) -> dict:
    _check_token(x_app_token, token)
    if not settings.anthropic_api_key:
        raise HTTPException(503, "ANTHROPIC_API_KEY is not configured on the server")
    if not files:
        raise HTTPException(400, "no files uploaded")
    if len(files) > settings.max_files_per_batch:
        raise HTTPException(413, f"too many files (max {settings.max_files_per_batch} per batch)")

    raws: list[tuple[str, bytes]] = []
    for f in files:
        data = f.file.read(settings.max_file_bytes + 1)
        if len(data) > settings.max_file_bytes:
            raise HTTPException(413, f"{f.filename} is larger than {settings.max_file_bytes // (1024 * 1024)} MB")
        raws.append((f.filename or "upload", data))

    roster = load_roster()
    roster_text = roster_prompt_text(roster)
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_EXTRACTIONS) as pool:
        results = list(pool.map(lambda a: _extract_one(a[0], a[1][0], a[1][1], roster_text), enumerate(raws)))

    return build_draft(results, roster, selected_plate=plate)


class CategorizeItem(BaseModel):
    description: str = Field(default="", max_length=500)
    unit: str | None = Field(default=None, max_length=50)


class CategorizeRequest(BaseModel):
    items: list[CategorizeItem] = Field(max_length=MAX_ITEMS)


@app.post("/api/categorize")
def categorize_lines(
    body: CategorizeRequest,
    x_app_token: str = Header(default=""),
    token: str = Query(default=""),
) -> dict:
    """Re-decides the category of bill lines from their (possibly just
    corrected) text. Text only, one cheap call -- the review page uses it so
    fixing a line's wording doesn't leave the old wording's category behind."""
    _check_token(x_app_token, token)
    if not settings.anthropic_api_key:
        raise HTTPException(503, "ANTHROPIC_API_KEY is not configured on the server")
    try:
        return {"categories": categorize([i.model_dump() for i in body.items])}
    except RuntimeError:
        logger.exception("categorize failed")
        raise HTTPException(502, "categorization failed, try again")


@app.post("/api/events")
def save_event(
    payload: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    allow_duplicates: bool = Form(False),
    x_app_token: str = Header(default=""),
    token: str = Query(default=""),
):
    """
    Saves a confirmed event (a reviewed bill, or later a manual entry).
    multipart: `payload` (JSON: vehicle_id, source, event, line_items,
    attachments, bill_extractions) + `files` in the same order as
    payload.attachments. Photos go to Storage, then everything is written
    in one database call -- all or nothing. Returns {event_id, attachments}.

    409 with the earlier save's details if a bill file was already saved
    (resend with allow_duplicates=true to save it anyway).
    """
    _check_token(x_app_token, token)
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "payload is not valid JSON")
    if data.get("source") == "bill":
        data.setdefault("extraction_model", EXTRACTION_MODEL)
    if len(files) > settings.max_files_per_batch * 2:
        raise HTTPException(413, "too many files")

    raws: list[tuple[str, bytes]] = []
    for f in files:
        raw = f.file.read(settings.max_file_bytes + 1)
        if len(raw) > settings.max_file_bytes:
            raise HTTPException(413, f"{f.filename} is larger than {settings.max_file_bytes // (1024 * 1024)} MB")
        raws.append((f.filename or "upload", raw))

    try:
        return events.save_event(data, raws, allow_duplicates=allow_duplicates)
    except events.InvalidEvent as exc:
        raise HTTPException(422, str(exc))
    except events.DuplicateBill as exc:
        return JSONResponse(status_code=409, content={"detail": "duplicate bill", "duplicates": exc.duplicates})
    except supabase.SupabaseError:
        logger.exception("saving event failed")
        raise HTTPException(502, "บันทึกไม่สำเร็จ ลองอีกครั้ง (ข้อมูลที่กรอกยังอยู่)")
