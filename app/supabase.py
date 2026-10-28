"""
Thin Supabase client over plain HTTP (PostgREST for tables, Storage for
files, RPC for the atomic save function) -- httpx only, no SDK, so there's
nothing to learn or version-pin beyond Supabase's documented REST API.

Uses the SERVICE key: this server is the only thing that talks to the
database, and it bypasses row-level security by design (RLS is on with no
policies, so nothing else -- not even the public anon key -- can read or
write). The key is read from the environment and never leaves this process.
"""
import copy
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional
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


# ---- a very short read cache for the small reference tables. One page load asks for the same vehicles / drivers / shops / plan groups from several places, and
# every ask is a round trip to the database; for CACHE_SECONDS the answer is reused. Any write through this module drops that table's answers at once, so what the
# app itself changes is never stale; a change made from outside (a script) shows within CACHE_SECONDS. Callers get a COPY (they pop and edit what they read).
CACHE_SECONDS = 5.0
CACHED_TABLES = {"vehicles", "drivers", "shops", "shop_aliases", "shop_contacts", "service_groups", "service_intervals", "vehicle_plans", "machines", "categories",
                 "mgr_types", "mgr_settings", "stock_items", "stock_item_aliases", "employees", "employee_rates", "hr_settings"}
_cache: dict = {}
_cache_lock = threading.Lock()


def _forget(table: Optional[str] = None) -> None:
    with _cache_lock:
        for key in [k for k in _cache if table is None or k[0] == table]:
            del _cache[key]


def select(table: str, params: Optional[dict] = None) -> list[dict]:
    """GET /rest/v1/<table>?<params> -- params use PostgREST filter syntax,
    e.g. {"select": "id,plate_number", "status": "eq.open", "order": "request_date.asc"}."""
    key = (table, json.dumps(params or {}, sort_keys=True, default=str)) if table in CACHED_TABLES else None
    if key:
        with _cache_lock:
            hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return copy.deepcopy(hit[1])
    resp = _client().get(f"/rest/v1/{table}", params=params or {})
    rows = _check(resp, f"select {table}").json()
    if key:
        with _cache_lock:
            _cache[key] = (time.time(), copy.deepcopy(rows))
    return rows


# ---- several independent reads at once: the endpoint waits for the slowest instead of the sum of all
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sb")


def parallel(*calls: Callable[[], Any]) -> list:
    """Run the callables (no arguments each) at the same time and return their results in the same order; the first error is raised."""
    futures = [_pool.submit(c) for c in calls]
    return [f.result() for f in futures]


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


def select_all_parallel(table: str, params: dict, page: int = 1000, first: int = 5) -> list[dict]:
    """select_all for a big table: the first `first` pages are asked for at the same time (nearly always enough), the rest one after another."""
    if "order" not in params:
        raise SupabaseError("select_all needs an order for stable paging")
    chunks = parallel(*[(lambda n=n: select(table, {**params, "limit": str(page), "offset": str(n * page)})) for n in range(first)])
    out: list[dict] = []
    for chunk in chunks:
        out.extend(chunk)
        if len(chunk) < page:
            return out
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
    _forget(table)
    resp = _client().post(f"/rest/v1/{table}", params=params, json=rows, headers={"Prefer": ",".join(prefer)})
    try:
        return _check(resp, f"insert {table}").json()
    finally:
        _forget(table)


def update(table: str, match: dict, values: dict) -> list[dict]:
    """PATCH rows matching `match` (PostgREST filters, e.g. {"id": "eq.<uuid>"})."""
    if not match:
        raise SupabaseError("refusing to update a whole table with no filter")
    _forget(table)
    resp = _client().patch(f"/rest/v1/{table}", params=match, json=values, headers={"Prefer": "return=representation"})
    try:
        return _check(resp, f"update {table}").json()
    finally:
        _forget(table)


def delete(table: str, match: dict) -> None:
    if not match:
        raise SupabaseError("refusing to delete a whole table with no filter")
    _forget(table)
    try:
        _check(_client().delete(f"/rest/v1/{table}", params=match), f"delete {table}")
    finally:
        _forget(table)                                                       # a delete can cascade into other tables
        _forget()


def rpc(function: str, args: dict) -> Any:
    _forget()                                                                # a function can write any table
    resp = _client().post(f"/rest/v1/rpc/{function}", json=args)
    try:
        return _check(resp, f"rpc {function}").json()
    finally:
        _forget()


def storage_upload(path: str, data: bytes, content_type: str) -> None:
    resp = _client().post(
        f"/storage/v1/object/{STORAGE_BUCKET}/{quote(path)}",
        content=data,
        headers={"Content-Type": content_type, "x-upsert": "false"},
    )
    _check(resp, f"storage upload {path}")


def storage_download(path: str) -> bytes:
    """One stored file, via the authenticated endpoint (works whether the bucket is public or not)."""
    resp = _client().get(f"/storage/v1/object/{STORAGE_BUCKET}/{quote(path)}")
    return _check(resp, f"storage download {path}").content


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
