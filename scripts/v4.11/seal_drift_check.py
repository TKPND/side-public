"""CLASS-03: Canonical JSON sha256 pure function for Phase 92 SEAL drift check.

D-22: 3 SEAL JSON (classifier_spec.json, filter_spec.json, vol_cuts.json) are
filename-sorted, each serialized as canonical bytes then sha256'd together.
Matches bash pipeline in scripts/v4.11/verify_signal_commit_v411.sh:
  for f in $(ls *.json | sort); do jq -cS . "$f"; done | sha256sum

The bash `jq -cS` appends a trailing newline after each JSON document.
Python replicates this: canonical_bytes(obj) + b"\\n" per file.

D-23' expected-value resolution order:
  1. env var SIGNAL_COMMIT_V411 (CI / manual override)
  2. module literal SIGNAL_COMMIT_V411_EXPECTED (fallback)
  3. STATE.md regex parse is deferred to v4.12+ tech debt

D-35: This module has no sibling import from scripts/v4.11 (stdlib + pathlib only),
      so it is callable both as `python scripts/v4.11/seal_drift_check.py` and via
      `sys.path.insert(0, scripts/v4.11)` + flat `from seal_drift_check import ...`.

D-32: Read-only guarantee — this module never writes to seal_dir.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
from typing import Any

EXPECTED_FILES: list[str] = [
    "classifier_spec.json",
    "filter_spec.json",
    "vol_cuts.json",
]
SIGNAL_COMMIT_V411_EXPECTED: str = (
    "f8ccc8a806b847230c238b12011a479c77f7f10e6aed3f9959e8dbecfaa93bae"
)
SEAL_DIR_DEFAULT: pathlib.Path = (
    pathlib.Path(__file__).resolve().parents[2]
    / ".planning"
    / "phases"
    / "92-scope-lock-pre-registration-seal"
    / "SEAL"
)


def canonical_bytes(obj: Any) -> bytes:
    """jq -cS equivalent (without trailing newline).

    sort_keys=True normalizes key order; separators=(",", ":") drops
    whitespace; ensure_ascii=False keeps non-ASCII as UTF-8. No trailing
    newline — use compute_signal_commit_v411() which adds \\n per file to
    match the bash pipeline.
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def compute_signal_commit_v411(seal_dir: pathlib.Path = SEAL_DIR_DEFAULT) -> str:
    """Return 64-char hex sha256 over 3 SEAL JSONs in sorted filename order.

    Matches bash pipeline:
      for f in $(ls *.json | sort); do jq -cS . "$f"; done | sha256sum

    jq -cS appends a trailing newline after each document, so each file
    contributes canonical_bytes(obj) + b"\\n" to the hash input.

    Parameters
    ----------
    seal_dir
        Directory containing the 3 SEAL JSON files. Defaults to the
        Phase 92 SEAL directory relative to this script.

    Returns
    -------
    str
        64-character lowercase hex digest (sha256).

    Raises
    ------
    FileNotFoundError
        If any of EXPECTED_FILES is missing from seal_dir.
    """
    h = hashlib.sha256()
    for fname in sorted(EXPECTED_FILES):
        fpath = seal_dir / fname
        if not fpath.exists():
            raise FileNotFoundError(f"SEAL file missing: {fpath}")
        obj = json.loads(fpath.read_text(encoding="utf-8"))
        h.update(canonical_bytes(obj))
        h.update(b"\n")  # jq -cS trailing newline per document
    return h.hexdigest()


def resolve_expected_commit() -> str:
    """D-23' resolution order: env var SIGNAL_COMMIT_V411 -> literal fallback.

    Returns
    -------
    str
        The expected 64-char hex commit hash.
    """
    return os.environ.get("SIGNAL_COMMIT_V411", SIGNAL_COMMIT_V411_EXPECTED)


def verify_seal_or_raise(seal_dir: pathlib.Path = SEAL_DIR_DEFAULT) -> None:
    """Fail-close on SEAL drift (D-22).

    Called at module top by vol_estimator.py and nyquist_audit_v411.py
    (Wave 1/2) to prevent silent degrade if SEAL JSON content changed.

    Raises
    ------
    RuntimeError
        If the computed hash does not match the expected commit hash.
        Message contains "SEAL drift" for test matching.
    """
    expected = resolve_expected_commit()
    actual = compute_signal_commit_v411(seal_dir)
    if actual != expected:
        raise RuntimeError(
            f"SEAL drift detected (D-22 fail-close):\n"
            f"  expected: {expected}\n"
            f"  actual:   {actual}\n"
            f"  seal_dir: {seal_dir}"
        )


if __name__ == "__main__":  # pragma: no cover
    print(compute_signal_commit_v411())
