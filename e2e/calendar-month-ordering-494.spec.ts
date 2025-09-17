import { test, expect, Page } from '@playwright/test';

const BASE = 'https://0.0.0.0:10443';

async function login(page: Page){
  await page.goto(`${BASE}/html_no_js/login`, { waitUntil: 'networkidle' });
  await page.waitForSelector('#username', { timeout: 10000 });
  await page.fill('#username', 'mbaily');
  await page.fill('#password', 'mypass');
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle', timeout: 15000 }),
    page.click('button[type=submit]')
  ]);
}

async function ensureTz(page: Page){
  await page.context().addCookies([{ name: 'tz', value: 'Australia/Melbourne', url: BASE }]);
}

function isSortedLexAsc(arr: string[]) {
  for (let i = 1; i < arr.length; i++) {
    if (arr[i-1] > arr[i]) return false;
  }
  return true;
}

test('calendar month view is chronologically ordered (Nov 2025)', async ({ page }) => {
  await page.context().tracing.start({ screenshots: true, snapshots: true });
  await login(page);
  await ensureTz(page);
  await page.goto(`${BASE}/html_no_js/calendar?year=2025&month=11`, { waitUntil: 'networkidle' });

  // Wait for occurrences to load
  const list = page.locator('.todos-list.full-bleed li.todo');
  await expect(list.first()).toBeVisible({ timeout: 20000 });

  // Extract the sequence of displayed occurrence dates (ISO date or ISO datetime)
  const metaTexts = await page.locator('.todos-list.full-bleed li.todo .meta').allTextContents();
  expect(metaTexts.length).toBeGreaterThan(0);

  // Verify chronological (lexicographic) order
  expect(isSortedLexAsc(metaTexts)).toBeTruthy();

  // If todo 494 exists in the page, verify it uses the 2025-11-17 date
  const target = page.locator('a[href="/html_no_js/todos/494"]');
  if (await target.count() > 0) {
    const li = target.first().locator('xpath=ancestor::li[contains(@class, "todo")]');
    const meta = await li.locator('.meta').first().textContent();
    expect(meta).toContain('2025-11-17');
  }

  await page.context().tracing.stop({ path: 'playwright-trace-month-ordering-494.zip' });
});
