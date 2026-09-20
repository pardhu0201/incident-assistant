"""Retrieval agent - hybrid search over the runbook/postmortem knowledge base."""

from __future__ import annotations

import time

from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState, RunContext, trace_event
from app.config import settings
from app.rag.retriever import build_context_block, retrieve
from app.telemetry import span


def retrieval_node(state: AgentState, config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    started = time.perf_counter()

    query = f"{state['incident_service']} {state['sample_message']} {state['sample_stack_trace']}"

    with span("agent.retrieval", incident_id=state["incident_id"]):
        chunks = retrieve(ctx.db, query, top_k=settings.retrieval_top_k)

    citations = [c.to_citation(i) for i, c in enumerate(chunks, start=1)]
    retrieved = [
        {**citation, "content": chunk.content}
        for citation, chunk in zip(citations, chunks, strict=True)
    ]
    context_block = build_context_block(chunks)

    note = (
        f"{len(chunks)} passage{'s' if len(chunks) != 1 else ''} from "
        f"{len({c.document_title for c in chunks})} document(s)"
        if chunks
        else "No matching runbook passages found"
    )

    return {
        "retrieved": retrieved,
        "context_block": context_block,
        "trace": [
            trace_event(
                ctx,
                "retrieval",
                note,
                status="ok" if chunks else "warning",
                payload={"query": query, "documents": sorted({c.document_title for c in chunks})},
                started=started,
            )
        ],
    }
