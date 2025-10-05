#!/usr/bin/env python3
"""
Fetch the actual rendered HTML and check lines 1301 and 1764.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__) + '/..')

import asyncio
from httpx import AsyncClient
from app.main import app

async def check_rendered_html():
    """Get the rendered HTML and check problem lines."""
    
    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
        # Try to get the calendar page
        # First, let's try without auth to see what happens
        response = await client.get("/html_no_js/calendar")
        
        print(f"Status: {response.status_code}")
        
        if response.status_code == 307 or response.status_code == 302:
            print(f"⚠️  Redirected to: {response.headers.get('location')}")
            print("Authentication required - calendar page needs login\n")
            return
        
        if response.status_code == 200:
            html = response.text
            lines = html.split('\n')
            
            print(f"✓ Got HTML: {len(lines)} lines, {len(html)} bytes\n")
            
            # Check line 1301
            if len(lines) >= 1301:
                print(f"Line 1301 (problem line 1):")
                print(f"  {lines[1300][:200]}")  # 0-indexed
                
                # Show context
                print(f"\nContext around line 1301:")
                for i in range(max(0, 1298), min(len(lines), 1304)):
                    marker = ">>> " if i == 1300 else "    "
                    print(f"{marker}{i+1}: {lines[i][:150]}")
            else:
                print(f"⚠️  HTML only has {len(lines)} lines, line 1301 doesn't exist")
            
            print(f"\n{'='*80}\n")
            
            # Check line 1764
            if len(lines) >= 1764:
                print(f"Line 1764 (problem line 2):")
                print(f"  {lines[1763][:200]}")  # 0-indexed
                
                # Show context
                print(f"\nContext around line 1764:")
                for i in range(max(0, 1761), min(len(lines), 1767)):
                    marker = ">>> " if i == 1763 else "    "
                    print(f"{marker}{i+1}: {lines[i][:150]}")
            else:
                print(f"⚠️  HTML only has {len(lines)} lines, line 1764 doesn't exist")
            
            # Check for common issues
            print(f"\n{'='*80}\n")
            print("Checking for common issues:")
            
            # Look for template syntax in JS
            if '{{' in html and '<script>' in html:
                print("  ⚠️  Found both {{ and <script> - potential template in JS")
            
            # Look for malformed script tags
            script_tags = html.count('<script>')
            script_close = html.count('</script>')
            print(f"  Script tags: {script_tags} open, {script_close} close")
            
            if script_tags != script_close:
                print("  ❌ Mismatched script tags!")
            
            # Check for inline styles that might break JS
            if '<style>' in html and '</script>' in html:
                # Check if style comes after script incorrectly
                first_script_end = html.find('</script>')
                first_style = html.find('<style>')
                if first_style > 0 and first_script_end > 0:
                    if first_style < first_script_end:
                        print("  ⚠️  Style tag appears before script end")

if __name__ == '__main__':
    asyncio.run(check_rendered_html())
