#!/usr/bin/env python3
"""
CDP micro-driver: Find and display a specific calendar event by title pattern.
Saves the event details to a file for other scripts to use.

Usage: python scripts/cdp_find_event.py "Water plants"
"""
import asyncio
import sys
import json
from playwright.async_api import async_playwright

async def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/cdp_find_event.py <title_pattern>")
        sys.exit(1)
    
    search_pattern = sys.argv[1]
    
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        contexts = browser.contexts
        
        if not contexts:
            print("❌ No browser contexts found")
            sys.exit(1)
        
        context = contexts[0]
        pages = context.pages
        
        if not pages:
            print("❌ No pages found")
            sys.exit(1)
        
        # Find calendar page
        page = None
        for pg in pages:
            if 'calendar' in pg.url:
                page = pg
                break
        
        if not page:
            page = pages[0]
            await page.goto('https://0.0.0.0:10443/html_no_js/calendar')
            await page.wait_for_load_state('networkidle')
        
        print(f"🔍 Searching for event matching: '{search_pattern}'")
        
        # Wait for todos
        await page.wait_for_selector('.todo', timeout=10000)
        
        # Get all todos
        todos = await page.locator('.todo').all()
        
        # Find matching todo
        found = None
        for todo in todos:
            title_el = todo.locator('.todo-main .wrap-text').first()
            title = await title_el.text_content()
            title = title.strip() if title else ''
            
            if search_pattern.lower() in title.lower():
                occ_id = await todo.get_attribute('data-occ-id')
                meta_el = todo.locator('.meta').first()
                meta = await meta_el.text_content()
                
                # Get button info
                ignore_btn = todo.locator('button.occ-ignore-occ').first()
                item_id = await ignore_btn.get_attribute('data-item-id') if await ignore_btn.count() > 0 else None
                
                found = {
                    'title': title,
                    'occ_id': occ_id,
                    'item_id': item_id,
                    'meta': meta.strip() if meta else '',
                    'search_pattern': search_pattern
                }
                break
        
        if not found:
            print(f"❌ No event found matching '{search_pattern}'")
            sys.exit(1)
        
        # Save to file
        with open('.cdp_event.json', 'w') as f:
            json.dump(found, f, indent=2)
        
        print(f"✅ Found event:")
        print(f"   Title:   {found['title']}")
        print(f"   OccID:   {found['occ_id']}")
        print(f"   ItemID:  {found['item_id']}")
        print(f"   Meta:    {found['meta']}")
        print(f"\n📁 Saved to .cdp_event.json")
        
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
