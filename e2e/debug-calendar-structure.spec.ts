import { test, expect } from '@playwright/test';

test('Debug calendar page structure', async ({ page }) => {
  // Navigate to calendar
  await page.goto('https://0.0.0.0:10443/html_no_js/calendar');
  
  // Wait for page to load
  await page.waitForLoadState('networkidle');
  
  // Take a screenshot
  await page.screenshot({ path: 'test-results/calendar-debug.png', fullPage: true });
  
  // Log the page HTML
  const html = await page.content();
  console.log('Page HTML length:', html.length);
  
  // Check what elements exist
  const listElements = await page.locator('li').count();
  console.log('Number of <li> elements:', listElements);
  
  const todoElements = await page.locator('[class*="todo"]').count();
  console.log('Number of elements with "todo" in class:', todoElements);
  
  const buttonElements = await page.locator('button').count();
  console.log('Number of <button> elements:', buttonElements);
  
  // Check if there's any calendar content
  const calendarContent = await page.locator('#calendar-content, #calendar, [id*="calendar"]').count();
  console.log('Calendar content elements:', calendarContent);
  
  // Log page title
  const title = await page.title();
  console.log('Page title:', title);
  
  // Log all classes used
  const allClasses = await page.evaluate(() => {
    const classes = new Set<string>();
    document.querySelectorAll('*').forEach(el => {
      el.classList.forEach(cls => classes.add(cls));
    });
    return Array.from(classes).sort();
  });
  console.log('All CSS classes on page:', allClasses.slice(0, 50).join(', '));
});
