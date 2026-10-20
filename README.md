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
`schema_v2.sql`, then `schema_v3_dashboard.sql`. Replace `<PROJECT_REF>` in
`schema.sql` first. Then seed the fleet:

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

## Dashboard

**In the app** ("ภาพรวม" tab, `GET /api/dashboard`): KPI cards (spend this
month / period, open requests, bills outstanding, average days to fix),
spend by month, by category, by truck and by shop, and a per-truck status
table that puts trucks needing attention first. Filters: period and branch.
Every figure is computed in `app/dashboard.py` (pure functions, unit-tested)
from the views below. `DEV_DASHBOARD_DEMO=1` swaps in made-up rows
(`app/dev_demo.py`) so the page can be tried with no data -- dev only.

Definitions: spend = confirmed events' `total_cost` by event date; category
spend comes from bill line items (can differ slightly from totals: VAT,
discounts); "not serviced lately" = no record for 90 days, a prompt to look
and not a service due date (the system has no intervals yet).

### Looker Studio (optional, for fancier charts)

`schema_v3_dashboard.sql` creates a read-only database login, `looker_ro`,
that can SELECT six views and nothing else (no writes, 20 s query limit, 5
connections). Looker Studio connects as that user -- never with the service
key. Set the password **in the Supabase SQL editor, not in the saved file**
(the script refuses the placeholder and anything under 20 characters; a real
password must never be committed).

Connect Looker Studio to the Supabase **session pooler** (IPv4; the direct
`db.<ref>.supabase.co` host is IPv6-only on the free plan): Supabase ->
Connect -> Session pooler gives the host and port; the username is
`looker_ro.<project_ref>`. One data source per view:

| View | One row per | Use it for |
|---|---|---|
| `v_events` | confirmed repair (bill or manual) | spend by month / truck / vendor / branch, data-quality rate |
| `v_line_items` | line on a bill | spend by category (`category_en` for English labels), parts and labour |
| `v_vehicle_status` | active truck | last service, open requests, bills outstanding |
| `v_active_requests` | open or awaiting-bill request | the live queue and its ageing |
| `v_requests` | every request ever | time-to-fix, requests per truck, closed-without-bill count |
| `v_attachments` | photo / PDF | links to open a bill's file |

## Look and feel

Light off-white by default; a moon/sun button in the top bar switches to dark
and remembers the choice on that device (`localStorage`). Colours are tokens
at the top of `static/app.css` -- change a palette there, not per component.
Fonts are self-hosted in `static/fonts/` (Inter for Latin and numbers, Noto
Sans Thai for Thai; both SIL OFL) so the page loads nothing from the internet.
