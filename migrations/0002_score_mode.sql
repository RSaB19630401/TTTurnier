-- TT-Turnier – Migration 0002: Wertungsmodus je Wettbewerb
-- 'points' = Ballpunkte je Satz erfassen (Standard, wie bisher)
-- 'sets'   = nur Satzergebnis erfassen (kein Ballpunktquotient)

ALTER TABLE competitions ADD COLUMN score_mode TEXT NOT NULL DEFAULT 'points';
