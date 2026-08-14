"use client";

import { useState, type FormEvent } from "react";
import {
  connectIntegration,
  disconnectIntegration,
  testIntegration,
  type Integration,
} from "@/lib/api";
import { tonePill, type UiStatus } from "@/lib/status";

// FastAPI sends errors as {"detail": "..."} and the api helper rethrows the raw
// body — show the sentence a human wrote, not the JSON wrapper.
function detailOf(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e);
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.detail === "string") return parsed.detail;
  } catch {
    // not JSON — fall through to the raw message
  }
  return raw || "Something went wrong.";
}

function agoLabel(iso: string | null, now: number): string {
  if (!iso) return "";
  const mins = Math.round((now - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

// A tinted letter tile instead of a logo file: no network fetch, no asset to
// license, and it can't break the layout.
const TILE: Record<string, string> = {
  hubspot: "bg-orange-50 text-orange-600",
  pipedrive: "bg-emerald-50 text-emerald-700",
  salesforce: "bg-sky-50 text-sky-700",
};

type Props = {
  integration: Integration;
  now: number;
  onChange: (next: Integration) => void;
  onRemoved: () => void;
};

export default function IntegrationCard({ integration: it, now, onChange, onRemoved }: Props) {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [reveal, setReveal] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState<"connect" | "test" | "disconnect" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pill: { text: string; tone: UiStatus["tone"] } = !it.available
    ? { text: "Coming soon", tone: "neutral" }
    : !it.connected
      ? { text: "Not connected", tone: "neutral" }
      : it.status === "error"
        ? { text: "Needs attention", tone: "amber" }
        : { text: "Connected", tone: "green" };

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy("connect");
    setError(null);
    try {
      const next = await connectIntegration(it.key, token);
      setToken("");
      setReveal(false);
      setOpen(false);
      onChange(next);
    } catch (err) {
      setError(detailOf(err));
    } finally {
      setBusy(null);
    }
  }

  async function runTest() {
    setBusy("test");
    setError(null);
    try {
      onChange(await testIntegration(it.key));
    } catch (err) {
      setError(detailOf(err));
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("disconnect");
    setError(null);
    try {
      await disconnectIntegration(it.key);
      setConfirming(false);
      onRemoved();
    } catch (err) {
      setError(detailOf(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section
      className={`card ${open ? "sm:col-span-2" : ""} ${
        it.available ? "" : "border-dashed bg-neutral-50/60"
      }`}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-sm font-semibold ${
            TILE[it.key] ?? "bg-neutral-100 text-neutral-600"
          }`}
        >
          {it.label[0]}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h2 className={`font-medium ${it.available ? "" : "text-neutral-500"}`}>{it.label}</h2>
            <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${tonePill[pill.tone]}`}>
              {pill.text}
            </span>
          </div>
          <p className="mt-1 text-xs text-neutral-500">{it.blurb}</p>

          {it.connected && (
            <p className="mt-2 text-xs text-neutral-400">
              {it.account_label ? `${it.account_label} · ` : ""}
              {it.token_hint}
              {it.last_verified_at ? ` · verified ${agoLabel(it.last_verified_at, now)}` : ""}
            </p>
          )}

          {it.status === "error" && it.last_error && (
            <p className="mt-2 rounded-lg bg-amber-50 px-2 py-1.5 text-xs text-amber-900">
              {it.last_error}
            </p>
          )}
          {error && (
            <p className="mt-2 rounded-lg bg-red-50 px-2 py-1.5 text-xs text-red-700">{error}</p>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {it.available && !it.connected && !open && (
              <button onClick={() => setOpen(true)} className="btn btn-primary">
                Connect
              </button>
            )}
            {it.connected && !confirming && (
              <>
                <button onClick={runTest} disabled={busy !== null} className="btn">
                  {busy === "test" ? "Testing…" : "Test connection"}
                </button>
                <button onClick={() => setOpen((v) => !v)} disabled={busy !== null} className="btn">
                  Replace token
                </button>
                <button
                  onClick={() => setConfirming(true)}
                  disabled={busy !== null}
                  className="text-xs text-neutral-500 hover:text-red-600"
                >
                  Disconnect
                </button>
              </>
            )}
            {confirming && (
              <>
                <span className="text-xs text-neutral-600">
                  Disconnect {it.label}? You’ll need the token again to reconnect.
                </span>
                <button onClick={remove} disabled={busy !== null} className="btn btn-warn">
                  {busy === "disconnect" ? "Disconnecting…" : "Yes, disconnect"}
                </button>
                <button onClick={() => setConfirming(false)} className="btn">
                  Cancel
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {open && it.available && (
        <form onSubmit={submit} className="mt-4 border-t border-neutral-100 pt-4">
          <ol className="space-y-1 text-xs text-neutral-600">
            {it.setup_steps.map((step, i) => (
              <li key={i}>
                <span className="mr-1 font-medium text-neutral-400">{i + 1}.</span>
                {step}
              </li>
            ))}
          </ol>
          {it.docs_url && (
            <a
              href={it.docs_url}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-block text-xs font-medium text-blue-700 hover:underline"
            >
              Open {it.label} settings ↗
            </a>
          )}
          <label className="eyebrow mt-4 block" htmlFor={`token-${it.key}`}>
            {it.token_label}
          </label>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <input
              id={`token-${it.key}`}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              type={reveal ? "text" : "password"}
              autoComplete="off"
              spellCheck={false}
              placeholder={`Paste your ${it.label} token`}
              className="edit-field min-w-64 flex-1"
            />
            <button
              type="button"
              onClick={() => setReveal((v) => !v)}
              className="text-xs text-neutral-500 hover:text-neutral-800"
            >
              {reveal ? "Hide" : "Show"}
            </button>
            <button type="submit" disabled={busy !== null || !token.trim()} className="btn btn-primary">
              {busy === "connect" ? "Verifying…" : "Connect"}
            </button>
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setError(null);
                setToken("");
              }}
              className="btn"
            >
              Cancel
            </button>
          </div>
          <p className="mt-2 text-xs text-neutral-400">
            Open Gong checks the token with {it.label} before saving it, and never shows it again.
          </p>
        </form>
      )}
    </section>
  );
}
