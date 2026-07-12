"""
TT-Turnier – lokale Offline-Version.

Startet dieselbe Anwendung wie in der Cloud, aber:
  * Daten in einer SQLite-Datei neben dem Programm (ttturnier.db)
  * Anmeldung aktiv (Benutzer/Passwort siehe unten bzw. ttturnier.ini)
  * Oberfläche im Standardbrowser unter http://127.0.0.1:8765

Beenden: dieses Fenster schließen (oder Strg+C).
"""
from __future__ import annotations
import configparser
import os
import sqlite3
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

# --- Pfade: funktioniert sowohl als .exe (PyInstaller) als auch als Skript ------
if getattr(sys, "frozen", False):                 # gebündelte .exe
    BUNDLE = Path(sys._MEIPASS)                   # entpackte Programmdaten
    HOME = Path(sys.executable).parent            # Ordner neben der .exe
else:
    BUNDLE = Path(__file__).resolve().parent
    HOME = BUNDLE

sys.path.insert(0, str(BUNDLE / "src"))

PUBLIC = BUNDLE / "public"
MIGRATIONS = BUNDLE / "migrations"
DB_FILE = HOME / "ttturnier.db"
INI_FILE = HOME / "ttturnier.ini"
PORT = 8765

DEFAULT_USER = "TTTurnier"
DEFAULT_PASSWORD = "TTAdmin"


def read_config():
    """Benutzer/Passwort aus ttturnier.ini lesen; Datei bei Bedarf anlegen."""
    cp = configparser.ConfigParser()
    if INI_FILE.exists():
        cp.read(INI_FILE, encoding="utf-8")
    else:
        cp["anmeldung"] = {"benutzer": DEFAULT_USER, "passwort": DEFAULT_PASSWORD}
        with open(INI_FILE, "w", encoding="utf-8") as f:
            cp.write(f)
    sec = cp["anmeldung"] if "anmeldung" in cp else {}
    return (sec.get("benutzer", DEFAULT_USER) or DEFAULT_USER,
            sec.get("passwort", DEFAULT_PASSWORD) or DEFAULT_PASSWORD)


def apply_migrations(path: Path):
    """Alle Migrationen anwenden; bereits vorhandene Spalten werden übersprungen."""
    con = sqlite3.connect(path)
    try:
        for f in sorted(MIGRATIONS.glob("*.sql")):
            raw = f.read_text(encoding="utf-8")
            # Kommentarzeilen entfernen (sie können Semikolons enthalten)
            sql = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("--"))
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                try:
                    con.execute(stmt)
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if "duplicate column" in msg or "already exists" in msg:
                        continue          # schon vorhanden – in Ordnung
                    raise
        con.commit()
    finally:
        con.close()


def main():
    user, password = read_config()
    apply_migrations(DB_FILE)

    from db import SqliteDB
    import app as appmod

    local_db = SqliteDB(str(DB_FILE))
    appmod.app.dependency_overrides[appmod.get_db] = lambda: local_db

    class LocalEnv:            # ersetzt die Cloudflare-Umgebung
        APP_USER = user
        APP_PASSWORD = password

    @appmod.app.middleware("http")
    async def inject_env(request, call_next):
        request.scope["env"] = LocalEnv
        return await call_next(request)

    # Oberfläche ausliefern (in der Cloud übernimmt das Cloudflare)
    appmod.app.mount("/", StaticFiles(directory=str(PUBLIC), html=True), name="static")

    url = f"http://127.0.0.1:{PORT}/"
    print("=" * 60)
    print("  TT-Turnier – lokale Version")
    print("=" * 60)
    print(f"  Adresse:    {url}")
    print(f"  Benutzer:   {user}")
    print(f"  Datenbank:  {DB_FILE}")
    print(f"  Zugangsdaten ändern: {INI_FILE}")
    print()
    print("  Zum Beenden dieses Fenster schließen.")
    print("=" * 60)

    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(appmod.app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                     # Fenster nicht wortlos schließen
        print("\nFEHLER:", e)
        input("\nMit Enter beenden …")
