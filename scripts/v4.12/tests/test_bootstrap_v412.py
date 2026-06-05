"""test_bootstrap_v412.py — Phase 103 Plan 02 Task 1 RED tests.

SHIP-V412-01 / SHIP-V412-02:
  - bootstrap_v412.py imports cleanly with M_PRIME_V412=32 hardcode
  - p_adj_v412.json emitted with m_prime=32 + 32 results (tested + padded)
  - _SIGNAL_COMMIT_V412 sha256 header == 91602348...

Test names per Plan 103-02-PLAN.md Task 1 Step 4.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BOOTSTRAP = _REPO_ROOT / "scripts" / "v4.12" / "bootstrap_v412.py"
_P_ADJ_OUTPUT = _REPO_ROOT / "data" / "v4.12" / "p_adj_v412.json"


def _load_bootstrap_v412():
    """Helper: dynamic import of scripts/v4.12/bootstrap_v412.py."""
    spec = importlib.util.spec_from_file_location("bootstrap_v412", _BOOTSTRAP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bootstrap_v412"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_bootstrap_imports_without_drift():
    """SHIP-V412-01: bootstrap_v412.py imports + M_PRIME_V412=32 hardcode."""
    mod = _load_bootstrap_v412()
    assert mod.M_PRIME_V412 == 32, (
        f"M_PRIME_V412 must equal 32 (SEAL filter_spec.json), got {mod.M_PRIME_V412}"
    )


def test_p_adj_emission_m_prime_32():
    """SHIP-V412-01: bootstrap_v412.main() emits p_adj_v412.json with m_prime=32."""
    # Run via subprocess to mimic real CLI invocation (D-02 minimal-diff fork).
    result = subprocess.run(
        ["uv", "run", "python", str(_BOOTSTRAP)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"bootstrap_v412.py exit != 0:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert _P_ADJ_OUTPUT.exists(), f"p_adj_v412.json not emitted at {_P_ADJ_OUTPUT}"
    doc = json.loads(_P_ADJ_OUTPUT.read_text(encoding="utf-8"))
    assert doc["provenance"]["m_prime"] == 32, (
        f"m_prime in p_adj_v412.json must be 32, got {doc['provenance']['m_prime']}"
    )
    assert len(doc["results"]) == 32, (
        f"results array length must be M_PRIME_V412=32, got {len(doc['results'])}"
    )


def test_signal_commit_v412_header_present():
    """SHIP-V412-01: _SIGNAL_COMMIT_V412 sha256 header embed (D-02)."""
    mod = _load_bootstrap_v412()
    expected = "91602348c0e08a3216d914dc159a48112f8fab64ccf8cce9464fdf7814a96555"
    assert mod._SIGNAL_COMMIT_V412 == expected, (
        f"_SIGNAL_COMMIT_V412 must equal Phase 101 SEAL hash, got {mod._SIGNAL_COMMIT_V412}"
    )
