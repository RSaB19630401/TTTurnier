"""
Auslosung mit Setzung und Vereinsschutz.

- Setzung: Gesetzte werden zuerst platziert (Gruppen: je einer pro Gruppe im
  Schlangensystem; KO: an den Standard-Setzpositionen).
- Vereinsschutz: Spieler desselben Vereins werden möglichst getrennt
  (verschiedene Gruppen bzw. keine Erstrunden-Paarung im KO). Heuristisch über
  mehrere Zufallsdurchläufe; nicht auflösbare Restkonflikte werden gemeldet.
"""
from __future__ import annotations
from dataclasses import dataclass
import random


@dataclass
class DrawEntry:
    id: int
    seeded: bool = False
    club_id: int | None = None
    seed_no: int | None = None   # optionale explizite Setznummer (klein = stark)


def next_pow2(n: int) -> int:
    p = 1
    while p < n:
        p *= 2
    return p


def seed_slots(size: int) -> list[int]:
    """Standard-Setzpositionen eines Baums: Position -> Setznummer (1-basiert)."""
    arr = [1, 2]
    while len(arr) < size:
        s = len(arr) * 2 + 1
        nxt = []
        for x in arr:
            nxt.append(x)
            nxt.append(s - x)
        arr = nxt
    return arr


# ---------------------------------------------------------------- Gruppen ----
def draw_groups(entries: list[DrawEntry], group_count: int,
                rng: random.Random | None = None, tries: int = 300):
    """Verteilt Meldungen auf `group_count` Gruppen. Rückgabe: (groups, conflicts)."""
    rng = rng or random.Random()
    seeded = [e for e in entries if e.seeded]
    unseeded = [e for e in entries if not e.seeded]
    seeded.sort(key=lambda e: (e.seed_no is None, e.seed_no if e.seed_no is not None else 0))

    # Gesetzte im Schlangensystem fix verteilen
    base = [[] for _ in range(group_count)]
    for i, e in enumerate(seeded):
        row = i // group_count
        col = i % group_count
        g = col if row % 2 == 0 else group_count - 1 - col
        base[g].append(e)

    def club_conflicts(groups):
        c = 0
        for g in groups:
            seen = {}
            for e in g:
                if e.club_id is not None:
                    seen[e.club_id] = seen.get(e.club_id, 0) + 1
            c += sum(v - 1 for v in seen.values() if v > 1)
        return c

    best, best_c = None, None
    for _ in range(tries):
        groups = [list(g) for g in base]
        pool = unseeded[:]
        rng.shuffle(pool)
        for e in pool:
            # Kandidaten: Gruppen minimaler Größe
            msize = min(len(g) for g in groups)
            cand = [gi for gi, g in enumerate(groups) if len(g) == msize]
            # bevorzugt ohne Vereinskonflikt
            clean = [gi for gi in cand
                     if e.club_id is None or all(m.club_id != e.club_id for m in groups[gi])]
            choice = rng.choice(clean if clean else cand)
            groups[choice].append(e)
        c = club_conflicts(groups)
        if best_c is None or c < best_c:
            best, best_c = groups, c
            if c == 0:
                break

    result = [[e.id for e in g] for g in best]
    conflicts = _list_group_conflicts(best)
    return result, conflicts


def _list_group_conflicts(groups):
    out = []
    for gi, g in enumerate(groups):
        by_club = {}
        for e in g:
            if e.club_id is not None:
                by_club.setdefault(e.club_id, []).append(e.id)
        for club, ids in by_club.items():
            if len(ids) > 1:
                out.append({"type": "group", "group": gi, "club_id": club, "entries": ids})
    return out


# -------------------------------------------------------------------- KO -----
def draw_ko(entries: list[DrawEntry], rng: random.Random | None = None, tries: int = 400):
    """Erzeugt die Startaufstellung (Slots) eines KO-Baums. Rückgabe: (slots, conflicts).
    slots[i] = entry_id oder None (Freilos). Länge = nächste Zweierpotenz."""
    rng = rng or random.Random()
    seeded = [e for e in entries if e.seeded]
    unseeded = [e for e in entries if not e.seeded]
    seeded.sort(key=lambda e: (e.seed_no is None, e.seed_no if e.seed_no is not None else 0))

    n = len(entries)
    size = next_pow2(max(n, 2))
    order = seed_slots(size)  # position -> Setznummer

    def build(pool_order):
        # Rang 1..k = Gesetzte, dann Ungesetzte, dann Freilose
        ranks = [e.id for e in seeded] + [e.id for e in pool_order] + [None] * (size - n)
        club = {e.id: e.club_id for e in entries}
        slots = [None] * size
        for p in range(size):
            r = order[p]
            slots[p] = ranks[r - 1] if r - 1 < len(ranks) else None
        return slots, club

    def r1_conflicts(slots, club):
        cs = []
        for i in range(0, size, 2):
            a, b = slots[i], slots[i + 1]
            if a is not None and b is not None and club.get(a) is not None and club.get(a) == club.get(b):
                cs.append((i, a, b))
        return cs

    best, best_club, best_c = None, None, None
    for _ in range(tries):
        pool = unseeded[:]
        rng.shuffle(pool)
        slots, club = build(pool)
        c = len(r1_conflicts(slots, club))
        if best_c is None or c < best_c:
            best, best_club, best_c = slots, club, c
            if c == 0:
                break

    conflicts = [{"type": "ko_r1", "slot": i, "entries": [a, b]}
                 for (i, a, b) in r1_conflicts(best, best_club)]
    return best, conflicts
