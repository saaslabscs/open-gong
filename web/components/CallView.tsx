"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  getCall,
  retryCall,
  editAgentRunOutput,
  resetAgentRunOutput,
  createShare,
  exportMarkdownUrl,
  exportJsonUrl,
  type CallDetail,
  type AgentRunSummary,
} from "@/lib/api";
import { humanizeStatus, tonePill, stageLabels } from "@/lib/status";
import { Cite, renderScalarField, type ClaimItem } from "@/lib/skillOutput";
import InsightsPanel from "./Insights";

function jumpTo(line: number) {
  const el = document.getElementById(`line-${line}`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("flash");
  void el.offsetWidth; // restart the animation
  el.classList.add("flash");
}

function SkillOutput({ skillName, fields }: { skillName: string; fields: Record<string, unknown> }) {
  return (
    <div className="card">
      <div className="eyebrow">{skillName.replaceAll("-", " ")}</div>
      <ul className="mt-2 space-y-2 text-sm leading-relaxed">
        {Object.entries(fields).map(([name, value]) => {
          if (Array.isArray(value)) {
            const items = value as ClaimItem[];
            if (items.length === 0) return <li key={name} className="text-neutral-400">{name.replaceAll("_", " ")}: none</li>;
            return items.map((item, i) => (
              <li key={`${name}-${i}`}>{item.text} <Cite evidence={item.evidence} onJump={jumpTo} /></li>
            ));
          }
          // A skill can hand back a document instead of a claim (a quote deck,
          // a generated PDF) — a bare "/decks/…" string would otherwise print
          // as inert text with nothing to click.
          if (typeof value === "string" && /^(https?:\/\/|\/)/.test(value)) {
            return (
              <li key={name} className="flex items-center gap-2">
                <span className="capitalize text-neutral-700">{name.replaceAll("_", " ")}:</span>
                <a href={value} target="_blank" rel="noopener noreferrer" className="btn text-xs">
                  Open / download
                </a>
              </li>
            );
          }
          const rendered = renderScalarField(value);
          return (
            <li key={name} className="flex items-center gap-2">
              <span className="capitalize text-neutral-700">{name.replaceAll("_", " ")}:</span>
              <span className="font-medium">{rendered.text}</span>
              <Cite evidence={rendered.evidence} onJump={jumpTo} />
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function AgentRunCard({ callId, agentRun, onChanged }: { callId: string; agentRun: AgentRunSummary; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draftText, setDraftText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function startEdit() {
    setDraftText(JSON.stringify(agentRun.output ?? {}, null, 2));
    setError(null);
    setEditing(true);
  }
  async function save() {
    setBusy(true);
    setError(null);
    try {
      const parsed = JSON.parse(draftText);
      await editAgentRunOutput(callId, agentRun.id, parsed);
      setEditing(false);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function reset() {
    setBusy(true);
    try {
      await resetAgentRunOutput(callId, agentRun.id);
      setEditing(false);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">
          {agentRun.agent_name}
          {agentRun.edited && <span className="ml-2 rounded bg-purple-100 px-1.5 py-0.5 text-xs text-purple-700">edited by you</span>}
        </h2>
        {!editing ? (
          <button onClick={startEdit} className="btn text-xs">Edit</button>
        ) : (
          <div className="flex gap-2">
            <button onClick={save} disabled={busy} className="btn btn-primary text-xs">Save</button>
            <button onClick={() => setEditing(false)} className="btn text-xs">Cancel</button>
            {agentRun.edited && <button onClick={reset} disabled={busy} className="btn text-xs">Revert to AI original</button>}
          </div>
        )}
      </div>
      {editing ? (
        <div>
          <textarea
            className="edit-field min-h-[240px] w-full font-mono text-xs"
            value={draftText}
            onChange={(e) => setDraftText(e.target.value)}
          />
          {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
        </div>
      ) : agentRun.output && Object.keys(agentRun.output).length > 0 ? (
        Object.entries(agentRun.output).map(([skillName, fields]) => (
          <SkillOutput key={skillName} skillName={skillName} fields={fields} />
        ))
      ) : (
        <p className="text-sm text-neutral-500">
          No skills applied.
          {agentRun.routing_reasoning && ` ${agentRun.routing_reasoning}`}
        </p>
      )}
    </div>
  );
}

export default function CallView({ id }: { id: string }) {
  const [data, setData] = useState<CallDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [share, setShare] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"summary" | "agents">("summary");

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

  const { call, run, transcript, agent_runs, insights } = data;
  const st = humanizeStatus(run.status);

  // "Still working" is a property of the run's status, not of how many agent
  // runs exist yet. A run that legitimately shipped with zero dispatched
  // agents is terminal — the poll below never re-fires for it, so showing a
  // spinner there would be a permanent lie.
  if (run.status === "running" || run.status === "pending") {
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

  // Terminal, but transcription itself never produced a transcript (e.g. bad
  // audio, an upstream API error). Without this branch, a permanently failed
  // transcription looked identical to "still transcribing" forever — the
  // poll above never re-fires for a terminal status, so the user would be
  // stuck staring at a spinner that describes progress that isn't happening.
  if (!transcript) {
    const transcribeStage = run.stages.find((s) => s.name === "transcribe");
    return (
      <Shell>
        <h1 className="text-xl font-semibold tracking-tight">{call.title}</h1>
        <p className="mt-6 text-sm text-neutral-600">
          Transcription failed{transcribeStage?.error ? `: ${transcribeStage.error}` : "."}
        </p>
        <button onClick={doRetry} disabled={busy} className="btn btn-warn mt-4">Retry</button>
      </Shell>
    );
  }

  // Terminal, and the guaranteed summary itself failed. Nothing downstream
  // ran (agents dispatch only after a successful summarize), so without this
  // branch the user landed in the no-agents case below and was told the agents
  // produced nothing — blaming agents for a summary failure, with the reason
  // shown nowhere.
  const summarizeStage = run.stages.find((s) => s.name === "summarize");
  if (!insights && summarizeStage?.status === "failed") {
    return (
      <Shell>
        <h1 className="text-xl font-semibold tracking-tight">{call.title}</h1>
        <p className="mt-6 text-sm text-neutral-600">
          Couldn&apos;t write the notes for this call
          {summarizeStage.error ? `: ${summarizeStage.error}` : "."}
        </p>
        <button onClick={doRetry} disabled={busy} className="btn btn-warn mt-4">Retry</button>
      </Shell>
    );
  }

  // Terminal, but the orchestrator picked no agents *and* there are no
  // guaranteed insights either — genuinely nothing to show. Say so, and show
  // why. If insights exist (the summarize/compose_email stages run
  // independently of agent dispatch), fall through to the tabbed layout below
  // so the Summary tab can still render them.
  if (agent_runs.length === 0 && !insights) {
    return (
      <Shell>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{call.title}</h1>
            <p className="mt-1 text-xs text-neutral-500">
              {call.participants.join(", ")} · {new Date(call.recorded_at).toLocaleDateString()}
            </p>
          </div>
          <span className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${tonePill[st.tone]}`}>
            {st.label}
          </span>
        </div>
        <p className="mt-6 text-sm text-neutral-600">
          {run.status === "failed"
            ? "This call finished without any agent notes."
            : "No agents matched this call, so there are no notes to show."}
        </p>
        {run.orchestrator_reasoning && (
          <p className="mt-3 text-xs text-neutral-400">Why no agents ran: {run.orchestrator_reasoning}</p>
        )}
        {(run.status === "failed" || run.status === "partial") && (
          <button onClick={doRetry} disabled={busy} className="btn btn-warn mt-4">Retry</button>
        )}
      </Shell>
    );
  }

  async function doRetry() { setBusy(true); try { await retryCall(id); await load(); } finally { setBusy(false); } }
  async function doShare() {
    setBusy(true);
    try { const { token } = await createShare(id); setShare(`${window.location.origin}/share/${token}`); } finally { setBusy(false); }
  }

  const failedSteps = agent_runs.flatMap((ar) => ar.steps.filter((s) => s.status === "failed"));

  return (
    <Shell>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{call.title}</h1>
          <p className="mt-1 text-xs text-neutral-500">
            {call.participants.join(", ")} · {new Date(call.recorded_at).toLocaleDateString()}
          </p>
        </div>
        <span className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${tonePill[st.tone]}`}>
          {st.busy && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
          {st.label}
        </span>
      </div>

      {(run.status === "partial" || run.status === "failed") && failedSteps.length > 0 && (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div className="flex items-start justify-between gap-3">
            <div>
              {failedSteps.map((s) => (
                <div key={s.name}><strong>{stageLabels[s.name] ?? s.name}</strong> couldn&apos;t finish: {s.error}</div>
              ))}
            </div>
            <button onClick={doRetry} disabled={busy} className="btn btn-warn shrink-0">Retry</button>
          </div>
        </div>
      )}

      {run.orchestrator_reasoning && (
        <p className="mt-3 text-xs text-neutral-400">Why these agents ran: {run.orchestrator_reasoning}</p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button onClick={doShare} disabled={busy} className="btn">Share link</button>
        <a href={exportMarkdownUrl(id)} className="btn" download>Export .md</a>
        <a href={exportJsonUrl(id)} className="btn" download>Export .json</a>
      </div>

      {share && (
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm">
          <span className="text-emerald-800">Public link:</span>
          <input readOnly value={share} className="flex-1 bg-transparent text-emerald-900" />
          <button onClick={() => { navigator.clipboard.writeText(share); setCopied(true); setTimeout(() => setCopied(false), 1500); }} className="btn">
            {copied ? "Copied ✓" : "Copy"}
          </button>
          <button onClick={() => setShare(null)} className="text-neutral-400">✕</button>
        </div>
      )}

      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="space-y-6 lg:col-span-7">
          <div role="tablist" aria-label="Call notes" className="flex gap-1 border-b border-neutral-200">
            {([["summary", "Summary"], ["agents", `Agent runs`]] as const).map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                id={`tab-${key}`}
                aria-selected={tab === key}
                aria-controls={`tabpanel-${key}`}
                onClick={() => setTab(key)}
                className={`-mb-px border-b-2 px-3 py-2 text-sm ${
                  tab === key
                    ? "border-neutral-900 font-medium text-neutral-900"
                    : "border-transparent text-neutral-500 hover:text-neutral-800"
                }`}
              >
                {label}
                {key === "agents" && agent_runs.length > 0 && (
                  <span className="ml-1.5 rounded-full bg-neutral-100 px-1.5 py-0.5 text-xs text-neutral-600">
                    {agent_runs.length}
                  </span>
                )}
              </button>
            ))}
          </div>

          {tab === "summary" ? (
            <div role="tabpanel" id="tabpanel-summary" aria-labelledby="tab-summary">
              <InsightsPanel insights={insights} onJump={jumpTo} />
            </div>
          ) : (
            <div role="tabpanel" id="tabpanel-agents" aria-labelledby="tab-agents" className="space-y-6">
              {agent_runs.map((ar) => (
                <AgentRunCard key={ar.id} callId={id} agentRun={ar} onChanged={load} />
              ))}
              {agent_runs.length === 0 && (
                <div className="card text-sm text-neutral-500">
                  No agents ran on this call.
                  {run.orchestrator_reasoning && ` ${run.orchestrator_reasoning}`}
                </div>
              )}
            </div>
          )}

          <ProcessingDetails run={run} />
        </div>

        <div className="lg:col-span-5">
          <div className="sticky top-4">
            <div className="mb-2 flex items-center justify-between">
              <div className="eyebrow">Transcript</div>
              <span className="text-xs text-neutral-400">click &quot;❝ proof&quot; in the notes to jump here</span>
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
    <main className="mx-auto max-w-5xl px-6 py-8">
      <Link href="/" className="text-xs text-neutral-500 hover:text-neutral-800">← All calls</Link>
      <div className="mt-3">{children}</div>
    </main>
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
