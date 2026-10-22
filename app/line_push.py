"""
Plain-text push messages to the owner's LINE (the manager's board reminders). One-way: nothing here reads replies and
no AI is involved. Needs LINE_CHANNEL_ACCESS_TOKEN and OWNER_LINE_USER_ID (the old Ticketing bot's OHM_LINE_USER_ID
works too). Not configured -> configured() is False and the reminders are simply not sent.
"""
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("vehicle_tracker.line")

PUSH_URL = "https://api.line.me/v2/bot/message/push"
MAX_CHARS = 4500                       # LINE allows 5000 per text message


class LinePushError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings.line_channel_access_token and settings.owner_line_user_id)


def push_text(text: str, *, client: Optional[httpx.Client] = None) -> None:
    """Sends `text` to the owner. Raises LinePushError if LINE refuses or cannot be reached."""
    if not configured():
        raise LinePushError("ยังไม่ได้ตั้งค่า LINE (LINE_CHANNEL_ACCESS_TOKEN และ OWNER_LINE_USER_ID)")
    body = {"to": settings.owner_line_user_id, "messages": [{"type": "text", "text": text[:MAX_CHARS]}]}
    headers = {"Authorization": f"Bearer {settings.line_channel_access_token}"}
    try:
        resp = (client or httpx).post(PUSH_URL, json=body, headers=headers, timeout=10)
    except httpx.HTTPError as exc:
        raise LinePushError(f"ติดต่อ LINE ไม่ได้: {exc}") from exc
    if resp.status_code != 200:
        logger.error("LINE push failed: %s %s", resp.status_code, resp.text[:200])
        raise LinePushError(f"LINE ปฏิเสธ (HTTP {resp.status_code})")
