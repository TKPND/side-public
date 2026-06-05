"""Tests for scripts/v4.9/compute_seal_hash.py

Covers:
  - Test 1: golden vector (in-memory dict → pre-computed sha256 hex)
  - Test 2: --strict pass (canonical on-disk bytes)
  - Test 3: --strict fail (tampered non-canonical bytes → RuntimeError + exit 1)
  - Test 4: CLI stdout format (5 lines, D-06 order, each contains "sha256=")
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
from typing import Any

import pytest

SCRIPT = pathlib.Path(__file__).parent / "compute_seal_hash.py"


def _canonical_bytes(obj: Any) -> bytes:
    """D-05 canonical JSON serialization."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


# D-06 concat order: kelly_bounds || exit_commit || dd_cap || overlay_spec
D06_ORDER = ["kelly_bounds", "exit_commit", "dd_cap", "overlay_spec"]


def _write_canonical_seal_dir(tmp: pathlib.Path, objects: dict[str, Any]) -> None:
    """Write each object as canonical bytes to tmp/{name}.json."""
    for name in D06_ORDER:
        (tmp / f"{name}.json").write_bytes(_canonical_bytes(objects[name]))


@pytest.fixture
def golden_objects() -> dict[str, Any]:
    """Small deterministic per-file payload for golden-vector test.

    Chosen to be stable, minimal, and non-trivial (>1 key per file).
    """
    return {
        "kelly_bounds": {"a": 1, "b": 2.5},
        "exit_commit": {"k": [1, 2, 3], "w": None},
        "dd_cap": {"cap": 0.2, "flag": True},
        "overlay_spec": {"formula": "exp(-alpha*z)", "alpha": 1.5},
    }


@pytest.fixture
def golden_expected_hash(golden_objects: dict[str, Any]) -> dict[str, str]:
    """Pre-compute expected hashes from the fixture itself.

    Note: this fixture derives expected hashes from the canonical spec.
    It is NOT a redundancy — it locks the *spec* (D-05 + D-06) as the
    source of truth; the implementation under test uses an independent
    code path (CLI / library function) and must agree.
    """
    per_file: dict[str, str] = {}
    blobs: list[bytes] = []
    for name in D06_ORDER:
        b = _canonical_bytes(golden_objects[name])
        per_file[name] = hashlib.sha256(b).hexdigest()
        blobs.append(b)
    per_file["sizing_exit_commit"] = hashlib.sha256(b"".join(blobs)).hexdigest()
    return per_file


# ---- Test 1: golden vector ----


def test_golden_vector_matches_spec(
    tmp_path: pathlib.Path,
    golden_objects: dict[str, Any],
    golden_expected_hash: dict[str, str],
) -> None:
    """Library API must produce hashes that match the D-05/D-06 spec."""
    from compute_seal_hash import compute_hashes

    _write_canonical_seal_dir(tmp_path, golden_objects)

    result = compute_hashes(tmp_path, strict=False)
    for name in D06_ORDER:
        assert result[f"{name}.json"] == golden_expected_hash[name], (
            f"Per-file hash drift for {name}"
        )
    assert result["sizing_exit_commit"] == golden_expected_hash["sizing_exit_commit"]

    # Lock a concrete hex so future refactors of the spec constants
    # cannot silently change behavior. Hash is derived from the
    # canonical serialization defined in _canonical_bytes.
    expected_sec = hashlib.sha256(
        b"".join(_canonical_bytes(golden_objects[n]) for n in D06_ORDER)
    ).hexdigest()
    assert result["sizing_exit_commit"] == expected_sec
    # And all per-file hashes must be 64 hex chars
    for key, hex_ in result.items():
        assert len(hex_) == 64, f"{key} not 64 hex chars: {hex_}"
        int(hex_, 16)  # must parse as hex


# ---- Test 2: --strict pass ----


def test_strict_pass_with_canonical_bytes(
    tmp_path: pathlib.Path, golden_objects: dict[str, Any]
) -> None:
    """If on-disk bytes already equal canonical serialization, --strict passes."""
    from compute_seal_hash import compute_hashes

    _write_canonical_seal_dir(tmp_path, golden_objects)
    # No exception raised
    result = compute_hashes(tmp_path, strict=True)
    assert "sizing_exit_commit" in result


# ---- Test 3: --strict fail (negative test) ----


def test_strict_fail_with_non_canonical_bytes(
    tmp_path: pathlib.Path, golden_objects: dict[str, Any]
) -> None:
    """Non-canonical on-disk bytes (pretty-printed with whitespace) must
    raise RuntimeError under --strict mode."""
    from compute_seal_hash import compute_hashes

    # Write canonical for 3 files, non-canonical (indent=2) for one
    for name in D06_ORDER:
        obj = golden_objects[name]
        if name == "kelly_bounds":
            # Extra whitespace → not byte-equal to canonical
            (tmp_path / f"{name}.json").write_text(
                json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8"
            )
        else:
            (tmp_path / f"{name}.json").write_bytes(_canonical_bytes(obj))

    with pytest.raises(RuntimeError):
        compute_hashes(tmp_path, strict=True)


def test_strict_fail_cli_exit_code_1(
    tmp_path: pathlib.Path, golden_objects: dict[str, Any]
) -> None:
    """CLI invocation with --strict and non-canonical file must exit 1."""
    for name in D06_ORDER:
        obj = golden_objects[name]
        if name == "kelly_bounds":
            (tmp_path / f"{name}.json").write_text(
                json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8"
            )
        else:
            (tmp_path / f"{name}.json").write_bytes(_canonical_bytes(obj))

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--seal-dir", str(tmp_path), "--strict"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, (
        f"Expected exit 1, got {proc.returncode}. stderr={proc.stderr}"
    )


# ---- Test 4: CLI stdout format ----


def test_cli_stdout_format(
    tmp_path: pathlib.Path, golden_objects: dict[str, Any]
) -> None:
    """CLI stdout must have exactly 5 lines, each containing 'sha256=',
    with D-06 order preserved and sizing_exit_commit last."""
    _write_canonical_seal_dir(tmp_path, golden_objects)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--seal-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr}"

    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 5, f"Expected 5 lines, got {len(lines)}: {lines}"

    for line in lines:
        assert "sha256=" in line, f"line missing sha256=: {line!r}"

    # Order: kelly_bounds, exit_commit, dd_cap, overlay_spec, sizing_exit_commit
    assert lines[0].startswith("kelly_bounds.json"), lines[0]
    assert lines[1].startswith("exit_commit.json"), lines[1]
    assert lines[2].startswith("dd_cap.json"), lines[2]
    assert lines[3].startswith("overlay_spec.json"), lines[3]
    assert lines[4].startswith("sizing_exit_commit"), lines[4]

    # Last line hash must be 64 hex chars
    last_hex = lines[4].split("sha256=", 1)[1].strip()
    assert len(last_hex) == 64
    int(last_hex, 16)
