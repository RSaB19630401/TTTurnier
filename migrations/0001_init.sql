-- TT-Turnier – D1/SQLite-Schema (Migration 0001)

CREATE TABLE IF NOT EXISTS clubs (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  prefix   TEXT NOT NULL DEFAULT '',
  name     TEXT NOT NULL,
  contact  TEXT NOT NULL DEFAULT '',
  remark   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS players (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id    INTEGER REFERENCES clubs(id),
  first_name TEXT NOT NULL DEFAULT '',
  last_name  TEXT NOT NULL DEFAULT '',
  sex        TEXT NOT NULL DEFAULT '',
  remark     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS competitions (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  name              TEXT NOT NULL,
  mode              TEXT NOT NULL,
  sets_to_win       INTEGER NOT NULL DEFAULT 3,
  points_per_set    INTEGER NOT NULL DEFAULT 11,
  group_count       INTEGER NOT NULL DEFAULT 1,
  advance_per_group INTEGER NOT NULL DEFAULT 2,
  ko_sets_to_win    INTEGER NOT NULL DEFAULT 3,
  status            TEXT NOT NULL DEFAULT 'setup'
);

CREATE TABLE IF NOT EXISTS entries (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  competition_id INTEGER NOT NULL REFERENCES competitions(id),
  seeded         INTEGER NOT NULL DEFAULT 0,
  seed_no        INTEGER,
  group_no       INTEGER,
  bracket_slot   INTEGER
);

CREATE TABLE IF NOT EXISTS entry_members (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id    INTEGER NOT NULL REFERENCES entries(id),
  player_id   INTEGER NOT NULL REFERENCES players(id),
  order_index INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS matches (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  competition_id INTEGER NOT NULL REFERENCES competitions(id),
  phase          TEXT NOT NULL,            -- group | ko
  round_no       INTEGER NOT NULL DEFAULT 0,
  group_no       INTEGER,
  bracket_round  INTEGER,
  bracket_index  INTEGER,
  home_entry_id  INTEGER REFERENCES entries(id),
  away_entry_id  INTEGER REFERENCES entries(id),
  walkover       TEXT,                     -- home | away | NULL
  status         TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS match_sets (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id    INTEGER NOT NULL REFERENCES matches(id),
  set_no      INTEGER NOT NULL,
  home_points INTEGER NOT NULL DEFAULT 0,
  away_points INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_players_club       ON players(club_id);
CREATE INDEX IF NOT EXISTS idx_entries_comp        ON entries(competition_id);
CREATE INDEX IF NOT EXISTS idx_members_entry       ON entry_members(entry_id);
CREATE INDEX IF NOT EXISTS idx_matches_comp        ON matches(competition_id);
CREATE INDEX IF NOT EXISTS idx_sets_match          ON match_sets(match_id);
