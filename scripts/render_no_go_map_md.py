#!/usr/bin/env python3
"""Render Side's No-Go Map JSON as deterministic Markdown."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


STATUS_ORDER = ("REJECTED", "WEAK", "BLOCKED", "OPEN")
JSON_SOURCE_PATH = "data/no_go_map/no_go_map_v1.json"


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    return data


def joined(value: Any) -> str:
    if not value:
        return "none"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def append_list(lines: list[str], label: str, values: Any) -> None:
    lines.append(f"- {label}:")
    for value in values or []:
        lines.append(f"  - {value}")


def append_entry(lines: list[str], entry: dict[str, Any]) -> None:
    lines.extend(
        [
            f"### {entry['id']}",
            "",
            f"- Family: {entry['family_id']}",
            f"- Type: {entry['entry_type']}",
            f"- Status: {entry['status']}",
            f"- Domain: {entry['domain']}",
            f"- Hypothesis: {entry['hypothesis']}",
            f"- Asset scope: {joined(entry.get('asset_scope'))}",
            f"- Milestones: {joined(entry.get('milestones'))}",
            f"- Phases: {joined(entry.get('phases'))}",
            f"- Data source: {entry['data_source']}",
            f"- Source semantics: {entry['source_semantics']}",
            f"- Validation gate: {entry['validation_gate']}",
            f"- Failure mode: {entry['failure_mode']}",
            f"- Classification rationale: {entry['classification_rationale']}",
        ]
    )
    if entry.get("same_form_scope"):
        lines.append(f"- Same-form scope: {entry['same_form_scope']}")
    if entry.get("blocker_type"):
        lines.append(f"- Blocker type: {entry['blocker_type']}")
    append_list(lines, "Evidence artifacts", entry.get("evidence_artifacts"))
    append_list(lines, "Planning conditions", entry.get("planning_conditions"))
    lines.append("")


def render(data: dict[str, Any]) -> str:
    entries = data["entries"]
    status_counts = Counter(entry["status"] for entry in entries)
    domain_counts = Counter(entry["domain"] for entry in entries)
    lines = [
        "# Side No-Go Map",
        "",
        f"Schema version: `{data['schema_version']}`",
        f"Scope: {data['scope']}",
        f"Generated from as of: {data['generated_from']['as_of']}",
        f"JSON source: `{JSON_SOURCE_PATH}`",
        "",
        "This map is a warning reference, not a hard gate.",
        "",
        "## Status Meanings",
        "",
        "- REJECTED: sufficiently tested; do not retry same hypothesis with same data/source/gates.",
        "- WEAK: WEAK means weak or incomplete evidence, not weak alpha.",
        "- BLOCKED: blocked by source/access/licensing/semantics/reproducibility/data availability.",
        "- OPEN: not directly tested; not endorsement.",
        "",
        "## Status Counts",
        "",
        "| Status | Count |",
        "| --- | --- |",
    ]

    for status in STATUS_ORDER:
        count = status_counts.get(status, 0)
        if count:
            lines.append(f"| {status} | {count} |")

    lines.extend(["", "## Domain Counts", "", "| Domain | Count |", "| --- | --- |"])
    for domain in sorted(domain_counts):
        lines.append(f"| {domain} | {domain_counts[domain]} |")
    lines.append("")

    for status in STATUS_ORDER:
        group = sorted((entry for entry in entries if entry["status"] == status), key=lambda entry: entry["id"])
        if not group:
            continue
        lines.extend([f"## {status}", ""])
        for entry in group:
            append_entry(lines, entry)

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: render_no_go_map_md.py <no_go_map.json>", file=sys.stderr)
        return 2
    try:
        sys.stdout.write(render(load_json(Path(argv[1]))))
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"render_no_go_map_md.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
