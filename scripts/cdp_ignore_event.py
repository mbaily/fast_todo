#!/usr/bin/env python3
"""
CDP micro-driver: Click the ignore button on a previously found event.
Reads event details from .cdp_event.json

Usage: python scripts/cdp_ignore_event.py
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
    
    print(f"🎯 Ignoring event: {event['title']}")
    print(f"   OccID:  {event['occ_id']}")
    print(f"   ItemID: {event['item_id']}")
    
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
        
        # Listen for console logs
        console_logs = []
        def log_handler(msg):
            text = msg.text
            console_logs.append(text)
            if 'DEBUG:' in text:
                print(f"  [Console] {text}")
        
        page.on('console', log_handler)
        
        # Wait for todos
        await page.wait_for_selector('.todo', timeout=10000)
        
        # Find the specific todo by occ_id
        todo_selector = f'li.todo[data-occ-id="{event["occ_id"]}"]'
        todo = page.locator(todo_selector).first()
        
        if await todo.count() == 0:
            print(f"❌ Todo with occ_id {event['occ_id']} not found on page")
            await browser.close()
            exit(1)
        
        # Find and click the ignore button
        ignore_btn = todo.locator('button.occ-ignore-occ').first()
        
        if await ignore_btn.count() == 0:
            print(f"⚠️  No ignore button found - event may already be ignored")
            await browser.close()
            exit(1)
        
        print(f"\n🖱️  Clicking ignore button...")
        await ignore_btn.click()
        
        # Wait for response
        print(f"⏱️  Waiting 2 seconds for response...")
        await asyncio.sleep(2)
        
        # Count console logs
        debug_logs = [log for log in console_logs if 'DEBUG:' in log]
        print(f"\n📝 Captured {len(debug_logs)} DEBUG console logs")
        
        # Check if fetchOccurrences was called
        fetch_called = any('fetchOccurrencesForCurrentWindow called' in log for log in console_logs)
        print(f"   fetchOccurrences called: {'✅ YES' if fetch_called else '❌ NO'}")
        
        # Check for response
        response_logs = [log for log in console_logs if 'ignore_response' in log or 'calendar_ignore_response' in log]
        print(f"   Server responses: {len(response_logs)}")
        
        print(f"\n✅ Ignore button clicked")
        
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
