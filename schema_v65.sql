-- v65 (10 Nov 2026): office logins. The owner keeps the access code (APP_TOKEN); the bookkeeper and anyone else get their own
-- sign-in name and a password they choose themselves from a one-time link, and see only the menus the owner ticks (app/users.py).
create table if not exists app_users (
  id bigserial primary key,
  name text not null,
  username text not null unique,
  role text not null default 'staff',
  menus text[] not null default '{}',
  pw_salt text,
  pw_hash text,
  invite_hash text unique,
  invite_expires timestamptz,
  failed int not null default 0,
  locked_until timestamptz,
  active boolean not null default true,
  created_at timestamptz not null default now()
);
create table if not exists app_sessions (
  id bigserial primary key,
  token_hash text not null unique,
  user_id bigint not null references app_users(id) on delete cascade,
  agent text,
  created_at timestamptz not null default now(),
  last_seen timestamptz,
  revoked boolean not null default false
);
create index if not exists app_sessions_user on app_sessions(user_id);
-- only the server (service key) reads these
alter table app_users enable row level security;
alter table app_sessions enable row level security;
