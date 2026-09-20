import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ListChecks, Loader2, PlayCircle, Sparkles } from "lucide-react";

import ActionCard from "../components/ActionCard";
import AgentTrace from "../components/AgentTrace";
import AnswerBody from "../components/AnswerBody";
import TestGatePanel from "../components/TestGatePanel";
import { ConfidenceMeter, FlagList, PageHeader, SeverityBadge, StatusPill } from "../components/primitives";
import { api, type IncidentDetail, type Run } from "../lib/api";

export default function IncidentDetailPage({ onApprovalChange }: { onApprovalChange?: () => void }) {
  const { id } = useParams<{ id: string }>();
  const [incident, setIncident] = useState<IncidentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [tab, setTab] = useState<"trace" | "sources">("trace");

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      setIncident(await api.incidentDetail(id));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const analyze = async () => {
    if (!id) return;
    setAnalyzing(true);
    try {
      await api.analyze(id);
      await load();
      onApprovalChange?.();
    } finally {
      setAnalyzing(false);
    }
  };

  if (loading || !incident) {
    return (
      <div className="flex min-h-screen items-center justify-center text-[var(--color-ink-3)]">
        <Loader2 size={22} className="animate-spin" />
      </div>
    );
  }

  const latestRun: Run | undefined = incident.runs[incident.runs.length - 1];

  const jumpToSource = (index: number) => {
    setTab("sources");
    setHighlighted(index);
    window.setTimeout(() => {
      document.getElementById(`source-${index}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 60);
  };

  return (
    <div className="min-h-screen">
      <PageHeader
        title={incident.title}
        subtitle={`${incident.service} · ${incident.event_count} events · first seen ${new Date(incident.first_seen + "Z").toLocaleString()}`}
        actions={
          <>
            <Link to="/incidents" className="btn-ghost">
              <ArrowLeft size={14} />
              Back
            </Link>
            <button className="btn-primary" onClick={() => void analyze()} disabled={analyzing}>
              {analyzing ? <Loader2 size={14} className="animate-spin" /> : <PlayCircle size={14} />}
              {latestRun ? "Re-analyze" : "Analyze"}
            </button>
          </>
        }
      />

      <div className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="min-w-0 space-y-5 px-5 py-6 sm:px-7">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={incident.severity} />
            <StatusPill status={incident.status} />
            {latestRun && (
              <span className="chip">{latestRun.llm_mode === "claude" ? "Claude" : "extractive (demo)"}</span>
            )}
          </div>

          <div className="panel-2 p-3.5">
            <div className="label mb-1.5">Sample log line</div>
            <p className="font-mono text-[12.5px] text-[var(--color-ink-2)]">{incident.sample_message}</p>
          </div>

          {!latestRun ? (
            <div className="panel p-6 text-center text-[13px] text-[var(--color-ink-3)]">
              Click "Analyze" to run retrieval, diagnosis, reporting and remediation for this incident.
            </div>
          ) : (
            <>
              <section className="panel p-4 sm:p-5">
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <span className="grid h-6 w-6 place-items-center rounded-full bg-gradient-to-br from-[var(--color-brand)] to-[var(--color-brand-2)] text-[#2a1200]">
                    <Sparkles size={12} />
                  </span>
                  <span className="text-[13px] font-medium">Diagnosis</span>
                  <span className="ml-auto font-mono text-[11px] text-[var(--color-ink-3)]">
                    {latestRun.latency_ms}ms
                  </span>
                </div>
                <AnswerBody
                  text={latestRun.diagnosis}
                  citations={latestRun.citations}
                  onCitationClick={jumpToSource}
                />
                {latestRun.flags.length > 0 && (
                  <div className="mt-3">
                    <FlagList flags={latestRun.flags} />
                  </div>
                )}
              </section>

              <section className="panel p-4 sm:p-5">
                <div className="mb-3 flex items-center gap-2">
                  <ListChecks size={14} className="text-[var(--color-ink-3)]" />
                  <span className="text-[13px] font-medium">Incident report &amp; checklist</span>
                </div>
                <p className="text-[13.5px] leading-relaxed text-[var(--color-ink-2)]">{latestRun.report}</p>
                {latestRun.checklist.length > 0 && (
                  <ul className="mt-3 space-y-1.5">
                    {latestRun.checklist.map((item, i) => (
                      <li key={i} className="flex items-start gap-2 text-[13px] text-[var(--color-ink-2)]">
                        <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-[var(--color-ink-3)]" />
                        {item}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {latestRun.test_gate && <TestGatePanel gate={latestRun.test_gate} />}

              {latestRun.proposed_fix && (
                <ActionCard
                  fix={latestRun.proposed_fix}
                  approvalId={latestRun.approval_id}
                  onDecided={() => onApprovalChange?.()}
                />
              )}
            </>
          )}
        </div>

        <aside className="min-w-0 border-t border-[var(--color-line)] bg-[rgba(14,19,34,0.4)] xl:border-t-0 xl:border-l">
          {latestRun ? (
            <>
              <div className="sticky top-0 z-10 flex gap-1 border-b border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2">
                {(["trace", "sources"] as const).map((key) => (
                  <button
                    key={key}
                    onClick={() => setTab(key)}
                    className={[
                      "rounded-md px-3 py-1.5 text-[12px] font-medium capitalize transition-colors",
                      tab === key
                        ? "bg-[var(--color-surface-3)] text-[var(--color-ink)]"
                        : "text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)]",
                    ].join(" ")}
                  >
                    {key === "trace" ? "Agent trace" : `Sources (${latestRun.citations.length})`}
                  </button>
                ))}
              </div>

              <div className="space-y-5 p-4">
                {tab === "trace" ? (
                  <>
                    <ConfidenceMeter value={latestRun.confidence} />
                    <AgentTrace runId={latestRun.run_id} />
                  </>
                ) : (
                  <div className="space-y-2">
                    {latestRun.citations.map((c) => {
                      const used = latestRun.used_citations.includes(c.index);
                      const active = highlighted === c.index;
                      return (
                        <article
                          key={c.chunk_id}
                          id={`source-${c.index}`}
                          className={[
                            "rounded-lg border p-3 transition-colors",
                            active
                              ? "border-[var(--color-brand)] bg-[rgba(251,146,60,0.08)]"
                              : "border-[var(--color-line)] bg-[var(--color-surface-2)]",
                            used ? "" : "opacity-60",
                          ].join(" ")}
                        >
                          <div className="flex items-center gap-2 text-[13px] font-medium">
                            <span className="grid h-5 w-5 place-items-center rounded bg-[rgba(251,146,60,0.18)] font-mono text-[11px] text-[var(--color-brand)]">
                              {c.index}
                            </span>
                            <span className="truncate">{c.document_title}</span>
                          </div>
                          <div className="mt-0.5 truncate text-[11px] text-[var(--color-ink-3)]">{c.heading}</div>
                          <p className="mt-2 text-[12.5px] leading-relaxed text-[var(--color-ink-2)]">{c.snippet}</p>
                        </article>
                      );
                    })}
                  </div>
                )}
              </div>
            </>
          ) : (
            <p className="px-4 py-8 text-center text-[13px] text-[var(--color-ink-3)]">
              Analyze this incident to see the agent trace.
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}
