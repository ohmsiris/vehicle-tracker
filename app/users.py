"""
Office logins (owner, 10 Nov 2026): the bookkeeper gets their own sign-in and sees only the menus the owner ticks for them.

* The owner keeps signing in with the old access code (APP_TOKEN): that is everything, including this page of people. The owner can
  also have their own name + password: a person with role "owner" sees everything too (owner, 10 Nov 2026).
* The owner adds a person (name, sign-in name, which menus) and gets a one-time link. The person opens it on their own computer and
  chooses their own password, so nobody else ever knows it. A forgotten password = the owner makes a new link.
* Signing in gives the browser a long random token (only its sha256 is kept, in app_sessions). It is sent exactly like the access code
  (X-App-Token), so every page works unchanged; the server refuses any /api/ path outside the person's menus (main.py, role_gate).
* Five wrong passwords lock the name for 10 minutes.
"""
import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

PW_TRIES, LOCK_MINUTES, INVITE_DAYS, MIN_PW = 5, 10, 7, 8

# What a person can be given. `nav` = the menu buttons shown, `hash` = the page opened, `api` = the server paths allowed.
MENUS = {
    "sales_sheets": {"label": "ใบขายรถสาย", "nav": ["navSalesSheets"], "hash": "#sales-sheets", "api": ["/api/sales-routes"]},
    "routes": {"label": "รถสาย · กำไรต่อคัน", "nav": ["navRoutes"], "hash": "#routes", "api": ["/api/routes"]},
}
ALWAYS = ("/api/meta", "/api/auth/")          # every signed-in person


class UserError(ValueError):
    """Refused, with a message shown as it is."""


def _hash_pw(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clean_username(u: str) -> str:
    return (u or "").strip().lower()


def valid_username(u: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9._-]{3,30}", u or ""))


def valid_password(pw: str) -> Optional[str]:
    """None when fine, else why not."""
    if len(pw or "") < MIN_PW:
        return f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PW} ตัว"
    if not re.fullmatch(r"[ -~]+", pw):
        return "รหัสผ่านมีตัวอักษรภาษาไทย — สลับแป้นพิมพ์เป็นภาษาอังกฤษแล้วใส่ใหม่"
    return None


def clean_menus(menus) -> list[str]:
    return [m for m in MENUS if m in (menus or [])]


def is_owner_user(user: Optional[dict]) -> bool:
    return bool(user) and user.get("role") == "owner"


def allowed(menus: list[str], path: str) -> bool:
    """May someone with these menus call this /api/ path?"""
    if path.startswith(ALWAYS):
        return True
    return any(path.startswith(p) for m in menus if m in MENUS for p in MENUS[m]["api"])


def menu_options() -> list[dict]:
    return [{"key": k, "label": v["label"]} for k, v in MENUS.items()]


def me_for(user: Optional[dict]) -> dict:
    """What the page needs to know about who is signed in. None = the owner's access code."""
    if user is None or is_owner_user(user):
        return {"role": "owner", "name": user["name"] if user else "เจ้าของ", "username": user.get("username") if user else None,
                "menus": list(MENUS), "nav": None, "hashes": None, "home": None}
    menus = clean_menus(user.get("menus"))
    return {"role": "staff", "name": user["name"], "username": user.get("username"), "menus": menus,
            "nav": [n for m in menus for n in MENUS[m]["nav"]], "hashes": [MENUS[m]["hash"] for m in menus],
            "home": MENUS[menus[0]]["hash"] if menus else None}


# --------------------------------------------------------------------------------------------- sessions (cached a minute)
_cache: dict = {}
_lock = threading.Lock()
CACHE_SECONDS = 60


def session_user(token: str) -> Optional[dict]:
    """The person a browser token belongs to, or None. Cached for a minute: a signed-out / disabled person stops within a minute."""
    from app import supabase
    if not token or len(token) > 200 or not supabase.configured():
        return None
    h = _sha(token)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(h)
        if hit and hit[1] > now:
            return hit[0]
    user = None
    rows = supabase.select("app_sessions", {"select": "id,user_id,revoked,last_seen", "token_hash": f"eq.{h}", "limit": "1"})
    if rows and not rows[0]["revoked"]:
        u = supabase.select("app_users", {"select": "id,name,username,menus,role,active", "id": f"eq.{rows[0]['user_id']}", "limit": "1"})
        if u and u[0]["active"]:
            user = u[0]
            seen = rows[0]["last_seen"]
            t = datetime.now(timezone.utc)
            if not seen or t - datetime.fromisoformat(seen) > timedelta(minutes=10):
                supabase.update("app_sessions", {"id": f"eq.{rows[0]['id']}"}, {"last_seen": t.isoformat()})
    with _lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[h] = (user, now + CACHE_SECONDS)
    return user


def _forget_cache() -> None:
    with _lock:
        _cache.clear()


def _new_session(user_id: int, agent: str) -> str:
    from app import supabase
    from app.route_sales import phone_label
    token = "u_" + secrets.token_urlsafe(32)
    supabase.insert("app_sessions", [{"token_hash": _sha(token), "user_id": user_id, "agent": phone_label(agent)[:60],
                                      "last_seen": datetime.now(timezone.utc).isoformat()}])
    return token


# --------------------------------------------------------------------------------------------- signing in
def login(username: str, password: str, agent: str = "") -> dict:
    from app import supabase
    u = clean_username(username)
    rows = supabase.select("app_users", {"select": "*", "username": f"eq.{u}", "limit": "1"}) if valid_username(u) else []
    if not rows or not rows[0]["active"] or not rows[0]["pw_hash"]:
        raise UserError("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
    user = rows[0]
    now = datetime.now(timezone.utc)
    if user["locked_until"] and datetime.fromisoformat(user["locked_until"]) > now:
        raise UserError(f"ใส่รหัสผิดหลายครั้ง ลองใหม่ใน {LOCK_MINUTES} นาที")
    if not hmac.compare_digest(_hash_pw(password or "", user["pw_salt"]), user["pw_hash"]):
        failed = user["failed"] + 1
        lock = (now + timedelta(minutes=LOCK_MINUTES)).isoformat() if failed >= PW_TRIES else None
        supabase.update("app_users", {"id": f"eq.{user['id']}"}, {"failed": 0 if lock else failed, "locked_until": lock})
        raise UserError("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
    supabase.update("app_users", {"id": f"eq.{user['id']}"}, {"failed": 0, "locked_until": None})
    return {"token": _new_session(user["id"], agent), "me": me_for(user)}


def logout(token: str) -> None:
    from app import supabase
    if token:
        supabase.update("app_sessions", {"token_hash": f"eq.{_sha(token)}"}, {"revoked": True})
        _forget_cache()


def invite_info(code: str) -> dict:
    u = _invited(code)
    return {"name": u["name"], "username": u["username"]}


def _invited(code: str) -> dict:
    from app import supabase
    if not code or len(code) > 100:
        raise UserError("ลิงก์นี้ใช้ไม่ได้แล้ว — ขอลิงก์ใหม่จากเจ้าของ")
    rows = supabase.select("app_users", {"select": "*", "invite_hash": f"eq.{_sha(code)}", "limit": "1"})
    if not rows or not rows[0]["active"] or not rows[0]["invite_expires"] or \
            datetime.fromisoformat(rows[0]["invite_expires"]) < datetime.now(timezone.utc):
        raise UserError("ลิงก์นี้ใช้ไม่ได้แล้ว — ขอลิงก์ใหม่จากเจ้าของ")
    return rows[0]


def accept_invite(code: str, password: str, agent: str = "") -> dict:
    """The person chooses their own password from the owner's one-time link, and is signed in."""
    from app import supabase
    u = _invited(code)
    why = valid_password(password)
    if why:
        raise UserError(why)
    salt = secrets.token_hex(16)
    supabase.update("app_users", {"id": f"eq.{u['id']}"}, {"pw_salt": salt, "pw_hash": _hash_pw(password, salt), "invite_hash": None,
                                                         "invite_expires": None, "failed": 0, "locked_until": None})
    supabase.update("app_sessions", {"user_id": f"eq.{u['id']}", "revoked": "eq.false"}, {"revoked": True})   # old password's browsers
    _forget_cache()
    return {"token": _new_session(u["id"], agent), "me": me_for(u)}


# --------------------------------------------------------------------------------------------- the owner's page
def _invite(user_id: int) -> str:
    from app import supabase
    code = secrets.token_urlsafe(24)
    supabase.update("app_users", {"id": f"eq.{user_id}"}, {"invite_hash": _sha(code),
                                                         "invite_expires": (datetime.now(timezone.utc) + timedelta(days=INVITE_DAYS)).isoformat()})
    return code


def list_users() -> dict:
    from app import supabase
    users = supabase.select_all("app_users", {"select": "id,name,username,menus,role,active,pw_hash,invite_expires,created_at", "order": "id.asc"})
    sess: dict = {}
    for s in supabase.select_all("app_sessions", {"select": "user_id,agent,created_at,last_seen", "revoked": "eq.false",
                                                  "order": "last_seen.desc.nullslast"}):
        sess.setdefault(s["user_id"], []).append({"agent": s["agent"], "since": s["created_at"], "last_seen": s["last_seen"]})
    out = []
    for u in users:
        out.append({"id": u["id"], "name": u["name"], "username": u["username"], "menus": clean_menus(u["menus"]), "active": u["active"],
                    "owner": u.get("role") == "owner",
                    "password_set": bool(u["pw_hash"]), "invite_open": bool(u["invite_expires"]) and
                    datetime.fromisoformat(u["invite_expires"]) > datetime.now(timezone.utc), "sessions": sess.get(u["id"], [])})
    return {"users": out, "menus": menu_options(), "invite_days": INVITE_DAYS}


def create_user(name: str, username: str, menus, owner: bool = False) -> dict:
    from app import supabase
    name = re.sub(r"\s+", " ", name or "").strip()[:60]
    u = clean_username(username)
    if not name:
        raise UserError("ใส่ชื่อ")
    if not valid_username(u):
        raise UserError("ชื่อเข้าระบบ: ภาษาอังกฤษตัวเล็ก / ตัวเลข 3-30 ตัว เช่น account1")
    if supabase.select("app_users", {"select": "id", "username": f"eq.{u}", "limit": "1"}):
        raise UserError("ชื่อเข้าระบบนี้มีคนใช้แล้ว")
    row = supabase.insert("app_users", [{"name": name, "username": u, "menus": clean_menus(menus),
                                         "role": "owner" if owner else "staff"}])[0]
    return {"id": row["id"], "invite": _invite(row["id"])}


def update_user(user_id: int, body: dict) -> dict:
    from app import supabase
    values = {}
    if "menus" in body:
        values["menus"] = clean_menus(body["menus"])
    if "active" in body:
        values["active"] = bool(body["active"])
    if "owner" in body:
        values["role"] = "owner" if body["owner"] else "staff"
    if "name" in body:
        n = re.sub(r"\s+", " ", str(body["name"] or "")).strip()[:60]
        if not n:
            raise UserError("ใส่ชื่อ")
        values["name"] = n
    if not values:
        raise UserError("ไม่มีอะไรเปลี่ยน")
    rows = supabase.update("app_users", {"id": f"eq.{int(user_id)}"}, values)
    if not rows:
        raise UserError("ไม่พบผู้ใช้นี้")
    if values.get("active") is False:
        supabase.update("app_sessions", {"user_id": f"eq.{int(user_id)}", "revoked": "eq.false"}, {"revoked": True})
    _forget_cache()
    return {"ok": True}


def new_invite(user_id: int) -> dict:
    """A new one-time link (forgotten password, or the first link ran out). The old password stops working only when the new one is set."""
    from app import supabase
    if not supabase.select("app_users", {"select": "id", "id": f"eq.{int(user_id)}", "limit": "1"}):
        raise UserError("ไม่พบผู้ใช้นี้")
    return {"invite": _invite(int(user_id))}


def sign_out_all(user_id: int) -> dict:
    from app import supabase
    n = len(supabase.update("app_sessions", {"user_id": f"eq.{int(user_id)}", "revoked": "eq.false"}, {"revoked": True}))
    _forget_cache()
    return {"signed_out": n}
