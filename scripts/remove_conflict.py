#!/usr/bin/env python3
import os
from pathlib import Path

TARGET = Path('scripts/test_recurrence_parsing.py')

def main():
    if TARGET.exists():
        try:
            TARGET.unlink()
            print('Removed', TARGET)
        except Exception as e:
            print('Failed to remove', TARGET, e)
    else:
        print('No conflicting file found:', TARGET)

if __name__ == '__main__':
    main()
