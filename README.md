# TT-Turnier

Tischtennis-Turnierverwaltung als **Cloudflare Python Worker** (FastAPI) mit
**D1** (serverloses SQLite) als Datenbank und einem statisch ausgelieferten
Web-Frontend. Ein Codestand, der **lokal läuft** (`pywrangler dev`) und
**deploybar** ist (`pywrangler deploy`).

## Funktionsumfang (Phase 1)

- Stammdaten: Vereine, Spieler, Wettbewerbe.
- Je Wettbewerb ein Turniermodus. Aktiv: **Rundensystem, Poule, Gruppensystem,
  KO-System, Gruppe + KO**. Geplant/angelegt: Schweizer System, Doppel-KO,
  vollständiges KO, Mêlée/Super-Mêlée (Auswahl sichtbar, noch nicht aktiv).
- Teilnehmerauswahl und Setzung (ankreuzen).
- **Auslosung mit Vereinsschutz**: gleiche Vereine werden über Gruppen bzw.
  Baumhälften getrennt; unvermeidbare Restkonflikte werden ehrlich gemeldet.
- Ergebniserfassung (Sätze oder kampflos) und **ITTF/DTTB-Rangliste**
  (Spielpunkte → Satzquotient → Ballpunktquotient → Los, iterativ über die
  Partien der Gleichstehenden).
- KO-Baum mit Freilosen und Live-Ableitung der Sieger.

## Architektur

```
Browser ──HTTP──> Cloudflare Worker (Python, FastAPI via ASGI)
                        │  bindings
                        ├── D1  (SQLite, Persistenz)
                        └── Assets (public/index.html – Frontend)
```

- `src/entry.py` – Worker-Einstiegspunkt, reicht Requests an FastAPI (`asgi.fetch`).
- `src/app.py` – FastAPI-Endpunkte, Persistenz über D1-SQL.
- `src/db.py` – dünne DB-Schnittstelle: `D1DB` (Worker) und `SqliteDB` (nur Tests).
- `src/domain/` – reine Fachlogik ohne I/O: `ranking.py` (Rangliste),
  `draw.py` (Auslosung/Vereinsschutz), `modes.py` (Modus-Registry).
- `public/index.html` – Single-File-Frontend (React via CDN, kein Build).
- `migrations/0001_init.sql` – Datenbankschema.

## Voraussetzungen

- Node.js ≥ 18 (für `wrangler`)
- Python ≥ 3.12 und [`uv`](https://docs.astral.sh/uv/)
- Ein Cloudflare-Account
- `wrangler login` einmalig ausgeführt

## Einrichtung

```bash
# 1) Abhängigkeiten / CLI
uv tool install workers-py           # stellt `pywrangler` bereit
npm install -g wrangler              # oder via npx nutzen

# 2) Bei Cloudflare anmelden
wrangler login

# 3) D1-Datenbank anlegen und ID eintragen
wrangler d1 create tt-turnier
#   -> die ausgegebene database_id in wrangler.toml bei `database_id` eintragen

# 4) Schema anwenden (lokal und in der Cloud)
wrangler d1 migrations apply tt-turnier --local
wrangler d1 migrations apply tt-turnier --remote
```

## Lokal starten

```bash
uv run pywrangler dev
```

Öffnet einen lokalen Server (inkl. lokaler D1 und statischem Frontend).
Die Oberfläche liegt unter `/`, die API unter `/api/...`.

## Deployen

```bash
uv run pywrangler deploy
```

Danach ist die App unter `https://tt-turnier.<dein-subdomain>.workers.dev` erreichbar.

## Tests

Die Fachlogik und die SQL-Persistenz sind gegen SQLite getestet (spiegelt die
D1-Semantik – der Worker selbst braucht die Cloudflare-Runtime):

```bash
uv run python tests/test_api.py
```

## GitHub

```bash
git init && git add . && git commit -m "TT-Turnier: Cloudflare Worker + D1"
git branch -M main
git remote add origin git@github.com:<user>/tt-turnier.git
git push -u origin main
```

Optionales automatisches Deploy bei Push ist in
`.github/workflows/deploy.yml` vorbereitet. Dafür im GitHub-Repo unter
*Settings → Secrets and variables → Actions* setzen:

- `CLOUDFLARE_API_TOKEN` – Token mit Rechten für Workers und D1
- `CLOUDFLARE_ACCOUNT_ID` – deine Account-ID

## Hinweise / Grenzen

- **Python Workers sind Beta.** FastAPI/Pydantic werden unterstützt; der
  Paketumfang ist auf die von der Runtime bereitgestellten Pakete beschränkt –
  daher kein SQLAlchemy-ORM, sondern direkter D1-SQL-Zugriff.
- **D1** ist produktionsreif; je Datenbank max. 10 GB, im Free-Tarif gelten
  tägliche Limits. Für eine Turnierverwaltung weit ausreichend.
- Der D1-Zugriff in `src/db.py` (`D1DB`) folgt der offiziellen Cloudflare-Doku
  und ist die einzige Schicht, die außerhalb der Worker-Runtime nicht lokal
  getestet werden kann.

## Nächste Modi (Roadmap)

Die Registry in `src/domain/modes.py` ist auf alle Modi vorbereitet. Als
Nächstes umsetzbar: **Schweizer System** (Paarung nach Punktgruppen + Buchholz),
**Doppel-KO**, **vollständiges KO** (Platzierungsspiele), **Mêlée/Super-Mêlée**
(nutzt die bereits angelegte `Entry`/`EntryMember`-Struktur für Doppel).
