"""AUDIT-01 archive checks for v4.11 Phase 92-95 traceability scope."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ROADMAP_MD = REPO_ROOT / ".planning" / "milestones" / "v4.11-ROADMAP.md"
REQUIREMENTS_MD = REPO_ROOT / ".planning" / "milestones" / "v4.11-REQUIREMENTS.md"


def _parse_archived_roadmap() -> dict[str, list[str]]:
    text = ROADMAP_MD.read_text(encoding="utf-8")
    phase_pattern = re.compile(r"^#{3,4} Phase (\d+):", re.MULTILINE)
    req_pattern = re.compile(r"\*\*Requirements\*\*:?\s*([^\n]+)")

    result: dict[str, list[str]] = {}
    matches = list(phase_pattern.finditer(text))
    for index, match in enumerate(matches):
        phase_num = int(match.group(1))
        if phase_num < 92 or phase_num > 95:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        req_match = req_pattern.search(text[start:end])
        if req_match:
            result[str(phase_num)] = [
                req.strip() for req in req_match.group(1).split(",") if req.strip()
            ]
    return result


def test_archived_v411_traceability_artifacts_exist() -> None:
    assert ROADMAP_MD.is_file()
    assert REQUIREMENTS_MD.is_file()


def test_roadmap_phase_95_parsed() -> None:
    """Archived ROADMAP must contain Phase 95 with >= 7 REQ ids."""
    parsed = _parse_archived_roadmap()
    assert "95" in parsed, f"Phase 95 missing. Got: {sorted(parsed.keys())}"
    reqs = parsed["95"]
    assert len(reqs) >= 7, f"Phase 95 has {len(reqs)} REQs, expected >= 7: {reqs}"


def test_traceability_has_all_v411_reqs() -> None:
    """Archived REQUIREMENTS traceability contains all SHIP-01..05 + AUDIT-01..02."""
    content = REQUIREMENTS_MD.read_text(encoding="utf-8")
    for req_id in [
        "SHIP-01",
        "SHIP-02",
        "SHIP-03",
        "SHIP-04",
        "SHIP-05",
        "AUDIT-01",
        "AUDIT-02",
    ]:
        assert f"| {req_id} | Phase 95 |" in content, (
            f"Row for {req_id} not found in archived Traceability table"
        )


def test_archived_requirements_traceability_is_stable() -> None:
    """Archived v4.11 requirements stay read-only and already complete."""
    content = REQUIREMENTS_MD.read_text(encoding="utf-8")
    assert "## Traceability" in content
    assert "SATISFIED: 18/18" in content
    assert "Unmapped: 0" in content


def test_phase_heading_pattern() -> None:
    """Parser handles ### Phase N: headings for archived Phase 92-95."""
    parsed = _parse_archived_roadmap()
    for phase_num in ["92", "93", "94", "95"]:
        assert phase_num in parsed, (
            f"Phase {phase_num} missing from archived ROADMAP. "
            f"Got: {sorted(parsed.keys())}"
        )
