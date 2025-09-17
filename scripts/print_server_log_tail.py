from pathlib import Path
p = Path('server.log')
if not p.exists():
    print('server.log not found')
    raise SystemExit(1)
lines = p.read_text().splitlines()
for ln in lines[-200:]:
    print(ln)
