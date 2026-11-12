"""Configuración por variables de entorno y carga de brands.yaml."""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def normalize(text: str) -> str:
    """minúsculas, sin tildes, solo letras/números separados por un espacio."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def has_phrase(norm_text: str, phrase: str) -> bool:
    """Coincidencia por palabra completa (o frase) sobre texto ya normalizado."""
    p = normalize(phrase)
    return bool(p) and f" {p} " in f" {norm_text} "


@dataclass
class Faq:
    id: str
    title: str
    keywords: list[str]
    answer: str
    handoff: bool = False


@dataclass
class Brand:
    key: str
    name: str
    button_title: str
    emoji: str = ""
    tagline: str = ""
    website: str = ""
    shopify_domain: str = ""
    currency: str = "CLP"
    phone_number_ids: list[str] = field(default_factory=list)
    strong_keywords: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    contact: dict = field(default_factory=dict)
    highlights: list[str] = field(default_factory=list)
    faqs: list[Faq] = field(default_factory=list)
    ai_guidelines: str = ""


def load_brands(path: Path | None = None) -> dict[str, Brand]:
    path = path or Path(os.getenv("BRANDS_FILE", ROOT / "brands.yaml"))
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["brands"]
    brands: dict[str, Brand] = {}
    for key, b in raw.items():
        faqs = [Faq(**{**f, "answer": f["answer"].strip()}) for f in b.pop("faqs", [])]
        b["phone_number_ids"] = [str(x) for x in b.get("phone_number_ids") or []]
        brands[key] = Brand(key=key, faqs=faqs, **b)
    # BRAND_PHONE_MAP="<phone_number_id>:<tienda>,..." permite fijar números sin tocar el YAML
    for pair in filter(None, os.getenv("BRAND_PHONE_MAP", "").replace(" ", "").split(",")):
        pnid, _, key = pair.partition(":")
        if key in brands and pnid not in brands[key].phone_number_ids:
            brands[key].phone_number_ids.append(pnid)
    return brands


@dataclass
class Settings:
    verify_token: str = ""
    app_secrets: list[str] = field(default_factory=list)
    whatsapp_token: str = ""
    graph_version: str = "v23.0"
    owner_notify_number: str = ""
    handoff_hours: float = 12
    session_hours: float = 24
    catalog_ttl_minutes: float = 15
    ai_enabled: bool = False
    ai_model: str = "claude-opus-5-5"
    kapso_api_key: str = ""
    kapso_webhook_secret: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.getenv
        has_key = bool(env("ANTHROPIC_API_KEY") or env("ANTHROPIC_AUTH_TOKEN"))
        return cls(
            verify_token=env("WHATSAPP_VERIFY_TOKEN", ""),
            app_secrets=[s for s in env("META_APP_SECRET", "").replace(" ", "").split(",") if s],
            whatsapp_token=env("WHATSAPP_TOKEN", ""),
            graph_version=env("GRAPH_API_VERSION", "v23.0"),
            owner_notify_number=re.sub(r"\D", "", env("OWNER_NOTIFY_NUMBER", "")),
            handoff_hours=float(env("HANDOFF_HOURS", "12")),
            session_hours=float(env("SESSION_HOURS", "24")),
            catalog_ttl_minutes=float(env("CATALOG_TTL_MINUTES", "15")),
            ai_enabled=has_key and env("AI_ENABLED", "true").lower() != "false",
            ai_model=env("CLAUDE_MODEL", "claude-opus-5-5"),
            kapso_api_key=env("KAPSO_API_KEY", ""),
            kapso_webhook_secret=env("KAPSO_WEBHOOK_SECRET", ""),
        )


def token_for(settings: Settings, phone_number_id: str) -> str:
    """Token por número (WHATSAPP_TOKEN_<phone_number_id>) o el token general."""
    return os.getenv(f"WHATSAPP_TOKEN_{phone_number_id}", "") or settings.whatsapp_token
