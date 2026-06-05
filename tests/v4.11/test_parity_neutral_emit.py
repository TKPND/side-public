"""Phase 94 FILT-02 — PARITY-V411-01 emit tests.

TDD RED phase: tests written before implementation exists.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

# conftest.py inserts scripts/v4.11 into sys.path (D-35 flat import).
import parity_neutral_emit  # type: ignore[import-not-found]


class TestFlatImportHygiene:
    def test_no_dotted_import_in_source(self):
        """D-35: source must NOT contain `scripts.v4_10` (dotted) in code or comments."""
        src = pathlib.Path("scripts/v4.11/parity_neutral_emit.py").read_text()
        assert "scripts.v4_10" not in src, "D-35 violation: dotted import found"

    def test_flat_import_no_leak(self, v4_10_ship_decision_path, tmp_path):
        """sys.path state must be unchanged before/after emit_neutral_parity."""
        if not v4_10_ship_decision_path.exists():
            pytest.skip("v4.10 baseline missing (real-data env)")
        pre = list(sys.path)
        out = tmp_path / "neutral" / "v4_11_ship_decision.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            parity_neutral_emit.emit_neutral_parity(
                v4_10_ship_decision=v4_10_ship_decision_path,
                out_path=out,
            )
        except FileNotFoundError as e:
            # emitter dependencies missing (dd_traces.parquet etc.) — still check path
            if "dd_traces" in str(e) or "p_adj" in str(e):
                pytest.skip(f"emitter data deps missing: {e}")
            raise
        post = list(sys.path)
        assert pre == post, f"sys.path leaked: pre={pre[:3]!r}, post={post[:3]!r}"


class TestParityBitExact:
    def test_parity_bitexact_6fields(self, v4_10_ship_decision_path, reports_v411_dir):
        """Run emit + compare nested 6 fields (PARITY-V411-01 acceptance).

        Requires dd_traces.parquet; skipped if absent.
        """
        if not v4_10_ship_decision_path.exists():
            pytest.skip("v4.10 baseline missing")
        dd_path = pathlib.Path("data/v4.10/dd_traces.parquet")
        if not dd_path.exists():
            pytest.skip("v4.10 dd_traces.parquet missing (real-data env)")

        out = reports_v411_dir / "neutral_mode" / "v4_11_ship_decision.json"
        parity_neutral_emit.emit_neutral_parity(
            v4_10_ship_decision=v4_10_ship_decision_path,
            out_path=out,
        )

        v410 = json.loads(v4_10_ship_decision_path.read_text())
        v411 = json.loads(out.read_text())
        a = v410["ship_metrics"]
        b = v411["ship_metrics"]
        assert a["edge_count_p_adj_005"] == b["edge_count_p_adj_005"]
        assert a["ship_verdict"] == b["ship_verdict"]
        assert a["coverage_tier"] == b["coverage_tier"]
        assert a["data_provenance"] == b["data_provenance"]
        assert (
            a["primary_metrics"]["turnover_sharpe_median"]
            == b["primary_metrics"]["turnover_sharpe_median"]
        )
        assert a["primary_metrics"]["es_median"] == b["primary_metrics"]["es_median"]

    def test_parity_6fields_from_copy(self, v4_10_ship_decision_path, tmp_path):
        """Verify that shutil.copy alone reproduces 6-field bit-exact match
        (baseline test for environments without dd_traces.parquet)."""
        import shutil

        if not v4_10_ship_decision_path.exists():
            pytest.skip("v4.10 baseline missing")
        out = tmp_path / "v4_11_ship_decision.json"
        shutil.copy(v4_10_ship_decision_path, out)
        v410 = json.loads(v4_10_ship_decision_path.read_text())
        v411 = json.loads(out.read_text())
        a = v410["ship_metrics"]
        b = v411["ship_metrics"]
        assert a["edge_count_p_adj_005"] == b["edge_count_p_adj_005"]
        assert a["ship_verdict"] == b["ship_verdict"]
        assert a["coverage_tier"] == b["coverage_tier"]
        assert a["data_provenance"] == b["data_provenance"]
        assert (
            a["primary_metrics"]["turnover_sharpe_median"]
            == b["primary_metrics"]["turnover_sharpe_median"]
        )
        assert a["primary_metrics"]["es_median"] == b["primary_metrics"]["es_median"]


class TestBaselineMissing:
    def test_file_not_found_error_with_helpful_message(self, tmp_path):
        """FileNotFoundError with descriptive message when baseline absent."""
        missing = tmp_path / "does_not_exist.json"
        out = tmp_path / "out.json"
        with pytest.raises(FileNotFoundError, match="v4.10 PARITY baseline"):
            parity_neutral_emit.emit_neutral_parity(
                v4_10_ship_decision=missing,
                out_path=out,
            )


class TestSysPathScoped:
    def test_sys_path_scoped_context_manager_defined(self):
        """_sys_path_scoped context manager must be defined in the module."""
        assert hasattr(parity_neutral_emit, "_sys_path_scoped"), (
            "_sys_path_scoped not found in module"
        )

    def test_sys_path_insert_in_source(self):
        """sys.path.insert must appear in the source (D-35 flat import)."""
        src = pathlib.Path("scripts/v4.11/parity_neutral_emit.py").read_text()
        assert "sys.path.insert" in src

    def test_shutil_copy_in_source(self):
        """shutil.copy must precede fill_ship_metrics call (overlay_evaluation precondition)."""
        src = pathlib.Path("scripts/v4.11/parity_neutral_emit.py").read_text()
        assert "shutil.copy" in src
        copy_pos = src.index("shutil.copy")
        fill_pos = src.index("fill_ship_metrics")
        assert copy_pos < fill_pos, "shutil.copy must appear before fill_ship_metrics"


class TestParityHarness:
    def test_script_is_executable(self):
        path = pathlib.Path("scripts/v4.11/parity_v411_01_check.sh")
        assert path.exists(), "parity harness script missing"
        mode = path.stat().st_mode
        assert mode & 0o111, f"not executable (mode={oct(mode)})"

    def test_script_uses_jq_cS(self):
        src = pathlib.Path("scripts/v4.11/parity_v411_01_check.sh").read_text()
        assert "jq -cS" in src, "must use canonical jq (-cS)"
        assert "ship_metrics.edge_count_p_adj_005" in src, (
            "nested 6-field selector required"
        )

    def test_harness_exit_0_on_match(self, v4_10_ship_decision_path):
        """If both v4.10 and v4.11 neutral_mode JSONs exist and match, harness exits 0."""
        if not v4_10_ship_decision_path.exists():
            pytest.skip("v4.10 baseline missing")
        v411_out = pathlib.Path("reports/v4.11/neutral_mode/v4_11_ship_decision.json")
        if not v411_out.exists():
            pytest.skip("v4.11 neutral_mode emit not yet produced")
        result = subprocess.run(
            ["bash", "scripts/v4.11/parity_v411_01_check.sh"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        assert "PASS" in result.stdout

    def test_harness_exit_1_on_missing_v411(self, tmp_path, monkeypatch):
        """Harness exits 1 with stderr 'missing' when v4.11 file absent."""
        import shutil

        v411 = pathlib.Path("reports/v4.11/neutral_mode/v4_11_ship_decision.json")
        backup = tmp_path / "backup.json"
        had_file = v411.exists()
        if had_file:
            shutil.copy(v411, backup)
            v411.unlink()
        try:
            result = subprocess.run(
                ["bash", "scripts/v4.11/parity_v411_01_check.sh"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 1
            assert "missing" in result.stderr.lower()
        finally:
            if had_file:
                shutil.copy(backup, v411)
