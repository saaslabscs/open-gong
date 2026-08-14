"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  listCalls,
  getStatus,
  type CallSummary,
  type Status,
} from "@/lib/api";
import { humanizeStatus, tonePill } from "@/lib/status";

function formatDuration(s: number | null) {
  return s == null ? "" : `${Math.floor(s / 60)}m ${s % 60}s`;
}

// The three sortable columns, each reduced to one comparable number. A call
// with no duration sorts below every timed one rather than as zero seconds.
type SortKey = "date" | "length" | "agents";
const SORT_VALUE: Record<SortKey, (c: CallSummary) => number> = {
  date: (c) => new Date(c.recorded_at).getTime(),
  length: (c) => c.duration_s ?? -1,
  agents: (c) => c.agent_count,
};
type Sort = { key: SortKey; dir: "asc" | "desc" };

function SortHeader({
  label,
  sortKey,
  sort,
  onSort,
  className = "",
}: {
  label: string;
  sortKey: SortKey;
  sort: Sort;
  onSort: (key: SortKey) => void;
  className?: string;
}) {
  const active = sort.key === sortKey;
  return (
    <th
      className={`py-2 font-semibold ${className}`}
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`inline-flex items-center gap-1 ${active ? "text-neutral-700" : "hover:text-neutral-600"}`}
      >
        {label}
        {active && <span className="text-[9px]">{sort.dir === "asc" ? "▲" : "▼"}</span>}
      </button>
    </th>
  );
}

export default function Home() {
  const [calls, setCalls] = useState<CallSummary[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [q, setQ] = useState("");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [agentFilter, setAgentFilter] = useState("all");
  const [days, setDays] = useState(30);
  // Newest first, matching the order the API returns.
  const [sort, setSort] = useState<Sort>({ key: "date", dir: "desc" });

  // Lazy initializer runs once, outside the render-purity check that flags
  // calling Date.now() directly in the render body (react-hooks/purity).
  // Day-granularity filtering doesn't need this to stay live-ticking.
  const [now] = useState(() => Date.now());

  // Only the agents that actually appear in the fetched calls — a filter that
  // offers a value matching nothing would be a dead end.
  const agentOptions = useMemo(
    () => Array.from(new Set(calls.flatMap((c) => c.agents ?? []))).sort(),
    [calls],
  );

  const visible = useMemo(() => {
    const value = SORT_VALUE[sort.key];
    return calls
      .filter((c) => {
        if (q && !c.title.toLowerCase().includes(q.toLowerCase())) return false;
        if (sourceFilter !== "all" && c.source !== sourceFilter) return false;
        if (statusFilter !== "all" && c.run_status !== statusFilter) return false;
        if (agentFilter !== "all" && !(c.agents ?? []).includes(agentFilter)) return false;
        if (days > 0) {
          const age = (now - new Date(c.recorded_at).getTime()) / 86400000;
          if (age > days) return false;
        }
        return true;
      })
      // sorts the array filter() just allocated, never `calls` itself
      .sort((a, b) => (sort.dir === "asc" ? value(a) - value(b) : value(b) - value(a)));
  }, [calls, q, sourceFilter, statusFilter, agentFilter, days, now, sort]);

  function toggleSort(key: SortKey) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));
  }

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

  return (
    <>
      <main className="mx-auto max-w-5xl px-6 py-10">
        {status && !status.can_process_uploads && (
          <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <strong>Want to analyze your own calls?</strong> The samples below work with no setup.
            To process uploads, run{" "}
            <code className="rounded bg-amber-100 px-1">make init</code> to add your keys.
          </div>
        )}

        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search calls"
            className="min-w-48 flex-1 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm"
          />
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="rounded-lg border border-neutral-300 px-2 py-1.5 text-sm">
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={0}>All time</option>
          </select>
          <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)} className="rounded-lg border border-neutral-300 px-2 py-1.5 text-sm">
            <option value="all">Any source</option>
            <option value="upload">Upload</option>
            <option value="url">Link</option>
            <option value="sample">Sample</option>
          </select>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="rounded-lg border border-neutral-300 px-2 py-1.5 text-sm">
            <option value="all">Any status</option>
            <option value="shipped">Ready</option>
            <option value="partial">Needs review</option>
            <option value="failed">Couldn’t finish</option>
            <option value="running">Analyzing</option>
          </select>
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="rounded-lg border border-neutral-300 px-2 py-1.5 text-sm"
            aria-label="Filter by agent"
          >
            <option value="all">Any agent</option>
            {agentOptions.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
          <Link href="/workspace" className="btn btn-primary">
            Add call
          </Link>
        </div>

        {/* Calls */}
        <div className="mt-8 flex items-baseline justify-between">
          <h2 className="eyebrow">Your calls</h2>
          <span className="text-xs text-neutral-400">{calls.length} total</span>
        </div>
        <table className="mt-6 w-full text-sm">
          <thead>
            <tr className="border-b border-neutral-200 text-left text-xs uppercase tracking-wide text-neutral-400">
              <SortHeader label="Date" sortKey="date" sort={sort} onSort={toggleSort} />
              <th className="py-2 font-semibold">Call</th>
              <th className="py-2 font-semibold">Source</th>
              <SortHeader label="Length" sortKey="length" sort={sort} onSort={toggleSort} />
              <th className="py-2 font-semibold">Status</th>
              <SortHeader label="Agents" sortKey="agents" sort={sort} onSort={toggleSort} className="text-right" />
            </tr>
          </thead>
          <tbody>
            {visible.map((c) => {
              const st = humanizeStatus(c.run_status);
              return (
                <tr key={c.id} className="border-b border-neutral-100 hover:bg-neutral-50">
                  <td className="py-3 whitespace-nowrap text-neutral-500">
                    {new Date(c.recorded_at).toLocaleDateString()}
                  </td>
                  <td className="py-3">
                    <Link href={`/calls/${c.id}`} className="font-medium hover:underline">
                      {c.title}
                    </Link>
                  </td>
                  <td className="py-3 text-neutral-500">{c.source}</td>
                  <td className="py-3 whitespace-nowrap text-neutral-500">{formatDuration(c.duration_s)}</td>
                  <td className="py-3">
                    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${tonePill[st.tone]}`}>
                      {st.busy && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
                      {st.label}
                    </span>
                  </td>
                  <td className="py-3 text-right text-neutral-500">{c.agent_count || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {visible.length === 0 && (
          <p className="mt-6 rounded-xl border border-dashed border-neutral-200 px-5 py-10 text-center text-sm text-neutral-400">
            {calls.length === 0 ? "No calls yet — choose Add call to get started." : "No calls match these filters."}
          </p>
        )}
      </main>
    </>
  );
}
