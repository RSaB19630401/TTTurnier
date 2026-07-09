"""Cloudflare Python Worker – Einstiegspunkt.

Reicht eingehende Requests über die von der Workers-Runtime bereitgestellte
ASGI-Bridge an die FastAPI-App weiter. `self.env` enthält die Bindings
(u. a. die D1-Datenbank `env.DB`) und wird in den ASGI-Scope gelegt, sodass
die Routen sie via `request.scope["env"]` erreichen.
"""
from workers import WorkerEntrypoint
import asgi

from app import app  # FastAPI-Instanz


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env)
