"""Hybrid retrieval over the seeded runbook corpus."""

from __future__ import annotations

import pytest

from app.rag.retriever import build_context_block, retrieve


@pytest.mark.parametrize(
    ("query", "expected_document"),
    [
        ("database connection pool exhausted timeout", "Database Connection Pool Runbook"),
        ("payment processor upstream timeout after deploy", "Payment Gateway Timeout Runbook"),
        ("heap usage climbing OutOfMemoryError", "Memory Leak and OOM Runbook"),
        ("rate limit exceeded queue depth traffic spike", "Capacity and Autoscaling Runbook"),
        ("rollback to last known good version", "Deployment Rollback Policy"),
    ],
)
def test_retrieval_finds_the_right_runbook(db, query, expected_document):
    results = retrieve(db, query, top_k=4)
    assert results, f"no results for {query!r}"
    titles = {r.document_title for r in results}
    assert expected_document in titles


def test_retrieval_returns_ranked_unique_chunks(db):
    results = retrieve(db, "connection pool exhausted", top_k=6)
    assert len({r.chunk_id for r in results}) == len(results)
    assert [r.rank for r in results] == list(range(1, len(results) + 1))


def test_context_block_is_numbered(db):
    results = retrieve(db, "memory leak restart", top_k=3)
    block = build_context_block(results)
    assert block.startswith("[1]")
    assert "source=" in block and "section=" in block
