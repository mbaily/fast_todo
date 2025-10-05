import { test, expect } from '@playwright/test';

/**
 * E2E test for calendar ignore button DOM refresh functionality.
 * Tests that clicking ignore buttons updates the DOM without requiring a page refresh.
 */

test.describe('Calendar Ignore Button DOM Refresh', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to home page first to ensure we get HTML, not JSON
    await page.goto('https://0.0.0.0:10443/html_no_js/', { waitUntil: 'networkidle' });
    
    // Check if we need to login
    const currentUrl = page.url();
    if (currentUrl.includes('login') || currentUrl.includes('auth')) {
      console.log('Login required - authenticating...');
      await page.fill('input[name="username"]', 'testuser');
      await page.fill('input[name="password"]', 'testpass123');
      await page.click('button[type="submit"]');
      await page.waitForURL('**/html_no_js/**');
    }
    
    // Now navigate to calendar
    await page.goto('https://0.0.0.0:10443/html_no_js/calendar');
    await page.waitForLoadState('networkidle');
    
    // Check if we got HTML or JSON
    const html = await page.content();
    if (html.includes('json-formatter') || html.length < 1000) {
      console.error('Got JSON response instead of HTML. Page content:', html.substring(0, 200));
      throw new Error('Calendar page returned JSON instead of HTML - authentication issue?');
    }
    
    // Wait for todos to load
    await page.waitForSelector('.todo, .calendar-event, li[class*="todo"]', { timeout: 10000 });
  });

  test('should refresh DOM when clicking ignore all button (🔕)', async ({ page }) => {
    // Find a todo occurrence that's not ignored
    const todoItem = page.locator('.todo').first();
    await expect(todoItem).toBeVisible();
    
    // Get the todo title for verification
    const todoTitle = await todoItem.locator('.todo-main .wrap-text').first().textContent();
    console.log(`Testing with todo: ${todoTitle}`);
    
    // Count occurrences before ignore
    const countBefore = await page.locator('.todo').count();
    console.log(`Occurrences before ignore: ${countBefore}`);
    
    // Click the ignore button (🔕)
    const ignoreButton = todoItem.locator('button.occ-ignore-occ').first();
    await expect(ignoreButton).toBeVisible();
    
    // Listen for console logs
    page.on('console', msg => {
      if (msg.text().includes('DEBUG:')) {
        console.log('Browser console:', msg.text());
      }
    });
    
    await ignoreButton.click();
    
    // Wait a moment for the fetch to complete and DOM to update
    await page.waitForTimeout(1000);
    
    // Check if DOM updated (occurrence should disappear or reduce count)
    const countAfter = await page.locator('.todo').count();
    console.log(`Occurrences after ignore: ${countAfter}`);
    
    // Verify the occurrence is no longer visible (or count reduced)
    // Since we ignored all occurrences of this todo, count should be less
    expect(countAfter).toBeLessThan(countBefore);
    
    console.log('✅ DOM updated successfully without page refresh!');
  });

  test('should refresh DOM when clicking ignore from date button (⏭️)', async ({ page }) => {
    // Find a recurring todo with multiple occurrences
    const allTodos = await page.locator('.todo').all();
    
    // Find a todo that appears multiple times (recurring)
    let recurringTodo = null;
    let recurringTodoTitle = '';
    
    for (const todo of allTodos) {
      const title = await todo.locator('.todo-main .wrap-text').first().textContent();
      const matchingTodos = await page.locator('.todo').filter({ hasText: title || '' }).count();
      
      if (matchingTodos > 1) {
        recurringTodo = todo;
        recurringTodoTitle = title || '';
        console.log(`Found recurring todo: ${recurringTodoTitle} (${matchingTodos} occurrences)`);
        break;
      }
    }
    
    if (!recurringTodo) {
      console.log('⚠️ No recurring todos found, skipping test');
      test.skip();
      return;
    }
    
    // Count occurrences of this specific todo before ignore
    const countBefore = await page.locator('.todo').filter({ hasText: recurringTodoTitle }).count();
    console.log(`Occurrences of "${recurringTodoTitle}" before ignore: ${countBefore}`);
    
    // Click the "ignore from this date" button (⏭️) on the first occurrence
    const ignoreFromButton = recurringTodo.locator('button.occ-ignore-from').first();
    await expect(ignoreFromButton).toBeVisible();
    
    // Listen for console logs
    page.on('console', msg => {
      if (msg.text().includes('DEBUG:')) {
        console.log('Browser console:', msg.text());
      }
    });
    
    await ignoreFromButton.click();
    
    // Wait a moment for the fetch to complete and DOM to update
    await page.waitForTimeout(1000);
    
    // Check if DOM updated (future occurrences should disappear)
    const countAfter = await page.locator('.todo').filter({ hasText: recurringTodoTitle }).count();
    console.log(`Occurrences of "${recurringTodoTitle}" after ignore-from: ${countAfter}`);
    
    // Verify some occurrences disappeared (future ones from that date)
    expect(countAfter).toBeLessThan(countBefore);
    
    console.log('✅ DOM updated successfully without page refresh!');
  });

  test('should show ignored todos when "Show ignored" is checked', async ({ page }) => {
    // First, ignore a todo
    const todoItem = page.locator('.todo').first();
    await expect(todoItem).toBeVisible();
    
    const ignoreButton = todoItem.locator('button.occ-ignore-occ').first();
    await ignoreButton.click();
    await page.waitForTimeout(1000);
    
    // Verify todo disappeared
    const countAfterIgnore = await page.locator('.todo').count();
    
    // Now check "Show ignored" checkbox
    const showIgnoredCheckbox = page.locator('input#show_ignored');
    await expect(showIgnoredCheckbox).toBeVisible();
    await showIgnoredCheckbox.check();
    
    // Wait for calendar to refresh
    await page.waitForTimeout(1000);
    
    // Count should increase (ignored todos now visible)
    const countWithIgnored = await page.locator('.todo').count();
    console.log(`Count after ignore: ${countAfterIgnore}, with show_ignored: ${countWithIgnored}`);
    
    expect(countWithIgnored).toBeGreaterThan(countAfterIgnore);
    
    // Verify there's at least one todo marked as "(ignored)"
    const ignoredLabel = page.locator('.meta:has-text("(ignored)")');
    await expect(ignoredLabel).toBeVisible();
    
    console.log('✅ Show ignored checkbox works correctly!');
  });

  test('should show unignore button and restore todo when clicked', async ({ page }) => {
    // First, ignore a todo
    const todoItem = page.locator('.todo').first();
    const todoTitle = await todoItem.locator('.todo-main .wrap-text').first().textContent();
    
    const ignoreButton = todoItem.locator('button.occ-ignore-occ').first();
    await ignoreButton.click();
    await page.waitForTimeout(1000);
    
    // Check "Show ignored"
    const showIgnoredCheckbox = page.locator('input#show_ignored');
    await showIgnoredCheckbox.check();
    await page.waitForTimeout(1000);
    
    // Find the ignored todo and click unignore button (↩️)
    const ignoredTodo = page.locator('.todo').filter({ hasText: todoTitle || '' }).first();
    const unignoreButton = ignoredTodo.locator('button.occ-unignore').first();
    
    await expect(unignoreButton).toBeVisible();
    await unignoreButton.click();
    await page.waitForTimeout(1000);
    
    // Uncheck "Show ignored" to verify todo is now visible normally
    await showIgnoredCheckbox.uncheck();
    await page.waitForTimeout(1000);
    
    // Verify the todo is now visible (not ignored)
    const restoredTodo = page.locator('.todo').filter({ hasText: todoTitle || '' }).first();
    await expect(restoredTodo).toBeVisible();
    
    // Should NOT have "(ignored)" label
    const ignoredLabel = restoredTodo.locator('.meta:has-text("(ignored)")');
    await expect(ignoredLabel).not.toBeVisible();
    
    console.log('✅ Unignore button works correctly!');
  });

  test('should display console DEBUG logs when buttons are clicked', async ({ page }) => {
    const consoleLogs: string[] = [];
    
    page.on('console', msg => {
      const text = msg.text();
      if (text.includes('DEBUG:')) {
        consoleLogs.push(text);
        console.log('Browser console:', text);
      }
    });
    
    // Click ignore button
    const ignoreButton = page.locator('button.occ-ignore-occ').first();
    await ignoreButton.click();
    await page.waitForTimeout(1000);
    
    // Verify we got console logs
    expect(consoleLogs.length).toBeGreaterThan(0);
    
    // Check for expected log patterns
    const hasIgnoreLog = consoleLogs.some(log => 
      log.includes('calendar_ignore_response') || log.includes('Ignoring todo')
    );
    
    expect(hasIgnoreLog).toBeTruthy();
    
    console.log('✅ Console DEBUG logs are working!');
    console.log(`Captured ${consoleLogs.length} debug logs`);
  });
});
