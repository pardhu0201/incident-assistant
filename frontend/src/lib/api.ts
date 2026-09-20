/** Typed client for the Incident Assistant API. */

const BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");
export const apiUrl = (path: string) => `${BASE}/api${path}`;

export interface Incident {
  id: string;
  title: string;
  service: string;
  severity: string;
  status: string;
  event_count: number;
  sample_message: string;
  first_seen: string;
  last_seen: string;
}

export interface Citation {
  index: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  heading: string;
  snippet: string;
  score: number;
  dense_score: number;
  lexical_score: number;
}

export interface CheckResult {
  name: string;
  passed: boolean;
  message: string;
  duration_ms: number;
}

export interface TestGate {
  tool_name: string;
  passed: boolean;
  checks: CheckResult[];
  total_ms: number;
}

export interface ProposedFix {
  tool_name: string;
  description?: string;
  risk: string;
  requires_approval: boolean;
  arguments: Record<string, unknown>;
  valid: boolean;
  blockers: string[];
  warnings: string[];
  preview: Record<string, unknown>;
  rationale: string;
}

export interface Verification {
  groundedness_score?: number;
  citation_coverage?: number;
  lexical_support?: number;
  error_relevance?: number;
  invalid_citations?: number[];
  unsupported_claims?: string[];
  fix_risk_notes?: string[];
  decision?: string;
  confidence?: number;
  [key: string]: unknown;
}

export interface Run {
  run_id: string;
  incident_id: string;
  status: string;
  diagnosis: string;
  report: string;
  checklist: string[];
  citations: Citation[];
  used_citations: number[];
  proposed_fix: ProposedFix | null;
  test_gate: TestGate | null;
  verification: Verification;
  flags: string[];
  confidence: number;
  requires_approval: boolean;
  approval_id: string | null;
  llm_mode: string;
  latency_ms: number;
  token_usage: Record<string, unknown> | null;
  trace_id: string;
}

export interface IncidentDetail extends Incident {
  runs: Run[];
}

export interface TraceEntry {
  seq: number;
  agent: string;
  label: string;
  status: string;
  summary: string;
  payload: Record<string, unknown>;
  duration_ms: number;
}

export interface Approval {
  id: string;
  run_id: string;
  incident_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  risk: string;
  rationale: string;
  flags: string[];
  confidence: number;
  test_gate_passed: boolean;
  status: string;
  decided_by: string | null;
  decision_note: string;
  execution_result: Record<string, unknown> | null;
  created_at: string;
  decided_at: string | null;
}

export interface Health {
  status: string;
  version: string;
  llm_mode: string;
  llm_model: string;
  database: string;
  embedding_provider: string;
  documents: number;
  chunks: number;
  open_incidents: number;
  otel_service_name: string;
}

export interface ServiceStateOut {
  name: string;
  version: string;
  previous_version: string;
  replica_count: number;
  status: string;
}

export interface Dashboard {
  incidents_total: number;
  incidents_by_status: Record<string, number>;
  runs_total: number;
  approvals_by_status: Record<string, number>;
  average_confidence: number;
  average_latency_ms: number;
  test_gate_pass_rate: number;
  fixes_executed: number;
  total_estimated_tokens: number;
  flag_counts: Record<string, number>;
  recent_runs: {
    id: string;
    incident_id: string;
    status: string;
    confidence: number;
    latency_ms: number;
    llm_mode: string;
    created_at: string;
  }[];
  span_summary: { name: string; count: number; avg_duration_ms: number }[];
}

export interface GraphTopology {
  nodes: { id: string; label: string; role: string }[];
  edges: { source: string; target: string; condition?: string }[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      /* no JSON body */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<Health>("/health"),
  graph: () => request<GraphTopology>("/graph"),
  services: () => request<ServiceStateOut[]>("/services"),
  dashboard: () => request<Dashboard>("/dashboard"),

  incidents: (status = "all") => request<Incident[]>(`/incidents?status=${status}`),
  incidentDetail: (id: string) => request<IncidentDetail>(`/incidents/${id}`),
  analyze: (id: string) => request<Run>(`/incidents/${id}/analyze`, { method: "POST" }),
  runTrace: (runId: string) => request<TraceEntry[]>(`/runs/${runId}/trace`),

  replaySample: () => request<{ events_ingested: number; incidents_opened: number; incidents_updated: number }>(
    "/logs/replay-sample",
    { method: "POST" },
  ),

  approvals: (status = "all") => request<Approval[]>(`/approvals?status=${status}`),
  decide: (id: string, decision: "approve" | "reject", note = "") =>
    request<{ status: string; message: string; execution_result: Record<string, unknown> | null }>(
      `/approvals/${id}/decision`,
      { method: "POST", body: JSON.stringify({ decision, note }) },
    ),
  audit: () => request<Record<string, unknown>[]>("/audit"),
};
