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


def test_error_coverage_ignores_service_name_and_measurements():
    from app.agents.verification_agent import query_coverage

    evidence = (
        "checkout api runbook: when the connection pool is exhausted after a timeout, restart"
    )
    svc = "checkout-api"
    # The service name is in every runbook for that service - it must not count.
    assert query_coverage("Printer out of toner", evidence, ignore_terms=svc) == 0.0
    assert query_coverage("checkout api printer out of toner", evidence, ignore_terms=svc) == 0.0
    # Volatile measurements ("28288ms") are not content - a real error is fully covered.
    real = "connection pool exhausted after 28288ms"
    assert query_coverage(real, evidence, ignore_terms=svc) == 1.0
