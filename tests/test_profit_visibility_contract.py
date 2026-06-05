"""Contract guards for the v9.0 profit visibility checkpoint."""

from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path

from scripts.generate_risk_contract_v2_adoption_closure_audit import (
    LIVE_RUNTIME_IMPLEMENTATION_PATHS,
    LIVE_RUNTIME_SURFACE_SOURCE,
    live_runtime_surface_absent_check,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DOC = ROOT / "docs/contracts/profit_visibility_contract_v1.md"

OUTCOME_LABELS = ("profit_visible", "honest_null_ship", "plumbing_only")
PROFIT_VISIBLE_REQUIRED_EVIDENCE = (
    "registered candidate",
    "realistic costs",
    "OOS/WFD or holdout evidence",
    "multiple-testing control",
    "leakage checks",
    "sample gates",
)
FORBIDDEN_PROFIT_CLAIMS = (
    "profitability implications without `profit_visible`",
    "live readiness claims",
    "broker/account/network readiness claims",
    "profit claims based on missing, zero, or post-hoc-relaxed costs",
)
NO_LIVE_SCOPE_BAN = (
    "live account fetcher",
    "credential loader/storage",
    "network/SDK/HTTP/socket/cloud secret/subprocess fetch",
    "broker adapter/order/mutation",
    "`side live`",
    "runtime public live emission expansion",
    "public schema expansion",
    "tiny live trade",
)
STAGE_LEDGER_ROWS = (
    "read-only account snapshot",
    "no-order live shadow preflight",
    "broker dry-run or sandbox validation",
    "tiny one-order mutation smoke test",
)
STAGE_LEDGER_COLUMNS = (
    "allowed actions",
    "forbidden actions",
    "required proof",
    "promotion gate",
)
REGISTRATION_FIELDS = (
    "signal family",
    "universe",
    "timeframe",
    "data source",
    "execution assumption",
    "sizing assumption",
    "expected economic rationale",
)
REJECTED_PLACEHOLDERS = ("blank", "unknown", "TBD")
PHASE_163_PROTOCOL_CONTRACT_FILES = (
    "docs/contracts/profit_visibility_registration_protocol_v1.md",
    "docs/contracts/profit_visibility_cost_model_v1.md",
)
PHASE_163_SCOPE_GUARD_ALLOWED_FILES = (
    "docs/contracts/profit_visibility_registration_protocol_v1.md",
    "docs/contracts/profit_visibility_cost_model_v1.md",
    "tests/test_profit_visibility_registration.py",
    "tests/test_profit_visibility_cost_model.py",
)
PHASE_163_PROTOCOL_SURFACES = (
    "split/OOS/WFD or holdout protocol",
    "leakage checks",
    "minimum eligible sample",
    "supported evaluation run",
    "strict byte seal",
    "OpenTimestamps",
    ".ots",
    "pre_anchor_result_bearing_runs",
    "protocol-critical immutability",
    "FWER/Holm",
    "error_rate_target = FWER",
    "alpha = 0.05",
    "finest-granularity hypothesis count",
    "equivalent-control dossier",
    "fees",
    "spread",
    "slippage",
    "turnover",
    "financing",
    "borrow",
    "conversion",
    "market-access assumptions",
    "base cost scenario",
    "adverse cost ladder",
    "cost_model_fingerprint",
    "notional",
    "capacity",
    "leverage",
    "max-loss",
    "measurement constraints only",
)
PHASE_163_PROTOCOL_NON_GOALS = (
    "OTS proof creation",
    "full OTS verifier integration",
    "`ProfitVisibilityReport.v1`",
    "full cost calculator",
    "economic metric computation",
    "runtime CLI wiring",
    "live/account/broker/network/credential paths",
    "public schema expansion",
    "protected archives",
    "golden",
    "seal",
    "parity",
    "SHA fixture updates",
)
FORBIDDEN_PROFIT_VISIBILITY_SURFACE_GLOBS = (
    "*profit_visibility*",
    "*ProfitVisibilityReport*",
)
FORBIDDEN_IGNORED_SURFACE_GLOBS = (
    "*broker*",
    "*credential*",
    "*evidence*",
    "*golden*",
    "*live*",
    "*oos*",
    "*parity*",
    "*seal*",
    "*sha*",
    "*wfd*",
)
FORBIDDEN_CONTRADICTORY_CLAIM_PATTERNS = (
    r"\b(?:is|are|becomes|became|approved as|claimable as)\s+profit[- ]ready\b",
    r"\b(?:is|are|becomes|became|approved as|claimable as)\s+live[- ]ready\b",
    r"\b(?:is|are|becomes|became|approved as|claimable as)\s+broker[- ]ready\b",
    r"\b(?:is|are|becomes|became|approved as|claimable as)\s+account[- ]ready\b",
    r"\bready to go live\b",
    r"\bready for live\b",
    r"\btiny live trade is approved\b",
)
PHASE_162_SCOPE_GUARD_ALLOWED_FILES = (
    "docs/contracts/profit_visibility_contract_v1.md",
    "tests/test_profit_visibility_contract.py",
)
# Named Phase 163 docs-plus-pytest boundary update: this composes the four
# explicit Phase 163 contract artifacts into the Phase 162 execution guard
# without allowing runtime, live/account/broker/network/credential, public
# schema, protected archive, golden, seal, parity, or SHA fixture surfaces.
ALLOWED_PHASE162_CHANGED_FILES = (
    *PHASE_162_SCOPE_GUARD_ALLOWED_FILES,
    *PHASE_163_SCOPE_GUARD_ALLOWED_FILES,
)
ALLOWED_PROFIT_VISIBILITY_SURFACE_FILES = ALLOWED_PHASE162_CHANGED_FILES
GENERATED_PYTHON_CACHE_PARTS = ("/__pycache__/", ".pyc")
GENERATED_DEPENDENCY_PREFIXES = (
    ".gsd/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "artifacts/",
    "env/",
    "logs/",
    "rust/target/",
    "target/",
    "venv/",
)
PHASE162_DEFAULT_DIFF_BASE = "b08ed3b"
PHASE162_SCOPE_GUARD_ENV = "PHASE162_SCOPE_GUARD"
PHASE162_TRACKING_ARTIFACT_PREFIX = (
    ".planning/phases/162-profit-visibility-contract-and-stage-gate-ledger/",
    ".planning/phases/163-candidate-registration-cost-model-and-evaluation-protocol/",
)
PHASE162_TRACKING_ARTIFACT_FILES = (
    ".planning/PROJECT.md",
    ".planning/REQUIREMENTS.md",
    ".planning/ROADMAP.md",
    ".planning/STATE.md",
)
PHASE166_DEFAULT_DIFF_BASE = "9a61ae2d"
PHASE166_SCOPE_GUARD_ENV = "PHASE166_SCOPE_GUARD"
PHASE166_ALLOWED_CHANGED_FILES = frozenset(
    {
        "scripts/validate_profit_visibility_report.py",
        "tests/test_profit_visibility_report.py",
        "tests/test_profit_visibility_null_ship.py",
        "tests/test_profit_visibility_contract.py",
        "tests/test_profit_visibility_closure.py",
    }
)
PHASE166_TRACKING_ARTIFACT_PREFIX = (
    ".planning/phases/166-claim-boundary-and-provenance-firewall/",
)
PHASE166_TRACKING_ARTIFACT_FILES = (
    ".planning/PROJECT.md",
    ".planning/REQUIREMENTS.md",
    ".planning/ROADMAP.md",
    ".planning/STATE.md",
)
PHASE166_SOURCE_GUARD_FILES = (
    Path("scripts/validate_profit_visibility_report.py"),
)
PHASE166_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "alpaca",
        "binance",
        "boto3",
        "ccxt",
        "httpx",
        "ib_insync",
        "requests",
        "socket",
        "urllib",
        "websocket",
    }
)
PHASE166_FORBIDDEN_PUBLIC_SCHEMA_PATHS = (
    ":(glob)docs/contracts/*profit_visibility*.schema.json",
    ":(glob)risk/contracts/**/*profit_visibility*.schema.json",
)
PHASE166_FORBIDDEN_SOURCE_TOKENS = (
    "no_go",
    "nogofamily",
)
PHASE167_DEFAULT_DIFF_BASE = "4fd1341c"
PHASE167_SCOPE_GUARD_ENV = "PHASE167_SCOPE_GUARD"
PHASE167_ALLOWED_WORKTREE_PATHS = frozenset(
    {
        "scripts/profit_visibility_costed_metrics.py",
        "tests/test_profit_visibility_costed_metrics.py",
        "tests/test_profit_visibility_contract.py",
        "tests/test_profit_visibility_closure.py",
    }
)
PHASE167_TRACKING_ARTIFACT_PREFIX = (
    ".planning/phases/167-costed-economic-metric-engine/",
)
PHASE167_TRACKING_ARTIFACT_FILES = (
    ".planning/REQUIREMENTS.md",
    ".planning/ROADMAP.md",
    ".planning/STATE.md",
)
PHASE167_SOURCE_GUARD_FILES = (
    Path("scripts/profit_visibility_costed_metrics.py"),
)
PHASE167_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "alpaca",
        "binance",
        "boto3",
        "ccxt",
        "google",
        "httpx",
        "ib_insync",
        "requests",
        "socket",
        "urllib",
        "websocket",
    }
)
PHASE167_FORBIDDEN_PUBLIC_SCHEMA_PATHS = (
    ":(glob)docs/contracts/*profit_visibility*.schema.json",
    ":(glob)risk/contracts/**/*profit_visibility*.schema.json",
)
PHASE167_FORBIDDEN_SOURCE_TOKENS = (
    "side live",
    "no_go_map_v1.json",
    "docs/reports/no_go_map.md",
    "broker",
    "order_id",
    "account_id",
    "credential",
    "api_key",
    "http://",
    "https://",
)
PHASE168_DEFAULT_DIFF_BASE = "c4702341"
PHASE168_SCOPE_GUARD_ENV = "PHASE168_SCOPE_GUARD"
PHASE168_ALLOWED_WORKTREE_PATHS = frozenset(
    {
        "scripts/profit_visibility_costed_metrics.py",
        "scripts/profit_visibility_statistical_evaluator.py",
        "tests/test_profit_visibility_costed_metrics.py",
        "tests/test_profit_visibility_statistical_evaluator.py",
        "tests/test_profit_visibility_contract.py",
        "tests/test_profit_visibility_closure.py",
        ".planning/REQUIREMENTS.md",
        ".planning/ROADMAP.md",
        ".planning/STATE.md",
    }
)
PHASE168_TRACKING_ARTIFACT_PREFIX = (
    ".planning/phases/168-permutation-and-holm-evaluation/",
)
PHASE168_TRACKING_ARTIFACT_FILES = (
    ".planning/REQUIREMENTS.md",
    ".planning/ROADMAP.md",
    ".planning/STATE.md",
)
PHASE168_SOURCE_GUARD_FILES = (
    Path("scripts/profit_visibility_statistical_evaluator.py"),
)
PHASE168_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "alpaca",
        "binance",
        "boto3",
        "ccxt",
        "google",
        "httpx",
        "ib_insync",
        "requests",
        "socket",
        "urllib",
        "websocket",
    }
)
PHASE168_FORBIDDEN_PUBLIC_SCHEMA_PATHS = (
    ":(glob)docs/contracts/*profit_visibility*.schema.json",
    ":(glob)risk/contracts/**/*profit_visibility*.schema.json",
)
PHASE168_FORBIDDEN_SOURCE_TOKENS = (
    "side live",
    "no_go_map_v1.json",
    "docs/reports/no_go_map.md",
    "order_id",
    "account_id",
    "api_key",
    "http://",
    "https://",
    "paper_forward_ready",
    "ProfitVisibilityReport.v1",
)
PHASE169_DEFAULT_DIFF_BASE = "33dc40b3"
PHASE169_SCOPE_GUARD_ENV = "PHASE169_SCOPE_GUARD"
PHASE169_ALLOWED_WORKTREE_PATHS = frozenset(
    {
        "scripts/profit_visibility_e2e_report.py",
        "scripts/validate_profit_visibility_report.py",
        "tests/test_profit_visibility_e2e_report.py",
        "tests/test_profit_visibility_report.py",
        "tests/test_profit_visibility_contract.py",
        "tests/test_profit_visibility_closure.py",
        "reports/v9.1/profit_visibility_report_v1_fixture.json",
        "reports/v9.1/profit_visibility_decision_report.md",
        "reports/v9.1/profit_visibility_review_bundle_manifest.json",
    }
)
PHASE169_TRACKING_ARTIFACT_PREFIX = (
    ".planning/phases/169-e2e-report-routing-and-closure-proof/",
)
PHASE169_TRACKING_ARTIFACT_FILES = (
    ".planning/REQUIREMENTS.md",
    ".planning/ROADMAP.md",
    ".planning/STATE.md",
)
PHASE169_USER_OWNED_UNTRACKED_FILES = frozenset({".lean-ctx.toml"})
PHASE169_SOURCE_GUARD_FILES = (
    Path("scripts/profit_visibility_e2e_report.py"),
)
PHASE169_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "alpaca",
        "azure",
        "binance",
        "boto3",
        "botocore",
        "ccxt",
        "google",
        "httpx",
        "ib_insync",
        "requests",
        "socket",
        "urllib",
        "websocket",
        "websockets",
    }
)
PHASE169_FORBIDDEN_PUBLIC_SCHEMA_PATHS = (
    ":(glob)docs/contracts/*profit_visibility*.schema.json",
    ":(glob)risk/contracts/**/*profit_visibility*.schema.json",
)
PHASE169_FORBIDDEN_SOURCE_TOKENS = (
    "side live",
    "no_go_map_v1.json",
    "docs/reports/no_go_map.md",
    "no_go_family",
    "no-go family evaluator",
    "source_ingest",
    "real_source",
    "market_data_fetch",
    "fetch_market_data",
    "live_account",
    "account_id",
    "broker_adapter",
    "place_order",
    "submit_order",
    "cancel_order",
    "modify_order",
    "order_id",
    "credential_loader",
    "load_credentials",
    "api_key",
    "secret_key",
    "requests.",
    "httpx.",
    "urllib.request",
    "socket.",
    "boto3.",
    "google.cloud",
    "alpaca.",
    "ccxt.",
    "emit_live",
    "public_live",
    "live_preflight_result",
    '"live_ready": true',
    '"account_ready": true',
    '"broker_ready": true',
    '"network_ready": true',
    '"credential_ready": true',
    '"runtime_ready": true',
)


def read_contract_doc() -> str:
    return CONTRACT_DOC.read_text(encoding="utf-8")


def assert_contract_contains_all(text: str, fragments: tuple[str, ...]) -> None:
    missing = [fragment for fragment in fragments if fragment not in text]
    assert not missing, f"missing contract fragments: {missing}"


def assert_no_forbidden_profit_visibility_surfaces() -> None:
    matches: set[str] = set()
    for pattern in FORBIDDEN_PROFIT_VISIBILITY_SURFACE_GLOBS:
        matches.update(
            git_lines(
                [
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "--",
                    pattern,
                ]
            )
        )
        matches.update(
            git_lines(
                [
                    "ls-files",
                    "--others",
                    "--ignored",
                    "--exclude-standard",
                    "--",
                    pattern,
                ]
            )
        )

    unexpected = [
        path
        for path in sorted(matches)
        if path not in ALLOWED_PROFIT_VISIBILITY_SURFACE_FILES
        and not is_generated_python_cache(path)
    ]
    assert unexpected == []


def assert_contract_has_no_contradictory_readiness_claims(text: str) -> None:
    matches = []
    for pattern in FORBIDDEN_CONTRADICTORY_CLAIM_PATTERNS:
        matches.extend(re.findall(pattern, text, flags=re.IGNORECASE))

    assert matches == []


def git_lines(args: list[str]) -> tuple[str, ...]:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return tuple(path for path in proc.stdout.splitlines() if path)


def git_check(args: list[str]) -> None:
    subprocess.run(["git", *args], cwd=ROOT, check=True, stdout=subprocess.PIPE)


def is_generated_python_cache(path: str) -> bool:
    return all(part in path for part in GENERATED_PYTHON_CACHE_PARTS)


def is_generated_dependency_path(path: str) -> bool:
    return path.startswith(GENERATED_DEPENDENCY_PREFIXES)


def resolve_phase162_diff_base() -> str:
    diff_base = os.environ.get("PHASE162_DIFF_BASE") or PHASE162_DEFAULT_DIFF_BASE
    try:
        resolved = git_lines(["rev-parse", "--verify", f"{diff_base}^{{commit}}"])[0]
    except (subprocess.CalledProcessError, IndexError) as exc:
        raise AssertionError(
            "PHASE162_DIFF_BASE must resolve to a commit for the scope guard"
        ) from exc
    try:
        git_check(["merge-base", "--is-ancestor", resolved, "HEAD"])
    except subprocess.CalledProcessError as exc:
        raise AssertionError(
            "PHASE162_DIFF_BASE must be an ancestor of HEAD for the scope guard"
        ) from exc
    if not git_lines(["diff", "--name-only", f"{resolved}..HEAD", "--"]):
        raise AssertionError(
            "PHASE162_DIFF_BASE must produce a non-empty phase diff for the scope guard"
        )
    return resolved


def tracked_changed_paths_for_phase() -> tuple[str, ...]:
    diff_base = resolve_phase162_diff_base()
    commands: list[list[str]] = []
    commands.append(["diff", "--name-only", f"{diff_base}..HEAD", "--"])
    commands.extend(
        (
            ["diff", "--cached", "--name-only", "--"],
            ["diff", "--name-only", "--"],
        )
    )

    paths: set[str] = set()
    for command in commands:
        paths.update(git_lines(command))
    return tuple(sorted(paths))


def untracked_or_ignored_paths_for_phase() -> tuple[str, ...]:
    paths: set[str] = set(git_lines(["ls-files", "--others", "--exclude-standard"]))
    for pattern in FORBIDDEN_IGNORED_SURFACE_GLOBS:
        paths.update(
            git_lines(
                [
                    "ls-files",
                    "--others",
                    "--ignored",
                    "--exclude-standard",
                    "--",
                    pattern,
                ]
            )
        )
    return tuple(sorted(paths))


def is_phase162_tracking_artifact(path: str) -> bool:
    return path in PHASE162_TRACKING_ARTIFACT_FILES or path.startswith(
        PHASE162_TRACKING_ARTIFACT_PREFIX
    )


def is_phase166_tracking_artifact(path: str) -> bool:
    return path in PHASE166_TRACKING_ARTIFACT_FILES or path.startswith(
        PHASE166_TRACKING_ARTIFACT_PREFIX
    )


def is_phase167_tracking_artifact(path: str) -> bool:
    return path in PHASE167_TRACKING_ARTIFACT_FILES or path.startswith(
        PHASE167_TRACKING_ARTIFACT_PREFIX
    )


def is_phase168_tracking_artifact(path: str) -> bool:
    return path in PHASE168_TRACKING_ARTIFACT_FILES or path.startswith(
        PHASE168_TRACKING_ARTIFACT_PREFIX
    )


def is_phase169_tracking_artifact(path: str) -> bool:
    return path in PHASE169_TRACKING_ARTIFACT_FILES or path.startswith(
        PHASE169_TRACKING_ARTIFACT_PREFIX
    )


def resolve_phase166_diff_base() -> str:
    diff_base = os.environ.get("PHASE166_DIFF_BASE") or PHASE166_DEFAULT_DIFF_BASE
    try:
        resolved = git_lines(["rev-parse", "--verify", f"{diff_base}^{{commit}}"])[0]
    except (subprocess.CalledProcessError, IndexError) as exc:
        raise AssertionError(
            "PHASE166_DIFF_BASE must resolve to a commit for the scope guard"
        ) from exc
    try:
        git_check(["merge-base", "--is-ancestor", resolved, "HEAD"])
    except subprocess.CalledProcessError as exc:
        raise AssertionError(
            "PHASE166_DIFF_BASE must be an ancestor of HEAD for the scope guard"
        ) from exc
    if not git_lines(["diff", "--name-only", f"{resolved}..HEAD", "--"]):
        raise AssertionError(
            "PHASE166_DIFF_BASE must produce a non-empty phase diff for the scope guard"
        )
    return resolved


def phase166_changed_paths_for_scope_guard() -> tuple[str, ...]:
    diff_base = resolve_phase166_diff_base()
    paths: set[str] = set(git_lines(["diff", "--name-only", f"{diff_base}..HEAD"]))
    paths.update(git_lines(["diff", "--cached", "--name-only", "--"]))
    paths.update(git_lines(["diff", "--name-only", "--"]))
    paths.update(git_lines(["ls-files", "--others", "--exclude-standard"]))
    return tuple(sorted(paths))


def resolve_phase167_diff_base() -> str:
    diff_base = os.environ.get("PHASE167_DIFF_BASE") or PHASE167_DEFAULT_DIFF_BASE
    try:
        resolved = git_lines(["rev-parse", "--verify", f"{diff_base}^{{commit}}"])[0]
    except (subprocess.CalledProcessError, IndexError) as exc:
        raise AssertionError(
            "PHASE167_DIFF_BASE must resolve to a commit for the scope guard"
        ) from exc
    try:
        git_check(["merge-base", "--is-ancestor", resolved, "HEAD"])
    except subprocess.CalledProcessError as exc:
        raise AssertionError(
            "PHASE167_DIFF_BASE must be an ancestor of HEAD for the scope guard"
        ) from exc
    if not git_lines(["diff", "--name-only", f"{resolved}..HEAD", "--"]):
        raise AssertionError(
            "PHASE167_DIFF_BASE must produce a non-empty phase diff for the scope guard"
        )
    return resolved


def phase167_changed_paths_for_scope_guard() -> tuple[str, ...]:
    diff_base = resolve_phase167_diff_base()
    paths: set[str] = set(git_lines(["diff", "--name-only", f"{diff_base}..HEAD"]))
    paths.update(git_lines(["diff", "--cached", "--name-only", "--"]))
    paths.update(git_lines(["diff", "--name-only", "--"]))
    paths.update(git_lines(["ls-files", "--others", "--exclude-standard"]))
    return tuple(sorted(paths))


def resolve_phase168_diff_base() -> str:
    diff_base = os.environ.get("PHASE168_DIFF_BASE") or PHASE168_DEFAULT_DIFF_BASE
    try:
        resolved = git_lines(["rev-parse", "--verify", f"{diff_base}^{{commit}}"])[0]
    except (subprocess.CalledProcessError, IndexError) as exc:
        raise AssertionError(
            "PHASE168_DIFF_BASE must resolve to a commit for the scope guard"
        ) from exc
    try:
        git_check(["merge-base", "--is-ancestor", resolved, "HEAD"])
    except subprocess.CalledProcessError as exc:
        raise AssertionError(
            "PHASE168_DIFF_BASE must be an ancestor of HEAD for the scope guard"
        ) from exc
    if not git_lines(["diff", "--name-only", f"{resolved}..HEAD", "--"]):
        raise AssertionError(
            "PHASE168_DIFF_BASE must produce a non-empty phase diff for the scope guard"
        )
    return resolved


def phase168_changed_paths_for_scope_guard() -> tuple[str, ...]:
    diff_base = resolve_phase168_diff_base()
    paths: set[str] = set(git_lines(["diff", "--name-only", f"{diff_base}..HEAD"]))
    paths.update(git_lines(["diff", "--cached", "--name-only", "--"]))
    paths.update(git_lines(["diff", "--name-only", "--"]))
    paths.update(git_lines(["ls-files", "--others", "--exclude-standard"]))
    return tuple(sorted(paths))


def resolve_phase169_diff_base() -> str:
    diff_base = os.environ.get("PHASE169_DIFF_BASE") or PHASE169_DEFAULT_DIFF_BASE
    try:
        resolved = git_lines(["rev-parse", "--verify", f"{diff_base}^{{commit}}"])[0]
    except (subprocess.CalledProcessError, IndexError) as exc:
        raise AssertionError(
            "PHASE169_DIFF_BASE must resolve to a commit for the scope guard"
        ) from exc
    try:
        git_check(["merge-base", "--is-ancestor", resolved, "HEAD"])
    except subprocess.CalledProcessError as exc:
        raise AssertionError(
            "PHASE169_DIFF_BASE must be an ancestor of HEAD for the scope guard"
        ) from exc
    head = git_lines(["rev-parse", "--verify", "HEAD^{commit}"])[0]
    if resolved == head:
        raise AssertionError("PHASE169_DIFF_BASE must not resolve to HEAD")
    if not git_lines(["diff", "--name-only", f"{resolved}..HEAD", "--"]):
        raise AssertionError(
            "PHASE169_DIFF_BASE must produce a non-empty phase diff for the scope guard"
        )
    return resolved


def phase169_changed_paths_for_scope_guard() -> tuple[str, ...]:
    diff_base = resolve_phase169_diff_base()
    paths: set[str] = set(git_lines(["diff", "--name-only", f"{diff_base}..HEAD"]))
    paths.update(git_lines(["diff", "--cached", "--name-only", "--"]))
    paths.update(git_lines(["diff", "--name-only", "--"]))
    paths.update(git_lines(["ls-files", "--others", "--exclude-standard"]))
    return tuple(sorted(paths))


def unexpected_phase167_changed_paths(paths: tuple[str, ...] | list[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if path not in PHASE167_ALLOWED_WORKTREE_PATHS
        and not is_phase167_tracking_artifact(path)
        and not is_generated_python_cache(path)
        and not is_generated_dependency_path(path)
    )


def unexpected_phase168_changed_paths(paths: tuple[str, ...] | list[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if path not in PHASE168_ALLOWED_WORKTREE_PATHS
        and not is_phase168_tracking_artifact(path)
        and not is_generated_python_cache(path)
        and not is_generated_dependency_path(path)
    )


def unexpected_phase169_changed_paths(paths: tuple[str, ...] | list[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if path not in PHASE169_ALLOWED_WORKTREE_PATHS
        and not is_phase169_tracking_artifact(path)
        and path not in PHASE169_USER_OWNED_UNTRACKED_FILES
        and not is_generated_python_cache(path)
        and not is_generated_dependency_path(path)
    )


def assert_phase166_scope_guard_allows_only_planned_changed_paths() -> None:
    if os.environ.get(PHASE166_SCOPE_GUARD_ENV) != "1":
        return

    unexpected = [
        path
        for path in phase166_changed_paths_for_scope_guard()
        if path not in PHASE166_ALLOWED_CHANGED_FILES
        and not is_phase166_tracking_artifact(path)
        and not is_generated_python_cache(path)
        and not is_generated_dependency_path(path)
    ]
    assert unexpected == []


def assert_phase168_scope_guard_allows_only_planned_changed_paths() -> None:
    if os.environ.get(PHASE168_SCOPE_GUARD_ENV) != "1":
        return

    unexpected = unexpected_phase168_changed_paths(
        phase168_changed_paths_for_scope_guard()
    )
    assert unexpected == []


def assert_phase167_scope_guard_allows_only_planned_changed_paths() -> None:
    if os.environ.get(PHASE167_SCOPE_GUARD_ENV) != "1":
        return

    unexpected = unexpected_phase167_changed_paths(
        phase167_changed_paths_for_scope_guard()
    )
    assert unexpected == []


def assert_phase169_scope_guard_allows_only_planned_changed_paths() -> None:
    if os.environ.get(PHASE169_SCOPE_GUARD_ENV) != "1":
        return

    unexpected = unexpected_phase169_changed_paths(
        phase169_changed_paths_for_scope_guard()
    )
    assert unexpected == []


def phase166_import_roots(path: Path) -> set[str]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def phase166_public_schema_paths() -> tuple[str, ...]:
    matches: set[str] = set()
    for pattern in PHASE166_FORBIDDEN_PUBLIC_SCHEMA_PATHS:
        matches.update(
            git_lines(
                [
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "--",
                    pattern,
                ]
            )
        )
    return tuple(sorted(matches))


def phase167_public_schema_paths() -> tuple[str, ...]:
    matches: set[str] = set()
    for pattern in PHASE167_FORBIDDEN_PUBLIC_SCHEMA_PATHS:
        matches.update(
            git_lines(
                [
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "--",
                    pattern,
                ]
            )
        )
    return tuple(sorted(matches))


def phase168_public_schema_paths() -> tuple[str, ...]:
    matches: set[str] = set()
    for pattern in PHASE168_FORBIDDEN_PUBLIC_SCHEMA_PATHS:
        matches.update(
            git_lines(
                [
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "--",
                    pattern,
                ]
            )
        )
    return tuple(sorted(matches))


def phase169_public_schema_paths() -> tuple[str, ...]:
    matches: set[str] = set()
    for pattern in PHASE169_FORBIDDEN_PUBLIC_SCHEMA_PATHS:
        matches.update(
            git_lines(
                [
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "--",
                    pattern,
                ]
            )
        )
    return tuple(sorted(matches))


def assert_phase166_forbidden_surfaces_absent() -> None:
    assert live_runtime_surface_absent_check()["passed"] is True
    assert phase166_public_schema_paths() == ()

    forbidden_imports: dict[str, list[str]] = {}
    forbidden_tokens: dict[str, list[str]] = {}
    for path in PHASE166_SOURCE_GUARD_FILES:
        roots = phase166_import_roots(path)
        blocked_roots = sorted(roots & PHASE166_FORBIDDEN_IMPORT_ROOTS)
        if blocked_roots:
            forbidden_imports[path.as_posix()] = blocked_roots

        source = (ROOT / path).read_text(encoding="utf-8").lower()
        blocked_tokens = [
            token for token in PHASE166_FORBIDDEN_SOURCE_TOKENS if token in source
        ]
        if blocked_tokens:
            forbidden_tokens[path.as_posix()] = blocked_tokens

    assert forbidden_imports == {}
    assert forbidden_tokens == {}


def assert_phase167_forbidden_surfaces_absent() -> None:
    assert live_runtime_surface_absent_check()["passed"] is True
    assert phase167_public_schema_paths() == ()

    forbidden_imports: dict[str, list[str]] = {}
    forbidden_tokens: dict[str, list[str]] = {}
    for path in PHASE167_SOURCE_GUARD_FILES:
        roots = phase166_import_roots(path)
        blocked_roots = sorted(roots & PHASE167_FORBIDDEN_IMPORT_ROOTS)
        if blocked_roots:
            forbidden_imports[path.as_posix()] = blocked_roots

        source = (ROOT / path).read_text(encoding="utf-8").lower()
        blocked_tokens = [
            token
            for token in PHASE167_FORBIDDEN_SOURCE_TOKENS
            if token.lower() in source
        ]
        if blocked_tokens:
            forbidden_tokens[path.as_posix()] = blocked_tokens

    assert forbidden_imports == {}
    assert forbidden_tokens == {}


def assert_phase168_forbidden_surfaces_absent() -> None:
    assert live_runtime_surface_absent_check()["passed"] is True
    assert phase168_public_schema_paths() == ()

    forbidden_imports: dict[str, list[str]] = {}
    forbidden_tokens: dict[str, list[str]] = {}
    for path in PHASE168_SOURCE_GUARD_FILES:
        roots = phase166_import_roots(path)
        blocked_roots = sorted(roots & PHASE168_FORBIDDEN_IMPORT_ROOTS)
        if blocked_roots:
            forbidden_imports[path.as_posix()] = blocked_roots

        source = (ROOT / path).read_text(encoding="utf-8")
        lowered = source.lower()
        blocked_tokens = [
            token
            for token in PHASE168_FORBIDDEN_SOURCE_TOKENS
            if token.lower() in lowered
        ]
        if blocked_tokens:
            forbidden_tokens[path.as_posix()] = blocked_tokens

    assert forbidden_imports == {}
    assert forbidden_tokens == {}


def assert_phase169_forbidden_surfaces_absent() -> None:
    assert live_runtime_surface_absent_check()["passed"] is True
    assert phase169_public_schema_paths() == ()

    forbidden_imports: dict[str, list[str]] = {}
    forbidden_tokens: dict[str, list[str]] = {}
    for path in PHASE169_SOURCE_GUARD_FILES:
        roots = phase166_import_roots(path)
        blocked_roots = sorted(roots & PHASE169_FORBIDDEN_IMPORT_ROOTS)
        if blocked_roots:
            forbidden_imports[path.as_posix()] = blocked_roots

        source = (ROOT / path).read_text(encoding="utf-8")
        lowered = source.lower()
        blocked_tokens = [
            token
            for token in PHASE169_FORBIDDEN_SOURCE_TOKENS
            if token.lower() in lowered
        ]
        if blocked_tokens:
            forbidden_tokens[path.as_posix()] = blocked_tokens

    assert forbidden_imports == {}
    assert forbidden_tokens == {}


def assert_phase168_uses_existing_holm_helper() -> None:
    source = (ROOT / "scripts/profit_visibility_statistical_evaluator.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    called_names = {
        node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "apply_holm_fwer" in source
    assert "apply_holm_fwer" in called_names
    assert "_holm_adjusted_pvalues" not in source
    assert "def apply_holm_fwer" not in source


def assert_changed_files_are_phase162_allowlist() -> None:
    if os.environ.get(PHASE162_SCOPE_GUARD_ENV) != "1":
        return

    tracked_changed = tuple(
        path
        for path in tracked_changed_paths_for_phase()
        if not is_phase162_tracking_artifact(path)
    )
    untracked_or_ignored_changed = tuple(
        path
        for path in untracked_or_ignored_paths_for_phase()
        if not is_phase162_tracking_artifact(path)
        and not is_generated_python_cache(path)
        and not is_generated_dependency_path(path)
    )
    unexpected = [
        path
        for path in tracked_changed + untracked_or_ignored_changed
        if path not in ALLOWED_PHASE162_CHANGED_FILES
    ]

    assert unexpected == []


def test_outcome_truth_table_pins_allowed_and_forbidden_claims() -> None:
    text = read_contract_doc()

    assert "## Outcome Truth Table" in text
    assert_contract_contains_all(text, OUTCOME_LABELS)
    assert_contract_contains_all(text, PROFIT_VISIBLE_REQUIRED_EVIDENCE)
    assert_contract_contains_all(text, FORBIDDEN_PROFIT_CLAIMS)
    assert "missing, zero, or post-hoc-relaxed costs force `profit_visible = false`" in text
    assert "must not be used as a profit-readiness or live-readiness label" in text
    assert "infrastructure readiness is not profit readiness" in text
    assert_contract_has_no_contradictory_readiness_claims(text)


def test_v9_scope_no_live_boundary_is_pinned() -> None:
    text = read_contract_doc()

    assert "## v9.0 No-Live Scope Ban" in text
    assert_contract_contains_all(text, NO_LIVE_SCOPE_BAN)
    assert "forbidden surface is a v9.0 scope violation that blocks the v9.0 claim" in text
    assert "cannot be downgraded to `plumbing_only`" in text
    assert "targeted grep is inspection evidence" in text
    assert "not a broad repo-wide forbidden-word hard fail" in text

    assert LIVE_RUNTIME_SURFACE_SOURCE.as_posix() == "rust/side-cli/src/main.rs"
    assert tuple(path.as_posix() for path in LIVE_RUNTIME_IMPLEMENTATION_PATHS) == (
        "rust/side-cli/src/cmd/live.rs",
        "rust/side-cli/src/cmd/live",
        "rust/side-cli/src/cmd/broker.rs",
        "rust/side-cli/src/cmd/broker",
        "rust/side-engine/src/live.rs",
        "rust/side-engine/src/live",
        "rust/side-engine/src/broker.rs",
        "rust/side-engine/src/broker",
    )
    assert live_runtime_surface_absent_check()["passed"] is True

    forbidden_matches = []
    for pattern in (
        "rust/side-cli/src/cmd/live*",
        "rust/side-cli/src/cmd/broker*",
        "rust/side-engine/src/live*",
        "rust/side-engine/src/broker*",
    ):
        forbidden_matches.extend(ROOT.glob(pattern))

    assert forbidden_matches == []


def test_stage_gate_ledger_pins_required_rows_and_gates() -> None:
    text = read_contract_doc()

    assert "## Post-v9.0 Stage-Gate Ledger" in text
    assert_contract_contains_all(text, STAGE_LEDGER_ROWS)
    assert_contract_contains_all(text, STAGE_LEDGER_COLUMNS)
    assert "no automatic promotion" in text
    assert "requires separate phase approval" in text
    assert "tiny one-order mutation smoke test" in text
    assert (
        "read-only account, no-order shadow, broker dry-run/sandbox, hard notional "
        "cap, max-loss cap, manual kill, idempotency, and reconciliation gates"
        in text
    )


def test_candidate_registration_surface_pins_minimal_fields() -> None:
    text = read_contract_doc()

    assert "## Candidate Registration Surface" in text
    assert_contract_contains_all(text, REGISTRATION_FIELDS)
    assert_contract_contains_all(text, REJECTED_PLACEHOLDERS)
    assert "minimal example" in text
    assert "expected economic rationale is a pre-evaluation hypothesis, not profit evidence" in text
    assert "thin, post-hoc, or result-derived rationale blocks `profit_visible` claims" in text


def test_phase_163_protocol_links_are_concrete_not_reserved() -> None:
    text = read_contract_doc()

    assert "## Phase 163 Protocol Contracts" in text
    assert_contract_contains_all(text, PHASE_163_PROTOCOL_CONTRACT_FILES)
    assert_contract_contains_all(text, PHASE_163_PROTOCOL_SURFACES)
    assert_contract_contains_all(text, PHASE_163_PROTOCOL_NON_GOALS)
    assert "## Reserved For Phase 163" not in text


def test_phase_163_scope_guard_allows_only_named_docs_and_pytest_artifacts() -> None:
    assert PHASE_163_SCOPE_GUARD_ALLOWED_FILES == (
        "docs/contracts/profit_visibility_registration_protocol_v1.md",
        "docs/contracts/profit_visibility_cost_model_v1.md",
        "tests/test_profit_visibility_registration.py",
        "tests/test_profit_visibility_cost_model.py",
    )
    assert ALLOWED_PHASE162_CHANGED_FILES == (
        *PHASE_162_SCOPE_GUARD_ALLOWED_FILES,
        *PHASE_163_SCOPE_GUARD_ALLOWED_FILES,
    )
    assert ALLOWED_PROFIT_VISIBILITY_SURFACE_FILES == ALLOWED_PHASE162_CHANGED_FILES


def test_profit_visibility_scope_does_not_create_schema_evidence_or_archive_surfaces() -> None:
    if os.environ.get(PHASE162_SCOPE_GUARD_ENV) != "1":
        return

    assert_no_forbidden_profit_visibility_surfaces()
    assert_changed_files_are_phase162_allowlist()


def test_phase166_forbidden_surfaces_and_source_ingest_absent() -> None:
    assert_phase166_forbidden_surfaces_absent()


def test_phase166_scope_guard_allows_only_planned_changed_paths() -> None:
    assert_phase166_scope_guard_allows_only_planned_changed_paths()


def test_phase167_forbidden_surfaces_and_source_ingest_absent() -> None:
    assert_phase167_forbidden_surfaces_absent()


def test_phase167_scope_guard_allows_only_planned_changed_paths() -> None:
    assert_phase167_scope_guard_allows_only_planned_changed_paths()


def test_phase168_forbidden_surfaces_and_source_ingest_absent() -> None:
    assert_phase168_forbidden_surfaces_absent()


def test_phase168_scope_guard_allows_only_planned_changed_paths() -> None:
    assert_phase168_scope_guard_allows_only_planned_changed_paths()


def test_phase169_forbidden_surfaces_and_source_ingest_absent() -> None:
    assert_phase169_forbidden_surfaces_absent()


def test_phase169_scope_guard_allows_only_planned_changed_paths() -> None:
    allowed_examples = tuple(sorted(PHASE169_ALLOWED_WORKTREE_PATHS))
    assert unexpected_phase169_changed_paths(allowed_examples) == []
    assert unexpected_phase169_changed_paths(
        (
            ".planning/phases/169-e2e-report-routing-and-closure-proof/169-04-SUMMARY.md",
            ".lean-ctx.toml",
        )
    ) == []
    assert unexpected_phase169_changed_paths(("README.md", "pyproject.toml")) == [
        "README.md",
        "pyproject.toml",
    ]

    assert_phase169_scope_guard_allows_only_planned_changed_paths()


def test_phase168_statistical_evaluator_uses_existing_holm_helper() -> None:
    assert_phase168_uses_existing_holm_helper()
