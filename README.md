# IceBox -- vehicle maintenance, bills and factory purchasing

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
`schema_v5.sql` (the "at the shop" step of the repair flow), then
`schema_v6.sql` (drivers, truck usage, main driver), then
`schema_v7.sql` (the rest of the master vehicle list: size class, registration papers, driver nickname), then
`schema_v8.sql` (the truck's own nickname), then
`schema_v9.sql` (machinery: machines, parts index, maintenance reports), then
`schema_v10.sql` and `schema_v11.sql` (the vehicle maintenance programme), then
`schema_v12.sql` (the manager's ticket board). Replace
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

## Home page ("หน้าแรก") and the app icon

The app opens on **หน้าแรก** (the top item of the sidebar), not on the repair board: one screen of cards, all read in ONE request
(`/api/home`, `app/home.py`; the server reads the tables side by side and keeps the answer 20 s, so a slow link pays for one round trip):
**วันนี้ต้องดู** (the manager's board digest + the maintenance-plan reminders, incl. "ทำพร้อมน้ำมันเครื่อง"), **รถ — งานซ่อม** (รอซ่อม / อยู่ที่อู่ /
รอบิล + the trucks at a shop), **ค่าใช้จ่าย** (repair spend and factory purchases, this month vs last), **สต็อก — ของใกล้หมด** (below the minimum set on
the stock page), **เครื่องจักร** (active machines, recent maintenance). Every card has a link to its full page. A card that cannot be built
(its schema file not run, or a database hiccup) says so by itself and the rest still show.

It is a first draft built to grow. To add a card: a loader + pure `build_*` function in `app/home.py` (add it to `CARDS`), and a `draw*` function +
an entry in `HOME_CARDS` in `static/home.js`. The ideas not built yet (`HOME_SOON` in `app/home.py`: ice production/sales, slips/finance, GMP
inspection, vehicle documents, driver licences, purchase orders) show as dashed "เร็วๆ นี้" cards saying what each needs before it can show numbers.
The other pages are unchanged (the old **ภาพรวม** is still under รถ).

**App icon**: the artwork is `static/icons/icebox-source.jpg`; `.venv/Scripts/python.exe scripts/make_icons.py` makes the tab icon (`favicon.ico`, also served at
`/favicon.ico`), the iPhone one (`apple-touch-icon.png`, a full square -- iOS rounds it) and the Android / "add to home screen" ones
(`icon-192/512.png`, `icon-maskable-512.png` with extra margin so a round mask never cuts the picture), wired in by `static/manifest.webmanifest`.

## Navigation: sidebar, cars, machinery

A left sidebar groups the app. Everything built so far lives under **รถ**
(repair board, where the trucks are, record a bill, the maintenance log, the
fleet roster, dashboard);
**เครื่องจักร** is a placeholder page for the future machinery section. The
sidebar collapses with the menu button (remembered per device) and is a
slide-in drawer on a phone.

The **รถ** group is ordered by what you do, with a line between each part: **ภาพรวม**, **สถานะงานซ่อม** (overview and status:
the repair board) · **บันทึกงานซ่อม / คิวซ่อม** (input) · **รถอยู่ที่ไหน**, **สมุดงานซ่อม** (output) · **ทะเบียนรถ**, **แผนซ่อมบำรุง**,
**ตั้งค่า** (editing).

**Input is one page** (`บันทึกงานซ่อม / คิวซ่อม`) with three tabs: **แจ้งซ่อม / เข้าคิว** (a repair request: truck, problem,
reporter, date, optionally already sent to a shop -- it opens on this tab), **มีบิล / ใบเสร็จ** and **ช่างทำเอง**. A bill started
from a board card or a truck always lands on the bill tab.

**รถใกล้ถึงคิวบำรุง** (top of สถานะงานซ่อม): the trucks the maintenance plan says are close to / past a round (the same list as
the reminders, `/api/plan/reminders`), one small card per truck with what is due. Drag a card into **รอซ่อม** (or press
"เข้าคิว ↓") and it becomes a repair request "ถึงรอบบำรุง: น้ำมันเครื่อง, …"; a truck already queued that way is not shown again.
"+ แจ้งซ่อม" goes to the request tab.

**Who to call**: on the board and "รถอยู่ที่ไหน" the server works out who to ring about each request (`fleet.call_target`): the truck's main driver -- or, for a factory truck (รถหน้าโรง, which has no driver), whoever reported the problem (ผู้แจ้ง), found among the drivers by nickname / name / first name when exactly one matches and has a number. A driver's number is a tap-to-call button (`tel:`) on the truck's page, the drivers list, the board cards
and "รถอยู่ที่ไหน". The shops' numbers are kept in the database and edited on the settings page (see "Settings" below), shown as call buttons under each shop on "รถอยู่ที่ไหน" and in the "ส่งซ่อมที่…"
dialog. A shop with no number yet (ฮีโน่ เอเซีย หนองแค) shows no call button.

## One repair event, read like a bill

The log (`#log`) is where history is found: choose one truck in its picker to see
that truck's whole history, with a header card (usage, main driver, last repair,
lifetime totals). Open a job to read it as a bill (read-only), with its
photos/PDFs and the board requests it closed. Pages are addressed by URL hash
(`#roster`, `#log`, `#where`, `#event=<id>`), so Back and bookmarks work; the
old `#search` and `#vehicle=<id>` links still land on the roster / that truck's log.

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

## After a car comes back from a shop, and while it is away

`schema_v17.sql`, `app/repair_followup.py`, `static/board.js`. No new column on the repair board: it all lives on the cards.

- **Ask the driver** (card in "กลับมาแล้ว — รอบิล"): when a car comes back from a shop (รถกลับมาแล้ว, with or without a bill) it
  gets a question for the day after: "รถปกติไหม?" with the driver's call button (icon + name; "ยังไม่มีเบอร์คนขับ" with a
  link to the truck page when there is none), **รถปกติ ✓**, **ยังมีปัญหา…** (asks what, and opens a new job in "รอซ่อม") and
  **ข้าม**. It turns amber on the day and red when late. A car stays in that column until the question is answered, even if
  its bill came first. Answers are kept (`check_status` pending / ok / problem / skipped).
- **Manager board sync** (`mgr_tickets.request_id` + `request_kind`): returning a car makes two tickets for the day after --
  "โทรถามคนขับ …" (type แก้ปัญหา / ซ่อม, with the driver's number so the card has a call button) and "ตามบิล … จาก <shop>"
  (type ตามงาน, with the shop's number). They close each other's twin: answering the question closes its ticket; finishing the
  ticket answers the question (ok; cancelled = skipped); a saved bill, "ไม่มีบิล" or cancelling closes the bill ticket; taking
  the return back cancels both. Made best-effort, so the repair board works without the manager tables.
- **อัปเดตความคืบหน้า** (card in "อยู่ที่อู่"): a progress note ("รออะไหล่ 2 วัน", quick chips), the latest one shows on the card
  with its age, all of them are listed in the dialog (and can be deleted). Optionally "เตือนให้ถามอีกครั้ง" (tomorrow / 2 / 3 /
  7 days) makes one "ถามความคืบหน้า …" ticket on the manager board; a newer reminder replaces it; returning the car closes it.
- Call buttons everywhere show the phone icon and the name only (no word "โทร").

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
3. **Settings -> Networking -> Generate Domain** (region: Singapore, next to the Supabase project). Open
   `https://<domain>/?token=<APP_TOKEN>` once; the key is kept on that device (localStorage, so a phone does not ask
   again) and removed from the address bar. To forget it on a device, clear that site's data.
4. Check: `/health` returns `{"ok": true}`; a wrong or missing token is refused;
   then record one real multi-page bill end to end (extraction takes 30-70 s, so
   this also proves the host doesn't cut long requests).

Run ONE instance only (the recurring-ticket timer lives inside the process; a rolling deploy overlaps two for a
moment, which is safe: the tickets have a unique index). A local copy that points at the same database must set
`DISABLE_SCHEDULER=1`. Generate the key on your own machine, not in chat:
`python -c "import secrets; print(secrets.token_urlsafe(32))"`.

The app logs `MISSING SETTINGS: ...` at start-up if a variable is absent.
Pages are served over HTTPS with `Referrer-Policy: no-referrer`, `nosniff` and
`X-Frame-Options: DENY`; there are no public API docs.

## Backups

Supabase's own backups don't exist on the Free plan, and on Pro they cover the
database but NOT the photos (Storage). So take your own:

```bash
.venv/Scripts/python.exe scripts/backup.py
```

Writes `backups/files/` (every photo/PDF -- repair bills and purchase bills -- downloaded once, never again) and
`backups/<date_time>/data/*.json` (EVERY table, 32 of them, a full copy each run) plus a `MANIFEST.json`. Each run is verified
(rows read back, every file's size compared with the database). Read-only against Supabase; `backups/` is gitignored because it is
business data -- copy it somewhere other than this computer (a USB drive, Google Drive). Restoring = re-inserting these JSON rows
(parents before children, in the order of `TABLES` in `app/backup.py`) and re-uploading `files/` into a Supabase project that has
all the schema files applied; the `was_low_confidence` column is not saved because the database recomputes it.
A table added by a later schema file MUST be added to `TABLES` (a test compares it with the schema files, so forgetting fails).
**Automatic weekly run**: the Windows task "IceBox backup" (Task Scheduler) runs `scripts/run_backup.cmd` every Monday 11:00 (a
missed run starts when the computer is next on; it needs the drive this folder is on to be plugged in) and writes the result to
`backups/last_run.log`. Created with `Register-ScheduledTask`; delete it with `Unregister-ScheduledTask -TaskName "IceBox backup"`.
Off-computer copy: after a successful backup `scripts/run_backup.cmd` copies to the Google Drive folder "IceBox Backup" in
My Drive, found through Google Drive for Desktop (it shows My Drive as a drive letter, E: on this computer, so the script looks for
`<letter>:\My Drive\IceBox Backup`): the photos into `files\` (only new ones) and this run's database export into `runs\<date>\`.
If Drive is not running the log says "NOT COPIED TO DRIVE". (The Drive connector inside the chat is not used for this: it can only
pass file contents through the conversation, which is not reliable for 40 MB of photos.)
Where the photos live: Supabase Storage, bucket `event-files` (public bucket, so a photo's link opens without a login; the
link contains a random id and only the app shows it), in the same Singapore project as the database.

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

**VAT on a repair bill**: under each bill's lines a tick "บิลนี้ยังไม่รวม VAT — เพิ่มบรรทัด VAT 7% ให้" adds a "VAT 7%" line
(category อื่นๆ) worth 7 % of that bill's other lines and keeps it in step while the lines change; type your own amount in
that line and it stays as typed; unticking or deleting the line removes it. A VAT line the reader already found counts as
ticked, so VAT cannot be added twice. The bill total follows the lines as usual (`static/app.js`, `vatToggle`).

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
- A truck that is already at a shop when you add it: pick the shop in the add form
  and it starts in "อยู่ที่อู่". (The `on_paper` column and "ไม่มีในใบแจ้ง" badge
  still exist in the database but the form no longer offers them -- entries made
  here are the record, with or without a paper sheet.)
- `schema_v5.sql` adds `shop`, `sent_date`, `expected_return`, `on_paper` to
  `repair_requests`, the `at_shop` status, `days_at_shop` / `at_shop_requests` on
  the views, and teaches `save_maintenance_event()` about it. Until it has been run
  the app works exactly as before and the new buttons say so instead of failing
  (`repair_requests.has_v5()` probes for the columns).

### "รถอยู่ที่ไหน" (`#where`)

The trucks that are out at a shop right now, grouped by shop (most trucks first):
days away, expected return, overdue flag, and the same "back" / "change shop"
buttons as the board. It reads the board's own list, so the two never disagree.

## Fleet roster ("ทะเบียนรถ", `#roster`)

Trucks and drivers under one roof (`app/fleet.py`, `static/roster.js`).

- **Trucks:** one row each with its **usage** (รถสาย, รถหน้าโรง, or any label you
  type once -- it is offered again next time) and **main driver**, both changeable
  straight from the row. "แก้ไข" edits plate / province / fleet number / branch /
  make / model / notes, and retires a truck ("ใช้งานอยู่" unticked) or brings it
  back. Retiring never deletes: the truck leaves every picker but its history
  stays in the log. A truck with work still open (waiting, at a shop, waiting for
  a bill) cannot be retired until that is closed.
- **Checks:** a plate (ignoring spaces) can exist once per province; a fleet number
  once per branch among active trucks (number 6 may exist in both branches, and a
  retired truck frees its number).
- **Drivers:** name, phone, branch, notes. A driver is "main driver" of any number
  of trucks; a driver who stops driving ("เลิกขับ") is removed from those trucks.
  Picking a truck on the repair form suggests its main driver as the reporter.
- `schema_v6.sql` adds the `drivers` table and `vehicles.usage`,
  `vehicles.main_driver_id`, `vehicles.notes`. Until it has been run the roster is
  read-only and says so (`fleet.has_v6()` probes for it); everything else works as before.

### The master vehicle list

The site is the master for trucks and drivers; `Drivers.xlsx` (sheet 1 the roster, sheet 2
the registration papers) was the source and is imported once.

- **The list is read-only:** each truck is one card and the whole card is a link to its page, so nothing
  can be changed by accident from the list.
- **The truck page** (`#truck=<id>`) shows everything laid out in the open: plate, number, branch, size
  class (รถเล็ก / รถใหญ่), usage, make / model, colour, the main driver with nickname and a tap-to-call
  phone number, the registration papers (date, type, engine and chassis numbers, owner, holder), repair
  totals, notes. "แก้ไข" turns the same page into a form (unsaved changes are kept if the page redraws,
  and leaving with unsaved changes asks first); "บันทึก" saves, "ยกเลิก" discards. Retiring a truck and
  choosing / creating its main driver happen there too. "+ เพิ่มรถ" opens `#truck=new`.
- **"ดาวน์โหลด Excel (CSV)"** on the roster exports the trucks currently shown with every column, so
  the master list can still go into a spreadsheet.
- **Import:** `scripts/import_roster.py` (dry run) / `--write`. It creates drivers, links each to their
  truck, sets usage (a driver written as "หน้าโรง" means รถหน้าโรง, anyone else means รถสาย), size class and
  the registration details. Matching is by plate, then by branch + fleet number. It never changes a
  truck's branch / number / plate, only fills usage, driver and registration details when they are empty
  (so it is safe to run again and never undoes edits made on the site), and lists every difference it
  leaves alone. Needs `schema_v6.sql` + `schema_v7.sql` and `pip install openpyxl`.
- `Drivers.xlsx` holds drivers' phone numbers: it is git-ignored and must not be committed.

**Truck nickname** (`vehicles.nickname`, `schema_v8.sql`): what the truck itself is called -- หัวขาว,
หัวน้ำเงิน, สต๊อคน้ำแข็ง (a driver's nickname is separate, on the driver). In the blue chip where the
fleet number goes, a truck with no number of its own (its "number" is just its plate) shows the
nickname; a truck with both shows the nickname next to the plate. Searchable, in the CSV, editable on
the truck page. The import fills it from the nickname column of "หน้าโรง" rows; usage stays รถหน้าโรง.

## Machinery ("เครื่องจักร")

`schema_v9.sql`, `app/machinery.py`, `static/machinery.js`. Three pages in the sidebar group **เครื่องจักร**:

- **ดัชนีเครื่องจักร** (`#machinery`): the machines as cards -- the five numbered ice machines
  (หลอดใหญ่ / เหลี่ยม / เล็ก, MYCOM 8WB-HE 50 ตัน, Sabroe SMC108L 30 ตัน) and the two brine-pond machines, which
  usually have no number and are named "เครื่องบ่อซอง Sabroe 30 ตัน" -- and the **parts / specs index** under them:
  every motor, pump, belt, bearing, breaker, contactor and overload with its spec, quantity and (for motors)
  hp / amps / current vs desired contactor and overload / status. Search finds any word across all of it
  ("calpeda", "B-82", "breaker 40A"); a row opens a form to correct or delete it, "+ เพิ่มรายการ" adds one.
  Rows can be tied to a machine or left as a plant area (โรงซอง 1, ระบบน้ำ, ลานโหลด ...).
- **A machine's page** (`#machine=<id>`): its details (view / edit like the truck page), its own parts, its
  latest reports. "+ เพิ่มเครื่อง" opens `#machine=new`.
- **บันทึกซ่อมบำรุง** (`#machine-log`): maintenance reports -- date, machine or area, equipment, kind (ซ่อม /
  เปลี่ยนอะไหล่ / ตรวจเช็ค / ทำความสะอาด / หล่อลื่น / อื่นๆ), what was done, why, who did it, cost, and for a
  replaced part its brand / model / serial / vendor. Filter by machine, kind, date, search.
- **Import** from the Google Sheet "Saraburi Maintenence Sheet" (download as .xlsx):
  `scripts/import_machinery.py <file>` (dry run) / `--write`. It reads "อะไหล่ Main", "Motor Index Main" (folded into
  the same rows: status, current vs desired), "Pressure Relief Valve" and the change logs
  ("Saraburi_General", "Kaeng Khoi"). Rows that already exist are skipped, so it can be re-run. The other tabs
  (routine cleaning schedule, purchase orders, price comparison, water calculation) are not imported yet.
- Until `schema_v9.sql` has been run the pages show a banner and writes explain what is missing
  (`machinery.has_v9()`).

## Maintenance programme ("แผนซ่อมบำรุง", `#plan`)

`schema_v10.sql`, `schema_v11.sql`, `app/service_plan.py`, `static/plan.js`, `scripts/seed_gps.py`, `scripts/seed_plan_groups.py`.
When is each truck's next oil change, filter, gearbox oil ... due -- and what did the last service do, what is missing,
what comes next. The numbers are the ones in `Maintenance_Plan.xlsx` (kept outside git).

- **The plan** has two rule sets of km per item (`DEFAULT_KM`): **รถเล็ก** (D-Max, Revo) and **รถใหญ่** (NLR/NMR/NPR,
  6- and 10-wheelers). Oil and engine filter 10,000 / 20,000; diesel filter 40,000 / 20,000 (+ water trap on big); air
  filter 40,000 / 80,000; gearbox, differential, brake/clutch, power steering and coolant 40,000; อัดจารบีทุกจุด (the wheel-grease item) 30,000 /
  20,000; valve adjustment 80,000 (big) and 100,000 (small: the Isuzu 2.5 only); valve cover gasket + injector seal 80,000 (big only);
  injector washers 100,000 (small only).
- **Every item is "whichever comes first, km or months".** The months come from the truck's **group**
  (`service_groups`, `vehicle_plans`): how many months one base step of km (10,000 small / 20,000 big) is worth for that
  truck. S2 = route pickups, 2 months per 10,000 km (so the 40,000 items every 8 months); S3 factory pickups, 3; S6 the
  repair truck, 6; B4 NLR on big-truck rules, 4 per 20,000; B5 route trucks, 5; B6 factory / CP trucks, 6; KK1 Kaeng
  Khoi #1, worked out from its own 242 km/day. `scripts/seed_plan_groups.py` put the 65 trucks in their groups
  (#41 and #54 have none = no schedule). The group of a truck is chosen on its page; the months of a group and the km
  of an item are changed on the second tab of `#plan` (`service_groups`, `service_intervals`).
- **A truck's page** shows its group and an Isuzu-style **chart**: the first column is the latest event (what it did,
  ✓), then "ครั้งถัดไป", "2 ครั้งถัดไป" ... one base step apart, each with its date. A dot means the item falls due in
  that round (red = already overdue, shown in the next round), "?" = never recorded. Each item is counted from the date
  it was last done, so it lines up like the Isuzu chart. With no odometer known the columns only say which round;
  once there is a mileage (on a bill, a record or a reading) the km of each round appears. A truck with no history
  shows "ยังไม่มีประวัติ". Everything is recomputed from the records every time, so it moves on by itself.
- **"+ บันทึกว่าทำแล้ว"** (on a truck's page or on a card of `#plan`) saves the ticked items as a **repair record done by
  hand** (`source = manual`, one line per item, the line text is the item's own name) -- the same kind of record as a
  mechanic's own work. So it shows in **สมุดงานซ่อม** and its page, where it can be edited, and a record entered by hand
  can be **deleted** (`DELETE /api/events/{id}`: never a bill, never a record that closed a board request) -- the way to undo a
  mis-click. "เลือกทั้งหมด" ticks every item (that is how to start a truck's count). Items already recorded for that
  truck on that day (a bill, an earlier click) are not recorded twice.
- **When it was last done** comes from the repair records: confirmed bills (a line such as "กรองเครื่อง" counts as that
  truck's engine filter that day -- `classify_line`; a top-up "(เติม)" and parts such as "สกรูแรงดันน้ำมันเครื่อง" are
  ignored) and records entered by hand; the mileage on the record is used when it has one. The truck's page lists them
  under "ประวัติที่ทำแล้ว" with a "ดู / แก้ไข" link to the record.
- **Which truck follows which group is set in one place**: `#plan` › ตั้งค่าระยะ › "รถแต่ละคันใช้กลุ่มไหน" (`#plan-settings`),
  every active truck by branch with a dropdown. Picking only marks the row yellow; nothing changes until "บันทึกการเปลี่ยนกลุ่ม
  (N คัน)" and a confirmation listing each change (`PUT /api/plan/vehicle-groups`). The truck's page only shows its group.
  A group whose months / km per day were changed has a "ค่าเริ่มต้น" button (`DELETE /api/plan/groups?code=`).
- **Changing the maintenance items is done in the code** (`app/service_plan.py`), not in the app (an add-your-own-item form
  existed for a day and was taken out; `schema_v20.sql` drops its table). To add an item: a line in `ITEMS` (name, table group),
  its km per rule set in `DEFAULT_KM` (a missing key = not for that kind of truck), a category in `ITEM_CATEGORY` if it is not
  "ไส้กรองและของเหลว", and how a bill line is recognised (`_LINE_RULES`: at the start of the line; `_ANYWHERE`: anywhere in it).
  Every item is counted in km; its months follow from the truck's group (its driving rate). An item only SOME trucks of a rule set
  need goes in `ONLY_FOR` (rule set, key) with a test on the truck's make / model text: today the small trucks' ปรับตั้งวาล์ว is for
  the **Isuzu 2.5** only (make contains "isuzu" and model contains "2.5" -- so write a new one's model like "D-Max 2.5"; the D-Max 1.9
  and the Revo have hydraulic valves and never get it). A truck that fails the test has no row for it in its plan and gets no reminder.
- Built in since: ล้างท่อร่วมไอดี (small trucks, 100,000 km, with the injector washers; a line that is the manifold itself --
  "ท่อร่วมไอดี", "เปลี่ยน / ล้าง / ทำความสะอาด ท่อร่วมไอดี" -- counts, since a new one is as clean as it gets; "ท่อไอดี" alone is the intake
  hose and needs ล้าง / ทำความสะอาด; a gasket line does not count), ฟลัชชิ่งน้ำมันเครื่อง (60,000 km both kinds -- about once a year in S2 and B4), เช็คช่วงล่าง (every oil change: 10,000 km small, 20,000 km big;
  any bill line with แหนบ / สลักคอม้า / ปีกนก / โช้ค or "เช็ค/ตรวจช่วงล่าง" counts as done), ปรับตั้งวาล์ว for the Isuzu 2.5 (100,000 km, with the
  injector washers). The big trucks' valve adjustment is every 80,000 km.
- Bill matching notes: a line that starts with "อัดจารบี" (or "ค่าอัดจารบี") counts as the wheel-bearing grease service -- the
  mechanic greases everything in one go; a bare "จารบี 1 กระป๋อง" or "หัวอัดจาระบี" is a part and does not. The group rows on the
  settings page show what the group's engine-oil round is (km as set for its rule set, and what that is in months), so changing
  the oil km below is visible above (the group's own "months per 10,000 km" is the driving rate and does not change).
- The settings page layout: reminders (full width), the groups (full width), then รถเล็ก (left) and รถใหญ่ (right) with the km
  of each item, and which truck follows which group.
- **Scheduled items travel with the oil change**: the aim is that the other items get done at an oil change, not on a day of their
  own. When a truck's oil change is in the reminders (inside the "coming close" window), every other item of that truck that would
  fall due before the NEXT oil change (its due date within the oil interval after this one) is added to its reminders, marked
  "ทำพร้อมน้ำมันเครื่อง" -- in the reminder text, the reminders list and the "รถใกล้ถึงคิวบำรุง" cards (so "เข้าคิว" puts them all in
  the one repair request). Items already reminded, snoozed ones, ones with no starting date, and items not ticked in the reminder
  list are not added (`_with_the_oil_change` in `app/service_plan.py`).
- **Reminders cover every item by default** ("เลือกทั้งหมด" on the settings card); untick what you do not want.
- **Real driving** only matters when it is known: odometer readings, GPS distance for a period (**+ ระยะทาง**, 12
  trucks seeded by `scripts/seed_gps.py`) -- then a truck that drives more than its group assumes gets its due date
  brought forward. Otherwise the months decide.
- **`#plan`** lists the trucks that are overdue / due soon first (filters: branch, size, search; "ทุกคัน", "ที่ยังไม่มีประวัติเลย").
- Until the two schema files have been run the page shows a banner and writes explain what is missing (`service_plan.has_v10()`).

## The manager's board ("ผู้จัดการ")

`schema_v12.sql`, `app/mgr.py`, `app/mgr_recurring.py`, `app/mgr_ai.py`, `app/mgr_jobs.py`, `static/today.js`,
`static/mgr.js`. The owner's own ticket system -- the same idea as the repair board, for follow-ups, money, fixing things,
the outside world, rounds and inspections. A group at the top of the sidebar with three pages:

- **บอร์ดของฉัน** (`#mgr`): ต้องทำ -> กำลังทำ -> รอคนอื่น / รอผล -> เสร็จแล้ว; drag a card or use its buttons (every column has a forward and a back button, one column at a time: ต้องทำ → กำลังทำ → รอผล → เสร็จแล้ว; the first has only forward). The last column shows only what was closed today (Thai calendar day) and starts empty each day; a button under it shows the earlier ones. "รอผล…" asks who /
  what it is waiting on (with a call button; the shops' numbers are offered), and a **follow-up date**: that day the card comes
  back to the top of its column marked "ถึงวันติดตามแล้ว". A ticket has a title, notes, type, place, urgent, due date + time, a
  follow-up date and a checklist. Done / cancelled tickets can be reopened.
- **The type is picked by the AI** (`mgr_ai.classify`: Claude chooses from the types with structured output) unless chosen by
  hand; the card shows an "AI" tag and the type can be changed in the edit dialog ("ให้ระบบเลือกใหม่" asks again). If the AI
  fails or there is no key, a keyword rule decides (marked as such), so adding a ticket never waits on it. The 13 default types
  are in `schema_v12.sql` (ตามงาน/ตามของ, การเงิน, แก้ปัญหา/ซ่อม, ติดต่อภายนอก, ลูกค้า/ขาย, จัดซื้อ, คน, ตรวจ/เยี่ยมโรงงาน,
  ตัดสินใจ/โครงการ, ซื้อของ, รายงาน, ส่วนตัว, อื่นๆ) and can be renamed, recoloured or switched off.
- **งานประจำ** (`#mgr-recurring`): recurring tickets -- every N days; chosen weekdays (every N weeks); days of the month (1, 15,
  "สิ้นเดือน") or "the first Monday"; every N months; yearly -- with a time of day, "create it N days ahead", optional start and
  end, and two modes: by the calendar, or **counted from the day the last one was finished** (one at a time). Tickets are created by
  the server (`mgr.generate_recurring`, every minute and whenever the board loads); one ticket per occurrence is enforced by a
  unique index, an occurrence missed by more than 3 days is skipped rather than piled up.
- **ตั้งค่า** (`#mgr-settings`): the reminders (on screen), the types, the places (branches, shops, other).
- **Reminders are on screen, never LINE** (the phone is on do-not-disturb): `app/mgr_jobs.py` + `static/today.js`, from
  `GET /api/today`. Red / amber numbers beside "บอร์ดของฉัน" and
  "แผนซ่อมบำรุง" in the menu, a message on screen when a ticket's own time comes, and -- if switched on in ผู้จัดการ ›
  ตั้งค่า -- a browser notification (only while the site is open in a tab). A background loop makes the recurring tickets
  every minute (`DISABLE_SCHEDULER=1` switches it off, e.g. for a second local copy).
- The old Ticketing bot is not removed yet: besides tickets it also reads bills, slips and supply photos and keeps the
  preventive-maintenance catalogue.

## Buying for the factory ("บิลซื้อของ", "เทียบราคา", "สต็อก")

`schema_v16.sql`, `app/purchasing.py`, `app/purchase_extraction.py`, `app/stock.py`, `static/buy.js`. Three pages in the
**เครื่องจักร** group:

- **บิลซื้อของ** (`#purchases` -- filters: search, supplier, branch, period --, new bill `#purchase-new`): a purchase bill /
  receipt from a supplier, read by Claude (one call per bill: seller, number, date -- พ.ศ. turned into ค.ศ. --, VAT mode,
  total, every line with quantity, unit and amounts; unsure fields come back yellow) or typed in ("กรอกเอง"). Several files
  can be chosen at once: "แต่ละไฟล์เป็นบิลคนละใบ" (one bill per file) or "ทุกไฟล์เป็นบิลใบเดียวกัน" (pages of one bill). They
  go into a **queue read in the background** (3 at a time, with a clock per bill; a number beside "บิลซื้อของ" in the menu
  counts the unsaved ones) -- the user can open other pages and come back to "ตรวจและบันทึก" each one. The queue is kept in
  the page's memory only (a refresh loses unsaved ones; the browser warns first). The check page shows the original photo
  above what was read; clicking it opens a **pop-up** (not a new tab: ↻ turns it, a click on the picture zooms, Esc / click outside /
  "ปิด" closes), and saved bills open their photos the same way. The reader copes with any orientation (tested at 0/90/180/270 degrees); EXIF
  rotation is baked in on upload. Photos go to Storage under `purchases/`; the same file saved twice is caught (409, can
  still be saved on purpose). A bill can be deleted. Which machine a part was for is NOT asked on the bill (a part often
  serves several machines): the plan is a default parts template per machine, later.
- **Suppliers** are shops of kind `supplier` (`shops.kind`), kept on **ตั้งค่า › ร้านค้า / ผู้ขาย** with the same words and
  numbers as the repair shops; the vendor on a bill is found by those words, an unknown one can be added from the bill.
  The car side (board, log, dashboard) only ever sees the repair shops.
- **Items** (`stock_items`): each bill line can say which thing it is ("เป็นของ"). Lines are matched by the item's words
  (longest wins, `a+b` pairs, same rule as the shops), while typing and again on save; a line linked by hand teaches the
  item its text, so the next bill that writes it the same way links itself. "+ รายการใหม่" makes an item from the line.
- **What one unit really cost**: the bill's total is spread over the lines by amount (so VAT added at the bottom and a
  discount on the whole bill count), divided by the quantity -- `unit_cost`, and `unit_cost_ex_vat` without the 7 %. A
  total far from the lines is not trusted (only the VAT is added). Prices are compared on these, never the printed price.
- **เทียบราคา** (`#prices`): per item the last price, the cheapest (where, when) and the latest price at each supplier,
  cheapest first, with the purchase history; switch between paid (with VAT) and before VAT. Below: the lines not linked
  to an item yet, each with a picker (that is how old bills get cleaned up).
- **สต็อก (ร่าง)** (`#stock`): a draft while the supply room is renovated. On hand = the sum of `stock_moves`: + รับเข้า,
  − เบิก (optionally for which machine), นับ (a stock-take writes the difference). A minimum shows "ควรสั่งเพิ่ม". Saving a
  bill can receive its linked lines into stock ("รับของเข้าสต็อกด้วย", off by default); deleting the bill takes them out again.
- Not built yet, on purpose: the slip reader / spending report (to be discussed -- likely from the confirmed bills, dated
  when the work or purchase happened, not the bank), purchase orders. The parts index (`#machinery`) stays as it is for now.

## Settings ("ตั้งค่า", `#settings`)

`schema_v15.sql`, `app/shops.py`, `static/settings.js`. The repair shops are no longer written in the code: they are in the
database (`shops`, `shop_aliases`, `shop_contacts`, filled from the old lists, so nothing changed) and edited in the app. A shop
has a name, **words** (what a bill or a typed name may say: "จึงกงเฮง", "isuzu" ...; the shop's own name counts already; when
several match, the longest wins; a **pair** like `มงคล+ไดนาโม` needs both parts in the name, in any order -- the safe way to use a
common word such as "ไดนาโม", which on its own matches no shop; the page has a test box that shows which shop and which word a bill's
name resolves to) and **contacts** (phone numbers with a department or a person's name). A dealership with
several departments is **one shop**: the Isuzu dealership is one place to go and one name in the log, the board and the
dashboard, with ศูนย์บริการ and ศูนย์ซ่อมสีและตัวถัง as two contacts; each bill still keeps the department as written on it.
Renaming a shop renames it in the repair requests and the manager's tickets, and keeps the old name as a word. A shop can be
switched off (old bills keep grouping under it; it is no longer offered). The same shops appear as the "shop" places on the
manager's board, so there is one list to look after. More settings (the other things that are still in the code) will be added to this page.
