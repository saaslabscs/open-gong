"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  getCall,
  retryCall,
  saveInsights,
  resetInsights,
  createShare,
  exportMarkdownUrl,
  exportJsonUrl,
  type CallDetail,
  type Insights,
  type Evidence,
} from "@/lib/api";
import { humanizeStatus, tonePill, stageLabels } from "@/lib/status";
import Header from "@/components/Header";

function jumpTo(line: number) {
  const el = document.getElementById(`line-${line}`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("flash");
  void el.offsetWidth; // restart the animation
  el.classList.add("flash");
}

function Cite({ evidence }: { evidence: Evidence[] }) {
  if (!evidence?.length) return null;
  // one chip per claim; jumps to the first cited line, tooltip lists all quotes
  const title = evidence.map((e) => `L${e.line}: “${e.quote}”`).join("\n");
  return (
    <button className="cite" title={title} onClick={() => jumpTo(evidence[0].line)}>
      ❝ proof{evidence.length > 1 ? ` ·${evidence.length}` : ""}
    </button>
  );
}

export default function CallView({ id }: { id: string }) {
  const [data, setData] = useState<CallDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Insights | null>(null);
  const [share, setShare] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => getCall(id).then(setData).catch((e) => setErr(String(e))), [id]);

  useEffect(() => {
    load();
    const t = setInterval(() => {
      setData((prev) => {
        if (prev && (prev.run.status === "running" || prev.run.status === "pending")) load();
        return prev;
      });
    }, 2500);
    return () => clearInterval(t);
  }, [load]);

  if (err) return <Shell><p className="text-sm text-red-600">{err}</p></Shell>;
  if (!data) return <Shell><p className="text-sm text-neutral-500">Loading…</p></Shell>;

  const { call, run, transcript, insights, compliance } = data;
  const st = humanizeStatus(run.status);

  if (!transcript || !insights) {
    return (
      <Shell>
        <h1 className="text-xl font-semibold tracking-tight">{call.title}</h1>
        <div className="mt-6 flex items-center gap-2 text-sm text-neutral-500">
          <span className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
          {transcript ? "Writing the notes…" : "Transcribing the call…"} This updates on its own.
        </div>
      </Shell>
    );
  }

  const view = editing && draft ? draft : insights;
  const email = view.follow_up_email;

  async function startEdit() { setDraft(JSON.parse(JSON.stringify(insights))); setEditing(true); }
  async function save() {
    if (!draft) return;
    setBusy(true);
    try { await saveInsights(id, draft); setEditing(false); await load(); } finally { setBusy(false); }
  }
  async function reset() {
    setBusy(true);
    try { await resetInsights(id); setEditing(false); await load(); } finally { setBusy(false); }
  }
  async function doRetry() { setBusy(true); try { await retryCall(id); await load(); } finally { setBusy(false); } }
  async function doShare() {
    setBusy(true);
    try { const { token } = await createShare(id); setShare(`${window.location.origin}/share/${token}`); } finally { setBusy(false); }
  }
  function copyEmail() {
    if (!email) return;
    navigator.clipboard.writeText(`Subject: ${email.subject}\n\n${email.body}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <Shell>
      {/* Title row */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{call.title}</h1>
          <p className="mt-1 text-xs text-neutral-500">
            {view.intent?.value && <span className="capitalize">{view.intent.value} call · </span>}
            {call.participants.join(", ")} · {new Date(call.recorded_at).toLocaleDateString()}
            {run.edited && <span className="ml-2 rounded bg-purple-100 px-1.5 py-0.5 text-purple-700">edited by you</span>}
          </p>
        </div>
        <span className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${tonePill[st.tone]}`}>
          {st.busy && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
          {st.label}
        </span>
      </div>

      {/* Flags */}
      {(run.status === "partial" || run.status === "failed") && <FailureBanner run={run} insights={insights} onRetry={doRetry} busy={busy} />}

      {/* Toolbar */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        {!editing ? (
          <>
            <button onClick={doShare} disabled={busy} className="btn">Share link</button>
            <a href={exportMarkdownUrl(id)} className="btn" download>Export .md</a>
            <a href={exportJsonUrl(id)} className="btn" download>Export .json</a>
            <button onClick={startEdit} className="btn">Edit notes</button>
          </>
        ) : (
          <>
            <button onClick={save} disabled={busy} className="btn btn-primary">Save</button>
            <button onClick={() => setEditing(false)} className="btn">Cancel</button>
            {run.edited && <button onClick={reset} disabled={busy} className="btn">Revert to AI original</button>}
            <span className="text-xs text-neutral-400">Editing the notes — evidence stays attached.</span>
          </>
        )}
      </div>

      {share && (
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm">
          <span className="text-emerald-800">Public link:</span>
          <input readOnly value={share} className="flex-1 bg-transparent text-emerald-900" />
          <button onClick={() => navigator.clipboard.writeText(share)} className="btn">Copy</button>
          <button onClick={() => setShare(null)} className="text-neutral-400">✕</button>
        </div>
      )}

      {/* Two columns: notes (primary) + transcript (reference) */}
      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="space-y-5 lg:col-span-7">
          <div className="card">
            <div className="eyebrow">Summary</div>
            <ul className="mt-2 space-y-2 text-sm leading-relaxed">
              {view.summary.map((s, i) => (
                <li key={i}>
                  {editing && draft ? (
                    <textarea className="edit-field" value={draft.summary[i].text}
                      onChange={(e) => { const d = { ...draft }; d.summary[i].text = e.target.value; setDraft({ ...d }); }} />
                  ) : (<>{s.text} <Cite evidence={s.evidence} /></>)}
                </li>
              ))}
            </ul>
          </div>

          <div className="card">
            <div className="eyebrow">Next steps</div>
            <ul className="mt-2 space-y-2 text-sm">
              {view.next_steps.map((n, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-neutral-300">▸</span>
                  <span>
                    {editing && draft ? (
                      <textarea className="edit-field" value={draft.next_steps[i].text}
                        onChange={(e) => { const d = { ...draft }; d.next_steps[i].text = e.target.value; setDraft({ ...d }); }} />
                    ) : (<>{n.text}{n.owner && <span className="text-neutral-400"> — {n.owner}</span>} <Cite evidence={n.evidence} /></>)}
                  </span>
                </li>
              ))}
              {view.next_steps.length === 0 && <li className="text-sm text-neutral-400">None captured.</li>}
            </ul>
          </div>

          {email && (
            <div className="card">
              <div className="flex items-center justify-between">
                <div className="eyebrow">Follow-up email</div>
                {!editing && (
                  <button onClick={copyEmail} className="btn text-xs">{copied ? "Copied ✓" : "Copy email"}</button>
                )}
              </div>
              {editing && draft && draft.follow_up_email ? (
                <div className="mt-2 space-y-2">
                  <input className="edit-field font-medium" value={draft.follow_up_email.subject}
                    onChange={(e) => { const d = { ...draft }; d.follow_up_email!.subject = e.target.value; setDraft({ ...d }); }} />
                  <textarea className="edit-field min-h-[160px]" value={draft.follow_up_email.body}
                    onChange={(e) => { const d = { ...draft }; d.follow_up_email!.body = e.target.value; setDraft({ ...d }); }} />
                </div>
              ) : (
                <div className="mt-2 text-sm">
                  <p className="font-medium">{email.subject}</p>
                  <p className="mt-2 whitespace-pre-wrap text-neutral-700">{email.body}</p>
                </div>
              )}
            </div>
          )}

          {view.objections.length > 0 && (
            <div className="card">
              <div className="eyebrow">Objections &amp; concerns</div>
              <ul className="mt-2 space-y-2 text-sm">
                {view.objections.map((o, i) => (
                  <li key={i}>
                    <span className="font-medium capitalize">{o.label}</span>
                    {o.status && <span className="ml-1 text-xs text-neutral-500">({o.status})</span>}: {o.detail} <Cite evidence={o.evidence} />
                  </li>
                ))}
              </ul>
            </div>
          )}

          <Scorecard sc={view.scorecard} />

          {compliance && <ComplianceDetails compliance={compliance} />}
          <ProcessingDetails run={run} />
        </div>

        {/* Transcript */}
        <div className="lg:col-span-5">
          <div className="sticky top-4">
            <div className="mb-2 flex items-center justify-between">
              <div className="eyebrow">Transcript</div>
              <span className="text-xs text-neutral-400">click “❝ proof” in the notes to jump here</span>
            </div>
            <ol className="max-h-[72vh] space-y-1 overflow-y-auto rounded-xl border border-neutral-200 bg-white p-4 text-sm">
              {transcript.lines.map((l) => (
                <li key={l.line} id={`line-${l.line}`} className="flex scroll-mt-4 gap-3 rounded px-1 py-0.5">
                  <span className="w-6 shrink-0 text-right text-xs text-neutral-300">{l.line}</span>
                  <span><span className="font-medium">{l.speaker}:</span> <span className="text-neutral-700">{l.text}</span></span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <Header />
      <main className="mx-auto max-w-5xl px-6 py-8">
        <Link href="/" className="text-xs text-neutral-500 hover:text-neutral-800">← All calls</Link>
        <div className="mt-3">{children}</div>
      </main>
    </>
  );
}

function Scorecard({ sc }: { sc: Insights["scorecard"] }) {
  const checks = sc.fields.filter((f) => f.kind === "deterministic");
  const scores = sc.fields.filter((f) => f.kind === "judgment");
  return (
    <div className="card">
      <div className="eyebrow">Scorecard <span className="text-neutral-300">· {sc.pack}</span></div>
      {checks.length > 0 && (
        <ul className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
          {checks.map((f) => (
            <li key={f.name} className="flex items-center gap-2">
              <span className={f.value ? "text-emerald-600" : f.value === false ? "text-neutral-300" : "text-neutral-300"}>
                {f.value ? "✓" : "○"}
              </span>
              <span className="capitalize text-neutral-700">{f.name.replaceAll("_", " ")}</span>
              <Cite evidence={f.evidence} />
            </li>
          ))}
        </ul>
      )}
      {scores.length > 0 && (
        <div className="mt-4 space-y-3">
          {scores.map((f) => (
            <div key={f.name}>
              <div className="flex items-center justify-between text-sm">
                <span className="capitalize font-medium">{f.name.replaceAll("_", " ")}</span>
                <span className="text-neutral-500">{f.score}/{f.max_score}</span>
              </div>
              <div className="mt-1 h-1.5 rounded-full bg-neutral-100">
                <div className="h-1.5 rounded-full bg-neutral-800" style={{ width: `${((f.score ?? 0) / (f.max_score ?? 5)) * 100}%` }} />
              </div>
              {f.justification && <p className="mt-1 text-xs text-neutral-500">{f.justification} <Cite evidence={f.evidence} /></p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ComplianceDetails({ compliance }: { compliance: NonNullable<CallDetail["compliance"]> }) {
  const checkedBy = compliance.source === "trace" ? "PyAI Trace" : "Open Gong's built-in check";
  return (
    <details className="card">
      <summary className="cursor-pointer text-sm font-medium text-neutral-700">
        Compliance review — {compliance.verdict === "PASS" ? "no issues found" : "flagged for review"}
        <span className="text-xs font-normal text-neutral-400"> (internal only, never shared)</span>
      </summary>
      {compliance.findings.length > 0 ? (
        <ul className="mt-3 space-y-2 text-sm">
          {compliance.findings.map((f, i) => (
            <li key={i}>
              <span className="font-medium capitalize">{f.rule.replaceAll("_", " ")}</span>{" "}
              <span className="text-xs uppercase text-red-600">{f.severity}</span>
              <span className="block text-neutral-600">{f.detail}</span> <Cite evidence={f.evidence} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-neutral-500">Nothing flagged.</p>
      )}
      <p className="mt-2 text-xs text-neutral-400">checked by {checkedBy} · ref {compliance.audit_hash}</p>
    </details>
  );
}

function ProcessingDetails({ run }: { run: CallDetail["run"] }) {
  return (
    <details className="rounded-xl border border-neutral-200 px-5 py-3">
      <summary className="cursor-pointer text-xs font-medium text-neutral-500">Processing details</summary>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {run.stages.map((s) => (
          <span key={s.name} title={s.error ?? ""}
            className={`rounded px-1.5 py-0.5 text-xs ${
              s.status === "ok" ? "bg-emerald-50 text-emerald-700"
                : s.status === "failed" ? "bg-red-50 text-red-700"
                : s.status === "skipped" ? "bg-neutral-100 text-neutral-400"
                : "bg-blue-50 text-blue-700"
            }`}>
            {stageLabels[s.name] ?? s.name}
          </span>
        ))}
      </div>
    </details>
  );
}

function FailureBanner({ run, insights, onRetry, busy }: {
  run: CallDetail["run"]; insights: Insights; onRetry: () => void; busy: boolean;
}) {
  const failed = run.stages.filter((s) => s.status === "failed");
  const dropped = insights.dropped_claims ?? [];
  return (
    <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      <div className="flex items-start justify-between gap-3">
        <div>
          {failed.map((s) => (
            <div key={s.name}><strong>{stageLabels[s.name] ?? s.name}</strong> couldn’t finish: {s.error}</div>
          ))}
          {dropped.length > 0 && (
            <div className={failed.length ? "mt-1" : ""}>
              {dropped.length} claim{dropped.length > 1 ? "s were" : " was"} dropped — no proof in the transcript, so {dropped.length > 1 ? "they" : "it"} didn’t ship.
            </div>
          )}
        </div>
        {failed.length > 0 && <button onClick={onRetry} disabled={busy} className="btn btn-warn shrink-0">Retry</button>}
      </div>
    </div>
  );
}
