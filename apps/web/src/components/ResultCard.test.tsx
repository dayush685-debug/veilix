import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ResultCard } from './ResultCard';
import { PrefsProvider } from '@/hooks/usePrefs';
import type { SearchResult } from '@/lib/api';

function makeResult(overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    url: 'https://example.com/article',
    title: 'An Example Result',
    snippet: 'A snippet describing the result.',
    kind: 'web',
    domain: 'example.com',
    engines: ['mojeek', 'qwant'],
    score: 2.5,
    published_at: null,
    author: null,
    media: null,
    ...overrides,
  };
}

function renderCard(result: SearchResult) {
  return render(
    <PrefsProvider>
      <ResultCard result={result} />
    </PrefsProvider>,
  );
}

describe('ResultCard', () => {
  it('renders the title as a link to the result', () => {
    renderCard(makeResult());
    const link = screen.getByRole('link', { name: 'An Example Result' });
    expect(link).toHaveAttribute('href', 'https://example.com/article');
  });

  it('always sets rel=noreferrer on outbound links', () => {
    renderCard(makeResult());
    // Stops the destination learning that Veilix sent the visitor, and closes
    // reverse-tabnabbing via window.opener.
    expect(screen.getByRole('link', { name: 'An Example Result' })).toHaveAttribute(
      'rel',
      'noreferrer',
    );
  });

  it('shows which engines returned the result', () => {
    renderCard(makeResult());
    expect(screen.getByText(/mojeek · qwant/)).toBeInTheDocument();
  });

  describe('hostile content (SF-005)', () => {
    it('escapes HTML in the title rather than rendering it', () => {
      // The title is authored by whoever ranked for the query.
      const hostile = '<img src=x onerror="alert(1)">Click me';
      renderCard(makeResult({ title: hostile }));

      // Present as text...
      expect(screen.getByText(hostile)).toBeInTheDocument();
      // ...and not as DOM. If this fails, a search result can run script.
      expect(document.querySelector('img[onerror]')).toBeNull();
    });

    it('escapes HTML in the snippet rather than rendering it', () => {
      const hostile = '<script>alert(1)</script>injected';
      renderCard(makeResult({ snippet: hostile }));

      expect(screen.getByText(hostile)).toBeInTheDocument();
      expect(document.querySelector('script')).toBeNull();
    });
  });

  describe('image results', () => {
    it('uses the proxied image path, never a third-party host', () => {
      renderCard(
        makeResult({
          kind: 'image',
          title: 'A photo',
          media: {
            image_url: '/img?url=https%3A%2F%2Fcdn.example.net%2Fp.jpg&h=abc',
            thumbnail_url: '/img?url=https%3A%2F%2Fcdn.example.net%2Fp.jpg&h=abc',
            width: null,
            height: null,
            duration_s: null,
            image_format: 'JPEG',
          },
        }),
      );

      const image = screen.getByRole('img', { name: 'A photo' });
      const src = image.getAttribute('src') ?? '';
      // A src starting with http would mean the browser connects directly to
      // the image host and hands it the viewer's address.
      expect(src.startsWith('/img?')).toBe(true);
      expect(src.startsWith('http')).toBe(false);
    });

    it('gives image results a meaningful alt text', () => {
      renderCard(
        makeResult({
          kind: 'image',
          title: 'Aurora over a fjord',
          media: {
            image_url: '/img?url=x&h=y',
            thumbnail_url: '/img?url=x&h=y',
            width: null,
            height: null,
            duration_s: null,
            image_format: null,
          },
        }),
      );
      // The title is the only description available; alt="" would leave a
      // screen-reader user with nothing.
      expect(screen.getByAltText('Aurora over a fjord')).toBeInTheDocument();
    });
  });
});
