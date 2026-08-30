const SHORTCUTS: [string, string][] = [
  ['/', 'Focus the search box from anywhere'],
  ['Enter', 'Run the search'],
  ['↑ ↓', 'Move through suggestions'],
  ['Esc', 'Dismiss suggestions or leave the search box'],
  ['← →', 'Move between result categories'],
  ['s', 'Open settings'],
  ['g', 'Go to the home page'],
  ['?', 'Show this page'],
];

export function Shortcuts() {
  return (
    <div className="mx-auto max-w-2xl px-4 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Keyboard shortcuts</h1>
      <p className="mt-3 text-sm text-[var(--text-secondary)]">
        Shortcuts are suppressed while you are typing in a field, so pressing
        a letter in the search box types it rather than triggering an action.
        Escape is the exception — it always dismisses.
      </p>

      <dl className="mt-8 divide-y divide-[var(--border-subtle)] overflow-hidden rounded-[var(--radius-card)] border border-[var(--border-subtle)]">
        {SHORTCUTS.map(([key, description]) => (
          <div key={key} className="flex items-center gap-4 px-4 py-3">
            <dt className="w-20 shrink-0">
              <kbd className="rounded border border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-2 py-1 font-mono text-xs">
                {key}
              </kbd>
            </dt>
            <dd className="text-sm text-[var(--text-secondary)]">{description}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
