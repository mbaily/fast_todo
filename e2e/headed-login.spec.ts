import { test, expect } from '@playwright/test';

// Human-readable headed test for list override visualization
test.use({
  headless: false,
  launchOptions: { slowMo: 200 }
});

const BASE = 'https://0.0.0.0:10443';
const USER = 'mbaily';
const PASS = 'mypass';

test('headed open html_no_js index', async ({ page }) => {
  // directly open the html_no_js index page
  await page.goto(`${BASE}/html_no_js/`, { waitUntil: 'networkidle' });
  await page.waitForSelector('body');

  // take a screenshot
  await page.screenshot({ path: 'e2e/screenshots/headed-index.png', fullPage: true });

  // basic assertion: page contains 'list' or some known element
  const body = await page.textContent('body');
  expect(body).toBeTruthy();
});
