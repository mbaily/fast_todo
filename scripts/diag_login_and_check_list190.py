#!/usr/bin/env python3
import httpx
from bs4 import BeautifulSoup

BASE='https://0.0.0.0:10443'
LOGIN_PATH='/html_no_js/login'
LIST_PATH='/html_no_js/list.html?list_id=190'

s = httpx.Client(verify=False, follow_redirects=True)
print('Posting login...')
resp = s.post(BASE+LOGIN_PATH, data={'username':'mbaily','password':'mypass'})
print('Status:', resp.status_code)
print('History:', [r.status_code for r in resp.history])
print('Cookies after login:', s.cookies.jar)

print('Fetching list page...')
resp2 = s.get(BASE+LIST_PATH)
print('List status:', resp2.status_code)
html = resp2.text
open('e2e/screenshots/list-190-raw.html','w').write(html)

soup = BeautifulSoup(html, 'html.parser')
# find sublist elements that include .priority-override
overrides = soup.select('.sublists .priority-override, .list .priority-override, .priority-override')
print('Found override elements count:', len(overrides))
for i,el in enumerate(overrides[:20],1):
    print(i, el.get_text(strip=True), 'parent:', el.find_parent().get('class'))

# specifically search for sublist with id=193 or link to list_id=193
links = soup.select('a[href*="list_id=193"], a[href*="/list/193"]')
print('Links to list 193:', [a.get('href') for a in links])

# look for a list row that contains 'test_list' or 'id=193'
if 'test_list' in html:
    print('Found name test_list in page')

print('Done')
