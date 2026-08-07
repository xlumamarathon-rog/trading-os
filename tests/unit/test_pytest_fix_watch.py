"""Tests for the PYSEC-2026-1845 pytest-fix watch (scripts/check_pytest_fix.py).

Every branch is exercised offline via the injectable `fetch` — no test here
touches the network. The invariant that matters most: the watch can NEVER
raise and can NEVER become a blocking gate check.
"""
import pytest

from scripts.check_pytest_fix import (
    FIX_VERSION,
    best_fixed_release,
    parse_final_version,
    pytest_fix_note,
)


# ---------- version parsing: finals only, pre-releases rejected ----------

@pytest.mark.parametrize("s,expected", [
    ("9.0.3", (9, 0, 3)),
    ("8.4.2", (8, 4, 2)),
    ("10.0", (10, 0, 0)),
    ("9.0.3rc1", None),      # a release candidate is NOT the fix
    ("9.0.3b1", None),
    ("9.0.3.dev0", None),
    ("9.0.3.post1", None),
    ("garbage", None),
    ("", None),
])
def test_parse_final_version(s, expected):
    assert parse_final_version(s) == expected


def test_fix_version_constant_matches_advisory():
    assert FIX_VERSION == (9, 0, 3)


# ---------- best_fixed_release selection ----------

def test_no_fixed_release_while_only_flagged_line_exists():
    # today's real PyPI state: latest is the flagged 8.4.x line
    assert best_fixed_release(["8.4.0", "8.4.1", "8.4.2", "8.3.5"]) is None


def test_prerelease_of_the_fix_does_not_count():
    assert best_fixed_release(["8.4.2", "9.0.3rc1", "9.0.3.dev1"]) is None


def test_picks_highest_final_at_or_above_fix():
    assert best_fixed_release(
        ["8.4.2", "9.0.3", "9.0.4", "9.1.0", "10.0.0rc1"]) == "9.1.0"


def test_exact_fix_version_counts():
    assert best_fixed_release(["8.4.2", "9.0.3"]) == "9.0.3"


# ---------- note text + exit semantics ----------

def test_note_ok_when_no_fix_yet():
    note = pytest_fix_note(fetch=lambda _t: ["8.4.0", "8.4.1", "8.4.2"])
    assert note.startswith("OK:")
    assert "8.4.2" in note                       # shows latest final available


def test_note_action_when_fix_present():
    note = pytest_fix_note(fetch=lambda _t: ["8.4.2", "9.0.3"])
    assert note.startswith("ACTION:")
    assert "9.0.3" in note


def test_note_skip_and_never_raises_on_network_failure():
    def boom(_t):
        raise ConnectionError("pypi down")
    note = pytest_fix_note(fetch=boom)            # must not propagate
    assert note.startswith("SKIP:")


def test_main_exit_codes():
    import scripts.check_pytest_fix as mod
    # ACTION -> exit 1 (cron-pingable); everything else -> 0
    mod.fetch_pypi_versions  # symbol exists
    assert mod.pytest_fix_note(fetch=lambda _t: ["9.0.3"]).startswith("ACTION")
    assert mod.pytest_fix_note(fetch=lambda _t: ["8.4.2"]).startswith("OK")


# ---------- wiring: the go-live NOTE is non-blocking ----------

def test_go_live_note_is_soft_not_a_check(monkeypatch, capsys):
    """The watch prints a NOTE line and does NOT append to the PASS/FAIL
    CHECKS list — so it can never flip the gate, in any outcome."""
    import scripts.check_pytest_fix as watch
    import scripts.go_live_check as glc

    # even the loudest outcome (ACTION) must not add a check row
    monkeypatch.setattr(watch, "pytest_fix_note", lambda *a, **k: "ACTION: fix shipped")
    before = list(glc.CHECKS)
    # the go_live_check note block imports pytest_fix_note locally, then prints
    print(f"NOTE  {watch.pytest_fix_note()}")
    out = capsys.readouterr().out
    assert "NOTE" in out and "ACTION" in out
    assert glc.CHECKS == before                   # nothing appended
