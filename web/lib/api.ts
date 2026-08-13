// Client-callable API layer. Browser fetches hit the backend directly
// (CORS allows localhost:3000). Override with NEXT_PUBLIC_API_BASE.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type Evidence = { quote: string; line: number };
export type RunStatus = "shipped" | "partial" | "failed" | "running" | "pending";

export type CallSummary = {
  id: string;
  title: string;
  source: string;
  duration_s: number | null;
  recorded_at: string;
  run_status: RunStatus;
};

export type Stage = {
  name: string;
  status: "ok" | "failed" | "pending" | "skipped";
  attempts: number;
  cost_usd: number;
  error: string | null;
  dropped_claims?: number;
};

// Still used by `ShareSnapshot` below (the /packs + /share features, out of
// scope for this task) even though `Insights`/`ComplianceFinding` — the other
// two shapes that used to live here — are fully dead now that Task 9 removed
// their backing endpoints and this task's `CallDetail` moves to `agent_runs`.
export type ScorecardField = {
  name: string;
  kind: "deterministic" | "judgment";
  value?: boolean | null;
  score?: number;
  max_score?: number;
  justification?: string;
  evidence: Evidence[];
};

export type AgentRunSummary = {
  id: string;
  agent_id: string;
  agent_name: string;
  status: "pending" | "shipped" | "partial" | "failed";
  steps: { name: string; status: string; attempts: number; cost_usd: number; error: string | null }[];
  output: Record<string, Record<string, unknown>> | null;
  edited: boolean;
  cost_usd: number;
};

export type CallDetail = {
  call: {
    id: string;
    title: string;
    source: string;
    duration_s: number | null;
    recorded_at: string;
    participants: string[];
  };
  run: { status: RunStatus; stages: Stage[]; orchestrator_reasoning: string | null };
  transcript: { language: string; lines: { line: number; speaker: string; text: string }[] } | null;
  agent_runs: AgentRunSummary[];
};

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error((await res.text()) || `API error ${res.status}`);
  return res.json();
}

export const apiBase = API_BASE;

export type Status = {
  llm: { ok: boolean; detail: string; provider: string };
  transcription: { ok: boolean; adapter: string; detail: string };
  can_process_uploads: boolean;
};

export const getStatus = () =>
  fetch(`${API_BASE}/api/status`, { cache: "no-store" }).then(j<Status>);

export const listCalls = () =>
  fetch(`${API_BASE}/api/calls`, { cache: "no-store" }).then(j<CallSummary[]>);

export const getCall = (id: string) =>
  fetch(`${API_BASE}/api/calls/${id}`, { cache: "no-store" }).then(j<CallDetail>);

export const uploadCall = (file: File) => {
  const fd = new FormData();
  fd.append("file", file);
  return fetch(`${API_BASE}/api/ingest/upload`, { method: "POST", body: fd }).then(
    j<{ call_id: string; created: boolean }>,
  );
};

export const ingestUrl = (url: string) =>
  fetch(`${API_BASE}/api/ingest/url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  }).then(j<{ call_id: string; created: boolean }>);

export const retryCall = (id: string) =>
  fetch(`${API_BASE}/api/calls/${id}/retry`, { method: "POST" }).then(j);

export const createShare = (id: string) =>
  fetch(`${API_BASE}/api/calls/${id}/share`, { method: "POST" }).then(
    j<{ token: string; url: string }>,
  );

export const getShare = (token: string) =>
  fetch(`${API_BASE}/api/share/${token}`, { cache: "no-store" }).then(
    j<{ snapshot: ShareSnapshot; created_at: string }>,
  );

export type ShareSnapshot = {
  title: string;
  recorded_at: string;
  duration_s: number | null;
  intent: string | null;
  summary: { text: string; evidence: Evidence[] }[];
  objections: { label: string; detail: string; status?: string; evidence: Evidence[] }[];
  next_steps: { text: string; owner?: string; evidence: Evidence[] }[];
  scorecard: { pack: string; fields: ScorecardField[] } | null;
  follow_up_email: { subject: string; body: string } | null;
};

export type Pack = {
  id: string;
  name: string;
  version: number;
  status: "draft" | "active" | "retired";
  instructions: string | null;
  json_schema: { properties: Record<string, unknown> };
  scoring_spec: {
    deterministic: string[];
    judgment: { name: string; max_score: number; instructions: string }[];
  };
};

export const listPacks = () => fetch(`${API_BASE}/api/packs`, { cache: "no-store" }).then(j<Pack[]>);

export const compilePack = (instructions: string) =>
  fetch(`${API_BASE}/api/packs/compile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instructions }),
  }).then(j<Pack>);

export const activatePack = (id: string) =>
  fetch(`${API_BASE}/api/packs/${id}/activate`, { method: "POST" }).then(j<Pack>);

export const deactivatePacks = () =>
  fetch(`${API_BASE}/api/packs/deactivate`, { method: "POST" }).then(j);

export const exportMarkdownUrl = (id: string, transcript = false) =>
  `${API_BASE}/api/calls/${id}/export.md${transcript ? "?transcript=true" : ""}`;
export const exportJsonUrl = (id: string) => `${API_BASE}/api/calls/${id}/export.json`;

export type Skill = {
  id: string;
  name: string;
  description: string;
  when_to_use: string;
  body_md: string;
  fields: { checks?: string[]; scores?: { name: string; max: number }[]; claims?: string[] } | null;
  source: "ui" | "upload";
  version: number;
};

export type Agent = {
  id: string;
  name: string;
  description: string;
  system_prompt: string;
  enabled: boolean;
  skills?: { id: string; name: string }[];
};

export const listAgents = () => fetch(`${API_BASE}/api/agents`, { cache: "no-store" }).then(j<Agent[]>);
export const getAgent = (id: string) => fetch(`${API_BASE}/api/agents/${id}`, { cache: "no-store" }).then(j<Agent>);
export const createAgent = (body: { name: string; description: string; system_prompt: string }) =>
  fetch(`${API_BASE}/api/agents`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then(j<Agent>);
export const updateAgent = (id: string, body: Partial<{ name: string; description: string; system_prompt: string; enabled: boolean }>) =>
  fetch(`${API_BASE}/api/agents/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then(j<Agent>);
export const deleteAgent = (id: string) => fetch(`${API_BASE}/api/agents/${id}`, { method: "DELETE" }).then(j);
export const attachSkill = (agentId: string, skillId: string) =>
  fetch(`${API_BASE}/api/agents/${agentId}/skills`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ skill_id: skillId }),
  }).then(j);
export const detachSkill = (agentId: string, skillId: string) =>
  fetch(`${API_BASE}/api/agents/${agentId}/skills/${skillId}`, { method: "DELETE" }).then(j);

export const listSkills = () => fetch(`${API_BASE}/api/skills`, { cache: "no-store" }).then(j<Skill[]>);
export const createSkill = (body: { name: string; description: string; when_to_use: string; body_md: string; fields: Skill["fields"] }) =>
  fetch(`${API_BASE}/api/skills`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then(j<Skill>);
export const updateSkill = (id: string, body: Partial<{ name: string; description: string; when_to_use: string; body_md: string; fields: Skill["fields"] }>) =>
  fetch(`${API_BASE}/api/skills/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then(j<Skill>);
export const deleteSkill = (id: string) => fetch(`${API_BASE}/api/skills/${id}`, { method: "DELETE" }).then(j);
export const uploadSkill = (file: File) => {
  const fd = new FormData();
  fd.append("file", file);
  return fetch(`${API_BASE}/api/skills/upload`, { method: "POST", body: fd }).then(j<Skill>);
};

export const editAgentRunOutput = (callId: string, agentRunId: string, output: Record<string, unknown>) =>
  fetch(`${API_BASE}/api/calls/${callId}/agent-runs/${agentRunId}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ output }),
  }).then(j<{ ok: boolean; edited: boolean }>);
export const resetAgentRunOutput = (callId: string, agentRunId: string) =>
  fetch(`${API_BASE}/api/calls/${callId}/agent-runs/${agentRunId}/reset`, { method: "POST" }).then(
    j<{ ok: boolean; edited: boolean }>,
  );
