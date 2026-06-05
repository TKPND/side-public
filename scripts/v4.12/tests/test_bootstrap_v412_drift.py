"""test_bootstrap_v412_drift.py — Phase 103 Plan 02 Task 2 D-45 fail-close tests.

T-103-01 Tampering mitigation:
  - filter_spec.json mutation (M_PRIME drift) → ImportError
  - signal_commit_v412.json mutation (sha256 drift) → ImportError

Pattern: shutil.copy backup → mutate → assert ImportError → restore in finally.
**Restoration MUST happen in finally OUTSIDE pytest.raises** to guarantee
SEAL integrity even when assertions fail (subagent-quality.md Recovery
operation 二次事故防止 rule).

Test names per Plan 103-02-PLAN.md Task 2 Step 2.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SEAL = (
    _REPO_ROOT
    / ".planning"
    / "phases"
    / "101-pre-reg-seal-signal-commit-v412-7th-anchor-macro-stance-estimator-nyquist-audit"
    / "SEAL"
)
_BOOTSTRAP = _REPO_ROOT / "scripts" / "v4.12" / "bootstrap_v412.py"


def _import_bootstrap_v412():
    """Force fresh module load so import-time SEAL gates re-run."""
    sys.modules.pop("bootstrap_v412", None)
    spec = importlib.util.spec_from_file_location("bootstrap_v412", _BOOTSTRAP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bootstrap_v412"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_seal_drift_raises_import_error(tmp_path):
    """D-45: filter_spec.json post_filter_m_prime mutation → ImportError."""
    spec_path = _SEAL / "filter_spec.json"
    backup = tmp_path / "filter_spec.json.bak"
    shutil.copy(spec_path, backup)
    raised = False
    try:
        data = json.loads(spec_path.read_text(encoding="utf-8"))
        data["fwer_denominator"]["post_filter_m_prime"] = 99  # bogus
        spec_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        with pytest.raises(ImportError, match="M_PRIME"):
            _import_bootstrap_v412()
        raised = True
    finally:
        # CRITICAL: restore SEAL even if assertion fails (二次事故防止)
        shutil.copy(backup, spec_path)
        # Drop the (potentially partially-loaded) module so subsequent tests
        # re-trigger import-time SEAL verification on the restored file.
        sys.modules.pop("bootstrap_v412", None)
    assert raised, "ImportError was not raised on M_PRIME mismatch"


def test_canonical_sha256_drift_raises_import_error(tmp_path):
    """D-45: macro_filter_spec.json mutation → canonical sha256 drift → ImportError.

    Targets a sealed_artifact (in the 4-list canonical chain) so that mutation
    triggers the sha256 replay gate. signal_commit_v412.json is NOT in the chain
    per D-23-v412 invariant, so we mutate macro_filter_spec.json instead.
    """
    target = _SEAL / "macro_filter_spec.json"
    backup = tmp_path / "macro_filter_spec.json.bak"
    shutil.copy(target, backup)
    raised = False
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        # Inject an extra key to mutate the canonical sha256 without breaking
        # JSON validity. _drift_marker is unambiguously an injection.
        data["_drift_marker"] = "phase-103-02-task-02-test"
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        with pytest.raises(ImportError, match=r"(sha256|drift|signal_commit)"):
            _import_bootstrap_v412()
        raised = True
    finally:
        # CRITICAL: restore SEAL even if assertion fails (二次事故防止)
        shutil.copy(backup, target)
        sys.modules.pop("bootstrap_v412", None)
    assert raised, "ImportError was not raised on canonical sha256 drift"
