#!/usr/bin/env python3
"""Validate Side's canonical No-Go Map JSON and rendered Markdown summary."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from render_no_go_map_md import render


STATUSES = {"REJECTED", "WEAK", "BLOCKED", "OPEN"}
ENTRY_TYPES = {"signal_hypothesis", "source_readiness", "pathway_closure"}
BLOCKER_TYPES = {"access", "licensing", "source_semantics", "reproducibility", "data_availability"}
REQUIRED_TOP_LEVEL = {"schema_version", "project", "scope", "generated_from", "entries"}
REQUIRED_ENTRY_FIELDS = {
    "id",
    "family_id",
    "entry_type",
    "hypothesis",
    "status",
    "domain",
    "asset_scope",
    "milestones",
    "phases",
    "data_source",
    "source_semantics",
    "validation_gate",
    "failure_mode",
    "classification_rationale",
    "evidence_artifacts",
    "planning_conditions",
}
ARRAY_FIELDS = {"asset_scope", "milestones", "phases", "evidence_artifacts", "planning_conditions"}
NON_EMPTY_ARRAY_FIELDS = {"asset_scope", "milestones", "evidence_artifacts", "planning_conditions"}
STRING_FIELDS = {
    "id",
    "family_id",
    "entry_type",
    "hypothesis",
    "status",
    "domain",
    "data_source",
    "source_semantics",
    "validation_gate",
    "failure_mode",
    "classification_rationale",
}
COUNT_TABLE_KEY_FIELDS = {"domain"}
COUNT_ROW_RE = re.compile(r"^\| ([^|]+) \| ([0-9]+) \|$")
COUNT_SEPARATOR_RE = re.compile(r"^\| -+ \| -+ \|$")


def fail(message: str) -> None:
    raise ValueError(message)


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON: {exc}")
    except OSError as exc:
        fail(f"could not read JSON: {exc}")
    if not isinstance(data, dict):
        fail("top-level JSON must be an object")
    return data


def require_string(entry: dict[str, Any], field: str) -> None:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        fail(f"{entry.get('id', '<missing id>')}: field {field} must be a non-empty string")


def require_array(entry: dict[str, Any], field: str, non_empty: bool) -> None:
    value = entry.get(field)
    if not isinstance(value, list):
        fail(f"{entry.get('id', '<missing id>')}: field {field} must be an array")
    if non_empty and not value:
        fail(f"{entry.get('id', '<missing id>')}: field {field} must be non-empty")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            fail(f"{entry.get('id', '<missing id>')}: field {field} must contain only non-empty strings")


def require_non_empty_string(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        fail(f"{field} must be a non-empty string")


def require_non_empty_string_list(value: Any, field: str) -> None:
    if not isinstance(value, list) or not value:
        fail(f"{field} must be a non-empty list")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            fail(f"{field} must contain only non-empty strings")


def reject_markdown_table_breaking_chars(entry: dict[str, Any], field: str) -> None:
    value = entry[field]
    if any(char in value for char in ("|", "\n", "\r")):
        fail(f"{entry['id']}: field {field} must not contain Markdown table delimiters or newlines")


def validate_entry(entry: Any) -> str:
    if not isinstance(entry, dict):
        fail("entries must contain objects")
    missing = sorted(REQUIRED_ENTRY_FIELDS - set(entry))
    if missing:
        fail(f"{entry.get('id', '<missing id>')}: missing required fields: {', '.join(missing)}")

    for field in STRING_FIELDS:
        require_string(entry, field)
    for field in COUNT_TABLE_KEY_FIELDS:
        reject_markdown_table_breaking_chars(entry, field)
    for field in ARRAY_FIELDS:
        require_array(entry, field, field in NON_EMPTY_ARRAY_FIELDS)

    status = entry["status"]
    if status not in STATUSES:
        fail(f"{entry['id']}: invalid status {status}")
    entry_type = entry["entry_type"]
    if entry_type not in ENTRY_TYPES:
        fail(f"{entry['id']}: invalid entry_type {entry_type}")
    if status == "REJECTED":
        if not isinstance(entry.get("same_form_scope"), str) or not entry["same_form_scope"].strip():
            fail(f"{entry['id']}: REJECTED entries require non-empty same_form_scope")
    if status == "BLOCKED":
        blocker_type = entry.get("blocker_type")
        if blocker_type not in BLOCKER_TYPES:
            fail(f"{entry['id']}: BLOCKED entries require valid blocker_type")
    return entry["id"]


def validate_top_level(data: dict[str, Any]) -> list[dict[str, Any]]:
    missing = sorted(REQUIRED_TOP_LEVEL - set(data))
    if missing:
        fail(f"missing top-level fields: {', '.join(missing)}")
    if data["schema_version"] != "no_go_map.v1":
        fail("schema_version must be no_go_map.v1")
    if data["project"] != "side":
        fail("project must be side")
    if not isinstance(data["entries"], list) or not data["entries"]:
        fail("entries must be a non-empty array")
    if not isinstance(data["generated_from"], dict):
        fail("generated_from must be an object")
    require_non_empty_string(data["scope"], "scope")
    require_non_empty_string(data["generated_from"].get("as_of"), "generated_from.as_of")
    require_non_empty_string_list(data["generated_from"].get("milestones"), "generated_from.milestones")
    return data["entries"]


def parse_markdown_counts(markdown: str, heading: str) -> dict[str, int]:
    section_match = re.search(rf"## {re.escape(heading)}\n(?P<body>.*?)(?:\n## |\Z)", markdown, re.S)
    if not section_match:
        fail(f"markdown missing section: {heading}")
    counts: dict[str, int] = {}
    for line in section_match.group("body").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in {"| Status | Count |", "| Domain | Count |"}:
            continue
        match = COUNT_ROW_RE.fullmatch(stripped)
        if not match:
            if stripped.startswith("|") and not COUNT_SEPARATOR_RE.fullmatch(stripped):
                fail(f"markdown malformed count row in {heading}: {stripped}")
            continue
        key = match.group(1).strip()
        if key in {"Status", "Domain"}:
            continue
        if key in counts:
            fail(f"markdown duplicate count row in {heading}: {key}")
        counts[key] = int(match.group(2))
    return counts


def validate_markdown(markdown_path: Path, data: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    try:
        markdown = markdown_path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"could not read Markdown: {exc}")
    if "warning reference, not a hard gate" not in markdown:
        fail("markdown must state that the map is a warning reference, not a hard gate")
    if "WEAK means weak or incomplete evidence" not in markdown:
        fail("markdown must define WEAK as weak evidence rather than weak alpha")

    expected_status = dict(Counter(entry["status"] for entry in entries))
    expected_domain = dict(Counter(entry["domain"] for entry in entries))
    if parse_markdown_counts(markdown, "Status Counts") != expected_status:
        fail("markdown status counts do not match JSON")
    if parse_markdown_counts(markdown, "Domain Counts") != expected_domain:
        fail("markdown domain counts do not match JSON")
    if markdown != render(data):
        fail("markdown does not match renderer output")


def validate(json_path: Path, markdown_path: Path | None = None) -> int:
    data = load_json(json_path)
    entries = validate_top_level(data)
    ids: set[str] = set()
    for entry in entries:
        entry_id = validate_entry(entry)
        if entry_id in ids:
            fail(f"duplicate id: {entry_id}")
        ids.add(entry_id)
    if markdown_path is not None:
        validate_markdown(markdown_path, data, entries)
    print(f"validated {len(entries)} entries")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3}:
        print("usage: validate_no_go_map.py <no_go_map.json> [rendered.md]", file=sys.stderr)
        return 2
    try:
        return validate(Path(argv[1]), Path(argv[2]) if len(argv) == 3 else None)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
