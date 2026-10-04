# Architecture

## Pipeline overview

```
POST /api/logs/ingest ─────► ingestion.pipeline ────► fingerprint + cluster
                                                              │
                                                              ▼
                                                       Incident (open/…)

POST /api/incidents/{id}/analyze
        │
        ▼
  agents.runner.analyse_incident
        │  (wraps the whole run in an "incident.analyse" OTel span,
        │   persists an AgentRun + one AgentRunEvent per stage)
        ▼
┌──────────────────────────── LangGraph StateGraph ────────────────────────────┐
│                                                                               │
│  retrieval ─► diagnosis ─► report ─► fix ─► test_gate ─► verification ─► ??? │
│                                                              │                │
│                                              ┌───────────────┴────────────┐   │
│                                              ▼                            ▼   │
│                                        approval_gate                    END   │
│                                     (persists Approval,                       │
│                                      never executes)                          │
└───────────────────────────────────────────────────────────────────────────────┘
        │
        ▼ (only on human decision)
POST /api/approvals/{id}/decision ──► services.approvals.decide
        │  re-validate args, re-run preflight, execute() only on "approve"
        ▼
   tools.registry.TOOLS[name].execute(db, args, approval_id)
```

Each of the seven agent stages runs inside its own `span("agent.<stage>", ...)`
context manager (`app/telemetry/__init__.py`), so the dashboard's per-span latency
chart and the incident detail page's Agent Trace panel are showing **real**
OpenTelemetry data, not synthetic timings.

## Stage-by-stage

1. **Retrieval** (`agents/retrieval_agent.py`) - hybrid search over the runbook/past-incident
   corpus: BM25 (k1=1.5, b=0.75) fused with dense cosine similarity from a
   dependency-free hashed bag-of-features embedder (`rag/embeddings.py`), blended via
   normalized-score + normalized-rank (60/40) rather than pure RRF, which flattens
   decisive BM25 margins on a corpus this size. A heading-relevance boost and
   Jaccard-based near-duplicate suppression stand in for MMR.

2. **Diagnosis** (`agents/diagnosis_agent.py`) - an LLM call (Claude, adaptive thinking)
   identifies the likely root cause from the retrieved evidence, citing passage ids.
   With no `ANTHROPIC_API_KEY`, a deterministic extractive fallback builds the same
   shape of answer directly from the top-cited passage, so the rest of the pipeline
   (fix proposal, test gate, verification, approval) is fully exercisable for free.

3. **Report** (`agents/report_agent.py`) - drafts the incident report and resolution
   checklist from the diagnosis and evidence.

4. **Fix** (`agents/fix_agent.py`) - a deterministic, word-boundary keyword match
   (`_matches`, `re.search(rf"\b{keyword}", text)`) over the diagnosis text picks one of
   three tools (`restart_service`, `rollback_deployment`, `scale_service`) and fills its
   arguments from live `ServiceState` (`_fill_gaps`), or proposes nothing if no pattern
   matches.

5. **Test gate** (`agents/test_gate.py`) - before a human ever sees a proposed fix, it
   must pass a named suite of independent checks specific to its tool (e.g.
   `check_no_restart_loop`, `check_target_differs_from_current`,
   `check_replica_capacity`). This is a second, independent safety net alongside each
   tool's own `preflight()` - not a replacement for it.

6. **Verification** (`agents/verification_agent.py`) - scores the diagnosis for
   groundedness: citation validity, citation coverage, IDF-weighted lexical support,
   ungrounded-number detection, and a relevance floor —
   `relevance = min(query_coverage, retrieval_strength)`, where `query_coverage` is the
   share of the error's own content words that appear in the retrieved evidence and
   `retrieval_strength` is the top BM25 score normalized against a "strong match"
   constant. The final confidence is `quality * (RELEVANCE_FLOOR + (1 - RELEVANCE_FLOOR) * relevance)`,
   so an error with no real match in the runbooks is capped low no matter how fluent the
   LLM's answer sounds, and is flagged `error_not_covered_by_runbooks`.

7. **Approval gate** - reached only via the conditional edge `route_after_verification()`,
   only when verification's `decision == "approval"`. It persists an `Approval` row
   (including whether the test gate passed) and stops - the graph has no tool-execution
   node.

## The human-approval boundary

This is the one invariant the whole design exists to protect: **the agent graph can only
propose an action; a separate service, reachable only after a human decision, can
execute it.**

- `tools/registry.py` defines each `ToolSpec` with three separate functions:
  `validate` (Pydantic), `preflight` (deterministic checks against live `ServiceState`),
  and `execute` (the actual mutation).
- The LangGraph pipeline calls `preflight` (via the test gate and the fix agent) but
  never calls `execute`.
- `services/approvals.py::decide()` is the **only** code path that calls `execute()`,
  and it only runs on `decision == "approve"` - it re-validates arguments and re-runs
  preflight immediately before executing, in case service state changed since the
  proposal was made.
- **Exactly-once execution.** A decision first *claims* the approval with an atomic
  `UPDATE ... WHERE status = 'pending'`, so two concurrent approvals cannot both run the
  fix: one wins, the other gets HTTP 409.
- **One live proposal per incident.** Re-analysing an incident retires any older
  pending approval for it (`status = "superseded"`); it was computed against state that
  may no longer hold. Preflight also blocks no-op actions (e.g. scaling to the current
  replica count), so a stale fix can never be reported as "Applied".
- **Optional lock-down.** With `ADMIN_TOKEN` set, approval decisions require an
  `X-Admin-Token` header. Ingestion and analysis stay open - they only propose. This is
  a shared secret, not user auth; a real deployment would sit behind SSO.
- An out-of-scope or low-confidence diagnosis never reaches the approval queue at all;
  `verification_agent.py`'s relevance floor routes it to `escalate` instead, so it can
  never be rubber-stamped by a distracted reviewer.

## Data model

Key tables (`app/db/models.py`):

- `LogEvent` - raw ingested log lines (`occurred_at` always naive UTC - see note below).
- `Incident` - clustered errors (`fingerprint`, `service`, `first_seen`/`last_seen`,
  `event_count`, `status`).
- `Document` / `Chunk` - the runbook/past-incident knowledge base.
- `AgentRun` / `AgentRunEvent` - one row per pipeline execution and one per stage,
  including `AgentRun.flags` (the guardrail flags verification raised).
- `Approval` - a proposed fix awaiting (or having received) a human decision, including
  `test_gate_passed`.
- `AuditLog` - immutable record of every decision and execution outcome.
- `ServiceState` - the simulated 3-service fleet (name/version/replica_count/status)
  that preflight and the test gate check against.
- `OtelSpan` - every finished span, persisted by the custom `DbSpanExporter`.

**Timestamps.** Log-derived times (`LogEvent.occurred_at`, `Incident.first_seen` /
`last_seen`) are stored naive UTC - `_parse_timestamp()` normalizes any aware or
`Z`-suffixed input - so incident-window clustering compares like with like.
Operational times (`ServiceState.last_restarted_at`, approval decisions) are written
aware, but SQLite hands them back *naive* in a later session while Postgres keeps them
aware. Any arithmetic against "now" therefore goes through `db.models.as_utc()` /
`minutes_since()`, which treat a naive value as UTC; subtracting the raw values used
to raise `TypeError` and broke every analysis on a service once it had been restarted.
The regression test re-reads the row in a fresh session, because a test in the
writing session only ever sees the aware in-memory copy.

## Incident clustering

`ingestion/fingerprint.py::compute_fingerprint()` combines the service name with a
normalized error message (numbers, UUIDs, IPs, timestamps, and quoted values stripped
via regex, deliberately **without** `\b` word boundaries around the number pattern,
since a boundary fails to match a digit run immediately followed by a unit suffix like
`28288ms`) and the top stack frame, hashed with BLAKE2b. `ingestion/clustering.py`
folds a new `ERROR`/`CRITICAL`/`FATAL` event into an existing incident on the same
service with a matching fingerprint if it falls within `INCIDENT_WINDOW_SECONDS` of
that incident's last event, otherwise opens a new one. Severity only ever escalates
(`ERROR` < `CRITICAL` < `FATAL`).

If the matched incident is already `resolved` and the event is newer than anything
the incident has seen, the incident is **reopened** (`status = "reopened"`, plus an
`incident.reopened` audit entry): a recurrence after a fix means the fix did not hold,
and on-call has to see it. Late, out-of-order lines from before the fix do not reopen it.

## Observability

`app/telemetry/__init__.py::configure_telemetry()` always attaches a `DbSpanExporter`
(a custom `SpanExporter` that writes every finished span into the `otel_spans` table),
so the dashboard's span-timing chart works with zero external dependencies. If
`OTEL_EXPORTER_OTLP_ENDPOINT` is set, a second `BatchSpanProcessor` also exports to that
OTLP collector (Honeycomb, Jaeger, Grafana Tempo, etc.) - the two are independent and
both active whenever the endpoint is configured.

## Frontend

A Vite/React/TypeScript SPA (`frontend/src`) built to a static bundle and served by
FastAPI from the same origin in production (`Dockerfile` stage 1 builds it, stage 2
copies the output into `backend/static`), so the whole product ships as one container
with no separate frontend host or CORS configuration needed. `lib/api.ts` is a small
typed fetch client; `pages/` holds the four views (Incidents, Incident detail,
Approvals, Dashboard); `components/primitives.tsx` and friends hold the shared
building blocks (status pills, confidence meters, key/value panels).
