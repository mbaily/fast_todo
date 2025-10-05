#!/usr/bin/env python3
"""
CDP micro-driver: Check the visual state of a previously found event.
Reads event details from .cdp_event.json and checks if it has correct buttons/indicators.

Usage: python scripts/cdp_check_event.py
"""
import asyncio
import json
import os
from playwright.async_api import async_playwright

async def main():
    # Load event details
    if not os.path.exists('.cdp_event.json'):
        print("❌ No .cdp_event.json found. Run cdp_find_event.py first.")
        exit(1)
    
    with open('.cdp_event.json', 'r') as f:
        event = json.load(f)
    
    print(f"🔍 Checking event: {event['title']}")
    print(f"   OccID:  {event['occ_id']}")
    
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        contexts = browser.contexts
        
        if not contexts:
            print("❌ No browser contexts found")
            exit(1)
        
        context = contexts[0]
        pages = context.pages
        
        if not pages:
            print("❌ No pages found")
            exit(1)
        
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
        
        # Wait for todos
        await page.wait_for_selector('.todo', timeout=10000)
        
        # Find the specific todo by occ_id
        todo_selector = f'li.todo[data-occ-id="{event["occ_id"]}"]'
        todo = page.locator(todo_selector).first()
        
        if await todo.count() == 0:
            print(f"❌ Todo with occ_id {event['occ_id']} not found on page")
            print(f"   Event may have been filtered out (not ignored when show_ignored=off)")
            await browser.close()
            exit(1)
        
        print(f"\n✅ Event found on page")
        
        # Check visual indicators
        print(f"\n📋 Visual indicators:")
        
        # Check for buttons
        has_ignore_btn = await todo.locator('button.occ-ignore-occ').count() > 0
        has_ignore_from_btn = await todo.locator('button.occ-ignore-from').count() > 0
        has_unignore_btn = await todo.locator('button.occ-unignore').count() > 0
        
        print(f"   Ignore button (🔕):      {'✅ YES' if has_ignore_btn else '❌ NO'}")
        print(f"   Ignore-from button (⏭️): {'✅ YES' if has_ignore_from_btn else '❌ NO'}")
        print(f"   Unignore button (↩️):    {'✅ YES' if has_unignore_btn else '❌ NO'}")
        
        # Check for (ignored) text in meta
        meta_el = todo.locator('.meta').first()
        meta_text = await meta_el.text_content()
        meta_text = meta_text.strip() if meta_text else ''
        has_ignored_text = '(ignored)' in meta_text
        
        print(f"   '(ignored)' text:        {'✅ YES' if has_ignored_text else '❌ NO'}")
        print(f"   Meta text: {meta_text}")
        
        # Determine state
        print(f"\n🎯 Event state:")
        if has_ignored_text and has_unignore_btn and not has_ignore_btn:
            print(f"   ✅ IGNORED (correct indicators)")
        elif not has_ignored_text and has_ignore_btn and not has_unignore_btn:
            print(f"   ✅ NOT IGNORED (correct indicators)")
        elif has_ignored_text and not has_unignore_btn:
            print(f"   ⚠️  IGNORED but missing unignore button")
        elif not has_ignored_text and has_unignore_btn:
            print(f"   ⚠️  NOT ignored but has unignore button")
        elif has_ignore_btn and has_unignore_btn:
            print(f"   ❌ BOTH ignore and unignore buttons present (BUG!)")
        else:
            print(f"   ⚠️  UNKNOWN state")
        
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
