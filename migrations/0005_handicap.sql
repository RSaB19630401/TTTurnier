-- TT-Turnier – Migration 0005: Vorgabeturnier
-- Bitte die beiden Befehle EINZELN nacheinander in der D1-Console ausführen.

ALTER TABLE competitions ADD COLUMN handicap_enabled INTEGER NOT NULL DEFAULT 0;

ALTER TABLE entries ADD COLUMN handicap INTEGER NOT NULL DEFAULT 0;
