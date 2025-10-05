import { test, expect, chromium } from '@playwright/test';

/**
 * Connect to existing Chrome browser via CDP to test ignore button refresh.
 * Chrome must be started with: chrome --remote-debugging-port=9222
 */

test('Test ignore button with existing browser session via CDP', async () => {
  // Connect to existing Chrome instance
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const contexts = browser.contexts();
  
  if (contexts.length === 0) {
    console.error('No browser contexts found. Is Chrome running with remote debugging?');
    throw new Error('No browser contexts available');
  }
  
  const context = contexts[0];
  const pages = context.pages();
  
  if (pages.length === 0) {
    console.error('No pages found in browser');
    throw new Error('No pages available');
  }
  
  // Use the first page (or find the calendar page)
  let page = pages[0];
  
  // Try to find the calendar page if multiple pages are open
  for (const p of pages) {
    const url = p.url();
    console.log('Found page:', url);
    if (url.includes('calendar')) {
      page = p;
      console.log('Using calendar page');
      break;
    }
  }
  
  // Force reload to get latest JavaScript code
  console.log('Reloading calendar page to get latest code...');
  await page.goto('https://0.0.0.0:10443/html_no_js/calendar', { waitUntil: 'networkidle' });
  
  console.log('Current page URL:', page.url());
  
  // Wait for todos to load
  await page.waitForSelector('.todo', { timeout: 10000 });
  
    // CRITICAL: Ensure "Show ignored" checkbox is CHECKED (to test in-place update of visual indicators)
  const showIgnoredCheckbox = page.locator('input#show_ignored');
  const isChecked = await showIgnoredCheckbox.isChecked();
  console.log(`\n⚙️  "Show ignored" checkbox is currently: ${isChecked ? 'CHECKED' : 'unchecked'}`);
  
  if (!isChecked) {
    console.log('   Checking "Show ignored" to test visual indicator updates...');
    await showIgnoredCheckbox.check();
    await page.waitForTimeout(1000); // Wait for refresh
  }
  
  const countBefore = await page.locator('.todo').count();
  console.log(`\n📊 Occurrences before ignore: ${countBefore}`);
  
  // Find first todo that is NOT ignored
  const allTodos = await page.locator('.todo').all();
  let firstTodo = null;
  let todoTitle = '';
  
  for (const todo of allTodos) {
    const title = await todo.locator('.todo-main .wrap-text').first().textContent();
    if (title && !title.includes('(ignored)')) {
      firstTodo = todo;
      todoTitle = title;
      break;
    }
  }
  
  if (!firstTodo) {
    console.log('\n⚠️  No non-ignored todos found! All todos are already ignored.');
    throw new Error('No non-ignored todos to test with');
  }
  
  console.log(`\n🎯 Testing with todo: "${todoTitle}"`);
  
  // Count how many occurrences this specific todo has
  const sameTodoCount = await page.locator('.todo').filter({ hasText: todoTitle }).count();
  console.log(`   This todo appears ${sameTodoCount} time(s) in the calendar`);
  
  // Set up console log listener BEFORE clicking
  const consoleLogs: string[] = [];
  page.on('console', msg => {
    const text = msg.text();
    const type = msg.type();
    consoleLogs.push(text);
    console.log(`  Browser console [${type}]:`, text);
  });
  
  // Also listen for page errors
  page.on('pageerror', err => {
    console.log('  Browser error:', err.message);
  });
  
  // Find and click the ignore button (🔕)
  const ignoreButton = firstTodo.locator('button.occ-ignore-occ').first();
  console.log('\n🖱️  Clicking ignore button...');
  await ignoreButton.click();
  
  // Wait a moment for the request
  console.log('\n⏱️  Waiting 2 seconds for response and DOM update...');
  await page.waitForTimeout(2000);
  
  // Check if DOM updated
  const countAfter = await page.locator('.todo').count();
  console.log(`\n📊 Occurrences after ignore: ${countAfter}`);
  
  // Check if the specific todo we ignored still appears (should with show_ignored=true)
  const sameTodoCountAfter = await page.locator('.todo').filter({ hasText: todoTitle }).count();
  console.log(`   The ignored todo now appears ${sameTodoCountAfter} time(s)`);
  
  // Check for visual indicators
  if (sameTodoCountAfter > 0) {
    const ignoredTodo = page.locator('.todo').filter({ hasText: todoTitle }).first();
    
    // Check for (ignored) label
    const hasIgnoredLabel = await ignoredTodo.locator('.meta:has-text("(ignored)")').count() > 0;
    console.log(`   Has "(ignored)" label: ${hasIgnoredLabel ? '✅ YES' : '❌ NO'}`);
    
    // Check for unignore button (↩️)
    const hasUnignoreButton = await ignoredTodo.locator('button.occ-unignore').count() > 0;
    console.log(`   Has unignore button (↩️): ${hasUnignoreButton ? '✅ YES' : '❌ NO'}`);
    
    // Check that ignore buttons are gone
    const hasIgnoreButton = await ignoredTodo.locator('button.occ-ignore-occ').count() > 0;
    console.log(`   Still has ignore button (🔕): ${hasIgnoreButton ? '❌ YES (should be gone)' : '✅ NO (correct)'}`);
    
    if (hasIgnoredLabel && hasUnignoreButton && !hasIgnoreButton) {
      console.log('\n✅ All visual indicators updated correctly!');
    } else {
      console.log('\n❌ Some visual indicators missing!');
    }
  }
  
  // Check console logs
  console.log(`\n📝 Captured ${consoleLogs.length} console logs`);
  const hasDebugLogs = consoleLogs.some(log => log.includes('DEBUG:'));
  console.log(`   Has DEBUG logs: ${hasDebugLogs}`);
  
  const hasIgnoreResponse = consoleLogs.some(log => 
    log.includes('calendar_ignore_response') || log.includes('Ignoring todo')
  );
  console.log(`   Has ignore response: ${hasIgnoreResponse}`);
  
  // Check if fetchOccurrencesForCurrentWindow was called
  const calledFetch = consoleLogs.some(log => log.includes('fetching occurrences'));
  console.log(`   Called fetchOccurrences: ${calledFetch}`);
  
  // Results
  console.log('\n' + '='.repeat(60));
  if (countAfter === countBefore) {
    console.log('✅ With "Show ignored" ON, count should stay the same (ignored items still shown)');
  } else if (countAfter < countBefore) {
    console.log('⚠️  Count decreased - but with "Show ignored" ON it should stay the same');
  }
  
  if (hasDebugLogs) {
    console.log('✅ Console DEBUG logs are working');
  } else {
    console.log('❌ No DEBUG logs captured');
  }
  
  console.log('='.repeat(60));
  
  // Take a screenshot
  await page.screenshot({ path: 'test-results/cdp-ignore-test.png', fullPage: true });
  console.log('\n📸 Screenshot saved to test-results/cdp-ignore-test.png');
  
  // Don't close the browser - we're just inspecting it
  await browser.close();
});
