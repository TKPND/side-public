"""v4.10 Phase 91 TRACE-01: Traceability auto-sync CLI.

Parses ROADMAP.md Phase sections and regenerates the ## Traceability table
in REQUIREMENTS.md. Idempotent; only writes when content would change.

Usage:
    uv run python scripts/traceability/sync_requirements.py          # write mode
    uv run python scripts/traceability/sync_requirements.py --check  # exit 0 if in-sync, 1 if drift

D-34: Standalone CLI — git hook independent. pytest fail-close via
test_traceability_in_sync.py::test_cli_check_exits_zero.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ACTIVE_ROADMAP_PATH = _REPO_ROOT / ".planning" / "ROADMAP.md"
_ACTIVE_REQUIREMENTS_PATH = _REPO_ROOT / ".planning" / "REQUIREMENTS.md"
_ARCHIVED_ROADMAP_PATH = _REPO_ROOT / ".planning" / "milestones" / "v4.10-ROADMAP.md"
_ARCHIVED_REQUIREMENTS_PATH = (
    _REPO_ROOT / ".planning" / "milestones" / "v4.10-REQUIREMENTS.md"
)
_ACTIVE_PHASES_DIR = _REPO_ROOT / ".planning" / "phases"
_TRAC_HEADER = "## Traceability"

# v4.10 milestone covers Phase 88-91 only; older phases live inside <details> blocks
_MIN_PHASE = 88


def _active_planning_has_v4_10_scope() -> bool:
    """Return True only while the active planning files still host v4.10."""
    if not (_ACTIVE_ROADMAP_PATH.exists() and _ACTIVE_REQUIREMENTS_PATH.exists()):
        return False
    text = _ACTIVE_ROADMAP_PATH.read_text(encoding="utf-8")
    return bool(re.search(r"^#{3,4} Phase (88|89|90|91):", text, re.MULTILINE))


_USE_ACTIVE_PLANNING = _active_planning_has_v4_10_scope()
_ROADMAP_PATH = _ACTIVE_ROADMAP_PATH if _USE_ACTIVE_PLANNING else _ARCHIVED_ROADMAP_PATH
_REQUIREMENTS_PATH = (
    _ACTIVE_REQUIREMENTS_PATH if _USE_ACTIVE_PLANNING else _ARCHIVED_REQUIREMENTS_PATH
)


def _targets_frozen_archive() -> bool:
    return _REQUIREMENTS_PATH.resolve() == _ARCHIVED_REQUIREMENTS_PATH.resolve()


# ---------------------------------------------------------------------------
# ROADMAP parser
# ---------------------------------------------------------------------------
def parse_roadmap() -> dict[str, list[str]]:
    """Parse ROADMAP.md and return {phase_str: [REQ, ...]} for Phase >= _MIN_PHASE.

    Looks for the pattern:
        #### Phase N: ...
        ...
        **Requirements**: REQ-A, REQ-B, ...

    Only captures phases >= 88 (v4.10 scope).
    """
    text = _ROADMAP_PATH.read_text(encoding="utf-8")

    # Find all ### / #### Phase N: ... sections (v4.10 uses ####, v4.11 uses ###)
    phase_pattern = re.compile(r"^#{3,4} Phase (\d+):", re.MULTILINE)
    req_pattern = re.compile(r"\*\*Requirements\*\*:?\s*([^\n]+)")

    result: dict[str, list[str]] = {}

    matches = list(phase_pattern.finditer(text))
    for i, m in enumerate(matches):
        phase_num = int(m.group(1))
        if phase_num < _MIN_PHASE:
            continue

        # Search for **Requirements**: within the next 600 chars (within the phase block)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section_text = text[start:end]

        req_match = req_pattern.search(section_text)
        if not req_match:
            continue

        raw = req_match.group(1).strip()
        reqs = [r.strip() for r in raw.split(",") if r.strip()]
        result[str(phase_num)] = reqs

    return result


# ---------------------------------------------------------------------------
# Status determination via VERIFICATION.md existence
# ---------------------------------------------------------------------------
def _get_phase_status(phase_num: str) -> str:
    """Return 'Satisfied' if VERIFICATION.md exists for given phase number, else 'Pending'."""
    if not _USE_ACTIVE_PLANNING:
        return "Satisfied"

    # Look for any directory matching .planning/phases/{NN}-*/
    prefix = f"{phase_num}-"
    if not _ACTIVE_PHASES_DIR.exists():
        return "Pending"
    for phase_dir in _ACTIVE_PHASES_DIR.iterdir():
        if not phase_dir.is_dir():
            continue
        if not phase_dir.name.startswith(prefix):
            continue
        # Look for *VERIFICATION.md in that directory
        for v_file in phase_dir.glob("*VERIFICATION.md"):
            if v_file.is_file():
                return "Satisfied"
    return "Pending"


# ---------------------------------------------------------------------------
# Traceability table renderer
# ---------------------------------------------------------------------------
def _render_traceability(phase_reqs: dict[str, list[str]]) -> str:
    """Build the Traceability table (header + rows) from phase_reqs dict.

    Row order: phases in ascending numeric order, within each phase: ROADMAP list order.
    Status: 'Satisfied' if phase VERIFICATION.md exists, else 'Pending'.
    """
    lines = [
        "| Requirement | Phase | Status |",
        "|---|---|---|",
    ]
    for phase_num in sorted(phase_reqs.keys(), key=int):
        status = _get_phase_status(phase_num)
        for req in phase_reqs[phase_num]:
            lines.append(f"| {req} | Phase {phase_num} | {status} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Core sync function
# ---------------------------------------------------------------------------
def sync_requirements(check: bool = False) -> bool:
    """Sync REQUIREMENTS.md ## Traceability table with ROADMAP.md.

    Args:
        check: If True, return True if already in-sync (no write needed), False if drift.
                If False, write updated content if needed, return True.

    Returns:
        bool: True = in-sync (or fixed), False = drift detected or frozen archive
        refused a write.
    """
    phase_reqs = parse_roadmap()
    requirements = _REQUIREMENTS_PATH.read_text(encoding="utf-8")

    # Locate ## Traceability section
    trac_match = re.search(r"^## Traceability\s*$", requirements, re.MULTILINE)
    if not trac_match:
        # No section found — cannot sync
        if check:
            return False
        return True

    trac_start = trac_match.start()

    # Find end of section: next ## heading or EOF (Pitfall 6: precise boundary)
    tail = requirements[trac_match.end():]
    next_section = re.search(r"^## ", tail, re.MULTILINE)
    if next_section:
        trac_end = trac_match.end() + next_section.start()
    else:
        trac_end = len(requirements)

    # Build new section content (preserve blurb that is between header and table)
    # The existing content between trac_start and trac_end may include:
    #   "## Traceability\n\n<blurb paragraph>\n\n| table |"
    # We preserve everything up to the first "|" (table start) and replace only the table.
    section_body = requirements[trac_start:trac_end]

    # Find the table within the section (first occurrence of a pipe-table row)
    table_match = re.search(r"^\|.*\|", section_body, re.MULTILINE)
    if table_match:
        # Preserve everything before the table (header + blurb)
        pre_table = section_body[: table_match.start()]
        # Find end of table (last consecutive pipe-table line)
        table_start_abs = trac_start + table_match.start()
        table_lines_match = re.search(
            r"^(\|[^\n]*\n)+", section_body[table_match.start():], re.MULTILINE
        )
        if table_lines_match:
            table_end_in_section = table_match.start() + table_lines_match.end()
        else:
            table_end_in_section = len(section_body)
        post_table = section_body[table_end_in_section:]
    else:
        # No table yet — append after header
        pre_table = section_body.rstrip() + "\n\n"
        post_table = ""
        table_end_in_section = len(section_body)

    new_table = _render_traceability(phase_reqs)
    new_section = pre_table + new_table + post_table

    new_content = requirements[:trac_start] + new_section + requirements[trac_end:]

    if check:
        return new_content == requirements

    if new_content != requirements:
        if _targets_frozen_archive():
            return False
        _REQUIREMENTS_PATH.write_text(new_content, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point for sync_requirements.py."""
    parser = argparse.ArgumentParser(
        description="Sync REQUIREMENTS.md Traceability table from ROADMAP.md"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: exit 0 if in-sync, exit 1 if drift detected (no write)",
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        default=None,
        help="Override REQUIREMENTS.md path; useful for mutable temp copies.",
    )
    parser.add_argument(
        "--roadmap",
        type=Path,
        default=None,
        help="Override ROADMAP.md path; useful for mutable temp copies.",
    )
    args = parser.parse_args()

    global _ROADMAP_PATH, _REQUIREMENTS_PATH, _USE_ACTIVE_PLANNING
    if args.roadmap is not None:
        _ROADMAP_PATH = args.roadmap
    if args.requirements is not None:
        _REQUIREMENTS_PATH = args.requirements
    if args.roadmap is not None or args.requirements is not None:
        _USE_ACTIVE_PLANNING = False

    if args.check:
        in_sync = sync_requirements(check=True)
        if in_sync:
            print("Traceability in-sync with ROADMAP.md")
            sys.exit(0)
        else:
            print("Traceability DRIFT detected — run sync_requirements.py to fix", file=sys.stderr)
            sys.exit(1)
    else:
        synced = sync_requirements(check=False)
        if synced:
            print("Traceability table synced.")
            sys.exit(0)
        print(
            "Traceability DRIFT detected in frozen archive; copy it to a mutable path first",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
