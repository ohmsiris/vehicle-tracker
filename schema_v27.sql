-- ---------------------------------------------------------------------------
-- schema_v27.sql -- each worker's own shift time (an exception to the default for his branch and position). Run after schema_v21. Safe to run twice.
--
--   employees.shift_start / shift_end   e.g. 08:00 and 17:00. Both empty = the default (ผลิต: Saraburi 12 h, Kaeng Khoi 9 h; other roles have free hours).
--                                       Set, the shift length (end - start, past midnight if the end is earlier) is what เวลาทำงาน flags a short day against.
-- ---------------------------------------------------------------------------
alter table employees add column if not exists shift_start time;
alter table employees add column if not exists shift_end time;
