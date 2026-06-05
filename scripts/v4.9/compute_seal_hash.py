"""Canonical-JSON sha256 CLI for v4.9 pre-registration SEAL.

Computes per-file sha256 of the 4 SEAL artifacts plus the
`sizing_exit_commit` (hash of the concatenated canonical bytes in
D-06 order).

Canonicalization (D-05):
    json.dumps(obj, sort_keys=True, separators=(",", ":"),
               ensure_ascii=False).encode("utf-8")

Concat order (D-06):
    kelly_bounds.json || exit_commit.json || dd_cap.json || overlay_spec.json

CLI::

    uv run python scripts/v4.9/compute_seal_hash.py \
        --seal-dir .planning/phases/85-pre-registration-seal/85-SEAL/ \
        [--strict]

--strict asserts that each on-disk JSON file's raw bytes equal the
canonical serialization of its parsed value. Used by Phase 88 startup
verify to detect whitespace / key-order drift.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any

# D-06: concat order for sizing_exit_commit. This list IS the spec.
D06_ORDER: tuple[str, ...] = (
    "kelly_bounds",
    "exit_commit",
    "dd_cap",
    "overlay_spec",
)


def canonical_bytes(obj: Any) -> bytes:
    """D-05 canonical JSON serialization.

    sort_keys=True normalizes key order; separators=(",", ":") drops
    whitespace; ensure_ascii=False keeps non-ASCII characters as UTF-8
    rather than \\u escapes.
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def compute_hashes(seal_dir: pathlib.Path, strict: bool = False) -> dict[str, str]:
    """Compute per-file + aggregate sha256 hex digests.

    Parameters
    ----------
    seal_dir
        Directory containing the 4 SEAL JSON files.
    strict
        If True, raise RuntimeError when any file's on-disk bytes do
        not equal its canonical serialization.

    Returns
    -------
    dict
        Keys: "kelly_bounds.json", "exit_commit.json", "dd_cap.json",
        "overlay_spec.json", "sizing_exit_commit". Values: 64-char hex.
    """
    result: dict[str, str] = {}
    blobs: list[bytes] = []
    mismatches: list[str] = []

    for name in D06_ORDER:
        path = seal_dir / f"{name}.json"
        on_disk = path.read_bytes()
        obj = json.loads(on_disk.decode("utf-8"))
        canonical = canonical_bytes(obj)

        if strict and on_disk != canonical:
            mismatches.append(name)

        result[f"{name}.json"] = hashlib.sha256(canonical).hexdigest()
        blobs.append(canonical)

    if mismatches:
        raise RuntimeError(
            "Canonical-byte mismatch in strict mode for: " + ", ".join(mismatches)
        )

    result["sizing_exit_commit"] = hashlib.sha256(b"".join(blobs)).hexdigest()
    return result


def format_output(result: dict[str, str]) -> str:
    """Render the CLI stdout block exactly as specified in the plan.

    Layout (5 lines, fixed column for readability):
        kelly_bounds.json    sha256=<64>
        exit_commit.json     sha256=<64>
        dd_cap.json          sha256=<64>
        overlay_spec.json    sha256=<64>
        sizing_exit_commit   sha256=<64>
    """
    lines: list[str] = []
    for name in D06_ORDER:
        key = f"{name}.json"
        lines.append(f"{key:<20s} sha256={result[key]}")
    lines.append(f"{'sizing_exit_commit':<20s} sha256={result['sizing_exit_commit']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute canonical-JSON sha256 for v4.9 SEAL artifacts"
    )
    parser.add_argument(
        "--seal-dir",
        required=True,
        type=pathlib.Path,
        help="Directory containing kelly_bounds.json / exit_commit.json / "
        "dd_cap.json / overlay_spec.json",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail if any file's on-disk bytes differ from canonical "
        "serialization (used by Phase 88 startup verify).",
    )
    args = parser.parse_args(argv)

    try:
        result = compute_hashes(args.seal_dir, strict=args.strict)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(format_output(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
