import { test, expect, chromium } from '@playwright/test';

test('manual flow (no helper): check -> reload -> click uncheck should show all sublists (list 490)', async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  try {
    const contexts = browser.contexts();
    const context = contexts.length ? contexts[0] : await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await context.newPage();
    page.on('console', msg => { try { console.log('PAGE', msg.type(), msg.text()); } catch(_){} });

    const url = 'https://127.0.0.1:10443/html_no_js/lists/490';
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.waitForSelector('section[aria-label="Sublists"]', { timeout: 5000 });

    const checkboxSel = '[data-sublists-hide-done-toggle]';
    await page.waitForSelector(checkboxSel, { timeout: 3000 });

    // Ensure it's checked
    const initiallyChecked = await page.evaluate((sel) => { const el = document.querySelector(sel) as HTMLInputElement|null; return !!(el && el.checked); }, checkboxSel);
    if (!initiallyChecked) await page.click(checkboxSel);

    // Reload
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForSelector('section[aria-label="Sublists"]', { timeout: 5000 });

    // Now click the checkbox to uncheck (simulate user click) without calling helper
    await page.waitForSelector(checkboxSel, { timeout: 3000 });
    await page.click(checkboxSel);
    await page.waitForTimeout(300);

    // Collect diagnostics
    const diag = await page.evaluate(() => {
      const secs = Array.from(document.querySelectorAll('section[aria-label="Sublists"]'));
      const boxes = Array.from(document.querySelectorAll('[data-sublists-hide-done-toggle], [data-todo-sublists-hide-done-toggle]')).map(function(b){ return { outerHTML: b.outerHTML, checked: (b as HTMLInputElement).checked }; });
      return {
        boxes: boxes,
        sections: secs.map(sec => ({ classList: Array.from(sec.classList||[]), items: Array.from(sec.querySelectorAll('ul.lists-list li.list-item')).map(i=>({ text:(i.textContent||'').trim().slice(0,80), dataCompleted: i.getAttribute('data-completed'), styleAttr: i.getAttribute('style')||'', computed: window.getComputedStyle(i).display })) }))
      };
    });

    const finalVisible = await page.evaluate(() => {
      const items = Array.from(document.querySelectorAll('section[aria-label="Sublists"] ul.lists-list li.list-item'));
      return { total: items.length, visible: items.filter(i => window.getComputedStyle(i).display !== 'none').length };
    });

    if (finalVisible.visible !== finalVisible.total) console.error('Manual-flow DIAG', JSON.stringify(diag, null, 2));

    expect(finalVisible.visible).toBe(finalVisible.total);
  } finally {
    try { await browser.close(); } catch(_){}
  }
});
