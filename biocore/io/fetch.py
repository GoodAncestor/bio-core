# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 GoodAncestor
"""Robust download helper for the refresh/mirror jobs.

Academic FTP/HTTP sources (NCBI, EWAS Catalog) can be slow or flaky, so every
bulk fetch is resumable and retrying rather than a single urlretrieve that dies
on a dropped connection (docs/DESIGN.md §3.3).

  - HTTP range resume: partial file kept as <dest>.part, restarted with a
    Range header from where it left off.
  - Bounded retries with backoff on connection errors / timeouts.
  - Optional size + checksum verification once complete.

Not a download manager — just enough to survive a slow academic mirror without
losing an hour of transfer to one hiccup.
"""
from __future__ import annotations
import os, time, hashlib, urllib.request, urllib.error
from pathlib import Path


def fetch(url: str, dest: str, *, retries: int = 5, backoff: float = 3.0,
          timeout: int = 60, expect_bytes: int | None = None,
          sha256: str | None = None, chunk: int = 1 << 20,
          log=print) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    for attempt in range(1, retries + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": "methylask"}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                # server ignored Range -> restart from scratch
                mode = "ab" if (have and r.status == 206) else "wb"
                if mode == "wb":
                    have = 0
                with open(part, mode) as fh:
                    while True:
                        buf = r.read(chunk)
                        if not buf:
                            break
                        fh.write(buf)
            break  # completed without exception
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == retries:
                raise
            wait = backoff * attempt
            log(f"  fetch retry {attempt}/{retries} after {type(e).__name__}; sleeping {wait:.0f}s "
                f"(have {part.stat().st_size if part.exists() else 0} bytes)")
            time.sleep(wait)

    size = part.stat().st_size
    if expect_bytes is not None and size != expect_bytes:
        raise IOError(f"{dest.name}: size {size} != expected {expect_bytes}")
    if sha256:
        h = hashlib.sha256()
        with open(part, "rb") as fh:
            for b in iter(lambda: fh.read(chunk), b""):
                h.update(b)
        if h.hexdigest() != sha256:
            raise IOError(f"{dest.name}: sha256 mismatch")
    os.replace(part, dest)
    return dest
