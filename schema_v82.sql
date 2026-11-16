-- v82 (owner, 16 Nov 2026): พิมพ์แล้ว -- a tick on each bill once it has been printed. Every bill before today counts as printed.
alter table tax_invoices add column if not exists printed_at timestamptz;
update tax_invoices set printed_at = now() where printed_at is null;
