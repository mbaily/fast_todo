import { test, expect, chromium } from '@playwright/test';

// This test connects to an already-running Chrome instance on remote debugging port 9222
test.describe('Sublists Hide Done (remote Chrome)', () => {
  test('list and todo sublists hide-done toggle', async () => {
    // Connect to existing browser instance (retry a few times if not ready)
    async function connectWithRetry(urls: string[], attempts = 8, delayMs = 500) {
      for (let i = 0; i < attempts; i++) {
        for (const u of urls) {
          try { return await chromium.connectOverCDP(u); } catch (e) { /* try next */ }
        }
        await new Promise(r => setTimeout(r, delayMs));
      }
      throw new Error('connect retries exhausted for urls: ' + urls.join(','));
    }
    const browser = await connectWithRetry(['http://127.0.0.1:9222','http://localhost:9222','http://0.0.0.0:9222']);
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await context.newPage();

    // Test URLs provided by user
  const todoUrl = 'https://0.0.0.0:10443/html_no_js/todos/571';
  const listUrl = 'https://0.0.0.0:10443/html_no_js/lists/490';

    // Helper: count visible sublists by checking computed style and aria-hidden/class
    async function countVisibleSublistsOnPage() {
      return await page.evaluate(() => {
        const items = Array.from(document.querySelectorAll('li.list-item')) as HTMLElement[];
        return items.filter(i => {
          if (i.getAttribute('aria-hidden') === 'true') return false;
          const style = window.getComputedStyle(i);
          if (style && style.display === 'none') return false;
          const rect = i.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        }).length;
      });
    }

    // --- Test list page behavior ---
    await page.goto(listUrl);
    await page.waitForLoadState('domcontentloaded');
    // Ensure debug attributes present
    const debugCb = await page.$('[data-sublists-hide-done-toggle]');
    expect(debugCb).not.toBeNull();

    // With hide done initially set on server for list 490, expect 1 visible sublist
    // But to be robust, toggle the control
    const checkbox = await page.$('[data-sublists-hide-done-toggle]');
    expect(checkbox).not.toBeNull();
    // If checkbox is checked, expect 1 visible; else 3 visible. We'll assert both transitions.
    const initialChecked = await page.evaluate((el) => (el as HTMLInputElement).checked, checkbox);
    if (!initialChecked) {
      // ensure toggling on results in 1
      await checkbox.click();
      await page.waitForFunction(() => {
        const items = Array.from(document.querySelectorAll('li.list-item')) as HTMLElement[];
        return items.filter(i => i.getAttribute('aria-hidden') !== 'true' && window.getComputedStyle(i).display !== 'none' && i.getBoundingClientRect().width>0).length === 1;
      }, { timeout: 3000 });
      const visibleAfterOn = await countVisibleSublistsOnPage();
      expect(visibleAfterOn).toBe(1);
      // toggle off
      await checkbox.click();
      await page.waitForFunction(() => {
        const items = Array.from(document.querySelectorAll('li.list-item')) as HTMLElement[];
        return items.filter(i => i.getAttribute('aria-hidden') !== 'true' && window.getComputedStyle(i).display !== 'none' && i.getBoundingClientRect().width>0).length === 3;
      }, { timeout: 3000 });
      const visibleAfterOff = await countVisibleSublistsOnPage();
      expect(visibleAfterOff).toBe(3);
    } else {
      // initial checked -> should show 1. Then toggle off -> 3
      const visibleInitial = await countVisibleSublistsOnPage();
      expect(visibleInitial).toBe(1);
      // toggle off
      await checkbox.click();
      await page.waitForFunction(() => {
        const items = Array.from(document.querySelectorAll('li.list-item')) as HTMLElement[];
        return items.filter(i => i.getAttribute('aria-hidden') !== 'true' && window.getComputedStyle(i).display !== 'none' && i.getBoundingClientRect().width>0).length === 3;
      }, { timeout: 3000 });
      const visibleAfterOff = await countVisibleSublistsOnPage();
      expect(visibleAfterOff).toBe(3);
      // toggle on again
      await checkbox.click();
      await page.waitForFunction(() => {
        const items = Array.from(document.querySelectorAll('li.list-item')) as HTMLElement[];
        return items.filter(i => i.getAttribute('aria-hidden') !== 'true' && window.getComputedStyle(i).display !== 'none' && i.getBoundingClientRect().width>0).length === 1;
      }, { timeout: 3000 });
      const visibleAfterOn = await countVisibleSublistsOnPage();
      expect(visibleAfterOn).toBe(1);
    }

    // --- Test todo page behavior ---
    await page.goto(todoUrl);
    await page.waitForLoadState('domcontentloaded');
    const todoCb = await page.$('[data-todo-sublists-hide-done-toggle]');
    expect(todoCb).not.toBeNull();
    const todoCheckbox = await page.$('[data-todo-sublists-hide-done-toggle]');
    expect(todoCheckbox).not.toBeNull();

    const todoInitialChecked = await page.evaluate((el) => (el as HTMLInputElement).checked, todoCheckbox);
    if (!todoInitialChecked) {
      await todoCheckbox.click();
      await page.waitForFunction(() => {
        const items = Array.from(document.querySelectorAll('li.list-item')) as HTMLElement[];
        return items.filter(i => i.getAttribute('aria-hidden') !== 'true' && window.getComputedStyle(i).display !== 'none' && i.getBoundingClientRect().width>0).length === 1;
      }, { timeout: 3000 });
      const vis = await countVisibleSublistsOnPage();
      expect(vis).toBe(1);
      await todoCheckbox.click();
      await page.waitForFunction(() => {
        const items = Array.from(document.querySelectorAll('li.list-item')) as HTMLElement[];
        return items.filter(i => i.getAttribute('aria-hidden') !== 'true' && window.getComputedStyle(i).display !== 'none' && i.getBoundingClientRect().width>0).length === 3;
      }, { timeout: 3000 });
      const vis2 = await countVisibleSublistsOnPage();
      expect(vis2).toBe(3);
    } else {
      const vis0 = await countVisibleSublistsOnPage();
      expect(vis0).toBe(1);
      await todoCheckbox.click();
      await page.waitForFunction(() => {
        const items = Array.from(document.querySelectorAll('li.list-item')) as HTMLElement[];
        return items.filter(i => i.getAttribute('aria-hidden') !== 'true' && window.getComputedStyle(i).display !== 'none' && i.getBoundingClientRect().width>0).length === 3;
      }, { timeout: 3000 });
      const vis1 = await countVisibleSublistsOnPage();
      expect(vis1).toBe(3);
    }

    await context.close();
    await browser.close();
  });
});
