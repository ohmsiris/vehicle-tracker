"""Tests never talk to the real Supabase project or Anthropic, whatever .env says."""
import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _hermetic_settings():
    saved = {k: getattr(settings, k) for k in ("supabase_url", "supabase_service_key", "app_token", "anthropic_api_key", "line_channel_access_token", "owner_line_user_id")}
    for k in saved:
        object.__setattr__(settings, k, "")
    yield
    for k, v in saved.items():
        object.__setattr__(settings, k, v)
