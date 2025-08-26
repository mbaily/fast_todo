import json
from pathlib import Path
import pytest

from app import utils

HERE = Path(__file__).parent


def load_expectations():
    p = HERE.parent / 'debuggings' / 'plain_dates_expected.json'
    with open(p, 'r', encoding='utf-8') as fh:
        arr = json.load(fh)
    return arr


@pytest.mark.parametrize('phrase,expected', load_expectations())
def test_extract_matches_expected(phrase, expected):
    """For each canonical phrase, ensure extract_dates finds the expected anchor(s).

    - If expected is null, assert extract_dates returns an empty list.
    - If expected is a string, assert at least one extracted ISO matches it.
    - If expected is a list, assert every expected ISO appears in extracted results
      (order-independent).
    """
    results = utils.extract_dates(phrase)
    # normalize to ISO-Z strings
    def to_iso(d):
        try:
            return d.astimezone(utils.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        except Exception:
            return str(d)

    iso_results = [to_iso(d) for d in results]

    if expected is None:
        assert iso_results == []
        return

    if isinstance(expected, list):
        for e in expected:
            assert e in iso_results, f"expected {e} in {iso_results} for phrase '{phrase}'"
    else:
        assert expected in iso_results, f"expected {expected} in {iso_results} for phrase '{phrase}'"
