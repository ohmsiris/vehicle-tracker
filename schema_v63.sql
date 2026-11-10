-- v63 (10 Nov 2026): which phone each driver sign-in is, so the office can see how many phones each driver is signed in on
-- and when each was last used. `agent` is a short description worked out from the browser at sign-in ("Android · Chrome").
alter table sale_driver_devices add column if not exists agent text;
