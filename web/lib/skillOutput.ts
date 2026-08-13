import type { Evidence } from "./api";

// A claims-list field: an array of cited statements. Rendered as one list item
// each by both the call view and the share page.
export type ClaimItem = { text: string; evidence: Evidence[] };

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
