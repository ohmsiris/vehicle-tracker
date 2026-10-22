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
`schema_v2.sql`, then `schema_v3_dashboard.sql` (only needed for Looker), then
`schema_v4.sql` (editing saved bills + the written summary), then
`schema_v5.sql` (the "at the shop" step of the repair flow). Replace
`<PROJECT_REF>` in `schema.sql` first. Then seed the fleet:

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

## Dates and years (พ.ศ. / ค.ศ.)

Bills are written in the Buddhist year (19/8/2569); databases and date
inputs use the standard year (2026-08-19). The rule here: **everything is
stored as ค.ศ. (ISO), and everything shown to a person uses the full พ.ศ.
year** -- "20 ต.ค. 2569", never a 2-digit year. Formatting is done by hand
(`beDate` in `static/board.js`), not with the browser's locale, so it reads
the same on every device. Date inputs show a line under them spelling out both
calendars, a พ.ศ. year typed into one is converted automatically, and the
server rejects a year >= 2400 with an explicit message. The bill reader
already corrects a พ.ศ. year the model read off a bill (`app/batch.py`).

## Navigation: sidebar, cars, machinery

A left sidebar groups the app. Everything built so far lives under **รถ**
(repair board, record a bill, vehicle search / history, dashboard);
**เครื่องจักร** is a placeholder page for the future machinery section. The
sidebar collapses with the menu button (remembered per device) and is a
slide-in drawer on a phone.

## Vehicle search and history ("ค้นหารถ / ประวัติ")

Search by plate, truck number, make/model or branch; open a truck to see its
repair events newest first, each with a one-line "what was done"; open an
event to read it as a bill (read-only), with its photos/PDFs and the board
requests it closed. Pages are addressed by URL hash (`#search`,
`#vehicle=<id>`, `#event=<id>`), so Back and bookmarks work.

**Editing a saved bill** ("แก้ไข" on the bill page): header fields, the
truck, a short summary, and the line items (same row as the review page, with
the same AI category re-check). Saving replaces the lines in one transaction
(`update_maintenance_event()` in `schema_v4.sql`); photos and the requests the
bill closed are untouched; lines you did not touch keep their "AI was unsure"
flags. Until `schema_v4.sql` has been run, the page says so instead of failing.

**Summary:** `maintenance_events.summary` is an optional written one-liner,
shown in the history list in place of the automatic preview (the biggest line
items by cost, labour last). It is only requested from the database once the
column exists (`app/history.py` probes for it), so the history pages work
before and after `schema_v4.sql`.

### AI summary of each repair

`app/summarize.py` writes the one-line "what was done" from a bill's lines
(text only; prices are never sent). Three ways it gets filled:
1. **On save** -- after a bill is saved the app writes a summary in the
   background (the save never waits for it, and never fails because of it).
2. **"✨ ให้ AI เขียนให้"** in the edit form drafts one from the lines as they
   are on the form right now; nothing is saved until you press save.
3. **Backfill** for bills saved before this existed:
   `scripts/backfill_summaries.py` (dry run) / `--write`.

It only ever writes into an EMPTY summary (a conditional update), so a summary
a person typed is never overwritten. All of it is a no-op until
`schema_v4.sql` has been run.

## Maintenance log ("สมุดงานซ่อม")

One list of every repair job ever done: saved bills, the mechanic's own entries,
and requests that never got a record (still waiting, at a shop, cancelled,
closed without a bill). A request that was closed by a saved bill is not listed
twice -- the bill is the record and shows "จากงานแจ้งซ่อม" underneath.

- **Filters** (all combine): free-text search (plate, truck number, shop,
  problem, line items; spaces/dashes ignored), date range with quick chips,
  status, shop, branch, category, source, cost range, "ควรตรวจ only".
  Filtering happens on the server (`app/logbook.py`, pure functions with tests);
  `app/logsource.py` caches the loaded rows for 15 s and every write clears it.
- **Shops** are folded to one name (`app/shops.py`): a bill that says
  "ชมรมย์การช่าง" and one that says "อู่ชมรม" are the same shop when filtering.
  The shop list is also where the shop buttons in the status flow come from.
- **Excel**: "ดาวน์โหลด Excel (CSV)" exports every match of the current
  filters (not just the loaded page), with a BOM so Thai opens correctly.
- Opening a bill from the log and pressing back returns to the log with the
  filters as you left them. URL: `#log`. API: `GET /api/log`, `GET /api/log.csv`.

## Hosting (Railway)

The app is one Python process with no local state (all data is in Supabase), so
any host that runs `uvicorn` works. `railway.json` has the start command and the
health check (`/health`); `.python-version` pins Python 3.12.

1. railway.com -> **New Project -> Deploy from GitHub repo** -> `ohmsiris/vehicle-tracker`,
   branch `main`. Make it a SEPARATE project from NongTick.
2. **Variables** (exactly these four; Railway sets `PORT` itself):
   `ANTHROPIC_API_KEY`, `APP_TOKEN` (a long random string), `SUPABASE_URL`,
   `SUPABASE_SERVICE_KEY`. Never set any `DEV_*` variable there (it would swap
   the dashboard for made-up data; the app logs an error at start-up if it sees one).
3. **Settings -> Networking -> Generate Domain.** Open `https://<domain>/?token=<APP_TOKEN>`
   once; the token is stored for that browser tab and removed from the address bar.
4. Check: `/health` returns `{"ok": true}`; a wrong or missing token is refused;
   then record one real multi-page bill end to end (extraction takes 30-70 s, so
   this also proves the host doesn't cut long requests).

The app logs `MISSING SETTINGS: ...` at start-up if a variable is absent.
Pages are served over HTTPS with `Referrer-Policy: no-referrer`, `nosniff` and
`X-Frame-Options: DENY`; there are no public API docs.

## Backups

Supabase's own backups don't exist on the Free plan, and on Pro they cover the
database but NOT the photos (Storage). So take your own:

```bash
.venv/Scripts/python.exe scripts/backup.py
```

Writes `backups/files/` (every photo/PDF, downloaded once, never again) and
`backups/<date_time>/data/*.json` (every table, a full copy each run) plus a
`MANIFEST.json`. Each run is verified (rows read back, every file's size
compared with the database). Read-only against Supabase; `backups/` is
gitignored because it is business data -- copy it somewhere other than this
computer (a USB drive, Google Drive). Restoring = re-inserting these JSON rows
and re-uploading `files/` into a Supabase project that has schemas 1-4 applied;
the `was_low_confidence` column is not saved because the database recomputes it.

## Recording: bill or mechanic's own work ("บันทึกซ่อม")

One page, two modes (switch at the top):
- **มีบิล / ใบเสร็จ** -- upload the shop's bill; the AI reads it; you review and confirm.
- **ช่างทำเอง (กรอกเอง)** -- for the internal mechanic's work, no bill and no AI reading:
  who did it, the date, optional mileage, what was done (shortcut buttons for the
  common jobs, or type it; price optional), optional photos, an optional summary
  (left empty, the AI writes one after saving) and notes. Saved through the same
  `POST /api/events` with `source = "manual"`, so it appears in the same history,
  dashboard and board. It can close the truck's waiting repair requests, and the
  board's "ซ่อมแล้ว" dialog has a **ช่างทำเอง → กรอกบันทึก** button that opens this
  form with the request already ticked.

## Repair flow: reported -> at a shop -> back -> bill

```
open --send_to_shop--> at_shop --returned--> awaiting_bill --(bill saved)--> completed
 |                        \--returned, no bill--------------------------> completed (no bill)
 |--"fixed" (own mechanic / shop never recorded)--> awaiting_bill
 \--cancel (also from at_shop / awaiting_bill)
```

- The board has three columns -- รอซ่อม, อยู่ที่อู่, กลับมาแล้ว รอบิล -- and each
  card shows a rail (แจ้ง -> ส่งอู่ -> กลับมา -> บิล), how many days it has been in
  its column (amber at 7 days at a shop, red at 14) and "เลยกำหนด N วัน" once an
  expected-return date has passed. Drag a card to the next column on a computer,
  or use the buttons (always available, and the only way on a phone); both open
  the same dialog. Moves go one step at a time; "back" undoes one step
  (waiting-for-bill -> at the shop, at the shop -> waiting).
- **Sending to a shop** asks for the shop (buttons for the usual ones, or type any;
  spellings are folded to one name by `app/shops.py`), the date sent (default
  today) and optionally when it should be back. **Back** asks for the return date and
  shows the days it was away. A saved bill closes a request in any of the three
  columns, so a bill that arrives early is fine.
- An emergency that went straight to a shop and never reached the paper sheet:
  tick "ไม่ได้ลงใบแจ้งซ่อมกระดาษ" and pick the shop when adding it. It is marked
  "ไม่มีในใบแจ้ง" and is found in the log (`#log`) afterwards.
- `schema_v5.sql` adds `shop`, `sent_date`, `expected_return`, `on_paper` to
  `repair_requests`, the `at_shop` status, `days_at_shop` / `at_shop_requests` on
  the views, and teaches `save_maintenance_event()` about it. Until it has been run
  the app works exactly as before and the new buttons say so instead of failing
  (`repair_requests.has_v5()` probes for the columns).

### "รถอยู่ที่ไหน" (`#where`)

The trucks that are out at a shop right now, grouped by shop (most trucks first):
days away, expected return, overdue flag, and the same "back" / "change shop"
buttons as the board. It reads the board's own list, so the two never disagree.
