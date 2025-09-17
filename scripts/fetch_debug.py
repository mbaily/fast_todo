#!/usr/bin/env python3
import httpx
import json
BASE='https://0.0.0.0:10443'
client=httpx.Client(verify=False)
# login
r = client.post(BASE + '/html_no_js/login', data={'username':'mbaily','password':'mypass'})
print('login', r.status_code)
# fetch debug
r = client.get(BASE + '/client-debug/lists/190/sublists')
print('status', r.status_code)
try:
    print(json.dumps(r.json(), indent=2)[:1000])
except Exception:
    print(r.text[:1000])
client.close()
