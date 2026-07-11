-- TT-Turnier – Migration 0004: Rundenzahl für Super-Mêlée
-- Anzahl der zu spielenden Runden (beim Anlegen festgelegt).

ALTER TABLE competitions ADD COLUMN round_count INTEGER NOT NULL DEFAULT 0;
