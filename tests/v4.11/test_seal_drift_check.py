"""CLASS-03 tests for scripts/v4.11/seal_drift_check.py (6 cases).

D-35: Flat import via sys.path.insert in conftest.py (scripts/v4.11 is dot-in-dir,
      cannot be a Python package). conftest.py inserts scripts/v4.11 into sys.path
      at module level before these imports execute.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess

import pytest

# D-35 flat import -- conftest.py already inserted scripts/v4.11 into sys.path.
from seal_drift_check import (  # type: ignore[import-not-found]
    EXPECTED_FILES,
    SIGNAL_COMMIT_V411_EXPECTED,
    canonical_bytes,
    compute_signal_commit_v411,
    verify_seal_or_raise,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def test_canonical_round_trip(seal_dir_canonical: pathlib.Path) -> None:
    """Test A: pure function returns same 64-char hex on repeated calls."""
    r1 = compute_signal_commit_v411(seal_dir_canonical)
    r2 = compute_signal_commit_v411(seal_dir_canonical)
    assert r1 == r2, "compute_signal_commit_v411 is not deterministic"
    assert _HEX64.match(r1), f"result is not 64-char lowercase hex: {r1!r}"


def test_drift_detection(seal_dir_canonical: pathlib.Path) -> None:
    """Test B: 1-byte mutation in filter_spec.json changes hash, verify_seal_or_raise raises."""
    fp = seal_dir_canonical / "filter_spec.json"
    obj = json.loads(fp.read_text())
    # Tamper: change allowed_buckets from ["HIGH"] to ["LOW"]
    obj["allowed_buckets"] = ["LOW"]
    fp.write_bytes(canonical_bytes(obj))

    # Hash must differ from expected
    tampered_hash = compute_signal_commit_v411(seal_dir_canonical)
    assert tampered_hash != SIGNAL_COMMIT_V411_EXPECTED, (
        "tampered JSON should produce a different hash"
    )

    # verify_seal_or_raise must raise RuntimeError with "SEAL drift"
    with pytest.raises(RuntimeError, match="SEAL drift"):
        verify_seal_or_raise(seal_dir_canonical)


def test_order_determinism() -> None:
    """Test C: EXPECTED_FILES constant is already sorted (invariant)."""
    assert sorted(EXPECTED_FILES) == EXPECTED_FILES, (
        f"EXPECTED_FILES must be pre-sorted; got {EXPECTED_FILES}"
    )


def test_missing_file_raises(seal_dir_canonical: pathlib.Path) -> None:
    """Test D: missing SEAL file raises FileNotFoundError."""
    (seal_dir_canonical / "vol_cuts.json").unlink()
    with pytest.raises(FileNotFoundError):
        compute_signal_commit_v411(seal_dir_canonical)


def test_bash_python_crosscheck(seal_dir_fixture: pathlib.Path) -> None:
    """Test E: bash verify_signal_commit_v411.sh stdout matches Python output bit-exact."""
    bash_result = subprocess.run(
        [
            "bash",
            str(_REPO_ROOT / "scripts" / "v4.11" / "verify_signal_commit_v411.sh"),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=_REPO_ROOT,
    )
    # Extract first 64-hex token from stdout
    m = re.search(r"[0-9a-f]{64}", bash_result.stdout)
    assert m, f"no hex64 token in bash stdout: {bash_result.stdout!r}"
    py_hash = compute_signal_commit_v411(seal_dir_fixture)
    assert m.group(0) == py_hash, (
        f"bash/python hash mismatch: bash={m.group(0)!r} python={py_hash!r}"
    )
    assert py_hash == SIGNAL_COMMIT_V411_EXPECTED, (
        f"python hash {py_hash!r} != expected literal {SIGNAL_COMMIT_V411_EXPECTED!r}"
    )


def test_state_md_anchor_matches_literal() -> None:
    """Test F: D-23' cross-check — STATE.md Sealed Anchors row matches module literal."""
    state_md = (_REPO_ROOT / ".planning" / "STATE.md").read_text()
    m = re.search(
        r"signal_commit_v411\s*\|\s*`?([0-9a-f]{64})`?",
        state_md,
    )
    assert m, (
        "signal_commit_v411 row not found in STATE.md (expected in Sealed Anchors table)"
    )
    assert m.group(1) == SIGNAL_COMMIT_V411_EXPECTED, (
        f"STATE.md anchor {m.group(1)!r} != module literal {SIGNAL_COMMIT_V411_EXPECTED!r}"
    )
