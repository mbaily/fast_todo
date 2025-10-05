import { test, expect, chromium } from '@playwright/test';

test('remote: unchecking hide-done shows completed sublists on todo and list pages', async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  try {
    const contexts = browser.contexts();
    const context = contexts.length ? contexts[0] : await browser.newContext({ ignoreHTTPSErrors: true });

    async function testPage(url: string, checkboxSelector: string) {
      // Try to reuse any existing page in the remote browser that has our origin so
      // we pick up the logged-in cookies and state. Fall back to creating a page
      // in the current context.
      // Always create a new page in the existing remote context so we have a fresh tab
      // but preserve cookies/session from the remote browser.
      let page = await context.newPage();
      // collect console messages
      const logs: string[] = [];
      page.on('console', msg => {
        try { logs.push(`${msg.type()}: ${msg.text()}`); } catch(_){}
      });
      await page.bringToFront();
      await page.goto(url, { waitUntil: 'networkidle' });
      await page.waitForSelector('section[aria-label="Sublists"]', { timeout: 5000 });

      // Ensure checkbox exists and uncheck it programmatically then call helper
      await page.waitForSelector(checkboxSelector, { timeout: 3000 });
      await page.evaluate((sel) => {
        const el = document.querySelector(sel) as HTMLInputElement | null;
        if (!el) return;
        el.checked = false;
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }, checkboxSelector);
      // call helper to ensure class toggling and inline-style clearing
      await page.evaluate(() => { try { if ((window as any).__ft_applySublistsHideDone) (window as any).__ft_applySublistsHideDone(false); } catch(e) {} });
      await page.waitForTimeout(300);

      // Compute expected counts from DOM attributes
      const counts = await page.evaluate(() => {
        const items = Array.from(document.querySelectorAll('section[aria-label="Sublists"] ul.lists-list li.list-item'));
        const total = items.length;
        const uncompleted = items.filter(i => (i.getAttribute('data-completed')||'').toLowerCase() !== 'true').length;
        return { total: total, uncompleted: uncompleted };
      });

      // Verify visible items equal total after unchecking
      const finalWhenUnchecked = await page.waitForFunction(() => {
        const secs = Array.from(document.querySelectorAll('section[aria-label="Sublists"]'));
        return secs.every(sec => {
          const items = Array.from(sec.querySelectorAll('ul.lists-list li.list-item'));
          return items.length === items.filter(i => window.getComputedStyle(i).display !== 'none').length;
        });
      }, { timeout: 3000 }).catch(async () => {
        // collect diagnostics
        const diag = await page.evaluate(() => {
          const secs = Array.from(document.querySelectorAll('section[aria-label="Sublists"]'));
          return secs.map(sec => ({ classList: Array.from(sec.classList||[]), items: Array.from(sec.querySelectorAll('ul.lists-list li.list-item')).map(i => ({ text: (i.textContent||'').trim().slice(0,80), dataCompleted: i.getAttribute('data-completed'), inlineStyle: i.getAttribute('style')||'', computedDisplay: window.getComputedStyle(i).display })) }));
        });
        console.error('DIAGNOSTICS for', url);
        console.error('Console logs:', logs.slice(-20));
        console.error('Diag:', JSON.stringify(diag, null, 2));
        throw new Error('Timed out waiting for sublists to become fully visible after unchecking.');
      });
      const finalCount = await page.evaluate(() => {
        const items = Array.from(document.querySelectorAll('section[aria-label="Sublists"] ul.lists-list li.list-item'));
        return items.filter(i => window.getComputedStyle(i).display !== 'none').length;
      });
      // finalCount should be either the uncompleted count (if checked) or total (if unchecked)
      const countsAfter = await page.evaluate(() => {
        const items = Array.from(document.querySelectorAll('section[aria-label="Sublists"] ul.lists-list li.list-item'));
        const total = items.length;
        const uncompleted = items.filter(i => (i.getAttribute('data-completed')||'').toLowerCase() !== 'true').length;
        return { total: total, uncompleted: uncompleted };
      });
      expect(finalCount === countsAfter.uncompleted || finalCount === countsAfter.total).toBe(true);
      // Close the page only if we created it (leave original pages alone)
    }

    await testPage('https://127.0.0.1:10443/html_no_js/todos/571', '[data-todo-sublists-hide-done-toggle]');
    await testPage('https://127.0.0.1:10443/html_no_js/lists/490', '[data-sublists-hide-done-toggle]');
  } finally {
    try { await browser.close(); } catch(_) { /* ignore */ }
  }
});
