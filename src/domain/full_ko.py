"""
Vollständiges KO (Platzierungsturnier).

Prinzip: Nach jeder Runde teilt sich eine Gruppe: Die Sieger spielen die obere
Hälfte der Plätze aus, die Verlierer die untere Hälfte. Rekursiv fortgesetzt,
bis jede Gruppe nur noch einen Teilnehmer enthält – dessen Platz steht dann fest.
Jeder Teilnehmer bestreitet exakt log2(S) Spiele (S = Baumgröße).

- `places_to`   begrenzt, bis zu welchem Platz ausgespielt wird. Gruppen, deren
                bester Platz darüber liegt, werden nicht mehr gespielt.
- Freilose      über den BYE-Platzhalter: ein Freilos verliert jedes Spiel und
                sinkt damit ans Ende der Platzierung; die realen Teilnehmer
                belegen die Plätze 1..n lückenlos.

Knoten-ID: 'pk:<startplatz>:<gruppengröße>:<index>'
Quellen:   ('slot', i) | ('W', node) | ('L', node)
"""
from __future__ import annotations
from math import log2

BYE = "__BYE__"


def build(n: int, places_to: int | None = None):
    """n = Zweierpotenz (>=2). Rückgabe: (nodes, places, meta)
    places: dict Platz -> Quelle (aufgelöst über evaluate)."""
    k = int(log2(n))
    if 2 ** k != n or k < 1:
        raise ValueError("Vollständiges KO benötigt eine Baumgröße 2, 4, 8, 16, …")
    P = n if places_to is None else max(2, min(places_to, n))
    nodes: dict[str, dict] = {}
    places: dict[int, tuple] = {}

    def group(start: int, srcs: list):
        m = len(srcs)
        if m == 1:
            places[start] = srcs[0]
            return
        if start > P:                      # dieser Platzbereich wird nicht ausgespielt
            return
        winners, losers = [], []
        for i in range(m // 2):
            nid = f"pk:{start}:{m}:{i}"
            nodes[nid] = {"home": srcs[2 * i], "away": srcs[2 * i + 1]}
            winners.append(("W", nid))
            losers.append(("L", nid))
        group(start, winners)              # Sieger: obere Plätze
        group(start + m // 2, losers)      # Verlierer: untere Plätze

    group(1, [("slot", i) for i in range(n)])
    meta = {"n": n, "k": k, "places_to": P}
    return nodes, places, meta


def evaluate(nodes, places, seeding, decided):
    """seeding: Entry-IDs je Startslot (oder BYE). decided: node_id -> Sieger-Entry-ID."""
    win, lose, occ = {}, {}, {}

    def source(src):
        t, ref = src
        if t == "slot":
            return seeding[ref] if ref < len(seeding) else None
        return winner_of(ref) if t == "W" else loser_of(ref)

    def resolve(nid):
        if nid in occ:
            return
        h = source(nodes[nid]["home"])
        a = source(nodes[nid]["away"])
        occ[nid] = (h, a)
        if h is None or a is None:
            return
        if h == BYE and a == BYE:
            win[nid] = BYE; lose[nid] = BYE
        elif h == BYE:
            win[nid] = a; lose[nid] = BYE
        elif a == BYE:
            win[nid] = h; lose[nid] = BYE
        else:
            w = decided.get(nid)
            if w is not None:
                win[nid] = w
                lose[nid] = a if w == h else h

    def winner_of(nid):
        resolve(nid); return win.get(nid)

    def loser_of(nid):
        resolve(nid); return lose.get(nid)

    for nid in nodes:
        resolve(nid)

    table = {}
    for place, src in places.items():
        v = source(src)
        if v is not None and v != BYE:
            table[place] = v
    return {"occ": occ, "winner": win, "loser": lose, "places": table}
