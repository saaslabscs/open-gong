"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CallDetail } from "@/lib/api";

// Turns "compose_email" into "Compose email" — no hardcoded stage list, so it
// stays correct if the pipeline grows new stages later.
function titleize(s: string) {
  return s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

type BaseStatus = "idle" | "ok" | "failed" | "skipped";
type Station = {
  key: string;
  label: string;
  kind: "stage" | "agent";
  base: BaseStatus;
};

function buildStations(call: CallDetail | null): Station[] {
  if (!call) return [];
  const stages: Station[] = call.run.stages.map((s) => ({
    key: `stage:${s.name}`,
    label: titleize(s.name),
    kind: "stage",
    base: s.status === "pending" ? "idle" : s.status === "skipped" ? "skipped" : s.status === "failed" ? "failed" : "ok",
  }));
  const agents: Station[] = call.agent_runs.map((a) => ({
    key: `agent:${a.id}`,
    label: a.agent_name,
    kind: "agent",
    base: a.status === "pending" ? "idle" : a.status === "failed" ? "failed" : "ok",
  }));
  return [...stages, ...agents];
}

function orbitPosition(index: number, total: number) {
  const angle = -Math.PI / 2 + (index / Math.max(total, 1)) * Math.PI * 2;
  return { x: 50 + 40 * Math.cos(angle), y: 50 + 38 * Math.sin(angle) };
}

type DisplayStatus = "idle" | "active" | "done" | "failed" | "skipped";

const STATUS_STYLE: Record<DisplayStatus, { stroke: string; badge: string; text: string }> = {
  idle: { stroke: "stroke-neutral-300", badge: "bg-neutral-100 text-neutral-400", text: "waiting" },
  active: { stroke: "stroke-amber-500", badge: "bg-amber-100 text-amber-700", text: "working" },
  done: { stroke: "stroke-neutral-700", badge: "bg-emerald-100 text-emerald-700", text: "done" },
  failed: { stroke: "stroke-red-400", badge: "bg-red-100 text-red-600", text: "failed" },
  skipped: { stroke: "stroke-neutral-200", badge: "bg-neutral-50 text-neutral-300", text: "skipped" },
};

function StickFigure({
  stroke,
  scale = 1,
  walking,
  thinking,
}: {
  stroke: string;
  scale?: number;
  walking: boolean;
  thinking: boolean;
}) {
  const limbClass = walking ? "figurine-limb-l" : "";
  const limbClassR = walking ? "figurine-limb-r" : "";
  return (
    <svg
      viewBox="0 0 40 64"
      width={40 * scale}
      height={64 * scale}
      className="figurine-idle overflow-visible"
      style={walking ? { animationDuration: "0.6s" } : undefined}
    >
      {thinking && (
        <text x="26" y="4" className="figurine-think fill-neutral-400" fontSize="10">
          ⋯
        </text>
      )}
      <circle cx="20" cy="10" r="7" className={stroke} fill="white" strokeWidth={2.5} />
      <line x1="20" y1="17" x2="20" y2="40" className={stroke} strokeWidth={2.5} strokeLinecap="round" />
      <line x1="20" y1="22" x2="8" y2="34" className={`${stroke} ${limbClass}`} strokeWidth={2.5} strokeLinecap="round" />
      <line x1="20" y1="22" x2="32" y2="34" className={`${stroke} ${limbClassR}`} strokeWidth={2.5} strokeLinecap="round" />
      <line x1="20" y1="40" x2="10" y2="60" className={`${stroke} ${limbClass}`} strokeWidth={2.5} strokeLinecap="round" />
      <line x1="20" y1="40" x2="30" y2="60" className={`${stroke} ${limbClassR}`} strokeWidth={2.5} strokeLinecap="round" />
    </svg>
  );
}

function PaperToken() {
  return (
    <svg viewBox="0 0 16 20" width={22} height={28} className="paper-token drop-shadow-sm">
      <path d="M2 2 H10 L14 6 V18 H2 Z" fill="#FBF8F2" stroke="#a3a3a3" strokeWidth={1} />
      <path d="M10 2 V6 H14" fill="none" stroke="#a3a3a3" strokeWidth={1} />
      <line x1="4" y1="10" x2="12" y2="10" stroke="#d4d4d4" strokeWidth={1} />
      <line x1="4" y1="13" x2="12" y2="13" stroke="#d4d4d4" strokeWidth={1} />
      <line x1="4" y1="16" x2="9" y2="16" stroke="#d4d4d4" strokeWidth={1} />
    </svg>
  );
}

function SpawningIndicator() {
  return (
    <div className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-3">
      <div className="flex items-end gap-3">
        {[0, 1, 2].map((i) => (
          <svg
            key={i}
            viewBox="0 0 40 64"
            width={30}
            height={48}
            className="figurine-spawn-pop overflow-visible"
            style={{ animationDelay: `${i * 0.25}s` }}
          >
            <circle cx="20" cy="10" r="7" className="stroke-neutral-400" fill="white" strokeWidth={2.5} />
            <line x1="20" y1="17" x2="20" y2="40" className="stroke-neutral-400" strokeWidth={2.5} strokeLinecap="round" />
            <line x1="20" y1="22" x2="8" y2="34" className="stroke-neutral-400" strokeWidth={2.5} strokeLinecap="round" />
            <line x1="20" y1="22" x2="32" y2="34" className="stroke-neutral-400" strokeWidth={2.5} strokeLinecap="round" />
            <line x1="20" y1="40" x2="10" y2="60" className="stroke-neutral-400" strokeWidth={2.5} strokeLinecap="round" />
            <line x1="20" y1="40" x2="30" y2="60" className="stroke-neutral-400" strokeWidth={2.5} strokeLinecap="round" />
          </svg>
        ))}
      </div>
      <p className="text-sm font-medium text-neutral-500">
        Spawning agents
        <span className="figurine-think" style={{ animationDelay: "0s" }}>.</span>
        <span className="figurine-think" style={{ animationDelay: "0.2s" }}>.</span>
        <span className="figurine-think" style={{ animationDelay: "0.4s" }}>.</span>
      </p>
    </div>
  );
}

const CENTER = { x: 50, y: 50 };
const WALK_MS = 1500;
const PROCESS_MS = 900;

export default function AgentWorkspace({ call, spawning }: { call: CallDetail | null; spawning?: boolean }) {
  const stations = useMemo(() => buildStations(call), [call]);
  const visited = useMemo(() => stations.filter((s) => s.base === "ok" || s.base === "failed"), [stations]);
  const stillSpawning =
    spawning || (!!call && (call.run.status === "running" || call.run.status === "pending") && visited.length === 0);

  const [orchPos, setOrchPos] = useState(CENTER);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [carrying, setCarrying] = useState(false);
  const [logged, setLogged] = useState<Station[]>([]);
  const [replayNonce, setReplayNonce] = useState(0);
  const reducedMotion = useRef(false);

  useEffect(() => {
    reducedMotion.current = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }, []);

  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    stations.forEach((s, i) => map.set(s.key, orbitPosition(i, stations.length)));
    return map;
  }, [stations]);

  // Keyed on the call's actual step outcomes — replaying only re-plays the
  // same real sequence, it never invents a new one.
  const sequenceKey = `${call?.call.id ?? ""}:${visited.map((v) => v.key + v.base).join(",")}:${replayNonce}`;

  useEffect(() => {
    let cancelled = false;
    const timers: ReturnType<typeof setTimeout>[] = [];

    let i = 0;
    const step = () => {
      if (cancelled) return;
      if (i >= visited.length) {
        setActiveKey(null);
        setOrchPos(CENTER);
        setCarrying(false);
        return;
      }
      const station = visited[i];
      const pos = positions.get(station.key) ?? CENTER;
      setActiveKey(station.key);
      setCarrying(true);
      setOrchPos(pos);
      timers.push(
        setTimeout(() => {
          if (cancelled) return;
          setCarrying(false);
          timers.push(
            setTimeout(() => {
              if (cancelled) return;
              setLogged((l) => [...l, station]);
              setCarrying(true);
              setOrchPos(CENTER);
              timers.push(
                setTimeout(() => {
                  if (cancelled) return;
                  i += 1;
                  step();
                }, WALK_MS),
              );
            }, PROCESS_MS),
          );
        }, WALK_MS),
      );
    };

    // Deferred one tick so the reset + kickoff never set state synchronously
    // within the effect body itself — every update below runs from a timer.
    timers.push(
      setTimeout(() => {
        if (cancelled) return;
        setLogged([]);
        setActiveKey(null);
        setOrchPos(CENTER);
        setCarrying(false);
        if (visited.length === 0) return;
        if (reducedMotion.current) {
          setLogged(visited);
          return;
        }
        step();
      }, 0),
    );

    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sequenceKey]);

  const loggedKeys = useMemo(() => new Set(logged.map((s) => s.key)), [logged]);

  function displayStatus(s: Station): DisplayStatus {
    if (s.key === activeKey) return "active";
    if (loggedKeys.has(s.key)) return s.base === "failed" ? "failed" : "done";
    if (s.base === "skipped") return "skipped";
    return "idle";
  }

  return (
    <div className="card !p-0 overflow-hidden">
      <div className="flex items-center justify-between border-b border-neutral-100 px-5 py-3">
        <div>
          <h2 className="eyebrow">Agent workspace</h2>
          <p className="mt-0.5 text-xs text-neutral-400">
            {call ? "What actually happened on this call" : "Pick or add a call to watch it get worked"}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {call && (
            <Link
              href={`/calls/${call.call.id}`}
              className="text-xs font-medium text-neutral-500 underline hover:text-neutral-800"
            >
              View full call log →
            </Link>
          )}
          <button
            onClick={() => setReplayNonce((n) => n + 1)}
            disabled={visited.length === 0}
            className="btn text-xs"
          >
            ↻ Replay
          </button>
        </div>
      </div>

      <div className="relative aspect-[16/9] w-full bg-gradient-to-b from-neutral-50 to-white">
        {stillSpawning && <SpawningIndicator />}

        {!stillSpawning && stations.length > 0 && (
          <div className="absolute left-1/2 top-1/2 aspect-square w-[76%] -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-dashed border-neutral-200" />
        )}

        {!stillSpawning && stations.map((s) => {
          const pos = positions.get(s.key) ?? CENTER;
          const status = displayStatus(s);
          const style = STATUS_STYLE[status];
          return (
            <div
              key={s.key}
              style={{ left: `${pos.x}%`, top: `${pos.y}%` }}
              className="absolute -translate-x-1/2 -translate-y-1/2 flex flex-col items-center transition-[left,top] duration-700"
            >
              {status === "active" && (
                <svg className="absolute -z-10" width={90} height={90} viewBox="0 0 90 90">
                  <circle cx="45" cy="45" r="26" className="station-pulse fill-amber-300" />
                </svg>
              )}
              <StickFigure stroke={style.stroke} scale={0.85} walking={false} thinking={status === "active"} />
              <span className="mt-1 max-w-[6.5rem] truncate text-center text-[11px] font-medium text-neutral-600">
                {s.label}
              </span>
              {s.kind === "stage" && (
                <span className="text-[9px] uppercase tracking-wide text-neutral-300">pipeline</span>
              )}
              <span className={`mt-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-medium ${style.badge}`}>
                {style.text}
              </span>
            </div>
          );
        })}

        {!stillSpawning && (stations.length === 0 ? (
          <p className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-center text-sm text-neutral-400">
            The orchestrator is idle — nothing to show yet.
          </p>
        ) : (
          <div
            style={{
              left: `${orchPos.x}%`,
              top: `${orchPos.y}%`,
              transitionProperty: "left, top",
              transitionDuration: `${WALK_MS}ms`,
              // Discrete steps while walking so it reads as footsteps, not a
              // glide; a plain ease once it's arrived and settling.
              transitionTimingFunction: carrying ? "steps(10, end)" : "ease-out",
            }}
            className="absolute -translate-x-1/2 -translate-y-1/2 flex flex-col items-center"
          >
            {carrying && <PaperToken />}
            <StickFigure stroke="stroke-neutral-900" scale={1.15} walking={carrying} thinking={false} />
            <span className="mt-1 text-[11px] font-semibold text-neutral-800">Orchestrator</span>
          </div>
        ))}
      </div>

      <ul className="divide-y divide-neutral-100 border-t border-neutral-100 font-mono text-[11px]">
        {logged.map((s) => (
          <li key={s.key} className="flex items-center justify-between px-5 py-2">
            <span className="text-neutral-600">{s.label.toUpperCase()}</span>
            <span className={s.base === "failed" ? "text-red-500" : "text-emerald-600"}>
              {s.base === "failed" ? "failed" : "ok"}
            </span>
          </li>
        ))}
        {logged.length === 0 && (
          <li className="px-5 py-6 text-center text-neutral-400">
            {stillSpawning
              ? "Spawning agents…"
              : stations.length === 0
                ? "Nothing to log yet."
                : "Waiting for the orchestrator to get to work…"}
          </li>
        )}
      </ul>
    </div>
  );
}
