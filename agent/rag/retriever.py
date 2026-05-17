"""
RunbookRetriever: queries the Supabase pgvector store for runbook chunks
relevant to a given incident description.

Usage:
    from agent.rag.retriever import get_retriever

    retriever = get_retriever()
    results = retriever.retrieve("OOMKilled with memory limit 128Mi", k=3)
    for r in results:
        print(r.title, r.similarity)
"""

from __future__ import annotations

import uuid

import structlog
from pydantic import BaseModel, field_validator
from sentence_transformers import SentenceTransformer
from supabase import Client, create_client

from agent.rag.settings import settings

log = structlog.get_logger()

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


class Runbook(BaseModel):
    id: uuid.UUID
    title: str
    source_file: str
    content: str
    similarity: float
    metadata: dict

    @field_validator("similarity")
    @classmethod
    def clamp_similarity(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


class RunbookRetriever:
    def __init__(self, client: Client, model: SentenceTransformer) -> None:
        self._client = client
        self._model = model

    def retrieve(self, query: str, k: int = 3) -> list[Runbook]:
        if not query or not query.strip():
            return []
        if not 1 <= k <= 20:
            raise ValueError(f"k must be between 1 and 20, got {k}")

        embedding = self._model.encode(
            query,
            normalize_embeddings=True,
        ).tolist()

        response = self._client.rpc(
            "match_runbooks",
            {"query_embedding": embedding, "match_count": k},
        ).execute()

        rows = response.data or []
        results = []
        for row in rows:
            try:
                results.append(Runbook(**row))
            except Exception as exc:
                log.warning("retriever.parse_error", row_id=row.get("id"), error=str(exc))
        return results


_retriever: RunbookRetriever | None = None


def get_retriever() -> RunbookRetriever:
    global _retriever
    if _retriever is None:
        log.info("retriever.init", model=EMBEDDING_MODEL)
        client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        model = SentenceTransformer(EMBEDDING_MODEL)
        _retriever = RunbookRetriever(client=client, model=model)
        log.info("retriever.ready")
    return _retriever
