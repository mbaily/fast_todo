#!/usr/bin/env python3
"""
Test what the calendar API endpoint actually returns for an authenticated request.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__) + '/..')

import asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db import async_session
from app.models import User
from sqlmodel import select

async def _run_calendar_api():
    """Async implementation; wrapped by sync test function so we do not require pytest-asyncio."""
    
    # Get a real user
    async with async_session() as sess:
        result = await sess.exec(select(User).limit(1))
        user = result.first()
        
        if not user:
            print("❌ No users found!")
            return
            
        print(f"✓ Testing with user: {user.username} (id={user.id})\n")
    
    # httpx >=0.28 removed the 'app=' shortcut on AsyncClient; use an explicit ASGITransport.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test 1: API without auth
        print("Test 1: API endpoint without authentication")
        response = await client.get(
            "/calendar/occurrences",
            params={
                "start": "2025-10-01T00:00:00Z",
                "end": "2025-10-31T23:59:59Z"
            },
            follow_redirects=False
        )
        
        print(f"  Status: {response.status_code}")
        print(f"  Content-Type: {response.headers.get('content-type')}")
        
        if response.status_code == 307 or response.status_code == 302:
            print(f"  ⚠️  Redirected to: {response.headers.get('location')}")
            print("  This means authentication is required!")
        elif response.status_code == 200:
            if 'application/json' in response.headers.get('content-type', ''):
                data = response.json()
                print(f"  ✓ JSON returned: {len(data.get('occurrences', []))} occurrences")
            else:
                print(f"  ❌ Wrong content type! Got HTML instead of JSON")
                print(f"  First 200 chars: {response.text[:200]}")
        else:
            print(f"  ❌ Unexpected status")
            print(f"  Response: {response.text[:200]}")
        
        # Test 2: API with session cookie (simulate logged in user)
        print("\n\nTest 2: API endpoint with session cookie")
        
        # Create a session by logging in
        login_response = await client.post(
            "/token",
            data={
                "username": user.username,
                "password": "test"  # Assuming test password
            },
            follow_redirects=False
        )
        
        if login_response.status_code == 200:
            # Extract token if returned
            token_data = login_response.json()
            token = token_data.get('access_token')
            print(f"  ✓ Got token: {token[:20] if token else 'None'}...")
            
            # Try with Authorization header
            response = await client.get(
                "/calendar/occurrences",
                params={
                    "start": "2025-10-01T00:00:00Z",
                    "end": "2025-10-31T23:59:59Z"
                },
                headers={"Authorization": f"Bearer {token}"} if token else {},
                follow_redirects=False
            )
            
            print(f"  Status: {response.status_code}")
            print(f"  Content-Type: {response.headers.get('content-type')}")
            
            if response.status_code == 200:
                if 'application/json' in response.headers.get('content-type', ''):
                    data = response.json()
                    print(f"  ✓ JSON returned: {len(data.get('occurrences', []))} occurrences")
                    
                    # Check structure
                    if data.get('occurrences'):
                        sample = data['occurrences'][0]
                        has_occ_id = 'occ_id' in sample
                        has_occ_hash = 'occ_hash' in sample
                        print(f"  ✓ occ_id present: {has_occ_id}")
                        print(f"  ✓ occ_hash value: {sample.get('occ_hash')}")
                else:
                    print(f"  ❌ Wrong content type! Got HTML instead of JSON")
                    print(f"  First 200 chars: {response.text[:200]}")
            else:
                print(f"  ❌ Status: {response.status_code}")
                print(f"  Response: {response.text[:200]}")
        else:
            print(f"  ⚠️  Login failed (status {login_response.status_code})")
            print("  This is expected if password is wrong")
        
        # Test 3: Check if HTML calendar page includes inline occurrences
        print("\n\nTest 3: HTML calendar page (server-side rendering)")
        
        response = await client.get(
            "/html_no_js/calendar",
            headers={"Authorization": f"Bearer {token}"} if 'token' in locals() else {},
            follow_redirects=False
        )
        
        print(f"  Status: {response.status_code}")
        
        if response.status_code == 200:
            html = response.text
            
            # Check if occurrences are server-rendered
            has_checkboxes = 'class="occ-complete"' in html
            num_checkboxes = html.count('class="occ-complete"')
            has_data_occ_id = 'data-occ-id=' in html
            num_data_occ_id = html.count('data-occ-id=')
            
            print(f"  ✓ Checkboxes found: {num_checkboxes}")
            print(f"  ✓ data-occ-id attributes: {num_data_occ_id}")
            
            if num_checkboxes == 0:
                print(f"  ⚠️  No server-rendered occurrences!")
                print("  This means JS must fetch them client-side")
                print("  If JS fetch fails, calendar will be empty!")
            else:
                print(f"  ✓ Server-rendered occurrences present")
        else:
            print(f"  ❌ Status: {response.status_code}")

def test_calendar_api():
    """Pytest entrypoint: run the async routine in an event loop.

    This avoids requiring pytest-asyncio or anyio plugin while still exercising
    the async client code. If a plugin is later added, this can be converted
    back to an async test with @pytest.mark.asyncio.
    """
    asyncio.run(_run_calendar_api())

if __name__ == '__main__':  # manual execution
    asyncio.run(_run_calendar_api())
