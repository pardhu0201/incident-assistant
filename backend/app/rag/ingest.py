"""Runbook/postmortem ingestion: parse -> chunk -> embed -> persist."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import SEED_CORPUS_DIR
from app.db.models import Chunk, Document
from app.logging_config import get_logger
from app.rag.chunking import chunk_document, parse_frontmatter
from app.rag.embeddings import get_embedder
from app.rag.retriever import invalidate_index

log = get_logger(__name__)


@dataclass
class IngestResult:
    document_id: str
    title: str
    source: str
    chunks: int
    status: str


def _checksum(text: str) -> str:
    embedder = get_embedder()
    fingerprint = f"{text}\x00{embedder.name}:{embedder.dim}"
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def ingest_text(
    db: Session, *, raw: str, source: str, fallback_title: str, doc_type: str = "runbook"
) -> IngestResult:
    parsed = parse_frontmatter(raw, fallback_title)
    checksum = _checksum(parsed.body)

    document = db.execute(select(Document).where(Document.source == source)).scalar_one_or_none()
    if document is not None and document.checksum == checksum:
        return IngestResult(document.id, document.title, source, document.chunk_count, "unchanged")

    status = "updated" if document is not None else "created"
    if document is None:
        document = Document(source=source)
        db.add(document)

    document.title = parsed.title
    document.doc_type = parsed.metadata.get("type", doc_type)
    document.checksum = checksum
    db.flush()

    db.execute(delete(Chunk).where(Chunk.document_id == document.id))

    pieces = chunk_document(parsed.title, parsed.body)
    embedder = get_embedder()
    vectors = embedder.embed_documents([p.content for p in pieces])

    for piece, vector in zip(pieces, vectors, strict=True):
        db.add(
            Chunk(
                document_id=document.id,
                ordinal=piece.ordinal,
                heading=piece.heading,
                content=piece.content,
                embedding=[float(x) for x in vector],
            )
        )
    document.chunk_count = len(pieces)
    db.commit()
    invalidate_index()

    log.info("Ingested %-40s %-9s %3d chunks", source, status, len(pieces))
    return IngestResult(document.id, document.title, source, len(pieces), status)


def ingest_seed_corpus(db: Session) -> list[IngestResult]:
    if not SEED_CORPUS_DIR.exists():
        log.warning("Seed corpus directory %s does not exist", SEED_CORPUS_DIR)
        return []
    results = []
    for path in sorted(SEED_CORPUS_DIR.iterdir()):
        if path.is_file() and path.suffix.lower() == ".md":
            results.append(
                ingest_text(
                    db,
                    raw=path.read_text(encoding="utf-8"),
                    source=path.name,
                    fallback_title=path.stem.replace("-", " ").title(),
                )
            )
    return results
