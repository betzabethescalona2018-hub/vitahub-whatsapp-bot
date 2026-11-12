import asyncio
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.bot import Bot, Incoming
from app.catalog import Catalog
from app.config import Settings, load_brands
from app.main import create_app, parse_messages
from app.router import BrandRouter
from app.whatsapp import WhatsAppClient


class FakeSender:
    def __init__(self):
        self.sent = []  # (tipo, destino, cuerpo, botones)

    async def send_text(self, pnid, to, body):
        self.sent.append(("text", to, body, None))
        return True

    async def send_buttons(self, pnid, to, body, buttons):
        self.sent.append(("buttons", to, body, buttons))
        return True

    async def mark_read(self, pnid, message_id):
        return True

    @property
    def last(self):
        return self.sent[-1][2]


def make_bot(**settings):
    brands = load_brands()
    sender = FakeSender()
    catalog = Catalog()

    async def offline(brand):  # las pruebas usan la copia local del catálogo, sin red
        raise OSError("sin red")

    catalog._fetch = offline
    return Bot(Settings(**settings), brands, sender, catalog=catalog), sender


_n = iter(range(10**6))


def msg(text="", pnid="SHARED", sender="56911111111", **kw):
    return Incoming(phone_number_id=pnid, sender=sender, message_id=f"m{next(_n)}", text=text, **kw)


def run(coro):
    return asyncio.run(coro)


# ---------- detección de tienda ----------
@pytest.fixture
def router():
    brands = load_brands()
    return BrandRouter(brands, Catalog().terms)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hola, tienen creatina?", "vitahubchile"),
        ("cuánto demora el despacho a Concepción", "vitahubchile"),
        ("quiero el pack de ashwagandha", "vitahubchile"),
        ("info de la plantilla de presupuesto en excel", "finanzas"),
        ("Hola vengo por VitaHub Finanzas", "finanzas"),
        ("quiero ordenar mis deudas y gastos", "finanzas"),
        ("me interesa el ebook", "digital"),
        ("hola, consulta por vitahub digital", "digital"),
        ("hola", None),
        ("quiero comprar", None),
    ],
)
def test_detect_from_text(router, text, expected):
    assert router.decide(phone_number_id="SHARED", text=text).brand == expected


def test_dedicated_number_wins(router):
    router.by_phone["111"] = "finanzas"
    d = router.decide(phone_number_id="111", text="tienen creatina?")
    assert d.brand == "finanzas" and d.locked


def test_referral_ad(router):
    d = router.decide(phone_number_id="SHARED", text="Hola, quiero más información", referral_text="Plantillas Excel para tu presupuesto")
    assert d.brand == "finanzas"


def test_session_is_sticky_but_switches_on_clear_signal(router):
    assert router.decide(phone_number_id="SHARED", text="y cuanto sale el envio?", current="finanzas").brand == "finanzas"
    assert router.decide(phone_number_id="SHARED", text="ok gracias", current="digital").brand == "digital"
    assert router.decide(phone_number_id="SHARED", text="y tienen magnesio?", current="finanzas").brand == "vitahubchile"


# ---------- flujo ----------
def test_ambiguous_asks_then_answers_pending_question():
    bot, sender = make_bot()
    run(bot.handle(msg("cuanto demoran en entregar?")))
    kind, _, body, buttons = sender.sent[-1]
    assert kind == "buttons" and [b[0] for b in buttons] == ["brand:vitahubchile", "brand:finanzas", "brand:digital"]
    run(bot.handle(msg("VitaHub Chile", kind="button", button_id="brand:vitahubchile")))
    assert "3 a 5 días hábiles" in sender.last


def test_each_store_answers_with_its_own_info():
    bot, sender = make_bot()
    run(bot.handle(msg("como es el envio de los suplementos?", sender="1")))
    assert "$3.500" in sender.last
    run(bot.handle(msg("como es la entrega de la plantilla excel?", sender="2")))
    assert "productos digitales" in sender.last and "$3.500" not in sender.last


def test_product_search_gives_price_and_link():
    bot, sender = make_bot()
    run(bot.handle(msg("precio de la creatina woman")))
    assert "Creatina WOMAN" in sender.last and "$15.990" in sender.last
    assert "https://vitahubchile.com/products/creatina-woman-monohidratada-300g" in sender.last


def test_greeting_on_dedicated_number_skips_picker():
    bot, sender = make_bot()
    bot.router.by_phone["222"] = "digital"
    run(bot.handle(msg("hola", pnid="222", name="Ana Pérez")))
    assert sender.sent[-1][0] == "text" and "VitaHub Digital" in sender.last and "Hola Ana" in sender.last


def test_handoff_pauses_bot_and_notifies_owner():
    bot, sender = make_bot(owner_notify_number="56999999999")
    run(bot.handle(msg("tienen magnesio?")))
    run(bot.handle(msg("quiero hablar con un asesor")))
    assert sender.sent[-1][1] == "56999999999" and "VitaHub Chile" in sender.last
    count = len(sender.sent)
    run(bot.handle(msg("mi pedido es el 1234")))
    assert len(sender.sent) == count  # bot en silencio
    run(bot.handle(msg("menú")))
    assert len(sender.sent) == count + 1


def test_owner_manual_reply_pauses_bot():
    bot, sender = make_bot()
    bot.owner_replied("SHARED", "56911111111")
    run(bot.handle(msg("tienen magnesio?")))
    assert sender.sent == []


def test_duplicate_webhook_delivery_is_ignored():
    bot, sender = make_bot()
    m = msg("tienen magnesio?")
    run(bot.handle(m))
    run(bot.handle(m))
    assert len(sender.sent) == 1


def test_ai_reply_and_handoff_tag():
    class FakeAI:
        async def answer(self, brand, products, history):
            assert brand.key == "vitahubchile" and len(products) > 10 and history[-1]["role"] == "user"
            return "Envíame tu número de pedido", True

    bot, sender = make_bot()
    bot.ai = FakeAI()
    run(bot.handle(msg("donde esta mi pedido de creatina")))
    assert sender.last == "Envíame tu número de pedido"
    assert bot.session("SHARED", "56911111111").human_until > 0


# ---------- webhook ----------
PAYLOAD = {
    "entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": "SHARED"},
        "contacts": [{"wa_id": "56911111111", "profile": {"name": "Ana"}}],
        "messages": [{"from": "56911111111", "id": "wamid.1", "type": "text", "text": {"body": "tienen berberina?"}}],
    }}]}]
}


def test_parse_messages():
    incoming, echoes = parse_messages(PAYLOAD)
    assert incoming[0].text == "tienen berberina?" and incoming[0].name == "Ana" and not echoes


def test_webhook_rejects_messages_without_app_secret():
    bot, sender = make_bot()
    client = TestClient(create_app(bot.settings, bot))
    assert client.post("/webhook", json=PAYLOAD).status_code == 403
    assert sender.sent == []


def test_webhook_verify_signature_and_reply():
    bot, sender = make_bot(verify_token="tok", app_secrets=["secret"])
    client = TestClient(create_app(bot.settings, bot))
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "tok", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    assert client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "x"}).status_code == 403

    body = json.dumps(PAYLOAD).encode()
    assert client.post("/webhook", content=body, headers={"x-hub-signature-256": "sha256=bad"}).status_code == 403
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert client.post("/webhook", content=body, headers={"x-hub-signature-256": sig}).status_code == 200
    assert "Berberina" in sender.last


def test_kapso_webhook_checks_signature_and_handles_manual_reply():
    bot, sender = make_bot(kapso_webhook_secret="kapso-secret")
    client = TestClient(create_app(bot.settings, bot))
    body = json.dumps(PAYLOAD).encode()
    assert client.post("/webhook/kapso", content=body).status_code == 403

    def post(payload):
        body = json.dumps(payload).encode()
        signature = hmac.new(b"kapso-secret", body, hashlib.sha256).hexdigest()
        return client.post("/webhook/kapso", content=body, headers={"X-Webhook-Signature": signature})

    echo = {"entry": [{"changes": [{"field": "smb_message_echoes", "value": {
        "metadata": {"phone_number_id": "SHARED"}, "message_echoes": [{"to": "56911111111"}]
    }}]}]}
    assert post(echo).status_code == 200
    assert post(PAYLOAD).status_code == 200
    assert sender.sent == []


def test_kapso_client_uses_proxy_and_project_key():
    async def send():
        def handle(request):
            assert str(request.url) == "https://api.kapso.ai/meta/whatsapp/v24.0/123/messages"
            assert request.headers["X-API-Key"] == "test-key"
            assert json.loads(request.content)["text"]["body"] == "Hola"
            return httpx.Response(200)

        sender = WhatsAppClient(Settings(kapso_api_key="test-key"))
        await sender.http.aclose()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            sender.http = client
            assert await sender.send_text("123", "56911111111", "Hola")

    run(send())
