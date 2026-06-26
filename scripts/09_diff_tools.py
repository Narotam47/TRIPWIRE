#!/usr/bin/env python3
"""
Script 09: Consecutive-version diff engine for MCP tool definitions.

Reads tool_history_full.jsonl, groups snapshots by (repo_url, tool_name),
and emits one record per consecutive pair of snapshots (a "diff event") plus
one no_drift_observed record for tools with only a single snapshot.

Output JSONL schema
───────────────────
For tools with ≥2 snapshots — one record per consecutive pair:
  repo_url             str
  tool_name            str
  from_sha             str   (8-char prefix only)
  to_sha               str
  from_date            str   (ISO-8601)
  to_date              str
  description_changed  bool
  description_diff     str   (unified diff, empty string if unchanged)
  schema_fields_added  list[str]
  schema_fields_removed list[str]
  schema_type_changes  list[{field, from_type, to_type}]
  name_unchanged_flag  bool  (always True within a group; the rug-pull indicator)
  status               "drift_event"

For tools with exactly 1 snapshot:
  repo_url             str
  tool_name            str
  snapshot_count       int   (1)
  commit_sha           str
  commit_date          str
  status               "no_drift_observed"

Usage
─────
  python scripts/09_diff_tools.py [--repos SLUG ...] [--out PATH]

  --repos   owner__repo slugs to process (test mode; omit for full run)
  --out     output JSONL path (default: data/processed/tool_diffs.jsonl)
"""

import argparse
import difflib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path


HISTORY_IN = Path("data/processed/tool_history_full.jsonl")
DEFAULT_OUT = Path("data/processed/tool_diffs.jsonl")


# ── schema helpers ────────────────────────────────────────────────────────────

def _field_type(schema: dict, field: str) -> str | None:
    prop = schema.get("properties", {}).get(field)
    if prop is None:
        return None
    return prop.get("type") or ("oneOf" if "oneOf" in prop else
                                "anyOf" if "anyOf" in prop else
                                "object")


def diff_schemas(s1: dict, s2: dict) -> tuple[list, list, list]:
    props1 = set(s1.get("properties", {}).keys())
    props2 = set(s2.get("properties", {}).keys())

    added   = sorted(props2 - props1)
    removed = sorted(props1 - props2)

    type_changes = []
    for field in sorted(props1 & props2):
        t1 = _field_type(s1, field)
        t2 = _field_type(s2, field)
        if t1 != t2:
            type_changes.append({"field": field, "from_type": t1, "to_type": t2})

    return added, removed, type_changes


def diff_descriptions(d1: str, d2: str) -> tuple[bool, str]:
    d1 = d1 or ""
    d2 = d2 or ""
    if d1 == d2:
        return False, ""
    lines1 = d1.splitlines(keepends=True)
    lines2 = d2.splitlines(keepends=True)
    udiff  = "".join(difflib.unified_diff(lines1, lines2,
                                          fromfile="before", tofile="after",
                                          lineterm=""))
    return True, udiff


# ── core ─────────────────────────────────────────────────────────────────────

def run(only_slugs: set[str] | None, out_path: Path) -> None:
    records = [json.loads(l) for l in HISTORY_IN.open()]

    if only_slugs:
        records = [r for r in records
                   if r["repo_url"].rstrip("/").rsplit("/", 2)[-2] + "__" +
                      r["repo_url"].rstrip("/").rsplit("/", 1)[-1] in only_slugs]
        print(f"Test mode: {len(records)} records for {len(only_slugs)} repo(s)")

    # Group and sort chronologically
    by_tool: dict[tuple, list] = defaultdict(list)
    for r in records:
        by_tool[(r["repo_url"], r["tool_name"])].append(r)
    for key in by_tool:
        by_tool[key].sort(key=lambda r: r.get("commit_date", ""))

    n_tools     = len(by_tool)
    n_static    = sum(1 for v in by_tool.values() if len(v) == 1)
    n_changed   = n_tools - n_static
    n_diffs_exp = sum(len(v) - 1 for v in by_tool.values() if len(v) > 1)

    print(f"Tools total          : {n_tools}")
    print(f"  no_drift_observed  : {n_static}")
    print(f"  with >=2 snapshots : {n_changed}  → {n_diffs_exp} consecutive diffs")

    t0 = time.time()
    written = 0

    with out_path.open("w") as fout:
        for i, ((repo_url, tool_name), snaps) in enumerate(by_tool.items(), 1):

            if len(snaps) == 1:
                s = snaps[0]
                rec = {
                    "repo_url"       : repo_url,
                    "tool_name"      : tool_name,
                    "snapshot_count" : 1,
                    "commit_sha"     : s["commit_sha"][:8],
                    "commit_date"    : s["commit_date"],
                    "status"         : "no_drift_observed",
                }
                fout.write(json.dumps(rec) + "\n")
                written += 1
                continue

            for j in range(len(snaps) - 1):
                s1, s2 = snaps[j], snaps[j + 1]

                desc_changed, desc_diff = diff_descriptions(
                    s1.get("description", ""),
                    s2.get("description", ""),
                )
                added, removed, type_changes = diff_schemas(
                    s1.get("input_schema", {}),
                    s2.get("input_schema", {}),
                )

                rec = {
                    "repo_url"             : repo_url,
                    "tool_name"            : tool_name,
                    "from_sha"             : s1["commit_sha"][:8],
                    "to_sha"               : s2["commit_sha"][:8],
                    "from_date"            : s1["commit_date"],
                    "to_date"              : s2["commit_date"],
                    "description_changed"  : desc_changed,
                    "description_diff"     : desc_diff,
                    "schema_fields_added"  : added,
                    "schema_fields_removed": removed,
                    "schema_type_changes"  : type_changes,
                    # Structural invariant: True for every record in this design
                    # because records are grouped by tool_name. See
                    # rename_candidates_summary.md for the complementary analysis
                    # of the 688 name-change events not captured here.
                    "is_inplace_mutation"  : True,
                    # True when the tool's implementation file was moved/renamed
                    # at this commit transition (name stable, file path changed).
                    "source_file_changed"  : s1.get("source_file") != s2.get("source_file"),
                    "status"               : "drift_event",
                }
                fout.write(json.dumps(rec) + "\n")
                written += 1

    elapsed = time.time() - t0
    print(f"Records written      : {written}  ({elapsed:.1f}s)")
    print(f"Output               : {out_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", nargs="+", default=None,
                    help="owner__repo slugs (test mode)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    only_slugs = set(args.repos) if args.repos else None
    run(only_slugs, Path(args.out))


if __name__ == "__main__":
    main()
