"""Prueba el bot en la terminal, sin WhatsApp.

    python -m app.simulate                 # número compartido por las 3 tiendas
    python -m app.simulate --numero 111    # simula el número exclusivo de una tienda

Escribe /1, /2 o /3 para simular un toque en el botón de tienda. Ctrl+C para salir.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools

from .ai import AIResponder
from .bot import Bot, Incoming
from .config import Settings, load_brands


class ConsoleSender:
    def __init__(self):
        self.buttons: list[tuple[str, str]] = []

    async def send_text(self, phone_number_id, to, body):
        print(f"\n🤖 → +{to}\n{body}\n")
        return True

    async def send_buttons(self, phone_number_id, to, body, buttons):
        self.buttons = buttons
        opts = "   ".join(f"[/{i} {t}]" for i, (_, t) in enumerate(buttons, 1))
        print(f"\n🤖 → +{to}\n{body}\n{opts}\n")
        return True

    async def mark_read(self, phone_number_id, message_id):
        return True


async def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument("--numero", default="000", help="phone_number_id que recibe los mensajes")
    args.add_argument("--sin-ia", action="store_true")
    opts = args.parse_args()

    settings = Settings.from_env()
    sender = ConsoleSender()
    ai = AIResponder(settings.ai_model) if settings.ai_enabled and not opts.sin_ia else None
    bot = Bot(settings, load_brands(), sender, ai=ai)
    print(f"Tiendas: {', '.join(bot.brands)} | IA: {'sí' if ai else 'no (reglas)'}\n")

    for n in itertools.count():
        try:
            text = (await asyncio.to_thread(input, "Tú: ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        inc = Incoming(phone_number_id=opts.numero, sender="56900000000", message_id=f"sim-{n}", name="Cliente", text=text)
        if text.startswith("/") and text[1:].isdigit() and 0 < int(text[1:]) <= len(sender.buttons):
            inc.kind, (inc.button_id, inc.text) = "button", sender.buttons[int(text[1:]) - 1]
        await bot.handle(inc)


if __name__ == "__main__":
    asyncio.run(main())
