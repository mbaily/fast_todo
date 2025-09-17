#!/usr/bin/env python3
import httpx
from bs4 import BeautifulSoup
import sys

BASE='https://0.0.0.0:10443'
LOGIN_PATH='/html_no_js/login'
TARGETS=['/html_no_js/list.html?list_id=190','/html_no_js/list/190','/html_no_js/?list_id=190','/html_no_js/']

client = httpx.Client(verify=False, follow_redirects=False)

print('=== POST login (no follow) ===')
resp = client.post(BASE+LOGIN_PATH, data={'username':'mbaily','password':'mypass'})
print('login status', resp.status_code)
print('headers:')
for k in ('location','set-cookie','content-type'):
    if k in resp.headers:
        print(' ',k,':', resp.headers[k])
if 'set-cookie' in resp.headers:
    print('raw set-cookie header(s):')
    for v in resp.headers.get_list('set-cookie'):
        print('  -', v)

# manual follow if redirect
if resp.status_code in (301,302,303,307,308) and 'location' in resp.headers:
    loc = resp.headers['location']
    print('redirect to', loc)
    # perform a GET to the redirect location using same client (cookies applied)
    r2 = client.get(BASE+loc if loc.startswith('/') else loc, follow_redirects=False)
    print('redirect GET', r2.status_code)
    # print any set-cookie
    if 'set-cookie' in r2.headers:
        for v in r2.headers.get_list('set-cookie'):
            print('  set-cookie:', v)

print('\n=== Cookies in client ===')
for c in client.cookies.jar:
    print(' ',c)

# try fetching each target, allow follow redirects for these
for t in TARGETS:
    url = BASE + t
    print('\n--- GET', url)
    r = client.get(url, follow_redirects=True)
    print('status', r.status_code)
    fname = 'e2e/screenshots/diag-' + t.replace('/','_').replace('?','_') + '.html'
    open(fname, 'w').write(r.text)
    print('wrote', fname, 'len', len(r.text))
    # parse
    soup = BeautifulSoup(r.text, 'html.parser')
    overrides = soup.select('.priority-override')
    print('overrides found:', len(overrides))
    if overrides:
        for o in overrides[:5]:
            print('  override text:', o.get_text(strip=True))
    # look for link to list 193
    links = soup.select('a[href*="list_id=193"], a[href*="/list/193"]')
    print('links to 193:', [a.get('href') for a in links])

print('\nDone')
client.close()
