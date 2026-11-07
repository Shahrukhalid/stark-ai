"""Meaning-based search with the owner's Gemini key (embeddings). Falls back to words when no key is set.

Only short text snippets are sent, and only when a search needs them; vectors are cached in the database.
"""

from __future__ import annotations

import math
from typing import Any

import httpx

API = "https://generativelanguage.googleapis.com/v1beta"
DIMS = 256


class Embedder:
    def __init__(self, api_key: str, transport=None):
        self.key = api_key.strip()
        self.transport = transport
        self.model = ""
        self.failed = False

    def ready(self) -> bool:
        return bool(self.key) and not self.failed

    async def _model(self, c: httpx.AsyncClient) -> str:
        if not self.model:
            r = await c.get("/models", params={"pageSize": 1000})
            r.raise_for_status()
            names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
                     if "embedContent" in (m.get("supportedGenerationMethods") or [])]
            pref = [n for n in names if n.startswith("gemini-embedding")] or [n for n in names if "embedding" in n]
            if not pref:
                raise RuntimeError("no embedding model")
            self.model = sorted(pref)[-1]
        return self.model

    async def embed(self, texts: list[str], kind: str = "RETRIEVAL_DOCUMENT") -> list[list[float]] | None:
        """Vectors for the texts (batches of 100), or None when embeddings aren't available."""
        if not self.ready() or not texts:
            return None
        out: list[list[float]] = []
        try:
            async with httpx.AsyncClient(base_url=API, timeout=30, transport=self.transport,
                                         headers={"x-goog-api-key": self.key}) as c:
                model = await self._model(c)
                for i in range(0, len(texts), 100):
                    body = {"requests": [{"model": f"models/{model}", "content": {"parts": [{"text": t[:2000]}]},
                                          "taskType": kind, "outputDimensionality": DIMS} for t in texts[i:i + 100]]}
                    r = await c.post(f"/models/{model}:batchEmbedContents", json=body)
                    if r.status_code == 429:
                        return None  # free limit: use words this time
                    r.raise_for_status()
                    out += [e.get("values") or [] for e in r.json().get("embeddings", [])]
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError):
            self.failed = True  # stop trying until the engine restarts
            return None
        return out if len(out) == len(texts) else None


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def keyword_score(query: str, text: str) -> float:
    from .core.memory import _tokens

    q = set(_tokens(query))
    if not q:
        return 0.0
    return len(q & set(_tokens(text))) / len(q)


def rank(query: str, items: list[dict[str, Any]], qvec: list[float] | None) -> list[dict[str, Any]]:
    """Hybrid score: meaning (when vectors exist) plus shared words."""
    for it in items:
        sem = cosine(qvec, it.get("vec") or []) if qvec else 0.0
        it["score"] = round(0.7 * sem + 0.3 * keyword_score(query, it["text"]) if qvec else keyword_score(query, it["text"]), 3)
    return sorted((i for i in items if i["score"] > (0.35 if qvec else 0)), key=lambda i: -i["score"])
