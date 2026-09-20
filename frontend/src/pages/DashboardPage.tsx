import { useEffect, useState } from "react";
import { Activity, Clock, FlaskConical, Gauge, Loader2, Server, ShieldCheck } from "lucide-react";

import { EmptyState, PageHeader, StatusPill } from "../components/primitives";
import { api, type Dashboard, type ServiceStateOut } from "../lib/api";

/* --------------------------------------------------------------------------
   Chart palette: one hue (sequential job) for the flag-count and span-timing
   magnitude bars - length carries the value. Incident/approval status uses
   the same reserved status palette validated across this portfolio's other
   dashboards (worst adjacent CVD Delta-E 9.4 against this surface).
   -------------------------------------------------------------------------- */
const SEQUENTIAL = "#fb923c";
const STATUS_FILL: Record<string, string> = {
  open: "#d97706",
  investigating: "#0284c7",
  awaiting_approval: "#d97706",
  diagnosed: "#059669",
  test_gate_failed: "#e11d48",
  escalated: "#e11d48",
  resolved: "#059669",
  pending: "#d97706",
  approved: "#059669",
  rejected: "#0284c7",
  failed: "#e11d48",
};

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [services, setServices] = useState<ServiceStateOut[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.dashboard(), api.services()])
      .then(([d, s]) => {
        setData(d);
        setServices(s);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-[var(--color-ink-3)]">
        <Loader2 size={22} className="animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <PageHeader
        title="Dashboard"
        subtitle="Diagnosis quality, latency, the test-gate pass rate, and simulated service state - the numbers an on-call lead actually checks."
      />

      <div className="space-y-7 px-5 py-6 sm:px-7">
        {!data || data.runs_total === 0 ? (
          <EmptyState
            icon={<Gauge size={18} />}
            title="No runs yet"
            body="Load the sample incidents and analyze a few, and quality, latency and test-gate statistics will appear here."
          />
        ) : (
          <>
            <StatRow data={data} />

            <div className="grid items-start gap-5 lg:grid-cols-2">
              <StatusBreakdown title="Incidents by status" counts={data.incidents_by_status} />
              <StatusBreakdown title="Approvals by status" counts={data.approvals_by_status} />
            </div>

            <div className="grid items-start gap-5 lg:grid-cols-2">
              <FlagBreakdown flags={data.flag_counts} />
              <SpanBreakdown spans={data.span_summary} />
            </div>

            <ServiceFleet services={services} />
          </>
        )}
      </div>
    </div>
  );
}

function StatRow({ data }: { data: Dashboard }) {
  const tiles = [
    { label: "Runs total", value: String(data.runs_total), icon: Gauge },
    {
      label: "Mean confidence",
      value: `${Math.round(data.average_confidence * 100)}%`,
      icon: ShieldCheck,
    },
    {
      label: "Test gate pass rate",
      value: `${Math.round(data.test_gate_pass_rate * 100)}%`,
      icon: FlaskConical,
    },
    {
      label: "Mean latency",
      value: `${Math.round(data.average_latency_ms)}ms`,
      icon: Clock,
      note: `${data.fixes_executed} fixes executed`,
    },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {tiles.map(({ label, value, icon: Icon, note }) => (
        <div key={label} className="panel p-4">
          <div className="flex items-center gap-2 text-[var(--color-ink-3)]">
            <Icon size={13} />
            <span className="label">{label}</span>
          </div>
          <div className="mt-2 font-mono text-[26px] leading-none font-semibold tracking-tight">{value}</div>
          {note && <div className="mt-1.5 text-[11.5px] text-[var(--color-ink-3)]">{note}</div>}
        </div>
      ))}
    </div>
  );
}

function StatusBreakdown({ title, counts }: { title: string; counts: Record<string, number> }) {
  const rows = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...rows.map(([, n]) => n));

  return (
    <section className="panel p-4 sm:p-5">
      <h2 className="text-sm font-semibold">{title}</h2>
      {rows.length === 0 ? (
        <p className="py-6 text-center text-[13px] text-[var(--color-ink-3)]">No data yet.</p>
      ) : (
        <ul className="mt-4 space-y-3">
          {rows.map(([status, count]) => {
            const fill = STATUS_FILL[status] ?? "#0284c7";
            return (
              <li key={status}>
                <div className="mb-1.5 flex items-center justify-between gap-3">
                  <StatusPill status={status} />
                  <span className="font-mono text-[12.5px] text-[var(--color-ink)]">{count}</span>
                </div>
                <div className="h-2.5 w-full rounded-[4px] bg-[var(--color-surface-2)]">
                  <div
                    className="h-full rounded-[4px] transition-[width] duration-500"
                    style={{ width: `${Math.max((count / max) * 100, 3)}%`, background: fill }}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function FlagBreakdown({ flags }: { flags: Record<string, number> }) {
  const rows = Object.entries(flags).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...rows.map(([, n]) => n));

  return (
    <section className="panel p-4 sm:p-5">
      <h2 className="text-sm font-semibold">Guardrail activity</h2>
      <p className="mt-0.5 mb-4 text-[12.5px] text-[var(--color-ink-3)]">How often each safety flag fired</p>
      {rows.length === 0 ? (
        <p className="py-6 text-center text-[13px] text-[var(--color-ink-3)]">No flags recorded.</p>
      ) : (
        <ul className="space-y-3">
          {rows.map(([flag, count]) => (
            <li key={flag}>
              <div className="mb-1.5 flex items-baseline justify-between gap-3">
                <span className="truncate text-[12.5px] text-[var(--color-ink-2)]">{flag.replace(/_/g, " ")}</span>
                <span className="shrink-0 font-mono text-[12.5px] text-[var(--color-ink)]">{count}</span>
              </div>
              <div className="h-2.5 w-full rounded-[4px] bg-[var(--color-surface-2)]">
                <div
                  className="h-full rounded-[4px] transition-[width] duration-500"
                  style={{ width: `${Math.max((count / max) * 100, 3)}%`, background: SEQUENTIAL }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SpanBreakdown({ spans }: { spans: Dashboard["span_summary"] }) {
  const max = Math.max(1, ...spans.map((s) => s.avg_duration_ms));

  return (
    <section className="panel p-4 sm:p-5">
      <h2 className="flex items-center gap-1.5 text-sm font-semibold">
        <Activity size={14} className="text-[var(--color-ink-3)]" />
        OpenTelemetry spans
      </h2>
      <p className="mt-0.5 mb-4 text-[12.5px] text-[var(--color-ink-3)]">
        Mean duration per span name, captured in-process (no external collector required)
      </p>
      {spans.length === 0 ? (
        <p className="py-6 text-center text-[13px] text-[var(--color-ink-3)]">No spans captured yet.</p>
      ) : (
        <ul className="space-y-3">
          {spans.map((s) => (
            <li key={s.name}>
              <div className="mb-1.5 flex items-baseline justify-between gap-3">
                <span className="truncate font-mono text-[12px] text-[var(--color-ink-2)]">{s.name}</span>
                <span className="shrink-0 font-mono text-[12px] text-[var(--color-ink)]">
                  {s.avg_duration_ms}ms &middot; &times;{s.count}
                </span>
              </div>
              <div className="h-2.5 w-full rounded-[4px] bg-[var(--color-surface-2)]">
                <div
                  className="h-full rounded-[4px] transition-[width] duration-500"
                  style={{ width: `${Math.max((s.avg_duration_ms / max) * 100, 3)}%`, background: SEQUENTIAL }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ServiceFleet({ services }: { services: ServiceStateOut[] }) {
  return (
    <section className="panel overflow-hidden">
      <div className="flex items-center gap-1.5 border-b border-[var(--color-line)] px-4 py-3.5 sm:px-5">
        <Server size={14} className="text-[var(--color-ink-3)]" />
        <h2 className="text-sm font-semibold">Simulated service fleet</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-left text-[13px]">
          <thead>
            <tr className="border-b border-[var(--color-line)] text-[var(--color-ink-3)]">
              <th className="px-4 py-2.5 font-medium sm:px-5">Service</th>
              <th className="px-3 py-2.5 font-medium">Version</th>
              <th className="px-3 py-2.5 font-medium">Replicas</th>
              <th className="px-3 py-2.5 font-medium sm:pr-5">Status</th>
            </tr>
          </thead>
          <tbody>
            {services.map((s) => (
              <tr key={s.name} className="border-b border-[var(--color-line)] last:border-0">
                <td className="px-4 py-2.5 font-mono sm:px-5">{s.name}</td>
                <td className="px-3 py-2.5 font-mono text-[var(--color-ink-2)]">{s.version}</td>
                <td className="px-3 py-2.5 font-mono text-[var(--color-ink-2)]">{s.replica_count}</td>
                <td className="px-3 py-2.5 sm:pr-5">
                  <StatusPill status={s.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
