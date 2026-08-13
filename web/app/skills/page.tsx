"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createSkill, deleteSkill, listSkills, uploadSkill, type Skill } from "@/lib/api";

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [whenToUse, setWhenToUse] = useState("");
  const [bodyMd, setBodyMd] = useState("");
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => listSkills().then(setSkills).catch(() => {}), []);
  useEffect(() => { refresh(); }, [refresh]);

  async function submit() {
    setError(null);
    try {
      if (!name.trim() || !description.trim() || !whenToUse.trim() || !bodyMd.trim()) {
        throw new Error("all fields are required");
      }
      await createSkill({ name, description, when_to_use: whenToUse, body_md: bodyMd, fields: null });
      setName(""); setDescription(""); setWhenToUse(""); setBodyMd("");
      refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  async function upload(file: File) {
    setError(null);
    try {
      await uploadSkill(file);
      refresh();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }

  async function remove(s: Skill) {
    await deleteSkill(s.id);
    refresh();
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="eyebrow mb-4 text-lg font-semibold">Skills</h1>

      <div className="mb-6 rounded-xl border border-neutral-200 p-4">
        <h2 className="mb-2 text-sm font-medium">New skill</h2>
        <input
          value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (kebab-case)"
          className="mb-2 w-full rounded-lg border border-neutral-300 px-3 py-1.5 text-sm"
        />
        <input
          value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Description"
          className="mb-2 w-full rounded-lg border border-neutral-300 px-3 py-1.5 text-sm"
        />
        <input
          value={whenToUse} onChange={(e) => setWhenToUse(e.target.value)} placeholder="When to use"
          className="mb-2 w-full rounded-lg border border-neutral-300 px-3 py-1.5 text-sm"
        />
        <textarea
          value={bodyMd} onChange={(e) => setBodyMd(e.target.value)} placeholder="Instructions (prose)"
          className="mb-2 w-full rounded-lg border border-neutral-300 px-3 py-1.5 text-sm" rows={4}
        />
        {error && <p className="mb-2 text-xs text-red-600">{error}</p>}
        <div className="flex items-center gap-2">
          <button onClick={submit} className="btn">Create skill</button>
          <span className="text-xs text-neutral-400">or</span>
          <button onClick={() => fileRef.current?.click()} className="btn">Upload .md file</button>
          <input
            ref={fileRef} type="file" accept=".md" className="hidden"
            onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
          />
        </div>
      </div>

      <ul className="space-y-2">
        {skills.map((s) => (
          <li key={s.id} className="rounded-xl border border-neutral-200 px-4 py-3">
            <div className="flex items-center justify-between">
              <span className="font-medium">{s.name}</span>
              <div className="flex items-center gap-2 text-xs">
                <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-neutral-500">{s.source}</span>
                <button onClick={() => remove(s)} className="text-red-500 hover:text-red-700">Delete</button>
              </div>
            </div>
            <p className="mt-1 text-xs text-neutral-500">{s.description}</p>
          </li>
        ))}
        {skills.length === 0 && (
          <li className="rounded-xl border border-dashed border-neutral-200 px-4 py-8 text-center text-sm text-neutral-400">
            No skills yet — create or upload one above.
          </li>
        )}
      </ul>
    </main>
  );
}
