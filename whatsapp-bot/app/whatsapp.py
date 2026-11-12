"""Cliente mínimo de WhatsApp Cloud API (envío de mensajes)."""
from __future__ import annotations

import logging

import httpx

from .config import Settings, token_for

log = logging.getLogger("bot.whatsapp")
MAX_TEXT = 4096


def chunks(body: str, size: int = MAX_TEXT) -> list[str]:
    out = []
    while len(body) > size:
        cut = body.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        out.append(body[:cut].rstrip())
        body = body[cut:].lstrip()
    return out + ([body] if body else [])


class WhatsAppClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.http = httpx.AsyncClient(timeout=15)

    async def _post(self, phone_number_id: str, payload: dict) -> bool:
        token = token_for(self.settings, phone_number_id)
        if not token:
            log.error("Falta WHATSAPP_TOKEN: no se envió el mensaje")
            return False
        url = f"https://graph.facebook.com/{self.settings.graph_version}/{phone_number_id}/messages"
        try:
            r = await self.http.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"messaging_product": "whatsapp", **payload},
            )
        except httpx.HTTPError as e:
            log.error("Error de red al enviar a WhatsApp: %s", e)
            return False
        if r.status_code >= 400:
            log.error("WhatsApp respondió %s: %s", r.status_code, r.text[:500])
            return False
        return True

    async def send_text(self, phone_number_id: str, to: str, body: str) -> bool:
        ok = True
        for part in chunks(body):
            ok &= await self._post(
                phone_number_id, {"to": to, "type": "text", "text": {"body": part, "preview_url": True}}
            )
        return ok

    async def send_buttons(self, phone_number_id: str, to: str, body: str, buttons: list[tuple[str, str]]) -> bool:
        """buttons: hasta 3 pares (id, título de máx. 20 caracteres)."""
        return await self._post(
            phone_number_id,
            {
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body[:1024]},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": i, "title": t[:20]}} for i, t in buttons[:3]
                        ]
                    },
                },
            },
        )

    async def mark_read(self, phone_number_id: str, message_id: str) -> bool:
        return await self._post(phone_number_id, {"status": "read", "message_id": message_id})
