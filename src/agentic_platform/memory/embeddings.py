"""Embedding abstraction (ADR-035).

* `HashingEmbedder` is local and deterministic: feature hashing of words and word
  pairs. It is **lexical, not semantic** ("car" and "automobile" do not match).
  It needs no network or key, so it is the default for development, tests and
  machines without a provider.
* `VoyageEmbedder` calls the Voyage AI embeddings API (Anthropic's recommended
  embeddings provider) and gives real semantic retrieval. Select it with
  MEMORY_EMBEDDING_PROVIDER=voyage and VOYAGE_API_KEY.

Changing the model makes stored vectors unsearchable for the new model, because
searches filter on `embedding_model`. Re-embed (re-ingest) after a change.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Sequence
from itertools import pairwise
from typing import Any

import httpx

from agentic_platform.core.config import MemorySettings
from agentic_platform.core.errors import (
    ConfigurationError,
    EmbeddingError,
    EmbeddingUnavailableError,
)
from agentic_platform.memory.ports import Embedder

_TOKEN = re.compile(r"\w+", re.UNICODE)


class HashingEmbedder:
    def __init__(self, dimensions: int = 256) -> None:
        if dimensions < 8:
            raise ValueError("dimensions must be >= 8")
        self._dims = dimensions

    @property
    def model_id(self) -> str:
        return f"hashing-v1-{self._dims}"

    @property
    def dimensions(self) -> int:
        return self._dims

    def _embed(self, text: str) -> list[float]:
        tokens = [t for t in _TOKEN.findall(text.lower()) if len(t) > 1 or t.isdigit()]
        features = Counter(tokens)
        features.update(f"{a} {b}" for a, b in pairwise(tokens))
        vector = [0.0] * self._dims
        for feature, count in features.items():
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self._dims
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class VoyageEmbedder:
    """Voyage AI `POST /v1/embeddings`. Batches inputs; maps HTTP failures to
    `EmbeddingError` (retryable: `EmbeddingUnavailableError`)."""

    BASE_URL = "https://api.voyageai.com/v1"
    MAX_BATCH = 128

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "voyage-3.5",
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        if not api_key:
            raise ConfigurationError("VOYAGE_API_KEY is required for the voyage embedder.")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._dims: int | None = None

    @property
    def model_id(self) -> str:
        return f"voyage:{self._model}"

    @property
    def dimensions(self) -> int:
        if self._dims is None:
            raise EmbeddingError("Dimensions are known after the first embedding call.")
        return self._dims

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), self.MAX_BATCH):
            out.extend(await self._call(list(texts[start : start + self.MAX_BATCH]), "document"))
        return out

    async def embed_query(self, text: str) -> list[float]:
        return (await self._call([text], "query"))[0]

    async def _call(self, texts: list[str], input_type: str) -> list[list[float]]:
        if not texts:
            return []
        payload = {"input": texts, "model": self._model, "input_type": input_type}
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout
            ) as client:
                response = await client.post(
                    f"{self._base_url}/embeddings",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.TimeoutException as exc:
            raise EmbeddingUnavailableError("Embedding request timed out.") from exc
        except httpx.HTTPError as exc:
            raise EmbeddingUnavailableError(
                f"Embedding request failed: {type(exc).__name__}"
            ) from exc

        status = response.status_code
        if status == 429 or status >= 500:
            raise EmbeddingUnavailableError(f"Embedding provider returned HTTP {status}.")
        if status >= 400:
            raise EmbeddingError(f"Embedding provider rejected the request: HTTP {status}.")
        try:
            body: dict[str, Any] = response.json()
            data = sorted(body["data"], key=lambda item: item["index"])
            vectors = [[float(x) for x in item["embedding"]] for item in data]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError("Embedding provider returned an unexpected body.") from exc
        if len(vectors) != len(texts) or len({len(v) for v in vectors}) != 1:
            raise EmbeddingError("Embedding provider returned inconsistent vectors.")
        self._dims = len(vectors[0])
        return vectors


def create_embedder(settings: MemorySettings) -> Embedder:
    if settings.embedding_provider == "voyage":
        key = settings.voyage_api_key.get_secret_value() if settings.voyage_api_key else ""
        return VoyageEmbedder(
            api_key=key,
            model=settings.embedding_model or "voyage-3.5",
            timeout_seconds=settings.embedding_timeout_seconds,
        )
    return HashingEmbedder(settings.embedding_dimensions)
