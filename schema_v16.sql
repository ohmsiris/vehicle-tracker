-- schema_v16.sql -- buying for the factory: suppliers, purchase bills read like the repair bills, the price of every
-- thing bought (where to buy it cheapest), and a first draft of the mechanics' stock room.
--
--   shops.kind          'repair' = a place a truck is sent to (as before) · 'supplier' = a shop the factory buys from.
--                       Both are kept on the settings page with the same words (to find them on a bill) and phone numbers.
--   stock_items         one row per thing kept / bought (a bearing 6205-2RS, a belt B-82, engine oil 15W-40 ...)
--   stock_item_aliases  words that, found in a bill line, mean that thing (like the shops' words)
--   purchase_bills      one bill / receipt from a supplier: who, when, number, VAT, total
--   purchase_attachments  the photo / PDF of the bill (in the event-files bucket, under purchases/)
--   purchase_lines      what was bought: as written on the bill, which thing it is, how many, what it really cost
--   stock_moves         in (+) / out (-) / counted, per thing: what is on the shelf = the sum (a draft: the room is
--                       being renovated, so nothing goes in automatically unless asked)

alter table shops add column if not exists kind text not null default 'repair';
alter table shops drop constraint if exists shops_kind_check;
alter table shops add constraint shops_kind_check check (kind in ('repair', 'supplier'));

create table if not exists stock_items (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    category    text,                         -- ลูกปืน / สายพาน / ไฟฟ้า / น้ำมัน-จาระบี ...
    unit        text,                         -- ตัว / เส้น / ลิตร / กล่อง
    spec        text,
    min_qty     numeric,                      -- below this the stock page says "ควรสั่งเพิ่ม"
    location    text,                         -- the shelf
    active      boolean not null default true,
    notes       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create unique index if not exists uq_stock_items_name on stock_items (lower(name));

create table if not exists stock_item_aliases (
    id       uuid primary key default gen_random_uuid(),
    item_id  uuid not null references stock_items (id) on delete cascade,
    alias    text not null
);
create unique index if not exists uq_stock_item_aliases_alias on stock_item_aliases (lower(alias));
create index if not exists ix_stock_item_aliases_item on stock_item_aliases (item_id);

create table if not exists purchase_bills (
    id                uuid primary key default gen_random_uuid(),
    vendor_id         uuid references shops (id) on delete set null,
    vendor_name       text,                   -- as written on the bill
    bill_date         date not null,
    bill_no           text,
    branch            text,
    vat_mode          text not null default 'none' check (vat_mode in ('included', 'excluded', 'none')),
    subtotal          numeric,
    vat_amount        numeric,
    discount          numeric,
    total             numeric,
    notes             text,
    source            text not null default 'bill' check (source in ('bill', 'manual')),
    extraction        jsonb,                  -- what the reader saw, for checking later
    extraction_model  text,
    created_at        timestamptz not null default now()
);
create index if not exists ix_purchase_bills_date on purchase_bills (bill_date desc);
create index if not exists ix_purchase_bills_vendor on purchase_bills (vendor_id);

create table if not exists purchase_attachments (
    id            uuid primary key default gen_random_uuid(),
    bill_id       uuid not null references purchase_bills (id) on delete cascade,
    storage_path  text not null,
    mime_type     text not null,
    byte_size     integer,
    sha256        text not null,
    sort_order    integer not null default 0,
    created_at    timestamptz not null default now()
);
create unique index if not exists uq_purchase_attachments_sha on purchase_attachments (sha256);

create table if not exists purchase_lines (
    id                    uuid primary key default gen_random_uuid(),
    bill_id               uuid not null references purchase_bills (id) on delete cascade,
    line_number           integer not null,
    description           text not null,
    item_id               uuid references stock_items (id) on delete set null,
    quantity              numeric,
    unit                  text,
    unit_price            numeric,           -- as on the bill
    line_total            numeric,           -- as on the bill
    unit_cost             numeric,           -- one unit, what was really paid (VAT and bill discount spread over the lines)
    unit_cost_ex_vat      numeric,
    machine_id            uuid references machines (id) on delete set null,
    used_for              text,              -- free text: "เครื่อง 3 ปั๊มน้ำ", "รถ #12"
    low_confidence_fields text[] not null default '{}',
    created_at            timestamptz not null default now()
);
create index if not exists ix_purchase_lines_bill on purchase_lines (bill_id);
create index if not exists ix_purchase_lines_item on purchase_lines (item_id);

create table if not exists stock_moves (
    id                uuid primary key default gen_random_uuid(),
    item_id           uuid not null references stock_items (id) on delete cascade,
    move_date         date not null,
    kind              text not null check (kind in ('in', 'out', 'count')),
    qty               numeric not null,      -- in: +, out: - ; count: the change that brings the shelf to what was counted
    purchase_line_id  uuid references purchase_lines (id) on delete set null,
    machine_id        uuid references machines (id) on delete set null,
    notes             text,
    created_at        timestamptz not null default now()
);
create index if not exists ix_stock_moves_item on stock_moves (item_id, move_date);

alter table stock_items          enable row level security;
alter table stock_item_aliases   enable row level security;
alter table purchase_bills       enable row level security;
alter table purchase_attachments enable row level security;
alter table purchase_lines       enable row level security;
alter table stock_moves          enable row level security;
revoke all on stock_items, stock_item_aliases, purchase_bills, purchase_attachments, purchase_lines, stock_moves from anon, authenticated;
