"""
Schweizer System (nur Einzel).

Regeln:
- Sieg 1, Niederlage 0, Freilos 1 Punkt.
- Paarung nach Punktgleichheit; nie zweimal derselbe Gegner.
- Freilos (bei ungerader Teilnehmerzahl) an den schlechtestplatzierten Spieler,
  der noch kein Freilos hatte.
- Feinwertung: Buchholz = Summe der Punkte der eigenen Gegner. Für eine
  Freilos-Runde wird ein fiktiver Gegner mit der eigenen Punktzahl angesetzt,
  damit das Freilos die Wertung nicht verzerrt.
"""
from __future__ import annotations
import random


def standings(player_ids, results, byes):
    """results: [{'home':id,'away':id,'winner':id}]  byes: [player_id, ...] (je Freilos ein Eintrag)
    Rückgabe: sortierte Rangliste mit points, buchholz, games."""
    pts = {p: 0 for p in player_ids}
    opps = {p: [] for p in player_ids}
    games = {p: 0 for p in player_ids}
    nbyes = {p: 0 for p in player_ids}
    for r in results:
        h, a, w = r["home"], r["away"], r["winner"]
        pts[w] += 1
        games[h] += 1; games[a] += 1
        opps[h].append(a); opps[a].append(h)
    for p in byes:
        pts[p] += 1
        games[p] += 1
        nbyes[p] += 1

    bh = {}
    for p in player_ids:
        s = sum(pts[o] for o in opps[p])
        s += nbyes[p] * pts[p]   # fiktiver Gegner je Freilos: eigene Punktzahl
        bh[p] = s

    rows = [{"player": p, "points": pts[p], "buchholz": bh[p],
             "games": games[p], "byes": nbyes[p]} for p in player_ids]
    rows.sort(key=lambda x: (-x["points"], -x["buchholz"], x["player"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def _pair_backtrack(order, played, i=0, acc=None):
    """Paart die Spieler in `order` (bereits nach Rang sortiert) so, dass kein Paar
    schon gegeneinander gespielt hat. Greedy mit Backtracking -> findet eine Lösung, falls möglich."""
    if acc is None:
        acc = []
    n = len(order)
    if i >= n:
        return acc
    if order[i] is None:
        return _pair_backtrack(order, played, i + 1, acc)
    a = order[i]
    for j in range(i + 1, n):
        b = order[j]
        if b is None or b in played[a]:
            continue
        order[i] = order[j] = None
        acc.append((a, b))
        res = _pair_backtrack(order, played, i + 1, acc)
        if res is not None:
            return res
        acc.pop()
        order[i], order[j] = a, b
    return None


def next_round(player_ids, results, byes, rng: random.Random,
               first_round_mode: str = "random", seeded: set | None = None):
    """Erzeugt die Paarungen der nächsten Runde.
    Rückgabe: (pairs, bye) – pairs = [(home, away)], bye = player_id oder None."""
    played = {p: set() for p in player_ids}
    for r in results:
        played[r["home"]].add(r["away"])
        played[r["away"]].add(r["home"])

    first = not results and not byes
    table = standings(player_ids, results, byes)
    rank_of = {r["player"]: r["rank"] for r in table}

    # --- Freilos-Kandidaten (ungerade Zahl): schlechtestplatziert ohne bisheriges Freilos zuerst
    odd = len(player_ids) % 2 == 1
    if odd:
        had = set(byes)
        prefer = [r["player"] for r in reversed(table) if r["player"] not in had]
        rest = [r["player"] for r in reversed(table) if r["player"] in had]
        bye_candidates = prefer + rest      # erste Wahl: schlechtester ohne Freilos
    else:
        bye_candidates = [None]

    last_err = None
    for bye in bye_candidates:
        pool = [p for p in player_ids if p != bye]

        if first:
            if first_round_mode == "seeded" and seeded:
                s = [p for p in pool if p in seeded]
                u = [p for p in pool if p not in seeded]
                rng.shuffle(s); rng.shuffle(u)
                pairs = [(a, b) for a, b in zip(s, u)]
                extra = s[len(u):] + u[len(s):]
                rng.shuffle(extra)
                for i in range(0, len(extra) - 1, 2):
                    pairs.append((extra[i], extra[i + 1]))
                return pairs, bye
            rng.shuffle(pool)
            return [(pool[i], pool[i + 1]) for i in range(0, len(pool) - 1, 2)], bye

        order = sorted(pool, key=lambda p: rank_of[p])
        pairs = _pair_backtrack(list(order), played)
        if pairs is not None:
            return pairs, bye
        last_err = True

    raise ValueError(
        "Für diese Runde ist keine Paarung ohne Gegner-Wiederholung mehr möglich. "
        "Bitte das Turnier mit weniger Runden planen.")


def max_rounds(n: int) -> int:
    """Sichere Obergrenze, bis zu der eine Paarung ohne Gegner-Wiederholung stets gelingt.
    (Empirisch abgesichert; die theoretische Grenze liegt höher, kann aber in Sackgassen führen.)
    In der Praxis werden ohnehin deutlich weniger Runden gespielt (~log2 n)."""
    if n < 3:
        return 1
    return max(1, n - 3 if n % 2 == 0 else n - 2)


def suggested_rounds(n: int) -> int:
    """Praxisüblicher Vorschlag: so viele Runden, dass sich ein klarer Sieger herausbildet."""
    r = 1
    while (1 << r) < n:
        r += 1
    return min(max(r, 3), max_rounds(n))
