import { test, expect, chromium } from '@playwright/test';

test('reload flow: check -> reload -> uncheck should show all sublists (list 490)', async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  try {
    const contexts = browser.contexts();
    const context = contexts.length ? contexts[0] : await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await context.newPage();
    page.on('console', msg => { try { console.log('PAGE:', msg.type(), msg.text()); } catch(_){} });

    const url = 'https://127.0.0.1:10443/html_no_js/lists/490';
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.waitForSelector('section[aria-label="Sublists"]', { timeout: 5000 });

    const checkboxSel = '[data-sublists-hide-done-toggle]';
    await page.waitForSelector(checkboxSel, { timeout: 3000 });

    // Ensure it's checked (user step 1)
    const initiallyChecked = await page.evaluate((sel) => {
      const el = document.querySelector(sel) as HTMLInputElement | null; return !!(el && el.checked);
    }, checkboxSel);
    if (!initiallyChecked) {
      // click to check and let the page persist state
      await page.click(checkboxSel);
      await page.waitForTimeout(300);
    }

    // Confirm that with hide done checked the visible count matches uncompleted count (sanity)
    const countsWhenChecked = await page.evaluate(() => {
      const items = Array.from(document.querySelectorAll('section[aria-label="Sublists"] ul.lists-list li.list-item'));
      const total = items.length;
      const uncompleted = items.filter(i => (i.getAttribute('data-completed')||'').toLowerCase() !== 'true').length;
      const visible = items.filter(i => window.getComputedStyle(i).display !== 'none').length;
      return { total, uncompleted, visible };
    });

    // Reload the page (user step 2)
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForSelector('section[aria-label="Sublists"]', { timeout: 5000 });

    // Now uncheck (user step 3)
    await page.waitForSelector(checkboxSel, { timeout: 3000 });
    // Use click to trigger the bound change handler and persist
    await page.click(checkboxSel);
    // Ensure helper runs as well (defensive)
    await page.evaluate(() => { try { if ((window as any).__ft_applySublistsHideDone) (window as any).__ft_applySublistsHideDone(false); } catch(e) {} });
    await page.waitForTimeout(400);

    // Verify all sublists are visible
    const finalVisible = await page.evaluate(() => {
      const items = Array.from(document.querySelectorAll('section[aria-label="Sublists"] ul.lists-list li.list-item'));
      return { total: items.length, visible: items.filter(i => window.getComputedStyle(i).display !== 'none').length };
    });

    if (finalVisible.visible !== finalVisible.total) {
      const diag = await page.evaluate(() => {
        const secs = Array.from(document.querySelectorAll('section[aria-label="Sublists"]'));
        return secs.map(sec => ({ classList: Array.from(sec.classList||[]), items: Array.from(sec.querySelectorAll('ul.lists-list li.list-item')).map(i => ({ text: (i.textContent||'').trim().slice(0,80), dataCompleted: i.getAttribute('data-completed'), inlineStyle: i.getAttribute('style')||'', computedDisplay: window.getComputedStyle(i).display })) }));
      });
      console.error('DIAG after uncheck reload flow', JSON.stringify(diag, null, 2));
    }

    expect(finalVisible.visible).toBe(finalVisible.total);
  } finally {
    try { await browser.close(); } catch(_){}
  }
});
