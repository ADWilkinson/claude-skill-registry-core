"""Process-bound, two-phase catalog retirement; never an archive deletion tool."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

LEDGER_PATH = "policy/community-retirements.json"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_ledger(text: str) -> list[dict[str, Any]]:
    records = json.loads(text, object_pairs_hook=_unique_object)
    if not isinstance(records, list):
        raise ValueError("retirement ledger must be a JSON array")
    identities = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "entry",
            "reason",
            "decision_url",
            "archive_policy",
        }:
            raise ValueError(
                "retirement record requires entry, reason, decision_url, archive_policy"
            )
        entry = record["entry"]
        if (
            not isinstance(entry, dict)
            or not all(
                isinstance(entry.get(key), str) and entry[key].strip() for key in ("name", "repo")
            )
            or not isinstance(entry.get("path"), str)
        ):
            raise ValueError("retirement entry requires name, repo and path")
        # Preserve separate decisions for successive versions of the same row.
        identity = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        if identity in identities:
            raise ValueError("duplicate exact retirement row")
        identities.add(identity)
        if record["archive_policy"] != "retain":
            raise ValueError("catalog retirement must retain historical archives")
        if not isinstance(record["reason"], str) or not record["reason"].strip():
            raise ValueError("retirement requires a reason")
        url = record["decision_url"]
        if not isinstance(url, str):
            raise ValueError("retirement requires a public maintainer decision URL")
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or parsed.query
            or re.fullmatch(
                r"/majiayu000/claude-skill-registry-core/(?:issues|pull)/[1-9][0-9]*",
                parsed.path,
            )
            is None
        ):
            raise ValueError("retirement decision must link to a core repository issue or PR")
    return records


def validate_retirement(base_text: str, head_text: str, records: list[dict[str, Any]]) -> list[str]:
    """Permit only exact, line-preserving removal of pre-authorized rows."""
    base = json.loads(base_text)
    head = json.loads(head_text)
    base_rows, head_rows = base["skills"], head["skills"]
    removed = [row for row in base_rows if row not in head_rows]
    if not removed or len(base_rows) - len(head_rows) != len(removed):
        return ["retirement must only remove distinct existing catalog rows"]
    if any(base_rows.count(row) != 1 for row in removed):
        return ["retirement cannot remove ambiguous duplicate rows"]
    if any(not any(record["entry"] == row for record in records) for row in removed):
        return [
            "catalog removal requires exact-row retirement authorization already on the base branch"
        ]
    expected_rows = [row for row in base_rows if row not in removed]
    if head_rows != expected_rows or {k: v for k, v in base.items() if k != "skills"} != {
        k: v for k, v in head.items() if k != "skills"
    }:
        return ["retirement must preserve all remaining rows, their order and catalog metadata"]
    lines = base_text.splitlines(keepends=True)
    kept = []
    removed_count = 0
    last_row_index = None
    for line in lines:
        try:
            row = json.loads(line.strip().removesuffix(","))
        except json.JSONDecodeError:
            row = None
        if isinstance(row, dict) and row in base_rows:
            if row in removed:
                removed_count += 1
                continue
            last_row_index = len(kept)
        kept.append(line)
    if removed_count != len(removed):
        return ["retirement requires the existing one-row-per-line catalog format"]
    if last_row_index is not None:
        line = kept[last_row_index]
        # The only permitted surviving-line change is its now-unneeded comma.
        body = line.rstrip("\r\n")
        if body.endswith(","):
            kept[last_row_index] = body[:-1] + line[len(body) :]
    if "".join(kept) != head_text:
        return ["retirement must not reformat surviving catalog lines"]
    return []
