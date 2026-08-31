import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SearchBar } from './SearchBar';
import { PrefsProvider } from '@/hooks/usePrefs';

function renderBar(props: Partial<React.ComponentProps<typeof SearchBar>> = {}) {
  const onSearch = vi.fn();
  render(
    <PrefsProvider>
      <SearchBar onSearch={onSearch} {...props} />
    </PrefsProvider>,
  );
  return { onSearch, input: screen.getByRole('combobox') };
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ suggestions: ['privacy tools', 'privacy policy'] }),
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('SearchBar', () => {
  it('submits the typed query', async () => {
    const user = userEvent.setup();
    const { onSearch, input } = renderBar();

    await user.type(input, 'meta search');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    expect(onSearch).toHaveBeenCalledWith('meta search');
  });

  it('trims whitespace and ignores an empty query', async () => {
    const user = userEvent.setup();
    const { onSearch, input } = renderBar();

    await user.type(input, '   ');
    // A whitespace-only query would produce a pointless upstream fan-out.
    expect(screen.getByRole('button', { name: 'Search' })).toBeDisabled();

    await user.clear(input);
    await user.type(input, '  spaced  {Enter}');
    expect(onSearch).toHaveBeenCalledWith('spaced');
  });

  it('exposes the ARIA combobox contract', () => {
    const { input } = renderBar();
    // Without these, suggestions are invisible to anyone not looking at the
    // screen, the component would look finished and be unusable.
    expect(input).toHaveAttribute('role', 'combobox');
    expect(input).toHaveAttribute('aria-expanded', 'false');
    expect(input).toHaveAttribute('aria-autocomplete', 'list');
  });

  it('marks the highlighted suggestion with aria-activedescendant', async () => {
    const user = userEvent.setup();
    const { input } = renderBar();

    await user.type(input, 'privacy');
    const options = await screen.findAllByRole('option');
    expect(options.length).toBeGreaterThan(0);

    await user.keyboard('{ArrowDown}');

    await waitFor(() => {
      expect(input).toHaveAttribute('aria-activedescendant', options[0]!.id);
      expect(options[0]).toHaveAttribute('aria-selected', 'true');
    });
  });

  it('wraps around when arrowing past the end of the list', async () => {
    const user = userEvent.setup();
    const { input } = renderBar();

    await user.type(input, 'privacy');
    const options = await screen.findAllByRole('option');

    // Two suggestions: down three times should land back on the first.
    await user.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}');

    await waitFor(() => {
      expect(input).toHaveAttribute('aria-activedescendant', options[0]!.id);
    });
  });

  it('submits the highlighted suggestion on Enter', async () => {
    const user = userEvent.setup();
    const { onSearch, input } = renderBar();

    await user.type(input, 'privacy');
    await screen.findAllByRole('option');
    await user.keyboard('{ArrowDown}{Enter}');

    expect(onSearch).toHaveBeenCalledWith('privacy tools');
  });

  it('submits the typed text when no suggestion is highlighted', async () => {
    const user = userEvent.setup();
    const { onSearch, input } = renderBar();

    await user.type(input, 'privacy');
    await screen.findAllByRole('option');
    // Enter without arrowing must use what the user typed, not guess for them.
    await user.keyboard('{Enter}');

    expect(onSearch).toHaveBeenCalledWith('privacy');
  });

  it('closes the suggestion list on Escape', async () => {
    const user = userEvent.setup();
    const { input } = renderBar();

    await user.type(input, 'privacy');
    await screen.findAllByRole('option');

    await user.keyboard('{Escape}');

    await waitFor(() => {
      expect(screen.queryByRole('option')).not.toBeInTheDocument();
    });
  });

  it('does not request suggestions below three characters', async () => {
    const user = userEvent.setup();
    const { input } = renderBar();

    await user.type(input, 'pr');
    await new Promise((resolve) => setTimeout(resolve, 250));

    // Every keystroke forwarded is another fragment of a query leaving the
    // instance, so the short-prefix gate is a privacy control, not a perf one.
    expect(fetch).not.toHaveBeenCalled();
  });

  it('has an accessible label for the clear button', async () => {
    const user = userEvent.setup();
    const { input } = renderBar();

    await user.type(input, 'something');
    expect(screen.getByRole('button', { name: 'Clear search' })).toBeInTheDocument();
  });
});
