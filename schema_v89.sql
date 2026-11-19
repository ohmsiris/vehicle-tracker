-- v89: row level security on the six tables that were made without it (the ledger and the tyre store), 19 Nov 2026.
-- The server uses the secret key, which is not subject to it; with no policies the public key can read and write nothing --
-- the same as every other table. EVERY NEW TABLE must have its own "enable row level security" line (tests/test_backup.py checks).
alter table public.acc_accounts enable row level security;
alter table public.acc_entries enable row level security;
alter table public.acc_lines enable row level security;
alter table public.acc_feed_marks enable row level security;
alter table public.tyre_stock_moves enable row level security;
alter table public.tyre_stock_sizes enable row level security;
revoke all on public.acc_accounts, public.acc_entries, public.acc_lines, public.acc_feed_marks, public.tyre_stock_moves, public.tyre_stock_sizes from anon, authenticated;

-- functions: a fixed search path (Supabase's advice)
alter function public.set_updated_at() set search_path = public, pg_temp;
alter function public.touch_status_changed_at() set search_path = public, pg_temp;
alter function public.update_maintenance_event(jsonb) set search_path = public, pg_temp;
alter function public.save_maintenance_event(jsonb) set search_path = public, pg_temp;
alter function public.amr_days(text, date) set search_path = public, pg_temp;
