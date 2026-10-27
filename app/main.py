"""
IceBox (vehicle maintenance, bills, factory purchasing) -- backend.

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
from contextlib import asynccontextmanager
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import attendance, dashboard, documents, employees, events, fleet, history, home, loans, rates, logbook, logsource, machinery, mgr, mgr_jobs, purchasing, repair_followup, repair_requests, service_plan, shops, stock, summarize, supabase
from app.purchase_extraction import read_bill as read_purchase_bill
from app.batch import build_draft
from app.categorize import MAX_ITEMS, categorize
from app.config import settings
from app.extraction import CATEGORIES, MODEL as EXTRACTION_MODEL, extract_file
from app.files import UnsupportedFile, prepare_file
from app.roster import load_roster, roster_prompt_text

logger = logging.getLogger("vehicle_tracker")
logging.basicConfig(level=logging.INFO)

# No public API docs: every endpoint is token-gated, but there is no reason to publish a map of them.
@asynccontextmanager
async def lifespan(_app):
    _startup_checks()
    mgr_jobs.start_scheduler()          # makes the recurring tickets (a no-op unless Supabase is configured)
    yield


app = FastAPI(title="IceBox", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

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
    # The page is opened with ?token=... once; make sure that URL is never sent on to another site
    # (photos load from Supabase), the browser can't be talked into guessing content types, and the
    # app can't be shown inside someone else's page.
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    path = request.url.path
    if path.startswith("/static/fonts/"):
        response.headers["Cache-Control"] = "public, max-age=604800"
    elif path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _startup_checks() -> None:
    """Say plainly in the log what is missing, instead of failing mysteriously on the first request."""
    missing = [name for name, val in (("APP_TOKEN", settings.app_token), ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
                                      ("SUPABASE_URL", settings.supabase_url), ("SUPABASE_SERVICE_KEY", settings.supabase_service_key)) if not val]
    if missing:
        logger.error("MISSING SETTINGS: %s -- the app will start but the related features will refuse to work", ", ".join(missing))
    if os.environ.get("DEV_DASHBOARD_DEMO") or os.environ.get("DEV_FIXTURE_PATH"):
        logger.error("DEV_* variables are set: the dashboard would show MADE-UP data. Remove them from the environment.")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    # The page itself holds no data and no secrets; every API call it makes
    # is token-gated.
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "icons" / "favicon.ico", media_type="image/x-icon")


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
    roster = load_roster()
    if supabase.configured():          # km to the next oil change per truck: the bill page suggests "mileage + this" for the next time
        oil = service_plan.oil_km_by_vehicle(roster)
        roster = [{**r, "oil_km": oil.get(r.get("id"))} for r in roster]
    return {"categories": CATEGORIES, "vehicles": roster, "fixers": fixers, "shops": shops.choices(), "shop_contacts": shops.directory(),
            "usages": fleet.usage_choices(roster), "branches": fleet.BRANCHES,
            "fleet_ready": fleet.has_v6() if supabase.configured() else False,
            "fleet_v7": fleet.has_v7() if supabase.configured() else False, "vehicle_classes": fleet.VEHICLE_CLASSES,
            "fleet_v8": fleet.has_v8() if supabase.configured() else False}


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


def _summarize_quietly(event_id: str) -> None:
    try:
        summarize.fill_summary(event_id)
    except Exception:
        logger.exception("could not write the summary for event %s", event_id)


@app.post("/api/events")
def save_event(
    background: BackgroundTasks,
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
        saved = events.save_event(data, raws, allow_duplicates=allow_duplicates)
        logsource.invalidate()
        # Write the one-line "what was done" AFTER replying, so saving never waits on the AI and
        # never fails because of it. (No-op until schema_v4.sql has been run.)
        if settings.anthropic_api_key:
            background.add_task(_summarize_quietly, saved["event_id"])
        return saved
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
    shop: str | None = Field(default=None, max_length=150)             # already at a shop when it is added
    sent_date: str | None = None
    expected_return: str | None = None
    on_paper: bool | None = None                                       # False = never written on the FOH sheet (emergency)


class EditRequest(BaseModel):
    problem: str | None = Field(default=None, max_length=600)
    reported_by: str | None = Field(default=None, max_length=150)
    urgent: bool | None = None
    vehicle_id: str | None = None
    request_date: str | None = None


class RequestAction(BaseModel):
    action: str
    fixed_by: str = Field(default="", max_length=150)
    fixed_date: str | None = None
    reason: str = Field(default="", max_length=600)
    shop: str = Field(default="", max_length=150)
    sent_date: str | None = None
    expected_return: str | None = None


def _board_call(fn, *args, **kwargs):
    """Runs a repair_requests operation and maps its errors to HTTP."""
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        result = fn(*args, **kwargs)
        logsource.invalidate()          # (reads clear it too -- harmless, and a write can never be missed)
        return result
    except (repair_requests.InvalidRequest, repair_followup.InvalidFollowup) as exc:
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
    rows = _board_call(repair_requests.list_active if scope == "active" else repair_requests.list_recent)
    return _with_call_targets(rows) if scope == "active" else rows


def _with_call_targets(rows: list) -> list:
    """Each active request says who to ring about it (the truck's driver, or for a factory truck whoever reported it).
    Best effort: the board must still load if the driver list cannot be read."""
    if not rows or not any(isinstance(r, dict) and r.get("vehicle_id") for r in rows) or not fleet.has_v6():
        return rows
    try:
        vehicles = supabase.select_all("vehicles", {"select": "id,usage,main_driver_id", "order": "id.asc"})
        return fleet.with_call_targets(rows, vehicles, fleet.list_drivers(include_inactive=False))
    except supabase.SupabaseError:
        logger.exception("could not work out who to call")
        return rows


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
        shop=body.shop, sent_date=body.sent_date, expected_return=body.expected_return,
    )


class CheckAnswer(BaseModel):
    result: str
    note: str = Field(default="", max_length=600)


class ProgressNote(BaseModel):
    note: str = Field(default="", max_length=600)
    remind_days: int | None = None


@app.post("/api/requests/{request_id}/check")
def request_check(request_id: str, body: CheckAnswer, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """The driver was asked how the car is after the shop: ok / problem (opens a new job) / skipped."""
    _check_token(x_app_token, token)
    return _board_call(repair_followup.answer_check, request_id, body.result, body.note)


@app.get("/api/requests/{request_id}/updates")
def request_updates(request_id: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    _check_token(x_app_token, token)
    return _board_call(repair_followup.list_updates, request_id)


@app.post("/api/requests/{request_id}/updates")
def request_add_update(request_id: str, body: ProgressNote, x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    """A progress note on a car at a shop (optionally with a reminder on the manager's board to ask again)."""
    _check_token(x_app_token, token)
    return _board_call(repair_followup.add_update, request_id, body.note, body.remind_days)


@app.delete("/api/requests/updates/{update_id}")
def request_delete_update(update_id: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    _board_call(repair_followup.delete_update, update_id)
    return {"ok": True}


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
def api_vehicles(include_inactive: bool = False, x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    """Every active truck (or every truck ever, with include_inactive) with its status, usage, main driver
    and lifetime repair count / spend."""
    _check_token(x_app_token, token)
    return _history_call(history.list_vehicles, include_inactive)


# ------------------------------------------------------- fleet: trucks and drivers
def _fleet_call(fn, *args):
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        return fn(*args)
    except fleet.InvalidFleet as exc:
        raise HTTPException(422, str(exc))
    except fleet.NotFound:
        raise HTTPException(404, "ไม่พบข้อมูลนี้")
    except supabase.SupabaseError:
        logger.exception("fleet change failed")
        raise HTTPException(502, "ทำรายการไม่สำเร็จ ลองอีกครั้ง")


# ------------------------------------------------------------- พนักงาน (employees; salaries, loans, letters later)
# Every endpoint here needs the app token AND, when HR_PIN is set on the server, the PIN (header X-HR-Pin).
def _hr_call(pin: str, fn, *args):
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        employees.check_pin(pin)
        return fn(*args)
    except employees.PinRequired:
        raise HTTPException(423, "ส่วนพนักงานล็อกอยู่ — ใส่ PIN ก่อน")
    except employees.InvalidEmployee as exc:
        raise HTTPException(422, str(exc))
    except employees.NotFound:
        raise HTTPException(404, "ไม่พบข้อมูลนี้")
    except supabase.SupabaseError:
        logger.exception("employee change failed")
        raise HTTPException(502, "ทำรายการไม่สำเร็จ ลองอีกครั้ง")


@app.get("/api/employees/options")
def api_employee_options(x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """Whether the section needs a PIN, and the choices -- readable without the PIN (the page asks for it from this)."""
    _check_token(x_app_token, token)
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    return employees.options()


@app.get("/api/employees")
def api_employees(x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, employees.list_employees)


@app.get("/api/employees/{employee_id}/rates")
def api_employee_rates(employee_id: str, x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, rates.list_rates, employee_id)


@app.post("/api/employees/{employee_id}/rates")
def api_add_employee_rate(employee_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, rates.add_rate, employee_id, body)


@app.delete("/api/rates/{rate_id}")
def api_delete_rate(rate_id: str, x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, rates.delete_rate, rate_id)


@app.get("/api/employees/{employee_id}")
def api_employee(employee_id: str, x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, employees.get_employee, employee_id)


@app.post("/api/employees")
def api_create_employee(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, employees.create_employee, body)


@app.patch("/api/employees/{employee_id}")
def api_edit_employee(employee_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default=""),
                      x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, employees.edit_employee, employee_id, body)


@app.post("/api/employees/{employee_id}/resign")
def api_resign_employee(employee_id: str, body: dict = Body(default={}), x_app_token: str = Header(default=""), token: str = Query(default=""),
                        x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, employees.resign, employee_id, body)


@app.post("/api/employees/{employee_id}/reinstate")
def api_reinstate_employee(employee_id: str, x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, employees.reinstate, employee_id)


@app.delete("/api/employees/{employee_id}")
def api_delete_employee(employee_id: str, x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    _hr_call(x_hr_pin, employees.delete_employee, employee_id)
    return {"ok": True}


# ------------------------------------------------------------- เงินกู้ (loans): same lock as the rest of พนักงาน
@app.get("/api/loans/options")
def api_loan_options(x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    return loans.options()


@app.get("/api/loans")
def api_loans(employee_id: str = "", x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, loans.list_loans, employee_id)


@app.post("/api/loans/preview")
def api_loan_preview(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, lambda: loans.preview(body, employees.today_th()))


@app.get("/api/loans/month")
def api_loan_month(month: str = "", x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, loans.month_deductions, month)


@app.post("/api/loans/month/apply")
def api_loan_month_apply(body: dict = Body(default={}), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, loans.apply_month, body.get("month") or "", body.get("loan_ids") or None)


@app.post("/api/loans")
def api_create_loan(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, loans.create_loan, body)


@app.get("/api/loans/{loan_id}")
def api_loan(loan_id: str, x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, loans.get_loan, loan_id)


@app.patch("/api/loans/{loan_id}")
def api_edit_loan(loan_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, loans.edit_loan, loan_id, body)


@app.post("/api/loans/{loan_id}/entries")
def api_loan_entry(loan_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, loans.add_entry, loan_id, body)


@app.delete("/api/loans/{loan_id}/entries/{entry_id}")
def api_loan_entry_delete(loan_id: str, entry_id: str, x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, loans.delete_entry, loan_id, entry_id)


@app.post("/api/loans/{loan_id}/cancel")
def api_loan_cancel(loan_id: str, body: dict = Body(default={}), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, loans.cancel_loan, loan_id, body)


@app.delete("/api/loans/{loan_id}")
def api_loan_delete(loan_id: str, x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    _hr_call(x_hr_pin, loans.delete_loan, loan_id)
    return {"ok": True}


# ------------------------------------------------------------- เวลาทำงาน (the fingerprint machine's report and the owner's decisions): same lock as พนักงาน
@app.get("/api/attendance")
def api_attendance(month: str = "", x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, attendance.get_month, month)


@app.post("/api/attendance/upload")
def api_attendance_upload(file: UploadFile = File(...), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    data = file.file.read(attendance.MAX_UPLOAD + 1)
    return _hr_call(x_hr_pin, attendance.import_report, data, file.filename or "")


@app.put("/api/attendance/review")
def api_attendance_review(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, attendance.set_review, body)


@app.post("/api/attendance/apply-suggestions")
def api_attendance_apply(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, attendance.apply_suggestions, body.get("month") or "")


# ------------------------------------------------------------- เอกสารพนักงาน (หนังสือเตือน / บันทึกข้อความ): same lock as the rest of พนักงาน
@app.get("/api/documents/options")
def api_document_options(x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    return documents.options()


@app.get("/api/documents")
def api_documents(employee_id: str = "", doc_type: str = "", x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, documents.list_documents, employee_id, doc_type)


@app.get("/api/documents/signature")
def api_document_signature(x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, lambda: {"data_uri": documents.get_signature()})


@app.put("/api/documents/signature")
def api_set_document_signature(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, documents.set_signature, body.get("data_uri"))


@app.delete("/api/documents/signature")
def api_clear_document_signature(x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, documents.clear_signature)


@app.get("/api/documents/suggest")
def api_document_suggest(employee_id: str, x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, documents.suggest, employee_id)


@app.post("/api/documents")
def api_create_document(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, documents.create_document, body)


@app.get("/api/documents/{doc_id}")
def api_document(doc_id: str, x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, documents.get_document, doc_id)


@app.patch("/api/documents/{doc_id}")
def api_update_document(doc_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, documents.update_document, doc_id, body)


@app.post("/api/documents/{doc_id}/print-options")
def api_document_print_options(doc_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, documents.set_print_options, doc_id, body)


@app.post("/api/documents/{doc_id}/issue")
def api_issue_document(doc_id: str, x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, documents.issue_document, doc_id)


@app.post("/api/documents/{doc_id}/acknowledge")
def api_acknowledge_document(doc_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, documents.acknowledge, doc_id, body)


@app.post("/api/documents/{doc_id}/cancel")
def api_cancel_document(doc_id: str, body: dict = Body(default={}), x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, documents.cancel_document, doc_id, body)


@app.post("/api/documents/{doc_id}/duplicate")
def api_duplicate_document(doc_id: str, x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    return _hr_call(x_hr_pin, documents.duplicate_document, doc_id)


@app.delete("/api/documents/{doc_id}")
def api_delete_document(doc_id: str, x_app_token: str = Header(default=""), token: str = Query(default=""), x_hr_pin: str = Header(default="")) -> dict:
    _check_token(x_app_token, token)
    _hr_call(x_hr_pin, documents.delete_document, doc_id)
    return {"ok": True}


@app.post("/api/vehicles")
def api_create_vehicle(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _fleet_call(fleet.create_vehicle, body)


@app.patch("/api/vehicles/{vehicle_id}")
def api_edit_vehicle(vehicle_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """Edits only the fields present in the body (usage, main driver, details, active = retire / bring back)."""
    _check_token(x_app_token, token)
    return _fleet_call(fleet.edit_vehicle, vehicle_id, body)


# ------------------------------------------------------------- machinery
def _machinery_call(fn, *args, **kwargs):
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        return fn(*args, **kwargs)
    except machinery.InvalidMachinery as exc:
        raise HTTPException(422, str(exc))
    except machinery.NotFound:
        raise HTTPException(404, "ไม่พบข้อมูลนี้")
    except supabase.SupabaseError:
        logger.exception("machinery call failed")
        raise HTTPException(502, "ทำรายการไม่สำเร็จ ลองอีกครั้ง")


@app.get("/api/machinery/options")
def api_machinery_options(x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return {**machinery.options(), "ready": machinery.has_v9() if supabase.configured() else False}


@app.get("/api/machines")
def api_machines(include_inactive: bool = False, x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    _check_token(x_app_token, token)
    return _machinery_call(machinery.list_machines, include_inactive)


@app.post("/api/machines")
def api_create_machine(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _machinery_call(machinery.create_machine, body)


@app.get("/api/machines/{machine_id}")
def api_machine(machine_id: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _machinery_call(machinery.get_machine, machine_id)


@app.patch("/api/machines/{machine_id}")
def api_edit_machine(machine_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _machinery_call(machinery.edit_machine, machine_id, body)


@app.get("/api/machine-parts")
def api_machine_parts(q: str = "", machine_id: str = "", location: str = "", part: str = "", limit: int = 200, offset: int = 0,
                      x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _machinery_call(machinery.list_parts, q=q, machine_id=machine_id, location=location, part=part, limit=limit, offset=offset)


@app.post("/api/machine-parts")
def api_create_part(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _machinery_call(machinery.create_part, body)


@app.patch("/api/machine-parts/{part_id}")
def api_edit_part(part_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _machinery_call(machinery.edit_part, part_id, body)


@app.delete("/api/machine-parts/{part_id}")
def api_delete_part(part_id: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _machinery_call(machinery.delete_part, part_id)


@app.get("/api/machine-events")
def api_machine_events(q: str = "", machine_id: str = "", area: str = "", kind: str = "", branch: str = "", date_from: str = "", date_to: str = "",
                       limit: int = 50, offset: int = 0, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _machinery_call(machinery.list_events, q=q, machine_id=machine_id, area=area, kind=kind, branch=branch, date_from=date_from, date_to=date_to,
                           limit=limit, offset=offset)


@app.post("/api/machine-events")
def api_create_machine_event(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _machinery_call(machinery.create_event, body)


@app.patch("/api/machine-events/{event_id}")
def api_edit_machine_event(event_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _machinery_call(machinery.edit_event, event_id, body)


# ------------------------------------------------------------- maintenance program
def _plan_call(fn, *args, **kwargs):
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        return fn(*args, **kwargs)
    except service_plan.InvalidPlan as exc:
        raise HTTPException(422, str(exc))
    except service_plan.NotFound:
        raise HTTPException(404, "ไม่พบข้อมูลนี้")
    except supabase.SupabaseError:
        logger.exception("maintenance plan call failed")
        raise HTTPException(502, "ทำรายการไม่สำเร็จ ลองอีกครั้ง")


@app.post("/api/plan/services")
def api_plan_record_service(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _plan_call(service_plan.record_service, body)


# ------------------------------------------------------------- buying for the factory: purchase bills, prices, stock
def _buy_call(fn, *args, **kwargs):
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        return fn(*args, **kwargs)
    except (purchasing.InvalidPurchase, stock.InvalidStock, shops.InvalidShop) as exc:
        raise HTTPException(422, str(exc))
    except (purchasing.NotFound, stock.NotFound):
        raise HTTPException(404, "ไม่พบรายการนี้")
    except supabase.SupabaseError:
        logger.exception("purchasing call failed")
        raise HTTPException(502, "ทำรายการไม่สำเร็จ ลองอีกครั้ง")


def _read_uploads(files: list[UploadFile], limit: int) -> list[tuple[str, bytes]]:
    if len(files) > limit:
        raise HTTPException(413, f"too many files (max {limit})")
    out = []
    for f in files:
        data = f.file.read(settings.max_file_bytes + 1)
        if len(data) > settings.max_file_bytes:
            raise HTTPException(413, f"{f.filename} is larger than {settings.max_file_bytes // (1024 * 1024)} MB")
        out.append((f.filename or "upload", data))
    return out


@app.get("/api/purchases/options")
def api_purchase_options(x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _buy_call(purchasing.options)


@app.post("/api/purchases/extract")
def api_purchase_extract(files: list[UploadFile] = File(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """The photos / PDF pages of ONE purchase bill -> a draft for the review page (nothing is saved)."""
    _check_token(x_app_token, token)
    if not settings.anthropic_api_key:
        raise HTTPException(503, "ANTHROPIC_API_KEY is not configured on the server")
    raws = _read_uploads(files, settings.max_files_per_batch)
    if not raws:
        raise HTTPException(400, "no files uploaded")
    prepared = []
    for name, raw in raws:
        try:
            prepared.append(prepare_file(raw))
        except UnsupportedFile as exc:
            raise HTTPException(422, f"{name}: {exc}")
    try:
        extraction, usage = read_purchase_bill(prepared)
    except Exception:
        logger.exception("purchase extraction failed")
        raise HTTPException(502, "ระบบอ่านบิลไม่สำเร็จ ลองอัปโหลดใหม่อีกครั้ง")
    items = _buy_call(purchasing.items_with_aliases) if purchasing.has_v16() else []
    return {"draft": purchasing.draft_from_extraction(extraction, items), "extraction": extraction, "usage": usage}


@app.post("/api/purchases")
def api_purchase_save(payload: str = Form(...), files: list[UploadFile] = File(default=[]), allow_duplicates: bool = Form(False),
                      x_app_token: str = Header(default=""), token: str = Query(default="")):
    _check_token(x_app_token, token)
    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "payload is not valid JSON")
    raws = _read_uploads(files, settings.max_files_per_batch)
    try:
        return _buy_call(purchasing.save_bill, data, raws, allow_duplicates=allow_duplicates, extraction_model=EXTRACTION_MODEL)
    except purchasing.DuplicateBill as exc:
        return JSONResponse(status_code=409, content={"detail": "duplicate bill", "duplicates": exc.found})


@app.get("/api/purchases")
def api_purchases(limit: int = Query(default=100), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _buy_call(purchasing.list_bills, limit)


@app.get("/api/purchases/{bill_id}")
def api_purchase(bill_id: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _buy_call(purchasing.bill_detail, bill_id)


@app.delete("/api/purchases/{bill_id}")
def api_purchase_delete(bill_id: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    _buy_call(purchasing.delete_bill, bill_id)
    return {"ok": True}


@app.patch("/api/purchases/lines/{line_id}")
def api_purchase_line(line_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _buy_call(purchasing.link_line, line_id, body)


@app.get("/api/prices")
def api_prices(q: str = Query(default="", max_length=80), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _buy_call(purchasing.prices, q)


@app.get("/api/stock")
def api_stock(x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _buy_call(stock.overview)


@app.post("/api/stock/items")
def api_stock_new_item(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _buy_call(stock.create_item, body)


@app.patch("/api/stock/items/{item_id}")
def api_stock_edit_item(item_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _buy_call(stock.update_item, item_id, body)


@app.get("/api/stock/items/{item_id}/moves")
def api_stock_moves(item_id: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    _check_token(x_app_token, token)
    return _buy_call(stock.item_moves, item_id)


@app.post("/api/stock/moves")
def api_stock_move(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _buy_call(stock.add_move, body)


@app.delete("/api/stock/moves/{move_id}")
def api_stock_move_delete(move_id: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    _buy_call(stock.delete_move, move_id)
    return {"ok": True}


# ------------------------------------------------------------- settings: the repair shops
def _shops_call(fn, *args, **kwargs):
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        out = fn(*args, **kwargs)
        logsource.invalidate()                 # the log folds shop names: it must read the new words
        return out
    except shops.InvalidShop as exc:
        raise HTTPException(422, str(exc))
    except shops.NotFound:
        raise HTTPException(404, "ไม่พบร้านนี้")
    except supabase.SupabaseError:
        logger.exception("shops call failed")
        raise HTTPException(502, "ทำรายการไม่สำเร็จ ลองอีกครั้ง")


@app.get("/api/settings/shops")
def api_settings_shops(x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _shops_call(shops.settings)


@app.get("/api/settings/shops/test")
def api_settings_shop_test(name: str = Query(default="", max_length=200), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """What shop a bill's vendor text is, and by which word (the settings page's test box)."""
    _check_token(x_app_token, token)
    return shops.explain(name)


@app.post("/api/settings/shops")
def api_settings_new_shop(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _shops_call(shops.create, body)


@app.patch("/api/settings/shops/{shop_id}")
def api_settings_edit_shop(shop_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _shops_call(shops.update, shop_id, body)


# ------------------------------------------------------------- the manager's board
def _mgr_call(fn, *args, **kwargs):
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        return fn(*args, **kwargs)
    except mgr.InvalidTicket as exc:
        raise HTTPException(422, str(exc))
    except mgr.NotFound:
        raise HTTPException(404, "ไม่พบข้อมูลนี้")
    except supabase.SupabaseError:
        logger.exception("manager board call failed")
        raise HTTPException(502, "ทำรายการไม่สำเร็จ ลองอีกครั้ง")


@app.get("/api/mgr/board")
def api_mgr_board(x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _mgr_call(mgr.board)


@app.post("/api/mgr/tickets")
def api_mgr_create(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _mgr_call(mgr.create_ticket, body)


@app.patch("/api/mgr/tickets/{ticket_id}")
def api_mgr_edit(ticket_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _mgr_call(mgr.edit_ticket, ticket_id, body)


@app.post("/api/mgr/tickets/{ticket_id}/action")
def api_mgr_action(ticket_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _mgr_call(mgr.act_ticket, ticket_id, str(body.get("action") or ""), body)


@app.get("/api/mgr/recurring")
def api_mgr_recurring(x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    _check_token(x_app_token, token)
    return _mgr_call(mgr.list_recurring)


@app.post("/api/mgr/recurring")
def api_mgr_create_recurring(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _mgr_call(mgr.create_recurring, body)


@app.patch("/api/mgr/recurring/{recurring_id}")
def api_mgr_edit_recurring(recurring_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _mgr_call(mgr.edit_recurring, recurring_id, body)


@app.delete("/api/mgr/recurring/{recurring_id}")
def api_mgr_delete_recurring(recurring_id: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    _mgr_call(mgr.delete_recurring, recurring_id)
    return {"ok": True}


@app.get("/api/mgr/settings")
def api_mgr_settings(x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """The settings page: types and places (including switched-off ones) and the reminder settings."""
    _check_token(x_app_token, token)

    def load():
        ready = mgr.has_v12()
        return {"ready": ready, "types": mgr.list_types(include_inactive=True) if ready else [], "places": mgr.list_places(include_inactive=True) if ready else [],
                "settings": mgr.get_settings() if ready else mgr.DEFAULT_SETTINGS}
    return _mgr_call(load)


@app.put("/api/mgr/settings")
def api_mgr_save_settings(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _mgr_call(mgr.save_settings, body)


@app.post("/api/mgr/types")
def api_mgr_new_type(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    _check_token(x_app_token, token)
    return _mgr_call(mgr.save_type, None, body)


@app.patch("/api/mgr/types/{key}")
def api_mgr_edit_type(key: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    _check_token(x_app_token, token)
    return _mgr_call(mgr.save_type, key, body)


@app.post("/api/mgr/places")
def api_mgr_new_place(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    _check_token(x_app_token, token)
    return _mgr_call(mgr.save_place, None, body)


@app.patch("/api/mgr/places/{place_id}")
def api_mgr_edit_place(place_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    _check_token(x_app_token, token)
    return _mgr_call(mgr.save_place, place_id, body)


@app.get("/api/today")
def api_today(fresh: bool = False, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """What needs attention today -- the numbers beside the menu, the bar at the top of every page, the timed reminders."""
    _check_token(x_app_token, token)
    return _mgr_call(mgr_jobs.today_summary, fresh=fresh)


@app.get("/api/home")
def api_home(fresh: bool = False, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """The home page: every card in one request (app/home.py)."""
    _check_token(x_app_token, token)
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    return home.load(fresh=fresh)


@app.get("/api/plan/options")
def api_plan_options(x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _plan_call(service_plan.options)


@app.get("/api/plan/overview")
def api_plan_overview(branch: str = "", vehicle_class: str = "", as_of: str = "", x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _plan_call(lambda: service_plan.overview(branch, vehicle_class, today=service_plan.parse_as_of(as_of)))


@app.get("/api/plan/vehicles/{vehicle_id}")
def api_plan_vehicle(vehicle_id: str, as_of: str = "", x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _plan_call(lambda: service_plan.vehicle_detail(vehicle_id, today=service_plan.parse_as_of(as_of)))


@app.get("/api/plan/reminders")
def api_plan_reminders(as_of: str = "", x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """What the car-maintenance reminders would say on a day (today, or one the look-ahead slider picked). Sends nothing."""
    _check_token(x_app_token, token)
    return _plan_call(lambda: service_plan.reminder_preview(today=service_plan.parse_as_of(as_of)))


@app.get("/api/plan/reminder-settings")
def api_plan_reminder_settings(x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _plan_call(service_plan.reminder_settings)


@app.put("/api/plan/reminder-settings")
def api_plan_save_reminder_settings(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _plan_call(service_plan.save_reminder_settings, body)


@app.post("/api/plan/snooze")
def api_plan_snooze(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """Do not remind about this item of this truck for the next N days."""
    _check_token(x_app_token, token)
    return _plan_call(service_plan.snooze, body)


@app.delete("/api/plan/snooze")
def api_plan_clear_snooze(vehicle_id: str, item_key: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _plan_call(service_plan.clear_snooze, vehicle_id, item_key)


@app.post("/api/plan/records")
def api_plan_add_records(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    _check_token(x_app_token, token)
    return _plan_call(service_plan.add_records, body)


@app.delete("/api/plan/records/{record_id}")
def api_plan_delete_record(record_id: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    _plan_call(service_plan.delete_record, record_id)
    return {"ok": True}


@app.post("/api/plan/readings")
def api_plan_add_reading(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _plan_call(service_plan.add_reading, body)


@app.delete("/api/plan/readings/{reading_id}")
def api_plan_delete_reading(reading_id: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    _plan_call(service_plan.delete_reading, reading_id)
    return {"ok": True}


@app.get("/api/plan/intervals")
def api_plan_intervals(x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _plan_call(service_plan.settings)


@app.put("/api/plan/groups")
def api_plan_set_group(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _plan_call(service_plan.set_group, body)


@app.delete("/api/plan/groups")
def api_plan_reset_group(code: str = Query(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _plan_call(service_plan.reset_group, code)


@app.put("/api/plan/vehicle-groups")
def api_plan_vehicle_groups(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """Which truck follows which group: the settings page saves several trucks at once."""
    _check_token(x_app_token, token)
    return _plan_call(service_plan.set_vehicle_groups, body)


@app.put("/api/plan/vehicles/{vehicle_id}/group")
def api_plan_vehicle_group(vehicle_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _plan_call(service_plan.set_vehicle_group, {"vehicle_id": vehicle_id, "group_code": body.get("group_code")})


@app.put("/api/plan/intervals")
def api_plan_set_interval(body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _plan_call(service_plan.set_interval, body)


@app.delete("/api/plan/intervals")
def api_plan_reset_interval(vehicle_class: str, item_key: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    return _plan_call(service_plan.reset_interval, vehicle_class, item_key)


@app.get("/api/drivers")
def api_drivers(include_inactive: bool = False, x_app_token: str = Header(default=""), token: str = Query(default="")) -> list:
    _check_token(x_app_token, token)
    return _fleet_call(fleet.list_drivers, include_inactive)


DRIVER_MOVED = "คนขับจัดการที่หน้า พนักงาน แล้ว — เพิ่ม/แก้ไขที่นั่น (ตำแหน่ง พนักงานขับรถสาย / หน้าโรง) ระบบสร้างและอัปเดตข้อมูลคนขับให้เอง"


@app.post("/api/drivers")
def api_create_driver(body: dict = Body(default={}), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    raise HTTPException(410, DRIVER_MOVED)


@app.patch("/api/drivers/{driver_id}")
def api_edit_driver(driver_id: str, body: dict = Body(default={}), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    _check_token(x_app_token, token)
    raise HTTPException(410, DRIVER_MOVED)


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


@app.delete("/api/events/{event_id}")
def api_delete_event(event_id: str, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """Deletes a record that was entered by hand (never a bill, never one that closed a board request)."""
    _check_token(x_app_token, token)
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        events.delete_event(event_id)
        logsource.invalidate()
    except events.InvalidEvent as exc:
        raise HTTPException(422, str(exc))
    except events.EventNotFound:
        raise HTTPException(404, "ไม่พบรายการนี้")
    except supabase.SupabaseError:
        logger.exception("deleting event failed")
        raise HTTPException(502, "ลบไม่สำเร็จ ลองอีกครั้ง")
    return {"ok": True}


@app.patch("/api/events/{event_id}")
def api_update_event(event_id: str, body: dict = Body(...), x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """Edits a saved event (header, truck, and a full replacement of its lines) atomically."""
    _check_token(x_app_token, token)
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        events.update_event(event_id, body)
        logsource.invalidate()
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


class SummarizeLine(BaseModel):
    description: str = Field(default="", max_length=500)
    category: str | None = Field(default=None, max_length=50)
    quantity: float | None = None
    unit: str | None = Field(default=None, max_length=50)


class SummarizeRequest(BaseModel):
    lines: list[SummarizeLine] = Field(max_length=80)
    performed_by: str | None = Field(default=None, max_length=200)


@app.post("/api/summarize")
def api_summarize(body: SummarizeRequest, x_app_token: str = Header(default=""), token: str = Query(default="")) -> dict:
    """Drafts the one-line summary from the lines currently on the edit form. Nothing is saved."""
    _check_token(x_app_token, token)
    if not settings.anthropic_api_key:
        raise HTTPException(503, "ANTHROPIC_API_KEY is not configured on the server")
    text = summarize.summarize([l.model_dump() for l in body.lines], {"performed_by": body.performed_by})
    if not text:
        raise HTTPException(502, "ให้ AI สรุปไม่สำเร็จ พิมพ์เองหรือลองอีกครั้ง")
    return {"summary": text}


# ------------------------------------------------------------ maintenance log
def _log_filters(q, date_from, date_to, branch, vehicle_id, shop, category, status, source, min_cost, max_cost, review) -> dict:
    return {"q": q, "date_from": date_from, "date_to": date_to, "branch": branch, "vehicle_id": vehicle_id, "shop": shop, "category": category,
            "status": status, "source": source, "min_cost": min_cost, "max_cost": max_cost, "review": review}


def _log_rows():
    if not supabase.configured():
        raise HTTPException(503, "Supabase is not configured on the server")
    try:
        return logsource.load_rows()
    except supabase.SupabaseError:
        logger.exception("log query failed")
        raise HTTPException(502, "โหลดสมุดงานซ่อมไม่สำเร็จ ลองอีกครั้ง")


@app.get("/api/log")
def api_log(
    q: str = "", date_from: str = "", date_to: str = "", branch: str = "", vehicle_id: str = "", shop: str = "", category: str = "",
    status: str = "all", source: str = "", min_cost: str = "", max_cost: str = "", review: str = "",
    sort: str = "date_desc", limit: int = 50, offset: int = 0,
    x_app_token: str = Header(default=""), token: str = Query(default=""),
) -> dict:
    """The maintenance log: every repair job (saved bills and manual entries, plus requests that have no record),
    filtered, sorted and paged, with totals over all matches and the choices for the filter drop-downs."""
    _check_token(x_app_token, token)
    rows = _log_rows()
    res = logbook.query(rows, _log_filters(q, date_from, date_to, branch, vehicle_id, shop, category, status, source, min_cost, max_cost, review),
                        sort=sort, limit=limit, offset=offset)
    res["facets"] = logbook.facets(rows)
    return res


@app.get("/api/log.csv")
def api_log_csv(
    q: str = "", date_from: str = "", date_to: str = "", branch: str = "", vehicle_id: str = "", shop: str = "", category: str = "",
    status: str = "all", source: str = "", min_cost: str = "", max_cost: str = "", review: str = "", sort: str = "date_desc",
    x_app_token: str = Header(default=""), token: str = Query(default=""),
):
    """The same filters as /api/log, every match, as a spreadsheet file."""
    _check_token(x_app_token, token)
    hits = logbook.matches(_log_rows(), _log_filters(q, date_from, date_to, branch, vehicle_id, shop, category, status, source, min_cost, max_cost, review), sort=sort)
    body = "\ufeff" + logbook.to_csv(hits)           # BOM so Excel reads the Thai text
    return Response(content=body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="maintenance-log-{repair_requests.today_bangkok().isoformat()}.csv"'})
