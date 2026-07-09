"""Cloudflare Python Worker – Einstiegspunkt (mit temporärer Fehlerdiagnose)."""
from workers import WorkerEntrypoint
import asgi
import traceback
from fastapi import Request
from fastapi.responses import PlainTextResponse

from app import app


# TEMPORÄR: zeigt den echten Python-Fehler im Browser statt "Internal Server Error".
# Wird nach der Fehlersuche wieder entfernt.
@app.exception_handler(Exception)
async def _debug_unhandled(request: Request, exc: Exception):
    return PlainTextResponse(traceback.format_exc(), status_code=500)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env)
