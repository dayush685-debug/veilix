import type { ReactNode } from "react";

/**
 * A titled block on a content page.
 *
 * One definition because Admin, Privacy and Settings each had their own copy
 * with the spacing drifted apart (mt-8 vs mt-10, mb-3 vs mb-4 vs none).
 */
export function Section({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="mt-8 border-t border-[var(--border-subtle)] pt-6">
      <h2 className="mb-4 text-sm font-medium uppercase tracking-wide text-[var(--text-muted)]">
        {title}
      </h2>
      {children}
    </section>
  );
}
