"""
Super-Mêlée – rundenweise zufällige Doppel mit der harten Regel
„nie zweimal derselbe Partner".

Partnerbildung über die Kreismethode (1-Faktorisierung des vollständigen
Graphen K_N für gerade N): sie zerlegt die N Spieler in N-1 Runden zu je N/2
Paaren, sodass jedes mögliche Paar genau einmal vorkommt. Damit sind bis zu
N-1 Runden garantiert partner-konfliktfrei. Die Paare werden anschließend
(zufällig) zu Spielen (Doppel gegen Doppel) zusammengestellt; bei ungerader
Paarzahl erhält ein Paar ein Freilos.
"""
from __future__ import annotations
import random


def partner_rounds(n: int) -> list[list[tuple[int, int]]]:
    """Alle N-1 partnerfreien Runden für gerade N. Rückgabe je Runde: Liste von Paaren (Indexpaare 0..n-1)."""
    if n % 2 != 0 or n < 2:
        raise ValueError("Teilnehmerzahl muss gerade und >= 2 sein.")
    arr = list(range(n))
    rounds = []
    for _ in range(n - 1):
        pairs = [(arr[i], arr[n - 1 - i]) for i in range(n // 2)]
        rounds.append(pairs)
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]  # rotieren, arr[0] fix
    return rounds


def max_rounds(n: int) -> int:
    return n - 1


def draw_round(pair_indices, player_ids, rng: random.Random):
    """Aus den Paaren einer Runde Spiele bilden (Doppel gegen Doppel).
    Rückgabe: (games, bye) – games = Liste [(team_a, team_b)], bye = Paar oder None."""
    pairs = [(player_ids[a], player_ids[b]) for (a, b) in pair_indices]
    rng.shuffle(pairs)
    bye = None
    if len(pairs) % 2 == 1:
        bye = pairs.pop()          # ein Paar erhält Freilos
    games = [(pairs[i], pairs[i + 1]) for i in range(0, len(pairs), 2)]
    return games, bye


# --------------------------------------------------------------- Wertung ------
def standings(player_ids, results):
    """results: Liste von dicts je gespieltem Spiel/Freilos:
       {'players':[ids...], 'points': {id: siegpunkte}, 'diff': {id: ball-/satzdifferenz}}
    Rückgabe: sortierte Rangliste [{player, points, diff, games}]."""
    agg = {pid: {"points": 0, "diff": 0, "games": 0} for pid in player_ids}
    for r in results:
        for pid in r["players"]:
            agg[pid]["points"] += r["points"].get(pid, 0)
            agg[pid]["diff"] += r["diff"].get(pid, 0)
            agg[pid]["games"] += 1
    rows = [{"player": pid, **agg[pid]} for pid in player_ids]
    rows.sort(key=lambda x: (-x["points"], -x["diff"], x["player"]))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows
