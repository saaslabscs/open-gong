"use client";

import { useCallback, useEffect, useState } from "react";
import { listCalls, getCall, type CallSummary, type CallDetail } from "@/lib/api";
import AddCallPanel from "@/components/AddCallPanel";
import AgentWorkspace from "@/components/AgentWorkspace";

export default function WorkspacePage() {
  const [calls, setCalls] = useState<CallSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CallDetail | null>(null);
  const [uploading, setUploading] = useState(false);

  const refresh = useCallback(() => listCalls().then(setCalls).catch(() => {}), []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  function selectCall(id: string | null) {
    setDetail(null);
    setActiveId(id);
  }

  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;
    const load = () => getCall(activeId).then((d) => !cancelled && setDetail(d)).catch(() => {});
    load();
    // Calls still running get their agent_runs filled in over the next few
    // seconds — poll until the run leaves the "running"/"pending" state.
    const t = setInterval(() => {
      setDetail((d) => {
        if (d && (d.run.status === "running" || d.run.status === "pending")) load();
        return d;
      });
    }, 2500);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [activeId]);

  function handleIngested(callId: string) {
    refresh();
    selectCall(callId);
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <div className="mb-6">
        <h1 className="text-lg font-semibold tracking-tight">Agent workspace</h1>
        <p className="mt-1 text-sm text-neutral-500">
          Add a call and watch the orchestrator hand it off to the agents that actually work it.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <select
          value={activeId ?? ""}
          onChange={(e) => selectCall(e.target.value || null)}
          className="min-w-56 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm"
        >
          <option value="">Choose an existing call…</option>
          {calls.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
        <span className="text-xs text-neutral-400">or add a new one below</span>
      </div>

      <div className="mt-4">
        <AddCallPanel onIngested={handleIngested} onBusyChange={setUploading} />
      </div>

      <div className="mt-8">
        <AgentWorkspace call={detail} spawning={uploading} />
      </div>
    </main>
  );
}
