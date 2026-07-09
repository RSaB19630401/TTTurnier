"""
Schmale asynchrone DB-Schnittstelle mit zwei Implementierungen:

- D1DB:     im Cloudflare-Worker, nutzt die D1-Binding (env.DB).
- SqliteDB: nur für lokale Logiktests ohne Worker-Runtime.

Beide sprechen SQL mit `?`-Platzhaltern (D1 wie SQLite identisch), sodass der
gesamte Anwendungscode unverändert gegen beide läuft.
"""
from __future__ import annotations


class Database:
    async def query(self, sql: str, params: tuple = ()) -> list[dict]: ...
    async def query_one(self, sql: str, params: tuple = ()) -> dict | None: ...
    async def execute(self, sql: str, params: tuple = ()) -> int | None: ...  # -> lastrowid


# --------------------------------------------------------------- D1 (Worker) --
class D1DB(Database):
    """Zugriff auf Cloudflare D1 über die Worker-Binding (env.DB)."""
    def __init__(self, d1):
        self.d1 = d1

    def _stmt(self, sql: str, params: tuple):
        s = self.d1.prepare(sql)
        return s.bind(*params) if params else s

    @staticmethod
    def _to_list(value):
        # Je nach Runtime ist das Ergebnis bereits eine Python-Liste,
        # oder ein JsProxy, der erst über .to_py() umgewandelt werden muss.
        if value is None:
            return []
        if hasattr(value, "to_py"):
            value = value.to_py()
        return list(value)

    @staticmethod
    def _to_dict(value):
        if hasattr(value, "to_py"):
            value = value.to_py()
        return dict(value) if value is not None else {}

    async def query(self, sql, params=()):
        res = await self._stmt(sql, params).all()
        rows = self._to_list(res.results)
        return [self._to_dict(r) for r in rows]

    async def query_one(self, sql, params=()):
        rows = await self.query(sql, params)
        return rows[0] if rows else None

    async def execute(self, sql, params=()):
        res = await self._stmt(sql, params).run()
        try:
            meta = self._to_dict(res.meta)
        except AttributeError:
            meta = {}
        return meta.get("last_row_id")


# ------------------------------------------------------ SQLite (nur lokal) ----
class SqliteDB(Database):
    """SQLite-Backend für lokale Logiktests (mirror der D1-API-Semantik)."""
    def __init__(self, path=":memory:"):
        import sqlite3
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")

    def init_schema(self, sql_script: str):
        self.con.executescript(sql_script)
        self.con.commit()

    async def query(self, sql, params=()):
        cur = self.con.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    async def query_one(self, sql, params=()):
        rows = await self.query(sql, params)
        return rows[0] if rows else None

    async def execute(self, sql, params=()):
        cur = self.con.execute(sql, params)
        self.con.commit()
        return cur.lastrowid
