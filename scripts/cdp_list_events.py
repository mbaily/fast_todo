#!/usr/bin/env python3
"""
CDP driver to list all calendar events with their details.
Usage: python scripts/cdp_list_events.py
"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        # Connect to Chrome with remote debugging on port 9222
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        contexts = browser.contexts
        
        if not contexts:
            print("❌ No browser contexts found")
            return
        
        context = contexts[0]
        pages = context.pages
        
        if not pages:
            print("❌ No pages found")
            return
        
        # Find calendar page
        page = None
        for p in pages:
            if 'calendar' in p.url:
                page = p
                break
        
        if not page:
            print("⚠️  No calendar page found, using first page")
            page = pages[0]
            await page.goto('https://0.0.0.0:10443/html_no_js/calendar')
            await page.wait_for_load_state('networkidle')
        
        print(f"📄 Page: {page.url}\n")
        
        # Wait for todos to load
        await page.wait_for_selector('.todo', timeout=10000)
        
        # Get all todos
        todos = await page.locator('.todo').all()
        
        print(f"📊 Found {len(todos)} calendar events\n")
        print("=" * 80)
        
        for i, todo in enumerate(todos, 1):
            # Get occ_id
            occ_id = await todo.get_attribute('data-occ-id')
            
            # Get title
            title_el = todo.locator('.todo-main .wrap-text').first()
            title = await title_el.text_content()
            title = title.strip() if title else 'N/A'
            
            # Get date/meta info
            meta_el = todo.locator('.meta').first()
            meta = await meta_el.text_content()
            meta = meta.strip() if meta else 'N/A'
            
            # Check for buttons
            has_ignore_btn = await todo.locator('button.occ-ignore-occ').count() > 0
            has_ignore_from_btn = await todo.locator('button.occ-ignore-from').count() > 0
            has_unignore_btn = await todo.locator('button.occ-unignore').count() > 0
            
            # Check for ignored text
            is_ignored_text = '(ignored)' in meta
            
            # Status indicators
            status = []
            if is_ignored_text:
                status.append('IGNORED')
            if has_unignore_btn:
                status.append('has ↩️')
            if has_ignore_btn:
                status.append('has 🔕')
            if has_ignore_from_btn:
                status.append('has ⏭️')
            
            status_str = ' '.join(status) if status else 'normal'
            
            print(f"{i:3}. [{status_str:30}]")
            print(f"     Title: {title[:60]}")
            print(f"     Date:  {meta}")
            print(f"     OccID: {occ_id}")
            print()
        
        print("=" * 80)
        
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
