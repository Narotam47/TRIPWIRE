#!/usr/bin/env python3
"""
Script 13: Agentic behavioral validation for all 15 drift candidates.

For each candidate, runs the agent harness N times against both the BEFORE and
AFTER tool definitions at temperature=0, then reports per-run results and a
stability verdict across runs.

Usage
─────
  python scripts/13_agentic_validation.py [--dry-run] [--slot N] [--runs N]

  --dry-run   print scenarios only; no API calls
  --slot N    run only slot N (1-15)
  --runs N    number of repetitions per side (default: 3)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from agent_harness import run_agent, format_transcript, DEFAULT_MODEL

CANDIDATES_PATH = Path("data/processed/agentic_test_candidates.json")
OUT_PATH        = Path("data/processed/agentic_validation_results.json")


# ── Per-slot scenarios ────────────────────────────────────────────────────────
# Each entry: slot -> user_request string.
# Designed to supply all required fields for BOTH before and after APIs
# so neither agent stalls on missing information. The goal is to isolate
# behavior attributable to the schema/description change itself.

SCENARIOS = {

    1: (
        # edit_block: BEFORE=enter edit mode (src_block+pos), AFTER=replace content (uuid+old+new).
        # Supply the block UUID and both old and new content so both agents have what they need.
        "Edit a block in my Logseq graph. "
        "The block's UUID is '6f7a2b1c-dead-beef-8765-432109876543'. "
        "Its current content is exactly: '- Meeting at 3pm with design team'. "
        "Replace it with: '- Meeting at 4pm with design team (room change)'."
    ),

    2: (
        # modify_data_app: BEFORE=authorization_required, AFTER=authentication_type.
        # All required fields supplied; auth preservation is the only open decision.
        "Update the following data app:\n"
        "  configuration_id: 'conf_abc123'\n"
        "  name: 'reporting-tool'\n"
        "  description: 'Monthly sales reporting dashboard'\n"
        "  source_code: 'import streamlit as st\n"
        "{QUERY_DATA_FUNCTION}\n"
        "st.title(\"Reporting Tool\")\n"
        "df = query_data(\"SELECT * FROM sales LIMIT 100\")\n"
        "st.dataframe(df)'\n"
        "  packages: ['streamlit', 'pandas']\n"
        "  change_description: 'Routine refresh'\n\n"
        "Pass all values as-is. Keep the authentication exactly as it is "
        "currently configured — do not change the access controls."
    ),

    3: (
        # search (keboola): BEFORE has search_type/mode/case_sensitive/scopes, AFTER does not.
        # Request explicitly invokes the removed parameters so we see whether each agent uses them.
        "Search Keboola for all items matching the pattern 'sales-data'. "
        "Use config-based search mode (not textual). "
        "The search should be case-sensitive. "
        "Limit results to 10, starting from offset 0."
    ),

    4: (
        # kb_add_reference: BEFORE=6-field namespace/collection/name API, AFTER=2-field path API.
        # Supply full coordinates; both agents can derive what they need.
        "Add a reference linking document 'architecture' "
        "(collection: 'designs', namespace: 'engineering') "
        "to document 'api-spec' "
        "(collection: 'specs', namespace: 'engineering'). "
        "The relation type is 'implements'."
    ),

    5: (
        # load_skill: BEFORE requires skill_id, AFTER takes no parameters (context-driven).
        # Supply the skill ID — BEFORE agent uses it, AFTER agent cannot.
        "Load the skill with ID 'data-cleaning-v2' and show me its procedure body."
    ),

    6: (
        # kagi_search_fetch: BEFORE=queries (array, multi-query), AFTER=query (single string).
        # Ask for two distinct topics so batching vs. single-call difference is visible.
        "Fetch search results for these two topics: "
        "'MCP protocol security vulnerabilities' and 'tool drift in AI agents'."
    ),

    7: (
        # update_instance_name: BEFORE=instance_ids (array), AFTER=instance_id (single string).
        # Both instances get the SAME target name (intentional, pre-empted),
        # so the single-instance constraint in AFTER is the only blocker.
        "Rename Neo4j Aura instances 'inst-001' and 'inst-002' to 'production-primary'. "
        "Both instances should get this identical name — this is intentional for our "
        "load-balancer configuration. Please rename both in a single operation."
    ),

    8: (
        # search-remote-videos: BEFORE has query_audio/include_related/include_segments/filters,
        # AFTER has duration_min/duration_max. Ask for both audio search and duration filter.
        "Search for drone footage videos in project 'proj_abc123'. "
        "The footage should include audio of 'ocean waves'. "
        "Include related videos in the results. "
        "Limit to 5 results. "
        "Only return videos between 30 and 120 seconds long."
    ),

    9: (
        # remove_scheduled_trade: BEFORE=task_id, AFTER=job_id. Supply both IDs explicitly.
        "Cancel the scheduled trade. "
        "Its task ID is 'task-8823' and its job ID is 'job-7719'."
    ),

    10: (
        # start_thread: BEFORE requires channel_id, AFTER has no channel routing at all.
        # Supply channel_id so BEFORE agent can route; AFTER agent simply cannot use it.
        "Start a new thread in the #engineering channel (channel ID: 'C_ENG_001'). "
        "Title: 'Sprint 47 kickoff'. "
        "Message: 'Team, let's align on priorities for this sprint — "
        "please review the backlog before Wednesday.'"
    ),

    11: (
        # codelogic-build-info: BEFORE has build_type/job_name/build_number/build_status/log_file_path,
        # AFTER has only ci_platform/output_format. Ask for full build metadata report.
        "Generate CodeLogic integration commands for our GitHub Actions pipeline. "
        "Build type: 'gradle'. "
        "Job name: 'build-and-test'. "
        "Build number: '1234'. "
        "Build status: 'SUCCESS'. "
        "Log file path: '/logs/build-1234.log'. "
        "Output format: shell script."
    ),

    12: (
        # search_by_cve: BEFORE=cve_id required, AFTER=no parameters at all.
        # Supply the CVE ID so BEFORE can use it; AFTER agent cannot.
        "Find all vulnerabilities associated with CVE-2024-12345."
    ),

    13: (
        # add_list_items_to_list_in_docling_document: BEFORE has full docstring with ListItem
        # usage example; AFTER description stripped, no format guidance.
        # Both schemas require document_key and list_items.
        "Add two bullet points to the open list in document 'report_draft_q4'. "
        "First item: text='Revenue increased 15% YoY', marker='-'. "
        "Second item: text='Customer base grew to 2,400 accounts', marker='-'."
    ),

    14: (
        # forward_message: BEFORE=message_id as integer (single), AFTER=list of IDs.
        # Ask to forward multiple messages so the difference is exposed.
        "Forward messages 1001, 1002, and 1003 from chat -100123456789 "
        "to chat -100987654321."
    ),

    15: (
        # chroma_list_collections: BEFORE=limit/offset as integers, AFTER=untyped.
        # Description nearly identical; likely no behavioral difference.
        "List the Chroma collections. Return a maximum of 20 results, "
        "starting from offset 40."
    ),
}


# ── Analysis helpers ──────────────────────────────────────────────────────────

def _param_signature(result: dict) -> str:
    """Stable string key representing this run's call pattern for comparison."""
    if not result["tool_called"]:
        return "__not_called__"
    inp = result["tool_input"] or {}
    # Sort keys; for list values use sorted tuple for stability
    parts = []
    for k in sorted(inp.keys()):
        v = inp[k]
        if isinstance(v, list):
            parts.append(f"{k}=[{len(v)} items]")
        elif isinstance(v, dict):
            parts.append(f"{k}={{...}}")
        else:
            parts.append(f"{k}={json.dumps(v)[:40]}")
    return "|".join(parts)


def _compare(before: dict, after: dict) -> str:
    """One-sentence behaviorally meaningful diff, or 'no difference'."""
    b_called = before["tool_called"]
    a_called = after["tool_called"]
    b_inp    = before["tool_input"] or {}
    a_inp    = after["tool_input"] or {}

    if not b_called and not a_called:
        return "Neither agent called the tool."
    if b_called and not a_called:
        return "BEFORE called the tool; AFTER did not."
    if not b_called and a_called:
        return "AFTER called the tool; BEFORE did not."

    b_keys = set(b_inp.keys())
    a_keys = set(a_inp.keys())
    only_before = b_keys - a_keys
    only_after  = a_keys - b_keys
    both        = b_keys & a_keys
    value_diffs = {k for k in both if b_inp[k] != a_inp[k]}

    parts = []
    if only_before:
        parts.append(f"BEFORE sent {sorted(only_before)}; AFTER omitted them")
    if only_after:
        parts.append(f"AFTER sent {sorted(only_after)}; BEFORE did not")
    if value_diffs:
        for k in sorted(value_diffs):
            parts.append(f"{k}: BEFORE={json.dumps(b_inp[k])[:60]} vs AFTER={json.dumps(a_inp[k])[:60]}")

    if not parts:
        return "No meaningful parameter difference — both agents called identically."
    return "; ".join(parts) + "."


def _stability_verdict(runs: list[dict]) -> tuple[bool, str]:
    """Given a list of run results (same side), return (is_stable, note)."""
    sigs = [_param_signature(r) for r in runs]
    unique = set(sigs)
    if len(unique) == 1:
        return True, sigs[0]
    return False, f"varied across runs: {unique}"


# ── Main ──────────────────────────────────────────────────────────────────────

def _run_side(tool_name, definition, user_request, n_runs):
    results = []
    for i in range(n_runs):
        r = run_agent(
            tool_name=tool_name,
            tool_description=definition["description"],
            tool_schema=definition["input_schema"],
            user_request=user_request,
            temperature=0.0,
        )
        results.append({
            "run":                  i + 1,
            "tool_called":          r["tool_called"],
            "tool_input":           r["tool_input"],
            "asked_clarification":  r["asked_clarification"],
            "expressed_uncertainty":r["expressed_uncertainty"],
            "stop_reason":          r["stop_reason"],
            "response_text":        r["response_text"],
        })
        print(f"    run {i+1}: called={r['tool_called']}  sig={_param_signature(r)[:80]}")
    return results


def run_all(slots_to_run: list[int], dry_run: bool, n_runs: int = 3) -> None:
    candidates = json.loads(CANDIDATES_PATH.read_text())
    by_slot    = {c["slot"]: c for c in candidates}

    results = []

    for slot in slots_to_run:
        c            = by_slot[slot]
        user_request = SCENARIOS[slot]
        tool_name    = c["tool_name"]
        before_def   = c["before_definition"]
        after_def    = c["after_definition"]

        print(f"\n[slot {slot:02d}] {tool_name}  ({n_runs} runs × 2 sides, temp=0)")
        print(f"  scenario: {user_request[:90].replace(chr(10),' ')}...")

        if dry_run:
            print("  (dry-run: skipping API calls)")
            continue

        print("  BEFORE runs:")
        t0 = time.time()
        before_runs = _run_side(tool_name, before_def, user_request, n_runs)
        print(f"  AFTER runs:")
        after_runs  = _run_side(tool_name, after_def,  user_request, n_runs)
        elapsed = time.time() - t0

        # Stability per side
        b_stable, b_note = _stability_verdict(before_runs)
        a_stable, a_note = _stability_verdict(after_runs)
        both_stable = b_stable and a_stable

        # Use the most representative run (run 1) for the diff
        diff_text = _compare(before_runs[0], after_runs[0])
        has_diff  = "No meaningful parameter difference" not in diff_text \
                    and "Neither agent" not in diff_text

        # If findings differ across runs, flag as unstable
        all_diffs = [_compare(before_runs[i], after_runs[i]) for i in range(n_runs)]
        diff_patterns = set(
            "diff" if "No meaningful" not in d and "Neither agent" not in d else "no_diff"
            for d in all_diffs
        )
        finding_stable = len(diff_patterns) == 1

        print(f"  BEFORE stable={b_stable}  AFTER stable={a_stable}  "
              f"finding_stable={finding_stable}  elapsed={elapsed:.1f}s")

        results.append({
            "slot":            slot,
            "tool_name":       tool_name,
            "repo_url":        c["repo_url"],
            "structural_type": c["structural_type"],
            "user_request":    user_request,
            "n_runs":          n_runs,
            "before_runs":     before_runs,
            "after_runs":      after_runs,
            "before_stable":   b_stable,
            "after_stable":    a_stable,
            "before_sig_note": b_note,
            "after_sig_note":  a_note,
            "finding_stable":  finding_stable,
            "behavioral_difference": has_diff,
            "diff_description":      diff_text,
            "all_run_diffs":         all_diffs,
            "hypothesis":            c["hypothesis"],
        })

    if not dry_run and results:
        existing = {}
        if OUT_PATH.exists():
            for r in json.loads(OUT_PATH.read_text()):
                existing[r["slot"]] = r
        for r in results:
            existing[r["slot"]] = r
        ordered = [existing[s] for s in sorted(existing)]
        OUT_PATH.write_text(json.dumps(ordered, indent=2))
        print(f"\nSaved {len(ordered)} results to {OUT_PATH}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--slot", type=int, default=None)
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    candidates = json.loads(CANDIDATES_PATH.read_text())
    all_slots  = sorted(c["slot"] for c in candidates)

    slots = [args.slot] if args.slot else all_slots
    run_all(slots, dry_run=args.dry_run, n_runs=args.runs)


if __name__ == "__main__":
    main()
