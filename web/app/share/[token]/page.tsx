import { getShare, type ShareSnapshot } from "@/lib/api";
import { notFound } from "next/navigation";

export default async function SharePage(props: PageProps<"/share/[token]">) {
  const { token } = await props.params;
  let snap: ShareSnapshot;
  try {
    snap = (await getShare(token)).snapshot;
  } catch {
    notFound();
  }

  return (
    <main className="mx-auto max-w-2xl px-6 py-12">
      <div className="mb-6 border-b border-neutral-200 pb-4">
        <h1 className="text-2xl font-semibold tracking-tight">{snap.title}</h1>
        <p className="mt-1 text-xs text-neutral-500">
          {new Date(snap.recorded_at).toLocaleDateString()}
          {snap.intent && ` · ${snap.intent}`} · shared via Open Gong
        </p>
      </div>

      {snap.summary.length > 0 && (
        <Block title="Summary">
          <ul className="list-disc space-y-1.5 pl-5 text-sm">
            {snap.summary.map((s, i) => <li key={i}>{s.text}</li>)}
          </ul>
        </Block>
      )}

      {snap.objections.length > 0 && (
        <Block title="Objections & concerns">
          <ul className="space-y-1.5 text-sm">
            {snap.objections.map((o, i) => (
              <li key={i}><span className="font-medium">{o.label}</span>: {o.detail}</li>
            ))}
          </ul>
        </Block>
      )}

      {snap.next_steps.length > 0 && (
        <Block title="Next steps">
          <ul className="list-disc space-y-1.5 pl-5 text-sm">
            {snap.next_steps.map((n, i) => (
              <li key={i}>{n.text}{n.owner && <span className="text-neutral-500"> — {n.owner}</span>}</li>
            ))}
          </ul>
        </Block>
      )}

      {snap.scorecard && snap.scorecard.fields.length > 0 && (
        <Block title="Scorecard">
          <ul className="space-y-1 text-sm">
            {snap.scorecard.fields.map((f) => (
              <li key={f.name}>
                <span className="font-medium">{f.name.replaceAll("_", " ")}</span>:{" "}
                {f.kind === "deterministic"
                  ? f.value ? "yes" : f.value === false ? "no" : "—"
                  : `${f.score}/${f.max_score}`}
              </li>
            ))}
          </ul>
        </Block>
      )}

      {snap.follow_up_email && (
        <Block title="Follow-up email">
          <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4 text-sm">
            <p className="font-medium">{snap.follow_up_email.subject}</p>
            <p className="mt-2 whitespace-pre-wrap text-neutral-700">{snap.follow_up_email.body}</p>
          </div>
        </Block>
      )}

      <p className="mt-10 text-center text-xs text-neutral-400">
        Read-only shared notes. The full transcript stays private.
      </p>
    </main>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-6">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500">{title}</h2>
      {children}
    </section>
  );
}
