# Vehicle Maintenance & Bill Tracker

Photograph or upload a repair bill (image or PDF) -> Claude reads it -> a human
reviews and corrects it on a web page -> it is saved to Supabase (Postgres +
Storage). Replaces the LINE-bot bill flow from the NongTick project, which
had no way to group several photos into one repair event.

## How it works

1. **Upload** (`static/`): pick the truck, add bill files and other photos.
2. **Extract** (`POST /api/extract`): each bill file is read by Claude in
   parallel; the results are merged into one draft event with sanity checks
   (`app/extraction.py`, `app/batch.py`). Nothing is saved yet.
3. **Review**: the page shows every bill beside its editable rows. Fields the
   model was unsure about are highlighted. Fixing a line's name re-picks its
   category (`POST /api/categorize`, `app/categorize.py`).
4. **Save** (`POST /api/events`, `app/events.py`): photos go to Storage, then
   the event, attachments and line items are written in ONE database call
   (`save_maintenance_event()` in `schema_v2.sql`) -- all or nothing.

Planned: manual entries for the internal mechanic, a repair-request board
(open -> awaiting bill -> completed, with a "no bill" option), a fleet view.
The tables for those already exist in `schema_v2.sql`.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
cp .env.example .env        # then fill it in
```

`.env` needs `ANTHROPIC_API_KEY`, `APP_TOKEN` (a long random string -- every
call spends API credit), `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`. The
service key bypasses row-level security: server-side only, never in a page.

**Database** (Supabase SQL Editor, in this order): `schema.sql`, then
`schema_v2.sql`. Replace `<PROJECT_REF>` in `schema.sql` first. Then seed the
fleet:

```bash
.venv/Scripts/python.exe scripts/seed_vehicles.py
```

**Run**

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

Open `http://localhost:8000/?token=<APP_TOKEN>`.

**Tests** (never touch the real Supabase project or the Anthropic API):

```bash
.venv/Scripts/python.exe -m pytest -q tests
```

## Layout

| Path | What |
|---|---|
| `app/extraction.py` | Bill-reading prompt + schema (ported from NongTick's `bill_extraction.py`) |
| `app/batch.py` | Merges per-file results into one draft; plausibility checks |
| `app/categorize.py` | Text-only category re-check (answers in English, mapped to Thai) |
| `app/events.py` | Validation, photo upload, the atomic save |
| `app/supabase.py` | Plain-HTTP Supabase client (tables, Storage, RPC) |
| `app/roster.py` | Fleet list: Supabase `vehicles` table (CSV only as fallback) |
| `schema.sql`, `schema_v2.sql` | Database; run in order |
| `static/` | The upload + review page |

## Things worth knowing

- The 16 categories live in three places that must match: `app/extraction.py`
  (`CATEGORIES`), `app/categorize.py` (`CATEGORY_EN`) and the `categories`
  table in `schema.sql`. Tests check all three.
- The model decides categories in English and the code maps back to Thai:
  choosing among Thai enum labels directly gave wrong picks for items it had
  identified correctly.
- Truck numbers are NOT unique across branches; lookups are by plate.
