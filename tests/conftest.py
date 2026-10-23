"""Tests never talk to the real Supabase project or Anthropic, whatever .env says."""
import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _hermetic_settings():
    saved = {k: getattr(settings, k) for k in ("supabase_url", "supabase_service_key", "app_token", "anthropic_api_key")}
    for k in saved:
        object.__setattr__(settings, k, "")
    yield
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


@pytest.fixture(autouse=True)
def _followup_not_ready_by_default(monkeypatch):
    """The follow-up tables (schema_v17) are "not there" unless a test says so: no test reaches the real database."""
    from app import repair_followup
    monkeypatch.setitem(repair_followup._probe, "at", 1e18)
    monkeypatch.setitem(repair_followup._probe, "ok", False)
