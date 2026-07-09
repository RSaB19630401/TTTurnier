-- TT-Turnier – Migration 0003: Doppel-Unterstützung
-- competition_type: 'single' (Einzel, Standard) | 'double' (Doppel)
-- pairing:          bei Doppel 'fixed' (feste Paare) | 'draw' (Partner auslosen); sonst NULL

ALTER TABLE competitions ADD COLUMN competition_type TEXT NOT NULL DEFAULT 'single';
ALTER TABLE competitions ADD COLUMN pairing TEXT;
