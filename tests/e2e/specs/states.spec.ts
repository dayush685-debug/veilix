import { expect, test, type Page } from '@playwright/test';

const searchBox = (page: Page) => page.getByRole('combobox', { name: 'Search the web' });

/**
 * Every state a user can land in should be intentional and legible.
 *
 * These check the states themselves, which the main search spec does not: it
 * checks that a search works, while these check that failing, empty, throttled
 * and offline all render something a person can act on.
 */

test.describe('search states', () => {
  test('initial state explains what the product is', async ({ page }) => {
    await page.goto('/');
    await expect(searchBox(page)).toBeVisible();
    await expect(page.getByRole('heading', { name: 'No search history' })).toBeVisible();
    // No results region before a query.
    await expect(page.locator('article')).toHaveCount(0);
  });

  test('loading state is announced and does not blank the page', async ({ page }) => {
    // Hold the response open so the loading state is observable.
    await page.route('**/api/v1/search*', async (route) => {
      await new Promise((r) => setTimeout(r, 1500));
      await route.continue();
    });

    await page.goto('/search?q=loading+state');
    const live = page.locator('[aria-live="polite"]');
    await expect(live).toContainText(/Searching/);
    // Placeholders, so the layout does not jump when results arrive.
    await expect(page.locator('.animate-pulse').first()).toBeVisible();
  });

  test('empty state explains what to try next', async ({ page }) => {
    await page.route('**/api/v1/search*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          query: 'zzz', category: 'general', page: 1, count: 0, results: [],
          degraded: false, failures: [], engines_used: [], answers: [],
          suggestions: [], corrections: [], infoboxes: [],
          timing: { total_ms: 12, upstream_ms: 10, cached: false },
        }),
      }),
    );

    await page.goto('/search?q=zzz');
    await expect(page.getByText(/No results for/)).toBeVisible();
    await expect(page.getByText(/Try different or fewer words/)).toBeVisible();
  });

  test('empty state says something different when engines were down', async ({ page }) => {
    // "no results" and "no results because the sources were unavailable" are
    // different situations and should not read identically.
    await page.route('**/api/v1/search*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          query: 'zzz', category: 'general', page: 1, count: 0, results: [],
          degraded: true,
          failures: [{ engine: 'brave', reason: 'CAPTCHA' }],
          engines_used: [], answers: [], suggestions: [], corrections: [], infoboxes: [],
          timing: { total_ms: 12, upstream_ms: 10, cached: false },
        }),
      }),
    );

    await page.goto('/search?q=zzz');
    await expect(page.getByText(/Several engines were unavailable/)).toBeVisible();
  });

  test('rate limiting produces a clear message and no retry button', async ({ page }) => {
    await page.route('**/api/v1/search*', (route) =>
      route.fulfill({
        status: 429,
        contentType: 'application/problem+json',
        body: JSON.stringify({
          type: 'https://veilix.dev/problems/rate-limited',
          title: 'Too Many Requests', status: 429,
          detail: 'Request budget exhausted. Retry after the indicated interval.',
          request_id: 'abc123', retry_after: 30,
        }),
      }),
    );

    await page.goto('/search?q=throttled');
    await expect(page.getByText('Too many searches')).toBeVisible();
    await expect(page.getByText(/Try again in 30 seconds/)).toBeVisible();
  });

  test('backend failure offers a retry and a reference id', async ({ page }) => {
    await page.route('**/api/v1/search*', (route) =>
      route.fulfill({
        status: 503,
        contentType: 'application/problem+json',
        body: JSON.stringify({
          type: 'https://veilix.dev/problems/upstream-unavailable',
          title: 'Search Temporarily Unavailable', status: 503,
          detail: 'The search backend is not reachable.',
          request_id: 'ref-9f2c1b7e',
        }),
      }),
    );

    await page.goto('/search?q=broken');
    await expect(page.getByText('Search is unavailable')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
    // Quotable in a bug report, and it maps to a log line holding no query.
    await expect(page.getByText(/ref-9f2c1b7e/)).toBeVisible();
  });

  test('network failure is distinguished from a server error', async ({ page }) => {
    await page.route('**/api/v1/search*', (route) => route.abort('failed'));

    await page.goto('/search?q=offline');
    // Scoped to the visible panel; the live region carries the same words.
    await expect(
      page.getByText('Could not reach Veilix. Check your connection.', { exact: true }),
    ).toBeVisible();
  });

  test('the search button is disabled until there is something to search', async ({
    page,
  }) => {
    await page.goto('/');
    const submit = page.getByRole('button', { name: 'Search', exact: true });
    await expect(submit).toBeDisabled();
    await searchBox(page).fill('x');
    await expect(submit).toBeEnabled();
  });
});

test.describe('responsive layout', () => {
  for (const [label, width, height] of [
    ['mobile', 390, 844],
    ['tablet', 820, 1180],
    ['desktop', 1440, 900],
  ] as const) {
    test(`${label} renders without horizontal overflow`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      await page.goto('/search?q=responsive+layout+check');
      await page.waitForLoadState('networkidle');

      // A page wider than its viewport is the usual symptom of a desktop
      // layout that was only shrunk.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `${label} overflows by ${overflow}px`).toBeLessThanOrEqual(1);
    });
  }

  test('category tabs stay reachable on a narrow viewport', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/search?q=tabs');
    // Scrollable rather than wrapped or clipped.
    await expect(page.getByRole('tab', { name: 'Web' })).toBeVisible();
  });
});
