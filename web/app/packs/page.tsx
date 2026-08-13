"use client";

import { useEffect, useState } from "react";
import { listPacks, compilePack, activatePack, deactivatePacks, type Pack } from "@/lib/api";
import Header from "@/components/Header";

const EXAMPLES = [
  "We're a B2B sales team. Score against MEDDIC, track competitor mentions and deal size, and flag if the prospect raises security or compliance concerns.",
  "Support team. Check that the agent verified the account, resolved the issue or set a clear follow-up, and flag churn risk. Rate empathy.",
];

export default function PacksPage() {
  const [packs, setPacks] = useState<Pack[]>([]);
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<Pack | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const refresh = () => listPacks().then(setPacks).catch(() => {});
  useEffect(() => { refresh(); }, []);

  const active = packs.find((p) => p.status === "active");

  async function compile() {
    if (!text.trim()) return;
    setBusy(true); setErr(null);
    try {
      setDraft(await compilePack(text.trim()));
      await refresh();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }
  async function activate(id: string) {
    setBusy(true);
    try { await activatePack(id); setDraft(null); await refresh(); } finally { setBusy(false); }
  }
  async function useBuiltins() {
    setBusy(true);
    try { await deactivatePacks(); await refresh(); } finally { setBusy(false); }
  }

  return (
    <>
      <Header />
      <main className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Insight packs</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Describe what you want scored, in plain English. Open Gong compiles it into a pack —
        review it, then activate it and new calls are analyzed your way.
      </p>

      <div className="mt-4 rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm">
        Active pack:{" "}
        {active ? (
          <>
            <strong>{active.name}</strong>
            <button onClick={useBuiltins} disabled={busy} className="btn ml-3">Use built-in sales/support instead</button>
          </>
        ) : (
          <span className="text-neutral-500">built-in sales &amp; support (picked automatically by intent)</span>
        )}
      </div>

      {/* Composer */}
      <div className="mt-6">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="e.g. Score against MEDDIC, track competitor mentions and deal size, flag security concerns…"
          className="min-h-[110px] w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
        />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button onClick={compile} disabled={busy || !text.trim()} className="btn btn-primary">
            {busy ? "Compiling…" : "Compile pack"}
          </button>
          {EXAMPLES.map((ex, i) => (
            <button key={i} onClick={() => setText(ex)} className="text-xs text-blue-600 hover:underline">
              example {i + 1}
            </button>
          ))}
        </div>
        {err && <p className="mt-2 text-xs text-red-600">{err}</p>}
      </div>

      {/* Draft review */}
      {draft && (
        <div className="mt-6 rounded-xl border border-blue-200 bg-blue-50/40 p-4">
          <div className="flex items-center justify-between">
            <h2 className="font-medium">Compiled: {draft.name} <span className="text-xs text-neutral-500">(draft — review before activating)</span></h2>
            <button onClick={() => activate(draft.id)} disabled={busy} className="btn btn-primary">Activate</button>
          </div>
          <PackFields pack={draft} />
        </div>
      )}

      {/* Existing packs */}
      {packs.filter((p) => p.status !== "active" || !draft || p.id !== draft.id).length > 0 && (
        <div className="mt-8">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">Your packs</h2>
          <ul className="mt-2 space-y-3">
            {packs.map((p) => (
              <li key={p.id} className="rounded-lg border border-neutral-200 p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <span className="font-medium">{p.name}</span>
                    <span className={`ml-2 rounded-full px-2 py-0.5 text-xs ${p.status === "active" ? "bg-emerald-100 text-emerald-800" : "bg-neutral-100 text-neutral-600"}`}>{p.status}</span>
                  </div>
                  {p.status !== "active" && (
                    <button onClick={() => activate(p.id)} disabled={busy} className="btn">Activate</button>
                  )}
                </div>
                {p.instructions && <p className="mt-1 text-xs text-neutral-500">“{p.instructions}”</p>}
                <PackFields pack={p} />
              </li>
            ))}
          </ul>
        </div>
      )}
      </main>
    </>
  );
}

function PackFields({ pack }: { pack: Pack }) {
  return (
    <div className="mt-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
      <div>
        <div className="text-xs font-semibold uppercase text-neutral-400">Yes/no checks</div>
        <ul className="mt-1 list-disc pl-5">
          {pack.scoring_spec.deterministic.map((d) => <li key={d}>{d.replaceAll("_", " ")}</li>)}
        </ul>
      </div>
      <div>
        <div className="text-xs font-semibold uppercase text-neutral-400">Judgment scores</div>
        <ul className="mt-1 list-disc pl-5">
          {pack.scoring_spec.judgment.map((j) => (
            <li key={j.name}>{j.name.replaceAll("_", " ")} <span className="text-neutral-400">(1–{j.max_score})</span></li>
          ))}
        </ul>
      </div>
    </div>
  );
}
