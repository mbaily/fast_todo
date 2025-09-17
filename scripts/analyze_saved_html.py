#!/usr/bin/env python3
import json
from bs4 import BeautifulSoup
from pathlib import Path

paths = list(Path('e2e/screenshots').glob('*.html'))
report = {}
for p in sorted(paths):
    txt = p.read_text()
    soup = BeautifulSoup(txt, 'html.parser')
    report[p.name] = {
        'contains_invalid_credentials': bool(soup.find(string=lambda s: s and 'Invalid credentials' in s)),
        'contains_test_list': bool(soup.find(string=lambda s: s and 'test_list' in s)),
        'links_to_193': [a.get('href') for a in soup.select('a[href*="list_id=193"], a[href*="/list/193"]')],
        'priority_override_count': len(soup.select('.priority-override')),
    }

open('e2e/screenshots/analysis-report.json','w').write(json.dumps(report, indent=2))
print('Wrote e2e/screenshots/analysis-report.json')
print(json.dumps(report, indent=2))
