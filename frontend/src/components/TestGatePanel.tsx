import { CheckCircle2, XCircle } from "lucide-react";

import type { TestGate } from "../lib/api";

/** Renders the automated test gate's per-check report - the second safety
 * net that runs before a fix can even be offered for human approval. */
export default function TestGatePanel({ gate }: { gate: TestGate }) {
  return (
    <div
      className="rounded-lg border p-3.5"
      style={{
        borderColor: gate.passed ? "rgba(5,150,105,0.35)" : "rgba(225,29,72,0.4)",
        background: gate.passed ? "rgba(5,150,105,0.06)" : "rgba(225,29,72,0.06)",
      }}
    >
      <div className="mb-2.5 flex items-center gap-2">
        {gate.passed ? (
          <CheckCircle2 size={14} className="text-[var(--color-ok)]" />
        ) : (
          <XCircle size={14} className="text-[var(--color-danger)]" />
        )}
        <span className="text-[13px] font-medium">
          Automated test gate - {gate.checks.filter((c) => c.passed).length}/{gate.checks.length} passed
        </span>
        <span className="ml-auto font-mono text-[11px] text-[var(--color-ink-3)]">{gate.total_ms}ms</span>
      </div>
      <ul className="space-y-1.5">
        {gate.checks.map((check) => (
          <li key={check.name} className="flex items-start gap-2 text-[12.5px]">
            {check.passed ? (
              <CheckCircle2 size={12} className="mt-0.5 shrink-0 text-[var(--color-ok)]" />
            ) : (
              <XCircle size={12} className="mt-0.5 shrink-0 text-[var(--color-danger)]" />
            )}
            <span className="min-w-0">
              <code className="text-[11px] text-[var(--color-ink-3)]">{check.name}</code>
              <span className="text-[var(--color-ink-2)]"> - {check.message}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
