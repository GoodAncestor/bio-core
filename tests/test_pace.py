# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
"""Rate pacing: the calendar-minute window, cross-process sharing, and the
deadline that keeps a paced report from becoming a slow one."""
import sqlite3
import pytest
from biocore.net.pace import RateLimiter, Deadline


def test_window_allows_exactly_the_rate_then_refuses(tmp_path, monkeypatch):
    rl = RateLimiter(str(tmp_path / "r.db"), "gnomad", 3)
    now = 1_000_000_020.0        # 20s into some minute
    assert [rl._claim(now) for _ in range(4)] == [True, True, True, False]


def test_window_resets_on_the_minute_boundary(tmp_path):
    """The window must be the CALENDAR minute, not 60s since first use: gnomAD
    counts by `new Date().getMinutes()`, and a sliding window on our side would
    let a burst straddle their boundary while looking fine on ours."""
    rl = RateLimiter(str(tmp_path / "r.db"), "gnomad", 2)
    base = 1_000_000_020.0                   # exactly on a minute boundary
    assert rl._claim(base) and rl._claim(base)
    assert not rl._claim(base + 59)          # same calendar minute, still full
    assert rl._claim(base + 60)              # next minute, allowance renewed


def test_two_limiters_on_one_db_share_the_allowance(tmp_path):
    """The limit is per IP and the app runs two uvicorn workers behind one
    address, so the count has to live outside the process. Two limiters on the
    same file stand in for those two workers."""
    db = str(tmp_path / "r.db")
    a, b = RateLimiter(db, "gnomad", 2), RateLimiter(db, "gnomad", 2)
    now = 1_000_000_020.0
    assert a._claim(now) and b._claim(now)
    assert not a._claim(now) and not b._claim(now)


def test_separate_keys_do_not_share(tmp_path):
    db = str(tmp_path / "r.db")
    now = 1_000_000_020.0
    assert RateLimiter(db, "gnomad", 1)._claim(now)
    assert RateLimiter(db, "alphagenome", 1)._claim(now)


def _clock(start=1_000_000_020.0):
    """A fake clock a sleep() call advances. Injecting both together matters: fake
    the sleep alone and the real clock stays inside the full window, so acquire()
    spins for up to a real minute — which is exactly what this test suite did
    before the `now` parameter existed."""
    t = [start]
    slept = []
    def sleep(s):
        slept.append(s); t[0] += s
    return t, slept, sleep


def test_acquire_waits_for_the_next_window(tmp_path):
    rl = RateLimiter(str(tmp_path / "r.db"), "k", 1)
    t, slept, sleep = _clock()
    assert rl.acquire(sleep=sleep, now=lambda: t[0])                 # slot is free
    assert rl.acquire(Deadline(300), sleep=sleep, now=lambda: t[0])  # waits, wins
    assert len(slept) == 1 and 0 < slept[0] <= 60.1


def test_acquire_refuses_rather_than_outlasting_the_deadline(tmp_path):
    """A report with 2 seconds of patience must not sleep 60 to make one more
    call — the deadline is a page-latency promise, not a suggestion."""
    rl = RateLimiter(str(tmp_path / "r.db"), "k", 1)
    t, slept, sleep = _clock()
    assert rl.acquire(sleep=sleep, now=lambda: t[0])
    assert not rl.acquire(Deadline(2), sleep=sleep, now=lambda: t[0])
    assert slept == []                                    # never slept at all


def test_deadline_expires(monkeypatch):
    import biocore.net.pace as pace
    t = [100.0]
    monkeypatch.setattr(pace.time, "monotonic", lambda: t[0])
    d = Deadline(10)
    assert not d.expired() and d.remaining() == 10
    t[0] = 111.0
    assert d.expired() and d.remaining() == 0
