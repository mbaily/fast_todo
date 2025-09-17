from pathlib import Path
p = Path('app/main.py')
text = p.read_text()
lines = text.splitlines()
start = 9985
end = 10040
for i in range(start-1, min(end, len(lines))):
    s = lines[i]
    lead = s[:len(s)-len(s.lstrip())]
    print(f"{i+1:5d}: lead_len={len(lead):2d} lead={repr(lead)} line={repr(s.lstrip())}")
