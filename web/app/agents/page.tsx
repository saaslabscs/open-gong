"use client";

import { useCallback, useEffect, useState } from "react";
import {
  attachSkill, createAgent, deleteAgent, detachSkill, getAgent, listAgents, listSkills, updateAgent,
  type Agent, type Skill,
} from "@/lib/api";

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selected, setSelected] = useState<Agent | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listAgents().then(setAgents).catch(() => {});
    listSkills().then(setSkills).catch(() => {});
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function openAgent(a: Agent) {
    const full = await getAgent(a.id);
    setSelected(full);
  }

  async function submit() {
    setError(null);
    try {
      if (!name.trim() || !description.trim() || !systemPrompt.trim()) {
        throw new Error("name, description, and system prompt are all required");
      }
      await createAgent({ name, description, system_prompt: systemPrompt });
      setName(""); setDescription(""); setSystemPrompt("");
      refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  async function toggleEnabled(a: Agent) {
    await updateAgent(a.id, { enabled: !a.enabled });
    refresh();
  }

  async function remove(a: Agent) {
    await deleteAgent(a.id);
    if (selected?.id === a.id) setSelected(null);
    refresh();
  }

  async function attach(skillId: string) {
    if (!selected) return;
    await attachSkill(selected.id, skillId);
    openAgent(selected);
  }

  async function detach(skillId: string) {
    if (!selected) return;
    await detachSkill(selected.id, skillId);
    openAgent(selected);
  }

  return (
    <main className="mx-auto flex max-w-5xl gap-8 px-6 py-10">
      <div className="flex-1">
        <h1 className="eyebrow mb-4 text-lg font-semibold">Agents</h1>

        <div className="mb-6 rounded-xl border border-neutral-200 p-4">
          <h2 className="mb-2 text-sm font-medium">New agent</h2>
          <input
            value={name} onChange={(e) => setName(e.target.value)} placeholder="Name"
            className="mb-2 w-full rounded-lg border border-neutral-300 px-3 py-1.5 text-sm"
          />
          <textarea
            value={description} onChange={(e) => setDescription(e.target.value)}
            placeholder="Description (what the orchestrator sees)"
            className="mb-2 w-full rounded-lg border border-neutral-300 px-3 py-1.5 text-sm" rows={2}
          />
          <textarea
            value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="System prompt (when to use which skill)"
            className="mb-2 w-full rounded-lg border border-neutral-300 px-3 py-1.5 text-sm" rows={3}
          />
          {error && <p className="mb-2 text-xs text-red-600">{error}</p>}
          <button onClick={submit} className="btn">Create agent</button>
        </div>

        <ul className="space-y-2">
          {agents.map((a) => (
            <li key={a.id} className="rounded-xl border border-neutral-200 px-4 py-3">
              <div className="flex items-center justify-between">
                <button onClick={() => openAgent(a)} className="text-left font-medium hover:underline">
                  {a.name}
                </button>
                <div className="flex items-center gap-2 text-xs">
                  <button onClick={() => toggleEnabled(a)} className="text-neutral-500 hover:text-neutral-900">
                    {a.enabled ? "Disable" : "Enable"}
                  </button>
                  <button onClick={() => remove(a)} className="text-red-500 hover:text-red-700">Delete</button>
                </div>
              </div>
              <p className="mt-1 text-xs text-neutral-500">{a.description}</p>
            </li>
          ))}
          {agents.length === 0 && (
            <li className="rounded-xl border border-dashed border-neutral-200 px-4 py-8 text-center text-sm text-neutral-400">
              No agents yet — create one above.
            </li>
          )}
        </ul>
      </div>

      {selected && (
        <div className="w-72 shrink-0 rounded-xl border border-neutral-200 p-4">
          <h2 className="mb-2 text-sm font-medium">{selected.name} — skills</h2>
          <ul className="mb-3 space-y-1">
            {(selected.skills ?? []).map((s) => (
              <li key={s.id} className="flex items-center justify-between text-sm">
                <span>{s.name}</span>
                <button onClick={() => detach(s.id)} className="text-xs text-red-500 hover:text-red-700">Remove</button>
              </li>
            ))}
            {(selected.skills ?? []).length === 0 && (
              <li className="text-xs text-neutral-400">No skills attached.</li>
            )}
          </ul>
          <label className="text-xs text-neutral-500">Attach a skill</label>
          <select
            onChange={(e) => e.target.value && attach(e.target.value)}
            value=""
            className="mt-1 w-full rounded-lg border border-neutral-300 px-2 py-1.5 text-sm"
          >
            <option value="">Choose a skill…</option>
            {skills
              .filter((s) => !(selected.skills ?? []).some((a) => a.id === s.id))
              .map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
          </select>
        </div>
      )}
    </main>
  );
}
