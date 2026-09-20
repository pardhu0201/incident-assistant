import { useState } from "react";
import { AlertTriangle, Ban, Check, Loader2, ShieldAlert, X } from "lucide-react";

import { api, type ProposedFix } from "../lib/api";
import { KeyValue, RiskBadge } from "./primitives";

/** The human-in-the-loop gate. The agent graph can build this card and the
 * automated test gate can clear it, but only approving here executes it. */
export default function ActionCard({
  fix,
  approvalId,
  onDecided,
}: {
  fix: ProposedFix;
  approvalId: string | null;
  onDecided?: (result: { status: string; message: string }) => void;
}) {
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [outcome, setOutcome] = useState<{ status: string; message: string } | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  const blocked = fix.blockers.length > 0;

  const decide = async (decision: "approve" | "reject") => {
    if (!approvalId) return;
    setBusy(decision);
    setError("");
    try {
      const result = await api.decide(approvalId, decision, note);
      setOutcome(result);
      onDecided?.(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not record the decision");
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="panel-2 overflow-hidden">
      <header className="flex flex-wrap items-center gap-2.5 border-b border-[var(--color-line)] bg-[var(--color-surface-3)] px-4 py-3">
        <ShieldAlert size={15} className="text-[var(--color-warn)]" />
        <span className="text-[13px] font-semibold">Approval required</span>
        <code className="rounded bg-[var(--color-surface)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--color-brand)]">
          {fix.tool_name}
        </code>
        <div className="ml-auto flex gap-1.5">
          <RiskBadge risk={fix.risk} />
        </div>
      </header>

      <div className="space-y-4 p-4">
        {fix.rationale && <p className="text-[13px] text-[var(--color-ink-2)]">{fix.rationale}</p>}

        <div>
          <div className="label mb-2">What will be executed</div>
          <KeyValue data={fix.preview} />
        </div>

        {blocked && (
          <IssueList tone="danger" icon={<Ban size={13} />} title="Blocked by policy" items={fix.blockers} />
        )}
        {fix.warnings.length > 0 && (
          <IssueList tone="warn" icon={<AlertTriangle size={13} />} title="Confirm before approving" items={fix.warnings} />
        )}

        {outcome ? (
          <div
            className="flex items-start gap-2.5 rounded-lg border p-3 text-[13px]"
            style={{
              borderColor: outcome.status === "approved" ? "rgba(5,150,105,0.35)" : "var(--color-line-strong)",
              background: outcome.status === "approved" ? "rgba(5,150,105,0.08)" : "var(--color-surface)",
            }}
          >
            {outcome.status === "approved" ? (
              <Check size={15} className="mt-0.5 shrink-0 text-[var(--color-ok)]" />
            ) : (
              <X size={15} className="mt-0.5 shrink-0 text-[var(--color-ink-3)]" />
            )}
            <span className="text-[var(--color-ink-2)]">{outcome.message}</span>
          </div>
        ) : approvalId ? (
          <div className="space-y-2.5 border-t border-[var(--color-line)] pt-3.5">
            <input
              className="field"
              placeholder="Optional note for the audit trail"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <div className="flex flex-wrap items-center gap-2">
              <button
                className="btn-ok"
                onClick={() => decide("approve")}
                disabled={busy !== null || blocked}
                title={blocked ? "Resolve the policy blockers first" : undefined}
              >
                {busy === "approve" ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                Approve and execute
              </button>
              <button className="btn-danger" onClick={() => decide("reject")} disabled={busy !== null}>
                {busy === "reject" ? <Loader2 size={14} className="animate-spin" /> : <X size={14} />}
                Reject
              </button>
              <span className="text-[11px] text-[var(--color-ink-3)]">Acting as on-call SRE</span>
            </div>
            {error && <p className="text-[12px] text-[var(--color-danger)]">{error}</p>}
          </div>
        ) : null}
      </div>
    </section>
  );
}

function IssueList({ tone, icon, title, items }: { tone: "danger" | "warn"; icon: React.ReactNode; title: string; items: string[] }) {
  const color = tone === "danger" ? "var(--color-danger)" : "var(--color-warn)";
  return (
    <div className="rounded-lg border p-3" style={{ borderColor: `${color}44`, background: `${color}0f` }}>
      <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold" style={{ color }}>
        {icon}
        {title}
      </div>
      <ul className="ml-4 list-disc space-y-1 text-[12.5px] text-[var(--color-ink-2)]">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
