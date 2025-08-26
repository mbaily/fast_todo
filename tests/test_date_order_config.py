import importlib
from datetime import datetime, timezone

import pytest

from app import config, utils


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


@pytest.mark.parametrize('order,expected_iso', [
    ('DMY', '2025-09-12T00:00:00Z'),  # 12 Sep 2025
    ('MDY', '2025-12-09T00:00:00Z'),  # Dec 9 2025
])
def test_date_order_respected(order, expected_iso):
    # modify config at runtime
    old = config.DATE_ORDER
    try:
        config.DATE_ORDER = order
        # reload utils to pick up runtime config via import-time path used
        importlib.reload(utils)
        res = utils.extract_dates('Follow up 12/9/2025')
        assert res, 'expected at least one date'
        assert to_iso(res[0]) == expected_iso
    finally:
        config.DATE_ORDER = old
        importlib.reload(utils)
