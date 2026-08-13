"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  listCalls,
  uploadCall,
  ingestUrl,
  getStatus,
  type CallSummary,
  type Status,
} from "@/lib/api";
import { humanizeStatus, tonePill } from "@/lib/status";
import Header from "@/components/Header";

function formatDuration(s: number | null) {
  return s == null ? "" : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export default function Home() {
  const [calls, setCalls] = useState<CallSummary[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => listCalls().then(setCalls).catch(() => {}), []);

  useEffect(() => {
    refresh();
    getStatus().then(setStatus).catch(() => {});
    const t = setInterval(() => {
      setCalls((prev) => {
        if (prev.some((c) => c.run_status === "running" || c.run_status === "pending")) refresh();
        return prev;
      });
    }, 2500);
    return () => clearInterval(t);
  }, [refresh]);

  async function ingest(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Header />
      <main className="mx-auto max-w-3xl px-6 py-10">
        {status && !status.can_process_uploads && (
          <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <strong>Want to analyze your own calls?</strong> The samples below work with no setup.
            To process uploads, run{" "}
            <code className="rounded bg-amber-100 px-1">make init</code> to add your keys.
          </div>
        )}

        {/* Upload */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            const f = e.dataTransfer.files?.[0];
            if (f) ingest(() => uploadCall(f));
          }}
          className={`rounded-xl border-2 border-dashed p-6 text-center transition ${
            drag ? "border-neutral-900 bg-neutral-50" : "border-neutral-200"
          }`}
        >
          <p className="text-sm font-medium text-neutral-800">
            {busy ? "Uploading…" : "Drop a call recording here"}
          </p>
          <p className="mt-1 text-xs text-neutral-400">MP3, WAV, M4A — or</p>
          <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
            <button onClick={() => fileRef.current?.click()} disabled={busy} className="btn">
              Choose a file
            </button>
            <input
              ref={fileRef}
              type="file"
              accept="audio/*"
              className="hidden"
              onChange={(e) => e.target.files?.[0] && ingest(() => uploadCall(e.target.files![0]))}
            />
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && url.trim() && ingest(() => ingestUrl(url.trim()).then(() => setUrl("")))}
              placeholder="paste a recording link"
              className="w-56 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm"
            />
          </div>
          {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
        </div>

        {/* Calls */}
        <div className="mt-8 flex items-baseline justify-between">
          <h2 className="eyebrow">Your calls</h2>
          <span className="text-xs text-neutral-400">{calls.length} total</span>
        </div>
        <ul className="mt-3 space-y-2">
          {calls.map((c) => {
            const st = humanizeStatus(c.run_status);
            return (
              <li key={c.id}>
                <Link
                  href={`/calls/${c.id}`}
                  className="flex items-center justify-between gap-4 rounded-xl border border-neutral-200 bg-white px-5 py-4 hover:border-neutral-300 hover:shadow-sm"
                >
                  <div className="min-w-0">
                    <div className="truncate font-medium">{c.title}</div>
                    <div className="mt-0.5 flex items-center gap-2 text-xs text-neutral-500">
                      {c.intent && <span className="capitalize">{c.intent} call</span>}
                      {c.duration_s != null && <span>· {formatDuration(c.duration_s)}</span>}
                      <span>· {new Date(c.recorded_at).toLocaleDateString()}</span>
                    </div>
                  </div>
                  <span className={`flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${tonePill[st.tone]}`}>
                    {st.busy && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
                    {st.label}
                  </span>
                </Link>
              </li>
            );
          })}
          {calls.length === 0 && (
            <li className="rounded-xl border border-dashed border-neutral-200 px-5 py-10 text-center text-sm text-neutral-400">
              No calls yet — drop a recording above to get started.
            </li>
          )}
        </ul>
      </main>
    </>
  );
}
