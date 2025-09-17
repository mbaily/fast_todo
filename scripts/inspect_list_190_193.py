from pathlib import Path
from bs4 import BeautifulSoup

html_path = Path('e2e/screenshots/list-190-dedicated.html')
if not html_path.exists():
    print('saved HTML not found:', html_path)
    raise SystemExit(1)

soup = BeautifulSoup(html_path.read_text(), 'html.parser')
# find list item with data-list-id=193
li = soup.select_one('li[data-list-id="193"]')
if li is None:
    # try to find a link to /lists/193 and print its parent
    a = soup.select_one('a[href*="/lists/193"]')
    if a is None:
        print('sublist 193 not found in HTML')
    else:
        parent = a.find_parent('li')
        if parent is None:
            print('link to /lists/193 found but no parent li; link snippet:')
            print(a.prettify())
        else:
            li = parent

if li is not None:
    print('Found element for sublist 193:')
    print('Tag:', li.name)
    print('Attributes:', dict(li.attrs))
    # print override badge(s) inside
    overrides = li.select('.priority-override')
    print('priority-override count:', len(overrides))
    for o in overrides:
        print(' override text:', o.get_text(strip=True))
        print(' override attrs:', dict(o.attrs))
    # print a short prettified snippet
    print('\nSnippet:')
    print(li.prettify()[:1000])
