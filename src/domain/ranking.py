"""
Gruppen-/Rundenrangliste nach ITTF / DTTB Wettspielordnung.

Kriterienreihenfolge:
  1) Spielpunkte (Sieg 2, Niederlage 1, nicht beendet/kampflos 0)
  2) bei Gleichstand NUR die Partien der Gleichstehenden untereinander, iterativ:
       Spielpunkte -> Satzquotient -> Ballpunktquotient -> Los
Trennt ein Kriterium die Menge in mehrere Ränge, wird jede weiterhin
mehrdeutige Teilmenge von vorne (ab Spielpunkten) neu bewertet.
Quelle: ITTF Handbook for Tournament Referees.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from math import gcd, inf


@dataclass
class MatchResult:
    """Ein abgeschlossenes Einzelspiel für die Wertung."""
    home: int          # entry id
    away: int
    home_sets: int
    away_sets: int
    home_points: int
    away_points: int
    walkover: bool = False  # True => Verlierer erhält 0 Spielpunkte


@dataclass
class Stat:
    entry_id: int
    mp: int = 0        # Spielpunkte
    gw: int = 0        # Sätze gewonnen
    gl: int = 0        # Sätze verloren
    pw: int = 0        # Ballpunkte gewonnen
    pl: int = 0        # Ballpunkte verloren
    played: int = 0
    wins: int = 0

    @property
    def set_ratio(self) -> float:
        return inf if self.gl == 0 and self.gw > 0 else (0.0 if self.gl == 0 else self.gw / self.gl)

    @property
    def point_ratio(self) -> float:
        return inf if self.pl == 0 and self.pw > 0 else (0.0 if self.pl == 0 else self.pw / self.pl)


def _ratio_key(won: int, lost: int) -> str:
    if lost == 0:
        return "INF" if won > 0 else "0/0"
    g = gcd(won, lost) or 1
    return f"{won // g}/{lost // g}"


def _ratio_val(won: int, lost: int) -> float:
    if lost == 0:
        return inf if won > 0 else 0.0
    return won / lost


def compute_stats(ids: list[int], matches: list[MatchResult]) -> dict[int, Stat]:
    """Kennzahlen NUR aus den übergebenen Partien (i.d.R. der Gleichstehenden untereinander)."""
    st = {i: Stat(i) for i in ids}
    idset = set(ids)
    for m in matches:
        if m.home not in idset or m.away not in idset:
            continue
        H, A = st[m.home], st[m.away]
        H.gw += m.home_sets; H.gl += m.away_sets
        A.gw += m.away_sets; A.gl += m.home_sets
        H.pw += m.home_points; H.pl += m.away_points
        A.pw += m.away_points; A.pl += m.home_points
        H.played += 1; A.played += 1
        home_won = m.home_sets > m.away_sets
        if home_won:
            H.mp += 2; H.wins += 1
            A.mp += 0 if m.walkover else 1
        else:
            A.mp += 2; A.wins += 1
            H.mp += 0 if m.walkover else 1
    return st


@dataclass
class RankingResult:
    order: list[int]                      # entry ids in Endreihenfolge
    stats: dict[int, Stat]                # Gesamtkennzahlen (alle Partien) je Spieler
    tie_break_used: bool = False
    unresolved: list[list[int]] = field(default_factory=list)  # per Los zu klärende Gruppen


def _resolve(ids: list[int], all_matches: list[MatchResult], flags: dict) -> list[int]:
    if len(ids) <= 1:
        return list(ids)
    sub = [m for m in all_matches if m.home in ids and m.away in ids]
    st = compute_stats(ids, sub)

    def split(key_of, val_of) -> list[int] | None:
        buckets: dict = {}
        for i in ids:
            k = key_of(i)
            buckets.setdefault(k, {"v": val_of(i), "ids": []})["ids"].append(i)
        if len(buckets) <= 1:
            return None
        tiers = sorted(buckets.values(), key=lambda b: b["v"], reverse=True)
        out: list[int] = []
        for t in tiers:
            if len(t["ids"]) > 1:
                flags["tie"] = True
            out.extend(_resolve(t["ids"], all_matches, flags))
        return out

    # 1) Spielpunkte
    o = split(lambda i: st[i].mp, lambda i: st[i].mp)
    if o is not None:
        return o
    # Spielpunkte trennen diese Menge nicht -> ein Tie-Break ist nötig
    flags["tie"] = True
    # 2) Satzquotient
    o = split(lambda i: _ratio_key(st[i].gw, st[i].gl), lambda i: _ratio_val(st[i].gw, st[i].gl))
    if o is not None:
        return o
    # 3) Ballpunktquotient
    o = split(lambda i: _ratio_key(st[i].pw, st[i].pl), lambda i: _ratio_val(st[i].pw, st[i].pl))
    if o is not None:
        return o
    # 4) unauflösbar -> Los
    flags.setdefault("unresolved", []).append(list(ids))
    return list(ids)


def rank_group(entry_ids: list[int], matches: list[MatchResult]) -> RankingResult:
    """Vollständige Gruppen-/Rundenrangliste."""
    flags: dict = {"tie": False, "unresolved": []}
    order = _resolve(list(entry_ids), matches, flags)
    total = compute_stats(list(entry_ids), matches)  # Gesamtzahlen zur Anzeige
    return RankingResult(order=order, stats=total,
                         tie_break_used=flags["tie"], unresolved=flags["unresolved"])
