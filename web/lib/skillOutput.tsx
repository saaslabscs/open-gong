import type { Evidence } from "./api";

// A claims-list field: an array of cited statements. Rendered as one list item
// each by both the call view and the share page.
export type ClaimItem = { text: string; evidence: Evidence[] };

/** The receipt chip. The product's whole promise is that a claim can be
 * checked, so this shows EVERY quote backing it and admits how many there
 * are — three divergent copies of this once meant the default Summary tab
 * showed one quote of three and said nothing about the rest.
 *
 * With `onJump` it is a button that scrolls the transcript rail to the first
 * cited line; without one (the public share page, which carries no
 * transcript) the quotes are a static tooltip. */
export function Cite({ evidence, onJump }: { evidence: Evidence[]; onJump?: (line: number) => void }) {
  if (!evidence?.length) return null;
  const title = evidence.map((e) => `L${e.line}: "${e.quote}"`).join("\n");
  const label = `❝ proof${evidence.length > 1 ? ` ·${evidence.length}` : ""}`;
  if (!onJump) {
    return <span className="cite cursor-help" title={title}>{label}</span>;
  }
  return (
    <button type="button" className="cite" title={title} onClick={() => onJump(evidence[0].line)}>
      {label}
    </button>
  );
}

/** Formats one skill-output field generically by shape, not by name — the
 * frontend mirror of `backend/app/render.py::_render_field`. A score is
 * `{score, justification, evidence}`, a check is `{value, evidence}`, and
 * anything else falls back to plain text. Array fields (claims lists) are
 * handled by the caller, which renders one list item per claim.
 *
 * Shared by `components/CallView.tsx` and `app/share/[token]/page.tsx` so a
 * call's notes dispatch identically whether viewed internally or shared. */
export function renderScalarField(value: unknown): { text: string; evidence: Evidence[] } {
  if (value && typeof value === "object" && "score" in (value as Record<string, unknown>)) {
    const v = value as { score: number; justification?: string; evidence: Evidence[] };
    return { text: `${v.score} — ${v.justification ?? ""}`, evidence: v.evidence ?? [] };
  }
  if (value && typeof value === "object" && "value" in (value as Record<string, unknown>)) {
    const v = value as { value: boolean | null; evidence: Evidence[] };
    return { text: v.value ? "yes" : v.value === false ? "no" : "—", evidence: v.evidence ?? [] };
  }
  return { text: String(value ?? ""), evidence: [] };
}
