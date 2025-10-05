import { test, expect } from '@playwright/test';

const BASE = 'https://0.0.0.0:10443';

test('dates appear in hide-icons list view SSR', async ({ page }) => {
  test.use({ headless: false, ignoreHTTPSErrors: true });

  // Login
  await page.goto(`${BASE}/html_no_js/login`, { waitUntil: 'networkidle' });
  await page.waitForSelector('#username', { timeout: 8000 });
  await page.fill('#username', 'mbaily');
  await page.fill('#password', 'mypass');
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle', timeout: 10000 }),
    page.click('button[type=submit]')
  ]);
  // Ensure logged in
  await page.waitForSelector('button[aria-label="Logout"]', { timeout: 10000 });

  // Go to the specific list with hide icons enabled
  await page.goto(`${BASE}/html_no_js/lists/230`, { waitUntil: 'networkidle' });

  // Check that the list has hide-icons mode
  const ul = page.locator('ul.todos-list');
  await expect(ul).toHaveClass(/hide-icons/);

  // Check that there are date strings after the 🔎
  // Since it's SSR, the dates should be in the HTML
  const content = await page.content();
  // Look for patterns like 🔎1/2 or 🔎2/1
  const datePattern = /🔎\d{1,2}\/\d{1,2}/g;
  const matches = content.match(datePattern);
  expect(matches).toBeTruthy();
  expect(matches!.length).toBeGreaterThan(0);
});
