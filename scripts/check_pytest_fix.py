#!/usr/bin/env python3
"""Watch PyPI for the pytest release that fixes PYSEC-2026-1845.

The advisory (local /tmp/pytest-of-{user} tempdir race — DoS / possible local
priv-esc) names pytest 9.0.3 as the fix, but as of Aug 2026 no such release
exists on PyPI: the latest published pytest is still the flagged line. Until
the fix ships, the mitigation is operational — go_live_check runs its pytest
invocation with a private mode-0700 --basetemp so the world-predictable
shared path is never used.

This script is the reminder that turns the eventual release into an action:

  ACTION: ...  (exit 1)  a FINAL release >= 9.0.3 is on PyPI — bump the pin
  OK: ...      (exit 0)  no fixed release yet — keep the mitigation
  SKIP: ...    (exit 0)  PyPI unreachable — never blocks anything

go_live_check surfaces the same text as a soft NOTE line: informational only,
never a PASS/FAIL row, so neither a PyPI outage nor an available fix can
affect the live gate.

Run standalone or from cron:  python3 scripts/check_pytest_fix.py
"""
from __future__ import annotations

import re
from typing import Callable, Optional

FIX_VERSION = (9, 0, 3)                              # per PYSEC-2026-1845
_FINAL = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")   # final releases only


def parse_final_version(s: str) -> Optional[tuple]:
    """(major, minor, micro) for FINAL releases; None for rc/a/b/dev/post or
    junk. Pre-releases must NOT satisfy the watch — an rc is not the fix."""
    m = _FINAL.match(s.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def best_fixed_release(versions) -> Optional[str]:
    """Highest FINAL release >= FIX_VERSION, or None while no fix exists."""
    fixed = [(t, v) for v in versions
             if (t := parse_final_version(v)) is not None and t >= FIX_VERSION]
    return max(fixed)[1] if fixed else None


def fetch_pypi_versions(timeout: float = 5.0) -> list:
    """All version strings PyPI knows for pytest (network)."""
    import httpx
    r = httpx.get("https://pypi.org/pypi/pytest/json", timeout=timeout,
                  headers={"Accept": "application/json"})
    r.raise_for_status()
    return list(r.json()["releases"].keys())


def pytest_fix_note(timeout: float = 5.0,
                    fetch: Callable = fetch_pypi_versions) -> str:
    """One-line status. NEVER raises — network trouble degrades to SKIP.
    `fetch` is injectable so tests exercise every branch offline."""
    try:
        versions = fetch(timeout)
    except Exception as exc:  # noqa: BLE001 — a watch must never block anything
        return (f"SKIP: pytest-fix watch could not reach PyPI "
                f"({type(exc).__name__}) — check skipped, mitigation stays")
    release = best_fixed_release(versions)
    if release:
        return (f"ACTION: pytest {release} is on PyPI — the PYSEC-2026-1845 fix "
                f"shipped. Bump requirements.txt to pytest>={release}, run the "
                f"full suite, then retire the --basetemp mitigation.")
    latest = max((t for v in versions if (t := parse_final_version(v))),
                 default=None)
    shown = ".".join(map(str, latest)) if latest else "unknown"
    return (f"OK: no fixed pytest yet (latest final on PyPI: {shown}; fix lands "
            f"in >={'.'.join(map(str, FIX_VERSION))}) — keep --basetemp mitigation")


def main() -> int:
    note = pytest_fix_note()
    print(note)
    return 1 if note.startswith("ACTION") else 0


if __name__ == "__main__":
    raise SystemExit(main())
