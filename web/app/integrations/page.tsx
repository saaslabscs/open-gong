"use client";

import { useCallback, useEffect, useState } from "react";
import IntegrationCard from "@/components/IntegrationCard";
import { listIntegrations, type Integration } from "@/lib/api";

export default function IntegrationsPage() {
  const [items, setItems] = useState<Integration[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Lazy initializer keeps Date.now() out of the render body (react-hooks/purity).
  // Minute-granularity "verified 2h ago" doesn't need to tick live.
  const [now] = useState(() => Date.now());

  const refresh = useCallback(
    () =>
      listIntegrations()
        .then((rows) => {
          setItems(rows);
          setLoadError(null);
        })
        .catch(() =>
          setLoadError("Couldn’t reach the Open Gong API. Is the backend running on port 8000?"),
        ),
    [],
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <h1 className="text-lg font-semibold">Integrations</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Connect the CRM your team already works in. Every token is checked against the provider
        before it’s saved, so a connection that appears here is one that works.
      </p>

      {loadError && (
        <div className="mt-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {loadError}
        </div>
      )}

      {items === null && !loadError && <p className="mt-6 text-sm text-neutral-400">Loading…</p>}

      <div className="mt-6 grid items-start gap-4 sm:grid-cols-2">
        {items?.map((it) => (
          <IntegrationCard
            key={it.key}
            integration={it}
            now={now}
            onChange={(next) =>
              setItems((prev) => prev?.map((p) => (p.key === next.key ? next : p)) ?? null)
            }
            onRemoved={refresh}
          />
        ))}
      </div>
    </main>
  );
}
