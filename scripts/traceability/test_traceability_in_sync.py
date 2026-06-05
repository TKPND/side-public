"""v4.10 Phase 91 TRACE-01: Traceability drift gate tests (TDD RED/GREEN).

Enforces that REQUIREMENTS.md Traceability table stays in-sync with ROADMAP.md
via sync_requirements.py. Used as a CI fail-close gate (D-34: pytest, not git hook).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Repo root resolution
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Add repo root to sys.path for module import
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _import_module():
    """Import sync_requirements using importlib (v4.10 dot-in-dirname workaround)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sync_requirements",
        _REPO_ROOT / "scripts" / "traceability" / "sync_requirements.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_module_with_mutable_archive(tmp_path: Path, monkeypatch):
    """Load sync module against temp copies so tests never mutate frozen archives."""
    mod = _import_module()
    roadmap_path = tmp_path / "v4.10-ROADMAP.md"
    requirements_path = tmp_path / "v4.10-REQUIREMENTS.md"
    roadmap_path.write_text(
        mod._ARCHIVED_ROADMAP_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    requirements_path.write_text(
        mod._ARCHIVED_REQUIREMENTS_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_ROADMAP_PATH", roadmap_path)
    monkeypatch.setattr(mod, "_REQUIREMENTS_PATH", requirements_path)
    monkeypatch.setattr(mod, "_USE_ACTIVE_PLANNING", False)
    return mod


# ---------------------------------------------------------------------------
# Test 1: parse_roadmap returns dict with string keys for phases 88-91
# ---------------------------------------------------------------------------
def test_parse_roadmap_keys():
    """parse_roadmap() dict must contain string keys '88', '89', '90', '91'."""
    mod = _import_module()
    result = mod.parse_roadmap()
    for phase in ("88", "89", "90", "91"):
        assert phase in result, f"parse_roadmap() missing key {phase!r}: got {list(result.keys())}"


# ---------------------------------------------------------------------------
# Test 2: parse_roadmap()["91"] == 5 specific REQs in order
# ---------------------------------------------------------------------------
def test_parse_roadmap_phase91_reqs():
    """Phase 91 must have exactly the 5 REQs in ROADMAP order."""
    mod = _import_module()
    result = mod.parse_roadmap()
    expected = ["SHIP-01", "SHIP-02", "SHIP-03", "SHIP-04", "TRACE-01"]
    assert result["91"] == expected, (
        f"Phase 91 REQs mismatch.\nExpected: {expected}\nGot:      {result['91']}"
    )


# ---------------------------------------------------------------------------
# Test 3: parse_roadmap()["88"] includes SEAL-V410-01/02/03 + CELLID-01
# ---------------------------------------------------------------------------
def test_parse_roadmap_phase88_reqs():
    """Phase 88 must include SEAL-V410-01, SEAL-V410-02, SEAL-V410-03, CELLID-01."""
    mod = _import_module()
    result = mod.parse_roadmap()
    for req in ("SEAL-V410-01", "SEAL-V410-02", "SEAL-V410-03", "CELLID-01"):
        assert req in result["88"], (
            f"Phase 88 missing {req!r}: got {result['88']}"
        )


# ---------------------------------------------------------------------------
# Test 4: sync_requirements(check=True) idempotent — two calls both return True
# ---------------------------------------------------------------------------
def test_sync_requirements_idempotent(tmp_path, monkeypatch):
    """sync_requirements(check=True) twice on same repo state → both True."""
    mod = _import_module_with_mutable_archive(tmp_path, monkeypatch)
    # Run sync (write mode) first to ensure table is up to date
    mod.sync_requirements(check=False)
    # Now two consecutive check calls must both pass
    first = mod.sync_requirements(check=True)
    second = mod.sync_requirements(check=True)
    assert first is True, "First check=True call returned False (not in-sync after write)"
    assert second is True, "Second check=True call returned False (not idempotent)"


# ---------------------------------------------------------------------------
# Test 5: manual drift → detect → fix → recover cycle
# ---------------------------------------------------------------------------
def test_sync_requirements_drift_roundtrip(tmp_path, monkeypatch):
    """Manually delete 1 Traceability row → check=True False → write → re-check True."""
    mod = _import_module_with_mutable_archive(tmp_path, monkeypatch)
    req_path = mod._REQUIREMENTS_PATH

    # First ensure file is in-sync
    mod.sync_requirements(check=False)

    # Read the current content
    original = req_path.read_text(encoding="utf-8")

    try:
        # Introduce drift: remove a row from the Traceability table
        drifted = original.replace("| TRACE-01 | Phase 91 | Satisfied |", "", 1)
        # If exact row not found with "Satisfied", try any status
        if drifted == original:
            import re
            drifted = re.sub(r"\| TRACE-01 \| Phase 91 \| \w+ \|", "", original, count=1)
        # Also handle the row format without trailing space
        if drifted == original:
            lines = original.splitlines(keepends=True)
            new_lines = [l for l in lines if "TRACE-01" not in l or "Phase 91" not in l]
            drifted = "".join(new_lines)

        assert drifted != original, "Could not introduce drift — test setup failed"
        req_path.write_text(drifted, encoding="utf-8")

        # check=True should detect the drift
        drift_detected = mod.sync_requirements(check=True)
        assert drift_detected is False, (
            "sync_requirements(check=True) returned True on drifted file — drift not detected"
        )

        # check=False should fix the drift
        mod.sync_requirements(check=False)

        # re-check should pass
        recovered = mod.sync_requirements(check=True)
        assert recovered is True, (
            "sync_requirements(check=True) still False after write — recovery failed"
        )
    finally:
        # Restore original to ensure test isolation
        req_path.write_text(original, encoding="utf-8")
        mod.sync_requirements(check=False)


# ---------------------------------------------------------------------------
# Test 6: content outside Traceability section is byte-identical after sync
# ---------------------------------------------------------------------------
def test_sync_does_not_touch_outside_traceability(tmp_path, monkeypatch):
    """Section outside ## Traceability must be byte-identical before and after sync."""
    mod = _import_module_with_mutable_archive(tmp_path, monkeypatch)
    req_path = mod._REQUIREMENTS_PATH

    original = req_path.read_text(encoding="utf-8")

    # Extract sections outside ## Traceability
    def extract_outside_traceability(text: str) -> str:
        import re
        # Find ## Traceability section
        m = re.search(r"^## Traceability\s*$", text, re.MULTILINE)
        if not m:
            return text
        # Find next ## section after Traceability
        next_section = re.search(r"^## ", text[m.end():], re.MULTILINE)
        if next_section:
            trac_end = m.end() + next_section.start()
        else:
            trac_end = len(text)
        # Return everything outside the Traceability section
        return text[: m.start()] + text[trac_end:]

    before = extract_outside_traceability(original)

    try:
        # Run sync
        mod.sync_requirements(check=False)
        after_text = req_path.read_text(encoding="utf-8")
        after = extract_outside_traceability(after_text)

        assert before == after, (
            "sync_requirements() modified content outside ## Traceability section"
        )
    finally:
        # Restore original
        req_path.write_text(original, encoding="utf-8")
        mod.sync_requirements(check=False)


# ---------------------------------------------------------------------------
# Test 7: CLI --check exit code: 0 when in-sync, 1 when drifted
# ---------------------------------------------------------------------------
def test_cli_check_exits_zero(tmp_path):
    """CLI: --check exits 0 when in-sync, 1 when drift detected."""
    script = _REPO_ROOT / "scripts" / "traceability" / "sync_requirements.py"
    mod = _import_module()
    req_path = tmp_path / "v4.10-REQUIREMENTS.md"
    roadmap_path = tmp_path / "v4.10-ROADMAP.md"
    req_path.write_text(
        mod._ARCHIVED_REQUIREMENTS_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    roadmap_path.write_text(
        mod._ARCHIVED_ROADMAP_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    base_cmd = [
        "uv",
        "run",
        "python",
        str(script),
        "--requirements",
        str(req_path),
        "--roadmap",
        str(roadmap_path),
    ]

    # Ensure in-sync state first
    subprocess.run(
        base_cmd,
        cwd=str(_REPO_ROOT),
        check=True,
        capture_output=True,
    )

    # Should exit 0 when in-sync
    result_ok = subprocess.run(
        [*base_cmd, "--check"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
    )
    assert result_ok.returncode == 0, (
        f"--check exited {result_ok.returncode} on in-sync file.\n"
        f"stdout: {result_ok.stdout.decode()}\n"
        f"stderr: {result_ok.stderr.decode()}"
    )

    # Introduce drift
    original = req_path.read_text(encoding="utf-8")
    try:
        import re
        drifted = re.sub(r"\| TRACE-01 \| Phase 91 \|[^\|]+\|", "", original, count=1)
        if drifted == original:
            lines = original.splitlines(keepends=True)
            drifted = "".join(l for l in lines if not ("TRACE-01" in l and "Phase 91" in l))
        req_path.write_text(drifted, encoding="utf-8")

        # Should exit 1 when drifted
        result_drift = subprocess.run(
            [*base_cmd, "--check"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
        )
        assert result_drift.returncode == 1, (
            f"--check exited {result_drift.returncode} on drifted file (expected 1).\n"
            f"stdout: {result_drift.stdout.decode()}\n"
            f"stderr: {result_drift.stderr.decode()}"
        )
    finally:
        req_path.write_text(original, encoding="utf-8")
        subprocess.run(
            base_cmd,
            cwd=str(_REPO_ROOT),
            capture_output=True,
        )
