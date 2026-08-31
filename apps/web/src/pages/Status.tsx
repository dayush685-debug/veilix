import { useEffect, useState } from 'react';
import { api, type EnginesResponse, type HealthResponse } from '@/lib/api';

/**
 * Public system status.
 *
 * Deliberately operational only: component health, breaker state, and which
 * engines are configured. It shows nothing about who is searching or what for,
 * because a status page that could answer those questions would be a
 * surveillance tool with a nice chart on it.
 */
export function Status() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [engines, setEngines] = useState<EnginesResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      try {
        const [h, e] = await Promise.all([
          api.health(controller.signal),
          api.engines(controller.signal),
        ]);
        if (!controller.signal.aborted) {
          setHealth(h);
          setEngines(e);
        }
      } catch {
        if (!controller.signal.aborted) setFailed(true);
      }
    }

    void load();
    // Polled rather than streamed: a 30-second interval is plenty for a status
    // page, and it avoids holding a connection open per viewer.
    const timer = setInterval(() => void load(), 30_000);

    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, []);

  if (failed) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-10">
        <h1 className="text-2xl font-semibold tracking-tight">Status</h1>
        <p className="mt-4 rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4 text-sm">
          The status endpoint is not responding. That usually means this
          instance is down instead of that the check is broken.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Status</h1>

      {!health ? (
        <p className="mt-4 text-sm text-[var(--text-muted)]">Checking…</p>
      ) : (
        <>
          <div className="mt-6 flex items-center gap-3 rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4">
            <Dot healthy={health.status === 'ok'} />
            <div>
              <p className="text-sm font-medium">
                {health.status === 'ok'
                  ? 'All components operational'
                  : 'Running degraded'}
              </p>
              <p className="text-xs text-[var(--text-muted)]">
                version {health.version} · search backend circuit:{' '}
                {health.breaker_state}
              </p>
            </div>
          </div>

          <section className="mt-8">
            <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-[var(--text-muted)]">
              Components
            </h2>
            <ul className="divide-y divide-[var(--border-subtle)] overflow-hidden rounded-[var(--radius-card)] border border-[var(--border-subtle)]">
              {health.components.map((component) => (
                <li key={component.name} className="flex items-start gap-3 px-4 py-3">
                  <Dot healthy={component.healthy} small />
                  <div className="min-w-0">
                    <p className="text-sm">{component.name}</p>
                    {component.detail && (
                      <p className="text-xs text-[var(--text-muted)]">
                        {component.detail}
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}

      {engines && (
        <section className="mt-8">
          <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-[var(--text-muted)]">
            Search engines
          </h2>
          <p className="text-sm text-[var(--text-secondary)]">
            {engines.count} engines enabled across {engines.categories.length}{' '}
            categories.
          </p>
          {/*
            Configured, not "healthy". Engine health is only known for engines
            that recent searches actually touched, so a green tick here would
            be a claim this page has not earned. Per-search failures are
            reported on the results page, where they are real.
          */}
          <p className="mt-2 text-xs text-[var(--text-muted)]">
            This lists what is configured. Whether a given engine is answering
            right now is only known once a search touches it — results pages
            report that per search, and upstream engines rate-limit self-hosted
            instances often enough that some are usually unavailable.
          </p>
          <ul className="mt-3 flex flex-wrap gap-1.5">
            {engines.engines.slice(0, 40).map((engine) => (
              <li
                key={engine.name}
                className="rounded-full border border-[var(--border-subtle)] px-2.5 py-1 text-2xs text-[var(--text-secondary)]"
              >
                {engine.name}
              </li>
            ))}
            {engines.engines.length > 40 && (
              <li className="px-2.5 py-1 text-2xs text-[var(--text-muted)]">
                +{engines.engines.length - 40} more
              </li>
            )}
          </ul>
        </section>
      )}
    </div>
  );
}

function Dot({ healthy, small = false }: { healthy: boolean; small?: boolean }) {
  return (
    <span
      className={[
        'mt-1 shrink-0 rounded-full',
        small ? 'size-2' : 'size-2.5',
        healthy ? 'bg-[var(--accent)]' : 'bg-[var(--warn-text)]',
      ].join(' ')}
      // Colour alone must not carry the meaning, that fails for colour-blind
      // users and in high-contrast modes.
      role="img"
      aria-label={healthy ? 'Operational' : 'Degraded'}
    />
  );
}
