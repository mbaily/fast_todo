#!/usr/bin/env python3
import httpx

BASE='https://0.0.0.0:10443'
LOGIN_PATH='/html_no_js/login'

client = httpx.Client(verify=False, follow_redirects=False)
print('Posting to', BASE+LOGIN_PATH)
r = client.post(BASE+LOGIN_PATH, data={'username':'mbaily','password':'mypass'})
print('Status:', r.status_code)
print('\nHeaders:')
for k,v in r.headers.items():
    if k.lower().startswith('set-cookie') or k.lower() in ('location','content-type'):
        print(f"  {k}: {v}")

body = r.text or ''
open('e2e/screenshots/login-post-body.html','w').write(body)
print('\nWrote e2e/screenshots/login-post-body.html (len=%d)'%len(body))

print('\nFirst 800 chars of body:\n')
print(body[:800])
client.close()
