import type { RunStatus } from "./api";

// Turn pipeline states into language a user understands and can act on.
export type UiStatus = {
  label: string;
  tone: "green" | "amber" | "red" | "blue" | "neutral";
  hint?: string;
  busy?: boolean;
};

export function humanizeStatus(s: RunStatus): UiStatus {
  switch (s) {
    case "shipped":
      return { label: "Ready", tone: "green" };
    case "partial":
      return { label: "Ready · needs review", tone: "amber", hint: "Some parts were flagged or dropped." };
    case "failed":
      return { label: "Couldn’t finish", tone: "red", hint: "Something failed — you can retry." };
    case "running":
      return { label: "Analyzing…", tone: "blue", busy: true };
    default:
      return { label: "Queued…", tone: "neutral", busy: true };
  }
}

export const tonePill: Record<UiStatus["tone"], string> = {
  green: "bg-emerald-100 text-emerald-800",
  amber: "bg-amber-100 text-amber-800",
  red: "bg-red-100 text-red-800",
  blue: "bg-blue-100 text-blue-800",
  neutral: "bg-neutral-100 text-neutral-600",
};

// Human labels for the internal pipeline stages (shown only in "details").
export const stageLabels: Record<string, string> = {
  transcribe: "Transcribe",
  detect_intent: "Detect call type",
  extract: "Extract insights",
  validate: "Verify evidence",
  score: "Score",
  compliance: "Compliance check",
  compose_email: "Draft email",
};
