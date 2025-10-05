import { test, expect, Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';

const BASE = process.env.PLAYWRIGHT_BASE ?? 'https://0.0.0.0:10443';
const TZ = 'Australia/Melbourne';

async function login(page: Page) {
  await page.goto(`${BASE}/html_no_js/login`, { waitUntil: 'networkidle' });
  await page.fill('#username', process.env.PLAYWRIGHT_USERNAME ?? 'mbaily');
  await page.fill('#password', process.env.PLAYWRIGHT_PASSWORD ?? 'mypass');
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle', timeout: 15000 }),
    page.click('button[type="submit"]'),
  ]);
}

async function ensureTimezoneCookie(page: Page) {
  await page.context().addCookies([{ name: 'tz', value: TZ, url: BASE }]);
}

test.describe('Calendar ignore-from workflow', () => {
  test('ignore from date onwards hides future occurrences until unignored', async ({ page }) => {

    const suffix = Date.now();
    const listName = `Playwright Ignore ${suffix}`;
    const todoTitle = `Playwright recurring ${suffix}`;
    const dtstartIso = '2025-10-05T00:00:00+00:00';

    const seedResult = execFileSync(
      'python',
      [
        'scripts/seed_ignore_scope_data.py',
        '--username', 'mbaily',
        '--password', 'mypass',
        '--list-name', listName,
        '--todo-text', todoTitle,
        '--dtstart', dtstartIso,
      ],
      {
        cwd: process.cwd(),
        env: { ...process.env, PYTHONPATH: process.cwd() },
      },
    ).toString();
    let todoId: string;
    try {
      const parsed = JSON.parse(seedResult);
      todoId = String(parsed.todo_id);
    } catch (err) {
      throw new Error(`Failed to parse seed output: ${seedResult}\n${err}`);
    }

    await ensureTimezoneCookie(page);
    await login(page);

    // Visit October 2025 calendar and locate our occurrences
  await page.goto(`${BASE}/html_no_js/calendar?year=2025&month=10`, { waitUntil: 'networkidle' });
    const occurrenceCheckboxes = (iso: string) =>
      page.locator(
        `#preact-calendar-root input.occ-complete[data-item-id="${todoId}"][data-occ-dt="${iso}"]`
      );
    const occurrenceCheckbox = (iso: string) => occurrenceCheckboxes(iso).first();

    const ignoreFromButtons = (iso: string) =>
      page.locator(
        `#preact-calendar-root button.occ-ignore-from[data-todo="${todoId}"][data-occ-dt="${iso}"]`
      );
    const ignoreFromButton = (iso: string) => ignoreFromButtons(iso).first();

    await expect(occurrenceCheckbox('2025-10-05T00:00:00+00:00')).toBeVisible({ timeout: 10000 });
    await expect(occurrenceCheckbox('2025-10-10T00:00:00+00:00')).toBeVisible();
    await expect(ignoreFromButton('2025-10-25T00:00:00+00:00')).toBeVisible();

    // Mark first two occurrences complete to match server-spec expectations.
    await occurrenceCheckbox('2025-10-05T00:00:00+00:00').check();
    await expect(occurrenceCheckbox('2025-10-05T00:00:00+00:00')).toBeChecked();
    await occurrenceCheckbox('2025-10-10T00:00:00+00:00').check();
    await expect(occurrenceCheckbox('2025-10-10T00:00:00+00:00')).toBeChecked();

    // Apply "ignore from this date onwards" on Oct 25.
    await ignoreFromButton('2025-10-25T00:00:00+00:00').click();

    await expect(ignoreFromButtons('2025-10-25T00:00:00+00:00')).toHaveCount(0);

    // Verify future October occurrences are gone while earlier ones remain.
    await expect(occurrenceCheckbox('2025-10-05T00:00:00+00:00')).toBeVisible();
    await expect(occurrenceCheckbox('2025-10-10T00:00:00+00:00')).toBeVisible();
  await expect(occurrenceCheckboxes('2025-10-25T00:00:00+00:00')).toHaveCount(0);
  await expect(occurrenceCheckboxes('2025-10-30T00:00:00+00:00')).toHaveCount(0);

    // Navigate to November to ensure no future occurrences appear.
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'networkidle' }),
      page.click('a.button:has-text("Next")')
    ]);
    await expect(
      page.locator(
        `#preact-calendar-root input.occ-complete[data-item-id="${todoId}"][data-occ-dt^="2025-11-"]`
      )
    ).toHaveCount(0);

    // Return to October and reveal ignored occurrences.
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'networkidle' }),
      page.click('a.button:has-text("Prev")')
    ]);

    await page.check('#show_ignored');

  const unignoreButtons = page.locator(`#preact-calendar-root button.occ-unignore[data-item-id="${todoId}"]`);
  await expect(unignoreButtons.first()).toBeVisible();

  const unignoreButton = unignoreButtons.first();
    await expect(unignoreButton).toBeVisible();

    // Unignore to restore future occurrences.
    await unignoreButton.click();

  await expect(ignoreFromButtons('2025-10-25T00:00:00+00:00').first()).toBeVisible();

    await page.uncheck('#show_ignored');

    // Verify October occurrences (including 25th) are present again and completions persisted.
    await expect(occurrenceCheckbox('2025-10-25T00:00:00+00:00')).toBeVisible({ timeout: 10000 });
    await expect(occurrenceCheckbox('2025-10-05T00:00:00+00:00')).toBeChecked();
    await expect(occurrenceCheckbox('2025-10-10T00:00:00+00:00')).toBeChecked();

    // Navigate to November and confirm occurrences return post-unignore.
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'networkidle' }),
      page.click('a.button:has-text("Next")')
    ]);
  await expect(occurrenceCheckbox('2025-11-04T00:00:00+00:00')).toBeVisible({ timeout: 10000 });
  });
});
