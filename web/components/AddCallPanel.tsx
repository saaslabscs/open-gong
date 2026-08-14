"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { uploadCall, ingestUrl } from "@/lib/api";

export default function AddCallPanel({
  onIngested,
  onBusyChange,
}: {
  onIngested: (callId: string) => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A re-sent file/link is deduped by the backend (created: false) and no job is
  // queued — without this the UI renders no change and the button looks dead.
  const [duplicateOf, setDuplicateOf] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function ingest(fn: () => Promise<{ call_id: string; created: boolean }>) {
    setBusy(true);
    onBusyChange?.(true);
    setError(null);
    setDuplicateOf(null);
    try {
      const { call_id, created } = await fn();
      onIngested(call_id);
      if (!created) setDuplicateOf(call_id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      onBusyChange?.(false);
    }
  }

  return (
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
          onKeyDown={(e) =>
            e.key === "Enter" &&
            url.trim() &&
            ingest(async () => {
              const r = await ingestUrl(url.trim());
              setUrl("");
              return r;
            })
          }
          placeholder="paste a recording link"
          className="w-56 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm"
        />
        <button
          disabled
          className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-sm font-medium text-red-400 opacity-60"
        >
          Webhook
          <span className="rounded-full bg-red-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-red-500">
            Coming soon
          </span>
        </button>
      </div>
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      {duplicateOf && (
        <p className="mt-2 text-xs text-neutral-500">
          Already ingested — nothing new to process.{" "}
          <Link href={`/calls/${duplicateOf}`} className="font-medium text-neutral-800 underline">
            Open the existing call
          </Link>
        </p>
      )}
    </div>
  );
}
