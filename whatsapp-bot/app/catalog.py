"""Catálogo por tienda: en vivo desde el /products.json público de Shopify, con caché
y una copia local (data/catalog_<tienda>.json) como respaldo si la tienda no responde."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import httpx

from .config import ROOT, Brand, normalize

log = logging.getLogger("bot.catalog")

STOPWORDS = {
    "pack", "para", "con", "los", "las", "del", "por", "una", "uno", "que", "combo", "unidades",
    "tu", "de", "el", "la", "en", "y", "mg", "ml", "kg", "gr", "caps", "x", "x2", "x3", "ella",
    "quiero", "tienen", "tienes", "hay", "venden", "precio", "precios", "vale", "cuesta", "cuanto",
    "hola", "buenas", "me", "un", "es", "al", "lo", "necesito", "busco", "comprar", "info",
    "informacion", "sobre", "mas", "si", "no", "como", "cual", "cuales", "producto", "productos",
}


@dataclass
class Product:
    title: str
    handle: str
    type: str
    price: int
    available: bool = True

    def url(self, brand: Brand) -> str:
        base = brand.website.rstrip("/") or f"https://{brand.shopify_domain}"
        return f"{base}/products/{self.handle}"


def money(amount: int, currency: str = "CLP") -> str:
    return "$" + f"{amount:,}".replace(",", ".") + ("" if currency == "CLP" else f" {currency}")


class Catalog:
    def __init__(self, ttl_minutes: float = 15):
        self.ttl = ttl_minutes * 60
        self._cache: dict[str, tuple[float, list[Product]]] = {}

    def _seed(self, brand: Brand) -> list[Product]:
        path = ROOT / "data" / f"catalog_{brand.key}.json"
        if not path.exists():
            return []
        return [Product(**p) for p in json.loads(path.read_text(encoding="utf-8"))]

    async def _fetch(self, brand: Brand) -> list[Product]:
        products: list[Product] = []
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            for page in range(1, 5):
                r = await client.get(
                    f"https://{brand.shopify_domain}/products.json", params={"limit": 250, "page": page}
                )
                r.raise_for_status()
                batch = r.json().get("products", [])
                for p in batch:
                    variants = p.get("variants") or [{}]
                    prices = [float(v["price"]) for v in variants if v.get("price")]
                    products.append(
                        Product(
                            title=p["title"],
                            handle=p["handle"],
                            type=p.get("product_type") or "",
                            price=int(min(prices)) if prices else 0,
                            available=any(v.get("available", True) for v in variants),
                        )
                    )
                if len(batch) < 250:
                    break
        return products

    async def products(self, brand: Brand) -> list[Product]:
        cached = self._cache.get(brand.key)
        if cached and time.time() - cached[0] < self.ttl:
            return cached[1]
        products: list[Product] = []
        if brand.shopify_domain:
            try:
                products = await self._fetch(brand)
            except Exception as e:  # la tienda no respondió: usar lo último conocido
                log.warning("No se pudo leer el catálogo de %s: %s", brand.key, e)
                products = cached[1] if cached else []
        products = products or self._seed(brand)
        self._cache[brand.key] = (time.time(), products)
        return products

    def cached(self, brand: Brand) -> list[Product]:
        cached = self._cache.get(brand.key)
        return cached[1] if cached else self._seed(brand)

    def terms(self, brand: Brand) -> set[str]:
        """Palabras distintivas de los nombres de producto (para detectar la tienda)."""
        out: set[str] = set()
        for p in self.cached(brand):
            out |= {t for t in normalize(p.title).split() if len(t) >= 5 and t not in STOPWORDS}
        return out

    async def search(self, brand: Brand, query: str, limit: int = 4) -> list[Product]:
        words = [w for w in normalize(query).split() if len(w) >= 3 and w not in STOPWORDS]
        if not words:
            return []
        scored = []
        for p in await self.products(brand):
            title = set(normalize(p.title).split())
            score = sum(1 for w in words if w in title or w.rstrip("s") in title)
            if score and p.available:
                scored.append((score, p))
        scored.sort(key=lambda sp: (-sp[0], sp[1].price))
        return [p for _, p in scored[:limit]]
