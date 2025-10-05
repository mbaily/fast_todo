import { test, expect, chromium } from '@playwright/test';

/**
 * E2E test for unignore button DOM refresh functionality.
 * Tests that clicking unignore button updates visual indicators without page refresh.
 */

test('Test unignore button with existing browser session via CDP', async () => {
  // Connect to existing Chrome instance
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const contexts = browser.contexts();
  
  if (contexts.length === 0) {
    throw new Error('No browser contexts available');
  }
  
  const context = contexts[0];
  const pages = context.pages();
  
  if (pages.length === 0) {
    throw new Error('No pages available');
  }
  
  let page = pages[0];
  
  // Try to find the calendar page
  for (const p of pages) {
    const url = p.url();
    if (url.includes('calendar')) {
      page = p;
      break;
    }
  }
  
  // Force reload to get latest JavaScript code
  console.log('Reloading calendar page to get latest code...');
  await page.goto('https://0.0.0.0:10443/html_no_js/calendar', { waitUntil: 'networkidle' });
  
  console.log('Current page URL:', page.url());
  
  // Wait for todos to load
  await page.waitForSelector('.todo', { timeout: 10000 });
  
  // Ensure "Show ignored" checkbox is CHECKED
  const showIgnoredCheckbox = page.locator('input#show_ignored');
  const isChecked = await showIgnoredCheckbox.isChecked();
  console.log(`\n⚙️  "Show ignored" checkbox is currently: ${isChecked ? 'CHECKED' : 'unchecked'}`);
  
  if (!isChecked) {
    console.log('   Checking "Show ignored"...');
    await showIgnoredCheckbox.check();
    await page.waitForTimeout(1000);
  }
  
  // Find a todo that IS ignored (has unignore button)
  const allTodos = await page.locator('.todo').all();
  let ignoredTodo = null;
  let todoTitle = '';
  
  for (const todo of allTodos) {
    const title = await todo.locator('.todo-main .wrap-text').first().textContent();
    const hasUnignoreBtn = await todo.locator('button.occ-unignore').count() > 0;
    
    if (title && hasUnignoreBtn) {
      ignoredTodo = todo;
      todoTitle = title;
      break;
    }
  }
  
  if (!ignoredTodo) {
    console.log('\n⚠️  No ignored todos found! Need to ignore one first.');
    throw new Error('No ignored todos to test with');
  }
  
  console.log(`\n🎯 Testing unignore with todo: "${todoTitle}"`);
  
  // Set up console log listener
  const consoleLogs: string[] = [];
  page.on('console', msg => {
    const text = msg.text();
    consoleLogs.push(text);
    console.log(`  Browser console [${msg.type()}]:`, text);
  });
  
  // Verify it has ignored indicators BEFORE unignoring
  const hasIgnoredLabelBefore = await ignoredTodo.locator('.meta:has-text("(ignored)")').count() > 0;
  const hasUnignoreButtonBefore = await ignoredTodo.locator('button.occ-unignore').count() > 0;
  const hasIgnoreButtonBefore = await ignoredTodo.locator('button.occ-ignore-occ').count() > 0;
  
  console.log('\n📋 Before unignore:');
  console.log(`   Has "(ignored)" label: ${hasIgnoredLabelBefore ? '✅' : '❌'}`);
  console.log(`   Has unignore button (↩️): ${hasUnignoreButtonBefore ? '✅' : '❌'}`);
  console.log(`   Has ignore button (🔕): ${hasIgnoreButtonBefore ? '❌ (should not)' : '✅'}`);
  
  // Click the unignore button
  const unignoreButton = ignoredTodo.locator('button.occ-unignore').first();
  console.log('\n🖱️  Clicking unignore button...');
  await unignoreButton.click();
  
  // Wait for response
  console.log('\n⏱️  Waiting 2 seconds for response and DOM update...');
  await page.waitForTimeout(2000);
  
  // Check if visual indicators updated
  console.log('\n📋 After unignore:');
  
  const hasIgnoredLabelAfter = await ignoredTodo.locator('.meta:has-text("(ignored)")').count() > 0;
  console.log(`   Has "(ignored)" label: ${hasIgnoredLabelAfter ? '❌ (should be gone)' : '✅ (correctly removed)'}`);
  
  const hasUnignoreButtonAfter = await ignoredTodo.locator('button.occ-unignore').count() > 0;
  console.log(`   Has unignore button (↩️): ${hasUnignoreButtonAfter ? '❌ (should be gone)' : '✅ (correctly removed)'}`);
  
  const hasIgnoreButtonAfter = await ignoredTodo.locator('button.occ-ignore-occ').count() > 0;
  console.log(`   Has ignore button (🔕): ${hasIgnoreButtonAfter ? '✅ (correctly restored)' : '❌ (should be present)'}`);
  
  const hasIgnoreFromButtonAfter = await ignoredTodo.locator('button.occ-ignore-from').count() > 0;
  console.log(`   Has ignore-from button (⏭️): ${hasIgnoreFromButtonAfter ? '✅ (correctly restored)' : '❌ (should be present)'}`);
  
  // Results
  console.log('\n' + '='.repeat(60));
  if (!hasIgnoredLabelAfter && !hasUnignoreButtonAfter && hasIgnoreButtonAfter && hasIgnoreFromButtonAfter) {
    console.log('✅ SUCCESS: All visual indicators updated correctly!');
  } else {
    console.log('❌ FAILED: Some visual indicators did not update');
    if (hasIgnoredLabelAfter) console.log('   - "(ignored)" text still present');
    if (hasUnignoreButtonAfter) console.log('   - Unignore button still present');
    if (!hasIgnoreButtonAfter) console.log('   - Ignore button not restored');
    if (!hasIgnoreFromButtonAfter) console.log('   - Ignore-from button not restored');
  }
  console.log('='.repeat(60));
  
  await page.screenshot({ path: 'test-results/cdp-unignore-test.png', fullPage: true });
  console.log('\n📸 Screenshot saved to test-results/cdp-unignore-test.png');
  
  await browser.close();
});
