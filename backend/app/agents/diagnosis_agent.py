"""Diagnosis agent - turns retrieved runbook evidence into a cited root-cause
analysis.

With Claude available this is a constrained synthesis call: only the
numbered passages may be used, and every claim must carry a citation marker.
Without a key it degrades to *extractive* selection rather than pretending to
reason: the highest-scoring sentences from the evidence are selected by
IDF-weighted overlap with the incident's own error text and returned verbatim
with their citation numbers.
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from app.agents.prompts import DIAGNOSIS_SYSTEM
from app.agents.state import AgentState, RunContext, trace_event
from app.logging_config import get_logger
from app.rag.embeddings import tokenize
from app.telemetry import span

log = get_logger(__name__)

_UNWRAP_RE = re.compile(r"(?<![.!?:;|])\n(?![\n\s]|[-*|#])")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?!\[\d)|\n{2,}|\n(?=\s*[-*])")
_BULLET_PREFIX_RE = re.compile(r"^\s*[-*]\s+")
_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "for",
    "on",
    "is",
    "are",
    "be",
    "with",
    "that",
    "this",
    "it",
    "as",
    "at",
    "by",
    "from",
    "can",
    "error",
    "failed",
    "failure",
}


class DiagnosisOutput(BaseModel):
    diagnosis: str = Field(description="Markdown root-cause analysis with [n] citation markers.")
    used_citations: list[int] = Field(default_factory=list)
    insufficient_evidence: bool = Field(default=False)


def split_sentences(text: str) -> list[str]:
    body = text.split("]\n", 1)[-1] if text.startswith("[") else text
    body = _UNWRAP_RE.sub(" ", body)
    parts = []
    for raw in _SENTENCE_RE.split(body):
        cleaned = " ".join(_BULLET_PREFIX_RE.sub("", raw).split())
        if len(cleaned) > 25 and not cleaned.startswith(("#", "|", "---")):
            parts.append(cleaned)
    return parts


def extractive_diagnosis(
    incident_text: str, retrieved: list[dict], limit: int = 4
) -> DiagnosisOutput:
    if not retrieved:
        return DiagnosisOutput(
            diagnosis=(
                "No runbook or past-incident passages matched this error. A human "
                "should investigate from the raw log evidence directly."
            ),
            insufficient_evidence=True,
        )

    doc_tokens = [set(tokenize(item["content"])) for item in retrieved]
    n_docs = len(doc_tokens) or 1
    df = Counter()
    for tokens in doc_tokens:
        df.update(tokens)

    q_tokens = [t for t in tokenize(incident_text) if t not in _STOPWORDS and len(t) > 2]
    q_set = set(q_tokens) or set(tokenize(incident_text))

    scored: list[tuple[float, int, str]] = []
    for item in retrieved:
        index = item["index"]
        rank_boost = 1.0 / math.sqrt(index)
        heading_terms = set(tokenize(item.get("heading", ""))) & q_set
        for sentence in split_sentences(item["content"]):
            s_tokens = set(tokenize(sentence))
            direct = q_set & s_tokens
            inherited = heading_terms - direct
            if not direct:
                continue
            idf = {t: math.log(1 + n_docs / (1 + df[t])) for t in direct | inherited}
            weight = sum(idf[t] for t in direct) + 0.6 * sum(idf[t] for t in inherited)
            breadth = len(direct | inherited) / max(1, len(q_set))
            length_penalty = 1.0 / (1.0 + abs(len(sentence) - 160) / 320)
            scored.append((weight * (0.5 + breadth) * rank_boost * length_penalty, index, sentence))

    scored.sort(key=lambda row: row[0], reverse=True)

    chosen: list[tuple[int, str]] = []
    seen: set[str] = set()
    per_passage: Counter = Counter()
    for _, index, sentence in scored:
        key = sentence[:80].lower()
        if key in seen or per_passage[index] >= 2:
            continue
        seen.add(key)
        per_passage[index] += 1
        chosen.append((index, sentence))
        if len(chosen) >= limit:
            break

    if not chosen:
        top = retrieved[0]
        return DiagnosisOutput(
            diagnosis=(
                f"The closest match is **{top['document_title']}** [{top['index']}], but it "
                "does not clearly cover this error signature."
            ),
            used_citations=[top["index"]],
            insufficient_evidence=True,
        )

    chosen.sort(key=lambda row: row[0])
    bullets = "\n".join(f"- {sentence} [{index}]" for index, sentence in chosen)
    diagnosis = (
        f"Likely relevant guidance from the runbooks:\n\n{bullets}\n\n"
        "_Extractive summary (demo mode): sentences are quoted directly from the "
        "cited documents._"
    )
    return DiagnosisOutput(
        diagnosis=diagnosis,
        used_citations=sorted({index for index, _ in chosen}),
        insufficient_evidence=False,
    )


def diagnosis_node(state: AgentState, config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    started = time.perf_counter()

    incident_text = f"{state['sample_message']}\n{state['sample_stack_trace']}"
    retrieved = state.get("retrieved") or []

    with span(
        "agent.diagnosis",
        incident_id=state["incident_id"],
        retrieved_passages=len(retrieved),
    ):
        user = (
            f"Service: {state['incident_service']}\n"
            f"Severity: {state['incident_severity']}, seen {state['incident_event_count']} times\n"
            f"Error message: {state['sample_message']}\n"
            f"Stack trace:\n{state['sample_stack_trace'] or '(none provided)'}\n\n"
            f"Numbered runbook/postmortem passages:\n\n{state.get('context_block') or '(none found)'}"
        )
        result = ctx.llm.structured(
            agent="diagnosis",
            system=DIAGNOSIS_SYSTEM,
            user=user,
            schema=DiagnosisOutput,
            fallback=lambda: extractive_diagnosis(incident_text, retrieved),
        )
    ctx.usage.add("diagnosis", result)
    output: DiagnosisOutput = result.value  # type: ignore[assignment]

    valid_indices = {item["index"] for item in retrieved}
    used = [c for c in output.used_citations if c in valid_indices]

    return {
        "diagnosis": output.diagnosis.strip(),
        "used_citations": used,
        "insufficient_evidence": output.insufficient_evidence or not retrieved,
        "trace": [
            trace_event(
                ctx,
                "diagnosis",
                f"Drafted diagnosis citing {len(used)} passage(s)"
                if used
                else "Drafted diagnosis with no usable citations",
                status="warning" if output.insufficient_evidence else "ok",
                payload={"used_citations": used, "mode": result.mode},
                started=started,
            )
        ],
    }
