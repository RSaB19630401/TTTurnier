"""TT-Turnier – FastAPI-Anwendung (läuft im Cloudflare Python Worker, Persistenz über D1)."""
from __future__ import annotations
from math import log2
import random

from fastapi import FastAPI, Request, Depends, HTTPException
from pydantic import BaseModel

from db import Database, D1DB
from domain import ranking as R
from domain import draw as D
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
         "status": c["status"], "score_mode": c["score_mode"]}
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
    cid = await db.execute(
        """INSERT INTO competitions (name,mode,sets_to_win,points_per_set,group_count,advance_per_group,ko_sets_to_win,status,score_mode)
           VALUES (?,?,?,?,?,?,?, 'setup', ?)""",
        (body.name, body.mode, body.sets_to_win, body.points_per_set,
         body.group_count, body.advance_per_group, body.ko_sets_to_win, body.score_mode))
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


# ---- Auslosung ----------------------------------------------------------------
def _draw_entries(entries: list[dict]) -> list[D.DrawEntry]:
    return [D.DrawEntry(id=e["id"], seeded=e["seeded"], club_id=e["club_id"], seed_no=e["seed_no"])
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
    rng = random.Random(seed)
    kind = mode_kind(c["mode"])
    conflicts = []

    if kind in ("group", "group_ko"):
        gc = effective_group_count(c["mode"], c["group_count"])
        if gc > len(entries):
            raise HTTPException(400, "Mehr Gruppen als Meldungen.")
        groups, conflicts = D.draw_groups(_draw_entries(entries), gc, rng)
        for gi, ids in enumerate(groups):
            for eid in ids:
                await db.execute("UPDATE entries SET group_no=? WHERE id=?", (gi, eid))
            for m in round_robin_schedule(ids):
                await db.execute(
                    """INSERT INTO matches (competition_id,phase,round_no,group_no,home_entry_id,away_entry_id)
                       VALUES (?,?,?,?,?,?)""", (cid, "group", m["round"], gi, m["home"], m["away"]))
    elif kind == "ko":
        slots, conflicts = D.draw_ko(_draw_entries(entries), rng)
        for pos, eid in enumerate(slots):
            if eid is not None:
                await db.execute("UPDATE entries SET bracket_slot=? WHERE id=?", (pos, eid))
        await _insert_ko_skeleton(db, cid, slots)
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

    await db.execute("DELETE FROM match_sets WHERE match_id=?", (mid,))
    walkover = body.walkover if body.walkover in ("home", "away") else None
    stw = c["ko_sets_to_win"] if m["phase"] == "ko" else c["sets_to_win"]
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
