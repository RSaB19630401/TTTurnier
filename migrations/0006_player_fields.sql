-- TT-Turnier – Migration 0006: Geburtsjahr in den Spieler-Stammdaten
-- (Das Geschlecht existiert bereits als Spalte `sex`.)

ALTER TABLE players ADD COLUMN birth_year INTEGER;
