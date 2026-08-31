import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

/**
 * Automated accessibility checks with axe-core.
 *
 * What this does and does not establish: axe catches roughly a third of WCAG
 * issues, all of them machine-detectable. A clean run means no contrast
 * failure, missing label, bad ARIA reference or broken heading order that a
 * tool can see. It is not a conformance claim, and this repository does not
 * make one - nothing here substitutes for using the site with a screen reader.
 *
 * Scoped to wcag2a, wcag2aa and wcag21aa so a failure maps to a specific
 * success criterion instead of a vendor best-practice rule.
 */

const TAGS = ['wcag2a', 'wcag2aa', 'wcag21aa'];

const PAGES: [name: string, path: string][] = [
  ['home', '/'],
  ['results', '/search?q=accessibility+scan'],
  ['settings', '/settings'],
  ['privacy', '/privacy'],
  ['about', '/about'],
  ['status', '/status'],
  ['shortcuts', '/shortcuts'],
  ['admin sign-in', '/admin'],
  ['not found', '/no-such-page'],
];

for (const [name, path] of PAGES) {
  test(`${name} has no detectable WCAG A/AA violations`, async ({ page }) => {
    await page.goto(path);
    await page.waitForLoadState('networkidle');

    const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();

    if (results.violations.length > 0) {
      const summary = results.violations
        .map(
          (v) =>
            `${v.id} (${v.impact}): ${v.help}\n    ${v.nodes
              .slice(0, 3)
              .map((n) => n.target.join(' '))
              .join('\n    ')}`,
        )
        .join('\n  ');
      throw new Error(`axe found ${results.violations.length} violation(s):\n  ${summary}`);
    }

    expect(results.violations).toEqual([]);
  });
}

test('dark theme has no detectable contrast failures', async ({ page }) => {
  // Contrast is theme-dependent, and the dark palette is a separate set of
  // tokens rather than an inversion, so it needs its own pass.
  await page.goto('/');
  await page.evaluate(() => {
    localStorage.setItem('veilix.theme', 'dark');
    document.documentElement.classList.add('dark');
  });
  await page.goto('/search?q=dark+theme+contrast');
  await page.waitForLoadState('networkidle');

  const results = await new AxeBuilder({ page })
    .withTags(TAGS)
    .include('body')
    .analyze();

  const contrast = results.violations.filter((v) => v.id === 'color-contrast');
  expect(contrast, JSON.stringify(contrast.map((v) => v.nodes.map((n) => n.target)))).toEqual([]);
});
