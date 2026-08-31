import { Section } from "@/components/Section";
import { usePrefs } from "@/hooks/usePrefs";
import type { ThemePref } from "@/lib/prefs";

export function Settings() {
  const { prefs, setPref, reset } = usePrefs();

  return (
    <div className="mx-auto max-w-2xl px-4 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
      <p className="mt-2 text-sm text-[var(--text-secondary)]">
        Stored in this browser only. Veilix has no accounts, so these never
        reach the server — a preference saved server-side would need an
        identifier to key it by, and that identifier is exactly the tracking
        token this product refuses to create.
      </p>

      <Section title="Appearance">
        <div className="space-y-5">
          <Field
            label="Theme"
            hint="Follows your system setting unless you choose otherwise."
          >
            <select
              value={prefs.theme}
              onChange={(event) =>
                setPref("theme", event.target.value as ThemePref)
              }
              className={selectClass}
            >
              <option value="system">Match system</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </Field>
        </div>
      </Section>

      <Section title="Search">
        <div className="space-y-5">
          <Field
            label="Safe search"
            hint="Applied to every search unless overridden by the filter on a results page."
          >
            <select
              value={String(prefs.safesearch)}
              onChange={(event) =>
                setPref("safesearch", Number(event.target.value) as 0 | 1 | 2)
              }
              className={selectClass}
            >
              <option value="0">Off</option>
              <option value="1">Moderate</option>
              <option value="2">Strict</option>
            </select>
          </Field>

          <Field
            label="Language"
            hint="Restricting language narrows results and is sent to upstream engines."
          >
            <select
              value={prefs.language}
              onChange={(event) => setPref("language", event.target.value)}
              className={selectClass}
            >
              <option value="auto">Any language</option>
              <option value="en">English</option>
              <option value="de">German</option>
              <option value="fr">French</option>
              <option value="es">Spanish</option>
              <option value="hi">Hindi</option>
              <option value="ja">Japanese</option>
            </select>
          </Field>
        </div>
      </Section>

      <Section title="Results">
        <div className="space-y-5">
          <Toggle
            label="Open results in a new tab"
            checked={prefs.openInNewTab}
            onChange={(value) => setPref("openInNewTab", value)}
          />
          <Toggle
            label="Show which engines found each result"
            hint="Useful for judging how much agreement is behind a result."
            checked={prefs.showProvenance}
            onChange={(value) => setPref("showProvenance", value)}
          />
          <Toggle
            label="Load thumbnails"
            hint="Thumbnails are proxied through this instance, so image hosts never see your address. Turning them off makes pages lighter and sends fewer requests."
            checked={prefs.showThumbnails}
            onChange={(value) => setPref("showThumbnails", value)}
          />
        </div>
      </Section>

      <Section title="Your data">
        <p className="text-sm text-[var(--text-secondary)]">
          Everything Veilix stores about you is on this page. There is nothing
          held server-side to export or erase, because nothing is collected —
          see the{" "}
          <a href="/privacy" className="text-[var(--accent)] hover:underline">
            privacy model
          </a>{" "}
          for the complete inventory.
        </p>
        <button
          type="button"
          onClick={reset}
          className="mt-4 rounded-full border border-[var(--border-subtle)] px-4 py-2 text-sm transition-colors hover:bg-[var(--surface-sunken)]"
        >
          Reset settings and clear local storage
        </button>
      </Section>
    </div>
  );
}

const selectClass =
  "rounded-[var(--radius-input)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 py-2 text-sm transition-colors hover:border-[var(--border-strong)]";

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-wrap items-center justify-between gap-3">
      <span className="flex-1">
        <span className="text-sm">{label}</span>
        {hint && (
          <span className="mt-0.5 block text-xs text-[var(--text-muted)]">
            {hint}
          </span>
        )}
      </span>
      {children}
    </label>
  );
}

/**
 * A checkbox styled as a switch.
 *
 * The real `<input type="checkbox">` is present and only visually hidden, so
 * it keeps native keyboard behaviour, form semantics, and the checked state a
 * screen reader announces. A `<div role="switch">` would need all of that
 * rebuilt by hand, and would usually be rebuilt slightly wrong.
 */
function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start justify-between gap-4">
      <span className="flex-1">
        <span className="text-sm">{label}</span>
        {hint && (
          <span className="mt-0.5 block text-xs leading-relaxed text-[var(--text-muted)]">
            {hint}
          </span>
        )}
      </span>
      <span className="relative mt-0.5 shrink-0">
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
          className="peer sr-only"
        />
        <span
          aria-hidden="true"
          className="block h-6 w-10 rounded-full bg-[var(--border-strong)] transition-colors peer-checked:bg-[var(--accent)] peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-[var(--focus-ring)]"
        />
        <span
          aria-hidden="true"
          className="pointer-events-none absolute left-0.5 top-0.5 size-5 rounded-full bg-white transition-transform peer-checked:translate-x-4"
        />
      </span>
    </label>
  );
}
