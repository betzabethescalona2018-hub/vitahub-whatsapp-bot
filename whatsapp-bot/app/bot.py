"""Flujo de conversación: identifica la tienda, responde y deriva a una persona."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from .catalog import Catalog, money
from .config import Brand, Settings, has_phrase, normalize
from .router import BrandRouter

log = logging.getLogger("bot")

GREETINGS = {"hola", "holi", "ola", "buenas", "buenos dias", "buen dia", "buenas tardes", "buenas noches", "hello", "hi", "alo"}
MENU_WORDS = {"menu", "inicio", "cambiar tienda", "cambiar de tienda", "otra tienda", "tiendas", "volver"}
RESUME_WORDS = MENU_WORDS | {"bot"}
HUMAN_PHRASES = [
    "asesor", "asesora", "humano", "persona", "ejecutivo", "ejecutiva", "agente", "vendedor", "vendedora",
    "hablar con alguien", "atencion al cliente", "reclamo", "reclamar",
]
PRICE_WORDS = ["precio", "precios", "cuanto cuesta", "cuanto vale", "cuanto sale", "valor", "vale", "cuesta"]
CATALOG_WORDS = ["catalogo", "productos", "que venden", "que tienen", "que ofrecen", "ver productos", "tienda", "pagina", "web", "link"]
HISTORY_LIMIT = 20


@dataclass
class Incoming:
    phone_number_id: str
    sender: str  # número del cliente (wa_id)
    message_id: str
    kind: str = "text"  # text | button | other
    text: str = ""
    button_id: str = ""
    name: str = ""
    referral_text: str = ""


@dataclass
class Session:
    brand: str | None = None
    history: list[dict] = field(default_factory=list)
    pending: str = ""  # consulta recibida antes de saber la tienda
    human_until: float = 0
    last_seen: float = field(default_factory=time.time)


class Bot:
    def __init__(self, settings: Settings, brands: dict[str, Brand], sender, catalog: Catalog | None = None, ai=None):
        self.settings = settings
        self.brands = brands
        self.sender = sender
        self.catalog = catalog or Catalog(settings.catalog_ttl_minutes)
        self.ai = ai
        self.router = BrandRouter(brands, self.catalog.terms)
        self.sessions: dict[str, Session] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._seen: OrderedDict[str, None] = OrderedDict()

    # ---------- sesiones ----------
    def session(self, phone_number_id: str, customer: str) -> Session:
        key = f"{phone_number_id}:{customer}"
        s = self.sessions.get(key)
        if not s or time.time() - s.last_seen > self.settings.session_hours * 3600:
            s = self.sessions[key] = Session()
        s.last_seen = time.time()
        return s

    def _duplicate(self, message_id: str) -> bool:
        if not message_id:
            return False
        if message_id in self._seen:
            return True
        self._seen[message_id] = None
        if len(self._seen) > 5000:
            self._seen.popitem(last=False)
        return False

    # ---------- entrada ----------
    async def handle(self, inc: Incoming) -> None:
        if self._duplicate(inc.message_id):
            return
        lock = self._locks.setdefault(f"{inc.phone_number_id}:{inc.sender}", asyncio.Lock())
        async with lock:
            try:
                await self._handle(inc)
            except Exception:
                log.exception("Error procesando mensaje %s", inc.message_id)

    def owner_replied(self, phone_number_id: str, customer: str) -> None:
        """El dueño respondió a mano desde la app de WhatsApp Business: el bot se calla."""
        s = self.session(phone_number_id, customer)
        s.human_until = time.time() + self.settings.handoff_hours * 3600

    async def _handle(self, inc: Incoming) -> None:
        s = self.session(inc.phone_number_id, inc.sender)
        if inc.message_id:
            await self.sender.mark_read(inc.phone_number_id, inc.message_id)
        say = lambda body: self.sender.send_text(inc.phone_number_id, inc.sender, body)  # noqa: E731
        norm = normalize(inc.text)

        # Conversación en manos de una persona: el bot no interviene
        if s.human_until > time.time():
            if norm not in RESUME_WORDS:
                return
            s.human_until = 0

        if inc.kind == "other":
            await say("Por ahora solo puedo leer mensajes de texto 🙏 ¿Me escribes tu consulta? Si prefieres hablar con una persona, escribe *asesor*.")
            return

        fixed = self.router.by_phone.get(inc.phone_number_id) or (next(iter(self.brands)) if len(self.brands) == 1 else None)

        # Elección de tienda con botón (o con 1/2/3 mientras no hay tienda)
        chosen = None
        if inc.button_id.startswith("brand:"):
            chosen = inc.button_id.split(":", 1)[1]
        elif not s.brand and not fixed and norm.isdigit() and 1 <= int(norm) <= len(self.brands):
            chosen = list(self.brands)[int(norm) - 1]
        if chosen in self.brands and not fixed:
            s.brand, pending, s.pending = chosen, s.pending, ""
            if pending:
                await self._answer(inc, s, self.brands[chosen], pending)
            else:
                await self._welcome(inc, s, self.brands[chosen])
            return

        if norm in MENU_WORDS:
            if fixed:
                await self._welcome(inc, s, self.brands[fixed])
            else:
                s.brand, s.pending = None, ""
                await self._ask_brand(inc)
            return

        decision = self.router.decide(
            phone_number_id=inc.phone_number_id, text=inc.text, referral_text=inc.referral_text, current=s.brand
        )
        is_greeting = norm in GREETINGS or (norm.startswith(("hola", "buenas", "buenos")) and len(norm.split()) <= 3)

        if decision.brand is None:
            if any(has_phrase(norm, p) for p in HUMAN_PHRASES):
                await self._handoff(inc, s, None, inc.text)
                return
            s.pending = "" if is_greeting else inc.text
            await self._ask_brand(inc)
            return

        brand = self.brands[decision.brand]
        switched = s.brand and s.brand != brand.key
        s.brand = brand.key
        if switched:
            s.history.clear()
            await say(f"Te atiendo por *{brand.name}* {brand.emoji}".strip())

        if any(has_phrase(norm, p) for p in HUMAN_PHRASES):
            await self._handoff(inc, s, brand, inc.text)
            return

        only_brand_name = any(norm == normalize(k) for k in brand.strong_keywords + [brand.name])
        if is_greeting or only_brand_name:
            if s.pending:
                text, s.pending = s.pending, ""
                await self._answer(inc, s, brand, text)
            else:
                await self._welcome(inc, s, brand)
            return

        await self._answer(inc, s, brand, inc.text)

    # ---------- mensajes base ----------
    async def _ask_brand(self, inc: Incoming) -> None:
        lines = [f"{b.emoji} *{b.name}*: {b.tagline}" for b in self.brands.values()]
        body = "¡Hola! 👋 Gracias por escribirnos. ¿Sobre cuál de nuestras tiendas es tu consulta?\n\n" + "\n".join(lines)
        buttons = [(f"brand:{b.key}", b.button_title) for b in self.brands.values()]
        if len(buttons) <= 3:
            await self.sender.send_buttons(inc.phone_number_id, inc.sender, body, buttons)
        else:
            body += "\n\n" + "\n".join(f"{i}. {b.name}" for i, b in enumerate(self.brands.values(), 1))
            await self.sender.send_text(inc.phone_number_id, inc.sender, body + "\n\nResponde con el número.")

    def _options(self, brand: Brand) -> list[tuple[str, str]]:
        opts = [("catalogo", "Ver productos y precios")] if (brand.website or brand.shopify_domain) else []
        opts += [(f"faq:{f.id}", f.title) for f in brand.faqs]
        return opts + [("humano", "Hablar con una persona")]

    def _menu(self, brand: Brand) -> str:
        return "\n".join(f"{i}. {title}" for i, (_, title) in enumerate(self._options(brand), 1))

    async def _welcome(self, inc: Incoming, s: Session, brand: Brand) -> None:
        name = f" {inc.name.split()[0]}" if inc.name.strip() else ""
        body = f"{brand.emoji} ¡Hola{name}! Soy el asistente virtual de *{brand.name}*, {brand.tagline}."
        if brand.highlights:
            body += "\n\n" + "\n".join(f"• {h}" for h in brand.highlights)
        if self.ai:
            body += "\n\nCuéntame, ¿en qué te ayudo? Si prefieres hablar con una persona, escribe *asesor*."
        else:
            body += "\n\nEscribe tu consulta o elige una opción:\n" + self._menu(brand)
        if not self.router.by_phone.get(inc.phone_number_id) and len(self.brands) > 1:
            body += "\n\n_Para cambiar de tienda escribe *menú*._"
        await self.sender.send_text(inc.phone_number_id, inc.sender, body)

    async def _handoff(self, inc: Incoming, s: Session, brand: Brand | None, last_text: str, announce: bool = True) -> None:
        s.human_until = time.time() + self.settings.handoff_hours * 3600
        if announce:
            hours = (brand.contact.get("hours") if brand else "") or ""
            body = "👩‍💻 Listo, te derivo con una persona de nuestro equipo. Te responderá por este mismo chat lo antes posible."
            if hours:
                body += f"\nHorario de atención: {hours}."
            body += "\n\nSi quieres volver al asistente automático, escribe *menú*."
            await self.sender.send_text(inc.phone_number_id, inc.sender, body)
        log.info("Derivación a humano: %s (%s)", inc.sender, brand.key if brand else "sin tienda")
        owner = self.settings.owner_notify_number
        if owner and owner != inc.sender:
            await self.sender.send_text(
                inc.phone_number_id,
                owner,
                f"🔔 Cliente espera atención humana\nTienda: {brand.name if brand else 'sin definir'}\n"
                f"Cliente: {inc.name or 's/n'} (+{inc.sender})\nÚltimo mensaje: {last_text[:300]}",
            )

    # ---------- respuestas ----------
    async def _answer(self, inc: Incoming, s: Session, brand: Brand, text: str) -> None:
        s.history.append({"role": "user", "content": text})
        s.history = s.history[-HISTORY_LIMIT:]
        while s.history and s.history[0]["role"] != "user":
            s.history.pop(0)

        reply, handoff = None, False
        if self.ai:
            result = await self.ai.answer(brand, await self.catalog.products(brand), s.history)
            if result:
                reply, handoff = result
        if reply is None:
            reply, handoff = await self._rules(brand, text)

        s.history.append({"role": "assistant", "content": reply})
        await self.sender.send_text(inc.phone_number_id, inc.sender, reply)
        if handoff:
            await self._handoff(inc, s, brand, text, announce=False)

    async def _catalog_reply(self, brand: Brand) -> str:
        products = [p for p in await self.catalog.products(brand) if p.available]
        site = brand.website or (f"https://{brand.shopify_domain}" if brand.shopify_domain else "")
        if not products:
            return f"Puedes ver todos nuestros productos aquí: {site}" if site else ""
        top = sorted(products, key=lambda p: p.price)[:6]
        lines = [f"• {p.title}: *{money(p.price, brand.currency)}*" for p in top]
        body = f"{brand.emoji} Algunos productos de *{brand.name}*:\n" + "\n".join(lines)
        if site:
            body += f"\n\nCatálogo completo: {site}"
        return body + "\n\nDime qué producto buscas y te paso precio y enlace."

    async def _rules(self, brand: Brand, text: str) -> tuple[str, bool]:
        """Respuestas sin IA: opciones numeradas, preguntas frecuentes y búsqueda de productos."""
        norm = normalize(text)
        options = self._options(brand)
        action = options[int(norm) - 1][0] if norm.isdigit() and 1 <= int(norm) <= len(options) else ""

        if action == "humano":
            return "👩‍💻 Te derivo con una persona de nuestro equipo. Te responderá por este mismo chat lo antes posible.", True
        if action.startswith("faq:"):
            faq = next(f for f in brand.faqs if f.id == action[4:])
            return faq.answer, faq.handoff

        found = await self.catalog.search(brand, text)
        wants_price = any(has_phrase(norm, w) for w in PRICE_WORDS)
        scored = [(sum(has_phrase(norm, k) for k in f.keywords), f) for f in brand.faqs]
        best_score, best_faq = max(scored, key=lambda sf: sf[0], default=(0, None))

        if best_score and not (wants_price and found):
            return best_faq.answer, best_faq.handoff
        if found:
            lines = [f"• *{p.title}*: {money(p.price, brand.currency)}\n  {p.url(brand)}" for p in found]
            return "Esto es lo que encontré:\n\n" + "\n".join(lines) + "\n\n¿Te ayudo con algo más?", False
        if action == "catalogo" or wants_price or any(has_phrase(norm, w) for w in CATALOG_WORDS):
            reply = await self._catalog_reply(brand)
            if reply:
                return reply, False
            return "Cuéntame qué producto te interesa y una persona del equipo te envía el detalle y el precio.", True
        return (
            f"No estoy seguro de haber entendido 🤔 Puedo ayudarte con esto de *{brand.name}*:\n"
            + self._menu(brand)
            + "\n\nResponde con el número o escribe tu consulta con otras palabras.",
            False,
        )
