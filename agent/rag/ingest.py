"""
Ingest runbook Markdown files into the Supabase pgvector store.

Chunking strategy:
  1. MarkdownHeaderTextSplitter splits on H2 headings (## ).
  2. RecursiveCharacterTextSplitter further splits sections > 1000 chars
     with 100-char overlap.

Embeddings are generated locally via sentence-transformers (BAAI/bge-small-en-v1.5,
384 dimensions). The first run downloads ~130MB of model weights — this is expected
one-time behaviour and is logged clearly.

Idempotency: each chunk is identified by a SHA-256 hash of (source_file,
chunk_index, content) stored in metadata.hash. On re-ingestion, existing
chunks whose hash matches are skipped; changed or new chunks are upserted.

Usage:
    py -3.12 -m agent.rag.ingest                         # ingest all runbooks
    py -3.12 -m agent.rag.ingest --file oomkilled-pod.md # ingest one file
    py -3.12 -m agent.rag.ingest --dry-run               # parse and chunk, no DB writes
"""

import argparse
import hashlib
import pathlib
import sys
from dataclasses import dataclass

import frontmatter
import structlog
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)
from sentence_transformers import SentenceTransformer
from supabase import Client, create_client

from agent.rag.settings import settings

log = structlog.get_logger()

RUNBOOKS_DIR = pathlib.Path(__file__).parent.parent.parent / "docs" / "runbooks"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100

_header_splitter = MarkdownHeaderTextSplitter(
    headers_to_split_on=[("##", "section")],
    strip_headers=False,
)
_char_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
)


@dataclass
class Chunk:
    title: str
    source_file: str
    chunk_index: int
    content: str
    content_hash: str
    metadata: dict


def _chunk_file(md_path: pathlib.Path) -> list[Chunk]:
    post = frontmatter.load(str(md_path))
    body: str = post.content
    fm_meta: dict = dict(post.metadata)

    # Title: use frontmatter title or first H1 line
    title = fm_meta.get("title") or ""
    if not title:
        for line in body.splitlines():
            if line.startswith("# "):
                title = line.lstrip("# ").strip()
                break
    if not title:
        title = md_path.stem

    header_docs = _header_splitter.split_text(body)
    char_docs = _char_splitter.split_documents(header_docs)

    chunks = []
    for idx, doc in enumerate(char_docs):
        content = doc.page_content.strip()
        if not content:
            continue
        h = hashlib.sha256(
            f"{md_path.name}|{idx}|{content}".encode()
        ).hexdigest()
        meta = {**fm_meta, **doc.metadata, "hash": h}
        chunks.append(
            Chunk(
                title=title,
                source_file=md_path.name,
                chunk_index=idx,
                content=content,
                content_hash=h,
                metadata=meta,
            )
        )
    return chunks


def _load_model() -> SentenceTransformer:
    log.info(
        "ingest.model_loading",
        model=EMBEDDING_MODEL,
        note="First run downloads ~130MB of model weights — this is a one-time operation.",
    )
    model = SentenceTransformer(EMBEDDING_MODEL)
    log.info("ingest.model_ready", model=EMBEDDING_MODEL)
    return model


def _fetch_existing_hashes(client: Client, source_file: str) -> dict[int, str]:
    response = (
        client.table("runbooks")
        .select("chunk_index, metadata")
        .eq("source_file", source_file)
        .execute()
    )
    return {
        row["chunk_index"]: row["metadata"].get("hash", "")
        for row in (response.data or [])
    }


def _upsert_chunk(client: Client, chunk: Chunk, embedding: list[float]) -> None:
    client.table("runbooks").upsert(
        {
            "title": chunk.title,
            "source_file": chunk.source_file,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "embedding": embedding,
            "metadata": chunk.metadata,
        },
        on_conflict="source_file,chunk_index",
    ).execute()


def ingest_files(
    md_paths: list[pathlib.Path],
    dry_run: bool = False,
) -> None:
    client: Client | None = None
    model: SentenceTransformer | None = None

    if not dry_run:
        client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        model = _load_model()

    total_files = 0
    total_chunks = 0
    total_upserted = 0
    total_skipped = 0

    for md_path in md_paths:
        log.info("ingest.file_start", file=md_path.name)
        chunks = _chunk_file(md_path)
        total_files += 1
        total_chunks += len(chunks)

        if dry_run:
            log.info("ingest.dry_run", file=md_path.name, chunks=len(chunks))
            for c in chunks:
                log.debug("ingest.chunk", index=c.chunk_index, chars=len(c.content))
            continue

        existing = _fetch_existing_hashes(client, md_path.name)  # type: ignore[arg-type]

        to_upsert = [
            c for c in chunks
            if existing.get(c.chunk_index) != c.content_hash
        ]
        skipped = len(chunks) - len(to_upsert)
        total_skipped += skipped

        if not to_upsert:
            log.info("ingest.file_skip", file=md_path.name, reason="all chunks unchanged")
            continue

        contents = [c.content for c in to_upsert]
        embeddings = model.encode(  # type: ignore[union-attr]
            contents,
            normalize_embeddings=True,
            show_progress_bar=False,
        )

        for chunk, emb in zip(to_upsert, embeddings):
            _upsert_chunk(client, chunk, emb.tolist())  # type: ignore[arg-type]

        total_upserted += len(to_upsert)
        log.info(
            "ingest.file_done",
            file=md_path.name,
            upserted=len(to_upsert),
            skipped=skipped,
        )

    log.info(
        "ingest.summary",
        files=total_files,
        chunks=total_chunks,
        upserted=total_upserted,
        skipped=total_skipped,
        dry_run=dry_run,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest runbooks into Supabase pgvector.")
    parser.add_argument(
        "--file",
        metavar="FILENAME",
        help="Ingest a single file by name (e.g. oomkilled-pod.md).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and chunk without writing to the database.",
    )
    args = parser.parse_args()

    if args.file:
        target = RUNBOOKS_DIR / args.file
        if not target.exists():
            log.error("ingest.file_not_found", path=str(target))
            sys.exit(1)
        paths = [target]
    else:
        paths = sorted(RUNBOOKS_DIR.glob("*.md"))
        if not paths:
            log.warning("ingest.no_files", directory=str(RUNBOOKS_DIR))
            sys.exit(0)

    ingest_files(paths, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
