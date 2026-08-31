import { usePrefs } from '@/hooks/usePrefs';
import type { SearchResult } from '@/lib/api';

/**
 * One search result. Every string here was authored by whoever ranked for the
 * query, so two rules hold:
 *
 * 1. Text renders as React children, never `dangerouslySetInnerHTML`, so
 *    markup in a snippet shows as characters instead of DOM.
 * 2. `rel="noreferrer"` on every outbound link, so the destination does not
 *    learn that Veilix sent the visitor.
 */
export function ResultCard({ result }: { result: SearchResult }) {
  const { prefs } = usePrefs();
  const linkTarget = prefs.openInNewTab ? '_blank' : undefined;

  if (result.kind === 'image') return <ImageResult result={result} />;

  return (
    <article className="group py-5 first:pt-0">
      <div className="flex gap-4">
        {prefs.showThumbnails && result.media?.thumbnail_url && (
          <Thumbnail src={result.media.thumbnail_url} />
        )}

        <div className="min-w-0 flex-1">
          <p className="mb-1 truncate text-xs text-[var(--text-muted)]">
            {result.domain}
          </p>

          <h3 className="text-lg leading-snug">
            <a
              href={result.url}
              target={linkTarget}
              // noreferrer also implies noopener, closing the reverse-tabnabbing
              // hole where a target=_blank page can rewrite window.opener.
              rel="noreferrer"
              className="text-[var(--accent)] visited:opacity-80 hover:underline"
            >
              {result.title}
            </a>
          </h3>

          {result.snippet && (
            <p className="mt-1.5 line-clamp-3 text-sm leading-relaxed text-[var(--text-secondary)]">
              {result.snippet}
            </p>
          )}

          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-[var(--text-muted)]">
            {result.published_at && (
              <time dateTime={result.published_at}>
                {formatDate(result.published_at)}
              </time>
            )}
            {result.author && <span>{result.author}</span>}
            {prefs.showProvenance && result.engines.length > 0 && (
              <span className="flex items-center gap-1">
                <span className="sr-only">Found by</span>
                {result.engines.join(' · ')}
              </span>
            )}
          </div>
        </div>
      </div>
    </article>
  );
}

function ImageResult({ result }: { result: SearchResult }) {
  const { prefs } = usePrefs();
  const src = result.media?.thumbnail_url ?? result.media?.image_url;

  return (
    <a
      href={result.url}
      target={prefs.openInNewTab ? '_blank' : undefined}
      rel="noreferrer"
      className="group block overflow-hidden rounded-[var(--radius-card)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] transition-colors hover:border-[var(--border-strong)]"
    >
      <div className="aspect-square overflow-hidden bg-[var(--surface-sunken)]">
        {prefs.showThumbnails && src ? (
          <img
            src={src}
            // The title is the only description available; a generic "image"
            // alt would be worse than useless to a screen reader.
            alt={result.title}
            loading="lazy"
            decoding="async"
            className="size-full object-cover transition-transform duration-200 group-hover:scale-[1.03]"
            // A broken proxied image should collapse quietly rather than show
            // a browser's default broken-image glyph.
            onError={(event) => {
              event.currentTarget.style.display = 'none';
            }}
          />
        ) : (
          <div className="flex size-full items-center justify-center text-xs text-[var(--text-muted)]">
            {prefs.showThumbnails ? 'No preview' : 'Thumbnails off'}
          </div>
        )}
      </div>
      <div className="p-2.5">
        <p className="truncate text-xs font-medium">{result.title}</p>
        <p className="truncate text-2xs text-[var(--text-muted)]">
          {result.domain}
        </p>
      </div>
    </a>
  );
}

function Thumbnail({ src }: { src: string }) {
  return (
    <div className="hidden size-20 shrink-0 overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-sunken)] sm:block">
      <img
        src={src}
        // Decorative here: the heading beside it already carries the meaning,
        // so an alt would make a screen reader read the same thing twice.
        alt=""
        loading="lazy"
        decoding="async"
        className="size-full object-cover"
        onError={(event) => {
          event.currentTarget.parentElement?.style.setProperty('display', 'none');
        }}
      />
    </div>
  );
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}
