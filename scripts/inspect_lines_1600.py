from pathlib import Path
p = Path('app/main.py')
if not p.exists():
    print('file not found')
    raise SystemExit(1)
lines = p.read_text().splitlines()
start = 1595
end = 1615
for i in range(start-1, min(end, len(lines))):
    print(f"{i+1:5d}: {lines[i]}")
