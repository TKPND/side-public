"""Canonical-JSON sha256 CLI for v4.10 pre-registration SEAL.

Computes per-file sha256 of the 4 SEAL artifacts plus the
``sizing_exit_commit_v410`` (hash of the concatenated canonical bytes in
sorted(filenames) order).

Canonicalization:
    json.dumps(obj, sort_keys=True, separators=(",", ":"),
               ensure_ascii=False).encode("utf-8")

No trailing newline. ``--strict`` asserts that each on-disk JSON file's raw
bytes equal the canonical serialization of its parsed value (byte-level
comparison). A mismatch raises RuntimeError with the offending filename.

Concat order (deterministic):
    sorted(EXPECTED_FILES)
    → dd_cap.json, gate_spec_v410.json, kelly_bounds_v410.json, overlay_spec.json

CLI::

    uv run python scripts/v4.10/compute_seal_hash_v410.py \\
        --seal-dir .planning/phases/88-pre-registration-seal-v4-10/88-SEAL/ \\
        [--strict]

stdout: JSON with keys ``hash_protocol``, ``per_file_hashes``,
``sizing_exit_commit_v410``.

v4.9 script (scripts/v4.9/compute_seal_hash.py) is untouched (D-10).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any

# v4.10 SEAL artifact filenames.  This set IS the spec (D-09).
EXPECTED_FILES: tuple[str, ...] = (
    "kelly_bounds_v410.json",
    "gate_spec_v410.json",
    "dd_cap.json",
    "overlay_spec.json",
)


def canonical_bytes(obj: Any) -> bytes:
    """Canonical JSON serialization.

    sort_keys=True normalizes key order; separators=(",", ":") drops
    whitespace; ensure_ascii=False keeps non-ASCII characters as UTF-8
    rather than \\u escapes.  No trailing newline — the return value of
    json.dumps never appends one.
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def compute_hashes(seal_dir: pathlib.Path, strict: bool = False) -> dict[str, Any]:
    """Compute per-file + aggregate sha256 hex digests.

    Parameters
    ----------
    seal_dir
        Directory containing the 4 SEAL JSON files.
    strict
        If True, raise RuntimeError when any file's on-disk bytes do
        not equal its canonical serialization (byte-level comparison).
        A trailing newline also triggers this error.

    Returns
    -------
    dict
        ``hash_protocol`` (str), ``per_file_hashes`` (dict[filename, hex64]),
        ``sizing_exit_commit_v410`` (hex64).
    """
    per_file_hashes: dict[str, str] = {}
    canonical_blobs: dict[str, bytes] = {}

    for filename in EXPECTED_FILES:
        path = seal_dir / filename
        on_disk: bytes = path.read_bytes()
        obj = json.loads(on_disk.decode("utf-8"))
        canon = canonical_bytes(obj)

        if strict and on_disk != canon:
            raise RuntimeError(
                f"canonical bytes drift in {filename}: "
                f"on-disk {len(on_disk)} bytes != canonical {len(canon)} bytes"
            )

        canonical_blobs[filename] = canon
        per_file_hashes[filename] = hashlib.sha256(canon).hexdigest()

    # Concatenation in sorted(filenames) order for determinism.
    concat = b"".join(canonical_blobs[fn] for fn in sorted(EXPECTED_FILES))
    sizing_exit_commit_v410 = hashlib.sha256(concat).hexdigest()

    return {
        "hash_protocol": (
            "sha256(canonical_bytes(json, sort_keys=True, "
            "separators=(',',':'), ensure_ascii=False, no_trailing_newline))"
        ),
        "per_file_hashes": per_file_hashes,
        "sizing_exit_commit_v410": sizing_exit_commit_v410,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute canonical-JSON sha256 for v4.10 SEAL artifacts"
    )
    parser.add_argument(
        "--seal-dir",
        required=True,
        type=pathlib.Path,
        help=(
            "Directory containing kelly_bounds_v410.json / gate_spec_v410.json / "
            "dd_cap.json / overlay_spec.json"
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Fail if any file's on-disk bytes differ from canonical "
            "serialization (including trailing newline).  Used by Plan 3 "
            "startup verify."
        ),
    )
    args = parser.parse_args(argv)

    try:
        result = compute_hashes(args.seal_dir, strict=args.strict)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
