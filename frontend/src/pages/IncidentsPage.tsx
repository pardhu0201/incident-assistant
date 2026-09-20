import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Loader2, PlayCircle, RefreshCw } from "lucide-react";

import { EmptyState, PageHeader, SeverityBadge, StatusPill } from "../components/primitives";
import { api, type Incident } from "../lib/api";

const FILTERS = ["all", "open", "investigating", "awaiting_approval", "diagnosed", "resolved"] as const;

export default function IncidentsPage() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [loading, setLoading] = useState(true);
  const [replaying, setReplaying] = useState(false);
  const [status, setStatus] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setIncidents(await api.incidents(filter));
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    void load();
  }, [load]);

  const replay = async () => {
    setReplaying(true);
    setStatus("");
    try {
      const result = await api.replaySample();
      setStatus(
        `Replayed ${result.events_ingested} log events -> ${result.incidents_opened} new incident(s), ${result.incidents_updated} updated.`,
      );
      await load();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Replay failed");
    } finally {
      setReplaying(false);
    }
  };

  return (
    <div className="min-h-screen">
      <PageHeader
        title="Incidents"
        subtitle="Errors grouped from application logs by fingerprint and time window. Analyze one to run diagnosis, reporting and remediation."
        actions={
          <>
            <button className="btn-ghost" onClick={() => void load()}>
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
              Refresh
            </button>
            <button className="btn-primary" onClick={() => void replay()} disabled={replaying}>
              {replaying ? <Loader2 size={14} className="animate-spin" /> : <PlayCircle size={14} />}
              Load sample incidents
            </button>
          </>
        }
      />

      <div className="px-5 py-5 sm:px-7">
        {status && (
          <p className="mb-4 rounded-lg border border-[var(--color-line-strong)] bg-[var(--color-surface-2)] px-3.5 py-2.5 text-[13px] text-[var(--color-ink-2)]">
            {status}
          </p>
        )}

        <div className="mb-5 flex flex-wrap gap-1.5">
          {FILTERS.map((option) => (
            <button
              key={option}
              onClick={() => setFilter(option)}
              className={[
                "rounded-lg px-3 py-1.5 text-[12.5px] font-medium capitalize transition-colors",
                filter === option
                  ? "bg-[var(--color-surface-3)] text-[var(--color-ink)]"
                  : "text-[var(--color-ink-3)] hover:bg-[var(--color-surface-2)]",
              ].join(" ")}
            >
              {option.replace(/_/g, " ")}
            </button>
          ))}
        </div>

        {loading && incidents.length === 0 ? (
          <div className="flex justify-center py-16 text-[var(--color-ink-3)]">
            <Loader2 size={20} className="animate-spin" />
          </div>
        ) : incidents.length === 0 ? (
          <EmptyState
            icon={<AlertTriangle size={18} />}
            title="No incidents"
            body='Click "Load sample incidents" to replay a synthetic log dataset - a database pool exhaustion, a payment gateway timeout after a deploy, a memory leak, and a traffic spike - and watch incidents form automatically.'
          />
        ) : (
          <div className="space-y-2">
            {incidents.map((incident) => (
              <Link
                key={incident.id}
                to={`/incidents/${incident.id}`}
                className="panel-2 flex flex-wrap items-center gap-3 p-3.5 transition-colors hover:border-[var(--color-line-strong)]"
              >
                <SeverityBadge severity={incident.severity} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13.5px] font-medium">{incident.title}</div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px] text-[var(--color-ink-3)]">
                    <span className="font-mono">{incident.service}</span>
                    <span>&middot;</span>
                    <span>{incident.event_count} events</span>
                    <span>&middot;</span>
                    <span>{new Date(incident.last_seen + "Z").toLocaleString()}</span>
                  </div>
                </div>
                <StatusPill status={incident.status} />
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
