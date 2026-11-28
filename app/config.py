"""
Settings from environment variables (and a local .env, if present).

    from app.config import settings
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Real env vars (Railway etc.) win over .env -- load_dotenv doesn't override.
load_dotenv(PROJECT_ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    # Stripped: a trailing newline/space pasted into a hosting dashboard is
    # a classic silent failure (see the NongTick repo's MOM_LINE_USER_ID).
    return os.environ.get(key, default).strip()


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    # Shared secret for /api/extract. Every call spends real money (one
    # Opus request per bill file), so the endpoint refuses to run at all
    # when this isn't configured instead of defaulting to open.
    app_token: str
    roster_csv_path: str  # fleet list shown to the model as plate-reading hints
    # Supabase project. The service key bypasses row-level security -- it
    # lives only in this server's environment, never in the browser or git.
    supabase_url: str
    supabase_service_key: str
    max_files_per_batch: int
    max_file_bytes: int
    # Optional second lock for the พนักงาน section (salaries, loans, warnings): when set, those pages and
    # endpoints also need this PIN. Empty = the app token alone opens them.
    hr_pin: str = ""
    # บัญชี › กล่องรับไฟล์ › อ่านด้วย AI (owner, 24 Nov 2026): the drop box reads handwritten scans and unknown files with Claude, only
    # when the owner clicks. On by default; INBOX_AI_ENABLED=0 switches the button off (app/inbox_ai.py then refuses with a Thai note).
    inbox_ai_enabled: bool = True
    # Cartrack GPS (owner, 29 Nov 2026): the Fleetweb user name + the API key made at Fleetweb > Settings > API Settings. They live
    # only in the server's environment. The owner's user is an HQ one that sees every branch's vehicles: app/cartrack.py keeps only
    # the vehicles whose plate is a truck here.
    cartrack_user: str = ""
    cartrack_api_key: str = ""
    cartrack_base: str = "https://fleetapi-th.cartrack.com/rest"


def load_settings() -> Settings:
    return Settings(
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        app_token=_env("APP_TOKEN"),
        roster_csv_path=_env("ROSTER_CSV_PATH", str(PROJECT_ROOT / "data" / "vehicle_roster.csv")),
        supabase_url=_env("SUPABASE_URL").rstrip("/"),
        supabase_service_key=_env("SUPABASE_SERVICE_KEY"),
        max_files_per_batch=int(_env("MAX_FILES_PER_BATCH", "12")),
        max_file_bytes=int(_env("MAX_FILE_BYTES", str(20 * 1024 * 1024))),
        hr_pin=_env("HR_PIN"),
        inbox_ai_enabled=_env("INBOX_AI_ENABLED", "1").lower() not in ("0", "false", "off", "no"),
        cartrack_user=_env("CARTRACK_USER"),
        cartrack_api_key=_env("CARTRACK_API_KEY"),
        cartrack_base=_env("CARTRACK_BASE", "https://fleetapi-th.cartrack.com/rest").rstrip("/"),
    )


settings = load_settings()
