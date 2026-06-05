"""Phase 106 Wave 0 RED scaffold — ABLATION-V413-02.

ablation_score.json canonical bytes + NaN/Infinity 文字列禁止 + Sobol keys 検証。
RFC 8259 準拠 (allow_nan=False) — RESEARCH.md Pitfall 1。

Analog: tests/v4_13/test_phase_105_evidence_sidecar.py
"""

from __future__ import annotations
import json
from pathlib import Path
import pytest

DIMENSIONS = ["pair", "fee_bps", "window", "regime_cuts", "sizing"]


def _load_score_bytes(path: Path) -> bytes:
    if not path.exists():
        pytest.fail(
            f"Wave 1 artifact 未 emit (Wave 0 RED expected): {path}\n"
            "Phase 106 Wave 1 で emit_ablation_v413.py が ablation_score.json を emit する."
        )
    return path.read_bytes()


def test_ablation_score_canonical_bytes(phase106_score_path: Path) -> None:
    """ablation_score.json が canonical bytes form (D-V413-07)."""
    raw = _load_score_bytes(phase106_score_path)
    d = json.loads(raw)
    canonical = (
        json.dumps(d, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    assert raw == canonical, (
        "canonical bytes invariant 違反: file bytes と "
        "json.dumps(sort_keys=True, indent=2, ensure_ascii=False)+\\n の再シリアライズ結果が不一致"
    )


def test_no_nan_string_in_canonical_bytes(phase106_score_path: Path) -> None:
    """RFC 8259 準拠: canonical bytes に "NaN" / "Infinity" 等の非標準 token が混入しない."""
    raw = _load_score_bytes(phase106_score_path)
    for forbidden in (b"NaN", b"Infinity", b"-Infinity"):
        assert forbidden not in raw, (
            f"canonical JSON に非標準 token {forbidden!r} 混入 (allow_nan=False 違反)"
        )


def test_first_order_total_order_keys_present(phase106_score_path: Path) -> None:
    d = json.loads(_load_score_bytes(phase106_score_path))
    for k in ("first_order", "total_order"):
        assert k in d, f"missing key: {k}"
        assert set(d[k].keys()) == set(DIMENSIONS), f"axis keys drift: {d[k].keys()}"
        for axis in DIMENSIONS:
            v = d[k][axis]
            assert v is None or isinstance(v, (int, float)), f"{k}[{axis}] = {v!r}"


def test_schema_version_pinned(phase106_score_path: Path) -> None:
    d = json.loads(_load_score_bytes(phase106_score_path))
    assert d["schema_version"] == "v4.13.1"


def test_axes_literal_order(phase106_score_path: Path) -> None:
    """axes は emit_degeneracy_proof.py:67 の DIMENSIONS literal 順を維持 (drift 検知)."""
    d = json.loads(_load_score_bytes(phase106_score_path))
    assert d["axes"] == DIMENSIONS


def test_milestone_breakdown_4_versions(phase106_score_path: Path) -> None:
    d = json.loads(_load_score_bytes(phase106_score_path))
    assert set(d["milestone_breakdown"].keys()) == {"v4.9", "v4.10", "v4.11", "v4.12"}
    for m, body in d["milestone_breakdown"].items():
        assert body["baseline_pass_count"] == 0, f"{m}: baseline must be 0"
        assert set(body["ablated_pass_count"].keys()) == set(DIMENSIONS)
        assert set(body["delta_by_axis"].keys()) == set(DIMENSIONS)
