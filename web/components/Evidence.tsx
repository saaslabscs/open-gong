import type { Evidence } from "@/lib/api";

export function EvidenceLinks({ evidence }: { evidence: Evidence[] }) {
  if (!evidence?.length) return null;
  return (
    <span className="ml-1.5 inline-flex gap-1 align-baseline">
      {evidence.map((ev, i) => (
        <a
          key={i}
          href={`#line-${ev.line}`}
          title={ev.quote}
          className="rounded bg-blue-50 px-1.5 py-0.5 text-[11px] font-medium text-blue-700 hover:bg-blue-100"
        >
          L{ev.line}
        </a>
      ))}
    </span>
  );
}
