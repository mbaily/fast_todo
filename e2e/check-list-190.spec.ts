test.use({ headless: false, launchOptions: { slowMo: 200 } });
import { test, expect } from '@playwright/test';

// Headed check for list 190's sublists override display
test.use({ headless: false, launchOptions: { slowMo: 200 }, ignoreHTTPSErrors: true });
const BASE = 'https://0.0.0.0:10443';

function colorOf(el: HTMLElement | null) {
  if (!el) return null;
  return window.getComputedStyle(el).color;
}

test('check list 190 sublists for override badge', async ({ page }) => {
  await page.context().tracing.start({ screenshots: true, snapshots: true });
  // try common paths used by app
  const paths = [
    '/html_no_js/list.html?list_id=190',
    '/html_no_js/list/190',
    '/html_no_js/?list_id=190',
    '/html_no_js/'
  ];

  let opened = false;
  // login first
  try {
    // perform login via the real form submit (no-JS page)
    await page.goto(`${BASE}/html_no_js/login`, { waitUntil: 'networkidle' });
    await page.waitForSelector('#username', { timeout: 8000 });
    await page.fill('#username', 'mbaily');
    await page.fill('#password', 'mypass');
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'networkidle', timeout: 10000 }),
      page.click('button[type=submit]')
    ]);
    // after navigation, toolbar should show Logout
    await page.waitForSelector('button[aria-label="Logout"]', { timeout: 10000 });
  } catch (err) {
    // capture diagnostics
    await page.screenshot({ path: 'e2e/screenshots/list-190-login-fail.png', fullPage: true }).catch(()=>{});
    const html = await page.content().catch(()=>'<no-content>');
    const fs = require('fs');
    fs.writeFileSync('e2e/screenshots/list-190-login-fail.html', html);
    throw err;
  }

  // go directly to the index view with list_id=190 (server renders the lists there)
  await page.goto(`${BASE}/html_no_js/?list_id=190`, { waitUntil: 'networkidle' });
  // wait shortly for server-side rendered content
  await page.waitForSelector('.lists-list, .sublists, .priority-override', { timeout: 8000 }).catch(()=>{});
  
  if (!opened) {
    // still continue with /html_no_js/
    await page.goto(`${BASE}/html_no_js/`, { waitUntil: 'networkidle' });
  }

  // wait for the ⑦ override to appear and stop as soon as we see it
  try {
    await page.waitForSelector(':scope .priority-override .priority-circle:text("⑦"), .priority-override:has-text("⑦")', { timeout: 15000 });
  } catch(e) {
    // not found within timeout - continue to gather what we have
  }

  // take a screenshot
  await page.screenshot({ path: 'e2e/screenshots/list-190.png', fullPage: true });

  // find sublist items and report their override badges
  const reports = await page.evaluate(() => {
    const rows: any[] = [];
    // index markup: lists are under .lists-list with .list-item rows
    const subEls = document.querySelectorAll('.lists-list .list-item');
    if (!subEls || subEls.length === 0) {
      // fallback: find any elements that look like list rows or links
      const links = Array.from(document.querySelectorAll('a[href*="list_id="]'));
      for (const a of links) {
        const overrideEl = a.querySelector('.priority-override .priority-circle, .priority-override');
        rows.push({ title: a.textContent?.trim(), overrideText: overrideEl ? overrideEl.textContent?.trim() : null });
      }
      return rows;
    }
    for (const el of Array.from(subEls)) {
      const titleEl = el.querySelector('.list-main .list-title') || el.querySelector('.list-title') || el.querySelector('a');
      const title = titleEl ? titleEl.textContent?.trim() : null;
      const overrideEl = el.querySelector('.priority-override .priority-circle, .priority-override');
      const overrideText = overrideEl ? overrideEl.textContent?.trim() : null;
      let color = null;
      if (overrideEl) {
        color = window.getComputedStyle(overrideEl).color;
      }
      rows.push({ title, overrideText, color });
    }
    return rows;
  });

  await page.context().tracing.stop({ path: 'playwright-trace-list-190.zip' });

  // save DOM report
  const fs = require('fs');
  fs.writeFileSync('e2e/screenshots/list-190-dom.json', JSON.stringify(reports, null, 2));

  console.log('DOM report:', reports);

  // assert we have at least one sublist report
  expect(reports.length).toBeGreaterThan(0);
});
