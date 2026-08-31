import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end tests run against the REAL stack.
 *
 * No mocked API, no stubbed SearXNG. These tests exercise the deployed
 * containers, which is the only way to catch the class of bug this project has
 * repeatedly produced: configuration that looks right, passes unit tests, and
 * does nothing — a header the proxy appends instead of replacing, an env var
 * the container never receives, an OTel extra missing from the image.
 *
 * The cost is that some assertions depend on upstream search engines, which
 * fail constantly by design. Tests are written to assert *the interface handles
 * whatever came back*, never that a particular result appeared.
 */
export default defineConfig({
  testDir: './specs',
  // Real searches take seconds and upstreams are slow, so the defaults are
  // generous. A tight timeout here produces flakes that look like bugs.
  timeout: 90_000,
  expect: { timeout: 15_000 },

  // Serial. The stack is a single small instance with rate limiting enabled;
  // parallel workers would trip the limiter and fail each other's tests.
  fullyParallel: false,
  workers: 1,

  // Never retry locally: a test that passes on retry is a flake, and hiding it
  // is how a real intermittent bug stays hidden. CI retries once, because
  // upstream engines genuinely are unreliable.
  retries: process.env.CI ? 1 : 0,
  forbidOnly: !!process.env.CI,

  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never' }]]
    : [['list']],

  use: {
    baseURL: process.env.VEILIX_E2E_URL ?? 'http://127.0.0.1:8088',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    // Deliberately not ignoring HTTPS errors: if a deployment serves a bad
    // certificate, these tests should say so rather than paper over it.
    ignoreHTTPSErrors: false,
  },

  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    // Mobile matters for a search product; the layout is mobile-first and
    // this is what proves it rather than a media query that looks right.
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
  ],
});
