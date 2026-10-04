import type { ReactNode } from "react";

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle: string;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--color-line)] px-5 py-5 sm:px-7">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        <p className="mt-0.5 max-w-2xl text-sm text-[var(--color-ink-3)]">{subtitle}</p>
      </div>
      {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

const BANDS = [
  { min: 0.75, label: "High confidence", color: "var(--color-ok)" },
  { min: 0.55, label: "Moderate confidence", color: "var(--color-warn)" },
  { min: 0, label: "Low confidence", color: "var(--color-danger)" },
];

export function confidenceBand(value: number) {
  return BANDS.find((band) => value >= band.min) ?? BANDS[2];
}

export function ConfidenceMeter({ value, compact }: { value: number; compact?: boolean }) {
  const band = confidenceBand(value);
  const pct = Math.round(value * 100);

  if (compact) {
    return (
      <span className="chip" style={{ color: band.color, borderColor: `${band.color}55` }}>
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: band.color }} />
        {pct}%
      </span>
    );
  }

  return (
    <div className="min-w-[168px]">
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <span className="label">{band.label}</span>
        <span className="font-mono text-sm font-semibold" style={{ color: band.color }}>
          {pct}%
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-surface-3)]">
        <div
          className="h-full rounded-full transition-[width] duration-500"
          style={{ width: `${Math.max(pct, 2)}%`, background: band.color }}
        />
      </div>
    </div>
  );
}

const FLAG_LABELS: Record<string, string> = {
  invalid_citation: "Invalid citation",
  insufficient_evidence: "Insufficient evidence",
  low_citation_coverage: "Uncited claims",
  error_not_covered_by_runbooks: "No matching runbook",
  test_gate_failed: "Test gate failed",
  fix_blocked: "Fix blocked by policy",
  fix_warning: "Fix policy warning",
  model_flagged_claims: "Model flagged a claim",
};

export function FlagList({ flags }: { flags: string[] }) {
  if (!flags.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {flags.map((flag) => {
        const danger = flag.includes("blocked") || flag.includes("invalid") || flag.includes("failed");
        const color = danger ? "var(--color-danger)" : "var(--color-warn)";
        return (
          <span key={flag} className="chip" style={{ color, borderColor: `${color}55` }} title={flag}>
            {FLAG_LABELS[flag] ?? flag.replace(/_/g, " ")}
          </span>
        );
      })}
    </div>
  );
}

const SEVERITY_COLOR: Record<string, string> = {
  CRITICAL: "var(--color-danger)",
  FATAL: "var(--color-danger)",
  ERROR: "var(--color-warn)",
};

export function SeverityBadge({ severity }: { severity: string }) {
  const color = SEVERITY_COLOR[severity] ?? "var(--color-info)";
  return (
    <span className="chip" style={{ color, borderColor: `${color}55` }}>
      {severity}
    </span>
  );
}

const STATUS_COLOR: Record<string, string> = {
  open: "var(--color-warn)",
  investigating: "var(--color-info)",
  awaiting_approval: "var(--color-warn)",
  diagnosed: "var(--color-ok)",
  test_gate_failed: "var(--color-danger)",
  escalated: "var(--color-danger)",
  resolved: "var(--color-ok)",
  reopened: "var(--color-danger)",
  pending: "var(--color-warn)",
  processing: "var(--color-info)",
  superseded: "var(--color-ink-3)",
  approved: "var(--color-ok)",
  rejected: "var(--color-ink-3)",
  failed: "var(--color-danger)",
};

export function StatusPill({ status }: { status: string }) {
  const color = STATUS_COLOR[status] ?? "var(--color-ink-3)";
  return (
    <span className="chip" style={{ color, borderColor: `${color}55` }}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

export function RiskBadge({ risk }: { risk: string }) {
  const color = risk === "high" ? "var(--color-danger)" : risk === "medium" ? "var(--color-warn)" : "var(--color-ok)";
  return (
    <span className="chip" style={{ color, borderColor: `${color}55` }}>
      {risk} risk
    </span>
  );
}

export function EmptyState({ icon, title, body }: { icon: ReactNode; title: string; body: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      <div className="grid h-11 w-11 place-items-center rounded-xl bg-[var(--color-surface-2)] text-[var(--color-ink-3)]">
        {icon}
      </div>
      <div>
        <div className="text-sm font-medium">{title}</div>
        <p className="mx-auto mt-1 max-w-sm text-sm text-[var(--color-ink-3)]">{body}</p>
      </div>
    </div>
  );
}

export function KeyValue({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (!entries.length) return null;
  return (
    <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
      {entries.map(([key, value]) => (
        <div key={key} className="min-w-0">
          <dt className="label">{key.replace(/_/g, " ")}</dt>
          <dd className="mt-0.5 truncate font-mono text-[13px] text-[var(--color-ink)]">
            {typeof value === "object" ? JSON.stringify(value) : String(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}
