"use client";

import type { Insights } from "@/lib/api";

function Cite({ evidence, onJump }: { evidence: { quote: string; line: number }[]; onJump: (line: number) => void }) {
  if (!evidence?.length) return null;
  return (
    <button className="cite" title={evidence[0].quote} onClick={() => onJump(evidence[0].line)}>
      ❝ proof
    </button>
  );
}

export default function InsightsPanel({
  insights,
  onJump,
}: {
  insights: Insights | null;
  onJump: (line: number) => void;
}) {
  if (!insights) {
    return (
      <div className="card text-sm text-neutral-500">
        No summary for this call yet.
      </div>
    );
  }

  const { summary, objections, next_steps, follow_up_email, dropped_claims } = insights;

  const hasSummary = !!summary?.length;
  const hasNextSteps = !!next_steps?.length;
  const hasObjections = !!objections?.length;
  const hasEmail = !!follow_up_email;

  const hasContent = hasSummary || hasNextSteps || hasObjections || hasEmail;

  if (!hasContent) {
    const message = dropped_claims && dropped_claims > 0
      ? `No claims with evidence to show (${dropped_claims} claims dropped).`
      : "No summary for this call yet.";
    return (
      <div className="card text-sm text-neutral-500">
        {message}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {hasSummary && (
        <section className="card">
          <h2 className="eyebrow">Summary</h2>
          <ul className="mt-3 space-y-2 text-sm">
            {summary.map((c, i) => (
              <li key={i}>
                {c.text}
                <Cite evidence={c.evidence} onJump={onJump} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {hasNextSteps && (
        <section className="card">
          <h2 className="eyebrow">Next steps</h2>
          <ul className="mt-3 space-y-2 text-sm">
            {next_steps.map((s, i) => (
              <li key={i}>
                {s.owner && <span className="font-medium">{s.owner} — </span>}
                {s.text}
                <Cite evidence={s.evidence} onJump={onJump} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {hasObjections && (
        <section className="card">
          <h2 className="eyebrow">Objections</h2>
          <ul className="mt-3 space-y-3 text-sm">
            {objections.map((o, i) => (
              <li key={i}>
                <span className="font-medium">{o.label}</span>
                {o.status && <span className="ml-1.5 text-xs text-neutral-500">({o.status})</span>}
                <p className="mt-0.5 text-neutral-700">
                  {o.detail}
                  <Cite evidence={o.evidence} onJump={onJump} />
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {hasEmail && <FollowUpEmail email={follow_up_email} />}
    </div>
  );
}

function FollowUpEmail({ email }: { email: { subject: string; body: string } }) {
  return (
    <section className="card">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="eyebrow">Follow-up email</h2>
        <button
          className="btn"
          onClick={() => navigator.clipboard.writeText(`Subject: ${email.subject}\n\n${email.body}`)}
        >
          Copy
        </button>
      </div>
      <p className="mt-3 text-sm font-medium">{email.subject}</p>
      <p className="mt-2 whitespace-pre-wrap text-sm text-neutral-700">{email.body}</p>
    </section>
  );
}
