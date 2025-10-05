import { test, expect, chromium } from '@playwright/test';

// This test connects to an already-running Chrome instance exposing the
// DevTools protocol on port 9222. It then navigates to the todo and list
// pages and toggles the "Hide Done" checkbox for sublists, asserting that
// the DOM shows the expected number of visible sublists after unchecking.

const BASE = process.env.FT_BASE_URL || 'https://127.0.0.1:10443';
const CDP = process.env.FT_CDP_URL || 'http://127.0.0.1:9222';

async function visibleSublistsCount(page: any) {
  return await page.evaluate(() => {
    try {
      var sect = document.querySelector('section[aria-label="Sublists"]');
      if (!sect) return 0;
      var lis = Array.from(sect.querySelectorAll('ul.lists-list li.list-item'));
      var visible = lis.filter(function(li){
        // Use offsetParent to detect visible elements (works for display:none)
        return !!(li.offsetParent || (window.getComputedStyle(li).display !== 'none'));
      });
      return visible.length;
    } catch (e) { return 0; }
  });
}

async function findAndToggleCheckbox(page: any) {
  // Find either the todo-specific or list-specific checkbox attribute
  const sel = 'input[data-todo-sublists-hide-done-toggle], input[data-sublists-hide-done-toggle]';
  const cb = await page.locator(sel).first();
  if (!await cb.count()) return false;
  const isChecked = await cb.evaluate((el: HTMLInputElement) => el.checked);
  // If it's checked (hiding done), click to uncheck
  if (isChecked) {
    await cb.click();
    return true;
  }
  return false;
}

test.describe('CDP-connected Chrome - sublists hide done', () => {
  test('unticking hide-done shows completed sublists on todo and list pages', async () => {
    // Connect to an existing Chrome instance via CDP
    const browser = await chromium.connectOverCDP(CDP);
    // Create a new context so we don't interfere with other tabs
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await context.newPage();

    // Pages to test
    const pages = [
      `${BASE}/html_no_js/todos/571`,
      `${BASE}/html_no_js/lists/490`,
    ];

    // Prefer an explicit CDP target WS if provided (this will attach directly
    // to that tab). Otherwise search existing pages in the connected browser
    // for a page whose URL contains the todo/list path and reuse it.
    const explicitTargetWs = process.env.FT_CDP_TARGET_WS || '';
    let usedContext = null;
    let usedPage = null;
    let createdContext = false;

    if (explicitTargetWs) {
      // Connect directly to the specific target WebSocket (attaches to that tab)
      const targetBrowser = await chromium.connectOverCDP(explicitTargetWs);
      const ctxs = targetBrowser.contexts();
      usedContext = ctxs && ctxs.length ? ctxs[0] : null;
      if (usedContext) usedPage = (usedContext.pages() && usedContext.pages().length) ? usedContext.pages()[0] : null;
      // If attach didn't give a page, create one in that context
      if (!usedPage && usedContext) usedPage = await usedContext.newPage();
      // Do not close targetBrowser here (it's the user's browser connection)
    } else {
      // Enumerate pages from the initially-connected browser and look for a matching URL
      const ctxs = browser.contexts();
      for (const c of ctxs) {
        const pgs = c.pages();
        for (const p of pgs) {
          const u = p.url();
          if (!u) continue;
          if (u.includes('/html_no_js/todos/571') || u.includes('/html_no_js/lists/490')) {
            usedContext = c; usedPage = p; break;
          }
        }
        if (usedPage) break;
      }
      // If we didn't find an existing page, create a temporary context/page (with relaxed certs)
      if (!usedPage) {
        usedContext = await browser.newContext({ ignoreHTTPSErrors: true });
        createdContext = true;
        usedPage = await usedContext.newPage();
      }
    }

    // Use the discovered page for navigation so we operate in the user's open tab
    for (const url of pages) {
      console.log('Visiting', url);
      // If the usedPage already is on the URL we need, avoid re-navigating (which may trigger cert prompts)
      try {
        if (usedPage.url() && usedPage.url().includes(url)) {
          console.log('Reusing existing page at', usedPage.url());
        } else {
          await usedPage.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
        }
      } catch (e) {
        // If navigation fails (e.g., due to cert), log and rethrow
        console.log('Navigation failed for', url, 'error:', e.message || e);
        throw e;
      }

      // Wait for the sublists section to be present (give more time on slow env)
      try {
        await page.waitForSelector('section[aria-label="Sublists"]', { timeout: 20000 });
      } catch (err) {
        const content = await page.content();
        console.log('Page HTML snippet (first 2000 chars):\n', content.slice(0, 2000));
        throw err;
      }

      // Initial visible count (when hide-done is checked server-side this may be 1)
      const before = await visibleSublistsCount(page);
      console.log('Visible before toggle:', before);

      // Toggle the checkbox if needed
      const toggled = await findAndToggleCheckbox(page);
      if (!toggled) {
        // If it wasn't checked, still try toggling twice to exercise the change
        const cb = await page.locator('input[data-todo-sublists-hide-done-toggle], input[data-sublists-hide-done-toggle]').first();
        if (await cb.count()) {
          await cb.click(); // toggle on
          await cb.click(); // toggle off again
        }
      }

      // Wait for DOM to update - watch for visible count to become 3
      let ok = false;
      for (let i = 0; i < 10; i++) {
        const now = await visibleSublistsCount(usedPage);
        console.log('Visible after toggle attempt', i, now);
        if (now === 3) { ok = true; break; }
        await page.waitForTimeout(300);
      }

      expect(ok, `expected 3 visible sublists at ${url}`).toBeTruthy();
    }

    // Only close the context we created — do NOT close the remote browser the
    // user is actively using.
    try {
      if (createdContext && usedContext) await usedContext.close();
    } catch (e) {}
  });
});
