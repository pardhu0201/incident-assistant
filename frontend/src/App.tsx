import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { AlertTriangle, Gauge, ShieldCheck, Siren } from "lucide-react";

import { api, type Health } from "./lib/api";
import IncidentsPage from "./pages/IncidentsPage";
import IncidentDetailPage from "./pages/IncidentDetailPage";
import ApprovalsPage from "./pages/ApprovalsPage";
import DashboardPage from "./pages/DashboardPage";

const NAV = [
  { to: "/incidents", label: "Incidents", icon: AlertTriangle },
  { to: "/approvals", label: "Approvals", icon: ShieldCheck },
  { to: "/dashboard", label: "Dashboard", icon: Gauge },
];

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [pendingCount, setPendingCount] = useState(0);

  const refreshPending = () =>
    api
      .approvals("pending")
      .then((rows) => setPendingCount(rows.length))
      .catch(() => setPendingCount(0));

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    refreshPending();
    const timer = window.setInterval(refreshPending, 20_000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="flex min-h-full flex-col lg:flex-row">
      <aside className="shrink-0 border-b border-[var(--color-line)] bg-[rgba(14,19,34,0.72)] backdrop-blur lg:sticky lg:top-0 lg:h-screen lg:w-64 lg:border-r lg:border-b-0">
        <div className="flex items-center gap-3 px-5 py-5">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-[var(--color-brand)] to-[var(--color-brand-2)] text-[#2a1200]">
            <Siren size={18} strokeWidth={2.4} />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">Incident Assistant</div>
            <div className="truncate text-[11px] text-[var(--color-ink-3)]">
              Detection &middot; Diagnosis &middot; Resolution
            </div>
          </div>
        </div>

        <nav className="flex gap-1 overflow-x-auto px-3 pb-3 lg:flex-col lg:overflow-visible lg:pb-0">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                [
                  "flex shrink-0 items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors",
                  isActive
                    ? "bg-[var(--color-surface-3)] font-medium text-[var(--color-ink)]"
                    : "text-[var(--color-ink-2)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-ink)]",
                ].join(" ")
              }
            >
              <Icon size={16} />
              <span>{label}</span>
              {to === "/approvals" && pendingCount > 0 && (
                <span className="ml-auto rounded-full bg-[var(--color-warn)] px-1.5 text-[11px] font-semibold text-[#2a1c00]">
                  {pendingCount}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        <SystemCard health={health} />
      </aside>

      <main className="min-w-0 flex-1">
        <Routes>
          <Route path="/" element={<Navigate to="/incidents" replace />} />
          <Route path="/incidents" element={<IncidentsPage />} />
          <Route path="/incidents/:id" element={<IncidentDetailPage onApprovalChange={refreshPending} />} />
          <Route path="/approvals" element={<ApprovalsPage onApprovalChange={refreshPending} />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="*" element={<Navigate to="/incidents" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function SystemCard({ health }: { health: Health | null }) {
  const live = health?.status === "ok";
  const claude = health?.llm_mode === "claude";

  return (
    <div className="hidden px-4 pb-5 lg:block">
      <div className="panel-2 space-y-2.5 p-3.5 text-[11px]">
        <div className="flex items-center gap-2">
          <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-[var(--color-ok)]" : "bg-[var(--color-danger)]"}`} />
          <span className="label">{live ? "System online" : "API unreachable"}</span>
        </div>

        {health && (
          <dl className="space-y-1.5 text-[var(--color-ink-3)]">
            <Row label="Diagnosis" value={claude ? health.llm_model : "Extractive (demo)"} accent={claude} />
            <Row label="Database" value={health.database} />
            <Row label="Runbooks" value={`${health.documents} docs / ${health.chunks} chunks`} />
            <Row label="Open incidents" value={String(health.open_incidents)} />
            <Row label="OTel service" value={health.otel_service_name} />
          </dl>
        )}

        {health && !claude && (
          <p className="border-t border-[var(--color-line)] pt-2.5 leading-relaxed text-[var(--color-ink-3)]">
            Running without an API key: diagnoses are extracted verbatim from
            the cited runbooks. Set <span className="font-mono">ANTHROPIC_API_KEY</span> for
            generative reasoning.
          </p>
        )}
      </div>
    </div>
  );
}

function Row({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="shrink-0">{label}</dt>
      <dd className={`truncate text-right font-mono ${accent ? "text-[var(--color-brand)]" : "text-[var(--color-ink-2)]"}`}>
        {value}
      </dd>
    </div>
  );
}
