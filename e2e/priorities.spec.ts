import { test, expect } from '@playwright/test';

const BASE = process.env.BASE_URL || 'https://0.0.0.0:10443';
const USER = process.env.TEST_USER || 'mbaily';
const PASS = process.env.TEST_PASS || 'mypass';

test('completed lists and todos are struck-through but priority circle is unchanged', async ({ page }) => {
  // Go to login page
  await page.goto(`${BASE}/html_no_js/login`);
  // Fill login form
  await page.fill('input[name="username"]', USER);
  await page.fill('input[name="password"]', PASS);
  await Promise.all([
    page.waitForNavigation(),
    page.click('button[type="submit"]')
  ]);

  // Navigate to priorities page and ensure at least one prioritized todo is completed.
  await page.goto(`${BASE}/html_no_js/priorities`);

  // Prefer to operate on an existing prioritized todo: open its todo page and submit the
  // server-rendered complete form (reliable CSRF + server flow) so the priorities page will
  // render the todo with the .done class. If none are available, fall back to the previous
  // creation + priority + complete approach.
  const candidate = page.locator('.todos-list a.wrap-text[href^="/html_no_js/todos/"]:not(.done)').first();
  if (await candidate.count() > 0) {
    const href = await candidate.getAttribute('href');
    const m = href && href.match(/\/html_no_js\/todos\/(\d+)/);
      if (m) {
      const id = m[1];
      // Open the todo page and call the complete endpoint via fetch using the page CSRF token
      await page.goto(`${BASE}/html_no_js/todos/${id}`);
      // Use fetch inside the page context to POST form-encoded data and avoid navigation
      const resOk = await page.evaluate(async (tid) => {
        try {
          var csrf = (document.querySelector('input[name="_csrf"]') || {}).value || '';
          var body = new URLSearchParams(); if (csrf) body.append('_csrf', csrf); body.append('done', 'true');
          var r = await fetch('/html_no_js/todos/' + tid + '/complete', { method: 'POST', credentials: 'same-origin', body: body });
          return r && (r.ok || r.status === 303);
        } catch (e) { return false; }
      }, id);
      // Allow a short moment for the server to commit changes
      await page.waitForTimeout(400).catch(() => {});
      // Verify the individual todo page reflects the completed state before checking priorities
      await page.goto(`${BASE}/html_no_js/todos/${id}`);
      const doneInput = page.locator('form#todo-complete-form input[name="done"]').first();
      const doneVal = await doneInput.getAttribute('value');
      // When completed, the form toggles to unmark so hidden 'done' value becomes 'false' and button shows ☑
      if (doneVal !== 'false') {
        const todoHtml = await page.locator('main').first().evaluate(e => e ? e.outerHTML : '');
        console.log('DEBUG: todo page after complete HTML:\n', todoHtml);
      }
      expect(doneVal).toBe('false');
      const completeBtn = page.locator('form#todo-complete-form button').first();
      await expect(completeBtn).toBeVisible();
      const btnText = (await completeBtn.innerText()).trim();
      expect(btnText).toContain('☑');
      // Now load priorities to assert global view
      await page.goto(`${BASE}/html_no_js/priorities`);
    }
  } else {
    // Fallback: create, priority and complete via in-page fetch (keeps session)
    const anyDone = await page.locator('.wrap-text.done').count();
    if (!anyDone) {
      const newText = 'e2e-temp-todo-' + String(Date.now());
      const createdId = await page.evaluate(async (text) => {
        try {
          var csrf = (document.querySelector('input[name="_csrf"]') || {}).value || '';
          // create via HTML endpoint
          var fd = new FormData(); fd.append('text', text); fd.append('list_id', '1'); if (csrf) fd.append('_csrf', csrf);
          var r = await fetch('/html_no_js/todos/create', { method: 'POST', credentials: 'same-origin', body: fd });
          if (!r.ok) return null;
          // Try to read JSON response if provided
          try { var todo = await r.json(); } catch(e) { return null; }
          var id = todo && todo.id ? todo.id : null;
          if (!id) return null;
          // set priority via form POST to html_no_js endpoint
          var p = new URLSearchParams(); if (csrf) p.append('_csrf', csrf); p.append('priority', '1');
          await fetch('/html_no_js/todos/' + id + '/priority', { method: 'POST', credentials: 'same-origin', body: p });
          // mark complete via form POST
          var c = new URLSearchParams(); if (csrf) c.append('_csrf', csrf); c.append('done', 'true');
          await fetch('/html_no_js/todos/' + id + '/complete', { method: 'POST', credentials: 'same-origin', body: c });
          return id;
        } catch (e) { return null; }
      }, newText);
      if (createdId) {
        await page.waitForTimeout(400);
        // Verify the new todo page shows completed state
        await page.goto(`${BASE}/html_no_js/todos/${createdId}`);
        const doneInput2 = page.locator('form#todo-complete-form input[name="done"]').first();
        const doneVal2 = await doneInput2.getAttribute('value');
        if (doneVal2 !== 'false') {
          const todoHtml = await page.locator('main').first().evaluate(e => e ? e.outerHTML : '');
          console.log('DEBUG: todo page after create+complete HTML:\n', todoHtml);
        }
        expect(doneVal2).toBe('false');
        const completeBtn2 = page.locator('form#todo-complete-form button').first();
        await expect(completeBtn2).toBeVisible();
        const btnText2 = (await completeBtn2.innerText()).trim();
        expect(btnText2).toContain('☑');
        await page.goto(`${BASE}/html_no_js/priorities`);
      }
    }
  }

  // Ensure completed items are visible: uncheck the "Hide completed" box if it's checked.
  const hideCb = page.locator('#hide-completed-checkbox');
  if (await hideCb.count() > 0) {
    if (await hideCb.isChecked()) {
      // clicking this checkbox triggers a location.reload() in the page script,
      // so wait for navigation.
      await Promise.all([page.waitForNavigation(), hideCb.click()]);
    }
  }

  // Find a completed list title (element with class list-title.done)
  const listDone = await page.locator('a.list-title.done').first();
  await expect(listDone).toBeVisible();
  const listDecoration = await listDone.evaluate((el) => getComputedStyle(el).textDecoration);
  expect(listDecoration).toContain('line-through');

  // Ensure priority circle (sibling) is not struck-through
  const listPriority = await listDone.locator('xpath=following-sibling::span//span[contains(@class,"priority-circle")]').first();
  if (await listPriority.count() > 0) {
    const priDec = await listPriority.evaluate((el) => getComputedStyle(el).textDecoration);
    expect(priDec).not.toContain('line-through');
  }

  // Find a completed todo text
  const todoDoneLocator = page.locator('.todos-list a.wrap-text.done');
  const todoDoneCount = await todoDoneLocator.count();
  console.log('DEBUG: completed todo anchors count on priorities =', todoDoneCount);
  if (todoDoneCount === 0) {
    const todosHtml = await page.locator('.todos-list').first().evaluate(e => e ? e.outerHTML : '');
    console.log('DEBUG: .todos-list HTML:\n', todosHtml);
  }
  const todoDone = await todoDoneLocator.first();
  await expect(todoDone).toBeVisible();
  const todoDecoration = await todoDone.evaluate((el) => getComputedStyle(el).textDecoration);
  expect(todoDecoration).toContain('line-through');

  // Ensure the todo's priority circle is not struck-through
  const todoPriority = await todoDone.locator('xpath=following::span//span[contains(@class,"priority-circle")]').first();
  if (await todoPriority.count() > 0) {
    const tpriDec = await todoPriority.evaluate((el) => getComputedStyle(el).textDecoration);
    expect(tpriDec).not.toContain('line-through');
  }
});
