"""Phase 94 FILT-03 tests — active-mode orchestration.

TDD RED: tests written before active_mode_emit.py exists.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime

import polars as pl
import pytest

# sys.path is configured via conftest.py (scripts/v4.11 added to path)
import active_mode_emit  # type: ignore[import-not-found]


class TestPhase93KillSwitchRead:
    def test_parses_nyquist_audit_json(self):
        path = pathlib.Path(
            ".planning/phases/93-vol-precompute-classifier-nyquist-audit/93-VALIDATION.md"
        )
        if not path.exists():
            pytest.skip("Phase 93 VALIDATION missing")
        block = active_mode_emit._read_phase93_kill_switch(path)
        assert "kill_switch_fired" in block
        assert isinstance(block["kill_switch_fired"], bool)

    def test_kill_switch_fired_is_true_from_real_file(self):
        """Phase 93 confirmed kill_switch_fired=true (VOL_HIGH n_min=6<20)."""
        path = pathlib.Path(
            ".planning/phases/93-vol-precompute-classifier-nyquist-audit/93-VALIDATION.md"
        )
        if not path.exists():
            pytest.skip("Phase 93 VALIDATION missing")
        block = active_mode_emit._read_phase93_kill_switch(path)
        assert block["kill_switch_fired"] is True, (
            "Phase 93 reported kill_switch_fired=true; real file must preserve this"
        )

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            active_mode_emit._read_phase93_kill_switch(tmp_path / "missing.md")

    def test_missing_marker_raises(self, tmp_path):
        md = tmp_path / "93-VALIDATION.md"
        md.write_text("no markers here\n")
        with pytest.raises(ValueError, match="NYQUIST_AUDIT_JSON"):
            active_mode_emit._read_phase93_kill_switch(md)


class TestActiveModeEmit:
    @pytest.fixture
    def synthetic_inputs(self, tmp_path):
        """Write minimal vol_per_slot + slot_labels parquets + stub 93-VALIDATION.md."""
        vol_path = tmp_path / "vol.parquet"
        labels_path = tmp_path / "labels.parquet"
        val_path = tmp_path / "93-VALIDATION.md"

        pl.DataFrame(
            {
                "pair": ["EURUSD", "EURUSD", "USDJPY"],
                "bar_time": [
                    datetime(2024, 1, 15),
                    datetime(2024, 1, 16),
                    datetime(2024, 1, 17),
                ],
                "bucket": ["VOL_HIGH", "VOL_MID", "VOL_HIGH"],
            }
        ).write_parquet(vol_path)

        pl.DataFrame(
            {
                "cell_id": ["c1", "c2", "c3", "c4"],
                "pair": ["EURUSD", "EURUSD", "USDJPY", "USDJPY"],
                "event_ts": [
                    datetime(2024, 1, 15),
                    datetime(2024, 1, 16),
                    datetime(2024, 1, 17),
                    datetime(2024, 1, 18),  # no vol match → VOL_NA → drop
                ],
            }
        ).write_parquet(labels_path)

        val_path.write_text(
            "pre-junk\n"
            "<!-- NYQUIST_AUDIT_JSON_BEGIN -->\n"
            "```json\n"
            '{"kill_switch_fired": true, "per_bucket": {"VOL_HIGH": {"n_min": 6}}}\n'
            "```\n"
            "<!-- NYQUIST_AUDIT_JSON_END -->\n"
            "post-junk\n",
        )
        return vol_path, labels_path, val_path

    def test_emit_writes_both_artifacts(self, synthetic_inputs, tmp_path):
        vol, labels, val = synthetic_inputs
        cells_out = tmp_path / "cells_post_filter.parquet"
        eval_out = tmp_path / "active_mode" / "filter_eval.json"
        active_mode_emit.emit_active_mode(
            vol_parquet=vol,
            slot_labels=labels,
            cells_out=cells_out,
            eval_out=eval_out,
            phase93_validation=val,
        )
        assert cells_out.exists()
        assert eval_out.exists()

    def test_filter_eval_keys(self, synthetic_inputs, tmp_path):
        vol, labels, val = synthetic_inputs
        cells_out = tmp_path / "cells_post_filter.parquet"
        eval_out = tmp_path / "active_mode" / "filter_eval.json"
        active_mode_emit.emit_active_mode(
            vol_parquet=vol,
            slot_labels=labels,
            cells_out=cells_out,
            eval_out=eval_out,
            phase93_validation=val,
        )
        payload = json.loads(eval_out.read_text())
        for key in (
            "post_filter_cell_count",
            "bucket_distribution",
            "kill_switch_consumed",
        ):
            assert key in payload, f"missing key: {key}"
        assert payload["kill_switch_consumed"] is True
        assert isinstance(payload["bucket_distribution"], dict)
        # 4 total cells: c1/c2/c3/c4
        assert sum(payload["bucket_distribution"].values()) == 4

    def test_pass_count_only_high(self, synthetic_inputs, tmp_path):
        vol, labels, val = synthetic_inputs
        cells_out = tmp_path / "cells_post_filter.parquet"
        eval_out = tmp_path / "active_mode" / "filter_eval.json"
        active_mode_emit.emit_active_mode(
            vol_parquet=vol,
            slot_labels=labels,
            cells_out=cells_out,
            eval_out=eval_out,
            phase93_validation=val,
        )
        df = pl.read_parquet(cells_out)
        pass_rows = df.filter(pl.col("pass_flag") == True)  # noqa: E712
        # Only c1 (EURUSD/VOL_HIGH) + c3 (USDJPY/VOL_HIGH) → 2.
        assert pass_rows.height == 2
        assert set(pass_rows["cell_id"].to_list()) == {"c1", "c3"}

    def test_output_schema(self, synthetic_inputs, tmp_path):
        vol, labels, val = synthetic_inputs
        cells_out = tmp_path / "cells_post_filter.parquet"
        eval_out = tmp_path / "active_mode" / "filter_eval.json"
        active_mode_emit.emit_active_mode(
            vol_parquet=vol,
            slot_labels=labels,
            cells_out=cells_out,
            eval_out=eval_out,
            phase93_validation=val,
        )
        df = pl.read_parquet(cells_out)
        assert df.schema["cell_id"] == pl.Utf8
        assert df.schema["pass_flag"] == pl.Boolean
        assert df.schema["bucket"] == pl.Utf8

    def test_kill_switch_source_fields(self, synthetic_inputs, tmp_path):
        vol, labels, val = synthetic_inputs
        cells_out = tmp_path / "cells_post_filter.parquet"
        eval_out = tmp_path / "active_mode" / "filter_eval.json"
        active_mode_emit.emit_active_mode(
            vol_parquet=vol,
            slot_labels=labels,
            cells_out=cells_out,
            eval_out=eval_out,
            phase93_validation=val,
        )
        payload = json.loads(eval_out.read_text())
        ks = payload.get("kill_switch_source")
        assert ks is not None, "kill_switch_source must be present"
        assert "phase93_kill_switch_fired" in ks
        assert ks["phase93_kill_switch_fired"] is True


class TestD40ProhibitionGuard:
    @pytest.fixture
    def synthetic_inputs(self, tmp_path):
        vol_path = tmp_path / "vol.parquet"
        labels_path = tmp_path / "labels.parquet"
        val_path = tmp_path / "93-VALIDATION.md"

        pl.DataFrame(
            {
                "pair": ["EURUSD"],
                "bar_time": [datetime(2024, 1, 15)],
                "bucket": ["VOL_HIGH"],
            }
        ).write_parquet(vol_path)
        pl.DataFrame(
            {
                "cell_id": ["c1"],
                "pair": ["EURUSD"],
                "event_ts": [datetime(2024, 1, 15)],
            }
        ).write_parquet(labels_path)
        val_path.write_text(
            "<!-- NYQUIST_AUDIT_JSON_BEGIN -->\n"
            '{"kill_switch_fired": true, "per_bucket": {}}\n'
            "<!-- NYQUIST_AUDIT_JSON_END -->\n"
        )
        return vol_path, labels_path, val_path

    def test_does_not_write_active_ship_decision(self, synthetic_inputs, tmp_path):
        """After active-mode emit, active_mode/v4_11_ship_decision.json must NOT exist."""
        vol, labels, val = synthetic_inputs
        cells_out = tmp_path / "cells_post_filter.parquet"
        eval_out = tmp_path / "emit_dir" / "filter_eval.json"
        active_mode_emit.emit_active_mode(
            vol_parquet=vol,
            slot_labels=labels,
            cells_out=cells_out,
            eval_out=eval_out,
            phase93_validation=val,
        )
        forbidden = eval_out.parent / "v4_11_ship_decision.json"
        assert not forbidden.exists(), (
            "D-40 violation: active ship_decision must NOT be emitted"
        )


class TestSC4StructuralSmoke:
    """SC#4 (A) semantics: pass_count == SEAL m_prime=64 (structural branch)
    OR (pass_count != 64 AND kill_switch_consumed=true AND phase93_kill_switch_fired=true)
    (real-data branch, audited deviation).

    Silent mismatch (count 乖離 AND kill_switch_consumed=false) = fail.
    """

    def test_sc4_real_data(self):
        cells_path = pathlib.Path("data/v4.11/cells_post_filter.parquet")
        eval_path = pathlib.Path("reports/v4.11/active_mode/filter_eval.json")
        seal_path = pathlib.Path(
            ".planning/phases/92-scope-lock-pre-registration-seal/SEAL/filter_spec.json"
        )
        if not (cells_path.exists() and eval_path.exists()):
            pytest.skip("Run active_mode_emit.py first to produce artifacts")
        spec = json.loads(seal_path.read_text())
        expected_m_prime = spec["fwer_denominator"]["post_filter_m_prime"]
        assert expected_m_prime == 64, (
            f"SEAL drift: expected 64, got {expected_m_prime}"
        )

        df = pl.read_parquet(cells_path)
        pass_count = int(df.filter(pl.col("pass_flag") == True).height)  # noqa: E712
        payload = json.loads(eval_path.read_text())
        kill_consumed = payload.get("kill_switch_consumed", False)
        kill_source = payload.get("kill_switch_source") or {}
        phase93_fired = kill_source.get("phase93_kill_switch_fired", False)

        # Branch (a): structural match.
        if pass_count == expected_m_prime:
            return

        # Branch (b): real-data audited deviation — both flags MUST be true.
        assert kill_consumed is True, (
            f"SC#4 FAIL (A): pass_count={pass_count} != m_prime={expected_m_prime} "
            f"AND kill_switch_consumed=False. Either a filter bug or SEAL drift. "
            f"Silent deviation is prohibited."
        )
        assert phase93_fired is True, (
            f"SC#4 FAIL (A): kill_switch_consumed=True but "
            f"phase93_kill_switch_fired={phase93_fired}. Audit trail inconsistent — "
            f"filter_eval.json.kill_switch_source must record the Phase 93 source flag."
        )
