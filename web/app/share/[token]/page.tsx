import { getShare, type ShareSnapshot, type SharedAgentRun } from "@/lib/api";
import { Cite, renderScalarField, type ClaimItem } from "@/lib/skillOutput";
import InsightsPanel from "@/components/Insights";
import { notFound } from "next/navigation";

// The snapshot is the frozen guaranteed summary plus a list of per-agent,
// per-skill outputs (see backend/app/render.py::share_snapshot) — the same
// shapes the call detail view renders, minus ids/steps/cost. Fields are
// dispatched by shape, not by name, via the shared `renderScalarField`.
// `Cite` is rendered without `onJump`, so evidence is a static tooltip: there
// is no transcript on a public share page to scroll to.

export default async function SharePage(props: PageProps<"/share/[token]">) {
  const { token } = await props.params;
  let snap: ShareSnapshot;
  try {
    snap = (await getShare(token)).snapshot;
  } catch {
    notFound();
  }

  const agentRuns = (snap.agent_runs ?? []).filter(
    (ar) => Object.keys(ar.output ?? {}).length > 0,
  );

  return (
    <main className="mx-auto max-w-2xl px-6 py-12">
      <div className="mb-6 border-b border-neutral-200 pb-4">
        <h1 className="text-2xl font-semibold tracking-tight">{snap.title}</h1>
        <p className="mt-1 text-xs text-neutral-500">
          {new Date(snap.recorded_at).toLocaleDateString()} · shared via Open Gong
        </p>
      </div>

      {/* The summary is the headline: a shared call commonly has one and no
          agent runs at all, which used to render "No notes were shared". */}
      {snap.insights && (
        <div className="mb-8">
          <InsightsPanel insights={snap.insights} />
        </div>
      )}

      {agentRuns.map((ar, i) => <AgentRunBlock key={`${ar.agent_name}-${i}`} agentRun={ar} />)}

      {agentRuns.length === 0 && !snap.insights && (
        <p className="text-sm text-neutral-500">No notes were shared for this call.</p>
      )}

      <p className="mt-10 text-center text-xs text-neutral-400">
        Read-only shared notes. The full transcript stays private.
      </p>
    </main>
  );
}

function AgentRunBlock({ agentRun }: { agentRun: SharedAgentRun }) {
  return (
    <section className="mb-8">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500">
        {agentRun.agent_name}
        {agentRun.edited && (
          <span className="ml-2 rounded bg-purple-100 px-1.5 py-0.5 text-[11px] normal-case tracking-normal text-purple-700">
            edited by a human
          </span>
        )}
      </h2>
      <div className="space-y-3">
        {Object.entries(agentRun.output ?? {}).map(([skillName, fields]) => (
          <SkillOutput key={skillName} skillName={skillName} fields={fields} />
        ))}
      </div>
    </section>
  );
}

function SkillOutput({ skillName, fields }: { skillName: string; fields: Record<string, unknown> }) {
  return (
    <div className="card">
      <div className="eyebrow">{skillName.replaceAll("-", " ")}</div>
      <ul className="mt-2 space-y-2 text-sm leading-relaxed">
        {Object.entries(fields ?? {}).map(([name, value]) => {
          if (Array.isArray(value)) {
            const items = value as ClaimItem[];
            if (items.length === 0)
              return <li key={name} className="text-neutral-400">{name.replaceAll("_", " ")}: none</li>;
            return items.map((item, i) => (
              <li key={`${name}-${i}`}>{item.text} <Cite evidence={item.evidence} /></li>
            ));
          }
          const rendered = renderScalarField(value);
          return (
            <li key={name} className="flex items-center gap-2">
              <span className="capitalize text-neutral-700">{name.replaceAll("_", " ")}:</span>
              <span className="font-medium">{rendered.text}</span>
              <Cite evidence={rendered.evidence} />
            </li>
          );
        })}
      </ul>
    </div>
  );
}
