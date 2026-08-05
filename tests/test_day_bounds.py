"""「一天」的边界必须按本地时区切，不能按 UTC 切。

这个 bug 只在本地日期和 UTC 日期不一致的时段才暴露（UTC+8 是本地 00:00–08:00），
所以用固定时区显式钉住，不依赖跑测试时的墙上时钟。
"""

import datetime as dt
import os
import time

import pytest

from app.timeutils import local_day_bounds


@pytest.fixture
def tz_shanghai():
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Shanghai"
    time.tzset()
    yield
    if original is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = original
    time.tzset()


def test_local_day_maps_to_shifted_utc_window(tz_shanghai) -> None:
    start, end = local_day_bounds(dt.date(2026, 8, 6))
    # UTC+8：本地 8/6 00:00 == UTC 8/5 16:00
    assert start == dt.datetime(2026, 8, 5, 16, 0, tzinfo=dt.UTC)
    assert end == dt.datetime(2026, 8, 6, 16, 0, tzinfo=dt.UTC)


def test_window_is_exactly_24h(tz_shanghai) -> None:
    start, end = local_day_bounds(dt.date(2026, 8, 6))
    assert end - start == dt.timedelta(days=1)


def test_late_evening_message_belongs_to_that_local_day(tz_shanghai) -> None:
    """本地 8/6 23:30（= UTC 8/6 15:30）必须算作 8/6，而不是 8/7。"""
    evening_utc = dt.datetime(2026, 8, 6, 15, 30, tzinfo=dt.UTC)
    start, end = local_day_bounds(dt.date(2026, 8, 6))
    assert start <= evening_utc < end

    next_start, _ = local_day_bounds(dt.date(2026, 8, 7))
    assert evening_utc < next_start


def test_early_morning_message_belongs_to_that_local_day(tz_shanghai) -> None:
    """本地 8/6 01:00（= UTC 8/5 17:00）必须算作 8/6 —— 这正是旧实现漏掉的区间。"""
    early_utc = dt.datetime(2026, 8, 5, 17, 0, tzinfo=dt.UTC)
    start, end = local_day_bounds(dt.date(2026, 8, 6))
    assert start <= early_utc < end


def test_consecutive_days_do_not_overlap_or_gap(tz_shanghai) -> None:
    _, end_first = local_day_bounds(dt.date(2026, 8, 6))
    start_second, _ = local_day_bounds(dt.date(2026, 8, 7))
    assert end_first == start_second
