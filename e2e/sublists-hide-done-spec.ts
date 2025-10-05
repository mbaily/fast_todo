import { test, expect, chromium, Browser, BrowserContext, Page, ConsoleMessage } from '@playwright/test';

async function connect(): Promise<{ browser: Browser, context: BrowserContext }> {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const contexts = browser.contexts();
  const context = contexts.length ? contexts[0] : await browser.newContext({ ignoreHTTPSErrors: true });
  return { browser, context };
}

async function openPage(context: BrowserContext, url: string): Promise<Page> {
  const page = await context.newPage();
  page.on('console', (msg: ConsoleMessage) => { try { console.log('PAGE', msg.type(), msg.text()); } catch(_){} });
  await page.bringToFront();
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForSelector('section[aria-label="Sublists"]', { timeout: 5000 });
  return page;
}

async function setHideDone(page: Page, checked: boolean) {
  const sel = '[data-todo-sublists-hide-done-toggle], [data-sublists-hide-done-toggle], input[name="sublists_hide_done"]';
  await page.waitForSelector(sel, { timeout: 3000 });
  await page.evaluate(({ s, checked }: { s: string, checked: boolean }) => {
    var el = document.querySelector(s);
    if (!el) return;
    try {
      if ((el as HTMLInputElement).checked !== undefined) (el as HTMLInputElement).checked = checked;
      // dispatch change
      el.dispatchEvent(new Event('change', { bubbles: true }));
    } catch(e) {}
  }, { s: sel, checked });
  // allow persistence/fetch to complete
  await page.waitForTimeout(300);
}

async function reloadAndAssertSSRChecked(page: Page, expected: boolean) {
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForSelector('section[aria-label="Sublists"]', { timeout: 5000 });
  const sel = '[data-todo-sublists-hide-done-toggle], [data-sublists-hide-done-toggle], input[name="sublists_hide_done"]';
  await page.waitForSelector(sel, { timeout: 3000 });
  const val = await page.evaluate((s: string) => { var el = document.querySelector(s); return !!(el && (el as HTMLInputElement).checked); }, sel);
  expect(val).toBe(expected);
}

async function visibleSublistsCount(page: Page) {
  return await page.evaluate(() => {
    const items = Array.from(document.querySelectorAll('section[aria-label="Sublists"] ul.lists-list li.list-item'));
    const total = items.length;
    const visible = items.filter(i => window.getComputedStyle(i).display !== 'none').length;
    return { total, visible };
  });
}

test('sublists hide-done SSR and visibility scenarios', async () => {
  const { browser, context } = await connect();
  try {
    // Scenario helper pages
    const todo571 = 'https://127.0.0.1:10443/html_no_js/todos/571';
    const list491 = 'https://127.0.0.1:10443/html_no_js/lists/491';
    const list490 = 'https://127.0.0.1:10443/html_no_js/lists/490';
    // 1) Check 'hide done' for sublists for both todo 571 and list 491. Reload and check SSR checked.
    const p1 = await openPage(context, todo571);
    await setHideDone(p1, true);
    await reloadAndAssertSSRChecked(p1, true);
    await p1.close();

    const p2 = await openPage(context, list491);
    await setHideDone(p2, true);
    await reloadAndAssertSSRChecked(p2, true);
    await p2.close();

    // 2) Uncheck for both and reload -> SSR unchecked
    const p3 = await openPage(context, todo571);
    await setHideDone(p3, false);
    await reloadAndAssertSSRChecked(p3, false);
    await p3.close();

    const p4 = await openPage(context, list491);
    await setHideDone(p4, false);
    await reloadAndAssertSSRChecked(p4, false);
    await p4.close();

    // 3) SSR with hide-done checked (tick first) then reload and check todo 571 and list 490 show 2 sublists
    const p5 = await openPage(context, todo571);
    await setHideDone(p5, true);
    await p5.reload({ waitUntil: 'networkidle' });
    const counts1 = await visibleSublistsCount(p5);
    // ensure server-rendered checkbox is checked
    await reloadAndAssertSSRChecked(p5, true);
    expect(counts1.visible).toBe(2);
    await p5.close();

    const p6 = await openPage(context, list490);
    // ensure list490 has SSR checked by setting and reloading
    await setHideDone(p6, true);
    await p6.reload({ waitUntil: 'networkidle' });
    const counts2 = await visibleSublistsCount(p6);
    await reloadAndAssertSSRChecked(p6, true);
    expect(counts2.visible).toBe(2);
    await p6.close();

    // 4) SSR with hide-done unchecked first (uncheck then reload) then both show 3 sublists
    const p7 = await openPage(context, todo571);
    await setHideDone(p7, false);
    await p7.reload({ waitUntil: 'networkidle' });
    const counts3 = await visibleSublistsCount(p7);
    await reloadAndAssertSSRChecked(p7, false);
    expect(counts3.visible).toBe(3);
    await p7.close();

    const p8 = await openPage(context, list490);
    await setHideDone(p8, false);
    await p8.reload({ waitUntil: 'networkidle' });
    const counts4 = await visibleSublistsCount(p8);
    await reloadAndAssertSSRChecked(p8, false);
    expect(counts4.visible).toBe(3);
    await p8.close();

    // 5) Check for todo 571 and list 490, reload, then uncheck -> both show 3
    const p9 = await openPage(context, todo571);
    await setHideDone(p9, true);
    await p9.reload({ waitUntil: 'networkidle' });
    await setHideDone(p9, false);
    const counts5 = await visibleSublistsCount(p9);
    expect(counts5.visible).toBe(3);
    await p9.close();

    const p10 = await openPage(context, list490);
    await setHideDone(p10, true);
    await p10.reload({ waitUntil: 'networkidle' });
    await setHideDone(p10, false);
    const counts6 = await visibleSublistsCount(p10);
    expect(counts6.visible).toBe(3);
    await p10.close();

    // 6) Uncheck for both, reload, then check -> both show 2
    const p11 = await openPage(context, todo571);
    await setHideDone(p11, false);
    await p11.reload({ waitUntil: 'networkidle' });
    await setHideDone(p11, true);
    const counts7 = await visibleSublistsCount(p11);
    expect(counts7.visible).toBe(2);
    await p11.close();

    const p12 = await openPage(context, list490);
    await setHideDone(p12, false);
    await p12.reload({ waitUntil: 'networkidle' });
    await setHideDone(p12, true);
    const counts8 = await visibleSublistsCount(p12);
    expect(counts8.visible).toBe(2);
    await p12.close();

  } finally {
    try { await browser.close(); } catch(_){}
  }
});
