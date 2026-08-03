# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
"""Pacing for third-party APIs — spend their capacity at the rate they publish.

We call several APIs we don't own (gnomAD, AlphaGenome) from a public endpoint, so
the number of requests a report can make is bounded by the genome someone uploaded
rather than by us. The wrong fix is a flat per-report cap: it truncates reports at
an arbitrary number that has nothing to do with the upstream's actual limit, and it
does that just as hard when we are running two reports a day as when we are running
two thousand. The right fix is to spend at the published RATE and let a report take
as many lookups as it needs.

What the upstreams actually say (checked 2026-08-03):

  gnomAD — a real, published number, enforced per client IP in their own server
    code (broadinstitute/gnomad-browser, graphql-api/src/graphql/rate-limiting.ts
    with the deployed values in deploy/manifests/browser/base/api.deployment.yaml):
    MAX_REQUESTS_PER_MINUTE=30, MAX_QUERY_COST_PER_MINUTE=300, and the `variant`
    field we query is @cost(value: 1). So 30 requests per calendar minute is the
    binding limit. There is NO daily or monthly quota.

  AlphaGenome — deliberately has no published number. The API team's answer on
    their own forum is that quotas are "regularly changed based on our available
    resources", and their advice for finding the ceiling is to increase concurrency
    "until you start getting RESOURCE_EXHAUSTED errors". So any daily budget we
    invented would be pure superstition. We pace conservatively and treat their
    RESOURCE_EXHAUSTED as the real signal.

Two mechanisms, deliberately separate:

  RateLimiter — how fast we may ask. Counts per CALENDAR minute, which is exactly
    how gnomAD's own limiter windows (`new Date().getMinutes()`), so staying under
    their number in our window keeps us under it in theirs. Shared across processes
    through SQLite because the limit is per IP and the app runs two uvicorn workers
    behind one address; an in-process counter would let each worker spend the whole
    allowance.

  Deadline — how long a report may WAIT for that rate. This is what keeps an inline
    report from turning into a ten-minute page load, and it is a latency decision,
    not a quota one. A report that runs out of deadline stops asking and says so.
"""
from __future__ import annotations
import sqlite3, time
from pathlib import Path


class Deadline:
    """A wall-clock budget for one report's waiting. Not a request count — the
    point is that the report stays responsive, not that it stays small."""

    def __init__(self, seconds: float):
        self.seconds = max(0.0, float(seconds))
        self._start = time.monotonic()

    def remaining(self) -> float:
        return max(0.0, self.seconds - (time.monotonic() - self._start))

    def expired(self) -> bool:
        return self.remaining() <= 0


class RateLimiter:
    """Fixed calendar-minute request counter, shared between processes via SQLite.

    Fixed-window rather than a token bucket on purpose: the server we are pacing
    against uses a fixed calendar-minute window, and matching its shape means our
    count and its count can never disagree about which minute a request landed in.
    A token bucket would smooth our side and still burst across their boundary.
    """

    def __init__(self, db_path: str, key: str, per_minute: int):
        self.db_path = str(db_path)
        self.key = key
        self.per_minute = max(1, int(per_minute))
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._con() as con:
            con.execute("CREATE TABLE IF NOT EXISTS rate_window("
                        "key TEXT PRIMARY KEY, minute INTEGER, n INTEGER)")

    def _con(self):
        # isolation_level=None + explicit BEGIN IMMEDIATE: two uvicorn workers can
        # claim slots at the same instant, and a deferred transaction would let
        # both read the same count before either writes.
        con = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        return con

    def _claim(self, now: float) -> bool:
        """Take one slot in the current minute, or return False if it is full."""
        minute = int(now // 60)
        con = self._con()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT minute, n FROM rate_window WHERE key=?",
                              (self.key,)).fetchone()
            n = row[1] if row and row[0] == minute else 0
            if n >= self.per_minute:
                con.execute("COMMIT")
                return False
            con.execute("INSERT OR REPLACE INTO rate_window VALUES (?,?,?)",
                        (self.key, minute, n + 1))
            con.execute("COMMIT")
            return True
        except sqlite3.OperationalError:
            # Locked out by a peer for the whole timeout. Refusing the slot is the
            # safe direction: worst case we under-spend our own allowance.
            return False
        finally:
            con.close()

    def acquire(self, deadline: "Deadline | None" = None,
                sleep=time.sleep, now=time.time) -> bool:
        """Claim a slot, waiting for the next minute if this one is full. Returns
        False if the deadline would expire first — the caller then skips the call
        rather than making it late.

        `sleep` and `now` are injectable together so a test can drive the clock:
        faking only sleep would leave the real clock still inside the full window
        and spin the loop for up to a real minute."""
        while True:
            t = now()
            if self._claim(t):
                return True
            wait = 60 - (t % 60) + 0.05        # to the start of the next window
            if deadline is not None and deadline.remaining() < wait:
                return False
            sleep(wait)
