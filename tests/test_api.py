"""End-to-End-Test der API gegen SQLite (spiegelt die D1-Semantik).

Die eigentliche Domänenlogik (Rangliste/Auslosung) ist separat getestet; hier
geht es um die SQL-Persistenz und die Endpunkt-Verkettung. Der Cloudflare-Worker
selbst (ASGI-Bridge + D1-Binding) ist nur eine dünne, dokumentierte Schicht
darüber und in dieser Umgebung nicht ausführbar.
"""
import os, sys, pathlib

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

from fastapi.testclient import TestClient
from db import SqliteDB
import app as appmod

SCHEMA = (HERE.parent / "migrations" / "0001_init.sql").read_text()


def make_client():
    db = SqliteDB(":memory:")
    db.init_schema(SCHEMA)
    appmod.app.dependency_overrides[appmod.get_db] = lambda: db
    return TestClient(appmod.app), db


def seed_clubs_players(c, n_clubs=2, n_players=8):
    clubs = [c.post("/api/clubs", json={"name": f"Verein {i+1}"}).json() for i in range(n_clubs)]
    pids = []
    for i in range(n_players):
        p = c.post("/api/players", json={"first_name": f"P{i+1}", "last_name": "T",
                                         "club_id": clubs[i % n_clubs]["id"]}).json()
        pids.append(p["id"])
    return clubs, pids


def play_all_group(c, cid, home_wins=True):
    ms = c.get(f"/api/competitions/{cid}/matches").json()
    for m in ms:
        c.post(f"/api/matches/{m['id']}/result", json={"sets": [[11, 5], [11, 7], [11, 9]]})
    return len(ms)


def test_group_ko_full():
    c, _ = make_client()
    _, pids = seed_clubs_players(c, 2, 8)
    comp = c.post("/api/competitions", json={"name": "GK", "mode": "group_ko",
                                             "group_count": 2, "advance_per_group": 2}).json()
    cid = comp["id"]
    c.put(f"/api/competitions/{cid}/entries", json={"player_ids": pids, "seeded_ids": pids[:2]})
    dr = c.post(f"/api/competitions/{cid}/draw?seed=7").json()
    assert dr["ok"]
    n = play_all_group(c, cid)
    assert n == 12  # 2 Gruppen à 4 -> je 6 Spiele
    rk = c.get(f"/api/competitions/{cid}/ranking").json()
    assert len(rk["groups"]) == 2 and len(rk["groups"][0]["rows"]) == 4
    bk = c.post(f"/api/competitions/{cid}/build_ko?seed=3").json()
    assert bk["ok"]
    # KO ausspielen
    for _ in range(4):
        br = c.get(f"/api/competitions/{cid}/bracket").json()
        for m in br["matches"]:
            if m["home"] and m["away"] and not m["winner"]:
                c.post(f"/api/matches/{m['id']}/result", json={"sets": [[11, 4], [11, 5], [11, 6]]})
    br = c.get(f"/api/competitions/{cid}/bracket").json()
    assert br["champion"] is not None
    print("group_ko: Sieger =", br["champion_name"], "| KO-Runden =", br["rounds"])


def test_ko_only_with_bye():
    c, _ = make_client()
    _, pids = seed_clubs_players(c, 3, 6)  # 6 -> Baumgröße 8, also 2 Freilose
    comp = c.post("/api/competitions", json={"name": "KO", "mode": "ko"}).json()
    cid = comp["id"]
    c.put(f"/api/competitions/{cid}/entries", json={"player_ids": pids, "seeded_ids": pids[:2]})
    dr = c.post(f"/api/competitions/{cid}/draw?seed=1").json()
    assert dr["ok"]
    for _ in range(5):
        br = c.get(f"/api/competitions/{cid}/bracket").json()
        for m in br["matches"]:
            if m["home"] and m["away"] and not m["winner"] and not m["bye"]:
                c.post(f"/api/matches/{m['id']}/result", json={"sets": [[11, 3], [11, 3], [11, 3]]})
    br = c.get(f"/api/competitions/{cid}/bracket").json()
    assert br["rounds"] == 3 and br["champion"] is not None
    print("ko: Sieger =", br["champion_name"], "| Runden =", br["rounds"])


def test_round_system_ranking():
    c, _ = make_client()
    _, pids = seed_clubs_players(c, 1, 5)
    comp = c.post("/api/competitions", json={"name": "RS", "mode": "round_system"}).json()
    cid = comp["id"]
    c.put(f"/api/competitions/{cid}/entries", json={"player_ids": pids, "seeded_ids": []})
    c.post(f"/api/competitions/{cid}/draw?seed=2")
    n = play_all_group(c, cid)
    assert n == 10  # 5 Spieler -> C(5,2)=10 Spiele in einer Runde
    rk = c.get(f"/api/competitions/{cid}/ranking").json()
    assert len(rk["groups"]) == 1 and len(rk["groups"][0]["rows"]) == 5
    print("round_system: 1 Gruppe,", len(rk["groups"][0]["rows"]), "Spieler,", n, "Spiele")


if __name__ == "__main__":
    test_group_ko_full()
    test_ko_only_with_bye()
    test_round_system_ranking()
    print("\nAlle Durchläufe OK.")
