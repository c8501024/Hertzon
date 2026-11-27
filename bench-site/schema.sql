-- Hertzon Bench results. Apply with: npx wrangler d1 execute hertzon-bench --remote --file=schema.sql
CREATE TABLE IF NOT EXISTS results (
  id          TEXT PRIMARY KEY,          -- short public id, used in the result link
  created     INTEGER NOT NULL,          -- unix time of the submission
  run_id      TEXT NOT NULL UNIQUE,      -- random id of the run: the same run is stored once
  suite       TEXT NOT NULL,
  hertzon     TEXT NOT NULL,
  platform    TEXT NOT NULL,             -- Windows, macOS, Linux
  arch        TEXT,
  cpu         TEXT NOT NULL,
  gpu         TEXT,
  memory_gb   REAL,
  bandwidth   REAL,                      -- measured memory bandwidth, GB/s
  cores       INTEGER,
  solver      TEXT,
  score       INTEGER,                   -- overall: geometric mean of the part scores, only when every part has one
  complete    INTEGER NOT NULL,          -- 1 when every part ran and every test counted
  hw_key      TEXT NOT NULL,             -- same hardware and solver, any version
  system_key  TEXT NOT NULL,             -- same hardware, solver and Hertzon version
  name        TEXT,
  data        TEXT NOT NULL,             -- the whole submission as JSON
  delete_hash TEXT NOT NULL,             -- SHA-256 of the delete key the submitter got
  hidden      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS results_suite ON results (suite, hidden);
-- submissions per address and day, for the rate limit: only a salted hash, removed after a day
CREATE TABLE IF NOT EXISTS rate (
  ip_hash TEXT NOT NULL,
  ts      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_ip ON rate (ip_hash, ts);
