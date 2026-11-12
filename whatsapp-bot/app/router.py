"""Decide a qué tienda pertenece una conversación."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Brand, has_phrase, normalize

STRONG, PRODUCT, NORMAL = 5, 2, 1


@dataclass
class Decision:
    brand: str | None  # None = no se pudo determinar: hay que preguntar
    locked: bool = False  # True si el número de WhatsApp es exclusivo de la tienda
    reason: str = ""


class BrandRouter:
    def __init__(self, brands: dict[str, Brand], catalog_terms=None):
        self.brands = brands
        self.catalog_terms = catalog_terms or (lambda brand: set())
        self.by_phone = {pnid: b.key for b in brands.values() for pnid in b.phone_number_ids}

    def scores(self, text: str) -> dict[str, int]:
        norm = normalize(text)
        words = set(norm.split())
        out = {}
        for b in self.brands.values():
            s = sum(STRONG for k in b.strong_keywords if has_phrase(norm, k))
            s += sum(NORMAL for k in b.keywords if has_phrase(norm, k))
            s += PRODUCT * len(words & self.catalog_terms(b))
            if b.shopify_domain and b.shopify_domain in text.lower():
                s += STRONG
            out[b.key] = s
        return out

    def _winner(self, text: str) -> tuple[str | None, int, dict[str, int]]:
        scores = self.scores(text)
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        (top, top_s), second_s = ranked[0], ranked[1][1] if len(ranked) > 1 else 0
        return (top if top_s > second_s else None), top_s, scores

    def decide(
        self, *, phone_number_id: str, text: str = "", referral_text: str = "", current: str | None = None
    ) -> Decision:
        # 1) Número exclusivo de una tienda
        if phone_number_id in self.by_phone:
            return Decision(self.by_phone[phone_number_id], locked=True, reason="numero")
        if len(self.brands) == 1:
            return Decision(next(iter(self.brands)), locked=True, reason="unica")

        # 2) Anuncio de origen (Click-to-WhatsApp)
        if referral_text:
            winner, _, _ = self._winner(referral_text)
            if winner:
                return Decision(winner, reason="anuncio")

        # 3) Contenido del mensaje
        winner, top, scores = self._winner(text)
        if current:
            # Solo cambia de tienda con una señal clara y sin señales de la tienda actual
            if winner and winner != current and top >= PRODUCT and scores.get(current, 0) == 0:
                return Decision(winner, reason="cambio")
            return Decision(current, reason="sesion")
        if winner:
            return Decision(winner, reason="mensaje")
        return Decision(None, reason="ambiguo")
