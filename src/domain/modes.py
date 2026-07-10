"""
Turniermodi als Registry (Strategy-Pattern, wie im Architekturentwurf).

Phase 1 aktiv über eine gemeinsame Round-Robin-Engine bzw. KO-Engine:
  round_system, poule, group, ko, group_ko
Geplant (Interface vorhanden, noch nicht aktiv):
  swiss, double_ko, full_ko, melee, super_melee
"""
from __future__ import annotations

MODES: dict[str, dict] = {
    "round_system": {"label": "Rundensystem", "kind": "group", "implemented": True,
                     "fixed_groups": 1,
                     "hint": "Jeder gegen jeden in einer Runde."},
    "poule":        {"label": "Poule", "kind": "group", "implemented": True,
                     "hint": "Round-Robin-Pool(s); Gruppenzahl konfigurierbar."},
    "group":        {"label": "Gruppensystem", "kind": "group", "implemented": True,
                     "hint": "Mehrere Gruppen, Rangliste je Gruppe."},
    "ko":           {"label": "KO-System", "kind": "ko", "implemented": True,
                     "hint": "Einfaches K.-o. mit Freilosen."},
    "group_ko":     {"label": "Gruppe + KO", "kind": "group_ko", "implemented": True,
                     "hint": "Gruppenphase, danach K.-o. der Besten."},
    "swiss":        {"label": "Schweizer System", "kind": "swiss", "implemented": False,
                     "hint": "Paarung nach Punktgruppen + Buchholz."},
    "double_ko":    {"label": "Doppel-KO", "kind": "double_ko", "implemented": True,
                     "hint": "Doppelte Ausscheidung: Winner-/Loser-Bracket, Grand Final mit Reset."},
    "full_ko":      {"label": "Vollständiges KO", "kind": "full_ko", "implemented": False,
                     "hint": "Platzierungsspiele für alle Ränge."},
    "melee":        {"label": "Mêlée", "kind": "melee", "implemented": False,
                     "hint": "Zufallsdoppel, individuelle Wertung."},
    "super_melee":  {"label": "Super-Mêlée", "kind": "super_melee", "implemented": False,
                     "hint": "Mêlée mit wechselnden Partnern je Runde."},
}


def mode_kind(mode: str) -> str:
    return MODES[mode]["kind"]


def effective_group_count(mode: str, group_count: int) -> int:
    m = MODES[mode]
    if m.get("fixed_groups"):
        return m["fixed_groups"]
    return max(1, group_count)


def round_robin_schedule(entry_ids: list[int]) -> list[dict]:
    """Kreismethode: gibt [{round, home, away}] zurück (Freilose je Runde ausgelassen)."""
    arr = list(entry_ids)
    if len(arr) % 2 == 1:
        arr.append(None)  # Freilos
    n = len(arr)
    out = []
    for r in range(n - 1):
        for i in range(n // 2):
            a, b = arr[i], arr[n - 1 - i]
            if a is not None and b is not None:
                out.append({"round": r + 1, "home": a, "away": b})
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]  # rotieren, arr[0] fix
    return out
