import { Section } from '@/components/Section';

/**
 * The privacy page.
 *
 * Written to be checkable rather than reassuring. It states the limits as
 * plainly as the guarantees, because a privacy page that only lists strengths
 * is marketing, and this product's whole claim is that it can be verified.
 */
export function Privacy() {
  return (
    <article className="mx-auto max-w-2xl px-4 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">
        What Veilix knows
      </h1>
      <p className="mt-3 text-sm leading-relaxed text-[var(--text-secondary)]">
        This page describes what the system does with data. It is written so you
        can check it against the source, not so it sounds good. Where the
        architecture cannot guarantee something, it says so.
      </p>
      <Section title="Never stored">
        <Table
          rows={[
            [
              "Your IP address",
              "Never written to disk or to the cache. See below.",
            ],
            [
              "Your search queries",
              "Held in memory for one request. Never persisted with any identifier.",
            ],
            [
              "Search history",
              "Not collected. There is no table, key, or file for it.",
            ],
            ["Accounts, emails, passwords", "The system has no user accounts."],
            ["Tracking cookies", "None are set."],
            ["Browser fingerprints", "Not computed."],
            ["Advertising identifiers", "None. There are no ads."],
          ]}
        />
      </Section>
      <Section title="Stored, briefly">
        <Table
          rows={[
            [
              "Rate-limit counters",
              "Keyed by a rotating hash, expires in minutes. See below.",
            ],
            [
              "Cached results",
              "Keyed by the query alone, with no identity. Short expiry, and can be turned off.",
            ],
            [
              "Aggregate metrics",
              "Request counts and latency. Counters only — never per-user series.",
            ],
            [
              "Operational logs",
              "Timestamp, route, status, duration. No query text, no IP.",
            ],
          ]}
        />
        <p className="mt-4 text-sm leading-relaxed text-[var(--text-secondary)]">
          That is the complete list. If something is not in these two tables,
          the system does not keep it.
        </p>
      </Section>
      <Section title="Counting you without remembering you">
        <p className="text-sm leading-relaxed text-[var(--text-secondary)]">
          Blocking abuse means telling clients apart, and anonymous clients are
          told apart by IP address. That is a real tension with a no-tracking
          product, so here is exactly how it is resolved.
        </p>
        <pre className="mt-3 overflow-x-auto rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-sunken)] p-3 font-mono text-2xs">
          key = HMAC-SHA256(your_ip, salt_that_rotates_daily)
        </pre>
        <p className="mt-3 text-sm leading-relaxed text-[var(--text-secondary)]">
          The raw address is used only as input to that hash and is never
          written anywhere. The salt lives in memory, rotates every day, and is
          never saved — so once it rotates, yesterday's keys cannot be linked
          back to any address by anyone, including the operator, including
          someone who seizes the database. Your activity today cannot be
          connected to your activity yesterday, because the calculation that
          would do it no longer exists.
        </p>
      </Section>
      <Section title="What upstream search engines see">
        <p className="text-sm leading-relaxed text-[var(--text-secondary)]">
          Veilix forwards your query to many engines. They receive the query
          text and <strong>this server's address, not yours</strong>. They do
          not get your IP, your cookies, or a referrer identifying you.
        </p>
        <Caveat>
          On a small instance the crowd you are hiding in is small. If you are
          the only person using it, “the instance searched for X” and “you
          searched for X” are the same statement to anyone watching the upstream
          side. Meta-search hides you in a crowd, and a private instance has no
          crowd.
        </Caveat>
      </Section>
      <Section title="Images">
        <p className="text-sm leading-relaxed text-[var(--text-secondary)]">
          Image results point at third-party hosts. Loaded directly, your
          browser would connect to each one and hand it your address for content
          you never chose to load from them. Veilix fetches thumbnails
          server-side instead, so those hosts see the instance. The cost is this
          server's bandwidth, which is the right side of that trade.
        </p>
      </Section>
      <Section title="The cache, and the one thing it leaks">
        <p className="text-sm leading-relaxed text-[var(--text-secondary)]">
          Results are cached by query alone, with nothing identifying who asked
          — which is what makes a shared cache compatible with this design at
          all.
        </p>
        <Caveat>
          A shared cache is a timing side channel. Someone able to measure
          response times can tell a cache hit from a miss, and so learn that{" "}
          <em>somebody</em> searched a term recently. They learn nothing about
          who. A short expiry limits the window; it does not remove the channel,
          and no design keeps a shared cache and removes it entirely.
        </Caveat>
      </Section>
      <Section title="What the operator can still see">
        <p className="text-sm leading-relaxed text-[var(--text-secondary)]">
          A privacy page that lists only strengths is marketing, so:
        </p>
        <ul className="mt-3 space-y-2 text-sm leading-relaxed text-[var(--text-secondary)]">
          <Bullet>
            The proxy terminates HTTPS, so queries exist in its memory while a
            request is being handled.
          </Bullet>
          <Bullet>
            The API process sees every query in order to do its job.
          </Bullet>
          <Bullet>
            Anyone with root on this machine could attach a debugger, capture
            memory, or modify the code to log queries. No application design
            prevents that.
          </Bullet>
          <Bullet>
            The cache holds query hashes. They cannot be reversed, but someone
            with access can confirm a <em>guess</em> by computing its hash.
          </Bullet>
        </ul>
        <p className="mt-4 rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4 text-sm leading-relaxed">
          What this system offers is a design that does not retain identifying
          data and does not want it. What it cannot offer is protection from
          whoever runs the machine. If you do not trust this operator, run your
          own instance — which is precisely why it is built to be self-hosted.
          <strong className="mt-2 block">
            Veilix does not claim to make you anonymous, and any claim that it
            does would be false.
          </strong>
        </p>
      </Section>
    </article>
  );
}

function Table({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="divide-y divide-[var(--border-subtle)] overflow-hidden rounded-[var(--radius-card)] border border-[var(--border-subtle)]">
      {rows.map(([term, detail]) => (
        <div
          key={term}
          className="grid gap-1 px-4 py-3 sm:grid-cols-[minmax(0,14rem)_1fr] sm:gap-4"
        >
          <dt className="text-sm font-medium">{term}</dt>
          <dd className="text-sm text-[var(--text-secondary)]">{detail}</dd>
        </div>
      ))}
    </dl>
  );
}

function Caveat({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-3 border-l-2 border-[var(--warn-text)] py-1 pl-4 text-sm leading-relaxed text-[var(--text-secondary)]">
      {children}
    </p>
  );
}

function Bullet({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex gap-2">
      <span aria-hidden="true" className="text-[var(--text-muted)]">
        —
      </span>
      <span>{children}</span>
    </li>
  );
}
