"""Verification agent - the quality gate between the diagnosis and a human.

Deliberately does not trust an LLM to grade its own diagnosis. Deterministic
checks run first, in both modes:

* **citation validity** - every `[n]` marker in the diagnosis points at a
  real retrieved passage
* **citation coverage** - share of factual sentences that carry a citation
* **evidence support** - IDF-weighted overlap between each cited sentence
  and the passage it cites
* **fix safety** - the test gate's own pass/fail, plus any preflight
  blockers/warnings the fix agent already surfaced
* **error-vs-evidence relevance** - retrieval always returns *something*
  (nearest neighbours exist even for unrelated text), so an error with no
  matching runbook can still produce a fully-cited, confidently-worded
  diagnosis stitched from loosely-related sentences that happen to share a
  few common words. This compares the incident's own error text against the
  retrieved evidence and gates the whole score by it - the same relevance
  floor used in this project's sibling RAG system, and the reason a
  genuinely unroutable error is scored low rather than diagnosed anyway.

A model's own confidence (when available) is treated as a second opinion
that can only pull the score down, never up - the same calibration approach
used throughout this project's sibling systems.
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from app.agents.diagnosis_agent import split_sentences
from app.agents.prompts import VERIFICATION_SYSTEM
from app.agents.state import AgentState, RunContext, trace_event
from app.config import settings
from app.rag.embeddings import tokenize
from app.telemetry import span

STRONG_MATCH_BM25 = 12.0
RELEVANCE_FLOOR = 0.15

_CITATION_RE = re.compile(r"\[(\d{1,2})\]")
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
    "you",
    "your",
    "will",
    "must",
    "may",
    "can",
    "not",
    "if",
    "any",
    "all",
    "has",
    "have",
}


class VerificationOutput(BaseModel):
    unsupported_claims: list[str] = Field(default_factory=list)
    fix_risk_notes: list[str] = Field(default_factory=list)
    llm_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    recommendation: str = Field(default="proceed", description="proceed | escalate")


def _is_claim_sentence(sentence: str) -> bool:
    cleaned = sentence.strip()
    if len(cleaned) < 30 or cleaned.endswith(("?", ":")):
        return False
    if cleaned.startswith(("_", "#", "|", ">")):
        return False
    words = [w for w in tokenize(cleaned) if w not in _STOPWORDS]
    return len(words) >= 5


def _lexical_support(sentence: str, passage: str, idf: dict[str, float]) -> float:
    s_tokens = [t for t in tokenize(sentence) if t not in _STOPWORDS and len(t) > 2]
    if not s_tokens:
        return 1.0
    p_tokens = set(tokenize(passage))
    total = sum(idf.get(t, 1.0) for t in s_tokens)
    matched = sum(idf.get(t, 1.0) for t in s_tokens if t in p_tokens)
    return matched / total if total else 0.0


def query_coverage(error_text: str, evidence_text: str) -> float:
    """Share of the error's own content words that appear in the evidence."""
    terms = {t for t in tokenize(error_text) if t not in _STOPWORDS and len(t) > 2}
    if not terms:
        return 1.0
    evidence_terms = set(tokenize(evidence_text))
    return len(terms & evidence_terms) / len(terms)


def check_groundedness(error_text: str, diagnosis: str, retrieved: list[dict]) -> dict:
    passages = {item["index"]: item["content"] for item in retrieved}

    df: Counter = Counter()
    for content in passages.values():
        df.update(set(tokenize(content)))
    n_docs = max(1, len(passages))
    idf = {term: math.log(1 + n_docs / (1 + count)) for term, count in df.items()}

    citations = [int(m.group(1)) for m in _CITATION_RE.finditer(diagnosis)]
    invalid = sorted({c for c in citations if c not in passages})

    sentences = [s for s in split_sentences(diagnosis) if _is_claim_sentence(s)]
    cited_sentences = [s for s in sentences if _CITATION_RE.search(s)]
    coverage = (len(cited_sentences) / len(sentences)) if sentences else 1.0

    support_scores: list[float] = []
    weak_sentences: list[str] = []
    for sentence in cited_sentences:
        refs = [int(m.group(1)) for m in _CITATION_RE.finditer(sentence)]
        texts = [passages[r] for r in refs if r in passages]
        if not texts:
            support_scores.append(0.0)
            weak_sentences.append(sentence)
            continue
        best = max(_lexical_support(sentence, text, idf) for text in texts)
        support_scores.append(best)
        if best < 0.45:
            weak_sentences.append(sentence)
    support = sum(support_scores) / len(support_scores) if support_scores else 0.0

    evidence_text = "\n".join(passages.values())
    term_coverage = query_coverage(error_text, evidence_text) if retrieved else 0.0
    lexical_scores = [item.get("lexical_score") for item in retrieved]
    if any(s is not None for s in lexical_scores):
        top_lexical = max(float(s or 0.0) for s in lexical_scores)
        retrieval_strength = min(1.0, top_lexical / STRONG_MATCH_BM25)
        relevance = min(term_coverage, retrieval_strength)
    else:
        relevance = term_coverage

    quality = 0.3 * coverage + 0.5 * support + 0.2 * (1.0 if retrieved else 0.0)
    score = quality * (RELEVANCE_FLOOR + (1.0 - RELEVANCE_FLOOR) * relevance)
    score = max(0.0, min(1.0, score - (0.25 if invalid else 0.0)))

    return {
        "invalid_citations": invalid,
        "citation_coverage": round(coverage, 3),
        "lexical_support": round(support, 3),
        "weak_sentences": weak_sentences[:5],
        "error_relevance": round(relevance, 3),
        "groundedness_score": round(score, 3),
    }


def verification_node(state: AgentState, config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    started = time.perf_counter()

    diagnosis = state.get("diagnosis", "")
    retrieved = state.get("retrieved") or []
    proposed_fix = state.get("proposed_fix")
    test_gate = state.get("test_gate")

    error_text = f"{state['incident_title']} {state.get('sample_message', '')} {state.get('sample_stack_trace', '')}"

    with span("agent.verification", incident_id=state["incident_id"]):
        checks = check_groundedness(error_text, diagnosis, retrieved)

        fix_blockers = list((proposed_fix or {}).get("blockers") or [])
        fix_warnings = list((proposed_fix or {}).get("warnings") or [])
        test_gate_failed = bool(test_gate and not test_gate.get("passed"))

        def deterministic_review() -> VerificationOutput:
            recommendation = "proceed"
            if (
                checks["invalid_citations"]
                or fix_blockers
                or test_gate_failed
                or state.get("insufficient_evidence")
            ):
                recommendation = "escalate"
            return VerificationOutput(
                unsupported_claims=checks["weak_sentences"],
                fix_risk_notes=[*fix_blockers, *fix_warnings],
                llm_confidence=checks["groundedness_score"],
                recommendation=recommendation,
            )

        if state.get("candidate_tool"):
            fix_text = (
                f"tool={proposed_fix['tool_name'] if proposed_fix else 'none'}\n"
                f"arguments={(proposed_fix or {}).get('arguments')}\n"
                f"test_gate_passed={test_gate.get('passed') if test_gate else 'not run'}\n"
                f"blockers={fix_blockers}\nwarnings={fix_warnings}"
            )
        else:
            fix_text = "(no fix proposed)"

        user = (
            f"Incident: {state['incident_title']}\n\n"
            f"Numbered evidence passages:\n{state.get('context_block') or '(none)'}\n\n"
            f"Diagnosis:\n{diagnosis or '(empty)'}\n\n"
            f"Proposed fix:\n{fix_text}\n\n"
            f"Automated checks already computed:\n"
            f"- citation coverage: {checks['citation_coverage']}\n"
            f"- lexical support: {checks['lexical_support']}\n"
            f"- invalid citations: {checks['invalid_citations']}\n"
            f"- test gate passed: {test_gate.get('passed') if test_gate else 'not run'}"
        )
        result = ctx.llm.structured(
            agent="verification",
            system=VERIFICATION_SYSTEM,
            user=user,
            schema=VerificationOutput,
            fallback=deterministic_review,
            max_tokens=2000,
        )
    ctx.usage.add("verification", result)
    review: VerificationOutput = result.value  # type: ignore[assignment]

    if result.mode == "claude":
        blended = 0.5 * checks["groundedness_score"] + 0.5 * review.llm_confidence
        confidence = min(blended, max(checks["groundedness_score"], review.llm_confidence))
    else:
        confidence = checks["groundedness_score"]
    confidence = round(max(0.0, min(1.0, confidence)), 3)

    flags: list[str] = []
    if checks["invalid_citations"]:
        flags.append("invalid_citation")
    if state.get("insufficient_evidence"):
        flags.append("insufficient_evidence")
    if checks["citation_coverage"] < 0.6 and cited_needed(diagnosis):
        flags.append("low_citation_coverage")
    if checks["error_relevance"] < 0.4:
        flags.append("error_not_covered_by_runbooks")
    if test_gate_failed:
        flags.append("test_gate_failed")
    if fix_blockers:
        flags.append("fix_blocked")
    if fix_warnings:
        flags.append("fix_warning")
    if review.unsupported_claims:
        flags.append("model_flagged_claims")

    needs_action = state.get("candidate_tool") not in (None, "")
    low_confidence = confidence < settings.confidence_threshold

    if needs_action and test_gate_failed:
        decision = "test_gate_failed"
    elif needs_action and (fix_blockers or low_confidence or review.recommendation == "escalate"):
        decision = "escalate"
    elif needs_action:
        decision = "approval"
    elif review.recommendation == "escalate" or low_confidence:
        decision = "escalate"
    else:
        decision = "diagnosed"

    verification = {
        **checks,
        "llm_confidence": round(review.llm_confidence, 3),
        "confidence": confidence,
        "recommendation": review.recommendation,
        "decision": decision,
        "unsupported_claims": review.unsupported_claims,
        "fix_risk_notes": review.fix_risk_notes,
        "mode": result.mode,
        "threshold": settings.confidence_threshold,
    }

    summary = {
        "diagnosed": "Diagnosis verified - no action required",
        "approval": "Fix passed the test gate - awaiting human approval",
        "test_gate_failed": "Fix failed the automated test gate - blocked before approval",
        "escalate": "Low confidence or unresolved risk - escalating to a human",
    }[decision]
    status = "ok" if decision in {"diagnosed", "approval"} else "error"

    return {
        "verification": verification,
        "confidence": confidence,
        "flags": flags,
        "requires_approval": decision == "approval",
        "status": {
            "diagnosed": "diagnosed",
            "approval": "awaiting_approval",
            "test_gate_failed": "test_gate_failed",
            "escalate": "escalated",
        }[decision],
        "trace": [
            trace_event(
                ctx,
                "verification",
                f"{summary} (confidence {confidence:.2f})",
                status=status,
                payload=verification,
                started=started,
            )
        ],
    }


def cited_needed(diagnosis: str) -> bool:
    return bool([s for s in split_sentences(diagnosis) if _is_claim_sentence(s)])
