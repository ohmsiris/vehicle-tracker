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

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import dashboard, events, history, repair_requests, supabase
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


@app.middleware("http")
async def static_cache_policy(request, call_next):
    """The page is several script/style files that must stay in step: after a
    deploy, a browser that kept an OLD board.js next to a NEW dashboard.js
    breaks the page. So the app's own files (and the page itself) are always
    re-checked with the server -- a cheap "304 not modified" when nothing
    changed -- while the fonts, which never change, are cached for a week."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/fonts/"):
        response.headers["Cache-Control"] = "public, max-age=604800"
    elif path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


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
    fixers = ["ช่างเอ๋"]   # the internal mechanic is always a one-tap choice
    if supabase.configured():
        try:
            fixers += [f for f in repair_requests.known_fixers() if f not in fixers]
        except supabase.SupabaseError:
            logger.exception("could not load fixer suggestions")
    return {"categories": CATEGORIES, "vehicles": load_roster(), "fixers": fixers}


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


# ------------------------------------------------------------ repair board
class NewRequest(BaseModel):
    vehicle_id: str | None = None
    problem: str = Field(default="", max_length=600)
    reported_by: str | None = Field(default=None, max_length=150)
    urgent: bool = False
    request_date: str | None = None


class EditRequest(BaseModel):
    problem: str | None = Field(default=None, max_length=600)
    reported_by: str | None = Field(default=None, max_length=150)
    urgent: bool | None = None


class RequestAction(BaseModel):
    action: str
    fixed_by: str = Field(default="", max_length=150)
    fixed_date: str | None = None
    reason: str = Field(default="", max_length=600)


def _board_call(fn, *args, **kwargs):
    """Runs a repair_requests operation and maps its errors to HTTP."""
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        return fn(*args, **kwargs)
    except repair_requests.InvalidRequest as exc:
        raise HTTPException(422, str(exc))
    except repair_requests.StaleRequest:
        raise HTTPException(409, "งานนี้ถูกเปลี่ยนสถานะไปแล้ว หน้าจอกำลังรีเฟรช")
    except supabase.SupabaseError:
        logger.exception("repair board call failed")
        raise HTTPException(502, "ทำรายการไม่สำเร็จ ลองอีกครั้ง")


@app.get("/api/requests")
def list_requests(scope: str = "active", x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    _check_token(x_app_token, token)
    if scope not in ("active", "recent"):
        raise HTTPException(400, "scope must be active or recent")
    return _board_call(repair_requests.list_active if scope == "active" else repair_requests.list_recent)


@app.post("/api/requests")
def create_request(body: NewRequest, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _board_call(repair_requests.create, body.model_dump())


@app.patch("/api/requests/{request_id}")
def edit_request(request_id: str, body: EditRequest, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _board_call(repair_requests.edit, request_id, body.model_dump(exclude_unset=True))


@app.post("/api/requests/{request_id}/action")
def request_action(request_id: str, body: RequestAction, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _board_call(
        repair_requests.act, request_id, body.action,
        fixed_by=body.fixed_by, fixed_date=body.fixed_date, reason=body.reason,
    )


# --------------------------------------------------------------- dashboard
@app.get("/api/dashboard")
def get_dashboard(
    months: int = 12,
    branch: str = "",
    x_app_token: str = Header(default=""),
    token: str = Query(default=""),
) -> dict:
    """Spend, categories, trucks, shops, fleet status and request stats for
    the dashboard page. Read-only; computed from the dashboard views (so it
    needs schema_v3_dashboard.sql applied)."""
    _check_token(x_app_token, token)
    today = repair_requests.today_bangkok()
    if os.environ.get("DEV_DASHBOARD_DEMO"):       # dev only -- made-up data, no database
        from app import dev_demo
        data = dev_demo.rows(today)
    else:
        if not supabase.configured():
            raise HTTPException(503, "Supabase is not configured on the server")
        try:
            data = {
                "events": supabase.select_all("v_events", {
                    "select": "event_id,event_date,event_month,vehicle_id,plate_number,vehicle_number,vehicle_branch,vendor,total_cost,source",
                    "order": "event_date.desc,event_id.asc"}),
                "lines": supabase.select_all("v_line_items", {
                    "select": "line_item_id,event_month,category,category_en,line_total,vehicle_branch", "order": "line_item_id.asc"}),
                "vehicles": supabase.select_all("v_vehicle_status", {"select": "*", "order": "vehicle_id.asc"}),
                "requests": supabase.select_all("v_requests", {
                    "select": "request_id,status,branch,days_in_status,days_to_fix,fixed_date,no_bill,has_record,status_changed_date", "order": "request_no.asc"}),
            }
        except supabase.SupabaseError:
            logger.exception("dashboard query failed")
            raise HTTPException(502, "โหลดข้อมูลภาพรวมไม่สำเร็จ ลองอีกครั้ง")
    return dashboard.build_dashboard(**data, today=today, months=months, branch=branch or None)


# ---------------------------------------------------- vehicle history (read-only)
def _history_call(fn, *args):
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        return fn(*args)
    except history.NotFound:
        raise HTTPException(404, "ไม่พบข้อมูลนี้")
    except supabase.SupabaseError:
        logger.exception("history query failed")
        raise HTTPException(502, "โหลดข้อมูลไม่สำเร็จ ลองอีกครั้ง")


@app.get("/api/vehicles")
def api_vehicles(x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    """Every active truck with its status and lifetime repair count / spend."""
    _check_token(x_app_token, token)
    return _history_call(history.list_vehicles)


@app.get("/api/vehicles/{vehicle_id}/events")
def api_vehicle_events(vehicle_id: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    """One truck's confirmed repair events, newest first, each with a one-line preview."""
    _check_token(x_app_token, token)
    return _history_call(history.vehicle_events, vehicle_id)


@app.get("/api/events/{event_id}")
def api_event(event_id: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """One confirmed event in full: header, every line, files, and the board requests it closed."""
    _check_token(x_app_token, token)
    return _history_call(history.event_detail, event_id)


@app.patch("/api/events/{event_id}")
def api_update_event(event_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """Edits a saved event (header, truck, and a full replacement of its lines) atomically."""
    _check_token(x_app_token, token)
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        events.update_event(event_id, body)
    except events.InvalidEvent as exc:
        raise HTTPException(422, str(exc))
    except events.EventNotFound:
        raise HTTPException(404, "ไม่พบรายการนี้")
    except events.MigrationMissing:
        raise HTTPException(503, "ยังแก้ไขไม่ได้ — ต้องรันไฟล์ schema_v4.sql ใน Supabase ก่อน")
    except supabase.SupabaseError:
        logger.exception("updating event failed")
        raise HTTPException(502, "บันทึกการแก้ไขไม่สำเร็จ ลองอีกครั้ง (ข้อมูลที่แก้ยังอยู่ในหน้านี้)")
    return {"ok": True}
