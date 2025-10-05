import { test, expect, chromium } from '@playwright/test';

/**
 * Thorough test to verify ignore button updates the CORRECT todo's visual indicators.
 * Tests that clicking ignore on todo A doesn't affect todo B.
 */

test('Verify ignore button updates correct todo only', async () => {
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
  console.log('Reloading calendar page...');
  await page.goto('https://0.0.0.0:10443/html_no_js/calendar', { waitUntil: 'networkidle' });
  
  // Ensure "Show ignored" checkbox is CHECKED
  const showIgnoredCheckbox = page.locator('input#show_ignored');
  const isChecked = await showIgnoredCheckbox.isChecked();
  
  if (!isChecked) {
    await showIgnoredCheckbox.check();
    await page.waitForTimeout(1000);
  }
  
  // Wait for todos to load
  await page.waitForSelector('.todo', { timeout: 10000 });
  
  // Find TWO different non-ignored todos
  const allTodos = await page.locator('.todo').all();
  const testTodos: { element: any; title: string; occId: string; }[] = [];
  
  for (const todo of allTodos) {
    const title = await todo.locator('.todo-main .wrap-text').first().textContent();
    const occId = await todo.getAttribute('data-occ-id');
    
    if (title && occId && !title.includes('(ignored)')) {
      testTodos.push({ element: todo, title, occId });
      
      if (testTodos.length === 2) {
        break;
      }
    }
  }
  
  if (testTodos.length < 2) {
    throw new Error('Need at least 2 non-ignored todos for this test');
  }
  
  const todoA = testTodos[0];
  const todoB = testTodos[1];
  
  console.log('\n🎯 Test Setup:');
  console.log(`   Todo A: "${todoA.title}" (occ_id: ${todoA.occId})`);
  console.log(`   Todo B: "${todoB.title}" (occ_id: ${todoB.occId})`);
  
  // Capture console logs
  const consoleLogs: string[] = [];
  page.on('console', (msg: any) => {
    const text = msg.text();
    consoleLogs.push(text);
    if (text.includes('DEBUG:')) {
      console.log(`  [Console] ${text}`);
    }
  });
  
  // Check initial state of both todos
  console.log('\n📋 Initial state:');
  
  const todoAHasIgnoreBtnBefore = await todoA.element.locator('button.occ-ignore-occ').count() > 0;
  const todoAHasUnignoreBtnBefore = await todoA.element.locator('button.occ-unignore').count() > 0;
  const todoAHasIgnoredTextBefore = await todoA.element.locator('.meta:has-text("(ignored)")').count() > 0;
  
  console.log(`   Todo A: ignore=${todoAHasIgnoreBtnBefore} unignore=${todoAHasUnignoreBtnBefore} text=${todoAHasIgnoredTextBefore}`);
  
  const todoBHasIgnoreBtnBefore = await todoB.element.locator('button.occ-ignore-occ').count() > 0;
  const todoBHasUnignoreBtnBefore = await todoB.element.locator('button.occ-unignore').count() > 0;
  const todoBHasIgnoredTextBefore = await todoB.element.locator('.meta:has-text("(ignored)")').count() > 0;
  
  console.log(`   Todo B: ignore=${todoBHasIgnoreBtnBefore} unignore=${todoBHasUnignoreBtnBefore} text=${todoBHasIgnoredTextBefore}`);
  
  // Click ignore button on Todo A ONLY
  const ignoreButtonA = todoA.element.locator('button.occ-ignore-occ').first();
  const ignoreButtonAItemId = await ignoreButtonA.getAttribute('data-item-id');
  
  console.log(`\n🖱️  Clicking ignore button on Todo A (item_id: ${ignoreButtonAItemId})...`);
  await ignoreButtonA.click();
  
  // Wait for update
  await page.waitForTimeout(2000);
  
  // Check final state of BOTH todos
  console.log('\n📋 After clicking ignore on Todo A:');
  
  const todoAHasIgnoreBtnAfter = await todoA.element.locator('button.occ-ignore-occ').count() > 0;
  const todoAHasUnignoreBtnAfter = await todoA.element.locator('button.occ-unignore').count() > 0;
  const todoAHasIgnoredTextAfter = await todoA.element.locator('.meta:has-text("(ignored)")').count() > 0;
  
  console.log(`   Todo A: ignore=${todoAHasIgnoreBtnAfter} unignore=${todoAHasUnignoreBtnAfter} text=${todoAHasIgnoredTextAfter}`);
  
  const todoBHasIgnoreBtnAfter = await todoB.element.locator('button.occ-ignore-occ').count() > 0;
  const todoBHasUnignoreBtnAfter = await todoB.element.locator('button.occ-unignore').count() > 0;
  const todoBHasIgnoredTextAfter = await todoB.element.locator('.meta:has-text("(ignored)")').count() > 0;
  
  console.log(`   Todo B: ignore=${todoBHasIgnoreBtnAfter} unignore=${todoBHasUnignoreBtnAfter} text=${todoBHasIgnoredTextAfter}`);
  
  // Check for in-place updates in console logs
  const todoAUpdates = consoleLogs.filter(log => 
    log.includes('in-place update') && log.includes(todoA.occId)
  );
  const todoBUpdates = consoleLogs.filter(log => 
    log.includes('in-place update') && log.includes(todoB.occId)
  );
  
  console.log(`\n🔍 Debug log analysis:`);
  console.log(`   Updates mentioning Todo A (${todoA.occId}): ${todoAUpdates.length}`);
  console.log(`   Updates mentioning Todo B (${todoB.occId}): ${todoBUpdates.length}`);
  
  // Verify Todo A was updated correctly (should be ignored now)
  console.log('\n' + '='.repeat(60));
  
  let allCorrect = true;
  
  if (!todoAHasIgnoredTextAfter) {
    console.log('❌ Todo A missing "(ignored)" text');
    allCorrect = false;
  } else {
    console.log('✅ Todo A has "(ignored)" text');
  }
  
  if (!todoAHasUnignoreBtnAfter) {
    console.log('❌ Todo A missing unignore button');
    allCorrect = false;
  } else {
    console.log('✅ Todo A has unignore button');
  }
  
  if (todoAHasIgnoreBtnAfter) {
    console.log('❌ Todo A still has ignore button (should be gone)');
    allCorrect = false;
  } else {
    console.log('✅ Todo A ignore button removed');
  }
  
  // Verify Todo B was NOT affected (should remain unchanged)
  if (todoBHasIgnoreBtnAfter !== todoBHasIgnoreBtnBefore) {
    console.log('❌ Todo B ignore button state changed (should be unchanged)');
    allCorrect = false;
  } else {
    console.log('✅ Todo B ignore button unchanged');
  }
  
  if (todoBHasUnignoreBtnAfter !== todoBHasUnignoreBtnBefore) {
    console.log('❌ Todo B unignore button state changed (should be unchanged)');
    allCorrect = false;
  } else {
    console.log('✅ Todo B unignore button unchanged');
  }
  
  if (todoBHasIgnoredTextAfter !== todoBHasIgnoredTextBefore) {
    console.log('❌ Todo B "(ignored)" text changed (should be unchanged)');
    allCorrect = false;
  } else {
    console.log('✅ Todo B "(ignored)" text unchanged');
  }
  
  console.log('='.repeat(60));
  
  if (allCorrect) {
    console.log('\n✅ SUCCESS: Only Todo A was modified, Todo B remained unchanged!');
  } else {
    console.log('\n❌ FAILED: Wrong todo was modified or both todos were affected!');
  }
  
  await page.screenshot({ path: 'test-results/cdp-correct-todo-test.png', fullPage: true });
  
  await browser.close();
});
