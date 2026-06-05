"""Tests for scripts/v4.10/compute_seal_hash_v410.py

Covers (5 tests minimum):
  - Test A: canonical round-trip (--strict exits 0, per_file_hashes reproducible)
  - Test B: drift detection with --strict (RuntimeError, offending filename in msg)
  - Test C: drift tolerance without --strict (exit 0, canonical hash emitted)
  - Test D: concatenation order determinism (sorted(filenames), fixed hash)
  - Test E: trailing newline detection with --strict (RuntimeError)
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
from typing import Any

import pytest

SCRIPT = pathlib.Path(__file__).parent / "compute_seal_hash_v410.py"

# v4.10 SEAL filenames (plan D-09 literal).
EXPECTED_FILES = (
    "kelly_bounds_v410.json",
    "gate_spec_v410.json",
    "dd_cap.json",
    "overlay_spec.json",
)

# Plan-specified sample data (minimal valid schema, plan Task 2 §3 literal).
SAMPLE_OBJECTS: dict[str, Any] = {
    "kelly_bounds_v410.json": {
        "b_estimator": "bca_bootstrap",
        "b_bootstrap_n": 2000,
        "b_bootstrap_seed": 20260422,
    },
    "gate_spec_v410.json": {
        "gate_mode": "point_kelly",
        "point_kelly_floor": 0.10,
        "size_cap": 0.25,
        "fold_aggregation": "all_fold_pass",
    },
    "dd_cap.json": {"dd_cap": 0.20},
    "overlay_spec.json": {"alpha": 1.0, "beta": 1.0},
}


def _canonical_bytes(obj: Any) -> bytes:
    """D-09 canonical JSON serialization — mirrors production code."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _write_canonical_seal_dir(tmp: pathlib.Path) -> None:
    """Write SAMPLE_OBJECTS to tmp/ as canonical bytes (no trailing newline)."""
    for filename in EXPECTED_FILES:
        (tmp / filename).write_bytes(_canonical_bytes(SAMPLE_OBJECTS[filename]))


# --------------------------------------------------------------------------- #
# Test A: canonical round-trip                                                #
# --------------------------------------------------------------------------- #


def test_canonical_round_trip(tmp_path: pathlib.Path) -> None:
    """--strict on canonical on-disk bytes: exit 0, hashes reproducible."""
    import sys as _sys

    _sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from compute_seal_hash_v410 import compute_hashes

    _write_canonical_seal_dir(tmp_path)

    # First call
    r1 = compute_hashes(tmp_path, strict=True)
    # Second call (determinism check)
    r2 = compute_hashes(tmp_path, strict=True)

    # Must produce same output both times
    assert r1["sizing_exit_commit_v410"] == r2["sizing_exit_commit_v410"]
    for fn in EXPECTED_FILES:
        assert r1["per_file_hashes"][fn] == r2["per_file_hashes"][fn]

    # All hex strings must be 64 chars
    for fn, hex_ in r1["per_file_hashes"].items():
        assert len(hex_) == 64, f"{fn} not 64 chars"
        int(hex_, 16)  # valid hex
    assert len(r1["sizing_exit_commit_v410"]) == 64
    int(r1["sizing_exit_commit_v410"], 16)


# --------------------------------------------------------------------------- #
# Test B: drift detection with --strict                                        #
# --------------------------------------------------------------------------- #


def test_strict_drift_detection(tmp_path: pathlib.Path) -> None:
    """Non-canonical on-disk bytes raise RuntimeError under --strict.

    The error message must contain the offending filename.
    """
    import sys as _sys

    _sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from compute_seal_hash_v410 import compute_hashes

    # Write canonical for 3 files, non-canonical (indent=2) for one
    offending = "kelly_bounds_v410.json"
    for filename in EXPECTED_FILES:
        if filename == offending:
            (tmp_path / filename).write_text(
                json.dumps(SAMPLE_OBJECTS[filename], indent=2, sort_keys=True),
                encoding="utf-8",
            )
        else:
            (tmp_path / filename).write_bytes(
                _canonical_bytes(SAMPLE_OBJECTS[filename])
            )

    with pytest.raises(RuntimeError) as exc_info:
        compute_hashes(tmp_path, strict=True)

    assert offending in str(exc_info.value), (
        f"Expected offending filename '{offending}' in error: {exc_info.value}"
    )


def test_strict_drift_cli_exit_1(tmp_path: pathlib.Path) -> None:
    """CLI with --strict and non-canonical file must exit 1."""
    offending = "gate_spec_v410.json"
    for filename in EXPECTED_FILES:
        if filename == offending:
            (tmp_path / filename).write_text(
                json.dumps(SAMPLE_OBJECTS[filename], indent=2, sort_keys=True),
                encoding="utf-8",
            )
        else:
            (tmp_path / filename).write_bytes(
                _canonical_bytes(SAMPLE_OBJECTS[filename])
            )

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--seal-dir", str(tmp_path), "--strict"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, (
        f"Expected exit 1, got {proc.returncode}. stderr={proc.stderr}"
    )


# --------------------------------------------------------------------------- #
# Test C: drift tolerance without --strict                                     #
# --------------------------------------------------------------------------- #


def test_drift_tolerant_without_strict(tmp_path: pathlib.Path) -> None:
    """Without --strict, non-canonical on-disk bytes: exit 0, hash emitted."""
    import sys as _sys

    _sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from compute_seal_hash_v410 import compute_hashes

    # Write all files non-canonical (indent=2)
    for filename in EXPECTED_FILES:
        (tmp_path / filename).write_text(
            json.dumps(SAMPLE_OBJECTS[filename], indent=2, sort_keys=True),
            encoding="utf-8",
        )

    # Must NOT raise
    result = compute_hashes(tmp_path, strict=False)
    assert "sizing_exit_commit_v410" in result
    assert len(result["sizing_exit_commit_v410"]) == 64

    # And it must agree with canonical version (recomputes canonical internally)
    _write_canonical_seal_dir(tmp_path)
    result_canonical = compute_hashes(tmp_path, strict=True)
    assert (
        result["sizing_exit_commit_v410"] == result_canonical["sizing_exit_commit_v410"]
    )


# --------------------------------------------------------------------------- #
# Test D: concatenation order sorted determinism                               #
# --------------------------------------------------------------------------- #


def test_concat_order_sorted_determinism(tmp_path: pathlib.Path) -> None:
    """sizing_exit_commit_v410 is produced by sorted(EXPECTED_FILES) concat.

    We lock the spec: independently computing sorted concat must match
    what the implementation returns.
    """
    import sys as _sys

    _sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from compute_seal_hash_v410 import compute_hashes, EXPECTED_FILES as IMPL_FILES

    _write_canonical_seal_dir(tmp_path)
    result = compute_hashes(tmp_path, strict=True)

    # Spec: sorted(EXPECTED_FILES)
    sorted_names = sorted(IMPL_FILES)
    blobs = [_canonical_bytes(SAMPLE_OBJECTS[fn]) for fn in sorted_names]
    expected_hash = hashlib.sha256(b"".join(blobs)).hexdigest()

    assert result["sizing_exit_commit_v410"] == expected_hash, (
        f"Concat order mismatch: got {result['sizing_exit_commit_v410']!r}, "
        f"expected (sorted) {expected_hash!r}"
    )


# --------------------------------------------------------------------------- #
# Test E: trailing newline detection with --strict                             #
# --------------------------------------------------------------------------- #


def test_trailing_newline_strict_fails(tmp_path: pathlib.Path) -> None:
    """Trailing \\n appended to on-disk bytes triggers RuntimeError under --strict."""
    import sys as _sys

    _sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from compute_seal_hash_v410 import compute_hashes

    offending = "dd_cap.json"
    for filename in EXPECTED_FILES:
        canon = _canonical_bytes(SAMPLE_OBJECTS[filename])
        if filename == offending:
            # Append trailing newline — violates canonical bytes invariant
            (tmp_path / filename).write_bytes(canon + b"\n")
        else:
            (tmp_path / filename).write_bytes(canon)

    with pytest.raises(RuntimeError) as exc_info:
        compute_hashes(tmp_path, strict=True)

    assert offending in str(exc_info.value), (
        f"Expected offending filename '{offending}' in error: {exc_info.value}"
    )
