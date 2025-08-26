"""Generate a file with many date/time strings in various languages and formats.

Writes one date/time candidate per line to a target file.

Usage: python tools/generate_date_samples.py --count 10000 --out data/date_samples.txt

This uses simple format templates across a curated set of languages. It's not
exhaustive but covers common month/day name variants and localized numeric
orders. The generator is deterministic unless --seed is provided.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta
import random
import os

LANG_TEMPLATES = {
    'en': [
        '{d} {mon} {Y}',
        '{mon} {d}, {Y}',
        '{Y}-{m:02d}-{d:02d}',
        '{d}/{m}/{Y}',
        '{d}.{m}.{Y}',
        '{d} {mon} {Y} {H}:{M}',
        'next {mon} {d}',
        '{d} {mon}',
    ],
    'fr': [
        '{d} {mon} {Y}',
        '{d}/{m}/{Y}',
        '{Y}-{m:02d}-{d:02d}',
        '{d} {mon} {Y} {H}h{M}',
    ],
    'es': [
        '{d} de {mon} de {Y}',
        '{d}/{m}/{Y}',
        '{Y}-{m:02d}-{d:02d}',
    ],
    'de': [
        '{d}. {mon} {Y}',
        '{d}.{m}.{Y}',
        '{Y}-{m:02d}-{d:02d}',
    ],
    'it': [
        '{d} {mon} {Y}',
        '{d}/{m}/{Y}',
    ],
    'nl': [
        '{d} {mon} {Y}',
        '{d}-{m}-{Y}',
    ],
    'pt': [
        '{d} de {mon} de {Y}',
        '{d}/{m}/{Y}',
    ],
    'sv': [
        '{Y}-{m:02d}-{d:02d}',
        '{d} {mon} {Y}',
    ],
    'ru': [
        '{d} {mon} {Y} г.',
        '{d}.{m}.{Y}',
    ],
    'ja': [
        '{Y}年{m}月{d}日',
        '{Y}/{m}/{d}',
    ],
    'zh': [
        '{Y}年{m}月{d}日',
        '{Y}/{m}/{d}',
    ],
}

MONTHS = {
    'en': ['January','February','March','April','May','June','July','August','September','October','November','December'],
    'fr': ['janvier','février','mars','avril','mai','juin','juillet','août','septembre','octobre','novembre','décembre'],
    'es': ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'],
    'de': ['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember'],
    'it': ['gennaio','febbraio','marzo','aprile','maggio','giugno','luglio','agosto','settembre','ottobre','novembre','dicembre'],
    'nl': ['januari','februari','maart','april','mei','juni','juli','augustus','september','oktober','november','december'],
    'pt': ['janeiro','fevereiro','março','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro'],
    'sv': ['januari','februari','mars','april','maj','juni','juli','augusti','september','oktober','november','december'],
    'ru': ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'],
    'ja': ['1','2','3','4','5','6','7','8','9','10','11','12'],
    'zh': ['1','2','3','4','5','6','7','8','9','10','11','12'],
    'es_alt': ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'],
}

WEEKDAYS = {
    'en': ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'],
}

def generate_samples(count: int, out: str, seed: int | None = 42):
    random.seed(seed)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    base = datetime(2020,1,1)
    with open(out, 'w', encoding='utf-8') as f:
        for i in range(count):
            # pick a language skewed toward English but include others
            lang = random.choices(list(LANG_TEMPLATES.keys()), weights=[40,8,8,6,6,4,4,4,6,4,4], k=1)[0]
            templates = LANG_TEMPLATES[lang]
            template = random.choice(templates)
            # pick a random date within +- 3650 days
            delta = random.randint(0, 3650*2)
            dt = base + timedelta(days=delta)
            # random time
            H = random.randint(0,23)
            M = random.randint(0,59)
            d = dt.day
            m = dt.month
            Y = dt.year
            mon = None
            if lang in MONTHS:
                mon = random.choice(MONTHS[lang])
            else:
                mon = MONTHS['en'][m-1]
            # occasionally use short month names
            if random.random() < 0.05 and lang == 'es':
                mon = MONTHS.get('es_alt', MONTHS['en'])[m-1]
            line = template.format(d=d,m=m,Y=Y,H=H,M=f"{M:02d}",mon=mon)
            # sometimes prefix with extra words to mimic long freeform text
            if random.random() < 0.25:
                prefix = random.choice(['Meet on', 'Reminder:', 'Fecha:', 'Nota:'])
                line = prefix + ' ' + line
            f.write(line + '\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--count', type=int, default=10000)
    p.add_argument('--out', type=str, default='data/date_samples.txt')
    p.add_argument('--seed', type=int, default=42)
    args = p.parse_args()
    generate_samples(args.count, args.out, seed=args.seed)

if __name__ == '__main__':
    main()
