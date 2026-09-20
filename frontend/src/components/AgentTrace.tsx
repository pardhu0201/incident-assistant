import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronRight,
  ClipboardList,
  FileSearch,
  FlaskConical,
  Lightbulb,
  Loader2,
  ShieldCheck,
  UserCheck,
  Wrench,
  X,
} from "lucide-react";

import { api, type TraceEntry } from "../lib/api";

const AGENT_ICONS: Record<string, typeof FileSearch> = {
  retrieval: FileSearch,
  diagnosis: Lightbulb,
  report: ClipboardList,
  fix: Wrench,
  test_gate: FlaskConical,
  verification: ShieldCheck,
  approval_gate: UserCheck,
};

const STATUS_COLOR: Record<string, string> = {
  ok: "var(--color-ok)",
  warning: "var(--color-warn)",
  error: "var(--color-danger)",
};

export default function AgentTrace({ runId }: { runId: string }) {
  const [trace, setTrace] = useState<TraceEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .runTrace(runId)
      .then((rows) => {
        if (!cancelled) setTrace(rows);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (loading) {
    return (
      <div className="flex justify-center py-6 text-[var(--color-ink-3)]">
        <Loader2 size={16} className="animate-spin" />
      </div>
    );
  }

  return (
    <ol className="space-y-1">
      {trace.map((entry) => (
        <TraceStep key={entry.seq} entry={entry} />
      ))}
    </ol>
  );
}

function TraceStep({ entry }: { entry: TraceEntry }) {
  const [open, setOpen] = useState(false);
  const Icon = AGENT_ICONS[entry.agent] ?? FileSearch;
  const color = STATUS_COLOR[entry.status] ?? "var(--color-ink-3)";
  const StatusIcon = entry.status === "error" ? X : entry.status === "warning" ? AlertTriangle : Check;
  const rows = summarise(entry);

  return (
    <li className="rise">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-3 rounded-lg px-1 py-2 text-left transition-colors hover:bg-[var(--color-surface-2)]"
      >
        <span
          className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border"
          style={{ borderColor: `${color}55`, background: `${color}14`, color }}
        >
          <Icon size={12} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className="text-[13px] font-medium text-[var(--color-ink)]">{entry.label}</span>
            <StatusIcon size={11} style={{ color }} className="shrink-0 self-center" />
            <span className="ml-auto shrink-0 font-mono text-[11px] text-[var(--color-ink-3)]">
              {entry.duration_ms}ms
            </span>
          </span>
          <span className="mt-0.5 block text-[13px] leading-snug text-[var(--color-ink-2)]">{entry.summary}</span>
        </span>
        {rows.length > 0 && (
          <ChevronRight
            size={14}
            className={`mt-1 shrink-0 text-[var(--color-ink-3)] transition-transform ${open ? "rotate-90" : ""}`}
          />
        )}
      </button>

      {open && rows.length > 0 && (
        <dl className="mb-1 ml-9 space-y-1.5 rounded-lg border border-[var(--color-line)] bg-[var(--color-surface-2)] p-3">
          {rows.map(([label, value]) => (
            <div key={label} className="flex gap-3 text-[12px]">
              <dt className="w-32 shrink-0 text-[var(--color-ink-3)]">{label}</dt>
              <dd className="min-w-0 flex-1 font-mono break-words text-[var(--color-ink-2)]">{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

function summarise(entry: TraceEntry): [string, string][] {
  const p = entry.payload ?? {};
  const rows: [string, string][] = [];
  const push = (label: string, value: unknown) => {
    if (value === undefined || value === null) return;
    if (Array.isArray(value)) {
      if (!value.length) return;
      rows.push([label, value.map(String).join(", ")]);
    } else if (typeof value === "object") {
      rows.push([label, JSON.stringify(value)]);
    } else if (String(value).length) {
      rows.push([label, String(value)]);
    }
  };

  switch (entry.agent) {
    case "retrieval":
      push("documents", p.documents);
      break;
    case "diagnosis":
      push("citations used", p.used_citations);
      push("mode", p.mode);
      break;
    case "fix":
      push("tool", p.tool);
      push("arguments", p.arguments);
      push("mode", p.mode);
      break;
    case "test_gate":
      push("checks", (p.checks as { name: string }[] | undefined)?.map((c) => c.name));
      break;
    case "verification":
      push("decision", p.decision);
      push("groundedness", fmtPct(p.groundedness_score));
      push("error relevance", fmtPct(p.error_relevance));
      push("invalid citations", p.invalid_citations);
      break;
    case "approval_gate":
      push("tool", p.tool_name);
      push("risk", p.risk);
      break;
  }
  return rows;
}

function fmtPct(value: unknown) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : undefined;
}
