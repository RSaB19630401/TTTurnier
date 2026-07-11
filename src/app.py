"""TT-Turnier – FastAPI-Anwendung (läuft im Cloudflare Python Worker, Persistenz über D1)."""
from __future__ import annotations
from math import log2
import random

from fastapi import FastAPI, Request, Depends, HTTPException
from pydantic import BaseModel

from db import Database, D1DB
from domain import ranking as R
from domain import draw as D
from domain import double_elim as DE
from domain import super_melee as SM
from domain import swiss as SW
from domain.modes import MODES, mode_kind, effective_group_count, round_robin_schedule

app = FastAPI(title="TT-Turnier")


# DB-Abhängigkeit: im Worker aus der D1-Binding (env.DB); in Tests überschrieben.
async def get_db(request: Request) -> Database:
    env = request.scope.get("env")
    if env is None:
        raise HTTPException(500, "Keine Umgebung/DB-Binding verfügbar.")
    return D1DB(env.DB)


# ------------------------------------------------------------- Hilfsfunktionen -
def outcome(score_mode, walkover, sets, home_id, away_id, sets_to_win, points_per_set):
    if walkover in ("home", "away"):
        hw = walkover == "home"
        return {"complete": True, "walkover": True,
                "winner": home_id if hw else away_id,
                "hs": sets_to_win if hw else 0, "as": 0 if hw else sets_to_win,
                "hp": (sets_to_win * points_per_set if hw else 0) if score_mode == "points" else 0,
                "ap": (0 if hw else sets_to_win * points_per_set) if score_mode == "points" else 0}
    if score_mode == "sets":
        # In diesem Modus enthält `sets` genau einen Eintrag [Sätze_Heim, Sätze_Gast].
        hs = as_ = 0
        if sets:
            hs, as_ = int(sets[0]["home_points"]), int(sets[0]["away_points"])
        complete = hs >= sets_to_win or as_ >= sets_to_win
        winner = home_id if hs >= sets_to_win else (away_id if as_ >= sets_to_win else None)
        # Keine Ballpunkte -> hp/ap = 0 (Ballpunktquotient entfällt, es folgt das Los).
        return {"complete": complete, "walkover": False, "winner": winner,
                "hs": hs, "as": as_, "hp": 0, "ap": 0}
    hs = as_ = hp = ap = 0
    for s in sets:
        h, a = s["home_points"], s["away_points"]
        if h == 0 and a == 0:
            continue
        hp += h; ap += a
        if h > a: hs += 1
        elif a > h: as_ += 1
    complete = hs >= sets_to_win or as_ >= sets_to_win
    winner = home_id if hs >= sets_to_win else (away_id if as_ >= sets_to_win else None)
    return {"complete": complete, "walkover": False, "winner": winner,
            "hs": hs, "as": as_, "hp": hp, "ap": ap}


async def get_comp(db, cid) -> dict:
    c = await db.query_one("SELECT * FROM competitions WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404, "Wettbewerb nicht gefunden")
    return c


async def load_entries(db, cid) -> list[dict]:
    rows = await db.query(
        """SELECT e.id, e.seeded, e.seed_no, e.group_no, e.bracket_slot,
                  COALESCE(GROUP_CONCAT(p.first_name || ' ' || p.last_name, ' / '), 'Meldung ' || e.id) AS name,
                  MIN(p.club_id) AS club_id
           FROM entries e
           LEFT JOIN entry_members m ON m.entry_id = e.id
           LEFT JOIN players p ON p.id = m.player_id
           WHERE e.competition_id = ?
           GROUP BY e.id ORDER BY e.id""", (cid,))
    for r in rows:
        r["seeded"] = bool(r["seeded"])
    return rows


async def load_matches(db, cid) -> list[dict]:
    ms = await db.query("SELECT * FROM matches WHERE competition_id=? ORDER BY id", (cid,))
    sets = await db.query(
        """SELECT s.* FROM match_sets s JOIN matches m ON m.id=s.match_id
           WHERE m.competition_id=? ORDER BY s.match_id, s.set_no""", (cid,))
    by = {}
    for s in sets:
        by.setdefault(s["match_id"], []).append(s)
    for m in ms:
        m["sets"] = by.get(m["id"], [])
    return ms


def comp_dto(c: dict, entries=None):
    d = {"id": c["id"], "name": c["name"], "mode": c["mode"],
         "mode_label": MODES[c["mode"]]["label"], "kind": mode_kind(c["mode"]),
         "sets_to_win": c["sets_to_win"], "points_per_set": c["points_per_set"],
         "group_count": effective_group_count(c["mode"], c["group_count"]),
         "advance_per_group": c["advance_per_group"], "ko_sets_to_win": c["ko_sets_to_win"],
         "status": c["status"], "score_mode": c["score_mode"],
         "competition_type": c["competition_type"], "pairing": c["pairing"],
         "round_count": c["round_count"]}
    if entries is not None:
        d["entries"] = [{"id": e["id"], "seeded": e["seeded"], "seed_no": e["seed_no"],
                         "group_no": e["group_no"], "bracket_slot": e["bracket_slot"],
                         "name": e["name"], "club_id": e["club_id"]} for e in entries]
    return d


# ==================================================================== CLUBS ====
class ClubIn(BaseModel):
    prefix: str = ""; name: str; contact: str = ""; remark: str = ""


@app.get("/api/clubs")
async def list_clubs(db=Depends(get_db)):
    return await db.query("SELECT * FROM clubs ORDER BY name")


@app.post("/api/clubs")
async def create_club(body: ClubIn, db=Depends(get_db)):
    cid = await db.execute("INSERT INTO clubs (prefix,name,contact,remark) VALUES (?,?,?,?)",
                           (body.prefix, body.name, body.contact, body.remark))
    return await db.query_one("SELECT * FROM clubs WHERE id=?", (cid,))


@app.put("/api/clubs/{cid}")
async def update_club(cid: int, body: ClubIn, db=Depends(get_db)):
    await db.execute("UPDATE clubs SET prefix=?,name=?,contact=?,remark=? WHERE id=?",
                     (body.prefix, body.name, body.contact, body.remark, cid))
    return await db.query_one("SELECT * FROM clubs WHERE id=?", (cid,))


@app.delete("/api/clubs/{cid}")
async def delete_club(cid: int, db=Depends(get_db)):
    n = await db.query_one("SELECT COUNT(*) c FROM players WHERE club_id=?", (cid,))
    if n and n["c"] > 0:
        raise HTTPException(400, "Verein hat noch Spieler.")
    await db.execute("DELETE FROM clubs WHERE id=?", (cid,))
    return {"ok": True}


# =================================================================== PLAYERS ===
class PlayerIn(BaseModel):
    first_name: str; last_name: str; sex: str = ""; club_id: int | None = None; remark: str = ""


@app.get("/api/players")
async def list_players(db=Depends(get_db)):
    rows = await db.query(
        """SELECT p.*, c.name AS club, (p.first_name||' '||p.last_name) AS name
           FROM players p LEFT JOIN clubs c ON c.id=p.club_id ORDER BY p.last_name, p.first_name""")
    return rows


@app.post("/api/players")
async def create_player(body: PlayerIn, db=Depends(get_db)):
    pid = await db.execute(
        "INSERT INTO players (club_id,first_name,last_name,sex,remark) VALUES (?,?,?,?,?)",
        (body.club_id, body.first_name, body.last_name, body.sex, body.remark))
    return await db.query_one("SELECT * FROM players WHERE id=?", (pid,))


@app.put("/api/players/{pid}")
async def update_player(pid: int, body: PlayerIn, db=Depends(get_db)):
    await db.execute(
        "UPDATE players SET club_id=?,first_name=?,last_name=?,sex=?,remark=? WHERE id=?",
        (body.club_id, body.first_name, body.last_name, body.sex, body.remark, pid))
    return await db.query_one("SELECT * FROM players WHERE id=?", (pid,))


@app.delete("/api/players/{pid}")
async def delete_player(pid: int, db=Depends(get_db)):
    await db.execute("DELETE FROM players WHERE id=?", (pid,))
    return {"ok": True}


# =============================================================== COMPETITIONS ===
class CompIn(BaseModel):
    name: str; mode: str
    sets_to_win: int = 3; points_per_set: int = 11
    group_count: int = 1; advance_per_group: int = 2; ko_sets_to_win: int = 3
    score_mode: str = "points"
    competition_type: str = "single"     # single | double
    pairing: str | None = None           # bei double: fixed | draw
    round_count: int = 0                  # Super-Mêlée: Anzahl Runden


@app.get("/api/modes")
async def list_modes():
    return [{"key": k, **v} for k, v in MODES.items()]


@app.get("/api/competitions")
async def list_comps(db=Depends(get_db)):
    rows = await db.query("SELECT * FROM competitions ORDER BY id DESC")
    return [comp_dto(c) for c in rows]


@app.post("/api/competitions")
async def create_comp(body: CompIn, db=Depends(get_db)):
    if body.mode not in MODES:
        raise HTTPException(400, "Unbekannter Modus")
    if not MODES[body.mode]["implemented"]:
        raise HTTPException(400, f"Modus „{MODES[body.mode]['label']}“ ist geplant, aber noch nicht aktiv.")
    if body.score_mode not in ("points", "sets"):
        raise HTTPException(400, "Ungültiger Wertungsmodus.")
    ctype = body.competition_type if body.competition_type in ("single", "double") else "single"
    pairing = None
    round_count = 0
    if body.mode == "super_melee":
        # Super-Mêlée ist ein Doppelwettbewerb, aber ohne feste Paare:
        # gemeldet werden einzelne Spieler, die Partner wechseln jede Runde.
        ctype = "double"
        pairing = None
        round_count = int(body.round_count)
        if round_count < 1:
            raise HTTPException(400, "Bitte die Anzahl der Runden angeben (mindestens 1).")
    elif body.mode == "swiss":
        ctype = "single"
        round_count = int(body.round_count)
        if round_count < 1:
            raise HTTPException(400, "Bitte die Anzahl der Runden angeben (mindestens 1).")
        pairing = body.pairing if body.pairing in ("random", "seeded") else "random"
    elif ctype == "double":
        if body.mode not in ("ko", "double_ko"):
            raise HTTPException(400, "Doppel ist als Einfach-KO, Doppel-KO oder Super-Mêlée verfügbar.")
        pairing = body.pairing if body.pairing in ("fixed", "draw") else None
        if pairing is None:
            raise HTTPException(400, "Bitte ein Paarbildungs-Verfahren wählen (feste Paare oder auslosen).")
    cid = await db.execute(
        """INSERT INTO competitions (name,mode,sets_to_win,points_per_set,group_count,advance_per_group,ko_sets_to_win,status,score_mode,competition_type,pairing,round_count)
           VALUES (?,?,?,?,?,?,?, 'setup', ?,?,?,?)""",
        (body.name, body.mode, body.sets_to_win, body.points_per_set,
         body.group_count, body.advance_per_group, body.ko_sets_to_win, body.score_mode, ctype, pairing, round_count))
    c = await get_comp(db, cid)
    return comp_dto(c, entries=[])


@app.get("/api/competitions/{cid}")
async def get_competition(cid: int, db=Depends(get_db)):
    c = await get_comp(db, cid)
    return comp_dto(c, entries=await load_entries(db, cid))


@app.delete("/api/competitions/{cid}")
async def delete_comp(cid: int, db=Depends(get_db)):
    await db.execute("DELETE FROM match_sets WHERE match_id IN (SELECT id FROM matches WHERE competition_id=?)", (cid,))
    await db.execute("DELETE FROM matches WHERE competition_id=?", (cid,))
    await db.execute("DELETE FROM entry_members WHERE entry_id IN (SELECT id FROM entries WHERE competition_id=?)", (cid,))
    await db.execute("DELETE FROM entries WHERE competition_id=?", (cid,))
    await db.execute("DELETE FROM competitions WHERE id=?", (cid,))
    return {"ok": True}


# ---- Teilnehmer & Setzung -----------------------------------------------------
class ParticipantsIn(BaseModel):
    player_ids: list[int]
    seeded_ids: list[int] = []


@app.put("/api/competitions/{cid}/entries")
async def set_participants(cid: int, body: ParticipantsIn, db=Depends(get_db)):
    c = await get_comp(db, cid)
    if c["status"] != "setup":
        raise HTTPException(400, "Teilnehmer sind nach der Auslosung fixiert.")
    await db.execute("DELETE FROM entry_members WHERE entry_id IN (SELECT id FROM entries WHERE competition_id=?)", (cid,))
    await db.execute("DELETE FROM entries WHERE competition_id=?", (cid,))
    seedset = set(body.seeded_ids)
    for pid in body.player_ids:
        p = await db.query_one("SELECT id FROM players WHERE id=?", (pid,))
        if not p:
            continue
        eid = await db.execute("INSERT INTO entries (competition_id,seeded) VALUES (?,?)",
                               (cid, 1 if pid in seedset else 0))
        await db.execute("INSERT INTO entry_members (entry_id,player_id,order_index) VALUES (?,?,0)", (eid, pid))
    return comp_dto(c, entries=await load_entries(db, cid))


# ---- Doppel: Paarbildung ------------------------------------------------------
async def _delete_entries(db, cid):
    await db.execute("DELETE FROM entry_members WHERE entry_id IN (SELECT id FROM entries WHERE competition_id=?)", (cid,))
    await db.execute("DELETE FROM entries WHERE competition_id=?", (cid,))


class PairsIn(BaseModel):
    pairs: list[list[int]]        # [[player1, player2], ...]
    seeded: list[int] = []        # Indizes gesetzter Paare


@app.put("/api/competitions/{cid}/pairs")
async def set_pairs(cid: int, body: PairsIn, db=Depends(get_db)):
    c = await get_comp(db, cid)
    if c["competition_type"] != "double" or c["pairing"] != "fixed":
        raise HTTPException(400, "Nur für Doppel mit festen Paaren.")
    if c["status"] != "setup":
        raise HTTPException(400, "Paare sind nach der Auslosung fixiert.")
    seen = set()
    for pr in body.pairs:
        if len(pr) != 2 or pr[0] == pr[1]:
            raise HTTPException(400, "Jedes Doppel braucht genau zwei verschiedene Spieler.")
        for pid in pr:
            if pid in seen:
                raise HTTPException(400, "Ein Spieler darf nur in einem Doppel stehen.")
            seen.add(pid)
    await _delete_entries(db, cid)
    for idx, pr in enumerate(body.pairs):
        eid = await db.execute("INSERT INTO entries (competition_id,seeded) VALUES (?,?)",
                               (cid, 1 if idx in set(body.seeded) else 0))
        for oi, pid in enumerate(pr):
            await db.execute("INSERT INTO entry_members (entry_id,player_id,order_index) VALUES (?,?,?)", (eid, pid, oi))
    return comp_dto(c, entries=await load_entries(db, cid))


@app.post("/api/competitions/{cid}/draw_partners")
async def draw_partners(cid: int, seed: int | None = None, db=Depends(get_db)):
    c = await get_comp(db, cid)
    if c["competition_type"] != "double" or c["pairing"] != "draw":
        raise HTTPException(400, "Nur für Doppel mit Partnerauslosung.")
    if c["status"] != "setup":
        raise HTTPException(400, "Nach der Auslosung nicht mehr möglich.")
    rows = await db.query(
        """SELECT e.id eid, e.seeded, COUNT(m.id) cnt, MIN(m.player_id) pid
           FROM entries e LEFT JOIN entry_members m ON m.entry_id=e.id
           WHERE e.competition_id=? GROUP BY e.id ORDER BY e.id""", (cid,))
    if any(r["cnt"] != 1 for r in rows):
        raise HTTPException(400, "Bitte zuerst die Teilnehmer wählen (Partner werden erst danach ausgelost).")
    seeded = [r["pid"] for r in rows if r["seeded"]]
    unseeded = [r["pid"] for r in rows if not r["seeded"]]
    if len(seeded) == 0 or len(seeded) != len(unseeded):
        raise HTTPException(400,
            f"Für die Partnerauslosung müssen gleich viele gesetzte und ungesetzte Spieler vorhanden sein "
            f"(aktuell {len(seeded)} gesetzt, {len(unseeded)} ungesetzt). Bitte Teilnehmer oder Setzung anpassen.")
    rng = random.Random(seed)
    rng.shuffle(unseeded)
    await _delete_entries(db, cid)
    for s_pid, u_pid in zip(seeded, unseeded):
        eid = await db.execute("INSERT INTO entries (competition_id,seeded) VALUES (?,0)", (cid,))
        await db.execute("INSERT INTO entry_members (entry_id,player_id,order_index) VALUES (?,?,0)", (eid, s_pid))
        await db.execute("INSERT INTO entry_members (entry_id,player_id,order_index) VALUES (?,?,1)", (eid, u_pid))
    return {"ok": True, "competition": comp_dto(c, entries=await load_entries(db, cid))}


class SeedingIn(BaseModel):
    seeded_entry_ids: list[int] = []


@app.put("/api/competitions/{cid}/seeding")
async def set_seeding(cid: int, body: SeedingIn, db=Depends(get_db)):
    c = await get_comp(db, cid)
    if c["status"] != "setup":
        raise HTTPException(400, "Setzung ist nach der Auslosung fixiert.")
    ids = set(body.seeded_entry_ids)
    rows = await db.query("SELECT id FROM entries WHERE competition_id=?", (cid,))
    for r in rows:
        await db.execute("UPDATE entries SET seeded=? WHERE id=?", (1 if r["id"] in ids else 0, r["id"]))
    return comp_dto(c, entries=await load_entries(db, cid))


# ---- Auslosung ----------------------------------------------------------------
def _draw_entries(entries: list[dict], doubles: bool = False) -> list[D.DrawEntry]:
    # Bei Doppel entfällt der Vereinsschutz -> club_id = None
    return [D.DrawEntry(id=e["id"], seeded=e["seeded"],
                        club_id=None if doubles else e["club_id"], seed_no=e["seed_no"])
            for e in entries]


async def _insert_ko_skeleton(db, cid, slots):
    size = len(slots)
    rounds = int(log2(size))
    for r in range(rounds):
        for i in range(size // (2 ** (r + 1))):
            home = slots[2 * i] if r == 0 else None
            away = slots[2 * i + 1] if r == 0 else None
            await db.execute(
                """INSERT INTO matches (competition_id,phase,bracket_round,bracket_index,home_entry_id,away_entry_id)
                   VALUES (?,?,?,?,?,?)""", (cid, "ko", r, i, home, away))


@app.post("/api/competitions/{cid}/draw")
async def draw(cid: int, seed: int | None = None, db=Depends(get_db)):
    c = await get_comp(db, cid)
    if c["status"] != "setup":
        raise HTTPException(400, "Auslosung wurde bereits durchgeführt.")
    entries = await load_entries(db, cid)
    if len(entries) < 2:
        raise HTTPException(400, "Mindestens 2 Meldungen nötig.")
    doubles = c["competition_type"] == "double"
    if doubles:
        counts = await db.query(
            """SELECT e.id, COUNT(m.id) cnt FROM entries e
               LEFT JOIN entry_members m ON m.entry_id=e.id
               WHERE e.competition_id=? GROUP BY e.id""", (cid,))
        if any(r["cnt"] != 2 for r in counts):
            raise HTTPException(400, "Doppel unvollständig: Bitte zuerst Paare bilden bzw. Partner auslosen.")
    rng = random.Random(seed)
    kind = mode_kind(c["mode"])
    conflicts = []

    if kind in ("group", "group_ko"):
        gc = effective_group_count(c["mode"], c["group_count"])
        if gc > len(entries):
            raise HTTPException(400, "Mehr Gruppen als Meldungen.")
        groups, conflicts = D.draw_groups(_draw_entries(entries, doubles), gc, rng)
        for gi, ids in enumerate(groups):
            for eid in ids:
                await db.execute("UPDATE entries SET group_no=? WHERE id=?", (gi, eid))
            for m in round_robin_schedule(ids):
                await db.execute(
                    """INSERT INTO matches (competition_id,phase,round_no,group_no,home_entry_id,away_entry_id)
                       VALUES (?,?,?,?,?,?)""", (cid, "group", m["round"], gi, m["home"], m["away"]))
    elif kind == "ko":
        slots, conflicts = D.draw_ko(_draw_entries(entries, doubles), rng)
        for pos, eid in enumerate(slots):
            if eid is not None:
                await db.execute("UPDATE entries SET bracket_slot=? WHERE id=?", (pos, eid))
        await _insert_ko_skeleton(db, cid, slots)
    elif kind == "double_ko":
        n = len(entries)
        if n < 4 or n > 64:
            raise HTTPException(400, "Doppel-KO ist für 4 bis 64 Teilnehmer möglich.")
        slots, _ = D.draw_ko(_draw_entries(entries, True), rng)  # Länge = nächste Zweierpotenz, Freilose = None
        for pos, eid in enumerate(slots):
            if eid is not None:
                await db.execute("UPDATE entries SET bracket_slot=? WHERE id=?", (pos, eid))
        S = len(slots)
        nodes, meta = DE.build(S)
        for nid in nodes:
            phase, r, i = nid.split(":")
            home = away = None
            if nid.startswith("wb:0:"):
                home, away = slots[2 * int(i)], slots[2 * int(i) + 1]
            await db.execute(
                """INSERT INTO matches (competition_id,phase,bracket_round,bracket_index,home_entry_id,away_entry_id)
                   VALUES (?,?,?,?,?,?)""", (cid, phase, int(r), int(i), home, away))
    else:
        raise HTTPException(400, "Für diesen Modus ist die Auslosung noch nicht aktiv.")

    await db.execute("UPDATE competitions SET status='drawn' WHERE id=?", (cid,))
    c = await get_comp(db, cid)
    return {"ok": True, "conflicts": conflicts, "competition": comp_dto(c, entries=await load_entries(db, cid))}


# ---- Ergebnisse ---------------------------------------------------------------
class ResultIn(BaseModel):
    sets: list[list[int]] | None = None
    walkover: str | None = None


async def _ko_state(db, c):
    """Leitet Teilnehmer/Sieger aller KO-Knoten live aus den Ergebnissen ab."""
    ms = await load_matches(db, c["id"])
    ko = [m for m in ms if m["phase"] == "ko"]
    if not ko:
        return {"occ": {}, "winners": {}, "rounds": 0, "by": {}}
    by = {(m["bracket_round"], m["bracket_index"]): m for m in ko}
    rounds = max(m["bracket_round"] for m in ko) + 1
    stw, pps = c["ko_sets_to_win"], c["points_per_set"]

    def node_entries(r, i):
        m = by[(r, i)]
        if r == 0:
            return [x for x in (m["home_entry_id"], m["away_entry_id"]) if x]
        return node_entries(r - 1, 2 * i) + node_entries(r - 1, 2 * i + 1)

    def winner(r, i):
        ents = node_entries(r, i)
        if len(ents) <= 1:
            return ents[0] if ents else None
        if r == 0:
            h, a = by[(r, i)]["home_entry_id"], by[(r, i)]["away_entry_id"]
        else:
            h, a = winner(r - 1, 2 * i), winner(r - 1, 2 * i + 1)
        if h is None or a is None:
            return None
        o = outcome(c["score_mode"], by[(r, i)]["walkover"], by[(r, i)]["sets"], h, a, stw, pps)
        return o["winner"] if o["complete"] else None

    occ, winners = {}, {}
    for (r, i) in by:
        if r == 0:
            occ[(r, i)] = (by[(r, i)]["home_entry_id"], by[(r, i)]["away_entry_id"])
        else:
            occ[(r, i)] = (winner(r - 1, 2 * i), winner(r - 1, 2 * i + 1))
        winners[(r, i)] = winner(r, i)
    return {"occ": occ, "winners": winners, "rounds": rounds, "by": by}


async def _de_state(db, c):
    """Live-Auswertung eines Doppel-KO aus den gespeicherten Ergebnissen."""
    ms = await load_matches(db, c["id"])
    de = [m for m in ms if m["phase"] in ("wb", "lb", "gf")]
    by = {f'{m["phase"]}:{m["bracket_round"]}:{m["bracket_index"]}': m for m in de}
    wb0 = [m for m in de if m["phase"] == "wb" and m["bracket_round"] == 0]
    n = len(wb0) * 2
    if n < 4:
        return None
    nodes, meta = DE.build(n)
    seeding = [None] * n
    for m in wb0:
        i = m["bracket_index"]
        # None auf einem WB-Startplatz bedeutet Freilos (BYE), nicht „unbekannt"
        seeding[2 * i] = m["home_entry_id"] if m["home_entry_id"] is not None else DE.BYE
        seeding[2 * i + 1] = m["away_entry_id"] if m["away_entry_id"] is not None else DE.BYE

    def row_winner(row):
        h, a = row["home_entry_id"], row["away_entry_id"]
        if h is None or a is None:
            return None
        o = outcome(c["score_mode"], row["walkover"], row["sets"], h, a,
                    c["ko_sets_to_win"], c["points_per_set"])
        return o["winner"] if o["complete"] else None

    decided = {}
    for nid, row in by.items():
        w = row_winner(row)
        if w is not None:
            decided[nid] = w
    st = DE.evaluate(nodes, meta, seeding, decided)
    st.update({"nodes": nodes, "meta": meta, "by": by, "seeding": seeding})
    return st


@app.post("/api/matches/{mid}/result")
async def post_result(mid: int, body: ResultIn, db=Depends(get_db)):
    m = await db.query_one("SELECT * FROM matches WHERE id=?", (mid,))
    if not m:
        raise HTTPException(404, "Spiel nicht gefunden")
    c = await get_comp(db, m["competition_id"])

    if m["phase"] == "ko" and (m["home_entry_id"] is None or m["away_entry_id"] is None):
        st = await _ko_state(db, c)
        h, a = st["occ"].get((m["bracket_round"], m["bracket_index"]), (None, None))
        if h is None or a is None:
            raise HTTPException(400, "Beide Teilnehmer stehen noch nicht fest.")
        await db.execute("UPDATE matches SET home_entry_id=?, away_entry_id=? WHERE id=?", (h, a, mid))
        m["home_entry_id"], m["away_entry_id"] = h, a

    if m["phase"] in ("wb", "lb", "gf"):
        st = await _de_state(db, c)
        nid = f'{m["phase"]}:{m["bracket_round"]}:{m["bracket_index"]}'
        if m["phase"] == "gf" and m["bracket_round"] == 1 and not st["reset_active"]:
            raise HTTPException(400, "Das Reset-Finale ist nicht nötig – der Sieger steht bereits fest.")
        h, a = st["occ"].get(nid, (None, None))
        if h == DE.BYE or a == DE.BYE:
            raise HTTPException(400, "Freilos – hier wird kein Ergebnis erfasst.")
        if h is None or a is None:
            raise HTTPException(400, "Beide Teilnehmer stehen noch nicht fest.")
        await db.execute("UPDATE matches SET home_entry_id=?, away_entry_id=? WHERE id=?", (h, a, mid))
        m["home_entry_id"], m["away_entry_id"] = h, a

    await db.execute("DELETE FROM match_sets WHERE match_id=?", (mid,))
    walkover = body.walkover if body.walkover in ("home", "away") else None
    stw = c["sets_to_win"] if m["phase"] in ("group", "melee", "swiss") else c["ko_sets_to_win"]
    if not walkover and body.sets:
        if c["score_mode"] == "sets":
            # Genau ein Satzergebnis erwartet: [Sätze_Heim, Sätze_Gast]
            hs, as_ = int(body.sets[0][0]), int(body.sets[0][1])
            win, lose = max(hs, as_), min(hs, as_)
            if win != stw or lose < 0 or lose >= stw or hs == as_:
                raise HTTPException(400,
                    f"Ungültiges Satzergebnis: Sieger muss genau {stw} Sätze haben, Verlierer 0 bis {stw - 1}.")
            await db.execute("INSERT INTO match_sets (match_id,set_no,home_points,away_points) VALUES (?,?,?,?)",
                             (mid, 1, hs, as_))
        else:
            for i, (h, a) in enumerate(body.sets):
                await db.execute("INSERT INTO match_sets (match_id,set_no,home_points,away_points) VALUES (?,?,?,?)",
                                 (mid, i + 1, int(h), int(a)))
    sets = await db.query("SELECT * FROM match_sets WHERE match_id=? ORDER BY set_no", (mid,))
    o = outcome(c["score_mode"], walkover, sets, m["home_entry_id"], m["away_entry_id"], stw, c["points_per_set"])
    await db.execute("UPDATE matches SET walkover=?, status=? WHERE id=?",
                     (walkover, "done" if o["complete"] else "pending", mid))
    return {"ok": True, "complete": o["complete"], "winner": o["winner"]}


# ---- Spielplan (Gruppen) ------------------------------------------------------
@app.get("/api/competitions/{cid}/matches")
async def list_group_matches(cid: int, db=Depends(get_db)):
    c = await get_comp(db, cid)
    entries = {e["id"]: e for e in await load_entries(db, cid)}
    ms = await load_matches(db, cid)
    out = []
    for m in ms:
        if m["phase"] != "group":
            continue
        o = outcome(c["score_mode"], m["walkover"], m["sets"], m["home_entry_id"], m["away_entry_id"],
                    c["sets_to_win"], c["points_per_set"])
        out.append({"id": m["id"], "round_no": m["round_no"], "group_no": m["group_no"],
                    "home": m["home_entry_id"], "away": m["away_entry_id"],
                    "home_name": entries.get(m["home_entry_id"], {}).get("name"),
                    "away_name": entries.get(m["away_entry_id"], {}).get("name"),
                    "sets": [[s["home_points"], s["away_points"]] for s in m["sets"]],
                    "walkover": m["walkover"], "complete": o["complete"], "winner": o["winner"]})
    return out


# ---- Rangliste ----------------------------------------------------------------
@app.get("/api/competitions/{cid}/ranking")
async def ranking(cid: int, db=Depends(get_db)):
    c = await get_comp(db, cid)
    entries = {e["id"]: e for e in await load_entries(db, cid)}
    ms = await load_matches(db, cid)
    gc = effective_group_count(c["mode"], c["group_count"])
    groups_out = []
    for gi in range(gc):
        ids = [eid for eid, e in entries.items() if e["group_no"] == gi]
        results = []
        for m in ms:
            if m["phase"] != "group" or m["group_no"] != gi:
                continue
            o = outcome(c["score_mode"], m["walkover"], m["sets"], m["home_entry_id"], m["away_entry_id"],
                        c["sets_to_win"], c["points_per_set"])
            if o["complete"]:
                results.append(R.MatchResult(m["home_entry_id"], m["away_entry_id"],
                                             o["hs"], o["as"], o["hp"], o["ap"], o["walkover"]))
        rr = R.rank_group(ids, results)
        rows = []
        for rank, eid in enumerate(rr.order):
            s = rr.stats[eid]
            rows.append({"rank": rank + 1, "entry": eid, "name": entries[eid]["name"],
                         "club_id": entries[eid]["club_id"], "mp": s.mp, "played": s.played,
                         "wins": s.wins, "gw": s.gw, "gl": s.gl, "pw": s.pw, "pl": s.pl,
                         "set_ratio": None if s.set_ratio == float("inf") else round(s.set_ratio, 3),
                         "point_ratio": None if s.point_ratio == float("inf") else round(s.point_ratio, 3),
                         "advances": rank < c["advance_per_group"]})
        groups_out.append({"group": gi, "rows": rows, "tie_break_used": rr.tie_break_used,
                           "unresolved": rr.unresolved})
    return {"groups": groups_out}


# ---- KO-Baum ------------------------------------------------------------------
@app.get("/api/competitions/{cid}/bracket")
async def bracket(cid: int, db=Depends(get_db)):
    c = await get_comp(db, cid)
    entries = {e["id"]: e for e in await load_entries(db, cid)}
    st = await _ko_state(db, c)
    if st["rounds"] == 0:
        return {"rounds": 0, "matches": []}
    occ, winners, rounds, by = st["occ"], st["winners"], st["rounds"], st["by"]
    out = []
    for (r, i), m in by.items():
        h, a = occ[(r, i)]
        w = winners[(r, i)]
        out.append({"id": m["id"], "round": r, "index": i, "home": h, "away": a,
                    "home_name": entries.get(h, {}).get("name"), "away_name": entries.get(a, {}).get("name"),
                    "winner": w, "winner_name": entries.get(w, {}).get("name"),
                    "bye": (h is not None) ^ (a is not None),
                    "sets": [[s["home_points"], s["away_points"]] for s in m["sets"]],
                    "walkover": m["walkover"]})
    champ = winners.get((rounds - 1, 0))
    return {"rounds": rounds, "matches": out, "champion": champ,
            "champion_name": entries.get(champ, {}).get("name")}


@app.get("/api/competitions/{cid}/double_bracket")
async def double_bracket(cid: int, db=Depends(get_db)):
    c = await get_comp(db, cid)
    if mode_kind(c["mode"]) != "double_ko":
        raise HTTPException(400, "Nur für Doppel-KO verfügbar.")
    names = {e["id"]: e["name"] for e in await load_entries(db, cid)}
    st = await _de_state(db, c)
    if not st:
        return {"ready": False, "matches": []}
    nodes, by, occ, winner = st["nodes"], st["by"], st["occ"], st["winner"]
    out = []
    for nid in nodes:
        phase, r, i = nid.split(":")
        row = by.get(nid)
        h, a = occ.get(nid, (None, None))
        w = winner.get(nid)
        bye = (h == DE.BYE) or (a == DE.BYE)
        hh = None if (h is None or h == DE.BYE) else h
        aa = None if (a is None or a == DE.BYE) else a
        ww = None if (w is None or w == DE.BYE) else w
        out.append({"node": nid, "phase": phase, "round": int(r), "index": int(i),
                    "id": row["id"] if row else None,
                    "home": hh, "away": aa, "home_name": names.get(hh), "away_name": names.get(aa),
                    "winner": ww, "winner_name": names.get(ww), "bye": bye,
                    "sets": [[s["home_points"], s["away_points"]] for s in (row["sets"] if row else [])],
                    "walkover": row["walkover"] if row else None,
                    "reset": nid == "gf:1:0"})
    return {"ready": True,
            "wb_rounds": st["meta"]["wb_rounds"], "lb_rounds": st["meta"]["lb_rounds"],
            "reset_active": st["reset_active"],
            "champion": st["champion"], "champion_name": names.get(st["champion"]),
            "runner_up": st["runner_up"], "runner_up_name": names.get(st["runner_up"]),
            "third": st["third"], "third_name": names.get(st["third"]),
            "matches": out}


# ---- Super-Mêlée --------------------------------------------------------------
async def _melee_participants(db, cid):
    rows = await db.query(
        """SELECT e.id eid, e.bracket_slot, COUNT(m.id) cnt, MIN(m.player_id) pid
           FROM entries e LEFT JOIN entry_members m ON m.entry_id=e.id
           WHERE e.competition_id=? GROUP BY e.id ORDER BY e.id""", (cid,))
    return [r for r in rows if r["cnt"] == 1]


async def _team_entry(db, cid, players, rnd):
    eid = await db.execute("INSERT INTO entries (competition_id,group_no) VALUES (?,?)", (cid, rnd))
    for oi, pid in enumerate(players):
        await db.execute("INSERT INTO entry_members (entry_id,player_id,order_index) VALUES (?,?,?)", (eid, pid, oi))
    return eid


@app.post("/api/competitions/{cid}/melee/draw")
async def melee_draw(cid: int, seed: int | None = None, db=Depends(get_db)):
    c = await get_comp(db, cid)
    if mode_kind(c["mode"]) != "super_melee":
        raise HTTPException(400, "Nur für Super-Mêlée verfügbar.")
    parts = await _melee_participants(db, cid)
    n = len(parts)
    if n < 4 or n % 2 != 0:
        raise HTTPException(400, "Super-Mêlée benötigt eine gerade Teilnehmerzahl von mindestens 4.")
    if c["round_count"] > SM.max_rounds(n):
        raise HTTPException(400,
            f"Mit {n} Teilnehmern sind höchstens {SM.max_rounds(n)} Runden ohne Partner-Wiederholung möglich "
            f"(eingestellt: {c['round_count']}). Bitte Teilnehmerzahl erhöhen oder weniger Runden wählen.")
    ms = await load_matches(db, cid)
    melee = [m for m in ms if m["phase"] == "melee"]
    played = max([m["round_no"] for m in melee], default=0)
    nxt = played + 1
    if nxt > c["round_count"]:
        raise HTTPException(400, "Alle geplanten Runden sind bereits ausgelost.")
    if played >= 1:
        last = [m for m in melee if m["round_no"] == played and m["status"] != "bye"]
        for m in last:
            o = outcome(c["score_mode"], m["walkover"], m["sets"], m["home_entry_id"], m["away_entry_id"],
                        c["sets_to_win"], c["points_per_set"])
            if not o["complete"]:
                raise HTTPException(400, "Bitte zuerst alle Ergebnisse der aktuellen Runde eintragen.")

    rng = random.Random((seed if seed is not None else random.randrange(1_000_000)))
    if nxt == 1:
        perm = list(range(n))
        rng.shuffle(perm)
        for idx, p in zip(perm, parts):
            await db.execute("UPDATE entries SET bracket_slot=? WHERE id=?", (idx, p["eid"]))
        parts = await _melee_participants(db, cid)

    slot_to_player = {p["bracket_slot"]: p["pid"] for p in parts}
    player_ids = [slot_to_player[i] for i in range(n)]
    pair_idx = SM.partner_rounds(n)[nxt - 1]
    games, bye = SM.draw_round(pair_idx, player_ids, random.Random((seed or 0) * 100 + nxt))
    for team_a, team_b in games:
        ea = await _team_entry(db, cid, team_a, nxt)
        eb = await _team_entry(db, cid, team_b, nxt)
        await db.execute(
            """INSERT INTO matches (competition_id,phase,round_no,home_entry_id,away_entry_id,status)
               VALUES (?,?,?,?,?, 'pending')""", (cid, "melee", nxt, ea, eb))
    if bye:
        eby = await _team_entry(db, cid, bye, nxt)
        await db.execute(
            """INSERT INTO matches (competition_id,phase,round_no,home_entry_id,away_entry_id,status)
               VALUES (?,?,?,?, NULL, 'bye')""", (cid, "melee", nxt, eby))
    await db.execute("UPDATE competitions SET status='running' WHERE id=?", (cid,))
    return {"ok": True, "round": nxt}


@app.get("/api/competitions/{cid}/melee")
async def melee_state(cid: int, db=Depends(get_db)):
    c = await get_comp(db, cid)
    if mode_kind(c["mode"]) != "super_melee":
        raise HTTPException(400, "Nur für Super-Mêlée verfügbar.")
    parts = await _melee_participants(db, cid)
    part_players = [p["pid"] for p in parts]
    prows = await db.query(
        "SELECT id, (first_name || ' ' || last_name) AS name FROM players", ())
    names = {r["id"]: r["name"] for r in prows}
    mem = await db.query(
        """SELECT em.entry_id, em.player_id FROM entry_members em
           JOIN entries e ON e.id=em.entry_id WHERE e.competition_id=? ORDER BY em.order_index""", (cid,))
    members = {}
    for r in mem:
        members.setdefault(r["entry_id"], []).append(r["player_id"])

    ms = await load_matches(db, cid)
    melee = [m for m in ms if m["phase"] == "melee"]
    results, rounds = [], {}
    for m in melee:
        r = m["round_no"]
        rounds.setdefault(r, {"round": r, "games": [], "bye": None})
        home = members.get(m["home_entry_id"], [])
        if m["status"] == "bye" or m["away_entry_id"] is None:
            rounds[r]["bye"] = {"players": home, "name": " / ".join(names.get(p, "?") for p in home)}
            results.append({"players": home, "points": {p: 1 for p in home}, "diff": {p: 0 for p in home}})
            continue
        away = members.get(m["away_entry_id"], [])
        o = outcome(c["score_mode"], m["walkover"], m["sets"], m["home_entry_id"], m["away_entry_id"],
                    c["sets_to_win"], c["points_per_set"])
        g = {"id": m["id"], "home": home, "away": away,
             "home_name": " / ".join(names.get(p, "?") for p in home),
             "away_name": " / ".join(names.get(p, "?") for p in away),
             "sets": [[s["home_points"], s["away_points"]] for s in m["sets"]],
             "walkover": m["walkover"], "winner": None, "complete": o["complete"]}
        if o["complete"]:
            home_won = o["winner"] == m["home_entry_id"]
            g["winner"] = "home" if home_won else "away"
            diff_h = (o["hs"] - o["as"]) if c["score_mode"] == "sets" else (o["hp"] - o["ap"])
            pts = {**{p: (2 if home_won else 0) for p in home}, **{p: (0 if home_won else 2) for p in away}}
            diff = {**{p: diff_h for p in home}, **{p: -diff_h for p in away}}
            results.append({"players": home + away, "points": pts, "diff": diff})
        rounds[r]["games"].append(g)

    standings = SM.standings(part_players, results)
    for row in standings:
        row["name"] = names.get(row["player"], "?")
    played = max(rounds) if rounds else 0
    last_done = True
    if played >= 1:
        last_done = all(g["complete"] for g in rounds[played]["games"])
    can_draw = played < c["round_count"] and (played == 0 or last_done)
    return {"round_count": c["round_count"], "played": played, "n": len(parts),
            "rounds": [rounds[r] for r in sorted(rounds)],
            "standings": standings, "can_draw": can_draw, "score_mode": c["score_mode"]}


# ---- Schweizer System ---------------------------------------------------------
async def _swiss_context(db, c):
    cid = c["id"]
    entries = await load_entries(db, cid)
    eids = [e["id"] for e in entries]
    names = {e["id"]: e["name"] for e in entries}
    ms = await load_matches(db, cid)
    sw = [m for m in ms if m["phase"] == "swiss"]
    results, byes, rounds = [], [], {}
    for m in sw:
        r = m["round_no"]
        rounds.setdefault(r, {"round": r, "games": [], "bye": None})
        if m["status"] == "bye" or m["away_entry_id"] is None:
            byes.append(m["home_entry_id"])
            rounds[r]["bye"] = {"entry": m["home_entry_id"], "name": names.get(m["home_entry_id"])}
            continue
        o = outcome(c["score_mode"], m["walkover"], m["sets"], m["home_entry_id"], m["away_entry_id"],
                    c["sets_to_win"], c["points_per_set"])
        g = {"id": m["id"], "home": m["home_entry_id"], "away": m["away_entry_id"],
             "home_name": names.get(m["home_entry_id"]), "away_name": names.get(m["away_entry_id"]),
             "sets": [[s["home_points"], s["away_points"]] for s in m["sets"]],
             "walkover": m["walkover"], "complete": o["complete"],
             "winner": o["winner"] if o["complete"] else None}
        if o["complete"]:
            results.append({"home": m["home_entry_id"], "away": m["away_entry_id"], "winner": o["winner"]})
        rounds[r]["games"].append(g)
    return entries, eids, names, rounds, results, byes


@app.post("/api/competitions/{cid}/swiss/draw")
async def swiss_draw(cid: int, seed: int | None = None, db=Depends(get_db)):
    c = await get_comp(db, cid)
    if mode_kind(c["mode"]) != "swiss":
        raise HTTPException(400, "Nur für das Schweizer System verfügbar.")
    entries, eids, names, rounds, results, byes = await _swiss_context(db, c)
    n = len(eids)
    if n < 3:
        raise HTTPException(400, "Mindestens 3 Teilnehmer nötig.")
    if c["round_count"] > SW.max_rounds(n):
        raise HTTPException(400,
            f"Mit {n} Teilnehmern sind höchstens {SW.max_rounds(n)} Runden ohne Gegner-Wiederholung sicher möglich "
            f"(eingestellt: {c['round_count']}).")
    played = max(rounds) if rounds else 0
    if played >= c["round_count"]:
        raise HTTPException(400, "Alle geplanten Runden sind bereits ausgelost.")
    if played >= 1:
        if not all(g["complete"] for g in rounds[played]["games"]):
            raise HTTPException(400, "Bitte zuerst alle Ergebnisse der aktuellen Runde eintragen.")
    seeded = {e["id"] for e in entries if e["seeded"]}
    rng = random.Random(seed if seed is not None else random.randrange(1_000_000))
    try:
        pairs, bye = SW.next_round(eids, results, byes, rng,
                                   first_round_mode=(c["pairing"] or "random"), seeded=seeded)
    except ValueError as e:
        raise HTTPException(400, str(e))
    nxt = played + 1
    for a, b in pairs:
        await db.execute(
            """INSERT INTO matches (competition_id,phase,round_no,home_entry_id,away_entry_id,status)
               VALUES (?,?,?,?,?, 'pending')""", (cid, "swiss", nxt, a, b))
    if bye is not None:
        await db.execute(
            """INSERT INTO matches (competition_id,phase,round_no,home_entry_id,away_entry_id,status)
               VALUES (?,?,?,?, NULL, 'bye')""", (cid, "swiss", nxt, bye))
    await db.execute("UPDATE competitions SET status='running' WHERE id=?", (cid,))
    return {"ok": True, "round": nxt}


@app.get("/api/competitions/{cid}/swiss")
async def swiss_state(cid: int, db=Depends(get_db)):
    c = await get_comp(db, cid)
    if mode_kind(c["mode"]) != "swiss":
        raise HTTPException(400, "Nur für das Schweizer System verfügbar.")
    entries, eids, names, rounds, results, byes = await _swiss_context(db, c)
    table = SW.standings(eids, results, byes)
    for r in table:
        r["name"] = names.get(r["player"])
    played = max(rounds) if rounds else 0
    last_done = played == 0 or all(g["complete"] for g in rounds[played]["games"])
    return {"round_count": c["round_count"], "played": played, "n": len(eids),
            "max_rounds": SW.max_rounds(len(eids)) if eids else 0,
            "rounds": [rounds[r] for r in sorted(rounds)],
            "standings": table,
            "can_draw": played < c["round_count"] and last_done}


# ---- Gruppe -> KO -------------------------------------------------------------
class BuildKoIn(BaseModel):
    qualifiers: list[int] | None = None   # None = Vorschau/Planung, sonst gewählte Kandidaten


def _plan_ko_qualifiers(rk: dict, N: int):
    """Ermittelt sichere Aufsteiger und die zur Auswahl stehenden Kandidaten.
    Kandidat = echter Patt an der Aufstiegsgrenze ('patt') oder bester Dritter/Auffüllen ('fill')."""
    safe, candidates = [], []
    for g in rk["groups"]:
        rows = g["rows"]; gi = g["group"]
        unresolved = g.get("unresolved") or []
        straddle, straddle_top = None, None
        for U in unresolved:
            ranks = [r["rank"] for r in rows if r["entry"] in U]
            if ranks and min(ranks) <= N and max(ranks) > N:
                straddle, straddle_top = set(U), min(ranks); break
        if straddle:
            safe_here = [r for r in rows if r["rank"] < straddle_top]
            cand_here = [r for r in rows if r["entry"] in straddle]
            reason = "patt"
        else:
            safe_here = [r for r in rows if r["rank"] <= N]
            cand_here = [r for r in rows if r["rank"] == N + 1]
            # bei Gleichstand auf dem N+1-Platz die ganze betroffene Gruppe aufnehmen
            for U in unresolved:
                if any(r["entry"] in U for r in cand_here):
                    for r in rows:
                        if r["entry"] in U and r not in cand_here:
                            cand_here.append(r)
            reason = "fill"
        safe.extend(r["entry"] for r in safe_here)
        for r in cand_here:
            candidates.append({"entry": r["entry"], "name": r["name"], "group": gi,
                               "rank": r["rank"], "mp": r["mp"], "gw": r["gw"], "gl": r["gl"],
                               "set_ratio": r["set_ratio"], "club_id": r["club_id"], "reason": reason})
    return safe, candidates


@app.post("/api/competitions/{cid}/build_ko")
async def build_ko(cid: int, body: BuildKoIn | None = None, seed: int | None = None, db=Depends(get_db)):
    c = await get_comp(db, cid)
    if mode_kind(c["mode"]) != "group_ko":
        raise HTTPException(400, "Nur für Gruppe+KO verfügbar.")
    ms = await load_matches(db, cid)
    if any(m["phase"] == "ko" for m in ms):
        raise HTTPException(400, "KO wurde bereits erzeugt.")
    for m in ms:
        if m["phase"] == "group":
            o = outcome(c["score_mode"], m["walkover"], m["sets"], m["home_entry_id"], m["away_entry_id"],
                        c["sets_to_win"], c["points_per_set"])
            if not o["complete"]:
                raise HTTPException(400, "Es sind noch nicht alle Gruppenspiele gespielt.")

    rk = await ranking(cid, db)
    N = c["advance_per_group"]
    safe, candidates = _plan_ko_qualifiers(rk, N)
    cand_ids = {x["entry"] for x in candidates}
    entries = {e["id"]: e for e in await load_entries(db, cid)}

    # Stufe 1: Auswahl nötig? -> Kandidaten zurückmelden, noch nicht bauen.
    if body is None or body.qualifiers is None:
        if candidates:
            return {"ok": True, "built": False, "needs_selection": True,
                    "regular_target": len(rk["groups"]) * N,
                    "safe": [{"entry": eid, "name": entries.get(eid, {}).get("name")} for eid in safe],
                    "candidates": candidates}
        chosen = []
    else:
        chosen = [q for q in body.qualifiers if q in cand_ids]

    # Stufe 2: bauen aus sicheren Aufsteigern + Auswahl
    final = list(dict.fromkeys(safe + chosen))
    if len(final) < 2:
        raise HTTPException(400, "Zu wenige Aufsteiger für ein KO.")
    rowmap = {r["entry"]: r for g in rk["groups"] for r in g["rows"]}
    dentries = [D.DrawEntry(id=eid, seeded=(rowmap[eid]["rank"] == 1),
                            club_id=rowmap[eid]["club_id"], seed_no=idx + 1)
                for idx, eid in enumerate(final)]
    slots, conflicts = D.draw_ko(dentries, random.Random(seed))
    for pos, eid in enumerate(slots):
        if eid is not None:
            await db.execute("UPDATE entries SET bracket_slot=? WHERE id=?", (pos, eid))
    await _insert_ko_skeleton(db, cid, slots)
    await db.execute("UPDATE competitions SET status='running' WHERE id=?", (cid,))
    return {"ok": True, "built": True, "conflicts": conflicts, "qualifiers": final}
