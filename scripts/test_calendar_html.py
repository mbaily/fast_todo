#!/usr/bin/env python3
"""
Test the calendar HTML page rendering to check for JavaScript errors.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__) + '/..')

import asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app

async def test_calendar_page():
    """Test loading the calendar page and check for errors."""
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First, check if we can reach the calendar endpoint
        print("Testing calendar API endpoint...")
        response = await client.get(
            "/calendar/occurrences",
            params={
                "start": "2025-10-01T00:00:00Z",
                "end": "2025-10-31T23:59:59Z"
            },
            cookies={"session": "test"},  # Mock session
            follow_redirects=False
        )
        
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"✓ API returned {len(data.get('occurrences', []))} occurrences")
            
            # Check structure
            if data.get('occurrences'):
                sample = data['occurrences'][0]
                print(f"\nSample occurrence structure:")
                for key in sample.keys():
                    print(f"  - {key}: {type(sample[key]).__name__}")
                    
                # Check for Phase 2 fields
                has_occ_id = 'occ_id' in sample
                has_occ_hash = 'occ_hash' in sample
                print(f"\nPhase 2 check:")
                print(f"  occ_id present: {has_occ_id}")
                print(f"  occ_hash present: {has_occ_hash}")
                print(f"  occ_hash value: {sample.get('occ_hash')}")
        else:
            print(f"❌ API call failed")
            print(f"Response: {response.text[:500]}")
        
        # Now test the HTML page
        print("\n\nTesting calendar HTML page...")
        response = await client.get(
            "/html_no_js/calendar",
            cookies={"session": "test"},
            follow_redirects=False
        )
        
        print(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            html = response.text
            print(f"✓ HTML page loaded ({len(html)} bytes)")
            
            # Check for key elements
            has_todos_list = 'class="todos-list"' in html
            has_occ_complete = 'class="occ-complete"' in html
            has_data_occ_id = 'data-occ-id=' in html
            has_data_hash = 'data-hash=' in html
            
            print(f"\nHTML structure:")
            print(f"  todos-list class: {has_todos_list}")
            print(f"  occ-complete checkboxes: {has_occ_complete}")
            print(f"  data-occ-id attributes: {has_data_occ_id}")
            print(f"  data-hash attributes: {has_data_hash}")
            
            # Check for JavaScript
            has_fetch_function = 'fetchOccurrencesForCurrentWindow' in html
            has_event_listener = "addEventListener('change'" in html
            
            print(f"\nJavaScript:")
            print(f"  fetchOccurrencesForCurrentWindow: {has_fetch_function}")
            print(f"  change event listener: {has_event_listener}")
            
            # Look for potential issues
            if 'data-occ-id=""' in html:
                print(f"\n⚠️  WARNING: Found empty data-occ-id attributes!")
            
            if not has_data_occ_id and not has_data_hash:
                print(f"\n⚠️  WARNING: No data-occ-id or data-hash attributes found!")
                print("This could mean occurrences aren't being rendered.")
                
        else:
            print(f"❌ HTML page failed to load")
            print(f"Response: {response.text[:500]}")

if __name__ == '__main__':
    asyncio.run(test_calendar_page())
