"""
prewarm_hf_cache.py — Phase 101 Wave 0 Task 3: HuggingFace model cache pre-warm
+ commit_sha pinning for FinBERT and RoBERTa.

Resolves Wave 1 dependency: macro_stance_estimator.py must load models at frozen
commit_sha (not floating tag) for SEAL replay determinism.

Process:
    1. HfApi().model_info(repo_id).sha → resolve current commit_sha (40-char hex)
    2. snapshot_download(repo_id, revision=sha) → cache files locally
    3. Emit HF_COMMIT_SHA.json with both SHAs + UTC ISO timestamp

Usage:
    uv run python scripts/v4.12/prewarm_hf_cache.py
    uv run python scripts/v4.12/prewarm_hf_cache.py --offline-ok  # use any cached sha if HF unreachable

Output: scripts/v4.12/HF_COMMIT_SHA.json
{
  "finbert":  {"repo_id": "ProsusAI/finbert",                                "sha": "<40-hex>"},
  "roberta":  {"repo_id": "cardiffnlp/twitter-roberta-base-sentiment-latest","sha": "<40-hex>"},
  "resolved_at": "2026-04-26T..."
}

Citations: D-69 (FinBERT/RoBERTa pin), D-23-v412 (SEAL replay determinism),
101-01-PLAN.md Task 3.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Sentinel value used when --offline-ok is set and HF is unreachable.
# Tests should treat this as "not yet resolved" — Wave 1 must rerun online.
_OFFLINE_SENTINEL = "0" * 40

MODELS: dict[str, str] = {
    "finbert": "ProsusAI/finbert",
    "roberta": "cardiffnlp/twitter-roberta-base-sentiment-latest",
}

OUTPUT_PATH = Path("scripts/v4.12/HF_COMMIT_SHA.json")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _resolve_and_download(
    alias: str, repo_id: str, *, offline_ok: bool
) -> dict[str, str]:
    """Resolve commit_sha via HfApi, then snapshot_download at that revision."""
    try:
        from huggingface_hub import HfApi, snapshot_download
    except ImportError:
        print(
            "ERROR: huggingface_hub not installed. Run: uv add huggingface_hub",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        info = HfApi().model_info(repo_id)
        sha = info.sha or ""
    except Exception as exc:  # network / auth / rate-limit
        if offline_ok:
            print(
                f"WARN: HF unreachable for {repo_id} ({exc}); writing offline sentinel.",
                file=sys.stderr,
            )
            return {"repo_id": repo_id, "sha": _OFFLINE_SENTINEL}
        print(f"ERROR: HfApi.model_info({repo_id}) failed: {exc}", file=sys.stderr)
        sys.exit(2)

    if not SHA_RE.match(sha):
        print(
            f"ERROR: {repo_id} returned non-conforming sha={sha!r} "
            f"(expected 40-char hex).",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        snapshot_download(repo_id=repo_id, revision=sha)
    except Exception as exc:
        if offline_ok:
            print(
                f"WARN: snapshot_download({repo_id}@{sha}) failed ({exc}); "
                f"continuing in offline-ok mode.",
                file=sys.stderr,
            )
        else:
            print(
                f"ERROR: snapshot_download({repo_id}@{sha}) failed: {exc}",
                file=sys.stderr,
            )
            sys.exit(2)

    print(f"OK: {alias} ({repo_id}) pinned at {sha}")
    return {"repo_id": repo_id, "sha": sha}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="prewarm_hf_cache.py — pin FinBERT + RoBERTa to commit_sha"
    )
    parser.add_argument(
        "--offline-ok",
        action="store_true",
        help="If HF unreachable, write offline sentinel sha (000…) instead of failing",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_PATH,
        help=f"Output JSON path (default: {OUTPUT_PATH})",
    )
    args = parser.parse_args(argv)

    payload: dict[str, object] = {}
    for alias, repo_id in MODELS.items():
        payload[alias] = _resolve_and_download(
            alias, repo_id, offline_ok=args.offline_ok
        )
    payload["resolved_at"] = datetime.now(tz=timezone.utc).isoformat()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(f"OK: wrote {args.output}")


if __name__ == "__main__":
    main()
