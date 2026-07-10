"""
Double Elimination (Doppel-KO) – Strukturaufbau für Teilnehmerzahlen 2^k (k>=2).

Erzeugt einen Knotengraphen: jeder Knoten (Match) bezieht seine beiden
Teilnehmer aus Quellen ('slot', s) = Startposition im Winner-Bracket,
('W', node) = Sieger eines Knotens, ('L', node) = Verlierer eines Knotens.

Reine Struktur ohne I/O – dieselbe Logik wird im Worker live ausgewertet.
Knoten-IDs: 'wb:r:i' (Winner-Bracket), 'lb:r:i' (Loser-Bracket),
'gf:0:0' (Grand Final), 'gf:1:0' (Reset-Finale).
"""
from __future__ import annotations
from math import log2


def build(n: int):
    """n = Zweierpotenz (>=4). Rückgabe: (nodes, meta)."""
    k = int(log2(n))
    if 2 ** k != n or k < 2:
        raise ValueError("Double Elimination benötigt eine Teilnehmerzahl 4, 8, 16, …")
    nodes: dict[str, dict] = {}

    def add(nid, home, away):
        nodes[nid] = {"home": home, "away": away}

    # ---- Winner-Bracket ----
    for r in range(k):
        for i in range(n >> (r + 1)):
            if r == 0:
                add(f"wb:0:{i}", ("slot", 2 * i), ("slot", 2 * i + 1))
            else:
                add(f"wb:{r}:{i}", ("W", f"wb:{r-1}:{2*i}"), ("W", f"wb:{r-1}:{2*i+1}"))
    wb_final = f"wb:{k-1}:0"

    # ---- Loser-Bracket ----
    lb_r = 0
    current: list[str] = []
    # Startrunde: Verlierer der WB-Runde 0 paaren
    for i in range(n >> 2):
        add(f"lb:{lb_r}:{i}", ("L", f"wb:0:{2*i}"), ("L", f"wb:0:{2*i+1}"))
        current.append(f"lb:{lb_r}:{i}")
    lb_r += 1
    for r in range(1, k):
        # Major-Runde: LB-Sieger gegen WB-Runde-r-Verlierer (gekreuzt gegen Wiedertreffen)
        losers = [f"wb:{r}:{i}" for i in range(n >> (r + 1))]
        losers_cross = list(reversed(losers))
        newcur = []
        for i in range(len(current)):
            add(f"lb:{lb_r}:{i}", ("W", current[i]), ("L", losers_cross[i]))
            newcur.append(f"lb:{lb_r}:{i}")
        current = newcur
        lb_r += 1
        if len(current) > 1:
            # Minor-Runde: LB-Sieger untereinander
            newcur = []
            for i in range(len(current) // 2):
                add(f"lb:{lb_r}:{i}", ("W", current[2 * i]), ("W", current[2 * i + 1]))
                newcur.append(f"lb:{lb_r}:{i}")
            current = newcur
            lb_r += 1
    lb_final = current[0]

    # ---- Grand Final (+ Reset) ----
    add("gf:0:0", ("W", wb_final), ("W", lb_final))
    add("gf:1:0", ("L", "gf:0:0"), ("W", "gf:0:0"))

    meta = {"n": n, "k": k, "wb_rounds": k, "lb_rounds": lb_r,
            "wb_final": wb_final, "lb_final": lb_final,
            "gf": "gf:0:0", "gf_reset": "gf:1:0"}
    return nodes, meta


BYE = "__BYE__"   # Freilos-Platzhalter: verliert jedes Match, zählt nie als Niederlage


def evaluate(nodes, meta, seeding, decided):
    """Wertet den Graphen aus. seeding-Einträge sind Entry-IDs, BYE (Freilos) oder None.
    Freilose lösen Matches automatisch auf (der reale Teilnehmer steigt kampflos auf)."""
    win, lose, occ = {}, {}, {}

    def is_real(x):
        return x is not None and x != BYE

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
            return  # ein Zubringer steht noch nicht fest
        if h == BYE and a == BYE:
            win[nid] = BYE; lose[nid] = BYE
        elif h == BYE:
            win[nid] = a; lose[nid] = BYE
        elif a == BYE:
            win[nid] = h; lose[nid] = BYE
        else:
            w = decided.get(nid)
            if w is not None:
                win[nid] = w; lose[nid] = a if w == h else h

    def winner_of(nid):
        resolve(nid); return win.get(nid)

    def loser_of(nid):
        resolve(nid); return lose.get(nid)

    for nid in nodes:
        resolve(nid)

    def real(x):
        return x if is_real(x) else None

    x = real(winner_of(meta["wb_final"]))
    y = real(winner_of(meta["lb_final"]))
    g1 = real(winner_of(meta["gf"]))
    champion = runner_up = None
    reset_active = False
    if x is not None and y is not None and g1 is not None:
        if g1 == x:
            champion, runner_up = x, y
        else:
            reset_active = True
            g2 = real(winner_of(meta["gf_reset"]))
            if g2 is not None:
                champion = g2
                runner_up = x if g2 == y else y
    third = real(loser_of(meta["lb_final"]))
    return {"occ": occ, "winner": win, "loser": lose,
            "champion": champion, "runner_up": runner_up, "third": third,
            "reset_active": reset_active}
