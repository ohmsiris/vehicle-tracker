"""
Thin Supabase client over plain HTTP (PostgREST for tables, Storage for
files, RPC for the atomic save function) -- httpx only, no SDK, so there's
nothing to learn or version-pin beyond Supabase's documented REST API.

Uses the SERVICE key: this server is the only thing that talks to the
database, and it bypasses row-level security by design (RLS is on with no
policies, so nothing else -- not even the public anon key -- can read or
write). The key is read from the environment and never leaves this process.
"""
import logging
from typing import Any, Optional
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger("vehicle_tracker.supabase")

STORAGE_BUCKET = "event-files"

_http: Optional[httpx.Client] = None


class SupabaseError(RuntimeError):
    """A Supabase call failed. str() is for logs, not for end users."""


def _client() -> httpx.Client:
    global _http
    if _http is None:
        if not settings.supabase_url or not settings.supabase_service_key:
            raise SupabaseError("SUPABASE_URL / SUPABASE_SERVICE_KEY are not configured")
        _http = httpx.Client(
            base_url=settings.supabase_url,
            headers={"apikey": settings.supabase_service_key, "Authorization": f"Bearer {settings.supabase_service_key}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
    return _http


def configured() -> bool:
    return bool(settings.supabase_url and settings.supabase_service_key)


def _check(resp: httpx.Response, what: str) -> httpx.Response:
    if resp.status_code >= 400:
        raise SupabaseError(f"{what} failed: HTTP {resp.status_code} {resp.text[:500]}")
    return resp


def select(table: str, params: Optional[dict] = None) -> list[dict]:
    """GET /rest/v1/<table>?<params> -- params use PostgREST filter syntax,
    e.g. {"select": "id,plate_number", "status": "eq.open", "order": "request_date.asc"}."""
    resp = _client().get(f"/rest/v1/{table}", params=params or {})
    return _check(resp, f"select {table}").json()


def select_all(table: str, params: dict, page: int = 1000) -> list[dict]:
    """Every row, paging past PostgREST's per-request cap. `params` must
    include a stable `order` or pages can overlap/skip."""
    if "order" not in params:
        raise SupabaseError("select_all needs an order for stable paging")
    out: list[dict] = []
    while True:
        chunk = select(table, {**params, "limit": str(page), "offset": str(len(out))})
        out.extend(chunk)
        if len(chunk) < page:
            return out


def insert(table: str, rows: list[dict], *, on_conflict: Optional[str] = None, ignore_duplicates: bool = False) -> list[dict]:
    prefer = ["return=representation"]
    params = {}
    if on_conflict:
        params["on_conflict"] = on_conflict
        prefer.append("resolution=ignore-duplicates" if ignore_duplicates else "resolution=merge-duplicates")
    resp = _client().post(f"/rest/v1/{table}", params=params, json=rows, headers={"Prefer": ",".join(prefer)})
    return _check(resp, f"insert {table}").json()


def update(table: str, match: dict, values: dict) -> list[dict]:
    """PATCH rows matching `match` (PostgREST filters, e.g. {"id": "eq.<uuid>"})."""
    if not match:
        raise SupabaseError("refusing to update a whole table with no filter")
    resp = _client().patch(f"/rest/v1/{table}", params=match, json=values, headers={"Prefer": "return=representation"})
    return _check(resp, f"update {table}").json()


def delete(table: str, match: dict) -> None:
    if not match:
        raise SupabaseError("refusing to delete a whole table with no filter")
    _check(_client().delete(f"/rest/v1/{table}", params=match), f"delete {table}")


def rpc(function: str, args: dict) -> Any:
    resp = _client().post(f"/rest/v1/rpc/{function}", json=args)
    return _check(resp, f"rpc {function}").json()


def storage_upload(path: str, data: bytes, content_type: str) -> None:
    resp = _client().post(
        f"/storage/v1/object/{STORAGE_BUCKET}/{quote(path)}",
        content=data,
        headers={"Content-Type": content_type, "x-upsert": "false"},
    )
    _check(resp, f"storage upload {path}")


def storage_delete(paths: list[str]) -> None:
    """Best-effort cleanup (used when a save fails after photos were uploaded)."""
    if not paths:
        return
    try:
        resp = _client().request("DELETE", f"/storage/v1/object/{STORAGE_BUCKET}", json={"prefixes": paths})
        if resp.status_code >= 400:
            logger.warning("storage cleanup of %d object(s) failed: HTTP %s", len(paths), resp.status_code)
    except httpx.HTTPError:
        logger.exception("storage cleanup failed")


def public_url(path: str) -> str:
    return f"{settings.supabase_url}/storage/v1/object/public/{STORAGE_BUCKET}/{quote(path)}"
