from pathlib import Path
import sys
p = Path('app/main.py')
if not p.exists():
    print('file not found:', p)
    sys.exit(1)
lines = p.read_text().splitlines()
# print a larger window around the reported syntax error
start = 9900
end = 10120
for i in range(start-1, min(end, len(lines))):
    print(f"{i+1:6d}: {lines[i]}")
