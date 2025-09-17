#!/usr/bin/env python3
import httpx
from bs4 import BeautifulSoup
BASE='https://0.0.0.0:10443'
client = httpx.Client(verify=False, follow_redirects=True)

resp = client.post(BASE+'/html_no_js/login', data={'username':'mbaily','password':'mypass'})
print('login status', resp.status_code)

r = client.get(BASE+'/html_no_js/')
print('GET /html_no_js/ status', r.status_code)

fname = 'e2e/screenshots/index.html'
open(fname, 'w').write(r.text)
print('wrote', fname, 'len', len(r.text))

soup = BeautifulSoup(r.text, 'html.parser')
ov = [o.get('data-override-priority-debug') for o in soup.select('li.list-item')]
print('list-item debug attrs count', len([x for x in ov if x is not None]))
for i, v in enumerate([x for x in ov if x is not None][:20]):
    print(' debug', i, v)

client.close()
