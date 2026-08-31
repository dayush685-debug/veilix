import { expect, test, type Page } from '@playwright/test';

/** The search input, named to disambiguate it from the filter <select>s,
 *  which also carry the combobox role. */
const searchBox = (page: Page) => page.getByRole('combobox', { name: 'Search the web' });

/**
 * The critical user flow: land, search, read results, navigate.
 *
 * Assertions are written against *behaviour under real conditions*, never
 * against particular results. Upstream engines CAPTCHA and rate-limit this
 * instance constantly, so "the page shows results for X" is a flaky assertion
 * about someone else's infrastructure. "The page renders a coherent state
 * given whatever came back" is an assertion about our code.
 */

test.describe('landing page', () => {
  test('shows the search box and focuses it', async ({ page }) => {
    await page.goto('/');

    const search = searchBox(page);
    await expect(search).toBeVisible();
    // Autofocused: a search-first page whose input needs a click wastes the
    // first interaction of every visit.
    await expect(search).toBeFocused();
  });

  test('states its privacy claims and links to the limits', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'No search history' })).toBeVisible();
    await expect(
      page.getByRole('link', { name: /what its operator can still technically see/i }),
    ).toBeVisible();
  });

  test('loads nothing from a third-party host', async ({ page }) => {
    // The strongest privacy property the interface can have, and the easiest
    // to lose by accident - one webfont import would break it.
    const external: string[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (!['127.0.0.1', 'localhost'].includes(url.hostname)) external.push(request.url());
    });

    await page.goto('/', { waitUntil: 'networkidle' });
    expect(external, `unexpected third-party requests: ${external.join(', ')}`).toHaveLength(0);
  });
});

test.describe('searching', () => {
  test('submitting a query navigates and renders a result state', async ({ page }) => {
    await page.goto('/');
    await searchBox(page).fill('open source search');
    await page.getByRole('button', { name: 'Search', exact: true }).click();

    // %20 rather than +: encodeURIComponent produces the former, and both are
    // valid in a query string. Matching the separator loosely keeps this test
    // about navigation rather than about encoding style.
    await expect(page).toHaveURL(/\/search\?q=open[+%20]{1,3}source[+%20]{1,3}search/);

    // Either results or a coherent empty state - both are correct outcomes on
    // an instance whose upstreams may all be blocked right now.
    const results = page.locator('article');
    const empty = page.getByText(/No results for/);
    await expect(results.first().or(empty)).toBeVisible({ timeout: 60_000 });
  });

  test('a deep link works on first load', async ({ page }) => {
    // Proves the SPA fallback: without it a shared search URL 404s.
    await page.goto('/search?q=distributed+systems');
    await expect(searchBox(page)).toHaveValue('distributed systems');
  });

  test('reports how many results came back, without inventing a total', async ({ page }) => {
    await page.goto('/search?q=privacy');
    const meta = page.getByText(/results on this page/);
    await expect(meta).toBeVisible({ timeout: 60_000 });

    // The upstream reports no total, so the interface must not imply one.
    // "about 4,300,000 results" here would be fabricated.
    await expect(page.getByText(/about [\d,]+ results/i)).toHaveCount(0);
  });

  test('names the engines that failed rather than hiding degradation', async ({ page }) => {
    await page.goto('/search?q=climate+change');
    await expect(
      page.locator('article').first().or(page.getByText(/No results for/)),
    ).toBeVisible({ timeout: 60_000 });

    const notice = page.getByText(/did not respond/);
    // Degradation is the normal case here, but not guaranteed on any given
    // run, so this asserts the disclosure is honest WHEN it appears.
    if (await notice.isVisible().catch(() => false)) {
      await notice.click();
      await expect(page.getByText(/rate-limit and CAPTCHA self-hosted instances/i)).toBeVisible();
    }
  });

  test('switching category re-runs the search and resets the page', async ({ page }) => {
    await page.goto('/search?q=mountains&page=3');
    await page.getByRole('tab', { name: 'Images' }).click();

    await expect(page).toHaveURL(/category=images/);
    // Staying on page 3 of a different result set shows an arbitrary slice of
    // something new.
    await expect(page).not.toHaveURL(/page=3/);
  });

  test('result links carry rel=noreferrer', async ({ page }) => {
    await page.goto('/search?q=wikipedia');
    const link = page.locator('article a[href^="http"]').first();

    if (await link.isVisible({ timeout: 60_000 }).catch(() => false)) {
      // Stops the destination learning that Veilix sent the visitor, and
      // closes reverse-tabnabbing.
      await expect(link).toHaveAttribute('rel', 'noreferrer');
    }
  });

  test('image results are served through the proxy, never a third-party host', async ({
    page,
  }) => {
    await page.goto('/search?q=aurora&category=images');
    const image = page.locator('article img, a img').first();

    if (await image.isVisible({ timeout: 60_000 }).catch(() => false)) {
      const src = await image.getAttribute('src');
      // A src starting with http means the browser connects directly to the
      // image host and hands it the viewer's address.
      expect(src?.startsWith('/img?')).toBe(true);
    }
  });
});

test.describe('keyboard and accessibility', () => {
  test('slash focuses the search box from anywhere', async ({ page }) => {
    await page.goto('/about');
    await page.keyboard.press('/');
    await expect(searchBox(page)).toBeFocused();
  });

  test('typing a slash inside the box types it rather than re-triggering', async ({
    page,
  }) => {
    // The classic shortcut bug that makes keyboard navigation feel hostile.
    await page.goto('/');
    const search = searchBox(page);
    await search.click();
    await search.fill('and/or');
    await expect(search).toHaveValue('and/or');
  });

  test('the skip link is the first tab stop on a page without autofocus', async ({
    page,
  }) => {
    // Deliberately NOT the home page. Home autofocuses the search box, so
    // forward-tabbing starts after it and never reaches the skip link that
    // precedes it in the DOM.
    //
    // That is a real tension between two accessibility affordances, and the
    // resolution is that on Home the search box IS the main content, so
    // skipping to it is what the skip link would have done anyway. On content
    // pages, where the link actually earns its place, it comes first.
    await page.goto('/about');
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  });

  test('the category tablist is one tab stop with arrow-key navigation', async ({ page }) => {
    await page.goto('/search?q=test');
    const web = page.getByRole('tab', { name: 'Web' });
    await web.focus();
    await page.keyboard.press('ArrowRight');

    // Nine separate tab stops would make reaching the results below tedious.
    await expect(page).toHaveURL(/category=images/);
  });

  test('results are announced to assistive technology', async ({ page }) => {
    await page.goto('/search?q=privacy');
    // Without a live region a keyboard user submits a search and hears
    // nothing: the page changed but focus did not move.
    await expect(page.locator('[aria-live="polite"]')).toBeAttached();
  });

  test('every page has exactly one h1', async ({ page }) => {
    for (const path of ['/', '/privacy', '/about', '/settings', '/status', '/shortcuts']) {
      await page.goto(path);
      await expect(page.locator('h1'), `on ${path}`).toHaveCount(1);
    }
  });
});

test.describe('settings', () => {
  test('a preference persists across a reload and is never sent to the server', async ({
    page,
  }) => {
    const settingsRequests: string[] = [];
    page.on('request', (r) => {
      if (r.url().includes('/api/') && r.method() !== 'GET') settingsRequests.push(r.url());
    });

    await page.goto('/settings');
    // The real checkbox is sr-only - the visible switch is a styled span - so
    // it is operated through its label, exactly as a user does.
    await page.getByText('Open results in a new tab').click();
    await page.reload();

    await expect(page.getByLabel('Open results in a new tab')).toBeChecked();
    // Preferences live in localStorage. A server-side preference would need an
    // identifier to key it by - exactly the tracking token this product refuses
    // to create.
    expect(settingsRequests).toHaveLength(0);
  });

  test('resetting clears stored preferences', async ({ page }) => {
    await page.goto('/settings');
    await page.getByText('Open results in a new tab').click();
    await expect(page.getByLabel('Open results in a new tab')).toBeChecked();
    await page.getByRole('button', { name: /Reset settings/ }).click();
    await expect(page.getByLabel('Open results in a new tab')).not.toBeChecked();
  });
});

test.describe('honesty', () => {
  test('the privacy page states what the operator can still see', async ({ page }) => {
    await page.goto('/privacy');
    await expect(page.getByText(/does not claim to make you anonymous/i)).toBeVisible();
    await expect(page.getByText(/terminates HTTPS/i)).toBeVisible();
  });

  test('the status page does not imply engine health it has not measured', async ({
    page,
  }) => {
    await page.goto('/status');
    await expect(page.getByText(/This lists what is configured/i)).toBeVisible();
  });
});

test.describe('operations page', () => {
  test('requires credentials and shows no user data', async ({ page }) => {
    await page.goto('/admin');
    await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
    await expect(page.getByText(/No user data is available here/i)).toBeVisible();
  });

  test('rejects wrong credentials', async ({ page }) => {
    await page.goto('/admin');
    await page.getByLabel('Password').fill('definitely-not-the-password');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('alert')).toContainText(/rejected/i);
  });
});
