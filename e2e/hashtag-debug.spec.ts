import { test, expect } from '@playwright/test';

test.use({ ignoreHTTPSErrors: true });

test('login and show hashtags debug', async ({ page }) => {
  try {
    const loginUrl = 'https://0.0.0.0:10443/html_no_js/login';
    const tagsUrl = 'https://0.0.0.0:10443/html_no_js/hashtags?debug=1';
    // navigate to login page
    await page.goto(loginUrl, { waitUntil: 'networkidle', timeout: 15000 });
    await page.fill('input[name="username"]', 'mbaily');
    await page.fill('input[name="password"]', 'mypass');
    // submit form and wait a short while for cookies to be set
    await Promise.all([
      page.click('button[type="submit"]'),
      page.waitForTimeout(1200)
    ]);

    // navigate to hashtags debug page
    const resp = await page.goto(tagsUrl, { waitUntil: 'networkidle', timeout: 15000 });
    console.log('INFO: hashtags page response status=' + (resp && resp.status()));

    // Try to read debug line; fallback to dumping a small portion of page HTML
    const dbgElem = page.locator('text=Debug:').first();
    let dbg = '';
    try {
      await dbgElem.waitFor({ timeout: 5000 });
      dbg = (await dbgElem.textContent()) || '';
      console.log('DEBUG_LINE:' + dbg.trim());
    } catch (e) {
      console.log('WARN: debug element not found, dumping page snippet');
      const content = await page.content();
      console.log('PAGE_SNIPPET:' + content.slice(0, 2000));
      // still assert so test fails visibly if no debug
      expect(dbg).toBeTruthy();
    }
  } catch (err) {
    console.log('ERROR during test:', String(err));
    throw err;
  }
});
