import { test, expect } from '@playwright/test';

const BASE = 'https://0.0.0.0:10443';

async function login(page){
  await page.goto(`${BASE}/html_no_js/login`, { waitUntil: 'networkidle' });
  await page.waitForSelector('#username', { timeout: 10000 });
  await page.fill('#username', 'mbaily');
  await page.fill('#password', 'mypass');
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle', timeout: 15000 }),
    page.click('button[type=submit]')
  ]);
}

// Ensure tz cookie is set so server anchors in Melbourne time
async function ensureTz(page){
  await page.context().addCookies([{ name: 'tz', value: 'Australia/Melbourne', url: BASE }]);
}

test('todo 468 appears in index calendar section', async ({ page }) => {
  await page.context().tracing.start({ screenshots: true, snapshots: true });
  await login(page);
  await ensureTz(page);
  await page.goto(`${BASE}/html_no_js/`, { waitUntil: 'networkidle' });
  // Expect calendar summary section to be present
  await page.waitForSelector('section.calendar-summary ul li', { timeout: 10000 });
  // Look for a link to todo 468 or text containing Paula every Tuesday
  const selector = 'section.calendar-summary a[href^="/html_no_js/todos/468"], section.calendar-summary li:has-text("every Tuesday")';
  const found = await page.locator(selector).first();
  await expect(found).toBeVisible({ timeout: 10000 });
  await page.context().tracing.stop({ path: 'playwright-trace-468.zip' });
});
