import py_compile, traceback, sys

path = 'app/main.py'
try:
    py_compile.compile(path, doraise=True)
    print('OK: {} compiles'.format(path))
    sys.exit(0)
except Exception as e:
    print('Compilation failed:', type(e).__name__)
    traceback.print_exc()
    # if SyntaxError, print nearby source lines
    if isinstance(e, SyntaxError):
        fn = e.filename or path
        lineno = e.lineno or 0
        try:
            with open(fn, 'r') as f:
                lines = f.readlines()
        except Exception:
            lines = []
        start = max(0, lineno - 5)
        end = min(len(lines), lineno + 4)
        print('\nContext around error:')
        for i in range(start, end):
            mark = '->' if (i + 1) == lineno else '  '
            print(f"{mark} {i+1:5d}: {lines[i].rstrip()}")
    sys.exit(2)
