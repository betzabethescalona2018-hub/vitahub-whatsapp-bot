"""Servidor del webhook de WhatsApp Cloud API.

Ejecutar:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response

from .ai import AIResponder
from .bot import Bot, Incoming
from .config import Settings, load_brands
from .whatsapp import WhatsAppClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot.web")


def valid_signature(secrets: list[str], body: bytes, header: str) -> bool:
    if not header.startswith("sha256="):
        return False
    return any(
        hmac.compare_digest(hmac.new(s.encode(), body, hashlib.sha256).hexdigest(), header[7:]) for s in secrets
    )


def parse_messages(payload: dict) -> tuple[list[Incoming], list[tuple[str, str]]]:
    """Devuelve (mensajes de clientes, respuestas manuales del dueño como (phone_number_id, cliente))."""
    incoming: list[Incoming] = []
    echoes: list[tuple[str, str]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            pnid = str((value.get("metadata") or {}).get("phone_number_id", ""))
            if change.get("field") == "smb_message_echoes":
                echoes += [(pnid, str(m["to"])) for m in value.get("message_echoes", []) if m.get("to")]
                continue
            if change.get("field") != "messages":
                continue
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                inc = Incoming(
                    phone_number_id=pnid,
                    sender=str(m.get("from", "")),
                    message_id=m.get("id", ""),
                    name=names.get(m.get("from"), ""),
                )
                ref = m.get("referral") or {}
                inc.referral_text = " ".join(str(ref.get(k, "")) for k in ("headline", "body", "source_url")).strip()
                kind = m.get("type")
                if kind == "text":
                    inc.text = (m.get("text") or {}).get("body", "")
                elif kind == "interactive":
                    reply = (m.get("interactive") or {}).get("button_reply") or (m.get("interactive") or {}).get("list_reply") or {}
                    inc.kind, inc.button_id, inc.text = "button", reply.get("id", ""), reply.get("title", "")
                elif kind == "button":
                    inc.text = (m.get("button") or {}).get("text", "")
                else:
                    inc.kind = "other"
                if inc.sender and pnid:
                    incoming.append(inc)
    return incoming, echoes


def create_app(settings: Settings | None = None, bot: Bot | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if bot is None:
        ai = AIResponder(settings.ai_model) if settings.ai_enabled else None
        bot = Bot(settings, load_brands(), WhatsAppClient(settings), ai=ai)
    if not settings.app_secrets:
        log.warning("META_APP_SECRET no configurado: no se verifica la firma del webhook (solo para pruebas)")
    log.info("Tiendas: %s | IA: %s", ", ".join(bot.brands), "activada" if bot.ai else "desactivada (reglas)")

    app = FastAPI(title="Bot WhatsApp VitaHub")
    app.state.bot = bot

    @app.get("/health")
    async def health():
        return {"ok": True, "tiendas": list(bot.brands), "ia": bool(bot.ai)}

    @app.get("/webhook")
    async def verify(request: Request):
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and settings.verify_token and hmac.compare_digest(
            q.get("hub.verify_token", ""), settings.verify_token
        ):
            return Response(q.get("hub.challenge", ""), media_type="text/plain")
        raise HTTPException(403, "Token de verificación inválido")

    @app.post("/webhook")
    async def receive(request: Request, tasks: BackgroundTasks):
        body = await request.body()
        if settings.app_secrets and not valid_signature(
            settings.app_secrets, body, request.headers.get("x-hub-signature-256", "")
        ):
            raise HTTPException(403, "Firma inválida")
        try:
            payload = json.loads(body)
        except ValueError:
            raise HTTPException(400, "JSON inválido")
        incoming, echoes = parse_messages(payload)
        for pnid, customer in echoes:
            bot.owner_replied(pnid, customer)
        for inc in incoming:
            tasks.add_task(bot.handle, inc)  # responder 200 de inmediato; Meta reintenta si demora
        return {"status": "ok"}

    return app


app = create_app()
