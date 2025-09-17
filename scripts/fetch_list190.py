#!/usr/bin/env python3
import httpx
from bs4 import BeautifulSoup
BASE='https://0.0.0.0:10443'
client = httpx.Client(verify=False, follow_redirects=True)

resp = client.post(BASE+'/html_no_js/login', data={'username':'mbaily','password':'mypass'})
print('login status', resp.status_code)

r = client.get(BASE+'/html_no_js/lists/190')
print('GET /html_no_js/lists/190 status', r.status_code)

fname = 'e2e/screenshots/list-190-dedicated.html'
open(fname, 'w').write(r.text)
print('wrote', fname, 'len', len(r.text))

soup = BeautifulSoup(r.text, 'html.parser')
ov = [o.get_text(strip=True) for o in soup.select('.priority-override')]
print('overrides count', len(ov))
for o in ov[:10]:
    print(' override text:', o)

links = [a.get('href') for a in soup.select('a[href*="/lists/193"], a[href*="list_id=193"]')]
print('links to 193:', links)

client.close()
