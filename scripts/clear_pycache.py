#!/usr/bin/env python3
import pathlib

def main():
    p = pathlib.Path(__file__).resolve().parent / '__pycache__'
    if p.exists():
        removed = 0
        for f in p.iterdir():
            name = f.name
            if name.startswith('test_recurrence_parsing') and name.endswith('.pyc'):
                try:
                    f.unlink()
                    removed += 1
                    print('Deleted', f)
                except Exception as e:
                    print('Could not delete', f, e)
        print('Done, removed', removed)
    else:
        print('No __pycache__ to clean')

if __name__ == '__main__':
    main()
